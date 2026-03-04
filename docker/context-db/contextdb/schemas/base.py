'''	ContextDB schemas for validating vector embeddings
'''
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import datetime


class EmbeddingBase(BaseModel):
    """Base schema for VectorItem."""
    embedding: List[float] = Field(..., description="Vector embedding as list of floats")

    # Model label and version
    model_label: str = Field(..., description="Label for the model used to create the embedding")
    model_version: str = Field(default=None, description='Version of the model used to create the embedding')

    @field_validator('embedding')
    @classmethod
    def validate_embedding(cls, v):
        if not v or len(v) == 0:
            raise ValueError('Vector must not be empty')
        if not all(isinstance(x, (int, float)) for x in v):
            raise ValueError('Vector must contain only numeric values')
        
        return v


class EmbeddingResponse(EmbeddingBase):
	'''	Schema for embedding responses
	'''
	uid: str
	ctime: datetime
	mtime: datetime

	class Config:
		from_attributes = True