# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""RoutingAgent - Intelligently routes requests and disambiguates parameters."""

import json
import os
from typing import Any, Dict, List, Optional

from gaia.agents.base.agent import Agent
from gaia.llm.llm_client import LLMClient
from gaia.logger import get_logger

from .system_prompt import ROUTING_ANALYSIS_PROMPT

logger = get_logger(__name__)


class RoutingAgent:
    """
    Routes user requests to appropriate agents with intelligent disambiguation.

    Currently handles Code agent routing. Future: Jira, Docker, etc.

    Flow:
    1. Analyze query with LLM to detect agent and parameters
    2. If parameters unknown, ask user for clarification
    3. Recursively re-analyze with user's response as added context
    4. Once resolved, return configured agent ready to execute
    """

    def __init__(self, **agent_kwargs):
        """Initialize routing agent with LLM client.

        Args:
            **agent_kwargs: Additional kwargs to pass to created agents
        """
        # Initialize LLM client for language detection
        # Extract LLM-specific params from agent_kwargs
        use_claude = agent_kwargs.get("use_claude", False)
        use_chatgpt = agent_kwargs.get("use_chatgpt", False)

        # Determine base_url: CLI arg > Environment > LLMClient default
        base_url = agent_kwargs.get("base_url")
        if base_url is None:
            # Read from environment if not provided
            base_url = os.getenv("LEMONADE_BASE_URL", "http://localhost:8000/api/v1")

        llm_kwargs = {
            "use_claude": use_claude,
            "use_openai": use_chatgpt,
            "base_url": base_url,
        }

        self.llm_client = LLMClient(**llm_kwargs)
        self.agent_kwargs = agent_kwargs  # Store for passing to created agents

        # Model to use for routing analysis (configurable via env var)
        self.routing_model = os.getenv(
            "AGENT_ROUTING_MODEL", "Qwen3-Coder-30B-A3B-Instruct-GGUF"
        )

    def process_query(
        self, query: str, conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> Agent:
        """
        Process query with optional conversation history from disambiguation rounds.

        Args:
            query: Original user query
            conversation_history: List of conversation turns [{"role": "user", "content": "..."}]

        Returns:
            Configured agent instance ready to execute

        Example:
            router = RoutingAgent()
            agent = router.process_query("Create Express API")
            result = agent.process_query("Create Express API")
        """
        if conversation_history is None:
            conversation_history = []

        # Add current query to conversation history if not already there
        if not conversation_history or conversation_history[-1].get("content") != query:
            conversation_history.append({"role": "user", "content": query})

        logger.info(
            f"Routing analysis for: '{query}' (conversation turns: {len(conversation_history)})"
        )

        # Analyze with LLM using conversation history
        analysis = self._analyze_with_llm(conversation_history)

        logger.info(f"Analysis result: {analysis}")

        # Check if we have all required parameters
        if self._has_unknowns(analysis):
            # Generate clarification question
            question = self._generate_clarification_question(analysis)
            print(f"\n{question}")
            user_response = input("> ").strip()

            if not user_response:
                logger.warning("Empty user response, using defaults")
                # Use defaults if user just hits enter
                return self._create_agent_with_defaults(analysis)

            # Add assistant question and user response to conversation history
            conversation_history.append({"role": "assistant", "content": question})
            conversation_history.append({"role": "user", "content": user_response})

            # Recursive call with enriched conversation history
            return self.process_query(query, conversation_history)

        # All parameters resolved, create agent
        return self._create_agent(analysis)

    def _analyze_with_llm(
        self, conversation_history: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """
        Analyze query with LLM to determine agent and parameters.

        Args:
            conversation_history: Full conversation including clarifications

        Returns:
            Analysis dict with agent, parameters, confidence, reasoning
        """
        # Build context from conversation history
        context_parts = []
        for turn in conversation_history:
            role = turn["role"]
            content = turn["content"]
            if role == "user":
                context_parts.append(f"User: {content}")
            elif role == "assistant":
                context_parts.append(f"Assistant: {content}")

        full_context = "\n".join(context_parts)

        # Format prompt with full conversation context
        analysis_prompt = f"""Analyze this conversation and determine the configuration parameters.

Conversation:
{full_context}

{ROUTING_ANALYSIS_PROMPT.split('User Request: "{query}"')[1]}"""

        # Wrap in Qwen chat format
        prompt = (
            f"<|im_start|>user\n{analysis_prompt}<|im_end|>\n<|im_start|>assistant\n"
        )

        try:
            response = self.llm_client.generate(
                prompt=prompt,
                model=self.routing_model,
                max_tokens=500,
                stop=["<|im_end|>", "<|im_start|>"],
                stream=False,
            )

            # Extract JSON from response
            response_text = response.strip()

            # Handle potential markdown code blocks
            if "```json" in response_text:
                response_text = (
                    response_text.split("```json")[1].split("```")[0].strip()
                )
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()

            # Remove any leading/trailing whitespace and parse
            result = json.loads(response_text)
            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            logger.error(f"Response was: {response_text}")
            # Fallback to defaults
            return {
                "agent": "code",
                "parameters": {"language": "unknown", "project_type": "unknown"},
                "confidence": 0.0,
                "reasoning": "JSON parse error, using defaults",
            }
        except Exception as e:
            logger.error(f"Error analyzing query: {e}")
            raise RuntimeError(f"Failed to analyze query with Lemonade: {e}") from e

    def _fallback_keyword_detection(self, query: str) -> Dict[str, Any]:
        """
        Fallback keyword-based detection when LLM fails.

        Args:
            query: User query

        Returns:
            Analysis dict with detected language and project type
        """
        query_lower = query.lower()

        # TypeScript/Node.js indicators
        ts_keywords = [
            "nextjs",
            "next.js",
            "express",
            "nestjs",
            "koa",
            "fastify",
            "mongodb",
            "mongoose",
            "node.js",
            "nodejs",
            "react",
            "vue",
            "angular",
            "svelte",
            "vite",
            "typescript",
        ]

        # Python indicators
        py_keywords = ["django", "flask", "fastapi", "pandas", "numpy", "python"]

        # Detect language
        has_ts = any(kw in query_lower for kw in ts_keywords)
        has_py = any(kw in query_lower for kw in py_keywords)

        if has_ts:
            language = "typescript"
            reasoning = f"Detected TypeScript keywords: {[kw for kw in ts_keywords if kw in query_lower]}"
        elif has_py:
            language = "python"
            reasoning = f"Detected Python keywords: {[kw for kw in py_keywords if kw in query_lower]}"
        else:
            language = "unknown"
            reasoning = "No framework keywords detected"

        # Detect project type based on language
        if language == "typescript":
            # All TypeScript web apps use Next.js fullstack approach
            if any(kw in query_lower for kw in ["cli", "tool", "script", "utility"]):
                project_type = "script"
            else:
                # Default to fullstack for any web-related TypeScript project
                project_type = "fullstack"
        elif language == "python":
            # Python project types
            if any(
                kw in query_lower
                for kw in [
                    "api",
                    "rest",
                    "backend",
                    "server",
                    "fastapi",
                    "flask",
                    "django",
                ]
            ):
                project_type = "api"
            elif any(kw in query_lower for kw in ["web", "website", "dashboard"]):
                project_type = "web"
            elif any(
                kw in query_lower
                for kw in ["cli", "tool", "script", "utility", "calculator"]
            ):
                project_type = "script"
            else:
                project_type = "unknown"
        else:
            # Unknown language - try to detect project type from keywords
            if any(
                kw in query_lower
                for kw in [
                    "api",
                    "rest",
                    "backend",
                    "web",
                    "app",
                    "dashboard",
                    "frontend",
                ]
            ):
                project_type = "fullstack"  # Assume web app
            elif any(kw in query_lower for kw in ["cli", "tool", "script", "utility"]):
                project_type = "script"
            else:
                project_type = "unknown"

        return {
            "agent": "code",
            "parameters": {"language": language, "project_type": project_type},
            "confidence": 0.8 if language != "unknown" else 0.3,
            "reasoning": f"Keyword detection: {reasoning}",
        }

    def _has_unknowns(self, analysis: Dict[str, Any]) -> bool:
        """
        Check if analysis has unknown parameters that need disambiguation.

        Args:
            analysis: Analysis result from LLM

        Returns:
            True if any required parameter is unknown or confidence is low
        """
        params = analysis.get("parameters", {})
        confidence = analysis.get("confidence", 0.0)

        # Check for explicit unknowns
        has_unknown_params = (
            params.get("language") == "unknown"
            or params.get("project_type") == "unknown"
        )

        # Check for low confidence (< 0.9 means LLM is guessing)
        low_confidence = confidence < 0.9

        return has_unknown_params or low_confidence

    def _generate_clarification_question(self, analysis: Dict[str, Any]) -> str:
        """
        Generate natural language clarification question based on unknowns.

        Args:
            analysis: Analysis result with unknowns

        Returns:
            Question string to ask user
        """
        params = analysis.get("parameters", {})
        language = params.get("language")
        project_type = params.get("project_type")

        if language == "unknown" and project_type == "unknown":
            return (
                "What kind of application would you like to build?\n"
                "(e.g., 'Next.js blog', 'Python CLI tool', 'Django API', 'React dashboard')"
            )
        elif language == "unknown":
            if project_type == "fullstack":
                return (
                    "What language/framework would you like to use for your web application?\n"
                    "(e.g., 'Next.js/TypeScript' for web apps, 'Django/Python' for APIs)"
                )
            elif project_type == "script":
                return (
                    "What language would you like to use for your script?\n"
                    "(e.g., 'Python', 'TypeScript/Node.js')"
                )
            else:
                return (
                    "What language/framework would you like to use?\n"
                    "(e.g., 'Next.js', 'Django', 'Python', 'TypeScript')"
                )
        elif project_type == "unknown":
            if language == "typescript":
                return (
                    "What type of TypeScript project would you like to create?\n"
                    "(e.g., 'web app' for Next.js full-stack, 'CLI tool' for Node.js script)"
                )
            else:  # python
                return (
                    "What type of Python project would you like to create?\n"
                    "(e.g., 'REST API', 'web app', 'CLI tool', 'data analysis script')"
                )

        return "Please provide more details about your project."

    def _create_agent(self, analysis: Dict[str, Any]) -> Agent:
        """
        Create configured agent based on analysis.

        Args:
            analysis: Resolved analysis with all parameters

        Returns:
            Configured agent instance
        """
        agent_type = analysis.get("agent", "code")
        params = analysis.get("parameters", {})

        if agent_type == "code":
            from gaia.agents.code.agent import CodeAgent

            language = params.get("language", "python")
            project_type = params.get("project_type", "script")

            logger.info(
                f"Creating CodeAgent with language={language}, project_type={project_type}"
            )

            # Merge routing params with any additional kwargs
            return CodeAgent(
                language=language, project_type=project_type, **self.agent_kwargs
            )
        else:
            raise ValueError(f"Unknown agent type: {agent_type}")

    def _create_agent_with_defaults(self, analysis: Dict[str, Any]) -> Agent:
        """
        Create agent with default values for unknown parameters.

        Args:
            analysis: Analysis that may have unknowns

        Returns:
            Configured agent with defaults
        """
        params = analysis.get("parameters", {})

        # Determine language with defaults
        language = params.get("language")
        if language == "unknown":
            # Default to Python as the safest option
            language = "python"
            logger.info("Defaulting to Python for unknown language")

        # Determine project type with smart defaults based on language
        project_type = params.get("project_type")
        if project_type == "unknown":
            if language == "typescript":
                # TypeScript defaults to fullstack (Next.js)
                project_type = "fullstack"
                logger.info("Defaulting to fullstack (Next.js) for TypeScript")
            else:
                # Python defaults to script
                project_type = "script"
                logger.info("Defaulting to script for Python")

        from gaia.agents.code.agent import CodeAgent

        return CodeAgent(
            language=language, project_type=project_type, **self.agent_kwargs
        )
