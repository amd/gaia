---
name: github-triage
description: Triage GitHub issues with the gh CLI — read the backlog, group duplicates, judge severity, and post the reply once the user approves it. Use when the user asks to triage issues, review a backlog, comment on or label an issue, or work out what to fix first in a repository.
license: MIT
version: 2.1.0
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
agent only, restricted to the commands in GAIA's policy table. There is no
connector, no server to start, and no token to hand GAIA: `gh` owns its own auth.

Run every GitHub command through `run_shell_command`.

## Setup

```bash
gh auth login        # once, interactively
gh auth status       # confirm: prints the account and its scopes
```

If `gh` is not installed the skill refuses to load and says so — it never loads
and then guesses. Install it from https://cli.github.com.

## What the grant allows

Three tiers. Which one a command lands in is decided by GAIA, not by this skill.

**Reads — run with no prompt.** `gh issue list|view|status`,
`gh pr list|view|diff|checks|status`, `gh repo list|view`,
`gh release list|view`, `gh run list|view`, `gh label list`,
`gh search issues|prs|repos|code|commits`, `gh auth status`, and `gh api` for GET
requests. Loading this skill is the consent, so a triage does not stop for a
modal on every read.

**Writes — the user approves each one.** `gh issue create`, `gh issue comment`,
`gh issue edit` (this is how labels and assignees change), `gh pr comment`,
`gh label create|edit`. GAIA shows the user the exact command and waits. They
answer yes, no, or "always for `gh issue comment`" — which lasts this session
and covers that verb only, not `gh` in general and not the shell.

Ask for one write at a time and let each be judged on its own. An "always"
answer covers the verb, not the repository or the flags, so a queue of similar
commands is how a user stops reading them.

Do not ask for permission in prose before running one; the prompt IS the ask.
Compose the command and run it. If the user says no, the tool returns a denial —
report it and move on. Never re-run a denied command hoping for a different
answer.

**Refused outright — no prompt, and approval is not available.** `gh auth token`
(prints the credential), `gh alias` (defines a shell command), `gh extension`
(installs and runs code), `gh config`, `gh codespace`, any `gh api` write
(`-X POST`, `-f`/`--field`, `graphql`), and the irreversible ones: `gh pr merge`,
`gh issue close`, `gh label delete`, `gh repo delete`. Also refused inside an
otherwise-approvable write: `--body-file` (uploads a local file's contents),
`--editor`, and `--web`.

A refused command returns an error, not a silent no-op. Report it as a refusal
and say what you would have run — do not work around it, and do not tell the
user that approving it is an option.

Any *other* shell command still needs the user's per-call approval, so keep to
`gh`; piping its output elsewhere will stop and ask.

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

6. **Post it, once the user approves.** Compose the comment or label change and
   run it. GAIA shows the user the exact command and will not run it until they
   say yes, so there is no need to paste a draft and ask them to do it by hand —
   that is the prompt's job. For a write GAIA refuses outright (closing an
   issue, merging a PR), say so plainly and hand the user the command.

## Rules

- Never close an issue on your own judgement — GAIA refuses `gh issue close`
  outright for exactly this reason. Recommend the close; let a human do it.
- Do not paste tokens, private URLs, or user emails into a public comment. The
  approval prompt shows the user the command, so anything you put in a `--body`
  is something they are being asked to publish.
- If `gh` reports the repository is not readable, say so — do not guess from the
  name.
- Report a refused command as a refusal. Never substitute an invented answer for
  one you could not fetch.

## Fork this

Point it at your own repo's labels and severity ladder. The same shape works for
any CLI in GAIA's command policy: declare `shell:execute:<binary>`,
document its login step, and write the procedure against real commands. Adding a
new CLI — GitLab's `glab`, say — is an entry in `BINARY_POLICIES`
(`gaia/skills/binaries.py`) plus a `SKILL.md` like this one.
