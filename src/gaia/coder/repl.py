# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``gaia-coder`` interactive REPL — Claude-Code-style coding session.

This is the primary surface for daily-driver use. ``gaia-coder`` with no
subcommand drops the user into a tool-using chat with the bound repo
loaded as context (``CLAUDE.md`` / ``AGENTS.md`` / ``GAIA.md`` /
``ARCHITECTURE.md`` / ``PROJECT_MAP.md`` injected into the system
prompt).

Goals:

1. **Fast iteration.** Multi-turn conversation, context retained across
   messages, code-aware tools (read/write/edit/search/shell/git) wired
   in by default.
2. **Safe by default.** Read-only tools auto-approve; mutating tools
   prompt; ``--yes`` auto-approves everything for trusted environments.
3. **Self-aware.** The agent has its own source under
   ``src/gaia/coder/`` and the project's plan / architecture docs in
   the prompt — it can read, search, and edit itself.
4. **Recoverable.** Sessions persist to
   ``$GAIA_CODER_HOME/sessions/<id>.jsonl`` and ``/load <id>`` resumes
   them.

Slash commands:

* ``/help``           — print this command list.
* ``/clear``          — drop the conversation history (keep the agent).
* ``/cost``           — print running token / USD totals.
* ``/tools``          — list every tool currently registered.
* ``/history``        — dump the raw conversation history (for debug).
* ``/yes``            — toggle auto-approve mode for the rest of the
                       session.
* ``/save [id]``      — persist the session under ``$GAIA_CODER_HOME/
                       sessions/<id>.jsonl`` (defaults to a timestamp).
* ``/load <id>``      — resume a saved session.
* ``/sessions``       — list saved sessions.
* ``/system``         — re-read the system-prompt sources from disk
                       (use after editing CLAUDE.md / GAIA.md / etc.).
* ``/cd <path>``      — change the agent's bound repo root.
* ``/model <name>``   — switch the LLM model for the next turn.
* ``/trust``          — print the current trust contract snapshot.
* ``/feedback ...``   — enqueue a feedback row (delegates to
                       :mod:`gaia.coder.stores.feedback`).
* ``/quit`` or ``/exit`` — leave the REPL.

The REPL falls back gracefully when ``rich`` or ``prompt_toolkit`` are
not installed (degraded but functional with stdlib ``input`` /
``print``).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from gaia.coder.agent import (
    READ_TOOLS,
    Agent,
    Message,
    PermissionPolicy,
    auto_approve_policy,
)
from gaia.coder.llm import AssistantTurn, CoderLLM
from gaia.coder.tool_schema import build_anthropic_tools

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.table import Table

    _HAS_RICH = True
except ImportError:  # pragma: no cover — rich is a soft dep here
    _HAS_RICH = False

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory

    _HAS_PROMPT_TOOLKIT = True
except ImportError:  # pragma: no cover
    _HAS_PROMPT_TOOLKIT = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# UI primitives — Rich when available, stdlib fallback otherwise
# ---------------------------------------------------------------------------


