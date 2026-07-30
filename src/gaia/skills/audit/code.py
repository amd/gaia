# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
AST analysis of a skill's Python code.

Analysis is AST-based, not regex-based, for a reason the gate's credibility
depends on: a regex over Python source cannot tell ``os.system(cmd)`` from
``"os.system"`` in a docstring, and cannot follow ``from os import system as
sh``. False positives train authors to ignore the gate.

The analyzer produces **two separate things**:

- :attr:`CodeAnalysis.findings` — constructs that are dangerous no matter what
  the manifest says (``eval``, ``shell=True``, credential harvesting,
  obfuscated payloads).
- :attr:`CodeAnalysis.domain_uses` — the factual record of which permission
  domains the code touches, which :mod:`gaia.skills.audit.engine` diffs against
  ``metadata.gaia.permissions``.

That split is what keeps the gate quiet about honest skills: a skill declaring
``network:read`` and calling ``requests.get`` gets a domain use and no finding.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from gaia.logger import get_logger
from gaia.skills.audit.findings import (
    CATEGORY_DANGEROUS_CALL,
    Finding,
    Severity,
    python_sources,
    relative_path,
)

log = get_logger(__name__)


@dataclass(frozen=True)
class DomainUse:
    """One observed touch of a permission domain."""

    domain: str
    level: str
    file: str
    line: int
    #: The resolved call or construct, e.g. ``subprocess.run``.
    detail: str
    #: A literal path/host argument when one was resolvable, else ``None``.
    literal_target: Optional[str] = None


@dataclass(frozen=True)
class ImportRef:
    """A top-level module name imported by the skill's code."""

    module: str
    file: str
    line: int


@dataclass
class CodeAnalysis:
    """Everything the code analyzer learned about a skill."""

    findings: tuple[Finding, ...] = ()
    domain_uses: tuple[DomainUse, ...] = ()
    imports: tuple[ImportRef, ...] = ()

    def domains(self) -> set[str]:
        """The distinct domains the code touches."""
        return {use.domain for use in self.domain_uses}

    def levels_for(self, domain: str) -> set[str]:
        """The distinct levels observed for one domain."""
        return {use.level for use in self.domain_uses if use.domain == domain}

    def merge(self, other: "CodeAnalysis") -> "CodeAnalysis":
        """Combine two analyses (used to fold per-file results together)."""
        return CodeAnalysis(
            findings=self.findings + other.findings,
            domain_uses=self.domain_uses + other.domain_uses,
            imports=self.imports + other.imports,
        )


# ----------------------------------------------------------------------
# Sink tables
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Sink:
    """A call whose presence means something for the audit."""

    #: Permission domain touched, or ``None`` for a pure danger signal.
    domain: Optional[str] = None
    level: str = ""
    #: Emit an intrinsic finding with this rule id (``None`` = record only).
    rule_id: Optional[str] = None
    severity: Severity = "medium"
    message: str = ""
    remediation: str = ""


_SHELL_REMEDIATION = (
    "Skills run inside the agent's process; shelling out escapes every "
    "permission the manifest declares. Use an in-process library, or declare "
    "'shell:execute' and expect a maintainer review."
)

