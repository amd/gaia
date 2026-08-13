---
name: github-triage
description: Triage GitHub issues with the gh CLI — read the backlog, group duplicates, judge severity, and draft the reply. Use when the user asks to triage issues, review a backlog, or work out what to fix first in a repository.
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

```bash
gh auth login        # once, interactively
gh auth status       # confirm: prints the account and its scopes
```

If `gh` is not installed the skill refuses to load and says so — it never loads
and then guesses. Install it from https://cli.github.com.

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

## Fork this

Point it at your own repo's labels and severity ladder. The same shape works for
any CLI in GAIA's read-only command policy: declare `shell:execute:<binary>`,
document its login step, and write the procedure against real commands. Adding a
new CLI — GitLab's `glab`, say — is an entry in `BINARY_POLICIES`
(`gaia/skills/binaries.py`) plus a `SKILL.md` like this one.
