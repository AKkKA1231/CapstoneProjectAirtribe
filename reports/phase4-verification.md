# Phase 4 Verification — Intelligent Routing and Semantic Cache

Verified on 2026-11-12 against the Docker Compose PostgreSQL 16/pgvector environment.

## Automated quality gate

- Ruff: passed.
- Strict Mypy: passed for the backend package.
- Pytest: 55 passed.
- Branch coverage: 80.89% (project gate: 80%).
- PostgreSQL integration includes real pgvector cosine lookup, tenant/scope filtering, TTL, and hit counters.

## Routing evaluation

Reproduce with:

```powershell
.\.venv\Scripts\python.exe scripts\routing_eval.py
```

| Metric | Result |
|---|---:|
| Evaluation cases | 10 |
| `rules-v1` accuracy | 100% |
| Length-only baseline accuracy | 40% |
| Trap-case accuracy | 100% |

The trap set includes short difficult prompts and long trivial prompts. Every decision records the resolved tier, reason code, confidence, auto-route flag, and classifier version.

## Cache threshold evaluation

Reproduce with:

```powershell
.\.venv\Scripts\python.exe scripts\cache_eval.py
```

| Case | Cosine similarity | Threshold | Result |
|---|---:|---:|---|
| Approved paraphrase | 1.0000 | 0.82 | Hit |
| Negative near-match | 0.1260 | 0.82 | Miss |

The local hash embedder is deterministic, versioned, normalized, and produces exactly 1,536 dimensions. It is a reproducible development/demo adapter, not a claim of production-quality semantic understanding; the embedding port allows a production model to replace it.

## Live gateway evidence

| Scenario | Provider | Cache | Cost | Result |
|---|---|---|---:|---|
| Team Alpha, short proof through `auto` | `mock-b` | miss | 0.0000372000 | Routed to `smart` |
| Team Beta, same proof through `auto` | `mock-a` | miss | 0.0000031000 | Policy downgrade to `fast` |
| First eligible request | `mock-a` | miss | 0.0000025000 | Stored complete response |
| Approved paraphrase | `cache` | hit | 0 | Replayed cached response |
| Same paraphrase, different tenant | `mock-a` | miss | 0.0000025000 | Tenant isolation preserved |
| Same paraphrase, different system message | `mock-a` | miss | 0.0000034000 | Configuration isolation preserved |
| Streaming repeat | `cache` | hit | 0 | Five valid SSE frames ending in `[DONE]` |

Recent request logs persisted `request_alias`, `resolved_alias`, `auto_routed`, routing tier/reason/confidence/version, `cache_hit`, status, cost, and streaming mode. The exercised cache entry recorded three hits and remained active under TTL.

## Deliberate initial limitations

- Only low-temperature requests are cache eligible (default maximum `0.2`).
- Partial streams are never cached.
- Tool/function-call responses are excluded until safe replay semantics are defined.
- Lookup is exact tenant/scope-filtered cosine search; ANN indexing is deferred until data volume justifies it.
