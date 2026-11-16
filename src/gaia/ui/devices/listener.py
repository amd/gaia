# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The TLS listener paired devices reach, in the backend's own process and loop.

A second uvicorn server serves the same app through :class:`DeviceGate`, which
answers the home network only, serves the API only, caps request bodies, and
lets nothing through without a live device token except the pairing route.
An allowed device reaches only the routes listed in :mod:`.routes`.
Revoking a device ends the requests it still has open.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import ipaddress
import json
import os
import socket
import ssl
import sys
import threading
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from gaia.logger import get_logger

from ..security import (
    DEVICE_AUTH_SCOPE_KEY,
    DEVICE_SCOPE_KEY,
    DevicePrincipal,
    ListenerScopeMiddleware,
)
from . import (
    DEVICE_PORT_ENV,
    HOME_NETWORKS,
    LISTENER_BODY_LIMIT_BYTES,
    LISTENER_HANDSHAKE_SECONDS,
    LISTENER_KEEP_ALIVE_SECONDS,
    LISTENER_MAX_CONCURRENCY,
    LISTENER_SHUTDOWN_SECONDS,
    LISTENER_UPLOAD_PATHS,
    PAIR_ROUTE,
    SELF_ROUTE,
    ListenerErrorCode,
)
from .routes import DEVICE_ROUTES, DeviceRouteTable

logger = get_logger(__name__)

_HOME = tuple(ipaddress.ip_network(n) for n in HOME_NETWORKS)
_BODY_LIMIT_MB = LISTENER_BODY_LIMIT_BYTES // (1024 * 1024)


class ListenerError(RuntimeError):
    """The listener didn't start; *code* is for the PC to translate."""

    def __init__(self, code: ListenerErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class OpenRequests:
    """Each device's requests in flight, so revoking it ends them."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_device: Dict[str, Set[Tuple[asyncio.AbstractEventLoop, Any]]] = {}

    def add(self, device_id: str, loop, ended: asyncio.Event) -> None:
        with self._lock:
            self._by_device.setdefault(device_id, set()).add((loop, ended))

    def discard(self, device_id: str, loop, ended: asyncio.Event) -> None:
        with self._lock:
            open_now = self._by_device.get(device_id, set())
            open_now.discard((loop, ended))
            if not open_now:
                self._by_device.pop(device_id, None)

    def end(self, device_id: str) -> None:
        """End *device_id*'s open requests; safe from any thread."""
        with self._lock:
            open_now = list(self._by_device.get(device_id, ()))
        for loop, ended in open_now:
            loop.call_soon_threadsafe(ended.set)

    def end_all(self) -> None:
        with self._lock:
            device_ids = list(self._by_device)
        for device_id in device_ids:
            self.end(device_id)


async def _refuse(send: Send, scope: Scope, status: int, detail: str, **extra) -> None:
    """Answer without the app: JSON for http, a policy close for a websocket."""
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    body = json.dumps({"detail": detail, **extra}).encode("utf-8")
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"),
    ]
    if status == 401:
        headers.append((b"www-authenticate", b'Bearer realm="gaia"'))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _bearer(headers: Headers) -> Optional[str]:
    scheme, _, value = headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


