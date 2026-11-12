# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Refuse code whose text names a protected folder or a credential file.

``run_python`` and an interpreter handed code on a shell line (``python -c``,
``powershell -Command``, ``node -e``) open files GAIA's file tools never see.
This reads the code before it runs and refuses it when the text names a path
the file tools refuse: a credential folder such as ``~/.ssh``, a credential
file such as ``.env`` or ``id_rsa``, or (for code that may write) GAIA's own
settings. Paths are judged in the same canonical form the file tools use, after
folding the plain ways of building one from pieces (``os.path.join``,
``Path(...) / x``, ``+``, ``+=``, f-strings, ``"".join``).

It is a screen, not a sandbox: code that works a path out while it runs
(arithmetic on characters, base64, a value read from a file or the network) is
not caught. Confining what code can open needs it to run with restrictions of
its own, which GAIA does not do yet.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set
from urllib.parse import unquote

from gaia.canonical_path import canonical_path, join_under
from gaia.logger import get_logger

log = get_logger(__name__)

#: Values kept per expression, so joins in a loop cannot multiply without end.
_MAX_VALUES = 32
#: Longer strings are data, not paths.
_MAX_PATH_TEXT = 1024
#: Code past this size is read as text, not folded.
_MAX_FOLD_BYTES = 256 * 1024

#: Ways scripts name the home folder.
_HOME_SPELLINGS = (
    "${env:USERPROFILE}",
    "$env:USERPROFILE",
    "$env:HOME",
    "${HOME}",
    "$HOME",
    "%USERPROFILE%",
    "%HOME%",
)
_HOME_ENV = frozenset({"HOME", "USERPROFILE"})

#: Calls whose result is a path built from their receiver and arguments.
_PATH_CALLS = frozenset(
    {
        "join",
        "joinpath",
        "abspath",
        "normpath",
        "realpath",
        "expanduser",
        "expandvars",
        "resolve",
        "absolute",
        "as_posix",
        "fspath",
        "str",
        "Path",
        "PurePath",
        "WindowsPath",
        "PosixPath",
        "PureWindowsPath",
        "PurePosixPath",
    }
)
_RECEIVER_CALLS = frozenset(
    {"joinpath", "resolve", "absolute", "as_posix", "expanduser"}
)
_HOME_CALLS = frozenset({"home", "homedir"})


@dataclass(frozen=True)
class ProtectedMention:
    """One string in some code that names a protected path, and why."""

    text: str
    reason: str


# --------------------------------------------------------------------------- judging


def _segments(text: str) -> List[str]:
    return [s for s in re.split(r"[\\/]+", text) if s]


def _credential_folder_names() -> Set[str]:
    """``.ssh``, ``.aws``…: the last segment of each credential folder."""
    from gaia import security

    names = set()
    for folder in security.SECRET_DIRECTORIES:
        leaf = os.path.basename(os.path.normpath(folder)).lower()
        if leaf.startswith("."):
            names.add(leaf)
    return names


def _name_reason(text: str) -> str:
    """Why *text*, read as a path, names a credential wherever it is, or ""."""
    from gaia import security

    segments = [s.lower().rstrip(" .") or s.lower() for s in _segments(text)]
    if not segments:
        return ""
    folders = _credential_folder_names()
    for segment in segments:
        if segment in folders:
            return f"'{segment}' is a folder of credentials"
    leaf = segments[-1]
    # A lone word like 'passwd' is as likely a dict key as a file name.
    if len(segments) == 1 and not re.search(r"[._]", leaf):
        return ""
    if leaf in {name.lower() for name in security.SENSITIVE_FILE_NAMES}:
        return f"'{leaf}' holds credentials, keys or secrets"
    if os.path.splitext(leaf)[1] in security.SENSITIVE_EXTENSIONS:
        return f"'{leaf}' is a certificate or private key"
    return ""


def _with_home(text: str) -> str:
    home = str(Path.home())
    for spelling in _HOME_SPELLINGS:
        text = re.sub(re.escape(spelling), lambda _m: home, text, flags=re.IGNORECASE)
    return text


