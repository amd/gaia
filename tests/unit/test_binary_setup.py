# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The CLI self-setup engine — install a skill's CLI, then sign it in.

``gaia.skills.binaries`` answers "may this command run?". ``binary_setup``
answers the question before it: the CLI is absent, or logged out, and the agent
can fix that itself once the user says yes.

Four properties, in order of how badly a regression would hurt:

1. **Each of the five states is told apart, because each needs a different
   remedy.** The one that matters most is ``ENV_TOKEN``: a credential from
   ``$GH_TOKEN`` *works*, and gh refuses to store another while the variable is
   set — so reporting it as "logged out" sends the user to run a command that
   cannot succeed.
2. **A failed status read is not a logged-out user.** An old CLI without the
   ``--json`` flag raises rather than being reported as signed out.
3. **What the user approves is what runs.** ``InstallPlan.command`` is a
   rendering of ``InstallPlan.argv``, never a second source of truth.
4. **No credential reaches the log or the model's context** — neither an access
   token nor the sign-in one-time code.

Every subprocess here is faked through the module's own injection points
(``which=``, ``runner=``, ``popen=``). Nothing installs anything, and nothing
talks to GitHub.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys

import pytest

from gaia.skills.binaries import BINARY_POLICIES
from gaia.skills.binary_setup import (
    ENV_TOKEN,
    INSUFFICIENT_SCOPES,
    MISSING,
    READY,
    UNAUTHENTICATED,
    InstallPlan,
    SetupError,
    detect,
    install_plan,
    run_install,
    scrub_secrets,
    start_device_login,
)

GH = BINARY_POLICIES["gh"]
PYTEST = BINARY_POLICIES["pytest"]


# ---------------------------------------------------------------------------
# Fixtures — the real shapes `gh auth status --json hosts` writes to stdout.
# It always exits 0, and reports state as data rather than prose.
# ---------------------------------------------------------------------------

LOGGED_OUT = '{"hosts":{}}'


def _account(**overrides) -> dict:
    """One entry of ``hosts["github.com"]``, signed in and fully scoped."""
    account = {
        "state": "success",
        "active": True,
        "host": "github.com",
        "login": "someone",
        "tokenSource": "keyring",
        "scopes": "gist, read:org, repo, workflow",
        "gitProtocol": "https",
    }
    account.update(overrides)
    return account


def _hosts(*accounts: dict) -> str:
    return json.dumps({"hosts": {"github.com": list(accounts)}})


def _on_path(name: str) -> str:
    return f"/usr/bin/{name}"


def _off_path(_name: str) -> None:
    return None


def _runner(stdout: str = "", *, stderr: str = "", returncode: int = 0):
    """A fake ``auth status`` that answers with *stdout* and records its argv."""

    def run(argv, **_kwargs):
        run.argv = list(argv)
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    run.argv = []
    return run


def _never_runs(argv, **_kwargs):
    raise AssertionError(f"ran {argv!r} when nothing should have been run")


def _detect(stdout: str, **kwargs):
    return detect(GH, which=_on_path, runner=_runner(stdout, **kwargs))


# ---------------------------------------------------------------------------
# Detection — the five states
# ---------------------------------------------------------------------------


def test_a_cli_that_is_not_on_path_is_missing_and_nothing_is_run():
    """Remedy is an install, and the status command is not even attempted."""
    status = detect(GH, which=_off_path, runner=_never_runs)

    assert status.state == MISSING
    assert status.needs_install is True
    assert status.needs_login is False
    assert status.ready is False
    assert "not installed" in status.detail


def test_no_accounts_at_all_is_unauthenticated():
    status = _detect(LOGGED_OUT)

    assert status.state == UNAUTHENTICATED
    assert status.needs_login is True
    assert status.needs_install is False
    assert status.account == ""


def test_a_revoked_or_expired_token_is_unauthenticated_not_ready():
    """gh still lists the account; ``state`` is how it says the token is dead."""
    status = _detect(_hosts(_account(state="error")))

    assert status.state == UNAUTHENTICATED
    assert status.needs_login is True
    assert status.account == "someone"
    assert "revoked" in status.detail or "expired" in status.detail


