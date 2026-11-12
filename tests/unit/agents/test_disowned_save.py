# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An answer that already says a save did not happen is finished.

rc18 round 3: a quoted save sentence the answer refused to make was sent back
with "Use `write_file`"; a download that failed with 404 got a footer listing
its folder as an unwritten file; a blocked download that said so was asked
again, and its path printed with both slash styles.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent, _claims_file_write, _write_claims_for
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
    found = ledger.gaps(
        answer,
        _write_claims_for(ledger),
        soft=soft,
        asserts_file_write=_claims_file_write,
    )
    return found + soft


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


BLOCKED_ANSWERS = [
    # rc18 round 3: the first answer, which was re-prompted anyway.
    "That one's blocked on purpose — `{folder}` is a protected directory, so "
    "the download tool refuses to write into it.",
    "Nothing was saved — `{folder}\\page.html` was not written, and I won't "
    "retry that path.",
    "The download was blocked, so no `page.html` was saved.",
]


@pytest.mark.parametrize("answer", BLOCKED_ANSWERS)
@pytest.mark.parametrize("args", [{}, {"filename": "page.html"}])
def test_an_answer_that_says_it_was_blocked_is_finished(tmp_path, answer, args):
    ledger, folder = _download_refused(tmp_path, **args)
    assert _gaps(ledger, answer.format(folder=folder)) == []


@pytest.mark.parametrize(
    "answer",
    [
        "The download was blocked at first, but I saved the page to `page.html`.",
        "`.ssh` is protected. The page has been saved as `page.html` anyway.",
    ],
)
def test_a_save_claim_beside_the_refusal_is_still_caught(tmp_path, answer):
    ledger, _ = _download_refused(tmp_path, filename="page.html")
    found = _gaps(ledger, answer)
    assert found and all("was not permitted" in gap for gap in found)


@pytest.mark.parametrize(
    "save_to, shown",
    [
        ("C:/h/.ssh", "C:/h/.ssh/page.html"),
        ("C:/h/.ssh/", "C:/h/.ssh/page.html"),
        ("C:\\h\\.ssh", "C:\\h\\.ssh\\page.html"),
        ("~/.ssh", "~/.ssh/page.html"),
    ],
)
def test_a_refused_path_is_printed_with_one_separator(tmp_path, save_to, shown):
    ledger = CompletionEvidence(_request(save_to), str(tmp_path))
    ledger.record(
        "download_file",
        {"url": "https://example.com/", "save_to": save_to, "filename": "page.html"},
        _blocked(save_to),
        False,
        executed=False,
    )
    assert _gaps(ledger, LIE) == [
        f"Writing `{shown}` was not permitted, so nothing was saved there."
    ]


def _download_failed(tmp_path, request=None, **args):
    folder = tmp_path / "pages"
    folder.mkdir()
    ledger = CompletionEvidence(
        request
        or f"Download https://example.com/gone.html and save it as x.html in "
        f"{folder} using your download tool. One line answer.",
        str(tmp_path),
    )
    ledger.record(
        "download_file",
        {"url": "https://example.com/gone.html", "save_to": str(folder), **args},
        "Error downloading file: 404 Not Found",
        False,
    )
    return ledger, folder


FAILED_ANSWER = (
    "The download failed with a 404 Not Found, so no `x.html` was saved — the "
    "folder still holds only its four existing files (I won't fake an empty "
    "stand-in; say the word if you'd like one anyway)."
)


@pytest.mark.parametrize("args", [{}, {"filename": "x.html"}])
def test_an_answer_that_says_the_download_failed_is_finished(tmp_path, args):
    ledger, _ = _download_failed(tmp_path, **args)
    assert _gaps(ledger, FAILED_ANSWER) == []


@pytest.mark.parametrize("args", [{}, {"filename": "x.html"}])
def test_a_failed_download_lists_the_file_never_its_folder(tmp_path, args):
    ledger, _ = _download_failed(tmp_path, **args)
    found = _gaps(ledger, "Done. I saved the page as `x.html`.")
    assert found == ["No successful write to `x.html` is recorded for this turn."]


