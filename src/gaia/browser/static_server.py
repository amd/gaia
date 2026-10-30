# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The file server ``dev_server`` runs for a folder of plain HTML.

Python's ``http.server`` with the edges a browser pane preview doesn't need
taken off: it answers only on 127.0.0.1 and ::1, and only to a request
addressed to one of those or localhost on its own port, lists no folders, and serves no
dotfile (``.env``, ``.git``) and nothing a link leads to outside the folder.

    python -m gaia.browser.static_server --port 8123 --directory <folder>
"""

from __future__ import annotations

import argparse
import errno
import os
import socket
import sys
import threading
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List, Optional, Sequence

#: What binding ::1 fails with on a machine that has no IPv6.
_NO_IPV6 = {
    errno.EADDRNOTAVAIL,
    errno.EAFNOSUPPORT,
    getattr(errno, "WSAEADDRNOTAVAIL", 10049),
    getattr(errno, "WSAEAFNOSUPPORT", 10047),
}


class StaticHandler(SimpleHTTPRequestHandler):
    """Serves files under ``directory`` and refuses everything else with a 403 or 404."""

    def __init__(self, *args, directory: str, port: int, **kwargs):
        self.root = os.path.normcase(os.path.realpath(directory))
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        super().__init__(*args, directory=directory, **kwargs)

    def send_head(self):
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in self.hosts:
            self.send_error(HTTPStatus.FORBIDDEN, "Unknown host")
            return None
        if not self._servable(self.translate_path(self.path)):
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None
        return super().send_head()

    def _servable(self, path: str) -> bool:
        """Inside the folder after links resolve, and no part of it a dotfile.

        Both checks read the resolved path, so another name for the same file
        (a Windows short name like ``ENV~1``, a link) can't get round them.
        """
        requested = os.path.relpath(path, self.directory)
        if ":" in requested:
            return False  # a Windows stream or drive name, never a web file
        real = os.path.normcase(os.path.realpath(path))
        if real != self.root and not real.startswith(self.root + os.sep):
            return False
        parts = (
            [] if real == self.root else Path(os.path.relpath(real, self.root)).parts
        )
        if requested != ".":
            parts = [*parts, *Path(requested).parts]
        return not any(part.startswith(".") for part in parts)

    def list_directory(self, path):
        self.send_error(HTTPStatus.NOT_FOUND, "File not found")
        return None

    def log_message(self, format, *args):  # noqa: A002 — the base class's name
        sys.stderr.write(f"{self.address_string()} {format % args}\n")


class _IPv6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def serve(directory: str, port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """A server bound to ``host``:``port``, not yet serving."""
    handler = partial(StaticHandler, directory=directory, port=port)
    server_class = _IPv6Server if ":" in host else ThreadingHTTPServer
    return server_class((host, port), handler)


def serve_loopback(directory: str, port: int) -> List[ThreadingHTTPServer]:
    """Servers on both 127.0.0.1 and ::1, so no other program can hold either half of the port.

    ``localhost`` can reach either address, and GAIA trusts the port on both.
    A machine with no IPv6 has no ::1 for anyone to take, so only then is
    127.0.0.1 alone enough; any other failure to bind ::1 is an error.
    """
    servers = [serve(directory, port)]
    try:
        servers.append(serve(directory, port, "::1"))
    except OSError as e:
        if e.errno not in _NO_IPV6:
            for server in servers:
                server.server_close()
            raise OSError(
                e.errno, f"could not also listen on [::1]:{port} ({e.strerror})"
            ) from e
    return servers


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--directory", required=True)
    args = parser.parse_args(argv)
    if not os.path.isdir(args.directory):
        parser.error(f"{args.directory} is not a folder.")
    servers = serve_loopback(args.directory, args.port)
    print(f"Serving {args.directory} on http://127.0.0.1:{args.port}/", flush=True)
    for server in servers[1:]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    servers[0].serve_forever()


if __name__ == "__main__":
    main()
