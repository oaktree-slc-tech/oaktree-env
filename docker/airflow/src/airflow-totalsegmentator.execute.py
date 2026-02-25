'''	This script implements the execution of an Airflow DAG as part
	of a Sonador Total Segmentator pipeline.
'''
import os, sys, logging, tempfile, json, subprocess, multiprocessing

from sonador_hook import SonadorHook
from object_storage_hook import ObjectStorageHook
from airflow.io.path import ObjectStoragePath
from airflow.hooks.subprocess import SubprocessHook

import SimpleITK as sitk
import numpy as np

logger = logging.getLogger(__name__)


def execute_cmd(cmd, bufsize=1, universal_newlines=True):
	'''	Execute the provided command and stream output to STDOUT / logging
	'''
	try:
		proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
			bufsize=bufsize, universal_newlines=universal_newlines)

		# Stream output
		for l in proc.stdout:
			sys.stdout.write(l)
			sys.stdout.flush()

		# Wait for the process to complete
		rcode = proc.wait()
		if rcode != 0:
			raise subprocess.CalledProcessError(rcode, cmd)

	except subprocess.CalledProcessError as err:
		logger.error('Unable to execute Total Segmentator commend. Cmd: "%s". Error: "%s".' % (
			' '.join(cmd), err
		))


def sonador_totalsegmentator_execute(conn_id, s3_conn_id, series_uid, totalsegmentator_labels,
		totalsegmentator_license=None, totalsegmentator_device='cpu', tmp_prefix='totalsegmentator/tmp'):
	'''	Execute segmentation of a Sonador series as part of an Airflow DAG.
	'''

	# Register Total Segmentator (if license provided)
	if totalsegmentator_license:
		execute_cmd([
			'/home/airflow/env/totalsegmentator/bin/totalseg_set_license', '-l', totalsegmentator_license,
		])

	# Unpack Total Segmentator labels
	if not totalsegmentator_labels:
		raise ValueError('Unable to execute Total Segmentator pipeline, no labels provided')
	if isinstance(totalsegmentator_labels, str):
		totalsegmentator_labels = json.loads(totalsegmentator_labels)

	# Initialize Sonador Server
	iserver = SonadorHook(**SonadorHook.hook_options(**{ 'conn_id': conn_id })).init_sonador_imageserver()

	# Initialize object storage
	storage_hook = ObjectStorageHook(**ObjectStorageHook.hook_options(**{ 's3_conn_id': s3_conn_id }))
	p = storage_hook.get_storage_params()
	objects = ObjectStoragePath(p.root, conn_id=p.conn_id)

	logging.info('Run Total Segmentator: sonador-conn="%s" s3-conn="%s" series-uid="%s" Labels: %s' % (
		conn_id, s3_conn_id, series_uid, ','.join(totalsegmentator_labels),
	))

	# Retrieve series from Sonador
	sx = iserver.get_series(series_uid)

	# Create tmp folder to run totalsegmentator
	with tempfile.TemporaryDirectory() as tmp:
		logging.debug('Begin download "%s.nii.gz" from s3="%s" for processing by Total Segmentator' % (
			sx.pk, s3_conn_id
		))

		# Retrieve file from S3 and write to local tmp
		sx_filename = '%s.nii.gz' % sx.pk
		sx_filepath = os.path.join(tmp, sx_filename)
		sx_s3filepath = objects / ('%s/%s/%s' % (tmp_prefix, sx.parent.pk, sx_filename))
		seg_outfolder = 'segmentations.%s' % sx.parent.pk
		seg_outfpath = os.path.join(tmp, seg_outfolder)

		# Create segmentation folder
		os.mkdir(seg_outfpath)
		logging.debug('Segmentation output directory created: %s' % seg_outfpath)

		with sx_s3filepath.open('rb') as s3_f:
			with open(sx_filepath, 'wb') as f:
				f.write(s3_f.read())

		# Create output folder for segmentations
		logging.debug('Image volume deployed "%s" successfully' % sx_filepath)
		
		# Run Total Segmentator
		cmd = [
			'/home/airflow/env/totalsegmentator/bin/TotalSegmentator',
			'--device', totalsegmentator_device, 
			'-i', sx_filepath, '-o', seg_outfpath,
			'--roi_subset'
		]
		cmd.extend(totalsegmentator_labels)

		logger.info('Total Segmenator cmd:\n%s' % ' '.join(cmd))
		execute_cmd(cmd)

		# Iterate through output directory and copy labels to remote storage
		for seg_outfile in os.listdir(seg_outfpath):

			# Read segmentation label to check if any structures where detected
			_seg_filepath = os.path.join(os.path.join(seg_outfpath, seg_outfile))
			_seg = sitk.ReadImage(_seg_filepath)
			if np.any(sitk.GetArrayFromImage(_seg)):

				# Copy segmentation file to S3
				seg_s3filepath = objects / ('%s/%s/segmentations.%s/%s' % (tmp_prefix, sx.parent.pk, sx.pk, seg_outfile))
				with seg_s3filepath.open('wb') as s3_f:
					with open(_seg_filepath, 'rb') as f:
						s3_f.write(f.read())

				logging.info('Segmentation "%s" written to "%s" successfully' % (_seg_filepath, str(seg_s3filepath)))

		logging.info('Total Segmentator for series-uid="%s" completed successfully. Labels: %s' % (
			sx.pk, ','.join(totalsegmentator_labels),
		))


if __name__ == '__main__':
	import argparse

	parser = argparse.ArgumentParser('Total Segmentator Airflow script for Sonador')
	parser.add_argument('--sonador-conn', required=True, dest='conn_id',
		help='Airflow Connection ID for Sonador')
	parser.add_argument('--objects-conn', required=True, dest='s3_conn_id',
		help='Airflow Connection ID for object storage')
	parser.add_argument('--series-uid', required=True, dest='series_uid',
		help='Sonador/Orthanc UID of the series to be segmented')
	parser.add_argument('--roi', required=True, dest='totalsegmentator_labels',
		help='Total Segmentator regions of interest to be processed')
	parser.add_argument('--device', default='cpu', dest='device',
		help='Compute device which Total Segmentator will be run on.')
	parser.add_argument('--totalsegmentator-license', dest='license', default=None,
		help='Total Segmenator license to use while running inference. If present, Total Segmentator '
			+ 'is able to access private models for ')

	# Parse args
	args = parser.parse_args()

	# Execute script
	sonador_totalsegmentator_execute(args.conn_id, args.s3_conn_id, args.series_uid,
		totalsegmentator_license=args.license, totalsegmentator_labels=args.totalsegmentator_labels,
		totalsegmentator_device=args.device)
