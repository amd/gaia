# Issue #2347 — verified steps to unblock the headless email agent

> **Do not paste this file verbatim into the public issue.** It is an internal
> record of what was tested and the exact commands that worked, for a human to
> review before replying to the reporter.

## What the reporter hit

A headless Debian user installed the v0.22 `.deb` and ran:

```bash
gaia email -q "Triage my inbox"
```

and got:

```
... Email sidecar binary unavailable in user mode: cannot read binaries.lock.json
at /home/rre/.gaia/venv/lib/python3.12/hub/agents/email/npm/binaries.lock.json:
[Errno 2] No such file ... This manifest ships with the email agent package;
reinstall/rebuild it if it is missing. Set GAIA_EMAIL_AGENT_MODE=dev ...
```

## Root cause (confirmed)

User mode resolves the sidecar binary in two steps: (1) a Hub-installed binary
recorded by an `.installed` sentinel, then (2) a lazy fetch driven by
`binaries.lock.json`. On a fresh `.deb` install there is **no sentinel** and
**no lock** — the lock ships only in the agent's *npm* package, never in the
Python wheel, and `default_lock_path()` points at a venv path that does not
exist in an installed layout. So step 2 raises, and the old message told the
user to "reinstall/rebuild" the wheel — which can never create that file. That
misdirection is the bug this change fixes.

The published **0.5.0 binary is live on the hub** and installs via the hub
catalog (`manifest.json`), which is **independent of `binaries.lock.json`**.

## The fix (message-only, Option B)

The user-mode failure now names remedies that actually work:

1. Install the agent from the Agent Hub (Agent UI → Install), or headless via a
   one-line `installer.install(...)`.
2. `GAIA_EMAIL_AGENT_MODE=dev` from a source checkout.
3. Where to look next: the sidecar log dir and `docs/guides/email`.

It no longer claims that reinstalling the wheel helps.

## Verified on Windows (win32-x64), against the LIVE hub

All four legs of the chain were exercised end-to-end:

1. **`installer.install("email")` yields a runnable binary — not a mis-routed
   wheel.** The manifest's `versions.0.5.0.artifacts[]` are binary-like
   filenames, so the installer classifies the artifact as **binary** (`artifact_kind=binary`),
   writes `email-agent.exe` (~42 MB) under `~/.gaia/agents/email/`, verifies its
   SHA-256 against the manifest, and writes an `.installed` sentinel. `language`
   stays `python`, `hot_registered=False` (correct for a binary — nothing to
   import). **No wheel mis-routing bug exists in the current code.**
2. **User-mode fetch finds the sentinel with no lock present.**
   `fetch.fetch_binary(out_dir=~/.gaia/agents/email)` returned the installed
   `email-agent.exe` (v0.5.0) via `_hub_installed_binary` — the lock was never
   consulted.
3. **The sidecar boots.** Spawning `email-agent.exe --host 127.0.0.1 --port <free>`:
   - `GET /health` → `{"status":"ok","service":"gaia-agent-email"}`
   - `GET /version` → `{"apiVersion":"2.4","agentVersion":"0.5.0"}`
4. **Cold-state repro** (no sentinel, no lock) now shows the new actionable
   message instead of "reinstall the wheel".

Transport note: this box's corporate proxy breaks Python's `requests` TLS chain,
so the download used a `curl` fetcher injected into `installer.install`. This
swaps **only** the HTTP transport — the installer's real logic (artifact
classification, SHA-256 verification against the manifest, sentinel write) ran
unchanged on the real bytes.

## Still to confirm on the self-hosted Linux `stx` runner

The reporter is on Linux; this was verified on Windows. The **cold-state
linux-x64 fetch + spawn** needs the Linux runner:

- `installer.install("email")` selects `email-agent-linux-x64`, `chmod +x`, spawns.
- `GET /health` / `GET /version` on Linux.

The code path is identical to Windows apart from the artifact filename and the
POSIX `chmod +x` (Windows skips it). No Linux-specific logic differs.

The **triage / connector leg** (real Gmail + a running Lemonade) is Stage 2 —
the human tests it on macOS/Windows with a real Google connector; it is out of
scope for this chain verification.

---

## Short instructions to unblock #2347 today (draft for the reporter)

A headless machine has no Agent UI to click **Install**, so install the verified
binary from the hub directly, then start the sidecar:

```bash
# 1. Install the published, SHA-verified email binary from the Agent Hub.
#    Writes ~/.gaia/agents/email/email-agent + an .installed sentinel.
python -c "from gaia.hub import installer; installer.install('email')"

# 2. Start the sidecar under the daemon (finds the sentinel; no lock needed).
gaia daemon start-agent email

# 3. Confirm it's healthy.
gaia daemon status
```

Then the connector + triage steps (needs a Google connector and a running
Lemonade server):

```bash
gaia connectors            # add/authorize the Google connector for the email agent
gaia email -q "Triage my inbox"
```

Alternative for developers working from a source checkout:

```bash
uv pip install -e hub/agents/email/python      # once
GAIA_EMAIL_AGENT_MODE=dev gaia email -q "Triage my inbox"
```

> If `installer.install('email')` fails on the reporter's box, capture the full
> error — that would mean the short-term plan needs a manifest/installer change,
> not just the message fix. (It did **not** fail in Windows testing.)
