# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The CLI self-setup tools — and the gate they can never get around.

``CliSetupToolsMixin`` lets the agent install a skill's CLI and sign it in
instead of telling the user to go and do it. That is a real escalation: it
mutates the machine and touches the user's GitHub account. Three properties
keep it honest, and each of them is a one-line change away from being lost:

1. **Every call is asked about, on its own.** Both mutating tools are in
   ``TOOLS_REQUIRING_CONFIRMATION``; no skill grant pre-authorises them, and no
   "always" grant is offered for them. A loaded skill must never be able to
   hand itself an install.
2. **The model picks which CLI, never what runs.** An unknown binary is refused
   rather than executed, and the ``command`` argument — which exists only so
   the approval prompt shows real text — refuses on a mismatch instead of
   quietly running the table's own command.
3. **A state the tool cannot fix is reported, not attempted.** ``env_token`` is
   the one that reads like a sign-in problem and is not.

Nothing here installs anything or contacts GitHub: the engine's entry points
are replaced on the module the tools read them from.
"""

from __future__ import annotations

import pytest

from gaia.agents.base.agent import TOOLS_REQUIRING_CONFIRMATION
from gaia.agents.base.tool_grants import grant_scope
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools import cli_setup_tools
from gaia.agents.tools.cli_setup_tools import CliSetupToolsMixin
from gaia.agents.tools.shell_tools import ShellToolsMixin
from gaia.skills.binaries import BINARY_POLICIES, BinaryGrants
from gaia.skills.binary_setup import (
    ENV_TOKEN,
    MISSING,
    READY,
    UNAUTHENTICATED,
    InstallPlan,
    SetupError,
    SetupStatus,
)

GH = BINARY_POLICIES["gh"]

SETUP_TOOLS = ("check_cli_setup", "install_cli", "sign_in_cli")
MUTATING_TOOLS = ("install_cli", "sign_in_cli")

WINGET = InstallPlan(
    binary="gh",
    argv=("winget", "install", "--id", "GitHub.cli", "--exact"),
    docs_url="https://cli.github.com",
)


# ---------------------------------------------------------------------------
# Hosts
# ---------------------------------------------------------------------------


class _Host(CliSetupToolsMixin):
    """A minimal agent-shaped host for driving the registered tools."""

    def __init__(self):
        self.warnings = []
        self.confirmations = []
        self.console = self

    # -- console surface the sign-in handoff uses --------------------------
    def print_warning(self, message: str) -> None:
        self.warnings.append(message)

    def confirm_tool_execution(self, name, args) -> bool:
        self.confirmations.append((name, args))
        return False  # the user walks away; nothing may proceed


class _Gated(ShellToolsMixin):
    """A host wired to the real confirmation gate, with a live ``gh`` grant."""

    from gaia.agents.base.agent import Agent as _Agent

    CONFIRMATION_REQUIRED_TOOLS: tuple = ()
    _tools_registry: dict = {}
    confirmation_required_tools = _Agent.confirmation_required_tools
    _call_is_pre_authorized = _Agent._call_is_pre_authorized
    _tool_requires_confirmation = _Agent._tool_requires_confirmation

    def __init__(self, *binaries: str):
        super().__init__()
        self._granted_binaries = BinaryGrants()
        for binary in binaries:
            self._granted_binaries.grant(binary, skill_name="github-triage")


@pytest.fixture(name="tools")
def _tools_fixture():
    """The three tools, registered and callable."""
    _Host().register_cli_setup_tools()
    return {name: _TOOL_REGISTRY[name]["function"] for name in SETUP_TOOLS}


def _status(state: str, **kwargs) -> SetupStatus:
    return SetupStatus(binary="gh", state=state, detail=f"gh is {state}.", **kwargs)


def _returns(value):
    def fake(*_args, **_kwargs):
        return value

    return fake


def _forbidden(label: str):
    def fake(*_args, **_kwargs):
        raise AssertionError(f"{label} ran without the call being allowed to")

    return fake


# ---------------------------------------------------------------------------
# The confirmation gate — the security-critical property
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", MUTATING_TOOLS)
def test_both_mutating_tools_always_require_confirmation(tool_name):
    assert tool_name in TOOLS_REQUIRING_CONFIRMATION


def test_the_read_only_check_raises_no_prompt():
    """It changes nothing, and prompting for it would train the user to click
    through the two prompts that matter."""
    assert "check_cli_setup" not in TOOLS_REQUIRING_CONFIRMATION


@pytest.mark.parametrize("tool_name", MUTATING_TOOLS)
def test_a_loaded_skill_can_never_pre_authorise_an_install_or_a_sign_in(tool_name):
    """The regression that would matter most.

    ``skill_grant_covers_call`` is the single place that decides a call runs
    with nobody asked. It exempts the ALLOW tier of ``run_shell_command`` and
    nothing else — otherwise loading a skill that declares ``shell:execute:gh``
    would silently buy it the right to install software.
    """
    host = _Gated("gh")
    args = {"binary": "gh", "command": "winget install --id GitHub.cli"}

    assert host.skill_grant_covers_call(tool_name, args) is False
    assert host._tool_requires_confirmation(tool_name, args) is True


def test_the_read_only_check_is_not_exempted_either():
    host = _Gated("gh")
    assert host.skill_grant_covers_call("check_cli_setup", {"binary": "gh"}) is False


@pytest.mark.parametrize("tool_name", MUTATING_TOOLS)
@pytest.mark.parametrize(
    "args",
    [
        {"binary": "gh", "command": "winget install --id GitHub.cli --exact"},
        {"binary": "gh", "command": "gh auth login --web"},
        {"binary": "gh"},
        {},
    ],
)
def test_no_always_grant_is_ever_offered_for_these_tools(tool_name, args):
    """ "Always allow" would make the next install unattended.

    ``grant_scope`` answering ``None`` is what stops the UI offering it — the
    user answers yes or no to each call, every time.
    """
    assert grant_scope(tool_name, args) is None


def test_a_shell_command_still_gets_its_narrow_grant():
    """The control: ``grant_scope`` is not simply returning None for everything."""
    assert grant_scope("run_shell_command", {"command": "gh issue list"}) is not None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_the_tools_register_on_a_host_shaped_like_a_real_agent():
    """A real agent is ``Agent`` plus mixins, and defines no ``register_tool``.

    The ``@tool`` decorator *is* the registration. A registration step that
    also calls a helper — the shape every other tool mixin was written with
    once — raises ``AttributeError`` on every agent composing this mixin, which
    is every ChatAgent, and it fails at start-up rather than at first use.
    """
    from gaia.agents.base.agent import Agent

    assert not hasattr(Agent, "register_tool")

    class _BareHost(CliSetupToolsMixin):
        pass

    _BareHost().register_cli_setup_tools()

    for name in SETUP_TOOLS:
        assert name in _TOOL_REGISTRY


def test_the_mixin_does_not_shadow_the_shells_pre_flight_hooks():
    """Both are duck-typed single names resolved through the MRO. A same-named
    method here would silently disable the shell's own refusal."""
    for hook in ("skill_grant_covers_call", "policy_refusal_for_call"):
        assert hook not in vars(CliSetupToolsMixin)


