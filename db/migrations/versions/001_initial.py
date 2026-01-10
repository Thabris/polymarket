"""Initial database schema

Revision ID: 001
Revises:
Create Date: 2025-01-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create markets table
    op.create_table(
        'markets',
        sa.Column('id', sa.String(100), nullable=False),
        sa.Column('condition_id', sa.String(100), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(100), nullable=True),
        sa.Column('end_date', sa.DateTime(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('token_id_yes', sa.String(100), nullable=True),
        sa.Column('token_id_no', sa.String(100), nullable=True),
        sa.Column('price_yes', sa.Float(), nullable=True),
        sa.Column('price_no', sa.Float(), nullable=True),
        sa.Column('volume_24h', sa.Float(), nullable=True),
        sa.Column('liquidity', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_markets_condition_id', 'markets', ['condition_id'])
    op.create_index('ix_markets_category', 'markets', ['category'])
    op.create_index('ix_markets_active', 'markets', ['active'])

    # Create prices table
    op.create_table(
        'prices',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('market_id', sa.String(100), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('open', sa.Float(), nullable=False),
        sa.Column('high', sa.Float(), nullable=False),
        sa.Column('low', sa.Float(), nullable=False),
        sa.Column('close', sa.Float(), nullable=False),
        sa.Column('volume', sa.Float(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['market_id'], ['markets.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_prices_market_id', 'prices', ['market_id'])
    op.create_index('ix_prices_timestamp', 'prices', ['timestamp'])
    op.create_index('ix_prices_market_timestamp', 'prices', ['market_id', 'timestamp'])

    # Create alerts table
    op.create_table(
        'alerts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('market_id', sa.String(100), nullable=True),
        sa.Column('alert_type', sa.String(50), nullable=False),
        sa.Column('severity', sa.String(20), nullable=False, server_default='info'),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('data', sa.Text(), nullable=True),
        sa.Column('acknowledged', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['market_id'], ['markets.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_alerts_market_id', 'alerts', ['market_id'])
    op.create_index('ix_alerts_alert_type', 'alerts', ['alert_type'])
    op.create_index('ix_alerts_created_at', 'alerts', ['created_at'])
    op.create_index('ix_alerts_type_created', 'alerts', ['alert_type', 'created_at'])

    # Create watchlist table
    op.create_table(
        'watchlist',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('market_id', sa.String(100), nullable=False),
        sa.Column('price_threshold_pct', sa.Float(), nullable=True),
        sa.Column('volume_threshold_usd', sa.Float(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('market_id')
    )
    op.create_index('ix_watchlist_market_id', 'watchlist', ['market_id'])

    # Create alert_config table
    op.create_table(
        'alert_config',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('key', sa.String(100), nullable=False),
        sa.Column('value', sa.String(500), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key')
    )
    op.create_index('ix_alert_config_key', 'alert_config', ['key'])


def downgrade() -> None:
    op.drop_table('alert_config')
    op.drop_table('watchlist')
    op.drop_table('alerts')
    op.drop_table('prices')
    op.drop_table('markets')
