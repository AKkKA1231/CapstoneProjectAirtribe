"""Protect application tables from Supabase Data API roles.

Revision ID: 0002_enable_row_level_security
Revises: 0001_initial_schema
Create Date: 2026-11-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_enable_row_level_security"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "providers",
    "model_aliases",
    "fallback_chains",
    "model_pricing",
    "virtual_keys",
    "virtual_key_allowed_aliases",
    "admission_counters",
    "request_logs",
    "budget_reservations",
    "provider_attempts",
    "cache_entries",
    "routing_eval_cases",
    "security_events",
)


def upgrade() -> None:
    """Enable RLS with no public policies; the backend database role remains authoritative."""

    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Restore the original table-level RLS state."""

    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
