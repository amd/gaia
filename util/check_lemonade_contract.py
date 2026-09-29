# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Check a running Lemonade Server against the contract GAIA actually relies on.

Pointed at a *candidate* build by ``.github/workflows/lemonade-candidate-smoke.yml``,
this answers one question a week early: would the next Lemonade release break GAIA?

It checks the two things that have actually broken:

* **The version string still parses.** v2026.39.1 switched the format from ``X.Y.Z``
  to ``YYYY.WW.N``. GAIA's gates turned the new shape into "cannot tell" and stopped
  checking, silently. Parsing is therefore not enough — the gate has to return a real
  verdict, so an unparseable version is a failure here rather than a shrug.
* **The health payload still carries the fields GAIA reads** — the top-level keys
  always, and the nested ``all_models_loaded[N].recipe_options.ctx_size`` when a
  model happens to be loaded. ``ctx_size`` already moved once, in 9.1.4, out of
  the top level into that nested shape.

  A freshly started server has no model loaded, so the nested check usually does
  not run. It SAYS so rather than reporting a pass it did not earn — load a model
  first if you want that covered.

Run it against any base URL::

    python util/check_lemonade_contract.py http://localhost:8000/api/v1

Pass ``--api-key`` (or set ``LEMONADE_API_KEY``) for a server that requires one —
GAIA's own embedded instance generates a key and answers 401 without it.

Exits 0 when the contract holds, 1 when it does not, and names what moved.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

# Keys GAIA reads off /health. "version" feeds every version gate; the agent
# readiness probe and LemonadeClient both read the loaded-model list to recover
# the context size a model was actually loaded at.
REQUIRED_HEALTH_KEYS = ("version", "all_models_loaded")


class ContractError(RuntimeError):
    """The running server no longer matches what GAIA depends on."""


def fetch_health(
    base_url: str, api_key: str | None = None, timeout: int = 30
) -> dict[str, Any]:
    """GET <base_url>/health, or raise ContractError naming the failure."""
    url = f"{base_url.rstrip('/')}/health"
    headers = {"User-Agent": "GAIA-contract/1.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        hint = ""
        if e.code in (401, 403):
            hint = " — pass --api-key or set LEMONADE_API_KEY"
        raise ContractError(f"{url} returned HTTP {e.code}{hint}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ContractError(f"{url} is not reachable: {e}") from e
    except json.JSONDecodeError as e:
        raise ContractError(f"{url} did not return JSON: {e}") from e


def check_health_shape(payload: dict[str, Any]) -> list[str]:
    """Return one finding per required key the payload no longer carries."""
    return [
        f"/health no longer carries {key!r} — GAIA reads it "
        f"(present: {sorted(payload)})"
        for key in REQUIRED_HEALTH_KEYS
        if key not in payload
    ]


def check_loaded_model_shape(payload: dict[str, Any]) -> tuple[list[str], bool]:
    """Check the nested shape GAIA reads a loaded model's context size out of.

    Returns ``(findings, checked)``. ``checked`` is False when no model is
    loaded — the caller reports that rather than counting it as a pass, because
    this is the exact shape that moved in 9.1.4 and an unchecked field looks
    identical to an intact one.
    """
    loaded = payload.get("all_models_loaded") or []
    if not isinstance(loaded, list) or not loaded:
        return [], False

    findings: list[str] = []
    for entry in loaded:
        if not isinstance(entry, dict):
            findings.append(
                f"all_models_loaded holds a {type(entry).__name__}, not an object"
            )
            continue
        options = entry.get("recipe_options")
        if not isinstance(options, dict):
            findings.append(
                "all_models_loaded[].recipe_options is missing — GAIA reads the "
                "loaded context size from there (9.1.4 moved it out of the top "
                f"level). Entry keys: {sorted(entry)}"
            )
        elif "ctx_size" not in options:
            findings.append(
                "all_models_loaded[].recipe_options has no 'ctx_size' — GAIA "
                f"reads it to confirm the window. Keys: {sorted(options)}"
            )
    return findings, True


def check_version_gate(version: str | None) -> list[str]:
    """Return findings if GAIA's version gate cannot reach a verdict on ``version``.

    Imported here rather than at module scope so ``--help`` works without the
    package installed.
    """
    from gaia.agents.base.readiness import parse_version, version_meets_min
    from gaia.version import LEMONADE_MIN_VERSION

    if not version:
        return ["/health reported no version — every GAIA version gate goes blind"]

    findings: list[str] = []
    parsed = parse_version(version)
    if parsed is None:
        findings.append(
            f"GAIA cannot parse the reported version {version!r}. Its gates treat "
            f"that as indeterminate and do not block, so they stop checking "
            f"without saying so — see gaia.agents.base.readiness.parse_version."
        )
        return findings

    verdict = version_meets_min(version, LEMONADE_MIN_VERSION)
    if verdict is None:
        findings.append(
            f"version_meets_min({version!r}, {LEMONADE_MIN_VERSION!r}) is "
            f"indeterminate even though the version parsed to {parsed}."
        )
    elif verdict is False:
        findings.append(
            f"The candidate reports {version!r}, below GAIA's supported floor "
            f"{LEMONADE_MIN_VERSION}. Expected for a genuine downgrade; otherwise "
            f"the version format changed in a way that sorts wrong."
        )
    return findings


def run(base_url: str, api_key: str | None = None) -> tuple[list[str], bool]:
    """Collect every contract finding.

    Returns ``(findings, nested_checked)``. An empty findings list means the
    contract holds as far as it was exercised — ``nested_checked`` says whether
    the loaded-model shape was among that.
    """
    payload = fetch_health(base_url, api_key=api_key)
    findings = check_health_shape(payload)
    findings += check_version_gate(payload.get("version"))
    nested, nested_checked = check_loaded_model_shape(payload)
    findings += nested
    return findings, nested_checked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base_url", help="e.g. http://localhost:8000/api/v1")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("LEMONADE_API_KEY"),
        help="Bearer token; defaults to $LEMONADE_API_KEY.",
    )
    args = parser.parse_args()

    try:
        findings, nested_checked = run(args.base_url, api_key=args.api_key)
    except ContractError as e:
        print(f"[FAIL] {e}")
        return 1

    if findings:
        print(f"[FAIL] Lemonade no longer matches GAIA's contract ({len(findings)}):")
        for finding in findings:
            print(f"  - {finding}")
        return 1

    print("[OK] Lemonade matches the contract GAIA depends on.")
    if not nested_checked:
        print(
            "[NOTE] No model was loaded, so the nested "
            "all_models_loaded[].recipe_options.ctx_size shape was NOT checked. "
            "Load a model before running this to cover it."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