def _in_users_files(real: str) -> bool:
    """Under the home folder or GAIA's settings, where the user's own account writes.

    System folders are left to the operating system, which refuses an ordinary
    user's write there; a snippet naming ``C:\\Program Files\\Git`` is routine.
    """
    from gaia import security

    roots = [str(Path.home()), *security.GAIA_STATE_DIRECTORIES]
    return any(security._path_is_within(Path(real), Path(root)) for root in roots)


def _path_reason(text: str, cwd: str, validator: Any, may_write: bool) -> str:
    """Why the file tools would refuse the path *text*, or ""."""
    if not text or len(text) > _MAX_PATH_TEXT or "\n" in text or "\x00" in text:
        return ""
    if text.lower().startswith("file:"):
        root = "" if os.name == "nt" else "/"
        text = unquote(re.sub(r"^file:/*", root, text, flags=re.IGNORECASE))
    elif "://" in text:
        return ""  # a web address; reaching it is the network rules' business
    text = _with_home(text)
    reason = _name_reason(text)
    if reason or validator is None:
        return reason
    if not (
        "/" in text
        or "\\" in text
        or text.startswith("~")
        or re.match(r"^[A-Za-z]:", text)
    ):
        return ""
    try:
        real = canonical_path(join_under(cwd, text), for_comparison=True)
    except (OSError, ValueError) as e:
        # UncheckablePathError included: a share or device spelling is judged by
        # its names above; a regex like '\\\\d' is not a path at all.
        log.debug("code screen: not judging %r as a path: %s", text, e)
        return ""
    blocked, why = validator.is_read_blocked(real)
    if blocked:
        return why
    if may_write and _in_users_files(real):
        blocked, why = validator.is_write_blocked(real)
        if blocked:
            return why
    return ""


# --------------------------------------------------------------------------- python


def _product(parts: List[List[str]], sep: str) -> List[str]:
    """Every way the alternatives in *parts* join, one from each, with *sep* between."""
    combos = [""]
    for index, options in enumerate(parts):
        options = options or [""]
        combos = [
            (f"{c}{sep}{o}" if index and sep and c and o else c + o)
            for c in combos
            for o in options
        ][:_MAX_VALUES]
    return combos


