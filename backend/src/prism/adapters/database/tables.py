"""SQLAlchemy table mappings for Prism's transactional source of truth."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

MONEY = Numeric(18, 10)


class Base(DeclarativeBase):
    """Declarative metadata root."""


class TimestampMixin:
    """UTC creation and update timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class Provider(TimestampMixin, Base):
    __tablename__ = "providers"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_env_var: Mapped[str | None] = mapped_column(String(200))
    timeout_ms: Mapped[int] = mapped_column(Integer, default=30_000, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (CheckConstraint("timeout_ms > 0", name="ck_providers_timeout_positive"),)


class ModelAlias(Base):
    __tablename__ = "model_aliases"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    alias: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    tier: Mapped[str] = mapped_column(String(20), nullable=False)
    primary_provider_id: Mapped[UUID | None] = mapped_column(ForeignKey("providers.id"))
    primary_model: Mapped[str | None] = mapped_column(String(200))
    max_input_tokens: Mapped[int | None] = mapped_column(Integer)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (
        CheckConstraint("tier IN ('fast', 'smart', 'auto')", name="ck_model_aliases_tier"),
        CheckConstraint(
            "(tier = 'auto' AND primary_provider_id IS NULL AND primary_model IS NULL) OR "
            "(tier <> 'auto' AND primary_provider_id IS NOT NULL AND primary_model IS NOT NULL)",
            name="ck_model_aliases_target",
        ),
        CheckConstraint(
            "max_input_tokens IS NULL OR max_input_tokens > 0",
            name="ck_model_aliases_input_tokens",
        ),
        CheckConstraint(
            "max_output_tokens IS NULL OR max_output_tokens > 0",
            name="ck_model_aliases_output_tokens",
        ),
    )


class FallbackTarget(Base):
    __tablename__ = "fallback_chains"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    alias_id: Mapped[UUID] = mapped_column(
        ForeignKey("model_aliases.id", ondelete="CASCADE"), nullable=False
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.id"), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("alias_id", "priority", name="uq_fallback_chain_priority"),
        CheckConstraint("priority > 0", name="ck_fallback_priority_positive"),
    )


class ModelPricing(Base):
    __tablename__ = "model_pricing"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.id"), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    input_cost_per_1k: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    output_cost_per_1k: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("provider_id", "model", "effective_from", name="uq_model_pricing_version"),
        CheckConstraint("input_cost_per_1k >= 0", name="ck_pricing_input_nonnegative"),
        CheckConstraint("output_cost_per_1k >= 0", name="ck_pricing_output_nonnegative"),
        Index(
            "ix_model_pricing_current",
            "provider_id",
            "model",
            unique=True,
            postgresql_where=text("effective_until IS NULL"),
        ),
    )


class VirtualKey(TimestampMixin, Base):
    __tablename__ = "virtual_keys"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    key_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    tenant_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    rpm_limit: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    monthly_budget_usd: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("rpm_limit > 0", name="ck_virtual_keys_rpm_positive"),
        CheckConstraint("monthly_budget_usd >= 0", name="ck_virtual_keys_budget_nonnegative"),
    )


class VirtualKeyAllowedAlias(Base):
    __tablename__ = "virtual_key_allowed_aliases"

    key_id: Mapped[UUID] = mapped_column(
        ForeignKey("virtual_keys.id", ondelete="CASCADE"), primary_key=True
    )
    alias_id: Mapped[UUID] = mapped_column(
        ForeignKey("model_aliases.id", ondelete="CASCADE"), primary_key=True
    )


class AdmissionCounter(Base):
    __tablename__ = "admission_counters"

    key_id: Mapped[UUID] = mapped_column(
        ForeignKey("virtual_keys.id", ondelete="CASCADE"), primary_key=True
    )
    rpm_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rpm_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    budget_month: Mapped[str] = mapped_column(String(7), nullable=False)
    spent_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    reserved_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (
        CheckConstraint("rpm_count >= 0", name="ck_admission_rpm_nonnegative"),
        CheckConstraint("spent_usd >= 0", name="ck_admission_spent_nonnegative"),
        CheckConstraint("reserved_usd >= 0", name="ck_admission_reserved_nonnegative"),
    )


