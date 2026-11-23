# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The agent can read the images GAIA generated, and nothing else in GAIA's folder (#8473).

The image tool saves under ``<GAIA_CONFIG_DIR>/cache/sd/images``, inside the
write-blocked state folder. Reads of an image file sitting directly there pass;
every other path in the state folder, every write, and every link or ``..``
that leads out of that one folder is refused exactly as before.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import gaia.config as gaia_config
from gaia import security
from gaia.agents.tools.path_access import read_access_error
from gaia.agents.tools.shell_tools import (
    ShellToolsMixin,
    _parse_line,
    _ps_code_outside_literals,
)
from gaia.security import PathValidator
from gaia.vlm.mixin import VLMToolsMixin

WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Windows path forms")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class _Tree:
    def __init__(self, root: Path):
        self.state = root / "state"
        self.images = self.state / "cache" / "sd" / "images"
        self.work = root / "work"
        self.images.mkdir(parents=True)
        self.work.mkdir()
        self.png = self.images / "sunset_SDXL-Turbo_20261118_184627_4b0819fb.png"
        self.png.write_bytes(PNG)
        (self.state / "config.json").write_text('{"default_model": "x"}')
        (self.state / "keys").mkdir()
        (self.state / "keys" / "signing.png").write_bytes(PNG)
        (self.state / "lemonade").mkdir()
        (self.state / "lemonade" / "last_key").write_text("secret")
        (self.state / "cache" / "other.png").write_bytes(PNG)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A GAIA state folder, write-blocked like ~/.gaia, holding one generated image."""
    tree = _Tree(tmp_path)
    monkeypatch.setattr(gaia_config, "GAIA_CONFIG_DIR", tree.state)
    real_state = os.path.realpath(tree.state)
    monkeypatch.setattr(
        security, "BLOCKED_DIRECTORIES", security.BLOCKED_DIRECTORIES | {real_state}
    )
    monkeypatch.setattr(
        security,
        "GAIA_STATE_DIRECTORIES",
        security.GAIA_STATE_DIRECTORIES | {real_state},
    )
    return tree


@pytest.fixture
def validator(tree):
    return PathValidator(allowed_paths=[str(tree.work)])


def _readable(validator, path) -> bool:
    return validator.validate_read(str(path), prompt_user=False)[0]


class _VlmHost(VLMToolsMixin):
    def __init__(self, validator):
        self.path_validator = validator
        self.vlm_client = MagicMock()
        self.vlm_client.extract_from_image.return_value = "a sunset"
        self.vlm_client.vlm_model = "test-vlm"


class _ShellHost(ShellToolsMixin):
    def __init__(self, validator):
        self.path_validator = validator

    def full_access_active(self):
        return False


def _shell_refusal(host, command, cwd):
    """The pre-flight verdict and the execution-time path check, as one answer."""
    error, steps = host._line_verdict(command, str(cwd))
    if error is not None:
        return error
    for step in steps or _parse_line(command, bypass_gates=False)[0]:
        refusal = host._path_traversal_refusal(
            step, str(cwd), frozenset(), line=command
        )
        if refusal is not None:
            return refusal
    return None


class TestGeneratedImageReads:
    def test_analyze_image_reads_a_generated_png(self, tree, validator):
        result = _VlmHost(validator)._analyze_image(str(tree.png))

        assert result["status"] == "success", result
        assert result["description"] == "a sunset"

    def test_read_access_check_passes(self, tree, validator):
        assert read_access_error(_VlmHost(validator), tree.png) is None

    @pytest.mark.parametrize("suffix", [".jpg", ".jpeg", ".webp", ".gif", ".PNG"])
    def test_every_image_type_the_tool_saves(self, tree, validator, suffix):
        image = tree.images / f"gen{suffix}"
        image.write_bytes(PNG)
        assert _readable(validator, image)

    def test_writes_stay_blocked(self, tree, validator):
        assert validator.is_write_blocked(str(tree.png))[0]
        assert validator.is_write_blocked(str(tree.images / "new.png"))[0]
        assert not validator.is_path_allowed(str(tree.png), prompt_user=False)
        # Nobody is offered a grant that would open writes too.
        assert validator.grant_refusal_reason(str(tree.png))

    def test_relocated_config_dir_moves_the_readable_folder(
        self, tree, validator, monkeypatch, tmp_path
    ):
        moved = tmp_path / "moved"
        (moved / "cache" / "sd" / "images").mkdir(parents=True)
        image = moved / "cache" / "sd" / "images" / "gen.png"
        image.write_bytes(PNG)
        monkeypatch.setattr(gaia_config, "GAIA_CONFIG_DIR", moved)

        assert _readable(validator, image)
        assert not _readable(validator, tree.png)

    def test_gaia_home_sets_the_folder_the_tool_saves_to(self, tmp_path):
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("GAIA_CONFIG_DIR", "GAIA_CONFIG_FILE")
        }
        env["GAIA_HOME"] = str(tmp_path / "relocated")
        env["PYTHONPATH"] = os.pathsep.join(
            [str(Path(security.__file__).resolve().parents[1])]
            + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
        )
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "import gaia.config as c; print(c.generated_images_dir())",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        assert Path(out) == tmp_path / "relocated" / "cache" / "sd" / "images"


class TestEverythingElseStaysRefused:
    @pytest.mark.parametrize(
        "relative",
        [
            "../../../config.json",
            "../../../lemonade/last_key",
            "../../../keys/signing.png",
            "../../other.png",
            "../../../cache/sd/images/../../other.png",
        ],
    )
    def test_dotdot_out_of_the_images_folder(self, tree, validator, relative):
        path = os.path.join(str(tree.images), *relative.split("/"))
        assert not _readable(validator, path)

    def test_state_files_outside_the_images_folder(self, tree, validator):
        for path in (
            tree.state / "config.json",
            tree.state / "keys" / "signing.png",
            tree.state / "lemonade" / "last_key",
            tree.state / "cache" / "other.png",
        ):
            assert not _readable(validator, path), path

    def test_a_non_image_in_the_images_folder(self, tree, validator):
        note = tree.images / "notes.txt"
        note.write_text("x")
        assert not _readable(validator, note)

    def test_an_image_in_a_subfolder(self, tree, validator):
        (tree.images / "sub").mkdir()
        nested = tree.images / "sub" / "x.png"
        nested.write_bytes(PNG)
        assert not _readable(validator, nested)

    def test_the_folder_itself_is_not_listable(self, tree, validator):
        assert not _readable(validator, tree.images)

    def test_a_symlink_named_png_pointing_at_config(self, tree, validator):
        link = tree.images / "evil.png"
        try:
            link.symlink_to(tree.state / "config.json")
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"cannot create symlinks here: {exc}")
        assert not _readable(validator, link)

    def test_a_symlink_to_an_image_elsewhere(self, tree, validator, tmp_path):
        elsewhere = tmp_path / "private"
        elsewhere.mkdir()
        (elsewhere / "photo.png").write_bytes(PNG)
        link = tree.images / "photo.png"
        try:
            link.symlink_to(elsewhere / "photo.png")
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"cannot create symlinks here: {exc}")
        assert not _readable(validator, link)

    def test_images_folder_re_pointed_elsewhere(self, tree, validator, tmp_path):
        elsewhere = tmp_path / "private"
        elsewhere.mkdir()
        (elsewhere / "photo.png").write_bytes(PNG)
        for child in tree.images.iterdir():
            child.unlink()
        tree.images.rmdir()
        try:
            if os.name == "nt":
                import _winapi

                _winapi.CreateJunction(str(elsewhere), str(tree.images))
            else:
                tree.images.symlink_to(elsewhere, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"cannot create a junction or symlink here: {exc}")

        assert not _readable(validator, tree.images / "photo.png")
        assert not _readable(validator, elsewhere / "photo.png")

    def test_a_hard_link_to_a_state_file(self, tree, validator):
        target = tree.state / "keys" / "signing.png"
        link = tree.images / "linked.png"
        try:
            os.link(target, link)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"cannot create hard links here: {exc}")
        assert not _readable(validator, link)

    def test_a_withheld_folder_wins(self, tree, validator):
        validator.withhold_path(str(tree.state))
        assert not _readable(validator, tree.png)

    def test_a_missing_image_is_not_readable(self, tree, validator):
        assert not _readable(validator, tree.images / "nope.png")


@WINDOWS
class TestWindowsPathForms:
    def test_case_variants_resolve_to_the_same_file(self, tree, validator):
        upper = str(tree.png).replace("images", "IMAGES").replace(".png", ".PNG")
        assert _readable(validator, upper)
        assert not _readable(
            validator, str(tree.state / "CONFIG.JSON").replace("cache", "CACHE")
        )

    def test_short_names_resolve_before_the_check(self, tree, validator):
        import ctypes

        def short(path):
            buf = ctypes.create_unicode_buffer(1024)
            ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024)
            return buf.value

        short_png = short(tree.png)
        short_config = short(tree.state / "config.json")
        if not short_png or short_png == str(tree.png):
            pytest.skip("8.3 short names are disabled on this volume")

        assert _readable(validator, short_png)
        assert not _readable(validator, short_config)


class TestShell:
    def test_a_read_only_line_may_name_the_image(self, tree, validator):
        host = _ShellHost(validator)
        assert _shell_refusal(host, f"cat {tree.png}", tree.work) is None

    def test_writing_into_the_images_folder_is_refused(self, tree, validator):
        source = tree.work / "a.png"
        source.write_bytes(PNG)
        host = _ShellHost(validator)
        for command in (
            f"cp {source} {tree.images / 'new.png'}",
            f"copy {source} {tree.images / 'new.png'}",
            f"cp {source} {tree.png}",
            f"mv {source} {tree.png}",
            f"rm {tree.png}",
        ):
            refusal = _shell_refusal(host, command, tree.work)
            assert refusal is not None, command
        assert tree.png.read_bytes() == PNG

    def test_a_read_only_line_still_cannot_reach_other_state_files(
        self, tree, validator
    ):
        host = _ShellHost(validator)
        for target in (
            tree.state / "config.json",
            tree.state / "lemonade" / "last_key",
            Path(os.path.join(str(tree.images), "..", "..", "..", "config.json")),
        ):
            assert _shell_refusal(host, f"cat {target}", tree.work) is not None

    def test_without_the_line_the_check_stays_closed(self, tree, validator):
        host = _ShellHost(validator)
        steps, _ = _parse_line(f"cat {tree.png}", bypass_gates=False)
        assert (
            host._path_traversal_refusal(steps[0], str(tree.work), frozenset())
            is not None
        )


def _verdict_like_an_allowed_file(tree, validator, template):
    """The image's verdict next to the same line on a PNG in an allowed folder."""
    host = _ShellHost(validator)
    mine = tree.work / "mine.png"
    mine.write_bytes(PNG)
    image = _shell_refusal(host, template.format(p=tree.png), tree.work)
    allowed = _shell_refusal(host, template.format(p=mine), tree.work)
    return image, allowed


