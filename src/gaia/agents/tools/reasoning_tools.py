# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""ReasoningEffortToolsMixin — the agent picks how hard its model reasons.

For models that grade reasoning instead of switching it (gpt-oss: low, medium,
high). A routine step at "high" wastes time; a hard step at "low" gets the
answer wrong. The model is the one that knows which step is which, so it sets
the level, and the level returns to the model's default at the next user turn.
"""

from typing import Any, Dict, Optional

from gaia.llm.lemonade_client import (
    REASONING_EFFORTS,
    requested_reasoning_effort,
    resolve_default_chat_model,
)
from gaia.logger import get_logger

logger = get_logger(__name__)


class ReasoningEffortToolsMixin:
    """
    Mixin providing a reasoning-effort tool.

    Tools provided:
    - set_reasoning_effort: Change the effort for the rest of this turn

    Registered only when the agent's model grades reasoning, so other models'
    prompts carry no tool they cannot use. ``Agent`` reads the chosen level
    through :meth:`reasoning_effort_kwargs` on every model call.
    """

    _reasoning_effort: Optional[str] = None

    def _effort_model(self) -> Optional[str]:
        return getattr(self, "model_id", None) or resolve_default_chat_model()

    def model_grades_reasoning(self) -> bool:
        # Claude ignores llama.cpp template fields.
        if getattr(self, "_use_claude", False):
            return False
        return requested_reasoning_effort(self._effort_model()) is not None

    def register_reasoning_effort_tools(self) -> None:
        """Register set_reasoning_effort when the model grades reasoning."""
        if not self.model_grades_reasoning():
            return
        from gaia.agents.base.tools import tool

        # Per agent, not global: the closure binds this agent's effort.
        if not hasattr(self, "_tool_overrides"):
            self._tool_overrides = {}

        @tool(display_label="Setting reasoning effort", registry=self._tool_overrides)
        def set_reasoning_effort(level: str, reason: str = "") -> Dict[str, Any]:
            """Set how much you reason for the rest of this request: low, medium or high.

            Set it once, when you know what the request needs: high for a
            multi-step plan, debugging, a calculation or a subtle comparison;
            low when the rest is routine (reading or listing files, lookups,
            a short reply). Each change makes the model re-read the whole
            conversation, so do not switch it step by step. Lasts until the
            user sends a new message.

            Args:
                level: "low", "medium" or "high".
                reason: Why this step needs that level, in a few words.
            """
            return self._set_reasoning_effort(level, reason)

        instance_tools = getattr(self, "_instance_tools", None)
        if instance_tools is not None:
            instance_tools["set_reasoning_effort"] = self._tool_overrides[
                "set_reasoning_effort"
            ]

    def _set_reasoning_effort(self, level: str, reason: str) -> Dict[str, Any]:
        if not self.model_grades_reasoning():
            # The session switched to a model with no effort setting.
            return {
                "status": "error",
                "error": (
                    f"{self._effort_model()} has no reasoning-effort setting, so "
                    "nothing changed. Continue without it."
                ),
            }
        chosen = (level or "").strip().lower()
        if chosen not in REASONING_EFFORTS:
            return {
                "status": "error",
                "error": (
                    f"level must be one of {', '.join(REASONING_EFFORTS)}; got "
                    f"{level!r}, so the effort is unchanged "
                    f"({self._current_reasoning_effort()})."
                ),
            }
        previous = self._current_reasoning_effort()
        self._reasoning_effort = chosen
        logger.info(
            "Reasoning effort %s -> %s: %s", previous, chosen, reason or "(no reason)"
        )
        return {"status": "success", "previous": previous, "level": chosen}

    def _current_reasoning_effort(self) -> Optional[str]:
        return self._reasoning_effort or requested_reasoning_effort(
            self._effort_model()
        )

    def reset_reasoning_effort(self) -> None:
        """Back to the model's default; called at the start of each user turn."""
        self._reasoning_effort = None

    def reasoning_effort_kwargs(self) -> Dict[str, Any]:
        """Request fields carrying the level the agent chose, if it chose one."""
        if not self._reasoning_effort or not self.model_grades_reasoning():
            return {}
        return {"chat_template_kwargs": {"reasoning_effort": self._reasoning_effort}}
