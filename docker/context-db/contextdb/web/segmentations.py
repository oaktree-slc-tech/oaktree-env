'''	Segmentation embedding REST API endpoints.
'''
import uuid, logging
from typing import List, Optional
from fastapi import Depends, HTTPException, Query, Request

from client.utils.object import pick

from sonador_fastapi.db import apply_query_pagination

from ..db.segmentations import SeriesSegmentationEmbedding
from ..schemas.base import EmbeddingResponse

logger = logging.getLogger(__name__)



def init_segmentation_embedding_endpints(app, sonador_dataservice_oidc, DatabaseSession):
	'''	Initialize Segmentation embedding REST API endpoints.

		@input app: FastAPI app instance
		@input sonador_dataservice_oidc: Sonador dataservice OIDC client
	'''

	@app.get('/embeddings/seg', response_model=List[EmbeddingResponse], tags=['embeddings'])
	async def list_seg_embeddings(request: Request, 
			model_label: Optional[str] = Query(None, description='Filter by model label'),
			model_version: Optional[str] = Query(None, description='Filter by model version'),
			page: Optional[int] = Query(1, ge=1, description="Page number"),
			items: Optional[int] = Query(100, ge=1, le=1000, description='Number of items per page')):
		'''	List segmentation vector embeddings, optionally filtered by model_label and model_version.
		'''
		with DatabaseSession() as session:

			# Query series segmentation embeddings
			_query = session.query(SeriesSegmentationEmbedding)
			if model_label:
				_query = _query.filter(SeriesSegmentationEmbedding.model_label == model_label)
			if model_version:
				_query = _query.filter(SeriesSegmentationEmbedding.model_version == model_version)

			# Apply pagination
			_query = apply_query_pagination(_query, page=page, items=items)
			return _query.all()
