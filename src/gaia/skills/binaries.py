# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The ``shell:execute:<binary>`` bridge — a skill brings its own CLI.

A skill that needs GitHub does not need a bespoke connector; it needs ``gh``.
Declaring ``shell:execute:gh`` in ``SKILL.md`` grants **that one binary**, to
**that one skill's session**, on **that one agent instance**, restricted to the
read-only subcommands named in :data:`BINARY_POLICIES` below.

Three properties make this safe enough to bridge while the general shell sandbox
is still deferred:

1. **Deny by default at two levels.** A binary with no entry here cannot be
   declared at all, and a subcommand absent from its entry is refused. Nothing is
   permitted by omission.
2. **No global state.** The grant lives on the agent instance
   (:class:`BinaryGrants`), never in a module-level allowlist. One agent's skill
   can never widen another agent's shell.
3. **Read-only by construction — for CLIs that talk to something *external*.**
   ``gh``'s tables list the subcommands that only read a remote. ``pytest`` is a
   different class of grant and says so in its own summary: it EXECUTES the
   project's own test code, the same trust boundary as the ungated
   ``execute_python_file`` tool. What its table restricts is invocation shape
   (no plugin injection, no interactive hang, no writes/paths outside the
   checkout) — never what the tests themselves do.

Adding a CLI is a data entry here plus a ``SKILL.md`` — never a new branch in the
shell tool. That is the acceptance test for this module: supporting GitLab must
be ``"glab": BinaryPolicy(...)`` and nothing else.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Mapping, Sequence

from gaia.skills.errors import FORMAT_DOCS_URL, SkillPermissionError

#: A binary name is a bare executable, never a path — the grant resolves off
#: ``PATH``, so neither a declared scope nor an invoked token may name a file the
#: skill chose. Applied to both ends: ``Permission.parse`` and
#: :func:`normalize_binary`.
BINARY_NAME_RE = re.compile(r"^[a-z][a-z0-9._+-]*$")

if TYPE_CHECKING:  # ``permissions`` imports this module — keep the edge one-way.
    from gaia.skills.permissions import Permission


@dataclass(frozen=True)
class Subcommand:
    """The read-only rule for one ``<binary> <subcommand>`` pair — or, when set
    as :attr:`BinaryPolicy.positional`, for a binary with no subcommand at all.

    Attributes:
        actions: Allowed third tokens (``gh issue **list**``). Empty plus
            ``free_form=False`` means the subcommand takes no action and any
            action token is refused.
        free_form: The subcommand's first positional is data, not an action
            (``gh api **repos/amd/gaia**``), so it is not matched against
            *actions*. ``denied_actions`` still applies.
        denied_actions: Action tokens refused even under ``free_form``.
        denied_flags: Flags refused outright — typically the ones that turn a
            read into a write.
        flag_values: ``{flag: allowed lowercase values}``. A listed flag must
            carry one of those values (``--method GET``).
        value_flags: Flags that consume the following token. Needed so a flag's
            value is never mistaken for the action positional.
        path_operands: For :attr:`BinaryPolicy.positional` rules only — every
            non-flag token is a path operand (pytest's test paths), not a
            single subcommand action, and each is checked for a shape that
            could escape the project (absolute, drive-letter, or ``..``).
        allowed_flags: For :attr:`BinaryPolicy.positional` rules only — the
            valueless flags this grant accepts. Positional-mode flag checking
            is an ALLOWLIST (unlike the subcommand mode's denylist above):
            a binary that takes no subcommand executes the caller's own
            arguments far more directly, so an unreviewed flag is refused
            rather than passed through.
        flag_value_prefixes: For positional rules — ``{flag: allowed lowercase
            value prefixes}``. Like ``flag_values`` but for a flag whose safe
            values share a prefix rather than an exact set (``-p no:...``).
        denied_flag_reasons: For positional rules — ``{flag: one-line reason}``
            shown in the refusal. Falls back to a generic message when absent.
    """

    actions: frozenset[str] = frozenset()
    free_form: bool = False
    denied_actions: frozenset[str] = frozenset()
    denied_flags: frozenset[str] = frozenset()
    flag_values: Mapping[str, frozenset[str]] = field(default_factory=dict)
    value_flags: frozenset[str] = frozenset()
    path_operands: bool = False
    allowed_flags: frozenset[str] = frozenset()
    flag_value_prefixes: Mapping[str, frozenset[str]] = field(default_factory=dict)
    denied_flag_reasons: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BinaryPolicy:
    """Everything core knows about one skill-declarable CLI.

    Attributes:
        binary: The executable name, as declared in ``shell:execute:<binary>``.
        summary: One line for error messages and ``gaia skill info``.
        install_hint: How to get it — named in the load-time failure when the
            binary is not on ``PATH``.
        subcommands: The read-only allowlist, keyed by subcommand. Absent ==
            refused. Ignored when ``positional`` is set.
        bare_flags: Flags accepted with no subcommand at all (``gh --version``).
            Also folded into the allowlist for a ``positional`` binary, where
            *every* invocation is "no subcommand".
        positional: Set instead of ``subcommands`` for a binary whose shape is
            ``<binary> [operands] [flags]`` with no subcommand step at all
            (``pytest tests/unit -k foo``, vs. ``gh issue list``). When set,
            every token in the invocation is validated against this one rule.
    """

    binary: str
    summary: str
    install_hint: str
    subcommands: Mapping[str, Subcommand] = field(default_factory=dict)
    bare_flags: frozenset[str] = frozenset({"--version", "--help", "-h"})
    positional: Subcommand | None = None

    def __post_init__(self) -> None:
        if bool(self.subcommands) == bool(self.positional is not None):
            raise ValueError(
                f"BinaryPolicy({self.binary!r}) must set exactly one of "
                "'subcommands' (a gh-shaped CLI) or 'positional' (a "
                "no-subcommand CLI like pytest), never both or neither."
            )


