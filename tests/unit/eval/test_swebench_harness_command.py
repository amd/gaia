# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The SWE-bench grading command must work from the harness's own cwd.

``evaluate`` runs the harness with ``cwd=report_dir``. A relative
``--predictions_path`` (CI passes ``--out eval-out/swebench``) then pointed
nowhere, and every instance went ungraded with FileNotFoundError.
"""

from pathlib import Path

from gaia.eval.bench.swebench import containerized_harness_command, harness_command


def _arg(command, flag):
    return command[command.index(flag) + 1]


def test_relative_paths_still_resolve_from_the_report_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = Path("eval-out") / "swebench"
    report_dir = out / "swebench"
    report_dir.mkdir(parents=True)
    preds = out / "predictions.jsonl"
    preds.write_text("{}\n", encoding="utf-8")

    command = harness_command(preds, "django__django-15851", report_dir)

    preds_arg, report_arg = (
        Path(_arg(command, "--predictions_path")),
        Path(_arg(command, "--report_dir")),
    )
    assert preds_arg.is_absolute() and report_arg.is_absolute()
    # What the harness sees: relative paths are joined onto its cwd.
    monkeypatch.chdir(report_dir)
    assert preds_arg.is_file()
    assert report_arg.samefile(Path.cwd())


def test_the_container_command_keeps_its_own_mount_paths(tmp_path):
    preds = tmp_path / "predictions.jsonl"
    preds.write_text("{}\n", encoding="utf-8")
    command = containerized_harness_command(preds, "x__y-1", tmp_path / "r")
    assert _arg(command, "--predictions_path") == "/preds/predictions.jsonl"
    assert _arg(command, "--report_dir") == "/work"