#: Exact dotted-name sinks.
SINKS: dict[str, Sink] = {
    # --- shell ---------------------------------------------------------
    "subprocess.run": Sink(
        "shell", "execute", "code.shell.subprocess", "high",
        "Spawns a subprocess.", _SHELL_REMEDIATION,
    ),
    "subprocess.call": Sink(
        "shell", "execute", "code.shell.subprocess", "high",
        "Spawns a subprocess.", _SHELL_REMEDIATION,
    ),
    "subprocess.check_call": Sink(
        "shell", "execute", "code.shell.subprocess", "high",
        "Spawns a subprocess.", _SHELL_REMEDIATION,
    ),
    "subprocess.check_output": Sink(
        "shell", "execute", "code.shell.subprocess", "high",
        "Spawns a subprocess.", _SHELL_REMEDIATION,
    ),
    "subprocess.Popen": Sink(
        "shell", "execute", "code.shell.subprocess", "high",
        "Spawns a subprocess.", _SHELL_REMEDIATION,
    ),
    "subprocess.getoutput": Sink(
        "shell", "execute", "code.shell.subprocess", "high",
        "Runs a shell command and captures its output.", _SHELL_REMEDIATION,
    ),
    "subprocess.getstatusoutput": Sink(
        "shell", "execute", "code.shell.subprocess", "high",
        "Runs a shell command and captures its output.", _SHELL_REMEDIATION,
    ),
    "os.system": Sink(
        "shell", "execute", "code.shell.os_system", "high",
        "Executes a command through the system shell.", _SHELL_REMEDIATION,
    ),
    "os.popen": Sink(
        "shell", "execute", "code.shell.os_system", "high",
        "Executes a command through the system shell.", _SHELL_REMEDIATION,
    ),
    "pty.spawn": Sink(
        "shell", "execute", "code.shell.os_system", "high",
        "Spawns a pseudo-terminal process.", _SHELL_REMEDIATION,
    ),
    "asyncio.create_subprocess_shell": Sink(
        "shell", "execute", "code.shell.subprocess", "high",
        "Spawns a subprocess through the shell.", _SHELL_REMEDIATION,
    ),
    "asyncio.create_subprocess_exec": Sink(
        "shell", "execute", "code.shell.subprocess", "high",
        "Spawns a subprocess.", _SHELL_REMEDIATION,
    ),
    # --- dynamic code execution ---------------------------------------
    "eval": Sink(
        None, "", "code.exec.eval", "critical",
        "Evaluates a string as Python code.",
        "Remove it. Any value reaching eval() — a tool argument, a fetched "
        "page, a model-authored string — becomes arbitrary code. Parse the "
        "input explicitly instead (ast.literal_eval for data, json.loads for JSON).",
    ),
    "exec": Sink(
        None, "", "code.exec.exec", "critical",
        "Executes a string as Python code.",
        "Remove it. Skills are distributed artifacts; exec() makes the code "
        "that actually runs unreviewable.",
    ),
    "compile": Sink(
        None, "", "code.exec.compile", "high",
        "Compiles a string into executable code.",
        "Remove it — compiled code is invisible to this audit.",
    ),
    "__import__": Sink(
        None, "", "code.exec.dynamic_import", "high",
        "Imports a module chosen at runtime.",
        "Import the modules you need at the top of the file so the audit and "
        "the reader can both see them.",
    ),
    "importlib.import_module": Sink(
        None, "", "code.exec.dynamic_import", "high",
        "Imports a module chosen at runtime.",
        "Import the modules you need at the top of the file so the audit and "
        "the reader can both see them.",
    ),
    "importlib.__import__": Sink(
        None, "", "code.exec.dynamic_import", "high",
        "Imports a module chosen at runtime.",
        "Import the modules you need at the top of the file.",
    ),
    "pickle.loads": Sink(
        None, "", "code.deserialization.pickle", "high",
        "Deserializes pickled data, which can execute arbitrary code.",
        "Use json for data interchange. Unpickling untrusted bytes is "
        "equivalent to running them.",
    ),
    "pickle.load": Sink(
        None, "", "code.deserialization.pickle", "high",
        "Deserializes pickled data, which can execute arbitrary code.",
        "Use json for data interchange.",
    ),
    "dill.loads": Sink(
        None, "", "code.deserialization.pickle", "high",
        "Deserializes pickled data, which can execute arbitrary code.",
        "Use json for data interchange.",
    ),
    "marshal.loads": Sink(
        None, "", "code.deserialization.pickle", "high",
        "Deserializes marshalled code objects.",
        "Use json for data interchange.",
    ),
    "yaml.load": Sink(
        None, "", "code.deserialization.yaml", "medium",
        "yaml.load can construct arbitrary Python objects.",
        "Use yaml.safe_load().",
    ),
    # --- network -------------------------------------------------------
    "socket.socket": Sink(
        "network", "write", "code.network.raw_socket", "medium",
        "Opens a raw socket.",
        "Use a high-level HTTP client so the declared network scope is "
        "meaningful; a raw socket can reach any host and port.",
    ),
    "socket.create_connection": Sink(
        "network", "write", "code.network.raw_socket", "medium",
        "Opens a raw socket connection.",
        "Use a high-level HTTP client so the declared network scope is meaningful.",
    ),
    "urllib.request.urlopen": Sink("network", "read"),
    "urllib.request.urlretrieve": Sink("network", "read"),
    "http.client.HTTPConnection": Sink("network", "write"),
    "http.client.HTTPSConnection": Sink("network", "write"),
    "ftplib.FTP": Sink("network", "write"),
    "smtplib.SMTP": Sink("network", "write"),
    "telnetlib.Telnet": Sink("network", "write"),
    "webbrowser.open": Sink("network", "read"),
    # --- filesystem ----------------------------------------------------
    "open": Sink("filesystem", "read"),  # level refined from the mode argument
    "io.open": Sink("filesystem", "read"),
    "os.remove": Sink("filesystem", "write"),
    "os.unlink": Sink("filesystem", "write"),
    "os.rename": Sink("filesystem", "write"),
    "os.replace": Sink("filesystem", "write"),
    "os.mkdir": Sink("filesystem", "write"),
    "os.makedirs": Sink("filesystem", "write"),
    "os.rmdir": Sink("filesystem", "write"),
    "os.truncate": Sink("filesystem", "write"),
    "os.chmod": Sink("filesystem", "write"),
    "os.chown": Sink("filesystem", "write"),
    "os.symlink": Sink("filesystem", "write"),
    "os.link": Sink("filesystem", "write"),
    "os.listdir": Sink("filesystem", "read"),
    "os.walk": Sink("filesystem", "read"),
    "os.scandir": Sink("filesystem", "read"),
    "os.stat": Sink("filesystem", "read"),
    "shutil.copy": Sink("filesystem", "write"),
    "shutil.copy2": Sink("filesystem", "write"),
    "shutil.copyfile": Sink("filesystem", "write"),
    "shutil.copytree": Sink("filesystem", "write"),
    "shutil.move": Sink("filesystem", "write"),
    "shutil.unpack_archive": Sink("filesystem", "write"),
    "shutil.rmtree": Sink(
        "filesystem", "write", "code.filesystem.destructive", "high",
        "Recursively deletes a directory tree.",
        "Narrow the deletion to specific files you created, or drop it. A "
        "recursive delete driven by a tool argument can erase a user's data.",
    ),
    # --- environment ---------------------------------------------------
    "os.getenv": Sink("env", "read"),
    # --- database ------------------------------------------------------
    "sqlite3.connect": Sink("database", "write"),
    "psycopg2.connect": Sink("database", "write"),
    "pymysql.connect": Sink("database", "write"),
    "sqlalchemy.create_engine": Sink("database", "write"),
    "pymongo.MongoClient": Sink("database", "write"),
    "redis.Redis": Sink("database", "write"),
    # --- native code ---------------------------------------------------
    "ctypes.CDLL": Sink(
        None, "", "code.native.ctypes", "high",
        "Loads a native shared library.",
        "Native code runs outside every Python-level check this audit can "
        "make. Remove it, or ship the capability as a reviewed agent tool.",
    ),
    "ctypes.WinDLL": Sink(
        None, "", "code.native.ctypes", "high",
        "Loads a native shared library.",
        "Native code runs outside every Python-level check this audit can make.",
    ),
    "ctypes.windll.LoadLibrary": Sink(
        None, "", "code.native.ctypes", "high",
        "Loads a native shared library.",
        "Native code runs outside every Python-level check this audit can make.",
    ),
}

