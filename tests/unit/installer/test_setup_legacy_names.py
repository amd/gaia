# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Windows setup installs the terminal under ``gaia-tui`` ids, not "Terminal Hub" (#5204).

An earlier setup used ``%LOCALAPPDATA%\\Programs\\GAIA Terminal Hub`` and the
``Software\\GAIA\\TerminalHub`` / ``GAIATerminalHub`` registry keys. A fresh
install must write none of them, and an upgrade or uninstall must still find
and remove them, or the old folder, PATH entry and Installed-apps entry are
orphaned. makensis only builds on a Windows runner, so these tests read the
script; the CI smoke test exercises the built setup.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
NSIS_DIR = REPO_ROOT / "installer" / "tui" / "nsis"
NSI = NSIS_DIR / "gaia-setup.nsi"
SMOKE = NSIS_DIR / "smoke-test.ps1"
JOURNEY = REPO_ROOT / "installer" / "headless" / "windows-journey.ps1"
GO_SETUP_DIR = REPO_ROOT / "tui" / "internal" / "update" / "setupdir_windows.go"
TUI_GUIDE = REPO_ROOT / "docs" / "guides" / "tui.mdx"

LEGACY_SETTINGS = r"Software\GAIA\TerminalHub"
LEGACY_UNINST = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\GAIATerminalHub"
LEGACY_FOLDER = "GAIA Terminal Hub"


def _nsi() -> str:
    return NSI.read_text(encoding="utf-8")


def _define(name: str) -> str:
    m = re.search(rf'^!define {name}\s+"([^"]*)"', _nsi(), re.MULTILINE)
    assert m, f"gaia-setup.nsi no longer defines {name}"
    return m.group(1)


def _block(kind: str, name: str) -> str:
    end = "SectionEnd" if kind == "Section" else "FunctionEnd"
    head = (
        rf'^Section "{re.escape(name)}"'
        if kind == "Section"
        else rf"^Function {re.escape(name)}\n"
    )
    m = re.search(rf"{head}.*?^{end}", _nsi(), re.M | re.S)
    assert m, f"gaia-setup.nsi has no {kind} {name}"
    return m.group(0)


def _install_dir() -> str:
    m = re.search(r'^InstallDir "([^"]+)"', _nsi(), re.M)
    assert m, "gaia-setup.nsi sets no InstallDir"
    return m.group(1).replace("${DEFAULT_DIR}", _define("DEFAULT_DIR"))


# ── A fresh install uses the current names ─────────────────────────────────


def test_install_folder_is_lowercase_gaia_tui_not_the_legacy_name():
    folder = _install_dir()
    assert folder == r"$LOCALAPPDATA\Programs\gaia-tui"
    # Never the desktop app's folder: its uninstaller deletes its own folder.
    assert folder.lower() != r"$localappdata\programs\gaia"


def test_registry_ids_are_the_current_ones():
    assert _define("SETTINGS_KEY") == r"Software\GAIA\gaia-tui"
    assert _define("UNINST_KEY").endswith(r"\Uninstall\gaia-tui")
    assert _define("GAIA_FONTS_KEY") == r"${SETTINGS_KEY}\Fonts"
    assert re.search(
        r'^InstallDirRegKey HKCU "\$\{SETTINGS_KEY\}" "InstallDir"', _nsi(), re.M
    )


def test_nothing_writes_a_legacy_id():
    for line in _nsi().splitlines():
        if re.match(
            r"\s*(WriteReg\w+|CreateShortcut|CreateDirectory|SetOutPath)\b", line
        ):
            assert "TerminalHub" not in line, line
            assert "Terminal Hub" not in line, line
            assert "LEGACY" not in line, line


def test_legacy_ids_are_kept_only_to_find_an_old_install():
    assert _define("LEGACY_SETTINGS_KEY") == LEGACY_SETTINGS
    assert _define("LEGACY_UNINST_KEY") == LEGACY_UNINST
    assert _define("LEGACY_FONTS_KEY") == r"${LEGACY_SETTINGS_KEY}\Fonts"
    # Literal legacy ids live only in their defines.
    body = re.sub(r'^!define LEGACY_\w+\s+"[^"]*"', "", _nsi(), flags=re.M)
    code = "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith(";")
    )
    assert "TerminalHub" not in code
    assert "Terminal Hub" not in code.replace("${LEGACY_PRODUCT_NAME}", "")


