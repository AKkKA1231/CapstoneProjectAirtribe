# Problem Statement — Prism: LLM Gateway and Semantic Cache

> **Project:** Prism LLM Gateway and Semantic Cache  
> **Program:** AI-first Software Engineering Capstone — Airtribe  
> **Source:** `FYFAE3H0GVK9QZ6O.pdf` (Airtribe Case Study, pp. 1–5)

---

## 1. Background & Context

Every AI-powered product in production today relies on an invisible infrastructure layer that sits between the application and the upstream model providers. This layer is responsible for credential management, cost accounting, request routing, provider failover, response caching, and multi-tenant access control — collectively referred to as an **LLM Gateway**.

Products like **LiteLLM**, **OpenRouter**, and **Portkey** exist precisely because this is now standard platform infrastructure — analogous to API gateways and load balancers a decade ago. Without a centralized gateway, every engineering team wires up LLM provider integrations independently, leading to:

- Fragmented key management and no unified cost visibility
- Duplicated provider-specific retry and failover logic
- No consistent metering or budget enforcement across teams
- Redundant spending when multiple users ask the same or semantically equivalent questions

**Prism** goes one step further: it is an **AI-first** gateway where the gateway itself makes intelligent decisions in the hot path — judging prompt difficulty to route to the right model tier, and matching *meaning* (not just strings) to serve repeated questions from cache.

---

## 2. Problem Definition

Teams building on top of LLM providers face a cluster of compounding engineering problems:

### 2.1 No Unified Provider Abstraction
Callers are tightly coupled to individual provider APIs (OpenAI, Anthropic, Cohere, etc.). When a provider changes its API, enforces rate limits, or experiences an outage, every downstream system breaks independently. There is no single entry point that abstracts provider differences behind a stable, OpenAI-compatible contract.

### 2.2 Manual and Coarse-Grained Model Selection
Engineers either hardcode a single model for all requests (overpaying for simple prompts) or implement brittle, manually-maintained routing rules. There is no automated mechanism that:
- Classifies prompt difficulty in real time
- Routes easy prompts to cheaper/faster models (e.g., `gpt-3.5-turbo` class)
- Routes hard prompts to stronger models (e.g., `gpt-4` class)
- Validates routing decisions against a labeled evaluation set to ensure accuracy beats a trivial baseline (e.g., length-only heuristics)

### 2.3 No Reliable Failover or Retry Strategy
When a provider goes down, rate-limits a tenant, or returns a transient error, most systems fail immediately and visibly. There is no production-grade fallback chain with:
- Ordered primary/fallback model resolution
- Bounded exponential backoff retries for transient errors
- Failover recording and telemetry
- Guarantees that failover cannot happen *after* response content has already been streamed to the client

### 2.4 Unbuffered Streaming Not Supported End-to-End
Streaming responses token-by-token is table stakes for modern AI products, yet passing a stream from provider → gateway → client without buffering requires careful handling of:
- Server-Sent Events (SSE) pass-through
- Backpressure preservation
- Graceful cancellation on client disconnect
- Accurate usage/cost recording even when the stream terminates early or partially fails

### 2.5 No Multi-Tenant Access Control or Budget Enforcement
Without a gateway, there is no way to:
- Issue scoped **virtual keys** per team/tenant that never expose provider credentials
- Enforce a **per-key model allowlist** (some tenants should only access certain models)
- Apply **requests-per-minute (RPM)** rate limits per key
- Cap **monthly cost budgets** per key and reject requests when the budget is exhausted
- Return distinct, machine-readable error codes per rejection type (invalid key, model not allowed, rate limited, budget exhausted)

Concurrent request admission must be handled atomically — naive implementations allow over-admission under load, breaking budget guarantees.

### 2.6 No Semantic Cache — Paying for Identical Tokens Twice
Current systems rely on exact-match caching at best. When two users ask semantically identical but lexically different questions (e.g., *"What is the capital of France?"* vs. *"Which city is France's capital?"*), both requests are forwarded to the provider and billed. A **semantic cache** that matches by embedding similarity rather than string equality can:
- Significantly reduce redundant LLM API spend
- Lower response latency for repeated or paraphrased questions
- Be scoped per virtual key to prevent cross-tenant cache leakage