class UI:
    """Console UI shim. Uses ``rich`` when available, ``print`` otherwise.

    Wrapping the rendering surface here means the REPL body can stay
    rendering-agnostic — every print goes through one of ``info``,
    ``user``, ``assistant``, ``tool``, ``error``, ``cost``, ``rule``.
    """

    def __init__(self, *, color: Optional[bool] = None) -> None:
        self._has_rich = _HAS_RICH and (color is not False)
        self._console = Console() if self._has_rich else None

    def rule(self, label: str = "") -> None:
        if self._console is not None:
            self._console.rule(label)
        else:
            print(f"\n--- {label} ---" if label else "\n---")

    def info(self, text: str) -> None:
        if self._console is not None:
            self._console.print(f"[dim]{text}[/dim]")
        else:
            print(text)

    def user(self, text: str) -> None:
        if self._console is not None:
            self._console.print(f"[bold cyan]you[/bold cyan]: {text}")
        else:
            print(f"you: {text}")

    def assistant(self, text: str) -> None:
        if not text.strip():
            return
        if self._console is not None:
            try:
                rendered = Markdown(text)
                self._console.print(
                    Panel.fit(
                        rendered,
                        title="gaia-coder",
                        border_style="green",
                        padding=(0, 1),
                    )
                )
            except Exception:  # pragma: no cover — defensive on rich edge cases
                self._console.print(f"[bold green]gaia-coder[/bold green]: {text}")
        else:
            print(f"gaia-coder: {text}")

    def tool(self, name: str, payload: Mapping[str, Any], *, is_error: bool) -> None:
        rendered_input = ", ".join(f"{k}={v!r}"[:60] for k, v in payload.items())
        prefix = "✗" if is_error else "•"
        if self._console is not None:
            colour = "red" if is_error else "yellow"
            self._console.print(
                f"  [{colour}]{prefix}[/{colour}] [bold]{name}[/bold]([dim]{rendered_input}[/dim])"
            )
        else:
            print(f"  {prefix} {name}({rendered_input})")

    def tool_result(self, name: str, content: str, *, is_error: bool) -> None:
        # Show first ~12 lines so the user can see what the tool did
        # without flooding the terminal. Full content is preserved in
        # the history and available via /history.
        lines = content.splitlines()
        head = "\n".join(lines[:12])
        suffix = (
            f"\n  …(+{len(lines) - 12} lines, /history for full)"
            if len(lines) > 12
            else ""
        )
        if self._console is not None:
            style = "red" if is_error else "dim"
            self._console.print(f"  [{style}]{head}{suffix}[/{style}]")
        else:
            print(f"  {head}{suffix}")

    def error(self, text: str) -> None:
        if self._console is not None:
            self._console.print(f"[bold red]error[/bold red]: {text}")
        else:
            print(f"error: {text}", file=sys.stderr)

    def cost(self, tokens_in: int, tokens_out: int, usd: float, turns: int) -> None:
        msg = (
            f"session: {turns} turns, "
            f"{tokens_in:,} in + {tokens_out:,} out tokens, "
            f"${usd:.4f}"
        )
        if self._console is not None:
            self._console.print(f"[dim]{msg}[/dim]")
        else:
            print(msg)

    def banner(self, *, model: str, repo_root: Path, tools_count: int) -> None:
        text = (
            "[bold]gaia-coder[/bold] — interactive coding session\n"
            f"model: [bold]{model}[/bold]   "
            f"repo: [bold]{repo_root}[/bold]   "
            f"tools: [bold]{tools_count}[/bold]\n"
            "[dim]/help for commands, Ctrl-D or /quit to exit[/dim]"
        )
        if self._console is not None:
            self._console.print(Panel.fit(text, border_style="cyan"))
        else:
            print("=" * 60)
            print("gaia-coder — interactive coding session")
            print(f"model: {model}    repo: {repo_root}    tools: {tools_count}")
            print("/help for commands, Ctrl-D or /quit to exit")
            print("=" * 60)

    def table(
        self, title: str, columns: Sequence[str], rows: Sequence[Sequence[str]]
    ) -> None:
        if self._console is not None:
            t = Table(title=title)
            for col in columns:
                t.add_column(col)
            for row in rows:
                t.add_row(*[str(c) for c in row])
            self._console.print(t)
        else:
            print(title)
            print(" | ".join(columns))
            print("-" * 60)
            for row in rows:
                print(" | ".join(str(c) for c in row))


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------


def _sessions_dir() -> Path:
    """Directory where chat sessions are persisted.

    Honours ``GAIA_CODER_HOME`` so tests can isolate; otherwise
    ``~/.gaia/coder/sessions/``.
    """
    base = os.environ.get("GAIA_CODER_HOME")
    root = Path(base) if base else Path.home() / ".gaia" / "coder"
    out = root / "sessions"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _serialise_history(history: Sequence[Message]) -> List[Dict[str, Any]]:
    """Convert :class:`Message` objects to JSON-safe dicts."""
    return [{"role": m.role, "content": m.content} for m in history]


def _deserialise_history(data: Sequence[Mapping[str, Any]]) -> List[Message]:
    return [Message(role=str(m["role"]), content=m["content"]) for m in data]


