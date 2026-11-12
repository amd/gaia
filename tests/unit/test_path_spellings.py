# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
r"""Every spelling of a protected file is judged as that file.

Windows names one file many ways — ``\\?\C:\x``, ``\\localhost\C$\x``,
``\\?\UNC\127.0.0.1\C$\x``, ``C:\x.`` — and a guard that compares strings judges
whichever one it was handed. Each test here spells a credential file another
way and checks that the refusal is the same hard one the plain path gets: no
approval card, nothing read, nothing written. A spelling that names another
computer or a device is refused outright.
"""

from __future__ import annotations

import ctypes
import json
import os
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import gaia.security as security
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_edit import file_read_record
from gaia.canonical_path import UncheckablePathError, canonical_path
from gaia.security import PathValidator

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows path spellings")
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX path spellings")

KEY = "sshmark-K9v4-30562"
ENV = "envmark-Q7x2-88417"

PROTECTED = [
    ".ssh/id_test",
    ".gnupg/pubring.kbx",
    ".aws/credentials",
    ".azure/accessTokens.json",
    ".kube/config",
    ".docker/config.json",
    ".config/gcloud/credentials.db",
    "AppData/Roaming/gcloud/credentials.db",
    "Documents/w/.env",
    "Documents/w/id_rsa",
    "Documents/w/server.pem",
]


