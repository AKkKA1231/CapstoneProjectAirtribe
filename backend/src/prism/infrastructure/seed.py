"""Validated, idempotent seed loading for local and demonstration environments."""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from prism.adapters.database.session import DatabaseManager
from prism.adapters.database.tables import (
    AdmissionCounter,
    FallbackTarget,
    ModelAlias,
    ModelPricing,
    Provider,
    VirtualKey,
    VirtualKeyAllowedAlias,
)
from prism.domain.security import digest_virtual_key
from prism.infrastructure.config import Settings, get_settings
from prism.infrastructure.logging import configure_logging

logger = structlog.get_logger(__name__)


class StrictSeedModel(BaseModel):
    """Base model that rejects misspelled seed fields."""

    model_config = ConfigDict(extra="forbid")


class ProviderSeed(StrictSeedModel):
    name: str
    base_url: str
    api_key_env_var: str | None = None
    timeout_ms: int = Field(default=30_000, gt=0)


class TargetSeed(StrictSeedModel):
    provider: str
    model: str


class AliasSeed(StrictSeedModel):
    alias: str
    tier: str
    primary: TargetSeed | None
    fallbacks: list[TargetSeed] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_target(self) -> "AliasSeed":
        if self.tier == "auto" and self.primary is not None:
            raise ValueError("auto aliases cannot have a physical primary target")
        if self.tier != "auto" and self.primary is None:
            raise ValueError("non-auto aliases require a physical primary target")
        return self


class CacheSeed(StrictSeedModel):
    enabled: bool = True
    ttl_seconds: int = Field(default=86_400, gt=0)
    similarity_threshold: float = Field(default=0.92, ge=0, le=1)
    schema_version: int = Field(default=1, gt=0)


class GatewaySeed(StrictSeedModel):
    providers: list[ProviderSeed]
    aliases: list[AliasSeed]
    cache: CacheSeed


class PricingSeed(StrictSeedModel):
    provider: str
    model: str
    input_cost_per_1k: Decimal = Field(ge=0)
    output_cost_per_1k: Decimal = Field(ge=0)


class VirtualKeySeed(StrictSeedModel):
    key: str = Field(min_length=16)
    tenant_name: str
    description: str | None = None
    allowed_aliases: list[str]
    rpm_limit: int = Field(gt=0)
    monthly_budget_usd: Decimal = Field(ge=0)


class SeedBundle(StrictSeedModel):
    gateway: GatewaySeed
    pricing: list[PricingSeed]
    keys: list[VirtualKeySeed]


def load_seed_bundle(
    config_dir: Path,
    gateway_file: str = "gateway_config.sample.json",
    pricing_file: str = "model_pricing.json",
    keys_file: str = "seed_keys.json",
) -> SeedBundle:
    """Load and validate all seed files before opening a database transaction."""

    gateway = GatewaySeed.model_validate_json(
        (config_dir / gateway_file).read_text(encoding="utf-8")
    )
    pricing = [
        PricingSeed.model_validate(item)
        for item in json.loads((config_dir / pricing_file).read_text(encoding="utf-8"))
    ]
    keys = [
        VirtualKeySeed.model_validate(item)
        for item in json.loads((config_dir / keys_file).read_text(encoding="utf-8"))
    ]

    provider_names = {provider.name for provider in gateway.providers}
    alias_names = {alias.alias for alias in gateway.aliases}
    for alias in gateway.aliases:
        targets = ([alias.primary] if alias.primary is not None else []) + alias.fallbacks
        unknown = {target.provider for target in targets} - provider_names
        if unknown:
            raise ValueError(f"alias {alias.alias!r} references unknown providers: {unknown}")
    for item in pricing:
        if item.provider not in provider_names:
            raise ValueError(f"pricing references unknown provider: {item.provider!r}")
    for key in keys:
        unknown = set(key.allowed_aliases) - alias_names
        if unknown:
            raise ValueError(f"key {key.tenant_name!r} references unknown aliases: {unknown}")

    return SeedBundle(gateway=gateway, pricing=pricing, keys=keys)


async def _seed_providers(
    session: AsyncSession, providers: list[ProviderSeed]
) -> dict[str, object]:
    provider_ids: dict[str, object] = {}
    for provider in providers:
        statement = (
            insert(Provider)
            .values(
                name=provider.name,
                base_url=provider.base_url,
                api_key_env_var=provider.api_key_env_var,
                timeout_ms=provider.timeout_ms,
                is_active=True,
            )
            .on_conflict_do_update(
                index_elements=[Provider.name],
                set_={
                    "base_url": provider.base_url,
                    "api_key_env_var": provider.api_key_env_var,
                    "timeout_ms": provider.timeout_ms,
                    "is_active": True,
                    "updated_at": datetime.now(UTC),
                },
            )
            .returning(Provider.id)
        )
        provider_ids[provider.name] = (await session.execute(statement)).scalar_one()
    return provider_ids


