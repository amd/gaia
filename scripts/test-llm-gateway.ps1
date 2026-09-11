<#
.SYNOPSIS
    End-to-end check of GAIA's AMD LLM gateway support against the real gateway.

.DESCRIPTION
    Stands up an embeddable Lemonade 11.8 on a private port, registers
    https://llm-api.amd.com/Unified/v1 with it, and runs real completions through the CLI
    and the agent path.

    Your token is read with Read-Host so it never enters PowerShell history,
    is held only in this process's environment, and is handed straight to
    Lemonade, which keeps it in memory and never writes it to disk. The script
    greps ~/.gaia afterwards to prove nothing leaked.

    ASCII only on purpose: Windows PowerShell 5.1 reads a BOM-less UTF-8 file
    as ANSI, and a stray multi-byte character breaks string parsing.

.PARAMETER Port
    Port for the private Lemonade. Default 15305.

.PARAMETER GatewayUrl
    Gateway base URL. Default https://llm-api.amd.com/Unified/v1 (note /v1, not /api/v1).

.PARAMETER KeepRunning
    Leave Lemonade running afterwards so you can drive the TUI against it.

.PARAMETER SkipDownload
    Reuse an already-downloaded embeddable instead of fetching it again.

.EXAMPLE
    .\scripts\test-llm-gateway.ps1

.EXAMPLE
    .\scripts\test-llm-gateway.ps1 -KeepRunning
#>
[CmdletBinding()]
param(
    [int]$Port = 15305,
    [string]$GatewayUrl = "https://llm-api.amd.com/Unified/v1",
    [switch]$KeepRunning,
    [switch]$SkipDownload
)

$ErrorActionPreference = "Stop"
$LemonadeVersion = "11.8.0"
$LocalKey = "gaia-local-" + [guid]::NewGuid().ToString("N").Substring(0, 12)
$EmbedRoot = Join-Path $env:USERPROFILE "lemonade-embed"
$EmbedDir = Join-Path $EmbedRoot "lemonade-embeddable-$LemonadeVersion-windows-x64"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$script:Failures = 0

function Step($n, $msg) { Write-Host "`n=== $n. $msg ===" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "  [PASS] $msg" -ForegroundColor Green }
function Bad($msg) { Write-Host "  [FAIL] $msg" -ForegroundColor Red; $script:Failures++ }
function Note($msg) { Write-Host "  $msg" -ForegroundColor DarkGray }

function Invoke-Gaia {
    # Runs the CLI from source and echoes its output. The token is never passed
    # as an argument; the CLI reads GAIA_GATEWAY_TOKEN from the environment.
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GaiaArgs)
    & python -m gaia.cli @GaiaArgs 2>&1 | ForEach-Object { "    $_" }
}

# ----------------------------------------------------------------- token ---
Step 1 "Gateway token"
Note "Read-Host keeps this out of PowerShell history; only the command is saved."
$secure = Read-Host "  Paste your llm.amd.com token" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
if ([string]::IsNullOrWhiteSpace($token)) { throw "No token entered." }
Ok "Token captured ($($token.Length) chars). It will not be printed again."

# -------------------------------------------------------------- lemonade ---
Step 2 "Embeddable Lemonade $LemonadeVersion"
if (-not $SkipDownload -or -not (Test-Path $EmbedDir)) {
    New-Item -ItemType Directory -Force -Path $EmbedRoot | Out-Null
    $zip = Join-Path $EmbedRoot "lemonade-embeddable-$LemonadeVersion-windows-x64.zip"
    $url = "https://github.com/lemonade-sdk/lemonade/releases/download/v$LemonadeVersion/lemonade-embeddable-$LemonadeVersion-windows-x64.zip"
    Note "Downloading (about 5 MB) ..."
    curl.exe -sL -o $zip $url
    if (-not (Test-Path $zip)) { throw "Download failed: $url" }
    Expand-Archive -Force -Path $zip -DestinationPath $EmbedRoot
    Ok "Unpacked to $EmbedDir"
}
else {
    Ok "Reusing $EmbedDir"
}

Push-Location $EmbedDir
try {
    # 228 bundled models take 10+ minutes to index and pin the disk. A gateway
    # setup needs none of them, so trim to 2. This is a documented embeddable
    # customization, not a workaround.
    $catalog = "resources/server_models.json"
    $count = (python -c "import json;print(len(json.load(open(r'$catalog'))))")
    if ([int]$count -gt 5) {
        python -c "import json;p=r'$catalog';d=json.load(open(p));json.dump(dict(list(d.items())[:2]),open(p,'w'))"
        Ok "Trimmed catalog from $count to 2 models (avoids a 10-minute cold start)"
    }
    else {
        Ok "Catalog already trimmed ($count models)"
    }

    New-Item -ItemType Directory -Force -Path rt\cache, rt\config | Out-Null
    $cfg = '{"config_version":2,"broadcast":false,"no_fetch_executables":true}'
    Set-Content -Path rt\config\config.json -Value $cfg -Encoding ascii

    Get-Process lemond -ErrorAction SilentlyContinue | Stop-Process -Force
    $env:LEMONADE_API_KEY = $LocalKey
    $lemonArgs = @(".\rt\cache", ".\rt\config", "--port", "$Port")
    $proc = Start-Process -PassThru -WindowStyle Hidden -FilePath ".\lemond.exe" -ArgumentList $lemonArgs
    Note "lemond started (pid $($proc.Id)) on port $Port"

    $healthy = $false
    foreach ($i in 1..40) {
        Start-Sleep -Seconds 3
        $health = "http://localhost:$Port/api/v1/health"
        $code = curl.exe -s -o NUL -w "%{http_code}" -m 5 -H "Authorization: Bearer $LocalKey" $health
        if ($code -eq "200") {
            $healthy = $true
            Ok "Lemonade healthy after $($i * 3)s"
            break
        }
    }
    if (-not $healthy) { throw "Lemonade never became healthy. Check $EmbedDir\rt" }
}
finally {
    Pop-Location
}

