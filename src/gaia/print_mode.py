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
import threading
import time
from fnmatch import fnmatchcase
from typing import Any, Dict, List, Optional, Sequence

from gaia.logger import get_logger

log = get_logger(__name__)

OUTPUT_FORMATS = ("text", "json")

# How long a turn told to stop at its time limit gets to wind down before it is
# abandoned: a model call already sent does not hear the stop.
HARD_STOP_GRACE_S = 5.0

#: Tools ``--no-web`` withholds: GAIA's own tools that search, read or download
#: from the internet, or install from it. The shell and MCP tools stay on.
WEB_TOOL_PATTERNS = (
    "search_web",
    "fetch_page",
    "fetch_webpage",
    "download_file",
    "open_url",
    "browser_*",
    "search_documentation",
    "search_skill_hub",
    "install_skill",
    "install_cli",
    "transcribe_media",
)

#: Added to the request under ``--no-web``: the shell can still reach the
#: internet, so the model is told the rule the withheld tools only imply.
NO_WEB_NOTE = (
    "\n\n[Web access is off for this run. Do not search the web or fetch "
    "pages, repositories or answers through the shell or a script either. "
    "Installing a package the task needs is fine.]"
)

#: Tools ``--no-skills`` withholds, so no skill can be found, loaded or learned.
SKILL_TOOL_PATTERNS = (
    "list_skills",
    "search_skill_hub",
    "install_skill",
    "capture_skill",
    "remove_skill",
    "load_skill",
    "unload_skill",
    "skill_status",
    "remember_skill_lesson",
)

# What `-p` holds when given no text. Not "": an empty $PROMPT in a script must
# be refused, not wait on a stdin that may never close.
READ_STDIN = "\0stdin"


def read_prompt(prompt: Optional[str]) -> str:
    """The prompt from ``-p``, or from stdin when ``-p`` was given no text."""
    if prompt != READ_STDIN and prompt is not None:
        if not prompt.strip():
            raise ValueError("gaia -p was given an empty prompt.")
        return prompt
    if sys.stdin is None or sys.stdin.isatty():
        raise ValueError(
            'gaia -p needs a prompt: pass it as `gaia -p "<prompt>"` or pipe it '
            'on stdin (`echo "<prompt>" | gaia -p`).'
        )
    stdin_buffer = getattr(sys.stdin, "buffer", None)
    if stdin_buffer is None:
        # Lightweight StringIO-style streams used by callers/tests do not
        # expose the raw byte stream. Real piped stdin does, so production
        # input still follows the explicit UTF-8 path below.
        text = sys.stdin.read()
    else:
        try:
            text = stdin_buffer.read().decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(
                "gaia -p expects piped stdin to be a UTF-8 text file. Save the "
                "prompt as UTF-8 (in Windows PowerShell 5.1: "
                "`Out-File -Encoding utf8`), or pass it as an argument: "
                'gaia -p "<prompt>".'
            ) from exc
    text = text.strip()
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
    if result.get("time_limit_reached"):
        record["time_limit_reached"] = True
    if record["is_error"]:
        record["error"] = _turn_error(result)
    if verbose:
        record["conversation"] = conversation
    return record


class PrintModeError(RuntimeError):
    """A refusal worded for the user, printed as it is."""

    #: The exit code a run that ends on this error returns.
    code = 1


class DownloadStopped(PrintModeError):
    """Ctrl+C during a model download, or at the question before it."""

    code = 130


def configure_console(console: Any, skip_permissions: bool, full_access: bool) -> None:
    """Set a headless console's permissions from the -p flags."""
    console.silence_final_answer = True
    # Headless: without this, a confirmation-gated tool is denied, as in `claude -p`.
    console.auto_approve_gated_tools = skip_permissions or full_access
    # Asked for by name on the command line, as the TUI's /full-access is.
    console.full_access = full_access


