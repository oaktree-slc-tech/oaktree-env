import logging, json
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
		'''	Retrieve the storage connection
		'''
		from airflow.models.connection import Connection
		from airflow.settings import Session

		# Retrieve S3 storage connection ID
		s3_conn = None

		# Retrieve connection from the database
		session = Session()
		try: s3_conn = session.query(Connection).filter(Connection.conn_id == self.conn_id).first()
		finally: session.close()

		if not s3_conn:
			raise ValueError('Unable to retrieve connection %s from databaase' % s3_conn_id)

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
		'''	Retrieve a list of available Object Storage connections
		'''
		from airflow.models.connection import Connection
		from airflow.settings import Session

		session = Session()

		try:
			# Query Airflow database for S3 connections
			connections = session.query(Connection).filter(Connection.conn_type==objects_conn_type)
			return [conn.conn_id for conn in connections]

		finally:
			session.close()

