# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Install, verify and uninstall the one Windows GAIA setup on a CLEAN runner.
# It writes to HKCU, HKLM, the user PATH, fonts and the desktop: never run it on
# a machine you use. CI runs it from .github/workflows/windows_setup.yml.
#
#   pwsh installer/tui/nsis/smoke-test.ps1 -Setup dist/gaia-0.25.0-win-x64-setup.exe -Version 0.25.0

param(
    [Parameter(Mandatory = $true)][string]$Setup,
    [Parameter(Mandatory = $true)][string]$Version
)

$ErrorActionPreference = 'Stop'
$Setup = (Resolve-Path $Setup).Path
$repo = (Resolve-Path "$PSScriptRoot\..\..\..").Path

$tuiDir    = "$env:LOCALAPPDATA\Programs\GAIA Terminal Hub"
$tuiArp    = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\GAIATerminalHub'
# electron-builder's identity for appId ai.amd.gaia; the .nsi pins the same.
$uiGuid    = '071ff68a-44b8-5d94-b099-f93e99d1c3f3'
$uiKey     = "HKCU:\Software\$uiGuid"
$uiArp     = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$uiGuid"
# Where the desktop app's per-user setup installs (electron-builder's own choice).
$uiDefault = "$env:LOCALAPPDATA\Programs\gaia-desktop"
$desktop   = [Environment]::GetFolderPath('Desktop')
$startMenu = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"
$runKey    = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'

function Invoke-Setup([string[]]$Arguments) {
    $p = Start-Process -FilePath $Setup -ArgumentList $Arguments -PassThru -Wait
    Write-Host "setup $($Arguments -join ' ') -> exit $($p.ExitCode)"
    return $p.ExitCode
}

function Assert-NothingInstalled([string]$When) {
    if (Test-Path $tuiDir) { throw "$When - $tuiDir exists" }
    if (Test-Path $tuiArp) { throw "$When - the terminal's Installed-apps entry exists" }
    if (Test-Path $uiKey)  { throw "$When - the desktop app's registry key exists" }
}

# An NSIS uninstaller re-runs itself from %TEMP% and returns at once; wait for
# the thing it removes rather than a fixed sleep.
function Wait-Gone([string]$Path, [int]$Seconds = 90) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Test-Path $Path) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
    if (Test-Path $Path) { throw "$Path was still there $Seconds s after the uninstaller ran" }
}

function Get-UiDir { (Get-ItemProperty -Path $uiKey -Name InstallLocation).InstallLocation }

if ((Test-Path $tuiDir) -or (Test-Path $uiKey)) { throw "GAIA is already installed - this runner is not clean" }

# ── 1. Refusals change nothing ─────────────────────────────────────────────
foreach ($bad in '/COMPONENTS=bogus', '/COMPONENTS=', '/COMPONENTS=ui,nope') {
    $rc = Invoke-Setup @('/S', $bad)
    if ($rc -ne 3) { throw "$bad should exit 3 (usage), got $rc" }
    Assert-NothingInstalled "after $bad"
}

# The developer build has its own gaia-tui on PATH and its own "GAIA" entry:
# refused whatever is chosen.
$devDir = Join-Path ([System.IO.Path]::GetTempPath()) 'gaia-dev-build'
New-Item -ItemType Directory -Force -Path $devDir | Out-Null
New-Item -Path 'HKCU:\Software\AMD\GAIA' -Force | Out-Null
Set-ItemProperty -Path 'HKCU:\Software\AMD\GAIA' -Name InstallDir -Value $devDir
try {
    # Keys alone, folder gone: a stale leftover, which must not block Setup.
    # Checked with /COMPONENTS=bogus so the run stops at the usage refusal
    # instead of installing anything.
    $rc = Invoke-Setup @('/S', '/COMPONENTS=bogus')
    if ($rc -ne 3) { throw "a stale developer-build key should not block Setup, got exit $rc" }
    Set-Content -Path "$devDir\gaia-tui.exe" -Value 'stub'
    $rc = Invoke-Setup @('/S', '/COMPONENTS=tui')
    if ($rc -ne 4) { throw "a developer build present should exit 4 (conflict), got $rc" }
    Assert-NothingInstalled 'with a developer build present'
} finally {
    Remove-Item -Path 'HKCU:\Software\AMD\GAIA' -Recurse -Force
    Remove-Item -Path $devDir -Recurse -Force
}

