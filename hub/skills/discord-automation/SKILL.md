---
name: discord-automation
description: Set up alerts, triggers and recurring summaries that GAIA sends to the user's Discord DM — "alert me if main breaks", "tell me when PR 12 is approved", "tell me right away when someone opens an issue on owner/repo", "summarize my PRs every few hours", "stop those updates". Use when the user asks to monitor, watch, keep an eye on, be alerted about, or get regular updates on something, and wants it in Discord.
license: MIT
version: 1.1.0
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

# Discord Automation

GAIA's Discord bridge can do three things on its own, without the user asking
again:

- **Watch a GitHub repository** and DM the user *the moment* someone other than
  them opens a PR or issue or leaves a comment. This is a plain poller, not a
  prompt: it never invents or repeats anything, and it skips the user's own
  accounts and bots.
- **Run a prompt on a timer** and DM the answer: a summary every few hours.
- **Run a trigger**: a cheap read-only *check* every few minutes, with no model
  involved, that wakes GAIA only when what the check prints changes — "alert me
  if main breaks", "tell me when PR 12 is approved". Quiet the rest of the time.

You set both up with the `gaia discord` CLI through `run_shell_command`. Every
command that changes something asks the user to approve it in Discord first,
so they always see exactly what you set up.

## Turn the request into watches and schedules

1. **Split what is urgent from what can wait.** "Alert me if", "let me know
   when", "if anything breaks" means a **trigger**. "Tell me right away when
   someone opens / comments" on a repo means a **watch**. "Every so often",
   "summaries", "a digest", "every morning" means a **schedule**. One request is
   often two of these. Never answer "alert me if X" with a plain schedule: it
   wakes the model every interval and reports even when nothing happened.
2. **Pin down the accounts that are the user's own.** Their own activity is not
   news to them, so the watch must not alert on it. If they named them, use
   those. If not, ask once: "Which GitHub accounts are yours?" Never guess.
3. **Pick an interval.** Use what they said. "Every once in a while" or "every
   few hours" means `3h`; "daily" or "every morning" means `1d`. The shortest
   allowed is `5m`.
4. **Write each scheduled prompt so it stands alone.** It runs hours later with
   no memory of this conversation. Name the repository, the accounts, the time
   window (the interval), what to report, and how short to be. For a GitHub
   summary, say to use the gh CLI and to link every PR and issue.

## Commands

Run each as its own `run_shell_command` call. Use double quotes only.

| Do this | Command |
|---|---|
| Alert on others' activity in a repo | `gaia discord watch --repo amd/gaia --mine kovtcharov,kovtcharov-amd` |
| Change which accounts count as the user's | the same command again with the new `--mine` |
| Stop watching | `gaia discord unwatch --repo amd/gaia` |
| List watches | `gaia discord watches` |
| Run a prompt on a timer | `gaia discord schedule-add --every 3h --prompt "..."` |
| Alert when something changes | `gaia discord schedule-add --every 10m --check "<read-only command>" --prompt "..."` |
| Check a branch's CI (for `--check`) | `gaia discord ci-status --repo amd/gaia --branch main` |
| List schedules | `gaia discord schedules` |
| Remove one schedule | `gaia discord schedule-remove --id <id from the list>` |
| Remove all schedules | `gaia discord schedule-remove --all` |

A good scheduled prompt for a GitHub summary:

```
gaia discord schedule-add --every 3h --prompt "Using the gh CLI, summarize activity in the last 3 hours on amd/gaia by kovtcharov and kovtcharov-amd: PRs opened or merged, PRs with failing checks, and review comments still unanswered. At most 8 bullets, each PR or issue as a Markdown link to its page. If nothing happened, say so in one line."
```

## Triggers: alert only when something changes

A trigger is a schedule with `--check`. Adding it runs the check once and prints
what it shows now; tell the user that, so they hear where things stand. After
that the bridge runs the check by itself every interval and runs the prompt,
with the check's old and new output, only when the output changes.

- **The check prints only the state that matters,** so it changes only when that
  state does. Never timestamps, run ids or URLs: those change on every run and
  would wake GAIA for nothing.
- **It is one GitHub read**: `gaia discord ci-status`, or a gh read that names
  the repository, such as `gh pr view 12 --repo OWNER/REPO --json
  reviewDecision --jq .reviewDecision` or `gh release list --repo OWNER/REPO
  --limit 1`. Write each flag and its value apart (`-L 5`, not `-L5`). No
  pipes, chains, redirects or `--paginate`; anything else is refused.
- **The prompt says what to do when it fires,** standing alone like a schedule's:
  find out what changed, link it, and keep it short; say so in one line when
  things went back to healthy.
- **Interval:** `10m` unless the user says otherwise; CI and reviews rarely move
  faster. The shortest allowed is `5m`.

Health of a branch — "report the health of main and alert me if a test breaks":

```
gaia discord schedule-add --every 10m --check "gaia discord ci-status --repo amd/gaia --branch main" --prompt "The CI status of amd/gaia main changed. If a workflow is failing, use the gh CLI to find its latest failed run on main, the failing job and test, and the commit that broke it; report each as a Markdown link (run, commit, PR if any) in at most 5 lines. If everything is passing again, say main is green again in one line."
```

The setup reply says where main stands now; after that the user hears only when
a workflow starts or stops failing.

## After setting up

- **Say what will happen, in plain words.** For example: "Done. I'll DM you
  right away when someone else opens a PR or issue or comments on amd/gaia, and
  every 3 hours I'll send a short summary of what kovtcharov and kovtcharov-amd
  did." The user saw each command in its Allow prompt; the summary is the plain
  version, and anything they ask about (ids, the exact prompt) you show in full.
- **To change or stop something,** list first, then remove by id. "Stop the
  updates" with more than one schedule means asking which one, or all of them.
- **If a command fails because Discord isn't connected,** say the updates go to
  Discord and that it is connected under Settings → Messaging in the GAIA app.
  Point them to that screen; it does the setup for them.
