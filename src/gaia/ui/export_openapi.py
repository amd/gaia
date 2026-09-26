# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Export the Agent UI backend's OpenAPI component schemas.

The committed ``api.schemas.json`` is what the Agent UI's generated TypeScript
types (``src/types/api.gen.ts``) are built from, so a pydantic model change
that isn't re-exported fails CI instead of silently drifting from the frontend.

Usage::

    # Regenerate (then run `npm run gen:api-types` in src/gaia/apps/webui):
    python -m gaia.ui.export_openapi

    # CI drift check — non-zero exit if the committed file is stale:
    python -m gaia.ui.export_openapi --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

ARTIFACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "apps"
    / "webui"
    / "src"
    / "types"
    / "api.schemas.json"
)

REGENERATE_HINT = (
    "python -m gaia.ui.export_openapi && "
    "(cd src/gaia/apps/webui && npm run gen:api-types)"
)


def build_schemas() -> Dict[str, Any]:
    """Return ``components.schemas`` from an in-process app (no server)."""
    from gaia.ui.server import create_app

    app = create_app(db_path=":memory:")
    return app.openapi()["components"]["schemas"]


def render(schemas: Dict[str, Any]) -> str:
    return json.dumps(schemas, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the committed schema file is stale",
    )
    args = parser.parse_args(argv)

    rendered = render(build_schemas())
    if args.check:
        committed = (
            ARTIFACT_PATH.read_text(encoding="utf-8") if ARTIFACT_PATH.exists() else ""
        )
        if committed != rendered:
            print(
                f"{ARTIFACT_PATH} is out of date with the Agent UI backend models.\n"
                f"Regenerate from the repo root: {REGENERATE_HINT}",
                file=sys.stderr,
            )
            return 1
        return 0

    ARTIFACT_PATH.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"Wrote {ARTIFACT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