def _assert_read_like_an_allowed_file(tree, validator, template):
    image, allowed = _verdict_like_an_allowed_file(tree, validator, template)
    if allowed is None:
        assert image is None, image
        return
    # Whatever the line needs (an approval, say), the image is not why.
    assert image is not None
    assert image.get("tier") == allowed.get("tier"), (image, allowed)
    assert "forbidden path" not in image["error"], image
    assert "Write blocked" not in image["error"], image


class TestShellQuotedAndWindowsReads:
    """Paths with spaces need quotes, so those are the forms a model uses (#8702)."""

    @pytest.mark.parametrize(
        "template",
        ['cat "{p}"', 'ls -l "{p}"', 'wc -c "{p}"', 'file "{p}"'],
    )
    def test_a_quoted_read(self, tree, validator, template):
        _assert_read_like_an_allowed_file(tree, validator, template)

    @pytest.mark.skipif(os.name == "nt", reason="cmd.exe passes single quotes on")
    def test_a_single_quoted_read(self, tree, validator):
        _assert_read_like_an_allowed_file(tree, validator, "cat '{p}'")

    @WINDOWS
    def test_single_quotes_stay_closed_where_cmd_does_not_strip_them(
        self, tree, validator
    ):
        # A program may split 'a -o b' where GAIA saw one word.
        host = _ShellHost(validator)
        assert _shell_refusal(host, f"cat '{tree.png}'", tree.work) is not None

    @WINDOWS
    @pytest.mark.parametrize(
        "template",
        [
            "type {p}",
            'type "{p}"',
            'dir "{p}"',
            'more "{p}"',
            "certutil -hashfile {p} SHA256",
            'certutil -hashfile "{p}" SHA256',
            "powershell -Command \"(Get-Item '{p}').Length\"",
            "powershell -Command \"Get-Item '{p}'\"",
            "powershell -Command \"Get-Item -LiteralPath '{p}'\"",
            "powershell -NoProfile -Command \"Get-FileHash '{p}'\"",
            "powershell -Command \"Get-FileHash -Algorithm SHA256 -Path '{p}'\"",
            "powershell -Command \"Test-Path '{p}'\"",
            "powershell -Command \"(Get-ChildItem '{p}').Length\"",
        ],
    )
    def test_a_windows_read(self, tree, validator, template):
        _assert_read_like_an_allowed_file(tree, validator, template)

    @WINDOWS
    @pytest.mark.parametrize(
        "template",
        [
            "powershell -Command \"Get-Item '{p}'\"",
            "powershell -Command \"Get-FileHash -Algorithm SHA256 '{p}'\"",
            "powershell -Command \"Get-Content -LiteralPath '{p}' -Raw\"",
            # A dot inside a path is not an invocation.
            "powershell -Command \"Get-ChildItem '.'\"",
        ],
    )
    def test_a_powershell_read_runs(self, tree, validator, template):
        """Every generated name holds 'SDXL-Turbo', read as a cmdlet until now."""
        host = _ShellHost(validator)
        command = template.format(p=tree.png)
        assert _shell_refusal(host, command, tree.work) is None

    @WINDOWS
    @pytest.mark.parametrize(
        "template",
        [
            "powershell -Command \"Get-Item '{p}' | Remove-Item\"",
            "powershell -Command \"Get-Item 'a-b' | Set-Content x\"",
            'powershell -Command "Get-Item \\"Remove-Item\\""',
            # An apostrophe inside a double-quoted string opens no literal.
            'powershell -Command "Get-Item \\"it\'s\\" | Remove-Item \'y\'"',
            # PowerShell takes curly quotes as quotes too.
            "powershell -Command \"Get-Item “it's” | Remove-Item 'y'\"",
            "powershell -Command \"Get-Item 'a`' | Remove-Item 'y'\"",
            # A string named as the command to run is still a command.
            "powershell -Command \"Get-Item '{p}' | & 'Remove-Item'\"",
            "powershell -Command \"& 'Remove-Item' '{p}'\"",
            "powershell -Command \"'Remove-Item {p}' | iex\"",
            # Dot-invocation of a quoted name, away from the start of the line.
            "powershell -Command \"Get-Item (. 'Out-File' '{p}')\"",
            "powershell -Command \"Get-Item {{ . 'Out-File' '{p}' }}\"",
            "powershell -Command \"Get-Item (.'Out-File' '{p}')\"",
            "powershell -Command \"Get-Item (. ('Out-File') '{p}')\"",
            "powershell -Command \"Get-Item -Path:.'Out-File'\"",
            "powershell -Command \"Get-Item '{p}' | Select-Object (. 'Out-File')\"",
            # A method named by a quoted string is still a method call.
            "powershell -Command \"(Get-Item '{p}').'Delete'()\"",
            'powershell -Command "(Get-Item \'{p}\').\\"Delete\\"()"',
        ],
    )
    def test_a_cmdlet_outside_single_quotes_is_still_seen(
        self, tree, validator, template
    ):
        host = _ShellHost(validator)
        command = template.format(p=tree.work / "mine.png")
        assert _shell_refusal(host, command, tree.work) is not None, command

    @WINDOWS
    @pytest.mark.parametrize(
        "template",
        [
            'del "{p}"',
            'erase "{p}"',
            'echo x > "{p}"',
            'type "{w}" > "{p}"',
            'copy "{w}" "{p}"',
            'move "{w}" "{p}"',
            'ren "{p}" other.png',
            "powershell -Command \"Remove-Item '{p}'\"",
            "powershell -Command \"Set-Content '{p}' x\"",
            "powershell -Command \"Copy-Item '{w}' '{p}'\"",
            "powershell -Command \"Get-Item '{p}'; Remove-Item '{p}'\"",
            "powershell -Command \"Get-Item '{p}' | Remove-Item\"",
            "powershell -Command \"(Get-Item '{p}').Delete()\"",
            'powershell -Command "Get-Item \\"{p}\\"$(Remove-Item x)"',
        ],
    )
    def test_a_windows_write_stays_refused(self, tree, validator, template):
        source = tree.work / "w.png"
        source.write_bytes(PNG)
        host = _ShellHost(validator)
        command = template.format(p=tree.png, w=source)
        assert _shell_refusal(host, command, tree.work) is not None, command
        assert tree.png.read_bytes() == PNG

    @pytest.mark.parametrize("quote", ['"', "'"])
    def test_a_quoted_read_still_cannot_reach_other_state_files(
        self, tree, validator, quote
    ):
        host = _ShellHost(validator)
        for target in (tree.state / "config.json", tree.state / "keys" / "signing.png"):
            command = f"cat {quote}{target}{quote}"
            assert _shell_refusal(host, command, tree.work) is not None, command

    @WINDOWS
    @pytest.mark.parametrize(
        "template",
        ['type "{t}"', "powershell -Command \"Get-Content '{t}'\""],
    )
    def test_a_windows_read_still_cannot_reach_other_state_files(
        self, tree, validator, template
    ):
        host = _ShellHost(validator)
        command = template.format(t=tree.state / "config.json")
        assert _shell_refusal(host, command, tree.work) is not None, command


