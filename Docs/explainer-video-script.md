# Prism Capstone — Scene-by-Scene Recording Script

Target length: 9–11 minutes. Record at 1080p. Keep the browser at 100% zoom and increase the PowerShell font enough that commands and output remain readable.

## Before recording — do not include this part

1. Start Docker Desktop and wait until the engine is running.
2. Open PowerShell in the repository root.
3. Open `http://localhost:5173` and `http://localhost:8000/docs` in browser tabs.
4. Open `Docs/Architecture.md` in the editor preview.
5. Close unrelated applications, notifications, terminals, and browser tabs.
6. Make sure no real passwords, tokens, email addresses, or personal folders are visible.
7. Create a Groq key at `https://console.groq.com/keys`, place it in `.env` as `GROQ_API_KEY=...`, and never show that file during the recording.
8. Wait at least 60 seconds before recording the RPM demonstration. Do not use the RPM demo key during preparation.
9. Run this preflight check:

```powershell
docker compose up --build -d
docker compose ps
Invoke-RestMethod http://localhost:8000/health/ready
```

All five services should be running and PostgreSQL should be healthy. Readiness should report `status: ok` and `database: ok`.

Paste this helper into PowerShell before recording. It keeps the live demonstration short and prints the important gateway signals:

```powershell
$base = 'http://localhost:8000'
$alphaKey = 'prism-demo-team-alpha'
$betaKey = 'prism-demo-team-beta'
$demoTag = Get-Date -Format 'HHmmss'

function Send-Prism {
    param([string]$Key, [string]$Model, [string]$Prompt)

    $body = @{
        model = $Model
        messages = @(@{ role = 'user'; content = $Prompt })
        temperature = 0
        max_tokens = 64
    } | ConvertTo-Json -Depth 5 -Compress

    $statusCode = 0
    $headers = @{}
    $content = ""

    try {
        $response = Invoke-WebRequest "$base/v1/chat/completions" `
            -Method Post `
            -Headers @{ Authorization = "Bearer $Key" } `
            -ContentType 'application/json' `
            -Body $body `
            -UseBasicParsing

        $statusCode = [int]$response.StatusCode
        $headers = $response.Headers
        $content = $response.Content
    }
    catch {
        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
            $headers = $_.Exception.Response.Headers
            $content = if ($_.ErrorDetails) { $_.ErrorDetails.Message } else { $_.Exception.Message }
        } else {
            throw $_
        }
    }

    [pscustomobject]@{
        Status   = $statusCode
        Provider = if ($headers['x-prism-provider']) { $headers['x-prism-provider'] } else { $headers.GetValues('x-prism-provider') }
        Cache    = if ($headers['x-prism-cache']) { $headers['x-prism-cache'] } else { $headers.GetValues('x-prism-cache') }
        Fallback = if ($headers['x-prism-fallback']) { $headers['x-prism-fallback'] } else { $headers.GetValues('x-prism-fallback') }
        CostUSD  = if ($headers['x-prism-cost-usd']) { $headers['x-prism-cost-usd'] } else { $headers.GetValues('x-prism-cost-usd') }
        Body     = $content
    } | Format-List
}
```

Do one private rehearsal before the final take. For a second take, change `$demoTag` so the cache demonstration begins with a genuine miss.

---

## Scene 1 — Title and project summary

Time: 0:00–0:35

### Show

Show the top of `README.md`, including the project name and overview.

### Say

> Hello, this is Prism, my capstone project. Prism is a multi-tenant, OpenAI-compatible LLM gateway. Applications send one standard chat request, while the gateway handles authentication, rate and budget limits, intelligent model routing, retry and fallback, streaming, semantic caching, accounting, recovery, and operational visibility.
>
> Normal requests use Groq's real OpenAI-compatible API. Local mock providers remain available only for deterministic failure and fallback scenarios.

Do not read the entire README. Pause briefly on the title and feature summary, then move on.

---

## Scene 2 — Architecture

Time: 0:35–1:25

### Show

Open `Docs/Architecture.md` and show its high-level architecture diagram. Point slowly to the client and console, FastAPI boundary, application services, ports and adapters, PostgreSQL with pgvector, Groq, and both mock providers.

### Say

> Prism uses a modular-monolith architecture with ports and adapters. The API layer owns HTTP validation and authentication. Application services implement admission control, routing, provider execution, caching, accounting, and recovery. The domain remains independent from FastAPI and SQLAlchemy.
>
> PostgreSQL is the source of truth. It provides durable configuration, atomic admission counters, request and provider-attempt logs, budget reservations, and pgvector-backed semantic-cache entries. Groq is the real upstream for normal traffic, while Mock A and Mock B implement the same adapter contract for deterministic resilience testing.
>
> I chose a modular monolith because it preserves clear boundaries and testability without introducing unnecessary distributed-system complexity.

---

## Scene 3 — Docker services and readiness

Time: 1:25–1:55

### Show and run

```powershell
docker compose ps
Invoke-RestMethod http://localhost:8000/health/ready
```

### Say

> Docker Compose starts five local services: PostgreSQL, two mock providers, the FastAPI gateway, and the React operations console. The gateway securely calls Groq over its OpenAI-compatible API. PostgreSQL is healthy, and readiness confirms that the required database dependency is available.

Expected: five services are `Up`, PostgreSQL is `healthy`, and readiness returns HTTP 200 with `database: ok`.

---

## Scene 4 — Standard completion and true SSE streaming

Time: 1:55–2:50

### Show and run

```powershell
Send-Prism -Key $alphaKey -Model 'fast' -Prompt "Hello from the Prism recording $demoTag"
```

Point to `Status`, `Provider`, `Cache`, `Fallback`, and `CostUSD`. Then run:

```powershell
$streamBody = @{
    model = 'fast'
    messages = @(@{ role = 'user'; content = "Stream a short explanation of Prism $demoTag" })
    temperature = 0
    max_tokens = 64
    stream = $true
} | ConvertTo-Json -Depth 5 -Compress