def save_session(
    *,
    session_id: str,
    history: Sequence[Message],
    model: str,
    repo_root: Path,
    usage: Mapping[str, Any],
) -> Path:
    """Persist a session to ``$GAIA_CODER_HOME/sessions/<id>.json``.

    JSON (not JSONL) for simplicity — sessions are typically <1MB.
    The schema version field lets us evolve the format later.
    """
    path = _sessions_dir() / f"{session_id}.json"
    payload = {
        "schema_version": 1,
        "session_id": session_id,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "repo_root": str(repo_root),
        "usage": dict(usage),
        "history": _serialise_history(history),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_session(session_id: str) -> Dict[str, Any]:
    """Load a previously-saved session.

    Raises:
        FileNotFoundError: if the session does not exist.
        ValueError: if the schema version is unknown.
    """
    path = _sessions_dir() / f"{session_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"session {session_id!r} not found at {path}. "
            "Use /sessions to list available sessions."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    schema = payload.get("schema_version")
    if schema != 1:
        raise ValueError(
            f"session {session_id!r}: unknown schema_version={schema!r}; "
            "this build supports schema_version=1"
        )
    return payload


def list_sessions() -> List[Dict[str, Any]]:
    """Return one row per saved session (id, mtime, model, message-count)."""
    out: List[Dict[str, Any]] = []
    for path in sorted(_sessions_dir().glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("list_sessions: skipping %s (%s)", path, e)
            continue
        out.append(
            {
                "id": payload.get("session_id", path.stem),
                "saved_at": payload.get("saved_at", "?"),
                "model": payload.get("model", "?"),
                "messages": len(payload.get("history", [])),
                "path": str(path),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Permission prompting
# ---------------------------------------------------------------------------


def _format_tool_call_for_prompt(name: str, tool_input: Mapping[str, Any]) -> str:
    """Render ``{name}({…})`` for a permission-prompt confirmation line."""
    keyed = ", ".join(f"{k}={v!r}" for k, v in tool_input.items())
    if len(keyed) > 200:
        keyed = keyed[:200] + "…"
    return f"{name}({keyed})"


class InteractivePolicy:
    """Permission policy that prompts the user for write tools.

    Wraps :func:`gaia.coder.agent.safe_default_policy` with an
    interactive resolver: when the underlying policy returns
    ``"prompt"``, this asks the operator for ``y/n/yes-to-all/quit``.

    The ``yes-to-all`` answer flips an internal flag that auto-approves
    every subsequent write tool for the lifetime of this policy
    instance — same UX as Claude Code's ``--yes`` mode toggled
    mid-session.
    """

    def __init__(self, *, ui: UI, auto_yes: bool = False) -> None:
        self._ui = ui
        self.auto_yes = auto_yes

    def __call__(
        self,
        name: str,
        tool_input: Mapping[str, Any],
    ) -> Optional[str]:
        # Read tools always pass.
        if name in READ_TOOLS:
            return None
        if self.auto_yes:
            return None
        # Anything else: prompt.
        rendered = _format_tool_call_for_prompt(name, tool_input)
        if self._ui._console is not None:  # noqa: SLF001 — small ui leak
            self._ui._console.print(  # noqa: SLF001
                f"[bold yellow]?[/bold yellow] tool [bold]{rendered}[/bold]"
            )
            self._ui._console.print(  # noqa: SLF001
                "[dim]   y=yes, n=no, a=yes-to-all, q=cancel turn[/dim]"
            )
        else:
            print(f"? tool {rendered}")
            print("   y=yes, n=no, a=yes-to-all, q=cancel turn")
        try:
            answer = input("> ").strip().lower() or "n"
        except (EOFError, KeyboardInterrupt):
            return "deny: cancelled by user (^C)"
        if answer in ("y", "yes"):
            return None
        if answer in ("a", "all", "yes-to-all"):
            self.auto_yes = True
            return None
        if answer in ("q", "quit", "cancel"):
            return "deny: user cancelled the turn"
        return f"deny: user declined ({answer!r})"


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------


SlashHandler = Callable[["Repl", List[str]], None]


def _slash_help(repl: "Repl", _args: List[str]) -> None:
    rows: List[Tuple[str, str]] = [
        ("/help", "show this list"),
        ("/clear", "drop conversation history"),
        ("/cost", "show running token / USD totals"),
        ("/tools", "list registered tools"),
        ("/history", "dump raw conversation history"),
        ("/yes", "toggle auto-approve mode"),
        ("/save [id]", "save session to ~/.gaia/coder/sessions/<id>.json"),
        ("/load <id>", "resume saved session"),
        ("/sessions", "list saved sessions"),
        ("/system", "re-read system prompt sources"),
        ("/cd <path>", "change repo root"),
        ("/model <name>", "switch LLM model"),
        ("/trust", "print trust contract snapshot"),
        ("/feedback <body>", "enqueue feedback row (use /feedback --severity X body)"),
        ("/quit  or  /exit", "leave the REPL"),
    ]
    repl.ui.table("slash commands", ("command", "description"), rows)


def _slash_clear(repl: "Repl", _args: List[str]) -> None:
    repl.agent.reset()
    repl.ui.info("history cleared")


def _slash_cost(repl: "Repl", _args: List[str]) -> None:
    u = repl.agent.session_usage
    repl.ui.cost(u.input_tokens, u.output_tokens, u.total_cost_usd, u.turns)


def _slash_tools(repl: "Repl", _args: List[str]) -> None:
    tools = build_anthropic_tools()
    rows = [(t["name"], t["description"][:80]) for t in tools]
    repl.ui.table(f"{len(rows)} tools", ("name", "description"), rows)


def _slash_history(repl: "Repl", _args: List[str]) -> None:
    for i, msg in enumerate(repl.agent.history):
        content = msg.content
        rendered = (
            json.dumps(content, indent=2, default=str)
            if not isinstance(content, str)
            else content
        )
        repl.ui.info(f"--- [{i}] {msg.role} ---")
        repl.ui.info(rendered[:1200] + ("…" if len(rendered) > 1200 else ""))


def _slash_yes(repl: "Repl", _args: List[str]) -> None:
    if isinstance(repl.policy, InteractivePolicy):
        repl.policy.auto_yes = not repl.policy.auto_yes
        state = "ON" if repl.policy.auto_yes else "OFF"
        repl.ui.info(f"auto-approve mode: {state}")
    else:
        repl.ui.error("auto-approve toggle requires InteractivePolicy")


def _slash_save(repl: "Repl", args: List[str]) -> None:
    sid = args[0] if args else f"{int(time.time())}-{uuid.uuid4().hex[:6]}"
    u = repl.agent.session_usage
    path = save_session(
        session_id=sid,
        history=repl.agent.history,
        model=repl.agent.llm.model,
        repo_root=repl.agent.repo_root,
        usage=asdict(u),
    )
    repl.ui.info(f"saved → {path}")


def _slash_load(repl: "Repl", args: List[str]) -> None:
    if not args:
        repl.ui.error("usage: /load <session-id>  (use /sessions to list)")
        return
    payload = load_session(args[0])
    repl.agent._history.clear()  # noqa: SLF001 — intentional (REPL is the agent's twin)
    repl.agent._history.extend(_deserialise_history(payload["history"]))
    repl.ui.info(
        f"resumed session {args[0]!r} ({len(repl.agent.history)} messages, "
        f"saved {payload.get('saved_at', '?')})"
    )


def _slash_sessions(repl: "Repl", _args: List[str]) -> None:
    rows = [
        (s["id"], s["saved_at"], s["model"], str(s["messages"]))
        for s in list_sessions()
    ]
    repl.ui.table(
        f"{len(rows)} saved sessions",
        ("id", "saved_at", "model", "msgs"),
        rows or [("(none)", "", "", "")],
    )


def _slash_system(repl: "Repl", _args: List[str]) -> None:
    # Forces re-read on next send by no-oping here — system_prompt() is
    # called fresh on every send. We just acknowledge the request.
    repl.ui.info("system prompt will be re-read from disk on the next send")


def _slash_cd(repl: "Repl", args: List[str]) -> None:
    if not args:
        repl.ui.error("usage: /cd <path>")
        return
    new_root = Path(args[0]).expanduser().resolve()
    if not new_root.exists() or not new_root.is_dir():
        repl.ui.error(f"path does not exist or is not a directory: {new_root}")
        return
    repl.agent._repo_root = new_root  # noqa: SLF001
    repl.ui.info(f"repo_root → {new_root}")


def _slash_model(repl: "Repl", args: List[str]) -> None:
    if not args:
        repl.ui.error("usage: /model <model-name>")
        return
    new_model = args[0]
    repl.agent._llm = CoderLLM(model=new_model)  # noqa: SLF001
    repl.ui.info(f"model → {new_model}")


def _slash_trust(repl: "Repl", _args: List[str]) -> None:
    # Read em.toml, render the §4.2 snapshot the existing trust CLI prints.
    try:
        from gaia.coder.cli import _em_toml_path, _render_tier_summary
        from gaia.coder.trust import EMConfig, load_em_config

        em_path = _em_toml_path()
        if not em_path.exists():
            repl.ui.info("no EM bound. Run: gaia-coder trust --bootstrap …")
            return
        cfg = load_em_config(em_path)
        rendered = _render_tier_summary(cfg)
        repl.ui.info(rendered)
    except Exception as e:  # noqa: BLE001 — surface the error to the user
        repl.ui.error(f"/trust failed: {e}")


def _slash_feedback(repl: "Repl", args: List[str]) -> None:
    """Enqueue a feedback row from inside the REPL.

    Accepts ``/feedback --severity high body of the feedback`` or just
    ``/feedback body of the feedback`` (severity defaults to ``info``).
    """
    if not args:
        repl.ui.error("usage: /feedback [--severity low|med|high|critical] <body>")
        return
    severity = "info"
    if args[0] == "--severity":
        if len(args) < 3:
            repl.ui.error("usage: /feedback --severity <level> <body>")
            return
        severity = args[1]
        body = " ".join(args[2:])
    else:
        body = " ".join(args)

    try:
        from gaia.coder.cli import _feedback_db_path
        from gaia.coder.stores.feedback import enqueue_feedback

        em_handle = "operator"
        try:
            from gaia.coder.cli import _em_toml_path
            from gaia.coder.trust import load_em_config

            em_path = _em_toml_path()
            if em_path.exists():
                em_handle = load_em_config(em_path).em_handle
        except Exception:  # noqa: BLE001
            pass

        feedback_id = f"repl-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        enqueue_feedback(
            db_path=_feedback_db_path(),
            feedback_id=feedback_id,
            body=body,
            severity=severity,
            from_handle=em_handle,
        )
        repl.ui.info(f"enqueued feedback {feedback_id} (severity={severity})")
    except Exception as e:  # noqa: BLE001
        repl.ui.error(f"/feedback failed: {e}")


_SLASH_COMMANDS: Dict[str, SlashHandler] = {
    "/help": _slash_help,
    "/?": _slash_help,
    "/clear": _slash_clear,
    "/cost": _slash_cost,
    "/tools": _slash_tools,
    "/history": _slash_history,
    "/yes": _slash_yes,
    "/save": _slash_save,
    "/load": _slash_load,
    "/sessions": _slash_sessions,
    "/system": _slash_system,
    "/cd": _slash_cd,
    "/model": _slash_model,
    "/trust": _slash_trust,
    "/feedback": _slash_feedback,
}


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------


class Repl:
    """The interactive REPL itself.

    Owns the :class:`Agent`, the :class:`UI`, and the input loop.
    Dispatches slash commands; everything else is sent to the agent.
    """

    def __init__(
        self,
        *,
        agent: Optional[Agent] = None,
        ui: Optional[UI] = None,
        auto_yes: bool = False,
        history_file: Optional[Path] = None,
    ) -> None:
        self.ui = ui or UI()
        self.policy: PermissionPolicy
        if auto_yes:
            self.policy = auto_approve_policy
        else:
            self.policy = InteractivePolicy(ui=self.ui, auto_yes=False)
        self.agent = agent or Agent(permission_policy=self.policy)
        # Make sure the agent uses our policy if the caller passed one in.
        # (The Agent constructor copied whatever it was given; we override
        # so the InteractivePolicy receives the real UI.)
        self.agent._policy = self.policy  # noqa: SLF001
        self._history_file = (
            history_file
            if history_file is not None
            else (
                Path(
                    os.environ.get("GAIA_CODER_HOME")
                    or (Path.home() / ".gaia" / "coder")
                )
                / "repl_history.txt"
            )
        )
        self._history_file.parent.mkdir(parents=True, exist_ok=True)
        if _HAS_PROMPT_TOOLKIT:
            self._prompt_session = PromptSession(
                history=FileHistory(str(self._history_file))
            )
        else:
            self._prompt_session = None

    def banner(self) -> None:
        tools_count = len(build_anthropic_tools())
        self.ui.banner(
            model=self.agent.llm.model,
            repo_root=self.agent.repo_root,
            tools_count=tools_count,
        )

    def _read_user_input(self) -> Optional[str]:
        """Read one user message. Returns ``None`` on EOF / Ctrl-D."""
        try:
            if self._prompt_session is not None:
                # prompt_toolkit handles multi-line via Esc-Enter; for v1
                # we keep it single-line. Multi-line paste still works
                # because prompt_toolkit supports it natively.
                return self._prompt_session.prompt("» ")
            return input("» ")
        except EOFError:
            return None
        except KeyboardInterrupt:
            self.ui.info("(use /quit to exit)")
            return ""

    def _on_assistant_turn(self, turn: AssistantTurn) -> None:
        # Only render text for turns that actually emitted text. A
        # tool_use-only turn has no user-visible output yet.
        if turn.text.strip():
            self.ui.assistant(turn.text)
        if turn.usage.total_tokens:
            self.ui.cost(
                turn.usage.input_tokens,
                turn.usage.output_tokens,
                turn.usage.total_cost_usd,
                self.agent.session_usage.turns,
            )

    def _on_tool_call(self, record: Dict[str, Any]) -> None:
        self.ui.tool(record["name"], record["input"], is_error=record["is_error"])
        self.ui.tool_result(
            record["name"], record["content"], is_error=record["is_error"]
        )

    def _handle_slash(self, line: str) -> bool:
        """Return True if ``line`` was a slash command (handled or unknown)."""
        if not line.startswith("/"):
            return False
        parts = line.split()
        cmd = parts[0]
        args = parts[1:]
        if cmd in ("/quit", "/exit"):
            raise SystemExit(0)
        handler = _SLASH_COMMANDS.get(cmd)
        if handler is None:
            self.ui.error(f"unknown command {cmd!r}; /help for the list")
            return True
        try:
            handler(self, args)
        except Exception as e:  # noqa: BLE001 — surface to user, don't crash
            self.ui.error(f"{cmd} failed: {e}")
            logger.exception("slash command %s raised", cmd)
        return True

    def run(self) -> int:
        """Run the REPL. Returns the desired exit code (0 on clean exit)."""
        self.banner()
        while True:
            try:
                line = self._read_user_input()
            except SystemExit as e:
                return int(e.code or 0)
            if line is None:
                self.ui.info("(EOF — bye)")
                return 0
            line = line.strip()
            if not line:
                continue
            if self._handle_slash(line):
                continue
            self.ui.user(line)
            try:
                self.agent.send(
                    line,
                    on_assistant_turn=self._on_assistant_turn,
                    on_tool_call=self._on_tool_call,
                )
            except KeyboardInterrupt:
                self.ui.info("(turn cancelled by ^C)")
            except Exception as e:  # noqa: BLE001 — surface to user
                self.ui.error(f"{type(e).__name__}: {e}")
                logger.exception("agent.send raised")


# ---------------------------------------------------------------------------
# Module entry — used by `gaia-coder` (no subcommand)
# ---------------------------------------------------------------------------


def run_repl(
    *,
    auto_yes: bool = False,
    model: Optional[str] = None,
    repo_root: Optional[Path] = None,
    include_github: bool = True,
    resume: Optional[str] = None,
) -> int:
    """Construct and run an interactive REPL.

    Args:
        auto_yes: If True, every tool call auto-approves (for trusted /
            CI environments). Default False — the REPL prompts for
            mutating tools.
        model: Override the default model.
        repo_root: Override the bound repo root (defaults to CWD).
        include_github: If False, omit the ``gh`` CLI tools.
        resume: If supplied, load this session id before showing the
            banner.

    Returns:
        The exit code the user-facing CLI should return.
    """
    llm = CoderLLM(model=model) if model else None
    agent = Agent(
        llm=llm,
        repo_root=repo_root,
        include_github=include_github,
    )
    repl = Repl(agent=agent, auto_yes=auto_yes)
    if resume:
        try:
            payload = load_session(resume)
            repl.agent._history.clear()  # noqa: SLF001
            repl.agent._history.extend(  # noqa: SLF001
                _deserialise_history(payload["history"])
            )
            repl.ui.info(
                f"resumed session {resume!r} " f"({len(repl.agent.history)} messages)"
            )
        except (FileNotFoundError, ValueError) as e:
            repl.ui.error(f"--resume failed: {e}")
            return 2
    return repl.run()


__all__ = [
    "InteractivePolicy",
    "Repl",
    "UI",
    "list_sessions",
    "load_session",
    "run_repl",
    "save_session",
]
