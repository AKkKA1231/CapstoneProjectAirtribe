"""Create the initial Prism schema.

Revision ID: 0001_initial_schema
Revises: None
Create Date: 2026-11-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = "NUMERIC(18,10)"


def upgrade() -> None:
    """Create extensions, tables, constraints, and indexes."""

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.execute(
        """
        CREATE TABLE providers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(100) NOT NULL UNIQUE,
            base_url TEXT NOT NULL,
            api_key_env_var VARCHAR(200),
            timeout_ms INTEGER NOT NULL DEFAULT 30000
                CONSTRAINT ck_providers_timeout_positive CHECK (timeout_ms > 0),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE TABLE model_aliases (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            alias VARCHAR(100) NOT NULL UNIQUE,
            tier VARCHAR(20) NOT NULL
                CONSTRAINT ck_model_aliases_tier CHECK (tier IN ('fast', 'smart', 'auto')),
            primary_provider_id UUID REFERENCES providers(id),
            primary_model VARCHAR(200),
            max_input_tokens INTEGER
                CONSTRAINT ck_model_aliases_input_tokens
                CHECK (max_input_tokens IS NULL OR max_input_tokens > 0),
            max_output_tokens INTEGER
                CONSTRAINT ck_model_aliases_output_tokens
                CHECK (max_output_tokens IS NULL OR max_output_tokens > 0),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_model_aliases_target CHECK (
                (tier = 'auto' AND primary_provider_id IS NULL AND primary_model IS NULL)
                OR
                (tier <> 'auto' AND primary_provider_id IS NOT NULL AND primary_model IS NOT NULL)
            )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE fallback_chains (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            alias_id UUID NOT NULL REFERENCES model_aliases(id) ON DELETE CASCADE,
            priority INTEGER NOT NULL
                CONSTRAINT ck_fallback_priority_positive CHECK (priority > 0),
            provider_id UUID NOT NULL REFERENCES providers(id),
            model VARCHAR(200) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_fallback_chain_priority UNIQUE (alias_id, priority)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE model_pricing (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            provider_id UUID NOT NULL REFERENCES providers(id),
            model VARCHAR(200) NOT NULL,
            input_cost_per_1k {MONEY} NOT NULL
                CONSTRAINT ck_pricing_input_nonnegative CHECK (input_cost_per_1k >= 0),
            output_cost_per_1k {MONEY} NOT NULL
                CONSTRAINT ck_pricing_output_nonnegative CHECK (output_cost_per_1k >= 0),
            effective_from TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            effective_until TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_model_pricing_version
                UNIQUE (provider_id, model, effective_from)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX ix_model_pricing_current
        ON model_pricing(provider_id, model)
        WHERE effective_until IS NULL
        """
    )
    op.execute(
        f"""
        CREATE TABLE virtual_keys (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            key_digest VARCHAR(64) NOT NULL UNIQUE,
            key_prefix VARCHAR(16) NOT NULL,
            tenant_name VARCHAR(200) NOT NULL,
            description TEXT,
            rpm_limit INTEGER NOT NULL DEFAULT 60
                CONSTRAINT ck_virtual_keys_rpm_positive CHECK (rpm_limit > 0),
            monthly_budget_usd {MONEY} NOT NULL
                CONSTRAINT ck_virtual_keys_budget_nonnegative CHECK (monthly_budget_usd >= 0),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE TABLE virtual_key_allowed_aliases (
            key_id UUID NOT NULL REFERENCES virtual_keys(id) ON DELETE CASCADE,
            alias_id UUID NOT NULL REFERENCES model_aliases(id) ON DELETE CASCADE,
            PRIMARY KEY (key_id, alias_id)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE admission_counters (
            key_id UUID PRIMARY KEY REFERENCES virtual_keys(id) ON DELETE CASCADE,
            rpm_window_start TIMESTAMPTZ NOT NULL,
            rpm_count INTEGER NOT NULL DEFAULT 0
                CONSTRAINT ck_admission_rpm_nonnegative CHECK (rpm_count >= 0),
            budget_month VARCHAR(7) NOT NULL,
            spent_usd {MONEY} NOT NULL DEFAULT 0
                CONSTRAINT ck_admission_spent_nonnegative CHECK (spent_usd >= 0),
            reserved_usd {MONEY} NOT NULL DEFAULT 0
                CONSTRAINT ck_admission_reserved_nonnegative CHECK (reserved_usd >= 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE request_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            key_id UUID NOT NULL REFERENCES virtual_keys(id),
            request_alias VARCHAR(100) NOT NULL,
            resolved_alias VARCHAR(100),
            provider_id UUID REFERENCES providers(id),
            model VARCHAR(200),
            status VARCHAR(40) NOT NULL,
            request_fingerprint VARCHAR(64) NOT NULL,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            cost_usd {MONEY},
            cache_hit BOOLEAN NOT NULL DEFAULT FALSE,
            cache_entry_id UUID,
            fallback_triggered BOOLEAN NOT NULL DEFAULT FALSE,
            auto_routed BOOLEAN NOT NULL DEFAULT FALSE,
            routing_tier VARCHAR(20),
            routing_reason VARCHAR(100),
            routing_confidence DOUBLE PRECISION,
            classifier_version VARCHAR(100),
            streamed BOOLEAN NOT NULL DEFAULT FALSE,
            latency_ms INTEGER,
            provider_latency_ms INTEGER,
            error_code VARCHAR(100),
            error_message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMPTZ,
            CONSTRAINT ck_request_logs_status CHECK (
                status IN (
                    'in_progress', 'success', 'error', 'partial_stream_error',
                    'rate_limited', 'budget_exhausted', 'model_not_allowed',
                    'timeout', 'cancelled'
                )
            )
        )
        """
    )
    op.execute("CREATE INDEX ix_request_logs_key_created ON request_logs(key_id, created_at DESC)")
    op.execute("CREATE INDEX ix_request_logs_created ON request_logs(created_at DESC)")
    op.execute("CREATE INDEX ix_request_logs_status ON request_logs(status)")

    op.execute(
        f"""
        CREATE TABLE budget_reservations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            key_id UUID NOT NULL REFERENCES virtual_keys(id),
            request_log_id UUID NOT NULL UNIQUE REFERENCES request_logs(id) ON DELETE CASCADE,
            reserved_usd {MONEY} NOT NULL
                CONSTRAINT ck_reservations_reserved_nonnegative CHECK (reserved_usd >= 0),
            settled_usd {MONEY}
                CONSTRAINT ck_reservations_settled_nonnegative
                CHECK (settled_usd IS NULL OR settled_usd >= 0),
            status VARCHAR(40) NOT NULL DEFAULT 'pending',
            reconciliation_required BOOLEAN NOT NULL DEFAULT FALSE,
            last_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            settled_at TIMESTAMPTZ,
            CONSTRAINT ck_budget_reservations_status CHECK (
                status IN (
                    'pending', 'settled', 'failed', 'cancelled',
                    'reconciliation_required'
                )
            )
        )
        """
    )
    op.execute("CREATE INDEX ix_budget_reservations_pending ON budget_reservations(key_id, status)")
    op.execute(
        f"""
        CREATE TABLE provider_attempts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            request_log_id UUID NOT NULL REFERENCES request_logs(id) ON DELETE CASCADE,
            attempt_number INTEGER NOT NULL,
            retry_number INTEGER NOT NULL DEFAULT 0,
            provider_id UUID NOT NULL REFERENCES providers(id),
            model VARCHAR(200) NOT NULL,
            outcome VARCHAR(40) NOT NULL,
            backoff_ms INTEGER,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            cost_usd {MONEY},
            started_at TIMESTAMPTZ NOT NULL,
            ended_at TIMESTAMPTZ,
            latency_ms INTEGER,
            http_status INTEGER,
            error_code VARCHAR(100),
            sanitized_error TEXT,
            CONSTRAINT uq_provider_attempt_sequence
                UNIQUE (request_log_id, attempt_number, retry_number),
            CONSTRAINT ck_provider_attempts_outcome CHECK (
                outcome IN (
                    'success', 'transient_error', 'timeout', 'rate_limited',
                    'fatal_error', 'cancelled', 'partial_stream_error'
                )
            )
        )
        """
    )
    op.execute("CREATE INDEX ix_provider_attempts_request ON provider_attempts(request_log_id)")

    op.execute(
        f"""
        CREATE TABLE cache_entries (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            key_id UUID NOT NULL REFERENCES virtual_keys(id) ON DELETE CASCADE,
            scope_hash VARCHAR(64) NOT NULL,
            request_fingerprint VARCHAR(64) NOT NULL,
            prompt_text TEXT NOT NULL,
            prompt_embedding vector(1536) NOT NULL,
            embedding_model VARCHAR(200) NOT NULL,
            embedding_version VARCHAR(100) NOT NULL,
            cache_schema_version INTEGER NOT NULL DEFAULT 1,
            response_body JSONB NOT NULL,
            model VARCHAR(200) NOT NULL,
            provider VARCHAR(100) NOT NULL,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            cost_usd {MONEY},
            hit_count INTEGER NOT NULL DEFAULT 0
                CONSTRAINT ck_cache_hit_count_nonnegative CHECK (hit_count >= 0),
            similarity_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.92,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_hit_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT ck_cache_similarity_range CHECK (
                similarity_threshold >= 0 AND similarity_threshold <= 1
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_cache_entries_scope ON cache_entries(key_id, scope_hash, expires_at)"
    )

    op.execute(
        """
        ALTER TABLE request_logs
        ADD CONSTRAINT fk_request_logs_cache_entry
        FOREIGN KEY (cache_entry_id) REFERENCES cache_entries(id) ON DELETE SET NULL
        """
    )
    op.execute(
        """
        CREATE TABLE routing_eval_cases (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            prompt_text TEXT NOT NULL,
            expected_tier VARCHAR(20) NOT NULL,
            difficulty_label TEXT,
            is_trap BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_routing_expected_tier CHECK (expected_tier IN ('fast', 'smart'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE security_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            event_type VARCHAR(100) NOT NULL,
            presented_key_fingerprint VARCHAR(64),
            request_id VARCHAR(100) NOT NULL,
            client_ip_hash VARCHAR(64),
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute("CREATE INDEX ix_security_events_created ON security_events(created_at DESC)")


def downgrade() -> None:
    """Drop the initial schema in reverse dependency order."""

    op.execute("DROP TABLE IF EXISTS security_events")
    op.execute("DROP TABLE IF EXISTS routing_eval_cases")
    op.execute("ALTER TABLE request_logs DROP CONSTRAINT IF EXISTS fk_request_logs_cache_entry")
    op.execute("DROP TABLE IF EXISTS cache_entries")
    op.execute("DROP TABLE IF EXISTS provider_attempts")
    op.execute("DROP TABLE IF EXISTS budget_reservations")
    op.execute("DROP TABLE IF EXISTS request_logs")
    op.execute("DROP TABLE IF EXISTS admission_counters")
    op.execute("DROP TABLE IF EXISTS virtual_key_allowed_aliases")
    op.execute("DROP TABLE IF EXISTS virtual_keys")
    op.execute("DROP TABLE IF EXISTS model_pricing")
    op.execute("DROP TABLE IF EXISTS fallback_chains")
    op.execute("DROP TABLE IF EXISTS model_aliases")
    op.execute("DROP TABLE IF EXISTS providers")
