"""Stable application errors exposed through Prism's API boundary."""


class PrismError(Exception):
    """Base error with an OpenAI-compatible public representation."""

    status_code = 400
    code = "invalid_request"
    error_type = "invalid_request_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class MissingApiKeyError(PrismError):
    status_code = 401
    code = "missing_api_key"
    error_type = "authentication_error"


class InvalidApiKeyError(PrismError):
    status_code = 401
    code = "invalid_api_key"
    error_type = "authentication_error"


class InactiveApiKeyError(PrismError):
    status_code = 403
    code = "inactive_api_key"
    error_type = "permission_error"


class ExpiredApiKeyError(PrismError):
    status_code = 403
    code = "expired_api_key"
    error_type = "permission_error"


class ModelNotAllowedError(PrismError):
    status_code = 403
    code = "model_not_allowed"
    error_type = "permission_error"


class RateLimitExceededError(PrismError):
    status_code = 429
    code = "rate_limit_exceeded"
    error_type = "rate_limit_error"


class BudgetExceededError(PrismError):
    status_code = 402
    code = "budget_exceeded"
    error_type = "insufficient_quota"


class PricingNotFoundError(PrismError):
    status_code = 422
    code = "pricing_not_found"


class DatabaseUnavailableError(PrismError):
    status_code = 503
    code = "database_unavailable"
    error_type = "service_unavailable_error"


class InvalidAdminKeyError(PrismError):
    status_code = 401
    code = "invalid_admin_key"
    error_type = "authentication_error"


class AliasNotFoundError(PrismError):
    status_code = 404
    code = "model_not_found"


class RoutingNotImplementedError(PrismError):
    status_code = 422
    code = "routing_not_implemented"


class ProviderUnavailableError(PrismError):
    status_code = 503
    code = "provider_unavailable"
    error_type = "api_error"


class ProviderTimeoutError(PrismError):
    status_code = 504
    code = "provider_timeout"
    error_type = "api_error"
