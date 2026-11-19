# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A read runs wherever the file is, with nobody asked.

Reading a file, a folder or a page is never refused for its location and never
stops to ask. Three reads stay refused: a secret, a folder the user removed
from the chat, and a path outside ``GAIA_ALLOWED_PATHS`` when that is set. A
file too large for one result comes back as its first part.
"""

import http.server
import ipaddress
import json
import socket
import threading

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.browser_tools import BrowserToolsMixin, _local_address_warning
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin
from gaia.agents.tools.path_access import read_access_error, with_outside_note
from gaia.security import PathValidator
from gaia.web.client import (
    BlockedHostError,
    WebClient,
    _assert_ip_allowed,
    is_local_ip,
    take_local_addresses,
)


@pytest.fixture
def folders(tmp_path):
    """``(allowed, outside)``: the session's folder and one beside it."""
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (allowed / "inside.txt").write_text("inside\n", encoding="utf-8")
    (outside / "notes.txt").write_text("from outside\n", encoding="utf-8")
    (outside / ".env").write_text("TOKEN=abc\n", encoding="utf-8")
    return allowed, outside


def _never_asked(path):
    raise AssertionError(f"a read asked for access to {path}")


@pytest.fixture
def validator(folders):
    """Scoped to ``allowed``, with a prompt that fails the test if it is shown."""
    made = PathValidator(allowed_paths=[str(folders[0])])
    made.set_access_prompt(_never_asked)
    return made


@pytest.fixture
def registry():
    saved = dict(_TOOL_REGISTRY)
    try:
        yield _TOOL_REGISTRY
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


# ------------------------------------------------------------- the validator


def test_a_path_outside_the_folders_may_be_read_without_asking(validator, folders):
    target = str(folders[1] / "notes.txt")

    assert validator.validate_read(target) == (True, "")
    assert validator.validate_read(target, prompt_user=False) == (True, "")
    # Reading it granted nothing: a write there is still out of scope.
    assert not validator.is_path_allowed(target, prompt_user=False)


def test_a_folder_outside_may_be_listed_without_asking(validator, folders):
    assert validator.validate_read(str(folders[1])) == (True, "")


def test_the_note_names_a_path_outside_the_folders(validator, folders):
    inside, outside = folders[0] / "inside.txt", folders[1] / "notes.txt"

    note = validator.outside_folders_note(str(outside))

    assert note == (
        f"'{outside}' is outside this session's folders. Reading it is fine; "
        "changing a file there asks the user first."
    )
    assert validator.outside_folders_note(str(inside)) == ""


def test_a_secret_outside_the_folders_is_still_refused(validator, folders):
    allowed, reason = validator.validate_read(str(folders[1] / ".env"))

    assert not allowed
    assert "Read blocked" in reason and "holds credentials" in reason


def test_a_folder_the_user_removed_is_still_refused(validator, folders):
    validator.withhold_path(str(folders[1]))

    allowed, reason = validator.validate_read(str(folders[1] / "notes.txt"))

    assert not allowed
    assert "which the user removed from this chat's folders" in reason


def test_a_confined_session_still_refuses_a_path_outside(folders, monkeypatch):
    monkeypatch.setenv("GAIA_ALLOWED_PATHS", str(folders[0]))
    confined = PathValidator(allowed_paths=[str(folders[0])])
    confined.set_access_prompt(_never_asked)

    allowed, reason = confined.validate_read(str(folders[1] / "notes.txt"))

    assert not allowed
    assert "is not in allowed paths" in reason
    assert "GAIA_ALLOWED_PATHS" in reason
    assert confined.validate_read(str(folders[0] / "inside.txt")) == (True, "")


def test_read_access_error_lets_an_outside_path_through(validator, folders):
    class Host:
        path_validator = validator

    assert read_access_error(Host(), folders[1] / "notes.txt") is None
    refused = read_access_error(Host(), folders[1] / ".env")
    assert refused["status"] == "error" and "Read blocked" in refused["error"]


def test_the_warning_joins_one_the_result_already_has(validator, folders):
    class Host:
        path_validator = validator

    result = {"status": "success", "warning": "limit=5 counts characters."}

    noted = with_outside_note(Host(), folders[1] / "notes.txt", result)

    assert noted["warning"].startswith("limit=5 counts characters. '")
    assert "outside this session's folders" in noted["warning"]
    failed = {"status": "error", "error": "File not found"}
    assert with_outside_note(Host(), folders[1] / "gone.txt", failed) is failed


# -------------------------------------------------------------- Python reads


class _PythonHost:
    def __init__(self, validator):
        self._validator = validator

    def _read_validator(self):
        return self._validator


def _python_refusal(validator, folders, code):
    from gaia.agents.base.python_scope import python_scope_refusal

    return python_scope_refusal(
        _PythonHost(validator), "run_python", {"code": code}, [str(folders[0])]
    )


def test_python_that_reads_outside_the_folders_runs_without_asking(validator, folders):
    target = (folders[1] / "notes.txt").as_posix()

    assert (
        _python_refusal(validator, folders, f"print(open('{target}').read())") is None
    )
    listing = f"import os; print(os.listdir('{folders[1].as_posix()}'))"
    assert _python_refusal(validator, folders, listing) is None


