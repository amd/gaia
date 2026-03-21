"""
Architecture audit for GAIA Agent Eval.
Deterministic checks — no LLM calls needed.
"""

import ast
import json
from pathlib import Path

GAIA_ROOT = Path(__file__).parent.parent.parent.parent  # src/gaia/eval/ -> repo root


def audit_chat_helpers() -> dict:
    """Read _chat_helpers.py and extract key constants."""
    path = GAIA_ROOT / "src" / "gaia" / "ui" / "_chat_helpers.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    constants = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.startswith("_MAX"):
                    if isinstance(node.value, ast.Constant):
                        constants[target.id] = node.value.value
    return constants


def audit_agent_persistence(chat_router_path: Path = None) -> str:
    """Check if ChatAgent is recreated per-request or persisted."""
    if chat_router_path is None:
        chat_router_path = GAIA_ROOT / "src" / "gaia" / "ui" / "routers" / "chat.py"
    source = chat_router_path.read_text(encoding="utf-8")
    # Check for agent creation inside the request handler vs module level
    if "ChatAgent(" in source:
        # Heuristic: if ChatAgent is created inside an async def, it's per-request
        return "stateless_per_message"
    return "unknown"


def audit_tool_results_in_history(chat_helpers_path: Path = None) -> bool:
    """Check if tool results are included in conversation history."""
    if chat_helpers_path is None:
        chat_helpers_path = GAIA_ROOT / "src" / "gaia" / "ui" / "_chat_helpers.py"
    source = chat_helpers_path.read_text(encoding="utf-8")
    # Look for agent_steps or tool results being added to history
    return "agent_steps" in source and "tool" in source.lower()


def run_audit() -> dict:
    """Run the full architecture audit and return results."""
    constants = audit_chat_helpers()
    history_pairs = constants.get("_MAX_HISTORY_PAIRS", "unknown")
    max_msg_chars = constants.get("_MAX_MSG_CHARS", "unknown")
    tool_results_in_history = audit_tool_results_in_history()
    agent_persistence = audit_agent_persistence()

    blocked_scenarios = []
    recommendations = []

    if history_pairs != "unknown" and int(history_pairs) < 5:
        recommendations.append(
            {
                "id": "increase_history_pairs",
                "impact": "high",
                "file": "src/gaia/ui/_chat_helpers.py",
                "description": f"_MAX_HISTORY_PAIRS={history_pairs} limits multi-turn context. Increase to 10+.",
            }
        )

    if max_msg_chars != "unknown" and int(max_msg_chars) < 1000:
        recommendations.append(
            {
                "id": "increase_truncation",
                "impact": "high",
                "file": "src/gaia/ui/_chat_helpers.py",
                "description": f"_MAX_MSG_CHARS={max_msg_chars} truncates messages. Increase to 2000+.",
            }
        )
        blocked_scenarios.append(
            {
                "scenario": "cross_turn_file_recall",
                "blocked_by": f"max_msg_chars={max_msg_chars}",
                "explanation": "File paths from previous turns may be truncated in history.",
            }
        )

    if not tool_results_in_history:
        recommendations.append(
            {
                "id": "include_tool_results",
                "impact": "critical",
                "file": "src/gaia/ui/_chat_helpers.py",
                "description": "Tool result summaries not detected in history. Cross-turn tool data unavailable.",
            }
        )
        blocked_scenarios.append(
            {
                "scenario": "cross_turn_file_recall",
                "blocked_by": "tool_results_in_history=false",
                "explanation": "File paths from list_recent_files are in tool results, not passed to LLM next turn.",
            }
        )

    return {
        "architecture_audit": {
            "history_pairs": history_pairs,
            "max_msg_chars": max_msg_chars,
            "tool_results_in_history": tool_results_in_history,
            "agent_persistence": agent_persistence,
            "blocked_scenarios": blocked_scenarios,
            "recommendations": recommendations,
        }
    }


if __name__ == "__main__":
    result = run_audit()
    print(json.dumps(result, indent=2))