class DeviceGate:
    """Device auth for the listener: pure ASGI, so websockets are covered too.

    Only ``Authorization: Bearer`` carries a token here. Cookies, query
    parameters and the mobile access token mean nothing on this listener.
    """

    def __init__(
        self,
        app: ASGIApp,
        access,
        *,
        allow_loopback: bool,
        routes: FrozenSet[str] = DEVICE_ROUTES,
    ) -> None:
        self.app = app
        self._access = access
        self._routes = DeviceRouteTable(app, routes)
        # Only a loopback-bound listener, which no other machine can reach.
        self._allow_loopback = allow_loopback

    def _home_peer(self, scope: Scope) -> bool:
        client = scope.get("client")
        try:
            address = ipaddress.ip_address(client[0]) if client else None
        except ValueError:
            return False
        if address is None or address.version != 4:
            return False
        if address.is_loopback:
            return self._allow_loopback
        return any(address in network for network in _HOME)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            return
        if not self._home_peer(scope):
            await _refuse(
                send, scope, 403, "GAIA answers devices on the home network only."
            )
            return
        path = scope.get("path", "")
        if not path.startswith(("/api/", "/v1/")):
            await _refuse(send, scope, 404, "Not Found")
            return
        scope = {**scope, DEVICE_AUTH_SCOPE_KEY: True}
        if scope["type"] == "http":
            capped = self._capped(scope, receive)
            if capped is None:
                await _refuse(
                    send,
                    scope,
                    413,
                    f"The request body is over {_BODY_LIMIT_MB} MB.",
                )
                return
            receive = capped
            if scope.get("method") == "POST" and path == PAIR_ROUTE:
                await self.app(scope, receive, send)
                return

        token = _bearer(Headers(scope=scope))
        device = self._access.authenticate(token) if token else None
        if device is None:
            logger.info(
                "Device auth: refused %s %s from %s (%s)",
                scope.get("method", "WEBSOCKET"),
                path,
                scope["client"][0],
                "no device token" if token is None else "unknown device token",
            )
            await _refuse(
                send,
                scope,
                401,
                "This needs a paired device's token. Pair the device again from "
                "the PC.",
                code="device_unknown",
            )
            return
        unpairing_itself = scope.get("method") == "DELETE" and path == SELF_ROUTE
        if not device.allowed and not unpairing_itself:
            await _refuse(
                send,
                scope,
                403,
                "This device is waiting for someone to allow it on the PC.",
                code="device_pending",
            )
            return
        if not self._routes.allows(scope.get("method", "WEBSOCKET"), path):
            await _refuse(
                send,
                scope,
                403,
                "Do this on the PC running GAIA. It isn't allowed from a "
                "paired device.",
                code="pc_only",
            )
            return
        scope[DEVICE_SCOPE_KEY] = DevicePrincipal(device.id, device.allowed)
        await self._serve(scope, receive, send, device.id)

    def _capped(self, scope: Scope, receive: Receive) -> Optional[Receive]:
        """*receive*, failing past the body limit; None when the length says so."""
        if scope.get("path") in LISTENER_UPLOAD_PATHS:
            return receive
        length = Headers(scope=scope).get("content-length", "")
        if length.isdigit() and int(length) > LISTENER_BODY_LIMIT_BYTES:
            return None
        received = 0

        async def capped() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > LISTENER_BODY_LIMIT_BYTES:
                    raise HTTPException(
                        413, f"The request body is over {_BODY_LIMIT_MB} MB."
                    )
            return message

        return capped

    async def _serve(
        self, scope: Scope, receive: Receive, send: Send, device_id: str
    ) -> None:
        """Run the app, and end the response the moment the device is revoked."""
        loop = asyncio.get_running_loop()
        ended = asyncio.Event()
        state = {"started": False, "done": False}

        async def tracked(message: Message) -> None:
            if message["type"] in ("http.response.start", "websocket.accept"):
                state["started"] = True
            elif message["type"] == "websocket.close" or (
                message["type"] == "http.response.body"
                and not message.get("more_body", False)
            ):
                state["done"] = True
            await send(message)

        self._access.requests.add(device_id, loop, ended)
        # A revoke after authenticate but before add had nothing to end.
        if not self._access.still_paired(device_id):
            self._access.requests.discard(device_id, loop, ended)
            logger.info("Device %s was revoked before its request ran", device_id)
            await _refuse(send, scope, 401, "This device was unpaired.")
            return
        app = asyncio.ensure_future(self.app(scope, receive, tracked))
        revoked = asyncio.ensure_future(ended.wait())
        try:
            done, _ = await asyncio.wait(
                {app, revoked}, return_when=asyncio.FIRST_COMPLETED
            )
            if app in done:
                app.result()
                return
            app.cancel()
            outcome = (await asyncio.gather(app, return_exceptions=True))[0]
            if not isinstance(outcome, (asyncio.CancelledError, type(None))):
                logger.warning(
                    "A revoked device's request ended with %s", type(outcome).__name__
                )
            logger.info("Device %s was revoked; ended its open request", device_id)
            if scope["type"] == "websocket":
                if not state["done"]:
                    await send({"type": "websocket.close", "code": 1008})
            elif not state["started"]:
                await _refuse(send, scope, 401, "This device was unpaired.")
            elif not state["done"]:
                await send({"type": "http.response.body", "body": b""})
        finally:
            revoked.cancel()
            if not app.done():
                app.cancel()
            self._access.requests.discard(device_id, loop, ended)