# ── Upgrading an install made under the legacy ids ─────────────────────────


def test_an_old_install_counts_as_the_terminal_being_installed():
    on_init = _block("Function", ".onInit")
    assert '"${LEGACY_SETTINGS_KEY}" "InstallDir"' in on_init


def test_an_old_install_in_the_default_folder_moves_to_the_new_one():
    on_init = _block("Function", ".onInit")
    assert "Call FindLegacyInstall" in on_init
    find = _block("Function", "FindLegacyInstall")
    assert '"${LEGACY_SETTINGS_KEY}" "InstallDir"' in find
    assert "${LEGACY_DEFAULT_DIR}" in find
    assert _define("LEGACY_DEFAULT_DIR") == rf"$LOCALAPPDATA\Programs\{LEGACY_FOLDER}"
    # Only the default folder moves; one the user chose is kept as theirs.
    assert "StrCpy $INSTDIR" in find


def test_upgrade_checks_and_stops_programs_in_the_old_folder_first():
    preflight = _block("Function", "Preflight")
    assert "$LegacyDir" in preflight
    assert preflight.count("Call AbortIfRunning") >= 6
    assert "Call StopLegacyDaemon" in preflight


def test_the_terminal_section_migrates_before_writing_new_keys():
    main = _block("Section", "GAIA Terminal UI (terminal)")
    assert "Call MigrateLegacyInstall" in main
    assert main.index("Call MigrateLegacyInstall") < main.index("WriteRegStr")


def test_migration_removes_the_old_folder_keys_and_path_entry():
    migrate = _block("Function", "MigrateLegacyInstall")
    for removed in (
        'DeleteRegKey HKCU "${LEGACY_UNINST_KEY}"',
        'DeleteRegKey HKCU "${LEGACY_SETTINGS_KEY}"',
        'Delete "$LegacyDir\\${TUI_EXE}"',
        'Delete "$LegacyDir\\${AGENT_EXE}"',
        'Delete "$LegacyDir\\${CORE_EXE}"',
        'RMDir /r "$LegacyDir\\_internal"',
        'Delete "$LegacyDir\\Uninstall.exe"',
        "Call RemoveLegacyFromUserPath",
    ):
        assert removed in migrate, f"migration no longer does: {removed}"
    # Anything the user put in the old folder stays.
    assert 'RMDir "$LegacyDir"' in migrate
    assert 'RMDir /r "$LegacyDir"' not in migrate
    # Its uninstaller would race this setup's PATH edit; it is never run.
    assert "Exec" not in migrate


def test_migration_keeps_the_record_of_which_fonts_are_gaias():
    """Uninstall removes only fonts the setup recorded installing."""
    migrate = _block("Function", "MigrateLegacyInstall")
    assert migrate.count("MoveFontRecord") == 4
    move = re.search(r"^!macro MoveFontRecord.*?^!macroend", _nsi(), re.M | re.S)
    assert move, "no MoveFontRecord macro"
    assert '"${LEGACY_FONTS_KEY}"' in move.group(0)
    assert 'WriteRegStr HKCU "${GAIA_FONTS_KEY}"' in move.group(0)
    assert migrate.index("MoveFontRecord") < migrate.index(
        'DeleteRegKey HKCU "${LEGACY_SETTINGS_KEY}"'
    )


def test_the_old_folder_is_dropped_from_path_with_the_same_safe_editor():
    remove = _block("Function", "RemoveLegacyFromUserPath")
    assert "Call ReadUserPath" in remove
    assert "Call PathWithoutSegment" in remove
    assert 'StrCpy $R2 "$LegacyDir"' in remove
    # The installer needs the segment remover too now, not only the uninstaller.
    assert '!insertmacro PathWithoutSegment ""' in _nsi()
    assert '!insertmacro PathWithoutSegment "un."' in _nsi()


