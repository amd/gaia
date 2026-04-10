# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Domain Analyzer - Stage 1 of the GAIA multi-stage pipeline.

The Domain Analyzer identifies domains involved in a task, extracts requirements,
and maps dependencies between domain entities.
"""

import json
import logging
from typing import Any, Dict, List

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool

logger = logging.getLogger(__name__)


class DomainAnalyzer(Agent):
    """
    Domain Analyzer Agent - Stage 1 of the multi-stage pipeline.

    This agent analyzes task descriptions to:
    1. Identify primary and secondary domains
    2. Extract domain-specific requirements
    3. Map cross-domain dependencies
    4. Assess domain complexity
    5. Produce a structured domain analysis blueprint

    The output blueprint is consumed by the Workflow Modeler (Stage 2).
    """

    def __init__(self, **kwargs):
        """Initialize the Domain Analyzer agent."""
        # Set default model for domain analysis
        kwargs.setdefault("model_id", "Qwen3.5-35B-A3B-GGUF")
        kwargs.setdefault("max_steps", 15)
        kwargs.setdefault("debug", False)

        super().__init__(**kwargs)

        # Domain analysis state
        self._identified_domains: List[str] = []
        self._domain_requirements: Dict[str, List[str]] = {}
        self._domain_constraints: Dict[str, List[str]] = {}
        self._cross_domain_dependencies: List[Dict[str, str]] = []
        self._complexity_score: float = 0.0
        self._confidence_score: float = 0.0

    def _register_tools(self):
        """Register domain analysis tools."""

        @tool
        def identify_domains(task_description: str) -> Dict[str, Any]:
            """
            Identify all domains involved in a task.

            Args:
                task_description: The task to analyze

            Returns:
                Dictionary with:
                - primary_domain: str
                - secondary_domains: List[str]
                - domain_descriptions: Dict[str, str]
            """
            # Use RAG to query domain knowledge
            domains_result = self._analyze_with_llm(
                f"Analyze this task and identify all domains involved: {task_description}",
                system_prompt="""You are a domain expert. Identify all domains mentioned or implied.
Return JSON:
{
  "primary_domain": "the main domain",
  "secondary_domains": ["list", "of", "secondary", "domains"],
  "domain_descriptions": {"domain": "brief description"}
}""",
            )

            self._identified_domains = domains_result.get("secondary_domains", [])
            if domains_result.get("primary_domain"):
                self._identified_domains.insert(0, domains_result["primary_domain"])

            logger.info(
                f"Identified {len(self._identified_domains)} domains: {self._identified_domains}"
            )
            return domains_result

        @tool
        def extract_requirements(domain: str, task_description: str) -> Dict[str, Any]:
            """
            Extract requirements for a specific domain.

            Args:
                domain: The domain to extract requirements for
                task_description: The task description

            Returns:
                Dictionary with requirements and constraints for the domain
            """
            requirements_result = self._analyze_with_llm(
                f"For the {domain} domain in this task: {task_description}, extract requirements and constraints.",
                system_prompt="""Extract domain-specific requirements.
Return JSON:
{
  "functional_requirements": ["list", "of", "requirements"],
  "non_functional_requirements": ["list", "of", "constraints"],
  "domain_knowledge_needed": ["topics", "to", "research"]
}""",
            )

            self._domain_requirements[domain] = requirements_result.get(
                "functional_requirements", []
            )
            self._domain_constraints[domain] = requirements_result.get(
                "non_functional_requirements", []
            )

            logger.info(
                f"Extracted {len(self._domain_requirements[domain])} requirements for {domain}"
            )
            return requirements_result

        @tool
        def map_dependencies(domain_a: str, domain_b: str) -> Dict[str, Any]:
            """
            Map dependencies between two domains.

            Args:
                domain_a: First domain
                domain_b: Second domain

            Returns:
                Dictionary describing the dependency relationship
            """
            dependency_result = self._analyze_with_llm(
                f"Map dependencies between {domain_a} and {domain_b}.",
                system_prompt="""Analyze cross-domain dependencies.
