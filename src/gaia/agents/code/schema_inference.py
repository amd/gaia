# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""AI-powered schema inference for Code Agent.

This module provides dynamic schema inference using AI (Perplexity or local LLM)
to understand what fields an application should have based on natural language
descriptions. NO hardcoded app types or patterns - all inference is AI-driven.

Example:
    User: "Build me a task tracker"
    AI Response: {"entity": "Task", "fields": [
        {"name": "title", "type": "string", "required": true},
        {"name": "completed", "type": "boolean", "required": true},
        {"name": "dueDate", "type": "datetime", "required": false}
    ]}
"""

import json
import logging
import os
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Schema inference prompt - instructs AI to return minimal, appropriate fields
# NOTE: This prompt is optimized for both Perplexity and local LLMs
SCHEMA_INFERENCE_PROMPT = """You are a database schema designer. Analyze the app description and return the schema for the MAIN entity only.

CRITICAL RULES:
1. Return ONLY ONE entity - the main data entity (NOT User, NOT Auth)
2. Keep fields MINIMAL - only what's absolutely necessary
3. DO NOT include: id, createdAt, updatedAt, userId (auto-generated)
4. Think about INTUITIVE UX (e.g., Address Book apps NEED a "first name" string field)

EXACT OUTPUT FORMAT (single JSON object, no array):
{{"entity": "EntityName", "fields": [{{"name": "fieldName", "type": "type", "required": true}}]}}

Valid types: string, text, number, boolean, datetime, email, url

EXAMPLES:
- "todo app" -> {{"entity": "Todo", "fields": [{{"name": "title", "type": "string", "required": true}}, {{"name": "completed", "type": "boolean", "required": true}}]}}
- "contact manager" -> {{"entity": "Contact", "fields": [{{"name": "firstName", "type": "string", "required": true}}, {{"name": "lastName", "type": "string", "required": true}}, {{"name": "email", "type": "email", "required": false}}]}}

App description: "{query}"

Keep the schema dead simple, and focus on the most basic fields needed. For example, if the app is a contact manager, include "firstName" and "lastName" fields, but do NOT add address fields unless absolutely necessary.