def test_an_environment_variable_token_is_its_own_state_and_not_a_login_problem():
    """The regression that matters most.

    ``$GH_TOKEN`` authenticates gh perfectly well, and gh refuses to store its
    own credentials while it is set. Folding this into ``unauthenticated``
    sends the user to run a sign-in the CLI will reject.
    """
    status = _detect(_hosts(_account(tokenSource="GH_TOKEN", scopes="gist")))

    assert status.state == ENV_TOKEN
    assert status.needs_login is False
    assert status.needs_install is False
    assert status.ready is False
    assert status.token_source == "GH_TOKEN"
    # The remedy is clearing the variable, never signing in.
    assert "clear GH_TOKEN" in status.detail
    assert "sign in again" not in status.detail.lower()


def test_an_env_token_wins_over_a_scope_shortfall():
    """Even short of scopes, a login is still not the move — see above."""
    status = _detect(_hosts(_account(tokenSource="GITHUB_TOKEN", scopes="gist")))

    assert status.state == ENV_TOKEN
    assert status.needs_login is False


def test_a_credential_store_source_is_not_mistaken_for_an_env_variable():
    """Stores are lower case (``keyring``), variables are not — shape is the test."""
    assert _detect(_hosts(_account(tokenSource="keyring"))).state == READY
    assert _detect(_hosts(_account(tokenSource="oauth_token"))).state == READY


def test_a_token_missing_a_required_scope_names_exactly_what_is_absent():
    status = _detect(_hosts(_account(scopes="gist")))

    assert status.state == INSUFFICIENT_SCOPES
    assert status.needs_login is True
    assert status.missing_scopes == frozenset({"repo", "read:org"})
    assert status.scopes == frozenset({"gist"})
    assert "read:org" in status.detail and "repo" in status.detail


def test_one_missing_scope_is_named_on_its_own():
    status = _detect(_hosts(_account(scopes="gist, repo, workflow")))

    assert status.state == INSUFFICIENT_SCOPES
    assert status.missing_scopes == frozenset({"read:org"})


def test_a_keyring_token_with_the_required_scopes_is_ready():
    status = _detect(_hosts(_account(scopes="repo, read:org")))

    assert status.state == READY
    assert status.ready is True
    assert status.needs_login is False
    assert status.needs_install is False
    assert status.account == "someone"
    assert status.token_source == "keyring"


def test_extra_scopes_beyond_what_is_required_are_fine():
    """gh adds its own floor; verification checks presence, never absence."""
    assert _detect(_hosts(_account(scopes="repo, read:org, gist, workflow"))).ready


def test_the_active_account_is_the_one_judged():
    """A machine can carry several logins; commands use the active one.

    Judging the wrong one reports scopes that are not in play — here it would
    call a short-scoped session ready.
    """
    status = _detect(
        _hosts(
            _account(login="other", active=False, scopes="repo, read:org"),
            _account(login="active-one", active=True, scopes="gist"),
        )
    )

    assert status.account == "active-one"
    assert status.state == INSUFFICIENT_SCOPES
    assert status.missing_scopes == frozenset({"repo", "read:org"})


def test_detect_runs_the_policys_own_status_command():
    """The argv comes from the table, so it is the table that is tested."""
    runner = _runner(LOGGED_OUT)
    detect(GH, which=_on_path, runner=runner)

    assert runner.argv == ["gh", "auth", "status", "--json", "hosts"]


def test_a_cli_needing_no_sign_in_is_ready_once_it_is_installed():
    """``pytest`` has no ``setup`` entry — being on PATH is the whole story."""
    status = detect(PYTEST, which=_on_path, runner=_never_runs)

    assert status.state == READY
    assert status.needs_login is False


