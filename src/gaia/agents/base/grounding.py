# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Does the answer rest on what this turn's tools actually did?

Three checks, each comparing the answer's prose with the turn's tool record:

* **look** — the request names a file or symbol and no tool touched it, or the
  answer says something does not exist with no lookup on record.
* **content** — values the answer presents as data (table cells, rows of a
  data block, decimals, dates) that appear in no tool output or earlier
  message. A tool's own arguments are not evidence for what it returned.
* **action** — the answer says it searched, read, ran, tested, verified or
  documented something, and no tool of that kind ran.
* **request** — the user asked outright for a command to be run, and nothing
  ran: the answer cannot be that command's output.

Figures in the files the turn wrote are checked by
:mod:`gaia.agents.base.deliverables`.

Each returns :class:`Finding` objects; the agent loop turns them into one
correction, then — if the next answer still has them — reasons for the
answer's one "not confirmed" note (:func:`~gaia.agents.base.verification.build_verification_scope`). A finding is a prompt to look, never a rewrite of the answer.

Records are the loop's ``_turn_tool_executions`` entries
(:func:`~gaia.agents.base.verification.verification_record` plus ``args`` and
``observed``). Pure and dependency-free, like :mod:`gaia.agents.base.claims`.
"""

from __future__ import annotations

import math
import os
import posixpath
import re
import shlex
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from gaia.agents.base.checks import check_kind
from gaia.agents.base.claims import (
    absence_claim,
    action_claims,
    admits_unverified,
    read_claims,
)
from gaia.agents.base.completion import WRITE_TOOLS, mentioned_paths
from gaia.agents.base.text_task import instruction_part
from gaia.agents.base.verification import is_check_execution, observed_text

LOOK = "look"
CONTENT = "content"
ACTION = "action"
REQUEST = "request"

#: How much of one tool result is kept for the checks.
OBSERVED_MAX_CHARS = 400_000


def observed_excerpt(text: str, limit: int = OBSERVED_MAX_CHARS) -> str:
    """*text* cut to *limit* chars, keeping both ends.

    A long log puts its results last — timings, test summaries — so a head-only
    cut drops exactly the values an answer reports.
    """
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n" + text[-half:]


@dataclass(frozen=True)
class Finding:
    """One gap between the answer and the record."""

    gate: str
    #: Sentence for the correction the model sees.
    correction: str
    #: First-person reason for the "not confirmed" note if the gap survives.
    note: str


# ---------------------------------------------------------------------------
# What kind of tool a record is, by name
# ---------------------------------------------------------------------------

_LISTING_WORDS = ("list", "browse", "tree", "glob")
#: "query" covers the document tools: query_documents, query_specific_file.
_SEARCH_WORDS = _LISTING_WORDS + ("search", "grep", "find", "locate", "query")
_READ_WORDS = _SEARCH_WORDS + (
    "read",
    "view",
    "open",
    "cat",
    "get",
    "extract",
    "analyze",
    "query",
    "fetch",
    "summar",
    "describe",
    "show",
    "inspect",
    "index",
)
_EXEC_WORDS = ("run", "exec", "shell", "python", "pytest", "test", "build", "lint")
_DOC_MARKERS = ("readme", ".md", ".rst", "docs/", "docs\\", "changelog")


def _name(record: Dict[str, Any]) -> str:
    return (record.get("tool") or "").strip().lower()


def _ran(record: Dict[str, Any]) -> bool:
    return bool(record.get("ran", True))


def _has(name: str, words: Sequence[str]) -> bool:
    return any(word in name for word in words)


def _args_text(record: Dict[str, Any]) -> str:
    return _slashed(observed_text(record.get("args") or {}))


def _slashed(text: str) -> str:
    return text.replace("\\", "/").lower()


def _arg_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for sub in value.values():
            yield from _arg_strings(sub)
    elif isinstance(value, (list, tuple)):
        for sub in value:
            yield from _arg_strings(sub)


#: Arguments that hold code a tool runs: its literals are not findings.
_CODE_ARGS = ("code", "script", "command", "cmd")
_CODE_NUMBER = re.compile(
    r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\w]|\.\d)"
)
_OUTPUT_NUMBER = re.compile(
    r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.,])\d+(?:\.\d+)?"
)


def evidence_text(record: Dict[str, Any], code_literals: bool = False) -> str:
    """What *record*'s result shows, minus what the call itself put there.

    A search result that repeats its query, or a "no occurrence of '$12'"
    reply, would otherwise make the call's own arguments look confirmed.
    With *code_literals*, numbers a script hard-codes are also dropped from
    what it printed: ``print(95)`` is not a source for 95.
    """
    text = record.get("observed") or ""
    args = record.get("args") or {}
    for value in _arg_strings(args):
        if not any(ch.isdigit() for ch in value):
            continue
        for quote in ("'", '"', "“"):
            close = "”" if quote == "“" else quote
            text = text.replace(f"{quote}{value}{close}", " ")
        if len(value) >= 8:
            text = text.replace(value, " ")
    if code_literals:
        literals: Set[float] = set()
        for key in _CODE_ARGS:
            code = args.get(key) if isinstance(args, dict) else None
            if isinstance(code, str):
                for match in _CODE_NUMBER.findall(code):
                    # ``5,000`` is 5000 in a string, and 5 and 0 in a tuple.
                    literals.add(float(match.replace(",", "")))
                    literals.update(float(part) for part in match.split(","))
        literals -= {0.0, 1.0}
        if literals:
            text = _OUTPUT_NUMBER.sub(
                lambda m: (
                    " "
                    if float(m.group(0).replace(",", "")) in literals
                    else m.group(0)
                ),
                text,
            )
    return text


def _looked(record: Dict[str, Any]) -> bool:
    return _ran(record) and _has(_name(record), _READ_WORDS + _EXEC_WORDS)


def _searched(record: Dict[str, Any]) -> bool:
    return _ran(record) and _has(_name(record), _SEARCH_WORDS + _EXEC_WORDS)


def _executed(record: Dict[str, Any]) -> bool:
    return _ran(record) and _has(_name(record), _EXEC_WORDS)


def _tested(record: Dict[str, Any]) -> bool:
    if not is_check_execution(record):
        return False
    label = record.get("check_label")
    return not label or (record.get("check_kind") or check_kind(label)) == "test"


_INTERPRETERS = frozenset(
    "python py pypy node nodejs deno bun ts-node tsx ruby perl php lua bash sh "
    "zsh dash pwsh powershell rscript julia java".split()
)
_SHELLS = frozenset({"bash", "sh", "zsh", "dash"})
#: Flags that only check syntax: the program never runs.
_SYNTAX_ONLY = {
    **{name: {"-c", "--check"} for name in ("node", "nodejs")},
    **{name: {"-n"} for name in _SHELLS},
    "php": {"-l", "--syntax-check"},
    "ruby": {"-c"},
    "perl": {"-c"},
}
#: Flags whose value is inline code rather than a file.
_INLINE_CODE = {
    "default": {"-c", "-e", "-E", "-p", "--eval", "--print", "-command"},
    "php": {"-r"},
}
#: Interpreter flags that take the next word as their value.
_FLAG_VALUES = {
    **{name: {"-W", "-X"} for name in ("python", "py", "pypy")},
    **{
        name: {"-r", "--require", "--import", "--loader"}
        for name in ("node", "nodejs", "tsx", "ts-node")
    },
    **{name: {"-o", "-O"} for name in _SHELLS},
    "php": {"-c", "-d"},
    "ruby": {"-I", "-r"},
    "java": {"-cp", "-classpath", "--class-path"},
}
#: Wrapper flags that take the next word as their value.
_WRAPPER_FLAG_VALUES = frozenset(
    "-s -k -n -u -g -p -C --signal --kill-after --with --python --project "
    "--directory --env-file --extra --package".split()
)
#: Wrappers that run the rest of the command: ``timeout 10 python f.py``.
_WRAPPERS = frozenset(
    "sudo time env nohup exec command nice ionice stdbuf timeout npx bunx".split()
)
#: Tools whose ``run`` subcommand wraps another command: ``uv run python f.py``.
_RUN_WRAPPERS = frozenset({"uv", "poetry", "pipenv", "pdm", "hatch"})
#: Tools whose ``run`` builds and runs the sources of a directory.
_BUILD_AND_RUN = {"go": (".go",), "cargo": (".rs",), "dotnet": (".cs", ".fs")}
_COMPILERS = frozenset(
    "gcc g++ cc c++ clang clang++ tcc rustc javac nvcc gfortran".split()
)
_SOURCE_EXTS = (
    ".c", ".cc", ".cpp", ".cxx", ".m", ".mm", ".rs", ".java", ".cu", ".f",
    ".f90", ".s",
)  # fmt: skip
_SEPARATORS = frozenset({"&&", "||", ";", ";;", "|", "|&", "&", "(", ")"})
#: Separators after which the call's exit status is not this command's.
_MASKING = frozenset({"||", ";", ";;", "&", "|", "|&"})
#: Output that shows a program crashed or reported an error.
_RUN_ERROR = re.compile(
    r"Traceback \(most recent call last\)|\b[A-Z]\w*(?:Error|Exception)\b:?"
    r"|(?im:^\s*(?:fatal\s+)?error\b)|\bError:|Segmentation fault|core dumped"
    r"|\bpanic:|\bAborted\b|exit(?:ed)? (?:with )?(?:code|status) -?[1-9]"
    r"|returned non-zero|command not found|No such file or directory"
    r"|is not recognized as an internal or external command"
)
_ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
_ABSOLUTE = re.compile(r"^(?:/|~|[a-z]:/)")


def _command_words(command: str) -> List[str]:
    # Windows separators become ``/``; an escaped quote stays a shell escape.
    text = re.sub(r"\\(?![\\'\"])", "/", command).replace("\n", " ; ")
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return re.findall(r"&&|\|\||[;|&()<>]+|[^\s;|&()<>]+", text)


def _segments(words: Sequence[str]) -> Iterable[Tuple[List[str], bool]]:
    """Each simple command, and whether the command's exit status is lost.

    ``f || true``, ``f; echo``, ``f &`` and ``f | head`` all report another
    command's status, so the call's success says nothing about ``f``.
    """
    segment: List[str] = []
    pending: Optional[Tuple[List[str], str]] = None
    skip_target = False
    for word in words:
        if skip_target:
            skip_target = False
        elif word in _SEPARATORS:
            if segment:
                if pending:
                    yield pending[0], pending[1] in _MASKING
                pending = (segment, word)
            segment = []
        elif word and set(word) <= set("<>&|"):
            # A redirect: drop its fd (``2>``) and its target.
            if segment and segment[-1].isdigit():
                segment.pop()
            skip_target = True
        else:
            segment.append(word)
    if pending:
        # ``f;`` with nothing after keeps f's status; ``f &`` never does.
        yield pending[0], pending[1] in _MASKING and (
            bool(segment) or pending[1] in ("&", "|", "|&")
        )
    if segment:
        yield segment, False


def _program_name(word: str) -> str:
    name = word.rsplit("/", 1)[-1].lower()
    name = name[:-4] if name.endswith(".exe") else name
    return re.sub(r"[\d.]+$", "", name) or name


#: A directory the record can't place (``cd -``): nothing resolves into it.
_UNKNOWN_DIR = "?"


def _resolve(path: str, cwd: Optional[str]) -> str:
    """*path* made absolute, or left relative to the session's workspace.

    File tools and the shell share that workspace as their relative base, so
    a relative path names the same file on both sides, and never an absolute
    one: the workspace itself isn't in the record.
    """
    if path.startswith("~"):
        path = os.path.expanduser(path)
    path = path.replace("\\", "/").lower()
    if cwd and not _ABSOLUTE.match(path):
        path = posixpath.join(cwd, path)
    return posixpath.normpath(path)


def _under(path: str, directory: str) -> bool:
    if directory == ".":
        return not _ABSOLUTE.match(path) and not path.startswith("../")
    return path.startswith(directory.rstrip("/") + "/")


def _skip_flags(words: List[str]) -> List[str]:
    while words and (words[0].startswith("-") or _ASSIGNMENT.match(words[0])):
        words = words[2:] if words[0] in _WRAPPER_FLAG_VALUES else words[1:]
    return words


def _interpreted(name: str, args: List[str]) -> Tuple[str, List[str]]:
    """``("files", paths)`` the interpreter runs, ``("shell", [code])`` for
    ``sh -c``, or ``("none", [])`` for inline code and syntax checks."""
    inline = _INLINE_CODE.get(name, _INLINE_CODE["default"])
    while args:
        word, args = args[0], args[1:]
        if word in _SYNTAX_ONLY.get(name, ()):
            return "none", []
        if name in _SHELLS and word == "-c":
            return ("shell", args[:1]) if args else ("none", [])
        if name in ("pwsh", "powershell") and word.lower() in ("-c", "-command"):
            return ("shell", [" ".join(args)]) if args else ("none", [])
        if word in inline:
            return "none", []
        if word == "-m" and name in ("python", "py", "pypy"):
            if not args or args[0] in ("py_compile", "compileall"):
                return "none", []
            module = args[0].replace(".", "/")
            return "files", [module + ".py", module + "/__main__.py"]
        if not word.startswith("-"):
            if name == "java" and not word.endswith(".java"):
                return "files", [word.replace(".", "/") + ".class"]
            return "files", [word]
        if word in _FLAG_VALUES.get(name, ()) and args:
            args = args[1:]
    return "none", []


def _run_events(
    command: str, cwd: Optional[str], masked: bool = False, subshell: bool = False
) -> Iterable[Tuple[Any, ...]]:
    """What a shell command runs or builds, segment by segment, after ``cd``.

    Yields ``("run", path, masked)``, ``("run_dir", directory, extensions,
    masked)``, ``("build", sources, output, masked)`` and, outside a subshell,
    ``("cd", directory)``. *masked* means the call's exit status isn't the
    command's own.
    """
    for words, lost in _segments(_command_words(command)):
        lost = lost or masked
        while words and _ASSIGNMENT.match(words[0]):
            words = words[1:]
        name = _program_name(words[0]) if words else ""
        if name in ("cd", "pushd", "chdir"):
            target = words[1] if len(words) > 1 else "~"
            cwd = _UNKNOWN_DIR if target == "-" else _resolve(target, cwd)
            if not subshell:
                yield ("cd", cwd)
            continue
        if name == "cmd" and len(words) > 2 and words[1].lower() in ("/c", "/k"):
            yield from _run_events(" ".join(words[2:]), cwd, lost, True)
            continue
        while words and (
            name in _WRAPPERS or (name in _RUN_WRAPPERS and words[1:2] == ["run"])
        ):
            lost = lost or name == "nohup"
            words = _skip_flags(words[2:] if name in _RUN_WRAPPERS else words[1:])
            if name == "timeout" and words:
                words = words[1:]  # the duration
            name = _program_name(words[0]) if words else ""
        if not words:
            continue
        args = words[1:]
        if name in _BUILD_AND_RUN:
            if args[:1] == ["run"]:
                exts = _BUILD_AND_RUN[name]
                operands = [a for a in args[1:] if not a.startswith("-")]
                files = [a for a in operands if a.endswith(exts)]
                for path in files:
                    yield ("run", _resolve(path, cwd), lost)
                if not files:
                    target = operands[0] if operands and name == "go" else "."
                    yield ("run_dir", _resolve(target, cwd), exts, lost)
            continue
        if name in _COMPILERS:
            if "-c" in args or "-fsyntax-only" in args or "-S" in args:
                continue
            sources = [
                _resolve(a, cwd)
                for a in args
                if not a.startswith("-") and a.lower().endswith(_SOURCE_EXTS)
            ]
            output = None
            for i, arg in enumerate(args):
                if arg == "-o" and i + 1 < len(args):
                    output = args[i + 1]
                elif arg.startswith("-o") and len(arg) > 2:
                    output = arg[2:]
            if name == "javac":
                outputs = [s.rsplit(".", 1)[0] + ".class" for s in sources]
            elif output is None and name == "rustc" and sources:
                outputs = [sources[0].rsplit(".", 1)[0]]
            else:
                outputs = [_resolve(output or "a.out", cwd)]
            for out in outputs if sources else []:
                yield ("build", sources, out, lost)
                yield ("build", sources, out + ".exe", lost)
            continue
        if name in ("deno", "bun") and args[:1] == ["run"]:
            args = args[1:]
        if name in _INTERPRETERS:
            kind, values = _interpreted(name, args)
            if kind == "shell":
                yield from _run_events(values[0], cwd, lost, True)
            for path in values if kind == "files" else []:
                yield ("run", _resolve(path, cwd), lost)
            continue
        yield ("run", _resolve(words[0], cwd), lost)


#: Result fields that describe the call, each followed by its value.
_RESULT_META = frozenset(
    "status return_code returncode exit_code duration_seconds cwd command "
    "truncated timed_out shell pid".split()
)
#: Result fields that hold what the program printed.
_RESULT_STREAMS = frozenset({"stdout", "stderr", "output", "steps"})


def _program_output(observed: str) -> str:
    """What the program printed, from a flattened tool result."""
    lines = observed.split("\n")
    kept: List[str] = []
    skip = False
    for line in lines:
        key = line.strip().lower()
        if skip:
            skip = False
        elif key in _RESULT_META:
            skip = True
        elif key not in _RESULT_STREAMS:
            kept.append(line)
    return "\n".join(kept)


def _ran_what_it_wrote(records: Sequence[Dict[str, Any]]) -> bool:
    """A file this turn wrote was then run, after its last write, and the run
    did not fail.

    Where there is no test suite, running the program is the test. A binary
    counts only when built from a written source after that source's last write.
    Output that shows an error never counts; a run whose exit status the shell
    hid (``|| true``, ``; echo``, ``&``, a pipe) counts only on clean output.
    """
    written: List[str] = []
    built: Dict[str, Set[str]] = {}
    ran: Set[str] = set()
    session_cwd: Optional[str] = None

    def credit(path: str) -> None:
        ran.update(w for w in written if w == path)
        ran.update(built.get(path, ()))

    for record in records:
        args = record.get("args")
        args = args if isinstance(args, dict) else {}
        if record.get("mutated"):
            for key, value in args.items():
                if not (isinstance(value, str) and value and key.endswith("path")):
                    continue
                path = _resolve(value, None)
                if path not in written:
                    written.append(path)
                ran.discard(path)
                built = {
                    out: sources
                    for out, sources in built.items()
                    if path not in sources
                }
            continue
        if not _executed(record) or record.get("failed"):
            continue
        output = _program_output(record.get("observed") or "")
        errored = bool(_RUN_ERROR.search(output))
        shown = bool(output.strip())
        command = args.get("command")
        if not isinstance(command, str):
            for key, value in args.items() if not errored else ():
                if isinstance(value, str) and value and key.endswith("path"):
                    credit(_resolve(value, None))
            continue
        cwd = args.get("working_directory") or args.get("cwd")
        cwd = _resolve(cwd, None) if isinstance(cwd, str) and cwd else session_cwd
        for event in _run_events(command, cwd):
            if event[0] == "cd":
                # The shell session keeps the directory a command moved to.
                session_cwd = event[1]
            elif errored or (event[-1] and not shown):
                continue
            elif event[0] == "run":
                credit(event[1])
            elif event[0] == "run_dir":
                ran.update(
                    w for w in written if w.endswith(event[2]) and _under(w, event[1])
                )
            else:
                fresh = {w for w in written if w in event[1]}
                if fresh:
                    built[event[2]] = fresh
    return bool(ran)


def _touched_docs(record: Dict[str, Any]) -> bool:
    return _ran(record) and _has(_args_text(record), _DOC_MARKERS)


# ---------------------------------------------------------------------------
# look: the request names something and nothing looked at it
# ---------------------------------------------------------------------------

#: Extensions that make a bare token a file name rather than a word.
_FILE_SUFFIX = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,7}$")
#: Suffixes files actually carry, so ``self.Y`` or ``Y.shape`` is not a file.
_FILE_EXTENSIONS = frozenset(
    "py pyi pyx ipynb js jsx mjs cjs ts tsx vue svelte json jsonl yaml yml toml "
    "ini cfg conf env lock xml html htm css scss less md mdx rst txt csv tsv log "
    "sql sh bash zsh ps1 bat cmd c h cc cpp cxx hpp hh hxx cu cuh hip cl s asm "
    "go rs java kt kts scala cs fs rb php swift m mm r jl lua pl dart zig "
    "cmake mk gradle proto graphql tf pdf docx xlsx pptx png jpg jpeg gif svg "
    "webp wav mp3 mp4 zip gz tar".split()
)
#: ``parse_updated()``: a call is code to read. A bare name may be a variable,
#: field, parameter or repository, so it is not.
_SYMBOL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\(\)")
_QUOTED_BLOCK = re.compile(r"^[ \t]*(`{3,}|~{3,}).*?^[ \t]*\1", re.M | re.S)


def _is_file_name(base: str) -> bool:
    suffix = _FILE_SUFFIX.search(base)
    return bool(suffix) and suffix.group(0)[1:].lower() in _FILE_EXTENSIONS


def named_targets(query: str) -> List[str]:
    """Files and snake_case functions *query* names, in order.

    A file needs a known extension ("dates.py", "toybox/dates.py"); a
    directory, the working folder the request opens with, or an attribute
    like ``self.Y`` is not something to read. A capitalised ``Name.js`` is a
    framework, not a file. A function is written as a call with an
    underscore, no dot and no capitals, so ``parse_updated()`` counts and
    ``print()``, ``os.path.join()`` or a bare `row_stride` do not. Code
    blocks the request quotes are material it hands over, not targets.
    """
    query = _QUOTED_BLOCK.sub("\n", query or "")
    targets: List[str] = []
    for path in mentioned_paths(query):
        base = re.split(r"[\\/]", path.rstrip("\\/"))[-1]
        framework = base[:1].isupper() and base.endswith(".js")
        if not _is_file_name(base) or framework:
            continue
        targets.append(path)
    for match in _SYMBOL.finditer(query):
        symbol = match.group(1)
        if "_" in symbol.strip("_") and symbol.islower() and symbol not in targets:
            targets.append(symbol)
    return targets


_FINDER_WORDS = ("search", "grep", "find", "locate", "glob")


def _read_it(target: str, records: Iterable[Dict[str, Any]]) -> bool:
    """True when a successful call read *target*.

    A file counts when a read or command call names it — by file name, since
    tools get relative and absolute spellings of the same file. A failed
    read, a listing, and a file name that only appears in a ``dir`` output
    did not read it. A symbol counts when any successful call names or shows
    it, a listing included: that tool put it in front of the model.
    """
    key = _target_key(target)
    is_file = _is_file_name(key)
    for record in records:
        name = _name(record)
        if not _ran(record) or record.get("failed"):
            continue
        if is_file:
            lister = _has(name, _FINDER_WORDS + _LISTING_WORDS)
            if not lister and key in _args_text(record):
                return True
        elif key in _args_text(record) or key in _slashed(record.get("observed") or ""):
            return True
    return False


def _found_missing(target: str, records: Iterable[Dict[str, Any]]) -> bool:
    """True when a call looked for *target* on disk and found it isn't there."""
    key = _target_key(target)
    return any(r.get("checked_absent") and key in _args_text(r) for r in records)


