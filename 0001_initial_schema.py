"""Initial SwasthyaGrid schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-10-29
"""
from alembic import op
from backend.database import Base

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Use the canonical SQLAlchemy metadata so this revision stays aligned with the shared service models.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
