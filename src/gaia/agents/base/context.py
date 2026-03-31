"""
GAIA Agent Context Definitions

Data classes for agent definitions, capabilities, triggers, and constraints.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple


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
        max_file_changes: Maximum number of files the agent may modify
        max_lines_per_file: Maximum lines allowed per file change
        requires_review: Whether changes require human review before applying
        timeout_seconds: Maximum execution time in seconds
        max_steps: Maximum number of execution steps
    """

    max_file_changes: int = 20
    max_lines_per_file: int = 500
    requires_review: bool = True
    timeout_seconds: int = 300
    max_steps: int = 100


@dataclass
class AgentResult:
    """
    Result from agent execution.

    Attributes:
        agent_id: Agent that produced this result
        success: Whether execution succeeded
        artifact: Output artifact
        output: Text output
        errors: List of errors
        metadata: Additional metadata
    """

    agent_id: str
    success: bool = True
    artifact: Any = None
    output: str = ""
    errors: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentDefinition:
    """
    Complete agent definition.

    Attributes:
        id: Unique agent identifier
        name: Human-readable agent name
        version: Agent version string
        category: Agent category/classification
        description: Agent purpose and capabilities
        capabilities: Agent capabilities
        triggers: Activation triggers
        system_prompt: System prompt used to initialize the agent
        tools: List of tool names available to the agent
        execution_targets: Target execution environments keyed by name
        constraints: Execution constraints
        metadata: Additional metadata
        enabled: Whether this agent definition is active
    """

    id: str
    name: str
    version: str
    category: str
    description: str
    capabilities: AgentCapabilities = field(default_factory=AgentCapabilities)
    triggers: AgentTriggers = field(default_factory=AgentTriggers)
    system_prompt: str = ""
    tools: List[str] = field(default_factory=list)
    execution_targets: Dict[str, Any] = field(default_factory=dict)
    constraints: AgentConstraints = field(default_factory=AgentConstraints)
    metadata: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    load_count: int = 0
    last_used: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "category": self.category,
            "description": self.description,
            "capabilities": self.capabilities.capabilities,
            "tools": self.tools,
            "execution_targets": self.execution_targets,
            "system_prompt": self.system_prompt,
            "triggers": {
                "keywords": self.triggers.keywords,
                "phases": self.triggers.phases,
                "complexity_range": list(self.triggers.complexity_range),
            },
            "constraints": {
                "max_file_changes": self.constraints.max_file_changes,
                "max_lines_per_file": self.constraints.max_lines_per_file,
                "requires_review": self.constraints.requires_review,
                "timeout_seconds": self.constraints.timeout_seconds,
                "max_steps": self.constraints.max_steps,
            },
            "metadata": self.metadata,
            "enabled": self.enabled,
            "load_count": self.load_count,
            "last_used": self.last_used.isoformat() if self.last_used else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentDefinition":
        """Create AgentDefinition from dictionary."""
        agent_data = data.get("agent", data)
        triggers_data = agent_data.get("triggers", {})
        constraints_data = agent_data.get("constraints", {})
        complexity = triggers_data.get("complexity_range", {})
        if isinstance(complexity, dict):
            complexity_range = (complexity.get("min", 0.0), complexity.get("max", 1.0))
        elif isinstance(complexity, (list, tuple)) and len(complexity) == 2:
            complexity_range = tuple(complexity)
        else:
            complexity_range = (0.0, 1.0)
        return cls(
            id=agent_data.get("id", ""),
            name=agent_data.get("name", ""),
            version=agent_data.get("version", "1.0.0"),
            category=agent_data.get("category", ""),
            description=agent_data.get("description", ""),
            capabilities=AgentCapabilities(
                capabilities=agent_data.get("capabilities", []),
                tools=agent_data.get("tools", []),
                execution_targets=agent_data.get("execution_targets", {}),
            ),
            triggers=AgentTriggers(
                keywords=triggers_data.get("keywords", []),
                phases=triggers_data.get("phases", []),
                complexity_range=complexity_range,
            ),
            system_prompt=agent_data.get("system_prompt", ""),
            tools=agent_data.get("tools", []),
            execution_targets=agent_data.get("execution_targets", {}),
            constraints=AgentConstraints(
                max_file_changes=constraints_data.get("max_file_changes", 20),
                max_lines_per_file=constraints_data.get("max_lines_per_file", 500),
                requires_review=constraints_data.get("requires_review", True),
                timeout_seconds=constraints_data.get("timeout_seconds", 300),
                max_steps=constraints_data.get("max_steps", 100),
            ),
            metadata=agent_data.get("metadata", {}),
            enabled=agent_data.get("enabled", True),
        )


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

    async def validate_input(self, task: str, context: Dict[str, Any]) -> tuple:
        """Validate input before execution. Returns (is_valid, errors)."""
        errors = []
        if not task or not task.strip():
            errors.append("Task description cannot be empty")
        return len(errors) == 0, errors

    async def process_output(self, result: Dict[str, Any]) -> "AgentResult":
        """Process raw execution output into AgentResult."""
        return AgentResult(
            agent_id=self.agent_id,
            success=result.get("success", True),
            artifact=result.get("artifact"),
            output=result.get("output", ""),
            errors=result.get("errors", []),
            metadata=result.get("metadata", {}),
        )

    def get_info(self) -> Dict[str, Any]:
        """Get agent information summary."""
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "description": self.description,
            "state": self.state.name,
            "capabilities": self.capabilities.capabilities,
            "triggers": {
                "keywords": self.triggers.keywords,
                "phases": self.triggers.phases,
            },
        }

    def _set_state(self, state: "AgentState") -> None:
        """Set agent state."""
        self.state = state

    def _set_error(self, error: str) -> None:
        """Set agent to failed state with error message."""
        self.state = AgentState.FAILED
