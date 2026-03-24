"""
GAIA Base Agent

Base class and definitions for GAIA agents.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Dict, List, Any, Optional, Callable


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
    """

    keywords: List[str] = field(default_factory=list)
    phases: List[str] = field(default_factory=list)
    complexity_range: tuple = (0.0, 1.0)


@dataclass
class AgentConstraints:
    """
    Agent execution constraints.

    Attributes:
        max_file_changes: Maximum files to change per execution
        max_lines_per_file: Maximum lines per file
        requires_review: Whether output requires review
        timeout_seconds: Execution timeout
    """

    max_file_changes: int = 20
    max_lines_per_file: int = 500
    requires_review: bool = True
    timeout_seconds: int = 300


@dataclass
class AgentDefinition:
    """
    Complete agent definition.

    Attributes:
        id: Unique agent identifier
        name: Human-readable name
        version: Agent version
        category: Agent category (planning, development, review, management)
        description: Agent description
        triggers: Trigger conditions
        capabilities: Agent capabilities
        system_prompt: System prompt content
        tools: Available tools
        execution_targets: Execution target configuration
        constraints: Execution constraints
        metadata: Additional metadata
        enabled: Whether agent is enabled
        load_count: Number of times loaded
        last_used: Last usage timestamp
    """

    id: str
    name: str
    version: str
    category: str
    description: str
    triggers: AgentTriggers = field(default_factory=AgentTriggers)
    capabilities: AgentCapabilities = field(default_factory=AgentCapabilities)
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
            "triggers": {
                "keywords": self.triggers.keywords,
                "phases": self.triggers.phases,
                "complexity_range": self.triggers.complexity_range,
            },
            "capabilities": {
                "capabilities": self.capabilities.capabilities,
                "tools": self.capabilities.tools,
                "execution_targets": self.capabilities.execution_targets,
            },
            "system_prompt": self.system_prompt,
            "tools": self.tools,
            "execution_targets": self.execution_targets,
            "constraints": {
                "max_file_changes": self.constraints.max_file_changes,
                "max_lines_per_file": self.constraints.max_lines_per_file,
                "requires_review": self.constraints.requires_review,
                "timeout_seconds": self.constraints.timeout_seconds,
            },
            "metadata": self.metadata,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentDefinition":
        """Create from dictionary."""
        triggers_data = data.get("triggers", {})
        capabilities_data = data.get("capabilities", {})
        constraints_data = data.get("constraints", {})

        return cls(
            id=data.get("id", data.get("agent", {}).get("id", "")),
            name=data.get("name", data.get("agent", {}).get("name", "")),
            version=data.get("version", data.get("agent", {}).get("version", "1.0.0")),
            category=data.get("category", data.get("agent", {}).get("category", "")),
            description=data.get("description", data.get("agent", {}).get("description", "")),
            triggers=AgentTriggers(
                keywords=triggers_data.get("keywords", []),
                phases=triggers_data.get("phases", []),
                complexity_range=tuple(triggers_data.get("complexity_range", [0.0, 1.0])),
            ),
            capabilities=AgentCapabilities(
                capabilities=capabilities_data.get("capabilities", []),
                tools=capabilities_data.get("tools", []),
                execution_targets=capabilities_data.get("execution_targets", {}),
            ),
            system_prompt=data.get("system_prompt", data.get("agent", {}).get("system_prompt", "")),
            tools=data.get("tools", []),
            execution_targets=data.get("execution_targets", {}),
            constraints=AgentConstraints(
                max_file_changes=constraints_data.get("max_file_changes", 20),
                max_lines_per_file=constraints_data.get("max_lines_per_file", 500),
                requires_review=constraints_data.get("requires_review", True),
                timeout_seconds=constraints_data.get("timeout_seconds", 300),
            ),
            metadata=data.get("metadata", {}),
            enabled=data.get("enabled", True),
        )


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