curl.exe -N "$base/v1/chat/completions" `
    -H "Authorization: Bearer $alphaKey" `
    -H 'Content-Type: application/json' `
    --data-binary $streamBody
```

### Say

> This is a normal OpenAI-compatible completion using Team Alpha's virtual key. The response headers explain which physical provider ran, whether cache or fallback was used, and the settled cost.
>
> Streaming is passed through as server-sent events instead of being buffered. A successful stream ends with the standard `data: [DONE]` marker. Once content has been emitted, Prism never changes providers because that could combine two inconsistent answers.

Expected: the standard request returns 200 from `groq`; streaming shows multiple `data:` events followed by `data: [DONE]`.

---

## Scene 5 — Intelligent `auto` routing

Time: 2:50–3:45

### Show and run

```powershell
Send-Prism -Key $alphaKey -Model 'auto' -Prompt "List three primary colors $demoTag"
Send-Prism -Key $alphaKey -Model 'auto' -Prompt "Prove that square root of two is irrational $demoTag"
.\.venv\Scripts\python.exe scripts\routing_eval.py
```

### Say

> The `auto` alias uses a versioned and explainable rules classifier. The simple listing request selects Groq's fast `openai/gpt-oss-20b` target. The mathematical proof selects the smart `openai/gpt-oss-120b` target.
>
> Every decision stores its tier, reason code, confidence, and classifier version. The committed evaluation scores 100 percent, compared with 40 percent for a length-only baseline, including 100 percent on trap cases.

Expected: both requests show provider `groq`; their response bodies identify the 20B and 120B models respectively. The evaluation reports `1.0`, `0.4`, and `1.0` accuracy values.

---

## Scene 6 — Retry and ordered fallback

Time: 3:45–4:25

### Show and run

```powershell
Send-Prism -Key $alphaKey -Model 'mock-fast' -Prompt "Recover this request from [[503]] $demoTag"
```

### Say

> For this deterministic fault-injection scene I use the explicit `mock-fast` alias, not the normal Groq-backed `fast` alias. The `503` marker makes Mock A fail predictably. Prism records the failed attempt and moves through the configured retry and fallback chain. Mock B succeeds, and the response reports `Fallback: true`.
>
> Fallback is allowed only before response commitment. Fatal errors and partial streams follow different policies to avoid duplicate billing or mixed responses.

Expected: HTTP 200, `Provider: mock-b`, and `Fallback: true`.

---

## Scene 7 — Semantic cache and tenant isolation

Time: 4:25–5:35

### Show and run

```powershell
Send-Prism -Key $alphaKey -Model 'fast' -Prompt "Explain semantic caching for an automobile recording-$demoTag"
Send-Prism -Key $alphaKey -Model 'fast' -Prompt "Describe semantic cache for a car recording-$demoTag"
Send-Prism -Key $betaKey  -Model 'fast' -Prompt "Describe semantic cache for a car recording-$demoTag"
.\.venv\Scripts\python.exe scripts\cache_eval.py
```

### Say

> The first Team Alpha request is a cache miss and stores an eligible deterministic response. The paraphrased request crosses the similarity threshold, so the second response is a zero-cost cache hit.
>
> The same paraphrase sent with Team Beta is a miss. Cache lookup is scoped by virtual key, conversation context, generation settings, resolved route, embedding version, and TTL. This prevents one tenant from receiving another tenant's cached response.
>
> The threshold is 0.82 and was selected from committed positive and negative evaluation cases rather than guessed during the demonstration.

