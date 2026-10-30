# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The file server ``dev_server`` runs for a folder of plain HTML.

Python's ``http.server`` with the edges a browser pane preview doesn't need
taken off: it answers only on 127.0.0.1 and only to a request addressed to
127.0.0.1 or localhost on its own port, lists no folders, and serves no
dotfile (``.env``, ``.git``) and nothing a link leads to outside the folder.

    python -m gaia.browser.static_server --port 8123 --directory <folder>
"""

from __future__ import annotations

import argparse
import os
import sys
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, Sequence


class StaticHandler(SimpleHTTPRequestHandler):
    """Serves files under ``directory`` and refuses everything else with a 403 or 404."""

    def __init__(self, *args, directory: str, port: int, **kwargs):
        self.root = os.path.normcase(os.path.realpath(directory))
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
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
        """Inside the folder after links resolve, and no part of it a dotfile."""
        real = os.path.normcase(os.path.realpath(path))
        if real != self.root and not real.startswith(self.root + os.sep):
            return False
        relative = os.path.relpath(path, self.directory)
        parts = [] if relative == "." else Path(relative).parts
        return not any(part.startswith(".") for part in parts)

    def list_directory(self, path):
        self.send_error(HTTPStatus.NOT_FOUND, "File not found")
        return None

    def log_message(self, format, *args):  # noqa: A002 — the base class's name
        sys.stderr.write(f"{self.address_string()} {format % args}\n")


def serve(directory: str, port: int) -> ThreadingHTTPServer:
    """A server bound to 127.0.0.1:``port``, not yet serving."""
    handler = partial(StaticHandler, directory=directory, port=port)
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--directory", required=True)
    args = parser.parse_args(argv)
    if not os.path.isdir(args.directory):
        parser.error(f"{args.directory} is not a folder.")
    with serve(args.directory, args.port) as httpd:
        print(f"Serving {args.directory} on http://127.0.0.1:{args.port}/", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
