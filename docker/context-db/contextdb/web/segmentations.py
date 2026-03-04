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

from ..db.segmentations import SeriesSegmentationEmbedding
from ..schemas.segmentations import SeriesSegmentationEmbeddingResponse, SeriesSegmentationEmbeddingRequestAction, \
	SeriesSegmentationEmbeddingSimilarityQuery, SeriesSegmentationEmbeddingSimilarityResponse

logger = logging.getLogger(__name__)



def init_segmentation_embedding_endpints(app, sonador_dataservice_oidc, iserver, DatabaseSession):
	'''	Initialize Segmentation embedding REST API endpoints.

		@input app: FastAPI app instance
		@input iserver: Imaging server to be associated with the FastAPI app
		@input sonador_dataservice_oidc: Sonador dataservice OIDC client
	'''

	
	@app.get('/embeddings/{group}/seg/{model_label}/{model_version}', response_model=List[SeriesSegmentationEmbeddingResponse], 
		tags=['segmentations'], summary='Retrieve image segmentation embeddings')
	async def list_seg_embeddings(request: Request, group: int, model_label: str, model_version: str,
			page: Optional[int] = Query(1, ge=1, description="Page number"),
			items: Optional[int] = Query(100, ge=1, le=1000, description='Number of items per page'),
			user=Depends(sonador_dataservice_oidc.api_authtoken_check)):
		'''	List segmentation vector embeddings, optionally filtered by model_label and model_version.
		'''
		# Check dataservice to ensure that the requested group is associated. If not, return 404.
		# Dataservice is retrieved from Sonador to ensure that the group listing is current.
		check_dataservice_group(sonador_dataservice_oidc.dataservice, group, app_label=app.title)

		# Ensure that the user is a member of the group. If not, return 403.
		check_dataservice_user_group(sonador_dataservice_oidc.dataservice, group, user, app_label=app.title)

		with DatabaseSession() as session:

			# Query series segmentation embeddings
			_query = session.query(SeriesSegmentationEmbedding).filter(
				SeriesSegmentationEmbedding.model_label == model_label).filter(
				SeriesSegmentationEmbedding.model_version == model_version)
				
			# Apply pagination
			_query = apply_query_pagination(_query, page=page, items=items)
			return _query.all()

	
	@app.post('/embeddings/{group}/seg', status_code=201, summary='Create image segmentation embedding', 
			response_model=SeriesSegmentationEmbeddingResponse, tags=['segmentations'])
	async def create_seg_embedding(request: Request, group: int, embedding: SeriesSegmentationEmbeddingRequestAction,
			user=Depends(sonador_dataservice_oidc.api_authtoken_check)):
		'''	Create a vector embedding for a segmentation
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

		# Create new VectorItem
		with DatabaseSession() as session:

			# Add embedding to database
			_embedding_db = SeriesSegmentationEmbedding(uid=str(uuid.uuid4()), group=group, 
				**pick(embedding, ('model_label', 'model_version', 'embedding', 'source', 'resource', 
					'quality', 'dice', 'hausdorff', 'notes')))
			session.add(_embedding_db)
			session.commit()

			session.refresh(_embedding_db)
			return _embedding_db

	
	@app.get('/embeddings/{group}/seg/{uid}', summary='Retrieve details for image segmentation embedding',
		response_model=SeriesSegmentationEmbeddingResponse, tags=['segmentations'])
	async def get_seg_embedding(request: Request, group: int, uid: str, user=Depends(sonador_dataservice_oidc.api_authtoken_check)):
		'''	Retrieve image segmentation embedding
		'''
		# Check dataservice to ensure that the request group is associated with the app (404) and the user is a member (403)
		check_dataservice_group(sonador_dataservice_oidc.dataservice, group, app_label=app.title)
		check_dataservice_user_group(sonador_dataservice_oidc.dataservice, group, user, app_label=app.title)

		with DatabaseSession() as session:

			# Retrieve embedding from database
			_embedding_db = session.query(SeriesSegmentationEmbedding) \
				.filter(SeriesSegmentationEmbedding.group == group, SeriesSegmentationEmbedding.uid == uid).first()
			if not _embedding_db:
				raise HTTPException(status_code=client_api.STATUS_404, detail='embedding="%s" does not exist in %s' % (
					uid, app.title
				))

			# Verify segmentation resouce on Orthanc
			try: _sx_seg = iserver.get_series(_embedding_db.resource)
			except ClientOperationError as err:

				# Raise 404 error and notify user that segmentation resource does not exist
				if getattr(err, 'http_code', None) and err.http_code == client_api.STATUS_404:
					raise HTTPException(status_code=client_api.STATUS_404,
						detail='Orphaned segmentation embedding. embedding="%s" series="%s" no longer saved in Orthanc medical databaase' % (
							uid, _embedding_db.resource,
						))

			return _embedding_db

	
	@app.put('/embeddings/{group}/seg/{uid}', summary='Update image segmentation embedding',
			response_model=SeriesSegmentationEmbeddingResponse, tags=['segmentations'])
	def update_seg_embedding(request: Request, group: int, uid: str, embedding: SeriesSegmentationEmbeddingRequestAction,
			user=Depends(sonador_dataservice_oidc.api_authtoken_check)):
		'''	Update image segmentation embedding
		'''
		# Check dataservice to ensure that the request group is associated with the app (404) and the user is a member (403)
		check_dataservice_group(sonador_dataservice_oidc.dataservice, group, app_label=app.title)
		check_dataservice_user_group(sonador_dataservice_oidc.dataservice, group, user, app_label=app.title)

		with DatabaseSession() as session:

			# Retrieve embedding from database
			_embedding_db = session.query(SeriesSegmentationEmbedding) \
				.filter(SeriesSegmentationEmbedding.group == group, SeriesSegmentationEmbedding.uid == uid).first()
			if not _embedding_db:
				raise HTTPException(status_code=client_api.STATUS_404, detail='embedding="%s" does not exist in %s' % (
					uid, app.title
				))

			# Update fields on database instance
			model_update_from_dict(_embedding_db, pick(
				embedding, ('model_label', 'model_version', 'embedding', 'source', 'resource', 'quality', 'dice', 'hausdorff', 'notes')))
			session.commit()
			session.refresh(_embedding_db)

			return _embedding_db

	
	@app.delete('/embeddings/{group}/seg/{uid}', summary='Delete image segmentation embedding', status_code=204, tags=['segmentations'])
	async def delete_seg_embedding(request: Request, group: int, uid: str, user=Depends(sonador_dataservice_oidc.api_authtoken_check)):
		'''	Delete image segmentation embedding
		'''
		# Check dataservice to ensure that the request group is associated with the app (404) and user is a member (403)
		check_dataservice_group(sonador_dataservice_oidc.dataservice, group, app_label=app.title)
		check_dataservice_user_group(sonador_dataservice_oidc.dataservice, group, user, app_label=app.title)

		with DatabaseSession() as session:

			# Retrieve embedding from database
			_embedding_db = session.query(SeriesSegmentationEmbedding) \
				.filter(SeriesSegmentationEmbedding.group == group, SeriesSegmentationEmbedding.uid == uid).first()
			if not _embedding_db:
				raise HTTPException(status_code=client_api.STATUS_404, detail='embedding="%s" does not exist in %s' % (
					uid, app.title
				))

			session.delete(_embedding_db)
			session.commit()			

			return JSONResponse(content=jsonable_encoder({
				'uid': uid,
				client_api.OPRESULT: 'delete-embedding',
				client_api.STATUS: client_api.SUCCESS,
			}))

	
	@app.post('/embeddings/{group}/seg/{model_label}/{model_version}/search',
			summary='Perform similarity search for image segmentation embeddings', 
			tags=['segmentations', 'ai'], response_model=List[SeriesSegmentationEmbeddingSimilarityResponse])
	async def seg_embedding_similarity_search(request: Request, group: int, model_label: str, model_version: str,
			query: SeriesSegmentationEmbeddingSimilarityQuery, 
			page: Optional[int] = Query(1, ge=1, description="Page number"),
			items: Optional[int] = Query(100, ge=1, le=1000, description='Number of items per page'),
			user=Depends(sonador_dataservice_oidc.api_authtoken_check)):
		'''	Find the most similar vectors to the provided query. Returns the most similar vectors with L2 distance.
		'''
		# Check dataservice to ensure that the request group is associated with the app (404) and user is a membe (403)
		check_dataservice_group(sonador_dataservice_oidc.dataservice, group, app_label=app.title)
		check_dataservice_user_group(sonador_dataservice_oidc.dataservice, group, user, app_label=app.title)

		with DatabaseSession() as session:
			
			# Createa similarity operator to execute the query
			try: _op = SeriesSegmentationEmbedding.embedding.l2_distance(query.embedding)
			except (DataError, PostgresDataException, Exception) as err:
				
				logger.error('Unable to create similarity op due. Error: "%s"\n%s' % (err, traceback.format_exc()))
				raise HTTPException(status_code=client_api.STATUS_400,
					detail='Unable to create similarity operation due to an error', error='%s' % err)
			
			# Execute similarity search
			try:
				_vectors = session.query(SeriesSegmentationEmbedding, _op.label('distance')).filter(
			 		SeriesSegmentationEmbedding.group == group, SeriesSegmentationEmbedding.model_label == model_label,
			 		SeriesSegmentationEmbedding.model_version == model_version).order_by(_op)
				_vectors = apply_query_pagination(_vectors, page=page, items=items)
			
			# Return error details to user
			except (DataError, PostgresDataException, Exception) as err:
				
				logger.error('Unable to execute similarity search for image segmentation embedding. Error: "%s"\n%s'
					% (err, traceback.format_exc()))
				raise HTTPException(status_code=client_api.STATUS_400, 
					detail='Unable to execute similarity search due to an error', error='%s' % err)

			# Unpack results for serialization
			return [SeriesSegmentationEmbeddingSimilarityResponse(distance=_r[1], **pick(_r[0], 
					('uid', 'ctime', 'mtime', 'model_label', 'model_version', 'embedding', 'source', 'resource', 
						'quality', 'dice', 'hausdorff', 'notes')))
				for _r in _vectors.all()]

