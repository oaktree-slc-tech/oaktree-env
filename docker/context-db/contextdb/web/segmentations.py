'''	Segmentation embedding REST API endpoints.
'''
import uuid, logging, sqlalchemy, traceback
from typing import List, Optional

from sqlalchemy.exc import DataError
from psycopg2.errors import DataException as PostgresDataException

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

from client import apisettings as client_api
from client.utils.object import pick
from client.errors import ClientOperationError

from sonador.apisettings import DCM_MODALITY_M3D, DCM_MODALITY_SEG

from sonador_fastapi.auth import check_dataservice_group, check_dataservice_user_group
from sonador_fastapi.db import apply_query_pagination, model_update_from_dict

from ..db.segmentations import InstanceSegmentationEmbedding, SeriesSegmentationEmbedding
from ..schemas.segmentations import InstanceSegmentationEmbeddingResponse, InstanceSegmentationEmbeddingSimilarityResponse, \
	SeriesSegmentationEmbeddingResponse, SeriesSegmentationEmbeddingSimilarityResponse, \
	SegmentationEmbeddingRequestAction, SegmentationEmbeddingSimilarityQuery

logger = logging.getLogger(__name__)



# -->  Segmentation API Helper Methods  <-- #


def fetch_segmentation_embeddings(DatabaseSession, EmbeddingDbModel, group, model_label, model_version, 
		segmentation_label=None, source=None, ground_truth=None, resource=None, page=100, items=100, **kwargs):
	'''	Retrieve segmentation embeddings for the provided embedding database model, model label, and model version.
	'''
	with DatabaseSession() as session:

		# Query series segmentation embeddings
		_query = session.query(EmbeddingDbModel).filter(EmbeddingDbModel.group == group) \
			.filter(EmbeddingDbModel.model_label == model_label) \
			.filter(EmbeddingDbModel.model_version == model_version)

		# Query by segmentation label, source, ground truth, and seg resource
		if segmentation_label:
			_query = _query.filter(EmbeddingDbModel.segmentation_label == segmentation_label)
		if source:
			_query = _query.filter(EmbeddingDbModel.source == source)
		if ground_truth:
			_query = _query.filter(EmbeddingDbModel.ground_truth == ground_truth)
		if resource:
			_query = _query.filter(EmbeddingDbModel.resource == resource)
			
		# Apply pagination
		_query = apply_query_pagination(_query, page=page, items=items)
		return _query.all()


def get_segmentation_embedding(app, iserver, DatabaseSession, EmbeddingDbModel, group, embedding_uid):
	'''	Retrieve segmentation embedding for the provided database model, group, and UID.

		@input DatabaseSession: sessionmaker class
		@input EmbeddingModel: embedding database model
		@input group (int): UID of group for which the embedding should be retrieved
		@input embedding_uid (str): UID of the embedding
	'''
	# Initialize 

	with DatabaseSession() as session:

		# Retrieve embedding from database
		_embedding_db = session.query(EmbeddingDbModel) \
			.filter(EmbeddingDbModel.group == group, EmbeddingDbModel.uid == embedding_uid).first()
		if not _embedding_db:
			raise HTTPException(status_code=client_api.STATUS_404, detail='embedding="%s" does not exist in %s' % (
				embedding_uid, app.title
			))

		# Verify segmentation resouce on Orthanc
		try: _dcm_resource = iserver.get_series(_embedding_db.resource)
		except ClientOperationError as err:

			# Raise 404 error and notify user that segmentation resource does not exist
			if getattr(err, 'http_code', None) and err.http_code == client_api.STATUS_404:
				raise HTTPException(status_code=client_api.STATUS_404,
					detail='Orphaned segmentation embedding. embedding="%s" series="%s" no longer saved in Orthanc medical databaase' % (
						embedding_uid, _embedding_db.resource,
					))

		return _embedding_db


def create_segmentation_embedding(app, DatabaseSession, EmbeddingDbModel, group, embedding):
	'''	Create a segmentation embedding instance for the provided model.
	'''
	with DatabaseSession() as session:

		# Add embedding to database
		_embedding_db = EmbeddingDbModel(uid=str(uuid.uuid4()), group=group, 
			**pick(embedding, ('model_label', 'model_version', 'embedding', 'source', 'resource', 'ground_truth',
				'segmentation_label', 'quality', 'dice', 'hausdorff', 'notes', 'misc')))
		session.add(_embedding_db)
		session.commit()

		session.refresh(_embedding_db)
		return _embedding_db


