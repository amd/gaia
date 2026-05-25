import os
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest


def _can_run_e2e(backend_url="http://localhost:4200"):
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
def test_chat_index_pdf_e2e():
    """Run `gaia chat --index <pdf> --query` against a real Lemonade instance.

    Skips if ANTHROPIC_API_KEY is not set or Lemonade backend is unreachable.
    """
    can_run, reason = _can_run_e2e()
    if not can_run:
        pytest.skip(f"Skipping E2E: {reason}")

    pdf_path = "eval/corpus/documents/safety_handbook_large.pdf"
    assert os.path.exists(pdf_path), f"PDF fixture missing: {pdf_path}"

    cmd = [
        "gaia",
        "chat",
        "--index",
        pdf_path,
        "--query",
        "What does the document say about water?",
        "--base-url",
        "http://localhost:4200",
    ]

    start = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - start

    assert (
        proc.returncode == 0
    ), f"gaia chat failed: stdout={proc.stdout[:200]} stderr={proc.stderr[:200]}"
    cap = 900
    assert elapsed < cap, f"gaia chat run took too long: {elapsed:.1f}s (cap {cap}s)"
    assert "water" in proc.stdout.lower() or "water" in proc.stderr.lower()