def _emit_failure(output_format: str, message: str, code: int = 1) -> int:
    """Report a run that never produced a turn, in the shape stdout promises."""
    message = _cli(message)
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
    attempts: int = 1,
    time_limit: Optional[int] = None,
    disallowed_tools: Sequence[str] = (),
    no_web: bool = False,
    no_skills: bool = False,
    yes: bool = False,
) -> int:
    """Run one headless turn and print its result. Returns the exit code.

    With *attempts* > 1, that many independent attempts run first, each in a
    copy of the current folder, and the turn combines their drafts.
    """
    if output_format not in OUTPUT_FORMATS:
        raise ValueError(
            f"Unknown --output-format {output_format!r}; choose one of: "
            f"{', '.join(OUTPUT_FORMATS)}."
        )
    if time_limit and attempts > 1:
        return _emit_failure(
            output_format,
            "--time-limit can't be combined with --attempts yet. Drop one of them.",
            code=2,
        )
    route = settings = None
    try:
        text = read_prompt(prompt)
        model, route, settings = _route(text, model, base_url, yes)
        if route is not None:
            _model_on_disk(
                model, base_url, setting=_routed_setting(settings, model), yes=yes
            )
        turn = dict(
            model=model,
            verbose=verbose,
            max_steps=max_steps,
            skip_permissions=skip_permissions,
            full_access=full_access,
            base_url=base_url,
            disallowed_tools=tuple(disallowed_tools),
            no_web=no_web,
            no_skills=no_skills,
            yes=yes,
        )
        record = (
            _run_with_attempts(text, attempts, **turn)
            if attempts > 1
            else _run_turn(text, time_limit=time_limit, **turn)
        )
        if route is not None:
            record["route"] = {
                k: _cli(v) if k in ("fix", "notice") else v
                for k, v in route.as_record().items()
            }
            if record["is_error"] and record.get("error"):
                record["error"] = _cli(
                    _explain_routed(settings, route, record["error"]) or record["error"]
                )
    except KeyboardInterrupt:
        return _emit_failure(output_format, "interrupted", code=130)
    except PrintModeError as exc:
        return _emit_failure(output_format, str(exc), code=exc.code)
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report, never a traceback
        # DEBUG, not INFO: the console handler prints INFO, traceback and all.
        log.debug("gaia -p failed", exc_info=True)
        return _emit_failure(
            output_format,
            _explain_routed(settings, route, exc) or f"{type(exc).__name__}: {exc}",
        )

    if output_format == "json":
        print(json.dumps(record, ensure_ascii=False))
    elif record["result"] or not record["is_error"]:
        # A failed turn with no answer leaves stdout empty, not one blank line.
        print(record["result"])
    if record["is_error"]:
        ended = f"the turn ended with status '{record['status']}'"
        error = record.get("error")
        detail = f": {error}" if error and error != ended else ""
        print(f"❌ gaia -p: {ended}{detail}", file=sys.stderr)
        return 1
    return 0


_SUMMED = (
    "num_steps",
    "num_tool_calls",
    "input_tokens",
    "cached_tokens",
    "output_tokens",
)


def _run_with_attempts(text: str, attempts: int, **turn: Any) -> Dict[str, Any]:
    """Independent attempts in copies of the folder, then the combining turn here."""
    from gaia.attempts import run_attempts, try_remove

    model = _model_on_disk(turn["model"], turn["base_url"], yes=turn["yes"])
    # Start the server once here, so parallel attempts don't race to start it.
    _start_server(model, turn["base_url"])
    flags: List[str] = []
    if model:
        flags += ["--model", model]
    if turn["max_steps"]:
        flags += ["--max-steps", str(turn["max_steps"])]
    if turn["full_access"]:
        flags.append("--full-access")
    elif turn["skip_permissions"]:
        flags.append("--dangerously-skip-permissions")
    if turn["base_url"]:
        flags += ["--base-url", turn["base_url"]]
    if turn["disallowed_tools"]:
        flags += ["--disallowed-tools", ",".join(turn["disallowed_tools"])]
    if turn["no_web"]:
        flags.append("--no-web")
    if turn["no_skills"]:
        flags.append("--no-skills")
    print(f"gaia -p: running {attempts} independent attempts…", file=sys.stderr)
    final_prompt, records, scratch = run_attempts(text, attempts, flags)
    try:
        record = _run_turn(final_prompt, extra_paths=[str(scratch)], **turn)
    finally:
        try_remove(scratch)
    record["attempts"] = [
        {k: r.get(k) for k in ("is_error", "status", "error", "duration_s", *_SUMMED)}
        for r in records
    ]
    for key in _SUMMED:
        record[key] += sum(int(r.get(key) or 0) for r in records)
    return record


