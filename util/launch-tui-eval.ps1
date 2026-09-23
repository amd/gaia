# Launch the flagship TUI for local eval driving (control mode, Haiku, no Lemonade).
# One TUI at a time — kill existing gaia-drive.exe before running this.
# Repo root derived from this script's location (util/ -> repo root), so the
# launcher works on any checkout rather than one developer's machine.
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$home_eval = "$root\eval\results\gaia-local-tui-validation"

# Agent-side auth: the Go TUI checks ANTHROPIC_API_KEY before spawning with --use-claude.
# Prefer the Max-subscription OAuth token (rides the plan, no per-call billing)
# over the .env pay-as-you-go key. Read fresh so an expiry refresh is picked up.
$cred = "$env:USERPROFILE\.claude\.credentials.json"
if (Test-Path $cred) {
  $tok = (Get-Content $cred -Raw | ConvertFrom-Json).claudeAiOauth.accessToken
  if ($tok) { $env:ANTHROPIC_API_KEY = $tok }
}
if (-not $env:ANTHROPIC_API_KEY) {
  $envline = Select-String -Path "$root\.env" -Pattern '^ANTHROPIC_API_KEY=' | Select-Object -First 1
  if ($envline) { $env:ANTHROPIC_API_KEY = $envline.Line.Split('=', 2)[1].Trim() }
}

$env:PYTHONPATH = "$root\src;$root\hub\agents\chat\python;$root\hub\agents\gaia\python"
$env:GAIA_TUI_HOME = "$home_eval\tui-home"
$env:GAIA_AGENT_LOG = "$home_eval\agent-session.log"
$env:GAIA_MEMORY_DISABLED = '1'   # embedder = Lemonade = forbidden on this box
$env:GAIA_DYNAMIC_TOOLS = '0'     # tool selection also embeds; full registry rides along
$env:PYTHONIOENCODING = 'utf-8'
# fake gh first so the github-triage skill's `gh` resolves to the fixture shim;
# venv Scripts next so the TUI spawns this branch's gaia-agent.
$env:PATH = "$root\tests\fixtures\gaia\fake_gh;$root\.venv\Scripts;" + $env:PATH
$env:GAIA_HUB_URL = 'http://127.0.0.1:8765/fixture_hub'
$env:GAIA_WEB_ALLOWED_HOSTS = '127.0.0.1'   # let the fetch tool reach the loopback fixture server

# Per-scenario skill isolation: a prior install (e.g. rss-digest) persists to
# ~/.gaia/skills on disk, so scrub the installable fixtures each launch and keep
# only the pre-seeded github-triage (restore it if a run removed it).
foreach ($s in @('rss-digest','experimental-notes','data-explore')) {
  $d = Join-Path "$env:USERPROFILE\.gaia\skills" $s
  if (Test-Path $d) { Remove-Item -Recurse -Force $d }
}
# Fresh scratchpad per scenario: the SQL scratchpad is ~/.gaia/scratchpad.db on
# disk and survives an agent restart, so re-loading a CSV across scenarios stacks
# duplicate rows and inflates every SUM. Remove it so each data scenario starts empty.
$sp = "$env:USERPROFILE\.gaia\scratchpad.db"
if (Test-Path $sp) { Remove-Item -Force $sp }
$ght = "$env:USERPROFILE\.gaia\skills\github-triage"
if (-not (Test-Path $ght)) { Copy-Item -Recurse "$root\hub\skills\github-triage" $ght }
New-Item -ItemType Directory -Force "$env:GAIA_TUI_HOME" | Out-Null
# --bypass-permissions mirrors CI's GAIA_AUTO_APPROVE_TOOLS=1: the eval lane
# asserts gated-action OUTCOMES; modal semantics are owned by the T1 gate tests
# and the tui-tagged manual checks. REFUSE-tier commands stay refused — the
# gate lives in the tool, not the confirmation layer.
$inner = "cd /d `"$root`" && tui\bin\gaia-drive.exe run gaia --use-claude --claude-model claude-haiku-4-5 --control-port 8817 --dev --bypass-permissions"
Start-Process -FilePath 'cmd.exe' -ArgumentList '/k', $inner -WindowStyle Normal