# ---------------------------------------------------------------------------
# The table. One entry per CLI a skill may declare.
# ---------------------------------------------------------------------------
#
# Deliberately ABSENT from `gh` — do not add them without re-reading why:
#   alias      defines an arbitrary shell command under a gh name
#   extension  installs and runs third-party code
#   codespace  opens a remote shell
#   config     sets the editor/pager, i.e. command execution
#   auth token prints the credential (hence auth allows `status` only)
#   secret / variable / ssh-key / gpg-key   credential surfaces
#   issue|pr create/edit/close/comment/merge  writes to the repository
#
# They need no explicit block: anything not listed below is refused.

_GH_API = Subcommand(
    free_form=True,
    # POST-by-default and can mutate through a query document.
    denied_actions=frozenset({"graphql"}),
    # Any field flag implies a request body, and a body implies a write.
    denied_flags=frozenset({"-f", "--field", "-F", "--raw-field", "--input"}),
    flag_values={"-X": frozenset({"get"}), "--method": frozenset({"get"})},
    value_flags=frozenset(
        {
            "-X",
            "--method",
            "-H",
            "--header",
            "--hostname",
            "-q",
            "--jq",
            "-t",
            "--template",
            "--cache",
            "-p",
            "--preview",
        }
    ),
)

_GH_COMMON_VALUE_FLAGS = frozenset(
    {
        "-R",
        "--repo",
        "-L",
        "--limit",
        "-s",
        "--state",
        "-l",
        "--label",
        "-a",
        "--assignee",
        "-A",
        "--author",
        "-S",
        "--search",
        "-q",
        "--jq",
        "-t",
        "--template",
        "--json",
        "--milestone",
        "--owner",
        "--branch",
        "--workflow",
        "--user",
        "--sort",
        "--order",
    }
)


def _gh(actions: Iterable[str]) -> Subcommand:
    """A plain read-only gh subcommand: an action allowlist and nothing else."""
    return Subcommand(actions=frozenset(actions), value_flags=_GH_COMMON_VALUE_FLAGS)


