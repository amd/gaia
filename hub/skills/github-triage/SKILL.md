---
name: github-triage
description: Triage GitHub work with the gh CLI — your unread notification inbox, or one repository's issue backlog. Groups what arrived, judges what is urgent, drafts the reply. Use when asked to triage issues or notifications, check the GitHub inbox, see what needs attention or what you are blocking, review a backlog, or work out what to fix first.
license: MIT
version: 2.0.0
metadata:
  gaia:
    security_tier: community
    permissions:
      - shell:execute:gh
    tools_required:
      - run_shell_command
    provenance:
      source: starter-pack
---

# GitHub Triage

The bring-your-own-CLI example. This skill declares one permission —
`shell:execute:gh` — which grants the GitHub CLI to this skill's session on this
agent only, restricted to read-only subcommands. There is no connector, no server
to start, and no token to hand GAIA: `gh` owns its own auth.

Run every GitHub command through `run_shell_command`.

## Setup

Check before you conclude anything is missing. A command that failed is not
proof `gh` is absent:

```bash
gh --version     # installed?  ("gh version", no dashes, is refused)
gh auth status   # logged in?  prints the account and its scopes
```

- `gh --version` fails → not installed. Offer to install it: `winget install
  GitHub.cli` (Windows), `brew install gh` (macOS), else https://cli.github.com.
  That is not a `gh` command, so the user approves it per-call — ask, then run it.
- `gh auth status` fails → installed but not logged in. `gh auth login` is
  interactive and you cannot drive it; hand that one step to the user.
- Any **other** failure is a bug in the command you sent, not a missing `gh`.
  Quote the error and fix the command. Never diagnose it as "not installed".

## What the grant allows

Read-only. `gh issue list|view|status`, `gh pr list|view|diff|checks|status`,
`gh repo list|view`, `gh release list|view`, `gh run list|view`, `gh label list`,
`gh search issues|prs|repos|code|commits`, `gh auth status`, and `gh api` for GET
requests.

Those run without prompting: loading this skill is the consent, so a triage does
not stop for a modal on every read.

Everything else is refused by GAIA, including `gh issue create`, any `gh api`
call with `-X POST` or a `-f`/`--field` body, `gh auth token`, `gh alias`,
`gh extension`, and `gh config`. A refused command returns an error, not a silent
no-op. That is the enforcement behind *Draft, do not send* below — do not try to
work around it. Any *other* shell command still needs the user's per-call
approval, so keep to `gh`; piping its output elsewhere will stop and ask.

## Which inbox

"My inbox", "my notifications", "what needs my attention", "what am I blocking"
mean the **notification feed** — start at *Triage the inbox*. A named repository
means its **backlog** — start at *Procedure*. Never substitute one for the other,
and never answer either from memory: if you did not run `gh` this turn, you have
not triaged.

## Triage the inbox

```bash
gh api "notifications?all=false&per_page=50" --jq ".[]|[.reason,.repository.full_name,.subject.type,.updated_at[0:10],.subject.title]|@tsv"
```

Rank by `reason` — it answers *who is blocked on me?*

| reason | act |
|---|---|
| `review_requested`, `assign` | first — someone is waiting on you |
| `mention`, `team_mention` | next — you were asked directly |
| `ci_activity` | only if it failed |
| `subscribed`, `author`, `comment` | batch; usually skip |

Then group and judge with steps 3–5 below, and close with action items that each
name a number and a verb. An empty inbox is a finding — say so, never pad it.

## Procedure

1. **Pull the backlog.** Ask for the repository if the user did not name one.

   ```bash
   gh issue list --repo <owner>/<repo> --limit 30 --json number,title,labels,createdAt
   ```

   Narrow with `--label bug`, `--search`, or `--state`.

2. **Read the ones that matter.**

   ```bash
   gh issue view <number> --repo <owner>/<repo> --json title,body,comments
   ```

   Read before judging — a one-line title is not enough to rank severity.

3. **Group before judging.** Cluster issues that describe the same underlying
   problem. Report the cluster, not each issue — a backlog of 40 is usually 12
   real problems.

4. **Judge each cluster on two axes:**
   - **Severity** — data loss > silent wrong answer > crash > annoyance.
   - **Reach** — everyone, one platform, or one reporter.

   Rank by the pair. A silent wrong answer affecting everyone outranks a crash
   affecting one person.

5. **Say what is missing.** For anything you cannot reproduce from the issue
   text, list the specific facts needed — version, platform, exact command.
   "Please provide more information" is not triage.

6. **Draft, do not send.** Produce the comment or label change and show it to the
   user. Never write to a repository without explicit confirmation for that
   specific action.

## Rules

- Never close an issue on your own judgement.
- Do not paste tokens, private URLs, or user emails into a public comment.
- If `gh` reports the repository is not readable, say so — do not guess from the
  name.
- Report a refused command as a refusal. Never substitute an invented answer for
  one you could not fetch.
- Write one line that survives every shell: **double quotes only, no single
  quotes, no `\` continuation, no spaces inside a `--jq` expression.** Windows
  hands the raw string to `cmd.exe` (single quotes and `\` there fail with "The
  system cannot find the path specified" — a quoting bug, not a missing binary);
  macOS and Linux get a pre-split argv with no shell, so `|`, `&` and `>` are
  literal characters, never operators. A command shaped this way behaves the
  same on all three.

## Fork this

Same shape for any CLI in GAIA's read-only policy: add it to `BINARY_POLICIES`
(`gaia/skills/binaries.py`) and write a `SKILL.md` like this one.
