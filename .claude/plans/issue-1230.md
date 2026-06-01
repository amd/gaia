---
type: plan
source-issue: 1230
repo: amd/gaia
title: "Generate + commit labelled email-triage corpus + gemma4 baseline"
created: 2026-06-01
status: in-progress
work_type: feature
complexity: medium
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 6
test_command: "python -m pytest tests/unit/email/test_corpus_integrity.py tests/integration/test_email_corpus_alignment.py -x"
build_command: "uv pip install -e \".[dev]\""
lint_command: "python util/lint.py --all"
branch: feat/issue-1230-email-corpus-gemma4-baseline
reflection_iterations: 0
agents_used:
  - general-purpose (code-explorer)
  - general-purpose (code-reviewer)
---

# Issue #1230 — Labelled email-triage corpus + gemma4 baseline

## Problem

Categorization accuracy is unmeasurable today. The only committed corpus is the
~10-message `_stub_inbox.mbox`, and `baseline_accuracy.json` records `0.70` as a
*stub* number against `Qwen3.5-35B-A3B-GGUF` — not the demo model and not a real
measurement. A deterministic 220-message generator
(`tests/fixtures/email/generate_mbox.py`) exists but its output has never been
committed, and its output does **not** line up with the test/runtime code that
consumes it.

## Findings from exploration (empirical, not assumed)

Running the generator into a temp dir and loading it through the real consumer
(`tests/fixtures/email/fake_gmail.py::mbox_message_to_gmail_payload`,
`FakeGmailBackend`) surfaced two **hard, integration-breaking** defects in the
generator output as-is:

1. **Ground-truth keying mismatch (Critical).** The generator keys
   `ground_truth.json` by the raw RFC `Message-ID` header
   (`<msg0001.512729@mail.acme-corp.example.com>`). But `FakeGmailBackend`
   (the only thing that loads the corpus — see
   `tests/integration/test_email_agent_triage.py`) keys every message by
   `sha256(Message-ID)[:16]` (`204df847518b333c`). Result measured: **0 of 220**
   ground-truth keys match the loaded message ids → the integration test's
   `results_by_id.get(msg_id)` returns `None` for every message and silently
   scores nothing. The committed corpus would be inert.

2. **Category-string mismatch (Critical).** The generator emits the low bucket
   as `"low_priority"` (underscore). The production heuristic constant is
   `CATEGORY_LOW_PRIORITY = "low priority"` (space) in
   `src/gaia/agents/email/tools/triage_heuristics.py`, the stub
   `ground_truth.json` uses `"low priority"`, and the integration test compares
   `result["category"] == gt["category"]` by exact string. Every low-priority
   message would score as a category miss.

Other observations:

- The `gaia eval agent` framework is scenario-YAML / Agent-UI-backend based
  (RAG-style) and has **no** email-categorization scenario type. The
  email-triage baseline mechanism is the integration test + `baseline_accuracy.json`,
  not `gaia eval agent`. `tests/fixtures/eval_baselines/` holds RAG scorecards
  and is unrelated. The schema doc (`_schema.md`) confirms the eval-runner YAML
  extension (F2) was descoped.
- Generated category distribution (after the fixes) sums to 220 across the 4
  taxonomy buckets. The literal `TARGET_COUNTS` constants in the generator are
  *seed inputs* (spam/ambiguous/malformed get folded into the 4 buckets via
  their `category` field), so the realized per-category counts differ from those
  constants. The realized split with the current seed is approximately
  urgent≈47, actionable≈56, informational≈80, low priority≈37.
- The generator already enforces: total == 220, mbox < 1 MB, RFC 2606 synthetic
  domains only (no live mailbox), deterministic seed, ≥30% HTML, calendar +
  forwarded + phishing coverage, persona repeat ranges. Synthetic-only (AC1) is
  satisfied by construction.

## Reconciliation decision (AC3)

**Keep 220 messages across the 4-category v0.20 taxonomy
(urgent / actionable / informational / low priority). Reject the 1000/5 figure.**

Rationale:

- The "1000 messages / 5 categories" figure has **no** in-repo plan backing it
  (searched `docs/plans/`, `.claude/plans/`, the email tests, and the fixture
  history). The authoritative taxonomy is `ALL_CATEGORIES` in
  `triage_heuristics.py` — exactly the 4 buckets the issue names. Adding a 5th
  category would require changing **production** classification code, which is
  out of scope for a corpus/baseline task and would break the existing triage
  contract.