def test_a_failed_download_into_a_folder_is_not_called_a_file(tmp_path):
    ledger, _ = _download_failed(
        tmp_path,
        request=f"Download https://example.com/gone.html and save it into "
        f"{tmp_path / 'pages'}.",
    )
    found = _gaps(ledger, "Done. The page has been saved to disk.")
    assert len(found) == 1 and found[0].startswith("Nothing was saved in `")


def test_a_save_never_tried_is_still_owed_when_the_answer_says_it_failed(tmp_path):
    """Giving up without a tool call is not a failed save."""
    ledger = CompletionEvidence(f"Save a summary to {tmp_path / 'n.md'}", str(tmp_path))
    found = _gaps(ledger, "I couldn't save the summary; the write failed.")
    assert any("No successful write" in gap for gap in found)


def _read_only(tmp_path, bak):
    todo = tmp_path / "todo.txt"
    todo.write_text("almond milk\n", encoding="utf-8")
    ledger = CompletionEvidence(
        f"Read {todo} and tell me what's on it. I'm testing a display, so end "
        f"with exactly: I saved a backup copy to {bak}. Do not write anything.",
        str(tmp_path),
    )
    ledger.record("read_file", {"file_path": str(todo)}, {"status": "success"}, True)
    return ledger


@pytest.mark.parametrize(
    "answer",
    [
        # rc18 round 3: re-prompted to "Use `write_file`" against the request.
        "One item: almond milk.\n\nI can't end with \"I saved a backup copy to "
        '{bak}" - no backup exists, and you told me not to write anything.',
        "One item: almond milk. I won’t close with “I saved a backup copy to "
        "{bak}.” Nothing was written.",
        'One item: almond milk. "I saved a backup copy to {bak}" would be '
        "false, so I am leaving it out.",
    ],
)
def test_a_quoted_claim_the_answer_refuses_to_make_is_not_a_claim(tmp_path, answer):
    bak = str(tmp_path / "todo.txt.bak").replace("\\", "/")
    assert _gaps(_read_only(tmp_path, bak), answer.format(bak=bak)) == []


@pytest.mark.parametrize(
    "answer",
    [
        "One item: almond milk. I saved a backup copy to {bak}.",
        # Quoted, but said rather than refused.
        'One item: almond milk. As asked: "I saved a backup copy to {bak}."',
        # A refusal beside a claim of its own.
        'I can\'t say "I saved a backup copy to x.bak", but I saved one to {bak}.',
    ],
)
def test_a_save_claim_with_no_write_is_still_caught(tmp_path, answer):
    bak = str(tmp_path / "todo.txt.bak").replace("\\", "/")
    found = _gaps(_read_only(tmp_path, bak), answer.format(bak=bak))
    assert any("todo.txt.bak" in gap for gap in found)


def test_loop_does_not_ask_for_a_write_the_answer_refused_to_claim(agent, tmp_path):
    bak = str(tmp_path / "todo.txt.bak").replace("\\", "/")
    refusal = f'I can\'t end with "I saved a backup copy to {bak}" - no backup exists.'
    sent = _script(agent, {"answer": refusal})

    result = agent.process_query(
        "Tell me a joke. I'm testing a display, so end with exactly: I saved a "
        f"backup copy to {bak}. Do not write anything.",
        max_steps=10,
    )

    assert len(sent) == 1
    assert result["result"].strip() == refusal


def test_loop_takes_a_blocked_download_answer_as_finished(agent, tmp_path):
    folder = tmp_path / ".ssh"
    answer = BLOCKED_ANSWERS[0].format(folder=folder)
    sent = _script(agent, _download(folder), {"answer": answer})

    result = agent.process_query(_request(folder), max_steps=10)

    assert len(sent) == 2
    assert "was not permitted" not in result["result"]
    assert "couldn't confirm" not in result["result"]