@pytest.fixture(autouse=True)
def _restore_tool_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake home with every credential folder, and a scope folder beside it."""
    fake = (tmp_path / "home").resolve()
    for rel in PROTECTED:
        target = fake / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(KEY if ".env" not in rel else ENV, encoding="utf-8")
    (fake / "Documents" / "plain.txt").write_text("plain", encoding="utf-8")
    (fake / "scope").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake))
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("USERPROFILE", str(fake))
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia_state"))
    monkeypatch.delenv("GAIA_ALLOWED_PATHS", raising=False)
    monkeypatch.setattr(security, "SECRET_DIRECTORIES", security._secret_directories())
    # tmp_path is in the system temp dir, which would refuse for another reason.
    monkeypatch.setattr(security, "_system_temp_roots", lambda: set())
    return fake


def _short_name(path: Path) -> str:
    buf = ctypes.create_unicode_buffer(32768)
    ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf))
    return buf.value


def _spellings(path: Path) -> dict:
    """Other names Windows opens *path* by."""
    plain = str(path)
    drive, rest = os.path.splitdrive(plain)
    share = f"{drive[0]}$"
    parent, leaf = os.path.split(plain)
    spellings = {
        "extended": "\\\\?\\" + plain,
        "extended-unc-localhost": f"\\\\?\\UNC\\localhost\\{share}{rest}",
        "extended-unc-127": f"\\\\?\\UNC\\127.0.0.1\\{share}{rest}",
        "unc-localhost": f"\\\\localhost\\{share}{rest}",
        "unc-127": f"\\\\127.0.0.1\\{share}{rest}",
        "unc-ipv6-loopback": f"\\\\[::1]\\{share}{rest}",
        "unc-hostname": f"\\\\{socket.gethostname()}\\{share}{rest}",
        "device": "\\\\.\\" + plain,
        "device-unc": f"\\\\.\\UNC\\localhost\\{share}{rest}",
        "nt-object": "\\??\\" + plain,
        "forward-extended": "//?/" + plain.replace("\\", "/"),
        "forward-unc": f"//localhost/{share}{rest}".replace("\\", "/"),
        "upper": plain.upper(),
        "parent-trailing-dot": parent + ".\\" + leaf,
        "extended-parent-trailing-dot": "\\\\?\\" + parent + ".\\" + leaf,
        "data-stream": plain + "::$DATA",
        "dot-segments": os.path.join(parent, "..", os.path.basename(parent), leaf),
    }
    short = _short_name(path.parent)
    if short and short.lower() != str(path.parent).lower():
        spellings["short-name"] = os.path.join(short, leaf)
    return spellings


def _cases():
    names = list(_spellings(Path("C:\\h\\.ssh\\id_test")))
    return [(rel, name) for rel in PROTECTED for name in names]


class _Asked:
    """An approval prompt that says yes, and remembers it was shown."""

    def __init__(self):
        self.paths = []

    def __call__(self, path):
        self.paths.append(Path(path))
        return True


def _validator(home):
    validator = PathValidator(allowed_paths=[str(home / "scope")])
    asked = _Asked()
    validator.set_access_prompt(asked)
    return validator, asked


# ── The canonicaliser ────────────────────────────────────────────────────


@windows_only
@pytest.mark.parametrize("name", list(_spellings(Path("C:\\h\\.ssh\\id_test"))))
def test_every_spelling_has_one_canonical_form(home, name):
    target = home / ".ssh" / "id_test"
    spelled = _spellings(target)[name]

    assert canonical_path(spelled).lower() == str(target).lower()


@windows_only
@pytest.mark.parametrize(
    "spelled, why",
    [
        ("\\\\203.0.113.7\\share\\x.txt", "another computer"),
        ("\\\\?\\UNC\\fileserver\\share\\x.txt", "another computer"),
        ("\\\\localhost\\Users\\x.txt", "shared folder"),
        ("\\\\?\\GLOBALROOT\\Device\\HarddiskVolume3\\x.txt", "device or volume"),
        ("\\\\?\\Volume{00000000-0000-0000-0000-000000000000}\\x.txt", "device"),
        ("\\\\.\\PhysicalDrive0", "device or volume"),
        ("\\\\.\\pipe\\gaia", "device or volume"),
        ("C:\\x\\notes.txt:hidden", "data stream"),
        ("C:\\x\\notes\x00.txt", "NUL"),
    ],
)
def test_a_path_that_is_not_a_file_on_this_pc_is_uncheckable(spelled, why):
    with pytest.raises(UncheckablePathError, match=why) as caught:
        canonical_path(spelled)
    assert "No approval can grant it" in str(caught.value)


@windows_only
def test_a_named_stream_compares_as_its_file_when_asked(home):
    target = home / ".ssh" / "id_test"

    assert canonical_path(f"{target}:x", for_comparison=True).lower() == (
        str(target).lower()
    )


@posix_only
def test_proc_self_root_is_the_file_it_points_at(home):
    if not os.path.isdir("/proc/self/root"):
        pytest.skip("no /proc on this system")
    target = home / ".ssh" / "id_test"

    assert canonical_path(f"/proc/self/root{target}") == str(target)


@posix_only
def test_a_double_slash_is_one_slash(home):
    target = home / ".ssh" / "id_test"

    assert canonical_path("/" + str(target)) == str(target)


def test_a_link_is_judged_as_where_it_points(home):
    link = home / "scope" / "innocent"
    try:
        link.symlink_to(home / ".ssh", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")

    assert canonical_path(link / "id_test").lower() == (
        str(home / ".ssh" / "id_test").lower()
    )


@windows_only
def test_a_junction_into_a_protected_folder_is_judged_as_that_folder(home):
    import _winapi

    link = home / "scope" / "innocent"
    _winapi.CreateJunction(str(home / ".ssh"), str(link))
    validator, asked = _validator(home)

    for spelled in (str(link / "id_test"), _spellings(link / "id_test")["unc-127"]):
        assert not validator.validate_read(spelled)[0], spelled
        assert not validator.validate_write(spelled)[0], spelled
    assert asked.paths == []


# ── The validator: every check judges the canonical form ─────────────────


@windows_only
@pytest.mark.parametrize("rel, name", _cases())
def test_a_spelled_protected_file_is_refused_without_a_card(home, rel, name):
    validator, asked = _validator(home)
    spelled = _spellings(home / rel)[name]

    read_ok, read_reason = validator.validate_read(spelled)
    write_ok, write_reason = validator.validate_write(spelled, content_size=1)

    assert not read_ok and not write_ok
    assert asked.paths == []
    assert validator.grant_refusal_reason(spelled)
    assert "No approval can grant it" in validator.access_denied_message(spelled)
    assert read_reason and write_reason


@windows_only
@pytest.mark.parametrize("name", list(_spellings(Path("C:\\h\\x\\plain.txt"))))
def test_an_ordinary_file_asks_about_its_canonical_path(home, name):
    validator, asked = _validator(home)
    target = home / "Documents" / "plain.txt"

    allowed, reason = validator.validate_read(_spellings(target)[name])

    assert allowed, reason
    assert [str(p).lower() for p in asked.paths] == [str(target).lower()]


@windows_only
@pytest.mark.parametrize(
    "spelled",
    [
        "\\\\203.0.113.7\\share\\x.txt",
        "\\\\?\\GLOBALROOT\\Device\\HarddiskVolume3\\x.txt",
        "\\\\localhost\\Users\\x.txt",
    ],
)
def test_an_uncheckable_path_is_refused_without_a_card(home, spelled):
    validator, asked = _validator(home)

    read_ok, read_reason = validator.validate_read(spelled)
    write_ok, write_reason = validator.validate_write(spelled)

    assert not read_ok and not write_ok
    assert asked.paths == []
    assert "No approval can grant it" in read_reason
    assert "No approval can grant it" in write_reason
    assert validator.grant_refusal_reason(spelled)
    assert validator.is_read_blocked(spelled)[0]
    assert validator.is_write_blocked(spelled)[0]


@windows_only
def test_a_grant_made_through_one_spelling_covers_the_canonical_file(home):
    validator, asked = _validator(home)
    target = home / "Documents" / "plain.txt"

    assert validator.validate_read(_spellings(target)["unc-localhost"])[0]
    assert validator.validate_read(str(target), prompt_user=False)[0]
    assert len(asked.paths) == 1


# ── The tools: read, write, edit, list, info, search, index, download ────


def _register(mixin_cls, register, validator):
    host = type("_Host", (mixin_cls,), {})()
    host.path_validator = validator
    host._path_validator = validator
    getattr(host, register)()
    return host, {name: _TOOL_REGISTRY[name]["function"] for name in _TOOL_REGISTRY}


def _io_tools(validator):
    from gaia.agents.tools.file_io_tools import FileIOToolsMixin

    return _register(FileIOToolsMixin, "register_file_io_tools", validator)


def _search_tools(validator):
    from gaia.agents.tools.file_tools import FileSearchToolsMixin

    return _register(FileSearchToolsMixin, "register_file_search_tools", validator)


def _fs_tools(validator):
    from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin

    return _register(FileSystemToolsMixin, "register_filesystem_tools", validator)


def _refused(result) -> bool:
    text = json.dumps(result, default=str) if isinstance(result, dict) else str(result)
    if KEY in text or ENV in text:
        return False
    if isinstance(result, dict):
        return result.get("status") == "error" or bool(result.get("error"))
    return text.startswith("Error") or "denied" in text.lower()


TOOL_SPELLINGS = [
    "extended",
    "extended-unc-localhost",
    "unc-localhost",
    "unc-127",
    "unc-hostname",
    "device-unc",
    "nt-object",
    "parent-trailing-dot",
]
FILES = [".ssh/id_test", "Documents/w/.env"]
#: A new file each case plants: any name in the folder, else another secret name.
PLANTED = {".ssh/id_test": "w-unc.txt", "Documents/w/.env": "credentials.json"}


@windows_only
@pytest.mark.parametrize("name", TOOL_SPELLINGS)
@pytest.mark.parametrize("rel", FILES)
@pytest.mark.parametrize("tools", [_io_tools, _search_tools, _fs_tools])
def test_read_tools_refuse_a_spelled_protected_file(home, rel, name, tools):
    validator, asked = _validator(home)
    host, registry = tools(validator)
    spelled = _spellings(home / rel)[name]

    with patch.object(security, "_is_interactive", return_value=False):
        result = registry["read_file"](spelled)

    assert _refused(result), result
    assert asked.paths == []


@windows_only
@pytest.mark.parametrize("name", TOOL_SPELLINGS)
@pytest.mark.parametrize("rel", FILES)
@pytest.mark.parametrize("tools", [_io_tools, _search_tools])
def test_write_and_edit_tools_refuse_a_spelled_protected_file(home, rel, name, tools):
    validator, asked = _validator(home)
    host, registry = tools(validator)
    target = home / rel
    before = target.read_text(encoding="utf-8")
    spelled = _spellings(target)[name]
    planted = target.parent / PLANTED[rel]
    spelled_new = _spellings(planted)[name]
    file_read_record(host).note(target)

    with patch.object(security, "_is_interactive", return_value=False):
        written = registry["write_file"](spelled, "planted")
        created = registry["write_file"](spelled_new, "planted")
        edited = registry["edit_file"](spelled, before, "planted")

    for result in (written, created, edited):
        assert _refused(result), result
    assert target.read_text(encoding="utf-8") == before
    assert not planted.exists()
    assert asked.paths == []


@windows_only
@pytest.mark.parametrize("name", TOOL_SPELLINGS)
def test_listing_info_and_search_tools_refuse_a_spelled_protected_folder(home, name):
    validator, asked = _validator(home)
    folder_spelled = _spellings(home / ".ssh")[name]
    file_spelled = _spellings(home / ".ssh" / "id_test")[name]

    with patch.object(security, "_is_interactive", return_value=False):
        _, search = _search_tools(validator)
        results = {
            "file_tools.get_file_info": search["get_file_info"](file_spelled),
            "file_tools.browse_directory": search["browse_directory"](folder_spelled),
            "file_tools.search_file_content": search["search_file_content"](
                "sshmark", folder_spelled
            ),
        }
        _, io = _io_tools(validator)
        results["file_io.search_code"] = io["search_code"](folder_spelled, "sshmark")
        _, fs = _fs_tools(validator)
        results["filesystem.file_info"] = fs["file_info"](file_spelled)
        results["filesystem.browse_directory"] = fs["browse_directory"](folder_spelled)
        results["filesystem.tree"] = fs["tree"](folder_spelled)

    for tool, result in results.items():
        assert _refused(result), (tool, result)
    assert asked.paths == []


@windows_only
@pytest.mark.parametrize("name", TOOL_SPELLINGS)
@pytest.mark.parametrize("rel", FILES)
def test_index_document_refuses_a_spelled_protected_file(home, rel, name):
    from gaia.agents.tools.rag_tools import RAGToolsMixin

    validator, asked = _validator(home)
    host = type("_Host", (RAGToolsMixin,), {})()
    host.path_validator = validator
    host._path_validator = validator
    host.rag = MagicMock()
    host.indexed_files = set()
    host.register_rag_tools()
    index_document = _TOOL_REGISTRY["index_document"]["function"]

    result = index_document(_spellings(home / rel)[name])

    assert _refused(result), result
    host.rag.index_document.assert_not_called()
    assert asked.paths == []


@windows_only
@pytest.mark.parametrize("name", TOOL_SPELLINGS)
def test_download_refuses_a_spelled_protected_folder(home, name):
    from gaia.agents.tools.browser_tools import BrowserToolsMixin
    from gaia.web.client import WebClient

    validator, asked = _validator(home)
    client = WebClient()
    get = MagicMock()

    class _Host(BrowserToolsMixin):
        def __init__(self):
            self._web_client = client
            self._path_validator = validator
            self._tools = {}

    registered = {}

    def fake_tool(atomic=True):
        def decorator(func):
            registered[func.__name__] = func
            return func

        return decorator

    with patch("gaia.agents.base.tools.tool", fake_tool):
        _Host().register_browser_tools()
    try:
        with (
            patch.object(client, "validate_url"),
            patch.object(client, "_rate_limit_wait"),
            patch.object(client._session, "get", get),
        ):
            result = registered["download_file"](
                "https://example.com/authorized_keys",
                save_to=_spellings(home / ".ssh")[name],
                filename="planted.txt",
            )
    finally:
        client.close()

    assert _refused(result), result
    get.assert_not_called()
    assert not (home / ".ssh" / "planted.txt").exists()
    assert asked.paths == []


@windows_only
@pytest.mark.parametrize("name", TOOL_SPELLINGS)
def test_a_shell_operand_spelled_another_way_is_refused(home, name):
    from gaia.agents.tools.shell_tools import ShellToolsMixin

    validator, asked = _validator(home)
    host = type("_Host", (ShellToolsMixin,), {})()
    host.path_validator = validator
    spelled = _spellings(home / ".ssh" / "id_test")[name]

    refusal = host._resolved_path_refusal("Argument", spelled, spelled, str(home))

    assert refusal is not None and refusal["status"] == "error"
    assert asked.paths == []


@windows_only
def test_a_shell_operand_on_another_computer_is_refused(home):
    from gaia.agents.tools.shell_tools import ShellToolsMixin

    validator, asked = _validator(home)
    host = type("_Host", (ShellToolsMixin,), {})()
    host.path_validator = validator
    spelled = "\\\\203.0.113.7\\share\\x.txt"

    refusal = host._resolved_path_refusal(
        "Argument", spelled, spelled, str(home), outside=[]
    )

    assert refusal is not None
    assert "another computer" in refusal["error"]
    assert asked.paths == []