def test_as_dict_is_a_stable_json_shape():
    payload = _detect(_hosts(_account(scopes="gist"))).as_dict()

    assert payload["binary"] == "gh"
    assert payload["state"] == INSUFFICIENT_SCOPES
    assert payload["scopes"] == ["gist"]
    assert payload["missing_scopes"] == ["read:org", "repo"]  # sorted, not a set
    assert json.loads(json.dumps(payload)) == payload


# ---------------------------------------------------------------------------
# A status read that failed is not a verdict
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stdout,stderr,returncode",
    [
        # An older gh that has never heard of --json.
        ("", "unknown flag: --json\n", 1),
        # The pre-JSON prose form, which is what parsing text used to hit.
        ("", "github.com\n  x Not logged in to github.com\n", 1),
        ("not json at all", "", 0),
        ("", "", 0),
    ],
)
def test_unparseable_status_output_raises_rather_than_reporting_logged_out(
    stdout, stderr, returncode
):
    """A read that told us nothing is not "the user is signed out".

    Reporting it as one sends someone to re-authenticate a session that was
    working, and hides the real fix (upgrade the CLI).
    """
    with pytest.raises(SetupError) as excinfo:
        _detect(stdout, stderr=stderr, returncode=returncode)

    message = str(excinfo.value)
    assert "Could not read 'gh' sign-in state" in message
    assert "too old" in message


def test_a_status_command_that_cannot_be_run_at_all_raises():
    def explode(argv, **_kwargs):
        raise OSError("no such process")

    with pytest.raises(SetupError, match="Could not read 'gh' sign-in state"):
        detect(GH, which=_on_path, runner=explode)


def test_a_token_in_the_failed_reads_stderr_is_scrubbed_before_it_is_raised():
    with pytest.raises(SetupError) as excinfo:
        _detect("", stderr="failed with token ghp_" + "A" * 24, returncode=1)

    assert "ghp_" not in str(excinfo.value)
    assert "[redacted]" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Install plan
# ---------------------------------------------------------------------------


def test_windows_installs_with_winget_non_interactively():
    """An agent's child has no terminal, so an agreement prompt is a hang."""
    plan = install_plan(GH, platform="win32")

    assert plan.argv[0] == "winget"
    assert "GitHub.cli" in plan.argv
    assert "--accept-package-agreements" in plan.argv
    assert "--accept-source-agreements" in plan.argv


def test_macos_installs_with_brew():
    assert install_plan(GH, platform="darwin").argv == ("brew", "install", "gh")


def test_linux_has_no_automated_install_and_says_so_with_a_url():
    """Deliberate: gh is in no stock distro repo, and adding GitHub's apt/dnf
    repo is a root-level trust change that belongs to the user."""
    assert install_plan(GH, platform="linux") is None
    assert GH.setup.install_docs_url == "https://cli.github.com"


@pytest.mark.parametrize("platform", ["win32", "darwin", "darwin23", "linux2"])
def test_the_platform_is_matched_on_its_prefix(platform):
    plan = install_plan(GH, platform=platform)
    assert (plan is None) is platform.startswith("linux")


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_the_approved_line_and_the_argv_that_runs_cannot_diverge(platform):
    """``command`` is display only — a rendering of ``argv``, not a second copy.

    If they could drift, the user would approve one command and another would
    run, which is the whole reason the tool refuses a mismatched echo.
    """
    plan = install_plan(GH, platform=platform)

    assert plan.command == " ".join(plan.argv)
    assert shlex.split(plan.command) == list(plan.argv)


def test_a_cli_with_no_setup_entry_refuses_to_be_installed():
    with pytest.raises(SetupError, match="no setup entry"):
        install_plan(PYTEST, platform="win32")


# ---------------------------------------------------------------------------
# Running an install — real subprocesses, but only ever this interpreter
# ---------------------------------------------------------------------------


def _python_plan(code: str) -> InstallPlan:
    """A plan that runs harmless Python instead of a package manager."""
    return InstallPlan(
        binary="fake-cli",
        argv=(sys.executable, "-c", code),
        docs_url="https://example.invalid/install",
    )


def test_a_successful_install_returns_its_output():
    output = run_install(_python_plan("print('installed fake-cli')"), timeout=60.0)

    assert "installed fake-cli" in output


