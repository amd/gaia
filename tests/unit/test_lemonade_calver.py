# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Lemonade's CalVer switch must not silently disable GAIA's version gates.

Lemonade v2026.39.1 changed the version format from ``X.Y.Z`` to ``YYYY.WW.N``
(dev builds: ``YYYY.WW.0~<count>.<hash>``) and called out in its release notes
that anything parsing or comparing version strings has to be updated.

GAIA compares Lemonade versions in seven independent places — the base agent
readiness probe, the Lemonade client's compatibility gate, both installers,
``LemonadeInfo.version_tuple``, the flagship GAIA agent's readiness server, and
the frozen email sidecar (the last two keep their own copies because neither can
import ``gaia.installer``). Each turns a version into an int tuple. Two failure
modes matter and neither raises:

* a release CalVer that parses wrong would compare wrong, and
* a dev CalVer that fails to parse makes the gate return "can't tell", so it
  stops running at all — a green test suite with the check switched off.

These tests pin the real strings the server reports (verified against a live
v2026.39.1 ``/api/v1/health``), so a future parser "simplification" that drops
CalVer support fails here instead of in the field. Every copy is covered — the
tuple parsers by parametrization, the two that compare instead of returning a
tuple (the Lemonade client's gate and the flagship agent's) by their own cases —
so fixing only some of them still fails here. The flagship agent's copy was
missed on the first pass of exactly this change.
"""

import ast
from pathlib import Path

import pytest

from gaia.agents.base.readiness import parse_version, version_meets_min
from gaia.installer.init_command import InitCommand
from gaia.installer.lemonade_installer import LemonadeInfo, LemonadeInstaller
from gaia.llm.lemonade_client import LemonadeClient
from gaia.llm.lemonade_launcher import _VERSION_RE
from gaia.version import LEMONADE_MIN_VERSION, LEMONADE_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]

# What a live Lemonade v2026.39.1 reports in /api/v1/health.
RELEASE_CALVER = "2026.39.1"
# The dev/candidate shape documented in the v2026.39.1 release notes.
DEV_CALVER = "2026.39.0~12.abc1234"
# The last semver release, for the transition comparison.
LAST_SEMVER = "11.9.0"


def _installer_parse(version):
    """The bound installer method, which takes no state beyond ``self``."""
    return LemonadeInstaller._parse_version(None, version)


def _info_parse(version):
    """``LemonadeInfo.version_tuple`` — the copy that parses the INSTALLED version.

    Shielded in the normal flow (``check_installation`` fills ``version`` from
    ``get_installed_version``, which already strips the suffix), but callers
    construct ``LemonadeInfo`` directly, so it must not depend on that.
    """
    return LemonadeInfo(installed=True, version=version).version_tuple


# Every independent parser, so a fix applied to only some of them fails here.
PARSERS = [
    pytest.param(parse_version, id="readiness"),
    pytest.param(InitCommand._parse_version, id="init_command"),
    pytest.param(_installer_parse, id="lemonade_installer"),
    pytest.param(_info_parse, id="lemonade_info_version_tuple"),
]


def _load_function_from_source(relative_path: str, name: str):
    """Compile ONE pure function out of a hub agent's module, without importing it.

    The hub agents are separate distributions. The root ``conftest`` only puts
    one on ``sys.path`` when it already resolves somewhere, so ``gaia_agent`` is
    absent in the unit-test CI job and a plain import raises
    ``ModuleNotFoundError``. The usual answer is ``importorskip`` — but that
    turns this file's whole reason for existing into a silent skip, which is the
    exact "the gate stopped running and nothing said so" failure these tests
    guard against.

    Both parsers are self-contained and need only ``re``, so compile the real
    function out of the real file instead. No package import, no FastAPI, no
    skip — and it still fails if someone edits the source it reads.
    """
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            module = ast.Module(
                body=[
                    ast.ImportFrom(
                        module="__future__",
                        names=[ast.alias(name="annotations", asname=None)],
                        level=0,
                    ),
                    ast.Import(names=[ast.alias(name="re", asname=None)]),
                    node,
                ],
                type_ignores=[],
            )
            ast.fix_missing_locations(module)
            namespace: dict = {}
            exec(compile(module, relative_path, "exec"), namespace)  # noqa: S102
            return namespace[name]
    raise AssertionError(f"{name} not found in {relative_path} — did it move?")


def _email_parse(version):
    """The frozen email sidecar keeps its own copy — cover it too."""
    parse = _load_function_from_source(
        "hub/agents/email/python/gaia_agent_email/api_routes.py", "_parse_version"
    )
    return parse(version)


# The flagship agent's copy compares rather than exposing a tuple, so it is
# covered by its own tests below instead of the ``PARSERS`` list.
def _gaia_server_meets_min(version, minimum):
    """The flagship GAIA agent's readiness gate.

    It reads ``/api/v1/health``'s ``version`` VERBATIM — nothing normalizes the
    CalVer dev suffix away first — so it must tolerate it itself.
    """
    meets = _load_function_from_source(
        "hub/agents/gaia/python/gaia_agent/server.py", "_version_meets_min"
    )
    return meets(version, minimum)


@pytest.mark.parametrize("parser", PARSERS + [pytest.param(_email_parse, id="email")])
def test_release_calver_parses_to_its_real_components(parser):
    """``2026.39.1`` must compare as (2026, 39, 1), not fail or truncate."""
    assert parser(RELEASE_CALVER) == (2026, 39, 1)


@pytest.mark.parametrize("parser", PARSERS + [pytest.param(_email_parse, id="email")])
def test_dev_calver_parses_instead_of_going_indeterminate(parser):
    """A ``0~12.abc1234`` build must still yield a comparable tuple.

    Without this the int() raises, the parser returns None, and every caller
    treats the version as unknown — which they deliberately do NOT block on.
    The gate would be off for all candidate builds.
    """
    assert parser(DEV_CALVER) == (2026, 39, 0)


@pytest.mark.parametrize("parser", PARSERS + [pytest.param(_email_parse, id="email")])
def test_calver_outranks_the_last_semver_release(parser):
    """The 11.9.0 -> 2026.39.1 switch must read as an upgrade, not a downgrade."""
    assert parser(RELEASE_CALVER) > parser(LAST_SEMVER)


@pytest.mark.parametrize("parser", PARSERS + [pytest.param(_email_parse, id="email")])
def test_garbage_is_still_unparseable(parser):
    """Tolerating CalVer must not turn every string into a fake version."""
    assert parser("not-a-version") is None


def test_pinned_version_is_accepted_by_the_readiness_gate():
    """Whatever LEMONADE_VERSION is pinned to must clear the supported floor."""
    assert version_meets_min(LEMONADE_VERSION, LEMONADE_MIN_VERSION) is True


def test_dev_build_of_the_pin_still_clears_the_floor():
    assert version_meets_min(DEV_CALVER, LEMONADE_MIN_VERSION) is True


def test_genuinely_old_version_is_still_rejected():
    """The gate must still bite — CalVer tolerance is not a blanket pass."""
    assert version_meets_min("9.1.4", LEMONADE_MIN_VERSION) is False


@pytest.mark.parametrize(
    "cli_output, expected",
    [
        ("lemonade version 2026.39.1", "2026.39.1"),
        ("lemonade version 2026.39.0~12.abc1234", "2026.39.0"),
        ("lemonade-server 11.9.0", "11.9.0"),
    ],
)
def test_cli_version_regex_extracts_calver(cli_output, expected):
    """``lemonade --version`` output is the other CalVer entry point."""
    match = _VERSION_RE.search(cli_output)
    assert match is not None, f"no version parsed from {cli_output!r}"
    assert match.group(1) == expected


# -- the flagship agent's readiness gate ------------------------------------
# It compares instead of returning a tuple, so it gets its own cases. This is
# the copy most users actually hit, and the one missed on the first pass.


def test_flagship_agent_accepts_release_calver():
    assert _gaia_server_meets_min(RELEASE_CALVER, LEMONADE_MIN_VERSION) is True


def test_flagship_agent_accepts_dev_calver():
    """Regression: this returned None, silently disabling the readiness check."""
    assert _gaia_server_meets_min(DEV_CALVER, LEMONADE_MIN_VERSION) is True


def test_flagship_agent_still_rejects_old_versions():
    assert _gaia_server_meets_min("9.1.4", LEMONADE_MIN_VERSION) is False


def test_flagship_agent_reports_garbage_as_indeterminate():
    assert _gaia_server_meets_min("not-a-version", LEMONADE_MIN_VERSION) is None


def test_flagship_agent_accepts_the_pinned_version():
    assert _gaia_server_meets_min(LEMONADE_VERSION, LEMONADE_MIN_VERSION) is True


# -- the Lemonade client's compatibility gate -------------------------------
# Its parser is a closure inside ``_check_version_compatibility``, so it is
# reached through the public method rather than the ``PARSERS`` list. The method
# returns False ONLY below the supported floor.


def _client_gate(actual):
    client = LemonadeClient.__new__(LemonadeClient)
    return LemonadeClient._check_version_compatibility(
        client, LEMONADE_VERSION, actual_version=actual, quiet=True
    )


def test_client_gate_accepts_release_calver():
    assert _client_gate(RELEASE_CALVER) is True


def test_client_gate_accepts_dev_calver():
    """Regression: ``int("0~12")`` raised, and the except-branch passed it anyway.

    It returned True for the *wrong* reason — "parsing failed, do not block" —
    so a genuinely old dev build would have been waved through too.
    """
    assert _client_gate(DEV_CALVER) is True


def test_client_gate_rejects_an_old_dev_build():
    """The case the pre-fix except-branch got wrong: old AND unparseable."""
    assert _client_gate("9.1.0~4.deadbee") is False


def test_client_gate_still_rejects_old_releases():
    assert _client_gate("9.1.4") is False
