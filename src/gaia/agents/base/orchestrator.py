from typing import Dict, Any, Optional, List
from gaia.agents.base.agent import Agent
from gaia.agents.registry import AgentRegistry
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


class AgentOrchestrator:
    """Routes tasks to agents by capability and manages multi-agent workflows."""

    def __init__(self, registry: AgentRegistry):
        self.registry = registry

    def route(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
        use_llm: bool = False,
    ) -> Agent:
        """Analyze query and return the best-matched agent instance."""
        context = context or {}
        phase = context.get("phase", "UNKNOWN")

        # 1. Use AgentRegistry to select the best agent ID
        agent_id = self.registry.select_agent(
            task_description=query, current_phase=phase, state=context
        )

        if not agent_id:
            logger.warning(
                f"No specific agent found for query: '{query}'. Falling back to chat agent."
            )
            agent_id = "chat"

        # 2. Instantiate the agent based on the ID
        return self._instantiate_agent(agent_id, context)

    def _instantiate_agent(self, agent_id: str, context: Dict[str, Any]) -> Agent:
        """Map agent_id to concrete Agent classes."""
        # Get definition from registry to pass to ConfigurableAgent if needed
        definition = self.registry.get_agent(agent_id)

        # Build kwargs from context
        kwargs = context.get("parameters", {})

        # Mapping to concrete classes
        # The 10 agent types typically available in GAIA
        if agent_id in (
            "code",
            "senior-developer",
            "frontend-specialist",
            "backend-specialist",
        ):
            from gaia.agents.code.agent import CodeAgent

            return CodeAgent(**kwargs)
        elif agent_id == "chat":
            from gaia.agents.chat.agent import ChatAgent

            return ChatAgent(**kwargs)
        elif agent_id == "docker":
            from gaia.agents.docker.agent import DockerAgent

            return DockerAgent(**kwargs)
        elif agent_id == "emr":
            from gaia.agents.emr.agent import MedicalIntakeAgent

            return MedicalIntakeAgent(**kwargs)
        elif agent_id == "jira":
            from gaia.agents.jira.agent import JiraAgent

            return JiraAgent(**kwargs)
        elif agent_id == "sd":
            from gaia.agents.sd.agent import SDAgent

            return SDAgent(**kwargs)
        elif agent_id == "blender":
            from gaia.agents.blender.agent import BlenderAgent

            return BlenderAgent(**kwargs)
        elif agent_id == "summarize":
            from gaia.agents.summarize.agent import SummarizerAgent

            return SummarizerAgent(**kwargs)
        else:
            # Fallback to configurable agent if definition exists
            if definition:
                from gaia.agents.configurable import ConfigurableAgent

                return ConfigurableAgent(definition=definition, **kwargs)
            else:
                # Ultimate fallback
                from gaia.agents.chat.agent import ChatAgent

                return ChatAgent(**kwargs)

    def delegate(self, from_agent: Agent, task: str, **kwargs) -> Any:
        """Agent A delegates a subtask to the best agent for that task."""
        # Simple delegation: route and execute
        target_agent = self.route(task, context=kwargs)
        logger.info(f"Delegating task '{task}' to {target_agent.__class__.__name__}")
        # Note: In a full async pipeline, this would await execution.
        # But this serves as the foundational interface.
        return target_agent

    def chain(self, tasks: List[Dict[str, Any]]) -> List[Any]:
        """Execute a sequence of agent tasks, passing context between steps."""
        # This will be fully implemented in PR 2, but we stub it out here
        # as requested in the issue design.
        results = []
        for task_def in tasks:
            task_str = task_def.get("task", "")
            agent = self.route(task_str, context=task_def)
            results.append(agent)
        return results
