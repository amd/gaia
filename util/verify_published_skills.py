# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Check the live hub serves every skill under ``hub/skills/`` as users get it.

Run after ``util/publish_skills.py``. For each skill in the repo it confirms the
hub catalogue lists it at the repo's version, then installs that version into a
throwaway skills folder through the same installer users run, which downloads
the bundle, checks its digest and verifies its signature. A skill passes only
when the signature is GAIA's built-in AMD key and the installed tier is the one
the skill claims.

Read-only on the hub; nothing touches the real ``~/.gaia``.

Usage::

    python util/verify_published_skills.py                 # GAIA_HUB_URL or production
    python util/verify_published_skills.py --hub-url URL --only rss-digest
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SKILLS_DIR = REPO_ROOT / "hub" / "skills"


@dataclass
class Check:
    name: str
    version: str
    ok: bool
    detail: str = ""


def verify_skill(
    directory: Path, catalog: dict, *, hub_url: str, home: Path, fetcher=None
) -> Check:
    from gaia.skills.errors import SkillError
    from gaia.skills.format import parse_skill_file
    from gaia.skills.install import install_skill
    from gaia.skills.manager import SkillManager
    from gaia.skills.signing import ROLE_AMD

    skill = parse_skill_file(directory, check_directory_name=False)
    name, version = skill.name, str(skill.version)

    entry = catalog.get(name)
    if entry is None:
        return Check(name, version, False, "not in the hub catalogue")
    listed = str(entry.get("latest_version") or entry.get("version") or "")
    if listed != version:
        return Check(name, version, False, f"hub lists {listed or 'no version'}")

    manager = SkillManager(
        user_skills_root=home / name / "skills", claude_skill_dirs=[home / "none"]
    )
    try:
        result = install_skill(
            f"{name}@{version}",
            manager=manager,
            base_url=hub_url,
            fetcher=fetcher,
            assume_yes=True,
        )
    except SkillError as exc:
        return Check(name, version, False, f"install failed: {exc}")

    signature = result.signature
    if signature is None or not signature.signed:
        return Check(name, version, False, "installed unsigned")
    if signature.role != ROLE_AMD:
        return Check(
            name,
            version,
            False,
            f"signed by {signature.key_id} (role {signature.role or 'untrusted'}), "
            "not GAIA's built-in AMD key",
        )
    if result.downgraded:
        return Check(
            name,
            version,
            False,
            f"installed as {result.installed_tier}, claims {result.claimed_tier}",
        )
    return Check(name, version, True, f"{result.installed_tier}, AMD-signed")


def run(
    skills_dir: Path,
    hub_url: str,
    only: Sequence[str] = (),
    *,
    fetcher=None,
    cache_path: Optional[Path] = None,
) -> List[Check]:
    from gaia.skills.hub import search_skills

    directories = sorted(p.parent for p in skills_dir.glob("*/SKILL.md"))
    if only:
        directories = [d for d in directories if d.name in set(only)]
        unknown = set(only) - {d.name for d in directories}
        if unknown:
            raise SystemExit(f"No skill folder for: {', '.join(sorted(unknown))}")
    if not directories:
        raise SystemExit(f"No skills (*/SKILL.md) under {skills_dir}.")

    with tempfile.TemporaryDirectory(prefix="gaia-verify-skills-") as tmp:
        home = Path(tmp)
        found = search_skills(
            "",
            base_url=hub_url,
            fetcher=fetcher,
            cache_path=cache_path or home / "catalog-cache.json",
            force=True,
        )
        if found.offline:
            raise SystemExit(
                f"The hub at {hub_url} did not answer; refusing to verify against "
                "a cached catalogue."
            )
        catalog = {str(e.get("id") or e.get("name")): e for e in found.entries}
        return [
            verify_skill(d, catalog, hub_url=hub_url, home=home, fetcher=fetcher)
            for d in directories
        ]


def main(argv: Optional[Sequence[str]] = None) -> int:
    from gaia.hub.catalog import get_hub_base_url

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hub-url", default=get_hub_base_url())
    parser.add_argument("--skills-dir", type=Path, default=DEFAULT_SKILLS_DIR)
    parser.add_argument("--only", nargs="*", default=[], help="Skill names")
    args = parser.parse_args(argv)

    from gaia.skills.errors import SkillError

    hub_url = args.hub_url.rstrip("/")
    try:
        checks = run(args.skills_dir, hub_url, args.only)
    except SkillError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Hub: {hub_url}\n\n{'SKILL':<22} {'VERSION':<10} RESULT")
    for c in checks:
        print(f"{c.name:<22} {c.version:<10} {'ok' if c.ok else 'FAIL'}  {c.detail}")
    failed = [c for c in checks if not c.ok]
    if failed:
        print(
            f"\n{len(failed)} of {len(checks)} skill(s) are not served as shipped. "
            "Re-run release_skills.yml, or bump the skill's version if its "
            "published contents differ.",
            file=sys.stderr,
        )
        return 1
    print(f"\nAll {len(checks)} skills install from the hub, AMD-signed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