# --------------------------------------------------------------- wire up ---
Step 3 "Point GAIA at it"
Set-Location $RepoRoot
$env:PYTHONPATH = Join-Path $RepoRoot "src"    # gaia is -e linked elsewhere
$env:LEMONADE_BASE_URL = "http://localhost:$Port/api/v1"
$env:GAIA_GATEWAY_TOKEN = $token               # read by gateway auth/test
Ok "PYTHONPATH, LEMONADE_BASE_URL, LEMONADE_API_KEY, GAIA_GATEWAY_TOKEN set"

Step 4 "Register the gateway"
Invoke-Gaia gateway install --base-url $GatewayUrl

Step 5 "Authenticate (token comes from the environment, never from argv)"
Invoke-Gaia gateway auth

Step 6 "Discovered models"
$modelsOut = Invoke-Gaia gateway models
$modelsOut
if ($modelsOut -match "Gemma-4-31B") { Ok "on-prem Gemma-4-31B discovered" }
else { Bad "Gemma-4-31B missing" }
if ($modelsOut -match "Claude-Opus-5") { Ok "Claude-Opus-5 discovered" }
else { Bad "Claude-Opus-5 missing" }

Step 7 "Inference: on-prem Gemma-4-31B"
$r = Invoke-Gaia gateway test --model amd.Gemma-4-31B "Reply with the single word: ok"
$r
if ($r -match "prompt \+") { Ok "completion returned with token usage" }
else { Bad "no completion from Gemma-4-31B" }

Step 8 "Inference: Claude-Opus-5"
$r = Invoke-Gaia gateway test --model amd.Claude-Opus-5 "What is 2+2? Answer with just the number."
$r
if ($r -match "prompt \+") { Ok "completion returned with token usage" }
else { Bad "no completion from Claude-Opus-5" }

Step 9 "Agent path (gaia llm -> agent -> gateway)"
Invoke-Gaia gateway use amd.Claude-Opus-5
$r = Invoke-Gaia llm --model amd.Claude-Opus-5 "Say hello in exactly three words."
$r
if ($LASTEXITCODE -eq 0) { Ok "agent path completed" }
else { Bad "agent path failed (exit $LASTEXITCODE)" }

Step 10 "Security: is the token anywhere on disk?"
$hits = @()
foreach ($root in @((Join-Path $env:USERPROFILE ".gaia"), $EmbedDir)) {
    if (Test-Path $root) {
        $found = Get-ChildItem -Path $root -Recurse -File -ErrorAction SilentlyContinue |
        Select-String -SimpleMatch -Pattern $token -ErrorAction SilentlyContinue
        if ($found) { $hits += $found }
    }
}
if ($hits.Count -eq 0) {
    Ok "token found in 0 files under ~/.gaia and the Lemonade directory"
}
else {
    Bad "TOKEN ON DISK in: $(($hits | ForEach-Object { $_.Path }) -join ', ')"
}
Note "gateway.json (should contain no secret):"
Get-Content (Join-Path $env:USERPROFILE ".gaia\gateway.json") -ErrorAction SilentlyContinue |
ForEach-Object { "    $_" }

# --------------------------------------------------------------- wrap up ---
Write-Host ""
if ($script:Failures -eq 0) { Write-Host "ALL CHECKS PASSED" -ForegroundColor Green }
else { Write-Host "$script:Failures CHECK(S) FAILED" -ForegroundColor Red }

if ($KeepRunning) {
    Write-Host "`nLemonade left running on port $Port for TUI testing:" -ForegroundColor Yellow
    Write-Host "  `$env:LEMONADE_BASE_URL = 'http://localhost:$Port/api/v1'"
    Write-Host "  `$env:LEMONADE_API_KEY  = '$LocalKey'"
    Write-Host "  cd tui; go build -o bin\gaia.exe .\cmd\gaia; .\bin\gaia.exe     # press g"
    Write-Host "  Stop it later with: Get-Process lemond | Stop-Process -Force"
}
else {
    Get-Process lemond -ErrorAction SilentlyContinue | Stop-Process -Force
    Note "Lemonade stopped."
}

# The token dies with this process. It was never written anywhere.
Remove-Item Env:\GAIA_GATEWAY_TOKEN -ErrorAction SilentlyContinue
$token = $null