class _Folder(ast.NodeVisitor):
    """The strings Python code holds or plainly builds.

    Names keep every value ever assigned to them, in source order, so ``p +=``
    after ``p =`` builds on it; anything not plain to see folds to nothing.
    """

    def __init__(self) -> None:
        self.names: Dict[str, List[str]] = {}
        # Names bound to a list or tuple literal, element by element, for a join.
        self.sequences: Dict[str, List[ast.AST]] = {}

    # -- binding, in source order

    def _bind(self, target: ast.AST, values: List[str]) -> None:
        if isinstance(target, ast.Name) and values:
            merged = self.names.get(target.id, []) + values
            self.names[target.id] = list(dict.fromkeys(merged))[:_MAX_VALUES]

    def visit_Assign(self, node: ast.Assign) -> None:
        values = self.values(node.value)
        for target in node.targets:
            self._bind(target, values)
            if isinstance(target, ast.Name) and isinstance(
                node.value, (ast.List, ast.Tuple)
            ):
                self.sequences[target.id] = list(node.value.elts)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._bind(node.target, self.values(node.value))
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.op, ast.Add) and isinstance(node.target, ast.Name):
            before = self.names.get(node.target.id, [""])
            self._bind(node.target, _product([before, self.values(node.value)], ""))
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._bind(node.target, self.values(node.value))
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._bind(node.target, self.values(node.iter))
        self.generic_visit(node)

    # -- folding

    def values(self, node: ast.AST, depth: int = 0) -> List[str]:
        if depth > 16:
            return []
        found = self._values(node, depth + 1)
        return list(dict.fromkeys(found))[:_MAX_VALUES]

    def _values(self, node: ast.AST, d: int) -> List[str]:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                return [node.value]
            if isinstance(node.value, bytes):
                return [node.value.decode("utf-8", "replace")]
            return []
        if isinstance(node, ast.Name):
            return list(self.names.get(node.id, []))
        if isinstance(node, ast.JoinedStr):
            return _product(
                [
                    (
                        [part.value]
                        if isinstance(part, ast.Constant)
                        else self.values(part.value, d)
                    )
                    for part in node.values
                ],
                "",
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
            sep = os.sep if isinstance(node.op, ast.Div) else ""
            return _product(
                [self.values(node.left, d), self.values(node.right, d)], sep
            )
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            found: List[str] = []
            for element in node.elts:
                found.extend(self.values(element, d))
            return found
        if isinstance(node, ast.Subscript):
            return self._environ(node)
        if isinstance(node, ast.Call):
            return self._call(node, d)
        return []

    def _call(self, node: ast.Call, d: int) -> List[str]:
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        args = [a for a in node.args if not isinstance(a, ast.Starred)]
        if name in _HOME_CALLS:
            return [str(Path.home())]
        if name in ("getenv", "get") and args:
            if any(k.upper() in _HOME_ENV for k in self.values(args[0], d)):
                return [str(Path.home())]
            return []
        if (
            name == "join"
            and isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Constant)
            and isinstance(func.value.value, str)
        ):
            pieces = args
            if len(args) == 1 and isinstance(args[0], (ast.List, ast.Tuple)):
                pieces = list(args[0].elts)
            elif len(args) == 1 and isinstance(args[0], ast.Name):
                pieces = self.sequences.get(args[0].id, pieces)
            return _product([self.values(p, d) for p in pieces], func.value.value)
        if name not in _PATH_CALLS:
            return []
        parts: List[List[str]] = []
        if isinstance(func, ast.Attribute) and name in _RECEIVER_CALLS:
            parts.append(self.values(func.value, d))
        parts.extend(self.values(a, d) for a in args)
        parts = [p for p in parts if p]
        return _product(parts, os.sep) if parts else []

    def _environ(self, node: ast.Subscript) -> List[str]:
        owner, key = node.value, node.slice
        if (
            isinstance(owner, ast.Attribute)
            and owner.attr == "environ"
            and isinstance(key, ast.Constant)
            and isinstance(key.value, str)
            and key.value.upper() in _HOME_ENV
        ):
            return [str(Path.home())]
        return []


def _python_strings(code: str) -> Optional[List[str]]:
    """Every string *code* holds or plainly builds; None when it is not Python."""
    if len(code) > _MAX_FOLD_BYTES:
        return None
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    folder = _Folder()
    folder.visit(tree)
    found: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.expr) and not isinstance(node, ast.Name):
            found.extend(folder.values(node))
    for values in folder.names.values():
        found.extend(values)
    return list(dict.fromkeys(found))


# --------------------------------------------------------------------------- text


_QUOTED = re.compile(r"'([^'\n]*)'|\"([^\"\n]*)\"|`([^`\n]*)`")
_WORD_BREAKS = re.compile(r"[\s|;&<>(){}\[\],=+]+")


def _text_strings(code: str) -> List[str]:
    """The quoted strings and bare words of code GAIA does not parse."""
    found = [
        next(g for g in m.groups() if g is not None) for m in _QUOTED.finditer(code)
    ]
    found.extend(word.strip("'\"`") for word in _WORD_BREAKS.split(code))
    return [s for s in dict.fromkeys(found) if s]


# --------------------------------------------------------------------------- api


def protected_paths_in_code(
    code: str,
    *,
    python: bool,
    cwd: str,
    validator: Any = None,
    may_write: bool = True,
) -> List[ProtectedMention]:
    """The protected paths *code* names, judged as the file tools judge them.

    Args:
        code: The source text.
        python: Fold it as Python; otherwise (or when it does not parse) read
            its quoted strings and words.
        cwd: Where it runs, for relative paths.
        validator: The agent's ``PathValidator``; without one only names are
            judged (``.ssh``, ``.env``, ``*.pem``…).
        may_write: Whether the code could write, so the write-only protections
            (GAIA's settings, login scripts) apply too.

    Returns:
        One mention per offending string, in the order found.
    """
    if not callable(getattr(validator, "is_read_blocked", None)):
        validator = None
    strings = _python_strings(code) if python else None
    if strings is None:
        strings = _text_strings(code)
    mentions: List[ProtectedMention] = []
    for text in strings:
        reason = _path_reason(text, cwd, validator, may_write)
        if reason:
            mentions.append(ProtectedMention(text=text, reason=reason))
    return mentions


