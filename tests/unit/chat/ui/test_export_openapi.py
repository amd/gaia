# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The committed Agent UI schema snapshot must match the live backend models.

``api.schemas.json`` is what the Agent UI's generated TypeScript types are
built from; if it lags the pydantic models, the frontend's compile-time drift
guard (``src/types/apiContract.ts``) checks against a stale backend.
"""

from gaia.ui import export_openapi


def test_committed_schemas_match_the_backend_models():
    committed = export_openapi.ARTIFACT_PATH.read_text(encoding="utf-8")
    assert committed == export_openapi.render(export_openapi.build_schemas()), (
        f"{export_openapi.ARTIFACT_PATH} is out of date with the Agent UI backend "
        f"models. Regenerate from the repo root: {export_openapi.REGENERATE_HINT}"
    )


def test_check_fails_on_a_stale_snapshot(tmp_path, monkeypatch, capsys):
    stale = tmp_path / "api.schemas.json"
    stale.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(export_openapi, "ARTIFACT_PATH", stale)

    assert export_openapi.main(["--check"]) == 1
    assert export_openapi.REGENERATE_HINT in capsys.readouterr().err


def test_write_then_check_round_trips(tmp_path, monkeypatch):
    target = tmp_path / "api.schemas.json"
    monkeypatch.setattr(export_openapi, "ARTIFACT_PATH", target)

    assert export_openapi.main([]) == 0
    assert export_openapi.main(["--check"]) == 0