Incorrect similarity thresholds produce false positives (wrong answers served) or false negatives (cache misses that waste money), making careful tuning against positive paraphrase pairs and negative near-miss pairs essential.

### 2.7 No Structured Request Logging or Usage Observability
Debugging AI traffic issues requires a complete audit trail. Currently there is no central store that records per-request:
- Which virtual key was used
- Which model and provider served the request
- Token counts (prompt + completion) and computed cost in USD
- Whether the response was served from cache or the live provider
- Whether a fallback was triggered and to which model
- End-to-end latency
- Final request status (success, error, timeout)

Without this, cost anomalies, budget overruns, and provider degradations are invisible until they become critical incidents.

### 2.8 No Ops Visibility or Admin Surface
Engineering and operations teams have no console to monitor:
- Usage and spend aggregated by virtual key
- Recent request history with status and cost
- Semantic cache hit rate trends
- Real-time provider health and fallback events

---

## 3. Proposed Solution — Prism Gateway

**Prism** is a production-grade, asynchronous Python LLM gateway that resolves all of the above with the following core capabilities:

| Capability | Description |
|---|---|
| **Unified OpenAI-compatible API** | Single `POST /v1/chat/completions` endpoint in front of ≥2 providers |
| **Virtual Key Auth & Multi-Tenancy** | Per-key model allowlists, RPM limits, monthly budget caps with atomic admission |
| **Smart Routing (`auto` alias)** | Classifier routes prompts to `fast` or `smart` tier; beats length-only baseline on labeled eval set |
| **Failover & Retry** | Ordered fallback chain with bounded exponential backoff; fallback blocked after stream starts |
| **Streaming Pass-Through** | True SSE pass-through, token-by-token, with backpressure, disconnect handling and `[DONE]` termination |
| **Semantic Response Cache** | Embedding-similarity cache scoped per virtual key; tuned on paraphrase positives and near-miss negatives |
| **Cost Metering & Usage Logs** | Per-request cost from provider token counts; full request log with cache/fallback/latency fields |
| **Response Header Contract** | Every response includes `x-prism-provider`, `x-prism-cache`, `x-prism-fallback`, `x-prism-cost-usd` |
| **Ops Console** | Simple React frontend: usage by key, recent requests, cache hit rate |

**Recommended Implementation (Approach A):** Custom asynchronous Python gateway with PostgreSQL as the durable store for keys, admission counters, budget reservations, usage logs, and cache entries. One atomic admission transaction keeps rate-limit and budget decisions consistent under concurrency.

---

## 4. Key Engineering Challenges

### 4.1 Atomic Admission Under Concurrency
Concurrent requests against the same virtual key must both check and update `spent + reserved` atomically. The admission row must be locked in a short, bounded transaction. Locks must **never** be held during network calls to upstream providers.

### 4.2 Cost Reservation & Settlement
Before dispatching to a provider, a conservative upper-bound cost must be reserved. After the provider responds with actual token counts, the reservation is settled to the real cost. Auto-classification and embedding calls must be included in budget accounting. Settlement records must be unique to prevent double-counting.

### 4.3 Streaming Failure Modes
Failover is only permitted **before** response content has been emitted. Once streaming begins, the gateway must track partial-stream failures and unresolved usage separately for reconciliation. The `[DONE]` sentinel must only be sent on clean, successful stream completion.

### 4.4 Semantic Cache Correctness
The cache must be partitioned by: virtual key + model/config + system instructions + conversation context + generation parameters. Candidates must be validated before serving (reject stale or semantically unsafe entries). TTLs are required. Similarity thresholds must be tuned to avoid false positives that serve wrong answers.

### 4.5 Smart Routing Beyond Length
The prompt difficulty classifier must correctly handle:
- Short-but-hard prompts (e.g., tricky math, ambiguous reasoning)
- Long-but-trivial prompts (e.g., verbose restatements of a simple question)

Routing accuracy must be validated on a labeled eval set and reported per case against a length-only baseline.

### 4.6 Provider Adapter Isolation
All provider-specific logic must live behind an adapter interface so mock providers and real providers are interchangeable without changing gateway core logic. Every upstream call must carry a timeout — the gateway must not hang indefinitely on a slow or dead provider.

