"""Persistence metadata and seed-configuration tests."""

import json
from pathlib import Path

from prism.adapters.database.tables import Base

EXPECTED_TABLES = {
    "admission_counters",
    "budget_reservations",
    "cache_entries",
    "fallback_chains",
    "model_aliases",
    "model_pricing",
    "provider_attempts",
    "providers",
    "request_logs",
    "routing_eval_cases",
    "security_events",
    "virtual_key_allowed_aliases",
    "virtual_keys",
}


def test_metadata_contains_the_complete_foundation_schema() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_auto_alias_can_exist_without_a_physical_provider_target() -> None:
    alias_table = Base.metadata.tables["model_aliases"]

    assert alias_table.c.primary_provider_id.nullable is True
    assert alias_table.c.primary_model.nullable is True


def test_request_log_cache_reference_is_tenant_safe_and_nullable() -> None:
    request_table = Base.metadata.tables["request_logs"]
    cache_table = Base.metadata.tables["cache_entries"]

    assert request_table.c.cache_entry_id.nullable is True
    assert cache_table.c.key_id.nullable is False
    assert cache_table.c.scope_hash.nullable is False


def test_seed_configuration_files_are_valid_and_reference_known_providers() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    config_dir = repository_root / "config"

    gateway = json.loads((config_dir / "gateway_config.sample.json").read_text("utf-8"))
    pricing = json.loads((config_dir / "model_pricing.json").read_text("utf-8"))
    keys = json.loads((config_dir / "seed_keys.json").read_text("utf-8"))

    provider_names = {provider["name"] for provider in gateway["providers"]}
    alias_names = {alias["alias"] for alias in gateway["aliases"]}

    assert provider_names == {"groq", "mock-a", "mock-b"}
    assert alias_names == {"fast", "smart", "auto", "mock-fast"}
    assert all(item["provider"] in provider_names for item in pricing)
    assert all(set(item["allowed_aliases"]) <= alias_names for item in keys)
