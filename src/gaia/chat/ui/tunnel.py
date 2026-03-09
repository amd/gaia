# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tunnel manager for mobile access to GAIA Chat UI.

Manages an ngrok tunnel to expose the local chat server for remote/mobile
access. Generates a UUID-based authentication token and provides QR code
data for easy mobile onboarding.
"""

import asyncio
import json
import logging
import platform
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class TunnelManager:
    """Manages an ngrok tunnel for mobile access.

    Spawns an ngrok process to create a public HTTPS URL pointing to the
    local GAIA Chat server. Generates a random UUID token for authentication.

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
            self._error = (
                "ngrok is not installed. Install it from https://ngrok.com/download "
                "or run: brew install ngrok (macOS) / choco install ngrok (Windows)"
            )
            logger.error(self._error)
            return self.get_status()

        # Fetch public IP (for ngrok interstitial password hint)
        await self._fetch_public_ip()

        # Kill any stale ngrok processes (free tier only allows 1)
        await self._kill_stale_ngrok()

        # Generate auth token
        self._token = str(uuid.uuid4())

        # Build ngrok command
        cmd = [ngrok_path, "http", str(self.port)]
        if self.domain:
            cmd = [ngrok_path, "http", "--domain", self.domain, str(self.port)]

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
                self._error = "Failed to get tunnel URL from ngrok"
                logger.error(self._error)
                await self.stop()

        except Exception as e:
            self._error = f"Failed to start ngrok: {e}"
            logger.error(self._error, exc_info=True)
            await self.stop()

        return self.get_status()

    async def stop(self) -> None:
        """Stop the ngrok tunnel."""
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
        """Kill any stale ngrok processes (free tier only allows 1 session)."""
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/f", "/im", "ngrok.exe"],
                    capture_output=True,
                    timeout=5,
                )
            else:
                subprocess.run(
                    ["pkill", "-f", "ngrok"],
                    capture_output=True,
                    timeout=5,
                )
            # Brief pause to let the process fully die
            await asyncio.sleep(0.5)
        except Exception:
            pass  # Ignore errors - there may be no stale process

    async def _fetch_public_ip(self) -> None:
        """Fetch the server's public IP (for ngrok interstitial password)."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get("https://api.ipify.org")
                if resp.status_code == 200:
                    self._public_ip = resp.text.strip()
                    logger.info("Public IP: %s", self._public_ip)
        except Exception as e:
            logger.debug("Could not fetch public IP: %s", e)
            self._public_ip = None

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
            The public HTTPS URL, or None if timed out.
        """
        elapsed = 0.0
        while elapsed < timeout:
            await asyncio.sleep(interval)
            elapsed += interval

            # Check if ngrok process died
            if self._process and self._process.poll() is not None:
                stderr = ""
                try:
                    stderr = self._process.stderr.read().decode(
                        "utf-8", errors="replace"
                    )
                except Exception:
                    pass
                logger.error("ngrok process exited unexpectedly: %s", stderr)
                self._error = (
                    f"ngrok exited: {stderr[:200]}"
                    if stderr
                    else "ngrok exited unexpectedly"
                )
                return None

            try:
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

        logger.error("Timed out waiting for ngrok tunnel (%.1fs)", timeout)
        return None
