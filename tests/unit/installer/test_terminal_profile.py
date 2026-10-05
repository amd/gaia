# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Windows setup's terminal profile must name a font the setup actually ships.

Windows Terminal silently substitutes a face it cannot find, so a fragment that
drifts from the bundled fonts still "works" -- in the wrong font. These tests tie
the fragment, the .nsi that installs it, and the committed font pin together.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALLER_TUI = REPO_ROOT / "installer" / "tui"
NSI = INSTALLER_TUI / "nsis" / "gaia-setup.nsi"
FRAGMENT = INSTALLER_TUI / "nsis" / "wt-fragment.json"
sys.path.insert(0, str(INSTALLER_TUI))

import fetch_fonts  # noqa: E402

LOCK = fetch_fonts.load_lock()
ANSI_KEYS = [
    "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
    "brightBlack", "brightRed", "brightGreen", "brightYellow",
    "brightBlue", "brightMagenta", "brightCyan", "brightWhite",
]  # fmt: skip


def _nsi() -> str:
    return NSI.read_text(encoding="utf-8")


def _render(install_dir: str) -> dict:
    """The fragment as the setup writes it: the path JSON-escaped into the token."""
    text = FRAGMENT.read_text(encoding="utf-8")
    return json.loads(
        text.replace("__GAIA_INSTDIR__", install_dir.replace("\\", "\\\\"))
    )


def test_fragment_template_is_ascii():
    # The setup reads the template with FileRead, which decodes the ANSI code page.
    FRAGMENT.read_bytes().decode("ascii")


@pytest.mark.parametrize(
    "install_dir",
    [
        r"C:\Users\pat\AppData\Local\Programs\GAIA Terminal Hub",
        r"D:\Program Files\GAIA",
    ],
)
def test_rendered_fragment_is_valid_and_points_at_the_install(install_dir):
    fragment = _render(install_dir)
    (profile,) = fragment["profiles"]
    assert profile["commandline"] == f'"{install_dir}\\gaia-tui.exe"'
    assert profile["icon"] == f"{install_dir}\\gaia.ico"


def test_fragment_names_the_bundled_font_family():
    (profile,) = _render(r"C:\x")["profiles"]
    assert profile["font"]["face"] == LOCK["family"] == "IBM Plex Mono"
    assert isinstance(profile["font"]["size"], (int, float))
    # Windows Terminal's schema types cellHeight and padding as strings.
    assert isinstance(profile["font"]["cellHeight"], str)
    assert isinstance(profile["padding"], str)
    assert profile["name"] == "GAIA" and profile["hidden"] is False


def test_fragment_scheme_is_complete_and_dark():
    fragment = _render(r"C:\x")
    (profile,) = fragment["profiles"]
    schemes = {s["name"]: s for s in fragment["schemes"]}
    scheme = schemes[profile["colorScheme"]]
    hex_re = re.compile(r"^#[0-9A-Fa-f]{6}$")
    for key in ["background", "foreground", "cursorColor", *ANSI_KEYS]:
        assert hex_re.match(scheme[key]), f"{key} is {scheme.get(key)!r}"
    # The TUI picks its dark palette by querying the background; it must read as dark.
    r, g, b = (int(scheme["background"][i : i + 2], 16) for i in (1, 3, 5))
    assert max(r, g, b) < 0x40


def test_shortcut_guid_matches_the_fragment():
    (profile,) = _render(r"C:\x")["profiles"]
    m = re.search(r'!define WT_PROFILE_GUID\s+"([^"]+)"', _nsi())
    assert m and m.group(1) == profile["guid"]


@pytest.mark.parametrize("macro", ["InstallFontFace", "UninstallFontFace"])
def test_nsi_installs_and_removes_exactly_the_pinned_faces(macro):
    calls = re.findall(rf'!insertmacro {macro}\s+"([^"]+)"\s+"([^"]+)"', _nsi())
    pinned = [(f["filename"], f["full_name"]) for f in LOCK["fonts"]]
    assert sorted(calls) == sorted(pinned)


def test_every_pinned_face_is_in_the_family():
    for face in LOCK["fonts"]:
        assert face["full_name"].startswith(LOCK["family"])


def test_nsi_ships_the_font_licence():
    m = re.search(r'!define FONT_LICENSE\s+"([^"]+)"', _nsi())
    assert m and m.group(1) == LOCK["license"]["filename"]
    assert 'File "${FONTS_DIR}\\${FONT_LICENSE}"' in _nsi()
    assert 'Delete "$INSTDIR\\${FONT_LICENSE}"' in _nsi()


def test_lock_pins_an_official_ibm_release():
    assert LOCK["url"].startswith("https://github.com/IBM/plex/releases/download/")
    for entry in [LOCK, LOCK["license"], *LOCK["fonts"]]:
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])


# ── fetch_fonts.stage ──────────────────────────────────────────────────────


def _fake_release(tamper: str | None = None) -> tuple[bytes, dict]:
    members = {"pkg/LICENSE.txt": b"OFL", "pkg/A.ttf": b"regular", "pkg/B.ttf": b"bold"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, b"tampered" if name == tamper else data)
    archive = buf.getvalue()
    sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    lock = {
        "release": "fake",
        "size": len(archive),
        "sha256": sha(archive),
        "license": {
            "member": "pkg/LICENSE.txt",
            "filename": "OFL.txt",
            "sha256": sha(b"OFL"),
        },
        "fonts": [
            {"member": "pkg/A.ttf", "filename": "A.ttf", "sha256": sha(b"regular")},
            {"member": "pkg/B.ttf", "filename": "B.ttf", "sha256": sha(b"bold")},
        ],
    }
    return archive, lock


def test_stage_writes_every_verified_member(tmp_path):
    archive, lock = _fake_release()
    written = fetch_fonts.stage(archive, lock, tmp_path)
    assert sorted(p.name for p in written) == ["A.ttf", "B.ttf", "OFL.txt"]
    assert (tmp_path / "B.ttf").read_bytes() == b"bold"


def test_stage_refuses_an_archive_that_does_not_match_the_pin(tmp_path):
    archive, lock = _fake_release()
    lock["sha256"] = "0" * 64
    with pytest.raises(SystemExit, match="does not match"):
        fetch_fonts.stage(archive, lock, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_stage_refuses_a_tampered_member_and_stages_nothing(tmp_path):
    archive, lock = _fake_release(tamper="pkg/B.ttf")
    # The archive digest is re-pinned, so only the per-member check can catch it.
    lock["size"], lock["sha256"] = (
        len(archive),
        hashlib.sha256(archive).hexdigest(),
    )
    with pytest.raises(SystemExit, match="pkg/B.ttf"):
        fetch_fonts.stage(archive, lock, tmp_path / "out")
    assert not (tmp_path / "out").exists()
