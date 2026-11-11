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
| **Knowledge Transfer** | *This file (`Docs/KNOWLEDGE_TRANSFER.md`)* | Bridge for Codex/Antigravity handover. |

*Original Airtribe reference PDFs:*
- Case Study Brief: `FYFAE3H0GVK9QZ6O.pdf`
- Implementation Plan: `Airtribe_Project_Implementation_Briefs.pdf` (Page 2)

---

## 3. Core Technical Decisions & Non-Negotiables

### 3.1 Architectural Selection: Approach A (Custom Async Python Monolith)
- **Framework:** Python 3.12+ with **FastAPI** + `asyncio` + `uvicorn`.
- **Database:** **PostgreSQL 16** with **`pgvector`** extension (used for relational tables, counters, logs, and vector semantic cache).
- **HTTP Client:** `httpx.AsyncClient` with custom timeouts per provider.
- **Why Approach A:** Direct ownership and visibility of the admission locking, streaming pipeline, error contracts, and custom headers required for the capstone assessment.

### 3.2 Provider Keys & Mocks (Crucial Clarification)
- **Do we need OpenRouter or paid external API keys?** **NO.**
- The project requirement explicitly states: *"One OpenAI-compatible POST /v1/chat/completions endpoint in front of at least 2 providers (mocks count)."*
- Testing and evaluation will run with **two local mock provider adapters** (e.g., Mock Provider A and Mock Provider B) to simulate:
  - Streaming SSE tokens
  - Latency and timeouts
  - 429 Rate Limits and 500 Failures (to trigger fallback)
- Real providers (e.g., OpenAI via `OPENAI_API_KEY`) can optionally be attached as an environment variable without core changes.

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
- `scope_hash` = SHA-256 of `(model_alias + system_prompt + temperature + max_tokens)`.
- Prevents cross-tenant data leaks and prevents serving responses generated under different system instructions.

---

## 4. Current Progress & Roadmap

### What is Completed:
- [x] Analyzed original Airtribe case study and implementation briefs (`FYFAE3H0GVK9QZ6O.pdf`).
- [x] Generated comprehensive `problemStatement.md`.
- [x] Generated production-ready `DataModel.md` (DDL, indexing, `pgvector`, settlement schemas).
- [x] Generated `HLD.md` evaluating 3 architectural designs and selecting Approach A.
- [x] Clarified provider dependencies (zero paid key requirement; mock-first strategy).

### What Needs to Be Done Next:
1. **Create `Docs/Architecture.md`**:
   - Detailed component breakdown: Auth Middleware, Admission Controller, Routing Engine & Difficulty Classifier, Provider Adapter Interface, SSE Streaming Handler, Semantic Cache Engine, Accounting & Logging Subsystem.
   - Sequence diagrams for: Non-streaming Cache Miss, Cache Hit, Streaming Pass-Through, Provider Failover mid-request, Budget Rejection.
2. **Create `Docs/implementationPlan.md`**:
   - Concrete 5-phase breakdown (Days 1–10 schedule matching the Airtribe brief).
   - Definition of Done (DoD) per phase.
3. **Phase 1 Execution (Foundation & Scaffolding)**:
   - Project directory structure (`src/`, `tests/`, `scripts/`, `migrations/`).
   - `docker-compose.yml` for PostgreSQL + `pgvector`.
   - Seed data ingestion: `model_pricing.json`, `seed_keys.json`, `gateway_config.sample.json`.
   - Mock provider adapters (streaming + non-streaming).

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
2. Review `Docs/problemStatement.md`, `Docs/DataModel.md`, and `Docs/HLD.md`.
3. Note our key architectural choices:
   - Approach A: FastAPI + PostgreSQL 16 + pgvector.
   - Strict atomic admission using `SELECT FOR UPDATE` on admission_counters (never hold locks over network calls).
   - Mocks are sufficient for the 2 providers; no OpenRouter key needed.
   - Required headers: x-prism-provider, x-prism-cache, x-prism-fallback, x-prism-cost-usd.

Please confirm you have digested this context, and summarize what we are building next (Docs/Architecture.md and Docs/implementationPlan.md).
```

