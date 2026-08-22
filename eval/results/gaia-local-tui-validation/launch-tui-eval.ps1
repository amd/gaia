# Launch the flagship TUI for local eval driving (control mode, Haiku, no Lemonade).
# One TUI at a time — kill existing gaia-drive.exe before running this.
$root = 'C:\Users\14255\Work\gaia'
$home_eval = "$root\eval\results\gaia-local-tui-validation"

# Agent-side auth: the Go TUI checks ANTHROPIC_API_KEY before spawning with --use-claude.
$envline = Select-String -Path "$root\.env" -Pattern '^ANTHROPIC_API_KEY=' | Select-Object -First 1
if ($envline) { $env:ANTHROPIC_API_KEY = $envline.Line.Split('=', 2)[1].Trim() }

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

New-Item -ItemType Directory -Force "$env:GAIA_TUI_HOME" | Out-Null
# --bypass-permissions mirrors CI's GAIA_AUTO_APPROVE_TOOLS=1: the eval lane
# asserts gated-action OUTCOMES; modal semantics are owned by the T1 gate tests
# and the tui-tagged manual checks. REFUSE-tier commands stay refused — the
# gate lives in the tool, not the confirmation layer.
$inner = "cd /d `"$root`" && tui\bin\gaia-drive.exe run gaia --use-claude --claude-model claude-haiku-4-5 --control-port 8817 --dev --bypass-permissions"
Start-Process -FilePath 'cmd.exe' -ArgumentList '/k', $inner -WindowStyle Normal
