'''	ContextDB schemas for validating vector embeddings
'''
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Any
from datetime import datetime


def validate_embedding(v):
    ''' Ensure that the embedding is populated with numeric values
    '''
    if not v or len(v) == 0:
        raise ValueError('Vector embedding must not be empty')
    if not all(isinstance(x, (int, float)) for x in v):
        raise ValueError('Vector embedding must contain only numeric values')

    return v


class EmbeddingBase(BaseModel):
    """ Base schema for Sonador vector embeddings
    """
    embedding: List[float] = Field(..., description="Vector embedding as list of floats")

    # Model label and version
    model_label: str = Field(..., description="Label for the model used to create the embedding")
    model_version: str = Field(..., description='Version of the model used to create the embedding')

    @field_validator('embedding')
    @classmethod
    def validate_embedding(cls, v):
        return validate_embedding(v)


class EmbeddingResponse(EmbeddingBase):
	'''	Schema for embedding responses
	'''
	uid: str = Field(..., description='UID of the embedding')
	ctime: datetime = Field(..., description='Created')
	mtime: datetime = Field(..., description='Modified')

	class Config:
		from_attributes = True


class EmbeddingRequestAction(EmbeddingBase):
    ''' Schema for creating a new embedding or updating an existing instance
    '''


class EmbeddingSimilarityQuery(BaseModel):
    ''' Schema for executing searches
    '''
    embedding: list[float] = Field(..., description='Vector embedding to be used for the lookup')

    @field_validator('embedding')
    @classmethod
    def validate_embedding(cls, v):
        return validate_embedding(v)