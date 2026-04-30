from typing import List, Optional, Any

from pydantic import BaseModel, Field, field_validator
from .base import EmbeddingResponse, EmbeddingRequestAction, EmbeddingSimilarityQuery



class SegmentationBaseEmbeddingMixin:
	'''	Mixin class providing attributes for Segmentation Embeddings
	'''
	segmentation_label: str = Field(..., description='Label for the segmentation')
	source: str = Field(..., description='Orthanc UID for the imaging source of the segmentation')
	resource: str = Field(..., 
		description='Orthanc UID for the DICOMseg or M3D representation of the segmentation')
	ground_truth: str = Field(...,
		description='Orthanc UID for the DICOMseg or M3D representation of the ground truth')
	
	quality: int = Field(..., description='Quality score for the segmentation')
	dice: float = Field(..., description='DICE score of the segmentation (against known ground-truth)')
	hausdorff: float = Field(..., 
		description='Hausdorff distance for the segmentation (deviation of surface against known ground truth)')
	
	notes: str = Field(default=None, description='Free-text notes and comments')
	misc: dict[str, Any] = Field(default_factory=dict, description='Segmentation JSON attributes')



## --> Instance Segmentation Schemas <-- ##


class InstanceSegmentationEmbeddingMixin(SegmentationBaseEmbeddingMixin):
	'''	Mixin class providing attributes for Instance Segmentation Embeddings
	'''


class InstanceSegmentationEmbeddingResponse(InstanceSegmentationEmbeddingMixin, EmbeddingResponse):
	'''	Vector embedding for a 2D slice of a segmentation
	'''


class InstanceSegmentationEmbeddingSimilarityResponse(InstanceSegmentationEmbeddingMixin, EmbeddingResponse):
	'''	Vector embedding for a 2D slice of a segmentation
	'''
	distance: float = Field(..., description='Similiarty to search query')


## --> Series Segmentation Schemas


class SeriesSegmentationEmbeddingMixin(SegmentationBaseEmbeddingMixin):
	'''	Mixin class providing attributes for Series Segmentation Embeddings
	'''


class SeriesSegmentationEmbeddingResponse(SeriesSegmentationEmbeddingMixin, EmbeddingResponse):
	'''	Vector embedding for the 3D representation of a segmentation.
	'''


class SeriesSegmentationEmbeddingSimilarityResponse(SeriesSegmentationEmbeddingResponse):
	'''	Response vector embedding for the 3D representation of a segmentation. Includes a "distance" score
		describing how closely the response matched the input query.
	'''
	distance: float = Field(..., description='Similiarty to search query')



## --> Segmentation Embedding Request Schemas <-- ##


class SegmentationEmbeddingRequestAction(SeriesSegmentationEmbeddingMixin, EmbeddingRequestAction):
	'''	Create a new segmentation embedding or udpate an existing instance
	'''


class SegmentationEmbeddingSimilarityQuery(EmbeddingSimilarityQuery):
	'''	Execute a similarity lookup for a segmentation embedding
	'''
	segmentation_label: str = Field(None, description='Segmentation label to be used for filtering results')