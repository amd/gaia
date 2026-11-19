# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The OpenAPI exporter refuses to run from a checkout other than the imported one."""

from gaia.ui import _export_checkout, export_openapi


def test_matching_checkout_proceeds(monkeypatch):
    monkeypatch.chdir(_export_checkout.MODULE_CHECKOUT)
    assert export_openapi.main(["--check"]) == 0


def test_other_checkout_is_refused(tmp_path, monkeypatch, capsys):
    other = tmp_path / "other"
    (other / "src" / "gaia").mkdir(parents=True)
    (other / "src" / "gaia" / "__init__.py").write_text("")
    monkeypatch.chdir(other)

    assert export_openapi.main([]) == 2
    err = capsys.readouterr().err
    assert str(_export_checkout.MODULE_CHECKOUT) in err
    assert str(other.resolve()) in err
    assert f"PYTHONPATH set to {other.resolve() / 'src'}" in err
    assert "$PWD" not in err


def test_outside_any_checkout_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert export_openapi.main(["--check"]) == 2
    assert "repo root" in capsys.readouterr().err