BINARY_POLICIES: dict[str, BinaryPolicy] = {
    "gh": BinaryPolicy(
        binary="gh",
        summary="GitHub CLI — read issues, pull requests, releases, and runs.",
        install_hint=(
            "Install the GitHub CLI from https://cli.github.com (winget install "
            "GitHub.cli / brew install gh / apt install gh), then authenticate "
            "with 'gh auth login'. Verify with 'gh auth status'."
        ),
        subcommands={
            "issue": _gh({"list", "view", "status"}),
            "pr": _gh({"list", "view", "diff", "checks", "status"}),
            "repo": _gh({"list", "view"}),
            "release": _gh({"list", "view"}),
            "run": _gh({"list", "view"}),
            "label": _gh({"list"}),
            "search": _gh({"issues", "prs", "repos", "code", "commits"}),
            # `status` only. `gh auth token` prints the credential.
            "auth": _gh({"status"}),
            "api": _GH_API,
        },
    ),
    # `pytest` is NOT a read-only grant in the sense `gh` is — it EXECUTES the
    # project's own test code. That is the same trust boundary as the
    # ungated `execute_python_file` tool (code already in the repo runs),
    # not a new one. What this policy restricts is the invocation shape: no
    # plugin injection, no interactive hang, no write outside the run, no
    # pointing pytest at config/paths outside the checkout. It does not, and
    # cannot, sandbox what the tests themselves do.
    #
    # Flags are an ALLOWLIST here (unlike `gh`'s denylist): a no-subcommand
    # binary parses the caller's arguments far more directly, so an
    # unreviewed flag is refused by default rather than passed through.
    #
    # Deliberately ABSENT — refused by simply not being in `allowed_flags` /
    # `value_flags` / `flag_values` / `flag_value_prefixes`:
    #   -s / --capture=no          not needed for a pass/fail run
    #   -l / --showlocals          can leak env vars/secrets into output
    #   --import-mode, --assert    change how test code is loaded/rewritten
    #   anything plugin-shaped that isn't `-p no:...` (see denied below)
    "pytest": BinaryPolicy(
        binary="pytest",
        summary=(
            "pytest — runs the project's own test suite. This EXECUTES "
            "project code (the same class as execute_python_file), not a "
            "read; the grant restricts flags and paths, never what a test "
            "itself does."
        ),
        install_hint=(
            "Install pytest with 'pip install pytest' (already a dev "
            "dependency: 'uv pip install -e \".[dev]\"'). Verify with "
            "'pytest --version'."
        ),
        positional=Subcommand(
            path_operands=True,
            allowed_flags=frozenset(
                {
                    "-q",
                    "--quiet",
                    "-v",
                    "--verbose",
                    "-x",
                    "--exitfirst",
                    "--collect-only",
                    "--co",
                    "--no-header",
                }
            ),
            # Any value is fine for these — -k/-m are pytest's own restricted
            # keyword/marker matchers (no eval, cannot call anything), and
            # --maxfail is just a count.
            value_flags=frozenset({"-k", "-m", "--maxfail"}),
            flag_values={
                "--tb": frozenset({"auto", "long", "short", "line", "native", "no"}),
            },
            # `-p <plugin>` auto-imports and runs that plugin's code at
            # collection time. Only a `no:`-prefixed *disable* is safe; `-p`
            # naming a plugin to enable is refused (falls through to the
            # denied-value error below, same message shape as gh's -X GET).
            flag_value_prefixes={"-p": frozenset({"no:"})},
            denied_flags=frozenset(
                {
                    # Interactive debuggers hang a non-interactive run forever
                    # (this process's stdin is DEVNULL — see shell_tools.py).
                    "--pdb",
                    "--trace",
                    # Report writers. This policy has no notion of "inside
                    # the project" to confine the destination to — deny
                    # outright rather than guess at containment.
                    "--junitxml",
                    "--result-log",
                    "--basetemp",
                    # Point pytest at a config/rootdir outside the checkout,
                    # changing what actually runs. Same "no containment
                    # notion" reasoning as the writers above.
                    "-c",
                    "--rootdir",
                    # Re-injects arbitrary CLI options (including `-p` with a
                    # real plugin, or any flag denied above) via an ini
                    # override — the one flag-level bypass of every other
                    # rule in this table, so it is denied on its own.
                    "-o",
                    "--override-ini",
                }
            ),
            denied_flag_reasons={
                "--pdb": "it drops into an interactive debugger and hangs a non-interactive run forever",
                "--trace": "it drops into an interactive debugger and hangs a non-interactive run forever",
                "--junitxml": "it writes a report file outside this grant's read-only run",
                "--result-log": "it writes a report file outside this grant's read-only run",
                "--basetemp": "it picks pytest's temp directory outside this grant's read-only run",
                "-c": "it can point pytest at a config file outside the project, changing what actually runs",
                "--rootdir": "it can point pytest at a config file outside the project, changing what actually runs",
                "-o": "it overrides pytest ini options (e.g. addopts), which can re-inject any flag this grant denies",
                "--override-ini": "it overrides pytest ini options (e.g. addopts), which can re-inject any flag this grant denies",
            },
        ),
    ),
}


