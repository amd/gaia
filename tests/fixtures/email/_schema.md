# Synthetic email-eval fixture schema (stub)

## Purpose

This stub fixture (`_stub_inbox.mbox` + `ground_truth.json`) is a **temporary
placeholder** until issue #848 lands its synthetic corporate-executive eval
dataset.

When #848 lands, the merge PR MUST verify field-by-field alignment with the
schema below before deleting the stub. The leading underscore on
`_stub_inbox.mbox` is a deliberate flag that this is temporary.

## ground_truth.json schema

Each entry in `ground_truth.json` is keyed by the Gmail message id (16-char
SHA1 prefix derived deterministically from the mbox `Message-ID` header by
`tests/fixtures/email/fake_gmail.py::mbox_message_to_gmail_payload`).

```json
{
  "<message_id>": {
    "category": "urgent | actionable | informational | low priority",
    "is_spam": false,
    "is_phishing": false,
    "is_thread_root": true,
    "thread_id": "<thread_id>",
    "has_attachment": false,
    "ambiguous": false,
    "rationale": "Free-text human reason for the label.",
    "sender_persona": "boss | direct_report | external_vendor | newsletter | service_alert | spam_bot | unknown"
  }
}
```

## Field semantics

| Field | Notes |
|---|---|
| `category` | Exactly one of the four #848 buckets. NEVER `URGENT`/`NEEDS_RESPONSE`/etc. (PR #916's old taxonomy). |
| `is_spam` | True ⇔ Gmail's SPAM label OR a heuristic match. The eval scores this independently of `category`. |
| `is_phishing` | True ⇔ subject keyword pair matches the conservative phishing list. Can co-fire with `is_spam`. |
| `is_thread_root` | True ⇔ this is the first message in a thread. Used to score thread-aware triage. |
| `thread_id` | Same as `gmail_message["threadId"]`. |
| `has_attachment` | True ⇔ at least one non-text part. |
| `ambiguous` | True ⇔ a reasonable human could disagree on the label. The eval lowers the per-message weight when this is True. |
| `rationale` | Hand-written ground-truth justification — used to debug agent disagreements. |
| `sender_persona` | Synthetic-dataset-only. Used by the eval report for breakdown by sender type. |

## Fidelity requirements

- **At least 30%** of messages must be `text/html` (or `multipart/alternative`
  with a real HTML branch). Real corporate / promotional email is overwhelmingly
  HTML-only; a `text/plain`-heavy synthetic dataset will NOT predict live
  accuracy.
- At least one calendar invite (`text/calendar` part).
- At least one forwarded message (`message/rfc822` part).
- At least one phishing-style payload to test `is_phishing` independently of
  `is_spam`.

## Path

The canonical fixture lives at `tests/fixtures/email/_stub_inbox.mbox`. There
is no `eval/corpus/email/synthetic_inbox.mbox` symlink — the eval-runner YAML
extension was descoped (see plan F2 amendment).