#: Module-prefix sinks, applied when no exact dotted name matched. Keyed by the
#: first dotted component so a whole third-party family is covered.
MODULE_PREFIX_SINKS: dict[str, Sink] = {
    "requests": Sink("network", "read"),
    "httpx": Sink("network", "read"),
    "aiohttp": Sink("network", "write"),
    "urllib3": Sink("network", "read"),
    "websockets": Sink("network", "write"),
    "websocket": Sink("network", "write"),
    "paramiko": Sink(
        "network", "write", "code.network.remote_shell", "high",
        "Opens an SSH connection, which can run commands on another host.",
        "Remove it. Remote command execution from a marketplace skill is "
        "outside what any declared permission can bound.",
    ),
    "fabric": Sink(
        "network", "write", "code.network.remote_shell", "high",
        "Opens an SSH connection, which can run commands on another host.",
        "Remove it — remote command execution cannot be bounded by the "
        "permission grammar.",
    ),
    "pyautogui": Sink(
        "desktop", "control", "code.desktop.control", "high",
        "Controls the mouse/keyboard or captures the screen.",
        "Desktop control can read anything on screen and act as the user. "
        "Declare 'desktop:control' and expect a maintainer review, or drop it.",
    ),
    "pynput": Sink(
        "desktop", "control", "code.desktop.control", "high",
        "Listens to or synthesizes keyboard/mouse input.",
        "Input capture is indistinguishable from a keylogger. Remove it.",
    ),
    "mss": Sink(
        "desktop", "control", "code.desktop.control", "high",
        "Captures the screen.",
        "Declare 'desktop:control' and expect a maintainer review, or drop it.",
    ),
    "keyboard": Sink(
        "desktop", "control", "code.desktop.control", "high",
        "Listens to or synthesizes keyboard input.",
        "Input capture is indistinguishable from a keylogger. Remove it.",
    ),
}

