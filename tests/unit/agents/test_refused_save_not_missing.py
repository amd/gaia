# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A save GAIA's own write policy refused is not a missing deliverable.

rc18: a download into ``.ssh`` was refused, then the completion check told the
model to use ``write_file`` there and the answer listed the folder and
``page.html`` as files with no recorded write.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent, _claims_file_write
from gaia.agents.base.completion import CompletionEvidence
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.base.verification import NOT_EXECUTED

HONEST = "I can't save into `.ssh`: that folder is protected. Pick another folder?"
LIE = "Done. I saved the page to `page.html`."


def _blocked(folder):
    return {
        **NOT_EXECUTED,
        "status": "error",
        "error": f"Write blocked: '{folder}' is inside protected directory '.ssh'",
    }


def _request(folder):
    return (
        f"Download https://example.com/ and save it as page.html into {folder} "
        "using your download tool."
    )


def _download_refused(tmp_path, **args):
    folder = tmp_path / ".ssh"
    ledger = CompletionEvidence(_request(folder), str(tmp_path))
    ledger.record(
        "download_file",
        {"url": "https://example.com/", "save_to": str(folder), **args},
        _blocked(folder),
        False,
        executed=False,
    )
    return ledger, folder


def _gaps(ledger, answer):
    soft = []
    found = ledger.gaps(answer, _claims_file_write, soft=soft)
    return found + soft


@pytest.mark.parametrize("args", [{}, {"filename": "page.html"}])
def test_a_refused_download_is_a_finished_answer(tmp_path, args):
    ledger, _ = _download_refused(tmp_path, **args)
    assert _gaps(ledger, HONEST) == []


@pytest.mark.parametrize("args", [{}, {"filename": "page.html"}])
def test_a_save_claim_after_a_refused_download_names_the_refusal(tmp_path, args):
    ledger, _ = _download_refused(tmp_path, **args)
    found = _gaps(ledger, LIE)
    assert found and all("was not permitted" in gap for gap in found)
    assert "No successful write" not in " ".join(found)


def test_a_write_the_blocklist_refused_is_a_finished_answer(tmp_path):
    folder = tmp_path / ".ssh"
    target = str(folder / "page.html")
    ledger = CompletionEvidence(f"Save the page to {target}", str(tmp_path))
    ledger.record(
        "write_file", {"file_path": target}, _blocked(target), False, executed=False
    )
    assert _gaps(ledger, HONEST) == []


def test_a_download_that_merely_failed_is_still_a_missing_save(tmp_path):
    folder = tmp_path / "pages"
    ledger = CompletionEvidence(_request(folder), str(tmp_path))
    ledger.record(
        "download_file",
        {"url": "https://example.com/", "save_to": str(folder)},
        "Error downloading file: connection reset",
        False,
    )
    assert "No successful write" in " ".join(_gaps(ledger, LIE))


def test_another_unwritten_file_is_still_listed_beside_the_refusal(tmp_path):
    folder = tmp_path / ".ssh"
    notes = tmp_path / "notes.md"
    ledger = CompletionEvidence(
        f"Download https://example.com/ into {folder} and write a summary to "
        f"{notes}.",
        str(tmp_path),
    )
    ledger.record(
        "download_file",
        {"url": "https://example.com/", "save_to": str(folder), "filename": "x.html"},
        _blocked(folder),
        False,
        executed=False,
    )
    found = " ".join(_gaps(ledger, "The download was refused. I saved the summary."))
    assert "notes.md" in found and "No successful write" in found


class _DownloadAgent(Agent):
    def _get_system_prompt(self):
        return "test"

    def _register_tools(self):
        @tool
        def download_file(url: str, save_to: str = "~/Downloads") -> dict:
            """Download a file."""
            return _blocked(save_to)

        @tool
        def write_file(file_path: str, content: str) -> dict:
            """Write a file."""
            raise AssertionError("the refused save must not be retried")


