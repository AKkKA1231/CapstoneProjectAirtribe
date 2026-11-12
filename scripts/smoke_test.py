"""End-to-end smoke test for the running Prism Docker Compose environment."""

import argparse
import asyncio
import json
from typing import Any
from uuid import uuid4

import httpx


def headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def chat(
    client: httpx.AsyncClient,
    key: str,
    *,
    model: str,
    prompt: str,
    stream: bool = False,
) -> httpx.Response:
    return await client.post(
        "/v1/chat/completions",
        headers=headers(key),
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 64,
            "stream": stream,
        },
    )


async def run(base_url: str, admin_key: str) -> dict[str, Any]:
    alpha = "prism-demo-team-alpha"
    beta = "prism-demo-team-beta"
    tag = uuid4().hex[:10]
    checks: list[dict[str, Any]] = []

    def record(name: str, passed: bool, **evidence: Any) -> None:
        checks.append({"name": name, "passed": passed, **evidence})
        if not passed:
            raise AssertionError(f"smoke check failed: {name}: {evidence}")

    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        ready = await client.get("/health/ready")
        record("readiness", ready.status_code == 200, status=ready.status_code)

        models = await client.get("/v1/models", headers=headers(alpha))
        model_ids = [item["id"] for item in models.json().get("data", [])]
        record(
            "model allowlist",
            model_ids == ["auto", "fast", "mock-fast", "smart"],
            models=model_ids,
        )

        basic = await chat(client, alpha, model="fast", prompt=f"Hello smoke {tag}")
        record(
            "non-stream completion",
            basic.status_code == 200
            and basic.headers.get("x-prism-provider") == "groq",
            provider=basic.headers.get("x-prism-provider"),
        )

        streamed = await chat(
            client, alpha, model="fast", prompt=f"Stream this smoke {tag}", stream=True
        )
        record(
            "SSE completion",
            streamed.status_code == 200
            and streamed.text.rstrip().endswith("data: [DONE]"),
            done=streamed.text.rstrip().endswith("data: [DONE]"),
        )

        easy = await chat(
            client, alpha, model="auto", prompt=f"List three colors {tag}"
        )
        hard = await chat(
            client,
            alpha,
            model="auto",
            prompt=f"Prove that square root of two is irrational {tag}",
        )
        record(
            "auto routing",
            easy.headers.get("x-prism-provider") == "groq"
            and hard.headers.get("x-prism-provider") == "groq"
            and easy.json().get("model") == "openai/gpt-oss-20b"
            and hard.json().get("model") == "openai/gpt-oss-120b",
            easy_provider=easy.headers.get("x-prism-provider"),
            hard_provider=hard.headers.get("x-prism-provider"),
            easy_model=easy.json().get("model"),
            hard_model=hard.json().get("model"),
        )

        fallback = await chat(
            client, alpha, model="mock-fast", prompt=f"Recover from [[503]] {tag}"
        )
        record(
            "ordered fallback",
            fallback.status_code == 200
            and fallback.headers.get("x-prism-provider") == "mock-b"
            and fallback.headers.get("x-prism-fallback") == "true",
            provider=fallback.headers.get("x-prism-provider"),
            fallback=fallback.headers.get("x-prism-fallback"),
        )

        source = f"Explain semantic caching for an automobile smoke-{tag}"
        paraphrase = f"Describe semantic cache for a car smoke-{tag}"
        first = await chat(client, alpha, model="fast", prompt=source)
        hit = await chat(client, alpha, model="fast", prompt=paraphrase)
        isolated = await chat(client, beta, model="fast", prompt=paraphrase)
        record(
            "semantic cache hit",
            first.headers.get("x-prism-cache") == "miss"
            and hit.headers.get("x-prism-cache") == "hit"
            and hit.headers.get("x-prism-cost-usd") == "0",
            first=first.headers.get("x-prism-cache"),
            second=hit.headers.get("x-prism-cache"),
        )
        record(
            "cross-tenant cache isolation",
            isolated.headers.get("x-prism-cache") == "miss",
            beta_cache=isolated.headers.get("x-prism-cache"),
        )

        ops = await client.get("/ops/usage", headers=headers(admin_key))
        record(
            "admin operations API",
            ops.status_code == 200 and isinstance(ops.json(), list),
            status=ops.status_code,
            tenants=len(ops.json()) if ops.status_code == 200 else 0,
        )

    return {"base_url": base_url, "tag": tag, "passed": len(checks), "checks": checks}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--admin-key", default="development-only-change-this-admin-key")
    args = parser.parse_args()
    report = asyncio.run(run(args.base_url, args.admin_key))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
