# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Windows display-adapter registry walk and its two readers, on a fake winreg."""

import sys
import types

import pytest

from gaia.llm import lemonade_embedded, model_fit
from gaia.llm.display_adapters import DISPLAY_CLASS_KEY, iter_display_adapters

MEMORY = "HardwareInformation.qwMemorySize"


class _Key:
    def __init__(self, values=None, subkeys=None):
        self.values = values or {}
        self.subkeys = subkeys or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeWinreg:
    HKEY_LOCAL_MACHINE = "HKLM"

    def __init__(self, adapters):
        # None marks a subkey that refuses to open; insertion order is EnumKey's.
        self.root = None if adapters is None else _Key(subkeys=adapters)

    def OpenKey(self, parent, path):
        if parent == self.HKEY_LOCAL_MACHINE:
            if self.root is None or path != DISPLAY_CLASS_KEY:
                raise OSError(2, "class key missing")
            return self.root
        key = parent.subkeys[path]
        if key is None:
            raise PermissionError(5, "access denied")
        return key

    def EnumKey(self, key, index):
        names = list(key.subkeys)
        if index >= len(names):
            raise OSError(259, "no more data")
        return names[index]

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(2, name)
        return key.values[name], 1


ADAPTERS = {
    "0000": _Key(
        {"DriverDesc": "AMD Radeon(TM) 8060S", "DriverVersion": "32.0.1", MEMORY: 2**33}
    ),
    "0001": _Key({"DriverDesc": "Basic Display"}),
    "0002": None,
    "Properties": _Key({"Other": 1}),
    "0003": _Key({"DriverDesc": "Second GPU", MEMORY: "not-an-int"}),
}


@pytest.fixture
def fake_winreg(monkeypatch):
    def install(adapters=ADAPTERS):
        monkeypatch.setitem(sys.modules, "winreg", _FakeWinreg(adapters))

    return install


def test_walk_yields_adapters_and_skips_unreadable_subkeys(fake_winreg):
    fake_winreg()

    assert list(iter_display_adapters("DriverVersion")) == [
        {"DriverDesc": "AMD Radeon(TM) 8060S", "DriverVersion": "32.0.1"},
        {"DriverDesc": "Basic Display"},
        {"DriverDesc": "Second GPU"},
    ]


def test_walk_yields_nothing_without_the_class_key(fake_winreg):
    fake_winreg(None)

    assert list(iter_display_adapters()) == []


def test_embedded_lists_name_and_version_with_blank_for_a_missing_version(
    fake_winreg,
):
    fake_winreg()

    assert lemonade_embedded._windows_display_adapters() == [
        ("AMD Radeon(TM) 8060S", "32.0.1"),
        ("Basic Display", ""),
        ("Second GPU", ""),
    ]


@pytest.mark.parametrize(
    "name, expected",
    [
        ("AMD Radeon(TM) 8060S", 8.0),
        ("Basic Display", 0.0),  # matches, but records no memory size
        ("Second GPU", 0.0),  # memory size that isn't an int
        ("Not Here", 0.0),
    ],
)
def test_model_fit_reads_the_named_adapters_memory(
    fake_winreg, monkeypatch, name, expected
):
    fake_winreg()
    monkeypatch.setattr(model_fit, "sys", types.SimpleNamespace(platform="win32"))

    assert model_fit._windows_adapter_memory_gb(name) == expected


def test_model_fit_skips_a_matching_adapter_without_a_size_for_a_later_one(
    fake_winreg, monkeypatch
):
    fake_winreg(
        {
            "0000": _Key({"DriverDesc": "Twin"}),
            "0001": _Key({"DriverDesc": "Twin", MEMORY: 2**30}),
        }
    )
    monkeypatch.setattr(model_fit, "sys", types.SimpleNamespace(platform="win32"))

    assert model_fit._windows_adapter_memory_gb("Twin") == 1.0
