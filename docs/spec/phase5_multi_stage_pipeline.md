# Phase 5: Multi-Stage Pipeline Architecture

## Overview

Phase 5 introduces a sophisticated multi-stage pipeline that transforms how GAIA processes tasks. Instead of directly selecting agents from a registry, the pipeline now performs deep analysis and explicit orchestration through four sequential stages:

```
TASK → Domain Analyzer → Workflow Modeler → Loom Builder → Pipeline Executor
  │         │                │                  │                 │
  │         ▼                ▼                  ▼                 ▼
  │   Understand       Map the          Build the         Execute the
  │   domains          workflow         agent loom        pipeline
  │   needed           (stages,         (agents +         with explicit
  │                   transitions)      connections)      tool calls
```

## Architectural Decision: Agent Definition Format

### Markdown with YAML Frontmatter

Agent definitions now use **Markdown files with YAML frontmatter** instead of pure YAML files.

**Rationale:**
- **YAML frontmatter** provides structured, machine-parsable metadata
- **Markdown body** provides human-readable prompts and documentation
- Files are discoverable and readable without special tooling
- Not hidden in `.claude/` - explicitly part of the codebase

**Example Agent Definition** (`agents/analytical_thinker.md`):

```markdown
---
id: analytical-thinker
name: Analytical Thinker
description: Domain analysis specialist
tools: [rag, file_search, mcp]
models: [Qwen3.5-35B-A3B-GGUF]
pipeline:
  entrypoint: "src/gaia/pipeline/agents/analytical_thinker.py::AnalyticalThinker"
  triggers:
    phases: ["PLANNING", "ANALYSIS"]
---

# Agent Prompt

You are an analytical thinker specializing in domain analysis and requirements gathering.

## Your Role

When analyzing a task, you will:
1. Identify the primary and secondary domains involved
2. Extract key requirements and constraints
3. Map stakeholders and their needs

## Tool Invocation Section

When analyzing a domain, you MUST explicitly call tools:

### Using RAG Tool
```python
result = await self.tools.rag.query("domain-specific query")
```

### Using File Search
```python
files = await self.tools.file_search.search_patterns(["**/*.py", "**/*.md"])
```

### Using MCP
```python
response = await self.mcp_client.call_tool("tool_name", {"arg": "value"})
```

## Output Format

Your analysis should produce:
1. List of identified domains
2. Domain-specific requirements
3. Cross-domain dependencies
```

---

## Stage 1: Domain Analyzer

### Purpose

The Domain Analyzer is the entry point for task processing. It analyzes the input task to understand:
- What knowledge domains are involved
- Domain-specific requirements and constraints
- Cross-domain dependencies

### Inputs

| Parameter | Type | Description |
|-----------|------|-------------|
| `task` | str | Natural language task description |
| `context` | dict | Additional context (user info, project state, etc.) |

### Outputs

```python
@dataclass
class DomainAnalysisResult:
    """Result from Domain Analyzer."""

    # Identified domains
    primary_domain: str
    secondary_domains: List[str]

    # Domain-specific details
    domain_requirements: Dict[str, Any]  # domain -> requirements
    domain_constraints: Dict[str, Any]   # domain -> constraints

    # Dependencies
    cross_domain_dependencies: List[Dict[str, str]]

    # Confidence and metadata
    confidence_score: float  # 0-1 confidence in analysis
    reasoning: str           # Explanation of analysis
```

### Implementation

```python
# src/gaia/pipeline/stages/domain_analyzer.py

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from gaia.agents.base import BaseAgent, AgentCapabilities, AgentTriggers

@dataclass
class DomainAnalysisResult:
    """Result from Domain Analyzer."""
    primary_domain: str
    secondary_domains: List[str]
    domain_requirements: Dict[str, Any]
    domain_constraints: Dict[str, Any]
    cross_domain_dependencies: List[Dict[str, str]]
    confidence_score: float
    reasoning: str


class DomainAnalyzer(BaseAgent):
    """
    Analyzes tasks to identify domains and requirements.

    The Domain Analyzer is the first stage in the multi-stage pipeline.
    It processes the input task and produces a structured analysis of
    the domains involved, their requirements, and dependencies.
    """

    def __init__(self, model_id: str = "Qwen3.5-35B-A3B-GGUF"):
        super().__init__(
            agent_id="domain-analyzer",
            name="Domain Analyzer",
            description="Analyzes tasks to identify domains and requirements",
            capabilities=AgentCapabilities(
                capabilities=["domain-analysis", "requirements-extraction"],
                tools=["rag", "file_search", "knowledge_graph"],
            ),
            triggers=AgentTriggers(
                phases=["DOMAIN_ANALYSIS"],
                keywords=["analyze", "understand", "requirements", "domain"],
            ),
        )
        self.model_id = model_id
        self._domain_knowledge_base = self._load_domain_knowledge()

    def _load_domain_knowledge(self) -> Dict[str, Any]:
        """Load domain knowledge base from RAG or config."""
        # Load predefined domain taxonomy
        return {
            "software-development": {
                "subdomains": ["backend", "frontend", "devops", "database"],
                "typical_requirements": ["functionality", "performance", "security"],
            },
            "data-science": {
                "subdomains": ["ml-modeling", "data-engineering", "visualization"],
                "typical_requirements": ["accuracy", "scalability", "reproducibility"],
            },
            # ... more domains
        }

    async def execute(self, context: Dict[str, Any]) -> DomainAnalysisResult:
        """
        Execute domain analysis on the input task.

        Args:
            context: Must contain 'task' key with task description

        Returns:
            DomainAnalysisResult with analysis
        """
        task = context.get("task", "")
        additional_context = context.get("context", {})

        # Step 1: Extract domain keywords from task
        domain_keywords = await self._extract_domain_keywords(task)

        # Step 2: Match against domain knowledge base
        domain_matches = await self._match_domains(domain_keywords)

        # Step 3: Extract domain-specific requirements
        requirements = await self._extract_requirements(
            task, domain_matches, additional_context
        )

        # Step 4: Identify cross-domain dependencies
        dependencies = await self._identify_dependencies(domain_matches, requirements)

        # Step 5: Calculate confidence and generate reasoning
        confidence, reasoning = await self._calculate_confidence(
            domain_matches, requirements
        )

        return DomainAnalysisResult(
            primary_domain=domain_matches["primary"],
            secondary_domains=domain_matches["secondary"],
            domain_requirements=requirements,
            domain_constraints=self._extract_constraints(task),
            cross_domain_dependencies=dependencies,
            confidence_score=confidence,
            reasoning=reasoning,
        )

    async def _extract_domain_keywords(self, task: str) -> List[str]:
        """Extract keywords that indicate domain involvement."""
        # Use LLM to extract domain-relevant keywords
        prompt = f"""Extract technical and domain-specific keywords from this task:

Task: {task}

Return a list of keywords that indicate what knowledge domains are involved."""
        # Call LLM and parse response
        return []

    async def _match_domains(self, keywords: List[str]) -> Dict[str, Any]:
        """Match keywords to known domains."""
        # Match against domain taxonomy
        return {"primary": "", "secondary": []}

    async def _extract_requirements(
        self, task: str, domain_matches: Dict, context: Dict
    ) -> Dict[str, Any]:
        """Extract requirements for each identified domain."""
        return {}

    async def _identify_dependencies(
        self, domains: Dict, requirements: Dict
    ) -> List[Dict[str, str]]:
        """Identify dependencies between domains."""
        return []

    async def _calculate_confidence(
        self, domains: Dict, requirements: Dict
    ) -> tuple[float, str]:
        """Calculate confidence score and generate reasoning."""
        return 0.0, ""
```