def _target_key(target: str) -> str:
    return _slashed(re.split(r"[\\/]", target.rstrip("\\/"))[-1])


def _names(items: Sequence[str]) -> str:
    quoted = [f"`{item}`" for item in items]
    return (
        quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " or " + quoted[-1]
    )


def look_findings(
    answer: str,
    query: str,
    records: Sequence[Dict[str, Any]],
    locate: Optional[Callable[[str], Optional[str]]] = None,
    history: str = "",
    plan_only: bool = False,
) -> List[Finding]:
    """The request's files and symbols nothing read, and unbacked "not found"s.

    A named file only counts when *locate* finds it on disk or the answer
    claims it does not exist — a request that merely mentions ``setup.py`` in
    passing is not asking for it to be read. The correction names where the
    file is, because a model that resolved the path against the wrong folder
    will do it again. A symbol counts when nothing showed it, but only in a
    turn about the project — a named file exists or a tool ran — so asking
    what `max_tokens` does is not a request to read code. A
    target an earlier turn (*history*) already discussed is not asked for again.
    A "not found" only counts in a turn about something the request named, so
    explaining what a 404 means is not a claim about the disk.

    Once the turn has read one of the request's targets, the others are
    candidates it chose to leave: they count only when the answer says it read
    them or says they don't exist. An answer that says it read a target counts
    whether or not the file is on disk. The same holds for every target when
    the user asked for a plan only (*plan_only*): it names files still to read.
    """
    targets = named_targets(query)
    if not targets:
        return []
    findings: List[Finding] = []
    absent = absence_claim(answer)
    absent_text = _slashed(absent or "")
    claimed = [_slashed(clause) for clause in read_claims(answer)]
    earlier = _slashed(history)
    missed = []
    found: List[str] = []
    where_is = {t: locate(t) if locate else None for t in targets if _is_file_name(t)}
    about_project = any(where_is.values()) or any(_ran(r) for r in records)
    engaged = plan_only or any(_read_it(t, records) for t in targets)
    for target in targets:
        if (
            _read_it(target, records)
            or _found_missing(target, records)
            or _slashed(target) in earlier
        ):
            continue
        key = _target_key(target)
        said_read = any(key in clause for clause in claimed)
        if engaged and not said_read and key not in absent_text:
            continue
        if target not in where_is:
            if about_project or said_read:
                missed.append(target)
            continue
        where = where_is[target]
        if where:
            found.append(f"`{target}` is at `{where}`")
        if absent is not None or where or said_read:
            missed.append(target)
    if missed:
        names = _names(missed)
        located = f" ({'; '.join(found)})" if found else ""
        findings.append(
            Finding(
                LOOK,
                f"You haven't read {names} this turn{located} — read "
                f"{'it' if len(missed) == 1 else 'them'} now, or say plainly that "
                "you didn't.",
                f"I didn't read {names} this turn",
            )
        )
    if absent and not any(
        _searched(r) or _looked(r) or r.get("checked_absent") for r in records
    ):
        findings.append(
            Finding(
                LOOK,
                f'Your answer says "{_clip(absent)}", but no search, listing or '
                "read ran this turn — look first, or drop the claim.",
                _ABSENT_NOTE,
            )
        )
    return findings