class _ListenerServer:
    """uvicorn's server, minus the signals the main server owns.

    Built lazily so importing this module needs no uvicorn.
    """

    @staticmethod
    def make(config, sock: socket.socket, context: ssl.SSLContext):
        import uvicorn

        class Server(uvicorn.Server):
            @contextlib.contextmanager
            def capture_signals(self):
                yield

            async def startup(self, sockets=None) -> None:
                del sockets  # The listener serves its own socket, bound below.
                # An empty list: the lifespan (off) and the bookkeeping, no bind.
                await super().startup(sockets=[])
                config = self.config

                def protocol(_loop=None):
                    made = config.http_protocol_class(
                        config=config,
                        server_state=self.server_state,
                        app_state=self.lifespan.state,
                        _loop=_loop,
                    )
                    # Request lines can carry a token sent in the wrong place.
                    made.access_log = False
                    return made

                loop = asyncio.get_running_loop()
                self.servers.append(
                    await loop.create_server(
                        protocol,
                        sock=sock,
                        ssl=context,
                        backlog=config.backlog,
                        ssl_handshake_timeout=LISTENER_HANDSHAKE_SECONDS,
                    )
                )

        return Server(config)


def local_ipv4_addresses() -> List[str]:
    """Every IPv4 address on this PC's up interfaces, loopback included."""
    import psutil

    stats = psutil.net_if_stats()
    found: List[str] = []
    for nic, entries in psutil.net_if_addrs().items():
        if nic in stats and not stats[nic].isup:
            continue
        for entry in entries:
            if entry.family == socket.AF_INET and entry.address not in found:
                found.append(entry.address)
    return found


# macOS answers a bind to an address that is gone (VPN utun, link-local,
# an interface dropping mid-check) with EADDRNOTAVAIL.
_ADDRESS_GONE = frozenset({errno.EADDRNOTAVAIL})


def _refuse_shared_port(port: int) -> None:
    """macOS lets a wildcard bind share *port* with a program on one address.

    Phones dialing that address would reach the other program and see a pin
    mismatch, so a holder on any local address counts as the port in use.
    The probe sets SO_REUSEADDR too, so this listener's own TIME_WAIT
    connections from a restart don't count.

    Addresses that vanished (``_ADDRESS_GONE``) are skipped.

    Raises:
        OSError: ``EADDRINUSE`` naming the address another program holds, or
            any other probe errno, named in the message.
    """
    for address in local_ipv4_addresses():
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((address, port))
        except OSError as e:
            if e.errno == errno.EADDRINUSE:
                raise OSError(
                    errno.EADDRINUSE,
                    f"another program holds {address}:{port}",
                ) from e
            if e.errno in _ADDRESS_GONE:
                logger.debug("Skipping %s: address is gone (%s)", address, e)
                continue
            raise OSError(
                e.errno,
                f"could not probe {address}:{port} "
                f"(errno {e.errno} {errno.errorcode.get(e.errno, '?')}): {e.strerror}",
            ) from e
        finally:
            probe.close()


def _bind(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            # SO_REUSEADDR on Windows would let another program take the port.
            sock.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_EXCLUSIVEADDRUSE,  # type: ignore[attr-defined]  # pylint: disable=no-member
                1,
            )
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        if sys.platform == "darwin" and ipaddress.ip_address(host).is_unspecified:
            _refuse_shared_port(int(sock.getsockname()[1]))
        sock.listen(LISTENER_MAX_CONCURRENCY * 4)
        sock.setblocking(False)
    except OSError:
        sock.close()
        raise
    return sock


