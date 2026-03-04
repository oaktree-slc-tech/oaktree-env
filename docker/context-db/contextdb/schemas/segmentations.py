from pydantic import BaseModel, Field, field_validator
from .base import EmbeddingResponse, EmbeddingRequestAction, EmbeddingSimilarityQuery


class SeriesSegmentationEmbeddingMixin:
	'''	Mixin class providing attributes for Segmentation Embeddings
	'''
	source: str = Field(..., description='Orthanc UID for the imaging source of the segmentation')
	resource: str = Field(..., 
		description='Orthanc UID for the DICOMseg or M3D representation of the segmentation')
	quality: int = Field(default=None, description='Quality score for the segmentation')
	dice: float = Field(default=None, description='DICE score of the segmentation (against known ground-truth)')
	hausdorff: float = Field(default=None, 
		description='Hausdorff distance for the segmentation (deviation of surface against known ground truth)')
	notes: str = Field(default=None, description='Free-text notes and comments')



class SeriesSegmentationEmbeddingResponse(SeriesSegmentationEmbeddingMixin, EmbeddingResponse):
	'''	Vector embedding for imaging data segmentation
	'''


class SeriesSegmentationEmbeddingSimilarityResponse(SeriesSegmentationEmbeddingResponse):
	'''	Response vector embedding for imaging data segmentation. Includes a "distance" score
		describing how closely the response matched the input query.
	'''
	distance: float = Field(..., description='Similiarty to search query')


class SeriesSegmentationEmbeddingRequestAction(SeriesSegmentationEmbeddingMixin, EmbeddingRequestAction):
	'''	Create a new segmentation embedding or udpate an existing instance
	'''


class SeriesSegmentationEmbeddingSimilarityQuery(EmbeddingSimilarityQuery):
	'''	Execute a similarity lookup for a segmentation embedding
	'''