# An all-users desktop app cannot be updated per-user without a second copy.
$hklmUi = "HKLM:\Software\$uiGuid"
New-Item -Path $hklmUi -Force | Out-Null
Set-ItemProperty -Path $hklmUi -Name InstallLocation -Value 'C:\Program Files\GAIA'
try {
    $rc = Invoke-Setup @('/S', '/COMPONENTS=ui')
    if ($rc -ne 4) { throw "an all-users desktop app should exit 4 (conflict), got $rc" }
    Assert-NothingInstalled 'with an all-users desktop app present'
} finally {
    Remove-Item -Path $hklmUi -Recurse -Force
}

# ── 2. Terminal only ───────────────────────────────────────────────────────
$rc = Invoke-Setup @('/S', '/COMPONENTS=tui')
if ($rc -ne 0) { throw "terminal-only install exited $rc" }

if (Test-Path $uiDefault) {
    throw "a terminal-only install wrote to $uiDefault - the desktop app's folder"
}
if (Test-Path $uiKey) { throw "a terminal-only install installed the desktop app" }
foreach ($f in 'gaia-tui.exe', 'gaia-agent.exe', 'Uninstall.exe') {
    if (-not (Test-Path "$tuiDir\$f")) { throw "$f did not land in $tuiDir" }
}
# The registry PATH, not $env:PATH: this process inherited its environment
# before the install, so it cannot see the change.
$regPath = (Get-ItemProperty -Path 'HKCU:\Environment' -Name Path).Path
if ($regPath -notlike "*$tuiDir*") { throw "the terminal's folder was not added to the user PATH" }
if (-not (Test-Path $tuiArp)) { throw "no Installed-apps entry for the terminal" }
if (-not (Test-Path "$desktop\GAIA Terminal Hub.lnk")) { throw "no desktop shortcut for the terminal" }

