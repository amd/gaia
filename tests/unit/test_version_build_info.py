# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Which GAIA is running: a release, a release candidate, or a build from a checkout."""

import json
from importlib.metadata import PackageNotFoundError

import pytest

from gaia import version

INSTALLED = "/venv/Lib/site-packages/gaia/version.py"


class _Dist:
    def __init__(self, recorded):
        self.recorded = recorded

    def read_text(self, name):
        assert name == "direct_url.json"
        return None if self.recorded is None else json.dumps(self.recorded)


@pytest.fixture
def installed(monkeypatch):
    def use(number, recorded, where=INSTALLED):
        monkeypatch.setattr(version, "get_package_version", lambda: number)
        monkeypatch.setattr(version, "__file__", where)
        monkeypatch.setattr(
            "importlib.metadata.distribution", lambda _name: _Dist(recorded)
        )
        monkeypatch.setattr(version, "get_git_hash", lambda **_kw: "57d72c9a")

    return use


def test_a_published_release(installed):
    installed("0.25.4", None)
    assert version.build_info() == {
        "version": "0.25.4",
        "kind": "release",
        "rc": None,
        "commit": None,
    }


def test_a_release_candidate_names_its_number(installed):
    installed("0.25.5rc2", None)
    info = version.build_info()
    assert info["kind"] == "release_candidate" and info["rc"] == "2"


def test_a_wheel_built_from_a_checkout_is_a_local_build(installed):
    installed(
        "0.25.4",
        {"url": "file:///C:/tmp/amd_gaia-0.25.4-py3-none-any.whl", "archive_info": {}},
    )
    assert version.build_info()["kind"] == "local"


def test_an_editable_install_is_source_with_its_commit(installed):
    installed(
        "0.25.4", {"url": "file:///C:/Users/you/gaia", "dir_info": {"editable": True}}
    )
    info = version.build_info()
    assert info["kind"] == "source" and info["commit"] == "57d72c9a"


def test_code_outside_site_packages_is_source_whatever_metadata_says(installed):
    installed("0.25.4", None, where="/home/you/gaia/src/gaia/version.py")
    assert version.build_info()["kind"] == "source"


def test_no_package_metadata_is_source(monkeypatch):
    monkeypatch.setattr(version, "get_package_version", lambda: "0.25.4")
    monkeypatch.setattr(version, "__file__", INSTALLED)

    def missing(_name):
        raise PackageNotFoundError("amd-gaia")

    monkeypatch.setattr("importlib.metadata.distribution", missing)
    monkeypatch.setattr(version, "get_git_hash", lambda **_kw: "")
    info = version.build_info()
    assert info["kind"] == "source" and info["commit"] is None
