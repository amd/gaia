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

**Baseline models (real measurement, recorded on the Strix Halo NPU):** record
BOTH Gemma-4 demo models and drop the Qwen3.5-35B stub. `Gemma-4-E4B-it-GGUF`
is the primary/demo model (matches the repo's existing `gemma-4-e4b-*`
baselines and the integration test) at **category_accuracy 0.6682 (147/220)**;
`Gemma-4-E2B-it-GGUF` is the smaller second model at **0.4455 (98/220)**,
recorded in `baseline_accuracy_e2b.json` via the `--out` flag.

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
5. **Baseline artifact + harness.** Add `tests/fixtures/email/score_baseline.py`
   (drives the LLM per message over the corpus, scores categories vs ground
   truth, writes the scorecard; `--out` selects the per-model baseline file)
   and record both Gemma-4 demo models. Category numbers are the real LLM
   measurement; `is_spam_accuracy`/`is_phishing_accuracy` are the
   model-independent confident-decision heuristic accuracy.

## Real-world baseline recipe (recorded on the NPU; re-runnable)

The email-triage **category** baseline is recorded by the dedicated
`score_baseline.py` harness, **not** `gaia eval agent` (no email scenario type
exists; F2 descoped) and **not** the triage integration test (its heuristic
path is model-independent — it does not produce an LLM category number).
`score_baseline.py` drives the LLM per message over the committed corpus and
scores categories against ground truth.

On the test machine with Lemonade at port 13305 (working dir = repo root;
Lemonade is single-tenant — run serially):

```bash
export LEMONADE_BASE_URL=http://localhost:13305

# Primary demo model -> baseline_accuracy.json (default --out):
python tests/fixtures/email/score_baseline.py \
    --model Gemma-4-E4B-it-GGUF --write

# Second demo model -> baseline_accuracy_e2b.json:
python tests/fixtures/email/score_baseline.py \
    --model Gemma-4-E2B-it-GGUF \
    --out tests/fixtures/email/baseline_accuracy_e2b.json --write

# (Drop --write for a dry run that only prints the scorecard.)
```

Recorded results (Strix Halo NPU, 2026-06-01):
- `Gemma-4-E4B-it-GGUF` (primary) -> `baseline_accuracy.json`,
  category_accuracy **0.6682** (147/220).
- `Gemma-4-E2B-it-GGUF` (second) -> `baseline_accuracy_e2b.json`,
  category_accuracy **0.4455** (98/220).

**spam / phishing gating.** The integration test scores `is_spam` and
`is_phishing` ONLY on the heuristic's *confident* decisions — it defers
low-confidence messages to the (not-yet-wired) LLM fallback, and scoring those
deferrals would penalize correct behaviour. The corpus carries inbox-spam with
no Gmail SPAM label that the keyword heuristic legitimately can't catch (FN on
deferral, FP=0). On confident decisions the heuristic is perfect
(spam 43/43, phishing 43/43), so `is_spam_accuracy`/`is_phishing_accuracy` are
recorded as `1.0` in `baseline_accuracy.json` (model-independent — computed by
running the heuristic over the corpus, not the LLM). Each axis (category, spam,
phishing) is a baseline-relative soft gate that xfails on a genuine regression.
`baseline_accuracy_e2b.json` omits the spam/phishing fields (category only).

## Acceptance criteria mapping

- **AC1** (synthetic corpus committed): generator output committed; RFC 2606
  domains only, deterministic — synthetic by construction.
- **AC2** (real demo-model baseline): recorded on the NPU — `Gemma-4-E4B-it-GGUF`
  0.6682 in `baseline_accuracy.json` (primary), `Gemma-4-E2B-it-GGUF` 0.4455 in
  `baseline_accuracy_e2b.json`. Re-runnable via the recipe above.
- **AC3** (size/split reconciled): 220/4, documented here + in commit + schema.
- **Test ACs**: unit (count/split/schema) + integration (1:1 GT↔mbox alignment).