# Terminal profile: the per-user fonts, their HKCU registration, and a Windows
# Terminal fragment that parses and names the bundled family.
$lock     = Get-Content "$repo\installer\tui\fonts\fonts.lock.json" -Raw | ConvertFrom-Json
$fontsDir = "$env:LOCALAPPDATA\Microsoft\Windows\Fonts"
$fontsReg = 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts'
$fragment = "$env:LOCALAPPDATA\Microsoft\Windows Terminal\Fragments\GAIA\gaia.json"
foreach ($f in $lock.fonts) {
    if (-not (Test-Path "$fontsDir\$($f.filename)")) { throw "$($f.filename) was not installed to $fontsDir" }
    $reg = (Get-ItemProperty -Path $fontsReg -Name "$($f.full_name) (TrueType)" -ErrorAction Stop)."$($f.full_name) (TrueType)"
    if ($reg -ne "$fontsDir\$($f.filename)") { throw "$($f.full_name) is registered as '$reg'" }
}
if (-not (Test-Path "$tuiDir\$($lock.license.filename)")) { throw "the font licence did not land in $tuiDir" }
if (-not (Test-Path $fragment)) { throw "no Windows Terminal fragment at $fragment" }
$bytes = [System.IO.File]::ReadAllBytes($fragment)
$text  = (New-Object System.Text.UTF8Encoding($false, $true)).GetString($bytes)
$wtProfile = ($text | ConvertFrom-Json).profiles[0]
if ($wtProfile.font.face -ne $lock.family) { throw "fragment names font '$($wtProfile.font.face)', setup ships '$($lock.family)'" }
if ($wtProfile.commandline -ne "`"$tuiDir\gaia-tui.exe`"") { throw "fragment commandline is '$($wtProfile.commandline)'" }
if ($text -match '__GAIA_INSTDIR__') { throw "fragment still carries the install-dir placeholder" }

# Shortcuts go through Windows Terminal only when it is installed -- detected
# the way the setup does: the app alias, then PATH.
$wtExe = (Test-Path "$env:LOCALAPPDATA\Microsoft\WindowsApps\wt.exe") -or [bool](Get-Command wt.exe -ErrorAction SilentlyContinue)
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut("$startMenu\GAIA\GAIA Terminal Hub.lnk")
Write-Host "terminal shortcut -> $($lnk.TargetPath) $($lnk.Arguments) (wt.exe on runner: $wtExe)"
if ($wtExe) {
    if ($lnk.TargetPath -notlike '*wt.exe' -or $lnk.Arguments -notlike "*$($wtProfile.guid)*") {
        throw "Windows Terminal is installed but the shortcut does not open the GAIA profile"
    }
} elseif ($lnk.TargetPath -ne "$tuiDir\gaia-tui.exe") {
    throw "Windows Terminal is absent but the shortcut targets '$($lnk.TargetPath)'"
}

$out = & "$tuiDir\gaia-tui.exe" version 2>&1 | Out-String
Write-Host "gaia-tui version -> $out"
if ($out -notmatch [regex]::Escape($Version)) { throw "gaia-tui did not report $Version" }

# ── 3. Re-run while the terminal is in use ─────────────────────────────────
# A running image is held with FILE_SHARE_READ|DELETE, so the setup cannot open
# it for write -- reproduced with an exclusive handle, which is deterministic on
# a headless runner. Plain /S also proves the default: it keeps what is
# installed (the terminal) rather than adding the desktop app.
$before = Get-FileHash "$tuiDir\gaia-tui.exe" -Algorithm SHA256
$held   = [System.IO.File]::Open("$tuiDir\gaia-tui.exe", 'Open', 'Read', 'Read')
try {
    $rc = Invoke-Setup @('/S')
    if ($rc -ne 2) { throw "re-install over an in-use gaia-tui should exit 2 (running), got $rc" }
} finally {
    $held.Dispose()
}
if ((Get-FileHash "$tuiDir\gaia-tui.exe" -Algorithm SHA256).Hash -ne $before.Hash) { throw "the refused install still rewrote gaia-tui.exe" }
if (Test-Path $uiKey) { throw "a plain /S re-run on a terminal-only machine added the desktop app" }

# ── 4. Add the desktop app ─────────────────────────────────────────────────
$rc = Invoke-Setup @('/S', '/COMPONENTS=ui')
if ($rc -ne 0) { throw "desktop app install exited $rc" }
$uiDir = Get-UiDir
Write-Host "desktop app installed to $uiDir"
if (-not (Test-Path "$uiDir\gaia-desktop.exe")) { throw "gaia-desktop.exe did not land in $uiDir" }
if (-not (Test-Path $uiArp)) { throw "no Installed-apps entry for the desktop app" }
if (-not (Test-Path "$desktop\GAIA.lnk")) { throw "no desktop shortcut for the desktop app" }
if (-not (Test-Path "$startMenu\GAIA.lnk")) { throw "no Start menu shortcut for the desktop app" }
if (-not (Get-ItemProperty -Path $runKey -Name 'GAIA' -ErrorAction SilentlyContinue)) { throw "no autostart entry for the desktop app" }
if ((Get-FileHash "$tuiDir\gaia-tui.exe" -Algorithm SHA256).Hash -ne $before.Hash) { throw "adding the desktop app changed the terminal" }

# ── 5. Plain /S with both installed updates both, and leaves one of each ──
$rc = Invoke-Setup @('/S')
if ($rc -ne 0) { throw "re-running with both installed exited $rc" }
$entries = Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall' |
    ForEach-Object { Get-ItemProperty $_.PSPath } |
    Where-Object { $_.DisplayName -in @('GAIA', 'GAIA Terminal Hub') }
$names = ($entries | ForEach-Object DisplayName | Sort-Object) -join ', '
if ($names -ne 'GAIA, GAIA Terminal Hub') { throw "expected one entry per component, found: $names" }

# ── 6. Refuse while the desktop app is in use ──────────────────────────────
$uiBefore = Get-FileHash "$uiDir\gaia-desktop.exe" -Algorithm SHA256
$held = [System.IO.File]::Open("$uiDir\gaia-desktop.exe", 'Open', 'Read', 'Read')
try {
    $rc = Invoke-Setup @('/S', '/COMPONENTS=ui')
    if ($rc -ne 2) { throw "re-install over an in-use gaia-desktop should exit 2 (running), got $rc" }
} finally {
    $held.Dispose()
}
if ((Get-FileHash "$uiDir\gaia-desktop.exe" -Algorithm SHA256).Hash -ne $uiBefore.Hash) { throw "the refused install still rewrote gaia-desktop.exe" }

# ── 7. Uninstall each component on its own ─────────────────────────────────
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.gaia" | Out-Null
Set-Content -Path "$env:USERPROFILE\.gaia\smoke-marker" -Value 'keep me'

$u = Start-Process -FilePath "$tuiDir\Uninstall.exe" -ArgumentList '/S' -PassThru -Wait
if ($u.ExitCode -ne 0) { throw "the terminal's uninstaller exited $($u.ExitCode)" }
Wait-Gone $tuiDir
$regPath = (Get-ItemProperty -Path 'HKCU:\Environment' -Name Path).Path
if ($regPath -like "*Programs\GAIA Terminal Hub*") { throw "the terminal's folder was left on the user PATH" }
if (Test-Path $tuiArp) { throw "the terminal's Installed-apps entry survived its uninstall" }
if (Test-Path "$desktop\GAIA Terminal Hub.lnk") { throw "the terminal's desktop shortcut survived its uninstall" }
foreach ($f in $lock.fonts) {
    if (Test-Path "$fontsDir\$($f.filename)") { throw "$($f.filename) survived the uninstall" }
    if ((Get-ItemProperty -Path $fontsReg -ErrorAction SilentlyContinue).PSObject.Properties.Name -contains "$($f.full_name) (TrueType)") {
        throw "$($f.full_name) is still registered after the uninstall"
    }
}
if (Test-Path $fragment) { throw "the Windows Terminal fragment survived the uninstall" }
if (-not (Test-Path "$uiDir\gaia-desktop.exe")) { throw "uninstalling the terminal removed the desktop app" }

$quiet = (Get-ItemProperty -Path $uiArp -Name QuietUninstallString).QuietUninstallString
Write-Host "desktop app quiet uninstall: $quiet"
$u = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $quiet -PassThru -Wait
if ($u.ExitCode -ne 0) { throw "the desktop app's uninstaller exited $($u.ExitCode)" }
Wait-Gone $uiArp
Wait-Gone "$uiDir\gaia-desktop.exe"
if (Test-Path "$desktop\GAIA.lnk") { throw "the desktop app's shortcut survived its uninstall" }

if (-not (Test-Path "$env:USERPROFILE\.gaia\smoke-marker")) {
    throw "an uninstaller deleted ~/.gaia - it must keep user data unless asked"
}

# ── 8. Desktop app only, on a machine with nothing installed ───────────────
$rc = Invoke-Setup @('/S', '/COMPONENTS=ui')
if ($rc -ne 0) { throw "desktop-app-only install exited $rc" }
if (-not (Test-Path "$(Get-UiDir)\gaia-desktop.exe")) { throw "desktop-app-only install did not install the app" }
if (Test-Path $tuiDir) { throw "a desktop-app-only install created $tuiDir" }
if (Test-Path $tuiArp) { throw "a desktop-app-only install registered the terminal" }
$quiet = (Get-ItemProperty -Path $uiArp -Name QuietUninstallString).QuietUninstallString
$u = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $quiet -PassThru -Wait
if ($u.ExitCode -ne 0) { throw "the desktop app's uninstaller exited $($u.ExitCode)" }
Wait-Gone $uiArp

# ── 9. Plain /S on a machine with nothing installed: the IT default ────────
$rc = Invoke-Setup @('/S')
if ($rc -ne 0) { throw "plain /S on a clean machine exited $rc" }
if (-not (Test-Path "$tuiDir\gaia-tui.exe")) { throw "plain /S on a clean machine did not install the terminal" }
if (-not (Test-Path "$(Get-UiDir)\gaia-desktop.exe")) { throw "plain /S on a clean machine did not install the desktop app" }
$u = Start-Process -FilePath "$tuiDir\Uninstall.exe" -ArgumentList '/S' -PassThru -Wait
if ($u.ExitCode -ne 0) { throw "the terminal's uninstaller exited $($u.ExitCode)" }
Wait-Gone $tuiDir
$quiet = (Get-ItemProperty -Path $uiArp -Name QuietUninstallString).QuietUninstallString
$u = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $quiet -PassThru -Wait
if ($u.ExitCode -ne 0) { throw "the desktop app's uninstaller exited $($u.ExitCode)" }
Wait-Gone $uiArp

Write-Host "OK -refusals changed nothing; terminal, desktop app and both installed, upgraded and uninstalled independently; user data intact"
