# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One binary, two transports: what ``gaia_agent.server.main`` dispatches on.

``gaia-agent`` is spawned two different ways and neither invocation may land in
the other's parser:

* the daemon runs ``<binary> --host H --port P`` (``gaia.daemon.sidecars.
  manager``) and expects the HTTP sidecar — note it passes NO ``--serve``, which
  is why the bind flags have to select HTTP on their own;
* the TUI runs ``<binary> --json-events`` and then speaks newline-delimited JSON
  over the child's pipes (``tui/internal/catalog/catalog.go``).

Only the uvicorn entry was ever frozen, so the TUI's argv died on
``unrecognized arguments: --json-events`` and no shipped binary could serve it.
The tests here pin the split, and pin that a flag from the wrong transport is a
loud argparse error rather than a quiet switch to the other one.
"""

from __future__ import annotations

import logging
import sys

import pytest

pytest.importorskip("gaia_agent")

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import stdio as stdio_mod  # noqa: E402


@pytest.fixture
def served(monkeypatch):
    """Records ``(app, kwargs)`` for every uvicorn boot main() asks for."""
    calls = []
    monkeypatch.setattr(
        uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs))
    )
    return calls


@pytest.fixture
def piped(monkeypatch):
    """Records the argv main() forwards to the stdio transport."""
    calls = []
    monkeypatch.setattr(stdio_mod, "main", lambda argv=None: calls.append(argv) or 0)
    return calls


def test_no_arguments_at_all_is_the_stdio_wire(served, piped):
    assert server_mod.main([]) == 0
    assert piped == [[]]
    assert served == []


def test_the_tui_argv_reaches_the_stdio_parser_verbatim(served, piped):
    argv = ["--json-events", "--dev", "--model", "Gemma-4-E4B-it-GGUF"]
    server_mod.main(list(argv))
    assert piped == [argv]
    assert served == []


def test_the_claude_flags_are_forwarded_untouched(served, piped):
    argv = ["--use-claude", "--claude-model", "claude-opus-5"]
    server_mod.main(list(argv))
    assert piped == [argv]
    assert served == []


def test_serve_runs_the_http_sidecar_on_the_loopback_defaults(served, piped):
    assert server_mod.main(["--serve"]) == 0
    assert len(served) == 1
    app, kwargs = served[0]
    assert app is server_mod.app
    assert kwargs["host"] == server_mod.DEFAULT_HOST
    assert kwargs["port"] == server_mod.DEFAULT_PORT
    assert piped == []


def test_the_daemons_bind_flags_select_http_without_serve(served, piped):
    """The daemon spawns the installed binary with no ``--serve`` at all."""
    server_mod.main(["--host", "127.0.0.1", "--port", "8141"])
    assert [kwargs["port"] for _, kwargs in served] == [8141]
    assert piped == []


def test_an_equals_form_bind_flag_still_selects_http(served, piped):
    server_mod.main(["--host=0.0.0.0", "--port=9000"])
    assert [(kw["host"], kw["port"]) for _, kw in served] == [("0.0.0.0", 9000)]
    assert piped == []


def test_serve_with_an_explicit_bind_is_accepted(served, piped):
    server_mod.main(["--serve", "--host", "127.0.0.1", "--port", "8199"])
    assert [(kw["host"], kw["port"]) for _, kw in served] == [("127.0.0.1", 8199)]
    assert piped == []


def test_a_stdio_flag_on_the_http_transport_is_refused_not_reinterpreted(served, piped):
    """Mixing the two transports must fail loudly.

    Silently serving HTTP (or silently dropping to stdio) would leave the caller
    running a transport it did not ask for, which is unreadable from the outside.
    """
    with pytest.raises(SystemExit) as excinfo:
        server_mod.main(["--serve", "--json-events"])
    assert excinfo.value.code == 2
    assert served == []
    assert piped == []


def test_argv_defaults_to_the_process_command_line(served, piped, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gaia-agent", "--json-events"])
    server_mod.main()
    assert piped == [["--json-events"]]
    assert served == []


def test_the_stdio_exit_code_is_the_binarys_exit_code(monkeypatch):
    monkeypatch.setattr(stdio_mod, "main", lambda argv=None: 1)
    assert server_mod.main(["--json-events"]) == 1


def test_the_asgi_app_stays_importable_at_module_level():
    """``uvicorn server:app --app-dir ...`` resolves it by attribute lookup.

    That is how the daemon starts the sidecar in dev mode, so the app must exist
    on the module without main() ever being called.
    """
    assert isinstance(getattr(server_mod, "app"), FastAPI)


def test_building_the_app_writes_nothing_to_the_log(caplog):
    """Importing this module is how the frozen binary reaches the stdio wire.

    ``app = build_app()`` runs at import, so a line logged from there lands on
    stdout ahead of the first JSON event and the TUI renders it as a malformed
    event. The caller-auth banner belongs to server startup instead.
    """
    with caplog.at_level(logging.DEBUG):
        server_mod.build_app()
    assert [r.getMessage() for r in caplog.records] == []


def test_the_caller_auth_banner_still_reaches_the_http_startup(caplog, monkeypatch):
    """Moving it to the lifespan must not lose it: it is a security notice."""
    monkeypatch.delenv("GAIA_GAIA_SIDECAR_TOKEN", raising=False)
    monkeypatch.delenv("GAIA_GAIA_SIDECAR_TOKEN_FILE", raising=False)
    with caplog.at_level(logging.WARNING):
        with TestClient(server_mod.build_app()):
            pass
    assert any("authentication DISABLED" in r.getMessage() for r in caplog.records)


def test_the_stdio_branch_takes_console_logging_off_stdout(monkeypatch, piped):
    """A log line written while the stdio graph imports would corrupt the wire."""
    calls = []
    monkeypatch.setattr(
        server_mod, "route_console_logging_to_stderr", lambda: calls.append(1)
    )
    server_mod.main(["--json-events"])
    assert calls == [1]


def test_the_two_transports_share_no_argv_spelling():
    """The dispatch is only unambiguous while the flag sets stay disjoint."""
    stdio_flags = {
        action.option_strings[0]
        for action in stdio_mod.build_parser()._actions
        if action.option_strings
    }
    assert stdio_flags.isdisjoint(server_mod._HTTP_SELECTORS)