def test_a_failed_install_raises_with_the_manual_url_and_what_it_said():
    plan = _python_plan("import sys; sys.stderr.write('no such package'); sys.exit(3)")

    with pytest.raises(SetupError) as excinfo:
        run_install(plan, timeout=60.0)

    message = str(excinfo.value)
    assert "exit 3" in message
    assert "https://example.invalid/install" in message
    assert "no such package" in message


def test_a_missing_package_manager_is_an_actionable_error_not_a_traceback():
    plan = InstallPlan(
        binary="fake-cli",
        argv=("gaia-no-such-package-manager", "install", "fake-cli"),
        docs_url="https://example.invalid/install",
    )

    with pytest.raises(SetupError) as excinfo:
        run_install(plan, timeout=60.0)

    assert "could not run 'gaia-no-such-package-manager'" in str(excinfo.value)
    assert "https://example.invalid/install" in str(excinfo.value)


def test_an_install_that_outruns_its_timeout_says_it_may_still_be_running():
    """A package manager killed mid-install has not necessarily done nothing,
    so the error says how to check rather than claiming a clean failure."""
    plan = _python_plan("import time; time.sleep(30)")

    with pytest.raises(SetupError) as excinfo:
        run_install(plan, timeout=0.5)

    message = str(excinfo.value)
    assert "did not finish within 0.5s" in message
    assert "fake-cli --version" in message


def test_install_output_is_scrubbed_before_the_model_ever_sees_it():
    output = run_install(
        _python_plan("print('token ghp_' + 'B' * 24)"),
        timeout=60.0,
    )

    assert "ghp_" not in output
    assert "[redacted]" in output


# ---------------------------------------------------------------------------
# Secret hygiene
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    [
        "ghp_" + "A" * 36,
        "gho_" + "B" * 36,
        "ghu_" + "C" * 36,
        "ghs_" + "D" * 36,
        "ghr_" + "E" * 36,
        "github_pat_" + "F" * 22,
        "github_pat_" + "1A" * 12 + "_" + "9z" * 10,
    ],
)
def test_every_github_token_shape_is_redacted(token):
    scrubbed = scrub_secrets(f"authenticated with {token} ok")

    assert token not in scrubbed
    assert "[redacted]" in scrubbed


@pytest.mark.parametrize(
    "text",
    [
        "the ghost of a token",
        "gh auth status --json hosts",
        "ghp_short",  # too short to be a credential
        "",
    ],
)
def test_ordinary_text_is_left_alone(text):
    assert scrub_secrets(text) == text


# ---------------------------------------------------------------------------
# Browser device login
# ---------------------------------------------------------------------------

DEVICE_OUTPUT = [
    "! First copy your one-time code: ABCD-1234\n",
    "Open this URL to continue in your web browser: "
    "https://github.com/login/device\n",
]


class _FakeStream:
    """``Popen.stdout``: iterable, and closeable like the real file object.

    The reader thread blocks on this pipe, so closing it is part of reaping the
    child — a fake without ``close()`` would let a leak pass unnoticed.
    """

    def __init__(self, lines):
        self._lines = iter(lines)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._lines)

    def close(self):
        self.closed = True


class _FakeLoginProcess:
    """The CLI child: prints *lines*, then stays alive until told to stop.

    ``poll()`` answers ``None`` until something reaps it, which is what a real
    device flow does — it polls GitHub while the user is in their browser.
    """

    def __init__(self, lines, *, exit_code: int = 0, hangs: bool = False):
        self.stdout = _FakeStream(lines)
        self._exit_code = exit_code
        self._hangs = hangs
        self.returncode = None
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self._hangs:
            raise subprocess.TimeoutExpired(cmd="gh", timeout=timeout or 0)
        if self.returncode is None:
            self.returncode = self._exit_code
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


def _popen_returning(process):
    def factory(argv, **kwargs):
        factory.calls.append((list(argv), kwargs))
        return process

    factory.calls = []
    return factory


