"""ContextDB init

Revision ID: 1dc673c97caf
Revises: 
Create Date: 2026-03-04 02:58:09.068098

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy_json import mutable_json_type

from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision = '1dc673c97caf'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():

    # Create segmentation embedding model
    op.create_table('sonador_contextdb__embedding_segmentation', 
        sa.Column('uid', sa.String(length=64), primary_key=True, unique=True),
        sa.Column('ctime', sa.DateTime()),
        sa.Column('mtime', sa.DateTime()),
        sa.Column('group', sa.BigInteger(), nullable=False),
        sa.Column('model_label', sa.String(length=128), nullable=False),
        sa.Column('model_version', sa.String(length=128), nullable=True),
        sa.Column('embedding', Vector(None), nullable=False),
        sa.Column('source', sa.String(length=64), nullable=False),
        sa.Column('resource', sa.String(length=64), nullable=False),
        sa.Column('dice', sa.Float(), nullable=True),
        sa.Column('hausdorff', sa.Float(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
    )


def downgrade():

    # Remove segmentation embedding model
    op.drop_table('sonador_contextdb__embedding_segmentation')