### Integration with Pipeline

The Domain Analyzer runs as the first stage after task intake:

```python
# In PipelineEngine._execute_pipeline()

async def _execute_pipeline(self) -> None:
    # Stage 1: Domain Analysis
    domain_result = await self._execute_domain_analyzer()
    self._state_machine.add_artifact("domain_analysis", domain_result)

    # Stage 2: Workflow Modeling (uses domain_result)
    workflow_model = await self._execute_workflow_modeler(domain_result)
    # ...
```

---

## Stage 2: Workflow Modeler

### Purpose

The Workflow Modeler takes the domain analysis and creates a structured workflow model that defines:
- Workflow stages and their sequence
- Transitions between stages
- Decision points and branching logic
- Entry/exit criteria for each stage

### Inputs

| Parameter | Type | Description |
|-----------|------|-------------|
| `domain_analysis` | DomainAnalysisResult | Output from Domain Analyzer |
| `task` | str | Original task description |
| `constraints` | dict | Any additional constraints |

### Outputs

```python
@dataclass
class WorkflowModel:
    """Workflow model from Workflow Modeler."""

    # Workflow identification
    workflow_id: str
    workflow_name: str

    # Stages in sequence
    stages: List["WorkflowStage"]

    # Transitions between stages
    transitions: List["WorkflowTransition"]

    # Decision points
    decision_points: List["DecisionPoint"]

    # Entry/exit criteria
    entry_criteria: Dict[str, Any]
    exit_criteria: Dict[str, Any]

    # Metadata
    estimated_complexity: float
    reasoning: str


@dataclass
class WorkflowStage:
    """A single stage in the workflow."""

    stage_id: str
    name: str
    description: str
    order: int  # Execution order

    # What this stage does
    activities: List[str]
    expected_artifacts: List[str]

    # Constraints
    time_estimate_minutes: int
    required_capabilities: List[str]


@dataclass
class WorkflowTransition:
    """Transition between workflow stages."""

    from_stage: str
    to_stage: str
    condition: str  # Condition for this transition
    guard_expression: Optional[str]  # Optional guard condition


@dataclass
class DecisionPoint:
    """A decision point in the workflow."""

    point_id: str
    stage_id: str
    question: str
    options: List[Dict[str, Any]]  # option -> next_stage mapping
    default_option: str
```

### Implementation