def test_python_that_reads_a_secret_outside_is_still_refused(folders):
    from gaia.agents.base.credential_reads import python_credential_refusal

    target = (folders[1] / ".env").as_posix()

    refusal = python_credential_refusal(
        "run_python", {"code": f"print(open('{target}').read())"}, [str(folders[0])]
    )

    assert refusal is not None and refusal["status"] == "error"
    assert "holds credentials, keys or secrets" in refusal["error"]


# ----------------------------------------------------------------- read_file


@pytest.fixture
def read_file(validator, registry):
    host = FileIOToolsMixin()
    host.path_validator = validator
    host.register_file_io_tools()
    return registry["read_file"]["function"]


def test_read_file_reads_a_file_outside_the_folders_with_a_warning(read_file, folders):
    target = folders[1] / "notes.txt"

    result = read_file(str(target))

    assert result["status"] == "success", result
    assert result["content"] == "from outside\n"
    assert result["warning"] == (
        f"'{target}' is outside this session's folders. Reading it is fine; "
        "changing a file there asks the user first."
    )


def test_read_file_inside_the_folders_has_no_warning(read_file, folders):
    result = read_file(str(folders[0] / "inside.txt"))

    assert result["status"] == "success", result
    assert "warning" not in result


def test_read_file_still_refuses_a_secret(read_file, folders):
    result = read_file(str(folders[1] / ".env"))

    assert result["status"] == "error"
    assert "Read blocked" in result["error"]
    assert "TOKEN" not in str(result)


def test_a_missing_file_outside_is_a_plain_not_found(read_file, folders):
    result = read_file(str(folders[1] / "gone.txt"))

    assert result["status"] == "error"
    assert result["error"].startswith("File not found:")
    assert "warning" not in result


# ------------------------------------------------ the search and list tools


@pytest.fixture
def search_tools(validator, registry):
    class Host(FileSearchToolsMixin):
        pass

    host = Host()
    host.path_validator = validator
    host.register_file_search_tools()
    return {name: entry["function"] for name, entry in registry.items()}


def test_a_folder_outside_is_listed_without_asking(search_tools, folders):
    result = search_tools["browse_directory"](str(folders[1]))

    assert result["status"] == "success", result
    assert "notes.txt" in json.dumps(result)


def test_a_folder_outside_is_searched_and_its_secret_skipped(search_tools, folders):
    result = search_tools["search_file_content"]("outside", directory=str(folders[1]))

    assert result["status"] == "success", result
    assert [m["file"] for m in result["matches"]] == [str(folders[1] / "notes.txt")]
    assert result["files_skipped_protected"] == 1


def test_file_info_outside_runs_without_asking(search_tools, folders):
    result = search_tools["get_file_info"](str(folders[1] / "notes.txt"))

    assert result["status"] == "success", result


def test_a_file_over_the_size_limit_comes_back_as_its_first_page(search_tools, folders):
    """The search mixin's own read_file, past its 10 MB limit."""
    big = folders[0] / "big.log"
    line = "0123456789abcdef" * 4 + "\n"
    big.write_text(line * (10_000_000 // len(line) + 10), encoding="utf-8")
    size = big.stat().st_size
    host = FileSearchToolsMixin()
    host.path_validator = PathValidator(allowed_paths=[str(folders[0])])
    saved = _TOOL_REGISTRY.pop("read_file", None)
    try:
        host.register_file_search_tools()
        read = _TOOL_REGISTRY["read_file"]["function"]
        result = read(str(big))
    finally:
        if saved is not None:
            _TOOL_REGISTRY["read_file"] = saved

    assert result["status"] == "success", result
    assert result["content"] == (line * 200)[:8000]
    assert result["chars_returned"] == 8000
    assert result["size_bytes"] == size and size > 10_000_000
    assert result["next_offset"] == 8000
    assert result["note"] == (
        f"Partial: characters 0-8,000 of a {size:,}-byte file. Continue with "
        "offset=8000."
    )


def test_the_filesystem_read_of_an_oversized_file_is_its_first_page(
    folders, registry, monkeypatch
):
    monkeypatch.setattr("gaia.agents.tools.filesystem_tools.MAX_READ_BYTES", 1024)
    big = folders[0] / "huge.txt"
    big.write_text("x" * 9000 + "TAIL", encoding="utf-8")

    class Host(FileSystemToolsMixin):
        pass

    host = Host()
    host.path_validator = PathValidator(allowed_paths=[str(folders[0])])
    host.register_filesystem_tools()
    read = registry["read_file"]["function"]

    first = json.loads(read(str(big)))
    rest = json.loads(read(str(big), offset=first["next_offset"], limit=8000))

    assert first["content"] == "x" * 8000
    assert first["next_offset"] == 8000
    assert first["note"] == (
        "Partial: the first 8,000 characters of a 8.8 KB file, too large to "
        "load whole. Continue with offset=8000."
    )
    assert first["content"] + rest["content"] == "x" * 9000 + "TAIL"
    assert rest["next_offset"] is None and "note" not in rest


# ------------------------------------------------------------- web addresses


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "::1", "10.0.0.5", "192.168.1.20", "172.16.3.4", "fd12::1"]
)
def test_this_pc_and_private_networks_are_local(address):
    assert is_local_ip(ipaddress.ip_address(address))
    _assert_ip_allowed(address, "box.lan", allow_local=True)


