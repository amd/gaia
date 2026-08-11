# Testing the email-agent fix on Linux (#2347)

**Goal:** confirm the published email binary installs and *runs* on Linux, and
that the error / setup messages guide you when something is missing.

**Prereqs:** a Linux x86-64 box with `git` and Python 3.10+.

---

## 1. Get the branch

```bash
git clone https://github.com/amd/gaia.git
cd gaia
git checkout fix/sidecar-user-mode-error-2347
python -m venv .venv && source .venv/bin/activate
pip install -e .          # if this errors: pip install -e ".[dev]"
```

## 2. (Optional) See the error is now helpful, not a dead end

```bash
gaia email -q "Triage my inbox"
```

Expect a clear message telling you to run `gaia agent install email` (not the old
"reinstall the package" dead end). Note it, then continue.

## 3. Install the published binary from the hub

```bash
gaia agent install email
ls -lh ~/.gaia/agents/email/
```

Expect `✅ Installed 'email' v0.5.0 …`, a ~68 MB `email-agent` file, and a
`.installed` file.

## 4. ⭐ The key test — does it actually run?

```bash
gaia daemon start-agent email
gaia daemon status
```

**Success = the green line:**

```
✅ agent 'email' sidecar running (mode: user, pid: …, port: …, api: v2…)
```

That proves the Linux binary installed, launched, passed its health check, and
answered its version handshake — the part that couldn't be verified on Windows.

> **➡️ You can stop here.** Steps 1–4 need no Google keys and no Lemonade. If the
> green line appears, the fix is confirmed on Linux.

---

## 5. (Optional) Full triage — only if you want to exercise a real inbox

Needs a running Lemonade server **and your own Google OAuth client** (GAIA ships
none).

**5a. Create a Google OAuth client** (Google Cloud Console):

1. Create or pick a project at <https://console.cloud.google.com>
2. **Enable the Gmail API** for that project
3. Configure the **OAuth consent screen** (External; add yourself as a test user)
4. Create an **OAuth client ID** of type **Desktop app** — this gives you a
   Client ID and Client Secret

**5b. Register, connect, and grant — in one connect command:**

```bash
gaia connectors configure google --client-id <ID> --client-secret <SECRET>

SCOPES="https://www.googleapis.com/auth/gmail.modify \
https://www.googleapis.com/auth/gmail.send \
https://www.googleapis.com/auth/calendar.events \
https://www.googleapis.com/auth/calendar.readonly"
gaia connectors connect google --scopes $SCOPES --grant-agent installed:email

gaia email -q "Triage my inbox"
```

> **Headless note:** `connect` prints a URL and finishes via a `127.0.0.1`
> callback **on the machine running GAIA**. Open the URL in a browser on that
> same machine, or forward the callback port over SSH — a browser on another
> machine cannot reach the loopback and the flow will time out.

You don't have to memorize these commands: if you run `gaia email` before setting
up Google, the error prints these exact steps (the CLI is self-documenting).

---

## What to report back

- **Most important:** did step 4 print the green `✅ … sidecar running` line?
- The error text from step 2.
- Any failure (and which step it happened on).
