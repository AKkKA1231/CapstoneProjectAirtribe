"""Seed validation and key-digest tests."""

from pathlib import Path

from prism.infrastructure.seed import digest_virtual_key, load_seed_bundle


def test_seed_bundle_loads_and_cross_references_configuration() -> None:
    repository_root = Path(__file__).resolve().parents[3]

    bundle = load_seed_bundle(repository_root / "config")

    assert {provider.name for provider in bundle.gateway.providers} == {
        "groq",
        "mock-a",
        "mock-b",
    }
    assert {alias.alias for alias in bundle.gateway.aliases} == {
        "fast",
        "smart",
        "auto",
        "mock-fast",
    }
    assert len(bundle.pricing) == 6
    assert len(bundle.keys) == 4


def test_virtual_key_digest_is_deterministic_and_pepper_scoped() -> None:
    raw_key = "prism-demo-team-alpha"

    first = digest_virtual_key(raw_key, "pepper-a")
    second = digest_virtual_key(raw_key, "pepper-a")
    different_pepper = digest_virtual_key(raw_key, "pepper-b")

    assert first == second
    assert first != different_pepper
    assert len(first) == 64
    assert raw_key not in first
