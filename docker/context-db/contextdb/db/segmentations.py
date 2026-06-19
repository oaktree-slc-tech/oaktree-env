from .base import DbBase, ContextDbMixin
from .vectorbase import VectorEmbeddingBaseMixin

from sonador_fastapi.db import set_ctime, set_mtime

from sqlalchemy import Column, ForeignKey, Integer as SqlInteger, Float as SqlFloat, String as SqlString, \
	DateTime as SqlDateTime, Boolean as SqlBoolean, Text as SqlText, select, event


class InstanceSegmentationEmbedding(VectorEmbeddingBaseMixin, ContextDbMixin, DbBase):
	'''	Instance vector representation of an image segmentation (2D/slice representation)
		which can be used for assessing quality against known ground truth. Provides
		fields for the sice (2D) DICE and Hausdorff (boundary) distances.

		@field source (SqlString): Orthanc imaging instance ID from which the segmentation was created.
		@field resource (SqlString): Orthanc series resource ID for the segmentation assessed by the model
			(DICOMseg or M3D).
		@field ground_truth (SqlString): Orthanc series resource ID of the segmentation ground truth
			(DICOMseg or M3D).
	'''
	__tablename__ = 'sonador_contextdb__instance_embedding_segmentation'

	# Segmentation label, imaging instance source, series UID of segmentation, and ground truth series UID
	segmentation_label = Column(SqlString(512), nullable=False)
	source = Column(SqlString(64), nullable=False)
	resource = Column(SqlString(64), nullable=False)
	ground_truth = Column(SqlString(64), nullable=False)

	# Segmentation Metrics
	quality = Column(SqlInteger, nullable=True)
	dice = Column(SqlFloat, nullable=True)
	hausdorff = Column(SqlFloat, nullable=True)

	# Free-text notes
	notes = Column(SqlText, nullable=True)


class SeriesSegmentationEmbedding(VectorEmbeddingBaseMixin, ContextDbMixin, DbBase):
	'''	Series vector representation of an image segmentation (3D representation) 
		which can be used for assessing quality against known ground truth. Provides fields
		for the volumetric segmentation DICE and Hausdorff surface distances.
	
		@field source (SqlString): Orthanc imaging series ID from which the segmentation was created.
		@field resource (SqlString): Orthanc series resource ID of the segmentation assessed by the model.
		@field gorund_truth (SqlString): Orthanc series resource ID of the segmentation ground truth.
	'''
	__tablename__ = 'sonador_contextdb__embedding_segmentation'	
	
	# Segmentation label, imaging source, series UID of segmentation, and ground truth series UID
	segmentation_label = Column(SqlString(512), nullable=False)
	source = Column(SqlString(64), nullable=False)
	resource = Column(SqlString(64), nullable=False)
	ground_truth = Column(SqlString(64), nullable=False)

	# Segmentation Metrics
	quality = Column(SqlInteger, nullable=True)
	dice = Column(SqlFloat, nullable=True)
	hausdorff = Column(SqlFloat, nullable=True)

	# Free-text notes
	notes = Column(SqlText, nullable=True)


# Set ctime and mtime
event.listens_for(InstanceSegmentationEmbedding, 'before_insert')(set_ctime)
event.listens_for(InstanceSegmentationEmbedding, 'before_update')(set_mtime)
event.listens_for(SeriesSegmentationEmbedding, 'before_insert')(set_ctime)
event.listens_for(SeriesSegmentationEmbedding, 'before_update')(set_mtime)