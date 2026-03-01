'''
# AI Example 1: Creating Segmentations using Total Segmentator from data stored in Sonador

This DAG performs **end-to-end anatomical segmentation** on a DICOM series stored in **Sonador/Orthanc**, 
runs **TotalSegmentator inference**, then converts the resulting labelmaps into **mesh (M3D) objects** that 
are **DICOM-encoded and uploaded back to Sonador**.


At a high level, the pipeline is split into three architectural phases:


1. Preparation (Sonador → NIfTI → S3)
2. Segmentation (TotalSegmentator inference in-container)
3. Mesh processing + DICOM encoding (S3 → STL → M3D → Sonador)



## Architecture and task flow


### 1: Environment + connectivity verification
Validates prerequisites before work begins:

- Confirms the **Sonador connection** is usable and the imaging server can be initialized.
- Confirms the **S3/Object Storage connection** is usable and the target bucket exists.
  - Uses boto3 with the S3 connection details and creates the bucket if missing.

This is a “fail fast” step: if credentials, endpoint URLs, or bucket config are wrong, the run stops here.


### 2: Convert Sonador DICOM → nii.gz and stage to S3
Pipeline:

- Loads the target **series** from Sonador using series_uid.
- Ensures modality is supported (**CT or MR**).
- Downloads/loads the series and converts it to **NIfTI (.nii.gz)** using **SimpleITK**.
- Uploads the .nii.gz into object storage under a temporary prefix.

This step produces the inference-ready volume in S3, decoupling inference from Sonador’s backing store and 
keeping the inference container “stateless” (everything is read/write via S3).


### 3: Run TotalSegmentator inference (BASH in container)
Inference is intentionally executed as a **BASH script** inside the Airflow worker container 
image **`oaktreetech/sonador-airflow.ai`**

- Executes the in-container entrypoint
- Pulls connection details from Airflow at runtime:
- Passes runtime parameters to the script
  - Sonador API endpoint/token, imaging server name, optional internal DNS routing
  - S3 endpoint/access/secret/bucket
  - series_uid
  - ROI/label list via --roi (JSON from totalsegmentator_labels)

**Output:** segmentation labelmaps written back to S3 under the temporary prefix, which the next task consumes.


### 4: Labelmaps → meshes → M3D DICOM upload
Pipeline:

- Lists segmentation outputs in S3 (under .../segmentations.<series_uid>...).
- For each labelmap:
  - Loads with **SimpleITK**
  - Skips empty labelmaps (no labeled voxels)
  - Converts labelmap → mesh via labelimg2mesh
  - Repairs mesh with **pymeshfix**
  - Applies **Taubin smoothing** (tunable)
  - Saves to STL in a temp folder
- Applies optional remapping + styling:
  - totalsegmentator_labelmap (rename TotalSegmentator labels to preferred DICOM labels)
  - m3d_dcm_num (force instance numbering)
  - m3d_colors (set mesh color per label)
- DICOM-encodes the meshes into an **M3D series** and uploads back to Sonador via dcm_encode_m3d_models, 
  using any provided series/instance header overrides.



## DAG parameters and inputs


### Connections
* `conn_id` *(string, enum)*  
  Sonador connection ID used for retrieving metadata and initializing the imaging server.
* `s3_conn_id` *(string, enum)*  
  Object storage connection used for staging the .nii.gz and reading/writing segmentations.

### Target series
* `series_uid` *(string)*  
  Sonador/Orthanc Series Instance UID (the series to segment).

### TotalSegmentator inference controls
* `totalsegmentator_labels` *(array, default: [])*  
  List of TotalSegmentator label names to infer. Passed to inference as "--roi" JSON.
* `totalsegmentator_labelmap` *(object|null, default {})*  
  Mapping from TotalSegmentator labels → alternative DICOM/clinical labels (normalizes naming for downstream use).

### Mesh/M3D encoding controls
* `m3d_colors` *(object|null, default {})*  
  Map `{ label -> "#RRGGBB" }` used for mesh coloring in the encoded M3D objects.
* `m3d_dcm_num` *(object|null, default {})*  
  Map `{ label -> integer }` used to force deterministic instance numbering (useful for downstream automation).
* `m3d_series_headers` *(object|null, default {})*  
  Extra DICOM series-level headers to apply when encoding the M3D series (e.g., Series Description).
* `m3d_series_num` *(integer, default 200)*  
  Series number used for the generated M3D series.
* `mesh_smooth_taubin_iter` *(integer, default 25)*  
  Taubin smoothing iterations (higher = smoother, but can oversmooth fine anatomy).
* `mesh_smooth_taubin_pass_band` *(number, default 0.025)*  
  Taubin smoothing pass band (lower = stronger smoothing per iteration; tune carefully).



## Tuning guidance for different segmentation workloads
For best results, it is recommended to tune the output of specific segmentation tasks. These can be
implemented as separate DAGs which call the TotalSegmentator DAG to create the desired output.


### 1: Choose the right label set for the job
The biggest lever is **totalsegmentator_labels**:

- **Targeted organ set (faster, less clutter):** provide only what you need (e.g., abdominal planning organs).
- **Broad anatomical sweep (more outputs, more post-processing time):** provide many labels for exploratory workflows.

Start narrow, then expand—mesh conversion and DICOM encoding cost scales with the number of non-empty labels.

### 2: Normalize labels to your internal schema
Use **totalsegmentator_labelmap** to translate TotalSegmentator naming into your preferred DICOM/clinical labels. 
This keeps semantics stable across model versions and task presets.

### 3: Make outputs deterministic for downstream automation
If downstream rules depend on ordering or instance numbers:

- Set m3d_dcm_num per label for stable instance numbering.
- Set m3d_colors per label for consistent visualization.

### 4: Balance mesh quality vs anatomical fidelity
Mesh processing is robust by design (repair + smoothing), but smoothing can blur fine detail:

- Increase mesh_smooth_taubin_iter if you see jagged/stair-step surfaces.
- Decrease it (or increase mesh_smooth_taubin_pass_band) if thin structures lose definition.

### 5: Encode useful context into DICOM metadata
Use m3d_series_headers to embed intent into the M3D series (especially when generating multiple 
segmentation series per patient), e.g., include a descriptive Series Description that indicates the task/dataset/run.

'''

