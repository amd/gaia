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
3. **Read-only by construction.** The tables list the subcommands that only
   *read*. Anything that mutates a remote, writes a file, installs code, or
   prints a credential is simply not listed.

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
    """The read-only rule for one ``<binary> <subcommand>`` pair.

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
    """

    actions: frozenset[str] = frozenset()
    free_form: bool = False
    denied_actions: frozenset[str] = frozenset()
    denied_flags: frozenset[str] = frozenset()
    flag_values: Mapping[str, frozenset[str]] = field(default_factory=dict)
    value_flags: frozenset[str] = frozenset()


@dataclass(frozen=True)
class BinaryPolicy:
    """Everything core knows about one skill-declarable CLI.

    Attributes:
        binary: The executable name, as declared in ``shell:execute:<binary>``.
        summary: One line for error messages and ``gaia skill info``.
        install_hint: How to get it — named in the load-time failure when the
            binary is not on ``PATH``.
        subcommands: The read-only allowlist. Absent == refused.
        bare_flags: Flags accepted with no subcommand at all (``gh --version``).
    """

    binary: str
    summary: str
    install_hint: str
    subcommands: Mapping[str, Subcommand]
    bare_flags: frozenset[str] = frozenset({"--version", "--help", "-h"})


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
