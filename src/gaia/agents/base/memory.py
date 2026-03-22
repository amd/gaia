# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
MemoryMixin: Persistent memory for any GAIA agent.

Hooks into the Agent lifecycle at 3 points:
1. System prompt injection (get_memory_system_prompt)
2. Tool execution wrapper (_execute_tool) — auto-logs calls + learns from errors
3. Post-query storage (_after_process_query) — stores conversations + extracts facts

Provides 5 LLM-facing tools: remember, recall, update_memory, forget, search_past_conversations.
Valid categories: fact, preference, error, skill, note, reminder.

Usage:
    class MyAgent(MemoryMixin, Agent):   # MemoryMixin MUST come before Agent
        def __init__(self, **kwargs):
            self.init_memory()          # Before super().__init__()
            super().__init__(**kwargs)

        def _register_tools(self):
            super()._register_tools()
            self.register_memory_tools()

Spec: docs/spec/agent-memory-architecture.md
"""

import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from gaia.agents.base.memory_store import (
    MAX_CONTENT_LENGTH,
    VALID_CATEGORIES,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Heuristic Extraction Patterns (simplified from gaia6)
# ============================================================================

# (compiled regex, category, context_override)
# context_override=None means use active context, "global" means always global
_PREFERENCE_PATTERNS = [
    (
        re.compile(
            r"(?:I\s+)?prefer\s+(.+?)(?:\s+(?:over|instead|rather)\s+|\.|,|$)",
            re.IGNORECASE,
        ),
        "preference",
        None,
    ),
    (
        re.compile(
            r"(?:always|never)\s+(.+?)(?:\.|,|$)",
            re.IGNORECASE,
        ),
        "preference",
        None,
    ),
]

_FACT_PATTERNS = [
    (
        re.compile(
            r"(?:my\s+name\s+is|I(?:'m| am)\s+(?:a|an|the)?\s*)\s*(.+?)(?:\.|,|$)",
            re.IGNORECASE,
        ),
        "fact",
        "global",  # Identity facts are always global
    ),
    (
        re.compile(
            r"(?:we|I)\s+(?:use|work with|build with)\s+(.+?)(?:\s+for\s+|\.|,|$)",
            re.IGNORECASE,
        ),
        "fact",
        None,
    ),
]

# Memory tools that should NOT be logged to tool_history
_MEMORY_TOOLS = frozenset(
    {"remember", "recall", "update_memory", "forget", "search_past_conversations"}
)


class MemoryMixin:
    """
    Mixin that gives any Agent persistent memory across sessions.

    Provides:
    - Working context via system prompt (preferences, facts, errors, upcoming)
    - Auto tool call logging with error learning
    - Conversation persistence with heuristic extraction
    - 5 CRUD tools for the LLM (remember, recall, update_memory, forget, search_past_conversations)
    """

    def init_memory(
        self, db_path: Optional[Path] = None, context: str = "global"
    ) -> None:
        """Initialize the memory subsystem.

        Creates/gets a MemoryStore instance and sets up session state.
        Call this BEFORE super().__init__() in your agent's __init__.

        Args:
            db_path: Optional path for the DB file. Default: ~/.gaia/memory.db
            context: Active context scope (e.g., 'work', 'personal', 'global').
        """
        from gaia.agents.base.memory_store import MemoryStore

        self._memory_store = MemoryStore(db_path)
        self._memory_store.prune()  # Enforce retention policy on startup (idempotent)
        # Decay confidence for stale knowledge on startup — this ensures long-running
        # servers (which never call reset_memory_session) still apply periodic decay.
        # Both prune() and apply_confidence_decay() are idempotent and fast on an
        # already-maintained database.
        self._memory_store.apply_confidence_decay()
        self._memory_session_id = str(uuid4())
        self._memory_context = context
        self._auto_extract_enabled = True
        # Stash for the process_query → _after_process_query handoff.
        # process_query() sets this to the clean user text before prepending
        # dynamic context, so _after_process_query() stores the original message
        # rather than the augmented version. Initialized here so the attribute
        # always exists even if _after_process_query() is called out-of-order.
        self._original_user_input: Optional[str] = None
        logger.info(
            "[MemoryMixin] initialized, session_id=%s context=%s",
            self._memory_session_id,
            context,
        )

    @property
    def memory_store(self):
        """Access the MemoryStore instance."""
        if not hasattr(self, "_memory_store"):
            raise RuntimeError("MemoryMixin not initialized. Call init_memory() first.")
        return self._memory_store

    @property
    def memory_session_id(self) -> str:
        """Current memory session ID.

        Raises RuntimeError if accessed before init_memory() is called, to
        prevent orphan session IDs that diverge from the one stored in the DB.
        """
        if not hasattr(self, "_memory_session_id"):
            raise RuntimeError("MemoryMixin not initialized. Call init_memory() first.")
        return self._memory_session_id

    @property
    def memory_context(self) -> str:
        """Current active context (e.g., 'work', 'personal', 'global')."""
        return getattr(self, "_memory_context", "global")

    def set_memory_context(self, context: str) -> None:
        """Switch active context. Affects system prompt filtering and default store context.

        Empty or whitespace-only context defaults to "global" — an empty string
        would match no stored items and silently lose all context-scoped lookups.

        Rebuilds the cached system prompt immediately so the new context's
        preferences/facts are visible to the LLM on the very next turn.
        Without this, the old context's content stays in the cache until the
        next natural rebuild (e.g., after adding tools).
        """
        context = (context or "").strip() or "global"
        old = self._memory_context
        self._memory_context = context
        logger.info("[MemoryMixin] context switched %s → %s", old, context)
        # Invalidate cached system prompt so the next query sees updated memory.
        # rebuild_system_prompt() is provided by the Agent base class.
        if hasattr(self, "rebuild_system_prompt"):
            self.rebuild_system_prompt()

    # ------------------------------------------------------------------
    # Hook 1: System Prompt Injection
    # ------------------------------------------------------------------

    def get_memory_system_prompt(self) -> str:
        """Build the STABLE memory section for the system prompt.

        Contains only content that rarely changes: preferences, facts, known errors.
        Time and upcoming items are intentionally excluded — they are injected
        per-turn via get_memory_dynamic_context() to keep this prompt frozen for
        LLM KV-cache reuse.
        """
        if not hasattr(self, "_memory_store"):
            return ""

        try:
            return self._build_stable_memory_prompt()
        except Exception as e:
            logger.warning("[MemoryMixin] failed to build stable memory prompt: %s", e)
            return ""

    def get_memory_dynamic_context(self) -> str:
        """Build the per-turn dynamic context string: current time + upcoming items.

        This is prepended to the user message each turn (not the system prompt),
        so the system prompt stays frozen for KV-cache reuse.
        Returns empty string if nothing time-sensitive is active.
        """
        if not hasattr(self, "_memory_store"):
            return ""

        try:
            return self._build_dynamic_memory_context()
        except Exception as e:
            logger.debug("[MemoryMixin] failed to build dynamic context: %s", e)
            return ""

    def _build_stable_memory_prompt(self) -> str:
        """Stable memory: preferences + facts + known errors. No timestamps."""
        ctx = self._memory_context
        sections = []

        # 1. Preferences (active context + global, exclude sensitive)
        prefs = self._get_context_items("preference", ctx, limit=10)
        if prefs:
            pref_lines = [f"  - {p['content']}" for p in prefs]
            sections.append("Preferences:\n" + "\n".join(pref_lines))

        # 2. Top facts (active context + global, by confidence)
        facts = self._get_context_items("fact", ctx, limit=5)
        if facts:
            fact_lines = [
                f"  - {f['content']} (confidence: {f['confidence']:.2f})" for f in facts
            ]
            sections.append("Known facts:\n" + "\n".join(fact_lines))

        # 3. Known error patterns
        errors = self._get_context_items("error", ctx, limit=5)
        if errors:
            error_lines = [f"  - {e['content']}" for e in errors]
            sections.append("Known errors to avoid:\n" + "\n".join(error_lines))

        if not sections:
            return ""

        result = "=== MEMORY ===\n" + "\n\n".join(sections)
        # Hard cap: prevent context overflow if many large items exist.
        # 4000 chars ≈ 1000 tokens — sufficient for preferences/facts without
        # crowding the actual conversation context.
        if len(result) > 4000:
            result = result[:4000] + "\n... (memory truncated)"
        return result

    def _build_dynamic_memory_context(self) -> str:
        """Dynamic per-turn context: current time + upcoming/overdue items."""
        store = self._memory_store
        ctx = self._memory_context
        lines = []

        # Current time
        now = datetime.now().astimezone()
        time_str = now.strftime("%Y-%m-%dT%H:%M:%S%z") + f" ({now.strftime('%A')})"
        lines.append(f"Current time: {time_str}")

        # Upcoming/overdue items
        upcoming = store.get_upcoming(within_days=7, context=ctx)
        if upcoming:
            up_lines = []
            for item in upcoming[:10]:
                due = item.get("due_at", "")[:10] if item.get("due_at") else "?"
                try:
                    due_dt = datetime.fromisoformat(item["due_at"])
                    # Old DB entries may have naive (no-timezone) due_at.
                    # Normalize to tz-aware before comparing with tz-aware `now`
                    # so the OVERDUE label is correct instead of silently wrong.
                    if due_dt.tzinfo is None:
                        due_dt = due_dt.astimezone()
                    label = "OVERDUE" if due_dt < now else "DUE"
                    up_lines.append(f"  - [{label} {due}] {item['content']}")
                except (ValueError, KeyError, TypeError):
                    up_lines.append(f"  - [DUE {due}] {item['content']}")
            lines.append("Upcoming/overdue:\n" + "\n".join(up_lines))
            lines.append(
                "After mentioning a time-sensitive item, call update_memory "
                "to set reminded_at so you don't repeat yourself."
            )

        return "[GAIA Memory Context]\n" + "\n\n".join(lines)

    def _get_context_items(
        self, category: str, context: str, limit: int = 10
    ) -> List[Dict]:
        """Get non-sensitive knowledge items for active context + global.

        Uses a single DB query (get_by_category_contexts) instead of two
        sequential get_by_category() calls, halving the DB round-trips when
        building the system prompt.
        """
        return self._memory_store.get_by_category_contexts(
            category, context, limit=limit
        )

    # ------------------------------------------------------------------
    # Hook 2: process_query Override (dynamic context injection)
    # ------------------------------------------------------------------

    def process_query(self, user_input, **kwargs):
        """Prepend per-turn dynamic context (time + upcoming) to the user message.

        The system prompt is left frozen so the LLM inference engine can reuse
        its KV cache across turns. Only the small dynamic section (current time,
        upcoming/overdue items) is injected per-turn by prepending it to the
        user message. The original user_input is saved so _after_process_query
        can store the clean version to conversation history.
        """
        # Save original so _after_process_query stores the clean user text
        self._original_user_input = user_input

        # Prepend dynamic context to the user message
        dynamic = self.get_memory_dynamic_context()
        augmented = f"{dynamic}\n\n{user_input}" if dynamic else user_input

        return super().process_query(augmented, **kwargs)

    # ------------------------------------------------------------------
    # Hook 3: _execute_tool Override (auto-logging)
    # ------------------------------------------------------------------

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
        """Override to auto-log every non-memory tool call.

        Memory tools are excluded to avoid noise and recursion.
        Failed tools auto-store errors as knowledge for future avoidance.
        """
        # Skip logging for memory tools
        if tool_name in _MEMORY_TOOLS:
            return super()._execute_tool(tool_name, tool_args)

        # Log non-memory tools
        start = time.time()
        error_msg = None
        result = None
        is_error = False

        try:
            result = super()._execute_tool(tool_name, tool_args)
            duration_ms = int((time.time() - start) * 1000)

            # Determine success from result dict
            is_error = isinstance(result, dict) and result.get("status") == "error"
            if is_error:
                error_msg = str(
                    result.get("error_brief") or result.get("error") or "Unknown error"
                )
        except Exception as exc:
            # Tool raised an exception — log it, then re-raise
            duration_ms = int((time.time() - start) * 1000)
            is_error = True
            error_msg = str(exc)
            result = {"status": "error", "error": error_msg}

            # Log to tool_history before re-raising
            try:
                if hasattr(self, "_memory_store"):
                    self._memory_store.log_tool_call(
                        session_id=self.memory_session_id,
                        tool_name=tool_name,
                        args=tool_args,
                        result_summary=f"EXCEPTION: {error_msg}"[:500],
                        success=False,
                        error=error_msg,
                        duration_ms=duration_ms,
                    )
                    self._auto_store_error(tool_name, error_msg)
            except Exception:
                pass
            raise

        # Truncate result summary
        result_str = str(result)
        result_summary = result_str[:500] if len(result_str) > 500 else result_str

        # Log to tool_history
        try:
            if hasattr(self, "_memory_store"):
                self._memory_store.log_tool_call(
                    session_id=self.memory_session_id,
                    tool_name=tool_name,
                    args=tool_args,
                    result_summary=result_summary,
                    success=not is_error,
                    error=error_msg,
                    duration_ms=duration_ms,
                )

                # Auto-store novel errors as knowledge
                if is_error and error_msg:
                    self._auto_store_error(tool_name, error_msg)
        except Exception as e:
            logger.debug("[MemoryMixin] tool logging failed: %s", e)

        return result

    def _auto_store_error(self, tool_name: str, error_msg: str) -> None:
        """Store a novel tool error as knowledge for future avoidance."""
        try:
            # Bail out if error_msg is empty: f"{tool_name}: " would pass store()'s
            # non-empty check but contain no useful signal for the LLM to learn from.
            if not error_msg or not error_msg.strip():
                return
            error_content = f"{tool_name}: {error_msg}"
            self._memory_store.store(
                category="error",
                content=error_content,
                source="error_auto",
                context=self._memory_context,
                confidence=0.5,
            )
            logger.debug("[MemoryMixin] auto-stored error: %s", error_content[:80])
        except Exception as e:
            logger.debug("[MemoryMixin] failed to auto-store error: %s", e)

    # ------------------------------------------------------------------
    # Hook 4: Post-Query Processing
    # ------------------------------------------------------------------

    def _after_process_query(self, user_input: str, assistant_response: str) -> None:
        """Store conversation turns and run heuristic extraction.

        Called after process_query() completes (via hook in agent.py).
        Uses _original_user_input (set by our process_query override) so that
        the dynamic context prefix is never persisted to conversation history.
        """
        if not hasattr(self, "_memory_store"):
            return

        # Use original (pre-augmentation) user text for storage.
        # _original_user_input is initialized to None in init_memory(); use
        # `or` so that None (not-yet-set) falls back to the passed user_input.
        clean_input = self._original_user_input or user_input

        # 1. Store conversation turns
        try:
            session_id = self.memory_session_id
            ctx = self._memory_context
            self._memory_store.store_turn(session_id, "user", clean_input, context=ctx)
            self._memory_store.store_turn(
                session_id, "assistant", assistant_response, context=ctx
            )
        except Exception as e:
            logger.warning("[MemoryMixin] failed to store conversation: %s", e)

        # 2. Heuristic extraction (user input only — assistant text is too noisy)
        if self._auto_extract_enabled:
            try:
                self._extract_heuristics(clean_input)
            except Exception as e:
                logger.debug("[MemoryMixin] heuristic extraction failed: %s", e)

    def _extract_heuristics(self, text: str) -> None:
        """Extract preferences and facts from user input via regex patterns.

        Uses finditer() so multiple matches per pattern are captured.
        Example: "I prefer Python. I prefer dark mode." → two preference entries.
        """
        # Cap input length to prevent quadratic regex backtracking on long inputs
        text = text[:2000]
        # Preference patterns
        for pattern, category, ctx_override in _PREFERENCE_PATTERNS:
            for match in pattern.finditer(text):
                extracted = match.group(0).strip()
                if 10 <= len(extracted) <= 300:
                    ctx = ctx_override or self._memory_context
                    self._memory_store.store(
                        category=category,
                        content=extracted,
                        source="heuristic",
                        confidence=0.3,
                        context=ctx,
                    )
                    logger.debug(
                        "[MemoryMixin] heuristic extracted: %s", extracted[:60]
                    )

        # Fact patterns
        for pattern, category, ctx_override in _FACT_PATTERNS:
            for match in pattern.finditer(text):
                extracted = match.group(0).strip()
                if 10 <= len(extracted) <= 300:
                    ctx = ctx_override or self._memory_context
                    self._memory_store.store(
                        category=category,
                        content=extracted,
                        source="heuristic",
                        confidence=0.3,
                        context=ctx,
                    )
                    logger.debug(
                        "[MemoryMixin] heuristic extracted: %s", extracted[:60]
                    )

    # ------------------------------------------------------------------
    # Tool Registration
    # ------------------------------------------------------------------

    def register_memory_tools(self) -> None:
        """Register the 5 memory tools with the agent's tool registry.

        Call this from _register_tools() in your agent subclass.

        KNOWN ARCHITECTURAL LIMITATION — global _TOOL_REGISTRY:
        The @tool decorator registers into a module-level global dict in
        gaia.agents.base.tools. Each call to register_memory_tools() overwrites
        the previous closures. In single-agent-per-process deployments (the
        current norm) this is harmless. However, if two MemoryMixin agents are
        instantiated in the same process (e.g., RoutingAgent spawning multiple
        agents), the second registration will overwrite the first agent's memory
        tool closures, silently redirecting them to the second agent's store.

        Mitigation: a per-agent tool registry (rather than the global dict) is
        the correct long-term fix. Until then, avoid running two MemoryMixin
        agents concurrently in the same process.
        """
        from gaia.agents.base.tools import tool

        mixin = self  # Capture for closures — see KNOWN ARCHITECTURAL LIMITATION above

        @tool
        def remember(
            fact: str,
            category: str = "fact",
            domain: str = "",
            due_at: str = "",
            context: str = "",
            sensitive: str = "false",
            entity: str = "",
        ) -> dict:
            """Store a fact, preference, or learning in persistent memory. Categories: fact, preference, error, skill, note, reminder. Use due_at for time-sensitive items (ISO 8601). Use context to scope (work, personal). Use sensitive=true for private data. Use entity for linking (person:name, app:name)."""
            # Validate fact is non-empty before hitting store(), which would raise
            # ValueError — memory tools bypass MemoryMixin's exception handler so
            # the exception would propagate instead of returning an error dict.
            if not fact or not fact.strip():
                return {"status": "error", "message": "fact must not be empty."}

            # Validate category against the single source of truth in memory_store
            if category not in VALID_CATEGORIES:
                return {
                    "status": "error",
                    "message": f"Invalid category. Use: {sorted(VALID_CATEGORIES)}",
                }

            # Validate due_at
            if due_at:
                try:
                    datetime.fromisoformat(due_at)
                except ValueError:
                    return {
                        "status": "error",
                        "message": "Invalid due_at. Use ISO 8601 format.",
                    }

            # Defaults
            ctx = context or mixin._memory_context
            sens = sensitive.lower() == "true" if sensitive else False

            was_truncated = len(fact) > MAX_CONTENT_LENGTH
            knowledge_id = mixin._memory_store.store(
                category=category,
                content=fact[:MAX_CONTENT_LENGTH],
                domain=domain or None,
                due_at=due_at or None,
                source="tool",
                context=ctx,
                sensitive=sens,
                entity=entity or None,
            )
            msg = f"Remembered: {fact[:80]}"
            if was_truncated:
                msg += f" (note: content was truncated to {MAX_CONTENT_LENGTH} chars)"
            return {
                "status": "stored",
                "knowledge_id": knowledge_id,
                "message": msg,
            }

        @tool
        def recall(
            query: str = "",
            category: str = "",
            context: str = "",
            entity: str = "",
            limit: int = 5,
        ) -> dict:
            """Search memory for relevant knowledge. With query: FTS5 search. Without: filter by category/context/entity. At least one parameter required."""
            if not any([query, category, context, entity]):
                return {
                    "status": "error",
                    "message": "Provide at least one of: query, category, context, entity",
                }

            # Clamp limit to prevent the LLM from requesting huge result sets
            limit = max(1, min(limit, 20))

            if query:
                results = mixin._memory_store.search(
                    query=query,
                    category=category or None,
                    context=context or None,
                    entity=entity or None,
                    top_k=limit,
                )
            elif entity:
                results = mixin._memory_store.get_by_entity(entity, limit=limit)
            elif category:
                results = mixin._memory_store.get_by_category(
                    category, context=context or None, limit=limit
                )
            else:
                # context-only filter — get_all_knowledge supports context without category
                page = mixin._memory_store.get_all_knowledge(
                    context=context, limit=limit
                )
                results = page.get("items", [])

            # Never expose sensitive items to the LLM through the recall tool.
            # Sensitive entries (API keys, credentials, etc.) are stored for
            # internal use only and must not appear in tool output.
            results = [r for r in results if not r.get("sensitive")]

            return {
                "status": "found" if results else "empty",
                "count": len(results),
                "results": results,
            }

        @tool
        def update_memory(
            knowledge_id: str,
            content: str = "",
            category: str = "",
            domain: str = "",
            due_at: str = "",
            reminded_at: str = "",
            context: str = "",
            sensitive: str = "",
            entity: str = "",
        ) -> dict:
            """Update an existing memory entry by ID. Only non-empty fields change. Set reminded_at=now after mentioning a time-sensitive item."""
            kwargs = {}
            if content:
                # Validate before passing to update() — whitespace-only would raise
                # ValueError there, which propagates since memory tools bypass the
                # MemoryMixin exception handler.
                if not content.strip():
                    return {
                        "status": "error",
                        "message": "content must not be empty or whitespace-only.",
                    }
                kwargs["content"] = content[:MAX_CONTENT_LENGTH]
            if category:
                if category not in VALID_CATEGORIES:
                    return {
                        "status": "error",
                        "message": f"Invalid category. Use: {sorted(VALID_CATEGORIES)}",
                    }
                kwargs["category"] = category
            if domain:
                kwargs["domain"] = domain
            if due_at:
                try:
                    datetime.fromisoformat(due_at)
                    kwargs["due_at"] = due_at
                except ValueError:
                    return {
                        "status": "error",
                        "message": "Invalid due_at. Use ISO 8601.",
                    }
            if reminded_at:
                if reminded_at.lower() == "now":
                    kwargs["reminded_at"] = datetime.now().astimezone().isoformat()
                else:
                    # Validate ISO 8601 — natural language strings like "tomorrow"
                    # would be stored as-is and silently break SQL date comparisons.
                    try:
                        datetime.fromisoformat(reminded_at)
                        kwargs["reminded_at"] = reminded_at
                    except ValueError:
                        return {
                            "status": "error",
                            "message": "Invalid reminded_at. Use ISO 8601 format or 'now'.",
                        }
            if context:
                kwargs["context"] = context
            if sensitive:
                kwargs["sensitive"] = sensitive.lower() == "true"
            if entity:
                kwargs["entity"] = entity

            if not kwargs:
                return {"status": "error", "message": "No fields to update."}

            content_truncated = content and len(content) > MAX_CONTENT_LENGTH
            success = mixin._memory_store.update(knowledge_id, **kwargs)
            if success:
                result = {"status": "updated", "knowledge_id": knowledge_id}
                if content_truncated:
                    result["note"] = "content was truncated to 2000 chars"
                return result
            return {"status": "not_found", "knowledge_id": knowledge_id}

        @tool
        def forget(knowledge_id: str) -> dict:
            """Remove a specific memory entry by ID."""
            removed = mixin._memory_store.delete(knowledge_id)
            if removed:
                return {"status": "removed", "knowledge_id": knowledge_id}
            return {"status": "not_found", "knowledge_id": knowledge_id}

        @tool
        def search_past_conversations(
            query: str = "", days: int = 0, limit: int = 10
        ) -> dict:
            """Search past conversations. Use query for keywords, days for time range, or both."""
            if not query and not days:
                return {
                    "status": "error",
                    "message": "Provide query (keywords) or days (time range) or both.",
                }

            # Clamp params to safe bounds to prevent table scans on huge ranges
            limit = max(1, min(limit, 50))
            if days:
                days = max(1, min(days, 365))

            results = []
            if query and days:
                # Keyword search + time filter
                keyword_results = mixin._memory_store.search_conversations(
                    query, limit=limit * 2
                )
                # Filter by time
                cutoff = (
                    datetime.now().astimezone() - timedelta(days=days)
                ).isoformat()
                results = [
                    r for r in keyword_results if r.get("timestamp", "") >= cutoff
                ][:limit]
            elif query:
                results = mixin._memory_store.search_conversations(query, limit=limit)
            elif days:
                results = mixin._memory_store.get_recent_conversations(
                    days=days, limit=limit
                )

            return {
                "status": "found" if results else "empty",
                "count": len(results),
                "results": results,
            }

        logger.info("[MemoryMixin] registered 5 memory tools")

    # ------------------------------------------------------------------
    # Session Management
    # ------------------------------------------------------------------

    def reset_memory_session(self) -> None:
        """Start a fresh memory session.

        Generates new session ID and applies confidence decay.
        """
        if hasattr(self, "_memory_store"):
            self._memory_store.apply_confidence_decay()
            self._memory_session_id = str(uuid4())
            logger.info(
                "[MemoryMixin] session reset, new session_id=%s",
                self._memory_session_id,
            )
