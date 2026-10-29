"""optional journal PIN

Revision ID: c4d8a1f0b6e2
Revises: 64e84da4d815
Create Date: 2026-10-29 10:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = 'c4d8a1f0b6e2'
down_revision = '64e84da4d815'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('participant', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pin_hash', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('pin_failures', sa.Integer(), server_default='0', nullable=False))
    with op.batch_alter_table('auth_session', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pin_unlocked_until', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table('auth_session', schema=None) as batch_op:
        batch_op.drop_column('pin_unlocked_until')
    with op.batch_alter_table('participant', schema=None) as batch_op:
        batch_op.drop_column('pin_failures')
        batch_op.drop_column('pin_hash')
