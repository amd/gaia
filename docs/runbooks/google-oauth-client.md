---
title: Google OAuth Client Setup
---

# Google OAuth Client

This runbook documents how to configure a Google OAuth client for GAIA's web integrations.

## Create OAuth credentials

1. Open the Google Cloud Console and create a new project or select an existing one.
2. Navigate to **APIs & Services → Credentials** and click **Create Credentials → OAuth client ID**.
3. Choose **Web application** and add the authorized redirect URI your deployment uses (e.g., `https://your-domain.example.com/oauth2/callback`).

> Note: Use the standard Markdown link format `[text](url)` for external links in MDX pages.

## Client ID and Secret

Copy the **Client ID** and **Client Secret** into your deployment's secrets store (do not commit secrets into source control).

## Scopes

Only request the scopes you need. Example:

```
openid email profile
```

## Troubleshooting

- If you receive an `invalid_request` error, verify the redirect URI exactly matches what you configured.
- If consent screen is not configured, set the OAuth consent screen in the Google Cloud Console.
