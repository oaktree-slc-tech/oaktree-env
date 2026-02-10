# ETL Example 2: Index Medical Images
# This example shows the use of the AirFlow Python operator in order
# to retrieve, index, verify, and remove a medical imaging scan.
import logging, requests, re, fnmatch, zipfile
from datetime import datetime, timedelta
from io import BytesIO

from airflow import DAG
from airflow.providers.standard.operators.bash import BashOperator
from airflow.providers.standard.operators.python import PythonOperator
from airflow.models import Variable
from airflow.sdk import Param

from client.utils.conversion import str2bool
from client.utils.object import pick

from sonador.apisettings import SONADOR_URL, SONADOR_APITOKEN, SONADOR_INTERNAL_DNS, \
	SONADOR_IMAGING_SERVER
from sonador.servers import SonadorServer
from sonador.apisettings import DCM_EXTENSIONS_DEFAULT, IMAGING_SERVER_RESOURCE_SERIES
from sonador.tasks.uploads import imageserver_upload_archive

from sonador_hook import SonadorHook

logger = logging.getLogger(__name__)


DCM_FPATTERNS = [re.compile(fnmatch.translate(p)) for p in DCM_EXTENSIONS_DEFAULT]
ARCHIVE_URL_DEFAULT =  'https://oak-tree.tech/documents/156/example.lung-ct.volume-3d.zip'


def verify_etl_env(**kwargs):
	'''	Log the command context to stderr
	'''
	hook = SonadorHook(**SonadorHook.hook_options(**kwargs))
	hook.verify_etl_env()
	iserver = hook.init_sonador_imageserver()


def sonador_index_imagearchive(**kwargs):
	'''	Download and index an image archive file
	'''
	iserver = SonadorHook(**SonadorHook.hook_options(**kwargs)).init_sonador_imageserver()

	# Retrieve ZIP archive from website
	ctzip = zipfile.ZipFile(
		BytesIO(requests.get(
			Variable.get('SONADOR_EXAMPLE02_ARCHIVEURL', default_var=ARCHIVE_URL_DEFAULT)).content))

	hcache, fcount = imageserver_upload_archive(iserver, ctzip)

	for mkey, dmeta in hcache.items():
		logger.info('Uploaded %s successfully to %s. Metadata: %s.' % (mkey, iserver.server_label, dmeta))

	return [pick(m, ('uid', 'resource', 'header')) for m in hcache if m.resource == IMAGING_SERVER_RESOURCE_SERIES]


def sonador_validate_indexop(**context):
	'''	Verify that images from the previous step were indexed correctly
	'''
	iserver = SonadorHook(**SonadorHook.hook_options(**context)).init_sonador_imageserver()
	ti = context['ti']

	# Retrieve upload results from indexing operation
	tdata = ti.xcom_pull(task_ids='example02-index-imagearchive') or {}

	# Iterate through items, verify upload, and pass along validated list of uploaded series
	sindex_validated = []

	for sdata in tdata:

		# Verify that upload was successful
		sresults = iserver.query({ sdata.get('header'): sdata.get('uid') })
		if len(sresults) > 0:

			# Retrieve series from Orthanc and compare the upload results from the previous
			# task to the number of indexed slices
			s = sresults[0]
			upload_verified = len(s.slices) > 0
			logger.info('Series %s: uploaded=%d match=%s' % (s.pk, len(s.slices), upload_verified))

			if upload_verified:
				sindex_validated.append(s.pk)

	return sindex_validated


def sonador_remove_series(**context):
	'''	Remove images indexed by the pipeline
	'''
	iserver = SonadorHook(**SonadorHook.hook_options(**context)).init_sonador_imageserver()
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
	'depends_on_past': False,
	'start_date': datetime(2024, 1, 1),
	'retries': 1,
	'retry_delay': timedelta(minutes=1),
}


# Retrieve available connections from the database
available_sonador_connections = SonadorHook.available_connections()


# Initialize SonadorExampleIndex DAG
dag = DAG('SonadorExample02-Index', default_args=default_arguments, params={
		'conn_id': Param(type='string', enum=available_sonador_connections, 
			default=available_sonador_connections[0] if available_sonador_connections else None),
	})


# Define task steps
l0 = PythonOperator(task_id='example02-verify-env', python_callable=verify_etl_env, dag=dag)
t1 = PythonOperator(task_id='example02-index-imagearchive', python_callable=sonador_index_imagearchive, dag=dag,
	depends_on_past=True, retries=2)
t2 = PythonOperator(task_id='example02-validate-indexop', python_callable=sonador_validate_indexop, dag=dag,
	depends_on_past=True)
t3 = PythonOperator(task_id='example02-remove-series', python_callable=sonador_remove_series, dag=dag,
	depends_on_past=True)


# Order tasks
l0.set_downstream(t1)
t1.set_downstream(t2)
t2.set_downstream(t3)
