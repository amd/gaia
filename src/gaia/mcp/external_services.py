#!/usr/bin/env python
#
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
External MCP Services Integration

Provides wrappers for external MCP services like Context7 and Perplexity
that run as separate processes via npx commands.
"""

import json
import os
import subprocess
import time
from typing import Any, Dict, List, Optional

from gaia.logger import get_logger

logger = get_logger(__name__)


class ExternalMCPService:
    """Base class for managing external MCP services via subprocess."""

    def __init__(
        self,
        command: List[str],
        env: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ):
        """
        Initialize external MCP service.

        Args:
            command: Command to start the MCP service (e.g., ["npx", "-y", "package"])
            env: Additional environment variables
            timeout: Timeout in seconds for subprocess calls
        """
        self.command = command
        self.env = {**os.environ.copy(), **(env or {})}
        self.timeout = timeout
        self.process = None

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call a tool on the external MCP service.

        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments as a dictionary

        Returns:
            Tool execution result
        """
        try:
            # Create JSON-RPC request
            request = {
                "jsonrpc": "2.0",
                "id": int(time.time() * 1000),
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }

            # Call the MCP service via subprocess
            result = subprocess.run(
                self.command,
                input=json.dumps(request) + "\n",
                capture_output=True,
                text=True,
                env=self.env,
                timeout=self.timeout,
            )

            if result.returncode != 0:
                logger.error(
                    f"MCP service error (exit {result.returncode}): {result.stderr}"
                )
                return {"error": f"Service failed: {result.stderr or 'Unknown error'}"}

            # Parse response
            try:
                response = json.loads(result.stdout)

                # Extract result from JSON-RPC response
                if "result" in response:
                    return response["result"]
                elif "error" in response:
                    return {"error": response["error"].get("message", "Unknown error")}
                else:
                    return {"error": "Invalid response format"}

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse MCP response: {e}")
                logger.debug(f"Raw output: {result.stdout}")
                return {"error": f"Invalid JSON response: {str(e)}"}

        except subprocess.TimeoutExpired:
            logger.error(f"MCP service call timed out after {self.timeout}s")
            return {"error": f"Request timed out after {self.timeout} seconds"}
        except Exception as e:
            logger.error(f"MCP service call failed: {e}")
            return {"error": str(e)}

    def list_tools(self) -> List[Dict[str, Any]]:
        """
        List available tools from the MCP service.

        Returns:
            List of tool definitions
        """
        try:
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

            result = subprocess.run(
                self.command,
                input=json.dumps(request) + "\n",
                capture_output=True,
                text=True,
                env=self.env,
                timeout=self.timeout,
            )

            if result.returncode == 0:
                response = json.loads(result.stdout)
                return response.get("result", {}).get("tools", [])

        except Exception as e:
            logger.warning(f"Failed to list tools: {e}")

        return []


