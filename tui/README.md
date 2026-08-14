# GAIA Terminal Hub

Browse, install, and chat with GAIA agents without leaving the terminal.

GAIA agents do real work for you — triage your inbox, answer questions about
your files, write code. The terminal hub is how you find them, install them,
and talk to them. Everything runs on your own machine: no account, and nothing
you type is sent to a hosted service.

> **Heads up — this is early.** There is no download yet. Getting the hub means
> building it yourself, so you will need `git`, [Go](https://go.dev/dl/), and
> [uv](https://docs.astral.sh/uv/) installed, and about half an hour. If that is
> not what you were hoping for, come back when it ships.

## Getting set up

Three one-time steps, in this order.

**1. Get GAIA itself.** The hub needs changes that are not in a published
release yet, so install from source rather than from PyPI:

```bash
git clone https://github.com/amd/gaia.git
cd gaia
uv venv && uv pip install -e .
```

That gives you `gaia`, GAIA's main command.

**2. Get a local AI running.** Because agents run on your machine, something on
your machine has to do the thinking. That is a local model server, and GAIA
sets it up for you:

```bash
gaia init
```

It downloads a model — several GB, so give it a while. If you skip this, the
hub will stop you before an agent starts and tell you the same thing.

**3. Build the hub.** From the same folder:

```bash
cd tui && make build                # -> tui/bin/gaia
cp bin/gaia ~/.local/bin/gaia-tui   # somewhere on your PATH
```

That gives you `gaia-tui`, the command in this README. It is a separate name
from `gaia` because the two have different subcommands and would otherwise
collide on your `PATH`.

## Your first run

```bash
gaia-tui
```

That opens the hub — a list of agents, what each one does, and whether you have
it. Pick one and it walks you through installing it, then drops you into a chat
with it.

Before an agent starts, the hub checks the few things that would otherwise make
it fail — is the model server running, is the model downloaded, does the agent
have what it needs. Anything not ready is shown with the exact command that
fixes it, so a failed check is a to-do list rather than a dead end.

Colours adapt to your terminal automatically. Some terminals never answer that
query (SSH, tmux, a CI log) — if the hub comes out hard to read, force it:

```bash
GAIA_TUI_THEME=light gaia-tui    # or dark; unset or "auto" = detect
```

## Commands worth knowing

```bash
gaia-tui                                     # open the hub
gaia-tui list                                # what the hub offers, and what you have
gaia-tui list --installed                    # local only, works offline
gaia-tui install email --trust               # install an agent
gaia-tui run email                           # chat with it
gaia-tui run email --query "triage my inbox" # one-shot: answer on stdout
gaia-tui uninstall email                     # remove it
gaia-tui status                              # is everything running, and what do I have
gaia-tui version
```

`--trust` on `install` is not a formality: an agent GAIA has not verified runs
third-party code on your machine, so the hub refuses until you say so.

Full command reference: <https://amd-gaia.ai/docs/reference/cli>

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
gaia-tui run gaia --query "..." --use-claude --claude-model claude-opus-4-8
```

**This sends your conversation off the machine**, which is the one thing GAIA
otherwise never does — so the chat header carries a `claude` chip for as long as
the mode is on, and the launch says so in the transcript. It needs
`ANTHROPIC_API_KEY` set, and it is a debugging tool, not a way to use GAIA.

Only the chat model moves. Retrieval over your documents still embeds locally
through Lemonade, so document questions need Lemonade running either way.

Paths that cannot honour the flag say so instead of quietly ignoring it: the
daemon transport refuses it, `--claude-model` without `--use-claude` refuses,
and `chat --subprocess` tells you to put the flag in the command line you own.

## Running against a local clone

Three independent layers, and only one of them has a flag.

**The hub binary** — just rebuild it: `cd tui && go build -o bin/gaia ./cmd/gaia`.

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

**It shows up in the list.** The hub reads `~/.gaia/agents/<id>/.installed`
sentinel files and adds an id it has never seen rather than ignoring it, with
sparse metadata — a sentinel only proves id and version:

```bash
mkdir -p ~/.gaia/agents/myagent
echo '{"id":"myagent","version":"0.1.0"}' > ~/.gaia/agents/myagent/.installed
gaia-tui list --installed     # myagent  0.1.0  installed
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

A leading `tui` word is accepted and dropped — `gaia-tui tui list` and `gaia-tui
list` are the same command — so the `gaia tui …` form used elsewhere in the docs
keeps working.

## Contributing: colours

New colours are added to `internal/ui/theme` and addressed by role
(`theme.Text`, `theme.Danger`, `theme.Accent`…) — never a raw
`lipgloss.Color("NNN")` in a screen file. Keep package-level colour vars as
`lipgloss.Style` values and call `.Render()` where the string is actually
used; a `.Render()` called at package-init time bakes in a string before
`theme.Init()` has picked light or dark, so it never changes again.
