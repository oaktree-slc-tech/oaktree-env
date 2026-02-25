'''	This script implements the execution of an Airflow DAG as part
	of a Sonador Total Segmentator pipeline.
'''
import os, sys, logging, tempfile, json, subprocess, multiprocessing, traceback, s3fs

from client.utils.urls import validate_url
from client.utils.object import pick

from sonador.parser import add_arguments_for_api_connection, \
	add_arguments_for_verify_ssl, add_arguments_for_internal_dns, imageserver_operation_options
from sonador.validate import validate_sonador_connection_args, validate_sonador_connection_credentials
from sonador.helpers import initenv_sonador_server

import SimpleITK as sitk
import numpy as np

logger = logging.getLogger(__name__)


# Constants for S3 Storage Connection
OBJECTS_ACCESSID = 'OBJECTS_ACCESSID'
OBJECTS_SECRET = 'OBJECTS_SECRET'
OBJECTS_ENDPOINT = 'OBJECTS_ENDPOINT'
OBJECTS_BUCKET = 'OBJECTS_BUCKET'
PARSER_OBJECTS_ACCESSID = 'storage_accessid'
PARSER_OBJECTS_SECRET = 'storage_secretkey'
PARSER_OBJECTS_ENDPOINT = 'storage_endpoint'
PARSER_OBJECTS_BUCKET = 'storage_bucket'


def validate_object_storage_connection_args(args, exitcode, 
		storage_accessid_attr=PARSER_OBJECTS_ACCESSID, 
		storage_secretkey_attr=PARSER_OBJECTS_SECRET,
		storage_endpoint_attr=PARSER_OBJECTS_ENDPOINT, error_prefix=''):
	'''	Ensure user-provided object storage arguments are sane
	'''
	if not getattr(args, storage_accessid_attr, None):
		logger.error('%sThe client requires an S3 Access ID. See --help for details.'
			% error_prefix)

	if not getattr(args, storage_secretkey_attr, None):
		logger.error('%sThe client requires an S3 Secret Key. See --help for details.'
			% error_prefix)

	# Verify endpoint value and structure
	if not getattr(args, storage_endpoint_attr, None):
		logger.error('%sA S3 service endpoint is required. See --help for details.'
			% error_prefix)

	# Verify S3 URL endpoint structure
	if getattr(args, storage_endpoint_attr):

		try: validate_url(getattr(args, storage_endpoint_attr))
		except ValueError as err:
			logger.error(
				'%sMalformed endpoint URL "%s", a valid http URL is required. Example: http://domain.com:port'
					% (error_prefix, getattr(args, storage_endpoint_attr)))
			exitcode = 1

	return exitcode



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

		raise err


