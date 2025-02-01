# AI Example 1: Creating Segmentations using Total Segmentator from data stored in Sonador.
import sys, os, logging, json, requests, re, fnmatch, zipfile, posixpath
from datetime import datetime, timedelta
from io import BytesIO

from collections import namedtuple

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator, ExternalPythonOperator
from airflow.utils.dates import days_ago
from airflow.models import Variable
from airflow.models.param import Param

from client.utils.conversion import str2bool
from client.utils.object import pick

from sonador.apisettings import SONADOR_URL, SONADOR_APITOKEN, SONADOR_INTERNAL_DNS, \
	SONADOR_IMAGING_SERVER
from sonador.servers import SonadorServer

logger = logging.getLogger(__name__)


TotalSegmentatorS3ConnectionParams = namedtuple(
	'TotalSegmentatorS3ConnectionParams',  ('conn_id', 'root'))


def object_storage_connections():
	'''	Retrieve a list of connection IDs for available S3 connections
	'''
	from object_storage_hook import ObjectStorageHook
	return ObjectStorageHook.available_connections()


def sonador_connections():
	'''	Retrieve a list of connection IDs for availbale Sonador connections
	'''
	from sonador_hook import SonadorHook
	return SonadorHook.available_connections()


def verify_etl_env(**kwargs):
	'''	Log the command context to stderr
	'''
	from sonador_hook import SonadorHook
	from object_storage_hook import ObjectStorageHook
	from airflow.io.path import ObjectStoragePath

	hook = SonadorHook(**SonadorHook.hook_options(**kwargs))
	hook.verify_etl_env()
	iserver = hook.init_sonador_imageserver()

	# Retrieve Airflow S3 connection and ensure that a variable has been defined specifying the 
	# working bucket for total segmentator.
	storage_hook = ObjectStorageHook(**ObjectStorageHook.hook_options(**kwargs))
	p = storage_hook.get_storage_params()
	objects = ObjectStoragePath(p.root, conn_id=p.conn_id)


def sonador_prepare_totalsegmentator_data(**kwargs):
	'''	Prepare Sonador image series for Total Segmentator inference. Pipeline:
	
		1.	Verify that the desired segmentation is supported by Total Segmentator
		2.	Download DICOM imaging data
		3.	Convert to nii.gz and upload converted nii.gz data to object storage
			for processing by Total Segmentator.
	'''
	import sys, os, logging, tempfile
	import SimpleITK as sitk

	from sonador_hook import SonadorHook
	from object_storage_hook import ObjectStorageHook
	from airflow.io.path import ObjectStoragePath

	from sonador.apisettings import DCM_MODALITY_MR, DCM_MODALITY_CT
	from sonador3d.imaging.volume import SonadorImagingVolume

	# Unpack DAG parameters
	params = kwargs.get('params', {})
	sx_uid = params.get('series_uid')
	tmp_prefix = kwargs.get('tmp_prefix') or params.get('tmp_prefix', 'totalsegmentator/tmp')
	if not sx_uid:
		raise ValueError('Unable to perform segmentation. Invalid series UID: "%s"' % sx_uid)
	if not tmp_prefix:
		raise ValueError(
			'Unable to perform segmentation. Invalid Total Segmentator tmp prefix: "%s"' % tmp_prefix)

	# Initialize Sonador Server
	iserver = SonadorHook(**SonadorHook.hook_options(**kwargs)).init_sonador_imageserver()

	# Initialize object storage
	storage_hook = ObjectStorageHook(**ObjectStorageHook.hook_options(**kwargs))
	p = storage_hook.get_storage_params()
	objects = ObjectStoragePath(p.root, conn_id=p.conn_id)
	objects.mkdir(exist_ok=True)

	# Retrieve series to be segmented by Total Segmentator, ensure that it is a supported modality (CT/MR)
	sx = iserver.get_series(sx_uid)
	if not sx.modality in (DCM_MODALITY_MR, DCM_MODALITY_CT):
		raise ValueError('Unable to segment series=%s. Unsupported modality: "%s"' % (sx.pk, sx.modality))

	# Create tmp folder for image download and conversion to nii.gz
	with tempfile.TemporaryDirectory() as tmp:
		logging.info('Download and process imaging data for series="%s" local-tmp="%s"' % (sx.pk, tmp))
		sx_filename = '%s.nii.gz' % sx.pk
		sx_filepath = os.path.join(tmp, sx_filename)

		# Retrieve imaging series and convert to nii.gz
		sitk.WriteImage(SonadorImagingVolume(sx).volume, sx_filepath)
		logger.info('Imaging data for series="%s" written to local-file="%s"' % (sx.pk, sx_filepath))

		# Upload file to S3 object storage temp
		sx_s3filepath = objects / ('%s/%s/%s' % (tmp_prefix, sx.parent.pk, sx_filename))
		with sx_s3filepath.open('wb') as s3_f:
			with open(sx_filepath, 'rb') as f:
				s3_f.write(f.read())

		logger.info('Data for series="%s" written to "%s" successfully' % (
			sx.pk, str(sx_s3filepath)
		))