def code_refusal(what: str, mentions: Iterable[ProtectedMention]) -> Dict[str, Any]:
    """The hard refusal for code that names a protected path."""
    from gaia.agents.base.verification import NOT_EXECUTED

    first = next(iter(mentions))
    return {
        **NOT_EXECUTED,
        "status": "error",
        "tier": "refuse",
        "has_errors": True,
        "error": (
            f"Not run: {what} names '{first.text}', and {first.reason.rstrip('.')}. "
            "No approval can grant it: code may not read or write a protected "
            "folder or credential file, just as the file tools may not. Do not "
            "copy, rename or rebuild the path to get around this; tell the user "
            "to open it themselves if they need it."
        ),
    }


def _host_cwd(host: Any) -> str:
    config = getattr(host, "config", None)
    workspace = getattr(config, "working_directory", None) or getattr(
        host, "working_directory", None
    )
    return str(workspace or os.getcwd())


def python_code_refusal(
    host: Any, code: Any, may_write: Optional[Callable[[str], bool]] = None
) -> Optional[Dict[str, Any]]:
    """The refusal *code* has earned before ``run_python`` runs it, or None."""
    if not isinstance(code, str) or not code.strip():
        return None
    mentions = protected_paths_in_code(
        code,
        python=True,
        cwd=_host_cwd(host),
        validator=getattr(host, "path_validator", None),
        may_write=True if may_write is None else bool(may_write(code)),
    )
    if not mentions:
        return None
    log.info(
        "Refused a Python snippet naming %r: %s", mentions[0].text, mentions[0].reason
    )
    return code_refusal("this Python snippet", mentions)


def python_code_preflight(
    host: Any, may_write: Optional[Callable[[str], bool]] = None
) -> Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """A ``@tool(preflight=...)`` check for a tool whose ``code`` argument is Python."""

    def preflight(args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return python_code_refusal(host, args.get("code"), may_write)

    return preflight


def python_file_refusal(
    host: Any,
    file_path: Any,
    may_write: Optional[Callable[[str], bool]] = None,
    script_args: Any = "",
) -> Optional[Dict[str, Any]]:
    """The refusal a script file (and the arguments it is handed) has earned, or None.

    A script that cannot be read is left to the tool, which reports it.
    """
    if not isinstance(file_path, str) or not file_path.strip():
        return None
    cwd = _host_cwd(host)
    validator = getattr(host, "path_validator", None)
    if isinstance(script_args, str) and script_args.strip():
        mentions = protected_paths_in_code(
            script_args, python=False, cwd=cwd, validator=validator
        )
        if mentions:
            return code_refusal("the script's arguments", mentions)
    try:
        path = Path(canonical_path(join_under(cwd, os.path.expanduser(file_path))))
        with open(path, "rb") as handle:
            source = handle.read(_MAX_FOLD_BYTES + 1).decode("utf-8", "replace")
    except (OSError, ValueError) as e:
        log.debug("code screen: not reading %s: %s", file_path, e)
        return None
    mentions = protected_paths_in_code(
        source,
        python=True,
        cwd=str(path.parent),
        validator=validator,
        may_write=True if may_write is None else bool(may_write(source)),
    )
    if not mentions:
        return None
    log.info(
        "Refused script %s naming %r: %s", path, mentions[0].text, mentions[0].reason
    )
    return code_refusal(f"the script {path.name}", mentions)


def python_file_preflight(
    host: Any, may_write: Optional[Callable[[str], bool]] = None
) -> Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """A ``@tool(preflight=...)`` check for a tool that runs a Python file."""

    def preflight(args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return python_file_refusal(
            host, args.get("file_path"), may_write, args.get("args", "")
        )

    return preflight
