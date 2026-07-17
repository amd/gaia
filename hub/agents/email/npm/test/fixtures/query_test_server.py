# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Dev-mode sidecar for the npm client's /query integration test (#2097).

Boots the REAL packaging/server.py app (real routes, real SSEOutputHandler,
real CanonicalTranslator, real caller-auth gate) with ONE swap: the
``query_routes.build_query_agent`` seam returns a scripted fake agent instead
of a live EmailTriageAgent, so the canonical wire is exercised end-to-end over
real HTTP without Lemonade or Gmail — the same seam the Python-side
``test_query_route.py`` acceptance harness swaps.

The fake picks its script from the query text:
  - a query containing "wait between steps" runs multi-step, parking on the
    run's cancel event between steps (so the Node test can cancel mid-run);
  - anything else drives one happy triage turn: status -> tool_call ->
    tool_result -> final.

Usage (spawned by test/query-integration.test.ts via startSidecar):
    python query_test_server.py --host 127.0.0.1 --port <port>
    python query_test_server.py --check   # import probe only; exits 0/1
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

_FIXTURES = Path(__file__).resolve().parent
# parents: [0]=test [1]=agent-email [2]=npm [3]=agents [4]=hub [5]=repo root
_REPO_ROOT = _FIXTURES.parents[5]

# First-party code comes from THIS checkout; third-party deps from the interpreter.
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "hub" / "agents" / "email" / "python"))


def _load_server_module():
    server_py = (
        _REPO_ROOT / "hub" / "agents" / "email" / "python" / "packaging" / "server.py"
    )
    spec = importlib.util.spec_from_file_location("server", server_py)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {server_py}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _HappyFakeAgent:
    """One happy triage turn: status -> tool_call -> tool_result -> final."""

    def __init__(self) -> None:
        self.conversation_history = []
        self.console = None
        self._cancel_event = None

    def process_query(self, query, max_steps=None):
        self.console.print_processing_start(query, 20, "fake-model")
        self.console.print_tool_usage("triage_inbox")
        self.console.pretty_print_json({"max_messages": 10}, title="Arguments")
        self.console.pretty_print_json({"ok": True, "count": 5})
        self.console.print_tool_complete()
        self.console.print_final_answer("Triaged 5 emails.", streaming=False)
        return {"answer": "Triaged 5 emails."}


class _CancelFakeAgent:
    """Multi-step run that parks on the cancel event BETWEEN steps."""

    def __init__(self) -> None:
        self.conversation_history = []
        self.console = None
        self._cancel_event = None

    def process_query(self, query, max_steps=None):
        self.console.print_processing_start(query, 5, "fake-model")
        for step in range(1, 4):
            if self._cancel_event is not None and self._cancel_event.is_set():
                self.console.print_final_answer(
                    "Stopped between steps.", streaming=False
                )
                return {"answer": "Stopped between steps."}
            self.console.print_tool_usage(f"tool_{step}")
            self.console.pretty_print_json({}, title="Arguments")
            self.console.pretty_print_json({"ok": True})
            self.console.print_tool_complete()
            if self._cancel_event is not None:
                # Bounded park so the Node test can cancel between steps; a
                # hung test still terminates.
                self._cancel_event.wait(timeout=10)
        self.console.print_final_answer("Completed all steps.", streaming=False)
        return {"answer": "Completed all steps."}


def _fake_build_query_agent(**_config_kwargs):
    # The scripted behavior is selected per-run inside process_query via the
    # query text, but agent construction happens before the text is known —
    # so return a dispatcher that picks the script on first process_query.
    class _Dispatcher:
        def __init__(self) -> None:
            self.conversation_history = []
            self.console = None
            self._cancel_event = None

        def process_query(self, query, max_steps=None):
            cls = _CancelFakeAgent if "wait between steps" in query else _HappyFakeAgent
            inner = cls()
            inner.conversation_history = self.conversation_history
            inner.console = self.console
            inner._cancel_event = self._cancel_event
            return inner.process_query(query, max_steps=max_steps)

    return _Dispatcher()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8131)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Probe that every import resolves, then exit 0 (no server).",
    )
    args = parser.parse_args()

    if args.port == 4001:
        raise SystemExit("port 4001 is reserved and must never be used")

    import uvicorn  # noqa: F401 — part of the --check probe
    from gaia_agent_email import query_routes

    server = _load_server_module()

    if args.check:
        print("ok")
        return 0

    query_routes.build_query_agent = _fake_build_query_agent
    uvicorn.run(server.app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
