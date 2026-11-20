# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Access from the home network: one object the routes, the gate and the
backend's lifespan share.

Nothing touches the disk until the PC turns the listener on, so a GAIA that
never used it keeps no device folder.
"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import os
import platform
import socket
import sqlite3
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from gaia.config import GaiaConfig, GaiaConfigError, devices_dir
from gaia.logger import get_logger

from . import (
    DEVICE_IDLE_DAYS,
    DEVICE_LISTENER_HOST,
    DEVICE_LISTENER_PORT,
    DEVICE_PORT_ENV,
    HOUSEKEEPING_SECONDS,
    OFFERED_NETWORKS,
    PAIRING_OFFER_VERSION,
    PC_NAME_MAX,
    PENDING_DEVICE_SECONDS,
)
from .listener import (
    DeviceListener,
    ListenerError,
    OpenRequests,
    local_ipv4_addresses,
)
from .pairing import PairingWindow, sanitize_device_name
from .storage import (
    TABLE_NAME,
    DeviceCertificateError,
    UnsafeDeviceFolderError,
    ensure_private_folder,
    load_identity,
    reset_folder,
    server_context,
)
from .store import Device, DeviceStore, read_pc_id

logger = get_logger(__name__)

_LISTENER_SETTING = "listener"


def iso(when: Optional[float]) -> Optional[str]:
    if when is None:
        return None
    return datetime.datetime.fromtimestamp(when, datetime.timezone.utc).isoformat(
        timespec="seconds"
    )


def pc_name() -> str:
    """The name a device shows for this PC."""
    for name in (platform.node(), socket.gethostname()):
        name = (name or "").strip()
        if name.lower().endswith(".local"):
            name = name[: -len(".local")]
        if name:
            return name[:PC_NAME_MAX]
    return "GAIA"


def configured_port() -> int:
    """The listener's port: ``GAIA_DEVICE_PORT``, else the default.

    Raises:
        ListenerError: The variable isn't a port number.
    """
    raw = os.environ.get(DEVICE_PORT_ENV, "").strip()
    if not raw:
        return DEVICE_LISTENER_PORT
    if not raw.isdigit() or not 1 <= int(raw) <= 65535:
        raise ListenerError(
            "start_failed",
            f"{DEVICE_PORT_ENV} is {raw!r}, which isn't a port number from 1 to "
            "65535. Fix or unset it, then restart GAIA.",
        )
    return int(raw)


def home_network_origins(host: str, port: int) -> List[str]:
    """``https://address:port`` for each home-network address this PC has."""
    bound = ipaddress.ip_address(host)
    if not bound.is_unspecified:
        return [f"https://{bound}:{port}"]
    offered = [ipaddress.ip_network(n) for n in OFFERED_NETWORKS]
    return [
        f"https://{address}:{port}"
        for address in map(ipaddress.ip_address, local_ipv4_addresses())
        if any(address in net for net in offered)
    ]


