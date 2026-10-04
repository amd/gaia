# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Export the OpenAPI spec for the flagship sidecar's REST surface — #4605.

The committed ``openapi.gaia.json`` is the **cross-implementation source of
truth** for the ``/v1/gaia/*`` contract: a typed TypeScript or Python client
conforms to it, and so does anything else embedding the sidecar instead of
reading ``docs/guides/gaia.mdx`` prose. This module both *produces* that
artifact and lets CI *diff* it, so a route or schema change that isn't
regenerated fails loudly instead of silently drifting from the published
contract — mirroring ``gaia_agent_email.export_openapi`` (#1645).

The spec is built from a minimal FastAPI app that mounts ONLY the gaia router
plus the three probe routes (``/health``, ``/version``,
``/v1/gaia/version``) — the same surface ``gaia_agent.server.build_app``
serves, so the exported spec is byte-for-byte what the frozen sidecar serves,
built without touching the real caller-auth environment (no token file, no
live model-server probe wiring).

Usage::

    # Regenerate the committed artifact (run after changing routes/contract):
    python -m gaia_agent.export_openapi

    # CI drift check — non-zero exit if the committed file is stale:
    python -m gaia_agent.export_openapi --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

# Stable identity for the exported document. Pinned (not derived from runtime
# state) so regenerating on any machine yields the same bytes.
OPENAPI_TITLE = "GAIA Agent API"
OPENAPI_DESCRIPTION = (
    "REST surface for the GAIA flagship agent sidecar (/v1/gaia/*). "
    "Cross-implementation contract for embedders and typed clients."
)

# Committed artifact lives at the gaia package root (next to pyproject.toml).
ARTIFACT_PATH = Path(__file__).resolve().parents[1] / "openapi.gaia.json"


def build_app():
    """Build a minimal FastAPI app mounting ONLY the gaia router + probes.

    Mirrors ``gaia_agent.server.build_app`` route-for-route, without its
    caller-auth env wiring or model-server warm-up lifespan — this app's
    whole purpose is a static schema, never a request.
    """
    from fastapi import FastAPI
    from gaia_agent import __version__, caller_auth
    from gaia_agent.server import AGENT_ID, API_VERSION, router

    app = FastAPI(
        title=OPENAPI_TITLE,
        version=API_VERSION,
        description=OPENAPI_DESCRIPTION,
        docs_url=None,
    )

    @app.get("/health", include_in_schema=True)
    async def health() -> Dict[str, str]:
        return {"status": "ok", "service": f"gaia-agent-{AGENT_ID}"}

    @app.get("/version", include_in_schema=True)
    async def version() -> Dict[str, str]:
        return {"apiVersion": API_VERSION, "agentVersion": __version__}

    @app.get(f"/v1/{AGENT_ID}/version", include_in_schema=True)
    async def agent_version() -> Dict[str, str]:
        return {"apiVersion": API_VERSION, "version": __version__, "agent": AGENT_ID}

    app.include_router(router, prefix=f"/v1/{AGENT_ID}")

    # Declare the sidecar's bearer gate (#4605) — this app mounts the SAME
    # router the sidecar gates in server.py, so its exported document must
    # describe the same posture even though this minimal app never wires the
    # dependency itself.
    caller_auth.install_openapi_security(app)
    return app


def build_spec() -> Dict[str, Any]:
    """Return the OpenAPI document for the ``/v1/gaia/*`` routes as a dict."""
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
        description="Export or verify the gaia sidecar REST OpenAPI artifact."
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
            "Regenerate it with:  python -m gaia_agent.export_openapi",
            file=sys.stderr,
        )
        return 1

    written = write_artifact(args.output)
    print(f"Wrote OpenAPI artifact: {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
