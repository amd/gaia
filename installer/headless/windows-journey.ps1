# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# One phase of one Windows installer journey:
#
#   pwsh installer/headless/windows-journey.ps1 -Step <download|install|upgrade|launch|health|preflight|quit|uninstall|leftovers>
#
# It installs and uninstalls GAIA for the current user: HKCU, the user PATH,
# fonts, shortcuts and %LOCALAPPDATA%\Programs. It refuses to run anywhere but a
# GitHub-hosted CI runner. Run it from
# .github/workflows/test_installers_headless.yml, never on a machine you use.
#
# Inputs (environment):
#   KIND          win-setup | agent-ui-exe | win-setup-upgrade | install-ps1
#   COMPONENTS    (win-setup) ui | tui | ui,tui
#   WORK          a fresh directory under $env:RUNNER_TEMP
#   RELEASE_JSON  resolve_release.py's output for the release under test
#
# Paths and registry ids are the ones installer/tui/nsis/smoke-test.ps1 asserts.

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('download', 'install', 'upgrade', 'launch', 'health', 'preflight', 'quit', 'uninstall', 'leftovers')]
    [string]$Step
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$repo = (Resolve-Path "$here\..\..").Path
$journey = Join-Path $here 'journey.py'

function Fail([string]$Message) { throw "$($env:ROW) ${Step}: $Message" }

# ── Safety: CI runners only, and only inside a sandbox under RUNNER_TEMP ────
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:CI -ne 'true') {
    throw 'refusing to run outside GitHub Actions: this installs and removes GAIA for the current user.'
}
if (-not $env:RUNNER_TEMP -or -not (Test-Path $env:RUNNER_TEMP)) { throw 'RUNNER_TEMP is not set.' }
$work = $env:WORK
if (-not $work) { throw 'WORK is required.' }
$work = [System.IO.Path]::GetFullPath($work)
$runnerTemp = [System.IO.Path]::GetFullPath($env:RUNNER_TEMP)
if (-not $work.StartsWith($runnerTemp + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "WORK ($work) must be inside RUNNER_TEMP ($runnerTemp)."
}
foreach ($d in 'dl', 'logs', 'home') { New-Item -ItemType Directory -Force -Path "$work\$d" | Out-Null }
$logs = "$work\logs"

$rel = Get-Content $env:RELEASE_JSON -Raw | ConvertFrom-Json
$version = $rel.version
# gaia-tui stamps the tag itself: `gaia-tui version` -> "gaia v0.25.1-rc2 (...)".
$tuiVersion = $rel.tui_version
$pep440 = $rel.pep440

# The install-script row runs with a sandbox profile; every other row is the
# installers' own per-user locations on this throwaway runner.
if ($env:KIND -eq 'install-ps1') {
    $userHome = "$work\home"
} else {
    $userHome = $env:USERPROFILE
}
$gaiaHome = "$userHome\.gaia"

$tuiDir    = "$env:LOCALAPPDATA\Programs\GAIA Terminal Hub"
$tuiArp    = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\GAIATerminalHub'
$uiGuid    = '071ff68a-44b8-5d94-b099-f93e99d1c3f3'
$uiKey     = "HKCU:\Software\$uiGuid"
$uiArp     = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$uiGuid"
$uiDefault = "$env:LOCALAPPDATA\Programs\gaia-desktop"
$desktop   = [Environment]::GetFolderPath('Desktop')
$startMenu = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"
$runKey    = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$fragment  = "$env:LOCALAPPDATA\Microsoft\Windows Terminal\Fragments\GAIA\gaia.json"

function Asset-Name([string]$Id, [switch]$Previous) {
    $map = if ($Previous) { $rel.prev_assets } else { $rel.assets }
    $name = $map.$Id
    if (-not $name) { Fail "the release carries no $Id" }
    return $name
}
function Asset-Url([string]$Name, [switch]$Previous) {
    $urls = if ($Previous) { $rel.prev_urls } else { $rel.urls }
    return $urls.$Name
}
function Fetch([string]$Url, [string]$Dest) {
    Write-Host "GET $Url"
    & curl.exe -fL --retry 3 --retry-delay 5 --connect-timeout 30 -sS -o $Dest $Url
    if ($LASTEXITCODE -ne 0) { Fail "download failed (curl exit $LASTEXITCODE): $Url" }
    if ((Get-Item $Dest).Length -eq 0) { Fail "downloaded nothing from $Url" }
    Write-Host "$Dest $((Get-Item $Dest).Length) bytes"
}

function Invoke-Installer([string]$Exe, [string[]]$Arguments) {
    $p = Start-Process -FilePath $Exe -ArgumentList $Arguments -PassThru -Wait
    Write-Host "$(Split-Path $Exe -Leaf) $($Arguments -join ' ') -> exit $($p.ExitCode)"
    return $p.ExitCode
}

# An NSIS uninstaller re-runs itself from %TEMP% and returns at once; wait for
# the thing it removes rather than a fixed sleep.
function Wait-Gone([string]$Path, [int]$Seconds = 120) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Test-Path $Path) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
    if (Test-Path $Path) { Fail "$Path was still there $Seconds s after the uninstaller ran" }
}