Return ONLY the JSON object for the MAIN entity:"""


def infer_schema(
    user_query: str,
    chat_sdk: Optional[Any] = None,
) -> Dict[str, Any]:
    """Infer schema fields from user's natural language query using AI.

    Uses cascading fallback: Perplexity API -> Local LLM -> Generic fallback.

    Args:
        user_query: The user's app description (e.g., "build me a todo app")
        chat_sdk: Optional ChatSDK instance for local LLM fallback

    Returns:
        Dictionary with:
        - entity: Suggested entity name (e.g., "Todo", "Task", "Contact")
        - fields: List of field definitions with name, type, required
        - source: Which method was used ("perplexity", "local_llm", "fallback")
    """
    # Check if this looks like an app creation request
    if not _is_app_creation_request(user_query):
        logger.debug(f"Query doesn't appear to be app creation: {user_query[:50]}...")
        return {"entity": None, "fields": [], "source": "skipped"}

    # Try Perplexity first (if API key is set)
    perplexity_key = os.getenv("PERPLEXITY_API_KEY")
    if perplexity_key:
        logger.info("Attempting schema inference via Perplexity")
        result = _infer_via_perplexity(user_query)
        if result.get("entity"):
            result["source"] = "perplexity"
            logger.info(
                f"Perplexity inferred schema: {result['entity']} with {len(result['fields'])} fields"
            )
            return result

    # Fall back to local LLM
    if chat_sdk:
        logger.debug("Attempting schema inference via local LLM")
        result = _infer_via_local_llm(user_query, chat_sdk)
        if result.get("entity"):
            result["source"] = "local_llm"
            logger.debug(
                f"Local LLM inferred schema: {result['entity']} with {len(result['fields'])} fields"
            )
            return result

    # Final fallback - no schema inference available
    logger.warning("No schema inference available - returning empty schema")
    return {"entity": None, "fields": [], "source": "fallback"}


def _is_app_creation_request(query: str) -> bool:
    """Check if the query appears to be an app creation request.

    Uses semantic patterns to detect app creation intent without hardcoding
    specific app types.
    """
    query_lower = query.lower()

    # App creation indicators (verbs + objects)
    creation_verbs = [
        "build",
        "create",
        "make",
        "develop",
        "generate",
        "design",
        "implement",
    ]
    app_objects = [
        "app",
        "application",
        "crud",
        "website",
        "site",
        "system",
        "tracker",
        "manager",
        "dashboard",
    ]

    # Check for creation verb + app object pattern
    has_creation_verb = any(verb in query_lower for verb in creation_verbs)
    has_app_object = any(obj in query_lower for obj in app_objects)

    # Also check for "for managing X" or "to track X" patterns
    management_patterns = [
        "for managing",
        "to manage",
        "to track",
        "for tracking",
        "to organize",
        "for organizing",
    ]
    has_management_pattern = any(
        pattern in query_lower for pattern in management_patterns
    )

    return (has_creation_verb and has_app_object) or has_management_pattern


def _infer_via_perplexity(query: str) -> Dict[str, Any]:
    """Infer schema using Perplexity API.

    Args:
        query: User's app description

    Returns:
        Schema result or empty dict on failure
    """
    try:
        from gaia.mcp.external_services import get_perplexity_service

        service = get_perplexity_service()
        prompt = SCHEMA_INFERENCE_PROMPT.format(query=query)
        result = service.search_web(prompt)

        if result.get("success") and result.get("answer"):
            return _parse_schema_response(result["answer"])

        logger.warning(
            f"Perplexity inference failed: {result.get('error', 'No answer')}"
        )
        return {"entity": None, "fields": []}

    except Exception as e:
        logger.warning(f"Perplexity inference error: {e}")
        return {"entity": None, "fields": []}


def _infer_via_local_llm(query: str, chat_sdk: Any) -> Dict[str, Any]:
    """Infer schema using local LLM via ChatSDK.

    Args:
        query: User's app description
        chat_sdk: ChatSDK instance for LLM calls

    Returns:
        Schema result or empty dict on failure
    """
    try:
        prompt = SCHEMA_INFERENCE_PROMPT.format(query=query)
        response = chat_sdk.send(prompt, max_tokens=500)

        if response and response.text:
            return _parse_schema_response(response.text)

        logger.warning("Local LLM returned empty response")
        return {"entity": None, "fields": []}

    except Exception as e:
        logger.warning(f"Local LLM inference error: {e}")
        return {"entity": None, "fields": []}


def _parse_schema_response(response: str) -> Dict[str, Any]:
    """Parse schema JSON from AI response.

    Handles various response formats including:
    - Clean JSON object
    - JSON array (takes first non-User entity)
    - JSON in markdown code blocks
    - JSON with surrounding text

    Args:
        response: Raw AI response text

    Returns:
        Parsed schema or empty dict on failure
    """
    try:
        # Try to extract JSON from the response
        json_str = _extract_json(response)
        if not json_str:
            logger.warning(f"Could not extract JSON from response: {response[:100]}...")
            return {"entity": None, "fields": []}

        logger.debug(f"Extracted JSON: {json_str[:200]}...")
        data = json.loads(json_str)

        # Handle array response - take first non-User/Auth entity
        if isinstance(data, list):
            logger.debug(
                f"Response is array with {len(data)} items, selecting main entity"
            )
            skip_names = {"user", "auth", "session", "account"}
            for item in data:
                if isinstance(item, dict):
                    name = item.get("entity", "").lower()
                    if name and name not in skip_names:
                        data = item
                        break
            else:
                # No suitable entity found, take first if available
                data = data[0] if data else {}

        # Validate it's a dict
        if not isinstance(data, dict):
            logger.warning(f"Expected dict but got {type(data).__name__}")
            return {"entity": None, "fields": []}

        # Validate required fields
        entity = data.get("entity")
        fields = data.get("fields", [])

        if not entity or not isinstance(fields, list):
            logger.warning(f"Invalid schema format: {data}")
            return {"entity": None, "fields": []}

        # Normalize fields, filter out auto-generated ones
        normalized_fields = []
        skip_fields = {"id", "createdat", "updatedat", "userid"}
        for field in fields:
            if isinstance(field, dict) and "name" in field:
                if field["name"].lower() in skip_fields:
                    continue
                normalized_fields.append(
                    {
                        "name": field["name"],
                        "type": field.get("type", "string"),
                        "required": field.get("required", False),
                    }
                )

        logger.debug(f"Parsed schema: {entity} with {len(normalized_fields)} fields")
        return {"entity": entity, "fields": normalized_fields}

    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error: {e}")
        return {"entity": None, "fields": []}
    except Exception as e:
        logger.warning(f"Schema parse error: {e}")
        return {"entity": None, "fields": []}


def _extract_json(text: str) -> Optional[str]:
    """Extract JSON from text, handling various formats.

    Args:
        text: Raw text possibly containing JSON

    Returns:
        Extracted JSON string or None
    """
    # Try to find JSON in code blocks first
    code_block_patterns = [
        r"```json\s*([\s\S]*?)\s*```",
        r"```\s*([\s\S]*?)\s*```",
    ]

    for pattern in code_block_patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()

    # Check if text starts with array or object to pick correct pattern
    stripped = text.strip()
    if stripped.startswith("["):
        # JSON array - extract it
        bracket_match = re.search(r"\[[\s\S]*\]", text)
        if bracket_match:
            return bracket_match.group(0)

    # Try to find JSON object directly
    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        return brace_match.group(0)

    # Return stripped text as last resort
    return text.strip()


def format_schema_context(schema_result: Dict[str, Any]) -> str:
    """Format inferred schema for injection into system prompt.

    Args:
        schema_result: Result from infer_schema()

    Returns:
        Formatted string for system prompt, or empty string if no schema
    """
    entity = schema_result.get("entity")
    fields = schema_result.get("fields", [])
    source = schema_result.get("source", "unknown")

    if not entity or not fields:
        return ""

    # Format fields for prompt
    field_lines = []
    for field in fields:
        name = field["name"]
        field_type = field["type"]
        required = "required" if field.get("required") else "optional"
        field_lines.append(f"  - {name}: {field_type} ({required})")

    fields_str = "\n".join(field_lines)

    context = f"""
## AI-Inferred Schema (source: {source})

Based on the user's request, the following schema has been determined:

**Entity:** {entity}
**Fields:**
{fields_str}

IMPORTANT: Use these fields when creating the data model and components.
- Use `manage_data_model` with these field names and types
- Use the same fields consistently across all tools (API, components, forms)
- Boolean fields (like 'completed') should render as checkboxes in forms and lists
"""

    return context