class Context7Service(ExternalMCPService):
    """Context7 documentation search service with caching and rate protection.

    This is an OPTIONAL service - the system works without it.
    """

    # Class-level availability tracking (cached after first check)
    _availability_checked: bool = False
    _is_available: bool = False

    def __init__(self, api_key: Optional[str] = None):
        """Initialize Context7 MCP service.

        Args:
            api_key: Optional Context7 API key (defaults to CONTEXT7_API_KEY env var)
        """
        # Get API key from parameter or environment
        api_key = api_key or os.getenv("CONTEXT7_API_KEY")
        env = {"CONTEXT7_API_KEY": api_key} if api_key else {}

        super().__init__(command=["npx", "-y", "@upstash/context7-mcp"], env=env)

        # Use persistent cache instead of session cache
        from gaia.mcp.context7_cache import Context7Cache, Context7RateLimiter

        self._cache = Context7Cache()
        self._rate_limiter = Context7RateLimiter()

    @classmethod
    def check_availability(cls) -> bool:
        """Check if Context7 can be used (npx available, package works).

        This check is cached after the first call to avoid repeated slow checks.

        Returns:
            True if Context7 is available and working, False otherwise
        """
        if cls._availability_checked:
            return cls._is_available

        cls._availability_checked = True

        # Check if npx is available
        try:
            import shutil

            if not shutil.which("npx"):
                logger.info("Context7 unavailable: npx not found in PATH")
                cls._is_available = False
                return False
        except Exception as e:
            logger.info(f"Context7 unavailable: failed to check for npx: {e}")
            cls._is_available = False
            return False

        # Try a simple operation to verify Context7 works
        try:
            service = cls()
            tools = service.list_tools()
            cls._is_available = len(tools) > 0
            if cls._is_available:
                logger.info(f"Context7 available ({len(tools)} tools found)")
            else:
                logger.info("Context7 unavailable: no tools returned from service")
        except Exception as e:
            logger.info(f"Context7 unavailable: {type(e).__name__}: {e}")
            cls._is_available = False

        return cls._is_available

    def _get_resolved_library_id(self, library: str) -> Optional[str]:
        """Resolve a library name to Context7-compatible ID with persistent caching.

        Args:
            library: Library name (e.g., "nextjs") or full ID (e.g., "/vercel/next.js")

        Returns:
            Resolved library ID or None if resolution failed
        """
        # Already a full ID (has /org/project format)
        if library.count("/") >= 2:
            return library if library.startswith("/") else f"/{library}"

        # Check persistent cache first
        cached = self._cache.get_library_id(library)
        if cached is not None:
            logger.debug(f"Cache hit for library ID: {library} -> {cached}")
            return cached

        # Rate limit check before API call
        can_proceed, reason = self._rate_limiter.can_make_request()
        if not can_proceed:
            logger.warning(f"Context7 rate limited: {reason}")
            return None

        # Resolve via API
        logger.info(f"Resolving library ID for '{library}' via Context7 API")
        self._rate_limiter.consume_token()
        resolved_id = self.resolve_library_id(library)

        # Record success/failure for circuit breaker
        if resolved_id:
            self._rate_limiter.record_success()
            logger.info(f"Resolved '{library}' to '{resolved_id}'")
        else:
            self._rate_limiter.record_failure()
            logger.warning(f"Could not resolve library ID for '{library}'")

        # Cache result (even None to avoid repeated failures)
        self._cache.set_library_id(library, resolved_id)

        return resolved_id

    def search_documentation(
        self, query: str, library: Optional[str] = None
    ) -> Dict[str, Any]:
        """Search documentation using Context7 with caching and rate protection.

        Args:
            query: Search query (e.g., "how to use useState")
            library: Optional library name to search in (e.g., "react")

        Returns:
            Documentation search results with code examples and references
        """
        # Check availability first (cached after first check)
        if not self.check_availability():
            return {
                "success": False,
                "documentation": "",
                "error": "Context7 not available - use embedded knowledge",
                "unavailable": True,  # Signal to LLM to use embedded patterns
            }

        # Resolve library ID first
        resolved_id = None
        if library:
            resolved_id = self._get_resolved_library_id(library)
            if not resolved_id:
                logger.warning(f"Could not resolve library '{library}'")

        # Check documentation cache
        cache_key_lib = resolved_id or "global"
        cached_docs = self._cache.get_documentation(cache_key_lib, query)
        if cached_docs:
            logger.info(f"Cache hit for documentation: {cache_key_lib}:{query[:30]}...")
            return {
                "success": True,
                "documentation": cached_docs,
                "cached": True,
            }

        # Rate limit check before API call
        can_proceed, reason = self._rate_limiter.can_make_request()
        if not can_proceed:
            logger.warning(f"Context7 rate limited: {reason}")
            return {
                "success": False,
                "error": reason,
                "documentation": "",
            }

        # Make API call
        self._rate_limiter.consume_token()
        arguments = {"topic": query}
        if resolved_id:
            arguments["context7CompatibleLibraryID"] = resolved_id

        result = self.call_tool("get-library-docs", arguments)

        if "error" in result:
            # Check if it's a rate limit error (HTTP 429)
            is_rate_limit = "429" in str(result.get("error", ""))
            self._rate_limiter.record_failure(is_rate_limit)

            logger.error(f"Context7 search failed: {result['error']}")
            return {
                "success": False,
                "error": result["error"],
                "documentation": "",
            }

        # Success - cache and return
        self._rate_limiter.record_success()
        docs = (
            result.get("content", [{}])[0].get("text", "")
            if result.get("content")
            else ""
        )
        self._cache.set_documentation(cache_key_lib, query, docs)

        return {
            "success": True,
            "documentation": docs,
            "cached": False,
            "raw_result": result,
        }

    def resolve_library_id(self, library_name: str) -> Optional[str]:
        """
        Resolve a library name to Context7-compatible library ID.

        Args:
            library_name: Library name (e.g., "react", "tensorflow")

        Returns:
            Context7-compatible library ID (e.g., "/facebook/react") or None
        """
        result = self.call_tool("resolve-library-id", {"libraryName": library_name})

        if "error" in result:
            logger.warning(f"Failed to resolve library ID: {result['error']}")
            return None

        # Extract library ID from response
        content = result.get("content", [])
        if not content or len(content) == 0:
            logger.warning("Empty content in Context7 response")
            return None

        text = content[0].get("text", "")
        logger.debug(f"Context7 resolve-library-id response text:\n{text[:800]}")

        import re

        # Parse ALL libraries from response (separated by ----------)
        # Multiple libraries may have the same title - need smart selection
        libraries = []
        blocks = text.split("----------")

        for block in blocks:
            if not block.strip():
                continue

            title_match = re.search(r"Title:\s*(.+)", block)
            id_match = re.search(
                r"Context7-compatible library ID:\s*(/[\w.-]+/[\w.-]+(?:/[\w.-]+)?)",
                block,
            )
            score_match = re.search(r"Benchmark Score:\s*([\d.]+)", block)
            versions_match = re.search(r"Versions:\s*(.+)", block)

            if id_match:
                libraries.append(
                    {
                        "title": title_match.group(1).strip() if title_match else "",
                        "id": id_match.group(1),
                        "score": float(score_match.group(1)) if score_match else 0,
                        "has_versions": versions_match is not None,
                    }
                )

        if not libraries:
            logger.warning(f"No library IDs found in response for '{library_name}'")
            return None

        # Selection strategy (in order of priority):
        # 1. Exact title match that has versions (indicates official repo)
        # 2. Exact title match with highest score
        # 3. Title contains search term, prefer ones with versions
        # 4. Highest benchmark score overall

        # Normalize for comparison (remove dots, spaces, dashes)
        def normalize(s):
            return s.lower().replace(".", "").replace("-", "").replace(" ", "")

        normalized_search = normalize(library_name)

        # Find exact matches (after normalization)
        exact_matches = [
            lib for lib in libraries if normalize(lib["title"]) == normalized_search
        ]

        if exact_matches:
            # Prefer ones with versions (usually the official repo)
            versioned = [lib for lib in exact_matches if lib["has_versions"]]
            if versioned:
                best = max(versioned, key=lambda x: x["score"])
                logger.info(
                    f"Resolved '{library_name}' to '{best['id']}' (exact match with versions, score={best['score']})"
                )
                return best["id"]

            # No versions, pick highest score
            best = max(exact_matches, key=lambda x: x["score"])
            logger.info(
                f"Resolved '{library_name}' to '{best['id']}' (exact match, score={best['score']})"
            )
            return best["id"]

        # No exact match - look for title containing search term
        partial_matches = [
            lib for lib in libraries if normalized_search in normalize(lib["title"])
        ]
        if partial_matches:
            versioned = [lib for lib in partial_matches if lib["has_versions"]]
            if versioned:
                best = max(versioned, key=lambda x: x["score"])
                logger.info(
                    f"Resolved '{library_name}' to '{best['id']}' (partial match with versions, score={best['score']})"
                )
                return best["id"]

            best = max(partial_matches, key=lambda x: x["score"])
            logger.info(
                f"Resolved '{library_name}' to '{best['id']}' (partial match, score={best['score']})"
            )
            return best["id"]

        # Fallback: highest score overall
        best = max(libraries, key=lambda x: x["score"])
        logger.info(
            f"Resolved '{library_name}' to '{best['id']}' (fallback: highest score={best['score']})"
        )
        return best["id"]