def _route(text: str, model: Optional[str], base_url: Optional[str], yes: bool = False):
    """``(model to run, route, settings)``: the hybrid router's pick, else
    *model* and two Nones.

    Decided once, before any attempt starts, so every attempt and the final
    turn run on the same model.
    """
    from gaia.llm.hybrid import HybridRouter, is_hybrid_model

    if not is_hybrid_model(_resolve_model(model)):
        return model, None, None
    router = HybridRouter()
    classifier = _model_on_disk(
        router.settings.classifier_model,
        base_url,
        setting="hybrid_classifier_model",
        yes=yes,
    )
    detected = _start_server(classifier, base_url)
    route = router.decide(text, base_url=base_url or detected)
    label = (
        f"{route.kind}, difficulty {route.difficulty}, {route.stakes} stakes"
        if route.classified
        else route.reason
    )
    print(f"gaia -p: hybrid runs this turn on {route.model} ({label})", file=sys.stderr)
    if route.notice:
        print(f"⚠️  gaia -p: {_cli(route.notice)}", file=sys.stderr)
    elif route.fix:
        print(
            f"⚠️  gaia -p: hybrid's classifier failed. To fix: {_cli(route.fix)}",
            file=sys.stderr,
        )
    return route.model, route, router.settings


def _routed_setting(settings: Any, model: str) -> str:
    """The hybrid config key that chose *model*, the turn's routed model."""
    if model == settings.strong_model:
        return "hybrid_strong_model"
    return "hybrid_cheap_model"


def _cli(text: str) -> str:
    """*text* with the apps' advice swapped for the CLI's commands."""
    from gaia.llm.surface_hints import surface_wording

    return surface_wording(text, "cli")


def _explain_routed(settings: Any, route: Any, error: Any) -> Optional[str]:
    """*error* restated with the hybrid setting that chose the turn's model."""
    if route is None:
        return None
    from gaia.llm.hybrid import explain_route_failure

    return explain_route_failure(settings, route.model, error)


def _resolve_model(model: Optional[str]) -> Optional[str]:
    """*model*, else the app's pick, else ``default_model``: ``hybrid`` kept."""
    if model is None:
        from gaia.config import GaiaConfig

        config = GaiaConfig.load()
        model = config.last_model or config.default_model or None
    return model


def _model_on_disk(
    model: Optional[str],
    base_url: Optional[str],
    setting: Optional[str] = None,
    yes: bool = False,
) -> str:
    """The model this run uses, downloaded first when it is not on disk.

    Unset means the model the Agent UI and TUI reopen on. A download is
    confirmed at the terminal first, unless *yes* (``--yes``) agreed to it in
    advance. *setting* names the config key that chose *model*, for the remedy
    when the id is unknown.
    """
    from gaia.llm.lemonade_client import (
        LemonadeClient,
        cloud_model_provider,
        find_model_requirement,
        resolve_chat_model,
    )

    requested = model
    model = model or resolve_chat_model()
    if cloud_model_provider(model):
        return model
    detected_base_url = _start_server(model, base_url, preload=False)
    client = LemonadeClient(base_url=base_url or detected_base_url, verbose=False)
    # A gateway model the catalog leaves out (no or expired token) has nothing to download.
    if client._is_cloud_model(model):  # pylint: disable=protected-access
        return model
    info = client.get_model_info(model)
    # GAIA's own models (a user.* pick, an NPU model) are listed only once pulled.
    if not info["listed"] and find_model_requirement(model) is None:
        raise PrintModeError(
            f"{model} isn't a model the model server knows, so gaia -p can't run "
            f"it. {_unknown_model_remedy(model, requested, setting)}"
        )
    if not info["downloaded"]:
        _confirm_download(model, info["size_gb"], yes)
        _download_model(client, model, info["size_gb"])
    return model


