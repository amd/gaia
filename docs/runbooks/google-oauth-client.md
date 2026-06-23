# Google OAuth Client — Runbook

**Owner:** GAIA team (file an issue → @kovtcharov-amd for changes).
**Audience:** GAIA core maintainers and CI operators.

This runbook documents how the Google OAuth client used by
`gaia.connectors` is created, rotated, and consumed. It is **not**
user-facing — end users never need to know the `client_id`.

## What this client is

A "Desktop app" OAuth 2.0 client registered in a Google Cloud project owned
by AMD. PKCE is used for the authorization code flow (no client secret).
Tokens are stored in the user's OS keychain by `gaia.connectors.store`;
nothing about the client travels with the user's data.

## Configuration

Set the environment variable before any GAIA process starts:

```bash
export GAIA_GOOGLE_CLIENT_ID="<numeric-id>.apps.googleusercontent.com"
```

The connections layer reads this at first use (`gaia.connectors.providers.get("google")`).
Missing → the layer raises `ConfigurationError`; the AgentUI surfaces a
503 on `/api/connections/*`, but the rest of the AgentUI keeps working
(per plan amendment A3).

For development against personal Google accounts, register your own
desktop client in Google Cloud Console and set the env var to its id.
Do NOT commit the id into the repository.

## Cloud Console setup

1. Visit [https://console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials).
2. Create a new project (or use an existing AMD-owned one).
3. **APIs & Services → OAuth consent screen**:
   - User Type: Internal (AMD-only) or External (broader).
   - Add the scopes you intend to support: `gmail.readonly`,
     `gmail.send`, `calendar.readonly`, `drive.readonly`, etc.
   - For "External" + sensitive scopes, submit for verification (4–6 wk).
4. **Credentials → Create Credentials → OAuth client ID**:
   - Application type: **Desktop app**.
   - Name: `GAIA Desktop` (or similar).
5. Copy the resulting client ID. There is no client secret in the desktop
   flow — PKCE replaces it.

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

- **In-Cloud-Console flow:** OAuth consent screen → "PUBLISH APP" →
  follow the form. Provide a privacy policy URL, demo video, and
  scope justification.
- **Timeline:** 4–6 weeks typical.
- **Until verified:** add internal QA accounts as test users so they
  can complete the flow without seeing the "unverified app" warning.

## Local development without a published client

For day-to-day development:
1. Create a personal/test Google Cloud project.
2. Add your own Google account as a test user on the consent screen.
3. Use that project's desktop client id in `GAIA_GOOGLE_CLIENT_ID`.
4. The "unverified app" warning appears once per user; click "Continue"
   to proceed.

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
