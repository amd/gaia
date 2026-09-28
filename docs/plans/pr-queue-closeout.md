# PR queue closeout — sequencing plan

State as of 2026-08-24, **after #2995 merged** (`fdd9f18`, 17:39 UTC). 48 PRs remain open.

Working document. Not wired into `docs/docs.json`; delete once the queue is drained.

---

## What changed today

1. **#2995 merged** — 286 files deleted, twelve per-task agents collapsed into flagship
   skills. Six PRs went `dirty` (conflicting) as a direct result: **#3047, #2957, #2601,
   #2550, #2872, #2870**.
2. **Fork CI unblocked** — 163 workflow runs approved across the 20 external PRs. They
   had been sitting at `action_required` since they were opened; the full suite is now
   executing for the first time on every one of them.

Two structural problems from the earlier audit are **still open**:

- **`main` requires nothing to merge.** `required_status_checks.contexts = []`,
  `required_approving_review_count = 0`. Every green check below is advisory. `strict:
  true` is set but inert, because it only gates on required checks and there are none.
- **Twelve maintainer PRs are one hidden stack**, not twelve independent changes:
  `#3026 ⊇ #3006 ⊇ #3005 ⊇ #3039`, plus `#3004` and `#3022`; near-contains `#3007`
  (34/35 files), `#3024` (16/17), `#3008` (9/10). `#3006`'s branch is a strict git
  ancestor of `#3026`'s.

---

## Sequence

### Wave 1 — decide the stack strategy, then merge it (biggest value, all green)

Nothing else in the queue is worth touching first: twelve PRs, ~28k lines, all `clean`,
all green, all conflict-free today. They will start conflicting with each other the
moment anything else lands.

**Pick one before merging anything:**

- **Option A (fast, 6 merges instead of 12):** merge #3026 as a unit → close #3006,
  #3005, #3004, #3039, #3022 as "landed in #3026" → rebase and merge the remainder,
  which shrink substantially.
- **Option B (correct):** re-cut as an explicit chain, each PR based on its predecessor
  rather than on `main`, so each diff shows only its own change. Costs ~a day of
  rebasing; buys reviewable units.

Option A means 8224 lines land on one approval. That is the same governance gap #2995
had. Worth naming out loud rather than pretending six merges reviewed twelve PRs.

Bottom-up merge order if you skip the roll-up: `3039 → 3004 → 3005 → 3006 → 3022 →
3026 → 3008 → 3007 → 3024 → 3009 → 3010 → 3023`.

| PR | Status | Ready? |
|---|---|---|
| 3026 | `clean`, 64 pass, 0 fail | ✅ superset — merging it lands 5 others |
| 3007 | `clean`, 60 pass | ✅ |
| 3009 | `clean`, 60 pass | ✅ |
| 3024 | `clean`, 59 pass | ✅ |
| 3010 | `clean`, 47 pass | ✅ |
| 3008 | `clean`, 45 pass | ✅ |
| 3006 | `clean`, 27 pass | ✅ (inside 3026) |
| 3005 | `clean`, 27 pass | ✅ (inside 3006) |
| 3004 | `clean`, 23 pass | ✅ (inside 3006) |
| 3022 | `clean`, 14 pass | ✅ (inside 3026) |
| 3039 | `clean`, 23 pass | ✅ (inside 3005) |
| 3023 | `unstable`, 48 pass, **1 fail** | ❌ `Example Agents Integration Tests (stx)` |

### Wave 2 — free wins, merge alongside Wave 1

| PR | Status | Ready? |
|---|---|---|
| 3053 | 18 pass, 0 fail — scheduled-audit CI fix | ✅ merge |
| 3028 | Lemonade 11.7.0, 6 pass | ✅ merge |
| 2929 | skill-lock drift, 34 pass, 0 fail | ✅ merge (no review yet) |
| 2978 | 59 pass, 0 fail, but GitHub reports `blocked` + `rebaseable: false` | ⚠️ open the merge box and find out why — no ruleset explains it |

**Close immediately, no analysis needed:**

| PR | Why |
|---|---|
| 2714 | Lemonade 11.5.1 — file set ⊂ #2861 ⊂ #3028 |
| 2861 | Lemonade 11.5.2 — superseded by #3028 |
| 2872 | electron bump in `hub/agents/emr/.../electron/` — **both files deleted by #2995**, now `dirty` |
| 2870 | electron bump in `src/gaia/apps/jira/webui/` — **both files deleted by #2995**, now `dirty` |
| 2868 | itomek draft, 1 file, 16 days idle |