class _DownloadProgress:
    """Draws a model pull's streamed events on stderr; stdout stays the answer's.

    A terminal gets one bar redrawn in place, anything else a plain line every
    ``LOG_STEP`` percent. Lemonade reports bytes per file, so a model's bytes
    are the files already finished plus the one in flight.
    """

    REDRAW_S = 0.2
    LOG_STEP = 10
    BAR_WIDTH = 24

    def __init__(self, model: str, planned_bytes: int = 0, stream=None, clock=None):
        self.model = model
        self.stream = stream if stream is not None else sys.stderr
        self.live = bool(getattr(self.stream, "isatty", lambda: False)())
        self.bytes_done = 0
        self.bytes_total = planned_bytes
        self._planned = planned_bytes
        self._clock = clock or time.monotonic
        self._finished = 0
        self._file: Optional[str] = None
        self._file_total = 0
        self._drawn_at: Optional[float] = None
        self._next_log = self.LOG_STEP
        self._line_open = False

    def __call__(self, event: Dict[str, Any]) -> None:
        kind = event.get("event")
        if kind == "complete":
            if self._line_open:
                self.bytes_done = self.bytes_total
                self._draw(100)
            self.close()
        if kind != "progress":
            return
        self._tally(event)
        if not self.bytes_total:
            return
        percent = min(100, int(self.bytes_done * 100 / self.bytes_total))
        if not self.live:
            if percent >= self._next_log:
                self._next_log = (percent // self.LOG_STEP + 1) * self.LOG_STEP
                self._write(
                    f"gaia -p: downloading {self.model}: {percent}% "
                    f"({self._sizes()})\n"
                )
            return
        now = self._clock()
        if self._drawn_at is not None and now - self._drawn_at < self.REDRAW_S:
            return
        self._drawn_at = now
        self._draw(percent)

    def _draw(self, percent: int) -> None:
        filled = self.BAR_WIDTH * percent // 100
        bar = "#" * filled + "-" * (self.BAR_WIDTH - filled)
        self._write(f"\r  [{bar}] {percent:3d}%  {self._sizes()}".ljust(60))
        self._line_open = True

    def close(self) -> None:
        """End the bar's line, so what prints next starts on its own."""
        if self._line_open:
            self._line_open = False
            self._write("\n")

    def _tally(self, event: Dict[str, Any]) -> None:
        name = event.get("file") or ""
        done = int(event.get("bytes_downloaded") or 0)
        if self._file is not None and name != self._file:
            self._finished += self._file_total
            self._file_total = 0
        self._file = name
        # Mid-file events can carry bytes_total 0, so keep the largest seen.
        self._file_total = max(
            self._file_total, int(event.get("bytes_total") or 0), done
        )
        self.bytes_done = self._finished + done
        total = int(event.get("total_download_size") or 0)
        self.bytes_total = max(
            total or self._planned or self._finished + self._file_total,
            self.bytes_done,
        )

    def _sizes(self) -> str:
        unit, name = (1e9, "GB") if self.bytes_total >= 1e9 else (1e6, "MB")
        return f"{self.bytes_done / unit:.1f} / {self.bytes_total / unit:.1f} {name}"

    def _write(self, text: str) -> None:
        self.stream.write(text)
        self.stream.flush()


def _confirm_download(model: str, size_gb: Optional[float], yes: bool) -> None:
    """Ask at the terminal before *model* is downloaded; refuse without a yes.

    The question and its answer use stderr and stdin, so stdout stays the
    answer's. With no terminal to ask at (stdin carries the prompt or is
    redirected, or stderr is), nothing is downloaded: ``--yes`` says yes in
    advance.
    """
    if yes:
        return
    size = f" ({size_gb:.1f} GB)" if size_gb else ""
    missing = f"{model} is not downloaded{size}"
    if not _stream_is_tty(sys.stdin) or not _stream_is_tty(sys.stderr):
        raise PrintModeError(
            f"{missing}, and there is no terminal to ask whether to download "
            "it, so nothing was downloaded. Pass --yes to download it, or "
            "--model with a model you have."
        )
    sys.stderr.write(f"gaia -p: {missing}. Download it now? [y/N] ")
    sys.stderr.flush()
    try:
        answer = sys.stdin.readline()
    except KeyboardInterrupt:
        sys.stderr.write("\n")
        raise DownloadStopped(
            f"Nothing was downloaded. Run the command again and answer y to "
            f"download {model}, or pass --yes."
        ) from None
    if not answer:
        sys.stderr.write("\n")  # EOF left the question's line open
    if answer.strip().lower() not in ("y", "yes"):
        raise PrintModeError(
            f"Nothing was downloaded. Run the command again and answer y to "
            f"download {model}, pass --yes to download without being asked, or "
            "pass --model with a model you have."
        )


def _stream_is_tty(stream: Any) -> bool:
    return stream is not None and bool(getattr(stream, "isatty", lambda: False)())


def _download_model(client: Any, model: str, size_gb: Optional[float]) -> None:
    """Download *model*, showing progress on stderr."""
    from gaia.llm.lemonade_client import (
        LemonadeClientError,
        ModelDownloadCancelledError,
    )

    size = f" ({size_gb:.1f} GB)" if size_gb else ""
    print(
        f"gaia -p: downloading {model}{size}. Ctrl+C stops the download.",
        file=sys.stderr,
    )
    progress = _DownloadProgress(model, planned_bytes=int((size_gb or 0) * 1e9))
    try:
        client.ensure_model_downloaded(model, show_progress=False, on_progress=progress)
    except (KeyboardInterrupt, ModelDownloadCancelledError) as exc:
        raise DownloadStopped(
            f"Download of {model} stopped. Run the same command again to "
            "continue it."
        ) from exc
    except LemonadeClientError as exc:
        raise PrintModeError(str(exc)) from exc
    finally:
        progress.close()
    print(f"gaia -p: downloaded {model}", file=sys.stderr)


def _unknown_model_remedy(
    model: str, requested: Optional[str], setting: Optional[str] = None
) -> str:
    """What makes the next run work, given where the unknown *model* came from.

    The order is ``--model``, then ``last_model``, then ``default_model``, so a
    remedy that changes a later one does nothing while an earlier one is set.
    """
    from gaia.config import GaiaConfig

    if setting:
        return (
            f"Run `gaia config set {setting} <id>` with a model you have, or "
            "correct the id."
        )
    if requested:
        return "Pass --model with a model you have, or correct the id."
    if GaiaConfig.load().last_model == model:
        return (
            "It is the model the GAIA app last used (`last_model`), which wins "
            "over `default_model`. Pass --model with a model you have, pick "
            "one you have in the GAIA app, run `gaia config set last_model "
            '<id>` (or `gaia config set last_model ""` to use '
            "`default_model`), or correct the id."
        )
    return (
        "Pass --model with a model you have, run `gaia config set "
        "default_model <id>`, or correct the id."
    )


def _start_server(
    model: Optional[str], base_url: Optional[str], preload: bool = True
) -> Optional[str]:
    """Start (or reach) the model server; return the base URL it reported."""
    from gaia.cli import initialize_lemonade_for_agent

    # quiet: stdout is the answer alone, so start-up progress must not reach it.
    success, detected_base_url = initialize_lemonade_for_agent(
        "chat", quiet=True, base_url=base_url, model=model, preload=preload
    )
    if not success:
        raise PrintModeError(
            "The model server could not be started. Run `gaia daemon status` for "
            "its state and `gaia init` if Lemonade is not installed."
        )
    return detected_base_url


def _run_turn(
    text: str,
    *,
    model: Optional[str],
    verbose: bool,
    max_steps: Optional[int],
    skip_permissions: bool,
    full_access: bool,
    base_url: Optional[str],
    extra_paths: Optional[List[str]] = None,
    time_limit: Optional[int] = None,
    disallowed_tools: Sequence[str] = (),
    no_web: bool = False,
    no_skills: bool = False,
    yes: bool = False,
) -> Dict[str, Any]:
    from gaia.llm.lemonade_client import resolve_lemonade_base_url
    from gaia.logger import configure

    log_manager = configure()
    log_manager.configure_agent_console(debug=False)

    model = _model_on_disk(model, base_url, yes=yes)
    try:
        from gaia_agent.agent import GaiaAgent, GaiaAgentConfig
    except ImportError:
        # The agent ships as its own binary here, as the apps do: run the turn
        # on it through the daemon, which brings up the model server for it.
        return _relay_turn(
            text,
            model=model,
            verbose=verbose,
            max_steps=max_steps,
            skip_permissions=skip_permissions,
            full_access=full_access,
            base_url=base_url,
            extra_paths=extra_paths,
            time_limit=time_limit,
            restricted=(
                "--disallowed-tools"
                if disallowed_tools
                else "--no-web" if no_web else "--no-skills" if no_skills else ""
            ),
        )
    detected_base_url = _start_server(model, base_url)

    agent = GaiaAgent(
        GaiaAgentConfig(
            base_url=base_url or detected_base_url or resolve_lemonade_base_url(),
            model_id=model,
            max_steps=max_steps,
            silent_mode=True,
            # A long reply sent in one piece can fail at the gateway; a stream does not.
            streaming=True,
            allowed_paths=[os.getcwd(), *(extra_paths or [])],
            **(
                {"skill_discovery": False, "skill_suggestions": False}
                if no_skills
                else {}
            ),
        )
    )
    withheld = restrict_tools(
        agent, disallowed_tools, no_web=no_web, no_skills=no_skills
    )
    configure_console(agent.console, skip_permissions, full_access)
    agent.no_one_to_ask = True  # one shot: a question would go unanswered
    if full_access:
        log.warning("Full access ENABLED at launch (gaia -p): shell gates off")
        print(
            "⚠️  gaia -p: full access is on — shell guardrails off, paths outside "
            "the current folder granted without asking.",
            file=sys.stderr,
        )
    agent._ensure_tool_loader_reset()  # pylint: disable=protected-access

    timer = _arm_time_limit(agent, time_limit) if time_limit else None
    started = time.time()
    abandoned = False
    try:
        query = text + NO_WEB_NOTE if no_web else text
        if time_limit:
            result, abandoned = _query_within_limit(agent, query, time_limit)
        else:
            result = agent.process_query(query) or {}
    finally:
        if timer is not None:
            timer.cancel()
        # An abandoned turn still holds the model; its memory pass would wait on it.
        if not abandoned:
            from gaia.agents.base.memory import drain_memory_extraction

            drain_memory_extraction(agent)
    record = summarize(
        result, agent.model_id or model, time.time() - started, verbose=verbose
    )
    if withheld:
        record["withheld_tools"] = withheld
    return record


def restrict_tools(
    agent: Any,
    disallowed_tools: Sequence[str],
    *,
    no_web: bool = False,
    no_skills: bool = False,
) -> List[str]:
    """Withhold the tools the -p flags turn off; return the names withheld.

    A ``--disallowed-tools`` pattern that matches no tool is an error. The
    ``--no-web`` and ``--no-skills`` sets withhold whichever of theirs this
    agent has, since a model's feature tier may already leave some out.
    """
    if not (disallowed_tools or no_web or no_skills):
        return []
    patterns = list(disallowed_tools)
    present = set(agent.get_tools_info())
    for wanted, preset in (
        (no_web, WEB_TOOL_PATTERNS),
        (no_skills, SKILL_TOOL_PATTERNS),
    ):
        if wanted:
            patterns += [
                p for p in preset if any(fnmatchcase(name, p) for name in present)
            ]
    if no_skills:
        agent._learned_skills_enabled = False  # pylint: disable=protected-access
    withheld = agent.deny_tools(list(dict.fromkeys(patterns))) if patterns else []
    agent.rebuild_system_prompt()
    return withheld


def _query_within_limit(
    agent: Any, text: str, seconds: int
) -> "tuple[Dict[str, Any], bool]":
    """``(result, abandoned)`` for a turn given *seconds*.

    A turn still running :data:`HARD_STOP_GRACE_S` past the limit is left to
    end with the process, and its result is built from what it had so far.
    """
    outcome: Dict[str, Any] = {}

    def _turn() -> None:
        try:
            outcome["result"] = agent.process_query(text) or {}
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            outcome["error"] = exc

    worker = threading.Thread(target=_turn, name="gaia-p-turn", daemon=True)
    worker.start()
    worker.join(seconds + HARD_STOP_GRACE_S)
    if worker.is_alive():
        log.warning("Abandoning the turn %ss past its time limit", HARD_STOP_GRACE_S)
        return (
            agent.time_limit_abandoned_result(
                f"Stopped at the {seconds}-second time limit (--time-limit) "
                "while a model call was still running."
            ),
            True,
        )
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"], False


def _arm_time_limit(agent: Any, seconds: int) -> threading.Timer:
    """Give the turn *seconds*: the agent wraps up before, a stop enforces it.

    The agent warns the model and stops its loop short of the limit so the
    requested files and the answer still get written. A turn still busy at the
    limit is stopped: a shell or Python run ends there and the turn ends at
    its next step. A model call already sent is not waited for past a short
    grace (:func:`_query_within_limit`).
    """
    from gaia.agents.base.agent import TurnCancel

    agent.time_limit_s = seconds
    cancel = TurnCancel()
    agent._cancel_event = cancel  # pylint: disable=protected-access
    timer = threading.Timer(
        seconds,
        cancel.cancel,
        args=[
            f"Stopped at the {seconds}-second time limit (--time-limit) before "
            "the turn finished."
        ],
    )
    timer.daemon = True
    timer.start()
    return timer


def _relay_turn(
    text: str,
    *,
    model: Optional[str],
    verbose: bool,
    max_steps: Optional[int],
    skip_permissions: bool,
    full_access: bool,
    base_url: Optional[str],
    extra_paths: Optional[List[str]] = None,
    time_limit: Optional[int] = None,
    restricted: str = "",
) -> Dict[str, Any]:
    """Run the turn on the GAIA agent's own binary, through the daemon.

    Confirmation-gated tools are refused there, as -p refuses them in-process
    without --dangerously-skip-permissions.
    """
    import io

    from gaia.daemon.agent_query import ConsoleRenderer, run_query

    if skip_permissions or full_access or base_url or time_limit or restricted:
        flag = (
            "--full-access"
            if full_access
            else (
                "--dangerously-skip-permissions"
                if skip_permissions
                else (
                    "--base-url"
                    if base_url
                    else "--time-limit" if time_limit else restricted
                )
            )
        )
        raise PrintModeError(
            f"{flag} needs the GAIA agent in this process, and here it runs as "
            "its own program through GAIA's background service, which uses "
            "GAIA's model server and asks before gated tools. Run without "
            f"{flag}, or from a GAIA install that includes the agent package "
            "(https://amd-gaia.ai/docs/reference/dev)."
        )

    cwd = os.getcwd()
    renderer = ConsoleRenderer(out=io.StringIO(), err=io.StringIO())
    started = time.time()
    outcome = run_query(
        "gaia",
        text,
        model=model,
        max_steps=max_steps,
        renderer=renderer,
        working_directory=cwd,
        allowed_paths=[cwd, *(extra_paths or [])],
        # Sent, even empty, it makes the final carry the turn's model messages.
        **({"turns": []} if verbose else {}),
    )
    succeeded = outcome.terminal_type == "final" and not outcome.failed
    usage = outcome.usage or {}
    result: Dict[str, Any] = {
        "status": "success" if succeeded else "error",
        "result": outcome.final_answer or "",
        "steps_taken": usage.get("steps"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("tokens"),
        "conversation": outcome.model_messages or [],
    }
    if not succeeded:
        result["error"] = (
            outcome.error_detail
            or (outcome.final_answer if outcome.failed else "")
            or "the GAIA agent's reply ended without an answer"
        )
    record = summarize(result, model, time.time() - started, verbose=verbose)
    # Prefer the agent's own counts; a final without them keeps the conversation's.
    for src, dst in (
        ("tools_used", "num_tool_calls"),
        ("cached_tokens", "cached_tokens"),
    ):
        if src in usage:
            record[dst] = int(usage[src] or 0)
    return record
