# Phase 5 Verification — Operations, Hardening, and Delivery

Verified on 2026-11-12 using Docker Desktop, Python 3.12, PostgreSQL 16 with pgvector, two mock providers, and the production React/Nginx console image.

## Automated gates

| Gate | Result |
|---|---|
| Ruff formatting and lint | Passed |
| Strict Mypy | Passed |
| Python test suite | 61 passed |
| Branch coverage | 82.62% (80% required) |
| React/TypeScript production build | Passed |
| NPM audit | 0 vulnerabilities |
| Docker Compose production images | Built and started from an isolated empty volume |

## Smoke test

`scripts/smoke_test.py` passed nine end-to-end checks:

1. Database-backed readiness.
2. Tenant model allowlist.
3. Non-streaming completion through Mock A.
4. SSE completion ending in `[DONE]`.
5. Easy/fast and difficult/smart `auto` routing.
6. Planned 503 with ordered fallback to Mock B.
7. Miss followed by approved paraphrase cache hit.
8. Cross-tenant cache miss.
9. Admin operations API.

Live limit demonstrations returned HTTP 402 for the budget-limited key and `200, 200, 429` for the two-RPM key.

## Load evidence

Command:

```powershell
.\.venv\Scripts\python.exe scripts\load_test.py --requests 20 --concurrency 5
```

| Metric | Result |
|---|---:|
| Requests | 20 |
| Successful | 20 |
| Elapsed | 0.511 s |
| Throughput | 39.11 requests/s |
| Mean latency | 117.15 ms |
| p50 latency | 115.15 ms |
| p95 latency | 134.05 ms |
| Maximum latency | 180.41 ms |

This is local demonstration evidence, not a production capacity claim.

## Accounting reconciliation

`scripts/accounting_report.py` reported `all_counter_totals_match: true` for Team Alpha, Team Beta, RPM Demo, and Budget Demo. For every tenant:

- `admission_counters.spent_usd` matched settled reservations for the active month.
- `admission_counters.reserved_usd` matched pending reservations.
- No reservation required manual reconciliation at report time.

## Recovery evidence

The PostgreSQL integration suite proved:

- A held advisory lock causes a second reconciler instance to skip work.
- A stale reservation with no provider attempt is cancelled and released from `reserved_usd`.
- A stale reservation with a provider attempt is marked `reconciliation_required` with `ambiguous_billable_attempt` instead of silently guessing cost.

## Operations and console evidence

- `/ops/usage`, `/ops/requests`, `/ops/cache`, and `/ops/provider-attempts` require the distinct admin key.
- Pagination rejects limits above 100.
- Filters cover tenant, dates, status, provider, cache result, and fallback result.
- API responses and console views omit complete key material, digests, request fingerprints, and prompts.
- `/metrics` exposes requests, latency, cache, provider attempts, fallbacks, and settlement failures behind admin authentication.
- The Nginx-served console returned HTTP 200 and passed responsive browser inspection at a narrow viewport.

An isolated `prismfresh` Compose project was also built from the current source with no host ports and a new database volume. PostgreSQL became healthy, `/health/ready` returned HTTP 200, the console returned HTTP 200, and all four demo keys were seeded. The temporary project and volume were removed after verification; the normal development stack remained running.

## Groq provider update

The provider configuration maps `fast` to Groq `openai/gpt-oss-20b` and `smart` to Groq `openai/gpt-oss-120b`. A live non-streaming completion returned HTTP 200 from `groq` with visible answer content and usage, and a live stream returned 81 SSE events followed by `[DONE]`. The explicit `mock-fast` fault path returned HTTP 200 from Mock B with `x-prism-fallback: true`. The complete 61-test suite, including provider-secret isolation, seed cross-reference validation, Ruff, and strict Mypy pass. The real provider secret exists only in the ignored `.env` file.
