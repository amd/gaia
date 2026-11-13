---
name: automations
description: Reminders, alerts, triggers, summaries and the heartbeat — work GAIA does on its own and reports later. "Remind me at 5pm", "alert me if main breaks", "tell me when a good weather window opens", "summarize my inbox every morning", "check in every 30 minutes", "what are you watching?", "pause that", "stop those updates". Use whenever the user wants something done later, repeatedly, or only when something changes, or wants to see or change what is set up.
license: MIT
version: 2.0.0
metadata:
  gaia:
    security_tier: community
    permissions:
      - shell:execute:gaia
    tools_required:
      - run_shell_command
    provenance:
      source: starter-pack
---

# Automations

GAIA can act without being asked again, through `gaia automation` run with
`run_shell_command`. Listing never asks; every change shows the user the exact
command and waits for them to approve it. Results reach the user through
Discord today, so if `add` says nothing delivers it yet, tell them to connect
Discord under Settings → Messaging in the GAIA app.

## Pick the kind by when GAIA should speak

| The user says | Kind | How |
|---|---|---|
| "remind me in 10 minutes / at 5pm / tomorrow 9am to …" | **reminder**, once | `--at` with `--say` (exact text, no AI) or `--prompt` (something to look up then) |
| "summarize … every morning at 8 / every 3 hours / every weekday" | **summary**, always speaks | `--every` with `--prompt`; `--at 8am` sets the time of day, `--days mon-fri` the days |
| "alert me if CI breaks", "tell me when PR 12 is approved" — a GitHub fact | **trigger**: a cheap check, speaks only when its output changes | `--every` with `--check` and `--prompt` |
| "let me know when a good weather window opens", "if anything urgent lands in my inbox" — needs judgment | **alert**: GAIA checks and stays silent unless something needs the user | `--every` with `--alert` and `--prompt` |
| "check in on things every 30 minutes", "keep an eye on my day" | **heartbeat**: an alert named `Heartbeat` whose prompt is their checklist | `--every 30m --alert --quiet 22:00-07:00` |
| "tell me right away when someone opens a PR or issue or comments on owner/repo" | **watch**: a plain GitHub poller, instant and never repeating | `gaia discord watch --repo owner/repo --mine <their accounts>` |

Never answer "alert me if …" with a summary: a summary speaks every time, even
when nothing happened. Prefer a trigger when one GitHub read shows the state;
use an alert when it takes reading the web, mail or files and judging.

## Commands

| Do this | Command |
|---|---|
| List everything | `gaia automation list` |
| One-time reminder | `gaia automation add --name "Stretch" --at 10m --say "Time to stretch"` |
| Summary | `gaia automation add --name "Morning inbox" --every 1d --at 8am --prompt "..."` |
| Trigger | `gaia automation add --name "main CI" --every 10m --check "gaia automation ci-status --repo amd/gaia --branch main" --prompt "..."` |
| Alert | `gaia automation add --name "Rainier window" --every 6h --alert --prompt "..."` |
| Change one | `gaia automation edit "main CI" --every 30m` (also `--prompt`, `--say`, `--at`, `--check`, `--quiet`, `--days`, `--rename`) |
| Pause / resume / run now | `gaia automation pause "Heartbeat"` (or `resume`, `run`) |
| Remove one / all | `gaia automation remove "Stretch"` / `gaia automation remove --all` |
| Instant GitHub activity alerts | `gaia discord watch --repo amd/gaia --mine kovtcharov,kovtcharov-amd` (again with a new `--mine` to change the accounts) |
| Stop those | `gaia discord unwatch --repo amd/gaia` |

Run each as its own `run_shell_command` call, with double quotes only. Refer to
an automation by its name (or the id from `list`). `list` shows GitHub watches
too, each with the command that changes or removes it. A `waiting:` row is not
an automation: it is a message that already ran and reaches Discord when the
bridge is back. Removing its automation drops it; to drop only the message,
`gaia automation remove "waiting:<id>"`.

For a watch, pin down which GitHub accounts are the user's own: their activity
is not news to them. Use the ones they named; if none, ask once ("Which GitHub
accounts are yours?"). Never guess.

## Writing them well

- **Name it in a few words** the user would use: "main CI", "Rainier window",
  "Heartbeat". That is how they will ask to change it.
- **Write the prompt so it stands alone.** It runs later with no memory of this
  chat: name the repository, mailbox, place or accounts; what counts as worth
  telling; and to link everything it cites.
- **Alerts:** say exactly what qualifies ("both weekend days: precipitation
  under 1 mm, summit wind under 25 mph"), and how far ahead to look. GAIA is
  told to reply with nothing when nothing qualifies, and not to repeat news it
  already sent.
- **Triggers:** the check is one GitHub read that prints only the state that
  matters — `gaia automation ci-status --repo OWNER/REPO --branch main`, or a
  gh read naming the repo, like `gh pr view 12 --repo OWNER/REPO --json
  reviewDecision --jq .reviewDecision`. Write flags and values apart (`-L 5`).
  Anything that could change something, pipes and `--paginate` are refused.
  Adding one runs the check once and prints what it shows: tell the user that.
- **Intervals:** what the user said; else `10m` for CI and reviews, `6h` for
  weather and slow news, `30m` for a heartbeat. The shortest is `5m`. Quiet
  hours (`--quiet 22:00-07:00`) hold a run until they end; use them for
  heartbeats and anything that would wake someone. `--days mon-fri` (or
  `weekends`, `mon,wed,fri`) skips other days — never ask the prompt to "reply
  with nothing on weekends".
- **Before adding, list what exists.** If something already covers part of the
  request, say so and offer to change it instead of adding a duplicate.

## After a change

- **Say what will happen, in plain words**, and when it next runs: "Done — I'll
  check every 6 hours and message you only when a weekend with good weather
  shows up." Show the exact command or prompt only if they ask.
- **To change or stop something,** list first, then edit or remove by name.
  "Stop the updates" with more than one candidate means asking which.
- **When asked what is set up,** list and answer in a short bullet list: name,
  kind, how often, next run, paused or not.