_ABSENT_NOTE = "I said something doesn't exist without searching for it this turn"


# ---------------------------------------------------------------------------
# content: values presented as data that no tool produced
# ---------------------------------------------------------------------------

#: "293,500,000,000, up 4%": a comma after the figure ends it.
_GROUPED = re.compile(r"(?<![\w.,])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?!\w|[,.]\d)")
_PLAIN = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w]|\.\d)")
_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_FENCE = re.compile(
    r"^[ \t]*(`{3,}|~{3,})[ \t]*([\w+-]*)[^\n]*\n(.*?)^[ \t]*\1[ \t]*$", re.M | re.S
)
#: Fenced blocks presented as data, not code.
_DATA_FENCES = frozenset(
    {"", "csv", "tsv", "text", "txt", "plain", "output", "console", "json"}
)
_CODE_LINE = re.compile(r"[()=;{}]|\b(?:def|import|return|class|print)\b")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}")
#: Unit → (dimension, factors to its base unit). KB/MB/GB mean 1000 or 1024.
_UNIT_SCALES: Dict[str, "tuple[str, tuple[float, ...]]"] = {
    "ns": ("time", (1e-9,)),
    "us": ("time", (1e-6,)),
    "µs": ("time", (1e-6,)),
    "μs": ("time", (1e-6,)),
    "ms": ("time", (1e-3,)),
    "s": ("time", (1.0,)),
    "sec": ("time", (1.0,)),
    "secs": ("time", (1.0,)),
    "b": ("size", (1.0,)),
    "kb": ("size", (1e3, 1024.0)),
    "mb": ("size", (1e6, 1024.0**2)),
    "gb": ("size", (1e9, 1024.0**3)),
    "kib": ("size", (1024.0,)),
    "mib": ("size", (1024.0**2,)),
    "gib": ("size", (1024.0**3,)),
}
_UNIT = r"ns|us|µs|μs|ms|secs|sec|s|kib|mib|gib|kb|mb|gb|b"
#: A cell or field that is one value and nothing else: "181.0", "12%", "3.5s".
_PURE_VALUE = re.compile(
    r"^\s*\**\s*(?:(?:US|C|A)?\$|€|£|¥)?\s?"
    r"(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)\s*"
    rf"(%|{_UNIT})?\s*\**\s*$",
    re.I,
)
#: A column's unit: "median (µs)", "size [MB]".
_HEADER_UNIT = re.compile(rf"[(\[]\s*({_UNIT})\s*[)\]]", re.I)
#: A unit right after a number in prose: "0.342 ms".
_PROSE_UNIT = re.compile(rf"\s?({_UNIT})(?![\w/])", re.I)
#: A number with an explicit unit in evidence: "0.3417 ms", "1572864 B".
#: Case-sensitive, so "5 b" or "8 Mb" (bits) is not read as bytes.
_SOURCE_QUANTITY = re.compile(
    r"(?<![\w.])(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s?"
    r"(ns|us|µs|μs|ms|secs|sec|s|KiB|MiB|GiB|KB|kB|MB|GB|B)(?![\w/])"
)
_PURE_DATE = re.compile(r"^\s*\**\s*(\d{4}-\d{2}-\d{2})\s*\**\s*$")
#: Column headers whose numbers the answer assigns itself.
_ORDINAL_HEADER = re.compile(
    r"^\s*\**\s*(?:#|no\.?|n|rank|priority|step|order|id|line|lines|severity|position|pos)\s*\**\s*$",
    re.I,
)
#: A version or model name is not a measurement: "Python 3.12", "gpt-4.1".
_VERSION_LEAD = re.compile(
    r"(?:\b[A-Z][\w-]*[ -]?|\bv[ -]?|\b[\w-]*\d[\w-]*[ -]?|[A-Za-z]-)$"
    r"|(?i:\b(?:version|python|node|py)[ -]?)$"
)
_ESTIMATE_LEAD = re.compile(
    r"(?:~|\b(?:about|approximately|approx\.?|around|roughly|nearly|almost))\s*"
    r"(?:(?:US|C|A)?[$€£¥]\s*)?$",
    re.I,
)


