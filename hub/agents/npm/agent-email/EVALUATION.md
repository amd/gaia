# Email Triage Agent — Evaluation Guide

How the Email Triage agent is evaluated, what the dataset is, and how to
reproduce the numbers yourself. For the **latest results**, see
[`SCORECARD.md`](https://github.com/amd/gaia/blob/main/hub/agents/npm/agent-email/SCORECARD.md);
this document is the background and reproduction recipe behind them.

## What this measures

The **Email Triage** agent sorts a Gmail inbox into priority/category buckets —
`URGENT`, `NEEDS_RESPONSE`, `FYI`, `PROMOTIONAL`, `PERSONAL` — so nothing
important gets buried. It runs **100% locally** on AMD Ryzen AI: no cloud, no
mail leaving the device.

The eval measures triage **quality** on a fixed, labelled corpus. The agent
triages every email through a **`FakeGmailBackend`** (a synthetic inbox loaded
from an mbox file — never a live mailbox), and each prediction is compared to a
human/vendor label. **No LLM judge is used** — scoring is deterministic
label-matching, so the numbers are stable and cheap to reproduce.

The harness is [`gaia eval benchmark`](https://github.com/amd/gaia/blob/main/src/gaia/eval/benchmark.py)
(`src/gaia/eval/benchmark.py`); it drives the unchanged agent and reuses the
shared scorecard renderer.

## The dataset

The corpus is **vendor-derived, not GAIA-synthesised**, and fully reproducible
from a single committed source of truth.

| File | Role |
|------|------|
| [`vendor_corpus_seed.jsonl`](https://github.com/amd/gaia/blob/main/tests/fixtures/email/vendor_corpus_seed.jsonl) | **Source of truth** (committed) — a deterministic, category-balanced subset of the vendor's labelled mailbox dataset, already in the schema-2.0 taxonomy |
| `synthetic_inbox.mbox` | **Generated** (gitignored) — the mbox the eval loads via `FakeGmailBackend` |
| `ground_truth.json` | **Generated** (gitignored) — per-email labels, keyed by the Gmail-derived id `sha256(Message-ID)[:16]` so labels align 1:1 with `FakeGmailBackend` |
| [`_schema.md`](https://github.com/amd/gaia/blob/main/tests/fixtures/email/_schema.md) | Full corpus schema, provenance chain, PII policy, and category split |

The two generated files are rebuilt from the seed on demand — never a live mailbox:

```sh
python tests/fixtures/email/generate_mbox.py           # seed -> mbox + ground_truth
python tests/fixtures/email/generate_mbox.py --verify   # check the two are in sync
```

**Provenance chain (all reproducible):**

```
vendor mailbox JSONL  --select_vendor_subset.py-->  vendor_corpus_seed.jsonl
                      --generate_mbox.py-->          synthetic_inbox.mbox + ground_truth.json
```

The corpus is category-balanced on purpose so per-category accuracy is meaningful
and the scarce `PERSONAL` bucket stays measurable. Selection excludes real-person
corpora (no personal PII enters a committed fixture); see `_schema.md` for the
exact policy and counts.

## A worked example (one labeled email)

**1. Input — a seed record (source of truth):**

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

`generate_mbox.py` writes this into the mbox and emits the matching
**ground-truth** entry, keyed by the Gmail-derived id:

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

**2. Output — the agent's prediction.** At eval time the agent triages the email
and returns a structured result (shape from
[`llm_triage.py`](https://github.com/amd/gaia/blob/main/hub/agents/python/email/gaia_agent_email/tools/llm_triage.py);
the values below are illustrative for this email):

```json
{
  "category": "NEEDS_RESPONSE",
  "is_spam": false,
  "confidence": 0.82,
  "reasoning": "Direct data request from a partner; expects a reply.",
  "suggested_action": "reply"
}
```

**3. Scoring.** The scorer compares the predicted `category` to the ground-truth
`category` and records one row:

```json
{ "id": "<gmail-id>", "predicted": "NEEDS_RESPONSE", "expected": "NEEDS_RESPONSE", "category_correct": true }
```

Because triage priority is **ordinal** (`URGENT > NEEDS_RESPONSE > FYI >
PROMOTIONAL`), the headline metric credits an exact match **or** an adjacent
bucket. For this email, predicting `URGENT` or `FYI` still counts toward the
aggregate, while `PROMOTIONAL` (two buckets away) does not.

## Understanding the metrics

| Metric | In aggregate? | Meaning |
|--------|:---:|---------|
| **within_one_bucket_accuracy** | ✅ headline | Share of emails whose predicted priority is exact or one bucket off — what a user feels: nothing urgent buried. The only metric in the aggregate. |
| **category_accuracy** | reported | Strict exact-match rate on the category label. Always lower than the headline; the harder bar. |
| **urgent_recall** | reported (anti-gaming floor) | Fraction of truly urgent emails caught. Keeps a model from inflating the headline by calling everything urgent. |
| **urgent_vs_not_accuracy** | reported | Binary urgent-vs-everything-else accuracy. |
| **personal_recall** | reported | Recall on the scarce `PERSONAL` bucket. |

Only `within_one_bucket_accuracy` carries weight; the rest are shown with weight
0, so the published aggregate is recomputable as
`100 × within_one_bucket_accuracy`. The exact formula and a worked recomputation
are in [`SCORECARD.md`](https://github.com/amd/gaia/blob/main/hub/agents/npm/agent-email/SCORECARD.md).

## Reproducing the scorecard

Runs the real eval on a source checkout of [`amd/gaia`](https://github.com/amd/gaia).
It needs a **Lemonade Server on AMD Ryzen AI hardware** (Strix Halo recommended);
the npm package alone does not ship the corpus or harness.

```sh
# 1. Install the eval extras and start the LLM backend (separate shell).
uv pip install -e ".[dev,eval,api]"
lemonade-server serve

# 2. Build the corpus from the committed seed (generated artifacts, gitignored).
python tests/fixtures/email/generate_mbox.py

# 3. Run the benchmark over the synthetic corpus (no live mailbox, no LLM judge).
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring \
GAIA_AGENT_TOOL_TIMEOUT=1800 \
PYTHONPATH="$(pwd)" \
gaia eval benchmark \
    --model Gemma-4-E4B-it-GGUF \
    --mbox-path tests/fixtures/email/synthetic_inbox.mbox \
    --ground-truth tests/fixtures/email/ground_truth.json \
    --limit 250 \
    --output-dir /tmp/email-eval

# 4. Regenerate SCORECARD.md from the run.
PYTHONPATH="$(pwd)" \
python hub/agents/python/email/packaging/gen_scorecard.py \
    --benchmark-dir /tmp/email-eval \
    --ground-truth tests/fixtures/email/ground_truth.json \
    --limit 250
```

Notes:

- **`GAIA_AGENT_TOOL_TIMEOUT=1800`** — full-corpus triage is one long tool call
  (~17 min on a 4B local model); a lower timeout abandons it mid-run and scores 0
  emails.
- **Run evals serially.** Never run two `gaia eval` processes against one Lemonade
  Server — they race-evict each other's model. Use `--experiments N` for
  run-to-run variance instead of parallel runs.
- **Variance:** `--experiments 3` repeats the run and records mean / stdev / 95%
  CI (surfaced in the scorecard's `acceptance_variance`).

## CI

- **Nightly** (`.github/workflows/test_email_agent_eval.yml`) — runs the benchmark
  over the synthetic corpus on the self-hosted AMD (`stx`) pool and uploads a gate
  report. It is **report mode**: the quality/perf/drafting gates log and upload but
  do not fail the build (the threshold manifests under `tests/fixtures/email/` ship
  `enforce: false`). Flip `enforce: true` in a manifest — data, not workflow — to
  start blocking on a breach.
- **Scorecard refresh** (`.github/workflows/email_scorecard_refresh.yml`) — when
  the agent's LLM-affecting code or the corpus changes, re-runs the real eval,
  regenerates `SCORECARD.md`, and runs a same-version regression check.
