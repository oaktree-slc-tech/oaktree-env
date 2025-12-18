import logging, json, os
from collections import namedtuple

from airflow.hooks.base import BaseHook

from client.utils.conversion import str2bool
from client.utils.object import pick

logger = logging.getLogger(__name__)


S3_CONN_PARAM = 's3_conn_id'
S3_CONN_ROOT_PARAM = 's3_root'
S3_CONN_ROOT_DEFAULT = 's3://airflow'
CONN_TYPE_S3 = 'aws'


ObjectConnectionParams = namedtuple(
	'TotalSegmentatorS3ConnectionParams',  ('conn_id', 'root'))


class ObjectStorageHook(BaseHook):
	'''	Integration hook to allow for storage of binary data within object storage
	'''
	objects_conn_param = 's3_conn_id'
	objects_conn_root_param = S3_CONN_ROOT_PARAM
	objects_conn_root_default = S3_CONN_ROOT_DEFAULT

	def __init__(self, conn_id: str='s3_default', **kwargs):
		'''	Initialize objects hook
		'''
		super().__init__()
		self.conn_id = conn_id
		self.objects_conn_root_param = kwargs.get('objects_conn_root_param', self.objects_conn_root_param)
		self.objects_conn_root_default = kwargs.get('objects_conn_root_default', self.objects_conn_root_default)

	def get_connection(self, **kwargs):
		'''	Retrieve the storage connection using Airflow 3.0 compatible method
		'''
		# Use BaseHook.get_connection which works in both Airflow 2.x and 3.x
		s3_conn = BaseHook.get_connection(self.conn_id)

		if not s3_conn:
			raise ValueError('Unable to retrieve connection %s from database' % self.conn_id)

		return s3_conn

	def get_storage_params(self, **kwargs):
		'''	Retrieve storage connection parameters
		'''
		s3_conn = self.get_connection(**kwargs)

		# Retrieve default storage bucket/key-prefix
		s3_conn_extra = getattr(s3_conn, 'extra', {}) or {}
		if isinstance(s3_conn_extra, str):
			s3_conn_extra = json.loads(s3_conn_extra)

		# Retrieve S3 root (back-fill with default if no root defined in connection)
		s3_root = s3_conn_extra.get(self.objects_conn_root_param, self.objects_conn_root_default)
	
		return ObjectConnectionParams(s3_conn.conn_id, s3_root)

	@classmethod
	def hook_options(cls, objects_conn_param=None, conf_param='dag_run', dag_params='params', **kwargs):
		'''	Retrieve the storage connection ID
		'''
		objects_conn_param = objects_conn_param or cls.objects_conn_param
		s3_conn_id = kwargs.get(objects_conn_param)

		conf = getattr(kwargs.get(conf_param, {}), 'conf', {})
		params = kwargs.get(dag_params, {})

		# Retrive connection name from parameters, fallback to conf
		s3_conn_id = s3_conn_id or params.get(objects_conn_param)
		if not s3_conn_id:
			s3_conn_id = conf.get(objects_conn_param)

		if not s3_conn_id:
			raise ValueError('Unable to retrieve S3 connection ID from DAG parameters or configuration')

		return { 'conn_id': s3_conn_id }

	@classmethod
	def available_connections(cls, objects_conn_type=CONN_TYPE_S3, **kwargs):
		'''	Retrieve a list of available Object Storage connections.

		In Airflow 3.0, direct database access during DAG parsing is not allowed.
		Falls back to environment variable S3_CONNECTIONS (comma-separated list)
		or returns a default connection list.
		'''
		# First try environment variable (works in all contexts)
		env_connections = os.environ.get('S3_CONNECTIONS', '')
		if env_connections:
			return [c.strip() for c in env_connections.split(',') if c.strip()]

		# Try database access (only works during task execution in Airflow 3.0)
		try:
			from airflow.models.connection import Connection
			from airflow.settings import Session

			session = Session()
			try:
				connections = session.query(Connection).filter(Connection.conn_type==objects_conn_type)
				return [conn.conn_id for conn in connections]
			finally:
				session.close()

		except RuntimeError as e:
			# Airflow 3.0: Direct database access not allowed during DAG parsing
			logger.debug('Database access not available during DAG parsing: %s', e)
			default_conn = os.environ.get('S3_DEFAULT_CONN', 's3_default')
			return [default_conn]