@dataclass(frozen=True)
class _Value:
    text: str
    number: Optional[float]
    decimals: int
    #: A key of ``_UNIT_SCALES`` when the answer states one.
    unit: Optional[str] = None
    #: The figure as written before its scale: 293.5 of "$293.5B".
    mantissa: Optional["_Value"] = None


def _value(text: str, unit: Optional[str] = None) -> _Value:
    if _DATE.fullmatch(text):
        return _Value(text, None, 0)
    plain = text.replace(",", "")
    decimals = len(plain.split(".", 1)[1]) if "." in plain else 0
    return _Value(text, float(plain), decimals, unit)


def _unit(text: Optional[str]) -> Optional[str]:
    key = (text or "").lower()
    return key if key in _UNIT_SCALES else None


_MAGNITUDES: Dict[str, float] = {
    "k": 1e3,
    "thousand": 1e3,
    "m": 1e6,
    "mm": 1e6,
    "mn": 1e6,
    "mil": 1e6,
    "million": 1e6,
    "b": 1e9,
    "bn": 1e9,
    "billion": 1e9,
    "t": 1e12,
    "tn": 1e12,
    "trillion": 1e12,
}
#: Scales that need no currency sign: "14.5 million". A bare "B" is bytes.
_WORD_MAGNITUDES = frozenset({"thousand", "million", "billion", "trillion", "bn"})
#: "$14.5M", "$293.5B", "€2.1bn", "14.5 million": an amount and its scale.
_SCALED = re.compile(
    r"(?<![\w.,])(?P<cur>(?:US|C|A)?\$|€|£|¥)?\s?"
    r"(?P<n>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?"
    r"(?P<mag>thousand|million|billion|trillion|mil|mn|mm|bn|tn|[kmbt])(?!\w)",
    re.I,
)


