# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Copy what `gaia init` fetches for one agent from the hub into a folder.

The setup journey installs the GAIA assistant in every test. Fetched live that
is ~20 downloads of a 150 MB binary per OS, and one slow or dropped one stalls a
test on "Downloading". This fetches it once, with retries, and the journey
serves the folder as GAIA_HUB_URL (see e2e/playwright.config.ts).

    python installer/headless/hub_mirror.py --out <dir> [--agent gaia]

Writes the hub's own bytes at the hub's own paths: index.json, the agent's
manifest.json, and, for this machine, its gaia-agent.yaml and the artifact the
installer would pick, checked against the manifest's sha256. Reads
GAIA_HUB_URL like GAIA does; file:// works, for tests.
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Callable, Optional

import requests

from gaia.hub import catalog, installer

ARTIFACT_TIMEOUT_SECONDS = 60


def fetch_with_retries(
    fetch: Callable[[], bytes], what: str, attempts: int, backoff: float
) -> bytes:
    """*fetch*, retried on a dropped connection, timeout or 5xx; never on a 4xx."""
    for attempt in range(1, attempts + 1):
        try:
            return fetch()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status < 500 or attempt == attempts:
                raise
            problem = exc
        except requests.RequestException as exc:
            if attempt == attempts:
                raise
            problem = exc
        wait = backoff * 2 ** (attempt - 1)
        print(f"{what}: attempt {attempt} failed ({problem}); retrying in {wait:.0f} s")
        time.sleep(wait)
    raise AssertionError("unreachable")


def mirror(
    out: Path,
    agent_id: str = "gaia",
    *,
    attempts: int = 4,
    backoff: float = 5.0,
    platform_key: Optional[str] = None,
) -> Path:
    """Mirror *agent_id* into *out*; returns the artifact's path."""
    base = catalog.get_hub_base_url()

    def get(rel: str, timeout: int = catalog._HTTP_TIMEOUT) -> bytes:
        data = fetch_with_retries(
            lambda: catalog.fetch_bytes(f"{base}/{rel}", timeout=timeout),
            rel,
            attempts,
            backoff,
        )
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return data

    get("index.json")
    manifest_rel = f"agents/{agent_id}/manifest.json"
    manifest = json.loads(get(manifest_rel))
    version, artifact = installer._resolve_version(
        manifest, None, platform_key=platform_key
    )
    get(f"agents/{agent_id}/{version}/gaia-agent.yaml")
    data = get(artifact["path"], timeout=ARTIFACT_TIMEOUT_SECONDS)
    actual = hashlib.sha256(data).hexdigest()
    if actual != str(artifact.get("sha256", "")).lower():
        raise SystemExit(
            f"{base}/{artifact['path']} has sha256 {actual}, but {manifest_rel} "
            f"says {artifact.get('sha256')}. The hub or the download is corrupt; "
            "re-run, and report it at https://github.com/amd/gaia/issues if it persists."
        )
    print(f"mirrored {agent_id} {version} ({len(data)} bytes) from {base} into {out}")
    return out / artifact["path"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--agent", default="gaia")
    parser.add_argument("--attempts", type=int, default=4)
    args = parser.parse_args(argv)
    mirror(args.out, args.agent, attempts=args.attempts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