```python
# src/gaia/pipeline/stages/workflow_modeler.py

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from gaia.agents.base import BaseAgent, AgentCapabilities, AgentTriggers
from gaia.utils.id_generator import generate_id

@dataclass
class WorkflowStage:
    """A single stage in the workflow."""
    stage_id: str
    name: str
    description: str
    order: int
    activities: List[str] = field(default_factory=list)
    expected_artifacts: List[str] = field(default_factory=list)
    time_estimate_minutes: int = 30
    required_capabilities: List[str] = field(default_factory=list)


@dataclass
class WorkflowTransition:
    """Transition between workflow stages."""
    from_stage: str
    to_stage: str
    condition: str
    guard_expression: Optional[str] = None


@dataclass
class DecisionPoint:
    """A decision point in the workflow."""
    point_id: str
    stage_id: str
    question: str
    options: List[Dict[str, Any]] = field(default_factory=list)
    default_option: str = ""


@dataclass
class WorkflowModel:
    """Workflow model from Workflow Modeler."""
    workflow_id: str
    workflow_name: str
    stages: List[WorkflowStage] = field(default_factory=list)
    transitions: List[WorkflowTransition] = field(default_factory=list)
    decision_points: List[DecisionPoint] = field(default_factory=list)
    entry_criteria: Dict[str, Any] = field(default_factory=dict)
    exit_criteria: Dict[str, Any] = field(default_factory=dict)
    estimated_complexity: float = 0.5
    reasoning: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "stages": [self._stage_to_dict(s) for s in self.stages],
            "transitions": [
                {
                    "from_stage": t.from_stage,
                    "to_stage": t.to_stage,
                    "condition": t.condition,
                    "guard": t.guard_expression,
                }
                for t in self.transitions
            ],
            "decision_points": [
                {
                    "point_id": dp.point_id,
                    "stage_id": dp.stage_id,
                    "question": dp.question,
                    "options": dp.options,
                    "default": dp.default_option,
                }
                for dp in self.decision_points
            ],
            "entry_criteria": self.entry_criteria,
            "exit_criteria": self.exit_criteria,
            "estimated_complexity": self.estimated_complexity,
            "reasoning": self.reasoning,
        }

    def _stage_to_dict(self, stage: WorkflowStage) -> Dict[str, Any]:
        """Convert stage to dictionary."""
        return {
            "stage_id": stage.stage_id,
            "name": stage.name,
            "description": stage.description,
            "order": stage.order,
            "activities": stage.activities,
            "expected_artifacts": stage.expected_artifacts,
            "time_estimate_minutes": stage.time_estimate_minutes,
            "required_capabilities": stage.required_capabilities,
        }


class WorkflowModeler(BaseAgent):
    """
    Creates structured workflow models from domain analysis.

    The Workflow Modeler is the second stage in the multi-stage pipeline.
    It takes the domain analysis and produces a structured workflow with
    stages, transitions, and decision points.
    """

    def __init__(self, model_id: str = "Qwen3.5-35B-A3B-GGUF"):
        super().__init__(
            agent_id="workflow-modeler",
            name="Workflow Modeler",
            description="Creates structured workflow models from domain analysis",
            capabilities=AgentCapabilities(
                capabilities=["workflow-modeling", "process-design"],
                tools=["rag", "workflow_templates", "pattern_library"],
            ),
            triggers=AgentTriggers(
                phases=["WORKFLOW_MODELING"],
                keywords=["workflow", "process", "stages", "transitions"],
            ),
        )
        self.model_id = model_id
        self._workflow_templates = self._load_workflow_templates()

    def _load_workflow_templates(self) -> Dict[str, Any]:
        """Load workflow templates from config or RAG."""
        return {
            "software-development": {
                "typical_stages": [
                    "requirements-analysis",
                    "design",
                    "implementation",
                    "testing",
                    "deployment",
                ],
            },
            "data-analysis": {
                "typical_stages": [
                    "data-collection",
                    "data-cleaning",
                    "exploratory-analysis",
                    "modeling",
                    "visualization",
                ],
            },
        }

    async def execute(
        self,
        domain_result: "DomainAnalysisResult",
        task: str,
        constraints: Optional[Dict[str, Any]] = None,
    ) -> WorkflowModel:
        """
        Create a workflow model from domain analysis.

        Args:
            domain_result: Output from Domain Analyzer
            task: Original task description
            constraints: Additional constraints

        Returns:
            WorkflowModel with structured workflow
        """
        workflow_id = generate_id(prefix="workflow")

        # Step 1: Select workflow template based on primary domain
        template = self._select_workflow_template(domain_result.primary_domain)

        # Step 2: Customize stages based on task specifics
        stages = await self._customize_stages(template, domain_result, task)

        # Step 3: Define transitions between stages
        transitions = await self._define_transitions(stages)

        # Step 4: Identify decision points
        decision_points = await self._identify_decision_points(stages, domain_result)

        # Step 5: Define entry/exit criteria
        entry_criteria = await self._define_entry_criteria(domain_result)
        exit_criteria = await self._define_exit_criteria(domain_result, task)

        # Step 6: Estimate complexity
        complexity = await self._estimate_complexity(stages, domain_result)

        # Step 7: Generate reasoning
        reasoning = await self._generate_reasoning(stages, domain_result)

        return WorkflowModel(
            workflow_id=workflow_id,
            workflow_name=f"{domain_result.primary_domain}-workflow",
            stages=stages,
            transitions=transitions,
            decision_points=decision_points,
            entry_criteria=entry_criteria,
            exit_criteria=exit_criteria,
            estimated_complexity=complexity,
            reasoning=reasoning,
        )

    def _select_workflow_template(self, primary_domain: str) -> Dict[str, Any]:
        """Select workflow template based on domain."""
        return self._workflow_templates.get(
            primary_domain, self._workflow_templates.get("generic", {})
        )

    async def _customize_stages(
        self, template: Dict, domain_result: "DomainAnalysisResult", task: str
    ) -> List[WorkflowStage]:
        """Customize workflow stages for this specific task."""
        # Use LLM to customize stages
        return []

    async def _define_transitions(
        self, stages: List[WorkflowStage]
    ) -> List[WorkflowTransition]:
        """Define transitions between stages."""
        transitions = []
        for i in range(len(stages) - 1):
            transitions.append(WorkflowTransition(
                from_stage=stages[i].stage_id,
                to_stage=stages[i + 1].stage_id,
                condition=f"Stage {stages[i].name} completed successfully",
            ))
        return transitions

    async def _identify_decision_points(
        self, stages: List[WorkflowStage], domain_result: "DomainAnalysisResult"
    ) -> List[DecisionPoint]:
        """Identify decision points in the workflow."""
        return []

    async def _define_entry_criteria(
        self, domain_result: "DomainAnalysisResult"
    ) -> Dict[str, Any]:
        """Define entry criteria for the workflow."""
        return {"domain_validated": True}

    async def _define_exit_criteria(
        self, domain_result: "DomainAnalysisResult", task: str
    ) -> Dict[str, Any]:
        """Define exit criteria for the workflow."""
        return {"task_completed": True}

    async def _estimate_complexity(
        self, stages: List[WorkflowStage], domain_result: "DomainAnalysisResult"
    ) -> float:
        """Estimate workflow complexity."""
        # Based on number of stages, domains involved, etc.
        base_complexity = len(stages) * 0.1
        domain_multiplier = 1 + (len(domain_result.secondary_domains) * 0.1)
        return min(1.0, base_complexity * domain_multiplier)

    async def _generate_reasoning(
        self, stages: List[WorkflowStage], domain_result: "DomainAnalysisResult"
    ) -> str:
        """Generate reasoning for the workflow model."""
        return f"Workflow designed for {domain_result.primary_domain} with {len(stages)} stages."
```

