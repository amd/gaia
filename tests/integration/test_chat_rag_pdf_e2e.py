import shutil
import subprocess
import time

import pytest


def _claude_available():
    return shutil.which("claude") is not None


@pytest.mark.integration
def test_chat_rag_pdf_e2e_short_timeout():
    """Run the safety_handbook_water scenario end-to-end against local Lemonade.

    Skips if the 'claude' CLI is not available on PATH (self-hosted runner requirement).
    Asserts the command completes within 90s and produces non-empty output.
    """
    if not _claude_available():
        pytest.skip(
            "'claude' CLI not found; skipping E2E that requires self-hosted runner"
        )

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

    assert elapsed < 90, f"E2E run took too long: {elapsed:.1f}s"
    # Expect non-empty stdout or stderr indicating run progress/results
    assert (
        proc.returncode == 0
    ), f"Eval command failed: stdout={proc.stdout[:200]} stderr={proc.stderr[:200]}"
    assert proc.stdout or proc.stderr
