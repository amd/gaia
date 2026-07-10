---
schema_version: 1
agent:
  name: Email Triage
  version: 0.3.0
recipe:
  dataset:
    reference: tests/fixtures/email/ground_truth.json
    description: 'Vendor-derived labelled email corpus for GAIA email-triage evaluation
      (FakeGmailBackend, schema-2.0 triage taxonomy: urgent / needs_response / fyi
      / promotional / personal); a deterministic, category-balanced subset of the
      vendor mailbox dataset'
    size: 249
  methodology: 'gaia eval benchmark over the vendor-derived labelled corpus via FakeGmailBackend;
    no LLM judge. The full 249-email corpus is scored (GAIA_EMAIL_TRIAGE_MAX_MESSAGES
    lifts the interactive per-call scan cap for the eval so the whole balanced corpus
    is covered). Aggregate = within-one-bucket ACCEPTANCE accuracy (#1437): triage
    priority is ordinal (URGENT>NEEDS_RESPONSE>FYI>PROMOTIONAL), so a prediction is
    credited when it is exact or an adjacent bucket (|rank diff|<=1) — what users
    feel (nothing urgent buried). Reported secondaries (not in the aggregate): urgent-vs-not
    binary accuracy, urgent recall (anti-gaming floor), and exact 4-way category_accuracy.
    The corpus uses the schema-2.0 taxonomy aligned with the agent''s output labels
    (#1874); averaged over 3 run(s) for run-to-run variance/CI (#1894)'
  config:
    harness: gaia eval benchmark
    model: Gemma-4-E4B-it-GGUF
    corpus: tests/fixtures/email/synthetic_inbox.mbox
    ground_truth: tests/fixtures/email/ground_truth.json
    limit: 250
    n_runs: 3
    acceptance_variance:
      n_runs: 3
      within_one_bucket_accuracy:
        n: 3
        mean: 0.834
        stdev: 0.0116
        min: 0.8273
        max: 0.8474
        cv_pct: 1.39
        ci95_half_width: 0.0131
        ci95_low: 0.8209
        ci95_high: 0.8471
      urgent_vs_not_accuracy:
        n: 3
        mean: 0.7845
        stdev: 0.0129
        min: 0.7751
        max: 0.7992
        cv_pct: 1.65
        ci95_half_width: 0.0146
        ci95_low: 0.7699
        ci95_high: 0.7991
      urgent_recall:
        n: 3
        mean: 0.9938
        stdev: 0.0054
        min: 0.9907
        max: 1.0
        cv_pct: 0.54
        ci95_half_width: 0.0061
        ci95_low: 0.9877
        ci95_high: 0.9999
      personal_recall:
        n: 3
        mean: 0.3636
        stdev: 0.0
        min: 0.3636
        max: 0.3636
        cv_pct: 0.0
        ci95_half_width: 0.0
        ci95_low: 0.3636
        ci95_high: 0.3636
      category_accuracy:
        n: 3
        mean: 0.7684
        stdev: 0.0129
        min: 0.759
        max: 0.7831
        cv_pct: 1.68
        ci95_half_width: 0.0146
        ci95_low: 0.7538
        ci95_high: 0.783
  environment:
    gaia_commit: 8dad5985
    lemonade_version: 10.7.0
    model: Gemma-4-E4B-it-GGUF
    hardware: AMD Ryzen AI MAX+ (Strix Halo)
results:
  test_cases_run: 249
  metrics:
  - name: within_one_bucket_accuracy
    value: 0.834
    weight: 1.0
  - name: urgent_vs_not_accuracy
    value: 0.7845
    weight: 0.0
  - name: urgent_recall
    value: 0.9938
    weight: 0.0
  - name: personal_recall
    value: 0.3636
    weight: 0.0
  - name: category_accuracy
    value: 0.7684
    weight: 0.0
  breakdown:
    per_category:
    - category: fyi
      total: 162
      correct: 125
      accuracy: 0.7716
    - category: needs_response
      total: 162
      correct: 162
      accuracy: 1.0
    - category: personal
      total: 99
      correct: 36
      accuracy: 0.3636
    - category: promotional
      total: 162
      correct: 103
      accuracy: 0.6358
    - category: urgent
      total: 162
      correct: 148
      accuracy: 0.9136
    top_confusions:
    - expected: promotional
      predicted: urgent
      count: 48
    - expected: personal
      predicted: needs_response
      count: 48
    - expected: fyi
      predicted: needs_response
      count: 37
    - expected: personal
      predicted: urgent
      count: 15
    - expected: urgent
      predicted: needs_response
      count: 12
aggregate:
  name: weighted_accuracy
  formula: round(100 * sum(weight_i * value_i) / sum(weight_i), 2)
  components:
  - metric: within_one_bucket_accuracy
    value: 0.834
    weight: 1.0
  - metric: urgent_vs_not_accuracy
    value: 0.7845
    weight: 0.0
  - metric: urgent_recall
    value: 0.9938
    weight: 0.0
  - metric: personal_recall
    value: 0.3636
    weight: 0.0
  - metric: category_accuracy
    value: 0.7684
    weight: 0.0
  value: 83.4
generated_at: '2026-06-30T12:20:57.795513+00:00'
inherited_from: null
---
# Email Triage — Eval Scorecard v0.3.0

**Aggregate score: 83.4** (out of 100)

## Recipe

| Field | Value |
|-------|-------|
| Dataset | [tests/fixtures/email/ground_truth.json](tests/fixtures/email/ground_truth.json) |
| Description | Vendor-derived labelled email corpus for GAIA email-triage evaluation (FakeGmailBackend, schema-2.0 triage taxonomy: urgent / needs_response / fyi / promotional / personal); a deterministic, category-balanced subset of the vendor mailbox dataset |
| Dataset size | 249 labeled examples |
| Test cases run | 249 |
| Methodology | gaia eval benchmark over the vendor-derived labelled corpus via FakeGmailBackend; no LLM judge. The full 249-email corpus is scored (GAIA_EMAIL_TRIAGE_MAX_MESSAGES lifts the interactive per-call scan cap for the eval so the whole balanced corpus is covered). Aggregate = within-one-bucket ACCEPTANCE accuracy (#1437): triage priority is ordinal (URGENT>NEEDS_RESPONSE>FYI>PROMOTIONAL), so a prediction is credited when it is exact or an adjacent bucket (|rank diff|<=1) — what users feel (nothing urgent buried). Reported secondaries (not in the aggregate): urgent-vs-not binary accuracy, urgent recall (anti-gaming floor), and exact 4-way category_accuracy. The corpus uses the schema-2.0 taxonomy aligned with the agent's output labels (#1874); averaged over 3 run(s) for run-to-run variance/CI (#1894) |

## Metrics

  - **within_one_bucket_accuracy**: 0.8340 × 1.0
  - **urgent_vs_not_accuracy**: 0.7845 × 0.0
  - **urgent_recall**: 0.9938 × 0.0
  - **personal_recall**: 0.3636 × 0.0
  - **category_accuracy**: 0.7684 × 0.0

## Aggregate score recomputation

Formula: `round(100 × Σ(weightᵢ × valueᵢ) / Σ(weightᵢ), 2)`

Worked example:

```
round(100 × ((0.8340 × 1.0) + (0.7845 × 0.0) + (0.9938 × 0.0) + (0.3636 × 0.0) + (0.7684 × 0.0)) / 1.0, 2) = 83.4
```

A reader can reproduce this value from the `aggregate.components` in the front
matter alone — no eval-harness access needed.

## Reproduction

Run the following commands from the repository root:

```sh
# Prerequisites: install the eval extras and start a Lemonade Server
# with the model on AMD Ryzen AI hardware (Strix Halo recommended).
uv pip install -e ".[dev,eval,api]"
lemonade-server serve   # in a separate shell; must stay running

# Step 0: build the corpus from the committed seed. The mbox +
# ground_truth are GENERATED artifacts (gitignored), so a fresh
# checkout must materialise them before the benchmark can read them.
python tests/fixtures/email/generate_mbox.py

# Step 1: run the benchmark (requires the Lemonade Server above with the
# model loaded; AMD Ryzen AI / Strix Halo recommended)
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring \
GAIA_AGENT_TOOL_TIMEOUT=1800 \
PYTHONPATH="$(pwd)" \
gaia eval benchmark \
    --model Gemma-4-E4B-it-GGUF \
    --mbox-path tests/fixtures/email/synthetic_inbox.mbox \
    --ground-truth tests/fixtures/email/ground_truth.json \
    --limit 250 \
    --output-dir /tmp/email-eval

# Step 2: generate this scorecard from the benchmark output
PYTHONPATH="$(pwd)" \
python hub/agents/python/email/packaging/gen_scorecard.py \
    --benchmark-dir /tmp/email-eval \
    --ground-truth tests/fixtures/email/ground_truth.json \
    --limit 250
```

See [eval-scorecard docs](https://amd-gaia.ai/docs/reference/eval-scorecard) and the [`adding-eval-scorecard` skill](.claude/skills/adding-eval-scorecard/SKILL.md) for the full setup guide.

## Environment

| Field | Value |
|-------|-------|
| gaia_commit | 8dad5985 |
| lemonade_version | 10.7.0 |
| model | Gemma-4-E4B-it-GGUF |
| hardware | AMD Ryzen AI MAX+ (Strix Halo) |

## Category breakdown (pooled across all 3 runs)

_Each of the 249 test cases is scored once per run, so the totals below sum to test_cases_run × 3._

| Category | Total | Correct | Accuracy |
|----------|-------|---------|----------|
| fyi | 162 | 125 | 0.7716 |
| needs_response | 162 | 162 | 1.0000 |
| personal | 99 | 36 | 0.3636 |
| promotional | 162 | 103 | 0.6358 |
| urgent | 162 | 148 | 0.9136 |

**Top confusions:**

  - promotional → urgent: 48
  - personal → needs_response: 48
  - fyi → needs_response: 37
  - personal → urgent: 15
  - urgent → needs_response: 12

<!-- scorecard:notes:start -->
## Dataset

The corpus is **vendor-derived, not GAIA-synthesised**, and fully reproducible
from a single committed source of truth:

| File | Role |
|------|------|
| [`tests/fixtures/email/vendor_corpus_seed.jsonl`](../../../../tests/fixtures/email/vendor_corpus_seed.jsonl) | **Source of truth** (committed) — a deterministic, category-balanced subset of the vendor's labelled mailbox dataset, already in the schema-2.0 taxonomy |
| `tests/fixtures/email/synthetic_inbox.mbox` | **Generated** (gitignored) — the mbox the eval loads via `FakeGmailBackend` |
| `tests/fixtures/email/ground_truth.json` | **Generated** (gitignored) — per-email labels (249 entries), keyed by the Gmail-derived id `sha256(Message-ID)[:16]` so labels align 1:1 with `FakeGmailBackend` |
| [`tests/fixtures/email/_schema.md`](../../../../tests/fixtures/email/_schema.md) | Full corpus schema, provenance chain, PII policy, and category split |

Both generated files are rebuilt from the seed on demand — never a live mailbox:

```sh
python tests/fixtures/email/generate_mbox.py          # seed -> mbox + ground_truth
python tests/fixtures/email/generate_mbox.py --verify  # check the two are in sync
```

Provenance: `vendor mailbox JSONL --select_vendor_subset.py--> vendor_corpus_seed.jsonl
--generate_mbox.py--> synthetic_inbox.mbox + ground_truth.json`. The five
schema-2.0 categories are `URGENT`, `NEEDS_RESPONSE`, `FYI`, `PROMOTIONAL`,
`PERSONAL` (the agent's own output labels).

### Worked example (one labeled email)

A seed record (source of truth):

```json
{
  "id": "004d89d1-cba8-4045-b94b-b6cab756529b",
  "sender": "Hugo Petit <hugo.petit@lenovo.com>",
  "subject": "Ryzen AI Adoption Trends Across ThinkPad Lineup - Data Request",
  "category": "NEEDS_RESPONSE",
  "suggestedAction": "reply",
  "is_phishing": false,
  "source_dataset": "spamassassin"
}
```

`generate_mbox.py` writes it into the mbox and emits the matching ground-truth
entry, keyed by the Gmail-derived id:

```json
"<gmail-id>": {
  "category": "NEEDS_RESPONSE",
  "priority": "normal",
  "is_spam": false,
  "is_phishing": false,
  "suggested_action": "reply",
  "source_dataset": "spamassassin"
}
```

At eval time the agent triages the email and predicts a category. Scoring
compares the prediction to `category` above. Because priority is ordinal
(`URGENT > NEEDS_RESPONSE > FYI > PROMOTIONAL`), the headline metric credits an
exact match **or** an adjacent bucket — so predicting `URGENT` or `FYI` here
still counts toward the aggregate, while `PROMOTIONAL` (two buckets away) does
not.

## Understanding the metrics

- **within_one_bucket_accuracy** *(headline, weight 1.0)* — share of emails whose
  predicted priority is exact or one bucket off. This is what a user feels:
  nothing urgent buried. It is the only metric in the aggregate.
- **category_accuracy** *(reported)* — strict exact-match rate on the category
  label. Always lower than the headline; it is the harder bar.
- **urgent_recall** *(reported, anti-gaming floor)* — fraction of truly urgent
  emails caught. A model can't inflate the headline by calling everything urgent
  without this staying high.
- **urgent_vs_not_accuracy** / **personal_recall** *(reported)* — binary
  urgent-vs-not accuracy and recall on the scarce `PERSONAL` bucket.

Only `within_one_bucket_accuracy` is weighted; the rest are shown with weight 0
so the aggregate stays recomputable as `100 x within_one_bucket_accuracy`.
<!-- scorecard:notes:end -->
