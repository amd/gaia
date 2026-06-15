# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Frozen-binary entrypoint for the GAIA Email Triage agent REST sidecar
(milestone #49, Phase 2 of email-agent-packaging).

This is the module PyInstaller freezes (see `freeze.py` / `release_agent_email.yml`).
It boots a **minimal**
FastAPI app that mounts ONLY the email REST router (``/v1/email/*``) plus two
dependency-free probes the sidecar lifecycle handshake needs:

    GET /health   -> {"status": "ok", "service": "gaia-agent-email"}
    GET /version  -> {"apiVersion": <contract SCHEMA_VERSION>,
                      "agentVersion": <package __version__>}

Why a minimal app instead of freezing the whole ``gaia api`` (openai_server)
app: the full app eagerly imports every registered agent (RAG, code, etc.),
which balloons the frozen binary and multiplies the freeze-time hidden-import
surface. The sidecar only needs the email surface, so we mount just that router.
The router import chain is identical to what ``openai_server`` mounts, so the
served contract is byte-for-byte the same.

The LLM stub (default ON — see the note below) swaps the triage service's local
Lemonade chat client for a deterministic stub, so the binary serves a
contract-valid triage round-trip with NO live LLM and NO Gmail connector. This is
what the smoke test, the npm demo, and CI exercise. **A production deployment
must pass ``--no-stub-llm``** so triage uses the local Lemonade model — see
`DEFAULT_STUB_LLM` and the host-side default in the npm lifecycle helpers.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("gaia_agent_email.sidecar")

# Default sidecar bind. NOT 4001 (reserved). 8131 is unused here.
DEFAULT_PORT = 8131
DEFAULT_HOST = "127.0.0.1"

# Whether triage uses the deterministic stub by default. ON so the smoke test,
# npm demo, and CI run with no live model/mailbox. Production passes
# --no-stub-llm (and the npm lifecycle helper should set stubLlm=False) to use
# the real local Lemonade model.
DEFAULT_STUB_LLM = True


# ---------------------------------------------------------------------------
# Deterministic LLM stub (active under --stub-llm; default ON — see module docs)
# ---------------------------------------------------------------------------


class _StubResponse:
    """Mimics the AgentSDK response object — carries a ``.text`` attribute."""

    def __init__(self, text: str) -> None:
        self.text = text


class _StubChat:
    """Deterministic stand-in for ``AgentSDK`` used by the email triage path.

    ``classify_email_llm`` and ``summarize_email_llm`` both call
    ``chat.send_messages(messages, system_prompt=..., temperature=...)`` and
    read ``response.text``. We branch on the system prompt to return a
    contract-valid classification JSON or a plain-text summary — no model, no
    network. Wired in only under ``--stub-llm``.
    """

    def send_messages(self, messages, system_prompt: str = "", **_kwargs):
        sp = (system_prompt or "").lower()
        if "classification" in sp:
            # Must be a value in the frozen taxonomy.
            return _StubResponse(
                json.dumps(
                    {
                        "category": "actionable",
                        "confidence": 0.95,
                        "reasoning": "stubbed deterministic classification",
                    }
                )
            )
        if "summarization" in sp:
            # Plain text only — the summarizer rejects empty output.
            user = messages[-1]["content"] if messages else ""
            first_line = next(
                (ln for ln in user.splitlines() if ln.startswith("Subject:")),
                "Subject: (none)",
            )
            return _StubResponse(
                f"[stub summary] {first_line.removeprefix('Subject:').strip()}"
            )
        return _StubResponse("[stub] unrecognized prompt")


def _install_llm_stub() -> None:
    """Patch the triage service so triage uses the deterministic stub chat."""
    from gaia_agent_email import api_routes

    api_routes._service._build_llm_chat = lambda *a, **k: _StubChat()  # type: ignore[attr-defined]
    log.info("LLM stub installed — triage will NOT call a live model.")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_app(stub_llm: bool = DEFAULT_STUB_LLM):
    """Build the minimal FastAPI app hosting the email REST surface."""
    from fastapi import FastAPI
    from gaia_agent_email import __version__ as agent_version
    from gaia_agent_email.api_routes import router as email_router
    from gaia_agent_email.contract import SCHEMA_VERSION

    app = FastAPI(
        title="GAIA Email Agent Sidecar",
        version=agent_version,
        description="Frozen-binary email triage REST sidecar.",
    )

    @app.get("/health", include_in_schema=True)
    async def health() -> dict:
        return {"status": "ok", "service": "gaia-agent-email"}

    @app.get("/version", include_in_schema=True)
    async def version() -> dict:
        # apiVersion is the host-facing REST contract version (the frozen
        # request/response schema); agentVersion is the package build.
        return {"apiVersion": SCHEMA_VERSION, "agentVersion": agent_version}

    app.include_router(email_router)

    if stub_llm:
        _install_llm_stub()

    return app


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="GAIA Email Triage REST sidecar (frozen binary entrypoint)."
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port.")
    parser.add_argument(
        "--no-stub-llm",
        action="store_true",
        help="Use the real local Lemonade LLM for triage instead of the stub.",
    )
    parser.add_argument(
        "--print-openapi",
        action="store_true",
        help="Print the OpenAPI JSON to stdout and exit (no server).",
    )
    args = parser.parse_args(argv)

    stub = not args.no_stub_llm
    app = build_app(stub_llm=stub)

    if args.print_openapi:
        print(json.dumps(app.openapi()))
        return 0

    import uvicorn

    log.info(
        "Starting GAIA email sidecar on http://%s:%d (stub_llm=%s)",
        args.host,
        args.port,
        stub,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
