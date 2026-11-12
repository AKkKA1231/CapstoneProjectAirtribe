# Knowledge Transfer & Agent Handover: Prism LLM Gateway and Semantic Cache

> **Purpose:** This document is the single source of truth for handover between agents (Antigravity, Codex, Cursor, Claude Code, etc.) and human developers. Any agent reading this file will immediately understand the project state, core invariants, architectural decisions, and next steps.

---

## 1. Project Identity & Goal

- **Project Name:** Prism LLM Gateway and Semantic Cache
- **Course / Track:** Airtribe AI-first Software Engineering Program (Capstone)
- **Primary Repo:** `github.com/airtribe-projects/prism-capstone` (local path: `c:\LOCAL_DISK_(D)\Projects\CapstoneProjectAirtribe`)
- **Core Mission:** Build a production-grade, multi-tenant, OpenAI-compatible LLM Gateway in Python that sits between client applications and upstream model providers.
- **Key Features:**
  1. **Unified Chat API:** `POST /v1/chat/completions` (OpenAI format).
  2. **Multi-Tenancy & Virtual Keys:** SHA-256 key hashing, per-key model allowlists, RPM limits, and hard monthly USD budgets.
  3. **Atomic Admission & Safe Accounting:** Concurrent-safe request gating via PostgreSQL row locking (`SELECT FOR UPDATE`), pre-flight cost reservation, and post-call settlement without holding DB locks during network I/O.
  4. **Smart Routing (`"model": "auto"`):** Difficulty classifier routing to `fast` or `smart` tier, proven to beat a length-only baseline on labeled eval traps.
  5. **Dynamic Failover & Retries:** Primary model with ordered fallback chain and exponential backoff. Strict rule: failover only permitted *before* any streaming tokens are emitted to the client.
  6. **True SSE Streaming Pass-Through:** Provider → Gateway → Client token-by-token pass-through, preserving backpressure, disconnect handling, terminating with `data: [DONE]`, and recording usage even on partial failures.
  7. **Tenant-Scoped Semantic Cache:** Vector embedding similarity search (`pgvector`), strictly isolated by `virtual_key_id` + configuration scope hash (`scope_hash`).
  8. **Observability & Ops Console:** Full audit logging (`request_logs`, `provider_attempts`), custom response header contract (`x-prism-*`), and a simple React ops dashboard.

---

## 2. Source Documents & Context Index

The repository's documentation lives in the `Docs/` directory:

| Document | Path | Purpose |
|---|---|---|
| **Problem Statement** | [`Docs/problemStatement.md`](./problemStatement.md) | Exhaustive breakdown of requirements, background, functional requirements (FR-01 to FR-14), non-functional requirements (NFR-01 to NFR-08), evaluation criteria, and deliverables. |
| **Data Model** | [`Docs/DataModel.md`](./DataModel.md) | Full PostgreSQL DDL for all 11 tables (`virtual_keys`, `admission_counters`, `budget_reservations`, `request_logs`, `provider_attempts`, `cache_entries` with `pgvector`, `model_pricing`, etc.). |
| **High Level Design** | [`Docs/HLD.md`](./HLD.md) | Analysis of 3 architectural approaches: **Approach A (Custom Async Monolith - Chosen/Recommended)**, Approach B (LiteLLM Hook Extension), Approach C (Microservices + Redis). |
| **Architecture** | [`Docs/Architecture.md`](./Architecture.md) | Accepted modular-monolith design, component boundaries, request flows, invariants, security, deployment, and testing architecture. |
| **Implementation Plan** | [`Docs/implementationPlan.md`](./implementationPlan.md) | Five-phase, ten-day delivery plan with tasks, definitions of done, verification gates, risks, and demonstration milestones. |
| **Knowledge Transfer** | *This file (`Docs/KNOWLEDGE_TRANSFER.md`)* | Bridge for Codex/Antigravity handover. |

*Original Airtribe reference PDFs:*
- Case Study Brief: `FYFAE3H0GVK9QZ6O.pdf`
- Implementation Plan: `Airtribe_Project_Implementation_Briefs.pdf` (Page 2)

---

## 3. Core Technical Decisions & Non-Negotiables

### 3.1 Architectural Selection: Approach A (Modular Monolith with Ports and Adapters)
- **Framework:** Python 3.12+ with **FastAPI** + `asyncio` + `uvicorn`.
- **Database:** **PostgreSQL 16** with **`pgvector`** extension (used for relational tables, counters, logs, and vector semantic cache).
- **HTTP Client:** `httpx.AsyncClient` with custom timeouts per provider.
- **Internal boundaries:** API, application, domain, ports, and infrastructure adapters remain separate even though the backend is deployed as one service.
- **Why Approach A:** Direct ownership and visibility of the admission locking, streaming pipeline, error contracts, and custom headers required for the capstone assessment.