def update_segmentation_embedding(app, DatabaseSession, EmbeddingDbModel, group, embedding_uid, embedding):
	'''	Update a segmentation embedding instance for the provided model
	'''
	with DatabaseSession() as session:

		# Retrieve embedding from database
		_embedding_db = session.query(EmbeddingDbModel) \
			.filter(EmbeddingDbModel.group == group, EmbeddingDbModel.uid == embedding_uid).first()
		if not _embedding_db:
			raise HTTPException(status_code=client_api.STATUS_404, detail='embedding="%s" does not exist in %s' % (
				embedding_uid, app.title
			))

		# Update fields on database instance
		model_update_from_dict(_embedding_db, pick(
			embedding, ('model_label', 'model_version', 'embedding', 'source', 'resource', 'quality', 'dice', 'hausdorff', 
				'notes', 'misc')))
		session.commit()
		session.refresh(_embedding_db)

		return _embedding_db


def delete_segmentation_embedding(app, DatabaseSession, EmbeddingDbModel, group, embedding_uid):
	'''	Remove a segmentation embedding instance for the provided model
	'''
	with DatabaseSession() as session:

		# Retrieve embedding from database
		_embedding_db = session.query(EmbeddingDbModel) \
			.filter(EmbeddingDbModel.group == group, EmbeddingDbModel.uid == embedding_uid).first()
		if not _embedding_db:
			raise HTTPException(status_code=client_api.STATUS_404, detail='embedding="%s" does not exist in %s' % (
				embedding_uid, app.title
			))

		session.delete(_embedding_db)
		session.commit()			

		return JSONResponse(content=jsonable_encoder({
			'uid': embedding_uid,
			client_api.OPRESULT: 'delete-embedding',
			client_api.STATUS: client_api.SUCCESS,
		}))


def segmentation_similarity_search(DatabaseSession, EmbeddingModel, group, model_label, model_version, query, SimilarityResponseClass,
		segmentation_label=None, page=100, items=100):
	'''	Find the most similar segmentation embeddings to the provided query. Returns the most similar vectors via L2 distance.
	'''
	with DatabaseSession() as session:
			
		# Createa similarity operator to execute the query
		try: _op = EmbeddingModel.embedding.l2_distance(query.embedding)
		except (DataError, PostgresDataException, Exception) as err:
			
			logger.error('Unable to create similarity op due. Error: "%s"\n%s' % (err, traceback.format_exc()))
			raise HTTPException(status_code=client_api.STATUS_400,
				detail='Unable to create similarity operation due to an error', error='%s' % err)
		
		# Execute similarity search
		try:

			# Execute vector similarity search
			_vectors = session.query(EmbeddingModel, _op.label('distance')).filter(
		 		EmbeddingModel.group == group, EmbeddingModel.model_label == model_label,
		 		EmbeddingModel.model_version == model_version).order_by(_op)

			# Filter by segmentation label
			if segmentation_label:
				_vectors = _vectors.filter(EmbeddingModel.segmentation_label == segmentation_label)

			# Apply pagination
			_vectors = apply_query_pagination(_vectors, page=page, items=items)
		
		# Return error details to user
		except (DataError, PostgresDataException, Exception) as err:
			
			logger.error('Unable to execute similarity search for image segmentation embedding. Error: "%s"\n%s'
				% (err, traceback.format_exc()))
			raise HTTPException(status_code=client_api.STATUS_400, 
				detail='Unable to execute similarity search due to an error', error='%s' % err)

		# Unpack results for serialization
		return [SimilarityResponseClass(distance=_r[1], **pick(_r[0], 
				('uid', 'ctime', 'mtime', 'model_label', 'model_version', 'embedding', 'source', 'resource', 'ground_truth',
					'segmentation_label', 'quality', 'dice', 'hausdorff', 'notes', 'misc')))
			for _r in _vectors.all()]


# -->  Instance Segmentation (2D) API Endpoints  <-- #


