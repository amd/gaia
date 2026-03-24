"""
Pytest fixtures for GAIA tests.
"""

import pytest
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional

from gaia.pipeline.state import PipelineContext, PipelineStateMachine, PipelineState
from gaia.pipeline.loop_manager import LoopManager, LoopConfig
from gaia.pipeline.decision_engine import DecisionEngine, DecisionType
from gaia.quality.scorer import QualityScorer
from gaia.agents.registry import AgentRegistry
from gaia.hooks.registry import HookRegistry, HookExecutor
from gaia.hooks.base import HookContext


@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_context() -> PipelineContext:
    """Create a sample pipeline context for testing."""
    return PipelineContext(
        pipeline_id="test-pipeline-001",
        user_goal="Implement a REST API endpoint",
        template="STANDARD",
        quality_threshold=0.90,
        max_iterations=5,
        concurrent_loops=3,
    )


@pytest.fixture
def sample_state_machine(sample_context: PipelineContext) -> PipelineStateMachine:
    """Create a sample state machine for testing."""
    return PipelineStateMachine(sample_context)


@pytest.fixture
def sample_loop_config() -> LoopConfig:
    """Create a sample loop configuration for testing."""
    return LoopConfig(
        loop_id="test-loop-001",
        phase_name="DEVELOPMENT",
        agent_sequence=["senior-developer", "quality-reviewer"],
        exit_criteria={"quality_threshold": 0.90},
        quality_threshold=0.90,
        max_iterations=3,
        timeout_seconds=60,
    )


@pytest.fixture
def sample_loop_manager() -> LoopManager:
    """Create a sample loop manager for testing."""
    return LoopManager(max_concurrent=5)


@pytest.fixture
def sample_decision_engine() -> DecisionEngine:
    """Create a sample decision engine for testing."""
    return DecisionEngine(
        config={
            "critical_patterns": ["security", "data loss", "breaking change"]
        }
    )


@pytest.fixture
def sample_quality_scorer() -> QualityScorer:
    """Create a sample quality scorer for testing."""
    return QualityScorer()


@pytest.fixture
def sample_agent_registry(tmp_path) -> AgentRegistry:
    """Create a sample agent registry for testing."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    return AgentRegistry(agents_dir=str(agents_dir), auto_reload=False)


@pytest.fixture
def sample_hook_registry() -> HookRegistry:
    """Create a sample hook registry for testing."""
    return HookRegistry()


@pytest.fixture
def sample_hook_executor(sample_hook_registry: HookRegistry) -> HookExecutor:
    """Create a sample hook executor for testing."""
    return HookExecutor(sample_hook_registry)


@pytest.fixture
def sample_hook_context() -> HookContext:
    """Create a sample hook context for testing."""
    return HookContext(
        event="TEST_EVENT",
        pipeline_id="test-pipeline-001",
        phase="DEVELOPMENT",
        agent_id="test-agent",
        state={"key": "value"},
        data={"test_data": "test"},
    )


@pytest.fixture
def sample_code() -> str:
    """Sample Python code for testing."""
    return """
def add(a: int, b: int) -> int:
    '''Add two numbers.'''
    return a + b

def multiply(a: int, b: int) -> int:
    '''Multiply two numbers.'''
    return a * b

class Calculator:
    '''Simple calculator class.'''

    def __init__(self):
        self.result = 0

    def calculate(self, operation: str, a: int, b: int) -> int:
        '''Perform a calculation.'''
        if operation == 'add':
            self.result = add(a, b)
        elif operation == 'multiply':
            self.result = multiply(a, b)
        return self.result
"""


@pytest.fixture
def sample_code_with_issues() -> str:
    """Sample Python code with quality issues for testing."""
    return """
def add(a,b):
    return a+b

def multiply(a,b):
    return a*b

# No docstrings
# No type hints
# Inconsistent spacing

class Calculator:
    def __init__(self):
        self.result=0

    def calculate(self,operation,a,b):
        if operation=='add':
            self.result=add(a,b)
        elif operation=='multiply':
            self.result=multiply(a,b)
        return self.result
"""


@pytest.fixture
def sample_requirements() -> list:
    """Sample requirements for testing."""
    return [
        "Create a REST API endpoint for user management",
        "Implement CRUD operations for users",
        "Add input validation for user data",
        "Include error handling for all endpoints",
    ]


@pytest.fixture
def sample_quality_context() -> Dict[str, Any]:
    """Sample context for quality evaluation."""
    return {
        "requirements": ["Build a REST API"],
        "language": "python",
        "template": "STANDARD",
    }
