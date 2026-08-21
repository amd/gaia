# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Build the servable fixture hub, signing bundles with an EPHEMERAL keypair.

Run at eval-setup time, once per run:

    python tests/fixtures/gaia/prepare_fixture_hub.py --skills-root <agent skills root>
    python tests/fixtures/gaia/serve_fixtures.py --dir tests/fixtures/gaia/fixture_hub/_prepared
    # GAIA_HUB_URL=http://127.0.0.1:<port>

What it does, per run:

1. Generates a throwaway ``eval-test-publisher`` Ed25519 keypair under
   ``<skills-root>/keys/`` (``gaia.skills.signing.generate_key``). The private
   key never exists outside that run's machine — nothing is committed.
2. Signs every skill source under ``fixture_hub/sources/`` EXCEPT the ones in
   ``--unsigned`` (default: github-triage stays unsigned — the corpus has a
   scenario asserting the refusal + ``--allow-experimental`` guidance).
3. Zips each bundle and writes the hub layout ``gaia.skills.hub`` fetches
   (index.json + per-skill manifest.json + versioned SKILL.md + artifact)
   into ``--out`` (default ``fixture_hub/_prepared``, gitignored).
4. Trust-adds the throwaway public key (role ``publisher``) to
   ``<skills-root>/trusted-keys.json`` so signed bundles attest ``community``.

``--skills-root`` is REQUIRED: it must be the skills root the agent under eval
actually uses, and defaulting to the developer's real ``~/.gaia/skills`` would
silently pollute their trust store with a test key.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "fixture_hub" / "sources"
DEFAULT_OUT = HERE / "fixture_hub" / "_prepared"
KEY_NAME = "eval-test-publisher"

# Fixed DOS timestamp: zip bytes depend only on content (incl. the per-run
# signature), never on build wall-clock.
_ZIP_DATE = (2026, 1, 1, 0, 0, 0)


def _zip_dir(source_dir: Path, destination: Path) -> bytes:
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(
                path.relative_to(source_dir).as_posix(), date_time=_ZIP_DATE
            )
            info.external_attr = 0o644 << 16
            bundle.writestr(info, path.read_bytes())
    return destination.read_bytes()


def prepare(
    skills_root: Path, out_dir: Path, unsigned: frozenset[str]
) -> dict[str, dict]:
    """Sign, zip, and lay out the hub. Returns ``{skill: summary}`` per skill.

    Raises on every failure (missing sources, unknown --unsigned name, signing
    errors) — a half-prepared hub must never look ready.
    """
    from gaia.skills.format import parse_skill
    from gaia.skills.signing import TrustStore, generate_key, sign_bundle

    source_dirs = sorted(d for d in SOURCES.iterdir() if d.is_dir())
    if not source_dirs:
        raise FileNotFoundError(
            f"No skill sources under {SOURCES} — the fixture is broken."
        )
    names = {d.name for d in source_dirs}
    unknown = unsigned - names
    if unknown:
        raise ValueError(
            f"--unsigned names skills that do not exist: {', '.join(sorted(unknown))}. "
            f"Available: {', '.join(sorted(names))}."
        )

    skills_root.mkdir(parents=True, exist_ok=True)
    key = generate_key(skills_root, name=KEY_NAME, force=True)

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    summaries: dict[str, dict] = {}
    index_entries = []
    for source in source_dirs:
        markdown = (source / "SKILL.md").read_text(encoding="utf-8")
        skill = parse_skill(markdown, source=str(source / "SKILL.md"))
        name, version = skill.name, skill.version
        if not version:
            raise ValueError(
                f"{source / 'SKILL.md'} declares no version — a hub fixture "
                "cannot be published without one."
            )
        sign = name not in unsigned

        with tempfile.TemporaryDirectory(prefix="gaia-fixture-hub-") as tmp:
            staging = Path(tmp) / name
            shutil.copytree(source, staging)
            if sign:
                sign_bundle(
                    staging,
                    name=name,
                    version=version,
                    key=key,
                    publisher=KEY_NAME,
                )
            version_dir = out_dir / "skills" / name / version
            version_dir.mkdir(parents=True)
            # Serve the bundle's own SKILL.md so R2 copy == signed copy.
            shutil.copy2(staging / "SKILL.md", version_dir / "SKILL.md")
            artifact_name = f"{name}-{version}.zip"
            payload = _zip_dir(staging, version_dir / artifact_name)

        manifest = {
            "name": name,
            "description": skill.description,
            "license": skill.license or "",
            "security_tier": skill.security_tier,
            "permissions": list(skill.gaia.permissions),
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
        (out_dir / "skills" / name / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        index_entries.append(
            {
                "id": name,
                "type": "skill",
                "name": name,
                "description": skill.description,
                "latest_version": version,
                "security_tier": skill.security_tier,
                "skill_metadata": {
                    "tools": [{"name": t} for t in skill.gaia.tools_required]
                },
            }
        )
        summaries[name] = {"version": version, "signed": sign}

    (out_dir / "index.json").write_text(
        json.dumps(
            {"generated_at": "2026-01-01T00:00:00Z", "agents": index_entries},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    store = TrustStore.load(skills_root)
    import base64

    trusted_id = store.add(
        public_key_b64=base64.b64encode(key.public_bytes).decode("ascii"),
        publisher=KEY_NAME,
        role="publisher",
    )
    store.save()

    for name, summary in summaries.items():
        summary["key_id"] = trusted_id if summary["signed"] else ""
    return summaries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skills-root",
        type=Path,
        required=True,
        help=(
            "Skills root of the agent under eval (keypair + trust store land "
            "here). Required — never defaulted, to keep a developer's real "
            "~/.gaia/skills trust store out of eval runs by accident."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="Directory for the servable hub (default: fixture_hub/_prepared).",
    )
    parser.add_argument(
        "--unsigned",
        default="github-triage",
        help=(
            "Comma-separated skills to leave deliberately UNSIGNED (default: "
            "github-triage — the install-refusal scenario target). Pass '' to "
            "sign everything."
        ),
    )
    args = parser.parse_args(argv)

    unsigned = frozenset(
        piece.strip() for piece in args.unsigned.split(",") if piece.strip()
    )
    summaries = prepare(args.skills_root.resolve(), args.out.resolve(), unsigned)

    for name, summary in sorted(summaries.items()):
        state = f"signed (key {summary['key_id']})" if summary["signed"] else "UNSIGNED"
        print(f"PREPARED {name} {summary['version']}: {state}")
    print(f"HUB {args.out.resolve()}")
    print("Serve it and point GAIA_HUB_URL at the served origin; see README.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