---

## Stage 3: Loom Builder

### Purpose

The Loom Builder weaves together agents into a cohesive execution chain with explicit tool call patterns. It takes the workflow model and:
- Selects appropriate agents for each stage
- Defines explicit tool call patterns for each agent
- Creates connections between agents (data flow, handoff protocols)
- Builds the "agent loom" - the interwoven execution fabric

### Inputs

| Parameter | Type | Description |
|-----------|------|-------------|
| `workflow_model` | WorkflowModel | Output from Workflow Modeler |
| `domain_analysis` | DomainAnalysisResult | Output from Domain Analyzer |
| `available_agents` | List[AgentDefinition] | Available agents from registry |

### Outputs

```python
@dataclass
class AgentLoom:
    """Agent loom from Loom Builder."""

    # Loom identification
    loom_id: str
    workflow_id: str

    # Agent stations (agents positioned in the loom)
    stations: List["AgentStation"]

    # Connections between stations
    connections: List["StationConnection"]

    # Tool call patterns
    tool_patterns: Dict[str, List["ToolCallPattern"]]

    # Execution order
    execution_order: List[str]  # List of station_ids in order

    # Handoff protocols
    handoff_protocols: Dict[str, "HandoffProtocol"]


@dataclass
class AgentStation:
    """An agent positioned in the loom."""

    station_id: str
    stage_id: str  # Which workflow stage this handles
    agent_id: str  # Which agent is assigned
    role_description: str

    # What this station does
    input_expectations: List[str]  # What it expects to receive
    output_productions: List[str]  # What it produces

    # Tool call patterns for this station
    tool_patterns: List[str]


@dataclass
class StationConnection:
    """Connection between two stations."""

    source_station: str
    target_station: str
    data_flow: Dict[str, Any]  # What data flows between them
    synchronization: str  # sync, async, broadcast


@dataclass
class ToolCallPattern:
    """Explicit tool call pattern."""

    pattern_id: str
    station_id: str
    tool_name: str
    call_signature: str
    expected_output: str
    error_handling: str


@dataclass
class HandoffProtocol:
    """Protocol for handoff between stations."""

    protocol_id: str
    from_station: str
    to_station: str
    handoff_type: str  # synchronous, asynchronous, broadcast
    validation_checks: List[str]
    rollback_on_failure: bool
```

### Implementation

