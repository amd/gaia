import os
import subprocess
import time
from urllib.request import urlopen
from urllib.error import URLError

import pytest


def _can_run_e2e(backend_url="http://localhost:4200"):
    # Require Anthropic judge key and reachable Lemonade backend health endpoint.
    if not os.getenv("ANTHROPIC_API_KEY"):
        return False, "ANTHROPIC_API_KEY not set"
    try:
        with urlopen(f"{backend_url}/api/health", timeout=2) as r:
            if r.status != 200:
                return False, f"backend health returned {r.status}"
    except URLError as e:
        return False, f"backend not reachable: {e}"
    return True, "ok"


@pytest.mark.integration
def test_chat_rag_pdf_e2e_short_timeout():
    """Run the safety_handbook_water scenario end-to-end against local Lemonade.

    Skips if ANTHROPIC_API_KEY is not set or the Lemonade backend is unreachable.
    Asserts the command exits successfully and completes within a generous timeout.
    """
    can_run, reason = _can_run_e2e()
    if not can_run:
        pytest.skip(f"Skipping E2E: {reason}")

    cmd = [
        "gaia",
        "eval",
        "agent",
        "--scenario",
        "safety_handbook_water",
        "--compare",
        "tests/fixtures/eval_baselines/gemma-4-e4b-d71cd914/scorecard_rag_quality.json",
    ]

    start = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - start

    # First ensure the command succeeded; if it failed, surface stdout/stderr.
    assert proc.returncode == 0, f"Eval command failed: stdout={proc.stdout[:200]} stderr={proc.stderr[:200]}"

    # Then assert the run finished within a generous cap (10 minutes) to avoid flakes.
    cap = 600
    assert elapsed < cap, f"E2E run took too long: {elapsed:.1f}s (cap {cap}s)"

    # Expect non-empty stdout or stderr indicating run progress/results
    assert proc.stdout or proc.stderr
