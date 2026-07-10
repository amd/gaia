# Weekly Claude deep-audit → triage issue + human-gated PRs

Design record for `.github/workflows/claude-weekly-audit.yml`.

## Why this matters

Every Claude job in this repo is **reactive** — it fires on a PR, a comment, or an
`@claude` mention (`claude.yml`). Nothing looks at the repo *proactively*, so debt that
ships inside a green PR — a feature with no doc page, a code path with no test, a silent
`except Exception: pass`, a `cli.mdx` that drifted from the real flags — sits unnoticed
until a user hits it. This workflow adds the missing proactive lens: a scheduled deep
review that files **one ranked triage issue** a maintainer skims and promotes.

**Human-gated, not an auto-merge bot.** The workflow produces *findings*, never commits.

## Modes

- **normal** (weekly) — reviews the last N days of merged work + the subsystems it touched.
- **deep** (monthly) — whole-codebase latent-debt sweep. Auto-selected on the first
  Monday of each month (day-of-month ≤ 7, since cron only fires Mondays); also selectable
  via `workflow_dispatch`. Deep runs still file issues — that is the point of the sweep.

## Dimensions (one read-only Claude job each, in parallel)

| Dimension | Looks for |
|-----------|-----------|
| `security` | injection, unsafe subprocess/eval, unsafe deserialization, secret handling, path traversal, fork-PR surface — **never posts detail publicly** (see below) |
| `correctness` | real logic bugs **and** every CLAUDE.md "Fail Loudly" violation (`except Exception: pass`, try/except returning a placeholder, silent degradation). **This dimension owns the silent-fallback check.** |
| `docs` | new feature with no `docs/` page or `docs.json` entry; `cli.mdx` drift; `amd-gaia.ai` links missing `/docs/`; hub-agent README/SPEC/SKILL/CHANGELOG drift |
| `tests` | new/changed code paths with no coverage; assertions that prove invocation, not call validity (CLAUDE.md "#1655" boundary rule) |
| `features` | half-finished follow-ups, TODO-as-placeholder, gaps a recent feature implies |

> **Fix vs. the original handoff:** the first draft had four dimensions
> (security/tests/docs/features) and listed the silent-fallback check in the scope
> narrative but assigned it to **no** dimension — it would have fallen through. This
> design adds a fifth **`correctness`** dimension and gives it that check explicitly, so
> the workflow covers code *correctness* (bugs) as well as code *debt* (tests, features).

## Synthesis → one triage issue + child issues

A synthesis job collects the five structured outputs, dedupes against already-open
`weekly-audit` issues, ranks by severity (🔴/🟡/🟢), and files:

- **Parent triage issue** (`weekly-audit`): a ranked checklist grouped by dimension, each
  line linking its child issue.
- **Per-finding child issues**, opened **without** any auto-fix label. A maintainer
  promotes one by applying **`bug`**; the existing `auto-fix` job (`claude.yml`) opens the
  PR. No new PR-creation code — humans gate every code change.

## Invariants (see the `weekly-audit-patterns` skill)

- **Dedup key** = `<dimension>:<path>:<symbol-or-section>` — a function/class name or doc
  heading, **never a line number** (line numbers move and re-file the finding every week).
  Embedded as `<!-- audit-key: KEY -->` in each child body; synthesis skips any key that
  already has an open child.
- **Security stays private**: full detail to the job run log only; a redacted stub in
  `findings-security.json`; the public issue shows a count + run-log pointer + `@kovtcharov-amd`.
- **Skip-if-empty**: normal mode exits before any Claude call on a no-change week.
- **Read-only**: `--allowedTools Read,Grep,Glob,Bash`; never install or run repo code.
- **Model** `claude-fable-5` via the top-level `AUDIT_MODEL` env (one place to change).

## Non-goals

- ❌ Auto-merging anything, or opening PRs directly (promotion via `bug` → `auto-fix`).
- ❌ Running `gaia eval agent` / Lemonade-dependent checks (this is static analysis).
- ❌ Replacing per-PR review — it complements `claude.yml`, doesn't duplicate it.
