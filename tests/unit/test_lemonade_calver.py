# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Lemonade's CalVer switch must not silently disable GAIA's version gates.

Lemonade v2026.39.1 changed the version format from ``X.Y.Z`` to ``YYYY.WW.N``
(dev builds: ``YYYY.WW.0~<count>.<hash>``) and called out in its release notes
that anything parsing or comparing version strings has to be updated.

GAIA compares Lemonade versions through one core implementation,
:func:`gaia.version.parse_version`. The flagship agent's readiness server
imports ``gaia.agents.base.readiness``, so it uses the core gate directly. Two
failure modes matter and neither raises:

* a release CalVer that parses wrong would compare wrong, and
* a dev CalVer that fails to parse makes the gate return "can't tell", so it
  stops running at all — a green test suite with the check switched off.

These tests pin the real strings the server reports (verified against a live
v2026.39.1 ``/api/v1/health``), so a future parser "simplification" that drops
CalVer support fails here instead of in the field. Every entry point is covered
— the tuple parsers by parametrization, the Lemonade client's gate (which
compares instead of returning a tuple) by its own cases. The flagship agent's
former copy was missed on the first pass of exactly this change, which is why
the core copies were collapsed onto one.
"""

import ast
from pathlib import Path

import pytest

from gaia.agents.base.readiness import parse_version, version_meets_min
from gaia.installer.lemonade_installer import LemonadeInfo
from gaia.llm.lemonade_client import LemonadeClient, LemonadeVersionError
from gaia.llm.lemonade_launcher import _VERSION_RE
from gaia.version import LEMONADE_MIN_VERSION, LEMONADE_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]

# What a live Lemonade v2026.39.1 reports in /api/v1/health.
RELEASE_CALVER = "2026.39.1"
# The dev/candidate shape documented in the v2026.39.1 release notes.
DEV_CALVER = "2026.39.0~12.abc1234"
# A dev build of the supported floor, which must clear it.
DEV_OF_FLOOR = f"{LEMONADE_MIN_VERSION}~12.abc1234"
# The last semver release, for the transition comparison.
LAST_SEMVER = "11.9.0"


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
    pytest.param(_info_parse, id="lemonade_info_version_tuple"),
]


@pytest.mark.parametrize("parser", PARSERS)
def test_release_calver_parses_to_its_real_components(parser):
    """``2026.39.1`` must compare as (2026, 39, 1), not fail or truncate."""
    assert parser(RELEASE_CALVER) == (2026, 39, 1)


@pytest.mark.parametrize("parser", PARSERS)
def test_dev_calver_parses_instead_of_going_indeterminate(parser):
    """A ``0~12.abc1234`` build must still yield a comparable tuple.

    Without this the int() raises, the parser returns None, and every caller
    treats the version as unknown — which they deliberately do NOT block on.
    The gate would be off for all candidate builds.
    """
    assert parser(DEV_CALVER) == (2026, 39, 0)


@pytest.mark.parametrize("parser", PARSERS)
def test_calver_outranks_the_last_semver_release(parser):
    """The 11.9.0 -> 2026.39.1 switch must read as an upgrade, not a downgrade."""
    assert parser(RELEASE_CALVER) > parser(LAST_SEMVER)


@pytest.mark.parametrize("parser", PARSERS)
def test_garbage_is_still_unparseable(parser):
    """Tolerating CalVer must not turn every string into a fake version."""
    assert parser("not-a-version") is None


def test_pinned_version_is_accepted_by_the_readiness_gate():
    """Whatever LEMONADE_VERSION is pinned to must clear the supported floor."""
    assert version_meets_min(LEMONADE_VERSION, LEMONADE_MIN_VERSION) is True


def test_dev_build_of_the_pin_still_clears_the_floor():
    assert version_meets_min(DEV_OF_FLOOR, LEMONADE_MIN_VERSION) is True


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
# The copy most users actually hit, and the one missed on the first pass. It now
# calls the core gate; read its source so a re-vendored copy fails here.


def test_flagship_agent_uses_the_core_gate():
    path = "hub/agents/gaia/python/gaia_agent/server.py"
    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
    defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    imported = {
        (node.module, alias.name)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "_version_meets_min" not in defined
    assert ("gaia.agents.base.readiness", "version_meets_min") in imported
    assert ("gaia.version", "LEMONADE_MIN_VERSION") in imported


# -- the Lemonade client's compatibility gate -------------------------------
# Reached through the method rather than the ``PARSERS`` list, because it
# compares instead of returning a tuple. It raises ONLY below the supported floor.


def _client_gate(actual):
    import logging

    client = LemonadeClient.__new__(LemonadeClient)
    client.log = logging.getLogger("test_lemonade_calver")
    client.base_url = "http://localhost:13305/api/v1"
    return LemonadeClient._check_version_compatibility(
        client, LEMONADE_VERSION, actual_version=actual, quiet=True
    )


def test_client_gate_accepts_release_calver():
    assert _client_gate(LEMONADE_MIN_VERSION) is True


def test_client_gate_accepts_dev_calver():
    """Regression: ``int("0~12")`` raised, and the except-branch passed it anyway.

    It returned True for the *wrong* reason — "parsing failed, do not block" —
    so a genuinely old dev build would have been waved through too.
    """
    assert _client_gate(DEV_OF_FLOOR) is True


def test_client_gate_rejects_an_old_dev_build():
    """The case the pre-fix except-branch got wrong: old AND unparseable."""
    with pytest.raises(LemonadeVersionError):
        _client_gate("9.1.0~4.deadbee")


def test_client_gate_still_rejects_old_releases():
    with pytest.raises(LemonadeVersionError):
        _client_gate("9.1.4")


def test_the_core_parsers_are_one_implementation():
    """All in-core entry points delegate, so a format change is a one-line fix."""
    from gaia.version import parse_version as canonical

    probe = "2026.39.0~12.abc1234"
    expected = canonical(probe)
    for parser in (
        parse_version,
        _info_parse,
    ):
        assert parser(probe) == expected
