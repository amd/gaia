# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""download_file asks permission for the file it writes, not its folder (#5301).

The home folder is never grantable as a whole, so asking about ``~`` itself made
every "save it in my home folder" download fail without a prompt. These tests
drive the real tool, a real ``WebClient`` and a real ``PathValidator`` against a
fake home; only the network is stubbed.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

import gaia.security as security
from gaia.security import PathValidator
from gaia.web.client import WebClient

BODY = b"Trail tent, 1.2 kg packed weight"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake home folder that the validator treats like the real one."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia_state"))
    monkeypatch.delenv("GAIA_ALLOWED_PATHS", raising=False)
    # tmp_path sits in the system temp dir, which no approval may grant.
    monkeypatch.setattr(security, "_system_temp_roots", lambda: set())
    return fake_home.resolve()


def _response(disposition='attachment; filename="tent.pdf"', body=BODY):
    response = MagicMock()
    response.status_code = 200
    response.headers = {"Content-Type": "application/pdf"}
    if disposition:
        response.headers["Content-Disposition"] = disposition
    response.iter_content.return_value = [body]
    return response


class _Harness:
    """The download_file tool wired to a real client and validator."""

    def __init__(self, validator, answer=True):
        from gaia.agents.tools.browser_tools import BrowserToolsMixin

        self.asked = []
        self.answer = answer
        self.validator = validator
        validator.set_access_prompt(self._prompt)

        self.client = WebClient()
        self.get = MagicMock(side_effect=lambda *a, **k: self.next_response)
        self.next_response = _response()

        client = self.client

        class Agent(BrowserToolsMixin):
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
            Agent().register_browser_tools()
        self._download = registered["download_file"]

    def _prompt(self, path):
        self.asked.append(path)
        return self.answer

    def download(self, url="https://example.com/files/x", **kwargs):
        with (
            patch.object(self.client, "validate_url"),
            patch.object(self.client, "_rate_limit_wait"),
            patch.object(self.client._session, "get", self.get),
        ):
            return self._download(url, **kwargs)

    def close(self):
        self.client.close()


@pytest.fixture
def harness(home, tmp_path):
    elsewhere = tmp_path / "workspace"
    elsewhere.mkdir()
    h = _Harness(PathValidator(allowed_paths=[str(elsewhere)]))
    yield h
    h.close()


class TestDownloadAsksForTheFile:
    def test_home_folder_download_asks_for_the_file(self, harness, home):
        result = harness.download(save_to="~")

        assert harness.asked == [home / "tent.pdf"]
        assert "Downloaded: tent.pdf" in result, result
        assert (home / "tent.pdf").read_bytes() == BODY

    def test_approval_grants_the_file_not_the_home_folder(self, harness, home):
        harness.download(save_to="~")

        assert not harness.validator.is_path_allowed(str(home), prompt_user=False)
        assert not harness.validator.is_path_allowed(
            str(home / "other.txt"), prompt_user=False
        )

    def test_each_new_file_is_asked_about_once(self, harness, home):
        harness.download(save_to="~")
        harness.next_response = _response('attachment; filename="stove.pdf"')
        result = harness.download(save_to="~")

        assert harness.asked == [home / "tent.pdf", home / "stove.pdf"]
        assert "Downloaded: stove.pdf" in result

    def test_default_destination_succeeds_after_approval(self, harness, home):
        result = harness.download()

        assert harness.asked == [home / "Downloads" / "tent.pdf"]
        assert (home / "Downloads" / "tent.pdf").read_bytes() == BODY
        assert "Downloaded: tent.pdf" in result

    def test_already_allowed_folder_is_not_asked_about(self, harness, tmp_path):
        result = harness.download(save_to=str(tmp_path / "workspace"))

        assert harness.asked == []
        assert (tmp_path / "workspace" / "tent.pdf").read_bytes() == BODY
        assert "Downloaded" in result