class TestPowerShellLiterals:
    """Only text PowerShell can never run is hidden from the cmdlet check."""

    @pytest.mark.parametrize(
        "body, code",
        [
            ("get-item 'c:\\a\\sdxl-turbo.png'", "get-item ''"),
            ("get-item 'it''s-here.png'", "get-item ''"),
            ("get-item 'a' 'b-c'", "get-item '' ''"),
        ],
    )
    def test_a_single_quoted_string_is_blanked(self, body, code):
        assert _ps_code_outside_literals(body) == code

    @pytest.mark.parametrize(
        "body",
        [
            # A ' inside double quotes opens nothing: the cmdlet after it is code.
            "get-item \"it's\" | out-host 'y'",
            # Curly quotes, a backtick or an open quote: judge the whole text.
            "get-item \u201cit's\u201d | out-host 'y'",
            "get-item 'a`' | out-host 'y'",
            "get-item 'a | out-host",
            "get-item \"a | out-host 'y'",
        ],
    )
    def test_nothing_that_may_run_is_hidden(self, body):
        assert "out-host" in _ps_code_outside_literals(body)


class TestARefusedReadNeverSaysWrite:
    """A read refused for a protected folder was worded as a blocked write."""

    def test_a_shell_read(self, tree, validator):
        refusal = _shell_refusal(
            _ShellHost(validator), f'cat "{tree.state / "config.json"}"', tree.work
        )
        assert refusal is not None
        assert "Write blocked" not in refusal["error"]
        assert "protected directory" in refusal["error"]

    def test_a_tool_read(self, tree, validator):
        allowed, reason = validator.validate_read(
            str(tree.state / "config.json"), prompt_user=False
        )
        assert not allowed
        assert "Write blocked" not in reason
        assert "protected directory" in reason

    @WINDOWS
    def test_a_windows_system_file(self, tree, validator):
        allowed, reason = validator.validate_read(
            r"C:\Windows\win.ini", prompt_user=False
        )
        assert not allowed
        assert "Write blocked" not in reason

    def test_a_refused_write_still_says_write(self, tree, validator):
        blocked, reason = validator.is_write_blocked(str(tree.state / "config.json"))
        assert blocked
        assert reason.startswith("Write blocked: ")
