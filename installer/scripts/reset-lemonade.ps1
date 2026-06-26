<#
.SYNOPSIS
    Wipe every Lemonade Server / llama.cpp install from a Windows box and
    reinstall exactly the version GAIA pins (src/gaia/version.LEMONADE_VERSION).

.DESCRIPTION
    Self-hosted CI runners accumulate stale Lemonade installs across user
    profiles (e.g. a leftover ``C:\Users\<user>\AppData\Local\lemonade_server``
    whose ``lemonade-server`` shim shadows the freshly-installed one on PATH).
    A stale server serves an old model catalog and an old llama.cpp that cannot
    load current models (e.g. Gemma-4 -> "Failed to load ... with llama-server").

    This script brings the box to a known-clean state:
      1. Stop every Lemonade / llama-server / tray process.
      2. Uninstall Lemonade via the Windows uninstall registry (msiexec /x).
      3. pip-uninstall lemonade-sdk from any Python on PATH.
      4. Delete per-user + systemprofile install dirs (all profiles) and the
         ``.cache\lemonade`` backend cache (llama.cpp binaries live here).
      5. Strip stale lemonade entries from the user + machine PATH.
      6. Download + silently install the matching ``lemonade.msi``.
      7. Verify: start the server, health-check, list the catalog, and confirm
         the GAIA default model is present and loadable.

    Run elevated (admin) on the runner. Removing other users' profiles and
    machine PATH entries requires it.

.PARAMETER Version
    Lemonade version to install. Defaults to LEMONADE_VERSION from
    src/gaia/version.py when run from a GAIA checkout, else 10.8.1.

.PARAMETER Port
    Port to verify the server on. Default 13305.