def _scaled_amounts(text: str) -> List["tuple[re.Match[str], _Value]"]:
    """Each scaled amount in *text*, valued in full to its written precision.

    "$14.5M" is 14,500,000 to the nearest 100,000. A lone letter is a scale
    only after a currency sign, so "$293.5B" is billions and "8 B" stays bytes.
    """
    found = []
    for match in _SCALED.finditer(text):
        magnitude = match.group("mag").lower()
        if not match.group("cur") and magnitude not in _WORD_MAGNITUDES:
            continue
        scale = _MAGNITUDES[magnitude]
        mantissa = _value(match.group("n"))
        full = mantissa.number * scale
        if not math.isfinite(full):
            continue
        places = mantissa.decimals - round(math.log10(scale))
        found.append(
            (match, _Value(match.group(0).strip(), full, places, mantissa=mantissa))
        )
    return found


def _cell_value(cell: str, column_unit: Optional[str] = None) -> Optional[_Value]:
    date = _PURE_DATE.match(cell)
    if date:
        return _value(date.group(1))
    match = _PURE_VALUE.match(cell)
    if not match:
        return None
    value = _value(match.group(1), _unit(match.group(2)) or column_unit)
    if match.group(2) == "%" or value.decimals or abs(value.number) >= 100:
        return value
    return None  # counts, week numbers, ordinals: too common to judge


