---
type: plan
source-issue: 1275
repo: amd/gaia
title: "Outlook.com personal mailbox connector (MS Graph mail backend)"
created: 2026-06-02
status: in-progress
work_type: code-feature
complexity: standard
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 4
test_command: ".venv/bin/python -m pytest tests/unit/agents/email/ tests/unit/connectors/test_microsoft_provider.py -q"
build_command: "uv pip install -e .[dev]"
lint_command: "python util/lint.py --black --isort"
branch: tmi/issue-1275-outlook-mailbox
reflection_iterations: 0
agents_used: [planning, execution, validation]
---

# Outlook.com personal mailbox connector (#1275)

Stacked on #1105 (`tmi/issue-1105-ms-oauth-provider`). Consumes the
`microsoft` connector + `MicrosoftOAuthProvider` already on the base branch.

## Goal

Let the Email Triage Agent operate on a personal Outlook.com / Hotmail /
Live mailbox via Microsoft OAuth, alongside (not replacing) the shipped
Gmail connector.

## Architecture — the seam #1105 anticipated

`gmail_backend.py`'s docstring explicitly designed the `GmailBackend`
Protocol's semantic verbs (`archive_message`, `mark_read`, …) to be
provider-agnostic so an Outlook backend could slot in. The email tools
(`read_tools`, `organize_tools`, `reply_tools`, `delete_tools`) call
methods on `self._gmail` (a `GmailBackend`) and consume **Gmail-API-v1
shaped** dicts (`payload.headers[]`, `payload.body.data` base64url,
`labelIds` with `INBOX`/`UNREAD`/`STARRED`).

Therefore `OutlookBackend` satisfies the SAME `GmailBackend` Protocol by
**translating MS Graph JSON → Gmail-API-v1 shape** on read, and Gmail
semantic verbs → MS Graph mutations on write. The tools cannot tell which
backend they are talking to (same parity contract the `FakeGmailBackend`
honours).

### MS Graph translation map

| Gmail verb / shape | MS Graph |
|---|---|
| `get_message(id)` full Gmail payload | `GET /me/messages/{id}` → build `payload.headers` from `subject`/`from`/`toRecipients`/`receivedDateTime`, `payload.body.data`=b64url(`body.content`) with `mimeType` from `body.contentType` |
| `list_messages(label_ids=["INBOX"])` | `GET /me/mailFolders/inbox/messages?$top=N` |
| `list_messages(label_ids=["UNREAD"])` | `…?$filter=isRead eq false` |
| `list_messages(query=...)` | `GET /me/messages?$search="..."` |
| `labelIds` INBOX / UNREAD / STARRED | `parentFolderId`==inbox / `isRead`==false / `flag.flagStatus`=="flagged" |
| `archive_message` (Gmail: remove INBOX) | `POST /me/messages/{id}/move {destinationId:"archive"}` |
| `trash_message` / `untrash_message` | move to `deleteditems` / back to `inbox` |
| `permanent_delete` | `DELETE /me/messages/{id}` |
| `mark_read` / `mark_unread` | `PATCH /me/messages/{id} {isRead:true|false}` |
| `add_star` / `remove_star` | `PATCH … {flag:{flagStatus:"flagged"|"notFlagged"}}` |
| `add_label` / `remove_label` | `PATCH … {categories:[...]}` (categories are Outlook's label analogue) |
| `create_draft` | `POST /me/messages {subject,body,toRecipients}` → `{id}` |
| `send_draft` | `POST /me/messages/{id}/send` |
| `send_message` | `POST /me/sendMail {message,saveToSentItems:true}` |
| `list_labels` | `GET /me/outlook/masterCategories` → mapped to label dicts |
| `get_thread(conversationId)` | `GET /me/messages?$filter=conversationId eq '...'` |

### Token seam (issue-named)

`_get_outlook_token()` → `get_credential_sync("microsoft",
agent_id="builtin:email", required_scopes=OUTLOOK_SCOPES)["access_token"]`.
The grant dispatcher raises `AuthRequiredError(AGENT_NOT_GRANTED)` when the
user hasn't granted the scopes (eager, pre-network). A runtime
no-access/expired token surfaces as a Graph 401/403 → `ConnectorsError`.
Both are actionable raises — never a silent empty list.

### Backend selection (wiring)

- `EmailAgentConfig` gains `mail_provider: str = "google"` (selector) and
  `outlook_backend: Optional[Any]` (eval/test injection seam, mirroring
  `gmail_backend`).
- New `EmailAgentConfig.resolve_mail_backend()` returns the right backend:
  injected backend > provider-selected live backend. Owns the
  google-vs-microsoft branch so `agent.py` stays a one-liner.
- `agent.py` line 194 changes from
  `self._gmail = config.gmail_backend or LiveGmailBackend(...)`
  to `self._gmail = config.resolve_mail_backend()`. **Backend-selection
  only** — flagged in handoff.
- `REQUIRED_CONNECTORS` gains a `microsoft` entry (Mail.ReadWrite +
  Mail.Send) so a microsoft-connected user is grant-checked correctly. The
  existing `google` entry is untouched (Gmail keeps working).

## Lane

OWN: new `src/gaia/agents/email/outlook_backend.py`, new
`src/gaia/agents/email/outlook_scopes.py` (MS Graph scope constants),
`config.py` wiring, one-line `agent.py` backend-selection swap +
`REQUIRED_CONNECTORS` microsoft entry, and tests under
`tests/unit/agents/email/`.

Do NOT modify: `providers/microsoft.py`, `catalog/microsoft.py`,
`gmail_backend.py` (read-only reference), tool files, `api/`.

## TDD steps

1. RED: `tests/unit/agents/email/test_outlook_backend.py` — mock token +
   `httpx.MockTransport`. Assert: read translation → Gmail shape; verbs hit
   correct Graph endpoints; 403/401 → `ConnectorsError` (no silent empty);
   token grant failure → `AuthRequiredError`; success list → normalized
   messages; the email read tools (`list_inbox_impl`, `triage_inbox_impl`)
   run unchanged against `OutlookBackend` and produce normal output.
   `tests/unit/agents/email/test_backend_selection.py` — config selector
   routes `microsoft`→Outlook, `google`→Gmail; Gmail not broken.
2. GREEN: implement `outlook_backend.py`, `outlook_scopes.py`, `config.py`
   wiring, `agent.py` one-liner.
3. Lint + full unit pass + self-review (token hygiene, no silent empty).

## Acceptance criteria coverage

- AC connect via MS OAuth → token seam + `microsoft` REQUIRED_CONNECTORS.
- AC triage works → tools run unchanged against `OutlookBackend` (test).
- AC alongside Gmail → selector test + untouched google path.
- AC test: success returns messages; no-access raises actionable error
  (401/403 ConnectorsError + AuthRequiredError grant path) — no silent
  empty list.

## Out of scope / risks

- Calendar (#1276) — not here. `OutlookCalendarBackend` is a sibling lead.
- Work/school (Entra) tenants — provider is `consumers`-only (#1105).
- Live OAuth recipe needs the same Azure app as #1105 + an Outlook.com
  account + interactive login (handoff).
