# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia agent-program {status|download} --json``: what the terminal UI reads."""

import hashlib
import json

import pytest

import gaia.config as config_mod
import gaia.version as version_mod
from gaia.cli import build_parser
from gaia.daemon.sidecars import fetch, gaiacpp_fetch, gaiacpp_fetch_cli

PAYLOAD = b"gaiacpp-binary " * 20000


@pytest.fixture(autouse=True)
def _own_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(fetch.NATIVE_FLAGSHIP_PATH_ENV, raising=False)
    monkeypatch.setattr(version_mod, "__version__", "0.26.1")
    monkeypatch.setattr(
        gaiacpp_fetch.plat, "current_platform_key", lambda *a, **k: "win32-x64"
    )


@pytest.fixture
def release(tmp_path, monkeypatch):
    folder = tmp_path / "release"
    folder.mkdir()
    name = gaiacpp_fetch.ASSETS["win32-x64"]
    (folder / name).write_bytes(PAYLOAD)
    sha = hashlib.sha256(PAYLOAD).hexdigest()
    (folder / gaiacpp_fetch.SUMS_NAME).write_text(f"{sha}  {name}\n", encoding="utf-8")
    monkeypatch.setenv(gaiacpp_fetch.ASSETS_URL_ENV, folder.as_uri())
    monkeypatch.setattr(fetch, "native_flagship_stamp", lambda path: "0.26.1")
    return folder


def _run(argv, capsys):
    args = build_parser().parse_args(["agent-program", *argv])
    assert args.action == "agent-program"
    code = gaiacpp_fetch_cli.handle(args)
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    return code, lines


def test_status_reports_the_download_this_release_offers(release, tmp_path, capsys):
    code, lines = _run(["status", "--json"], capsys)
    assert code == 0
    assert lines == [
        {
            "installed": False,
            "path": None,
            "target": str(
                tmp_path / "home" / "bin" / fetch.native_flagship_executable_name()
            ),
            "can_download": True,
            "size_bytes": len(PAYLOAD),
            "version": "0.26.1",
            "reason": None,
            "retry": False,
        }
    ]


def test_status_says_plainly_when_the_release_has_no_gaiacpp(
    tmp_path, monkeypatch, capsys
):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv(gaiacpp_fetch.ASSETS_URL_ENV, empty.as_uri())
    code, lines = _run(["status", "--json"], capsys)
    assert code == 1
    assert lines[0]["can_download"] is False
    assert lines[0]["retry"] is False
    assert "this GAIA release has no gaiacpp to download" in lines[0]["reason"]


def test_status_marks_an_unreachable_release_as_worth_asking_again(monkeypatch, capsys):
    def unreachable(*a, **k):
        raise RuntimeError("could not reach the release: timed out")

    monkeypatch.setattr(gaiacpp_fetch, "download_info", unreachable)
    code, lines = _run(["status", "--json"], capsys)
    assert code == 1
    assert lines[0]["retry"] is True
    assert "timed out" in lines[0]["reason"]


def test_download_streams_progress_then_installs(release, tmp_path, capsys):
    code, lines = _run(["download", "--json"], capsys)
    target = tmp_path / "home" / "bin" / fetch.native_flagship_executable_name()
    assert code == 0
    assert lines[-1] == {"event": "done", "path": str(target)}
    progress = [line for line in lines if line["event"] == "progress"]
    assert progress[-1] == {
        "event": "progress",
        "bytes_done": len(PAYLOAD),
        "bytes_total": len(PAYLOAD),
    }
    assert target.read_bytes() == PAYLOAD
    assert fetch.native_install_record(target)["version"] == "0.26.1"


def test_download_refuses_a_file_that_fails_its_checksum(release, tmp_path, capsys):
    (release / gaiacpp_fetch.ASSETS["win32-x64"]).write_bytes(b"tampered")
    code, lines = _run(["download", "--json"], capsys)
    assert code == 1
    assert lines[-1]["event"] == "failed"
    assert "SHA-256 mismatch" in lines[-1]["error"]
    assert not list((tmp_path / "home" / "bin").glob("gaiacpp*"))


def test_the_command_is_hidden_from_help(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["-h"])
    assert "agent-program" not in capsys.readouterr().out
