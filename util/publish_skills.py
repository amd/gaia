# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Publish every first-party skill under ``hub/skills/`` to the Agent Hub.

Runs ``gaia skill publish`` per skill, signed with the AMD key GAIA trusts out of
the box (``BUILTIN_TRUSTED_KEYS``), so a published skill installs as first-party
with no trust setup. ``.github/workflows/release_skills.yml`` runs it on every
final release tag; a maintainer can dispatch that workflow by hand.

Hub versions are immutable, so for each skill:

* version not on the hub → publish it;
* version on the hub and its signed file digests match this folder → skip;
* version on the hub but the files differ → FAIL: the skill changed without a
  version bump, and that change can never reach users until it gets one.

Usage::

    # What CI runs (GAIA_HUB_TOKEN set, key at ~/.gaia/skills/keys/amd-skills.key)
    python util/publish_skills.py --hub-url "$GAIA_HUB_PUBLISH_URL"

    # Every gate, no upload, any key
    python util/publish_skills.py --dry-run
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SKILLS_DIR = REPO_ROOT / "hub" / "skills"
DEFAULT_KEY_NAME = "amd-skills"

STATUS_PUBLISHED = "published"
STATUS_DRY_RUN = "ok (dry-run)"
STATUS_SKIPPED = "already on hub"
STATUS_FAILED = "failed"

# fetch(url) -> bytes, or None for a 404; raises on any other failure.
Fetch = Callable[[str], Optional[bytes]]
# run(cmd) -> (returncode, combined output)
Runner = Callable[[List[str]], "tuple[int, str]"]


class PublishSkillsError(Exception):
    """The run cannot start, or a skill cannot be published as-is."""


@dataclass
class SkillOutcome:
    name: str
    version: str
    status: str
    detail: str = ""