# ---------------------------------------------------------------------------
# Deny by default — the model picks a CLI, not a command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", SETUP_TOOLS)
@pytest.mark.parametrize("binary", ["kubectl", "curl", "", "gh; rm -rf /", "GH.exe"])
def test_a_binary_with_no_policy_is_refused_not_executed(
    tools, monkeypatch, tool_name, binary
):
    """A CLI GAIA cannot gate is not one it offers to install either."""
    monkeypatch.setattr(cli_setup_tools, "detect", _forbidden("detect"))
    monkeypatch.setattr(cli_setup_tools, "run_install", _forbidden("run_install"))
    monkeypatch.setattr(
        cli_setup_tools, "start_device_login", _forbidden("start_device_login")
    )

    kwargs = {"binary": binary}
    if tool_name in MUTATING_TOOLS:
        kwargs["command"] = "winget install --id GitHub.cli"
    result = tools[tool_name](**kwargs)

    assert result["status"] == "error"
    assert result["has_errors"] is True
    assert "no setup policy" in result["error"]
    # Says what it CAN set up, so the model retries with a real one.
    assert "gh, pytest" in result["error"]


def test_the_binary_name_is_matched_case_and_whitespace_insensitively(
    tools, monkeypatch
):
    monkeypatch.setattr(cli_setup_tools, "detect", _returns(_status(READY)))
    monkeypatch.setattr(cli_setup_tools, "install_plan", _returns(WINGET))

    assert tools["check_cli_setup"]("  GH  ")["status"] == "success"


# ---------------------------------------------------------------------------
# check_cli_setup
# ---------------------------------------------------------------------------


