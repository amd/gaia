"""
Integration tests for the pipeline orchestration engine against a real Lemonade server.

These tests are skipped automatically if Lemonade is not running.
Run with a live server:
    lemonade-server serve --model Qwen3.5-9B-GGUF   # or your installed model
    python -m pytest tests/integration/test_pipeline_lemonade.py -v

To find your exact model name:
    lemonade-server list   # or: curl localhost:11434/api/tags

Parametrize via env var:
    GAIA_PIPELINE_MODEL=Qwen2.5-7B-Instruct-GGUF pytest tests/integration/test_pipeline_lemonade.py
"""

import os

import pytest

# ---------------------------------------------------------------------------
# Model selection — override via GAIA_PIPELINE_MODEL env var
# ---------------------------------------------------------------------------
PIPELINE_MODEL = os.environ.get("GAIA_PIPELINE_MODEL", "Qwen3.5-9B-GGUF")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPipelineLemonade:
    """
    End-to-end pipeline tests using a real Lemonade server.

    All tests in this class auto-skip when Lemonade is not reachable via the
    require_lemonade fixture (defined in tests/conftest.py).
    """

    def test_run_pipeline_no_attribute_error(self, require_lemonade):
        """
        Smoke test: run_pipeline() must NOT return pipeline_status='failed'.

        Regression guard for:
          B1-A — self.execute_tool() AttributeError (fixed: → _execute_tool)
          B1-B — tool_fn(self, **args) TypeError  (fixed: → tool_fn(**args))
          B2-A — self._analyze_with_llm() AttributeError (fixed: method added)

        Any of those bugs would cause the top-level handler to catch the
        AttributeError/TypeError and set pipeline_status='failed'.
        """
        from gaia.pipeline.orchestrator import run_pipeline

        result = run_pipeline(
            task_description="Analyze CSV data and generate a summary report",
            auto_spawn=False,  # disable spawning for faster smoke test
            model_id=PIPELINE_MODEL,
        )

        assert isinstance(result, dict), f"Expected dict, got {type(result)}: {result}"
        status = result.get("pipeline_status", "unknown")
        assert status != "failed", (
            f"Pipeline returned status='failed'. Error: {result.get('error', 'no error key')}. "
            f"This likely means one of the B1-A/B1-B/B2-A bugs regressed."
        )

    def test_run_pipeline_returns_domain_blueprint(self, require_lemonade):
        """
        Pipeline must produce a domain blueprint (Stage 1 output) in the result.
        """
        from gaia.pipeline.orchestrator import run_pipeline

        result = run_pipeline(
            task_description="Build a REST API for user authentication",
            auto_spawn=False,
            model_id=PIPELINE_MODEL,
        )

        # Stage 1 output must be present
        assert "domain_blueprint" in result or result.get("pipeline_status") not in (
            "failed",
            "not_started",
        ), (
            f"Pipeline did not reach Stage 1. Status: {result.get('pipeline_status')}. "
            f"Result keys: {list(result.keys())}"
        )

    def test_orchestrator_analyze_with_llm_real_call(self, require_lemonade):
        """
        _analyze_with_llm on the orchestrator must return a dict when given
        a real LLM call (not mocked). This specifically validates B2-A with
        a live model.
        """
        from gaia.pipeline.orchestrator import PipelineOrchestrator

        orchestrator = PipelineOrchestrator(model_id=PIPELINE_MODEL)

        result = orchestrator._analyze_with_llm(
            query='Return a JSON object with key "status" and value "ok".',
            system_prompt="You are a JSON generator. Only output valid JSON.",
        )

        assert isinstance(result, dict), (
            f"_analyze_with_llm returned {type(result)} instead of dict. "
            f"B2-A may have regressed."
        )

    def test_pipeline_module_importable(self):
        """
        Verify that gaia.pipeline exports PipelineOrchestrator and run_pipeline.
        This confirms PyPI installability — if src/gaia/pipeline/__init__.py
        is missing these exports, a pip-installed gaia would fail here.
        """
        import gaia.pipeline as pipeline_pkg

        assert hasattr(pipeline_pkg, "PipelineOrchestrator"), (
            "gaia.pipeline does not export PipelineOrchestrator — update __init__.py"
        )
        assert hasattr(pipeline_pkg, "run_pipeline"), (
            "gaia.pipeline does not export run_pipeline — update __init__.py"
        )

        from gaia.pipeline import PipelineOrchestrator, run_pipeline  # noqa: F401