def sonador_totalsegmentator_create_m3d_series(**kwargs):
	''' Create an M3D series from Total Segmentator data saved to S3 object storage. Pipelin.

		1. Retrieve segmentation label persisted to S3 by Total Segmentator
		2. Convert labelmaps to mesh instances
		3. Run mesh processing pipeline to fill holes, fix inverted triangles, and smooth
		4. (optional) Substitute Total Segmentator labels for labels provided by param
		5. DICOM encode mesh and upload to Sonador
	'''
	import sys, os, logging, tempfile
	
	import SimpleITK as sitk
	import numpy as np
	import pymeshfix

	from sonador_hook import SonadorHook
	from object_storage_hook import ObjectStorageHook
	from airflow.io.path import ObjectStoragePath

	from sonador.apisettings import DCM_DATE_STRFORMAT, DCM_TIME_STRFORMAT
	from sonador.apisettings.m3d import ANATOMY_BONE_HEXCOLOR
	from sonador.tasks.m3d import dcm_encode_m3d_models

	from sonador3d.spatial.helpers import labelimg2mesh

	# Unpack DAG parameters
	params = kwargs.get('params', {})
	sx_uid = params.get('series_uid')
	stl_sx_nums = kwargs
	if not sx_uid:
		raise ValueError('Unable to perform segmentation. Invalid series UID: "%s"' % sx_uid)

	# Retrieve mesh parameters 
	totalsegmentator_labelmap = params.get('totalsegmentator_labelmap') or {}
	m3d_colors = params.get('m3d_colors')
	m3d_dcm_num = params.get('m3d_dcm_num')
	m3d_series_headers = params.get('m3d_series_headers') or {}
	m3d_instance_headers = params.get('m3d_instance_headers') or {}
	m3d_series_num = params.get('m3d_series_num')
	mesh_smooth_taubin_iter = params.get('mesh_smooth_taubin_iter')
	mesh_smooth_taubin_pass_band = params.get('mesh_smooth_taubin_pass_band')

	# DAG options
	tmp_prefix = kwargs.get('tmp_prefix') or params.get('tmp_prefix', 'totalsegmentator/tmp')
	if not tmp_prefix:
		raise ValueError(
			'Unable to perform segmentation. Invalid Total Segmentator tmp prefix: "%s"' % tmp_prefix)

	# Initialize Sonador Server
	iserver = SonadorHook(**SonadorHook.hook_options(**kwargs)).init_sonador_imageserver()

	# Initialize object storage
	storage_hook = ObjectStorageHook(**ObjectStorageHook.hook_options(**kwargs))
	p = storage_hook.get_storage_params()
	objects = ObjectStoragePath(p.root, conn_id=p.conn_id)
	objects.mkdir(exist_ok=True)

	# Retrieve series that was segmented by Total Segmentator
	sx = iserver.get_series(sx_uid)

	with tempfile.TemporaryDirectory() as tmp:

		# Placeholder options for DCM encoding
		_stl_instance_numbers = {}
		_stl_file_colors = {}
		_stl_models = {}

		# Retrieve file keys of segmentations stored in object storage
		seg_objects = objects / ('%s/%s/segmentations.%s' % (tmp_prefix, sx.parent.pk, sx.pk))
		for i, s3_filepath in enumerate(seg_objects.iterdir()):

			# Parse STL label
			stl_folder, stl_filename = posixpath.split(s3_filepath)
			totalsegmentator_label, stl_ext = posixpath.splitext(stl_filename.replace('%s.' % sx.pk, ''))
			totalsegmentator_label = totalsegmentator_label.replace('.nii', '')
			stl_label = totalsegmentator_labelmap.get(totalsegmentator_label) or totalsegmentator_label
			
			# Instance numbering and color
			stl_num = m3d_dcm_num.get(stl_label) if m3d_dcm_num.get(stl_label) else i
			stl_color = m3d_colors.get(stl_label) if m3d_colors.get(stl_label) else ANATOMY_BONE_HEXCOLOR

			logger.info('series=%s: seg="%s"' % (sx.pk, str(s3_filepath)))

			# Retrieve segmentation, write to local storage to allow for loading with SimpleITK,
			# and convert to mesh using labelimg2mesh
			with s3_filepath.open('rb') as s3_f:

				# Write segmentation file to local folder
				tmp_filepath = os.path.join(tmp, stl_filename)
				with open(tmp_filepath, 'wb') as tmp_f:
					tmp_f.write(s3_f.read())

				# Read segmentation labelmap from disk and check for labeled voxels
				_seg = sitk.ReadImage(tmp_filepath)
				if np.any(sitk.GetArrayFromImage(_seg)):

					# Convert total segmentator label (if a dict mapping was provided)
					_seg_stl0 = labelimg2mesh(_seg)
					_seg_mfix = pymeshfix.MeshFix(_seg_stl0)
					_seg_mfix.repair()

					# Smooth using a taubin filter and save to tmp folder
					_stl_tmp_filepath = os.path.join(tmp, '%s.%s.stl' % (sx.pk, stl_label))
					_seg_stl1 = _seg_mfix.mesh.smooth_taubin(
						n_iter=mesh_smooth_taubin_iter, pass_band=mesh_smooth_taubin_pass_band)
					_seg_stl1.save(_stl_tmp_filepath)

					# STL DICOM attributes
					_stl_instance_numbers[stl_label] = stl_num
					_stl_file_colors[stl_label] = stl_color

					# Add segmentation stream to models to be encoded
					with open(_stl_tmp_filepath, 'rb') as f_stl:
						stl_stream = BytesIO(f_stl.read())
						_stl_models[stl_label] = stl_stream

					logger.info('series="%s" totalsegmentator-label="%s" label="%s" mesh-color="%s"' % (
						sx.pk, totalsegmentator_label, stl_label, stl_color
					))
		if _stl_models:

			# DCM encode and upload segmentations to Sonador
			_stl_dcm_encode_kargs = {
				'stl_instance_numbers': _stl_instance_numbers,
				'stl_file_colors': _stl_file_colors,
				'm3d_series_headers': m3d_series_headers,
				'm3d_series_number': m3d_series_num,
				'm3d_series_headers': m3d_series_headers,
				'm3d_instance_headers': m3d_instance_headers,
			}
			dcm_encode_m3d_models(sx, _stl_models, **pick(_stl_dcm_encode_kargs, (
				'stl_instance_numbers', 'stl_file_colors', 'm3d_series_headers', 'm3d_series_number', 
				'm3d_series_headers', 'm3d_instance_headers')))