# ---------------------------------------------------------------------------
# Per-instance grant ledger
# ---------------------------------------------------------------------------


class BinaryGrants:
    """Which binaries this **one agent instance** may run, and for which skills.

    Keyed by binary so two skills declaring ``gh`` share one grant, and
    unloading one of them does not revoke the other's.
    """

    def __init__(self) -> None:
        self._holders: dict[str, set[str]] = {}

    def grant(self, binary: str, *, skill_name: str) -> None:
        """Grant *binary* for the lifetime of *skill_name*'s load."""
        self._holders.setdefault(binary, set()).add(skill_name)

    def revoke_skill(self, skill_name: str) -> list[str]:
        """Drop *skill_name*'s hold. Returns the binaries that lost their last one."""
        revoked = []
        for binary, holders in list(self._holders.items()):
            holders.discard(skill_name)
            if not holders:
                del self._holders[binary]
                revoked.append(binary)
        return sorted(revoked)

    def binaries(self) -> frozenset[str]:
        """Every currently granted binary."""
        return frozenset(self._holders)

    def __contains__(self, binary: object) -> bool:
        return binary in self._holders

    def __bool__(self) -> bool:
        return bool(self._holders)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"BinaryGrants({sorted(self._holders)!r})"


# ---------------------------------------------------------------------------
# Load-time resolution
# ---------------------------------------------------------------------------


def binary_permissions(permissions: Sequence["Permission"]) -> list["Permission"]:
    """The ``shell:execute:<binary>`` entries of *permissions*."""
    return [p for p in permissions if p.is_binary_bridged]


def refuse_unpoliced_binaries(
    permissions: Sequence["Permission"], *, skill_name: str
) -> None:
    """Refuse a binary grant core has no read-only policy for.

    Called from :func:`gaia.skills.permissions.refuse_unbridged_permissions`, so
    every entry point — install, publish, migrate, ``register_skill_tools``,
    ``Agent.load_skill`` — gets the same gate.

    Raises:
        SkillPermissionError: the declared binary is not in :data:`BINARY_POLICIES`,
            so its subcommands cannot be gated.
    """
    for permission in binary_permissions(permissions):
        binary = (permission.scope or "").lower()
        if binary in BINARY_POLICIES:
            continue
        raise SkillPermissionError(
            f"Skill '{skill_name}' declares 'shell:execute:{binary}', but GAIA "
            f"ships no read-only command policy for {binary!r}, so the grant "
            "cannot be enforced and the skill is refused rather than loaded "
            "unenforced. Declarable binaries: "
            f"{', '.join(sorted(BINARY_POLICIES)) or '(none)'}. To add one, "
            "contribute an entry to BINARY_POLICIES in "
            f"gaia/skills/binaries.py. See {FORMAT_DOCS_URL}#permission-model"
        )


def resolve_binary_policies(
    permissions: Sequence["Permission"],
    *,
    skill_name: str,
    require_installed: bool = True,
) -> list[BinaryPolicy]:
    """Resolve a skill's shell permissions to policies, failing loudly.

    Args:
        permissions: The skill's parsed permissions. Non-shell entries are ignored.
        skill_name: Named in every error.
        require_installed: Check ``PATH``. Off only for tests and static audits
            that inspect a skill without intending to run it.

    Raises:
        SkillPermissionError: the declared binary has no policy (so it cannot be
            gated), or is not installed (so the skill would load with a silent
            capability gap — the exact failure this bridge exists to prevent).
    """
    refuse_unpoliced_binaries(permissions, skill_name=skill_name)

    policies: list[BinaryPolicy] = []
    for permission in binary_permissions(permissions):
        policy = BINARY_POLICIES[(permission.scope or "").lower()]
        if require_installed and shutil.which(policy.binary) is None:
            raise SkillPermissionError(
                f"Skill '{skill_name}' needs the '{policy.binary}' command, which "
                f"is not on PATH. {policy.summary} {policy.install_hint} "
                "The skill is refused rather than loaded without the tool it "
                "documents — a half-loaded skill produces confident answers from "
                "no data."
            )
        if policy not in policies:
            policies.append(policy)
    return policies


# ---------------------------------------------------------------------------
# Invocation gate
# ---------------------------------------------------------------------------