function Ui-Dir {
    if (-not (Test-Path $uiKey)) { return $null }
    return (Get-ItemProperty -Path $uiKey -Name InstallLocation -ErrorAction SilentlyContinue).InstallLocation
}

function Wants([string]$Component) {
    switch ($env:KIND) {
        'win-setup' { return ($env:COMPONENTS -split ',') -contains $Component }
        'agent-ui-exe' { return $Component -eq 'ui' }
        'win-setup-upgrade' { return $Component -eq 'ui' }
        'install-ps1' { return $Component -eq 'tui' }
    }
    return $false
}

# Resolved now, so a phase that puts GAIA's venv first on PATH still runs the
# runner's Python for the helpers.
$python = (Get-Command python -ErrorAction Stop).Source

# The PATH a terminal opened after the install gets: the setup writes the user
# PATH in the registry, which this already-running shell never re-reads.
function Use-NewTerminalPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:PATH = "$machine;$user;" + (Split-Path $python)
}

function Tui-Exe {
    if ($env:KIND -eq 'install-ps1') { return "$gaiaHome\bin\gaia-tui.exe" }
    return "$tuiDir\gaia-tui.exe"
}

# A release stamps gaia-tui in two spellings today: the raw binaries say the
# tag (v0.25.1-rc2), the setup's copy the package version (0.25.1-rc.2).
# Either names this release.
function Check-TuiVersion([string[]]$Expected) {
    $out = & (Tui-Exe) version 2>&1 | Out-String
    Write-Host "gaia-tui version -> $out"
    if ($LASTEXITCODE -ne 0) { Fail "gaia-tui version exited $LASTEXITCODE" }
    $want = @($Expected | Where-Object { $_ })
    if ($want.Count -and -not ($want | Where-Object { $out -match [regex]::Escape($_) })) {
        Fail "gaia-tui reports '$out', expected $($want -join ' or ')"
    }
}

# ── The Agent UI ──────────────────────────────────────────────────────────────
function Start-AgentUi {
    $dir = Ui-Dir
    if (-not $dir -or -not (Test-Path "$dir\gaia-desktop.exe")) { Fail "no installed gaia-desktop.exe to launch (InstallLocation: '$dir')" }
    # These rows install a published release; one before #5629 runs gaia init
    # (several GB of models) on first launch. Later apps ignore the variable.
    $env:GAIA_SKIP_GAIA_INIT = '1'
    $p = Start-Process -FilePath "$dir\gaia-desktop.exe" -WindowStyle Hidden -PassThru
    Set-Content -Path "$work\app.pid" -Value $p.Id
    Start-Sleep -Seconds 10
    if ($p.HasExited) { Get-Content "$gaiaHome\electron-main.log" -Tail 50 -ErrorAction SilentlyContinue; Fail "the app exited within 10s of launch (exit $($p.ExitCode))" }
    if (-not (Test-Path "$gaiaHome\electron-main.log")) { Fail "the app is running but wrote no $gaiaHome\electron-main.log" }
    Write-Host "launched pid $($p.Id)"
}

