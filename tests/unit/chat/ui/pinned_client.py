# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A test client that pins the device listener's certificate before sending a byte.

What a paired device's native transport must do: TLS with no CA, hostname or
date check; the leaf's SHA-256 compared with the pairing code's pin right
after the handshake; and only then the request. Redirects are not followed.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import ssl
from typing import Dict, Optional, Tuple


class PinMismatch(ConnectionError):
    """The listener's certificate isn't the one the pairing code named."""


class PinnedConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        port: int,
        pin: str,
        timeout: float = 10.0,
        max_tls: Optional[ssl.TLSVersion] = None,
    ) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        if max_tls is not None:
            context.minimum_version = max_tls
            context.maximum_version = max_tls
        super().__init__(host, port, timeout=timeout, context=context)
        self._pin = pin

    def connect(self) -> None:
        super().connect()
        leaf = self.sock.getpeercert(binary_form=True)
        if hashlib.sha256(leaf).hexdigest() != self._pin:
            self.sock.close()
            self.sock = None
            raise PinMismatch("the listener's certificate doesn't match the pin")


def call(
    port: int,
    pin: str,
    method: str,
    path: str,
    *,
    token: Optional[str] = None,
    body: Optional[dict] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Dict[str, str], bytes]:
    """One request over a fresh pinned connection: status, headers, body."""
    connection = PinnedConnection("127.0.0.1", port, pin)
    sent = {"X-Gaia-UI": "1", **(headers or {})}
    if token:
        sent["Authorization"] = f"Bearer {token}"
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        sent["Content-Type"] = "application/json"
    try:
        connection.request(method, path, body=payload, headers=sent)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()
