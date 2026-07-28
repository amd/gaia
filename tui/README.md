# GAIA Terminal Hub

Browse, install, and chat with GAIA agents without leaving the terminal.

The terminal hub is a single static Go binary. It talks to the local GAIA
daemon over HTTP/SSE, so agents run on your machine — nothing is sent to a
hosted service.

## Install

Download the binary for your platform from the Agent Hub and put it on your
`PATH`. Building from source:

```bash
cd tui && make build     # -> tui/bin/gaia
```

## Use

```bash
gaia                                    # open the hub
gaia list                               # what the hub offers, and what you have
gaia list --installed                   # local only, no network call
gaia install email --trust              # download and install an agent
gaia run email                          # interactive chat
gaia run email --query "triage my inbox"   # one-shot: answer on stdout
gaia status                             # background service + installed agents
gaia version                            # version, commit, build date
```

The binary accepts a leading `tui` word and drops it, so `gaia tui list` and
`gaia list` are the same command. That keeps the docs' `gaia tui …` form working
when the Python CLI dispatches to this binary.

## Exit codes

`gaia run --query` is scriptable:

- `0` — the agent answered and nothing reported a failure
- `1` — an error, or a tool failed and nothing recovered it (even if the agent
  still wrote an answer, so `gaia run … && next-step` does not fire over work
  that never happened)
- `3` — a confirmation gate held back a destructive action this run had no way
  to approve: nothing broke, and nothing was done

## Preflight

Before an agent starts, the hub checks the things that actually stop it from
working — the daemon is up, the agent's sidecar is running, the local model
server is reachable at a compatible version, the model is downloaded, plus any
per-agent checks. Each row shows what failed and the command that fixes it.

A check that cannot be determined renders `[?]` rather than a checkmark, and
never counts as ready.

## Documentation

Full command reference: <https://amd-gaia.ai/docs/reference/cli>