### 3.2 Groq Provider Key and Mocks
- Normal `fast` and `smart` traffic uses Groq through `https://api.groq.com/openai/v1` and requires `GROQ_API_KEY` in the ignored `.env` file.
- Clients never receive or submit the Groq secret. They authenticate to Prism with tenant-scoped virtual keys such as `prism-demo-team-alpha`.
- Two local mock providers remain available through the explicit `mock-fast` alias to simulate:
  - Streaming SSE tokens
  - Latency and timeouts
  - 429 Rate Limits and 500 Failures (to trigger fallback)
- Groq and the mocks share the same provider adapter, retry, logging, streaming, accounting, and settlement path.

### 3.3 The Response Header Contract
Every response from `/v1/chat/completions` (including cached and fallback responses) MUST return these HTTP headers:
- `x-prism-provider`: Upstream provider name used (e.g. `mock-openai`, `mock-anthropic`, or `cache`)
- `x-prism-cache`: `hit` | `miss`
- `x-prism-fallback`: `true` | `false`
- `x-prism-cost-usd`: Estimated or actual cost in USD (formatted e.g. `0.00045000`)

### 3.4 Concurrency & Admission Invariants
- **Atomic check-and-reserve:** 
  1. Acquire lock: `SELECT ... FROM admission_counters WHERE key_id = $1 FOR UPDATE;`
  2. Validate RPM window (sliding window or 60-second rollover).
  3. Validate budget: `spent_usd + reserved_usd + conservative_estimate <= monthly_budget_usd`.
  4. Update `reserved_usd += conservative_estimate` and commit transaction immediately.
  5. **NEVER hold the database lock while making the upstream network call.**
  6. Upon completion (or failure), settle the reservation against actual token usage.

### 3.5 Semantic Cache Scoping
- Semantic lookups MUST be scoped by:
  `WHERE key_id = :key_id AND scope_hash = :scope_hash`
- `scope_hash` is derived from deterministic serialization of the full response-affecting context: complete conversation, instructions, resolved routing policy, generation settings, tools, response format, and cache/embedding schema versions.
- Prevents cross-tenant data leaks and prevents serving responses generated under different system instructions.

---

## 4. Current Progress & Roadmap

### What is Completed:
- [x] Analyzed original Airtribe case study and implementation briefs (`FYFAE3H0GVK9QZ6O.pdf`).
- [x] Generated comprehensive `problemStatement.md`.
- [x] Generated production-ready `DataModel.md` (DDL, indexing, `pgvector`, settlement schemas).
- [x] Generated `HLD.md` evaluating 3 architectural designs and selecting Approach A.
- [x] Generated `Architecture.md` with the accepted modular-monolith design and detailed request flows.
- [x] Generated `implementationPlan.md` with five phases across ten focused delivery days.
- [x] Clarified provider dependencies (zero paid key requirement; mock-first strategy).
- [x] Implemented the Phase 1 backend foundation: FastAPI package structure, validated settings, structured logging, shared HTTP/database lifecycle, health endpoints, quality tooling, and CI.
- [x] Added the refined SQLAlchemy schema, initial Alembic migration, PostgreSQL/pgvector Compose service, validated seed configuration, and idempotent seed loader.
- [x] Verified Ruff, strict Mypy, Alembic offline rendering, 19 tests, and the 80% coverage gate (83.73% achieved).
- [x] Completed the live-infrastructure gate on Docker Desktop: PostgreSQL is healthy, pgvector is enabled, Alembic is at `0001_initial_schema`, and health endpoints return `ok`. The current seed contains Groq plus two deterministic mock providers, four aliases, and four virtual keys.
- [x] Completed Phase 2 security: HMAC-SHA-256 virtual-key authentication, distinct missing/invalid/inactive/expired errors, safe security-event persistence, admin-key separation, and tenant alias allowlists.
- [x] Added authenticated OpenAI-compatible `GET /v1/models`; Team Alpha also receives the test-only `mock-fast` alias, while Team Beta remains limited to `auto`/`fast`.
- [x] Completed Phase 2 atomic accounting: decimal cost estimates, pricing lookup, row-locked RPM/monthly-budget admission, committed reservations, idempotent settlement, and non-billable release.
- [x] Verified Phase 2 with 32 passing tests on Python 3.12 and PostgreSQL, including a 10-request concurrency race with zero over-admission; coverage is 85.21%.
- [x] Completed Phase 3 provider execution with a provider-neutral contract, pooled OpenAI-compatible HTTP adapter, bounded retry/backoff, ordered failover, and one persisted record per physical provider call.
- [x] Added two separate deterministic HTTP mock-provider containers supporting success, 429/503, fatal errors, malformed responses, streaming, usage, and incomplete-stream scenarios.
- [x] Added authenticated `POST /v1/chat/completions` for non-streaming JSON and true SSE streaming, including all required `x-prism-*` headers.
- [x] Enforced the stream-commitment invariant: pre-content failures may retry/fail over; post-content failures never do, never emit `[DONE]`, and settle conservatively as `partial_stream_error`.
- [x] Verified Phase 3 with 43 passing Python 3.12 tests and 81.41% coverage plus live Docker demonstrations of primary success, fallback success, clean streaming, partial-stream failure, and fatal non-billable release.
- [x] Completed Phase 4 intelligent routing with deterministic `fast`/`smart` aliases, the explainable versioned `rules-v1` `auto` classifier, tenant-policy downgrade/override behavior, and persisted reason/confidence/version telemetry.
- [x] Added committed routing evaluation cases and `scripts/routing_eval.py`; the classifier scores 100% on 10 cases and all traps versus 40% for the length-only baseline.
- [x] Completed the tenant-scoped semantic cache using a deterministic 1,536-dimension local embedding adapter, exact pgvector cosine search, TTL, conservative temperature eligibility, schema/model version filtering, hit counters, and full configuration scope hashing.
- [x] Added JSON and SSE cache-hit replay, zero-cost reservation release, cache response headers, request-log linkage, tool-call exclusion, and fail-open behavior for lookup/store infrastructure errors.
- [x] Verified Phase 4 with 55 passing Python 3.12 tests and 80.89% branch coverage, including real PostgreSQL/pgvector checks for paraphrase similarity, negative near-miss rejection, TTL, hit counters, cross-key isolation, and configuration isolation.
- [x] Live Docker verification proved Alpha `auto` reasoning routes to `smart`/Mock B, Beta policy downgrades it to `fast`/Mock A, a paraphrase returns a zero-cost cache hit, other tenants and contexts miss, and cached streams end with `[DONE]`.
- [x] Completed Phase 5 admin operations APIs for usage, recent requests, cache statistics, and provider attempts with bounded pagination and tenant/date/status/provider/cache/fallback filters.
- [x] Added the responsive React/Vite operations console, production Nginx image, safe in-memory admin authentication, and loading/empty/error/filter states without exposing keys, digests, fingerprints, or prompts.
- [x] Added periodic and on-demand stale-reservation reconciliation protected by a PostgreSQL advisory lock; no-attempt reservations are released and potentially billable attempts are explicitly flagged.
- [x] Added explicit CORS, structured request metrics, admin-protected Prometheus exposition, provider-attempt/cache/fallback metrics, settlement-failure counters, and database readiness.
- [x] Added reproducible smoke, load, routing, cache, and accounting scripts plus Phase 4/5 verification reports, the root README, and the explainer-video script.
- [x] Final verification: 61 passing Python 3.12 tests, 82.62% branch coverage, strict Mypy/Ruff clean, React production build clean, nine smoke paths passed, 20/20 load requests succeeded at 134.05 ms p95, all accounting counter totals matched, and an isolated clean-volume Docker Compose startup passed.

