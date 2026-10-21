# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia -p``: run the flagship agent once, headless, and print the answer.

The scriptable counterpart of ``claude -p``. stdout carries only the answer
(``--output-format text``) or one JSON object (``--output-format json``);
progress, logs and failure reasons go to stderr. The exit code is 0 when the
turn succeeded and 1 otherwise.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

from gaia.logger import get_logger

log = get_logger(__name__)

OUTPUT_FORMATS = ("text", "json")


def read_prompt(prompt: Optional[str]) -> str:
    """The prompt from ``-p``, or from stdin when ``-p`` was given no text."""
    if prompt:
        return prompt
    if sys.stdin is None or sys.stdin.isatty():
        raise ValueError(
            'gaia -p needs a prompt: pass it as `gaia -p "<prompt>"` or pipe it '
            'on stdin (`echo "<prompt>" | gaia -p`).'
        )
    text = sys.stdin.read().strip()
    if not text:
        raise ValueError("gaia -p read an empty prompt from stdin.")
    return text


def _cached_tokens(conversation: List[Any]) -> int:
    from gaia.agents.base.agent import _sum_cached_tokens

    return int(_sum_cached_tokens([m for m in conversation if isinstance(m, dict)]))


def _turn_error(result: Dict[str, Any]) -> str:
    """Why a turn did not succeed, from what the agent recorded."""
    if result.get("error"):
        return str(result["error"])
    history = [e for e in result.get("error_history") or [] if isinstance(e, dict)]
    if history:
        return str(history[-1].get("error") or history[-1])
    return f"the turn ended with status '{result.get('status') or 'error'}'"


def summarize(
    result: Dict[str, Any],
    model: Optional[str],
    duration_s: float,
    verbose: bool = False,
) -> Dict[str, Any]:
    """The JSON record ``--output-format json`` prints for one turn."""
    conversation = result.get("conversation") or []
    status = result.get("status") or "error"
    record: Dict[str, Any] = {
        "type": "result",
        "is_error": status != "success",
        "status": status,
        "result": result.get("result") or "",
        "model": model,
        "num_steps": int(result.get("steps_taken") or 0),
        "num_tool_calls": sum(
            1 for m in conversation if isinstance(m, dict) and m.get("role") == "tool"
        ),
        "input_tokens": int(result.get("input_tokens") or 0),
        "cached_tokens": _cached_tokens(conversation),
        "output_tokens": int(result.get("output_tokens") or 0),
        "duration_s": round(duration_s, 2),
        "error_count": int(result.get("error_count") or 0),
    }
    if record["is_error"]:
        record["error"] = _turn_error(result)
    if verbose:
        record["conversation"] = conversation
    return record


def configure_console(console: Any, skip_permissions: bool, full_access: bool) -> None:
    """Set a headless console's permissions from the -p flags."""
    console.silence_final_answer = True
    # Headless: without this, a confirmation-gated tool is denied, as in `claude -p`.
    console.auto_approve_gated_tools = skip_permissions or full_access
    # Asked for by name on the command line, as the TUI's /full-access is.
    console.full_access = full_access


def _emit_failure(output_format: str, message: str, code: int = 1) -> int:
    """Report a run that never produced a turn, in the shape stdout promises."""
    if output_format == "json":
        print(
            json.dumps(
                {
                    "type": "result",
                    "is_error": True,
                    "status": "error",
                    "error": message,
                },
                ensure_ascii=False,
            )
        )
    print(f"❌ gaia -p: {message}", file=sys.stderr)
    return code


def run(
    prompt: Optional[str],
    *,
    model: Optional[str] = None,
    output_format: str = "text",
    verbose: bool = False,
    max_steps: Optional[int] = None,
    skip_permissions: bool = False,
    full_access: bool = False,
    base_url: Optional[str] = None,
) -> int:
    """Run one headless turn and print its result. Returns the exit code."""
    if output_format not in OUTPUT_FORMATS:
        raise ValueError(
            f"Unknown --output-format {output_format!r}; choose one of: "
            f"{', '.join(OUTPUT_FORMATS)}."
        )
    try:
        text = read_prompt(prompt)
        record = _run_turn(
            text,
            model=model,
            verbose=verbose,
            max_steps=max_steps,
            skip_permissions=skip_permissions,
            full_access=full_access,
            base_url=base_url,
        )
    except KeyboardInterrupt:
        return _emit_failure(output_format, "interrupted", code=130)
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report, never a traceback
        # INFO reaches the log file, not the console: the traceback stays findable.
        log.info("gaia -p failed", exc_info=True)
        return _emit_failure(output_format, f"{type(exc).__name__}: {exc}")

    if output_format == "json":
        print(json.dumps(record, ensure_ascii=False))
    else:
        print(record["result"])
    if record["is_error"]:
        print(
            f"❌ gaia -p: the turn ended with status '{record['status']}'"
            + (f": {record['error']}" if record.get("error") else ""),
            file=sys.stderr,
        )
        return 1
    return 0


def _run_turn(
    text: str,
    *,
    model: Optional[str],
    verbose: bool,
    max_steps: Optional[int],
    skip_permissions: bool,
    full_access: bool,
    base_url: Optional[str],
) -> Dict[str, Any]:
    from gaia.cli import initialize_lemonade_for_agent
    from gaia.llm.lemonade_client import resolve_lemonade_base_url
    from gaia.logger import log_manager

    log_manager.configure_agent_console(debug=False)

    if model is None:
        from gaia.config import GaiaConfig

        model = GaiaConfig.load().default_model or None

    # quiet: stdout is the answer alone, so start-up progress must not reach it.
    success, detected_base_url = initialize_lemonade_for_agent(
        "chat", quiet=True, base_url=base_url, model=model
    )
    if not success:
        raise RuntimeError(
            "The model server could not be started. Run `gaia daemon status` for "
            "its state and `gaia init` if Lemonade is not installed."
        )

    try:
        from gaia_agent.agent import GaiaAgent, GaiaAgentConfig
    except ImportError as e:
        from gaia.agents.install_hints import agent_import_error_message

        raise RuntimeError(
            agent_import_error_message(
                e,
                "The GAIA agent is not installed",
                "gaia-agent-gaia",
                next_step="Then re-run `gaia -p`.",
            )
        ) from e

    agent = GaiaAgent(
        GaiaAgentConfig(
            base_url=base_url or detected_base_url or resolve_lemonade_base_url(),
            model_id=model,
            max_steps=max_steps,
            silent_mode=True,
            streaming=False,
            allowed_paths=[os.getcwd()],
        )
    )
    configure_console(agent.console, skip_permissions, full_access)
    if full_access:
        log.warning("Full access ENABLED at launch (gaia -p): shell gates off")
        print(
            "⚠️  gaia -p: full access is on — shell guardrails off, paths outside "
            "the current folder granted without asking.",
            file=sys.stderr,
        )
    agent._ensure_tool_loader_reset()  # pylint: disable=protected-access

    started = time.time()
    try:
        result = agent.process_query(text) or {}
    finally:
        from gaia.agents.base.memory import drain_memory_extraction

        drain_memory_extraction(agent)
    return summarize(
        result, agent.model_id or model, time.time() - started, verbose=verbose
    )