def test_the_check_returns_the_state_and_the_commands_that_would_act_on_it(
    tools, monkeypatch
):
    monkeypatch.setattr(cli_setup_tools, "detect", _returns(_status(UNAUTHENTICATED)))
    monkeypatch.setattr(cli_setup_tools, "install_plan", _returns(WINGET))

    result = tools["check_cli_setup"]("gh")

    assert result["status"] == "success"
    assert result["state"] == UNAUTHENTICATED
    assert result["install_command"] == WINGET.command
    assert result["sign_in_command"] == "gh " + " ".join(GH.setup.auth_login_argv)
    assert result["can_install_here"] is True


def test_a_platform_with_no_packaged_install_says_so_rather_than_offering_one(
    tools, monkeypatch
):
    monkeypatch.setattr(cli_setup_tools, "detect", _returns(_status(MISSING)))
    monkeypatch.setattr(cli_setup_tools, "install_plan", _returns(None))

    result = tools["check_cli_setup"]("gh")

    assert result["can_install_here"] is False
    assert result["install_command"] == ""
    assert "no packaged install" in result["detail"]
    # The table's own docs URL, not a literal — a substring check against a
    # hardcoded URL reads as URL sanitization to CodeQL, and this asserts the
    # stronger property anyway: the message ends by pointing at that URL.
    assert result["detail"].endswith(f"install it from {GH.setup.install_docs_url}.")


def test_a_status_read_that_failed_is_surfaced_not_swallowed(tools, monkeypatch):
    def explode(*_args, **_kwargs):
        raise SetupError("gh is too old for --json")

    monkeypatch.setattr(cli_setup_tools, "detect", explode)

    result = tools["check_cli_setup"]("gh")

    assert result["status"] == "error"
    assert "too old" in result["error"]


# ---------------------------------------------------------------------------
# install_cli
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "supplied",
    [
        "winget install --id GitHub.cli --exact && whoami",
        "winget install --id Evil.Package --exact",
        "brew install gh",
        "",
    ],
)
def test_an_echoed_command_that_is_not_the_tables_own_refuses_the_install(
    tools, monkeypatch, supplied
):
    """The prompt shows the model's argument, so it has to be what runs.

    Running the table's command anyway would make the approval prompt a lie;
    refusing keeps the argument a display field that cannot be an injection
    point.
    """
    monkeypatch.setattr(cli_setup_tools, "install_plan", _returns(WINGET))
    monkeypatch.setattr(cli_setup_tools, "detect", _forbidden("detect"))
    monkeypatch.setattr(cli_setup_tools, "run_install", _forbidden("run_install"))

    result = tools["install_cli"]("gh", supplied)

    assert result["status"] == "error"
    assert "Refusing to install" in result["error"]
    assert WINGET.command in result["error"]


def test_whitespace_differences_in_the_echo_are_not_a_mismatch(tools, monkeypatch):
    monkeypatch.setattr(cli_setup_tools, "install_plan", _returns(WINGET))
    monkeypatch.setattr(cli_setup_tools, "detect", _returns(_status(READY)))
    monkeypatch.setattr(cli_setup_tools, "run_install", _forbidden("run_install"))

    result = tools["install_cli"]("gh", f"  {WINGET.command.replace(' ', '  ')} ")

    assert result.get("status") != "error"


def test_installing_over_a_working_copy_is_not_the_action_that_was_approved(
    tools, monkeypatch
):
    monkeypatch.setattr(cli_setup_tools, "install_plan", _returns(WINGET))
    monkeypatch.setattr(cli_setup_tools, "detect", _returns(_status(READY)))
    monkeypatch.setattr(cli_setup_tools, "run_install", _forbidden("run_install"))

    result = tools["install_cli"]("gh", WINGET.command)

    assert result["installed"] is False
    assert "already installed" in result["detail"]


def test_a_platform_with_no_packaged_install_refuses_rather_than_guessing(
    tools, monkeypatch
):
    monkeypatch.setattr(cli_setup_tools, "install_plan", _returns(None))
    monkeypatch.setattr(cli_setup_tools, "run_install", _forbidden("run_install"))

    result = tools["install_cli"]("gh", "apt install gh")

    assert result["status"] == "error"
    assert "no packaged install" in result["error"]


