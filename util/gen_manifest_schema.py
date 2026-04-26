# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Regenerate `schemas/agent-manifest.schema.json` from the Pydantic model.

Run this script whenever `AgentManifest` or `KNOWN_TOOLS` in
`src/gaia/agents/registry.py` changes, so editors consuming the schema
(VS Code YAML extension, etc.) stay in sync.

Usage:
    python util/gen_manifest_schema.py
    python util/gen_manifest_schema.py --check   # exit 1 if schema drifts
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "schemas" / "agent-manifest.schema.json"

SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def build_schema() -> dict:
    """Build the JSON Schema from the Pydantic model and KNOWN_TOOLS."""
    from gaia.agents.registry import KNOWN_TOOLS, AgentManifest

    schema = AgentManifest.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://amd-gaia.ai/schemas/agent-manifest.schema.json"
    schema["title"] = "GAIA Agent Manifest"
    schema["description"] = (
        "Declarative manifest for a GAIA agent loaded from agent.yaml. "
        "Validated at load time by AgentManifest in src/gaia/agents/registry.py."
    )

    # Constrain `tools` items to known tool names so editors surface typos early.
    props = schema.get("properties", {})
    if "tools" in props:
        props["tools"]["items"] = {
            "type": "string",
            "enum": sorted(KNOWN_TOOLS.keys()),
            "description": "Name of a tool mixin registered in KNOWN_TOOLS.",
        }

    return schema


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if the on-disk schema is stale (do not write).",
    )
    args = parser.parse_args()

    schema = build_schema()
    new_text = json.dumps(schema, indent=2, sort_keys=False) + "\n"

    if args.check:
        if not SCHEMA_PATH.exists():
            print(f"[FAIL] {SCHEMA_PATH} does not exist", file=sys.stderr)
            return 1
        existing = SCHEMA_PATH.read_text(encoding="utf-8")
        if existing != new_text:
            print(
                f"[FAIL] {SCHEMA_PATH} is stale — run `python util/gen_manifest_schema.py`",
                file=sys.stderr,
            )
            return 1
        print(f"[OK] {SCHEMA_PATH} is up to date")
        return 0

    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(new_text, encoding="utf-8")
    print(f"[OK] Wrote {SCHEMA_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
