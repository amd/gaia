# Microsoft OAuth Client — Runbook

**Owner:** GAIA team (file an issue → @kovtcharov-amd for changes).
**Audience:** GAIA core maintainers and CI operators.

This runbook documents how the Microsoft OAuth client used by
`gaia.connectors` is created, rotated, and consumed. It is **not**
user-facing — end users never need to know the `client_id`.

## What this client is

A **public client** ("Mobile and desktop applications" platform) app
registration on the Microsoft identity platform v2.0, targeting the
`consumers` tenant (personal Microsoft accounts — Outlook.com / Hotmail /
Live.com). PKCE is used for the authorization code flow; **there is no
client secret** — PKCE replaces it. Tokens are stored in the user's OS
keychain by `gaia.connectors.store`; nothing about the client travels with
the user's data.

## Configuration

Set the environment variable before any GAIA process starts:

```bash
export GAIA_MICROSOFT_CLIENT_ID="<application-client-id-guid>"
```

The connectors layer reads this at first use
(`gaia.connectors.providers.get("microsoft")`). Missing → the layer raises
`ConfigurationError`; the AgentUI surfaces the connector as "not
configured" but the rest of the AgentUI keeps working.

For development against personal Microsoft accounts, register your own
public-client app in the Azure Portal and set the env var to its
Application (client) ID. Do NOT commit the id into the repository.

## Azure Portal setup

1. Visit <https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade>.
2. **New registration**:
   - Name: `GAIA Desktop` (or similar).
   - Supported account types: **Personal Microsoft accounts only**
     (matches the `consumers` tenant).
3. **Authentication → Add a platform → Mobile and desktop applications**:
   - Add the redirect URI **`http://127.0.0.1/callback`**. GAIA's loopback
     server sends this exact value; the port is dynamic and Microsoft
     ignores it for loopback, but scheme/host/path must match (the
     `/callback` path is required, so a bare `http://127.0.0.1` won't work).
     If the portal's redirect textbox rejects the `http://` + `127.0.0.1`
     combination, add it via the **Manifest** (Microsoft Graph format):
     `"publicClient": { "redirectUris": ["http://127.0.0.1/callback"] }`.
   - **Do not** add a client secret.
4. Copy the **Application (client) ID** from the app's **Overview** page.

> GAIA uses the `127.0.0.1` loopback (Microsoft's own recommendation over
> `localhost`, for IPv4/IPv6 reliability) for both Google and Microsoft.

## Tenant choice

This provider hard-codes `TENANT = "consumers"` in
[`src/gaia/connectors/providers/microsoft.py`](../../src/gaia/connectors/providers/microsoft.py).

- `consumers` — personal accounts only (the #1105 scope).
- `organizations` — Azure AD work/school accounts only.
- `common` — both, but trips conditional access on some tenants.

Switching audiences is a one-line change to the `TENANT` constant, but
enterprise (work/school) Graph access is tracked as separate P2 work
(#1280 / #1281) and should not be bundled into the personal-account path.

## Rotation procedure

Rotation is **expected to invalidate every existing user's stored refresh
token** because the connectors layer's `client_id_hash` tripwire detects
the mismatch and clears entries on next read.

1. Create a new app registration in the Azure Portal (don't delete the old
   one yet).
2. Update `GAIA_MICROSOFT_CLIENT_ID` everywhere (CI secrets, environment
   files, internal docs).
3. Restart all GAIA processes. The lifespan tripwire sweep clears stored
   entries that were bound to the old `client_id_hash`.
4. Users see a "Reconnect" prompt in AgentUI Settings → Connections (or
   `gaia connectors connect microsoft` from the CLI). They re-authorize.
5. Once all known users have reconnected, delete the old app registration.

What breaks during rotation:
- Active access tokens issued under the old `client_id` continue to work
  until they expire (~1 hour).
- Refresh tokens issued under the old `client_id` are rejected by Microsoft
  with `invalid_grant`. The user reconnects; nothing else fails.

## Security boundaries

- Refresh tokens NEVER cross the public Python API or the FastAPI router.
- There is no client secret to leak — the public-client PKCE flow does not
  use one.
- The `client_id_hash` is a **CRC32** fingerprint of the client id (NOT a
  cryptographic hash, and NOT the client id itself); it is used only for
  log correlation and the rotation tripwire, so it can be logged without
  leaking the client id.
- The OAuth `state` parameter is a per-flow random nonce compared via
  `hmac.compare_digest`; mismatched callbacks return 400.
- A refresh token is issued only because the `offline_access` scope is in
  the connector's `default_scopes`.

## Diagnostics

Trouble: "Connect button does nothing in AgentUI."

1. Confirm `GAIA_MICROSOFT_CLIENT_ID` is set (or credentials were saved via
   the Settings → Connections → Microsoft form).
2. Check the AgentUI server log for the connectors tripwire sweep on boot.
3. If the loopback callback timed out: the loopback binds `127.0.0.1` on an
   ephemeral port, so a firewall misconfiguration is the usual culprit.