def normalize_binary(token: str) -> str:
    """The grant key for *token* as typed, or ``""`` when it is not a bare name.

    A path spelling never matches a grant: ``./gh`` and ``C:\\evil\\gh.exe`` are
    files the caller chose, not the CLI the skill declared, so they fall through
    to the ordinary command whitelist (which refuses them).
    """
    name = token.strip().strip('"').lower()
    if os.name == "nt" and name.endswith(".exe"):
        name = name[: -len(".exe")]
    return name if BINARY_NAME_RE.match(name) else ""


def _split_flag(token: str) -> tuple[str, str | None]:
    """Split one flag token into ``(name, attached value)``.

    Mirrors how Go's ``pflag`` — what ``gh`` uses — actually parses, including
    the attached short form. ``-XDELETE``, ``-X=DELETE``, and ``--method=DELETE``
    all carry their value in the token; missing that is how a method rule gets
    bypassed by deleting one space.
    """
    if token.startswith("--"):
        name, _, attached = token.partition("=")
        return name, attached if attached else None
    name, rest = token[:2], token[2:]
    return name, rest.lstrip("=") or None


def _flag_name(token: str) -> str:
    """The flag's name, without any value attached to the same token."""
    return _split_flag(token)[0]


def validate_invocation(policy: BinaryPolicy, argv: Sequence[str]) -> str | None:
    """Check one granted invocation against *policy*. Returns an error, or None.

    *argv* is the shlex-split command, ``argv[0]`` being the binary.
    """
    tokens = list(argv[1:])

    if policy.positional is not None:
        return _validate_positional_invocation(policy, tokens)

    allowed_subcommands = ", ".join(sorted(policy.subcommands))

    # Leading flags may only be the valueless ones (`gh --version`); anything
    # else before the subcommand could carry a value and shift the parse.
    index = 0
    while index < len(tokens) and tokens[index].startswith("-"):
        if _flag_name(tokens[index]) not in policy.bare_flags:
            return (
                f"'{policy.binary} {tokens[index]}' is not allowed: only "
                f"{', '.join(sorted(policy.bare_flags))} may precede a subcommand. "
                f"Put the subcommand first, e.g. '{policy.binary} "
                f"{sorted(policy.subcommands)[0]} ...'."
            )
        index += 1

    if index >= len(tokens):
        return None  # bare `gh`, `gh --version` — help text only

    subcommand = tokens[index].lower()
    rule = policy.subcommands.get(subcommand)
    if rule is None:
        return (
            f"'{policy.binary} {subcommand}' is not allowed. This skill's grant "
            f"covers read-only {policy.binary} commands only: {allowed_subcommands}. "
            f"Anything that writes, installs, opens a shell, or prints a credential "
            "is refused."
        )

    rest = tokens[index + 1 :]
    denied_flags = rule.denied_flags

    action: str | None = None
    cursor = 0
    while cursor < len(rest):
        token = rest[cursor]
        cursor += 1
        if not token.startswith("-") or token == "-":
            if action is None:
                action = token
            continue

        name, inline = _split_flag(token)
        if name in denied_flags:
            return (
                f"'{policy.binary} {subcommand} {name}' is not allowed: that flag "
                "sends a request body, which makes the call a write. Read-only "
                f"{policy.binary} {subcommand} calls only."
            )

        takes_value = name in rule.flag_values or name in rule.value_flags
        value = inline
        if takes_value and inline is None:
            value = rest[cursor] if cursor < len(rest) else None
            cursor += 1  # the value is never the action positional

        if name in rule.flag_values:
            allowed = rule.flag_values[name]
            if value is None or value.lower() not in allowed:
                spelled = " ".join(filter(None, (name, value)))
                return (
                    f"'{policy.binary} {subcommand} {spelled}' is not allowed: "
                    f"{name} may only be "
                    f"{', '.join(sorted(v.upper() for v in allowed))}. "
                    "Other methods can modify the remote."
                )

    if action is not None and action.lower().strip("/") in rule.denied_actions:
        return (
            f"'{policy.binary} {subcommand} {action}' is not allowed: it can "
            "mutate the remote even through what looks like a read."
        )

    if not rule.free_form:
        if action is None:
            return (
                f"'{policy.binary} {subcommand}' needs a read-only action: "
                f"{', '.join(sorted(rule.actions))}."
            )
        if action.lower() not in rule.actions:
            return (
                f"'{policy.binary} {subcommand} {action}' is not allowed. "
                f"Allowed {subcommand} actions: {', '.join(sorted(rule.actions))}."
            )

    return None


