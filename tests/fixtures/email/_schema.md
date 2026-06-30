# Email-triage corpus schema

## Purpose

`synthetic_inbox.mbox` + `ground_truth.json` are the committed, deterministic
corpus for email-triage categorization eval. The corpus is **vendor-derived**:
the labelled emails come from the vendor's mailbox dataset (already in the
schema-2.0 triage taxonomy). The committed source of truth is
`vendor_corpus_seed.jsonl` — a deterministic, balanced subset of that dataset.

**Provenance chain (all reproducible):**

```
vendor mailbox JSONL  --select_vendor_subset.py-->  vendor_corpus_seed.jsonl
                      --generate_mbox.py-->          synthetic_inbox.mbox + ground_truth.json
```

- Regenerate the corpus from the committed seed: `python tests/fixtures/email/generate_mbox.py`
- Re-select the seed from the vendor source: `python tests/fixtures/email/select_vendor_subset.py --source <vendor.jsonl>`

**PII / provenance.** Selection is restricted to `origin_type` ∈
{synthetic, public_llm_labeled}, and the raw real-person corpora (Enron,
Hillary-Clinton) are excluded — no real personal correspondence enters a
committed fixture. Public spam corpora (SpamAssassin / ling_spam) are kept: the
vendor has wrapped them in synthetic sender/recipient envelopes (no personal PII)
and they are needed for the spam axis. The large vendor source file is **not**
committed (size + provenance); only the selected seed + derived artifacts are.

The filename `synthetic_inbox.mbox` is retained for continuity with importers; the
mbox is no longer GAIA-synthesised. The small `_stub_inbox.mbox` remains a legacy
fixture; new tests should use `synthetic_inbox.mbox`.

## Size / category split

- **249 messages**, balanced across the **five** schema-2.0 taxonomy categories
  (`URGENT`, `NEEDS_RESPONSE`, `FYI`, `PROMOTIONAL`, `PERSONAL`).
- The authoritative taxonomy is `ALL_CATEGORIES` in the email agent's
  `triage_heuristics` (now in the `gaia-agent-email` wheel); the builder imports it
  so the corpus can't carry a label outside it.
- Distribution: **54** each of URGENT / NEEDS_RESPONSE / FYI / PROMOTIONAL, and
  **33** PERSONAL (the scarcest bucket in the source — all eligible PERSONAL are
  taken). Balanced on purpose so per-category accuracy is meaningful and PERSONAL
  (#1437) is measurable. The spam/phishing axes are non-empty (≈63 spam, ≈48
  phishing) so they stay scoreable.
- The corpus stays under the 1 MB CI size guard (~312 KB).

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
    "taxonomy": ["URGENT", "NEEDS_RESPONSE", "FYI", "PROMOTIONAL", "PERSONAL"],
    "key": "gmail-id (sha256(Message-ID)[:16]) — aligns with FakeGmailBackend"
  },
  "<gmail_id>": {
    "category": "URGENT | NEEDS_RESPONSE | FYI | PROMOTIONAL | PERSONAL",
    "priority": "high | normal | low",
    "is_spam": false,
    "is_phishing": false,
    "is_thread_root": true,
    "thread_id": "<gmail_thread_id>",
    "has_attachment": false,
    "ambiguous": false,
    "rationale": "Free-text human reason for the label.",
    "sender_persona": "sarah_chen | ... | jamie_rivera | grace_okafor | spam_unknown | unknown"
  }
}
```

## Field semantics

| Field | Notes |
|---|---|
| `category` | Exactly one of the five schema-2.0 buckets (`URGENT`, `NEEDS_RESPONSE`, `FYI`, `PROMOTIONAL`, `PERSONAL`). `PERSONAL` is interpersonal mail (friends/family), orthogonal to the priority ladder. The generator derives every label from `ALL_CATEGORIES` via `_BUCKET_TO_CATEGORY` so it can never drift. |
| `is_spam` | True ⇔ spam-flagged. Scored independently of `category`. |
| `is_phishing` | True ⇔ phishing payload. Can co-fire with `is_spam`. |
| `is_thread_root` | True ⇔ first message in a thread. |
| `thread_id` | The Gmail-derived `threadId` (matches `FakeGmailBackend`). |
| `has_attachment` | True ⇔ at least one non-text part. |
| `ambiguous` | True ⇔ a reasonable human could disagree on the label. Vendor data carries no per-email ambiguity flag, so this is `false` for the whole corpus. |
| `rationale` | Ground-truth justification (empty for the vendor corpus). |
| `sender_persona` | The vendor `mailbox_persona` (e.g. `amd_pm`, `amd_executive`, `amd_developer`). Used for per-sender-type eval breakdown. |
| `suggested_action` | The vendor `suggestedAction` verb (`reply` / `archive` / `none`), schema-2.0. |
| `source_dataset` | Provenance of the email within the vendor dataset (e.g. `synthetic_llm`, `spamassassin`). |

## Builder properties (enforced by tests)

The corpus is real labelled mail converted 1:1 from the seed, so synthesis-fidelity
requirements (multipart/attachments/threading/malformed edge-cases) no longer
apply. What is enforced:

- Size equals the committed seed (`generate_mbox.TOTAL_MESSAGES`).
- Every label is a valid schema-2.0 category; all five buckets are populated and
  PERSONAL has ≥20 examples.
- The spam and phishing axes are non-empty (so they stay scoreable).
- Keys are Gmail-derived ids; ground truth aligns 1:1 with `FakeGmailBackend`.
- The build is deterministic (`--verify`); mbox stays under 1 MB.

## Baseline

> ⚠️ `baseline_accuracy.json` / `baseline_accuracy_e2b.json` were recorded on the
> previous synthetic 220-message corpus and are **stale** for this vendor-derived
> 249-message corpus. Re-record them on AMD hardware (`score_baseline.py`) before
> relying on the `test_email_agent_triage` integration gate (which is
> Lemonade-gated and skips without a live server).

The baselines are recorded via the **production heuristic + LLM-assist triage
path** (#1107) — `triage_inbox_impl(fake_gmail, classifier=make_llm_classifier(agent.chat))`
over the corpus — so they are apples-to-apples with what the integration test
gates on. `baseline_accuracy.json` records `Gemma-4-E4B-it-GGUF`;
`baseline_accuracy_e2b.json` records `Gemma-4-E2B-it-GGUF`. Re-record with:

```bash
export LEMONADE_BASE_URL=http://localhost:13305   # Lemonade / NPU
python tests/fixtures/email/score_baseline.py --model Gemma-4-E4B-it-GGUF --write
python tests/fixtures/email/score_baseline.py --model Gemma-4-E2B-it-GGUF \
    --out tests/fixtures/email/baseline_accuracy_e2b.json --write
```

The integration test gates each axis (category, is_spam, is_phishing)
baseline-relative (`accuracy - tolerance_pp`).