```python
# src/gaia/pipeline/stages/loom_builder.py

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from gaia.agents.base import AgentDefinition, AgentCapabilities, AgentTriggers
from gaia.agents.base import BaseAgent
from gaia.utils.id_generator import generate_id

@dataclass
class AgentStation:
    """An agent positioned in the loom."""
    station_id: str
    stage_id: str
    agent_id: str
    role_description: str
    input_expectations: List[str] = field(default_factory=list)
    output_productions: List[str] = field(default_factory=list)
    tool_patterns: List[str] = field(default_factory=list)


@dataclass
class StationConnection:
    """Connection between two stations."""
    source_station: str
    target_station: str
    data_flow: Dict[str, Any] = field(default_factory=dict)
    synchronization: str = "sync"


@dataclass
class ToolCallPattern:
    """Explicit tool call pattern."""
    pattern_id: str
    station_id: str
    tool_name: str
    call_signature: str
    expected_output: str
    error_handling: str = "raise"


@dataclass
class HandoffProtocol:
    """Protocol for handoff between stations."""
    protocol_id: str
    from_station: str
    to_station: str
    handoff_type: str = "synchronous"
    validation_checks: List[str] = field(default_factory=list)
    rollback_on_failure: bool = True


@dataclass
class AgentLoom:
    """Agent loom from Loom Builder."""
    loom_id: str
    workflow_id: str
    stations: List[AgentStation] = field(default_factory=list)
    connections: List[StationConnection] = field(default_factory=list)
    tool_patterns: Dict[str, List[ToolCallPattern]] = field(default_factory=dict)
    execution_order: List[str] = field(default_factory=list)
    handoff_protocols: Dict[str, HandoffProtocol] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "loom_id": self.loom_id,
            "workflow_id": self.workflow_id,
            "stations": [
                {
                    "station_id": s.station_id,
                    "stage_id": s.stage_id,
                    "agent_id": s.agent_id,
                    "role_description": s.role_description,
                    "input_expectations": s.input_expectations,
                    "output_productions": s.output_productions,
                    "tool_patterns": s.tool_patterns,
                }
                for s in self.stations
            ],
            "connections": [
                {
                    "source_station": c.source_station,
                    "target_station": c.target_station,
                    "data_flow": c.data_flow,
                    "synchronization": c.synchronization,
                }
                for c in self.connections
            ],
            "tool_patterns": {
                station_id: [
                    {
                        "pattern_id": p.pattern_id,
                        "tool_name": p.tool_name,
                        "call_signature": p.call_signature,
                        "expected_output": p.expected_output,
                        "error_handling": p.error_handling,
                    }
                    for p in patterns
                ]
                for station_id, patterns in self.tool_patterns.items()
            },
            "execution_order": self.execution_order,
            "handoff_protocols": {
                pid: {
                    "protocol_id": hp.protocol_id,
                    "from_station": hp.from_station,
                    "to_station": hp.to_station,
                    "handoff_type": hp.handoff_type,
                    "validation_checks": hp.validation_checks,
                    "rollback_on_failure": hp.rollback_on_failure,
                }
                for pid, hp in self.handoff_protocols.items()
            },
        }


class LoomBuilder(BaseAgent):
    """
    Builds agent looms from workflow models.

    The Loom Builder is the third stage in the multi-stage pipeline.
    It takes the workflow model and weaves agents together with explicit
    tool call patterns and connections.
    """

    def __init__(
        self,
        model_id: str = "Qwen3.5-35B-A3B-GGUF",
        agent_registry: Optional[Any] = None,
    ):
        super().__init__(
            agent_id="loom-builder",
            name="Loom Builder",
            description="Weaves agents together with tool call patterns",
            capabilities=AgentCapabilities(
                capabilities=["agent-orchestration", "tool-composition"],
                tools=["agent_registry", "pattern_library"],
            ),
            triggers=AgentTriggers(
                phases=["LOOM_BUILDING"],
                keywords=["loom", "weave", "orchestrate", "compose"],
            ),
        )
        self.model_id = model_id
        self._agent_registry = agent_registry

    async def execute(
        self,
        workflow_model: "WorkflowModel",
        domain_result: "DomainAnalysisResult",
        available_agents: Optional[List[AgentDefinition]] = None,
    ) -> AgentLoom:
        """
        Build an agent loom from a workflow model.

        Args:
            workflow_model: Output from Workflow Modeler
            domain_result: Output from Domain Analyzer
            available_agents: Available agents from registry

        Returns:
            AgentLoom with woven agents
        """
        loom_id = generate_id(prefix="loom")

        # Use provided agents or fetch from registry
        if available_agents is None and self._agent_registry:
            available_agents = list(self._agent_registry.get_all_agents().values())

        # Step 1: Assign agents to each workflow stage
        stations = await self._assign_agents_to_stages(
            workflow_model.stages, domain_result, available_agents
        )

        # Step 2: Define connections between stations
        connections = await self._define_connections(
            stations, workflow_model.transitions
        )

        # Step 3: Build tool call patterns for each station
        tool_patterns = await self._build_tool_patterns(stations, domain_result)

        # Step 4: Define execution order
        execution_order = [s.station_id for s in sorted(stations, key=lambda x: x.stage_id)]

        # Step 5: Create handoff protocols
        handoff_protocols = await self._create_handoff_protocols(connections)

        return AgentLoom(
            loom_id=loom_id,
            workflow_id=workflow_model.workflow_id,
            stations=stations,
            connections=connections,
            tool_patterns=tool_patterns,
            execution_order=execution_order,
            handoff_protocols=handoff_protocols,
        )

    async def _assign_agents_to_stages(
        self,
        stages: List["WorkflowStage"],
        domain_result: "DomainAnalysisResult",
        available_agents: List[AgentDefinition],
    ) -> List[AgentStation]:
        """Assign agents to workflow stages."""
        stations = []
        for stage in stages:
            # Find best agent for this stage based on capabilities
            best_agent = self._find_best_agent(
                stage.required_capabilities, available_agents
            )

            if best_agent:
                station = AgentStation(
                    station_id=generate_id(prefix="station"),
                    stage_id=stage.stage_id,
                    agent_id=best_agent.id,
                    role_description=f"Handles {stage.name} stage",
                    input_expectations=await self._define_input_expectations(stage),
                    output_productions=stage.expected_artifacts,
                    tool_patterns=best_agent.tools,
                )
                stations.append(station)

        return stations

    def _find_best_agent(
        self,
        required_capabilities: List[str],
        available_agents: List[AgentDefinition],
    ) -> Optional[AgentDefinition]:
        """Find best agent for required capabilities."""
        best_match = None
        best_score = 0

        for agent in available_agents:
            if not agent.enabled:
                continue

            # Score based on capability match
            agent_caps = set(agent.capabilities.capabilities)
            required_caps = set(required_capabilities)
            overlap = len(agent_caps & required_caps)

            if overlap > best_score:
                best_score = overlap
                best_match = agent

        return best_match

    async def _define_connections(
        self,
        stations: List[AgentStation],
        transitions: List["WorkflowTransition"],
    ) -> List[StationConnection]:
        """Define connections between stations based on workflow transitions."""
        connections = []
        station_map = {s.stage_id: s for s in stations}

        for transition in transitions:
            from_station = station_map.get(transition.from_stage)
            to_station = station_map.get(transition.to_stage)

            if from_station and to_station:
                connections.append(StationConnection(
                    source_station=from_station.station_id,
                    target_station=to_station.station_id,
                    data_flow={
                        "artifacts": from_station.output_productions,
                        "context": "workflow_context",
                    },
                    synchronization="sync",
                ))

        return connections

    async def _build_tool_patterns(
        self,
        stations: List[AgentStation],
        domain_result: "DomainAnalysisResult",
    ) -> Dict[str, List[ToolCallPattern]]:
        """Build explicit tool call patterns for each station."""
        patterns = {}

        for station in stations:
            station_patterns = []

            # Generate patterns based on agent tools and domain requirements
            for tool_name in station.tool_patterns:
                pattern = ToolCallPattern(
                    pattern_id=generate_id(prefix="pattern"),
                    station_id=station.station_id,
                    tool_name=tool_name,
                    call_signature=f"await self.tools.{tool_name}.call(...)",
                    expected_output=f"Result from {tool_name}",
                    error_handling="retry_with_backoff",
                )
                station_patterns.append(pattern)

            patterns[station.station_id] = station_patterns

        return patterns

    async def _create_handoff_protocols(
        self, connections: List[StationConnection]
    ) -> Dict[str, HandoffProtocol]:
        """Create handoff protocols for each connection."""
        protocols = {}

        for i, conn in enumerate(connections):
            protocol = HandoffProtocol(
                protocol_id=generate_id(prefix="handoff"),
                from_station=conn.source_station,
                to_station=conn.target_station,
                handoff_type=conn.synchronization,
                validation_checks=[
                    "verify_artifact_presence",
                    "validate_artifact_format",
                ],
                rollback_on_failure=True,
            )
            protocols[f"handoff_{i}"] = protocol

        return protocols
```

---

## Stage 4: Pipeline Executor

### Purpose

The Pipeline Executor runs the orchestrated agent loom with explicit tool calls. It:
- Executes stations in the defined order
- Manages tool invocations explicitly
- Handles handoffs between stations
- Collects artifacts and metrics
- Reports execution status

### Inputs

