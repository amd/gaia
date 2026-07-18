# Google OAuth Client — Runbook

**Owner:** GAIA team (file an issue → @kovtcharov-amd for changes).
**Audience:** GAIA core maintainers and CI operators.

This runbook documents how the Google OAuth client used by
`gaia.connectors` is created, rotated, and consumed. It is **not**
user-facing — end users never need to know the `client_id`.

> **Scope:** this runbook covers the **AMD-managed** client (the shared
> `GAIA_GOOGLE_CLIENT_ID`) and its rotation. If you are an end user
> bringing **your own** OAuth client for a personal Google account, follow
> the user-facing walkthrough at
> [`docs/connectors/google.mdx`](../connectors/google.mdx) instead — it
> covers the same console steps from the BYO-client angle.
>
> **End state:** [#2104](https://github.com/amd/gaia/issues/2104) ships a
> verified AMD-managed client so end users skip the Cloud Console entirely
> and just click **Connect**. Until it lands, both this runbook (AMD
> client) and the user guide (BYO client) apply.

## What this client is

A "Desktop app" OAuth 2.0 client registered in a Google Cloud project owned
by AMD. PKCE protects the authorization code flow, but Google's token
endpoint still requires the `client_secret` to be present in every exchange
even for Desktop-app clients — so both the client id **and** secret must be
configured. Tokens are stored in the user's OS keychain by
`gaia.connectors.store`; nothing about the client travels with the user's data.

## Configuration

Set the environment variables before any GAIA process starts:

```bash
export GAIA_GOOGLE_CLIENT_ID="<numeric-id>.apps.googleusercontent.com"
export GAIA_GOOGLE_CLIENT_SECRET="<client-secret>"
```

The connections layer reads these at first use (`gaia.connectors.providers.get("google")`).
Missing → the layer raises `ConfigurationError`; the AgentUI surfaces a
503 on `/api/connections/*`, but the rest of the AgentUI keeps working
(per plan amendment A3).

For development against personal Google accounts, register your own
desktop client in Google Cloud Console and set the env var to its id.
Do NOT commit the id into the repository. (Personal-account users should
follow [`docs/connectors/google.mdx`](../connectors/google.mdx), which
covers the same steps from the BYO-client angle.)

## Cloud Console setup

> **Console layout changed (2024–2025).** Google moved the OAuth settings
> that used to live under **APIs & Services → OAuth consent screen** and
> **→ Credentials** into the **Google Auth Platform**, split across three
> tabs: **Branding**, **Audience**, and **Clients**. The steps below use
> the new layout with direct links; old menu paths are noted as fallbacks.

1. Create a new project (or use an existing AMD-owned one) and make sure it
   is selected in the project dropdown.
2. **Enable the required APIs — mandatory.** GAIA can only call APIs
   explicitly enabled on the project; skip this and the OAuth flow succeeds
   but the first mailbox call returns a raw Google `403`
   ([#2116](https://github.com/amd/gaia/issues/2116)). Enable each API you
   support, then wait 1–2 minutes for propagation:
   - [Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com)
   - [Google Calendar API](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com)
   - [Google Drive API](https://console.cloud.google.com/apis/library/drive.googleapis.com)
3. **Google Auth Platform → Branding + Audience** (old path:
   **APIs & Services → OAuth consent screen**):
   - **Audience → User type**: Internal (AMD Workspace org only) or
     External (broader / personal accounts — a personal `@gmail.com` has
     no Internal option).
   - **Branding**: app name, support email, developer contact.
   - For "External" + sensitive scopes, submit for verification (4–6 wk).
     The scopes GAIA supports (`gmail.readonly`, `gmail.send`,
     `gmail.modify`, `calendar.readonly`, `calendar.events`,
     `drive.readonly`, …) are declared in
     [`src/gaia/connectors/catalog/google.py`](../../src/gaia/connectors/catalog/google.py).
   - **Audience → Test users**: while the app is in Testing status, only
     listed test users can authorize — add internal QA accounts here.
4. **Google Auth Platform → Clients** (old path:
   **APIs & Services → Credentials**) → **Create client**:
   - Application type: **Desktop app**.
   - Name: `GAIA Desktop` (or similar).
5. Copy the resulting client ID. Google issues a client secret alongside
   it; the Desktop-app PKCE flow still requires the secret to be present in
   every token exchange, so store both.

## Rotation procedure

Rotation is **expected to invalidate every existing user's stored
refresh token** because the connections layer's `client_id_hash` tripwire
detects the mismatch and clears entries on next read.

1. Create a new desktop client in Cloud Console (don't delete the old one yet).
2. Update `GAIA_GOOGLE_CLIENT_ID` everywhere (CI secrets, environment
   files, internal docs).
3. Restart all GAIA processes. The lifespan tripwire sweep clears
   stored entries that were bound to the old `client_id_hash`.
4. Users see a "Reconnect" prompt in AgentUI Settings → Connections (or
   `gaia connectors connect google` from the CLI). They re-authorize.
5. Once all known users have reconnected (or after the soak window),
   delete the old client in Cloud Console.

What breaks during rotation:
- Active access tokens issued under the old `client_id` continue to work
  until they expire (~1 hour).
- Refresh tokens issued under the old `client_id` are rejected by Google
  with `invalid_grant`. The user reconnects; nothing else fails.
- Stored connection metadata (account email, scopes) is preserved at the
  keyring level until the tripwire fires; then it's cleared.

## Verification submission

Sensitive scopes (`gmail.*`, `drive.*`, etc.) require Google's
verification before unverified users can authorize. Until then, only
test users listed on the consent screen can complete the OAuth flow.

- **In-Cloud-Console flow:** Google Auth Platform → **Audience** →
  "Publish app" (old path: OAuth consent screen → "PUBLISH APP") → follow
  the form. Provide a privacy policy URL, demo video, and scope
  justification.
- **Timeline:** 4–6 weeks typical.
- **Until verified:** add internal QA accounts as test users so they
  can complete the flow.
- **Test-mode token expiry:** while the app stays in Testing status,
  Google expires each test user's refresh token roughly **7 days** after
  consent, so testers must periodically reconnect. This is a Google policy
  for unverified test apps, not a GAIA bug.

## Local development without a published client

For day-to-day development:
1. Create a personal/test Google Cloud project.
2. Enable the Gmail / Calendar / Drive APIs you'll exercise (mandatory —
   see [Cloud Console setup](#cloud-console-setup)).
3. Add your own Google account as a test user (Google Auth Platform →
   Audience → Test users).
4. Use that project's desktop client id + secret in
   `GAIA_GOOGLE_CLIENT_ID` / `GAIA_GOOGLE_CLIENT_SECRET`.
5. The "Google hasn't verified this app" warning appears during
   authorization; click **Advanced → Continue** to proceed. Expect to
   reconnect about weekly while in Testing mode (7-day token expiry).

## Diagnostics

Trouble: "Connect button does nothing in AgentUI."

1. With `GAIA_DEBUG=1`, hit `GET /api/connections/_debug` - returns
   provider registration state, env-var presence, keyring backend,
   grants-path writability, and in-flight flow count.
2. Check the AgentUI server log for "connections: tripwire sweep complete"
   — confirms lifespan fired.
3. If the loopback callback timed out: try a different port (the loopback
   uses an ephemeral port - `127.0.0.1:0` - so this is rare; firewall
   misconfig is the usual culprit).

## Security boundaries

- Refresh tokens NEVER cross the public Python API or the FastAPI router.
- The keyring backend allowlist (`PlaintextKeyring`/`EncryptedKeyring`
  refused) prevents silent fallback to plaintext file storage on Linux
  without SecretService.
- The `client_id_hash` is sha256 of the client id, NOT the client id
  itself; it can be logged at INFO without leaking the client id.
- The OAuth `state` parameter is a per-flow random nonce compared via
  `hmac.compare_digest`; mismatched callbacks return 400.