class PairingUnavailable(RuntimeError):
    """The PC can't show a pairing code; *code* is ``listener_off`` or ``no_home_network``."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class DeviceAccess:
    """Paired devices: their listener, certificate, tokens and pairing window."""

    def __init__(
        self,
        app,
        folder: Optional[Path] = None,
        host: str = DEVICE_LISTENER_HOST,
        port: Optional[int] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._app = app
        self._folder = folder
        self._host = host
        self._port = port
        self._clock = clock
        self.pairing = PairingWindow(clock=clock)
        self.requests = OpenRequests()
        self._store: Optional[DeviceStore] = None
        self._listener: Optional[DeviceListener] = None
        self._pin: Optional[str] = None
        self._error: Optional[Tuple[str, str]] = None
        self._housekeeping: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    @property
    def folder(self) -> Path:
        return self._folder or devices_dir()

    @property
    def listening(self) -> bool:
        return self._listener is not None and self._listener.running

    @property
    def requests_served(self) -> int:
        return self._listener.requests_served if self._listener else 0

    def _open_store(self) -> DeviceStore:
        if self._store is None:
            ensure_private_folder(self.folder)
            self._store = DeviceStore(self.folder / TABLE_NAME, clock=self._clock)
        return self._store

    def _existing_store(self) -> Optional[DeviceStore]:
        """The table if the PC ever turned the listener on; never creates it."""
        if self._store is None and not (self.folder / TABLE_NAME).exists():
            return None
        return self._open_store()

    # ── The listener ──────────────────────────────────────────────────────

    async def start(self) -> None:
        """Turn the listener on, making the key and certificate the first time.

        Raises:
            ListenerError: It can't start, or ``device_pairing`` is off; the
                status says why too.
        """
        try:
            enabled = GaiaConfig.load().device_pairing
        except GaiaConfigError as e:
            raise self._failed(ListenerError("start_failed", str(e))) from e
        if not enabled:
            raise ListenerError(
                "start_failed",
                'Device pairing is off. Set "device_pairing": true in '
                "~/.gaia/config.json to try it.",
            )
        async with self._lock:
            await self._start()

    async def _start(self) -> None:
        if self.listening:
            return
        self._error = None
        try:
            identity = load_identity(self.folder)
            store = self._open_store()
            context = server_context(identity)
            port = self._port if self._port is not None else configured_port()
            listener = DeviceListener(self._app, self, self._host, port)
            await listener.start(context)
        except UnsafeDeviceFolderError as e:
            raise self._failed(ListenerError("unsafe_folder", str(e))) from e
        except DeviceCertificateError as e:
            raise self._failed(ListenerError("certificate", str(e))) from e
        except ListenerError as e:
            raise self._failed(e) from e
        self._listener, self._pin = listener, identity.pin
        store.set_setting(_LISTENER_SETTING, "on")
        self._housekeeping = asyncio.create_task(self._housekeep())

    def _failed(self, error: ListenerError) -> ListenerError:
        self._error = (error.code, error.detail)
        logger.error("Device listener not started (%s): %s", error.code, error.detail)
        return error

    async def stop(self, *, remember: bool = True) -> None:
        """Turn the listener off; *remember* keeps it off across restarts."""
        async with self._lock:
            await self._stop(remember=remember)

    async def _stop(self, *, remember: bool) -> None:
        housekeeping, self._housekeeping = self._housekeeping, None
        if housekeeping is not None and housekeeping is not asyncio.current_task():
            housekeeping.cancel()
        self.pairing.close()
        if self._listener is not None:
            await self._listener.stop()
            self._listener = None
        if remember and self._store is not None:
            self._store.set_setting(_LISTENER_SETTING, "off")

    async def resume(self) -> None:
        """At startup: turn the listener back on if the PC left it on."""
        try:
            if not GaiaConfig.load().device_pairing:
                return
        except GaiaConfigError as e:
            logger.error("Device listener not resumed: %s", e)
            return
        try:
            store = self._existing_store()
        except UnsafeDeviceFolderError as e:
            self._failed(ListenerError("unsafe_folder", str(e)))
            return
        if store is None or store.setting(_LISTENER_SETTING) != "on":
            return
        try:
            await self.start()
        except ListenerError as e:
            logger.error("Device listener not resumed (%s): %s", e.code, e.detail)

    async def shutdown(self) -> None:
        """When the backend stops: stop serving, keep the PC's choice."""
        await self.stop(remember=False)
        if self._store is not None:
            self._store.close()
            self._store = None

    async def _housekeep(self) -> None:
        while True:
            await asyncio.sleep(HOUSEKEEPING_SECONDS)
            try:
                still_on = GaiaConfig.load().device_pairing
            except GaiaConfigError as e:
                logger.error("Stopping the device listener: %s", e)
                still_on = False
            if not still_on:
                logger.warning("device_pairing is off; stopping the device listener")
                await self.stop(remember=False)
                return
            store = self._store
            if store is None:
                continue
            try:
                expired = await asyncio.to_thread(store.expire)
            except sqlite3.Error as e:
                logger.error("Expiring idle devices failed (%s); trying again later", e)
                continue
            for device_id in expired:
                logger.info("Device %s expired and was unpaired", device_id)
                self.requests.end(device_id)

    # ── What the PC sees ──────────────────────────────────────────────────

    def status(self) -> dict:
        port = self._listener.port if self.listening and self._listener else None
        if port is None:
            try:
                port = self._port if self._port is not None else configured_port()
            except ListenerError:
                port = DEVICE_LISTENER_PORT
        code, detail = self._error or (None, None)
        expires = self.pairing.expires_at()
        return {
            "listening": self.listening,
            "port": port,
            "error_code": code,
            "error_detail": detail,
            "pairing_open": expires is not None,
            "pairing_expires_at": iso(expires),
            "idle_expiry_days": DEVICE_IDLE_DAYS,
            "pending_expiry_minutes": PENDING_DEVICE_SECONDS // 60,
            "failed_redemptions": self.pairing.failures(),
        }

    def devices(self) -> List[Device]:
        store = self._existing_store()
        return store.devices() if store else []

    def now(self) -> float:
        return self._clock()

    def describe_id(self, device_id: str) -> Optional[dict]:
        store = self._existing_store()
        device = store.get(device_id) if store else None
        return self.describe(device) if device else None

    @staticmethod
    def describe(device: Device) -> dict:
        """A device as the PC lists it: no token, no hash."""
        return {
            "id": device.id,
            "name": device.name,
            "platform": device.platform,
            "status": device.status,
            "created_at": iso(device.created_at),
            "last_seen_at": iso(device.last_seen_at),
            "expires_at": iso(device.expires_at),
        }

    # ── Pairing ───────────────────────────────────────────────────────────

    def open_pairing(self) -> dict:
        """A new pairing code for the PC to show as a QR code.

        Raises:
            PairingUnavailable: The listener is off, or there is no address.
        """
        if not self.listening or self._listener is None or self._pin is None:
            raise PairingUnavailable(
                "listener_off",
                "Turn on access from the home network before pairing a device.",
            )
        origins = home_network_origins(self._host, self._listener.port or 0)
        if not origins:
            raise PairingUnavailable(
                "no_home_network",
                "This PC has no home-network address (10.x, 172.16-31.x or "
                "192.168.x) for a device to reach. Connect it to your home "
                "network, then try again.",
            )
        invitation, expires_at = self.pairing.open()
        return {
            "v": PAIRING_OFFER_VERSION,
            "name": pc_name(),
            "pc_id": self._open_store().pc_id(),
            "origins": origins,
            "pin": self._pin,
            "invite": invitation,
            "exp": int(expires_at),
        }

    def redeem(
        self, invitation: str, device_name: str, platform_name: str, peer: str
    ) -> dict:
        """Pair a device with *invitation*; its new pending record and token.

        Raises:
            ValueError: The device name has nothing printable.
            PairingRefused: The invitation doesn't open the window.
        """
        name = sanitize_device_name(device_name)
        store = self._open_store()
        device, token = self.pairing.redeem(
            invitation, peer, lambda: store.add_pending(name, platform_name)
        )
        logger.info("Device %s (%s) paired from %s, pending", device.id, name, peer)
        return {
            "device_id": device.id,
            "token": token,
            "pc_name": pc_name(),
            "pc_id": store.pc_id(),
            "status": device.status,
            "expires_at": iso(device.expires_at),
        }

    # ── The gate and the PC's controls ────────────────────────────────────

    def authenticate(self, token: str) -> Optional[Device]:
        store = self._store
        return store.authenticate(token) if store else None

    def still_paired(self, device_id: str) -> bool:
        store = self._store
        return bool(store and store.get(device_id))

    def allow(self, device_id: str) -> Optional[Device]:
        store = self._existing_store()
        device = store.allow(device_id) if store else None
        if device is not None:
            logger.info("Device %s allowed", device_id)
        return device

    def revoke(self, device_id: str, *, end_requests: bool = True) -> bool:
        """Unpair a device; its open requests end too unless *end_requests* is
        False, for a device unpairing itself, whose own request must finish."""
        store = self._existing_store()
        gone = bool(store and store.revoke(device_id))
        if gone:
            logger.info("Device %s unpaired", device_id)
            if end_requests:
                self.requests.end(device_id)
        return gone

    async def reset(self) -> None:
        """A new key and pin, every device unpaired; the listener as it was.

        Works on a folder the listener refuses, since this is its remedy.

        Raises:
            UnsafeDeviceFolderError: The folder isn't a plain folder of this user's.
            ListenerError: The listener was on and doesn't start again.
        """
        async with self._lock:
            was_listening = self.listening
            await self._stop(remember=False)
            pc_id = read_pc_id(self.folder / TABLE_NAME)
            if self._store is not None:
                self._store.close()
                self._store = None
            self.requests.end_all()
            self._pin = self._error = None
            if not os.path.lexists(self.folder):
                return
            reset_folder(self.folder)
            store = self._open_store()
            if pc_id:
                store.adopt_pc_id(pc_id)
            store.set_setting(_LISTENER_SETTING, "on" if was_listening else "off")
            logger.info("Pairing reset: new key, every device unpaired")
            if was_listening:
                await self._start()