# Default arugments and storage connections
available_sonador_connections = sonador_connections()
available_storage_connections = object_storage_connections()

default_args = {
	'owner': 'sonador',
	'depends_on_past': False,
	'starts_date': days_ago(1),
	'retries': 1,
	'retry_delay': timedelta(minutes=1),
}


# Initialize TotalSegmentator DAG
dag = DAG('Sonador-TotalSegmentator', default_args=default_args, schedule_interval=None, params={

	# Sonador and S3 connections
	'conn_id': Param(type='string', enum=available_sonador_connections, 
		default=available_sonador_connections[0] if available_sonador_connections else None,
		description='Connection ID of the Sonador connection to be used for retrieving the data '
			+ 'to be segmented.'),
	's3_conn_id': Param(type='string', enum=available_storage_connections,
		default=available_storage_connections[0] if available_storage_connections else None,
		description='Connection ID of the Object Storage (S3) connection to be used for storing '
			+ 'segmentation arrays during the pipeline.'),

	# Series
	'series_uid': Param(type='string', description='Sonador/Orthanc UID of the series to be segmented.'),

	# Total Segmentator options
	'totalsegmentator_labels': Param(type='array', default=[],
		description='Total Segmentator labels to be inferred during processing. One per line.'),
	'totalsegmentator_labelmap': Param(type=['object', 'null'], default={},
		description='Hashmap of Total Segmentator labels to an alternative DCM label which should be '
			+ 'used when creating mesh instances. Values should be keyed to Total Segmentator labels.'),
	'm3d_colors': Param(type=['object', 'null'], default={},
		description='Hashmap of DCM labels to hexadecimal color codes.'),
	'm3d_dcm_num': Param(type=['object', 'null'], default={},
		description='Hashmap of DCM labels to integers. The integer value will be used for the instance '
			+ 'number of the mesh associated with the DCM label.'),
	'm3d_series_headers': Param(type=['object', 'null'], default={},
		description='Headers to be added to the mesh series'),
	'm3d_series_num': Param(type='integer', default=200, description='Number to add to the M3D series'),
	'mesh_smooth_taubin_iter': Param(type='integer', default=25, 
		description='Number of iterations for which the Taubin smoothing algorithm should be applied.'),
	'mesh_smooth_taubin_pass_band': Param(type='number', default=0.025,
		description='Band pass filter value to apply to the Taubin smoothing in the mesh pipeline.')
})


# Define task steps
l0 = PythonOperator(task_id='totalsegmentator-verify-env', python_callable=verify_etl_env, dag=dag)
t1 = PythonOperator(task_id='totalsegmentator-prepare-seg-data', 
	python_callable=sonador_prepare_totalsegmentator_data, dag=dag)
t2 = BashOperator(task_id='totalsegmentator-execute', dag=dag, bash_command=r'''
		/home/airflow/env/totalsegmentator/bin/python3 \
		/home/airflow/env/totalsegmentator/bin/airflow-totalsegmentator.execute.py \
		--sonador-conn {{ params.conn_id }} \
		--objects-conn {{ params.s3_conn_id }} \
		--series-uid {{ params.series_uid }} \
		--roi '{{ params.totalsegmentator_labels | tojson }}'
	''')
t3 = PythonOperator(task_id='totalesegmentator-m3d-series', 
	python_callable=sonador_totalsegmentator_create_m3d_series, dag=dag)


# Order tasks
l0.set_downstream(t1)
t1.set_downstream(t2)
t2.set_downstream(t3)