| Parameter | Type | Description |
|-----------|------|-------------|
| `agent_loom` | AgentLoom | Output from Loom Builder |
| `workflow_model` | WorkflowModel | Output from Workflow Modeler |
| `initial_context` | dict | Initial execution context |

### Outputs

```python
@dataclass
class ExecutionResult:
    """Result from Pipeline Executor."""

    # Execution identification
    execution_id: str
    loom_id: str
    workflow_id: str

    # Status
    status: str  # success, failed, partial
    completed_stations: List[str]
    failed_stations: List[str]

    # Artifacts produced
    artifacts: Dict[str, Any]  # station_id -> artifact

    # Tool call history
    tool_call_history: List["ToolCallRecord"]

    # Metrics
    execution_time_seconds: float
    total_tool_calls: int
    successful_tool_calls: int
    failed_tool_calls: int

    # Errors
    errors: List[Dict[str, Any]]


@dataclass
class ToolCallRecord:
    """Record of a tool call."""

    record_id: str
    station_id: str
    tool_name: str
    call_signature: str
    arguments: Dict[str, Any]
    result: Any
    status: str  # success, failed
    duration_ms: float
    timestamp: datetime
```

### Implementation

```python
# src/gaia/pipeline/stages/pipeline_executor.py

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from gaia.agents.base import BaseAgent, AgentCapabilities, AgentTriggers
from gaia.utils.id_generator import generate_id

@dataclass
class ToolCallRecord:
    """Record of a tool call."""
    record_id: str
    station_id: str
    tool_name: str
    call_signature: str
    arguments: Dict[str, Any]
    result: Any
    status: str
    duration_ms: float
    timestamp: datetime


@dataclass
class ExecutionResult:
    """Result from Pipeline Executor."""
    execution_id: str
    loom_id: str
    workflow_id: str
    status: str = "running"
    completed_stations: List[str] = field(default_factory=list)
    failed_stations: List[str] = field(default_factory=list)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    tool_call_history: List[ToolCallRecord] = field(default_factory=list)
    execution_time_seconds: float = 0.0
    total_tool_calls: int = 0
    successful_tool_calls: int = 0
    failed_tool_calls: int = 0
    errors: List[Dict[str, Any]] = field(default_factory=list)


class PipelineExecutor(BaseAgent):
    """
    Executes orchestrated agent looms.

    The Pipeline Executor is the fourth and final stage in the multi-stage pipeline.
    It executes the agent loom with explicit tool calls and manages the execution flow.
    """

    def __init__(self, model_id: str = "Qwen3.5-35B-A3B-GGUF"):
        super().__init__(
            agent_id="pipeline-executor",
            name="Pipeline Executor",
            description="Executes orchestrated agent looms",
            capabilities=AgentCapabilities(
                capabilities=["pipeline-execution", "tool-orchestration"],
                tools=["agent_invocation", "tool_execution", "metrics_collection"],
            ),
            triggers=AgentTriggers(
                phases=["PIPELINE_EXECUTION"],
                keywords=["execute", "run", "orchestrate"],
            ),
        )
        self.model_id = model_id
        self._agent_cache = {}  # Cache of instantiated agents

    async def execute(
        self,
        agent_loom: "AgentLoom",
        workflow_model: "WorkflowModel",
        initial_context: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """
        Execute an agent loom.

        Args:
            agent_loom: Output from Loom Builder
            workflow_model: Output from Workflow Modeler
            initial_context: Initial execution context

        Returns:
            ExecutionResult with execution details
        """
        execution_id = generate_id(prefix="exec")
        start_time = datetime.now()

        result = ExecutionResult(
            execution_id=execution_id,
            loom_id=agent_loom.loom_id,
            workflow_id=workflow_model.workflow_id,
        )

        context = initial_context or {}

        # Execute stations in order
        for station_id in agent_loom.execution_order:
            station = next(
                (s for s in agent_loom.stations if s.station_id == station_id), None
            )

            if not station:
                continue

            try:
                # Execute station
                station_result = await self._execute_station(
                    station, agent_loom, context
                )

                # Record success
                result.completed_stations.append(station_id)
                result.artifacts[station_id] = station_result.get("artifact")

                # Update context for next station
                context = self._update_context(context, station_result)

                # Record tool calls
                result.tool_call_history.extend(station_result.get("tool_calls", []))
                result.total_tool_calls += len(station_result.get("tool_calls", []))

            except Exception as e:
                # Record failure
                result.failed_stations.append(station_id)
                result.errors.append({
                    "station_id": station_id,
                    "error": str(e),
                    "timestamp": datetime.now().isoformat(),
                })

                # Check if we should continue or halt
                should_halt = await self._should_halt_on_error(e, station)
                if should_halt:
                    result.status = "failed"
                    break

        # Calculate final metrics
        end_time = datetime.now()
        result.execution_time_seconds = (end_time - start_time).total_seconds()
        result.successful_tool_calls = sum(
            1 for tc in result.tool_call_history if tc.status == "success"
        )
        result.failed_tool_calls = sum(
            1 for tc in result.tool_call_history if tc.status == "failed"
        )

        # Determine overall status
        if not result.failed_stations and not result.errors:
            result.status = "success"
        elif result.completed_stations and not result.failed_stations:
            result.status = "success"
        elif result.completed_stations and result.failed_stations:
            result.status = "partial"
        else:
            result.status = "failed"

        return result

    async def _execute_station(
        self,
        station: "AgentStation",
        agent_loom: "AgentLoom",
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute a single station."""
        # Get or instantiate agent
        agent = await self._get_agent(station.agent_id)

        # Prepare execution context
        exec_context = {
            **context,
            "station_id": station.station_id,
            "stage_id": station.stage_id,
            "expected_inputs": station.input_expectations,
        }

        # Execute agent with explicit tool call tracking
        tool_calls = []
        agent_result = None

        # Execute tool calls explicitly
        tool_patterns = agent_loom.tool_patterns.get(station.station_id, [])
        for pattern in tool_patterns:
            call_start = datetime.now()
            try:
                tool_result = await self._execute_tool(
                    agent, pattern.tool_name, pattern.call_signature, exec_context
                )

                call_duration = (datetime.now() - call_start).total_seconds() * 1000

                tool_calls.append(ToolCallRecord(
                    record_id=generate_id(prefix="tc"),
                    station_id=station.station_id,
                    tool_name=pattern.tool_name,
                    call_signature=pattern.call_signature,
                    arguments=exec_context,
                    result=tool_result,
                    status="success",
                    duration_ms=call_duration,
                    timestamp=datetime.now(),
                ))

                # Update context with tool result
                exec_context[f"{pattern.tool_name}_result"] = tool_result

            except Exception as e:
                call_duration = (datetime.now() - call_start).total_seconds() * 1000

                tool_calls.append(ToolCallRecord(
                    record_id=generate_id(prefix="tc"),
                    station_id=station.station_id,
                    tool_name=pattern.tool_name,
                    call_signature=pattern.call_signature,
                    arguments=exec_context,
                    result=None,
                    status="failed",
                    duration_ms=call_duration,
                    timestamp=datetime.now(),
                ))

                # Handle error based on pattern
                if pattern.error_handling == "raise":
                    raise

        # Execute agent's primary function
        agent_result = await agent.execute(exec_context)

        return {
            "artifact": agent_result,
            "tool_calls": tool_calls,
            "context_updated": exec_context,
        }

    async def _get_agent(self, agent_id: str) -> Any:
        """Get or instantiate agent by ID."""
        if agent_id in self._agent_cache:
            return self._agent_cache[agent_id]

        # Load agent from registry or create from definition
        # This would integrate with the ConfigurableAgent system
        agent = None  # Placeholder
        self._agent_cache[agent_id] = agent
        return agent

    async def _execute_tool(
        self,
        agent: Any,
        tool_name: str,
        call_signature: str,
        context: Dict[str, Any],
    ) -> Any:
        """Execute a specific tool call."""
        # Get tool from agent
        tool = getattr(agent.tools, tool_name, None)
        if not tool:
            raise ValueError(f"Tool {tool_name} not found on agent")

        # Execute tool with context
        return await tool.call(**context)

    def _update_context(
        self, context: Dict[str, Any], station_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update context after station execution."""
        return {
            **context,
            **station_result.get("context_updated", {}),
            "last_station_artifact": station_result.get("artifact"),
        }

    async def _should_halt_on_error(
        self, error: Exception, station: "AgentStation"
    ) -> bool:
        """Determine if execution should halt on error."""
        # Critical errors always halt
        critical_keywords = ["critical", "fatal", "unrecoverable"]
        if any(kw in str(error).lower() for kw in critical_keywords):
            return True

        # Check if this is a required station
        # (would check workflow model for required stages)
        return False
```

