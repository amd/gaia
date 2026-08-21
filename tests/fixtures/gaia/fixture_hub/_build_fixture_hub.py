# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regenerate the fixture hub from the real starter skills (committed output).

Copies ``hub/skills/<name>/SKILL.md`` verbatim, zips each skill with fixed
timestamps (deterministic bytes), and stamps the real sha256 into
``manifest.json`` + ``index.json`` in the exact shapes ``gaia.skills.hub``
fetches. Run from the repo root whenever a fixture skill's SKILL.md changes:

    python tests/fixtures/gaia/fixture_hub/_build_fixture_hub.py
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]
SKILLS = ("github-triage", "data-explore")

# Fixed DOS timestamp so the zip bytes (and their sha256) are reproducible.
_ZIP_DATE = (2026, 1, 1, 0, 0, 0)

_FRONTMATTER_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---", re.DOTALL)


def _front_matter(markdown: str) -> dict:
    match = _FRONTMATTER_RE.match(markdown)
    if not match:
        raise ValueError("SKILL.md has no YAML front matter")
    return yaml.safe_load(match.group(1))


def _deterministic_zip(destination: Path, skill_markdown: bytes) -> bytes:
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as bundle:
        info = zipfile.ZipInfo("SKILL.md", date_time=_ZIP_DATE)
        info.external_attr = 0o644 << 16
        bundle.writestr(info, skill_markdown)
    return destination.read_bytes()


def main() -> None:
    index_entries = []
    for name in SKILLS:
        source = REPO_ROOT / "hub" / "skills" / name / "SKILL.md"
        markdown = source.read_text(encoding="utf-8")
        meta = _front_matter(markdown)
        version = str(meta["version"])
        gaia_meta = (meta.get("metadata") or {}).get("gaia") or {}

        version_dir = HERE / "skills" / name / version
        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / "SKILL.md").write_text(markdown, encoding="utf-8", newline="\n")

        artifact_name = f"{name}-{version}.zip"
        payload = _deterministic_zip(
            version_dir / artifact_name, markdown.encode("utf-8")
        )

        manifest = {
            "name": name,
            "description": meta.get("description", ""),
            "license": meta.get("license", ""),
            "security_tier": gaia_meta.get("security_tier", "experimental"),
            "permissions": gaia_meta.get("permissions", []),
            "latest_version": version,
            "versions": {
                version: {
                    "artifact": {
                        "filename": artifact_name,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "size_bytes": len(payload),
                        "path": f"skills/{name}/{version}/{artifact_name}",
                        "content_type": "application/zip",
                    }
                }
            },
        }
        (HERE / "skills" / name / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
        )

        index_entries.append(
            {
                "id": name,
                "type": "skill",
                "name": name,
                "description": meta.get("description", ""),
                "latest_version": version,
                "security_tier": gaia_meta.get("security_tier", "experimental"),
                "skill_metadata": {
                    "tools": [
                        {"name": tool} for tool in gaia_meta.get("tools_required", [])
                    ]
                },
            }
        )

    index = {"generated_at": "2026-01-01T00:00:00Z", "agents": index_entries}
    (HERE / "index.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"fixture hub rebuilt for: {', '.join(SKILLS)}")


if __name__ == "__main__":
    main()