def _prove_handshake(context: ssl.SSLContext) -> None:
    """Complete one TLS handshake in memory on *context*, as each connection will.

    Raises:
        ListenerError: The handshake fails, so no device could be answered.
    """
    from gaia.tls import stock_ssl_context

    client_context = stock_ssl_context(
        ssl.PROTOCOL_TLS_CLIENT, check_hostname=False, verify_mode=ssl.CERT_NONE
    )
    to_server, to_client = ssl.MemoryBIO(), ssl.MemoryBIO()
    from_server, from_client = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = client_context.wrap_bio(to_client, from_client)
    server = context.wrap_bio(to_server, from_server, server_side=True)
    try:
        for _ in range(8):
            finished = 0
            for side in (client, server):
                try:
                    side.do_handshake()
                    finished += 1
                except ssl.SSLWantReadError:
                    pass
            to_server.write(from_client.read())
            to_client.write(from_server.read())
            if finished == 2:
                return
    except ssl.SSLError as e:
        reason = f" ({type(e).__name__}: {e})"
        error: Optional[BaseException] = e
    else:
        reason, error = "", None
    raise ListenerError(
        "start_failed",
        f"GAIA's listener for paired devices couldn't complete a TLS "
        f"handshake{reason}, so it didn't start. Turn access off and on in "
        "Settings, or restart GAIA; if it keeps failing, report it at "
        "https://github.com/amd/gaia/issues.",
    ) from error


def _in_use(error: OSError) -> bool:
    return error.errno == errno.EADDRINUSE or getattr(error, "winerror", None) in (
        10048,
        10013,
    )


class DeviceListener:
    """One running listener: its socket, its server and the task serving it."""

    def __init__(self, app: ASGIApp, access, host: str, port: int) -> None:
        self._app = app
        self._access = access
        self._host = host
        self._port = port
        self._server: Any = None
        self._task: Optional[asyncio.Task] = None
        self.port: Optional[int] = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def requests_served(self) -> int:
        return self._server.server_state.total_requests if self._server else 0

    async def start(self, context: ssl.SSLContext) -> int:
        """Bind and serve; the port it listens on.

        Raises:
            ListenerError: The port is taken, or the server didn't come up.
        """
        import uvicorn

        _prove_handshake(context)
        try:
            sock = _bind(self._host, self._port)
        except OSError as e:
            if _in_use(e):
                raise ListenerError(
                    "port_in_use",
                    f"Port {self._port} is in use by another program, so GAIA "
                    "can't listen for paired devices. Close that program, or set "
                    f"{DEVICE_PORT_ENV} to a free port and restart GAIA; devices "
                    "then need to pair again.",
                ) from e
            raise ListenerError(
                "start_failed",
                f"GAIA can't listen for paired devices on port {self._port}: {e}.",
            ) from e
        port = int(sock.getsockname()[1])
        config = uvicorn.Config(
            ListenerScopeMiddleware(
                DeviceGate(
                    self._app,
                    self._access,
                    allow_loopback=ipaddress.ip_address(self._host).is_loopback,
                )
            ),
            lifespan="off",
            # The main server already configured logging for the process.
            log_config=None,
            limit_concurrency=LISTENER_MAX_CONCURRENCY,
            timeout_keep_alive=LISTENER_KEEP_ALIVE_SECONDS,
            timeout_graceful_shutdown=LISTENER_SHUTDOWN_SECONDS,
            host=self._host,
            port=port,
            log_level="info",
            # False would mute the main server's access log, which is global;
            # this listener's protocols log no access lines instead.
            access_log=True,
            # Device auth decides who a request is from; a header never does.
            proxy_headers=False,
            forwarded_allow_ips="",
        )
        self._server = _ListenerServer.make(config, sock, context)
        self._task = asyncio.create_task(self._server.serve())
        while not self._server.started:
            if self._task.done():
                sock.close()
                error = self._task.exception()
                raise ListenerError(
                    "start_failed",
                    f"GAIA's listener for paired devices stopped as it started: "
                    f"{error or 'no reason given'}.",
                ) from error
            await asyncio.sleep(0.01)
        self.port = port
        logger.info("Listening for paired devices on %s:%d", self._host, port)
        return port

    async def stop(self) -> None:
        if self._server is None or self._task is None:
            return
        self._access.requests.end_all()
        # pylint: disable-next=attribute-defined-outside-init
        self._server.should_exit = True
        try:
            await asyncio.wait_for(self._task, LISTENER_SHUTDOWN_SECONDS * 2)
        except asyncio.TimeoutError:
            # pylint: disable-next=attribute-defined-outside-init
            self._server.force_exit = True
            logger.warning("The device listener took too long to stop; forced it")
            await self._task
        self._server = None
        self._task = None
        logger.info("Stopped listening for paired devices")
