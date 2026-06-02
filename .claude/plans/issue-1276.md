---
type: plan
source-issue: 1276
repo: amd/gaia
title: "Outlook.com personal calendar connector (MS Graph calendar backend)"
created: 2026-06-02
status: in-progress
work_type: code-feature
complexity: standard
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 5
test_command: ".venv/bin/python -m pytest tests/unit/agents/email/ tests/unit/connectors/test_microsoft_provider.py -q"
build_command: "uv pip install -e .[dev]"
lint_command: "python util/lint.py --black --isort"
branch: tmi/issue-1276-outlook-calendar
reflection_iterations: 0
agents_used: [planning, execution, validation]
---

# Outlook.com personal calendar connector (#1276)

Stacked on #1275 (`tmi/issue-1275-outlook-mailbox`), which itself stacks on
#1105 (`tmi/issue-1105-ms-oauth-provider`). Consumes the `microsoft` connector
+ `MicrosoftOAuthProvider` (#1105) and mirrors the `LiveOutlookBackend` mail
pattern (#1275) for the calendar surface.

## Goal

Let the Email Triage Agent read the user's personal Outlook.com / Hotmail /
Live calendar via Microsoft OAuth (MS Graph `/me/calendarView` + `/me/events`),
alongside (not replacing) the shipped Google calendar connector.

## Acceptance criteria (from the issue)

- Reads the user's personal Outlook.com calendar via Microsoft OAuth (#1105) —
  MS Graph `/me/calendarView` / `/me/events`.
- Works alongside the shipped Google calendar connector (Google path untouched).
- Unit test (mocked OAuth + Graph HTTP): connect-success returns calendar
  events; a no-access / insufficient-scope case raises an actionable error,
  NOT a silent empty result.

## Architecture seam

The email agent's calendar tools (`calendar_tools.py`) call methods on a
`CalendarBackend` (Protocol in `calendar_backend.py`) and consume **Google
Calendar v3 shaped** dicts:

- `list_events(...)` -> `{"items": [{id, summary, start:{dateTime|date},
  end:{dateTime|date}, location, organizer:{email}}, ...]}`.
- `create_event(...)` -> dict with `.get("id")`.
- `update_event_rsvp(event_id, attendee_email, response_status)` where status
  is `accepted` / `declined` / `tentative` / `needsAction`.

So the Outlook calendar backend mirrors `LiveOutlookBackend`'s approach exactly:
**translate Microsoft Graph event JSON into the Google Calendar v3 shape** on
read, and map the Google RSVP verbs / create payload onto Graph mutations on
write. The tools then operate on Outlook and Google calendars interchangeably,
with zero tool changes.

### Graph -> Google event translation (read)

- Graph `event.start` / `event.end` are `dateTimeTimeZone` objects
  (`{dateTime, timeZone}`). Google uses `{dateTime}` (timed) or `{date}`
  (all-day). Map: `isAllDay==true` -> `{date: <YYYY-MM-DD>}`; else
  `{dateTime: <graph dateTime>}`. (Graph `dateTime` is a naive local string
  paired with `timeZone`; the tools only read the value for display, so a
  passthrough of `dateTime` preserves the wall-clock time.)
- `event.subject` -> `summary`; `event.location.displayName` -> `location`;
  `event.organizer.emailAddress.address` -> `organizer.email`; `event.id` ->
  `id`; `event.webLink` -> `htmlLink` (informational).
- Wrap the translated list as `{"items": [...]}` to match the Google envelope
  the tool reads (`data.get("items", [])`).

### Google -> Graph (write)

- `list_events(time_min, time_max)`: when BOTH bounds are present, use
  `GET /me/calendarView?startDateTime=&endDateTime=` (expands recurring series
  into instances within the window — the Outlook analogue of Google's
  `singleEvents=true`). When bounds are absent, fall back to `GET /me/events`
  ordered by start. `$top` = max_results.
- `create_event(...)`: `POST /me/events` with `{subject, start:{dateTime,
  timeZone:"UTC"}, end:{...}, location:{displayName}, body:{contentType:"text",
  content:description}, attendees:[{emailAddress:{address}, type:"required"}]}`.
  Google passes `start={"dateTime": iso}`; Graph needs a paired `timeZone`, so
  attach `"UTC"` when the caller did not provide one.
- `update_event_rsvp(...)`: Graph has no PATCH-attendees RSVP for the invitee.
  It uses action endpoints: `POST /me/events/{id}/accept` /
  `.../decline` / `.../tentativelyAccept` with body `{sendResponse: true}`.
  Map Google status -> endpoint: `accepted`->accept, `declined`->decline,
  `tentative`->tentativelyAccept, `needsAction` (no Graph analogue) -> raise an
  actionable `ConnectorsError` rather than silently no-op. `attendee_email` is
  unused on Graph (the action acts as the authenticated `/me`); kept in the
  signature for Protocol parity.

## Token + error contract (mirror `LiveOutlookBackend` exactly)

- `access_token_fn` invoked on EVERY request (cheap via connectors cache) so a
  mid-call revoke surfaces as a 401, not a stale-token success.
- Every non-2xx raises `ConnectorsError` built from status + truncated body
  ONLY — never from a wrapper exception that could leak the `Authorization:
  Bearer ...` header. 401 -> reconnect guidance; 403 -> insufficient
  Calendars scope guidance. A no-access / empty result is NEVER swallowed into
  an empty list.
- Module-level `_get_outlook_calendar_token()` requests the `microsoft`
  connector with `OUTLOOK_CALENDAR_SCOPES` (Calendars.ReadWrite); the grant
  dispatcher raises `AuthRequiredError` BEFORE any network call if the user has
  not granted -> propagates (never empty token / empty calendar).

## Backend selection (mirror `resolve_mail_backend`)

Add `calendar_provider: str = "google"` to `EmailAgentConfig` and a
`resolve_calendar_backend()` method analogous to `resolve_mail_backend`:

  1. Injected `calendar_backend` (eval/test seam) always wins.
  2. `google` -> `LiveCalendarBackend(_get_calendar_token)`.
  3. `microsoft` -> `LiveOutlookCalendarBackend(_get_outlook_calendar_token)`.
  4. Unknown -> `ConfigurationError` (fail loudly).

Default stays `google`, so the shipped Google calendar path is unchanged. The
agent's existing `calendar_backend=object()` injection in current tests keeps
winning (resolution short-circuits on the injected backend).

## Files (lane-bounded)

- NEW `src/gaia/agents/email/outlook_calendar_backend.py` — `LiveOutlookCalendarBackend`
  + `graph_event_to_google` translation + `_get_outlook_calendar_token`.
- EDIT `src/gaia/agents/email/outlook_scopes.py` — add `OUTLOOK_CALENDAR_SCOPES`
  (additive; mail scopes untouched).
- EDIT `src/gaia/agents/email/config.py` — `calendar_provider` field +
  `resolve_calendar_backend()` (additive; `resolve_mail_backend` untouched).
- EDIT `src/gaia/agents/email/agent.py` — calendar-backend wiring ONLY
  (`self._calendar = config.resolve_calendar_backend()`). Flag as collision-risk
  touch. The mail wiring from #1275 is left as-is.
- NEW `tests/unit/agents/email/test_outlook_calendar_backend.py` — behavioral
  tests (httpx MockTransport + mocked token seam).
- EDIT `tests/unit/agents/email/test_backend_selection.py` — add
  `resolve_calendar_backend` selection tests (additive class; existing classes
  untouched).

Out of lane (consume read-only, do NOT modify): `providers/microsoft.py`,
`catalog/microsoft.py` (#1105 — already declares Calendars scopes),
`outlook_backend.py` (#1275 — mirror only), `calendar_tools.py`,
`read_tools.py`, `reply_tools.py`, `api/`.

`calendar_backend.py`: NOT modified — its `CalendarBackend` Protocol already
expresses the full contract the Outlook backend satisfies structurally. No
shared base needed.

## TDD order

1. RED: write `test_outlook_calendar_backend.py` (Protocol conformance, read
   translation to Google shape, calendarView vs events endpoint choice, RSVP
   action endpoints, create payload, token freshness, 401/403 actionable raise
   + no token leak, token-resolver grant propagation) and the
   `resolve_calendar_backend` selection tests. Run -> fail (module missing).
2. GREEN: implement `outlook_calendar_backend.py`, `OUTLOOK_CALENDAR_SCOPES`,
   `resolve_calendar_backend`, agent wiring. Run -> pass.
3. Regression: full `tests/unit/agents/email/` + `test_microsoft_provider.py`
   green (Google calendar + mail paths unaffected).

## Validation

- `test_command` green.
- Self-review for high-confidence bugs/security: token hygiene (no Bearer leak
  in errors), no silent empty on 403/401, no cloud-LLM surface added.
- Lint: `python util/lint.py --black --isort`.

## Eval trigger

NOT an LLM-affecting surface — no prompt, tool schema, model, or error-
classification change in the agent loop. `gaia eval agent` NOT required.