class TestRefusedDestinationSaysWhy:
    def test_declined_file_names_the_path(self, harness, home):
        harness.answer = False

        result = harness.download(save_to="~")

        assert result.startswith("Error:"), result
        assert str(home / "tent.pdf") in result
        assert "not in allowed paths" in result
        assert not (home / "tent.pdf").exists()

    def test_declined_new_folder_is_not_created(self, harness, home):
        harness.answer = False

        harness.download(save_to="~/Gear")

        assert harness.asked == [home / "Gear" / "tent.pdf"]
        assert not (home / "Gear").exists()

    def test_unaskable_destination_gives_the_reason(self, home, tmp_path, monkeypatch):
        scope = tmp_path / "scope"
        scope.mkdir()
        monkeypatch.setenv("GAIA_ALLOWED_PATHS", str(scope))
        h = _Harness(PathValidator(allowed_paths=[str(scope)]))
        try:
            result = h.download(save_to="~")
        finally:
            h.close()

        assert h.asked == []
        assert str(home / "tent.pdf") in result, result
        assert "No approval can grant it" in result
        assert "GAIA_ALLOWED_PATHS" in result
        assert "save_to='~/Downloads'" in result
        assert not (home / "tent.pdf").exists()

    def test_user_refusal_does_not_suggest_a_way_around_it(self, harness, home):
        harness.answer = False

        result = harness.download(save_to="~")

        assert "~/Downloads" not in result


class TestWriteGuardrailsStillHold:
    def test_blocked_directory_is_refused_before_any_request(
        self, harness, tmp_path, monkeypatch
    ):
        protected = tmp_path / "protected"
        protected.mkdir()
        monkeypatch.setattr(
            security,
            "BLOCKED_DIRECTORIES",
            security.BLOCKED_DIRECTORIES | {str(protected.resolve())},
        )

        result = harness.download(save_to=str(protected))

        assert result.startswith("Error:"), result
        assert "protected directory" in result
        assert str(protected.resolve()) in result
        harness.get.assert_not_called()
        assert harness.asked == []
        assert list(protected.iterdir()) == []

    def test_sensitive_filename_is_refused_without_asking(self, harness, home):
        harness.next_response = _response('attachment; filename="credentials.json"')

        result = harness.download(save_to="~")

        assert "blocked by security policy" in result, result
        assert harness.asked == []
        assert not (home / "credentials.json").exists()

    def test_existing_file_is_not_overwritten_or_asked_about(self, harness, home):
        mine = home / "tent.pdf"
        mine.write_bytes(b"my own notes")

        result = harness.download(save_to="~")

        assert "Refusing to overwrite" in result, result
        assert mine.read_bytes() == b"my own notes"
        assert harness.asked == []

    def test_size_limit_still_applies(self, harness, home):
        harness.client._max_download_size = 4

        result = harness.download(save_to="~")

        assert "Error" in result
        assert not (home / "tent.pdf").exists()
        assert not (home / "tent.pdf.part").exists()


class TestHostileNamesCannotEscapeTheDestination:
    @pytest.mark.parametrize(
        "disposition",
        [
            'attachment; filename="../../escape.pdf"',
            'attachment; filename="..\\\\..\\\\escape.pdf"',
            'attachment; filename="sub/dir/escape.pdf"',
            'attachment; filename="CON"',
            'attachment; filename="nul.txt"',
            'attachment; filename="COM1.pdf"',
        ],
    )
    def test_header_name_lands_inside_the_destination(self, harness, home, disposition):
        harness.next_response = _response(disposition)

        result = harness.download(save_to="~")

        assert len(harness.asked) == 1, result
        asked = harness.asked[0]
        assert asked.parent == home
        assert os.sep not in asked.name and "/" not in asked.name
        assert asked.name.split(".")[0].upper() not in WebClient._WINDOWS_RESERVED
        assert asked.exists()

    @pytest.mark.parametrize(
        "filename", ["../../escape.pdf", "..\\..\\escape.pdf", "a/b/escape.pdf"]
    )
    def test_filename_argument_cannot_escape(self, harness, home, filename):
        harness.download(save_to="~", filename=filename)

        assert len(harness.asked) == 1
        assert harness.asked[0].parent == home

    def test_dotdot_destination_is_asked_about_resolved(self, harness, home):
        harness.answer = False

        harness.download(save_to="~/Downloads/../Gear/..")

        assert harness.asked == [home / "tent.pdf"]
        assert ".." not in str(harness.asked[0])
