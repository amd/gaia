# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Optional mixin that adds governance to a GAIA ``Agent`` subclass.

Usage::

    from gaia import Agent
    from gaia.governance import GaiaGovernanceAdapter, GovernedAgentMixin

    class MyGovernedAgent(GovernedAgentMixin, MyAgent):
        pass

    agent = MyGovernedAgent(governance_adapter=my_adapter, actor_id="alice")

The mixin wraps :meth:`Agent._execute_tool` through ``super()``. If no
adapter is supplied it is a no-op, so adding the mixin to an agent has
zero runtime cost by default. **No edits to ``gaia.agents.base.agent``
are required.**

Decision flow
-------------

Every intercepted tool call drives the full adapter pipeline:

1. The tool call is mapped to an :class:`ActionRequest`.
2. ``adapter.govern_action`` yields a :class:`GovernanceDecision`.
3. A synthetic :class:`WorkflowTransition` is built and passed through
   ``adapter.handle_transition``.
4. **ALLOW** → the underlying ``_execute_tool`` runs.
5. **BLOCK** → the tool is short-circuited with a denied result and
   the adapter issues a BLOCK receipt.
6. **REVIEW** → a checkpoint is opened. The mixin asks the first
   available reviewer — ``self.console.confirm_tool_execution`` (GAIA's
   existing confirmation surface) or an explicit
   ``governance_reviewer`` callback — and resolves the checkpoint
   APPROVE / REJECT accordingly. An APPROVE runs the tool; a REJECT
   short-circuits. Either way, a receipt is issued.