import sys, os, logging, json, requests, re, fnmatch, zipfile, posixpath
from datetime import datetime, timedelta
from io import BytesIO

from collections import namedtuple

from airflow import DAG
from airflow.providers.standard.operators.bash import BashOperator
from airflow.providers.standard.operators.python import PythonOperator, ExternalPythonOperator, BranchPythonOperator
from airflow.models import Variable
from airflow.sdk import Param

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
	import boto3
	from botocore.exceptions import ClientError
	from sonador_hook import SonadorHook
	from object_storage_hook import ObjectStorageHook

	# Verify Sonador connection first
	hook = SonadorHook(**SonadorHook.hook_options(**kwargs))
	hook.verify_etl_env()
	iserver = hook.init_sonador_imageserver()
	logger.info('Sonador connection verified successfully')

	# Retrieve Airflow S3 connection and ensure bucket exists
	storage_hook = ObjectStorageHook(**ObjectStorageHook.hook_options(**kwargs))
	p = storage_hook.get_storage_params()
	logger.info(f'S3 storage params: conn_id={p.conn_id}, root={p.root}')

	# Extract bucket name from s3://bucket-name path
	bucket_name = p.root.replace('s3://', '').split('/')[0]
	logger.info(f'Target bucket: {bucket_name}')

	# Get connection details for direct boto3 usage
	conn = storage_hook.get_connection()
	conn_extra = conn.extra if isinstance(conn.extra, dict) else json.loads(conn.extra or '{}')

	# Log connection details (mask password)
	endpoint_url = conn_extra.get('endpoint_url', 'http://object-storage:9000')
	logger.info(f'S3 endpoint: {endpoint_url}, login: {conn.login}, has_password: {bool(conn.password)}')

	# Create boto3 client directly with explicit endpoint
	s3_client = boto3.client('s3',
		endpoint_url=endpoint_url,
		aws_access_key_id=conn.login,
		aws_secret_access_key=conn.password,
		region_name=conn_extra.get('region_name', 'us-east-1')
	)

	# Create bucket if it doesn't exist
	try:
		s3_client.head_bucket(Bucket=bucket_name)
		logger.info(f'S3 bucket exists: {bucket_name}')
	except ClientError as e:
		error_code = e.response.get('Error', {}).get('Code', '')
		if error_code in ('404', 'NoSuchBucket'):
			s3_client.create_bucket(Bucket=bucket_name)
			logger.info(f'Created S3 bucket: {bucket_name}')
		else:
			logger.error(f'S3 error: {e}')
			raise


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
	from airflow.providers.amazon.aws.hooks.s3 import S3Hook

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

	# Initialize object storage (bucket must already exist)
	storage_hook = ObjectStorageHook(**ObjectStorageHook.hook_options(**kwargs))
	p = storage_hook.get_storage_params()
	s3_hook = S3Hook(aws_conn_id=p.conn_id)	

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
		sx_s3filepath = '%s/%s/%s' % (tmp_prefix, sx.parent.pk, sx_filename)
		s3_hook.load_file(filename=sx_filepath, key=sx_s3filepath, bucket_name=p.root, replace=True)

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

	from airflow.providers.amazon.aws.hooks.s3 import S3Hook

	from sonador_hook import SonadorHook
	from object_storage_hook import ObjectStorageHook

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

	# Initialize object storage (bucket must already exist)
	storage_hook = ObjectStorageHook(**ObjectStorageHook.hook_options(**kwargs))
	p = storage_hook.get_storage_params()
	s3_hook = S3Hook(aws_conn_id=p.conn_id)

	# Retrieve series that was segmented by Total Segmentator
	sx = iserver.get_series(sx_uid)

	with tempfile.TemporaryDirectory() as tmp:

		# Placeholder options for DCM encoding
		_stl_instance_numbers = {}
		_stl_file_colors = {}
		_stl_models = {}

		# Retrieve file keys of segmentations stored in object storage
		for i, s3_filepath in enumerate(s3_hook.list_keys(bucket_name=p.root,
				prefix='%s/%s/segmentations.%s' % (tmp_prefix, sx.parent.pk, sx.pk))):

			logger.info('series=%s: seg="%s"' % (sx.pk, str(s3_filepath)))

			# Parse STL label
			stl_folder, stl_filename = posixpath.split(s3_filepath)
			totalsegmentator_label, stl_ext = posixpath.splitext(stl_filename.replace('%s.' % sx.pk, ''))
			totalsegmentator_label = totalsegmentator_label.replace('.nii', '')
			stl_label = totalsegmentator_labelmap.get(totalsegmentator_label) or totalsegmentator_label
			
			# Instance numbering and color
			stl_num = m3d_dcm_num.get(stl_label) if m3d_dcm_num.get(stl_label) else i
			stl_color = m3d_colors.get(stl_label) if m3d_colors.get(stl_label) else ANATOMY_BONE_HEXCOLOR			

			# Retrieve segmentation, write to local storage to allow for loading with SimpleITK,
			# and convert to mesh using labelimg2mesh			

			# Write segmentation file to local folder
			tmp_filepath = os.path.join(tmp, stl_filename)
			s3_hook.download_file(s3_filepath, bucket_name=p.root, local_path=tmp,
				use_autogenerated_subdir=False, preserve_file_name=True)

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
	'start_date': datetime(2024, 1, 1),
	'retries': 1,
	'retry_delay': timedelta(minutes=1),
}