@pytest.fixture
def agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _DownloadAgent(silent_mode=True, skip_lemonade=True)
    agent.streaming = False
    agent._tool_requires_confirmation = lambda *a, **kw: False
    agent.console = MagicMock()
    agent.console.cancelled = None
    yield agent
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _script(agent, *turns):
    sent = []
    left = list(turns)

    def send(messages, *a, **kw):
        sent.append([dict(m) for m in messages])
        assert left, "unexpected model call"
        return MagicMock(text=json.dumps(left.pop(0)), stats={})

    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = send
    return sent


def _download(folder):
    return {
        "tool": "download_file",
        "tool_args": {"url": "https://example.com/", "save_to": str(folder)},
    }


def test_loop_takes_the_refusal_as_the_answer(agent, tmp_path):
    folder = tmp_path / ".ssh"
    sent = _script(agent, _download(folder), {"answer": HONEST})

    result = agent.process_query(_request(folder), max_steps=10)

    assert len(sent) == 2
    assert HONEST in result["result"]
    assert "No successful write" not in result["result"]
    assert "couldn't confirm" not in result["result"]


def test_loop_never_asks_for_the_refused_save_through_another_tool(agent, tmp_path):
    folder = tmp_path / ".ssh"
    sent = _script(agent, _download(folder), {"answer": LIE}, {"answer": LIE})

    result = agent.process_query(_request(folder), max_steps=10)

    correction = sent[2][-1]["content"]
    assert "Do not retry that write, here or anywhere else" in correction
    assert "write_file" not in correction
    assert "was not permitted" in result["result"]
    assert "No successful write" not in result["result"]


@pytest.mark.parametrize(
    "request_text",
    [
        "Download https://example.com/ into {folder} and write a summary to notes.md.",
        "Download https://example.com/ into {folder}. Then save a summary as notes.md",
        "Write a summary to notes.md, then download https://example.com/ to {folder}",
    ],
)
def test_a_refused_folder_does_not_swallow_a_file_meant_elsewhere(
    tmp_path, request_text
):
    """A download refused with no file name is not every bare file's excuse."""
    folder = tmp_path / ".ssh"
    ledger = CompletionEvidence(request_text.format(folder=folder), str(tmp_path))
    ledger.record(
        "download_file",
        {"url": "https://example.com/", "save_to": str(folder)},
        _blocked(folder),
        False,
        executed=False,
    )
    claimed = (
        "I can't save into `.ssh`, it is protected. I saved the summary to notes.md."
    )
    found = " ".join(_gaps(ledger, claimed))
    assert "No successful write to `notes.md`" in found


def test_the_file_named_beside_the_refused_folder_is_still_excused(tmp_path):
    folder = tmp_path / ".ssh"
    ledger = CompletionEvidence(
        f"Save https://example.com/ as page.html in {folder}, and write notes.md.",
        str(tmp_path),
    )
    ledger.record(
        "download_file",
        {"url": "https://example.com/", "save_to": str(folder)},
        _blocked(folder),
        False,
        executed=False,
    )
    claimed = "I can't save into `.ssh`, it is protected. I saved notes.md."
    found = " ".join(_gaps(ledger, claimed))
    assert "No successful write to `page.html`" not in found
    assert "No successful write to `notes.md`" in found


def _refused_download(
    tmp_path, request_text, folder, url="https://example.com/a.pdf", **args
):
    ledger = CompletionEvidence(request_text, str(tmp_path))
    ledger.record(
        "download_file",
        {"url": url, **args},
        {**NOT_EXECUTED, "status": "error", "error": f"Access denied: {folder}"},
        False,
        executed=False,
    )
    return ledger


