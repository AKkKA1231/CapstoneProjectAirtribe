# Prism — LLM Gateway and Semantic Cache

Prism is a multi-tenant, OpenAI-compatible LLM gateway built as a modular FastAPI monolith. It demonstrates virtual-key security, atomic RPM and budget admission, provider retry/failover, true SSE streaming, explainable smart routing, tenant-safe semantic caching with PostgreSQL/pgvector, conservative recovery, and an operations console.

`fast` and `smart` use Groq's OpenAI-compatible API. Two local mock providers remain available for deterministic retry, fallback, and failure demonstrations.

## Architecture

```text
Client / Ops Console
        │
        ▼
FastAPI API boundary ── auth, validation, headers, admin policy
        │
        ▼
Application services ── admission, routing, cache, provider execution, recovery
        │
        ├── PostgreSQL 16 + pgvector (source of truth, locks, cache, telemetry)
        ├── Groq (real OpenAI-compatible upstream)
        └── Mock A / Mock B (deterministic fallback and test upstreams)
```

The code follows ports and adapters inside one deployable backend. See [Architecture.md](Docs/Architecture.md) for request flows, invariants, and trade-offs.

## Quick start on Windows

Requirements:

- Docker Desktop with the Linux engine running.
- Git.
- Optional for local development: Python 3.12–3.14 and Node.js 22+.

From the repository root:

```powershell
Copy-Item .env.example .env
# Edit .env and set GROQ_API_KEY to a key created at https://console.groq.com/keys
docker compose up --build -d
docker compose ps
```

If Docker is not on `PATH`, restart PowerShell after installing Docker Desktop. The services are:

| Service | URL |
|---|---|
| Gateway API | <http://localhost:8000> |
| Swagger UI | <http://localhost:8000/docs> |
| Operations console | <http://localhost:5173> |
| Mock Provider A | <http://localhost:8101/health> |
| Mock Provider B | <http://localhost:8102/health> |
| PostgreSQL | `localhost:5432` |

The Groq secret is read only by the gateway container from `GROQ_API_KEY`. Never commit `.env` or send the Groq key to Prism as the client credential.

