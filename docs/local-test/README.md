# Local end-to-end test — OAuth connections (issue #915)

This directory holds a recipe and a tiny agent for testing the
connections layer against a real Google account. **Not shipped to
production users** — this is a developer aid.

## Prerequisites

1. A Google Cloud project (personal or AMD-owned) with a **Desktop app**
   OAuth client. The full Cloud Console procedure is in
   [`../runbooks/google-oauth-client.md`](../runbooks/google-oauth-client.md).
2. The project's OAuth consent screen has your Google account on its
   test-user list (until the project is verified, only listed accounts
   can complete the flow).
3. The scopes `openid`, `https://www.googleapis.com/auth/userinfo.email`,
   and `https://www.googleapis.com/auth/gmail.readonly` are added to the
   consent screen.

## Recipe (~5 minutes)

```bash
# 1. Set the client id (no secret — PKCE).
export GAIA_GOOGLE_CLIENT_ID="<your-id>.apps.googleusercontent.com"

# 2. Install the test agent.
mkdir -p ~/.gaia/agents/oauth-test
cp docs/local-test/oauth-test-agent/agent.py ~/.gaia/agents/oauth-test/agent.py

# 3. Build the AgentUI frontend so the Settings page reflects this branch.
cd src/gaia/apps/webui && npm install && npm run build && cd -

# 4. Start the AgentUI.
gaia chat --ui
```

In the AgentUI:

5. Open Settings (gear icon) → scroll to **Connections** → click
   **Connect** next to Google. Your default browser opens. Pick your
   test-user account, click through the unverified-app warning if you
   see one, and grant the requested scopes.
6. Within ~2 seconds you should see "Connected as your-email@…" in the
   Settings page.
7. Switch the active agent to **"OAuth Test (Gmail)"** in the agent
   selector.
8. Send a message: `list 5 recent emails`.
9. The first time, the consent dialog appears: "Grant 'OAuth Test
   (Gmail)' read-only access to your Gmail inbox?" Click **Grant**.
10. The agent calls Gmail, the bearer token is fetched live, and the
    reply lists 5 subjects from your inbox.

## What this test validates

- ✅ Settings → Connections renders, Connect button works.
- ✅ OAuth PKCE flow completes; refresh token lands in OS keychain.
- ✅ Loopback `127.0.0.1:<ephemeral>/callback` round-trips.
- ✅ SSE event `connection.connected` updates AgentUI in &lt;2s.
- ✅ `REQUIRED_CONNECTORS` declared by the custom agent surfaces in
  the consent dialog with plain-language scope text.
- ✅ Per-agent grant gates `get_access_token_sync` (first call without
  grant raises `AuthRequiredError(AGENT_NOT_GRANTED)`).
- ✅ After grant, sync→async bridge fetches a real bearer token.
- ✅ Live Gmail API call succeeds.
- ✅ Disconnect from Settings → Connections clears the keyring entry
  and the chip flips to "Not connected" within 2s.
- ✅ Restart AgentUI: connection persists (refresh token is in keychain).

## Cleanup

```bash
gaia connectors disconnect google
gaia connectors grants revoke google "custom:<sha-prefix>:oauth-test"
rm -rf ~/.gaia/agents/oauth-test/
```

Or, from Settings → Connections in AgentUI:
- Click **Disconnect** next to Google.
- Click **Revoke** next to the OAuth Test agent under per-agent grants.
- Optionally remove the test agent in Settings → Custom Agents.

## CLI smoke test (no AgentUI)

The same primitives work without the UI:

```bash
# Connect — opens system browser exactly like the UI does.
gaia connectors connect google \
    --scopes https://www.googleapis.com/auth/gmail.readonly

# Show what's connected.
gaia connectors status

# Grant the test agent.
# (the namespaced id is printed by registry on agent load — look for
#  "Registered Python agent: oauth-test" in the AgentUI server log,
#  or use the SDK to compute it: from gaia.agents.registry import
#  _compute_custom_origin_hash; ":".join(["custom",
#  _compute_custom_origin_hash(Path.home() / ".gaia/agents/oauth-test/agent.py"),
#  "oauth-test"]).
gaia connectors grants grant google custom:<sha-prefix>:oauth-test \
    --scopes https://www.googleapis.com/auth/gmail.readonly

# Revoke from the same surface.
gaia connectors grants revoke google custom:<sha-prefix>:oauth-test
gaia connectors disconnect google
```

## Troubleshooting

- **"Connect" does nothing**: open `GET /api/connections/_debug`
  (set `GAIA_DEBUG=1` first). The response names every common cause
  (missing env var, wrong keyring backend, grants path not writable).
- **"Insecure keyring backend"**: install `gnome-keyring` (Linux) and
  start a session with `dbus-run-session`. macOS/Windows are fine
  out of the box.
- **"unverified app" warning in browser**: expected for personal
  Cloud projects. Click "Advanced → Continue to <app>" once.
- **403 from Gmail**: scope mismatch. Disconnect, reconnect passing
  `--scopes` followed by `https://www.googleapis.com/auth/gmail.readonly`
  (the test agent's required scope).