def test_an_install_that_leaves_the_cli_off_path_is_not_reported_as_success(
    tools, monkeypatch
):
    """The package manager said it worked and the binary still is not there —
    almost always a PATH this process inherited before the install."""
    monkeypatch.setattr(cli_setup_tools, "install_plan", _returns(WINGET))
    monkeypatch.setattr(cli_setup_tools, "detect", _returns(_status(MISSING)))
    monkeypatch.setattr(
        cli_setup_tools, "run_install", _returns("Successfully installed")
    )

    result = tools["install_cli"]("gh", WINGET.command)

    assert result["status"] == "error"
    assert "still not on PATH" in result["error"]


def test_a_successful_install_reports_the_state_it_left_behind(tools, monkeypatch):
    states = iter([_status(MISSING), _status(UNAUTHENTICATED)])
    monkeypatch.setattr(cli_setup_tools, "install_plan", _returns(WINGET))
    monkeypatch.setattr(cli_setup_tools, "detect", lambda *_a, **_k: next(states))
    monkeypatch.setattr(cli_setup_tools, "run_install", _returns("Installed gh"))

    result = tools["install_cli"]("gh", WINGET.command)

    assert result["installed"] is True
    assert result["state"] == UNAUTHENTICATED  # honest: it still needs a sign-in
    assert result["command_output"] == "Installed gh"


def test_an_install_failure_carries_what_the_package_manager_said(tools, monkeypatch):
    def explode(*_args, **_kwargs):
        raise SetupError("winget exited 1: no applicable installer")

    monkeypatch.setattr(cli_setup_tools, "install_plan", _returns(WINGET))
    monkeypatch.setattr(cli_setup_tools, "detect", _returns(_status(MISSING)))
    monkeypatch.setattr(cli_setup_tools, "run_install", explode)

    result = tools["install_cli"]("gh", WINGET.command)

    assert result["status"] == "error"
    assert "no applicable installer" in result["error"]


# ---------------------------------------------------------------------------
# sign_in_cli
# ---------------------------------------------------------------------------


def _sign_in_command() -> str:
    return "gh " + " ".join(GH.setup.auth_login_argv)


@pytest.mark.parametrize(
    "supplied", ["gh auth login --web", "gh auth token", "winget install gh", ""]
)
def test_an_echoed_sign_in_command_that_is_not_the_tables_own_is_refused(
    tools, monkeypatch, supplied
):
    monkeypatch.setattr(cli_setup_tools, "detect", _forbidden("detect"))
    monkeypatch.setattr(
        cli_setup_tools, "start_device_login", _forbidden("start_device_login")
    )

    result = tools["sign_in_cli"]("gh", supplied)

    assert result["status"] == "error"
    assert "Refusing to sign in" in result["error"]


def test_an_environment_variable_token_is_reported_not_signed_in_over(
    tools, monkeypatch
):
    """The one state a sign-in cannot fix, and the one most easily mistaken for
    a sign-in problem: gh refuses to store credentials while ``$GH_TOKEN`` is
    set, so starting a login would fail after prompting the user for nothing.
    """
    status = SetupStatus(
        binary="gh",
        state=ENV_TOKEN,
        detail="clear GH_TOKEN from the environment and ask again",
        token_source="GH_TOKEN",
    )
    monkeypatch.setattr(cli_setup_tools, "detect", _returns(status))
    monkeypatch.setattr(
        cli_setup_tools, "start_device_login", _forbidden("start_device_login")
    )

    result = tools["sign_in_cli"]("gh", _sign_in_command())

    assert result["status"] == "error"
    assert result["state"] == ENV_TOKEN
    assert "clear GH_TOKEN" in result["error"]


def test_signing_in_to_a_cli_that_is_not_installed_is_refused(tools, monkeypatch):
    monkeypatch.setattr(cli_setup_tools, "detect", _returns(_status(MISSING)))
    monkeypatch.setattr(
        cli_setup_tools, "start_device_login", _forbidden("start_device_login")
    )

    result = tools["sign_in_cli"]("gh", _sign_in_command())

    assert result["status"] == "error"
    assert "not installed" in result["error"]


def test_an_already_signed_in_cli_raises_no_browser_flow(tools, monkeypatch):
    monkeypatch.setattr(cli_setup_tools, "detect", _returns(_status(READY)))
    monkeypatch.setattr(cli_setup_tools, "install_plan", _returns(WINGET))
    monkeypatch.setattr(
        cli_setup_tools, "start_device_login", _forbidden("start_device_login")
    )

    result = tools["sign_in_cli"]("gh", _sign_in_command())

    assert result["signed_in"] is True
    assert "Already signed in" in result["detail"]


