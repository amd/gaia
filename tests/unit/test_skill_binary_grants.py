# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The ``shell:execute:<binary>`` bridge (#2932).

Four properties, in order of how badly a regression would hurt:

1. A granted CLI is still read-only — no write, no credential, no code install.
2. The grant is per agent instance and per skill; it is revoked on unload.
3. Nothing global is mutated, so one agent's skill cannot widen a sibling's shell.
4. A skill whose binary is missing or unpoliced fails loudly at load.
"""

from __future__ import annotations

import shlex

import pytest

from gaia.agents.tools.shell_tools import ALLOWED_COMMANDS, ShellToolsMixin
from gaia.skills.binaries import (
    BINARY_POLICIES,
    BinaryGrants,
    normalize_binary,
    resolve_binary_policies,
    validate_invocation,
)
from gaia.skills.errors import SkillPermissionError, SkillValidationError
from gaia.skills.permissions import (
    Permission,
    parse_permissions,
    refuse_unbridged_permissions,
)

GH = BINARY_POLICIES["gh"]


def check(command: str) -> str | None:
    """Run one gh command line through the policy gate."""
    return validate_invocation(GH, shlex.split(command))


# ---------------------------------------------------------------------------
# Permission grammar
# ---------------------------------------------------------------------------


def test_scoped_shell_execute_parses_as_a_binary_grant():
    (permission,) = parse_permissions(["shell:execute:gh"], skill_name="t")
    assert permission == Permission(domain="shell", level="execute", scope="gh")
    assert permission.is_binary_bridged
    assert not permission.is_local_capability
    assert not permission.is_connector_bridged


def test_scoped_shell_execute_is_no_longer_refused():
    permissions = parse_permissions(["shell:execute:gh"], skill_name="t")
    refuse_unbridged_permissions(permissions, skill_name="t")  # must not raise


def test_unscoped_shell_execute_is_refused_as_a_request_for_the_whole_shell():
    """The grammar allows it; the runtime does not grant it."""
    permissions = parse_permissions(["shell:execute"], skill_name="t")
    assert permissions[0].is_local_capability
    assert not permissions[0].is_binary_bridged
    with pytest.raises(SkillPermissionError, match="asks for the whole shell"):
        refuse_unbridged_permissions(permissions, skill_name="t")


def test_an_unpoliced_binary_is_refused_at_the_same_chokepoint():
    """Install, publish, migrate, and load all funnel through this one call."""
    permissions = parse_permissions(["shell:execute:kubectl"], skill_name="t")
    with pytest.raises(SkillPermissionError, match="no read-only command policy"):
        refuse_unbridged_permissions(permissions, skill_name="t")


@pytest.mark.parametrize(
    "scope", ["./evil", "/usr/bin/gh", "C:\\tmp\\gh.exe", "gh issue list", "-gh"]
)
def test_a_path_shaped_scope_is_refused(scope):
    """The grant names a binary off PATH, never a path the skill chose."""
    with pytest.raises(SkillValidationError, match="bare executable name"):
        parse_permissions([f"shell:execute:{scope}"], skill_name="t")


def test_other_local_capability_domains_are_still_refused():
    for raw in ("filesystem:write", "database:write", "desktop:control", "env:read"):
        with pytest.raises(SkillPermissionError, match="local-capability"):
            refuse_unbridged_permissions(
                parse_permissions([raw], skill_name="t"), skill_name="t"
            )


def test_shell_none_still_asks_for_nothing():
    (permission,) = parse_permissions(["shell:none"], skill_name="t")
    assert permission.grants_nothing
    assert not permission.is_binary_bridged
    refuse_unbridged_permissions([permission], skill_name="t")


# ---------------------------------------------------------------------------
# Load-time resolution — fail loudly
# ---------------------------------------------------------------------------


def test_an_unpoliced_binary_is_refused_with_the_declarable_set():
    permissions = parse_permissions(["shell:execute:kubectl"], skill_name="t")
    with pytest.raises(SkillPermissionError) as excinfo:
        resolve_binary_policies(permissions, skill_name="t", require_installed=False)
    message = str(excinfo.value)
    assert "no read-only command policy" in message
    assert "gh" in message  # names what IS declarable
    assert "BINARY_POLICIES" in message  # names where to add one


def test_a_missing_binary_fails_loudly_and_names_how_to_install_it(monkeypatch):
    monkeypatch.setattr("gaia.skills.binaries.shutil.which", lambda _name: None)
    permissions = parse_permissions(["shell:execute:gh"], skill_name="t")
    with pytest.raises(SkillPermissionError) as excinfo:
        resolve_binary_policies(permissions, skill_name="t")
    message = str(excinfo.value)
    assert "'gh' command, which is not on PATH" in message
    assert "cli.github.com" in message


def test_a_present_binary_resolves(monkeypatch):
    monkeypatch.setattr(
        "gaia.skills.binaries.shutil.which", lambda name: f"/usr/bin/{name}"
    )
    permissions = parse_permissions(["shell:execute:gh"], skill_name="t")
    assert [p.binary for p in resolve_binary_policies(permissions, skill_name="t")] == [
        "gh"
    ]


def test_non_shell_permissions_resolve_to_no_binary():
    permissions = parse_permissions(["network:read", "mcp:connect:x"], skill_name="t")
    assert resolve_binary_policies(permissions, skill_name="t") == []


# ---------------------------------------------------------------------------
# Security rules for gh
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "gh issue list --repo amd/gaia --limit 30 --json number,title,labels",
        "gh issue view 2932 --repo amd/gaia --json title,body,comments",
        "gh issue status",
        "gh pr list --repo amd/gaia",
        "gh pr diff 10 --repo amd/gaia",
        "gh pr checks 10",
        "gh repo view amd/gaia",
        "gh release list --repo amd/gaia",
        "gh run view 1 --log",
        "gh label list --repo amd/gaia",
        "gh search issues 'timeout' --repo amd/gaia",
        "gh auth status",
        "gh api repos/amd/gaia/issues",
        "gh api -X GET repos/amd/gaia/issues",
        "gh api --method=get repos/amd/gaia",
        "gh api --paginate repos/amd/gaia/issues",
        "gh --version",
        "gh",
    ],
)
def test_read_only_gh_commands_are_allowed(command):
    assert check(command) is None


def test_gh_auth_token_is_blocked_because_it_prints_the_credential():
    error = check("gh auth token")
    assert error is not None
    assert "auth token" in error


@pytest.mark.parametrize(
    "command",
    [
        "gh issue create --title x --body y",
        "gh issue close 1",
        "gh issue comment 1 --body hi",
        "gh issue edit 1 --add-label bug",
        "gh pr merge 1",
        "gh pr checkout 1",
        "gh pr create --fill",
        "gh repo clone amd/gaia",
        "gh repo delete amd/gaia",
        "gh release create v1",
        "gh run rerun 1",
        "gh label create bug",
    ],
)
def test_mutating_gh_commands_are_blocked(command):
    assert check(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "gh alias set pwn 'issue list'",
        "gh extension install someone/evil",
        "gh ext exec evil",
        "gh codespace ssh",
        "gh cs ssh",
        "gh config set editor vim",
        "gh secret list",
        "gh variable list",
        "gh ssh-key list",
        "gh gpg-key list",
    ],
)
def test_code_execution_and_credential_subcommands_are_blocked(command):
    """Deny-by-default: none of these are listed, so none of them run."""
    assert check(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "gh api -X POST repos/amd/gaia/issues",
        "gh api --method PATCH repos/amd/gaia",
        "gh api --method=DELETE repos/amd/gaia",
        "gh api -X PUT repos/x",
        "gh api -X",
    ],
)
def test_gh_api_is_get_only(command):
    assert check(command) is not None


@pytest.mark.parametrize(
    "flag", ["-f a=b", "--field a=b", "-F a=@f", "--raw-field a=b", "--input body.json"]
)
def test_gh_api_field_flags_are_blocked_because_a_body_means_a_write(flag):
    assert check(f"gh api repos/amd/gaia/issues {flag}") is not None


@pytest.mark.parametrize("command", ["gh api graphql", "gh api graphql --paginate"])
def test_gh_api_graphql_is_blocked(command):
    assert check(command) is not None


def test_a_flag_value_is_never_mistaken_for_the_action():
    """`--method GET` must not be read as the api path, nor `-L 5` as an action."""
    assert check("gh issue list -L 5 --repo amd/gaia") is None
    assert check("gh api -H 'Accept: application/json' repos/x") is None


def test_a_value_flag_cannot_smuggle_a_subcommand_in_front():
    assert check("gh --repo x issue list") is not None


def test_the_error_names_what_is_allowed():
    error = check("gh issue create --title x")
    assert "list" in error and "view" in error


# ---------------------------------------------------------------------------
# The grant ledger
# ---------------------------------------------------------------------------


def test_grants_are_revoked_when_the_last_holding_skill_unloads():
    grants = BinaryGrants()
    grants.grant("gh", skill_name="github-triage")
    grants.grant("gh", skill_name="release-notes")
    assert grants.binaries() == frozenset({"gh"})

    assert grants.revoke_skill("github-triage") == []  # release-notes still holds it
    assert "gh" in grants
    assert grants.revoke_skill("release-notes") == ["gh"]
    assert grants.binaries() == frozenset()
    assert not grants


def test_revoking_an_unknown_skill_is_a_no_op():
    grants = BinaryGrants()
    grants.grant("gh", skill_name="a")
    assert grants.revoke_skill("never-loaded") == []
    assert "gh" in grants


@pytest.mark.parametrize(
    "token,expected",
    [
        ("gh", "gh"),
        ("GH", "gh"),
        ("gh.exe", "gh"),
        ("GH.EXE", "gh"),
        ("C:\\Program Files\\GitHub CLI\\gh.exe", "gh"),
        ("/usr/bin/gh", "gh"),
    ],
)
def test_binary_names_normalize_to_the_grant_key(token, expected):
    """A path or .exe spelling must not slip past — or bypass — the gate."""
    assert normalize_binary(token) == expected


# ---------------------------------------------------------------------------
# The shell tool's gate
# ---------------------------------------------------------------------------


class _Shell(ShellToolsMixin):
    """A bare mixin host — no path validator, no agent."""


def test_a_policed_binary_is_refused_without_a_grant():
    error = ShellToolsMixin._validate_command(
        "gh", ["gh", "issue", "list"], "gh issue list"
    )
    assert error is not None
    assert "shell:execute:gh" in error["error"]


def test_a_granted_binary_passes_the_shell_gate():
    error = ShellToolsMixin._validate_command(
        "gh",
        ["gh", "issue", "list", "--repo", "amd/gaia"],
        "gh issue list --repo amd/gaia",
        granted_binaries=frozenset({"gh"}),
    )
    assert error is None


def test_a_grant_does_not_widen_past_the_read_only_policy():
    error = ShellToolsMixin._validate_command(
        "gh",
        ["gh", "auth", "token"],
        "gh auth token",
        granted_binaries=frozenset({"gh"}),
    )
    assert error is not None


def test_a_grant_for_one_binary_does_not_grant_another():
    error = ShellToolsMixin._validate_command(
        "kubectl",
        ["kubectl", "get", "pods"],
        "kubectl get pods",
        granted_binaries=frozenset({"gh"}),
    )
    assert error is not None
    assert "not in the allowed list" in error["error"]


def test_granting_never_mutates_module_state():
    """The invariant that keeps one agent's skill out of every other agent."""
    before = set(ALLOWED_COMMANDS)
    grants = BinaryGrants()
    grants.grant("gh", skill_name="github-triage")
    ShellToolsMixin._validate_command(
        "gh",
        ["gh", "issue", "list"],
        "gh issue list",
        granted_binaries=grants.binaries(),
    )
    assert set(ALLOWED_COMMANDS) == before
    assert "gh" not in ALLOWED_COMMANDS
    # A second, ungranted host still refuses it.
    assert _Shell().granted_binaries == frozenset()


def test_policy_binaries_never_shadow_a_builtin_whitelist_entry():
    """A policy entry must not silently take over an always-allowed command."""
    assert not set(BINARY_POLICIES) & ALLOWED_COMMANDS


def test_the_ungranted_whitelist_is_unchanged():
    """An agent with no skill loaded behaves exactly as before."""
    host = _Shell()
    assert host.granted_binaries == frozenset()
    assert ShellToolsMixin._validate_command("ls", ["ls", "-la"], "ls -la") is None
    assert (
        ShellToolsMixin._validate_command("git", ["git", "push"], "git push")
        is not None
    )