Return JSON:
{
  "from_domain": "domain_a",
  "to_domain": "domain_b",
  "dependency_type": "data|control|temporal|resource",
  "description": "description of dependency",
  "direction": "unidirectional|bidirectional"
}""",
            )

            dependency_result["from"] = domain_a
            dependency_result["to"] = domain_b
            self._cross_domain_dependencies.append(dependency_result)

            logger.info(f"Mapped dependency: {domain_a} -> {domain_b}")
            return dependency_result

        @tool
        def load_component_template_domain(component_path: str) -> Dict[str, Any]:
            """
            Load a component template from the component-framework.

            Args:
                component_path: Path relative to component-framework/

            Returns:
                Component with frontmatter and content
            """
            return self.load_component(component_path)

        @tool
        def save_analysis_result(artifact_name: str, _result: Dict[str, Any]) -> str:
            """
            Save domain analysis result as an artifact.

            Args:
                artifact_name: Name for the artifact
                result: Analysis result dictionary

            Returns:
                Path to saved artifact
            """
            # Save to component-framework/knowledge/
            content = f"# Domain Analysis: {artifact_name}\n\n"
            content += "## Identified Domains\n\n"
            for domain in self._identified_domains:
                content += f"- {domain}\n"

            content += "\n## Requirements\n\n"
            for domain, reqs in self._domain_requirements.items():
                content += f"### {domain}\n"
                for req in reqs:
                    content += f"- {req}\n"

            content += "\n## Dependencies\n\n"
            for dep in self._cross_domain_dependencies:
                content += f"- {dep['from']} -> {dep['to']}: {dep.get('description', 'dependency')}\n"

            component_path = (
                f"knowledge/domain-{artifact_name.lower().replace(' ', '-')}.md"
            )
            frontmatter = {
                "template_id": f"domain-{artifact_name.lower().replace(' ', '-')}",
                "template_type": "knowledge",
                "version": "1.0.0",
                "description": f"Domain analysis for {artifact_name}",
            }

            return self.save_component(component_path, content, frontmatter)

    def _analyze_with_llm(self, query: str, system_prompt: str) -> Dict[str, Any]:
        """Helper method to analyze with LLM and parse JSON response."""
        try:
            messages = [{"role": "user", "content": query}]
            response = self.chat.send_messages(messages, system_prompt=system_prompt)
            response_text = (
                response.text if hasattr(response, "text") else str(response)
            )

            # Extract JSON from response
            if isinstance(response_text, str):
                # Try to extract JSON block
                import re

                json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
                if json_match:
                    return json.loads(json_match.group())
                else:
                    logger.warning(
                        f"Could not extract JSON from response: {response_text[:200]}"
                    )
                    return {"raw_response": response_text}
            elif isinstance(response, dict):
                return response
            else:
                return {"raw_response": response_text}

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            return {"error": f"JSON parse error: {e}"}
        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            return {"error": str(e)}

    def analyze(self, task_description: str) -> Dict[str, Any]:
        """
        Perform domain analysis on a task description.

        Args:
            task_description: The task to analyze

        Returns:
            Domain analysis blueprint with:
            - primary_domain: str
            - secondary_domains: List[str]
            - domain_requirements: Dict[str, List[str]]
            - domain_constraints: Dict[str, List[str]]
            - cross_domain_dependencies: List[Dict]
            - complexity_score: float (0.0-1.0)
            - confidence_score: float (0.0-1.0)
            - reasoning: str
        """
        logger.info(f"Starting domain analysis for: {task_description[:100]}...")

        # Step 1: Identify domains
        self.execute_tool("identify_domains", {"task_description": task_description})

        # Step 2: Extract requirements for each domain
        for domain in self._identified_domains[:5]:  # Limit to top 5 domains
            self.execute_tool(
                "extract_requirements",
                {"domain": domain, "task_description": task_description},
            )

        # Step 3: Map dependencies between domains
        for i, domain_a in enumerate(self._identified_domains[:3]):
            for domain_b in self._identified_domains[i + 1 : 3]:
                self.execute_tool(
                    "map_dependencies", {"domain_a": domain_a, "domain_b": domain_b}
                )

        # Step 4: Calculate complexity score
        self._complexity_score = self._calculate_complexity()
        self._confidence_score = self._calculate_confidence()

        # Step 5: Build and return blueprint
        blueprint = {
            "primary_domain": (
                self._identified_domains[0] if self._identified_domains else "unknown"
            ),
            "secondary_domains": self._identified_domains[1:],
            "domain_requirements": self._domain_requirements,
            "domain_constraints": self._domain_constraints,
            "cross_domain_dependencies": self._cross_domain_dependencies,
            "complexity_score": self._complexity_score,
            "confidence_score": self._confidence_score,
            "reasoning": self._generate_reasoning(),
        }

        logger.info(
            f"Domain analysis complete. Complexity: {self._complexity_score:.2f}, Confidence: {self._confidence_score:.2f}"
        )
        return blueprint

    def _calculate_complexity(self) -> float:
        """Calculate domain complexity score based on analysis."""
        # Factors:
        # - Number of domains (more = more complex)
        # - Number of requirements (more = more complex)
        # - Number of dependencies (more = more complex)
        # - Dependency types (bidirectional > unidirectional)

        domain_factor = min(
            len(self._identified_domains) / 10.0, 1.0
        )  # Cap at 10 domains

        total_reqs = sum(len(reqs) for reqs in self._domain_requirements.values())
        req_factor = min(total_reqs / 20.0, 1.0)  # Cap at 20 requirements

        dep_factor = min(
            len(self._cross_domain_dependencies) / 5.0, 1.0
        )  # Cap at 5 dependencies

        # Weighted average
        complexity = 0.3 * domain_factor + 0.4 * req_factor + 0.3 * dep_factor
        return round(complexity, 2)

    def _calculate_confidence(self) -> float:
        """Calculate confidence score in the analysis."""
        # Factors:
        # - Domains identified (more coverage = higher confidence)
        # - Requirements extracted per domain
        # - Dependencies mapped

        if not self._identified_domains:
            return 0.0

        # Base confidence from having identified domains
        base_confidence = 0.5

        # Bonus for requirements
        if self._domain_requirements:
            base_confidence += 0.2

        # Bonus for dependencies
        if self._cross_domain_dependencies:
            base_confidence += 0.2

        # Bonus for multiple domains analyzed
        if len(self._identified_domains) > 1:
            base_confidence += 0.1

        return min(round(base_confidence, 2), 1.0)

    def _generate_reasoning(self) -> str:
        """Generate human-readable reasoning for the analysis."""
        reasoning_parts = []

        if self._identified_domains:
            reasoning_parts.append(
                f"Identified {len(self._identified_domains)} domains: {', '.join(self._identified_domains)}."
            )

        total_reqs = sum(len(reqs) for reqs in self._domain_requirements.values())
        if total_reqs > 0:
            reasoning_parts.append(
                f"Extracted {total_reqs} requirements across {len(self._domain_requirements)} domains."
            )

        if self._cross_domain_dependencies:
            reasoning_parts.append(
                f"Mapped {len(self._cross_domain_dependencies)} cross-domain dependencies."
            )

        return " ".join(reasoning_parts)

    def execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a registered tool by name."""
        # Get tool from registry
        from gaia.agents.base.tools import _TOOL_REGISTRY

        if tool_name not in _TOOL_REGISTRY:
            logger.error(f"Tool {tool_name} not registered")
            return {"error": f"Tool {tool_name} not registered"}

        tool_fn = _TOOL_REGISTRY[tool_name]["function"]
        return tool_fn(**tool_args)