def _table_values(lines: List[str]) -> List[_Value]:
    values: List[_Value] = []
    header: Optional[List[str]] = None
    for index, line in enumerate(lines):
        if not line.strip().startswith("|"):
            header = None
            continue
        cells = [c for c in line.strip().strip("|").split("|")]
        if _TABLE_RULE.match(line):
            continue
        if header is None:
            nxt = lines[index + 1] if index + 1 < len(lines) else ""
            if _TABLE_RULE.match(nxt):
                header = cells
                continue
            header = []
        for col, cell in enumerate(cells):
            if col < len(header) and _ORDINAL_HEADER.match(header[col]):
                continue
            unit = _HEADER_UNIT.search(header[col]) if col < len(header) else None
            value = _cell_value(cell, _unit(unit.group(1)) if unit else None)
            if value is not None:
                values.append(value)
    return values


def _prose_values(text: str) -> List[_Value]:
    """Decimals, dates, percentages and 1,234-style numbers in running text."""
    values: List[_Value] = []
    for match in _DATE.finditer(text):
        values.append(_value(match.group(0)))
    text = _DATE.sub(" ", text)
    scaled = _scaled_amounts(text)
    for match, value in scaled:
        raw = match.group("n")
        lead = text[max(0, match.start() - 24) : match.start()]
        if not ("." in raw or "," in raw):
            continue
        if _VERSION_LEAD.search(lead) or _ESTIMATE_LEAD.search(lead):
            continue
        values.append(value)
    for match, _ in reversed(scaled):
        text = text[: match.start()] + " " + text[match.end() :]
    grouped = list(_GROUPED.finditer(text))
    # "37,160.80" is one figure, not 37 and 160.80.
    plain = [
        m
        for m in _PLAIN.finditer(text)
        if not any(g.start() <= m.start() < g.end() for g in grouped)
    ]
    for match in grouped + plain:
        raw = match.group(0)
        after = text[match.end() : match.end() + 1]
        if after == "+" or not ("." in raw or "," in raw or after == "%"):
            continue  # "3.11+" is a version floor
        lead = text[max(0, match.start() - 24) : match.start()]
        if (
            _VERSION_LEAD.search(lead)
            and not _is_money_number(text, match.start(), match.end())
            or _ESTIMATE_LEAD.search(lead)
        ):
            continue
        unit = _PROSE_UNIT.match(text, match.end())
        values.append(_value(raw, _unit(unit.group(1)) if unit else None))
    return values


#: Currency codes and words accepted after a prose amount as well as before it.
_MONEY_SUFFIX = r"(?:USD|EUR|GBP|CAD|AUD|JPY|CHF|CNY|INR|dollars?|euros?|pounds?)"
#: "$2,708.95", "€1,379.87", "USD 1,375.25": an amount to the cent or the thousand.
_PROSE_MONEY_PREFIX = re.compile(
    r"(?:(?:US|C|A)?\$|€|£|¥|\b(?:USD|EUR|GBP|CAD|AUD|JPY|CHF|CNY|INR)\s?)\s?"
    r"(?P<n>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+)(?!\d|,\d)"
)
#: "2,708.95 USD" and "2,708.95 dollars" are money too.
_PROSE_MONEY_SUFFIX = re.compile(
    r"(?<![\w.,])(?P<n>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+)\s+" rf"{_MONEY_SUFFIX}\b"
)


def _stated_money(text: str) -> Set[str]:
    """The exact amounts of money *text* states, as written, estimates aside."""
    amounts = set()
    for pattern in (_PROSE_MONEY_PREFIX, _PROSE_MONEY_SUFFIX):
        for match in pattern.finditer(text):
            lead = text[max(0, match.start() - 24) : match.start()]
            if not _ESTIMATE_LEAD.search(lead) and float(match["n"].replace(",", "")):
                amounts.add(match.group("n"))
    return amounts


def _is_money_number(text: str, start: int, end: int) -> bool:
    """Whether the number at *start:end* has its own currency marker."""
    lead = text[max(0, start - 24) : end]
    if any(m.end("n") == len(lead) for m in _PROSE_MONEY_PREFIX.finditer(lead)):
        return True
    return _PROSE_MONEY_SUFFIX.match(text[start : end + 24]) is not None


def _tables_and_prose(answer: str) -> "tuple[List[_Value], str]":
    """Values in *answer*'s tables and data blocks, and the prose around them."""
    values: List[_Value] = []

    def data_block(match: "re.Match[str]") -> str:
        if match.group(2).lower() in _DATA_FENCES:
            for line in match.group(3).splitlines():
                if _CODE_LINE.search(line) and match.group(2).lower() != "json":
                    continue
                for field in re.split(r"[,;\t|]|\s{2,}|:\s", line):
                    value = _cell_value(field.strip().strip('"'))
                    if value is not None:
                        values.append(value)
        return "\n"

    text = _FENCE.sub(data_block, answer or "")
    text = re.sub(r"`[^`\n]*`", " ", text)
    lines = text.splitlines()
    values.extend(_table_values(lines))
    prose = "\n".join(line for line in lines if not line.strip().startswith("|"))
    return values, prose


def presented_values(answer: str, prose_figures: bool = False) -> List[_Value]:
    """Values *answer* presents as data, in order, without duplicates.

    Table cells that are one value, fields of an untagged/CSV/JSON code block,
    and — only when the answer has one of those, or *prose_figures* is set —
    decimals, dates and percentages in its prose. Otherwise only the exact
    amounts of money its prose states are data. Code blocks in a language,
    inline code, small whole numbers, estimates and version numbers are left
    alone.
    """
    values, prose = _tables_and_prose(answer)
    if values or prose_figures:
        values.extend(_prose_values(prose))
    else:
        # A date or percentage beside a price in a chat reply is not data.
        money = _stated_money(prose)
        values = [
            v
            for v in _prose_values(prose)
            if v.text in money or (v.mantissa is not None and v.mantissa.text in money)
        ]
    seen: Set["tuple[str, Optional[str]]"] = set()
    unique = []
    for value in values:
        if (value.text, value.unit) not in seen:
            seen.add((value.text, value.unit))
            unique.append(value)
    return unique


#: Numbers in evidence text, units attached or not: "341.7us", "1.50x", "3.4e-04".
_SOURCE_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?!\d|\.\d)")


def _source_numbers(text: str) -> Set[float]:
    numbers: Set[float] = set()
    for match in _SOURCE_NUMBER.finditer(text):
        number = float(match.group(0).replace(",", ""))
        if math.isfinite(number):
            numbers.update((number, abs(number)))
    return numbers