### Wave 3 — the six PRs #2995 broke

These need a rebase before anything else can be said about them.

| PR | Status | Action |
|---|---|---|
| 3047 | `dirty` + 3 fails (`Test MCPAgent` ×2, `GAIA CLI Linux`) | rebase, then fix — **it gates #2980** |
| 2957 | `dirty`, docs only | trivial rebase |
| 2601 | `dirty`, 1 fail, **26 days old** | ask the author to rebase or close |
| 2550 | `dirty`, 2 fails, `CHANGES_REQUESTED`, **27 days old** | **close** — it adds You.com search to `hub/agents/browser`, which no longer exists |
| 2872 / 2870 | `dirty` | close (Wave 2) |

### Wave 4 — external contributors, now that CI actually runs

Full suites are executing for the first time. Re-read these once they settle.

| PR | Status now | Ready? |
|---|---|---|
| 2954 | 44 pass, 0 fail | ✅ likely merge |
| 3042 | 15 pass, 5 running | ⏳ wait |
| 3021 | 15 pass, 4 running | ⏳ wait |
| 3045 | 13 pass, 3 running | ⏳ wait |
| 3041 | 14 pass, 0 fail | ✅ likely merge |
| 3027 | 14 pass, 4 running | ⏳ wait |
| 3020 | 13 pass, 1 running | ⏳ wait |
| 3046 | 9 pass, 5 running | ⏳ wait |
| 3052 | 6 pass, 9 running | ⏳ wait |
| 3051 | 6 pass, 10 running | ⏳ wait |
| 3050 | 7 pass, **1 fail** (`pr-review / run`) | ⚠️ the review workflow is broken, not the PR |
| 2976 | 14 pass, **1 fail** (`GAIA CLI Linux`) | ❌ fix first |
| 2876 | 29 pass, **1 fail** (`validate`) | ❌ + needs a security read (third-party LLM provider) |
| 3014 | **0 checks — approval did not take** | ⚠️ re-approve; + security read (third-party LLM provider) |
| 3032 | 13 pass, `blocked`, `CHANGES_REQUESTED` | ❌ author action |
| 2980 | 39 pass, **2 fails** (`Unit Tests py3.11/3.12`) | ❌ blocked on #3047 |

### Wave 5 — the ones with real regressions

| PR | Status | Action |
|---|---|---|
| 3003 | **fails the Gemma baseline eval** + `GAIA CLI Linux` | per CLAUDE.md an eval regression is a merge blocker — fix the prompt/clamp and re-run, don't waive |
| 2930 | fails `Email Triage Eval` + Gemma baselines | same — and it's a PR that *changes* eval CI, so a failing eval is signal not noise |
| 2931 | **all four C++ jobs failing**, 11 days | fix or close |
| 2871 | 5 fails (example-app build + electron runtime) | re-run dependabot after the app tree settles post-#2995 |
| 2869 | 29 pass, 1 running | ⏳ wait |
| 3048 | 28 pass, 5 running | ⏳ wait |
| 3049 | 62 pass, 1 fail (`Publish SARIF`) | likely a matrix-expression bug in the skill-audit workflow |

### Wave 6 — governance (do not skip)

1. **Set required status checks on `main`.** At minimum `Lint`, `Unit Tests (py3.10)`,
   `Unit Tests (py3.12)`, `Security Tests`. Today a red PR merges as easily as a green one.
2. **Decide the fork-CI policy.** Flip to "Require approval for first-time contributors
   only", or keep the manual gate and put it on the review checklist. The state we just
   cleared — 221 runs parked indefinitely with nobody watching — is the worst of both.
   Note that a meaningful share of these workflows run on **self-hosted AMD hardware**
   (`[self-hosted, strix-halo]`, `[self-hosted, Windows, lemonade-eval]`,
   `[self-hosted, Windows, stx]`), so auto-approving forks means running untrusted
   contributor code on your own machines. That is the reason to choose deliberately.
3. **Fix `pr-review / run`** — failing on #3050 and previously on #3048/#3049 alike.
4. **Stop empty-body rubber-stamp approvals.** Most external PRs carried an `APPROVED`
   review from `itomek` with no body, given while zero tests had ever run on the code.

---

## Honest risks

- Wave 1 Option A trades review depth for throughput. Choose it knowingly.
- Wave 6 item 1 will turn several currently-mergeable PRs red on the day it lands. That
  is the point, but it will not feel like progress.
- Every "✅ ready" below rests on CI that is **not** required to pass. Until Wave 6 item 1
  ships, green is a courtesy, not a gate.
