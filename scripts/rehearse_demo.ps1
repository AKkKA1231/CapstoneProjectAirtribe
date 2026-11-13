# Prism LLM Gateway - Interactive Rehearsal & Recording Walkthrough
# This script walks you through all 11 scenes step-by-step.
# You can mimic each scene while recording your screen.

Clear-Host
$base = 'http://localhost:8000'
$alphaKey = 'prism-demo-team-alpha'
$betaKey = 'prism-demo-team-beta'
$demoTag = Get-Date -Format 'HHmmss'

function Send-Prism {
    param([string]$Key, [string]$Model, [string]$Prompt)

    $body = @{
        model       = $Model
        messages    = @(@{ role = 'user'; content = $Prompt })
        temperature = 0
        max_tokens  = 64
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
        }
        else {
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

function Show-Header {
    param([string]$Title, [string]$Time, [string]$Show, [string]$Say)
    Write-Host "================================================================================" -ForegroundColor Cyan
    Write-Host "🎬 $Title (Target Time: $Time)" -ForegroundColor Yellow
    Write-Host "================================================================================" -ForegroundColor Cyan
    Write-Host "`n🖥️  WHAT TO SHOW ON SCREEN:" -ForegroundColor Green
    Write-Host "   $Show" -ForegroundColor White
    Write-Host "`n🗣️  WHAT TO SAY:" -ForegroundColor Magenta
    Write-Host "   \"$Say\"" -ForegroundColor Gray
    Write-Host "`n⚡ RUNNING COMMANDS FOR THIS SCENE:`n" -ForegroundColor Yellow
}

# -----------------------------------------------------------------------------
# Scene 1
# -----------------------------------------------------------------------------
Show-Header -Title "Scene 1 — Title and Project Summary" `
    -Time "0:00 - 0:35" `
    -Show "Show the top of README.md in VS Code preview." `
    -Say "Hello, this is Prism, my capstone project. Prism is a multi-tenant, OpenAI-compatible LLM gateway. Applications send one standard chat request, while the gateway handles authentication, rate and budget limits, intelligent model routing, retry and fallback, streaming, semantic caching, accounting, recovery, and operational visibility. Normal requests use Groq's real OpenAI-compatible API. Local mock providers remain available only for deterministic failure and fallback scenarios."

Read-Host "Press [Enter] to move to Scene 2..."

# -----------------------------------------------------------------------------
# Scene 2
# -----------------------------------------------------------------------------
Clear-Host
Show-Header -Title "Scene 2 — Architecture" `
    -Time "0:35 - 1:25" `
    -Show "Show Docs/Architecture.md preview in VS Code (point slowly to client, FastAPI, application services, PostgreSQL + pgvector, Groq, and Mocks)." `
    -Say "Prism uses a modular-monolith architecture with ports and adapters. The API layer owns HTTP validation and authentication. Application services implement admission control, routing, provider execution, caching, accounting, and recovery. PostgreSQL is the source of truth, providing durable config, atomic admission counters, request logs, budget reservations, and pgvector-backed semantic-cache entries. Groq is the real upstream for normal traffic, while Mock A and Mock B implement the same adapter contract for deterministic resilience testing."

Read-Host "Press [Enter] to move to Scene 3..."

# -----------------------------------------------------------------------------
# Scene 3
# -----------------------------------------------------------------------------
Clear-Host
Show-Header -Title "Scene 3 — Docker Services & Readiness" `
    -Time "1:25 - 1:55" `
    -Show "Switch to PowerShell terminal window." `
    -Say "Docker Compose starts five local services: PostgreSQL, two mock providers, the FastAPI gateway, and the React operations console. The gateway securely calls Groq over its OpenAI-compatible API. PostgreSQL is healthy, and readiness confirms that the required database dependency is available."

Write-Host "Running: docker compose ps" -ForegroundColor Cyan
docker compose ps
Write-Host "`nRunning: Invoke-RestMethod http://localhost:8000/health/ready" -ForegroundColor Cyan
Invoke-RestMethod http://localhost:8000/health/ready | Format-List

Read-Host "Press [Enter] to move to Scene 4..."

# -----------------------------------------------------------------------------
# Scene 4
# -----------------------------------------------------------------------------
Clear-Host
Show-Header -Title "Scene 4 — Standard Completion & True SSE Streaming" `
    -Time "1:55 - 2:50" `
    -Show "Terminal window (point to Status, Provider, Cache, Fallback, CostUSD headers, then show streaming tokens ending in [DONE])." `
    -Say "This is a normal OpenAI-compatible completion using Team Alpha's virtual key. The response headers explain which physical provider ran, whether cache or fallback was used, and the settled cost. Streaming is passed through as server-sent events instead of being buffered. A successful stream ends with the standard data: [DONE] marker. Once content has been emitted, Prism never changes providers."

Write-Host "1. Non-streaming call (fast alias -> Groq):" -ForegroundColor Cyan
Send-Prism -Key $alphaKey -Model 'fast' -Prompt "Hello from the Prism recording $demoTag"

Write-Host "`n2. True SSE Streaming pass-through (token-by-token):" -ForegroundColor Cyan
$streamBody = @{
    model       = 'fast'
    messages    = @(@{ role = 'user'; content = "Stream a short explanation of Prism $demoTag" })
    temperature = 0
    max_tokens  = 64
    stream      = $true
} | ConvertTo-Json -Depth 5 -Compress

$streamBody | curl.exe -N "$base/v1/chat/completions" `
    -H "Authorization: Bearer $alphaKey" `
    -H 'Content-Type: application/json' `
    --data-binary '@-'

Read-Host "`nPress [Enter] to move to Scene 5..."

# -----------------------------------------------------------------------------
# Scene 5
# -----------------------------------------------------------------------------
Clear-Host
Show-Header -Title "Scene 5 — Intelligent Auto Routing" `
    -Time "2:50 - 3:45" `
    -Show "Terminal window showing auto routing decisions and routing_eval.py output." `
    -Say "The auto alias uses a versioned rules classifier. The simple listing request selects Groq's fast 20B target. The mathematical proof selects the smart 120B target. Every decision stores its tier, reason code, and confidence. The committed evaluation scores 100 percent, compared with 40 percent for a length-only baseline."

Write-Host "1. Easy prompt (auto -> fast tier / 20B):" -ForegroundColor Cyan
Send-Prism -Key $alphaKey -Model 'auto' -Prompt "List three primary colors $demoTag"

Write-Host "`n2. Hard prompt (auto -> smart tier / 120B):" -ForegroundColor Cyan
Send-Prism -Key $alphaKey -Model 'auto' -Prompt "Prove that square root of two is irrational $demoTag"

Write-Host "`n3. Running routing evaluation against baseline:" -ForegroundColor Cyan
.\.venv\Scripts\python.exe scripts\routing_eval.py

Read-Host "`nPress [Enter] to move to Scene 6..."

# -----------------------------------------------------------------------------
# Scene 6
# -----------------------------------------------------------------------------
Clear-Host
Show-Header -Title "Scene 6 — Retry and Ordered Fallback" `
    -Time "3:45 - 4:25" `
    -Show "Terminal window (point out Mock A 503 failure and fallback to Mock B with Fallback: true)." `
    -Say "For this deterministic fault-injection scene I use the explicit mock-fast alias. The 503 marker makes Mock A fail predictably. Prism records the failed attempt and moves through the configured retry and fallback chain. Mock B succeeds, and the response reports Fallback: true. Fallback is allowed only before response commitment."

Write-Host "Fault injection with mock-fast and [[503]]:" -ForegroundColor Cyan
Send-Prism -Key $alphaKey -Model 'mock-fast' -Prompt "Recover this request from [[503]] $demoTag"

Read-Host "Press [Enter] to move to Scene 7..."

# -----------------------------------------------------------------------------
# Scene 7
# -----------------------------------------------------------------------------
Clear-Host
Show-Header -Title "Scene 7 — Semantic Cache & Tenant Isolation" `
    -Time "4:25 - 5:35" `
    -Show "Terminal window showing Team Alpha miss, Team Alpha paraphrase hit (CostUSD: 0), and Team Beta cross-tenant miss." `
    -Say "The first Team Alpha request is a cache miss and stores an eligible response. The paraphrased request crosses the similarity threshold, so the second response is a zero-cost cache hit. The same paraphrase sent with Team Beta is a miss. Cache lookup is scoped by virtual key, conversation context, generation settings, resolved route, embedding version, and TTL, preventing any cross-tenant data leakage."

Write-Host "1. Team Alpha initial request (miss):" -ForegroundColor Cyan
Send-Prism -Key $alphaKey -Model 'fast' -Prompt "Explain semantic caching for an automobile recording-$demoTag"

Write-Host "`n2. Team Alpha paraphrase (HIT, zero cost):" -ForegroundColor Cyan
Send-Prism -Key $alphaKey -Model 'fast' -Prompt "Describe semantic cache for a car recording-$demoTag"

Write-Host "`n3. Team Beta same paraphrase (MISS - tenant isolated):" -ForegroundColor Cyan
Send-Prism -Key $betaKey  -Model 'fast' -Prompt "Describe semantic cache for a car recording-$demoTag"

Write-Host "`n4. Running cache evaluation suite:" -ForegroundColor Cyan
.\.venv\Scripts\python.exe scripts\cache_eval.py

Read-Host "`nPress [Enter] to move to Scene 8..."

# -----------------------------------------------------------------------------
# Scene 8
# -----------------------------------------------------------------------------
Clear-Host
Show-Header -Title "Scene 8 — Atomic RPM & Budget Controls" `
    -Time "5:35 - 6:20" `
    -Show "Terminal window showing RPM responses (200, 200, 429) and Budget rejection (402)." `
    -Say "This key permits two requests per minute, so the third request returns HTTP 429. The second key has an intentionally tiny budget and returns HTTP 402 before a provider is called. Admission uses a short PostgreSQL transaction with row locking. Estimated cost is reserved atomically before provider execution, then settled exactly once using actual usage."

Write-Host "1. Sending 3 requests on 2-RPM limited key:" -ForegroundColor Cyan
1..3 | ForEach-Object {
    Write-Host "--- RPM Request $_ ---" -ForegroundColor DarkGray
    Send-Prism -Key 'prism-demo-rpm-limited' -Model 'fast' -Prompt "RPM request $_ recording-$demoTag"
}

Write-Host "`n2. Sending request on budget-limited key:" -ForegroundColor Cyan
Send-Prism -Key 'prism-demo-budget-limited' -Model 'fast' -Prompt "Budget rejection recording-$demoTag"

Read-Host "Press [Enter] to move to Scene 9..."

# -----------------------------------------------------------------------------
# Scene 9
# -----------------------------------------------------------------------------
Clear-Host
Show-Header -Title "Scene 9 — Operations Console" `
    -Time "6:20 - 7:50" `
    -Show "Open Chrome browser at http://localhost:5173. Enter admin key: development-only-change-this-admin-key and click Connect. Tour Overview, Requests, Cache Hits, and Providers tabs." `
    -Say "The admin console explains what happened without direct database access. The overview aggregates usage, tokens, spend, cache hits, and fallbacks by tenant. The request audit trail exposes routing and operational signals, and the provider view preserves each retry and fallback attempt independently. The console never returns raw API keys, key digests, or prompts. The admin key remains only in page memory."

Write-Host "👉 Open your browser at http://localhost:5173 and demonstrate the tabs!" -ForegroundColor Green
Read-Host "Press [Enter] to move to Scene 10..."

# -----------------------------------------------------------------------------
# Scene 10
# -----------------------------------------------------------------------------
Clear-Host
Show-Header -Title "Scene 10 — Recovery, Metrics, & Smoke Verification" `
    -Time "7:50 - 9:20" `
    -Show "Show http://localhost:8000/docs in browser, then return to terminal to run smoke_test.py and show phase5-verification.md." `
    -Say "Prism includes conservative crash recovery. A stale reservation with no provider attempt is safe to release. If any potentially billable provider attempt exists, Prism does not guess; it marks the reservation for manual reconciliation. A PostgreSQL advisory lock ensures that only one reconciler runs at a time. The nine-path smoke suite validates all core features."

Write-Host "1. Running the 9-path end-to-end smoke test:" -ForegroundColor Cyan
.\.venv\Scripts\python.exe scripts\smoke_test.py

Write-Host "`n2. Displaying Phase 5 verification evidence summary:" -ForegroundColor Cyan
Get-Content reports\phase5-verification.md | Select-Object -First 35

Read-Host "`nPress [Enter] to move to Scene 11..."

# -----------------------------------------------------------------------------
# Scene 11
# -----------------------------------------------------------------------------
Clear-Host
Show-Header -Title "Scene 11 — Conclusion" `
    -Time "9:20 - 9:50" `
    -Show "Show Console Overview or README.md title." `
    -Say "To conclude, Prism provides one OpenAI-compatible API with secure multi-tenant admission, explainable routing, provider resilience, true streaming, isolated semantic caching, exact accounting, conservative recovery, metrics, and an operations console. The final Python 3.12 and PostgreSQL suite has 61 passing tests with 82.62 percent branch coverage. Thank you."

Write-Host "`n🎉 Rehearsal Walkthrough Complete! You are ready to record your video!" -ForegroundColor Green