Expected: Team Alpha shows miss, then hit with zero cost; Team Beta shows a miss.

---

## Scene 8 — Atomic RPM and budget controls

Time: 5:35–6:20

### Show and run

```powershell
1..3 | ForEach-Object {
    Send-Prism `
        -Key 'prism-demo-rpm-limited' `
        -Model 'fast' `
        -Prompt "RPM request $_ recording-$demoTag"
}

Send-Prism `
    -Key 'prism-demo-budget-limited' `
    -Model 'fast' `
    -Prompt "Budget rejection recording-$demoTag"
```

### Say

> This key permits two requests per minute, so the third request returns HTTP 429. The second key has an intentionally tiny budget and returns HTTP 402 before a provider is called.
>
> Admission uses a short PostgreSQL transaction with row locking. Estimated cost is reserved atomically before provider execution, then settled exactly once using actual usage. This prevents concurrent requests from over-admitting the same budget.

Expected: RPM responses are 200, 200, then 429; the budget response is 402. If the first RPM request returns 429, stop, wait 60 seconds, update `$demoTag`, and retake this scene.

---

## Scene 9 — Operations console

Time: 6:20–7:50

### Show

Open `http://localhost:5173`, enter the local demo admin key below, and click **Connect**:

```text
development-only-change-this-admin-key
```

Show these views:

1. **Overview** — request count, token volume, spend, cache-hit rate, tenant usage, and cache efficiency.
2. **Requests** — route, provider, status, cache, fallback, stream, latency, and cost columns.
3. Select **Cache: Hits** briefly to show the zero-cost cache record.
4. Select **Fallback: Triggered** briefly to show the forced fallback record.
5. **Providers** — show normal Groq attempts and the deterministic Mock A failure followed by Mock B success.

### Say

> The admin console explains what happened without direct database access. The overview aggregates usage, tokens, spend, cache hits, and fallbacks by tenant. The request audit trail exposes routing and operational signals, and the provider view preserves each retry and fallback attempt independently.
>
> The console never returns raw API keys, key digests, request fingerprints, prompts, or provider response bodies. The admin key remains only in page memory. Production would place this interface behind operator identity and TLS.

Move the cursor slowly and pause on each table. Clear filters before moving to another tab.

---

## Scene 10 — Recovery, metrics, and verification

Time: 7:50–9:20

### Show

Open `http://localhost:8000/docs` and briefly show:

- `GET /ops/usage`
- `GET /ops/requests`
- `GET /ops/cache`
- `GET /ops/provider-attempts`
- `POST /ops/reconcile`
- `GET /metrics`

Return to PowerShell and run:

```powershell
.\.venv\Scripts\python.exe scripts\smoke_test.py
Get-Content reports\phase5-verification.md
```

### Say

> Prism includes conservative crash recovery. A stale reservation with no provider attempt is safe to release. If any potentially billable provider attempt exists, Prism does not guess; it marks the reservation for manual reconciliation. A PostgreSQL advisory lock ensures that only one reconciler runs at a time.
>
> Prometheus-format request, latency, cache, fallback, provider-attempt, and settlement-failure metrics are protected by the admin policy.
>
> The nine-path smoke suite validates readiness, model policy, standard and streaming completions, routing, fallback, semantic cache, tenant isolation, and the operations API.

Expected: the smoke report shows `passed: 9`; the verification report shows 61 tests passed and 82.62 percent branch coverage.

---

## Scene 11 — Final conclusion

Time: 9:20–9:50

### Show

Return to the Prism console overview or README title.

### Say

> To conclude, Prism provides one OpenAI-compatible API with secure multi-tenant admission, explainable routing, provider resilience, true streaming, isolated semantic caching, exact accounting, conservative recovery, metrics, and an operations console.
>
> The final Python 3.12 and PostgreSQL suite has 61 passing tests with 82.62 percent branch coverage. Ruff, strict Mypy, the React production build, clean Docker startup, smoke tests, load evidence, and accounting reconciliation all pass. Thank you.

Pause for two seconds, then stop the recording.

---

## Final submission checklist

- Watch the recording once at normal speed and confirm text is readable.
- Confirm audio is clear and no notification sounds are present.
- Confirm the recording shows the 200/200/429 RPM sequence and 402 budget response.
- Confirm cache shows miss, zero-cost hit, and cross-tenant miss.
- Confirm fallback shows Mock B and `Fallback: true`.
- Confirm streaming ends with `[DONE]`.
- Do not expose personal or real credentials.
- Commit and push the complete project before submitting the GitHub link.
- Open the GitHub repository in an incognito window to confirm the submitted link and documentation are accessible.