def _fetch(url: str) -> Optional[bytes]:
    import requests

    resp = requests.get(url, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.content


def _run(cmd: List[str]) -> "tuple[int, str]":
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def local_digests(directory: Path) -> dict[str, str]:
    """Digests of the files ``gaia skill publish`` would put in the bundle."""
    from gaia.skills.publish import EXCLUDED_NAMES, EXCLUDED_SUFFIXES
    from gaia.skills.signing import SIGNATURE_FILENAME, file_digests

    return {
        path: digest
        for path, digest in file_digests(directory).items()
        if path != SIGNATURE_FILENAME
        and not any(part in EXCLUDED_NAMES for part in path.split("/"))
        and not path.endswith(EXCLUDED_SUFFIXES)
    }


def published_digests(
    name: str, version: str, hub_url: str, fetch: Fetch
) -> Optional[dict[str, str]]:
    """Signed file digests of *name* @ *version* on the hub, or None if unpublished."""
    from gaia.skills.hub import skill_artifact_url, skill_manifest_url
    from gaia.skills.signing import BUILTIN_TRUSTED_KEYS

    raw = fetch(skill_manifest_url(name, hub_url))
    if raw is None:
        return None
    manifest = json.loads(raw)
    entry = (manifest.get("versions") or {}).get(version)
    if entry is None:
        return None

    artifact = entry.get("artifact") or (entry.get("artifacts") or [None])[0]
    if not isinstance(artifact, dict) or not artifact.get("filename"):
        raise PublishSkillsError(
            f"Hub manifest for '{name}' {version} has no artifact filename, so its "
            "contents cannot be compared. Inspect "
            f"{skill_manifest_url(name, hub_url)}."
        )
    bundle = fetch(skill_artifact_url(name, version, artifact["filename"], hub_url))
    if bundle is None:
        raise PublishSkillsError(
            f"Hub manifest lists '{name}' {version} but its artifact "
            f"{artifact['filename']} is missing (404). This is hub-side damage — "
            "report it; the version cannot be republished."
        )
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        signature_name = f"{name}/SIGNATURE.json"
        if signature_name not in archive.namelist():
            raise PublishSkillsError(
                f"Published '{name}' {version} is unsigned, so its contents cannot "
                "be compared. Bump the skill's version to publish a signed one."
            )
        record = json.loads(archive.read(signature_name))
    if record.get("key_id") not in BUILTIN_TRUSTED_KEYS:
        raise PublishSkillsError(
            f"Published '{name}' {version} is signed by key {record.get('key_id')}, "
            "not GAIA's built-in AMD key, so it installs as experimental. Bump the "
            "skill's version so the release publishes an AMD-signed one."
        )
    return dict(record["files"])


def require_amd_key(key_name: str) -> str:
    """Return the key id, or fail if the signing key is not GAIA's built-in AMD key.

    A wrong key would publish immutable versions that every install treats as
    untrusted, so this is checked before anything is uploaded.
    """
    from gaia.skills.manager import user_skills_dir
    from gaia.skills.signing import BUILTIN_TRUSTED_KEYS, ROLE_AMD, load_key

    key = load_key(user_skills_dir(), name=key_name)
    entry = BUILTIN_TRUSTED_KEYS.get(key.key_id)
    if entry is None or entry["role"] != ROLE_AMD:
        raise PublishSkillsError(
            f"Signing key '{key_name}' ({key.key_id}) is not GAIA's built-in AMD "
            "skill key, so every install would treat these skills as untrusted. "
            "Set the GAIA_SKILL_SIGNING_KEY secret on the agent-publish environment "
            "to the key whose public half is in BUILTIN_TRUSTED_KEYS "
            "(src/gaia/skills/signing.py)."
        )
    return key.key_id


def discover(skills_dir: Path, only: Sequence[str]) -> List[Path]:
    found = sorted(p.parent for p in skills_dir.glob("*/SKILL.md"))
    if only:
        wanted = set(only)
        unknown = wanted - {p.name for p in found}
        if unknown:
            raise PublishSkillsError(
                f"No skill folder under {skills_dir} for: {', '.join(sorted(unknown))}"
            )
        found = [p for p in found if p.name in wanted]
    if not found:
        raise PublishSkillsError(f"No skills (*/SKILL.md) under {skills_dir}.")
    return found


def process_skill(
    directory: Path,
    *,
    hub_url: str,
    key_name: str,
    dry_run: bool,
    fetch: Fetch,
    run: Runner,
) -> SkillOutcome:
    from gaia.skills.format import parse_skill_file

    skill = parse_skill_file(directory, check_directory_name=False)
    name, version = skill.name, str(skill.version)

    remote = published_digests(name, version, hub_url, fetch)
    if remote is not None:
        local = local_digests(directory)
        if remote == local:
            return SkillOutcome(name, version, STATUS_SKIPPED)
        changed = sorted(
            set(remote) ^ set(local)
            | {f for f in set(remote) & set(local) if remote[f] != local[f]}
        )
        return SkillOutcome(
            name,
            version,
            STATUS_FAILED,
            f"{version} is already published with different contents "
            f"({', '.join(changed)}). Bump 'version:' in {directory / 'SKILL.md'}.",
        )

    cmd = [
        "gaia",
        "skill",
        "publish",
        str(directory),
        "--hub-url",
        hub_url,
        "--key-name",
        key_name,
        "--publisher",
        "AMD",
    ]
    if dry_run:
        cmd.append("--dry-run")
    code, output = run(cmd)
    print(output.rstrip())
    if code != 0:
        return SkillOutcome(name, version, STATUS_FAILED, output.strip()[-400:])
    return SkillOutcome(name, version, STATUS_DRY_RUN if dry_run else STATUS_PUBLISHED)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    from gaia.hub.catalog import get_hub_base_url

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--hub-url",
        default=get_hub_base_url(),
        help="Hub origin to check and publish to (default: GAIA_HUB_URL or the "
        "production hub)",
    )
    parser.add_argument("--skills-dir", type=Path, default=DEFAULT_SKILLS_DIR)
    parser.add_argument(
        "--key-name",
        default=DEFAULT_KEY_NAME,
        help="Signing key under ~/.gaia/skills/keys/",
    )
    parser.add_argument("--only", nargs="*", default=[], help="Skill names")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run every gate without uploading; the key need not be the AMD key",
    )
    return parser.parse_args(argv)


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    fetch: Fetch = _fetch,
    run: Runner = _run,
) -> int:
    args = parse_args(argv)
    hub_url = args.hub_url.rstrip("/")
    try:
        if not args.dry_run:
            print(f"Signing with built-in AMD key {require_amd_key(args.key_name)}")
        directories = discover(args.skills_dir, args.only)
    except PublishSkillsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    outcomes = []
    for d in directories:
        try:
            outcomes.append(
                process_skill(
                    d,
                    hub_url=hub_url,
                    key_name=args.key_name,
                    dry_run=args.dry_run,
                    fetch=fetch,
                    run=run,
                )
            )
        except PublishSkillsError as exc:
            outcomes.append(SkillOutcome(d.name, "?", STATUS_FAILED, str(exc)))

    print(f"\n{'SKILL':<22} {'VERSION':<10} STATUS")
    for o in outcomes:
        print(f"{o.name:<22} {o.version:<10} {o.status}")
        if o.detail:
            print(f"    {o.detail}")
    failed = [o for o in outcomes if o.status == STATUS_FAILED]
    if failed:
        print(f"\n{len(failed)} skill(s) failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