# First launch installs the Python backend from PyPI before it can serve, so
# the bound is generous; the threshold file is what flags it getting slower.
function Wait-AgentUiHealth {
    $pid0 = [int](Get-Content "$work\app.pid")
    $deadline = (Get-Date).AddMinutes(25)
    $port = $null; $ok = $false
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-Process -Id $pid0 -ErrorAction SilentlyContinue)) {
            Get-Content "$gaiaHome\electron-main.log" -Tail 80 -ErrorAction SilentlyContinue
            Fail 'the app exited before its backend answered'
        }
        $m = Select-String -Path "$gaiaHome\electron-main.log" -Pattern 'ui-port[ =]+(\d+)' -ErrorAction SilentlyContinue | Select-Object -Last 1
        if ($m) {
            $port = $m.Matches[0].Groups[1].Value
            try {
                $h = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 5
                if ($h.service -eq 'gaia-agent-ui') { $ok = $true; break }
            } catch { }
        }
        Start-Sleep -Seconds 3
    }
    if (-not $ok) {
        Get-Content "$gaiaHome\electron-main.log" -Tail 80 -ErrorAction SilentlyContinue
        Fail 'the backend did not answer /api/health within 25 min'
    }
    Write-Host "GET /api/health on $port -> ok (service gaia-agent-ui)"
    $got = & "$gaiaHome\venv\Scripts\gaia.exe" --version 2>&1 | Out-String
    Write-Host "backend gaia --version -> $got"
    if ($got -notmatch [regex]::Escape($pep440)) { Fail "the backend the app installed is '$got', expected $pep440" }
}

function Stop-AgentUi {
    $backendPid = Get-Content "$gaiaHome\backend.pid" -ErrorAction SilentlyContinue
    if ($backendPid) { & taskkill.exe /T /F /PID $backendPid 2>&1 | Out-Null }
    & taskkill.exe /T /F /IM gaia-desktop.exe 2>&1 | Out-Null
    Get-Process | Where-Object { $_.Path -and $_.Path -like "$gaiaHome\venv\*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
}

# ── The TUI checks ────────────────────────────────────────────────────────────
function Start-Fake {
    Remove-Item "$work\fake.port" -ErrorAction SilentlyContinue
    # The flagship profile's models (INIT_PROFILES in src/gaia/installer/
    # init_command.py) and the larger default a big machine picks, as downloaded.
    $fakeArgs = @("$here\fake_model_server.py", '--port-file', "$work\fake.port", '--log', "$logs\fake-requests.jsonl",
        '--model', 'Gemma-4-E4B-it-GGUF', '--model', 'user.embeddinggemma-300m-GGUF', '--model', 'Qwen3.6-35B-A3B-GGUF')
    $p = Start-Process -FilePath $python -ArgumentList $fakeArgs `
        -PassThru -RedirectStandardOutput "$logs\fake-server.out" -RedirectStandardError "$logs\fake-server.err"
    Set-Content -Path "$work\fake.pid" -Value $p.Id
    for ($i = 0; $i -lt 150 -and -not (Test-Path "$work\fake.port"); $i++) { Start-Sleep -Milliseconds 200 }
    if (-not (Test-Path "$work\fake.port")) { Get-Content "$logs\fake-server.err"; Fail 'the fake model server did not start' }
    return "http://127.0.0.1:$((Get-Content "$work\fake.port").Trim())/api/v1"
}

function One-Shot {
    Use-NewTerminalPath
    $url = Start-Fake
    $env:LEMONADE_BASE_URL = $url
    try {
        & $python $journey bounded --seconds 600 --stdout "$logs\oneshot.out" --stderr "$logs\oneshot.err" -- `
            (Tui-Exe) chat --agent gaia --query 'Reply with a short greeting.' --timeout 8m
        $rc = $LASTEXITCODE
    } finally {
        Remove-Item Env:\LEMONADE_BASE_URL -ErrorAction SilentlyContinue
        Stop-Process -Id ([int](Get-Content "$work\fake.pid")) -Force -ErrorAction SilentlyContinue
    }
    Write-Host '--- stdout ---'; Get-Content "$logs\oneshot.out" -ErrorAction SilentlyContinue
    Write-Host '--- stderr (tail) ---'; Get-Content "$logs\oneshot.err" -Tail 60 -ErrorAction SilentlyContinue
    Write-Host '--- endpoints the agent called ---'
    if (Test-Path "$logs\fake-requests.jsonl") {
        Get-Content "$logs\fake-requests.jsonl" | ForEach-Object { $_ | ConvertFrom-Json } |
            Group-Object { "$($_.method) $(($_.path -split '\?')[0]) $($_.status)" } | ForEach-Object { "$($_.Count) $($_.Name)" }
    } else { Write-Host '(no requests reached the fake server)' }
    if ($rc -ne 0) { Fail "the one-shot turn exited $rc" }
    if (-not (Select-String -Path "$logs\oneshot.out" -Pattern 'fake model server' -Quiet)) { Fail 'the one-shot exited 0 but did not print the model''s answer' }
}