---

## Integration with Existing Pipeline

### Updated PipelineEngine

The PipelineEngine is updated to route through the new stages:

```python
# In src/gaia/pipeline/engine.py

class PipelineEngine:
    """Main pipeline orchestrator."""

    async def _execute_pipeline(self) -> None:
        """Execute all pipeline stages."""
        task = self._context.user_goal

        # Stage 1: Domain Analysis
        domain_analyzer = DomainAnalyzer()
        domain_result = await domain_analyzer.execute({"task": task})
        self._state_machine.add_artifact("domain_analysis", domain_result)
        self._nexus.commit(
            agent_id="domain-analyzer",
            event_type="stage_completed",
            payload={"stage": "domain_analysis"},
            phase="DOMAIN_ANALYSIS",
        )

        # Stage 2: Workflow Modeling
        workflow_modeler = WorkflowModeler()
        workflow_model = await workflow_modeler.execute(
            domain_result=domain_result,
            task=task,
        )
        self._state_machine.add_artifact("workflow_model", workflow_model)
        self._nexus.commit(
            agent_id="workflow-modeler",
            event_type="stage_completed",
            payload={"stage": "workflow_modeling"},
            phase="WORKFLOW_MODELING",
        )

        # Stage 3: Loom Building
        loom_builder = LoomBuilder(agent_registry=self._agent_registry)
        agent_loom = await loom_builder.execute(
            workflow_model=workflow_model,
            domain_result=domain_result,
        )
        self._state_machine.add_artifact("agent_loom", agent_loom)
        self._nexus.commit(
            agent_id="loom-builder",
            event_type="stage_completed",
            payload={"stage": "loom_building"},
            phase="LOOM_BUILDING",
        )

        # Stage 4: Pipeline Execution
        pipeline_executor = PipelineExecutor()
        execution_result = await pipeline_executor.execute(
            agent_loom=agent_loom,
            workflow_model=workflow_model,
            initial_context={"task": task},
        )
        self._state_machine.add_artifact("execution_result", execution_result)
        self._nexus.commit(
            agent_id="pipeline-executor",
            event_type="stage_completed",
            payload={"stage": "pipeline_execution"},
            phase="PIPELINE_EXECUTION",
        )

        # Determine terminal state based on execution result
        if execution_result.status == "success":
            self._state_machine.transition(
                PipelineState.COMPLETED,
                "Pipeline execution complete",
            )
        else:
            self._state_machine.transition(
                PipelineState.FAILED,
                f"Pipeline execution {execution_result.status}",
            )
```

### Updated Pipeline Phases

The traditional phases are augmented with the new stages:

```python
class PipelinePhase:
    """Pipeline phase constants."""

    # New multi-stage pipeline phases
    DOMAIN_ANALYSIS = "DOMAIN_ANALYSIS"
    WORKFLOW_MODELING = "WORKFLOW_MODELING"
    LOOM_BUILDING = "LOOM_BUILDING"
    PIPELINE_EXECUTION = "PIPELINE_EXECUTION"

    # Traditional phases (still supported)
    PLANNING = "PLANNING"
    DEVELOPMENT = "DEVELOPMENT"
    QUALITY = "QUALITY"
    DECISION = "DECISION"

    ALL = [
        DOMAIN_ANALYSIS,
        WORKFLOW_MODELING,
        LOOM_BUILDING,
        PIPELINE_EXECUTION,
        PLANNING,
        DEVELOPMENT,
        QUALITY,
        DECISION,
    ]
```