class PerplexityService(ExternalMCPService):
    """Perplexity web search service."""

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Perplexity MCP service.

        Args:
            api_key: Perplexity API key (defaults to PERPLEXITY_API_KEY env var)
        """
        api_key = api_key or os.getenv("PERPLEXITY_API_KEY")
        if not api_key:
            logger.warning(
                "PERPLEXITY_API_KEY not set - web search will not be available"
            )

        super().__init__(
            command=["npx", "-y", "server-perplexity-ask"],
            env={"PERPLEXITY_API_KEY": api_key} if api_key else {},
        )

    def search_web(self, query: str) -> Dict[str, Any]:
        """
        Search the web using Perplexity.

        Args:
            query: Search query

        Returns:
            Web search results with answer and sources
        """
        result = self.call_tool(
            "perplexity_ask", {"messages": [{"role": "user", "content": query}]}
        )

        if "error" in result:
            logger.error(f"Perplexity search failed: {result['error']}")
            return {"success": False, "error": result["error"], "answer": ""}

        # Extract answer from response
        content = result.get("content", [])
        answer = ""
        if content and len(content) > 0:
            answer = content[0].get("text", "")

        return {"success": True, "answer": answer, "raw_result": result}


# Singleton instances for reuse
_context7_service: Optional[Context7Service] = None
_perplexity_service: Optional[PerplexityService] = None


def get_context7_service() -> Context7Service:
    """Get or create Context7 service singleton."""
    global _context7_service
    if _context7_service is None:
        _context7_service = Context7Service()
    return _context7_service


def get_perplexity_service() -> PerplexityService:
    """Get or create Perplexity service singleton."""
    global _perplexity_service
    if _perplexity_service is None:
        _perplexity_service = PerplexityService()
    return _perplexity_service
