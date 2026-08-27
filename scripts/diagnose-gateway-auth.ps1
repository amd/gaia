<#
.SYNOPSIS
    Work out which auth header the AMD LLM gateway wants for your token.

.DESCRIPTION
    Talks to https://llm-api.amd.com/Unified/v1/models directly, with no GAIA and no
    Lemonade in the way, so a failure here is about the credential rather than
    anything GAIA does with it.

    The gateway answers an unauthenticated request with 302 to SSO, and does so
    for a wrong credential too, so the header it actually honours cannot be
    determined without a real token. This tries each candidate and reports
    which one returns a model list.

    Your token is read with Read-Host, never echoed, never written to disk, and
    is gone when this process exits.

    ASCII only: Windows PowerShell 5.1 reads a BOM-less UTF-8 file as ANSI.
#>
[CmdletBinding()]
param(
    [string]$GatewayUrl = "https://llm-api.amd.com/Unified/v1"
)

$ErrorActionPreference = "Stop"
$url = ($GatewayUrl.TrimEnd('/')) + "/models"

$secure = Read-Host "Paste your llm.amd.com token" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
if ([string]::IsNullOrWhiteSpace($token)) { throw "No token entered." }

Write-Host "`nProbing $url with a $($token.Length)-character token`n" -ForegroundColor Cyan

# name, header-name, value-prefix
$candidates = @(
    @("Authorization: Bearer (GAIA default)", "Authorization", "Bearer "),
    @("api-key (Azure OpenAI style)", "api-key", ""),
    @("Ocp-Apim-Subscription-Key (Azure APIM)", "Ocp-Apim-Subscription-Key", ""),
    @("X-Api-Key", "X-Api-Key", ""),
    @("Authorization (raw, no prefix)", "Authorization", ""),
    @("api-key with Bearer prefix", "api-key", "Bearer ")
)

$winner = $null
foreach ($c in $candidates) {
    $label = $c[0]; $hName = $c[1]; $hPrefix = $c[2]
    # Invoke-WebRequest keeps the token out of argv. Passing it as a curl.exe
    # -H argument made it readable by any local user in the process list.
    $headers = @{ $hName = "$hPrefix$token" }
    $code = 0
    $body = $null
    try {
        $resp = Invoke-WebRequest -Uri $url -Headers $headers -Method Get `
            -MaximumRedirection 0 -SkipHttpErrorCheck -TimeoutSec 20 `
            -ErrorAction Stop
        $code = [int]$resp.StatusCode
        $body = $resp.Content
    }
    catch {
        if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
    }

    $verdict = "no"
    $detail = ""
    if ($code -eq "200") {
        try {
            $json = $body | ConvertFrom-Json
            $items = if ($json.data) { $json.data } else { $json }
            $n = @($items).Count
            if ($n -gt 0) {
                $verdict = "YES"
                $detail = "$n models"
                if (-not $winner) { $winner = $c }
            }
            else { $detail = "200 but empty list" }
        }
        catch { $detail = "200 but body is not JSON (probably an HTML login page)" }
    }
    elseif ($code -match "^30") { $detail = "$code redirect to SSO (credential not accepted)" }
    else { $detail = "HTTP $code" }

    $colour = if ($verdict -eq "YES") { "Green" } else { "DarkGray" }
    Write-Host ("  [{0,-3}] {1,-40} {2}" -f $verdict, $label, $detail) -ForegroundColor $colour
}

Write-Host ""
if ($winner) {
    $hName = $winner[1]; $hPrefix = $winner[2]
    Write-Host "The gateway accepts: $hName`: $hPrefix<token>" -ForegroundColor Green
    Write-Host "`nRegister it with:" -ForegroundColor Cyan
    if ($hName -eq "Authorization" -and $hPrefix -eq "Bearer ") {
        Write-Host "  gaia gateway install --base-url $GatewayUrl"
        Write-Host "  (this is already GAIA's default, so the earlier failure was the token itself)"
    }
    else {
        Write-Host "  gaia gateway install --base-url $GatewayUrl ``"
        Write-Host "    --auth-header-name '$hName' --auth-header-prefix '$hPrefix'"
    }
}
else {
    Write-Host "No candidate returned a model list." -ForegroundColor Red
    Write-Host @"

That means the token itself was not accepted, rather than GAIA sending it the
wrong way. Worth checking:

  - Is it an API key, or a session cookie copied from the browser? A browser
    login does not produce a token usable from curl.
  - Does llm.amd.com have a separate 'API keys' or 'tokens' page? The gateway
    portal and its API often issue different credentials.
  - Has it expired, or is it scoped to a project you have to name in the URL?
  - Is a corporate proxy intercepting TLS on this host?
"@ -ForegroundColor Yellow
}

$token = $null
