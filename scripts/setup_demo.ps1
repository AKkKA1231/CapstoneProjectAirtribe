# Load demo environment variables and the Send-Prism helper function into the current PowerShell session.
# Usage: . .\scripts\setup_demo.ps1

$global:base = 'http://localhost:8000'
$global:alphaKey = 'prism-demo-team-alpha'
$global:betaKey = 'prism-demo-team-beta'
$global:demoTag = Get-Date -Format 'HHmmss'

function global:Send-Prism {
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

Write-Host "✓ Prism demo session initialized!" -ForegroundColor Green
Write-Host "  `$base     = $base" -ForegroundColor Gray
Write-Host "  `$alphaKey = $alphaKey" -ForegroundColor Gray
Write-Host "  `$betaKey  = $betaKey" -ForegroundColor Gray
Write-Host "  `$demoTag  = $demoTag" -ForegroundColor Gray
Write-Host "  Send-Prism function is ready to use." -ForegroundColor Gray