---

## Backward Compatibility

### Supporting Both YAML and Markdown Formats

The AgentRegistry is updated to support both formats:

```python
# In src/gaia/agents/registry.py

class AgentRegistry:
    """Dynamic agent registry with dual format support."""

    async def _load_all_agents(self) -> None:
        """Load all agent definitions from YAML and MD files."""
        if not self._agents_dir:
            return

        # Load YAML files (backward compatibility)
        yaml_files = list(self._agents_dir.glob("*.yaml"))
        yaml_files.extend(self._agents_dir.glob("*.yml"))
        for yaml_file in yaml_files:
            try:
                agent = await self._load_agent_yaml(yaml_file)
                async with self._lock:
                    self._agents[agent.id] = agent
            except Exception as e:
                logger.error(f"Failed to load YAML agent from {yaml_file}: {e}")

        # Load Markdown files with frontmatter (new format)
        md_files = list(self._agents_dir.glob("*.md"))
        for md_file in md_files:
            try:
                agent = await self._load_agent_markdown(md_file)
                async with self._lock:
                    self._agents[agent.id] = agent
            except Exception as e:
                logger.error(f"Failed to load MD agent from {md_file}: {e}")

    async def _load_agent_markdown(self, md_file: Path) -> AgentDefinition:
        """Load agent from Markdown file with YAML frontmatter."""
        from gaia.utils.frontmatter_parser import parse_markdown_frontmatter

        with open(md_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Parse frontmatter and body
        frontmatter, body = parse_markdown_frontmatter(content)

        # Build AgentDefinition from frontmatter
        agent_data = frontmatter.get("agent", frontmatter)

        # Add body as system_prompt (or store separately)
        agent_data["system_prompt"] = body

        return AgentDefinition.from_dict({"agent": agent_data})
```

---

## File Structure

```
gaia/
├── src/gaia/
│   └── pipeline/
│       ├── engine.py              # Updated PipelineEngine
│       ├── stages/
│       │   ├── __init__.py
│       │   ├── domain_analyzer.py
│       │   ├── workflow_modeler.py
│       │   ├── loom_builder.py
│       │   └── pipeline_executor.py
│       └── utils/
│           └── frontmatter_parser.py
│   └── agents/
│       ├── registry.py            # Updated with dual format support
│       └── base/
│           └── context.py         # Updated AgentDefinition
├── agents/                         # New: Agent definitions (Markdown + frontmatter)
│   ├── analytical_thinker.md
│   ├── workflow_architect.md
│   ├── loom_weaver.md
│   └── executor.md
└── config/agents/                  # Existing: Legacy YAML definitions
    ├── planning-analysis-strategist.yaml
    └── ...
```

---

## Testing Strategy

### Unit Tests

```python
# tests/unit/pipeline/test_domain_analyzer.py

import pytest
from gaia.pipeline.stages.domain_analyzer import DomainAnalyzer, DomainAnalysisResult

@pytest.fixture
def domain_analyzer():
    return DomainAnalyzer(model_id="Qwen3-0.6B-GGUF")

@pytest.mark.asyncio
async def test_domain_extraction(domain_analyzer):
    """Test domain extraction from task."""
    context = {
        "task": "Build a REST API with user authentication",
        "context": {"project_type": "web-application"},
    }

    result = await domain_analyzer.execute(context)

    assert isinstance(result, DomainAnalysisResult)
    assert result.primary_domain in ["software-development", "api-development"]
    assert "authentication" in result.domain_requirements
```

### Integration Tests

```python
# tests/integration/pipeline/test_multi_stage_pipeline.py

import pytest
from gaia.pipeline.engine import PipelineEngine
from gaia.pipeline.state import PipelineContext

@pytest.mark.asyncio
async def test_full_pipeline_execution(require_lemonade):
    """Test full multi-stage pipeline execution."""
    engine = PipelineEngine()
    context = PipelineContext(
        pipeline_id="test-pipeline-001",
        user_goal="Create a data visualization dashboard",
    )

    await engine.initialize(context, {"template": "STANDARD"})
    result = await engine.start()

    # Verify all stages completed
    assert result.state.name == "COMPLETED"
    assert "domain_analysis" in result.artifacts
    assert "workflow_model" in result.artifacts
    assert "agent_loom" in result.artifacts
    assert "execution_result" in result.artifacts
```

---

## Migration Path

### Phase 1: Parser and Format Support

1. Implement Markdown frontmatter parser
2. Update AgentRegistry to support both formats
3. Create example agent definitions in new format

### Phase 2: Stage Implementations

1. Implement Domain Analyzer with basic domain taxonomy
2. Implement Workflow Modeler with template library
3. Implement Loom Builder with agent registry integration
4. Implement Pipeline Executor with tool tracking

### Phase 3: Integration and Testing

1. Integrate stages into PipelineEngine
2. Add comprehensive unit tests
3. Add integration tests
4. Document usage patterns

### Phase 4: Production Readiness

1. Add metrics and monitoring
2. Implement error recovery
3. Optimize performance
4. Create migration guide for existing agents

---

## Success Criteria

1. **Parser**: Markdown + frontmatter parser handles all agent definition fields
2. **Domain Analyzer**: Correctly identifies primary and secondary domains for test tasks
3. **Workflow Modeler**: Generates valid workflow models with stages and transitions
4. **Loom Builder**: Creates agent looms with explicit tool patterns
5. **Pipeline Executor**: Successfully executes looms and tracks all tool calls
6. **Integration**: Full pipeline executes end-to-end with artifact propagation
7. **Backward Compatibility**: Existing YAML agents continue to work