### What Needs to Be Done Next:
1. **Prepare submission and version control**:
   - Review the complete working tree, create a feature commit, and push it to the intended GitHub branch.
   - Record the final explainer video using `Docs/explainer-video-script.md` and the exact demonstration order in the README.
   - Replace local development credentials and configure TLS/operator identity before any non-local deployment.

---

## 5. Working Together: Antigravity & Codex Synchronization

### Can Codex and Antigravity Work Together Simultaneously?
**YES, seamlessly.** Both Antigravity and Codex operate on the **same local file system** (`c:\LOCAL_DISK_(D)\Projects\CapstoneProjectAirtribe`).

### Recommended Co-Working Guidelines:
1. **The Git Repo / Filesystem is the Single Source of Truth:**
   - Any file created or modified by Codex is immediately visible to Antigravity, and vice versa.
2. **Avoid Race Conditions on the Same File:**
   - Don't ask both tools to edit the exact same file at the exact same moment.
   - Commit often (`git add . && git commit -m "..."`) so changes can be tracked, diffed, or reverted cleanly.
3. **Use the `Docs/` folder as the Context Anchor:**
   - Whenever starting a new session with Codex (or Antigravity), tell the agent:
     > *"We are building the Prism LLM Gateway. Read `Docs/KNOWLEDGE_TRANSFER.md` and the existing files in `Docs/` to see our current state before taking the next step."*

---

## 6. Ready-to-Use Prompt for Codex

When opening Codex in this project directory, paste the following prompt:

```text
Hello! We are working on the "Prism: LLM Gateway and Semantic Cache" Airtribe Capstone project in this workspace.

Please do the following before writing code:
1. Read `Docs/KNOWLEDGE_TRANSFER.md` to understand our architecture, tech stack, and progress.
2. Review `Docs/problemStatement.md`, `Docs/DataModel.md`, `Docs/HLD.md`, `Docs/Architecture.md`, and `Docs/implementationPlan.md`.
3. Note our key architectural choices:
   - Modular monolith with ports and adapters: FastAPI + PostgreSQL 16 + pgvector.
   - Strict atomic admission using `SELECT FOR UPDATE` on admission_counters (never hold locks over network calls).
   - Mocks are sufficient for the 2 providers; no OpenRouter key needed.
   - Required headers: x-prism-provider, x-prism-cache, x-prism-fallback, x-prism-cost-usd.

Please confirm you have digested this context, then continue with Phase 4 from `Docs/implementationPlan.md`.
```