_UNSAFE_OPERAND_RE = re.compile(r"^[a-zA-Z]:[\\/]")


def _unsafe_operand(token: str) -> bool:
    """True when *token* could name a path outside the project checkout.

    Purely lexical — ``validate_invocation`` is never given a cwd, so it can
    only refuse shapes that are never a legitimate in-repo test path: a POSIX
    absolute path, a Windows drive letter, or a ``..`` path component. This is
    the only path check a ``path_operands`` binary gets: the shell tool's own
    path-traversal scan (``shell_tools.py``) skips every segment whose binary
    is skill-granted, on the assumption a granted CLI's operands are remote
    ids, not local paths — true for ``gh``, false for ``pytest``.
    """
    if token.startswith(("/", "\\")):
        return True
    if _UNSAFE_OPERAND_RE.match(token):
        return True
    return ".." in re.split(r"[\\/]", token)


def _validate_positional_invocation(
    policy: BinaryPolicy, tokens: Sequence[str]
) -> str | None:
    """``validate_invocation`` for a :attr:`BinaryPolicy.positional` binary.

    No subcommand step: every token is either a path operand or a flag, and
    flags are checked against an ALLOWLIST (``rule.allowed_flags`` /
    ``value_flags`` / ``flag_values`` / ``flag_value_prefixes``), not the
    subcommand path's denylist — see the "Flags are an ALLOWLIST here"
    comment on the ``pytest`` entry for why.
    """
    rule = policy.positional
    known_flags = (
        rule.allowed_flags
        | policy.bare_flags
        | frozenset(rule.flag_values)
        | frozenset(rule.value_flags)
        | frozenset(rule.flag_value_prefixes)
    )

    cursor = 0
    while cursor < len(tokens):
        token = tokens[cursor]
        cursor += 1

        if not token.startswith("-") or token == "-":
            if rule.path_operands and _unsafe_operand(token):
                return (
                    f"'{policy.binary} {token}' is not allowed: operand paths "
                    "must stay inside the project checkout — no leading '/', "
                    "drive letter, or '..' component."
                )
            continue

        name, inline = _split_flag(token)

        if name in rule.denied_flags:
            reason = rule.denied_flag_reasons.get(
                name, "it is outside this grant's read-only flag set"
            )
            return f"'{policy.binary} {name}' is not allowed: {reason}."

        if name in rule.allowed_flags or name in policy.bare_flags:
            if inline is not None:
                # A bundled/'='-attached form on a flag we only ever validated
                # as bare (`-xvs`, `--quiet=1`) could smuggle an unreviewed
                # flag or value past this allowlist unexamined.
                return (
                    f"'{policy.binary} {token}' is not allowed: {name} takes "
                    "no value — pass it on its own, e.g. "
                    f"'{policy.binary} {name} ...' not bundled or '='-attached."
                )
            continue

        takes_value = (
            name in rule.flag_values
            or name in rule.value_flags
            or name in rule.flag_value_prefixes
        )
        if not takes_value:
            return (
                f"'{policy.binary} {name}' is not allowed. This skill's grant "
                f"covers a fixed set of read-only flags: "
                f"{', '.join(sorted(known_flags))}."
            )

        value = inline
        if inline is None:
            value = tokens[cursor] if cursor < len(tokens) else None
            cursor += 1  # the value is never mistaken for a path operand

        if name in rule.flag_values:
            allowed = rule.flag_values[name]
            if value is None or value.lower() not in allowed:
                spelled = " ".join(filter(None, (name, value)))
                return (
                    f"'{policy.binary} {spelled}' is not allowed: {name} may "
                    f"only be {', '.join(sorted(allowed))}."
                )
        elif name in rule.flag_value_prefixes:
            prefixes = rule.flag_value_prefixes[name]
            if value is None or not any(
                value.lower().startswith(prefix) for prefix in prefixes
            ):
                spelled = " ".join(filter(None, (name, value)))
                return (
                    f"'{policy.binary} {spelled}' is not allowed: {name} only "
                    f"accepts a value starting with "
                    f"{', '.join(sorted(prefixes))!r}."
                )
        # else: a bare `value_flags` entry (e.g. -k, -m, --maxfail) — any
        # value is accepted, matching gh's `value_flags` semantics.

    return None