@pytest.mark.parametrize(
    "address",
    [
        "169.254.169.254",
        "169.254.10.10",
        "fe80::1",
        "fd00:ec2::254",
        "100.100.100.200",
        "::ffff:169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
    ],
)
def test_cloud_metadata_and_link_local_stay_blocked(address):
    assert not is_local_ip(ipaddress.ip_address(address))
    with pytest.raises(BlockedHostError) as blocked:
        _assert_ip_allowed(address, "metadata.test", allow_local=True)
    assert "cloud-metadata, link-local or reserved address" in str(blocked.value)


def test_a_caller_that_does_not_allow_local_still_blocks_it():
    """The shell's check for a command that reaches the local network."""
    with pytest.raises(BlockedHostError):
        _assert_ip_allowed("192.168.1.20", "nas.lan")


def _resolves_to(monkeypatch, address):
    take_local_addresses()
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: [(socket.AF_INET, 0, 0, "", (address, 0))],
    )


def test_the_client_validates_a_local_url_and_remembers_the_address(monkeypatch):
    _resolves_to(monkeypatch, "192.168.1.20")
    client = WebClient.__new__(WebClient)

    url = "http://nas.lan:8080/status"
    assert client.validate_url(url, allow_local=True) == url
    assert take_local_addresses() == {"nas.lan": "192.168.1.20"}
    assert take_local_addresses() == {}


def test_a_caller_checking_for_the_public_web_still_refuses_a_local_url(monkeypatch):
    """The live browser and the media downloader validate without allow_local."""
    _resolves_to(monkeypatch, "192.168.1.20")
    client = WebClient.__new__(WebClient)

    with pytest.raises(BlockedHostError):
        client.validate_url("http://nas.lan:8080/status")


def test_the_client_still_refuses_the_metadata_address(monkeypatch):
    _resolves_to(monkeypatch, "169.254.169.254")
    client = WebClient.__new__(WebClient)

    with pytest.raises(BlockedHostError):
        client.validate_url(
            "http://169.254.169.254/latest/meta-data/", allow_local=True
        )
    assert take_local_addresses() == {}


def test_a_public_address_is_not_recorded(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    client = WebClient.__new__(WebClient)

    client.validate_url("https://example.com/", allow_local=True)

    assert take_local_addresses() == {}


def test_a_host_the_operator_named_carries_no_warning(monkeypatch):
    monkeypatch.setenv("GAIA_WEB_ALLOWED_HOSTS", "127.0.0.1")
    _resolves_to(monkeypatch, "127.0.0.1")
    client = WebClient.__new__(WebClient)

    client.validate_url("http://127.0.0.1:8765/atlas.html", allow_local=True)

    assert take_local_addresses() == {}


def test_the_warning_names_the_address():
    assert _local_address_warning({}) == ""
    assert _local_address_warning({"127.0.0.1": "127.0.0.1"}) == (
        "[Warning: this came from 127.0.0.1, an address on this PC or the local "
        "network, not the public web.]\n"
    )
    assert "nas.lan (192.168.1.20)" in _local_address_warning(
        {"nas.lan": "192.168.1.20"}
    )


class _Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"hello from this pc"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def local_page():
    """The URL of a page served on this PC for the length of the test."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/notes.txt"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def fetch_page(registry):
    host = BrowserToolsMixin()
    host._web_client = WebClient()
    host.register_browser_tools()
    try:
        yield registry["fetch_page"]["function"]
    finally:
        host._web_client.close()


@pytest.mark.allow_network
def test_fetch_page_reads_a_page_on_this_pc_and_says_where_it_came_from(
    fetch_page, local_page, monkeypatch
):
    monkeypatch.delenv("GAIA_WEB_ALLOWED_HOSTS", raising=False)

    out = fetch_page(local_page)

    assert out.startswith(
        "[Warning: this came from 127.0.0.1, an address on this PC or the local "
        "network, not the public web.]\n"
    )
    assert "hello from this pc" in out


@pytest.mark.allow_network
def test_fetch_page_gives_no_warning_for_a_host_the_operator_named(
    fetch_page, local_page, monkeypatch
):
    monkeypatch.setenv("GAIA_WEB_ALLOWED_HOSTS", "127.0.0.1")

    out = fetch_page(local_page)

    assert "Warning" not in out
    assert "hello from this pc" in out


def test_fetch_page_still_refuses_the_metadata_address(fetch_page):
    out = fetch_page("http://169.254.169.254/latest/meta-data/")

    assert out.startswith("Error: Blocked: 169.254.169.254 resolves to")
    assert "cloud-metadata, link-local or reserved address" in out