# ── Uninstall cleans up whatever the legacy ids left ───────────────────────


def test_uninstall_removes_current_and_legacy_keys_and_shortcuts():
    uninstall = _block("Section", "Uninstall")
    for removed in (
        'DeleteRegKey HKCU "${UNINST_KEY}"',
        'DeleteRegKey HKCU "${SETTINGS_KEY}"',
        'DeleteRegKey HKCU "${LEGACY_UNINST_KEY}"',
        'DeleteRegKey HKCU "${LEGACY_SETTINGS_KEY}"',
        'Delete "$DESKTOP\\${LEGACY_PRODUCT_NAME}.lnk"',
    ):
        assert removed in uninstall, f"uninstall no longer does: {removed}"
    # Fonts are read before either key is deleted.
    assert uninstall.index("UninstallFontFace") < uninstall.index(
        'DeleteRegKey HKCU "${LEGACY_SETTINGS_KEY}"'
    )


def test_uninstall_removes_fonts_recorded_under_either_key():
    macro = re.search(r"^!macro UninstallFontFace.*?^!macroend", _nsi(), re.M | re.S)
    assert macro
    assert '"${GAIA_FONTS_KEY}"' in macro.group(0)
    assert '"${LEGACY_FONTS_KEY}"' in macro.group(0)


# ── Everything else that names the folder or keys agrees ───────────────────


def test_the_tui_self_updater_reads_the_key_the_setup_writes():
    go = GO_SETUP_DIR.read_text(encoding="utf-8")
    m = re.search(r"registry\.OpenKey\(registry\.CURRENT_USER, `([^`]+)`", go)
    assert m, "setupdir_windows.go no longer opens an HKCU key"
    assert m.group(1) == _define("SETTINGS_KEY")


def test_smoke_test_checks_the_current_folder_and_entry():
    text = SMOKE.read_text(encoding="utf-8")
    assert r'$tuiDir    = "$env:LOCALAPPDATA\Programs\gaia-tui"' in text
    assert (
        r"$tuiArp    = 'HKCU:\Software\Microsoft\Windows\CurrentVersion"
        r"\Uninstall\gaia-tui'" in text
    )


def test_journey_accepts_the_layout_the_release_under_test_recorded():
    """The journeys install a published release, which may predate #5204."""
    text = JOURNEY.read_text(encoding="utf-8")
    for dir_, key in (
        ("gaia-tui", r"Software\GAIA\gaia-tui"),
        (LEGACY_FOLDER, LEGACY_SETTINGS),
    ):
        assert '"$env:LOCALAPPDATA\\Programs\\' + dir_ + '"' in text
        assert rf"'HKCU:\{key}'" in text
    # The layout comes from the key the setup wrote, never from a guess.
    assert "Where-Object { Test-Path $_.Key }" in text
    assert "foreach ($d in $tuiDirs)" in text


def test_an_explicit_install_folder_wins_over_an_old_custom_one():
    """``/D=`` leaves $INSTDIR off the default; the legacy folder must not undo it."""
    find = _block("Function", "FindLegacyInstall")
    assert '${AndIf} $INSTDIR == "${DEFAULT_DIR}"' in find
    assert find.index("${DEFAULT_DIR}") < find.index('StrCpy $INSTDIR "$R0"')


def test_smoke_test_upgrades_a_planted_legacy_install():
    smoke = SMOKE.read_text(encoding="utf-8")
    assert "$legacyDir" in smoke and "$legacyArp" in smoke
    assert "the legacy folder was left behind" in smoke
    assert "the legacy Installed-apps entry was left behind" in smoke
    assert "the legacy folder was left on the user PATH" in smoke


def test_docs_name_the_current_install_folder():
    guide = TUI_GUIDE.read_text(encoding="utf-8")
    assert r"%LOCALAPPDATA%\Programs\gaia-tui" in guide
    assert LEGACY_FOLDER not in guide