#: Bare method-name sinks, for calls on values the AST cannot resolve
#: (``Path(p).write_text(...)``). Curated to names that are distinctive enough
#: not to collide with ordinary objects — ``.read()``/``.write()``/``.replace()``
#: are deliberately absent.
METHOD_SINKS: dict[str, Sink] = {
    "write_text": Sink("filesystem", "write"),
    "write_bytes": Sink("filesystem", "write"),
    "read_text": Sink("filesystem", "read"),
    "read_bytes": Sink("filesystem", "read"),
    "iterdir": Sink("filesystem", "read"),
    "unlink": Sink("filesystem", "write"),
    "rmdir": Sink("filesystem", "write"),
    "mkdir": Sink("filesystem", "write"),
    "touch": Sink("filesystem", "write"),
    "chmod": Sink("filesystem", "write"),
    "symlink_to": Sink("filesystem", "write"),
    "hardlink_to": Sink("filesystem", "write"),
}

#: Names that are network *writes* when they appear as the final attribute.
_NETWORK_WRITE_VERBS = frozenset(
    {
        "post", "put", "patch", "delete", "send", "sendall", "sendto",
        "sendmail", "upload", "request", "stream", "Session", "ClientSession",
        "Client", "connect",
    }
)

#: Builtin sinks — matched by bare name only when not shadowed locally.
_BUILTIN_SINKS = frozenset({"eval", "exec", "compile", "__import__", "open"})

#: Paths whose mere mention means credential access.
_CREDENTIAL_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\.ssh/", "an SSH key directory"),
    (r"id_rsa|id_ed25519|id_ecdsa|id_dsa", "a private SSH key"),
    (r"\.aws/credentials|\.aws/config", "AWS credentials"),
    (r"\.netrc", "a .netrc credentials file"),
    (r"\.docker/config\.json", "Docker registry credentials"),
    (r"\.kube/config", "a Kubernetes credential file"),
    (r"\.gnupg", "a GnuPG keyring"),
    (r"\.git-credentials", "stored Git credentials"),
    (r"\.npmrc|\.pypirc", "a package-registry token file"),
    (r"/etc/(shadow|passwd)", "the system account database"),
    (r"\.config/gh/hosts\.yml", "GitHub CLI credentials"),
    (r"(^|/)\.env$", "a .env secrets file"),
    (r"Login Data|Cookies|key4\.db|logins\.json", "browser-stored credentials"),
)

_SUPPRESSION_RE = re.compile(r"#\s*(noqa|nosec|type:\s*ignore)\b", re.IGNORECASE)
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/=\s]+$")
_HEX_RE = re.compile(r"^[0-9a-fA-F\s]+$")
_BLOB_MIN_LENGTH = 200

#: Decoders whose output, fed to exec/eval, is an obfuscated payload.
_DECODERS = frozenset(
    {
        "base64.b64decode", "base64.b64encode", "base64.b85decode",
        "base64.a85decode", "base64.b32decode", "base64.b16decode",
        "base64.urlsafe_b64decode", "codecs.decode", "bytes.fromhex",
        "zlib.decompress", "gzip.decompress", "bz2.decompress",
        "lzma.decompress", "binascii.unhexlify", "binascii.a2b_base64",
    }
)


# ----------------------------------------------------------------------
# The visitor
# ----------------------------------------------------------------------


