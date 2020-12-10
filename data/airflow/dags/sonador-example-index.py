# ETL Example 2: Index Medical Images
# This example shows the use of the AirFlow Python operator in order
# to retrieve, index, verify, and remove a medical imaging scan.
import logging, requests, re, fnmatch, zipfile
from datetime import datetime, timedelta
from io import BytesIO

from airflow import DAG
from airflow.operators.bash_operator import BashOperator
from airflow.operators.python_operator import PythonOperator
from airflow.utils.dates import days_ago
from airflow.models import Variable

from sonador.apisettings import SONADOR_URL, SONADOR_APITOKEN, SONADOR_INTERNAL_DNS, \
	SONADOR_IMAGING_SERVER
from sonador.helpers import SonadorServer
from sonador.apisettings import DCM_EXTENSIONS_DEFAULT

logger = logging.getLogger(__name__)


DCM_FPATTERNS = [re.compile(fnmatch.translate(p)) for p in DCM_EXTENSIONS_DEFAULT]
ARCHIVE_URL_DEFAULT =  'https://oak-tree.tech/documents/156/example.lung-ct.volume-3d.zip'


def init_sonador_imageserver(sonador_url, sonador_apitoken, imageserver, internal_dns=True):
	'''	Initialize the Sonador connection and image server instance

		@returns SonadorImagingServer instance
	'''
	sconn = SonadorServer(sonador_url, None, None, sonador_apitoken, internal_dns=internal_dns)
	return sconn.get_imageserver(imageserver)


def sonador_serverargs():
	'''	Retrieve Sonador configuration from AriFlow
	'''
	sonador_url = Variable.get(SONADOR_URL)
	sonador_apitoken = Variable.get(SONADOR_APITOKEN)
	imageserver = Variable.get(SONADOR_IMAGING_SERVER)
	internal_dns = Variable.get(SONADOR_INTERNAL_DNS)

	return sonador_url, sonador_apitoken, imageserver, internal_dns


def verify_etl_env(**kwargs):
	'''	Log the command context to stderr
	'''
	sonador_url, sonador_apitoken, imageserver, internal_dns = sonador_serverargs()

	if not sonador_url or not sonador_apitoken or not imageserver:
		logger.error('Invalid Sonador configuration.\nURL: %s\nAPI Token: %s\nImage Server: %s' 
			% (sonador_url, sonador_apitoken, imageserver))
		raise ValueError('Invalid Sonador configuration. Please provide valid URL, API token, and image server ID.')

	iserver = init_sonador_imageserver(sonador_url, sonador_apitoken, imageserver, internal_dns=internal_dns)


def sonador_index_imagearchive(**kwargs):
	'''	Download and index an image archive file
	'''
	iserver = init_sonador_imageserver(*sonador_serverargs())

	# Retrieve ZIP archive from website
	ctzip = zipfile.ZipFile(
		BytesIO(requests.get(
			Variable.get('SONADOR_EXAMPLE02_ARCHIVEURL', default_var=ARCHIVE_URL_DEFAULT)).content))

	# Locate all DCM files included in the archive, index, track the series IDs
	dcmfiles = []
	series = {}

	# List files form the zip archive, check file pattern, and add matching patterns
	fnames = ctzip.namelist()
	for p in DCM_FPATTERNS:
		dcmfiles.extend([f for f in fnames if p.search(f)])

	# Iterate through the file names, extract form the archive, upload to Sonador/Orthanc
	for dcmf in dcmfiles:
	
		# Open file reference from the archive, upload the file to Sonador/Orthanc
		afile = ctzip.open(dcmf)
		r = iserver.upload_image(afile)
		rdata = r.json()
		logger.debug('DCM file %s uploaded successfully to %s.\n%s' % (dcmf, iserver.server_label, rdata))

		if rdata.get('ParentSeries'):
			series[rdata.get('ParentSeries')] = series.get(rdata.get('ParentSeries'), 0)+1

	return series


def sonador_validate_indexop(**context):
	'''	Verify that images from the previous step were indexed correctly
	'''
	iserver = init_sonador_imageserver(*sonador_serverargs())
	ti = context['ti']

	# Retrieve upload results from indexing operation
	tdata = ti.xcom_pull(task_ids='example02-index-imagearchive') or {}
	logger.info('DCM series uploaded to server:\n%s' % 
		'\n'.join(['%s: %s' % (k,v) for k,v in tdata.items()]) if tdata else '')

	# Iterate through items, verify upload, and pass along validated list of uploaded series
	sindex_validated = []

	for sid, scount in tdata.items():

		# Retrieve series from Orthanc and compare the upload results from the previous
		# task to the number of indexed slices
		s = iserver.get_series(sid)
		upload_verified = scount == len(s.slices)
		logger.info('Series %s: uploaded=%d indexed=%d match=%s' % (sid, scount, len(s.slices), upload_verified))

		if upload_verified:
			sindex_validated.append(sid)

	return sindex_validated


def sonador_remove_series(**context):
	'''	Remove images indexed by the pipeline
	'''
	iserver = init_sonador_imageserver(*sonador_serverargs())
	ti = context['ti']

	series_validated = ti.xcom_pull(task_ids='example02-validate-indexop') or []

	# Retrieve validated series and remove them from the server
	for sid in series_validated:

		# Remove series from Orthanc
		s = iserver.get_series(sid)
		s.delete()
		logger.info('%s: series removed successfully' % sid)


# Default arguments
default_arguments = {
	'owner': 'sonador',
	'dependes_on_past': False,
	'start_date': days_ago(1),
	'retries': 1,
	'retry_delay': timedelta(minutes=1),
}


# Initialize SonadorExampleIndex DAG
dag = DAG('SonadorExample02-Index', default_args=default_arguments)


# Define task steps
l0 = PythonOperator(task_id='example02-verify-env', python_callable=verify_etl_env, dag=dag)
t1 = PythonOperator(task_id='example02-index-imagearchive', python_callable=sonador_index_imagearchive, dag=dag,
	depends_on_past=True, retries=2)
t2 = PythonOperator(task_id='example02-validate-indexop', python_callable=sonador_validate_indexop, dag=dag,
	depends_on_past=True, provide_context=True)
t3 = PythonOperator(task_id='example02-remove-series', python_callable=sonador_remove_series, dag=dag,
	depends_on_past=True, provide_context=True)


# Order tasks
l0.set_downstream(t1)
t1.set_downstream(t2)
t2.set_downstream(t3)