def test_the_one_time_code_and_url_are_parsed_out_of_the_clis_own_output():
    process = _FakeLoginProcess(DEVICE_OUTPUT)
    popen = _popen_returning(process)

    login = start_device_login(GH, popen=popen, code_wait_s=5.0)

    assert login.code == "ABCD-1234"
    assert login.url == "https://github.com/login/device"
    assert popen.calls[0][0] == ["gh", *GH.setup.auth_login_argv]
    login.cancel()


def test_the_sign_in_child_gets_no_terminal_and_no_browser_hook(monkeypatch):
    """``BROWSER`` names a command gh will execute. A value inherited from the
    environment would run under the sign-in rather than open a page — and the
    user is handed the URL anyway."""
    monkeypatch.setenv("BROWSER", "calc.exe")
    popen = _popen_returning(_FakeLoginProcess(DEVICE_OUTPUT))

    login = start_device_login(GH, popen=popen, code_wait_s=5.0)

    _argv, kwargs = popen.calls[0]
    assert "BROWSER" not in kwargs["env"]
    assert kwargs["stdin"] == subprocess.DEVNULL
    login.cancel()


def test_a_login_that_never_prints_a_code_fails_fast_instead_of_hanging():
    """There is nothing to hand the user, so GAIA says so rather than waiting
    on a process that is not going to speak."""
    process = _FakeLoginProcess(["Logging in to github.com...\n", "working\n"])

    with pytest.raises(SetupError) as excinfo:
        start_device_login(GH, popen=_popen_returning(process), code_wait_s=1.0)

    message = str(excinfo.value)
    assert "did not produce a browser sign-in code within 1s" in message
    assert "gh auth login --web" in message
    assert process.killed, "the child was left polling GitHub with nobody to reap it"


def test_a_code_with_no_url_is_refused_rather_than_half_handed_over():
    process = _FakeLoginProcess(["! First copy your one-time code: ABCD-1234\n"])

    with pytest.raises(SetupError, match="no URL"):
        start_device_login(GH, popen=_popen_returning(process), code_wait_s=2.0)

    assert process.killed


def test_a_cli_with_no_sign_in_command_refuses_to_start_one():
    with pytest.raises(SetupError, match="no setup entry"):
        start_device_login(PYTEST, popen=_popen_returning(_FakeLoginProcess([])))


def test_the_returned_output_no_longer_carries_the_one_time_code():
    """The code goes to the user through the prompt and stops there.

    Returning it would put a live credential in the model's context, and from
    there into the transcript permanently.
    """
    login = start_device_login(
        GH,
        popen=_popen_returning(_FakeLoginProcess(DEVICE_OUTPUT)),
        code_wait_s=5.0,
    )

    output = login.wait()

    assert "ABCD-1234" not in output
    assert "[one-time code]" in output


def test_a_failed_sign_in_says_so_without_leaking_the_code():
    login = start_device_login(
        GH,
        popen=_popen_returning(_FakeLoginProcess(DEVICE_OUTPUT, exit_code=1)),
        code_wait_s=5.0,
    )

    with pytest.raises(SetupError) as excinfo:
        login.wait()

    message = str(excinfo.value)
    assert "exit 1" in message
    assert "ABCD-1234" not in message
    assert "gh auth login --web" in message


def test_a_user_who_never_finishes_is_given_up_on_and_the_child_is_reaped():
    process = _FakeLoginProcess(DEVICE_OUTPUT, hangs=True)
    login = start_device_login(GH, popen=_popen_returning(process), code_wait_s=5.0)
    login.timeout_s = 0.5

    with pytest.raises(SetupError) as excinfo:
        login.wait()

    assert "timed out" in str(excinfo.value)
    assert "Nothing was changed" in str(excinfo.value)
    assert process.killed


def test_cancel_is_safe_to_call_twice():
    login = start_device_login(
        GH,
        popen=_popen_returning(_FakeLoginProcess(DEVICE_OUTPUT)),
        code_wait_s=5.0,
    )

    login.cancel()
    login.cancel()  # must not raise — the tool calls it in a finally block