# The Windows setup always installs Lemonade (by design), so "no Lemonade" here
# is a Lemonade that does not answer: LEMONADE_BASE_URL on a closed port. The
# one-shot must refuse quickly and say how to fix it -- not hang, not exit 0.
function Preflight-NoLemonade {
    Use-NewTerminalPath
    $env:LEMONADE_BASE_URL = 'http://127.0.0.1:9/api/v1'
    try {
        & $python $journey bounded --seconds 120 --stdout "$logs\preflight.out" --stderr "$logs\preflight.err" --elapsed-file "$work\preflight.elapsed" -- `
            (Tui-Exe) chat --agent gaia --query 'hello' --timeout 2m
        $rc = $LASTEXITCODE
    } finally {
        Remove-Item Env:\LEMONADE_BASE_URL -ErrorAction SilentlyContinue
    }
    Write-Host '--- stderr ---'; Get-Content "$logs\preflight.err" -ErrorAction SilentlyContinue
    if ($rc -eq 124) { Fail 'with Lemonade not answering, the one-shot was still running after 120s; it should refuse in seconds' }
    if ($rc -eq 0) { Fail 'with Lemonade not answering, the one-shot exited 0' }
    $err = Get-Content "$logs\preflight.err" -Raw
    if ($err -notmatch 'is not ready') { Fail 'the refusal does not say what is not ready' }
    if ($err -notmatch '(?i)lemonade') { Fail 'the refusal does not name Lemonade' }
    if ($err -notmatch 'Nothing was sent') { Fail 'the refusal does not say the query was not sent' }
    Write-Host "refused in $((Get-Content "$work\preflight.elapsed").Trim())s with exit $rc"
}

# ── Uninstall helpers ─────────────────────────────────────────────────────────
function Uninstall-Tui {
    if (-not (Test-Path "$tuiDir\Uninstall.exe")) { return }
    $rc = Invoke-Installer "$tuiDir\Uninstall.exe" @('/S')
    if ($rc -ne 0) { Fail "the terminal's uninstaller exited $rc" }
    Wait-Gone $tuiDir
}
function Uninstall-Ui {
    if (-not (Test-Path $uiArp)) { return }
    $dir = Ui-Dir
    $quiet = (Get-ItemProperty -Path $uiArp -Name QuietUninstallString).QuietUninstallString
    Write-Host "desktop app quiet uninstall: $quiet"
    $u = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $quiet -PassThru -Wait
    if ($u.ExitCode -ne 0) { Fail "the desktop app's uninstaller exited $($u.ExitCode)" }
    Wait-Gone $uiArp
    if ($dir) { Wait-Gone "$dir\gaia-desktop.exe" }
}

function Setup-Name { return Asset-Name 'win-setup' }

# The installer the previous stable release offered Windows users: its one
# setup when it had one, else the desktop app's own setup.
function Previous-Installer {
    if (-not $rel.prev_tag) { Fail 'there is no previous stable release to upgrade from' }
    if ($rel.prev_assets.'win-setup') { return @{ Id = 'win-setup'; Name = $rel.prev_assets.'win-setup' } }
    if ($rel.prev_assets.'agent-ui-exe') { return @{ Id = 'agent-ui-exe'; Name = $rel.prev_assets.'agent-ui-exe' } }
    Fail "$($rel.prev_tag) carries no Windows installer"
}

# ── Phases ────────────────────────────────────────────────────────────────────
switch ($Step) {
    'download' {
        switch ($env:KIND) {
            'win-setup' { $n = Setup-Name; Fetch (Asset-Url $n) "$work\dl\$n" }
            'agent-ui-exe' { $n = Asset-Name 'agent-ui-exe'; Fetch (Asset-Url $n) "$work\dl\$n" }
            'win-setup-upgrade' {
                $prev = Previous-Installer
                Fetch (Asset-Url $prev.Name -Previous) "$work\dl\$($prev.Name)"
                $n = Setup-Name; Fetch (Asset-Url $n) "$work\dl\$n"
            }
            default { Fail "no download for $($env:KIND)" }
        }
    }

    'install' {
        if ($env:KIND -ne 'install-ps1') {
            if ((Test-Path $tuiDir) -or (Test-Path $uiKey) -or (Test-Path $gaiaHome)) { Fail 'GAIA is already on this runner: it is not clean' }
        }
        switch ($env:KIND) {
            'win-setup' {
                $rc = Invoke-Installer "$work\dl\$(Setup-Name)" @('/S', "/COMPONENTS=$($env:COMPONENTS)")
                if ($rc -ne 0) { Fail "setup exited $rc" }
            }
            'agent-ui-exe' {
                $rc = Invoke-Installer "$work\dl\$(Asset-Name 'agent-ui-exe')" @('/S')
                if ($rc -ne 0) { Fail "the desktop app's setup exited $rc" }
            }
            'win-setup-upgrade' {
                # The state an upgrading user is in: the previous release
                # installed AND used once, so its backend venv exists.
                $prev = Previous-Installer
                $args0 = if ($prev.Id -eq 'win-setup') { @('/S', '/COMPONENTS=ui') } else { @('/S') }
                $rc = Invoke-Installer "$work\dl\$($prev.Name)" $args0
                if ($rc -ne 0) { Fail "the $($rel.prev_tag) installer exited $rc" }
                $script:pep440 = $rel.prev_pep440
                Start-AgentUi
                Wait-AgentUiHealth
                Stop-AgentUi
                $marker = "$gaiaHome\headless-upgrade-marker"
                Set-Content -Path $marker -Value ([guid]::NewGuid().ToString())
                Copy-Item $marker "$work\marker.expected"
                Write-Host "$($rel.prev_tag) installed, launched once, marker written"
            }
            'install-ps1' {
                if (Test-Path $gaiaHome) { Fail "$gaiaHome exists before install: the sandbox is not fresh" }
                $script = "$repo\installer\scripts\install.ps1"
                if (Select-String -Path $script -Pattern 'GAIA_RELEASE_TAG' -Quiet) {
                    $env:GAIA_RELEASE_TAG = $rel.tag
                } else {
                    Write-Host "::notice::install.ps1 on this ref does not read GAIA_RELEASE_TAG (#5509 not merged): it installs the newest published release, not $($rel.tag)."
                }
                # The documented `irm ... | iex`, in Windows PowerShell, with the
                # profile pointed at the sandbox.
                $env:USERPROFILE = $userHome
                $env:HOME = $userHome
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -Raw '$script' | Invoke-Expression"
                if ($LASTEXITCODE -ne 0) { Fail "install.ps1 failed (exit $LASTEXITCODE)" }
                foreach ($f in "$gaiaHome\venv\Scripts\gaia.exe", "$gaiaHome\bin\gaia-tui.exe", "$gaiaHome\bin\gaia-agent.exe") {
                    if (-not (Test-Path $f)) { Fail "install.ps1 finished but $f is not there" }
                }
            }
        }
        if (Wants 'tui') {
            if ($env:KIND -ne 'install-ps1') {
                foreach ($f in 'gaia-tui.exe', 'gaia-agent.exe', 'Uninstall.exe') {
                    if (-not (Test-Path "$tuiDir\$f")) { Fail "$f did not land in $tuiDir" }
                }
                $regPath = (Get-ItemProperty -Path 'HKCU:\Environment' -Name Path).Path
                if ($regPath -notlike "*$tuiDir*") { Fail "the terminal's folder was not added to the user PATH" }
            }
        } elseif (Test-Path $tuiDir) { Fail "$tuiDir exists though the terminal was not chosen" }
        if (Wants 'ui') {
            $dir = Ui-Dir
            if (-not $dir -or -not (Test-Path "$dir\gaia-desktop.exe")) { Fail "the desktop app is not installed (InstallLocation: '$dir')" }
        } elseif (Test-Path $uiKey) { Fail 'the desktop app was installed though it was not chosen' }
    }

    'upgrade' {
        if ($env:KIND -ne 'win-setup-upgrade') { Fail "no upgrade for $($env:KIND)" }
        # Plain /S keeps what is installed: the desktop app, upgraded in place.
        $rc = Invoke-Installer "$work\dl\$(Setup-Name)" @('/S')
        if ($rc -ne 0) { Fail "the upgrade setup exited $rc" }
        $shown = (Get-ItemProperty -Path $uiArp -Name DisplayVersion).DisplayVersion
        Write-Host "Installed apps now shows the desktop app at $shown"
        if ($shown -ne $version) { Fail "after the upgrade the desktop app is $shown, expected $version" }
        if ((Get-Content "$gaiaHome\headless-upgrade-marker" -Raw) -ne (Get-Content "$work\marker.expected" -Raw)) {
            Fail 'the upgrade lost or changed ~\.gaia\headless-upgrade-marker'
        }
        Write-Host 'marker survived the upgrade'
    }

    'launch' {
        if (Wants 'ui') { Start-AgentUi }
        if (Wants 'tui') {
            if ($env:KIND -eq 'install-ps1') { Check-TuiVersion @() } else { Check-TuiVersion @($tuiVersion, $version) }
            if ($env:KIND -eq 'install-ps1') {
                & "$gaiaHome\venv\Scripts\gaia.exe" --version
                if (Select-String -Path "$repo\installer\scripts\install.ps1" -Pattern 'GAIA_RELEASE_TAG' -Quiet) {
                    Check-TuiVersion @($tuiVersion, $version)
                    $got = & "$gaiaHome\venv\Scripts\gaia.exe" --version 2>&1 | Out-String
                    if ($got -notmatch [regex]::Escape($pep440)) { Fail "install.ps1 was asked for $($rel.tag) but installed $got" }
                }
            }
        }
    }

    'health' {
        if (Wants 'ui') {
            Wait-AgentUiHealth
            if ($env:KIND -eq 'win-setup-upgrade' -and
                (Get-Content "$gaiaHome\headless-upgrade-marker" -Raw) -ne (Get-Content "$work\marker.expected" -Raw)) {
                Fail 'the upgraded app lost or changed ~\.gaia\headless-upgrade-marker on first launch'
            }
        }
        if (Wants 'tui') {
            # One-Shot takes the PATH a new terminal would, which is where
            # install.ps1 put GAIA too.
            if ($env:KIND -eq 'install-ps1') { $env:USERPROFILE = $userHome; $env:HOME = $userHome }
            One-Shot
        }
    }

    'preflight' {
        if ($env:KIND -eq 'install-ps1') { $env:USERPROFILE = $userHome; $env:HOME = $userHome }
        Preflight-NoLemonade
    }

    # Closing the window only hides the app to the tray, and the tray's Quit
    # needs a click, so the app and its backend are stopped as a process tree.
    'quit' {
        Stop-AgentUi
        $left = Get-Process -ErrorAction SilentlyContinue | Where-Object {
            $_.ProcessName -eq 'gaia-desktop' -or ($_.Path -and $_.Path -like "$gaiaHome\venv\*")
        }
        if ($left) { Fail "still running after the app was stopped: $(($left | ForEach-Object { "$($_.Id) $($_.Path)" }) -join '; ')" }
    }

    'uninstall' {
        if ($env:KIND -eq 'install-ps1') {
            $env:USERPROFILE = $userHome; $env:HOME = $userHome
            # Who holds files in the venv when the uninstall starts: a file a
            # running process has loaded cannot be deleted on Windows.
            $holders = Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like "$gaiaHome\*" -or $_.CommandLine -like "*$gaiaHome*" }
            Write-Host '--- processes using the GAIA home before uninstall ---'
            $holders | ForEach-Object { "  $($_.ProcessId) $($_.CommandLine)" }
            & "$gaiaHome\venv\Scripts\gaia.exe" uninstall --purge --yes
            $rc = $LASTEXITCODE
            if ($rc -ne 0) {
                Write-Host '--- and after it ---'
                Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like "$gaiaHome\*" } | ForEach-Object { "  $($_.ProcessId) $($_.CommandLine)" }
                Fail "gaia uninstall --purge exited $rc"
            }
        } else {
            Uninstall-Tui
            Uninstall-Ui
        }
    }

    'leftovers' {
        $problems = @()
        if ($env:KIND -eq 'install-ps1') {
            foreach ($p in "$gaiaHome\venv", "$gaiaHome\bin") { if (Test-Path $p) { $problems += "left behind: $p" } }
            $regPath = (Get-ItemProperty -Path 'HKCU:\Environment' -Name Path -ErrorAction SilentlyContinue).Path
            if ($regPath -like "*$gaiaHome*") { $problems += 'the user PATH still names the sandbox .gaia' }
        } else {
            foreach ($p in $tuiDir, $tuiArp, $uiArp, $uiDefault, "$desktop\GAIA.lnk", "$desktop\GAIA Terminal UI.lnk",
                           "$startMenu\GAIA.lnk", "$startMenu\GAIA\GAIA Terminal UI.lnk", $fragment) {
                if (Test-Path $p) { $problems += "left behind: $p" }
            }
            $regPath = (Get-ItemProperty -Path 'HKCU:\Environment' -Name Path -ErrorAction SilentlyContinue).Path
            if ($regPath -like '*Programs\GAIA Terminal Hub*') { $problems += "the terminal's folder is still on the user PATH" }
            if (Get-ItemProperty -Path $runKey -Name 'GAIA' -ErrorAction SilentlyContinue) { $problems += 'the desktop app still starts at sign-in (Run key GAIA)' }
            if (Test-Path $uiKey) { Write-Host "::warning::$uiKey survived the uninstall" }
        }
        $running = Get-Process -ErrorAction SilentlyContinue | Where-Object {
            $_.ProcessName -in @('gaia-desktop', 'gaia-tui', 'gaia-agent') -or ($_.Path -and $_.Path -like "$gaiaHome\venv\*")
        }
        foreach ($r in $running) { $problems += "still running: $($r.ProcessName) ($($r.Path))" }
        # Kept on purpose: user data in ~\.gaia and Lemonade Server (the setup
        # never uninstalls it; `gaia uninstall --purge-lemonade` does).
        Write-Host '--- kept by design ---'
        if (Test-Path $gaiaHome) { Get-ChildItem $gaiaHome -Force | Select-Object -First 30 | ForEach-Object { "  ~\.gaia\$($_.Name)" } }
        Get-ItemProperty 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -like '*Lemonade*' } | ForEach-Object { "  $($_.DisplayName) $($_.DisplayVersion)" }
        if ($problems) { $problems | ForEach-Object { Write-Host "::error::$_" }; Fail "$($problems.Count) leftover(s)" }
        Write-Host 'nothing left behind'
    }
}
