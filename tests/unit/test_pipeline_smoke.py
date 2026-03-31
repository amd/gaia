"""
Smoke tests for GAIA Pipeline Orchestration modules.

Verifies all public imports, core construction, and the quickstart
async pattern from docs/guides/pipeline.mdx execute without error.
No real LLM or external services are required.
"""

import asyncio

import pytest


class TestPipelineImports:
    """Verify every import shown in docs/sdk/infrastructure/pipeline.mdx resolves."""

    def test_import_pipeline_engine(self):
        from gaia.pipeline.engine import PipelineEngine  # noqa: F401

    def test_import_pipeline_context(self):
        from gaia.pipeline.state import PipelineContext  # noqa: F401

    def test_import_pipeline_snapshot(self):
        from gaia.pipeline.state import PipelineSnapshot  # noqa: F401

    def test_import_pipeline_state(self):
        from gaia.pipeline.state import PipelineState  # noqa: F401

    def test_import_audit_logger(self):
        from gaia.pipeline.audit_logger import AuditEventType, AuditLogger  # noqa: F401

    def test_import_defect_router(self):
        from gaia.pipeline.defect_router import (  # noqa: F401
            DefectRouter,
            DefectSeverity,
            DefectType,
            create_defect,
        )

    def test_import_defect_remediation_tracker(self):
        from gaia.pipeline.defect_remediation_tracker import (  # noqa: F401
            DefectRemediationTracker,
            DefectStatus,
        )

    def test_import_recursive_template(self):
        from gaia.pipeline.recursive_template import (  # noqa: F401
            RecursivePipelineTemplate,
            get_recursive_template,
        )

    def test_import_phase_contract(self):
        from gaia.pipeline.phase_contract import (  # noqa: F401
            PhaseContract,
            PhaseContractRegistry,
        )

    def test_import_pipeline_package_exports(self):
        import gaia.pipeline as pipeline_pkg  # noqa: F401

        assert hasattr(pipeline_pkg, "PipelineState")
        assert hasattr(pipeline_pkg, "PipelineContext")
        assert hasattr(pipeline_pkg, "AuditLogger")
        assert hasattr(pipeline_pkg, "DefectRouter")
        assert hasattr(pipeline_pkg, "DefectRemediationTracker")

    def test_metrics_collector_optional(self):
        try:
            from gaia.pipeline.metrics import MetricsCollector  # noqa: F401
        except ImportError:
            pass  # Expected — metrics.py is not yet implemented


class TestPipelineContextConstruction:

    def test_minimal_construction(self):
        from gaia.pipeline.state import PipelineContext

        ctx = PipelineContext(
            pipeline_id="smoke-001",
            user_goal="Build a REST API",
        )
        assert ctx.pipeline_id == "smoke-001"
        assert ctx.user_goal == "Build a REST API"
        assert 0.0 <= ctx.quality_threshold <= 1.0
        assert ctx.max_iterations > 0

    def test_full_construction(self):
        from gaia.pipeline.state import PipelineContext

        ctx = PipelineContext(
            pipeline_id="smoke-002",
            user_goal="Build a REST API with auth and tests",
            quality_threshold=0.75,
            max_iterations=3,
        )
        assert ctx.quality_threshold == 0.75
        assert ctx.max_iterations == 3


class TestPipelineStateEnum:

    def test_terminal_states(self):
        from gaia.pipeline.state import PipelineState

        assert PipelineState.COMPLETED.is_terminal()
        assert PipelineState.FAILED.is_terminal()
        assert PipelineState.CANCELLED.is_terminal()

    def test_active_states(self):
        from gaia.pipeline.state import PipelineState

        assert PipelineState.RUNNING.is_active()
        assert PipelineState.INITIALIZING.is_active()
        assert PipelineState.READY.is_active()
        assert PipelineState.PAUSED.is_active()

    def test_terminal_not_active(self):
        from gaia.pipeline.state import PipelineState

        assert not PipelineState.COMPLETED.is_active()
        assert not PipelineState.FAILED.is_active()


class TestAuditLoggerDemo:

    def test_audit_logger_chain(self):
        from gaia.pipeline.audit_logger import AuditEventType, AuditLogger

        audit = AuditLogger(logger_id="smoke-audit")
        audit.log(AuditEventType.PIPELINE_START, pipeline_id="demo-001")
        audit.log(AuditEventType.PHASE_ENTER, phase="PLANNING")
        audit.log(
            AuditEventType.AGENT_SELECTED, agent_id="planning-analysis-strategist"
        )
        audit.log(AuditEventType.PHASE_EXIT, phase="PLANNING")
        audit.log(AuditEventType.QUALITY_EVALUATED, payload={"score": 0.83})
        assert audit.verify_integrity() is True

    def test_audit_export_json(self):
        from gaia.pipeline.audit_logger import AuditEventType, AuditLogger

        audit = AuditLogger(logger_id="smoke-audit-json")
        audit.log(AuditEventType.PIPELINE_START, pipeline_id="x")
        exported = audit.export_log(format="json")
        assert isinstance(exported, str)
        assert len(exported) > 0


class TestQuickstartAsync:

    def test_quickstart_reaches_terminal_state(self):
        from gaia.pipeline.engine import PipelineEngine
        from gaia.pipeline.state import PipelineContext, PipelineState

        async def run():
            engine = PipelineEngine()
            context = PipelineContext(
                pipeline_id="smoke-quickstart-001",
                user_goal="Build a REST API with authentication and unit tests",
                quality_threshold=0.75,
                max_iterations=3,
            )
            await engine.initialize(context, config={"template": "rapid"})
            snapshot = await engine.start()
            engine.shutdown()
            return snapshot

        snapshot = asyncio.run(run())
        assert snapshot is not None
        assert snapshot.state is not None
        from gaia.pipeline.state import PipelineState

        assert snapshot.state.is_terminal()
