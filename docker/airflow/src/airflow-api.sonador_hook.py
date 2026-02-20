import logging, json, os, traceback
from collections import namedtuple

from airflow.models import Variable
from airflow.sdk.bases.hook import BaseHook

from client.utils.conversion import str2bool
from client.utils.urls import build_url, validate_url
from client.utils.object import pick

from sonador.apisettings import SONADOR_URL, SONADOR_IMAGING_SERVER, \
	SONADOR_INTERNAL_DNS, SONADOR_VERIFY_SSL
from sonador.servers import SonadorServer

logger = logging.getLogger(__name__)


def _is_dag_parsing_context():
	'''Check if we're in DAG parsing context (not task execution)'''
	# In Airflow 3.0, during DAG parsing there's no task instance context
	try:
		from airflow.sdk.execution_time.supervisor import IS_SUPERVISOR_PROCESS
		return not IS_SUPERVISOR_PROCESS
	except ImportError:
		return False


SonadorAirflowConnectionParams = namedtuple('SonadorAirflowConnectionParams', (
	'url', 'apitoken', 'imageserver_uid', 'internal_dns', 'verify_ssl'
))


CONN_TYPE_GENERIC = 'generic'


class SonadorHook(BaseHook):
	'''	Integration hook to allow for interaction with Sonador
	'''
	sonador_conn_param = 'conn_id'

	def __init__(self, conn_id: str='sonador'):
		'''	Initialize Sonador hook
		'''
		super().__init__()
		self.conn_id = conn_id

	def get_connection(self, sonador_conn_param=None, **kwargs):
		'''	Retrieve the requested connection using Airflow 3.0 compatible method
		'''
		sonador_conn_param = sonador_conn_param or self.sonador_conn_param
		conn_id = kwargs.get(sonador_conn_param, self.conn_id)

		# Use BaseHook.get_connection which works in both Airflow 2.x and 3.x
		conn = BaseHook.get_connection(conn_id)

		if not conn:
			raise ValueError('Unable to retrieve connection %s from database' % conn_id)

		return conn

	def get_conn_params(self):
		'''	Retrieve the connection parameters from the airflow connection
		'''
		# Retrieve connection from the database
		conn = self.get_connection()
		
		# Build and validate URL
		sonador_url = build_url(
			getattr(conn, 'schema', None) or 'https', 
			'%s:%s' % (conn.host, getattr(conn, 'port', 443)))
		validate_url(sonador_url)

		# Sonador API token
		sonador_apitoken = getattr(conn, 'password', None)
		if not sonador_apitoken:
			raise ValueError('Invalid Sonador connection, no API token provided')

		# Parse extra parameters from connection instance
		conn_extra = getattr(conn, 'extra', {}) or {}
		if isinstance(conn_extra, str):
			conn_extra = json.loads(conn_extra)

		# Retrieve imaging server UID and DNS
		iserver_uid = conn_extra.get(SONADOR_IMAGING_SERVER)
		internal_dns = str2bool(conn_extra.get(SONADOR_INTERNAL_DNS, True))
		verify_ssl = str2bool(conn_extra.get(SONADOR_VERIFY_SSL, False))

		return SonadorAirflowConnectionParams(
			sonador_url, sonador_apitoken, iserver_uid, internal_dns, verify_ssl)

	def verify_etl_env(self):
		'''	Check connection parameters and log errors
		'''
		p = self.get_conn_params()

		if not p.url or not p.apitoken or not p.imageserver_uid:
			logger.error('Invalid Sonador configuration.\nURL: %s\nAPI Token: %s\nImage Server: %s' 
				% (p.url, p.apitoken, p.imageserver_uid))
			raise ValueError(('Invalid Sonador configuration. Please provide valid URL, API token, and image server ID. '
				+ 'Check connection configuration for conn="%s"') % self.conn_id)

	def init_sonador_imageserver(self):
		'''	Initialize Sonador imageserver instance
		'''
		p = self.get_conn_params()
		conn = SonadorServer(p.url, apitoken=p.apitoken, internal_dns=p.internal_dns, verify=p.verify_ssl)

		return conn.get_imageserver(p.imageserver_uid)

	@classmethod
	def hook_options(cls, sonador_conn_param=None, conf_param='dag_run', dag_params='params', **kwargs):
		'''	Retrieve hook options from the DAG conf
		'''
		# Retrieve Sonador options from DAG
		sonador_conn_param = sonador_conn_param or cls.sonador_conn_param
		conf = getattr(kwargs.get(conf_param, {}), 'conf', {})
		params = kwargs.get(dag_params, {})

		# Retrieve connection ID from parameters, fallback to conf
		hook_options = pick(params, (sonador_conn_param,))
		if not hook_options.get(sonador_conn_param):
			hook_options.update(pick(conf, (sonador_conn_param,)))

		return hook_options

	@classmethod
	def available_connections(cls, sonador_conn_type=CONN_TYPE_GENERIC, **kwargs):
		'''	Retrieve a list of available Sonador connections.

		In Airflow 3.0, direct database access during DAG parsing is not allowed.
		Falls back to environment variable SONADOR_CONNECTIONS (comma-separated list)
		or returns a default connection list.
		'''
		# First try environment variable (works in all contexts)
		env_connections = Variable.get('SONADOR_CONNECTIONS', default_var='')
		if env_connections:
			return [c.strip() for c in env_connections.split(',') if c.strip()]

		# Retrieve default connection
		default_conn = Variable.get('SONADOR_DEFAULT_CONN', default_var='sonador')
		return [default_conn]