async def _seed_aliases(
    session: AsyncSession,
    aliases: list[AliasSeed],
    provider_ids: dict[str, object],
) -> dict[str, object]:
    alias_ids: dict[str, object] = {}
    for alias in aliases:
        primary_provider_id = (
            provider_ids[alias.primary.provider] if alias.primary is not None else None
        )
        primary_model = alias.primary.model if alias.primary is not None else None
        statement = (
            insert(ModelAlias)
            .values(
                alias=alias.alias,
                tier=alias.tier,
                primary_provider_id=primary_provider_id,
                primary_model=primary_model,
                is_active=True,
            )
            .on_conflict_do_update(
                index_elements=[ModelAlias.alias],
                set_={
                    "tier": alias.tier,
                    "primary_provider_id": primary_provider_id,
                    "primary_model": primary_model,
                    "is_active": True,
                },
            )
            .returning(ModelAlias.id)
        )
        alias_id = (await session.execute(statement)).scalar_one()
        alias_ids[alias.alias] = alias_id

        await session.execute(delete(FallbackTarget).where(FallbackTarget.alias_id == alias_id))
        for priority, fallback in enumerate(alias.fallbacks, start=1):
            session.add(
                FallbackTarget(
                    alias_id=alias_id,
                    priority=priority,
                    provider_id=provider_ids[fallback.provider],
                    model=fallback.model,
                )
            )
    return alias_ids


async def _seed_pricing(
    session: AsyncSession,
    pricing: list[PricingSeed],
    provider_ids: dict[str, object],
) -> None:
    for item in pricing:
        provider_id = provider_ids[item.provider]
        existing_id = await session.scalar(
            select(ModelPricing.id).where(
                ModelPricing.provider_id == provider_id,
                ModelPricing.model == item.model,
                ModelPricing.effective_until.is_(None),
            )
        )
        if existing_id is None:
            session.add(
                ModelPricing(
                    provider_id=provider_id,
                    model=item.model,
                    input_cost_per_1k=item.input_cost_per_1k,
                    output_cost_per_1k=item.output_cost_per_1k,
                )
            )
        else:
            await session.execute(
                update(ModelPricing)
                .where(ModelPricing.id == existing_id)
                .values(
                    input_cost_per_1k=item.input_cost_per_1k,
                    output_cost_per_1k=item.output_cost_per_1k,
                )
            )


async def _seed_virtual_keys(
    session: AsyncSession,
    keys: list[VirtualKeySeed],
    alias_ids: dict[str, object],
    settings: Settings,
) -> None:
    now = datetime.now(UTC)
    budget_month = now.strftime("%Y-%m")
    pepper = settings.auth_pepper.get_secret_value()

    for item in keys:
        digest = digest_virtual_key(item.key, pepper)
        statement = (
            insert(VirtualKey)
            .values(
                key_digest=digest,
                key_prefix=item.key[:12],
                tenant_name=item.tenant_name,
                description=item.description,
                rpm_limit=item.rpm_limit,
                monthly_budget_usd=item.monthly_budget_usd,
                is_active=True,
            )
            .on_conflict_do_update(
                index_elements=[VirtualKey.key_digest],
                set_={
                    "tenant_name": item.tenant_name,
                    "description": item.description,
                    "rpm_limit": item.rpm_limit,
                    "monthly_budget_usd": item.monthly_budget_usd,
                    "is_active": True,
                    "updated_at": now,
                },
            )
            .returning(VirtualKey.id)
        )
        key_id = (await session.execute(statement)).scalar_one()

        await session.execute(
            delete(VirtualKeyAllowedAlias).where(VirtualKeyAllowedAlias.key_id == key_id)
        )
        session.add_all(
            [
                VirtualKeyAllowedAlias(key_id=key_id, alias_id=alias_ids[alias])
                for alias in item.allowed_aliases
            ]
        )
        await session.execute(
            insert(AdmissionCounter)
            .values(
                key_id=key_id,
                rpm_window_start=now,
                rpm_count=0,
                budget_month=budget_month,
                spent_usd=Decimal("0"),
                reserved_usd=Decimal("0"),
            )
            .on_conflict_do_nothing(index_elements=[AdmissionCounter.key_id])
        )


async def seed_database(settings: Settings, bundle: SeedBundle) -> None:
    """Apply all seed data atomically and idempotently."""

    database = DatabaseManager(settings)
    try:
        async with database.session() as session, session.begin():
            # Treat the selected seed profile as the active routing source of truth.
            # Historical rows remain available for audit foreign keys, but cannot route traffic.
            await session.execute(update(ModelAlias).values(is_active=False))
            await session.execute(update(Provider).values(is_active=False))
            provider_ids = await _seed_providers(session, bundle.gateway.providers)
            alias_ids = await _seed_aliases(session, bundle.gateway.aliases, provider_ids)
            await _seed_pricing(session, bundle.pricing, provider_ids)
            await _seed_virtual_keys(session, bundle.keys, alias_ids, settings)
    finally:
        await database.close()


async def run() -> None:
    """Load settings, validate files, and seed the configured database."""

    settings = get_settings()
    configure_logging(settings)
    bundle = load_seed_bundle(
        settings.seed_config_dir,
        gateway_file=settings.seed_gateway_file,
        pricing_file=settings.seed_pricing_file,
        keys_file=settings.seed_keys_file,
    )
    if settings.bootstrap_tenant_key is not None:
        bundle.keys.append(
            VirtualKeySeed(
                key=settings.bootstrap_tenant_key.get_secret_value(),
                tenant_name=settings.bootstrap_tenant_name,
                description="Environment-provisioned production tenant key",
                allowed_aliases=["fast", "smart", "auto"],
                rpm_limit=settings.bootstrap_tenant_rpm_limit,
                monthly_budget_usd=settings.bootstrap_tenant_monthly_budget_usd,
            )
        )
    await seed_database(settings, bundle)
    logger.info(
        "seed_completed",
        providers=len(bundle.gateway.providers),
        aliases=len(bundle.gateway.aliases),
        keys=len(bundle.keys),
    )


def main() -> None:
    """Console entry point."""

    asyncio.run(run())


if __name__ == "__main__":
    main()
