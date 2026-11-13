"""Bounded concurrent gateway load test with machine-readable latency evidence."""

import argparse
import asyncio
import json
from statistics import mean
from time import perf_counter
from uuid import uuid4

import httpx


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return ordered[index]


async def run(base_url: str, requests: int, concurrency: int) -> dict[str, object]:
    semaphore = asyncio.Semaphore(concurrency)
    tag = uuid4().hex[:8]

    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:

        async def one(index: int) -> tuple[int, float]:
            async with semaphore:
                started = perf_counter()
                response = await client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": "Bearer prism-demo-team-alpha"},
                    json={
                        "model": "fast",
                        "messages": [
                            {
                                "role": "user",
                                "content": f"Load test {tag} request {index}",
                            }
                        ],
                        "temperature": 0.7,
                        "max_tokens": 16,
                    },
                )
                return response.status_code, (perf_counter() - started) * 1000

        started = perf_counter()
        results = await asyncio.gather(*(one(index) for index in range(requests)))
        elapsed = perf_counter() - started

    statuses: dict[int, int] = {}
    latencies = []
    for status, latency in results:
        statuses[status] = statuses.get(status, 0) + 1
        latencies.append(latency)
    return {
        "requests": requests,
        "concurrency": concurrency,
        "elapsed_seconds": elapsed,
        "throughput_rps": requests / elapsed,
        "status_counts": statuses,
        "latency_ms": {
            "mean": mean(latencies),
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": max(latencies),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.requests <= 60 or not 1 <= args.concurrency <= 20:
        raise SystemExit("requests must be 1..60 and concurrency must be 1..20")
    print(
        json.dumps(
            asyncio.run(run(args.base_url, args.requests, args.concurrency)), indent=2
        )
    )


if __name__ == "__main__":
    main()