class _Login:
    """A device flow that has printed its code and is polling GitHub."""

    code = "ABCD-1234"
    url = "https://github.com/login/device"

    def __init__(self, error: str = ""):
        self.cancelled = 0
        self.waited = 0
        self._error = error

    def cancel(self):
        self.cancelled += 1

    def wait(self):
        self.waited += 1
        if self._error:
            raise SetupError(self._error)
        return ""


def _start_sign_in(monkeypatch, login, state=UNAUTHENTICATED):
    host = _Host()
    host.register_cli_setup_tools()
    monkeypatch.setattr(cli_setup_tools, "detect", _returns(_status(state)))
    monkeypatch.setattr(cli_setup_tools, "start_device_login", _returns(login))
    result = _TOOL_REGISTRY["sign_in_cli"]["function"]("gh", _sign_in_command())
    return host, result


def test_the_one_time_code_is_handed_to_the_user_before_the_wait(monkeypatch):
    """The code cannot be typed by the agent — that is what a one-time code is
    for — so the tool shows it and then waits on the CLI itself.
    """
    login = _Login()
    host, _ = _start_sign_in(monkeypatch, login)

    assert any("ABCD-1234" in warning for warning in host.warnings)
    assert any(login.url in warning for warning in host.warnings)
    assert login.waited == 1


def test_the_wait_is_on_the_cli_not_on_a_second_approval_prompt(monkeypatch):
    """Consent is taken before the flow starts. A second prompt would be asking
    the user to *report* progress, and every confirmation channel times out or
    blocks in a way that strands the polling child.
    """
    host, _ = _start_sign_in(monkeypatch, _Login())

    assert host.confirmations == [], "a second approval prompt was raised"


def test_a_sign_in_that_fails_reaps_the_child_and_says_so(monkeypatch):
    login = _Login(error="timed out with no completed browser sign-in")
    _, result = _start_sign_in(monkeypatch, login)

    assert result["status"] == "error"
    assert "timed out" in result["error"]
    assert login.cancelled >= 1, "the CLI child was left polling GitHub"


def test_a_sign_in_that_did_not_take_is_not_reported_as_success(monkeypatch):
    """gh can exit zero having granted nothing useful. The state after the flow
    is what decides, not the exit code.
    """
    _, result = _start_sign_in(monkeypatch, _Login())

    assert result["status"] == "error"
    assert "still" in result["error"]


class _ApprovingHost(_Host):
    """The user enters the code and comes back to say so."""

    def confirm_tool_execution(self, name, args) -> bool:
        self.confirmations.append((name, args))
        return True


def test_a_completed_sign_in_is_verified_against_the_cli_not_its_exit_code(
    monkeypatch,
):
    """A sign-in that "succeeded" without the scopes the skill needs is still a
    skill that cannot run. Finding out now beats finding out on first use."""

    class _Login:
        code = "ABCD-1234"
        url = "https://github.com/login/device"

        def cancel(self):
            pass

        def wait(self):
            return "Logged in as someone"

    states = iter([_status(UNAUTHENTICATED), _status(READY)])
    host = _ApprovingHost()
    host.register_cli_setup_tools()
    monkeypatch.setattr(cli_setup_tools, "detect", lambda *_a, **_k: next(states))
    monkeypatch.setattr(cli_setup_tools, "install_plan", _returns(WINGET))
    monkeypatch.setattr(cli_setup_tools, "start_device_login", _returns(_Login()))

    result = _TOOL_REGISTRY["sign_in_cli"]["function"]("gh", _sign_in_command())

    assert result["signed_in"] is True
    assert result["state"] == READY


def test_a_sign_in_that_lands_short_of_the_required_scopes_is_not_called_done(
    monkeypatch,
):
    class _Login:
        code = "ABCD-1234"
        url = "https://github.com/login/device"

        def cancel(self):
            pass

        def wait(self):
            return ""

    short = SetupStatus(
        binary="gh",
        state="insufficient_scopes",
        detail="the account is missing the permission read:org",
        missing_scopes=frozenset({"read:org"}),
    )
    states = iter([_status(UNAUTHENTICATED), short])
    _ApprovingHost().register_cli_setup_tools()
    monkeypatch.setattr(cli_setup_tools, "detect", lambda *_a, **_k: next(states))
    monkeypatch.setattr(cli_setup_tools, "start_device_login", _returns(_Login()))

    result = _TOOL_REGISTRY["sign_in_cli"]["function"]("gh", _sign_in_command())

    assert result["status"] == "error"
    assert "still not usable" in result["error"]
    assert result["missing_scopes"] == ["read:org"]