class RequestLog(Base):
    __tablename__ = "request_logs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    key_id: Mapped[UUID] = mapped_column(ForeignKey("virtual_keys.id"), nullable=False)
    request_alias: Mapped[str] = mapped_column(String(100), nullable=False)
    resolved_alias: Mapped[str | None] = mapped_column(String(100))
    provider_id: Mapped[UUID | None] = mapped_column(ForeignKey("providers.id"))
    model: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(MONEY)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cache_entry_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cache_entries.id", ondelete="SET NULL"), nullable=True
    )
    fallback_triggered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_routed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    routing_tier: Mapped[str | None] = mapped_column(String(20))
    routing_reason: Mapped[str | None] = mapped_column(String(100))
    routing_confidence: Mapped[float | None] = mapped_column(Float)
    classifier_version: Mapped[str | None] = mapped_column(String(100))
    streamed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    provider_latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('in_progress', 'success', 'error', 'partial_stream_error', "
            "'rate_limited', 'budget_exhausted', 'model_not_allowed', 'timeout', "
            "'cancelled')",
            name="ck_request_logs_status",
        ),
        Index("ix_request_logs_key_created", "key_id", text("created_at DESC")),
        Index("ix_request_logs_created", text("created_at DESC")),
        Index("ix_request_logs_status", "status"),
    )


class BudgetReservation(Base):
    __tablename__ = "budget_reservations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    key_id: Mapped[UUID] = mapped_column(ForeignKey("virtual_keys.id"), nullable=False)
    request_log_id: Mapped[UUID] = mapped_column(
        ForeignKey("request_logs.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    reserved_usd: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    settled_usd: Mapped[Decimal | None] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False)
    reconciliation_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'settled', 'failed', 'cancelled', 'reconciliation_required')",
            name="ck_budget_reservations_status",
        ),
        CheckConstraint("reserved_usd >= 0", name="ck_reservations_reserved_nonnegative"),
        CheckConstraint(
            "settled_usd IS NULL OR settled_usd >= 0",
            name="ck_reservations_settled_nonnegative",
        ),
        Index("ix_budget_reservations_pending", "key_id", "status"),
    )


class ProviderAttempt(Base):
    __tablename__ = "provider_attempts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    request_log_id: Mapped[UUID] = mapped_column(
        ForeignKey("request_logs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_number: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.id"), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    backoff_ms: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(MONEY)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(100))
    sanitized_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "request_log_id",
            "attempt_number",
            "retry_number",
            name="uq_provider_attempt_sequence",
        ),
        CheckConstraint(
            "outcome IN ('success', 'transient_error', 'timeout', 'rate_limited', "
            "'fatal_error', 'cancelled', 'partial_stream_error')",
            name="ck_provider_attempts_outcome",
        ),
        Index("ix_provider_attempts_request", "request_log_id"),
    )


class CacheEntry(Base):
    __tablename__ = "cache_entries"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    key_id: Mapped[UUID] = mapped_column(
        ForeignKey("virtual_keys.id", ondelete="CASCADE"), nullable=False
    )
    scope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_embedding: Mapped[Any] = mapped_column(Vector(1536), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    embedding_version: Mapped[str] = mapped_column(String(100), nullable=False)
    cache_schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(MONEY)
    hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    similarity_threshold: Mapped[float] = mapped_column(Float, default=0.92, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("hit_count >= 0", name="ck_cache_hit_count_nonnegative"),
        CheckConstraint(
            "similarity_threshold >= 0 AND similarity_threshold <= 1",
            name="ck_cache_similarity_range",
        ),
        Index("ix_cache_entries_scope", "key_id", "scope_hash", "expires_at"),
    )


class RoutingEvalCase(Base):
    __tablename__ = "routing_eval_cases"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    expected_tier: Mapped[str] = mapped_column(String(20), nullable=False)
    difficulty_label: Mapped[str | None] = mapped_column(Text)
    is_trap: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (
        CheckConstraint("expected_tier IN ('fast', 'smart')", name="ck_routing_expected_tier"),
    )


class SecurityEvent(Base):
    __tablename__ = "security_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    presented_key_fingerprint: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(100), nullable=False)
    client_ip_hash: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (Index("ix_security_events_created", text("created_at DESC")),)