def sonador_totalsegmentator_execute(args, series_uid, totalsegmentator_labels,
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

	# Initialize Sonador Imageserver
	iserver = initenv_sonador_server(sonador_url=args.endpoint, **pick(args, 
			('access_id', 'secret_key', 'apitoken', 'internal_dns', 'verify_ssl'))) \
		.get_imageserver(args.server)

	# Initialize object storage
	s3 = s3fs.S3FileSystem(key=args.storage_accessid, secret=args.storage_secretkey, client_kwargs={
		'endpoint_url': args.storage_endpoint
	})

	logging.info('Run Total Segmentator: sonador-conn="%s" s3-conn="%s" series-uid="%s" Labels: %s' % (
		args.endpoint, args.storage_endpoint, series_uid, ','.join(totalsegmentator_labels),
	))

	# Retrieve series from Sonador
	sx = iserver.get_series(series_uid)

	# Create tmp folder to run totalsegmentator
	with tempfile.TemporaryDirectory() as tmp:
		logging.debug('Begin download "%s.nii.gz" from s3="%s" for processing by Total Segmentator' % (
			sx.pk, args.storage_endpoint,
		))

		# Retrieve file from S3 and write to local tmp
		sx_filename = '%s.nii.gz' % sx.pk
		sx_filepath = os.path.join(tmp, sx_filename)
		sx_s3filepath = 's3://%s/%s/%s/%s' % (args.storage_bucket, tmp_prefix, sx.parent.pk, sx_filename)
		seg_outfolder = 'segmentations.%s' % sx.parent.pk
		seg_outfpath = os.path.join(tmp, seg_outfolder)

		# Create segmentation folder
		os.mkdir(seg_outfpath)
		logging.debug('Segmentation output directory created: %s' % seg_outfpath)

		with s3.open(sx_s3filepath, 'rb') as s3_f:
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
				seg_s3filepath = 's3://%s/%s/%s/segmentations.%s/%s' % (
					args.storage_bucket, tmp_prefix, sx.parent.pk, sx.pk, seg_outfile)
				with s3.open(seg_s3filepath, 'wb') as s3_f:
					with open(_seg_filepath, 'rb') as f:
						s3_f.write(f.read())

				logging.info('Segmentation "%s" written to "%s" successfully' % (_seg_filepath, str(seg_s3filepath)))

		logging.info('Total Segmentator for series-uid="%s" completed successfully. Labels: %s' % (
			sx.pk, ','.join(totalsegmentator_labels),
		))


if __name__ == '__main__':
	import argparse

	parser = argparse.ArgumentParser('Total Segmentator Airflow script for Sonador')
	add_arguments_for_api_connection(parser)
	add_arguments_for_verify_ssl(parser)
	add_arguments_for_internal_dns(parser)
	imageserver_operation_options(parser)

	# Object Storage Connection Parameters
	parser.add_argument('--storage-endpoint', dest=PARSER_OBJECTS_ENDPOINT,
		default=os.environ.get(OBJECTS_ENDPOINT),
		help=('''S3 storage instance to which API requests should be sent (including scheme, port, path). 
			May also be provided as the %s shell environment variable.''' % OBJECTS_ENDPOINT).replace('\t', '').replace('\n', ''))
	parser.add_argument('--storage-access-id', dest=PARSER_OBJECTS_ACCESSID,
		default=os.environ.get(OBJECTS_ACCESSID),
		help=('''Access ID used for authentication to S3 storage. May also be provided 
			as the %s shell environment variable.''' % OBJECTS_ACCESSID).replace('\t', '').replace('\n', ''))
	parser.add_argument('--storage-secret-key', dest=PARSER_OBJECTS_SECRET,
		default=os.environ.get(OBJECTS_SECRET),
		help=('''Secret key used for authentication to S3 storage. May also be 
			provided as the %s shell environment variable.''' % OBJECTS_SECRET).replace('\t', '').replace('\n', ''))
	parser.add_argument('--storage-bucket', '-S', dest=PARSER_OBJECTS_BUCKET, 
		default=os.environ.get(OBJECTS_BUCKET), 
		help=('S3 bucket which should be used to retrieve case folder data. Can also be provided using the %s '
			+ 'environment variable.') % OBJECTS_BUCKET)

	parser.add_argument('--series-uid', required=True, dest='series_uid',
		help='Sonador/Orthanc UID of the series to be segmented')
	parser.add_argument('--roi', required=True, dest='totalsegmentator_labels',
		help='Total Segmentator regions of interest to be processed')
	parser.add_argument('--device', default='cpu', dest='device',
		help='Compute device which Total Segmentator will be run on.')
	parser.add_argument('--totalsegmentator-license', dest='license', default=None,
		help='Total Segmenator license to use while running inference. If present, Total Segmentator '
			+ 'is able to access private models for segmentation tasks.')

	# Parse args and validate
	args = parser.parse_args()
	exitcode = 0
	
	try:
		exitcode = validate_sonador_connection_args(args, exitcode)
		exitcode = validate_sonador_connection_credentials(args, exitcode)
		exitcode = validate_object_storage_connection_args(args, exitcode)

	except Exception as err:
		logger.error('Unable to execute Sonador API operation. Error: %s. Traceback:\n%s' % (
			err, traceback.format_exc(),
		))

		exitcode = 1

	finally:
		
		# Exit if an error was found during initialization
		if exitcode: exit(exitcode)

	# Execute script
	exitcode = 0
	try:
		
		# Run TotalSegmentator
		sonador_totalsegmentator_execute(args, args.series_uid,
			totalsegmentator_license=args.license, totalsegmentator_labels=args.totalsegmentator_labels,
			totalsegmentator_device=args.device)

	except Exception as err:
		exitcode = 1
		logger.error('Unable to run TotalSegmentator due to an error. Error: %s. Traceback:\n%s' % (
			err, traceback.format_exc(),
		))

	finally: exit(exitcode)