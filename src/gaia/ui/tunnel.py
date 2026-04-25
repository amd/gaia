# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tunnel manager for mobile access to GAIA Agent UI.

Manages an ngrok tunnel to expose the local GAIA server for remote/mobile
access. Generates a UUID-based authentication token and provides QR code
data for easy mobile onboarding.
"""

import asyncio
import logging
import os
import platform
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Error helpers ──────────────────────────────────────────────────────────

_NGROK_INSTALL_HINT = (
    "ngrok is not installed. Install it from https://ngrok.com/download "
    "or run one of:\n"
    "    brew install ngrok                          # macOS\n"
    "    choco install ngrok                         # Windows\n"
    "    sudo snap install ngrok                     # Linux (snap)\n"
    "    curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc | "
    "sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null && "
    "echo 'deb https://ngrok-agent.s3.amazonaws.com buster main' | "
    "sudo tee /etc/apt/sources.list.d/ngrok.list && "
    "sudo apt update && sudo apt install ngrok       # Linux (apt)"
)

_NGROK_AUTHTOKEN_HINT = (
    "ngrok authtoken not configured. Sign up for a free account at "
    "https://dashboard.ngrok.com/signup, copy your authtoken from "
    "https://dashboard.ngrok.com/get-started/your-authtoken, then run:\n"
    "    ngrok config add-authtoken <YOUR_TOKEN>"
)

_NGROK_AUTHTOKEN_REJECTED_HINT = (
    "Your ngrok authtoken was rejected by ngrok's servers. It is usually "
    "correctly formatted but invalid -- this happens if you reset it, were "
    "removed from a team, or the credential was revoked. Re-copy a fresh "
    "authtoken from https://dashboard.ngrok.com/get-started/your-authtoken "
    "and run:\n    ngrok config add-authtoken <FRESH_TOKEN>"
)

_NGROK_SESSION_LIMIT_HINT = (
    "ngrok is already running elsewhere. Free ngrok plans allow only 1 "
    "active tunnel at a time. Stop any other ngrok processes (check your "
    "dashboard at https://dashboard.ngrok.com/agents) and try again."
)


def _ngrok_config_candidates() -> list:
    """All locations where ngrok might have stashed a YAML config.

    Different ngrok versions and OS combinations pick different default
    paths. We probe them all -- spurious extras are harmless.

    Observed locations:
    - macOS (docs):     ~/Library/Application Support/ngrok/ngrok.yml
    - macOS (ngrok 3+): ~/.config/ngrok/ngrok.yml  (actual behaviour,
                        honored by ngrok even though docs advertise the
                        Application Support path)
    - Linux:            $XDG_CONFIG_HOME/ngrok/ngrok.yml
                        (or ~/.config/ngrok/ngrok.yml as fallback)
    - Windows:          %LOCALAPPDATA%\\ngrok\\ngrok.yml
    - Legacy v2:        ~/.ngrok2/ngrok.yml
    """
    candidates = []

    # XDG / Linux default -- also used by ngrok 3.x on macOS in practice.
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    candidates.append(Path(xdg) / "ngrok" / "ngrok.yml")

    # macOS documented path
    if platform.system() == "Darwin":
        candidates.append(
            Path.home() / "Library" / "Application Support" / "ngrok" / "ngrok.yml"
        )

    # Windows
    if platform.system() == "Windows":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            candidates.append(Path(local_app) / "ngrok" / "ngrok.yml")

    # Legacy ngrok v2 path
    candidates.append(Path.home() / ".ngrok2" / "ngrok.yml")

    return candidates


def _check_ngrok_authtoken_configured() -> bool:
    """Best-effort preflight check for an ngrok authtoken.

    Checks (in order):
      1. ``$NGROK_AUTHTOKEN`` env var -- ngrok v3 honours this directly.
      2. Every known ngrok config path for a non-empty ``authtoken:``
         entry, matching both the flat ``authtoken: xxx`` form (v2) and the
         nested ``agent:\\n  authtoken: xxx`` form (v3 default).

    Returns True if a token appears to be configured, False otherwise.
    Used to surface a helpful error BEFORE spawning ngrok (which otherwise
    just hangs or emits cryptic errors).

    A pure-text scan is intentional -- the YAML files can contain comments,
    aliases, and other constructs that ``yaml.safe_load`` may choke on for
    legitimate ngrok configs (it's tolerated, but we don't want a parse
    error to silently disable preflight). False positives are far cheaper
    here than false negatives: the worst a false positive does is let
    ngrok run and emit its own (good) error message; a false negative
    blocks a working setup behind a misleading hint.
    """
    if (os.environ.get("NGROK_AUTHTOKEN") or "").strip():
        logger.debug("ngrok authtoken found via $NGROK_AUTHTOKEN")
        return True

    for p in _ngrok_config_candidates():
        try:
            if p.is_file():
                content = p.read_text(errors="ignore")
                # Look for a non-empty ``authtoken:`` entry anywhere in the
                # file. Matches both the v2 flat form and the v3 nested
                # ``agent:\n  authtoken: ...`` layout — indentation doesn't
                # matter once we're scanning line-by-line for the prefix.
                for line in content.splitlines():
                    s = line.strip()
                    if s.startswith("authtoken:"):
                        value = s[len("authtoken:") :].strip().strip("'\"")
                        if value:
                            logger.debug("ngrok authtoken found at %s", p)
                            return True
        except Exception as e:
            logger.debug("ngrok config probe failed for %s: %s", p, e)
            continue
    return False


def _parse_ngrok_error(stderr_text: str) -> str:
    """Translate ngrok stderr/stdout into a user-friendly error message.

    Detects the most common failure modes (missing authtoken, session
    limit reached, network issues) and returns instructions the user
    can act on.  Falls back to the first line of raw output if nothing
    matches.
    """
    text = (stderr_text or "").strip()
    if not text:
        return (
            "ngrok exited without output. Try running the command manually to "
            "see the error: ngrok http 4200"
        )

    low = text.lower()

    # ERR_NGROK_107: authtoken is well-formed but rejected (revoked,
    # reset, or belongs to a team the user was removed from). Distinct
    # from "missing / malformed" below -- the fix is different.
    if (
        "err_ngrok_107" in low
        or "properly formed, but it is invalid" in low
        or "credential was explicitly revoked" in low
        or "reset your authtoken" in low
    ):
        return _NGROK_AUTHTOKEN_REJECTED_HINT

    # ERR_NGROK_4018 or generic authtoken issues -- malformed or missing.
    if (
        "err_ngrok_4018" in low
        or "authtoken" in low
        or "authentication failed" in low
        or "account not authorized" in low
        or "not signed in" in low
    ):
        return _NGROK_AUTHTOKEN_HINT

    # Simultaneous session limit (ERR_NGROK_108).
    if (
        "err_ngrok_108" in low
        or "simultaneous ngrok" in low
        or "limited to 1 simultaneous" in low
    ):
        return _NGROK_SESSION_LIMIT_HINT

    # Local port conflict (4040 web interface or bind address in use).
    if "address already in use" in low or "bind: address already" in low:
        return (
            "ngrok's local port (4040) is already in use. Another ngrok "
            "process may still be running -- stop it and try again."
        )

    # Network / DNS problems. The "connection refused" branch is filtered to
    # the ngrok hostname so generic "connection refused" from a local service
    # doesn't get mis-attributed; the others (no such host / dial tcp / network
    # unreachable) are already specific enough on their own.
    if (
        "no such host" in low
        or "dial tcp" in low
        or "network is unreachable" in low
        or ("connection refused" in low and "tunnel.ngrok.com" in low)
    ):
        return (
            "Could not reach ngrok's servers. Check your internet connection "
            "(and any firewall/proxy blocking outbound HTTPS) and try again."
        )

    # TLS / certificate issues. ``x509`` alone is unambiguous (Go TLS errors
    # only). ``certificate`` is generic enough to appear in non-TLS contexts,
    # so it's only matched together with ``verify`` -- the canonical
    # ``failed to verify certificate`` shape.
    if "x509" in low or ("certificate" in low and "verify" in low):
        return (
            "ngrok could not establish a secure connection to its servers. "
            "Your system clock may be wrong, or a corporate proxy is "
            "intercepting TLS. Fix the clock / disable the proxy and retry."
        )

    # Fallback: first non-empty line, truncated.
    first_line = next((ln for ln in text.splitlines() if ln.strip()), text)
    return f"ngrok failed to start: {first_line[:300]}"


class TunnelManager:
    """Manages an ngrok tunnel for mobile access.

    Spawns an ngrok process to create a public HTTPS URL pointing to the
    local GAIA Agent UI server. Generates a random UUID token for authentication.

    Usage:
        manager = TunnelManager(port=4200)
        status = await manager.start()
        # status.url -> https://abc123.ngrok-free.app
        # status.token -> uuid string
        await manager.stop()
    """

    def __init__(self, port: int, domain: Optional[str] = None):
        """Initialize the tunnel manager.

        Args:
            port: Local server port to tunnel.
            domain: Optional custom ngrok domain (paid plan).
        """
        self.port = port
        self.domain = domain
        self._process: Optional[subprocess.Popen] = None
        self._url: Optional[str] = None
        self._token: Optional[str] = None
        self._started_at: Optional[str] = None
        self._error: Optional[str] = None
        self._public_ip: Optional[str] = None
        self._start_lock = asyncio.Lock()

    @property
    def active(self) -> bool:
        """Whether the tunnel is currently active."""
        return (
            self._process is not None
            and self._process.poll() is None
            and self._url is not None
        )

    def get_status(self) -> dict:
        """Get current tunnel status.

        Returns:
            Dict with tunnel status fields.
        """
        return {
            "active": self.active,
            "url": self._url if self.active else None,
            "token": self._token if self.active else None,
            "startedAt": self._started_at,
            "error": self._error,
            "publicIp": self._public_ip,
        }

    def validate_token(self, token: str) -> bool:
        """Validate a mobile access token.

        Args:
            token: Token string to validate.

        Returns:
            True if token matches the active tunnel's token.
        """
        if not self.active or not self._token:
            return False
        return token == self._token

    async def start(self) -> dict:
        """Start the ngrok tunnel.

        Returns:
            Tunnel status dict with url, token, etc.

        Raises:
            RuntimeError: If ngrok is not installed or tunnel fails to start.
        """
        async with self._start_lock:
            return await self._start_unlocked()

    async def _start_unlocked(self) -> dict:
        """Internal start implementation (caller must hold _start_lock)."""
        # Check if already running
        if self.active:
            logger.info("Tunnel already active at %s", self._url)
            return self.get_status()

        # Reset state
        self._error = None
        self._url = None

        # Check ngrok installation
        ngrok_path = self._find_ngrok()
        if not ngrok_path:
            self._error = _NGROK_INSTALL_HINT
            logger.error("ngrok not found on PATH")
            return self.get_status()

        # Preflight: is the ngrok authtoken configured? Catches the #1
        # first-run failure mode before we waste 15s waiting on a hung tunnel.
        if not _check_ngrok_authtoken_configured():
            self._error = _NGROK_AUTHTOKEN_HINT
            logger.error("ngrok authtoken not configured -- aborting tunnel start")
            return self.get_status()

        # Fetch public IP (for ngrok interstitial password hint)
        await self._fetch_public_ip()

        # Kill any stale ngrok processes (free tier only allows 1)
        await self._kill_stale_ngrok()

        # Generate auth token
        self._token = str(uuid.uuid4())

        # Build ngrok command. --log=stdout --log-format=logfmt makes
        # ngrok emit structured logs to stdout/stderr so we can surface
        # meaningful errors instead of staring at a hung process.
        base_args = [
            "http",
            "--log=stdout",
            "--log-format=logfmt",
        ]
        if self.domain:
            base_args += ["--domain", self.domain]
        cmd = [ngrok_path, *base_args, str(self.port)]

        logger.info("Starting ngrok: %s", " ".join(cmd))

        try:
            # Spawn ngrok process
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
            )

            # Poll ngrok's local API to get the tunnel URL
            self._url = await self._poll_ngrok_api()

            if self._url:
                self._started_at = datetime.now(timezone.utc).isoformat()
                self._error = None
                logger.info(
                    "Tunnel started: %s (token: %s...)", self._url, self._token[:8]
                )
            else:
                # _poll_ngrok_api already sets self._error with a friendly
                # message; keep a sensible fallback if it somehow didn't.
                if not self._error:
                    self._error = (
                        "ngrok did not open a tunnel within 15 seconds. "
                        "Check your internet connection and authtoken, then retry."
                    )
                logger.error("Tunnel start failed: %s", self._error)
                # Preserve the diagnostic error across cleanup -- stop()
                # clears _error by design (for user-initiated stops), so we
                # save + restore it here so the API caller actually sees
                # what went wrong.
                saved_error = self._error
                await self.stop()
                self._error = saved_error

        except Exception as e:
            self._error = f"Failed to start ngrok: {e}"
            logger.error(self._error, exc_info=True)
            saved_error = self._error
            await self.stop()
            self._error = saved_error

        return self.get_status()

    async def stop(self) -> None:
        """Stop the ngrok tunnel.

        Clears ``_url``, ``_started_at``, and ``_error`` by design -- a
        user-initiated stop should reset all transient state.  Callers
        that need to preserve a diagnostic ``_error`` across ``stop()``
        (e.g. on a failed start) must save + restore it themselves.
        """
        if self._process:
            logger.info("Stopping ngrok tunnel...")
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning("ngrok didn't terminate gracefully, killing...")
                    self._process.kill()
                    self._process.wait(timeout=3)
            except Exception as e:
                logger.warning("Error stopping ngrok: %s", e)
            finally:
                for pipe in (self._process.stdout, self._process.stderr):
                    if pipe:
                        try:
                            pipe.close()
                        except Exception:
                            pass
                self._process = None

        self._url = None
        self._started_at = None
        self._error = None
        logger.info("Tunnel stopped")

    def _find_ngrok(self) -> Optional[str]:
        """Find the ngrok executable in PATH.

        Returns:
            Path to ngrok binary, or None if not found.
        """
        # Try shutil.which first (cross-platform)
        path = shutil.which("ngrok")
        if path:
            return path

        # On Windows, also try .cmd extension
        if platform.system() == "Windows":
            path = shutil.which("ngrok.cmd")
            if path:
                return path

        return None

    async def _kill_stale_ngrok(self) -> None:
        """Kill any stale ngrok processes (free tier only allows 1 session).

        Uses exact-process-name matching (``pkill -x`` / ``taskkill /im``) on
        purpose: a broader ``pkill -f ngrok`` would match command lines like
        ``vim ngrok.md`` or ``python ngrok_client.py``, including the user's
        own unrelated work. Exact match still catches every legitimate
        ``ngrok`` agent process — the only thing the free-tier session-limit
        cleanup actually needs to clear.
        """
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/f", "/im", "ngrok.exe"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            else:
                subprocess.run(
                    ["pkill", "-x", "ngrok"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            # Brief pause to let the process fully die
            await asyncio.sleep(0.5)
        except Exception:
            pass  # Ignore errors - there may be no stale process

    async def _fetch_public_ip(self) -> None:
        """Fetch the server's public IP (for ngrok interstitial password)."""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get("https://api.ipify.org")
                if resp.status_code == 200:
                    self._public_ip = resp.text.strip()
                    logger.info("Public IP: %s", self._public_ip)
        except Exception as e:
            logger.debug("Could not fetch public IP: %s", e)
            self._public_ip = None

    def _drain_ngrok_output(self) -> str:
        """Best-effort drain of ngrok's stdout+stderr for error reporting.

        Called after ngrok has exited or been terminated.  Returns combined
        stdout+stderr text (truncated if excessively long).
        """
        combined = []
        for pipe_name in ("stdout", "stderr"):
            pipe = getattr(self._process, pipe_name, None) if self._process else None
            if pipe is None:
                continue
            try:
                # Since ngrok has exited (or we just killed it), read() won't
                # block -- all data is already in the kernel buffer.
                raw = pipe.read() or b""
                if raw:
                    combined.append(raw.decode("utf-8", errors="replace"))
            except Exception as e:
                logger.debug("Error draining ngrok %s: %s", pipe_name, e)
        text = "\n".join(combined).strip()
        # Truncate to keep logs manageable; friendly parser takes first line.
        return text[:4000]

    async def _poll_ngrok_api(
        self, timeout: float = 15.0, interval: float = 0.5
    ) -> Optional[str]:
        """Poll ngrok's local API to get the tunnel URL.

        ngrok exposes a local API at http://127.0.0.1:4040/api/tunnels
        that we can query to find the public HTTPS URL.

        Args:
            timeout: Maximum time to wait in seconds.
            interval: Polling interval in seconds.

        Returns:
            The public HTTPS URL, or None if timed out (self._error is set
            with a user-friendly message in all failure cases).
        """
        elapsed = 0.0
        while elapsed < timeout:
            await asyncio.sleep(interval)
            elapsed += interval

            # Check if ngrok process died
            if self._process and self._process.poll() is not None:
                stderr = self._drain_ngrok_output()
                logger.error(
                    "ngrok exited after %.1fs. Output:\n%s",
                    elapsed,
                    stderr or "(empty)",
                )
                self._error = _parse_ngrok_error(stderr)
                return None

            try:
                import httpx

                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.get("http://127.0.0.1:4040/api/tunnels")
                    if resp.status_code == 200:
                        data = resp.json()
                        tunnels = data.get("tunnels", [])
                        for tunnel in tunnels:
                            if tunnel.get("proto") == "https":
                                url = tunnel.get("public_url")
                                if url:
                                    return url
                        # If no HTTPS tunnel found yet, keep polling
            except Exception:
                # ngrok API not ready yet, keep polling
                pass

        # Timed out. ngrok is still running but didn't open an HTTPS tunnel.
        # Most likely cause: authtoken rejected by the server but the agent
        # is retrying silently.  Kill it, drain output, and surface a
        # friendly diagnosis.
        logger.error("Timed out waiting for ngrok tunnel (%.1fs)", timeout)
        stderr = ""
        try:
            if self._process and self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=2)
                stderr = self._drain_ngrok_output()
                logger.error("ngrok output on timeout:\n%s", stderr or "(empty)")
        except Exception as e:
            logger.debug("Error terminating timed-out ngrok: %s", e)

        if stderr:
            self._error = _parse_ngrok_error(stderr)
        else:
            self._error = (
                "ngrok started but didn't open a public tunnel within 15s. "
                "Common causes: authtoken rejected, network blocked, or "
                "ngrok servers unreachable. Run 'ngrok http 4200' manually "
                "to see the real error."
            )
        return None