def init_instance_segmentation_embedding_endpoints(app, sonador_dataservice_oidc, iserver, DatabaseSession):
	'''	Initialize Instance embedding REST API endpoints.

		@input app: FastAPI app instance
		@input iserver: Imaging server to be associated with the FastAPI app
		@input sonador_dataservice_oidc: Sonador dataservice OIDC client
		@input DatabaseSession: SQLAlchemy sessionmaker class
	'''


	@app.get('/embeddings/{group}/seg/instance/{model_label}/{model_version}', response_model=List[InstanceSegmentationEmbeddingResponse], 
		tags=['segmentations', 'instance'], summary='Retrieve instance segmentation embeddings')
	async def list_instance_seg_embeddings(request: Request, group: int, model_label: str, model_version: str,
			segmentation_label: Optional[str] = Query('', description='Filter by segmentation label'),
			source: Optional[str] = Query('', description='Filter by segmentation image source UID'),
			ground_truth: Optional[str] = Query('', description='Filter by segmentation ground truth'),
			resource: Optional[str] = Query('', description='Filter by segmentation resource UID'),
			page: Optional[int] = Query(1, ge=1, description="Page number"),
			items: Optional[int] = Query(100, ge=1, le=1000, description='Number of items per page'),
			user=Depends(sonador_dataservice_oidc.api_authtoken_check)):
		'''	List instance segmentation vector embeddings, optionally filtered by model_label and model_version.
		'''
		# Check dataservice to ensure that the requested group is associated. If not, return 404.
		# Dataservice is retrieved from Sonador to ensure that the group listing is current.
		check_dataservice_group(sonador_dataservice_oidc.dataservice, group, app_label=app.title)

		# Ensure that the user is a member of the group. If not, return 403.
		check_dataservice_user_group(sonador_dataservice_oidc.dataservice, group, user, app_label=app.title)

		# Retrieve instance (2D) segmentation embeddings
		return fetch_segmentation_embeddings(DatabaseSession, InstanceSegmentationEmbedding, group, model_label, model_version,
			segmentation_label=segmentation_label, source=source, ground_truth=ground_truth, resource=resource,
			page=page, items=items)


	@app.post('/embeddings/{group}/seg/instance', status_code=201, summary='Create image instance (2D) segmentation embedding', 
			response_model=InstanceSegmentationEmbeddingResponse, tags=['segmentations', 'instance'])
	async def create_instance_seg_embedding(request: Request, group: int, embedding: SegmentationEmbeddingRequestAction,
			user=Depends(sonador_dataservice_oidc.api_authtoken_check)):
		'''	Create a vector embedding for a instance (2D) segmentation
		'''
		# Check dataservice to ensure that the request group is associated with the app (404) and the user is a member (403)
		check_dataservice_group(sonador_dataservice_oidc.dataservice, group, app_label=app.title)
		check_dataservice_user_group(sonador_dataservice_oidc.dataservice, group, user, app_label=app.title)

		# Check imaging source, segmentation instance, and ground truth to ensure they exist
		# and that the user has access to them.
		try: _dcm_source = iserver.get_dcm_instance(embedding.source)
		except ClientOperationError as err:
			
			# Raise 404 error and notify user that source imaging does not exist
			if getattr(err, 'http_code', None) and err.http_code == client_api.STATUS_404:
				raise HTTPException(status_code=client_api.STATUS_404, 
					detail='Source imaging series="%s" does not exist' % embedding.source)
			
			raise err

		try: _sx_seg = iserver.get_series(embedding.resource)
		except ClientOperationError as err:

			# Raise 404 error and notify user that segmentation resource does not exist
			if getattr(err, 'http_code', None) and err.http_code == client_api.STATUS_404:
				raise HTTPException(status_code=client_api.STATUS_404,
					detail='Segmentation series="%s" does not exist' % embedding.resource)

		# Ensure that the segmentation resource is M3D or SEg
		if not _sx_seg.modality in (DCM_MODALITY_SEG, DCM_MODALITY_M3D):
			raise HTTPException(status_code=client_api.STATUS_400, 
				detail='Invalid segmentation series=%s modality=%s' % (_sx_seg.pk, _sx_seg.modality))

		try: _sx_gold = iserver.get_series(embedding.ground_truth)
		except ClientOperationError as err:

			# Raise 404 error and notify user that ground truth resource does not exist
			if getattr(err, 'http_code', None) and err.http_code == client_api.STATUS_404:
				raise HTTPException(status_code=client_api.STATUS_404,
					detail='Ground truth segmentation series series="%s" does not exist' % embedding.ground_truth)

		# Ensure that the segmentation ground truth is M3D or SEG
		if not _sx_gold.modality in (DCM_MODALITY_SEG, DCM_MODALITY_M3D):
			raise HTTPException(status_code=client_api.STATUS_400, 
				detail='Invalid ground-truth segmentation series=%s modality=%s' % (_sx_seg.pk, _sx_seg.modality))

		# Create new instance embedding
		return create_segmentation_embedding(app, DatabaseSession, InstanceSegmentationEmbedding, group, embedding)

	
	@app.get('/embeddings/{group}/seg/instance/{uid}', summary='Retrieve details for image instance (2D) segmentation embedding',
		response_model=InstanceSegmentationEmbeddingResponse, tags=['segmentations', 'instance'])
	async def get_seg_embedding(request: Request, group: int, uid: str, user=Depends(sonador_dataservice_oidc.api_authtoken_check)):
		'''	Retrieve details for an instance segmentation embedding
		'''
		# Check dataservice to ensure that the request group is associated with the app (404) and the user is a member (403)
		check_dataservice_group(sonador_dataservice_oidc.dataservice, group, app_label=app.title)
		check_dataservice_user_group(sonador_dataservice_oidc.dataservice, group, user, app_label=app.title)

		# Create image server instance for user request


		return get_segmentation_embedding(app, iserver, DatabaseSession, InstanceSegmentationEmbedding, group, uid)

	
	@app.put('/embeddings/{group}/seg/instance/{uid}', summary='Update image instance (2D) segmentation embedding',
			response_model=InstanceSegmentationEmbeddingResponse, tags=['segmentations', 'instance'])
	def update_seg_embedding(request: Request, group: int, uid: str, embedding: SegmentationEmbeddingRequestAction,
			user=Depends(sonador_dataservice_oidc.api_authtoken_check)):
		'''	Update series segmentation embedding
		'''
		# Check dataservice to ensure that the request group is associated with the app (404) and the user is a member (403)
		check_dataservice_group(sonador_dataservice_oidc.dataservice, group, app_label=app.title)
		check_dataservice_user_group(sonador_dataservice_oidc.dataservice, group, user, app_label=app.title)

		return update_segmentation_embedding(app, DatabaseSession, InstanceSegmentationEmbedding, group, uid, embedding)


	@app.delete('/embeddings/{group}/seg/instance/{uid}', summary='Delete image instance (2D) segmentation embedding', 
			status_code=204, tags=['segmentations', 'instance'])
	async def delete_seg_embedding(request: Request, group: int, uid: str, user=Depends(sonador_dataservice_oidc.api_authtoken_check)):
		'''	Delete series segmentation embedding
		'''
		# Check dataservice to ensure that the request group is associated with the app (404) and user is a member (403)
		check_dataservice_group(sonador_dataservice_oidc.dataservice, group, app_label=app.title)
		check_dataservice_user_group(sonador_dataservice_oidc.dataservice, group, user, app_label=app.title)

		return delete_segmentation_embedding(app, DatabaseSession, InstanceSegmentationEmbedding, group, uid)

	@app.post('/embeddings/{group}/seg/instance/{model_label}/{model_version}/search',
			summary='Perform similarity search for image instance (2D) segmentation embeddings', 
			tags=['segmentations', 'instance', 'ai'], response_model=List[SeriesSegmentationEmbeddingSimilarityResponse])
	async def seg_embedding_similarity_search(request: Request, group: int, model_label: str, model_version: str,
			query: SegmentationEmbeddingSimilarityQuery, 
			page: Optional[int] = Query(1, ge=1, description="Page number"),
			items: Optional[int] = Query(100, ge=1, le=1000, description='Number of items per page'),
			user=Depends(sonador_dataservice_oidc.api_authtoken_check)):
		'''	Find the most similar instance embeddings to the provided query. Returns the most similar vectors via L2 distance.
		'''
		# Check dataservice to ensure that the request group is associated with the app (404) and user is a membe (403)
		check_dataservice_group(sonador_dataservice_oidc.dataservice, group, app_label=app.title)
		check_dataservice_user_group(sonador_dataservice_oidc.dataservice, group, user, app_label=app.title)

		return segmentation_similarity_search(DatabaseSession, InstanceSegmentationEmbedding, group, model_label, model_version,
			query, InstanceSegmentationEmbeddingSimilarityResponse, segmentation_label=query.segmentation_label, 
			page=page, items=items)



