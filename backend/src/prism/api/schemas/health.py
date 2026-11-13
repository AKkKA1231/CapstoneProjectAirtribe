"""Health endpoint schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """A safe health response with no configuration secrets."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "unavailable"]
    service: str
    version: str
    checks: dict[str, str]
