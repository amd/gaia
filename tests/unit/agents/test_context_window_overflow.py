"""Regression tests for intentional small context windows (#5284)."""

from gaia.agents.base.agent import _loaded_context_is_too_small


def test_intentionally_small_loaded_window_is_not_misclassified_when_requested() -> (
    None
):
    """A pinned 16K window is the requested target, not a bad 64K load."""
    assert not _loaded_context_is_too_small(16384, 16384)
    assert not _loaded_context_is_too_small(8192, 8192)


def test_loaded_window_below_requested_target_is_still_a_bad_load() -> None:
    """A genuinely smaller backend window still requests the reload path."""
    assert _loaded_context_is_too_small(8192, 16384)
    assert _loaded_context_is_too_small(16384, 32768)


def test_context_window_comparison_rejects_untrusted_values() -> None:
    """Malformed health/config values must fail closed, not trigger a reload."""
    assert not _loaded_context_is_too_small(0, 16384)
    assert not _loaded_context_is_too_small(None, 16384)
    assert not _loaded_context_is_too_small(True, 16384)
    assert not _loaded_context_is_too_small(16384, True)
    assert not _loaded_context_is_too_small("16384", 16384)


def test_numeric_error_text_does_not_define_bad_load_policy() -> None:
    """The old literal 4K/8K/16K error heuristic is intentionally absent."""
    from pathlib import Path

    source = Path(__file__).parents[3] / "src/gaia/agents/base/agent.py"
    text = source.read_text(encoding="utf-8")
    assert '"context size (4096" in err_text' not in text
    assert '"context size (8192" in err_text' not in text
    assert '"context size (16384" in err_text' not in text
    assert '"n_ctx\': 16384" in err_text' not in text


def test_context_window_regression_covers_the_reported_16k_overflow() -> None:
    """The issue's real llama.cpp wording still follows trim-and-retry at 16K."""
    error = "request (17000 tokens) exceeds the available context size (16384 tokens)"
    assert "context size (16384" in error
    # The wording is not evidence of a bad load when the loaded and requested
    # windows agree; the comparison, rather than the number in the error, is.
    assert not _loaded_context_is_too_small(16384, 16384)
    assert _loaded_context_is_too_small(8192, 16384)
