# GAIA Terminal UI

Chat with GAIA in your terminal — no browser, no account, nothing you type
leaves your machine.

`gaia-tui` boots straight into the flagship `gaia` agent: conversation,
documents, data, web research, memory, skills. There is nothing to browse and
nothing to pick — one binary, one agent, unless you ask for another by id
(see [Running another agent](#running-another-agent)).

## Install

One command installs the `gaia` CLI, the local model runtime, and the terminal
UI itself:

```bash
# macOS and Linux
curl -fsSL https://amd-gaia.ai/install.sh | sh
```

```powershell
# Windows
irm https://amd-gaia.ai/install.ps1 | iex
```

Then set up the local model — several GB, so give it a while — and launch:

```bash
gaia init
gaia-tui
```

`gaia init` installs Lemonade Server, the runtime that does the thinking on your
machine. Skip it and the readiness gate stops you before the agent starts and
tells you the same thing.

### Or download the binary directly

The installer fetches these and verifies their SHA-256; you can also take one
yourself. Rename it to `gaia-tui` and put it on your `PATH`:

| Platform | Download |
|---|---|
| Windows x64 | [`gaia-win-x64.exe`](https://hub.amd-gaia.ai/agents/terminal-hub/0.23.0/gaia-win-x64.exe) |
| Windows ARM64 | [`gaia-win-arm64.exe`](https://hub.amd-gaia.ai/agents/terminal-hub/0.23.0/gaia-win-arm64.exe) |
| macOS Apple Silicon | [`gaia-darwin-arm64`](https://hub.amd-gaia.ai/agents/terminal-hub/0.23.0/gaia-darwin-arm64) |
| macOS Intel | [`gaia-darwin-x64`](https://hub.amd-gaia.ai/agents/terminal-hub/0.23.0/gaia-darwin-x64) |
| Linux x64 | [`gaia-linux-x64`](https://hub.amd-gaia.ai/agents/terminal-hub/0.23.0/gaia-linux-x64) |
| Linux ARM64 | [`gaia-linux-arm64`](https://hub.amd-gaia.ai/agents/terminal-hub/0.23.0/gaia-linux-arm64) |

`https://hub.amd-gaia.ai/agents/terminal-hub/manifest.json` lists what is
published, with the SHA-256 of each build. A direct download still needs the
`gaia` CLI on your `PATH` — first-time setup (`gaia init`) and the `email`
agent's daemon both go through it — so the one-line installer above is the
shorter route.

The binary is installed as `gaia-tui`, never as `gaia`: the two have different
subcommands and would collide on your `PATH`.

### Building from source

For working on the TUI itself. Needs `git`, [Go](https://go.dev/dl/), and
[uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/amd/gaia.git
cd gaia && uv venv && uv pip install -e .
cd tui && make build                # -> tui/bin/gaia-tui
cp bin/gaia-tui ~/.local/bin/       # somewhere on your PATH
```

## Your first run

```bash
gaia-tui
```

That opens on a splash frame (the GAIA mascot), then a readiness gate, then
chat. The gate checks the few things that would otherwise make the agent fail —
is `gaia-agent` on this machine, is the local model server running, are the
models downloaded. Anything not ready is shown with the exact command that
fixes it, and `f` runs the fix that can be automated (downloading the models)
without leaving the terminal.

If `gaia-agent` itself is missing, the gate stops there and points you at the
installer — there is no in-TUI download for the agent binary.

Colours adapt to your terminal automatically. Some terminals never answer that
query (SSH, tmux, a CI log) — if the screen comes out hard to read, force it:

```bash
GAIA_TUI_THEME=light gaia-tui    # or dark; unset or "auto" = detect
```

## Running another agent

`gaia-tui` ships one agent by default, but it can also drive `email` by id —
it keeps its own readiness gate (background service, sidecar, local model,
mailbox):

```bash
gaia-tui run email                             # chat with it
gaia-tui run email --query "triage my inbox"   # one-shot: answer on stdout
gaia-tui chat --agent email                    # same as `run email`
```

Installing and uninstalling a hub sidecar agent like `email` is the Python
CLI's job, not the terminal UI's:

```bash
gaia hub list                 # what the Agent Hub offers, and what you have
gaia hub install email --trust
gaia hub uninstall email
```

## Commands worth knowing

```bash
gaia-tui                    # launch straight into the flagship agent's chat
gaia-tui run <id>           # chat with an agent by id (e.g. email)
gaia-tui run <id> --query "…"  # one-shot: answer on stdout, exit 0/1/3
gaia-tui chat --agent <id>  # same idea, via the chat subcommand
gaia-tui status             # is the background service running, and what do I have
gaia-tui version
```

Full command reference: <https://amd-gaia.ai/docs/reference/cli>

## Running on gateway-hosted models

Press <kbd>g</kbd> in the hub to connect the AMD LLM gateway — larger on-prem
models and frontier models, running through the same Lemonade your local models
use. The screen walks you through the gateway URL, your token, and which models
to enable; the active one becomes the default for new chats.

Your token is handed to Lemonade, which keeps it in memory only. The TUI masks
the field and writes nothing but your model selection to `~/.gaia/gateway.json`.
Needs Lemonade 11.8.0+.

Details: <https://amd-gaia.ai/docs/guides/llm-gateway>

---

# Going deeper

For developers and power users. If you just want to use GAIA agents, you are
already done.

## Exit codes

`gaia-tui run --query` is built to be scripted:

- `0` — the agent answered and nothing reported a failure
- `1` — an error, or a tool failed and nothing recovered it. This holds even
  when the agent still wrote an answer, so `gaia-tui run … && next-step` never
  fires over work that did not actually happen.
- `3` — a confirmation gate held back a destructive action this run had no way
  to approve: nothing broke, and nothing was done

`--timeout` bounds a single `--query` turn (default 15m).

In the pre-run checks, a condition that cannot be determined renders `[?]`
rather than a checkmark and never counts as ready — unknown is never treated as
fine.

## Testing the harness against Claude

`--use-claude` runs an agent on Anthropic's Claude API instead of the local
model. It exists so that when something goes wrong you can tell *which* thing
went wrong: on a known-good model, a bad answer is the harness's fault, not the
model's.

```bash
gaia-tui run gaia --query "..." --use-claude
gaia-tui run gaia --use-claude --claude-model claude-haiku-4-5
```

**This sends your conversation off the machine**, which is the one thing GAIA
otherwise never does — so the chat header names the exact model in use
(`claude · haiku-4.5`) for as long as the mode is on, colored to mark it
remote, and the launch says so in the transcript. It needs `ANTHROPIC_API_KEY`
set (a repo-root `.env` works too).

`--claude-model` takes one of `claude-opus-5`, `claude-sonnet-5`,
`claude-haiku-4-5`, `claude-fable-5` — there is no date suffix. Anything else
is refused at the command line, with the accepted ids, rather than forwarded to
Anthropic to come back a 404 mid-turn.

**A Claude session does not start the local backend at all.** First-boot setup
is skipped, so `LemonadeServer.exe` is never launched and the first answer is
not held behind an install. The transcript says so, and says what it costs:
retrieval, memory and the code index still embed through Lemonade (Anthropic
has no embeddings API), so those need `gaia init --profile chat
--skip-chat-model`, or `/setup` in the composer, before they work.

Paths that cannot honour the flag say so instead of quietly ignoring it: the
daemon transport refuses it, `--claude-model` without `--use-claude` refuses,
and `chat --subprocess` tells you to put the flag in the command line you own.

**Switching models mid-session:** the gaia agent also takes `/model` in the
chat composer — `/model` alone lists every switchable id (the curated Claude
5 family, plus whatever Lemonade currently has downloaded), and `/model <id>`
swaps the live client without losing conversation history or loaded skills.
Typing the space in `/model ` turns the slash palette into a model picker, so
the Claude ids are pickable rather than remembered; local ids stay behind bare
`/model`, since only the agent knows what this machine has downloaded.

An unknown id, a missing credential, or an unreachable Lemonade Server all
refuse the switch with an actionable message and leave the session on
whichever model was already working. Backends never swap themselves: a local
switch with Lemonade down is refused with both ways forward (start the server,
or name a Claude id), never silently answered somewhere else.

## Running against a local clone

Three independent layers, and only one of them has a flag.

**The TUI binary** — just rebuild it: `cd tui && go build -o bin/gaia-tui ./cmd/gaia`.

**GAIA core / the daemon** — no flag exists. The daemon serves whichever
checkout launched it, so you point it at your clone by launching it from an
editable install (`uv pip install -e .`) or with `PYTHONPATH=<clone>/src`, then
re-anchoring it:

```bash
gaia daemon stop && gaia daemon start
gaia daemon status    # "api: v1.1" is repo source; "v1" is the released wheel
```

The footgun: a per-user daemon keeps serving the checkout that launched it no
matter which directory you run the CLI from. If your edits do not seem to take,
that is almost always why.

**An agent from source** — `--mode user` (the default) runs the published frozen
binary; `--mode dev` runs it from a checkout:

```bash
gaia daemon start-agent email --mode dev [--dev-src-dir <path>]
```

Dev mode resolves your shell's own checkout (`git rev-parse --show-toplevel`)
and compares it — never executes it — against the checkout the daemon is
anchored to; a mismatch is refused loudly, naming both checkouts and the fix.
`--dev-src-dir` is the explicit escape hatch, and wants the agent's package
directory (`<clone>/hub/agents/email/python`), not the repo root.

## An agent that only exists in your clone

**It shows up in `status`.** The catalog reads `~/.gaia/agents/<id>/.installed`
sentinel files and adds an id it has never seen rather than ignoring it, with
sparse metadata — a sentinel only proves id and version:

```bash
mkdir -p ~/.gaia/agents/myagent
echo '{"id":"myagent","version":"0.1.0"}' > ~/.gaia/agents/myagent/.installed
gaia-tui status     # lists myagent under "Installed in ~/.gaia/agents"
```

**It will not run through the daemon.** Sidecar specs are built into GAIA core
and there is deliberately no runtime registration route, so `gaia daemon
start-agent myagent` fails with `unknown agent 'myagent'; registered agents:
email`. `--mode dev` develops an agent core already knows about; it does not add
a new one. Getting a brand-new agent supervised end to end is not documented yet.

## A daemon of your own

The daemon keys off `$HOME/.gaia/host/instance.json`, so a throwaway `HOME`
gets you a private daemon — own port, own agents, own state — that cannot
collide with your normal setup or a colleague's session on the same box. Set it
per command; an exported `HOME` outlives the command that needed it and quietly
redirects everything else you run in that shell.

```bash
TMPHOME=$(mktemp -d)
HOME="$TMPHOME" gaia daemon start
HOME="$TMPHOME" gaia daemon stop
rm -rf "$TMPHOME"
```

## The `tui` prefix

A leading `tui` word is accepted and dropped — `gaia-tui tui status` and
`gaia-tui status` are the same command — so the `gaia tui …` form used
elsewhere in the docs keeps working.

## Contributing: colours

New colours are added to `internal/ui/theme` and addressed by role
(`theme.Text`, `theme.Danger`, `theme.Accent`…) — never a raw
`lipgloss.Color("NNN")` in a screen file. Keep package-level colour vars as
`lipgloss.Style` values and call `.Render()` where the string is actually
used; a `.Render()` called at package-init time bakes in a string before
`theme.Init()` has picked light or dark, so it never changes again.
