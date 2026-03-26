import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
import sys

sys.path.insert(0, str(Path("src").absolute()))

from gaia.agents.base.orchestrator import AgentOrchestrator
from gaia.agents.registry import AgentRegistry

# Import the actual agent classes to test instantiation
from gaia.agents.code.agent import CodeAgent
from gaia.agents.chat.agent import ChatAgent
from gaia.agents.docker.agent import DockerAgent
from gaia.agents.jira.agent import JiraAgent
from gaia.agents.sd.agent import SDAgent
from gaia.agents.emr.agent import MedicalIntakeAgent
from gaia.agents.blender.agent import BlenderAgent
from gaia.agents.summarize.agent import SummarizerAgent

@pytest.fixture
def registry():
    reg = AgentRegistry()
    # Mock select_agent to just return what we pass as task description
    reg.select_agent = MagicMock(side_effect=lambda task_description, **kwargs: task_description)
    return reg

@pytest.fixture
def orchestrator(registry):
    return AgentOrchestrator(registry)

def test_orchestrator_routes_to_code(orchestrator):
    agent = orchestrator.route("code")
    assert isinstance(agent, CodeAgent)

def test_orchestrator_routes_to_chat(orchestrator):
    agent = orchestrator.route("chat")
    assert isinstance(agent, ChatAgent)

def test_orchestrator_routes_to_docker(orchestrator):
    agent = orchestrator.route("docker")
    assert isinstance(agent, DockerAgent)

def test_orchestrator_routes_to_jira(orchestrator):
    agent = orchestrator.route("jira")
    assert isinstance(agent, JiraAgent)

def test_orchestrator_routes_to_sd(orchestrator):
    agent = orchestrator.route("sd")
    assert isinstance(agent, SDAgent)

def test_orchestrator_routes_to_emr(orchestrator):
    agent = orchestrator.route("emr")
    assert isinstance(agent, MedicalIntakeAgent)

def test_orchestrator_routes_to_blender(orchestrator):
    agent = orchestrator.route("blender")
    assert isinstance(agent, BlenderAgent)

@patch("gaia.agents.summarize.agent.RAGSDK")
def test_orchestrator_routes_to_summarize(mock_rag, orchestrator):
    agent = orchestrator.route("summarize")
    assert isinstance(agent, SummarizerAgent)

def test_orchestrator_fallback_unknown(orchestrator, registry):
    registry.select_agent.side_effect = lambda **kwargs: "unknown_agent_id"
    agent = orchestrator.route("something random")
    assert isinstance(agent, ChatAgent)

def test_orchestrator_delegate(orchestrator):
    mock_from = MagicMock()
    # It delegates and returns the target agent (for now, synchronously)
    target = orchestrator.delegate(mock_from, "docker")
    assert isinstance(target, DockerAgent)

def test_orchestrator_chain(orchestrator):
    tasks = [{"task": "code"}, {"task": "docker"}]
    results = orchestrator.chain(tasks)
    assert len(results) == 2
    assert isinstance(results[0], CodeAgent)
    assert isinstance(results[1], DockerAgent)