---

## 5. Functional Requirements

| # | Requirement |
|---|---|
| FR-01 | Load `model_pricing.json`, `seed_keys.json`, and `gateway_config.sample.json` without altering alias names or seed key values |
| FR-02 | Expose `POST /v1/chat/completions` compatible with the OpenAI API contract, backed by ≥2 providers |
| FR-03 | Authenticate every request with a virtual key; reject with distinct error codes for invalid key / model not allowed / rate limited / budget exhausted |
| FR-04 | Enforce per-key model allowlist, RPM rate limit, and monthly cost budget atomically |
| FR-05 | Resolve model alias (`fast`, `smart`, `auto`) to a primary provider with an ordered fallback chain |
| FR-06 | Classify prompt difficulty for `auto` alias and route to `fast` or `smart` tier |
| FR-07 | Retry transient provider errors with bounded exponential backoff |
| FR-08 | Pass streaming responses (`stream: true`) token-by-token without buffering; terminate with `data: [DONE]` |
| FR-09 | Serve semantically similar prompts from cache, scoped per virtual key |
| FR-10 | Compute and record per-request cost using provider-reported token counts and the price table |
| FR-11 | Log every request with: key, model, provider, status, tokens, cost, cache result, fallback flag, latency |
| FR-12 | Expose usage summary and recent-request APIs |
| FR-13 | Return response headers: `x-prism-provider`, `x-prism-cache`, `x-prism-fallback`, `x-prism-cost-usd` |
| FR-14 | Provide a simple ops console showing usage by key, recent requests, and cache hit rate |

---

## 6. Non-Functional Requirements

| # | Requirement |
|---|---|
| NFR-01 | Usage accounting must remain accurate under concurrent requests (zero over-admission) |
| NFR-02 | Every upstream provider call must have a configurable timeout; the gateway must never hang |
| NFR-03 | Provider integration must be behind an adapter; swapping mock ↔ real provider requires no core changes |
| NFR-04 | Automated tests must cover: cost computation, rate limiting, cache decisions, model allowlists, routing behavior |
| NFR-05 | Must pass `scripts/smoke_test.py`; load test and routing eval results must be reported honestly |
| NFR-06 | Cache must not leak responses across different virtual keys (tenant isolation) |
| NFR-07 | Smart routing classifier must exceed a length-only baseline on the labeled routing eval set |
| NFR-08 | The gateway data plane must remain OpenAI-compatible at the API contract level |

---

## 7. Deliverables

1. **Functional LLM gateway** with ops console (React)
2. **README** covering setup, API documentation, architecture, routing/failover design, cache scoping logic, and known limitations
3. **Public GitHub repository** (https://github.com/airtribe-projects/prism-capstone)
4. **Seeded demo keys** and instructions to reproduce the demo with mock providers
5. **Verification report** including:
   - `smoke_test.py` output
   - `load_test.py` report (over-admission results, latency summary)
   - Routing eval accuracy per case vs. length-only baseline
   - Accounting totals
6. **Explainer video** demonstrating: streaming, smart routing, budget rejection, rate limiting, semantic cache hit on a paraphrased prompt, and live provider failover

---

## 8. Assessment Criteria

| Category | Weight | Focus |
|---|---|---|
| **Functionality** | Core | Gateway loads data, serves completions, streams, enforces keys/limits/budgets, routes, caches, meters, and shows ops console |
| **Correctness Under Pressure** | Core | Accurate accounting under concurrency, no over-admission, working failover, no cross-tenant cache leakage |
| **Code Quality** | Supporting | Clean, organized, maintainable, easy to understand |
| **Design & Architecture** | Supporting | Sound backend architecture — streaming, adapters, rate limiting, cost metering, retries, failover, multi-tenancy |
| **Documentation & Demo** | Supporting | Clear README, complete demo covering full Prism workflow |

---

*Document generated from: `FYFAE3H0GVK9QZ6O.pdf` (Airtribe Prism Case Study) and `Airtribe_Project_Implementation_Briefs.pdf` (Prism LLM Gateway and Semantic Cache, pp. 2)*

