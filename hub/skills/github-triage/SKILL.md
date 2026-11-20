---
name: github-triage
description: Answer GitHub questions and triage GitHub work with the gh CLI — PRs, issues, commits, CI runs, releases, the notification inbox or a repo's backlog. Links every item; posts a reply only once you approve it. Use for any request about a GitHub repo, PR, issue, commit, CI run or release — what is open, merged, failing or changed — or to triage, check the inbox, see what needs attention or what you are blocking, comment on or label an issue, or decide what to fix first.
license: MIT
version: 2.4.1
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

Call `check_cli_setup("gh")` first. It is read-only, prompts nobody, and returns
the one thing worth acting on — a `state`. Never infer setup from a `gh` command
that failed, and never run `gh --version` to guess.

| `state` | Remedy |
|---|---|
| `ready` | None. Start the triage. |
| `missing` | `install_cli`. Linux has no automated install — give the user https://cli.github.com. |
| `unauthenticated` | `sign_in_cli`. |
| `insufficient_scopes` | `sign_in_cli` — signing in again re-requests `repo` and `read:org`. |
| `env_token` | **Never `sign_in_cli`.** The credential is a `GH_TOKEN`/`GITHUB_TOKEN` from the environment, and `gh` refuses to store its own while that is set. Relay the `detail` verbatim — it says whether the token is working, or expired and needing a new value in the variable — and move on. |

Pass `command` back exactly as `check_cli_setup` returned it — `install_command`
or `sign_in_command`. That string is what the user sees in the approval prompt,
and one that does not match is refused rather than run.

Both tools stop and ask, every time: the user sees the exact command and answers
before anything runs. There is no "always", and loading this skill pre-approves
neither. Signing in is a **handoff** — GAIA starts the flow and shows a one-time
code and a URL, then waits while the user enters the code in their browser. You
cannot type it for them.

Report a setup failure as a failure. Both tools name the manual command when they
give up; hand that to the user rather than claiming a step succeeded.

Any **other** `gh` failure is a bug in the command you sent, not a missing `gh`.
Quote the error and fix the command. Never diagnose it as "not installed".

## What the grant allows

Four tiers. Which one a command lands in is decided by GAIA, not by this skill.

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

**Refused — nothing can approve these.** They end, land or delete someone
else's work: `gh issue close`, `gh pr close`, `gh pr merge`, `gh label delete`,
`gh repo delete`, `gh release delete`, and `gh api -X DELETE`. GAIA never runs
them and shows no prompt, and no answer from the user changes that. When asked
for one, don't run it to find out. Say plainly that GAIA isn't permitted to do
it and that nothing changed, then give the user the exact command to run
themselves. Never offer it for approval, in that reply or any later one.

**Outside this skill's grant — approved one run at a time.** These are not part
of a triage: don't run them. If one is truly needed, GAIA shows the user its
exact text and a risk line, and they may approve that one run, never "always":
`gh auth token` and `gh auth status -t` (both print the credential), `gh alias`
(defines a shell command), `gh extension` (installs and runs code),
`gh config`, `gh codespace`, and any other API write (`gh api -X POST`,
`-f`/`--field`, `graphql`). The same goes for these flags on any subcommand: `--body-file`
(uploads a local file's contents), `--editor`, `--web` (opens a browser,
returns you nothing), and `--watch` (blocks until the run finishes). The
`repo`, `release`, `run`, `search`, and `auth` read commands accept only
reviewed flags from GAIA's policy table. The mixed read/write `issue`, `pr`,
and `label` commands use a denylist for flags; their supported read actions
remain limited to the actions listed above.

A blocked command returns an error, not a silent no-op, and nothing ran. Report
it as what it is. If the error says no approval can change it, it is a refusal:
the user runs it themselves, so don't present approving it as a path. Either
way, do not work around it.

Any *other* shell command still needs the user's per-call approval, so keep to
`gh`; piping its output elsewhere will stop and ask.

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
   gh issue list --repo <owner>/<repo> --limit 30 --json number,title,labels,createdAt,url
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

- Link everything you name. Every issue, PR, run or release in your answer is a
  Markdown link to its page, like
  `[#6779](https://github.com/amd/gaia/pull/6779)`, never a bare number. Ask
  `gh` for the `url` field (`--json ...,url`) so you have it.
- Never close an issue on your own judgement — GAIA refuses `gh issue close`
  outright for exactly this reason. Recommend the close; let a human do it.
- Do not paste tokens, private URLs, or user emails into a public comment. The
  approval prompt shows the user the command, so anything you put in a `--body`
  is something they are being asked to publish.
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

Same shape for any CLI in GAIA's command policy: add it to `BINARY_POLICIES`
(`gaia/skills/binaries.py`) and write a `SKILL.md` like this one.
