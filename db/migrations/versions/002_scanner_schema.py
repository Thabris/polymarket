"""Scanner platform schema: extend markets, add signal/paper/family tables.

Revision ID: 002
Revises: 001
Create Date: 2026-08-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '002'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MARKET_COLUMNS = [
    sa.Column('slug', sa.String(300), nullable=True),
    sa.Column('start_date', sa.DateTime(), nullable=True),
    sa.Column('closed', sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column('accepting_orders', sa.Boolean(), nullable=False, server_default=sa.true()),
    sa.Column('outcomes', sa.Text(), nullable=True),
    sa.Column('best_bid', sa.Float(), nullable=True),
    sa.Column('best_ask', sa.Float(), nullable=True),
    sa.Column('spread', sa.Float(), nullable=True),
    sa.Column('last_trade_price', sa.Float(), nullable=True),
    sa.Column('one_day_price_change', sa.Float(), nullable=True),
    sa.Column('tick_size', sa.Float(), nullable=True),
    sa.Column('event_id', sa.String(100), nullable=True),
    sa.Column('event_slug', sa.String(300), nullable=True),
    sa.Column('group_item_title', sa.String(300), nullable=True),
    sa.Column('neg_risk', sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column('neg_risk_market_id', sa.String(120), nullable=True),
    sa.Column('fees_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column('fee_rate', sa.Float(), nullable=True),
    sa.Column('fee_type', sa.String(60), nullable=True),
    sa.Column('resolution_source', sa.Text(), nullable=True),
    sa.Column('uma_resolution_status', sa.String(60), nullable=True),
    sa.Column('resolved_outcome', sa.String(20), nullable=True),
    sa.Column('closed_time', sa.DateTime(), nullable=True),
    sa.Column('tags', sa.Text(), nullable=True),
    sa.Column('last_synced_at', sa.DateTime(), nullable=True),
]


def upgrade() -> None:
    for col in MARKET_COLUMNS:
        op.add_column('markets', col)
    op.create_index('ix_markets_closed', 'markets', ['closed'])
    op.create_index('ix_markets_event_id', 'markets', ['event_id'])
    op.create_index('ix_markets_last_synced_at', 'markets', ['last_synced_at'])

    op.create_table(
        'events',
        sa.Column('id', sa.String(100), nullable=False),
        sa.Column('slug', sa.String(300), nullable=True),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('neg_risk', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('tags', sa.Text(), nullable=True),
        sa.Column('end_date', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'signals',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('dedup_key', sa.String(300), nullable=False),
        sa.Column('strategy', sa.String(50), nullable=False),
        sa.Column('market_id', sa.String(100), nullable=True),
        sa.Column('token_id', sa.String(100), nullable=True),
        sa.Column('side', sa.String(20), nullable=False),
        sa.Column('grade', sa.String(5), nullable=False, server_default='B'),
        sa.Column('score', sa.Float(), nullable=True),
        sa.Column('entry_price', sa.Float(), nullable=True),
        sa.Column('entry_zone_low', sa.Float(), nullable=True),
        sa.Column('entry_zone_high', sa.Float(), nullable=True),
        sa.Column('size_hint', sa.Float(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='open'),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('notified', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('acknowledged', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('snapshot', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['market_id'], ['markets.id']),
        sa.UniqueConstraint('dedup_key'),
    )
    op.create_index('ix_signals_strategy', 'signals', ['strategy'])
    op.create_index('ix_signals_market_id', 'signals', ['market_id'])
    op.create_index('ix_signals_status', 'signals', ['status'])
    op.create_index('ix_signals_created_at', 'signals', ['created_at'])
    op.create_index(
        'ix_signals_strategy_status_created', 'signals',
        ['strategy', 'status', 'created_at'],
    )

    op.create_table(
        'paper_positions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('signal_id', sa.Integer(), nullable=False),
        sa.Column('strategy', sa.String(50), nullable=False),
        sa.Column('market_id', sa.String(100), nullable=True),
        sa.Column('token_id', sa.String(100), nullable=True),
        sa.Column('side', sa.String(20), nullable=False),
        sa.Column('entry_price', sa.Float(), nullable=False),
        sa.Column('size', sa.Float(), nullable=False),
        sa.Column('fees_paid', sa.Float(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(20), nullable=False, server_default='open'),
        sa.Column('exit_price', sa.Float(), nullable=True),
        sa.Column('exit_reason', sa.String(30), nullable=True),
        sa.Column('pnl', sa.Float(), nullable=True),
        sa.Column('opened_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['signal_id'], ['signals.id']),
        sa.UniqueConstraint('signal_id'),
    )
    op.create_index('ix_paper_positions_strategy', 'paper_positions', ['strategy'])
    op.create_index('ix_paper_positions_status', 'paper_positions', ['status'])

    op.create_table(
        'real_fills',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('signal_id', sa.Integer(), nullable=False),
        sa.Column('side', sa.String(20), nullable=False),
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('size', sa.Float(), nullable=False),
        sa.Column('fee_paid', sa.Float(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='open'),
        sa.Column('exit_price', sa.Float(), nullable=True),
        sa.Column('pnl', sa.Float(), nullable=True),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['signal_id'], ['signals.id']),
    )
    op.create_index('ix_real_fills_signal_id', 'real_fills', ['signal_id'])
    op.create_index('ix_real_fills_status', 'real_fills', ['status'])

    op.create_table(
        'market_families',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('family_key', sa.String(300), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('family_key'),
    )

    op.create_table(
        'family_members',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('family_id', sa.Integer(), nullable=False),
        sa.Column('market_id', sa.String(100), nullable=False),
        sa.Column('tranche_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('deadline', sa.DateTime(), nullable=True),
        sa.Column('group_item_title', sa.String(300), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['family_id'], ['market_families.id']),
        sa.ForeignKeyConstraint(['market_id'], ['markets.id']),
        sa.UniqueConstraint('family_id', 'market_id', name='uq_family_market'),
    )
    op.create_index('ix_family_members_family_id', 'family_members', ['family_id'])

    op.create_table(
        'scanner_runs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('scanner', sa.String(50), nullable=False),
        sa.Column('started_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('ok', sa.Boolean(), nullable=True),
        sa.Column('items_scanned', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('signals_emitted', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_scanner_runs_scanner', 'scanner_runs', ['scanner'])


def downgrade() -> None:
    op.drop_table('scanner_runs')
    op.drop_table('family_members')
    op.drop_table('market_families')
    op.drop_table('real_fills')
    op.drop_table('paper_positions')
    op.drop_table('signals')
    op.drop_table('events')
    op.drop_index('ix_markets_last_synced_at', table_name='markets')
    op.drop_index('ix_markets_event_id', table_name='markets')
    op.drop_index('ix_markets_closed', table_name='markets')
    with op.batch_alter_table('markets') as batch:
        for col in MARKET_COLUMNS:
            batch.drop_column(col.name)