# -->  Series Segmentation (3D) API Endpoints  <-- #


def init_series_segmentation_embedding_endpoints(app, sonador_dataservice_oidc, iserver, DatabaseSession):
	'''	Initialize Segmentation embedding REST API endpoints.

		@input app: FastAPI app instance
		@input iserver: Imaging server to be associated with the FastAPI app
		@input sonador_dataservice_oidc: Sonador dataservice OIDC client
		@input DatabaseSession: SQLAlchemy sessionmaker class
	'''

	
	@app.get('/embeddings/{group}/seg/series/{model_label}/{model_version}', response_model=List[SeriesSegmentationEmbeddingResponse], 
		tags=['segmentations', 'series'], summary='Retrieve image series (3D) segmentation embeddings')
	async def list_seg_embeddings(request: Request, group: int, model_label: str, model_version: str,
			segmentation_label: Optional[str] = Query('', description='Filter by segmentation label'),
			source: Optional[str] = Query('', description='Filter by segmentation image source UID'),
			ground_truth: Optional[str] = Query('', description='Filter by segmentation ground truth'),
			resource: Optional[str] = Query('', description='Filter by segmentation resource UID'),
			page: Optional[int] = Query(1, ge=1, description="Page number"),
			items: Optional[int] = Query(100, ge=1, le=1000, description='Number of items per page'),
			user=Depends(sonador_dataservice_oidc.api_authtoken_check)):
		'''	List series segmentation vector embeddings, optionally filtered by model_label and model_version.
		'''
		# Check dataservice to ensure that the requested group is associated. If not, return 404.
		# Dataservice is retrieved from Sonador to ensure that the group listing is current.
		check_dataservice_group(sonador_dataservice_oidc.dataservice, group, app_label=app.title)

		# Ensure that the user is a member of the group. If not, return 403.
		check_dataservice_user_group(sonador_dataservice_oidc.dataservice, group, user, app_label=app.title)

		# Retrieve instance (3D) segmentation embeddings
		return fetch_segmentation_embeddings(DatabaseSession, SeriesSegmentationEmbedding, model_label, model_version,
			segmentation_label=segmentation_label, source=source, resource=resource, ground_truth=ground_truth,
			page=page, items=items)

	
	@app.post('/embeddings/{group}/seg/series', status_code=201, summary='Create image series (3D) segmentation embedding', 
			response_model=SeriesSegmentationEmbeddingResponse, tags=['segmentations', 'series'])
	async def create_seg_embedding(request: Request, group: int, embedding: SegmentationEmbeddingRequestAction,
			user=Depends(sonador_dataservice_oidc.api_authtoken_check)):
		'''	Create a vector embedding for a series segmentation
		'''
		# Check dataservice to ensure that the request group is associated with the app (404) and the user is a member (403)
		check_dataservice_group(sonador_dataservice_oidc.dataservice, group, app_label=app.title)
		check_dataservice_user_group(sonador_dataservice_oidc.dataservice, group, user, app_label=app.title)

		# Check imaging source and segmentation instance to ensure they exist
		# and that the user has access to them.
		try: _sx_source = iserver.get_series(embedding.source)
		except ClientOperationError as err:
			
			# Raise 404 error and notify user that source imaging does not exist
			if getattr(err, 'http_code', None) and err.http_code == client_api.STATUS_404:
				raise HTTPException(status_code=client_api.STATUS_404, 
					detail='Source imaging series="%s" does not exist' % embedding.source)
			
			raise err

		try: _sx_seg = iserver.get_series(embedding.resource)
		except ClientOperationError as err:

			# Raise 404 error and notify user that segmentation resource does not exist
			if getattr(err, 'http_code', None) and err.http_code == client_api.STATUS_404:
				raise HTTPException(status_code=client_api.STATUS_404,
					detail='Segmentation series="%s" does not exist' % embedding.resource)

		# Ensure that the segmentation resource is M3D or SEg
		if not _sx_seg.modality in (DCM_MODALITY_SEG, DCM_MODALITY_M3D):
			raise HTTPException(status_code=client_api.STATUS_400, 
				detail='Invalid segmentation series=%s modality=%s' % (_sx_seg.pk, _sx_seg.modality))

		try: _sx_gold = iserver.get_series(embedding.ground_truth)
		except ClientOperationError as err:

			# Raise 404 error and notify user that ground truth resource does not exist
			if getattr(err, 'http_code', None) and err.http_code == client_api.STATUS_404:
				raise HTTPException(status_code=client_api.STATUS_404,
					detail='Ground truth segmentation series series="%s" does not exist' % embedding.ground_truth)

		# Ensure that the segmentation resource is M3D or SEg
		if not _sx_gold.modality in (DCM_MODALITY_SEG, DCM_MODALITY_M3D):
			raise HTTPException(status_code=client_api.STATUS_400, 
				detail='Invalid ground-truth segmentation series=%s modality=%s' % (_sx_seg.pk, _sx_seg.modality))

		# Create new series embedding
		return create_segmentation_embedding(app, DatabaseSession, SeriesSegmentationEmbedding, group, embedding)

	
	@app.get('/embeddings/{group}/seg/series/{uid}', summary='Retrieve details for image series (3D) segmentation embedding',
		response_model=SeriesSegmentationEmbeddingResponse, tags=['segmentations', 'series'])
	async def get_seg_embedding(request: Request, group: int, uid: str, user=Depends(sonador_dataservice_oidc.api_authtoken_check)):
		'''	Retrieve details for a series segmentation embedding
		'''
		# Check dataservice to ensure that the request group is associated with the app (404) and the user is a member (403)
		check_dataservice_group(sonador_dataservice_oidc.dataservice, group, app_label=app.title)
		check_dataservice_user_group(sonador_dataservice_oidc.dataservice, group, user, app_label=app.title)

		return get_segmentation_embedding(app, iserver, DatabaseSession, SeriesSegmentationEmbedding, group, uid)

	
	@app.put('/embeddings/{group}/seg/series/{uid}', summary='Update image series (3D) segmentation embedding',
			response_model=SeriesSegmentationEmbeddingResponse, tags=['segmentations', 'series'])
	def update_seg_embedding(request: Request, group: int, uid: str, embedding: SegmentationEmbeddingRequestAction,
			user=Depends(sonador_dataservice_oidc.api_authtoken_check)):
		'''	Update series segmentation embedding
		'''
		# Check dataservice to ensure that the request group is associated with the app (404) and the user is a member (403)
		check_dataservice_group(sonador_dataservice_oidc.dataservice, group, app_label=app.title)
		check_dataservice_user_group(sonador_dataservice_oidc.dataservice, group, user, app_label=app.title)

		return update_segmentation_embedding(DatabaseSession, SeriesSegmentationEmbedding, group, uid, embedding)

	
	@app.delete('/embeddings/{group}/seg/series/{uid}', summary='Delete image series (3D) segmentation embedding', 
			status_code=204, tags=['segmentations', 'series'])
	async def delete_seg_embedding(request: Request, group: int, uid: str, user=Depends(sonador_dataservice_oidc.api_authtoken_check)):
		'''	Delete series segmentation embedding
		'''
		# Check dataservice to ensure that the request group is associated with the app (404) and user is a member (403)
		check_dataservice_group(sonador_dataservice_oidc.dataservice, group, app_label=app.title)
		check_dataservice_user_group(sonador_dataservice_oidc.dataservice, group, user, app_label=app.title)

		return delete_segmentation_embedding(app, DatabaseSession, SeriesSegmentationEmbedding, group, uid)

	
	@app.post('/embeddings/{group}/seg/series/{model_label}/{model_version}/search',
			summary='Perform similarity search for image series (3D) segmentation embeddings', 
			tags=['segmentations', 'series', 'ai'], response_model=List[SeriesSegmentationEmbeddingSimilarityResponse])
	async def seg_embedding_similarity_search(request: Request, group: int, model_label: str, model_version: str,
			query: SegmentationEmbeddingSimilarityQuery, 
			page: Optional[int] = Query(1, ge=1, description="Page number"),
			items: Optional[int] = Query(100, ge=1, le=1000, description='Number of items per page'),
			user=Depends(sonador_dataservice_oidc.api_authtoken_check)):
		'''	Find the most similar series embeddings to the provided query. Returns the most similar vectors via L2 distance.
		'''
		# Check dataservice to ensure that the request group is associated with the app (404) and user is a membe (403)
		check_dataservice_group(sonador_dataservice_oidc.dataservice, group, app_label=app.title)
		check_dataservice_user_group(sonador_dataservice_oidc.dataservice, group, user, app_label=app.title)

		return segmentation_similarity_search(DatabaseSession, SeriesSegmentationEmbedding, group, model_label, model_version,
			query, SeriesSegmentationEmbeddingSimilarityResponse, segmentation_label=query.segmentation_label, 
			page=page, items=items)
