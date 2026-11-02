#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Check a real first-run setup, for .github/workflows/test_setup_real_download.yml.

    python check_real_setup.py --cold
    python check_real_setup.py --url http://127.0.0.1:4200 --chat-model <id>

--cold fails unless this machine has no GAIA home and no model on disk, which
is the state that workflow's download has to start from. --url asks the running
backend the question the app asks: is setup done, and do the models really
load? It fails naming what is not.

Stdlib only: it runs on stock GitHub-hosted runners.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

START_TIMEOUT_S = 120
# Loading each real model on a hosted runner's CPU.
CHECK_TIMEOUT_S = 900


def problems_with_cold_start(home: Path, hf_cache: Path) -> List[str]:
    """Why this machine isn't a first run; empty when it is."""
    problems = []
    if (home / ".gaia").exists():
        problems.append(f"{home / '.gaia'} already exists")
    if hf_cache.is_dir() and any(hf_cache.glob("models--*")):
        problems.append(f"{hf_cache} already holds models")
    return problems


def problems_with_setup(
    check: Dict[str, Any], plan: Dict[str, Any], chat: str
) -> List[str]:
    """Why the backend's answers don't show a finished setup; empty when they do."""
    problems = []
    if not check.get("ready"):
        reasons = "; ".join(check.get("reasons") or []) or "no reason given"
        problems.append(f"setup is not ready (stage {check.get('stage')}): {reasons}")
    if (plan.get("chat_model") or {}).get("id") != chat:
        problems.append(f"the chat model is {plan.get('chat_model')}, not {chat}")
    if not (plan.get("chat_model") or {}).get("downloaded"):
        problems.append(f"{chat} is not downloaded")
    if not (plan.get("lemonade") or {}).get("reachable"):
        problems.append("the backend can't reach the model server")
    return problems


def _get(url: str, timeout: float) -> Dict[str, Any]:
    request = urllib.request.Request(url, headers={"X-Gaia-UI": "1"})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return json.load(reply)


def _wait_for(base: str) -> None:
    deadline = time.monotonic() + START_TIMEOUT_S
    while True:
        try:
            _get(f"{base}/api/health", 5)
            return
        except (urllib.error.URLError, OSError) as e:
            if time.monotonic() > deadline:
                raise SystemExit(
                    f"error: no backend at {base} after {START_TIMEOUT_S} s: {e}"
                ) from e
            time.sleep(1)


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--cold", action="store_true", help="fail unless this is a first run"
    )
    ap.add_argument("--url", help="the running backend to ask")
    ap.add_argument(
        "--chat-model", help="with --url: the chat model setup must have used"
    )
    args = ap.parse_args(argv)
    if not args.cold and not (args.url and args.chat_model):
        ap.error("pass --cold, or --url with --chat-model")

    if args.cold:
        hf_home = os.environ.get("HF_HOME")
        cache = (
            Path(hf_home) / "hub"
            if hf_home
            else Path.home() / ".cache" / "huggingface" / "hub"
        )
        problems = problems_with_cold_start(Path.home(), cache)
    else:
        _wait_for(args.url)
        check = _get(f"{args.url}/api/setup/check?load=true", CHECK_TIMEOUT_S)
        plan = _get(f"{args.url}/api/setup/plan", 60)
        print(
            json.dumps({"check": check, "chat_model": plan.get("chat_model")}, indent=2)
        )
        problems = problems_with_setup(check, plan, args.chat_model)

    for problem in problems:
        print(f"::error::{problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
