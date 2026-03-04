import abc, logging, datetime
from typing import Union, Sequence
from collections import OrderedDict

from sqlalchemy import Column, ForeignKey, Integer as SqlInteger, String as SqlString, \
	DateTime as SqlDateTime, Boolean as SqlBoolean, select
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from pgvector.sqlalchemy import Vector

from sonador.imaging.orthanc.base import ImagingSeries, ImagingStudy, ImagingPatient, DcmInstance

from .base import DbBase, AutoDbBase

logger = logging.getLogger(__name__)


class VectorEmbeddingBaseMixin:
	'''	Mixin class providing identifiers and common fields for AI context
		augmentation models to be used with Sonador.		
	'''
	uid = Colum(SqlString(64), primary_key=True, unique=True)
	ctime = Column(SqlDateTime(), nullable=True)
	mtime = Column(SqlDateTime(), nullable=True)

	# Group to which the vector embedding belongs
	group = Column(SqlInteger)

	# Model Identifiers
	model_label = Column(SqlString(128), nullable=False)
	model_version = Column(SqlString(128), nullable=True)

	# Embedding
	embedding = Column(Vector(None), nullable=False)
