"""session replays, handouts and links

Revision ID: d7a3e5b2c901
Revises: c4d8a1f0b6e2
Create Date: 2026-11-05 00:30:00

"""
from alembic import op
import sqlalchemy as sa


revision = 'd7a3e5b2c901'
down_revision = 'c4d8a1f0b6e2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'session_resource',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('week_key', sa.String(length=32), nullable=False),
        sa.Column('kind', sa.String(length=8), nullable=False),
        sa.Column('title', sa.String(length=120), nullable=False),
        sa.Column('s3_key', sa.String(length=300), nullable=True),
        sa.Column('url', sa.String(length=500), nullable=True),
        sa.Column('filename', sa.String(length=200), nullable=True),
        sa.Column('content_type', sa.String(length=120), nullable=True),
        sa.Column('size', sa.BigInteger(), nullable=True),
        sa.Column('ready', sa.Boolean(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('session_resource', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_session_resource_week_key'), ['week_key'], unique=False)


def downgrade():
    with op.batch_alter_table('session_resource', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_session_resource_week_key'))
    op.drop_table('session_resource')
