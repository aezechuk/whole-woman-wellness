"""intentions and check-ins

Revision ID: b7e2f9c13d55
Revises: a3c9d2e41b70
Create Date: 2026-10-20 16:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = 'b7e2f9c13d55'
down_revision = 'a3c9d2e41b70'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('intention',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('participant_id', sa.Uuid(), nullable=False),
        sa.Column('area_key', sa.String(length=32), nullable=False),
        sa.Column('week_key', sa.String(length=32), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['participant_id'], ['participant.id']),
        sa.PrimaryKeyConstraint('id'))
    with op.batch_alter_table('intention', schema=None) as b:
        b.create_index(b.f('ix_intention_participant_id'), ['participant_id'], unique=False)
    op.create_table('intention_checkin',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('intention_id', sa.Uuid(), nullable=False),
        sa.Column('week_key', sa.String(length=32), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['intention_id'], ['intention.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('intention_id', 'week_key'))
    with op.batch_alter_table('intention_checkin', schema=None) as b:
        b.create_index(b.f('ix_intention_checkin_intention_id'), ['intention_id'], unique=False)
    op.create_table('anon_checkin',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('cohort_id', sa.Uuid(), nullable=False),
        sa.Column('week_key', sa.String(length=32), nullable=False),
        sa.Column('area_key', sa.String(length=32), nullable=False),
        sa.Column('days', sa.Integer(), nullable=False),
        sa.Column('next_step', sa.String(length=16), nullable=False),
        sa.Column('week_number', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['cohort_id'], ['cohort.id']),
        sa.PrimaryKeyConstraint('id'))
    with op.batch_alter_table('anon_checkin', schema=None) as b:
        b.create_index(b.f('ix_anon_checkin_cohort_id'), ['cohort_id'], unique=False)


def downgrade():
    with op.batch_alter_table('anon_checkin', schema=None) as b:
        b.drop_index(b.f('ix_anon_checkin_cohort_id'))
    op.drop_table('anon_checkin')
    with op.batch_alter_table('intention_checkin', schema=None) as b:
        b.drop_index(b.f('ix_intention_checkin_intention_id'))
    op.drop_table('intention_checkin')
    with op.batch_alter_table('intention', schema=None) as b:
        b.drop_index(b.f('ix_intention_participant_id'))
    op.drop_table('intention')
