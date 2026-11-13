"""Tenant identity and authorization policies."""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from prism.domain.errors import (
    ExpiredApiKeyError,
    InactiveApiKeyError,
    InvalidApiKeyError,
    ModelNotAllowedError,
)


def digest_virtual_key(raw_key: str, pepper: str) -> str:
    """Produce the indexed HMAC-SHA-256 digest stored instead of a raw key."""

    return hmac.new(pepper.encode(), raw_key.encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class VirtualKeyRecord:
    id: UUID
    tenant_name: str
    rpm_limit: int
    monthly_budget_usd: Decimal
    is_active: bool
    expires_at: datetime | None
    allowed_aliases: frozenset[str]


@dataclass(frozen=True, slots=True)
class TenantContext:
    key_id: UUID
    tenant_name: str
    rpm_limit: int
    monthly_budget_usd: Decimal
    allowed_aliases: frozenset[str]

    def authorize_alias(self, alias: str) -> None:
        """Reject access to aliases outside this key's explicit allowlist."""

        if alias not in self.allowed_aliases:
            raise ModelNotAllowedError(f"Model alias {alias!r} is not allowed for this key")


def authenticate_record(record: VirtualKeyRecord | None, now: datetime) -> TenantContext:
    """Convert a persistence record into a trusted tenant identity."""

    if record is None:
        raise InvalidApiKeyError("The supplied API key is invalid")
    if not record.is_active:
        raise InactiveApiKeyError("The supplied API key is inactive")
    if record.expires_at is not None and record.expires_at <= now:
        raise ExpiredApiKeyError("The supplied API key has expired")
    return TenantContext(
        key_id=record.id,
        tenant_name=record.tenant_name,
        rpm_limit=record.rpm_limit,
        monthly_budget_usd=record.monthly_budget_usd,
        allowed_aliases=record.allowed_aliases,
    )