- 220 is a 22× jump over the 10-message stub — enough to make per-category
  accuracy meaningful — while staying comfortably under the generator's 1 MB CI
  size guard. 1000 messages risks blowing that guard and bloats the repo for no
  measurement benefit at this stage.
- The generator, schema doc, integration test, and runtime constants are all
  already built around 220/4. Reconciling *up* to 1000/5 would mean rewriting
  all four; reconciling *to the code* (220/4) is the low-risk, internally
  consistent choice.

So the reconciliation is: **fix the generator so its output matches the code
that consumes it (220/4, `"low priority"` string, Gmail-id keys), commit that
output, and record the baseline against it.** No production code changes.

## Implementation

1. **TDD first.** Add failing tests:
   - `tests/unit/email/test_corpus_integrity.py` — generator produces exactly
     220 messages; every GT entry has a category in `ALL_CATEGORIES`; the 4
     realized counts sum to 220; GT schema is well-formed (required fields
     present, types correct); `_meta` block present and well-formed.
   - `tests/integration/test_email_corpus_alignment.py` — load the **committed**
     corpus through `FakeGmailBackend`; assert GT ids align 1:1 with loaded
     message ids (no orphans, no dupes, no missing).
2. **Fix the generator** (`generate_mbox.py`):
   - Emit `"low priority"` (space) for the low bucket — derive from
     `ALL_CATEGORIES` so it can never drift again.
   - Key `ground_truth.json` by the Gmail-derived id
     (`sha256(Message-ID)[:16]`), the same transform `fake_gmail.py` uses, so GT
     aligns 1:1 with the loaded corpus. Re-use the existing helper rather than
     re-deriving the hash inline.
   - Add a `_meta` block to `ground_truth.json` matching the stub's shape
     (`fixture`, `fixture_kind: "synthetic"`, `schema_version`, taxonomy note).
3. **Generate + commit** `synthetic_inbox.mbox` + `ground_truth.json`. Make
   tests green.
4. **Update `_schema.md`** to reflect: keys are Gmail-derived ids (already
   documented), category taxonomy uses `"low priority"` (space), corpus is the
   220-message synthetic dataset (no longer "stub"), and the 220/4 reconciliation
   decision + why not 1000/5.
5. **Baseline artifact.** Write a `baseline_accuracy.json` template pointed at
   `gemma4-it-e2b` for the orchestrator to fill from the real-world run. The
   orchestrator records the real number on the test machine.

## Real-world baseline recipe (for the orchestrator — DO NOT run here)

The email-triage baseline is recorded by the integration test, **not**
`gaia eval agent` (no email scenario type exists; F2 descoped). On the test
machine with Lemonade serving `gemma4-it-e2b` at port 13305:

```bash
# 1. Point the integration test at the demo model + corpus.
export GAIA_EMAIL_EVAL_MODEL=gemma4-it-e2b
export LEMONADE_BASE_URL=http://localhost:13305

# 2. Run the triage integration test against the committed 220-msg corpus and
#    capture per-category / spam / phishing accuracy (prints the breakdown).
python -m pytest tests/integration/test_email_agent_triage.py \
  -k corpus -s -v
```

The orchestrator transcribes the printed `category`, `spam`, `phishing`
accuracies into:

`tests/fixtures/email/baseline_accuracy.json`

with `"model": "gemma4-it-e2b"`, the measured `category_accuracy` /
`is_spam_accuracy` / `is_phishing_accuracy`, `"_recorded_on"`, and
`"_recorded_by": "real measurement on gemma4-it-e2b"` (replacing the stub
Qwen number). Commit that file on this branch.

Working dir: repo root. Lemonade single-tenant — run serially, never alongside
another eval.

## Acceptance criteria mapping

- **AC1** (synthetic corpus committed): generator output committed; RFC 2606
  domains only, deterministic — synthetic by construction.
- **AC2** (real gemma4-it-e2b baseline): recipe above + `baseline_accuracy.json`
  template; orchestrator records the number.
- **AC3** (size/split reconciled): 220/4, documented here + in commit + schema.
- **Test ACs**: unit (count/split/schema) + integration (1:1 GT↔mbox alignment).