.PARAMETER VerifyModel
    Model id to confirm is present + loadable after install.
    Default Gemma-4-E4B-it-GGUF (GAIA's DEFAULT_MODEL_NAME).

.PARAMETER KeepModelCache
    Keep the Hugging Face model cache (avoids re-downloading GGUFs). Off by
    default -- a full clean also clears downloaded models.

.PARAMETER SkipInstall
    Clean only; do not reinstall (leaves the box with no Lemonade).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File installer\scripts\reset-lemonade.ps1
.EXAMPLE
    .\reset-lemonade.ps1 -Version 10.8.1 -VerifyModel Qwen3-4B-Instruct-2507-GGUF
#>
[CmdletBinding()]
param(
    [string]$Version,
    [int]$Port = 13305,
    [string]$VerifyModel = "Gemma-4-E4B-it-GGUF",
    [switch]$KeepModelCache,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Continue"
function Log($m) { Write-Host "[reset-lemonade] $m" }
function Section($m) { Write-Host "`n===== $m =====" }

# ---- Resolve target version -------------------------------------------------
if (-not $Version) {
    $verPy = Join-Path $PSScriptRoot "..\..\src\gaia\version.py"
    if (Test-Path $verPy) {
        $line = Select-String -Path $verPy -Pattern 'LEMONADE_VERSION\s*=\s*"([0-9.]+)"' | Select-Object -First 1
        if ($line) { $Version = $line.Matches[0].Groups[1].Value }
    }
    if (-not $Version) { $Version = "10.8.1" }
}
Log "Target Lemonade version: $Version"

# ---- 1. Stop processes ------------------------------------------------------
Section "Stopping Lemonade / llama-server processes"
foreach ($name in @("LemonadeServer", "lemonade-server", "lemonade", "llama-server", "llama-server-dev", "lemonade-server-dev")) {
    Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
        Log "Killing $($_.Name) (PID $($_.Id))"
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
}
# Python processes hosting a lemonade server
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match "python" -and $_.CommandLine -match "lemonade" } |
    ForEach-Object { Log "Killing python lemonade host (PID $($_.ProcessId))"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

# ---- 2. Uninstall via registry (msiexec /x) ---------------------------------
Section "Uninstalling Lemonade via Windows uninstall registry"
$uninstallRoots = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*"
)
foreach ($root in $uninstallRoots) {
    Get-ItemProperty $root -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -match "Lemonade" } |
        ForEach-Object {
            $dn = $_.DisplayName
            if ($_.PSChildName -match '^\{[0-9A-Fa-f-]+\}$') {
                Log "Uninstalling '$dn' ($($_.PSChildName)) via msiexec /x"
                $p = Start-Process -FilePath "msiexec.exe" `
                    -ArgumentList "/x", $_.PSChildName, "/quiet", "/norestart" `
                    -Wait -PassThru -NoNewWindow
                Log "  msiexec /x exit code: $($p.ExitCode)"
            } elseif ($_.UninstallString) {
                Log "Uninstalling '$dn' via UninstallString"
                try { cmd /c "`"$($_.UninstallString)`" /quiet /norestart" 2>&1 | Out-Null } catch { Log "  failed: $_" }
            }
        }
}

# ---- 3. pip uninstall lemonade-sdk ------------------------------------------
Section "Removing pip-installed lemonade-sdk (all Python on PATH)"
$pythons = @()
foreach ($exe in @("python", "python3")) {
    Get-Command $exe -ErrorAction SilentlyContinue | ForEach-Object { $pythons += $_.Source }
}
$pythons = $pythons | Sort-Object -Unique
foreach ($py in $pythons) {
    Log "pip uninstall lemonade-sdk via $py"
    & $py -m pip uninstall -y lemonade-sdk 2>&1 | ForEach-Object { Log "  $_" }
}

# ---- 4. Delete install dirs + backend cache (all profiles) ------------------
Section "Deleting install directories and backend cache"
$targets = New-Object System.Collections.Generic.List[string]
# Every user profile's per-user install (catches the stale nimbys one)
Get-ChildItem "C:\Users" -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    $targets.Add((Join-Path $_.FullName "AppData\Local\lemonade_server"))
    $targets.Add((Join-Path $_.FullName ".cache\lemonade"))
    if (-not $KeepModelCache) {
        $targets.Add((Join-Path $_.FullName ".cache\huggingface\hub"))  # downloaded GGUFs + llama.cpp models
    }
}
# Service-account profile (CI runs as a service)
$targets.Add("C:\windows\system32\config\systemprofile\AppData\Local\lemonade_server")
$targets.Add("C:\windows\system32\config\systemprofile\.cache\lemonade")
if (-not $KeepModelCache) {
    $targets.Add("C:\windows\system32\config\systemprofile\.cache\huggingface\hub")
}
# Program Files installs
$targets.Add("$env:ProgramFiles\Lemonade Server")
$targets.Add("${env:ProgramFiles(x86)}\Lemonade Server")

foreach ($t in ($targets | Sort-Object -Unique)) {
    if (Test-Path $t) {
        Log "Removing $t"
        Remove-Item -LiteralPath $t -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path $t) { Log "  WARN: could not fully remove (in use / permissions): $t" }
    }
}
if ($KeepModelCache) { Log "KeepModelCache set -- left Hugging Face model cache in place" }

# ---- 5. Strip stale lemonade PATH entries -----------------------------------
Section "Cleaning lemonade entries from PATH (User + Machine)"
foreach ($scope in @("User", "Machine")) {
    try {
        $p = [Environment]::GetEnvironmentVariable("Path", $scope)
        if ($p) {
            $kept = ($p -split ';' | Where-Object { $_ -and ($_ -notmatch "lemonade") })
            $new = ($kept -join ';')
            if ($new -ne $p) {
                [Environment]::SetEnvironmentVariable("Path", $new, $scope)
                Log "Stripped lemonade entries from $scope PATH"
            } else {
                Log "No lemonade entries in $scope PATH"
            }
        }
    } catch { Log "Could not edit $scope PATH (need admin?): $_" }
}

if ($SkipInstall) { Section "SkipInstall set -- done (no reinstall)"; return }

# ---- 6. Install the pinned MSI ----------------------------------------------
Section "Installing Lemonade $Version (silent MSI)"
$url  = "https://github.com/lemonade-sdk/lemonade/releases/download/v$Version/lemonade.msi"
$dest = Join-Path $env:TEMP "lemonade-$Version.msi"
Log "Downloading $url"
try {
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -TimeoutSec 600
} catch {
    Log "ERROR: MSI download failed: $_"; exit 1
}
Log "Running: msiexec /i $dest /quiet /norestart"
$msi = Start-Process -FilePath "msiexec.exe" `
    -ArgumentList "/i", $dest, "/quiet", "/norestart", "/L*v", "$env:TEMP\lemonade-msi-install.log" `
    -Wait -PassThru -NoNewWindow
Log "msiexec exit code: $($msi.ExitCode)"
if ($msi.ExitCode -ne 0) {
    Log "MSI install failed. Tail of log:"
    Get-Content "$env:TEMP\lemonade-msi-install.log" -Tail 60 -ErrorAction SilentlyContinue | ForEach-Object { Log "  $_" }
    exit 1
}

# ---- 7. Verify --------------------------------------------------------------
Section "Verifying install (server start + catalog + load)"
# Locate the freshly installed server binary.
$serverExe = $null
foreach ($cand in @(
    "$env:LOCALAPPDATA\lemonade_server\bin\LemonadeServer.exe",
    "$env:ProgramFiles\Lemonade Server\bin\LemonadeServer.exe",
    "${env:ProgramFiles(x86)}\Lemonade Server\bin\LemonadeServer.exe"
)) { if (Test-Path $cand) { $serverExe = $cand; break } }

if (-not $serverExe) {
    $found = Get-ChildItem "C:\Users\*\AppData\Local\lemonade_server\bin\LemonadeServer.exe","C:\Program Files*\Lemonade Server\bin\LemonadeServer.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { $serverExe = $found.FullName }
}
Log "Server binary: $serverExe"

# v10.5 removed the `lemonade-server` shim, so launch LemonadeServer.exe directly
# with a bare `--port` (there is no `serve` subcommand). Without --port it binds
# whatever stale config.json says rather than $Port.
$proc = $null
if ($serverExe) {
    Log "Starting LemonadeServer.exe with --port $Port"
    $proc = Start-Process -FilePath $serverExe -ArgumentList "--port", "$Port" -PassThru -WindowStyle Hidden
} else {
    Log "ERROR: no server binary found after install"; exit 1
}

$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    try {
        $h = Invoke-RestMethod -Uri "http://localhost:$Port/api/v1/health" -TimeoutSec 5
        $ready = $true; Log "Health OK (version=[$($h.version)])"; break
    } catch { }
}
if (-not $ready) { Log "ERROR: server did not become healthy on port $Port"; exit 1 }

try {
    $m = Invoke-RestMethod -Uri "http://localhost:$Port/api/v1/models" -TimeoutSec 20
    Log "Catalog model count: $($m.data.Count)"
    $present = ($m.data | Where-Object { $_.id -eq $VerifyModel }).Count -gt 0
    Log "VerifyModel '$VerifyModel' present in catalog: $present"
} catch { Log "Could not list models: $_" }

Log "Pulling '$VerifyModel' ..."
try {
    $body = @{ model_name = $VerifyModel } | ConvertTo-Json
    Invoke-RestMethod -Uri "http://localhost:$Port/api/v1/pull" -Method POST -ContentType "application/json" -Body $body -TimeoutSec 1200 | Out-Null
    Log "PULL OK"
} catch {
    Log "PULL FAILED: $($_.Exception.Message)"
    if ($_.ErrorDetails) { Log "  Details: $($_.ErrorDetails.Message)" }
}

Log "Loading '$VerifyModel' (proves llama.cpp can run it) ..."
try {
    $body = @{ model_name = $VerifyModel } | ConvertTo-Json
    Invoke-RestMethod -Uri "http://localhost:$Port/api/v1/load" -Method POST -ContentType "application/json" -Body $body -TimeoutSec 300 | Out-Null
    Log "LOAD OK -- '$VerifyModel' runs on this box"
} catch {
    Log "LOAD FAILED: $($_.Exception.Message)"
    if ($_.ErrorDetails) { Log "  Details: $($_.ErrorDetails.Message)" }
}

Section "Done"
Log "If health/pull/load all reported OK, the runner now serves '$VerifyModel'."
Log "Re-open the shell (or refresh PATH) so the new lemonade-server is picked up."
