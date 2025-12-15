# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""External MCP service tools for Code Agent.

Provides tools for:
- Context7: Documentation lookup and library reference search
- Perplexity: Web search for current information and best practices
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ExternalToolsMixin:
    """Mixin providing external MCP service tools.

    This mixin provides tools for:
    - Searching library documentation (Context7)
    - Searching the web for current information (Perplexity)

    Tools provided:
    - search_documentation: Search library documentation and code examples
    - search_web: Search the web for current information
    """

    def register_external_tools(self) -> None:
        """Register all external service tools."""
        from gaia.agents.base.tools import tool
        from gaia.mcp.external_services import (
            get_context7_service,
            get_perplexity_service,
        )

        # ============================================================
        # CONTEXT7 DOCUMENTATION SEARCH
        # ============================================================

        @tool
        def search_documentation(
            query: str, library: Optional[str] = None
        ) -> Dict[str, Any]:
            """Search library documentation and code examples using Context7.

            IMPORTANT: This is an OPTIONAL tool that may not be available. If unavailable,
            use your embedded knowledge from training data.

            Use this tool when you need to look up:
            - Library API documentation
            - Code examples and usage patterns
            - Best practices for specific libraries
            - Function/class signatures and parameters

            Args:
                query: The search query or topic (e.g., "useState hook", "async/await")
                library: Optional library name to search in (e.g., "react", "tensorflow", "fastapi").
                        If not specified, Context7 will search across relevant libraries.

            Returns:
                Dictionary containing:
                - success: Whether the search was successful
                - documentation: Retrieved documentation text with code examples
                - error: Error message if search failed
                - guidance: Helpful guidance when tool is unavailable

            Example usage:
                # Search React documentation for useState
                result = search_documentation("useState hook", library="react")

                # Search for general Python async patterns
                result = search_documentation("async/await best practices")
            """
            try:
                logger.info(
                    f"Searching documentation: query='{query}', library={library}"
                )

                service = get_context7_service()
                result = service.search_documentation(query, library)

                # If Context7 is unavailable, provide helpful guidance
                if result.get("unavailable"):
                    logger.info(
                        "Context7 not available - guiding LLM to use embedded knowledge"
                    )
                    return {
                        "success": False,
                        "documentation": "",
                        "error": "Context7 not available. Use your embedded knowledge for this pattern.",
                        "guidance": "Most common library patterns are in your training data. Try the standard approach first. If you encounter errors after 2 attempts, escalate to the user.",
                    }

                if result.get("success"):
                    logger.info("Documentation search successful")
                    return {
                        "success": True,
                        "documentation": result.get("documentation", ""),
                    }
                else:
                    error_msg = result.get("error", "Unknown error")
                    logger.warning(f"Documentation search failed: {error_msg}")
                    return {
                        "success": False,
                        "documentation": "",
                        "error": error_msg,
                    }

            except Exception as e:
                logger.error(f"Documentation search error: {e}", exc_info=True)
                return {
                    "success": False,
                    "documentation": "",
                    "error": f"Search failed: {str(e)}",
                    "guidance": "The documentation search tool failed. Use your embedded knowledge for common patterns.",
                }

        # ============================================================
        # PERPLEXITY WEB SEARCH
        # ============================================================

        @tool
        def search_web(query: str) -> Dict[str, Any]:
            """Search the web for current information using Perplexity AI.

            Use this tool when you need to look up:
            - Current best practices or trends
            - Recent updates or changes to libraries/frameworks
            - Solutions to specific problems or errors
            - Comparisons between different approaches
            - Information not available in library documentation

            Args:
                query: The search query (e.g., "latest Python best practices 2025",
                      "how to fix CORS error in FastAPI")

            Returns:
                Dictionary containing:
                - success: Whether the search was successful
                - answer: Concise answer with relevant information
                - error: Error message if search failed

            Example usage:
                # Search for current best practices
                result = search_web("Python async best practices 2025")

                # Search for error solutions
                result = search_web("how to fix ModuleNotFoundError in Python")

            Note:
                Requires PERPLEXITY_API_KEY environment variable to be set.
            """
            try:
                logger.info(f"Searching web: query='{query}'")

                service = get_perplexity_service()
                result = service.search_web(query)

                if result.get("success"):
                    logger.info("Web search successful")
                    return {
                        "success": True,
                        "answer": result.get("answer", ""),
                    }
                else:
                    error_msg = result.get("error", "Unknown error")
                    logger.warning(f"Web search failed: {error_msg}")
                    return {
                        "success": False,
                        "answer": "",
                        "error": error_msg,
                    }

            except Exception as e:
                logger.error(f"Web search error: {e}", exc_info=True)
                return {
                    "success": False,
                    "answer": "",
                    "error": f"Search failed: {str(e)}",
                }
