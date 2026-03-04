from .base import DbBase, ContextDbMixin
from .vectorbase import VectorEmbeddingBaseMixin

from sqlalchemy import Column, ForeignKey, Integer as SqlInteger, Float as SqlFloat, String as SqlString, \
	DateTime as SqlDateTime, Boolean as SqlBoolean, Text as SqlText, select


class SeriesSegmentationEmbedding(VectorEmbeddingBaseMixin, ContextDbMixin, DbBase):
	'''	Vector representation of an image segmentation which can be used for assessing
		quality against segmentations with known ground truth. Provides fields
		for the segmentation DICE and Hausdorff distances.
	
		@field source (SqlString): Orthanc imaging resource ID from which the segmentation
			was created.
		@field resource (SqlString): Orthanc resource ID of the segmentation
	'''
	__tablename__ = 'sonador_contextdb__embedding_segmentation'

	# Segmentation imaging source and series UID
	source = Column(SqlString(64), nullable=False)
	resource = Column(SqlString(64), nullable=False)

	# Metrics
	dice = Column(SqlFloat, nullable=True)
	hausdorff = Column(SqlFloat, nullable=True)

	# Free-text notes
	notes = Column(SqlText, nullable=True)