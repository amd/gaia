# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The one place argv chooses a transport.

``gaia-agent`` serves two: the HTTP sidecar the daemon and the Agent UI speak,
and the newline-delimited JSON wire a parent process spawns it for. Which one
runs is decided here, and **nothing heavy is imported until the decision is
made**.

That last part is the whole reason this module exists rather than the dispatch
living in :mod:`gaia_agent.server`. ``server`` imports FastAPI, Starlette and
Pydantic at module scope and builds the ASGI app on import — and FastAPI is not
a dependency of the base install, it ships in the ``[ui]``/``[api]`` extras. A
console script pointing at ``server:main`` would therefore make the *stdio*
transport require FastAPI and construct a web app it never serves, on a machine
that may not have it installed at all.

Before this module the console script pointed straight at ``stdio:main``, past
the dispatcher entirely, so ``gaia-agent --serve`` exited with ``unrecognized
arguments: --serve``. The frozen binary and the daemon's dev mode both reach the
app by module path, which is why the documented HTTP transport was unreachable
only from the package that documents it.
"""

from __future__ import annotations

import sys
from typing import List, Optional

#: argv spellings that select the HTTP sidecar. ``--serve`` is the explicit
#: selector; the bind flags imply it because the daemon spawns the installed
#: binary as ``<binary> --host H --port P`` with no ``--serve``
#: (``gaia.daemon.sidecars.manager.build_spawn_command``). Neither spelling
#: exists in the stdio parser and none of its flags exist in the HTTP one, so
#: the split is unambiguous.
HTTP_SELECTORS = ("--serve", "--host", "--port")

TRANSPORT_HELP = """\
gaia-agent serves two transports from one binary, chosen by argv:

  gaia-agent --serve [--host HOST] [--port PORT]
      The HTTP sidecar: the /v1/gaia/* contract the daemon and the Agent UI
      speak. Bound to 127.0.0.1:8141 unless told otherwise.

  gaia-agent [OPTIONS]
      Newline-delimited JSON over stdin/stdout -- one query per line in, one
      turn's canonical events out. This is what a parent process spawns. Its
      options:
"""


def selects_http(argv: List[str]) -> bool:
    """Whether *argv* asks for the HTTP sidecar rather than the stdio wire."""
    return any(
        arg == flag or arg.startswith(f"{flag}=")
        for arg in argv
        for flag in HTTP_SELECTORS
    )


def main(argv: Optional[List[str]] = None) -> int:
    """Dispatch this process onto one of the agent's two transports.

    A flag belonging to the other transport is an argparse error from the one
    that was selected — never a quiet switch to the other.
    """
    args = list(sys.argv[1:] if argv is None else argv)

    if selects_http(args):
        # Imported here, not at module scope: this is the branch that may use
        # FastAPI, and it is the only one that should have to have it.
        from gaia_agent.server import serve_http

        return serve_http(args)

    # The stdio parser cannot mention a mode it does not own.
    if any(arg in ("-h", "--help") for arg in args):
        print(TRANSPORT_HELP)

    # stdout is about to become the event wire, so nothing imported below may
    # log to it — a stray line reaches the reader as a malformed event.
    from gaia.logger import route_console_logging_to_stderr

    route_console_logging_to_stderr()

    from gaia_agent.stdio import main as stdio_main

    return stdio_main(args)