Groq setup references: [create an API key](https://console.groq.com/keys), [OpenAI compatibility](https://console.groq.com/docs/openai), and [supported models and pricing](https://console.groq.com/docs/models).

Readiness check:

```powershell
Invoke-RestMethod http://localhost:8000/health/ready
```

## Local demonstration credentials

These values are deliberately non-production seed credentials committed for the local demo:

| Purpose | Credential |
|---|---|
| Team Alpha: `fast`, `smart`, `auto`, `mock-fast` | `prism-demo-team-alpha` |
| Team Beta: `fast`, `auto` | `prism-demo-team-beta` |
| Two-request RPM demonstration | `prism-demo-rpm-limited` |
| Budget rejection demonstration | `prism-demo-budget-limited` |
| Operations admin | `development-only-change-this-admin-key` |

Production startup rejects these development secrets and wildcard CORS.

There are two deliberately separate bearer-token boundaries:

- Clients authenticate to Prism with a virtual tenant key such as `prism-demo-team-alpha`.
- Prism authenticates outbound requests to Groq with the private `GROQ_API_KEY` environment variable.

This separation prevents an upstream provider secret from reaching browsers or tenants and preserves per-tenant limits, budgets, cache isolation, and audit records.

## Chat API

Non-streaming request:

```powershell
$headers = @{ Authorization = 'Bearer prism-demo-team-alpha' }
$body = @{
  model = 'auto'
  messages = @(@{ role = 'user'; content = 'Prove that square root of two is irrational.' })
  temperature = 0
  max_tokens = 128
} | ConvertTo-Json -Depth 5

Invoke-WebRequest http://localhost:8000/v1/chat/completions `
  -Method Post -Headers $headers -ContentType 'application/json' -Body $body
```

Every completion reports:

- `x-prism-provider`: selected provider or `cache`.
- `x-prism-cache`: `hit` or `miss`.
- `x-prism-fallback`: whether a fallback provider was used.
- `x-prism-cost-usd`: settled provider cost, or zero for cache hits.
- `x-prism-request-id`: request-log identifier.

Set `stream = $true` to receive SSE chunks. Successful streams end with `data: [DONE]`. Once any content is visible, Prism never changes providers; an interrupted committed stream is recorded and settled conservatively.

## Routing and failover

- `fast` resolves to Groq `openai/gpt-oss-20b`.
- `smart` resolves to Groq `openai/gpt-oss-120b`.
- `auto` uses the versioned, explainable `rules-v1` classifier.
- The classifier records tier, reason, confidence, and version with the request.
- Tenant policy can downgrade or override the classifier's physical tier.
- Retry and ordered fallback happen only before streaming commitment.

The Team Alpha-only `mock-fast` alias uses local providers for repeatable failure demonstrations. Use that alias with these prompt markers:

| Prompt marker | Behavior |
|---|---|
| `[[503]]` | Mock A fails transiently; `mock-fast` falls back to Mock B |
| `[[429]]` | Mock A rate-limits; retry/fallback path runs |
| `[[fatal]]` | Fatal non-retryable provider error |
| `[[stream-fail-after-first]]` | Stream closes after visible content; no fallback and no `[DONE]` |

Run the routing evidence:

```powershell
.\.venv\Scripts\python.exe scripts\routing_eval.py
```

The committed set scores 100% for `rules-v1`, 40% for the length-only baseline, and 100% on trap cases.

## Semantic cache

Only deterministic, low-temperature, complete non-tool responses are stored initially. Lookup requires exact matches on:

- Virtual key / tenant.
- Full conversation and system/developer context.
- All supported response-affecting generation options.
- Resolved routing tier.
- Cache schema, embedding model, and embedding version.
- TTL and configured minimum similarity.

The final user query is embedded separately, enabling approved paraphrases while preventing context/configuration sharing. Cached JSON can be converted to valid SSE. Partial streams and tool/function-call responses are not cached.

```powershell
.\.venv\Scripts\python.exe scripts\cache_eval.py
```

## Operations and recovery

Admin-protected endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /ops/usage` | Requests, tokens, spend, cache hits, and fallbacks by key |
| `GET /ops/requests` | Bounded, filtered recent requests |
| `GET /ops/cache` | Cache entries, hit counters, and request hit rate |
| `GET /ops/provider-attempts` | Physical attempts, retries, errors, and latency |
| `POST /ops/reconcile` | Run stale-reservation recovery under an advisory lock |
| `GET /metrics` | Admin-protected Prometheus text metrics |

Filters include tenant, time range, status, provider, cache result, and fallback result. Pagination is restricted to 1–100 rows per request. Responses expose only safe key prefixes—not raw keys, key digests, request fingerprints, prompts, or sanitized provider bodies.

The background reconciler also runs periodically:

- Stale reservations with no provider attempt are safely released.
- Reservations with any potentially billable attempt are marked `reconciliation_required`; Prism never guesses their cost.
- A PostgreSQL transaction-scoped advisory lock permits only one active reconciler.

Open <http://localhost:5173>, enter the local admin key, and inspect overview, recent requests, cache efficiency, and provider events.

## Verification

Install local backend tooling:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\backend[dev]"
```

Quality gates:

```powershell
.\.venv\Scripts\python.exe -m ruff format --check backend migrations mock-providers scripts
.\.venv\Scripts\python.exe -m ruff check backend migrations mock-providers scripts
.\.venv\Scripts\python.exe -m mypy backend\src
npm --prefix frontend run build
```

Run the full PostgreSQL test suite in Docker:

```powershell
docker build --target test -t prism-backend-test -f backend/Dockerfile .
docker run --rm --network capstoneprojectairtribe_default `
  -e PRISM_TEST_DATABASE_URL=postgresql+asyncpg://prism:prism@postgres:5432/prism `
  prism-backend-test
```

End-to-end evidence:

```powershell
.\.venv\Scripts\python.exe scripts\smoke_test.py
.\.venv\Scripts\python.exe scripts\load_test.py --requests 20 --concurrency 5
.\.venv\Scripts\python.exe scripts\accounting_report.py
```

See [Phase 4 verification](reports/phase4-verification.md) and [Phase 5 verification](reports/phase5-verification.md).

## Demonstration order

1. Start Compose and show readiness.
2. Send a successful non-streaming request.
3. Show streaming and `[DONE]`.
4. Compare easy and difficult `auto` routes.
5. Use `mock-fast` with `[[503]]` and show deterministic fallback to Mock B.
6. Repeat an approved paraphrase and show a zero-cost cache hit.
7. Send it with Team Beta and show a cache miss.
8. Send three requests with `prism-demo-rpm-limited`; the third returns 429.
9. Send one request with `prism-demo-budget-limited`; it returns 402.
10. Open the operations console and show usage, cache, and fallback records.

## Known limitations

- The local deterministic hash embedder is designed for reproducible tests and demos, not broad production semantic quality.
- Cache lookup uses exact tenant/scope-filtered cosine search; ANN indexing is deferred until data volume justifies it.
- Provider health does not yet drive an adaptive circuit breaker.
- In-process Prometheus counters reset when the gateway restarts; durable operational truth remains in PostgreSQL.
- The console accepts an admin key in memory; production deployments should place it behind proper operator identity and TLS.
- PostgreSQL is the single deployment dependency and source of truth; the initial design does not use Redis or multiple independently deployed backend services.

## Documentation

- [Problem statement](Docs/problemStatement.md)
- [Architecture](Docs/Architecture.md)
- [Data model](Docs/DataModel.md)
- [Implementation plan](Docs/implementationPlan.md)
- [Knowledge transfer](Docs/KNOWLEDGE_TRANSFER.md)
- [Explainer video script](Docs/explainer-video-script.md)