@pytest.mark.parametrize(
    "request_text",
    [
        "Save a summary of my Downloads folder to summary.md, and download "
        "https://example.com/a.pdf.",
        "Save a summary to summary.md in Downloads, and download "
        "https://example.com/a.pdf.",
        "Save the summary as summary.md in Downloads, and download "
        "https://example.com/a.pdf.",
    ],
)
def test_a_refused_download_does_not_excuse_a_different_file(tmp_path, request_text):
    """Only the refused call's own file is excused, not a file beside its folder."""
    ledger = _refused_download(tmp_path, request_text, "~/Downloads")
    found = " ".join(_gaps(ledger, "I couldn't download the logo: access was denied."))
    assert "No successful write to `summary.md`" in found


def test_a_refused_download_does_not_excuse_a_save_with_no_path(tmp_path):
    ledger = _refused_download(
        tmp_path,
        "Write notes.md listing what is in my Downloads folder, and download "
        "https://example.com/a.pdf.",
        "~/Downloads",
    )
    found = " ".join(_gaps(ledger, "I couldn't download the logo: access was denied."))
    assert "requested output file has no recorded successful write" in found


def test_the_refused_downloads_own_file_is_excused_and_its_neighbour_is_not(tmp_path):
    folder = tmp_path / "out"
    ledger = _refused_download(
        tmp_path,
        f"Download https://example.com/a.pdf and save it as report.pdf in {folder}, "
        f"and save a summary as summary.md in {folder}.",
        folder,
        save_to=str(folder),
        filename="report.pdf",
    )
    found = " ".join(_gaps(ledger, "I couldn't save the report: access denied."))
    assert "`report.pdf`" not in found
    assert "No successful write to `summary.md`" in found


def test_the_url_file_of_a_refused_folder_download_is_excused(tmp_path):
    folder = tmp_path / "out"
    ledger = _refused_download(
        tmp_path,
        f"Download https://example.com/a.pdf and save it as a.pdf in {folder}.",
        folder,
        save_to=str(folder),
    )
    assert _gaps(ledger, "I couldn't save a.pdf: access denied.") == []


def test_a_name_placed_in_the_folder_is_not_the_download_when_the_url_names_one(
    tmp_path,
):
    folder = tmp_path / "out"
    ledger = _refused_download(
        tmp_path,
        f"Download https://example.com/a.pdf and save it as a.pdf in {folder}, "
        f"and save a summary as summary.md in {folder}.",
        folder,
        save_to=str(folder),
    )
    found = " ".join(_gaps(ledger, "I couldn't save a.pdf: access denied."))
    assert "`a.pdf`" not in found
    assert "No successful write to `summary.md`" in found


def test_a_folder_name_inside_another_word_does_not_place_a_file(tmp_path):
    ledger = _refused_download(
        tmp_path,
        f"Download https://example.com/ and save it as page.html into "
        f"{tmp_path / '.sshkeys'}.",
        tmp_path / ".ssh",
        url="https://example.com/",
        save_to=str(tmp_path / ".ssh"),
    )
    found = " ".join(_gaps(ledger, HONEST))
    assert "No successful write to `page.html`" in found


@pytest.mark.parametrize(
    "request_text",
    [
        "Download https://example.com/ into {folder}, and save a summary to notes.md "
        "in {folder}.",
        "Download https://example.com/ into {folder}, and save {folder}/notes.md.",
    ],
)
def test_a_folder_download_does_not_excuse_another_file_in_that_folder(
    tmp_path, request_text
):
    """A prompt declined for the download is not a decline for notes.md."""
    folder = tmp_path / "docs"
    ledger = _refused_download(
        tmp_path,
        request_text.format(folder=folder),
        folder,
        url="https://example.com/",
        save_to=str(folder),
    )
    found = " ".join(_gaps(ledger, "I couldn't download it: you declined."))
    assert "notes.md" in found and "not permitted" not in found


def test_a_full_path_given_as_the_downloads_name_is_excused(tmp_path):
    folder = tmp_path / ".ssh"
    ledger = _refused_download(
        tmp_path,
        f"Download https://example.com/ and save it as {folder / 'page.html'}.",
        folder,
        url="https://example.com/",
        save_to=str(folder),
    )
    assert _gaps(ledger, HONEST) == []
