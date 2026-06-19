# Outlook via Power Automate — Enterprise Bypass Plan

> **Date:** 2026-05-04
>
> **Status:** Draft (0% implemented)
>
> **Priority:** High — unblocks Outlook integration on IT-managed machines
> where direct Azure AD app registration is not available
>
> **Related issues:** [#645](https://github.com/amd/gaia/issues/645) (Email
> Triage Agent), [#927](https://github.com/amd/gaia/issues/927) (Connectors
> framework), [#915](https://github.com/amd/gaia/issues/915) (OAuth PKCE for
> Google), [#660](https://github.com/amd/gaia/issues/660) (Email & Calendar
> via browser automation)
>
> **Related plans:** [Connectors Framework](connectors.mdx), [Email &
> Calendar](email-calendar-integration.mdx), [Email Triage
> Agent](email-triage-agent.mdx), [Security Model](security-model.mdx)
>
> **Scope:** A new `webhook` connector type and Power Automate flow templates
> that let GAIA read/write Outlook email and calendar on corporate machines
> where IT blocks custom Azure AD app registration. Ships alongside — not
> instead of — the planned native MS Graph OAuth connector.

---

## 1. Problem

The connectors framework (PR #926) ships with Google OAuth PKCE and an
`mcp-outlook` MCP server entry that requires `MS_CLIENT_ID` /
`MS_CLIENT_SECRET`. On a corporate IT-managed machine, these credentials are
unobtainable:

- **Azure AD app registration** requires tenant admin consent
  (or at minimum an App Registration policy that allows user-created apps).
- **Conditional Access policies** may block OAuth flows from non-approved
  client IDs even if the user can register an app.
- **The `outlook-mcp-server` npm package** is unverified and depends on the
  same blocked credentials.
- **Playwright browser automation** works but is fragile, slow, and breaks
  on Outlook Web UI changes.

This blocks every AMD-internal developer and most enterprise users from the
email triage agent's Outlook path.

---

## 2. Solution: Power Automate as a Relay

Power Automate is already IT-approved and licensed in most M365 E3/E5 tenants.
Its built-in Outlook connector runs under the user's existing M365 identity —
no custom Azure AD app needed.

**Architecture:**

```
GAIA Agent
    │
    ├─ HTTP POST (JSON) ──► Power Automate Cloud Flow
    │                           │
    │                           ├─ Built-in Outlook connector
    │                           │   (pre-approved by IT, runs as user)
    │                           │
    │                           ▼
    │                       Exchange / Outlook
    │
    ◄─ JSON response ──────┘
```

The GAIA agent calls Power Automate HTTP trigger URLs. Each flow wraps one
Outlook operation via the built-in connector. The flow runs under the user's
M365 session — Conditional Access, MFA, DLP, and audit logs all apply
normally. IT sees standard Power Automate traffic, not an unknown third-party
app.

### 2.1 Prerequisites

Power Automate is not universally available across all M365 SKUs. Before
setup, the user must confirm:

1. **License:** Power Automate for Microsoft 365 (included in E3/E5).
   The free plan does **not** include HTTP request triggers. M365 Business
   Basic and M365 Apps for Enterprise may not include it either.
2. **DLP policies:** The tenant's Power Platform DLP policies must allow the
   "HTTP" connector. Some enterprise tenants classify HTTP as a "blocked"
   connector to prevent data exfiltration. If IT has blocked HTTP connectors,
   flows will import but will not run.
3. **Environment access:** The user must be able to create flows in their
   default Power Platform environment (some tenants restrict flow creation
   to specific environments).

The setup guide (§7.2) includes specific error messages for each of these
prerequisites and guidance on requesting IT exceptions if needed.

---

## 3. Connector Type: `webhook`

The connectors framework (PR #926) ships two types: `oauth_pkce` and
`mcp_server`. This plan adds a third: `webhook`.

### 3.1 Type Definition

| Property | Value |
|----------|-------|
| **Type ID** | `webhook` |
| **Configures** | One or more named endpoint URLs via `config_schema` |
| **Stores** | Endpoint URLs in keyring (they are bearer tokens) |
| **`get_credential()` returns** | `{endpoints: {name: url}}` |
| **Handler class** | `WebhookHandler` |

### 3.2 Why a New Type (Not `api_token` or `mcp_server`)

- **Not `api_token`:** API token connectors store a single secret and pass
  it as a header. Webhook connectors store multiple named URLs that are the
  _endpoints themselves_, not auth headers.
- **Not `mcp_server`:** No stdio/SSE server process to manage. GAIA calls
  URLs directly via `aiohttp`.
- **Not `composite_form`:** That type is for multi-field credential bundles
  (Jira host + user + token). Webhook is simpler — just URL fields.

### 3.3 Spec Changes

`ConnectorType` in `spec.py` expands from
`Literal["oauth_pkce", "mcp_server"]` to
`Literal["oauth_pkce", "mcp_server", "webhook"]`.

**No new fields on `ConnectorSpec`.** The `WebhookHandler` derives endpoint
names from `config_schema` entries with `secret=True`. This follows the
framework's principle: type-specific fields on `ConnectorSpec` are reserved
for runtime parameters that `config_schema` cannot express (like
`mcp_command` for MCP servers). Webhook endpoint names are already expressed
by the `config_schema` keys — adding a `webhook_endpoints` field would be a
DRY violation.

### 3.4 Handler: `WebhookHandler`

```python
class WebhookHandler:
    """Handler for type='webhook' connectors."""

    async def configure(self, spec: ConnectorSpec, config: dict[str, Any]) -> dict[str, Any]:
        """Store endpoint URLs in keyring.

        Validates: HTTPS only, well-formed URL. Follows the McpServerHandler
        pattern of direct keyring.set_password() calls using the key pattern
        ``<connector_id>:<field_key>``.
        """
        verify_keyring_backend()
        for field in spec.config_schema:
            url = config.get(field.key)
            if url:
                _validate_url(url)  # HTTPS only, valid URL structure
                keyring.set_password(SERVICE_NAME, f"{spec.id}:{field.key}", url)
        return {"status": "configured"}

    async def get_credential(self, spec: ConnectorSpec, agent_id: str | None,
                             required_scopes: list[str] | None) -> dict[str, Any]:
        """Return {endpoints: {name: url}} from keyring.

        ``required_scopes`` is ignored for webhook connectors — grant checks
        use a fixed scope vocabulary (see §3.5).
        """
        endpoints = {}
        for field in spec.config_schema:
            if field.secret:
                val = keyring.get_password(SERVICE_NAME, f"{spec.id}:{field.key}")
                if val:
                    endpoints[field.key] = val
        return {"endpoints": endpoints}

    async def test(self, spec: ConnectorSpec) -> dict[str, Any]:
        """Test connectivity to read-only endpoints only.

        Sends ``{"_gaia_test": true}`` to each endpoint. The flow templates
        check this flag and return a safe echo response without performing
        any write operations. Write endpoints (e.g. ``send``) are skipped
        during testing to avoid side effects.
        """
        ...

    async def disconnect(self, spec: ConnectorSpec) -> None:
        """Remove all stored URLs from keyring. Idempotent."""
        for field in spec.config_schema:
            if field.secret:
                try:
                    keyring.delete_password(SERVICE_NAME, f"{spec.id}:{field.key}")
                except keyring.errors.PasswordDeleteError:
                    pass  # Already absent — idempotent
```

### 3.5 Scope Vocabulary and Per-Agent Grants

Webhook connectors define a fixed scope vocabulary so the existing
`grants.json` per-agent grant system works without special-casing:

| Scope | Gates endpoint | Description |
|-------|---------------|-------------|
| `email.read` | `inbox`, `read` | Read and summarize email |
| `email.send` | `send` | Send email (GAIA confirmation still required) |
| `calendar.read` | `calendar` | Read calendar events |

An agent declaring `REQUIRED_CONNECTIONS = [ConnectorRequirement("power-automate-outlook", scopes=["email.read"])]`
can only access the `inbox` and `read` endpoints. The `send` endpoint
requires an explicit `email.send` grant. `get_credential()` filters returned
endpoints to only those matching the granted scopes.

### 3.6 Security: Trigger URLs Are Bearer Tokens

Power Automate HTTP trigger URLs contain an embedded SAS token
(`sig=<base64>`). Anyone with the URL can invoke the flow. Therefore:

- **Store in keyring** — same as OAuth refresh tokens. Never in plaintext
  JSON. Follow the `McpServerHandler` pattern: direct `keyring.set_password()`
  calls with key pattern `<connector_id>:<endpoint_name>`.
- **`verify_keyring_backend()` on configure** — reject `PlaintextKeyring`
  and weak file-backed backends before storing URLs (same check as OAuth).
- **Per-agent grants** — same `grants.json` ledger gates access. An agent
  must have an explicit grant for `power-automate-outlook` before
  `get_credential()` returns the URLs.
- **HTTPS only** — `WebhookHandler.configure()` rejects non-HTTPS URLs
  via `urllib.parse.urlparse()` scheme check.
- **No URL logging** — handler must never log the full URL. All
  `aiohttp.ClientResponseError` exceptions must be wrapped to strip the
  query string before re-raising, since `str(e)` includes the full URL
  (and thus the SAS token). Module docstring documents this discipline.
- **SAS token expiry detection** — at `configure()` time, parse the `se`
  (signed expiry) query parameter from the SAS URL and store the expiry
  timestamp alongside the URL. `test()` warns if expiry is within 14 days.
  This prevents silent failure when the SAS token expires.

---

## 4. Catalog Entry: `power-automate-outlook`

```python
ConnectorSpec(
    id="power-automate-outlook",
    display_name="Outlook via Power Automate",
    icon="⚡",
    category="email",
    tier=2,
    type="webhook",
    description=(
        "Access Outlook email and calendar through Power Automate "
        "HTTP triggers. Works on IT-managed machines where direct "
        "Azure AD app registration is blocked."
    ),
    instructions_md=SETUP_INSTRUCTIONS,   # Step-by-step with screenshots
    docs_url="https://amd-gaia.ai/connectors/power-automate-outlook",
    config_schema=(
        ConfigField(
            key="inbox",
            label="Inbox Flow URL",
            kind="secret",
            secret=True,
            help_md="HTTP trigger URL from your **gaia-inbox** flow.",
        ),
        ConfigField(
            key="read",
            label="Read Email Flow URL",
            kind="secret",
            secret=True,
            required=False,
            help_md="HTTP trigger URL from your **gaia-read-email** flow.",
        ),
        ConfigField(
            key="send",
            label="Send Draft Flow URL",
            kind="secret",
            secret=True,
            required=False,
            help_md="HTTP trigger URL from your **gaia-send-draft** flow.",
        ),
        ConfigField(
            key="calendar",
            label="Calendar Flow URL",
            kind="secret",
            secret=True,
            required=False,
            help_md="HTTP trigger URL from your **gaia-calendar** flow.",
        ),
    ),
)
```

### 4.1 Coexistence with `mcp-outlook`

The catalog already has `mcp-outlook` (id `"mcp-outlook"`, type
`"mcp_server"`). Both connectors coexist:

- **`mcp-outlook`**: For users who *can* obtain `MS_CLIENT_ID` /
  `MS_CLIENT_SECRET` (personal M365 accounts, or IT-approved Azure AD apps).
  Add `help_md` note: "Requires Azure AD app registration. If your IT
  blocks this, use 'Outlook via Power Automate' instead."
- **`power-automate-outlook`**: For users on IT-managed machines. Add
  `help_md` note: "For IT-managed machines. If you have Azure AD app
  credentials, use 'Outlook / Microsoft 365' for lower latency."

Neither is deprecated. The email triage agent's source auto-discovery
(§6) selects the best available adapter.

---

## 5. Power Automate Flow Templates

Ship importable `.zip` flow packages and `.json` definitions in
`templates/power-automate/` (Power Automate supports solution
export/import). Each flow includes a `_gaia_test` flag check that
short-circuits on test requests without performing any write operations.

### 5.1 `gaia-inbox` — List Recent Emails

**Trigger:** HTTP POST `{"count": 20, "folder": "Inbox", "filter": "unread"}`

**Actions:**
1. Get emails (V3) — Outlook connector, `$top={count}`, folder, filter
2. Select — map to `{id, subject, from, receivedDateTime, preview, isRead}`
3. Response — return JSON array

**Response shape:**
```json
{
  "emails": [
    {
      "id": "AAMk...",
      "subject": "Q2 budget review",
      "from": "sarah@contoso.com",
      "receivedDateTime": "2026-05-04T09:15:00Z",
      "preview": "Hi, please review the attached...",
      "isRead": false
    }
  ],
  "_gaia_flow_version": 1
}
```

**Pagination note:** Exchange's "Get emails" action paginates internally.
If `count=100` but the connector returns only 50 (page 1), the flow returns
50 with no `hasMore` indicator. The adapter treats every response as
potentially partial. For v1 this is acceptable — the triage agent processes
whatever it receives. v2 may add cursor-based pagination.

### 5.2 `gaia-read-email` — Read Single Email

**Trigger:** HTTP POST `{"message_id": "AAMk..."}`

**Actions:**
1. Get email (V2) — by message ID
2. Response — `{subject, from, to, body, receivedDateTime, hasAttachments, _gaia_flow_version}`

### 5.3 `gaia-send-draft` — Send Email

**Trigger:** HTTP POST `{"to": "...", "subject": "...", "body": "...", "reply_to_id": null}`

**Actions:**
1. Check `_gaia_test` flag — if true, return echo response without sending
2. If `reply_to_id` → Reply to email (V3); else → Send email (V2)
3. Response — `{"status": "sent", "message_id": "AAMk..."}`

**Critical:** The email triage agent's send policy still applies — GAIA shows
a confirmation dialog before calling this flow. The flow itself sends
immediately on trigger (the `_gaia_test` guard only protects the `test()`
method, not production calls).

### 5.4 `gaia-calendar` — List Calendar Events

**Trigger:** HTTP POST `{"start": "2026-05-04T00:00:00Z", "end": "2026-05-05T00:00:00Z"}`

**Actions:**
1. Get events (V4) — Outlook connector, calendarView with start/end
2. Select — map to `{id, subject, start, end, location, organizer, isAllDay}`
3. Response — return JSON array with `_gaia_flow_version`

### 5.5 `gaia-echo` — Connection Test

**Trigger:** HTTP POST `{"_gaia_test": true}`

**Actions:**
1. Response — `{"status": "ok", "timestamp": "<utcnow>", "user": "<flow-owner-upn>"}`

Ships as a 5th template for connection validation. The `WebhookHandler.test()`
method calls this endpoint (if configured) as a lightweight health check
that does not consume Outlook connector API calls.

### 5.6 Flow Versioning

Each flow response includes a `_gaia_flow_version` integer field. The
adapter validates this on each response:
- If missing: warn "Your Power Automate flow may be outdated — reimport
  from templates/power-automate/"
- If less than expected: same warning with version details
- If equal or greater: no warning

This allows GAIA to detect outdated flow templates after a GAIA upgrade
changes the expected request/response schema.

---

## 6. Email Triage Agent Integration

The email triage agent (plan: [email-triage-agent.mdx](email-triage-agent.mdx))
uses a provider-agnostic `EmailSource` interface. Power Automate becomes a
third adapter alongside Gmail MCP and native Graph API:

```python
class PowerAutomateEmailSource:
    """EmailSource adapter backed by Power Automate webhook flows.

    All HTTP calls use an explicit 30-second timeout (Power Automate cold
    starts can take 5-10 seconds). No implicit retry — 5xx errors surface
    directly to the agent as actionable errors.

    SECURITY: aiohttp exceptions embed the full URL (including SAS token)
    in str(e). This class wraps all HTTP calls to strip query strings from
    error messages before re-raising.
    """

    TIMEOUT = aiohttp.ClientTimeout(total=30)

    def __init__(self, endpoints: dict[str, str]):
        self.inbox_url = endpoints["inbox"]
        self.read_url = endpoints.get("read")
        self.send_url = endpoints.get("send")
        self.calendar_url = endpoints.get("calendar")

    async def _post(self, url: str | None, payload: dict) -> dict:
        """POST JSON to a flow endpoint with URL-safe error handling."""
        if url is None:
            raise ConfigurationError(
                "This endpoint is not configured. Add its URL in "
                "Settings → Connectors → Outlook via Power Automate."
            )
        try:
            async with aiohttp.ClientSession(timeout=self.TIMEOUT) as session:
                resp = await session.post(url, json=payload)
                resp.raise_for_status()
                data = await resp.json()
                # Validate flow version
                version = data.get("_gaia_flow_version")
                if version is None:
                    logger.warning("Flow response missing _gaia_flow_version")
                return data
        except aiohttp.ClientError as e:
            # Strip query string (contains SAS token) from error message
            safe_msg = _strip_query_string(str(e))
            raise ConnectionError(safe_msg) from e

    async def list_emails(self, count=20, folder="Inbox", filter="unread"):
        data = await self._post(
            self.inbox_url,
            {"count": count, "folder": folder, "filter": filter},
        )
        return data.get("emails", [])

    async def read_email(self, message_id: str):
        return await self._post(self.read_url, {"message_id": message_id})

    async def send_email(self, to, subject, body, reply_to_id=None):
        return await self._post(
            self.send_url,
            {"to": to, "subject": subject, "body": body, "reply_to_id": reply_to_id},
        )

    async def list_events(self, start, end):
        data = await self._post(
            self.calendar_url,
            {"start": start, "end": end},
        )
        return data.get("events", [])
```

The triage agent picks the adapter based on which connector is configured:

| Connector | Adapter | Priority | Rationale |
|-----------|---------|----------|-----------|
| `google` (OAuth) | `GmailMCPEmailSource` | 1 (preferred) | Direct API, lowest latency |
| `microsoft` (OAuth, future) | `GraphAPIEmailSource` | 1 (preferred) | Direct API, lowest latency |
| `power-automate-outlook` (webhook) | `PowerAutomateEmailSource` | 2 | Reliable but higher latency |
| `mcp-outlook` (MCP server) | `MCPEmailSource` | 3 | Unverified npm package |

**Note:** This priority order differs from the [email-calendar
plan](email-calendar-integration.mdx) which positions MCP as the primary
Phase 1 path. For Outlook specifically, `mcp-outlook` is deprioritized
because the npm package is unverified and requires the same Azure AD
credentials that are blocked on IT machines. The MCP-first strategy holds
for Gmail (where `gmail-mcp-server` is verified and works).

---

## 7. Setup Experience

### 7.1 Onboarding in Agent UI

The ideal onboarding flow when a user has GAIA installed on their IT machine
and wants to connect to Outlook:

**Step 1 — Discovery (Settings → Connectors)**

User opens Settings → Connectors. Two Outlook tiles are visible:
- "Outlook / Microsoft 365" (MCP) — shows "Requires Azure AD credentials"
- "Outlook via Power Automate" (webhook) — shows "Works on IT-managed
  machines"

The Power Automate tile's `instructions_md` renders an inline setup wizard:

**Step 2 — Guided Setup (in-tile)**

```
┌─────────────────────────────────────────────────────┐
│  ⚡ Outlook via Power Automate                      │
│                                                     │
│  Access your Outlook inbox and calendar through     │
│  Power Automate. No Azure AD app registration       │
│  required.                                          │
│                                                     │
│  ┌─── Step 1: Import Flow Templates ─────────────┐  │
│  │                                                │  │
│  │  Download these 4 flow templates and import    │  │
│  │  them at flow.microsoft.com:                   │  │
│  │                                                │  │
│  │  📥 gaia-inbox.zip      (list emails)          │  │
│  │  📥 gaia-read-email.zip (read single email)    │  │
│  │  📥 gaia-send-draft.zip (send email)           │  │
│  │  📥 gaia-calendar.zip   (calendar events)      │  │
│  │                                                │  │
│  │  [Open Power Automate ↗]  [Download All ↓]    │  │
│  └────────────────────────────────────────────────┘  │
│                                                     │
│  ┌─── Step 2: Paste Trigger URLs ────────────────┐  │
│  │                                                │  │
│  │  Inbox Flow URL *                              │  │
│  │  ┌──────────────────────────────────────────┐  │  │
│  │  │ https://prod-XX.logic.azure.com/...      │  │  │
│  │  └──────────────────────────────────────────┘  │  │
│  │                                                │  │
│  │  Read Email Flow URL                           │  │
│  │  ┌──────────────────────────────────────────┐  │  │
│  │  │ (optional)                               │  │  │
│  │  └──────────────────────────────────────────┘  │  │
│  │                                                │  │
│  │  Send Draft Flow URL                           │  │
│  │  ┌──────────────────────────────────────────┐  │  │
│  │  │ (optional)                               │  │  │
│  │  └──────────────────────────────────────────┘  │  │
│  │                                                │  │
│  │  Calendar Flow URL                             │  │
│  │  ┌──────────────────────────────────────────┐  │  │
│  │  │ (optional)                               │  │  │
│  │  └──────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────┘  │
│                                                     │
│  [Test Connection]                     [Save]       │
│                                                     │
│  ┌─── Per-Agent Grants ──────────────────────────┐  │
│  │  Email Triage Agent          [●] email.read   │  │
│  │                              [ ] email.send   │  │
│  │                              [●] calendar.read│  │
│  └────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

**Step 3 — Test (1 click)**

User clicks "Test Connection." GAIA calls the `gaia-echo` endpoint (if
configured) and then the `inbox` endpoint with `{"_gaia_test": true, "count": 1}`.
Success shows: "Connected — 15 unread emails in your inbox."
Failure shows actionable errors:
- 401: "Trigger URL expired — regenerate in Power Automate"
- 429: "Rate limit hit — try again in a few minutes"
- SSL error: "Your network proxy may require configuration — see docs"
- Connection error: "Cannot reach Power Automate — check network"

**Step 4 — Grant + Go**

User toggles on the email triage agent with `email.read` scope. Closes
settings. Types "check my email" — it works.

### 7.2 CLI Flow

```bash
# List available connectors
gaia connectors list

# Configure (interactive paste-URL flow)
gaia connectors connect power-automate-outlook
  # Prompts: Inbox Flow URL: <paste>
  # Prompts: Read Email Flow URL (optional): <paste or enter to skip>
  # ...
  # Tests connection
  # Stores URLs in keyring

# Grant agent access
gaia connectors grants grant power-automate-outlook builtin:email-agent \
  --scopes email.read calendar.read

# Verify
gaia connectors test power-automate-outlook
```

### 7.3 Documentation

Ship a step-by-step guide with screenshots at
`docs/guides/power-automate-outlook.mdx`:

- Prerequisites (license, DLP policy, environment access)
- How to import a flow template (with screenshots)
- Where to find the HTTP trigger URL (with screenshots)
- How to test the connection in GAIA
- Troubleshooting (flow throttling, expired URLs, DLP blocks, proxy/SSL)
- Security considerations (who can see trigger URLs, how to rotate,
  run history retention)
- Warning: "Do not share your GAIA flows with other users — the trigger
  URL is a bearer token. Power Automate run history retains
  request/response bodies for 28 days, which includes email content."

---

## 8. Constraints and Tradeoffs

### 8.1 Tradeoffs vs. Native Graph API

| Dimension | Power Automate | Native MS Graph API |
|-----------|---------------|---------------------|
| IT approval | Already approved | Requires Azure AD admin |
| Setup effort | ~10 min (import flows + paste URLs) | ~0 (if IT pre-approves) |
| Latency | 2–5 s per call (cloud round-trip) | ~200 ms (direct API) |
| Rate limits | 6,000 calls/day (M365 E3/E5) | 10,000/10 min (Graph) |
| Maintenance | User owns flows (updates on Outlook connector changes) | GAIA owns code |
| Offline | No (cloud flows) | Yes (cached tokens) |
| Scope control | Per-flow (user decides what each flow can do) | Per-scope (OAuth consent) |
| Audit trail | Power Automate run history + M365 audit log | Azure AD sign-in logs |

### 8.2 Rate Limits

Power Automate per-user limits on M365 E3/E5:

| Limit | Value | Impact |
|-------|-------|--------|
| API calls per 24h | 6,000 | ~250/hour — enough for hourly triage of typical inbox |
| Concurrent runs | 300 | Not a concern for single-user agent |
| Flow run duration | 30 days (cloud) | Not a concern (our flows return in &lt;5 s) |
| HTTP request size | 100 MB | Not a concern (email metadata is small) |
| HTTP response size | 100 MB | Sufficient for all but absurd attachment downloads |

For scheduled triage (C2), the agent batches inbox reads to 1–2 calls per
cycle. A 15-minute triage schedule consumes ~200 calls/day — well within
limits.

### 8.3 Known Limitations

1. **Cloud dependency:** Flows run in Microsoft's cloud. If Power Automate
   is down, email integration is unavailable. No local fallback.
2. **URL rotation:** If the user regenerates a flow's trigger URL (or
   re-creates the flow), they must update the URL in GAIA Settings. SAS
   token expiry is parsed at configure time and warnings surface when
   expiry is within 14 days — but there is no automatic URL rotation.
3. **No push notifications:** Power Automate can't push to GAIA when new
   email arrives. The agent must poll. This is acceptable for scheduled
   triage but means no real-time "urgent email" alerts via this path.
4. **Single-user:** Each user sets up their own flows. No shared/delegated
   mailbox support in v1.
5. **Attachment handling:** Flows can return attachment metadata but
   downloading large attachments through Power Automate adds latency and
   counts against rate limits. Defer attachment download to v2.
6. **Pagination:** Exchange may paginate internally. If `count=100` but the
   Outlook connector returns only 50 (page 1), the flow returns 50 with
   no `hasMore` indicator. The adapter treats every response as potentially
   partial.
7. **Response schema brittleness:** The response schema depends on the
   Outlook connector action version (V2, V3, V4) pinned at flow import
   time and the user's "Select" step mapping. If a user modifies the flow,
   the adapter may receive unexpected field names. Response validation
   (§5.6, `_gaia_flow_version`) mitigates this.

---

## 9. Security Considerations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Trigger URL leaked | High — anyone can invoke the flow | Stored in OS keyring; HTTPS only; never logged; SAS query strings stripped from error messages |
| Flow invoked by unauthorized agent | Medium — prompt injection could extract URL | Per-agent grants with scope vocabulary gate `get_credential()`; URL not in tool schema |
| Flow sends email without user approval | High — bypasses GAIA's send confirmation | GAIA's send policy enforced in agent; `send_email()` not callable without confirmation gate |
| IT blocks outbound HTTP to Power Automate | Medium — corporate proxy/TLS inspection | Handler respects `HTTPS_PROXY`/`NO_PROXY` env vars; SSL errors produce actionable message pointing to proxy/CA configuration |
| Email content transits Microsoft cloud | Expected — Power Automate runs in M365 | Same as normal Outlook usage; no additional exposure vs. reading email in browser |
| Stale trigger URL (SAS expired) | Medium — silent failure | SAS `se` parameter parsed at configure time; `test()` warns if &lt;14 days remain; 401 on call surfaces actionable error |
| Prompt injection via email content | Medium — malicious email subjects in flow response | Not unique to this path (Gmail MCP has same risk); send-confirmation guardrail is the defense layer |
| Flow sharing exposes trigger URLs | Medium — co-owners see URLs | Setup guide warns: "Do not share flows. Run history retains request/response for 28 days." |

**Note on data locality:** Unlike the Gmail MCP path where email content is
processed entirely locally, the Power Automate path means email content
transits Microsoft's cloud (as it does for normal Outlook usage). The local
processing guarantee applies to the _LLM inference_ — GAIA still runs the
triage model locally via Lemonade. The email content is fetched from M365
(via Power Automate) and processed on-device.

**Proxy/TLS inspection handling:** On corporate machines, outbound HTTPS
often routes through a proxy that performs TLS inspection with a corporate
root CA. The `WebhookHandler` and `PowerAutomateEmailSource` must:
- Respect `HTTPS_PROXY` / `NO_PROXY` environment variables via `aiohttp`
- Catch `aiohttp.ClientConnectorSSLError` and produce: "Connection failed —
  if your organization uses a web proxy, set HTTPS_PROXY. If you see an SSL
  certificate error, your proxy may perform TLS inspection — add your
  corporate root CA to the system trust store."

---

## 10. Implementation Plan

### Phase 1: Webhook Connector Type + Power Automate Outlook (~3 days)

| Task | Effort | Output |
|------|--------|--------|
| Add `webhook` to `ConnectorType` literal + `_VALID_TYPES` | 0.5 h | `spec.py` update |
| `WebhookHandler` implementing `ConnectorHandler` Protocol | 4 h | `src/gaia/connectors/webhook.py` |
| Keyring storage: direct `keyring.set_password()` per McpServerHandler pattern | 1 h | No new `store.py` functions |
| SAS token expiry parsing at configure time | 1 h | In `WebhookHandler.configure()` |
| Catalog entry: `power-automate-outlook` | 1 h | `catalog/power_automate.py` |
| Cross-link `mcp-outlook` ↔ `power-automate-outlook` help text | 0.5 h | `catalog/mcp_servers.py` update |
| Register handler + catalog in `__init__` imports | 0.5 h | Wiring |
| Unit tests for `WebhookHandler` | 4 h | `tests/unit/connectors/test_webhook.py` |
| Power Automate flow templates (4+1 flows) | 4 h | `templates/power-automate/*.zip` + `.json` |
| Setup guide with screenshots | 3 h | `docs/guides/power-automate-outlook.mdx` |
| CLI: `gaia connectors connect power-automate-outlook` | 1 h | Paste-URL flow in CLI |
| Agent UI: reuse existing generic form rendering for `config_schema` | 0.5 h | Zero new React components |

**Total:** ~3 days

**Unit test coverage (minimum):**
- HTTPS-only validation (reject `http://`, empty, non-URL strings)
- Partial configuration (only `inbox` configured, others `None`)
- `disconnect()` idempotency (double-disconnect does not error)
- `test()` skips write endpoints (`send`)
- SAS token expiry parsing (valid `se` param, missing `se`, malformed URL)
- Keyring backend refusal (`PlaintextKeyring` rejected)
- `get_credential()` scope filtering (granted `email.read` → only
  `inbox`/`read` returned)
- Error message URL stripping (SAS token not in error strings)

### Phase 2: Email Triage Agent Adapter (~1 day)

| Task | Effort | Output |
|------|--------|--------|
| `PowerAutomateEmailSource` adapter class with URL-safe error handling | 3 h | `src/gaia/agents/email/sources/power_automate.py` |
| Response validation (check `_gaia_flow_version`, expected keys) | 1 h | In adapter class |
| Auto-discovery: pick adapter based on configured connector | 2 h | Source selection in agent init |
| Integration test with mocked flow responses | 2 h | `tests/integration/test_power_automate_source.py` |
| Integration test: mocked 401/404/429 responses | 1 h | Error handling coverage |

### Phase 3: Future Improvements

| Task | Depends On | Output |
|------|-----------|--------|
| URL health check on agent startup (warn if 401/404 or near-expiry) | Phase 1 | Proactive stale-URL detection |
| Power Automate Desktop (PAD) local adapter | PAD API stability | Fully local path, no cloud |
| Shared/delegated mailbox support | IT requirements | Multi-mailbox triage |
| Attachment download via separate flow | Rate limit analysis | Full email content |
| Rate limit tracking (calls/day counter) | User reports of 429s | Proactive limit warnings |

---

## 11. Alternatives Considered

### 11.1 Wait for IT to Approve Native MS Graph OAuth

**Rejected for now.** Getting a custom Azure AD app approved through IT can
take weeks to months. Power Automate is a day-one workaround that unblocks
development and dogfooding while the native path is pursued in parallel.
Both connectors coexist — users with IT-approved credentials use `microsoft`
(OAuth); users without use `power-automate-outlook` (webhook).

### 11.2 Power Automate Desktop (PAD) Instead of Cloud

**Deferred.** PAD runs locally and could interact with Outlook Desktop
directly, avoiding the cloud round-trip. But: PAD requires installation,
its local API is less stable, and the flows are more complex (UI automation
vs. API calls). Cloud flows are simpler for v1; PAD is a Phase 3 option if
latency or rate limits become issues.

### 11.3 Outlook COM API via `win32com.client`

**Rejected.** Explicitly rejected in the [email-calendar
plan](email-calendar-integration.mdx): "too fragile and Windows-only." COM
API is also blocked by some IT policies and breaks in RDP/Citrix sessions.

### 11.4 `exchangelib` (EWS)

**Not applicable.** Exchange Web Services (EWS) is deprecated by Microsoft
in favor of Graph API. Many M365 tenants have EWS disabled. Not a viable
path for new development.

### 11.5 Browser Automation (Playwright)

**Parallel path, not a replacement.** Playwright → outlook.office.com works
but is slower, fragile to UI changes, and requires a headed browser. The
`mcp-playwright` connector already supports this as a manual fallback. Power
Automate is more reliable for production use.

---

## 12. Open Questions

| # | Question | Options | Recommendation |
|---|----------|---------|----------------|
| 1 | Should `webhook` type be generic or Power Automate-specific? | Generic (reusable for Zapier, Make, n8n) vs. specific | **Generic** — the handler is just "call URL, get JSON". Power Automate is the first user but Zapier/Make work identically. |
| 2 | Flow template format: `.zip` (solution export) or JSON definition? | `.zip` (importable in Power Automate UI) vs. `.json` (copy-paste into flow designer) | **Both** — `.zip` for import, `.json` for reference/customization. |
| 3 | Should GAIA ship a "test" flow that just echoes the request? | Yes (easier setup validation) vs. no (clutters templates) | **Yes** — ship `gaia-echo` as a 5th template for connection testing. |
| 4 | Rate limit tracking: should GAIA track calls/day? | Yes (warn before hitting limit) vs. no (let Power Automate return 429) | **No for v1** — let 429s surface as actionable errors. Add tracking in v2 if users hit limits. |
| 5 | Should the webhook type support custom request headers? | Yes (future-proof for other webhook providers) vs. no (YAGNI for Power Automate) | **No for v1** — Power Automate triggers don't need custom headers. Add if a concrete connector needs it. |
| 6 | Should the webhook type validate URL host domains? | Yes (restrict to `*.logic.azure.com` for Power Automate) vs. no (generic type) | **No** — the type is generic. Connector-specific domain validation could be added per-catalog-entry if needed (e.g., optional `allowed_hosts` in a future `config_schema` extension). |
| 7 | Drive-by fix: `verify_keyring_backend()` in `McpServerHandler`? | Fix in same PR vs. separate PR | **Same PR** — small change, prevents `McpServerHandler` from silently storing secrets in plaintext keyring. |

---

## Appendix A: Review Findings Incorporated

This plan was reviewed by architecture-reviewer and code-reviewer agents.
Key findings incorporated:

1. **Dropped `webhook_endpoints` field** — endpoint names derived from
   `config_schema` entries instead of a redundant type-specific field on
   `ConnectorSpec`. (Architecture finding 1.1)
2. **Added scope vocabulary** — `email.read`, `email.send`, `calendar.read`
   scopes for per-agent grants. Without this, the grant system either
   blocks all access or grants unrestricted access. (Code review §1)
3. **Fixed `configure()` signature** — matches `ConnectorHandler` Protocol:
   `(spec, config)`, not `(spec, field_values, emitter)`. (Code review §1)
4. **Safe `test()` for write endpoints** — flows check `_gaia_test` flag;
   `test()` skips write endpoints entirely. (Code review §1)
5. **URL-safe error handling** — `aiohttp` exceptions embed full URLs; all
   errors wrapped to strip query strings before logging/raising. (Code
   review §4)
6. **Keyring storage follows McpServerHandler pattern** — direct
   `keyring.set_password()` with `<connector_id>:<field_key>` keys. No new
   `store.py` functions. (Code review §2)
7. **Added prerequisites section** — M365 licensing, DLP policies,
   environment access. (Architecture finding 6.1)
8. **Added proxy/TLS inspection handling** — `HTTPS_PROXY` support,
   actionable SSL error messages. (Architecture finding 6.2)
9. **Added SAS token expiry detection** — parse `se` parameter at configure
   time, warn at 14 days. (Architecture finding 3.1)
10. **Added flow versioning** — `_gaia_flow_version` in responses enables
    outdated flow detection. (Code review §6)
11. **Added prompt injection acknowledgment** — flow responses contain email
    content; same risk as Gmail MCP. (Architecture finding 3.3)
12. **Added flow sharing/governance warning** — trigger URLs are bearer
    tokens; run history retains email content for 28 days. (Architecture
    finding 6.3)
13. **Added pagination limitation** — Exchange may return partial results
    without `hasMore` indicator. (Code review §6)
14. **Cross-linked `mcp-outlook` ↔ `power-automate-outlook`** — both
    coexist with mutual help text. (Code review §3)
