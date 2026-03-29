"""
GAIA Agent Context Definitions

Data classes for agent definitions, capabilities, triggers, and constraints.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Dict, List, Any, Optional, Callable, Tuple


class AgentState(Enum):
    """Agent execution states."""

    IDLE = auto()
    RUNNING = auto()
    PAUSED = auto()
    COMPLETED = auto()
    FAILED = auto()


@dataclass
class AgentCapabilities:
    """
    Agent capabilities definition.

    Attributes:
        capabilities: List of capability names
        tools: List of tool names the agent can use
        execution_targets: Target execution environments
    """

    capabilities: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    execution_targets: Dict[str, str] = field(default_factory=dict)


@dataclass
class AgentTriggers:
    """
    Agent trigger conditions.

    Attributes:
        keywords: Keywords that activate this agent
        phases: Pipeline phases where agent is active
        complexity_range: (min, max) complexity range
        state_conditions: State-based activation conditions
        defect_types: Defect types that trigger this agent
    """

    keywords: List[str] = field(default_factory=list)
    phases: List[str] = field(default_factory=list)
    complexity_range: Tuple[float, float] = (0.0, 1.0)
    state_conditions: Dict[str, Any] = field(default_factory=dict)
    defect_types: List[str] = field(default_factory=list)


@dataclass
class AgentConstraints:
    """
    Agent execution constraints.

    Attributes:
        timeout_seconds: Maximum execution time in seconds
        max_file_changes: Maximum number of files to change
        max_lines_per_file: Maximum lines to change per file
        requires_review: Whether the agent requires review
        max_steps: Maximum execution steps
    """

    timeout_seconds: Optional[int] = None
    max_file_changes: int = 10
    max_lines_per_file: int = 100
    requires_review: bool = False
    max_steps: int = 100


@dataclass
class AgentDefinition:
    """
    Complete agent definition.

    Attributes:
        id: Unique agent identifier
        name: Human-readable agent name
        description: Agent purpose and capabilities
        capabilities: Agent capabilities
        triggers: Activation triggers
        constraints: Execution constraints
        metadata: Additional metadata
        version: Agent version
        category: Agent category
        enabled: Whether agent is enabled
        system_prompt: Default system prompt
        tools: List of tool names
        execution_targets: Target execution environments
    """

    id: str
    name: str
    description: str
    version: str = "1.0.0"
    category: str = ""
    enabled: bool = True
    system_prompt: str = ""
    tools: List[str] = field(default_factory=list)
    execution_targets: Dict[str, str] = field(default_factory=dict)
    capabilities: AgentCapabilities = field(default_factory=AgentCapabilities)
    triggers: AgentTriggers = field(default_factory=AgentTriggers)
    constraints: AgentConstraints = field(default_factory=AgentConstraints)
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseAgent(ABC):
    """
    Abstract base agent for pipeline orchestration.

    This is different from the main Agent class - it's designed for
    pipeline phase execution rather than interactive chat.
    """

    def __init__(
        self,
        agent_id: str,
        name: str,
        description: str,
        capabilities: Optional[AgentCapabilities] = None,
        triggers: Optional[AgentTriggers] = None,
        constraints: Optional[AgentConstraints] = None,
    ):
        self.agent_id = agent_id
        self.name = name
        self.description = description
        self.capabilities = capabilities or AgentCapabilities()
        self.triggers = triggers or AgentTriggers()
        self.constraints = constraints or AgentConstraints()
        self.state = AgentState.IDLE

    @abstractmethod
    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the agent's primary function."""
        pass

    def can_handle(self, task: str, phase: str, state: Dict[str, Any]) -> bool:
        """Check if this agent can handle a given task."""
        # Check phase match
        if phase not in self.triggers.phases:
            return False

        # Check keywords
        task_lower = task.lower()
        if self.triggers.keywords:
            if not any(kw.lower() in task_lower for kw in self.triggers.keywords):
                return False

        # Check complexity
        complexity = state.get("complexity", 0.5)
        min_complex, max_complex = self.triggers.complexity_range
        if not (min_complex <= complexity <= max_complex):
            return False

        return True