class _Analyzer(ast.NodeVisitor):
    """Resolves call targets through import aliases and matches them to sinks."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        #: local symbol -> dotted origin (``sh`` -> ``os.system``)
        self.aliases: dict[str, str] = {}
        #: names bound locally, so a shadowed builtin is not a sink
        self.shadowed: set[str] = set()
        self.findings: list[Finding] = []
        self.domain_uses: list[DomainUse] = []
        self.imports: list[ImportRef] = []

    # -- import tracking ------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.asname:
                self.aliases[alias.asname] = alias.name
            else:
                # ``import a.b`` binds ``a``.
                self.aliases[alias.name.split(".")[0]] = alias.name.split(".")[0]
            self.imports.append(
                ImportRef(alias.name.split(".")[0], self.filename, node.lineno)
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:  # relative import — a module inside the skill
            self.generic_visit(node)
            return
        module = node.module or ""
        for alias in node.names:
            local = alias.asname or alias.name
            self.aliases[local] = f"{module}.{alias.name}" if module else alias.name
        if module:
            self.imports.append(
                ImportRef(module.split(".")[0], self.filename, node.lineno)
            )
        self.generic_visit(node)

    # -- shadow tracking ------------------------------------------------

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.shadowed.add(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.shadowed.add(node.name)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.shadowed.add(node.name)
        self.generic_visit(node)

    # -- the interesting bit --------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        dotted = self._resolve(node.func)
        if dotted is not None:
            self._match_sink(node, dotted)
        else:
            self._match_method(node)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id in {"builtins", "__builtins__"}:
            self._add_finding(
                "code.exec.builtins_access",
                "high",
                "Reaches into the builtins namespace, which can retrieve "
                "eval/exec/__import__ by a computed name.",
                node.lineno,
                "Call the functions you need directly so the audit can see them.",
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        dotted = self._resolve(node)
        if dotted == "os.environ":
            self._handle_environ(node)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self._check_string_literal(node.value, node.lineno)
        self.generic_visit(node)

    # -- resolution -----------------------------------------------------

    def _resolve(self, node: ast.AST) -> Optional[str]:
        """Resolve a Name/Attribute chain to a dotted name via import aliases."""
        parts: list[str] = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if not isinstance(current, ast.Name):
            return None
        parts.append(current.id)
        parts.reverse()

        base = parts[0]
        if base in self.aliases:
            resolved = self.aliases[base]
            return ".".join([resolved] + parts[1:])
        if len(parts) == 1 and base in _BUILTIN_SINKS and base not in self.shadowed:
            return base
        return ".".join(parts) if len(parts) > 1 else None

    def _match_sink(self, node: ast.Call, dotted: str) -> None:
        sink = SINKS.get(dotted)
        if sink is None:
            sink = self._prefix_sink(dotted)
        if sink is None:
            self._check_obfuscated_exec(node, dotted)
            return

        level = sink.level
        literal_target = _first_string_arg(node)

        if dotted in ("open", "io.open"):
            level = _open_mode_level(node)
        elif sink.domain == "network":
            level = _network_level(dotted, sink.level)

        if sink.domain:
            self.domain_uses.append(
                DomainUse(
                    domain=sink.domain,
                    level=level,
                    file=self.filename,
                    line=node.lineno,
                    detail=dotted,
                    literal_target=literal_target,
                )
            )

        if sink.rule_id:
            self._add_finding(
                sink.rule_id, sink.severity, sink.message, node.lineno, sink.remediation
            )

        if _has_shell_true(node):
            self._add_finding(
                "code.shell.injection",
                "critical",
                f"{dotted} is called with shell=True, so its argument is "
                "interpreted by the shell.",
                node.lineno,
                "Pass the command as a list and leave shell=False. With "
                "shell=True any value that reaches the command string — a tool "
                "argument or model output — becomes shell code.",
            )

        self._check_obfuscated_exec(node, dotted)

    def _prefix_sink(self, dotted: str) -> Optional[Sink]:
        return MODULE_PREFIX_SINKS.get(dotted.split(".")[0])

    def _match_method(self, node: ast.Call) -> None:
        """Match a call on an unresolvable value by its method name."""
        if not isinstance(node.func, ast.Attribute):
            return
        sink = METHOD_SINKS.get(node.func.attr)
        if sink is None or not sink.domain:
            return
        self.domain_uses.append(
            DomainUse(
                domain=sink.domain,
                level=sink.level,
                file=self.filename,
                line=node.lineno,
                detail=f".{node.func.attr}()",
                literal_target=_first_string_arg(node),
            )
        )

    def _check_obfuscated_exec(self, node: ast.Call, dotted: str) -> None:
        """Flag ``exec(b64decode(...))`` — a payload hidden from review."""
        if dotted not in ("exec", "eval", "compile"):
            return
        for descendant in ast.walk(node):
            if descendant is node or not isinstance(descendant, ast.Call):
                continue
            inner = self._resolve(descendant.func)
            if inner and (inner in _DECODERS or inner.split(".")[-1] == "fromhex"):
                self._add_finding(
                    "code.obfuscation.encoded_exec",
                    "critical",
                    f"Executes the output of {inner} — an encoded payload that "
                    "no reviewer or scanner can read.",
                    node.lineno,
                    "Remove it. Encoding code to hide it from review is not a "
                    "pattern any legitimate skill needs.",
                )
                return

    def _handle_environ(self, node: ast.Attribute) -> None:
        """Distinguish a named lookup from harvesting the whole environment."""
        parent = getattr(node, "parent", None)
        if isinstance(parent, ast.Subscript):
            self._add_env_read(node.lineno, "os.environ[...]")
            return
        if isinstance(parent, ast.Attribute):
            if parent.attr in ("get", "setdefault"):
                self._add_env_read(node.lineno, f"os.environ.{parent.attr}()")
                return
            # .items()/.copy()/.keys()/.values() → the whole environment
        self._add_finding(
            "code.env.bulk_read",
            "high",
            "Reads the entire process environment rather than the specific "
            "variables the skill needs.",
            node.lineno,
            "Read the variables you declared, by name: "
            "os.getenv('MY_API_KEY'). A bulk read collects every secret the "
            "host process holds, including ones unrelated to this skill.",
        )
        self._add_env_read(node.lineno, "os.environ", bulk=True)

    def _add_env_read(self, line: int, detail: str, *, bulk: bool = False) -> None:
        self.domain_uses.append(
            DomainUse(
                domain="env",
                level="read",
                file=self.filename,
                line=line,
                detail=detail,
                literal_target="*" if bulk else None,
            )
        )

    def _check_string_literal(self, value: str, line: int) -> None:
        for pattern, description in _CREDENTIAL_PATTERNS:
            if re.search(pattern, value):
                self._add_finding(
                    "code.credentials.file_access",
                    "high",
                    f"References {description} ({value!r}).",
                    line,
                    "A skill has no legitimate reason to read the user's "
                    "credentials. Take the value it needs as a declared "
                    "env_vars entry or a connector grant instead.",
                    snippet=value[:200],
                )
                return

        # Encoded payloads are unbroken tokens (possibly wrapped across lines);
        # prose of the same length is not. Requiring no intra-line spaces keeps
        # a long docstring out of this rule even when it happens to use only
        # base64-legal characters.
        compact = "".join(value.split())
        if (
            len(compact) >= _BLOB_MIN_LENGTH
            and " " not in value
            and (_BASE64_RE.match(compact) or _HEX_RE.match(compact))
        ):
            self._add_finding(
                "code.obfuscation.blob",
                "medium",
                f"Contains a {len(compact)}-character encoded literal.",
                line,
                "If this is data, load it from a file so a reviewer can read "
                "it. If it is code, it must not be encoded.",
                snippet=compact[:120],
            )

    def _add_finding(
        self,
        rule_id: str,
        severity: Severity,
        message: str,
        line: int,
        remediation: str,
        *,
        snippet: Optional[str] = None,
    ) -> None:
        self.findings.append(
            Finding(
                rule_id=rule_id,
                severity=severity,
                category=CATEGORY_DANGEROUS_CALL,
                message=message,
                file=self.filename,
                line=line,
                remediation=remediation,
                snippet=snippet,
            )
        )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _has_shell_true(node: ast.Call) -> bool:
    for keyword in node.keywords:
        if keyword.arg == "shell":
            value = keyword.value
            if isinstance(value, ast.Constant):
                return bool(value.value)
            # A computed shell= value cannot be ruled out.
            return True
    return False


def _open_mode_level(node: ast.Call) -> str:
    """Return ``write`` when an ``open()`` call can modify the file."""
    mode: Optional[ast.expr] = None
    if len(node.args) >= 2:
        mode = node.args[1]
    for keyword in node.keywords:
        if keyword.arg == "mode":
            mode = keyword.value
    if mode is None:
        return "read"
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return "write" if any(c in mode.value for c in "wax+") else "read"
    return "write"  # computed mode — assume the stronger level


def _network_level(dotted: str, default: str) -> str:
    """Infer read vs write from the final attribute of a network call."""
    return "write" if dotted.split(".")[-1] in _NETWORK_WRITE_VERBS else default


def _first_string_arg(node: ast.Call) -> Optional[str]:
    for arg in node.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


def _attach_parents(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node  # type: ignore[attr-defined]


def _suppression_findings(source: str, filename: str) -> list[Finding]:
    """One finding per suppression comment — never trusted, always re-verified."""
    findings: list[Finding] = []
    for number, line in enumerate(source.splitlines(), start=1):
        if _SUPPRESSION_RE.search(line):
            findings.append(
                Finding(
                    rule_id="code.suppression",
                    severity="info",
                    category=CATEGORY_DANGEROUS_CALL,
                    message="Carries a linter/scanner suppression comment.",
                    file=filename,
                    line=number,
                    remediation="This audit re-verifies suppressed lines from "
                    "scratch rather than trusting the comment. Remove the "
                    "suppression or explain it in the skill's documentation.",
                )
            )
    return findings


def _exfiltration_findings(
    findings: list[Finding], domain_uses: list[DomainUse], filename: str
) -> list[Finding]:
    """Flag credential harvesting combined with outbound network access.

    Either half alone is ordinary; together, in one file, they are the shape of
    a credential stealer. Deliberately keyed on *bulk* env reads and credential
    *files* — a named ``os.getenv('MY_API_KEY')`` next to an HTTP call is how
    every legitimate API-using skill works and must stay clean.
    """
    harvest = [
        f
        for f in findings
        if f.rule_id in ("code.env.bulk_read", "code.credentials.file_access")
    ]
    egress = [u for u in domain_uses if u.domain == "network" and u.level == "write"]
    if not harvest or not egress:
        return []

    return [
        Finding(
            rule_id="code.exfiltration.credentials",
            severity="critical",
            category=CATEGORY_DANGEROUS_CALL,
            message=(
                f"Reads credentials (line {harvest[0].line}) and sends data to "
                f"the network (line {egress[0].line}) in the same file."
            ),
            file=filename,
            line=egress[0].line,
            remediation=(
                "Separate the two, or remove the credential read. A skill that "
                "collects secrets and makes outbound requests is "
                "indistinguishable from a credential stealer, so the gate "
                "treats it as one."
            ),
        )
    ]


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


def analyze_source(source: str, *, filename: str) -> CodeAnalysis:
    """Analyze one Python source file's text."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return CodeAnalysis(
            findings=(
                Finding(
                    rule_id="code.unparseable",
                    severity="high",
                    category=CATEGORY_DANGEROUS_CALL,
                    message=f"Is not valid Python and cannot be audited: {exc.msg}.",
                    file=filename,
                    line=exc.lineno or 1,
                    remediation=(
                        "Fix the syntax error. The gate refuses to pass code it "
                        "could not read rather than assume it is harmless."
                    ),
                ),
            )
        )

    _attach_parents(tree)
    analyzer = _Analyzer(filename)
    analyzer.visit(tree)

    findings = analyzer.findings + _suppression_findings(source, filename)
    findings += _exfiltration_findings(
        analyzer.findings, analyzer.domain_uses, filename
    )

    return CodeAnalysis(
        findings=tuple(findings),
        domain_uses=tuple(analyzer.domain_uses),
        imports=tuple(analyzer.imports),
    )


def analyze_code(directory: Path | str) -> CodeAnalysis:
    """Analyze every Python file in a skill directory.

    Covers ``tools.py`` and ``scripts/`` — and any other ``*.py``, so a skill
    cannot escape the scan by hiding the payload in ``helper.py``.
    """
    directory = Path(directory)
    analysis = CodeAnalysis()
    for path in python_sources(directory):
        relative = relative_path(path, directory)
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            analysis = analysis.merge(
                CodeAnalysis(
                    findings=(
                        Finding(
                            rule_id="code.unreadable",
                            severity="high",
                            category=CATEGORY_DANGEROUS_CALL,
                            message=f"Could not be read as UTF-8 text: {exc}.",
                            file=relative,
                            line=1,
                            remediation=(
                                "Ship the file as UTF-8 Python source. The gate "
                                "refuses to pass code it could not read."
                            ),
                        ),
                    )
                )
            )
            continue
        analysis = analysis.merge(analyze_source(source, filename=relative))
    return analysis