class _Corpus:
    """Every number and date the turn's evidence holds."""

    def __init__(self, text: str):
        self.dates = set(_DATE.findall(text))
        text = _DATE.sub(" ", text)
        # "2,184.0" is a CSV row to one reader and 2184 to another: keep both.
        self.numbers = _source_numbers(text) | {
            float(match.group(0).replace(",", ""))
            for pattern in (_GROUPED, _PLAIN)
            for match in pattern.finditer(text)
        }
        # "$293.5B" in a source also holds 293,500,000,000.
        self.numbers.update(value.number for _, value in _scaled_amounts(text))
        # Each unit-tagged number in its dimension's base unit (seconds, bytes).
        self.quantities: Dict[str, Set[float]] = {}
        for match in _SOURCE_QUANTITY.finditer(text):
            dimension, factors = _UNIT_SCALES[match.group(2).lower()]
            number = float(match.group(1))
            self.quantities.setdefault(dimension, set()).update(
                number * f for f in factors if math.isfinite(number * f)
            )
        self._rounded: Dict[int, Set[int]] = {}

    def holds(self, value: _Value) -> bool:
        if value.number is None:
            return value.text in self.dates
        if value.decimals < 0:
            # Only to its precision: "0.45 shown as 45%" would let $30,000 source $3M.
            return any(_rounds_to(n, value) for n in self.numbers) or (
                value.mantissa is not None and self._holds_number(value.mantissa)
            )
        return self._holds_number(value) or self._holds_converted(value)

    def _holds_converted(self, value: _Value) -> bool:
        """*value* rounds from an evidence quantity in another unit of its dimension."""
        if value.unit is None:
            return False
        dimension, factors = _UNIT_SCALES[value.unit]
        return any(
            _rounds_to(base / factor, value)
            for base in self.quantities.get(dimension, ())
            for factor in factors
        )

    def _holds_number(self, value: _Value) -> bool:
        places = value.decimals
        if places not in self._rounded:
            scale = 10**places
            # 0.45 in the output may be shown as 45%; halves round either way.
            self._rounded[places] = {
                key
                for n in self.numbers
                for shown in (n * scale, n * 100 * scale)
                if math.isfinite(shown)
                for key in (math.floor(shown + 0.5), round(shown))
            }
        return round(value.number * 10**places) in self._rounded[places]

    def sources(self, value: _Value) -> List[float]:
        """The evidence numbers *value* is a rounding of."""
        if value.number is None:
            return []
        return [n for n in self.numbers if _rounds_to(n, value)][:3]


def _rounds_to(number: float, value: _Value) -> bool:
    half = 0.5 * 10 ** (-value.decimals)
    return math.isfinite(number) and abs(number - value.number) <= half + 1e-9


def _derived(value: _Value, operands: Sequence[Set[float]]) -> bool:
    """*value* is one arithmetic step from two grounded values the answer shows.

    Each set is one shown value and the evidence numbers it rounds. Pairing
    a value with its own source would ground its double, its half, 1 and 100%.
    """
    for i, left in enumerate(operands):
        for right in operands[i + 1 :]:
            if left & right:
                continue
            for a in left:
                for b in right:
                    results = [a + b, abs(a - b), a * b, (a + b) / 2]
                    for x, y in ((a, b), (b, a)):
                        if y:
                            results += [x / y, 100 * x / y]
                    if any(_rounds_to(r, value) for r in results):
                        return True
    return False


def _total_of(value: _Value, grounded: Sequence[_Value]) -> bool:
    """*value* is the sum of every grounded value the answer shows to its precision."""
    parts = [
        v.number
        for v in grounded
        if v.number is not None and v.decimals == value.decimals and v.unit is None
    ]
    return len(parts) > 2 and _rounds_to(sum(parts), value)


#: Tools that run a computation the model would otherwise do in its head.
SCRIPT_COMPUTATION_TOOLS = frozenset(
    {"run_python", "execute_python_file", "query_data"}
)


def _computation_failed(records: Sequence[Dict[str, Any]]) -> bool:
    """A script was asked for this turn and none of its runs succeeded."""
    scripts = [r for r in records if _name(r) in SCRIPT_COMPUTATION_TOOLS]
    return bool(scripts) and not any(_ran(r) and not r.get("failed") for r in scripts)


def content_findings(
    answer: str, records: Sequence[Dict[str, Any]], context: str = ""
) -> List[Finding]:
    """Values the answer presents that no tool output or message holds.

    *context* is everything else the model was given this turn — the request
    and earlier messages. A value one arithmetic step from two grounded
    numbers the answer also states, prose included, is grounded (a total, a
    difference, an average of two, a ratio) — computed from the shown numbers
    and from the evidence values they round, so a ratio worked out wrong is
    still a finding. After a computation that failed, decimals in prose count
    too: the model has done the arithmetic itself. Silent when no tool ran: an
    answer from knowledge has nothing to be grounded in, and the look check
    owns that case.
    """
    if not any(_ran(r) for r in records):
        return []
    values = presented_values(answer, prose_figures=_computation_failed(records))
    if not values:
        return []
    corpus = _Corpus("\n".join([context] + [evidence_text(r) for r in records]))
    grounded = [v for v in values if corpus.holds(v)]
    stated = grounded + [
        v
        for v in (_value(m.group(0)) for m in _PLAIN.finditer(_DATE.sub(" ", answer)))
        if corpus.holds(v)
    ]
    by_value: Dict[float, Set[float]] = {}
    for v in stated:
        if v.number is not None and len(by_value) < 20:
            by_value.setdefault(v.number, {v.number}).update(corpus.sources(v))
    operands = list(by_value.values())
    # Prose summing up a file the turn wrote is the deliverable check's to note.
    written = _Corpus(
        ""
        if _tables_and_prose(answer)[0]
        else "\n".join(
            observed_text(r.get("args") or {})
            for r in records
            if _name(r) in WRITE_TOOLS
        )
    )
    missing = [
        v.text
        for v in values
        if v not in grounded
        and not written.holds(v)
        and not (
            v.number is not None and (_derived(v, operands) or _total_of(v, grounded))
        )
    ]
    if not missing:
        return []
    shown = ", ".join(missing[:6]) + (", …" if len(missing) > 6 else "")
    return [
        Finding(
            CONTENT,
            f"These values in your answer appear in no tool output this turn: {shown}. "
            "Read the file back (or rerun the command) and report what it shows, or "
            "mark the values as unverified.",
            f"I didn't find {shown} in any tool output this turn",
        )
    ]


# ---------------------------------------------------------------------------
# action: "I searched / ran / tested …" with no such tool on record
# ---------------------------------------------------------------------------

_ACTION_EVIDENCE: Dict[str, "tuple[Callable[[Dict[str, Any]], bool], str]"] = {
    "searched": (_searched, "no search or listing ran this turn"),
    "read": (_looked, "no file was read this turn"),
    "ran": (_executed, "no command or script ran this turn"),
    "tested": (_tested, "no test run is recorded for this turn"),
    "verified": (_looked, "no tool ran this turn to check it"),
    "documented": (
        _touched_docs,
        "no documentation file was read or written this turn",
    ),
}