class BaseAgent(ABC):
    """
    Abstract base class for all GAIA agents.

    Agents are specialized AI assistants that handle specific tasks
    within the pipeline. Each agent has:
    - A unique identifier
    - Specific capabilities and tools
    - Trigger conditions for activation
    - Execution constraints

    Subclasses must implement:
    - execute(): Main execution method
    - validate_input(): Input validation
    - process_output(): Output processing
    """

    agent_id: str = "base_agent"
    agent_name: str = "Base Agent"
    category: str = "base"

    def __init__(self, definition: Optional[AgentDefinition] = None):
        """
        Initialize agent.

        Args:
            definition: Optional agent definition
        """
        self._definition = definition
        self._state = AgentState.IDLE
        self._execution_count = 0
        self._last_error: Optional[str] = None

    @property
    def definition(self) -> Optional[AgentDefinition]:
        """Get agent definition."""
        return self._definition

    @property
    def state(self) -> AgentState:
        """Get current agent state."""
        return self._state

    @property
    def execution_count(self) -> int:
        """Get execution count."""
        return self._execution_count

    @abstractmethod
    async def execute(
        self,
        task: str,
        context: Dict[str, Any],
        tools: Optional[List[Any]] = None,
    ) -> AgentResult:
        """
        Execute the agent task.

        Args:
            task: Task description
            context: Execution context
            tools: Available tools

        Returns:
            AgentResult with execution outcome

        Raises:
            AgentExecutionError: If execution fails
        """
        pass

    async def validate_input(
        self,
        task: str,
        context: Dict[str, Any],
    ) -> tuple[bool, List[str]]:
        """
        Validate input before execution.

        Args:
            task: Task description
            context: Execution context

        Returns:
            Tuple of (is_valid, error_messages)
        """
        errors = []

        if not task:
            errors.append("Task description is required")

        if not context.get("user_goal"):
            errors.append("User goal must be specified in context")

        return len(errors) == 0, errors

    async def process_output(
        self,
        result: AgentResult,
        context: Dict[str, Any],
    ) -> AgentResult:
        """
        Process and validate output after execution.

        Args:
            result: Raw agent result
            context: Execution context

        Returns:
            Processed AgentResult
        """
        # Default implementation just returns the result
        return result

    def can_handle(
        self,
        task: str,
        phase: str,
        complexity: float = 0.5,
    ) -> bool:
        """
        Check if agent can handle a task.

        Args:
            task: Task description
            phase: Current pipeline phase
            complexity: Task complexity (0-1)

        Returns:
            True if agent can handle the task
        """
        if not self._definition:
            return True  # Base agent can handle anything

        triggers = self._definition.triggers

        # Check phase
        if triggers.phases and phase not in triggers.phases:
            return False

        # Check complexity
        min_complex, max_complex = triggers.complexity_range
        if not (min_complex <= complexity <= max_complex):
            return False

        # Check keywords
        if triggers.keywords:
            task_lower = task.lower()
            if not any(kw.lower() in task_lower for kw in triggers.keywords):
                return False

        return True

    def get_capabilities(self) -> List[str]:
        """Get list of agent capabilities."""
        if self._definition:
            return self._definition.capabilities.capabilities
        return []

    def get_tools(self) -> List[str]:
        """Get list of available tools."""
        if self._definition:
            return self._definition.tools
        return []

    def get_info(self) -> Dict[str, Any]:
        """Get agent information."""
        return {
            "id": self.agent_id,
            "name": self.agent_name,
            "category": self.category,
            "state": self._state.name,
            "execution_count": self._execution_count,
            "last_error": self._last_error,
            "capabilities": self.get_capabilities(),
            "tools": self.get_tools(),
        }

    def _set_state(self, state: AgentState) -> None:
        """Set agent state."""
        self._state = state

    def _increment_execution(self) -> None:
        """Increment execution count."""
        self._execution_count += 1

    def _set_error(self, error: str) -> None:
        """Set last error."""
        self._last_error = error
