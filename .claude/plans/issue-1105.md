---
type: plan
source-issue: 1105
repo: amd/gaia
title: "Microsoft OAuth provider for MS Graph (consumers tenant, PKCE)"
created: 2026-06-02
status: complete
work_type: code-feature
complexity: standard
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 5
test_command: ".venv/bin/python -m pytest tests/unit/connectors/ -q"
build_command: "uv pip install -e .[dev]"
lint_command: "python util/lint.py --black --isort"
branch: tmi/issue-1105-ms-oauth-provider
reflection_iterations: 1
agents_used: [planning, execution, validation]
---

# Issue #1105 — Microsoft OAuth provider for MS Graph

## Goal
Add Microsoft as a second OAuth-PKCE provider alongside Google, scoped to the
`consumers` tenant (personal Outlook.com / Hotmail / Live accounts). This is the
FOUNDATION for #1275 (Outlook mailbox) and #1276 (Outlook calendar): the provider
+ catalog must be reusable so those later leads only add agent tools, not OAuth.

## Acceptance criteria
- `MicrosoftOAuthProvider` (MS identity platform v2.0, PKCE, tenant `consumers`).
- `catalog/microsoft.py` declaring Mail.Read, Mail.Send, Calendars.ReadWrite.
- Microsoft tile surfaces in Settings → Connectors (driven by REGISTRY.all()).
- Unit tests (mock all network): authorization-URL construction, token-exchange
  request shape, scope matching, tenant=`consumers`, refresh-body shape.

## Key protocol facts (verified against learn.microsoft.com 2026-01 revision)
- authorize: https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize
- token:     https://login.microsoftonline.com/consumers/oauth2/v2.0/token
- **Public/native PKCE client MUST NOT send client_secret** (differs from Google,
  which requires one). So client_secret is optional, env/keyring only — NOT a
  required setup field. token_request_body / refresh_request_body omit it unless
  one is explicitly configured (confidential-app edge case).
- refresh_token only returned if `offline_access` scope requested →
  `offline_access` MUST be in provider.default_scopes + catalog default/available
  so the SHARED flow.py (which hard-requires refresh_token) succeeds unmodified.
- id_token (account email) only returned if `openid` scope requested → include
  `openid` so flow._decode_email_from_id_token can populate account_email.
- response_mode=query is correct for the loopback /callback GET; set it
  explicitly via authorization_params() so MS never defaults to fragment.
- scopes are space-delimited (same join as Google).

## Lane boundary (parallel-lead collision avoidance)
- OWN (new): providers/microsoft.py, catalog/microsoft.py, docs/connectors/microsoft.mdx,
  tests/unit/connectors/test_microsoft_provider.py.
- OWN (edit): providers/__init__.py (lazy-register microsoft branch),
  catalog/__init__.py (import microsoft module + add to __all__).
- DO NOT TOUCH: connectors/cli.py, connectors/handler.py (#1084 owns CLI flags),
  providers/google.py, catalog/google.py (reference only), flow.py (shared).

## TDD steps
1. RED: write tests/unit/connectors/test_microsoft_provider.py asserting:
   endpoints (consumers tenant in both URLs), client_id read at instantiation
   (env GAIA_MICROSOFT_CLIENT_ID) not import, ConfigurationError naming the env
   vars when missing, lazy registry registration, Protocol compliance,
   client_id_hash CRC32, authorization_url has PKCE+state+response_type+
   response_mode=query, token_request_body has code/code_verifier/redirect_uri/
   grant_type and NO client_secret for public client, refresh_request_body shape,
   catalog declares the three required scopes + offline_access + openid, catalog
   default_scopes triggers refresh + id_token, no import side effects.
2. GREEN: implement providers/microsoft.py mirroring google.py (env var fallback
   GAIA_MICROSOFT_CLIENT_ID/_SECRET, keyring peek, no import side effects), add
   catalog/microsoft.py (ConnectorSpec id="microsoft", oauth_provider_ref="microsoft",
   setup form = Client ID only, secret optional), register lazy branch in
   providers/__init__.py, wire catalog/__init__.py, add docs/connectors/microsoft.mdx
   (docs_url test requires the mdx to exist for amd-gaia.ai URLs).
3. Refactor + full connectors suite green + lint.

## Integration seam for #1275 / #1276
- Both call the SAME generic surface they'd use for Google: the connector id is
  "microsoft"; agents declare REQUIRED_CONNECTORS=[ConnectorRequirement(
  connector_id="microsoft", scopes=[...])]; get a token via the oauth_pkce
  handler's get_credential (Authorization: Bearer <access_token> against
  https://graph.microsoft.com/v1.0/...). No Microsoft-specific code leaks into
  the agents — they only need the MS Graph scope URLs, all of which are in
  catalog available_scopes.

## Validation
- .venv/bin/python -m pytest tests/unit/connectors/ -q  (no live OAuth)
- .venv/bin/python util/lint.py --black --isort
- self-review diff for PKCE correctness + no secret logging.