If ``REVIEW`` decisions are returned and neither a console nor a
reviewer is available, the mixin **fails closed** and rejects the
tool. This matches the intent of the decision type ("do not execute
without review") and avoids silent pass-through.
"""

from __future__ import annotations

import logging
from typing import Any

from .action_mapper import map_gaia_tool_call_to_action_request
from .adapter import GaiaGovernanceAdapter
from .config import GovernanceCallback, GovernanceConfig, GovernanceReviewer
from .decorators import read_risk_tags
from .exceptions import GaiaGovernanceError
from .schemas import (
    ActionRequest,
    CheckpointResolution,
    GovernanceDecision,
    WorkflowTransition,
    new_id,
)

logger = logging.getLogger(__name__)


class GovernedAgentMixin:
    """Mix-in: intercept ``_execute_tool`` and drive the full adapter flow."""

    governance_adapter: GaiaGovernanceAdapter | None
    _governance_actor_id: str
    _governance_workflow_id: str | None
    _governance_risk_tags: dict[str, list[str]]
    _governance_callback: GovernanceCallback | None
    _governance_reviewer: GovernanceReviewer | None

    def __init__(
        self,
        *args: Any,
        governance: GovernanceConfig | None = None,
        governance_adapter: GaiaGovernanceAdapter | None = None,
        governance_actor_id: str = "gaia-agent",
        governance_workflow_id: str | None = None,
        governance_risk_tags: dict[str, list[str]] | None = None,
        governance_callback: GovernanceCallback | None = None,
        governance_reviewer: GovernanceReviewer | None = None,
        **kwargs: Any,
    ) -> None:
        # Prefer the structured config if supplied; fall back to the
        # per-kwarg form so both styles work.
        # Deep-copy the inner lists so callers cannot mutate the agent's
        # risk-tag table after construction by holding onto the original
        # reference (e.g. ``tags = ["review"]; agent = MyAgent(...,
        # governance_risk_tags={"foo": tags}); tags.append("blocked")``).
        if governance is not None:
            self.governance_adapter = governance.adapter
            self._governance_actor_id = governance.actor_id
            self._governance_workflow_id = governance.workflow_id
            self._governance_risk_tags = {
                k: list(v) for k, v in governance.risk_tags.items()
            }
            self._governance_callback = governance.callback
            self._governance_reviewer = governance.reviewer
        else:
            self.governance_adapter = governance_adapter
            self._governance_actor_id = governance_actor_id
            self._governance_workflow_id = governance_workflow_id
            self._governance_risk_tags = {
                k: list(v) for k, v in (governance_risk_tags or {}).items()
            }
            self._governance_callback = governance_callback
            self._governance_reviewer = governance_reviewer
        super().__init__(*args, **kwargs)

    # ---- public plumbing --------------------------------------------------

    def _execute_tool(self, tool_name: str, tool_args: dict[str, Any]) -> Any:
        adapter = self.governance_adapter
        if adapter is None:
            return super()._execute_tool(tool_name, tool_args)  # type: ignore[misc]

        # HIGH-2 fix: resolve the canonical tool name BEFORE governance so
        # risk tags keyed to the canonical name (e.g. ``mcp_time_get_current_time``)
        # cannot be bypassed by the LLM calling the unprefixed alias
        # (e.g. ``get_current_time``). Falls through to the raw name when
        # the base Agent does not expose a resolver.
        canonical = self._resolve_canonical_tool_name(tool_name)
        action = self._build_action_request(canonical, tool_args)
        decision = adapter.govern_action(action)
        self._invoke_callback(tool_name, tool_args, action, decision)

        transition = self._build_transition(action, tool_args)
        outcome = adapter.handle_transition(transition, decision)

        if outcome.status == "CONTINUE":
            return super()._execute_tool(tool_name, tool_args)  # type: ignore[misc]

        if outcome.status == "TERMINATED":
            return self._denied_result(
                tool_name,
                decision.decision,
                decision.reason,
                decision.policy_version,
                decision.rule_ids,
                outcome.metadata.get("receipt_id"),
            )

        if outcome.status == "CHECKPOINT_OPEN":
            return self._handle_review_checkpoint(
                adapter,
                tool_name,
                tool_args,
                decision,
                transition,
                outcome.checkpoint_id,
            )

        # Unknown outcome → fail closed.
        return self._denied_result(
            tool_name,
            "ERROR",
            f"unknown transition outcome: {outcome.status}",
            decision.policy_version,
            [],
            None,
        )

    # ---- internals --------------------------------------------------------

    def _resolve_canonical_tool_name(self, tool_name: str) -> str:
        """Return the canonical tool name if the base Agent can resolve it.

        GAIA's ``Agent._resolve_tool_name`` maps unprefixed aliases
        (e.g. ``get_current_time``) to registry keys
        (e.g. ``mcp_time_get_current_time``). Governance must key on the
        canonical name or risk tags can be trivially bypassed.
        """
        resolver = getattr(self, "_resolve_tool_name", None)
        if callable(resolver):
            try:
                resolved = resolver(tool_name)  # pylint: disable=not-callable
                if resolved:
                    return resolved
            except LookupError:
                pass  # Tool not in registry — fall through to raw name.
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "governance: _resolve_tool_name raised unexpectedly for %r; "
                    "falling back to raw name — canonical tag lookup may be incomplete",
                    tool_name,
                    exc_info=True,
                )
        return tool_name

    def _build_action_request(
        self, tool_name: str, tool_args: dict[str, Any]
    ) -> ActionRequest:
        # Merge decorator-declared tags with explicit dict tags — additive
        # (union, deduplicated). Decorator tags come first; explicit dict
        # tags are appended. Neither side overrides the other.
        decorated_tags = read_risk_tags(self._lookup_tool_fn(tool_name))
        explicit_tags = self._governance_risk_tags.get(tool_name, [])
        merged_tags = list(dict.fromkeys([*decorated_tags, *explicit_tags]))
        return map_gaia_tool_call_to_action_request(
            tool_name,
            tool_args,
            {
                "actor_id": self._governance_actor_id,
                "workflow_id": self._governance_workflow_id,
                "risk_tags": merged_tags,
                "source": "gaia",
            },
        )

    @staticmethod
    def _lookup_tool_fn(tool_name: str) -> Any | None:
        """Return the registered tool function, or None if absent.

        Read through GAIA's tool registry so we can inspect
        ``__gaia_governance__`` attributes placed by :func:`govern`.
        """
        try:
            from gaia.agents.base.tools import _TOOL_REGISTRY  # type: ignore
        except ImportError:
            return None
        entry = _TOOL_REGISTRY.get(tool_name)
        if not entry:
            return None
        return entry.get("function")

    def _build_transition(
        self, action: ActionRequest, tool_args: dict[str, Any]
    ) -> WorkflowTransition:
        workflow_id = self._governance_workflow_id or f"wf_{self._governance_actor_id}"
        return WorkflowTransition(
            workflow_id=workflow_id,
            transition_id=new_id("tx"),
            from_state="READY",
            to_state=f"TOOL:{action.tool_name}",
            transition_type="tool_call",
            related_action_id=action.action_id,
            payload={"tool_args": dict(tool_args)},
        )

    def _invoke_callback(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        action: ActionRequest,
        decision: GovernanceDecision,
    ) -> None:
        if self._governance_callback is None:
            return
        try:
            self._governance_callback(tool_name, tool_args, action, decision)
        except Exception:  # pylint: disable=broad-exception-caught
            # Observational callbacks must never break tool execution.
            logger.warning(
                "governance: callback raised for tool %r; continuing",
                tool_name,
                exc_info=True,
            )

    def _handle_review_checkpoint(
        self,
        adapter: GaiaGovernanceAdapter,
        tool_name: str,
        tool_args: dict[str, Any],
        decision: GovernanceDecision,
        transition: WorkflowTransition,
        checkpoint_id: str | None,
    ) -> Any:
        if checkpoint_id is None:
            raise GaiaGovernanceError("CHECKPOINT_OPEN without checkpoint_id")
        approved, review_error = self._prompt_review(tool_name, tool_args, decision)
        # Stamp the reject reason with the exception type/message when the
        # REJECT was caused by a reviewer crash, so the audit log can tell
        # "reviewer chose no" apart from "reviewer raised".
        if approved:
            resolution_label = "APPROVE"
            reason = "reviewer approved"
        elif review_error is not None:
            resolution_label = "REJECT"
            reason = f"reviewer raised {type(review_error).__name__}: {review_error}"
        else:
            resolution_label = "REJECT"
            reason = "reviewer rejected"
        resolution = CheckpointResolution(
            resolution=resolution_label,
            actor_id=self._governance_actor_id,
            reason=reason,
        )
        resolved = adapter.resolve_checkpoint(
            checkpoint_id, resolution, transition.workflow_id
        )
        if resolved.status == "RESUMED":
            return super()._execute_tool(tool_name, tool_args)  # type: ignore[misc]
        return self._denied_result(
            tool_name,
            "REVIEW_REJECTED",
            "tool rejected at review checkpoint",
            decision.policy_version,
            decision.rule_ids,
            resolved.metadata.get("receipt_id"),
        )

    def _prompt_review(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        decision: GovernanceDecision,
    ) -> tuple[bool, BaseException | None]:
        """Ask the registered reviewer to approve or reject.

        Returns ``(approved, exception_or_None)``. When the reviewer
        raises, the second element captures the exception so the audit
        log can record that the REJECT was due to a crash, not a "no"
        decision. ``BaseException`` (KeyboardInterrupt, SystemExit) is
        intentionally NOT caught — those should propagate.

        Only an **explicit** ``governance_reviewer`` callback is
        honored. GAIA's ``AgentConsole.confirm_tool_execution`` is NOT
        consulted automatically because its default implementation
        auto-approves (``OutputHandler.confirm_tool_execution`` returns
        ``True`` unless a subclass overrides it). Silently treating an
        auto-approving console as a reviewer would break the
        fail-closed contract.

        Callers that want console-driven review must pass::

            governance_reviewer=lambda name, args, _d: (
                self.console.confirm_tool_execution(name, args)
            )

        when they have verified their console actually blocks
        (e.g. ``SSEOutputHandler`` which awaits a frontend response).
        """
        reviewer = self._governance_reviewer
        if reviewer is None:
            # Fail closed: REVIEW means "do not run without review".
            return False, None
        try:
            return bool(reviewer(tool_name, tool_args, decision)), None
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                "governance: reviewer raised for tool %r; failing closed",
                tool_name,
                exc_info=True,
            )
            return False, exc

    @staticmethod
    def _denied_result(
        tool_name: str,
        governance_decision: str,
        reason: str,
        policy_version: str,
        rule_ids: list[str],
        receipt_id: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "denied",
            "error": f"Tool '{tool_name}' blocked by governance: {reason}",
            "governance_decision": governance_decision,
            "policy_version": policy_version,
            "rule_ids": list(rule_ids),
            "error_displayed": True,
        }
        if receipt_id is not None:
            payload["receipt_id"] = receipt_id
        return payload