def action_findings(
    answer: str, records: Sequence[Dict[str, Any]], may_act: bool = True
) -> List[Finding]:
    """Work the answer reports that no tool of that kind did.

    With *may_act* false the user barred tools this turn, so the correction
    asks only for the claim to go, never for the work to be done.
    """
    remedy = (
        "Do it now, or drop the claim."
        if may_act
        else "The user asked for no tools this turn, so don't call one: drop "
        "the claim or reword it so it doesn't say you did."
    )
    findings: List[Finding] = []
    for kind, phrase in action_claims(answer):
        backed, why = _ACTION_EVIDENCE[kind]
        if any(backed(r) for r in records):
            continue
        if kind == "tested" and _ran_what_it_wrote(records):
            continue
        findings.append(
            Finding(
                ACTION,
                f'Your answer says "{phrase}", but {why}. {remedy}',
                f'I said "{phrase}", but {why}',
            )
        )
    return findings


#: "use your shell tool": only a shell execution answers it.
_SHELL = r"(?:shell|terminal|command[- ]line|bash|powershell|cmd)"
_SHELL_REQUEST = re.compile(
    rf"\b(?:use|using|with)\s+(?:your|the)\s+{_SHELL}\b"
    # "In the shell, run: dir", but not "In the terminal, can I run it?"
    rf"|\b(?:in|from)\s+(?:your|the|a)\s+{_SHELL}[\s,:]+(?:please\s+)?(?:run|execute)\b"
    # The shell tool named outright.
    r"|\buse\s+(?:the\s+)?run_shell_command\b",
    re.IGNORECASE,
)
#: A message that opens with "run"/"execute" and a command: quoted, a known
#: program, "this/the command", "this:", or a program with a switch
#: ("cmdkey /list"). Not "run a web search" or "run me through it", and only
#: the opening: a pasted README's "Run npm install" is not a request.
_RUN = r"(?:run|execute)(?:\s+|(?=:))(?:exactly\b\s*)?"
#: A command spelled out: quoted, after a colon, or a program with a switch.
_SPELLED_OUT = (
    r"[`\"']"
    r"|(?:(?:this|the\s+following)\b\s*(?:exact\s+)?(?:commands?\s*)?)?:"
    # Not "Run the -v flag" or "Run me -through".
    r"|(?!(?:a|an|the|it|me|us|them|this|that)\b)[\w.\-]+\s+(?:--?|/)[A-Za-z?]"
)
_COMMAND_REQUEST = re.compile(
    rf"\A\s*(?:(?:please|now|then|ok(?:ay)?)[\s,]+)*{_RUN}"
    rf"(?:{_SPELLED_OUT}"
    r"|(?:git|gh|npm|npx|pip|python|py|pytest|node|go|cargo|make|cmake|docker|"
    r"ls|dir|pwd|cd|echo|cat|type|whoami|hostname|ipconfig|ping|uv|ruff|"
    r"black|mypy|tsc|yarn|pnpm|dotnet|java|mvn|gradle)\b"
    r"|(?:the|this|that|these|the\s+following)\s+(?:exact\s+|one\s+)?"
    r"(?:commands?|script|tests?|test\s+suite|build|linter|lint)\b)"
    # "Can you run …" is often a question about ability: only a spelled-out
    # command makes it a request.
    rf"|\A\s*(?:please[\s,]+)?(?:can|could|would|will)\s+you\s+(?:please\s+)?"
    rf"{_RUN}(?:{_SPELLED_OUT})",
    re.IGNORECASE,
)
_SAYS_NOT_RUN = re.compile(
    r"\b(?:didn't|did not|couldn't|could not|can't|cannot|won't|unable to|"
    r"haven't|have not|(?:am\s+)?not\s+able\s+to)\s+(?:run|execute)\b"
    r"|\bdon't have (?:a|any) (?:shell|terminal)\b",
    re.IGNORECASE,
)


def request_findings(
    answer: str, query: str, records: Sequence[Dict[str, Any]]
) -> List[Finding]:
    """The user asked for a command to be run and nothing ran (#5735).

    An answer then reads as that command's output while the model made it up
    from context: the cwd in its prompt, an earlier turn's result, the clock.
    A shell request needs a shell run; a command named another way is answered
    by any tool that ran, since the agent may have the right one for it.
    """
    if _SAYS_NOT_RUN.search(answer):
        return []
    if _SHELL_REQUEST.search(query):
        if any(_executed(r) for r in records):
            return []
    elif _COMMAND_REQUEST.search(query):
        if any(_ran(r) for r in records):
            return []
    else:
        return []
    return [
        Finding(
            REQUEST,
            "You were asked to run a command and ran nothing this turn, so your "
            "answer cannot be its output. Run it now, or say that you didn't.",
            "I was asked to run a command and ran nothing",
        )
    ]


# ---------------------------------------------------------------------------
# All of them, and what the loop says
# ---------------------------------------------------------------------------


def ungrounded(
    answer: str,
    query: str,
    records: Sequence[Dict[str, Any]],
    *,
    history: str = "",
    locate: Optional[Callable[[str], Optional[str]]] = None,
    plan_only: bool = False,
    may_act: bool = True,
) -> List[Finding]:
    """Every gap between *answer* and this turn's tool record, look first.

    *may_act* is false when the user barred tools this turn.

    *history* is the text of earlier turns. An answer that already says it did
    not look, or that its figures are unverified, has nothing left to correct
    about looking or figures — only a false report of work done still counts.
    """
    # Files to read and commands to run come from the instruction; the figure
    # check below reads the whole query, since figures may come from the text.
    asked = instruction_part(query)
    findings = look_findings(answer, asked, records, locate, history, plan_only)
    if admits_unverified(answer):
        findings = [f for f in findings if f.note == _ABSENT_NOTE]
    else:
        findings += content_findings(answer, records, history + "\n" + query)
    return (
        findings
        + action_findings(answer, records, may_act)
        + request_findings(answer, asked, records)
    )


def grounding_correction(findings: Sequence[Finding]) -> str:
    """The one message that asks the model to close *findings*."""
    return (
        "[check:grounding] "
        + " ".join(f.correction for f in findings)
        + " Then give your complete answer again: it replaces the one above, "
        "which the user will not see. Write it for the user, in the language of "
        "their question, and don't mention this check."
    )


def unverified_reasons(findings: Sequence[Finding]) -> List[str]:
    """Reasons for the "not confirmed" note, for gaps that survived correction."""
    return list(dict.fromkeys(f.note for f in findings))


def path_locator(
    roots: Sequence[Optional[str]],
) -> Callable[[str], Optional[str]]:
    """A ``locate`` for :func:`look_findings`: the file's absolute path, or ``None``.

    An absolute path is taken as given; a relative one is tried under each of
    *roots* in order.
    """

    def locate(path: str) -> Optional[str]:
        expanded = os.path.expanduser(path)
        candidates = (
            [expanded]
            if os.path.isabs(expanded)
            else [os.path.join(root, expanded) for root in roots if root]
        )
        for candidate in candidates:
            if os.path.isfile(candidate):
                return os.path.normpath(candidate)
        return None

    return locate


def _clip(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
