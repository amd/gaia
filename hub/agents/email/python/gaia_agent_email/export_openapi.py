# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Export the OpenAPI spec for the email REST surface (``/v1/email/*``) — #1645.

The committed ``openapi.email.json`` is the **cross-implementation source of
truth** for the email contract: the ``@amd-gaia/agent-email`` npm client and the
future C++ build both conform to it. This module both *produces* that artifact
and lets CI *diff* it, so a route or schema change that isn't regenerated fails
loudly instead of silently drifting from the published contract.

The spec is built from a minimal FastAPI app that mounts ONLY the email router —
the same ``gaia_agent_email.api_routes.router`` the product server
(``gaia.api.openai_server``) and the freeze server mount — so the exported spec
is byte-for-byte what a host serves, with none of the unrelated ``gaia api``
routes.

Usage::

    # Regenerate the committed artifact (run after changing routes/contract):
    python -m gaia_agent_email.export_openapi

    # CI drift check — non-zero exit if the committed file is stale:
    python -m gaia_agent_email.export_openapi --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

from gaia_agent_email.version import API_VERSION

# Stable identity for the exported document. Pinned (not derived from runtime
# state) so regenerating on any machine yields the same bytes.
OPENAPI_TITLE = "GAIA Email Agent API"
OPENAPI_DESCRIPTION = (
    "REST surface for the GAIA Email Triage agent (/v1/email/*). "
    "Cross-implementation contract for the npm client and native builds."
)

# Committed artifact lives at the email package root (next to pyproject.toml).
ARTIFACT_PATH = Path(__file__).resolve().parents[1] / "openapi.email.json"


def build_app():
    """Build a minimal FastAPI app mounting ONLY the email router.

    Mirrors how the product/freeze servers mount the router, so the generated
    spec matches what a host actually serves.
    """
    from fastapi import FastAPI
    from gaia_agent_email.api_routes import router as email_router
    from gaia_agent_email.connection_intake_routes import (
        router as connection_intake_router,
    )

    app = FastAPI(
        title=OPENAPI_TITLE,
        version=API_VERSION,
        description=OPENAPI_DESCRIPTION,
    )
    app.include_router(email_router)
    # OAuth forward-out intake (#2154) — part of the frozen sidecar contract
    # (schema 2.5), so it belongs in the published artifact, unlike the
    # playground-only connector routes (include_in_schema=False).
    app.include_router(connection_intake_router)
    return app


def build_spec() -> Dict[str, Any]:
    """Return the OpenAPI document for the ``/v1/email/*`` routes as a dict."""
    return build_app().openapi()


def render(spec: Dict[str, Any]) -> str:
    """Serialize a spec to the canonical on-disk form (stable, diff-friendly)."""
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_artifact(path: Path = ARTIFACT_PATH) -> Path:
    """Generate the spec and write it to ``path``. Returns the path written."""
    path.write_text(render(build_spec()), encoding="utf-8")
    return path


def check_artifact(path: Path = ARTIFACT_PATH) -> bool:
    """Return True iff the committed artifact matches a freshly built spec.

    Used by CI and the contract test to detect drift. Reads the committed file
    and compares against the canonical render — never rewrites it.
    """
    if not path.exists():
        return False
    return path.read_text(encoding="utf-8") == render(build_spec())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Export or verify the email REST OpenAPI artifact."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the committed artifact is stale (no write).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ARTIFACT_PATH,
        help=f"Artifact path (default: {ARTIFACT_PATH}).",
    )
    args = parser.parse_args(argv)

    if args.check:
        if check_artifact(args.output):
            print(f"OpenAPI artifact up to date: {args.output}")
            return 0
        print(
            f"OpenAPI artifact is STALE or missing: {args.output}\n"
            "Regenerate it with:  python -m gaia_agent_email.export_openapi",
            file=sys.stderr,
        )
        return 1

    written = write_artifact(args.output)
    print(f"Wrote OpenAPI artifact: {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
