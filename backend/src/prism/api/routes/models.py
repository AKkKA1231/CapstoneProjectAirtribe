"""Authenticated model discovery constrained by the tenant allowlist."""

from fastapi import APIRouter

from prism.api.dependencies import TenantDependency
from prism.api.schemas.models import ModelObject, ModelsResponse

router = APIRouter(prefix="/v1", tags=["models"])


@router.get("/models", response_model=ModelsResponse)
async def list_models(tenant: TenantDependency) -> ModelsResponse:
    return ModelsResponse(data=[ModelObject(id=alias) for alias in sorted(tenant.allowed_aliases)])
