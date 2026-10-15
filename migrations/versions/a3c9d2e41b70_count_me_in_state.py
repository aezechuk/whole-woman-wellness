"""count me in: state

Revision ID: a3c9d2e41b70
Revises: 64180a217d18
Create Date: 2026-10-15 15:50:00

"""
from alembic import op
import sqlalchemy as sa


revision = 'a3c9d2e41b70'
down_revision = '64180a217d18'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('demographic_response', schema=None) as batch_op:
        batch_op.add_column(sa.Column('state', sa.String(length=16), nullable=False, server_default='skip'))


def downgrade():
    with op.batch_alter_table('demographic_response', schema=None) as batch_op:
        batch_op.drop_column('state')
