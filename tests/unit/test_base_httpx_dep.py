# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The base install must keep httpx below 1.0, pre-releases included.

openai inspects whatever ``httpx`` is already imported for ``httpx.Timeout``,
and the httpx 1.0 pre-releases removed it. ``pip install --pre amd-gaia``
(how a release candidate is installed) resolved httpx 1.0.dev6 through
tavily-python, and every chat turn then failed with
``module 'httpx' has no attribute 'Timeout'``.
"""

from __future__ import annotations

from packaging.requirements import Requirement

from tests.unit.test_base_keyring_dep import _parse_install_requires


def _httpx_requirement() -> Requirement:
    reqs = [Requirement(r) for r in _parse_install_requires()]
    matches = [r for r in reqs if r.name.lower() == "httpx"]
    assert matches, (
        "setup.py install_requires must declare httpx: core imports it directly "
        f"and an unbounded transitive pin lets --pre pull httpx 1.0. Got: {reqs}"
    )
    return matches[0]


def test_base_install_requires_caps_httpx_below_1() -> None:
    spec = _httpx_requirement().specifier
    assert not spec.contains("1.0.dev6", prereleases=True)
    assert not spec.contains("1.0.0")


def test_base_install_requires_allows_current_httpx() -> None:
    spec = _httpx_requirement().specifier
    assert spec.contains("0.28.1")
    assert spec.contains("0.27.0")