# Initialize TotalSegmentator DAG
dag = DAG('Sonador-TotalSegmentator', default_args=default_args, schedule=None, params={

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
}, tags=['sonador-io', 'sonador-ai', 'm3d', 'dcm-segmentation'], doc_md=__doc__)


# Define task steps
l0 = PythonOperator(task_id='totalsegmentator-verify-env', python_callable=verify_etl_env, dag=dag)
t1 = PythonOperator(task_id='totalsegmentator-prepare-seg-data', 
	python_callable=sonador_prepare_totalsegmentator_data, dag=dag)
t2 = BashOperator(task_id='totalsegmentator-execute', dag=dag, bash_command=r'''
		{% set license = var.value.get('SONADOR_TOTALSEGMENTATOR_LICENSE', '') %}		
		{% set sonador = conn[params.conn_id] %}
		{% set sonadorx = sonador.extra_dejson or {} %}
		{% set s3 = conn[params.s3_conn_id] %}
		{% set s3x = s3.extra_dejson or {} %}

		/home/airflow/env/totalsegmentator/bin/python3 \
		/home/airflow/env/totalsegmentator/bin/airflow-totalsegmentator.execute.py \
		--api-endpoint "{{ sonador.schema }}://{{ sonador.host }}:{{ sonador.port }}" \
		--api-token "{{ sonador.password }}" {% if sonadorx.get('SONADOR_INTERNAL_DNS', False) %}--internal-dns{% endif %} \
		--server "{{ sonadorx.get('SONADOR_IMAGING_SERVER', '') }}" \
		--storage-endpoint "{{ s3x.get('endpoint_url', '') }}" \
		--storage-access-id "{{ s3x.get('aws_access_key_id', '') }}" \
		--storage-secret-key "{{ s3x.get('aws_secret_access_key', '') }}" \
		--storage-bucket "{{ s3x.get('OBJECTS_BUCKET', 'airflow') }}" \
		--series-uid {{ params.series_uid }} {% if license %}--totalsegmentator-license '{{ license }}'{% endif %} \
		--roi '{{ params.totalsegmentator_labels | tojson }}'
	''')
t3 = PythonOperator(task_id='totalsegmentator-m3d-series', 
	python_callable=sonador_totalsegmentator_create_m3d_series, dag=dag)


# Order tasks
l0.set_downstream(t1)
t1.set_downstream(t2)
t2.set_downstream(t3)
