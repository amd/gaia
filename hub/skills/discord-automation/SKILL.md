---
name: discord-automation
description: Set up recurring summaries and instant alerts that GAIA sends to the user's Discord DM — "summarize my PRs every few hours", "tell me right away when someone opens an issue on owner/repo", "stop those updates". Use when the user asks to monitor, watch, keep an eye on, or get regular updates about something, especially a GitHub repository, and wants the results in Discord.
license: MIT
version: 1.0.0
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

GAIA's Discord bridge can do two things on its own, without the user asking
again:

- **Watch a GitHub repository** and DM the user *the moment* someone other than
  them opens a PR or issue or leaves a comment. This is a plain poller, not a
  prompt: it never invents or repeats anything, and it skips the user's own
  accounts and bots.
- **Run a prompt on a timer** and DM the answer: a summary every few hours.

You set both up with the `gaia discord` CLI through `run_shell_command`. Every
command that changes something asks the user to approve it in Discord first,
so they always see exactly what you set up.

## Turn the request into watches and schedules

1. **Split what is urgent from what can wait.** "Tell me right away", "as soon
   as", "immediately" means a watch. "Every so often", "summaries", "a digest",
   "every morning" means a schedule. One request is often both.
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
| List schedules | `gaia discord schedules` |
| Remove one schedule | `gaia discord schedule-remove --id <id from the list>` |
| Remove all schedules | `gaia discord schedule-remove --all` |

A good scheduled prompt for a GitHub summary:

```
gaia discord schedule-add --every 3h --prompt "Using the gh CLI, summarize activity in the last 3 hours on amd/gaia by kovtcharov and kovtcharov-amd: PRs opened or merged, PRs with failing checks, and review comments still unanswered. At most 8 bullets, each PR or issue as a Markdown link to its page. If nothing happened, say so in one line."
```

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
