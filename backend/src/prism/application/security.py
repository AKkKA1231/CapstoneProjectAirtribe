"""Authentication and authorization use cases."""

import hashlib
import hmac
from datetime import datetime

from prism.domain.errors import InvalidAdminKeyError
from prism.domain.security import TenantContext, authenticate_record, digest_virtual_key
from prism.ports.repositories import SecurityRepository


class AuthenticationService:
    def __init__(self, repository: SecurityRepository, pepper: str) -> None:
        self._repository = repository
        self._pepper = pepper

    async def authenticate(self, raw_key: str, now: datetime) -> TenantContext:
        digest = digest_virtual_key(raw_key, self._pepper)
        record = await self._repository.find_virtual_key(digest)
        return authenticate_record(record, now)


def authenticate_admin(presented_key: str, configured_key: str) -> None:
    """Compare admin credentials without data-dependent early exit."""

    presented_digest = hashlib.sha256(presented_key.encode()).digest()
    configured_digest = hashlib.sha256(configured_key.encode()).digest()
    if not hmac.compare_digest(presented_digest, configured_digest):
        raise InvalidAdminKeyError("The supplied admin API key is invalid")
