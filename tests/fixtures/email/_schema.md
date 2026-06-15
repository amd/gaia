# Synthetic email-triage corpus schema

## Purpose

`synthetic_inbox.mbox` + `ground_truth.json` are the committed, deterministic
synthetic corpus for email-triage categorization eval (#1230). The corpus is
**fully synthetic** (RFC 2606 example domains, deterministic seed) — it is never
a live corporate mailbox. Regenerate with
`python tests/fixtures/email/generate_mbox.py`.

The small `_stub_inbox.mbox` (~10 messages) remains only as a legacy fixture;
new tests should use `synthetic_inbox.mbox`.

## Size / category split (reconciliation, #1230 AC3)

- **220 messages**, across the **four** v0.20 taxonomy categories.
- The "1000 messages / 5 categories" figure from early planning was rejected:
  there is no in-repo plan backing it, and the authoritative taxonomy is
  `ALL_CATEGORIES` in `src/gaia/agents/email/tools/triage_heuristics.py` — the
  four buckets below. Adding a fifth category would require changing production
  classification code, which is out of scope for a corpus/baseline task. 220/4
  keeps the corpus under the 1 MB CI size guard while being a 22x jump over the
  stub. The reconciliation is "fix the generator to match the code that consumes
  it," not "grow the corpus to match an unsourced number."

## ground_truth.json keying

`ground_truth.json` is keyed by the **Gmail-derived id** —
`sha256(Message-ID header)[:16]` — exactly as
`tests/fixtures/email/fake_gmail.py::mbox_message_to_gmail_payload` derives it.
This guarantees the ground truth aligns **1:1** with `FakeGmailBackend` when the
eval loads the corpus. Keying by the raw RFC `Message-ID` (the pre-#1230 bug)
made every lookup miss, silently scoring nothing.

A `_meta` block (key `_meta`, and any `_`-prefixed key) carries provenance and is
NOT a message entry — consumers must skip `_`-prefixed keys.

```json
{
  "_meta": {
    "fixture": "synthetic_inbox.mbox",
    "fixture_kind": "synthetic",
    "schema_version": 2,
    "taxonomy": ["urgent", "actionable", "informational", "low priority"],
    "key": "gmail-id (sha256(Message-ID)[:16]) — aligns with FakeGmailBackend"
  },
  "<gmail_id>": {
    "category": "urgent | actionable | informational | low priority",
    "priority": "high | normal | low",
    "is_spam": false,
    "is_phishing": false,
    "is_thread_root": true,
    "thread_id": "<gmail_thread_id>",
    "has_attachment": false,
    "ambiguous": false,
    "rationale": "Free-text human reason for the label.",
    "sender_persona": "sarah_chen | ... | spam_unknown | unknown"
  }
}
```

## Field semantics

| Field | Notes |
|---|---|
| `category` | Exactly one of the four buckets. The low bucket is `"low priority"` (space), NEVER `"low_priority"` — it MUST match `CATEGORY_LOW_PRIORITY` / `ALL_CATEGORIES`. The generator derives every label from `ALL_CATEGORIES` via `_BUCKET_TO_CATEGORY` so it can never drift. |
| `is_spam` | True ⇔ spam-flagged. Scored independently of `category`. |
| `is_phishing` | True ⇔ phishing payload. Can co-fire with `is_spam`. |
| `is_thread_root` | True ⇔ first message in a thread. |
| `thread_id` | The Gmail-derived `threadId` (matches `FakeGmailBackend`). |
| `has_attachment` | True ⇔ at least one non-text part. |
| `ambiguous` | True ⇔ a reasonable human could disagree on the label. |
| `rationale` | Hand-written ground-truth justification. |
| `sender_persona` | Synthetic-only. Used for per-sender-type eval breakdown. |

## Fidelity requirements (enforced by the generator + tests)

- ≥30% of messages are `text/html` / `multipart/alternative`.
- At least one calendar invite (`text/calendar`), one forwarded message, and one
  phishing-style payload.
- Threading headers, malformed/parser-edge cases, and persona recurrence ranges.
- mbox stays under 1 MB.

## Baseline

The baselines are recorded via the **production heuristic + LLM-assist triage
path** (#1107) — `triage_inbox_impl(fake_gmail, classifier=make_llm_classifier(agent.chat))`
over the 220 corpus — so they are apples-to-apples with what the integration
test gates on. This supersedes the earlier stub-inbox + Qwen3.5-35B baseline
and the standalone single-prompt classifier.

`baseline_accuracy.json` records the primary demo model `Gemma-4-E4B-it-GGUF`;
`baseline_accuracy_e2b.json` records the second model `Gemma-4-E2B-it-GGUF`.
Both carry `category_accuracy` + `category_breakdown`, plus `is_spam_accuracy`
and `is_phishing_accuracy` measured on the same run. Produced by:

```bash
export LEMONADE_BASE_URL=http://localhost:13305   # Lemonade / NPU
python tests/fixtures/email/score_baseline.py --model Gemma-4-E4B-it-GGUF --write
python tests/fixtures/email/score_baseline.py --model Gemma-4-E2B-it-GGUF \
    --out tests/fixtures/email/baseline_accuracy_e2b.json --write
```

The integration test gates each axis (category, is_spam, is_phishing)
baseline-relative (`accuracy - tolerance_pp`). spam/phishing are not perfect on
the 220 corpus: `is_spam`/`is_phishing` are heuristic-set and the LLM follow-up
only revises the *category*, so it cannot flip those flags — gating
baseline-relative (vs a faked 100% assert) keeps the real spam-recall ceiling
visible.
