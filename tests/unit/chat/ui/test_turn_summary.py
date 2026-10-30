# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The per-turn summary the Agent UI side rail renders (#5222)."""

import json
import os
from types import SimpleNamespace

import pytest

from gaia.llm.inference_location import InferenceLocation
from gaia.ui._chat_helpers import _agent_location, _done_event, _turn_summary
from gaia.ui.database import ChatDatabase
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.turn_summary import TurnLedger, is_openable, upload_display_names
from gaia.ui.utils import message_to_response, uploads_dir

LOCAL = InferenceLocation("lemonade", "Gemma-4-E4B-it-GGUF", remote=False)
FIREWORKS = InferenceLocation("fireworks", "fireworks.glm-5p3-flash", remote=True)
GATEWAY = InferenceLocation("amd", "amd.some-model", remote=True)

SEARCH_TEXT = (
    'Web search results for: "kestrel"\n\n'
    "1. Kestrel - Wikipedia\n   https://en.wikipedia.org/wiki/Kestrel\n   A falcon.\n\n"
    "2. Kestrel facts\n   https://www.audubon.org/kestrel\n\n"
    "Use fetch_page(url) to read the full content of any result."
)


def _call(ledger, tool, args, data, success=True):
    ledger.tool_started(tool)
    ledger.tool_args(args)
    ledger.tool_result(data, success)


# ── Sources ─────────────────────────────────────────────────────────────


def test_web_research_lists_the_pages_it_read():
    ledger = TurnLedger()
    _call(ledger, "search_web", {"query": "kestrel"}, SEARCH_TEXT)
    _call(
        ledger,
        "fetch_page",
        {"url": "https://en.wikipedia.org/wiki/Kestrel"},
        "Page: Kestrel - Wikipedia\nURL: https://en.wikipedia.org/wiki/Kestrel\n\nText",
    )
    summary = ledger.summary(location=LOCAL)
    assert summary["pages"] == [
        {
            "url": "https://en.wikipedia.org/wiki/Kestrel",
            "title": "Kestrel - Wikipedia",
            "kind": "page",
        }
    ]
    assert summary["sites"] == ["duckduckgo.com", "en.wikipedia.org"]


def test_search_hits_stand_in_when_no_page_was_read():
    ledger = TurnLedger()
    _call(ledger, "search_web", {"query": "kestrel"}, SEARCH_TEXT)
    pages = ledger.summary(location=LOCAL)["pages"]
    assert [p["url"] for p in pages] == [
        "https://en.wikipedia.org/wiki/Kestrel",
        "https://www.audubon.org/kestrel",
    ]
    assert {p["kind"] for p in pages} == {"search_result"}


def test_a_failed_fetch_is_not_a_source_but_its_site_was_contacted():
    ledger = TurnLedger()
    _call(
        ledger,
        "fetch_page",
        {"url": "https://example.com/x"},
        "Error: 404 Client Error",
        success=False,
    )
    summary = ledger.summary(location=LOCAL)
    assert summary["pages"] == []
    assert summary["sites"] == ["example.com"]


def test_document_passages_are_named_by_the_attached_file_not_the_upload_id():
    ledger = TurnLedger()
    upload = "C:/Users/me/.gaia/chat/uploads/f27f4885-cec8-4161-9dbd-ebc8a3c25005.pdf"
    chunks = [
        {"source_file": upload, "page": 2, "content": "a"},
        {"source_file": upload, "page": 1, "content": "b"},
        {"source_file": upload, "page": 2, "content": "c"},
        {"source_file": "C:/docs/plan.docx", "content": "d"},
    ]
    _call(
        ledger,
        "query_specific_file",
        {"file_path": upload},
        json.dumps({"status": "success", "chunks": chunks}),
    )
    docs = ledger.summary(
        location=LOCAL,
        display_names={"f27f4885-cec8-4161-9dbd-ebc8a3c25005.pdf": "kestrel-plan.pdf"},
    )["documents"]
    assert docs[0] == {
        "name": "kestrel-plan.pdf",
        "path": upload,
        "pages": [1, 2],
        "passages": 3,
    }
    assert docs[1]["name"] == "plan.docx"


@pytest.mark.parametrize(
    "url",
    [
        "javascript://example.com/%0aalert(1)",
        "JAVASCRIPT://example.com/x",
        "data://example.com/text/html,<script>",
        "file://server/share/doc.pdf",
        "ftp://example.com/x",
        "//example.com/no-scheme",
    ],
)
def test_a_page_with_a_non_web_address_is_never_a_source(url):
    ledger = TurnLedger()
    _call(ledger, "fetch_page", {"url": "https://example.com/"}, {"url": url})
    _call(ledger, "browser_open", {"url": url}, "Page: Trap")
    assert ledger.summary(location=LOCAL)["pages"] == []


def test_a_passage_two_queries_both_returned_is_counted_once():
    ledger = TurnLedger()
    doc = "C:/docs/plan.pdf"
    same = {"source_file": doc, "page": 3, "content": "the budget is $4M"}
    for query in ("budget", "how much money"):
        _call(
            ledger,
            "query_specific_file",
            {"file_path": doc, "query": query},
            {"status": "success", "chunks": [same, {**same, "page": 4}]},
        )
    _call(
        ledger,
        "query_documents",
        {"query": "budget"},
        {"chunks": [{**same, "content": "a different passage"}, dict(same)]},
    )
    (entry,) = ledger.summary(location=LOCAL)["documents"]
    assert entry["passages"] == 3
    assert entry["pages"] == [3, 4]


def test_passages_without_text_are_still_counted_and_odd_bodies_do_not_break():
    ledger = TurnLedger()
    doc = "C:/docs/plan.pdf"
    chunks = [
        {"source_file": doc},
        {"source_file": doc},
        {"source_file": doc, "content": ["not", "hashable"]},
        {"source_file": doc, "chunk_index": 7},
        {"source_file": doc, "chunk_index": 7},
        {"source_file": "C:/docs/other.pdf", "page": 1, "content": "x"},
        {"source_file": doc, "page": 1, "content": "x"},
    ]
    _call(ledger, "search_documents", {}, {"chunks": chunks})
    docs = ledger.summary(location=LOCAL)["documents"]
    assert [d["passages"] for d in docs] == [5, 1]


@pytest.mark.parametrize("name", ["feed.xml", "FEED.XML", "page.html", "x.svg"])
def test_types_that_can_open_in_a_browser_are_not_openable(name):
    assert not is_openable(f"C:/out/{name}")


def test_upload_display_names_reads_the_composer_link_labels():
    root = uploads_dir().resolve()
    message = (
        f"What does this say? [kestrel-plan.pdf]({root}/abc.pdf) and "
        "[notes.md](/api/files/uploads/def.md) but not [x](https://x.com/y.pdf)"
    )
    assert upload_display_names(message) == {
        "abc.pdf": "kestrel-plan.pdf",
        "def.md": "notes.md",
    }


def test_upload_display_names_matches_the_uploads_folder_however_it_is_spelled():
    root = uploads_dir().resolve()
    spellings = {
        "a.pdf": f"{root.as_posix()}/a.pdf",
        "b.pdf": f"{root}{os.sep}b.pdf",
        "c.pdf": f"{root}/sub/../c.pdf",
    }
    if os.name == "nt":
        spellings["d.pdf"] = f"{str(root).upper()}\\d.pdf"
    message = " ".join(f"[{n}-label]({t})" for n, t in spellings.items())
    assert upload_display_names(message) == {n: f"{n}-label" for n in spellings}


def test_upload_display_names_ignores_lookalike_folders_and_escapes():
    root = uploads_dir().resolve()
    message = (
        f"[sibling]({root}-evil/a.pdf) [parent]({root}/../b.pdf) "
        f"[prefix]({root}x/c.pdf) [bare]({root})"
    )
    assert upload_display_names(message) == {}


# ── Files ───────────────────────────────────────────────────────────────


def test_files_written_this_turn_are_listed_once_with_whether_they_can_be_opened(
    tmp_path,
):
    report = tmp_path / "recommendation.md"
    script = tmp_path / "build.py"
    by_script = tmp_path / "bom.xlsx"
    for path in (report, script, by_script):
        path.write_text("x", encoding="utf-8")
    ledger = TurnLedger()
    _call(ledger, "write_file", {"file_path": str(report)}, {"status": "success"})
    _call(ledger, "write_python_file", {"file_path": str(script)}, {"status": "ok"})
    _call(
        ledger,
        "write_file",
        {"file_path": str(tmp_path / "denied.md")},
        {"status": "denied"},
        success=False,
    )
    files = ledger.summary(location=LOCAL, written_files=[str(by_script), str(report)])[
        "files"
    ]
    assert [(f["name"], f["openable"]) for f in files] == [
        ("bom.xlsx", True),
        ("recommendation.md", True),
        ("build.py", False),
    ]


def test_each_file_records_when_it_was_last_changed(tmp_path):
    out = tmp_path / "notes.md"
    out.write_text("x", encoding="utf-8")
    files = TurnLedger().summary(location=LOCAL, written_files=[str(out)])["files"]
    assert files[0]["mtime"] == out.stat().st_mtime


def test_a_relative_or_deleted_path_is_not_offered(tmp_path):
    ledger = TurnLedger()
    _call(ledger, "write_file", {"file_path": "out.md"}, {"status": "success"})
    _call(
        ledger,
        "write_file",
        {"file_path": str(tmp_path / "gone.md")},
        {"status": "success"},
    )
    assert ledger.summary(location=LOCAL)["files"] == []


# ── Where it ran, live data, cost ───────────────────────────────────────


def test_a_local_turn_says_this_pc_and_costs_nothing():
    summary = TurnLedger().summary(location=LOCAL)
    assert summary["model"] == {
        "remote": False,
        "provider": "lemonade",
        "provider_name": "Lemonade on this machine",
        "name": "Gemma-4-E4B-it-GGUF",
    }
    assert summary["cost"] == {"kind": "local"}


def test_a_cloud_turn_names_the_provider_and_estimates_its_cost():
    summary = TurnLedger().summary(
        location=FIREWORKS,
        usage={"input_tokens": 1_000_000, "cached_tokens": 0, "tokens": 1_000_000},
    )
    assert summary["model"]["remote"] is True
    assert summary["model"]["provider_name"] == "Fireworks AI"
    assert summary["model"]["name"] == "glm-5p3-flash"
    assert summary["cost"] == {"kind": "estimate", "usd": 0.65}


def test_cloud_cost_without_a_rate_card_or_token_counts_is_unknown():
    assert TurnLedger().summary(
        location=GATEWAY, usage={"input_tokens": 5, "tokens": 5}
    )["cost"] == {"kind": "unknown"}
    assert TurnLedger().summary(location=FIREWORKS)["cost"] == {"kind": "unknown"}
    assert (
        TurnLedger().summary(location=GATEWAY)["model"]["provider_name"]
        == "AMD LLM Gateway"
    )


def test_an_unknown_location_is_reported_not_assumed_local():
    summary = TurnLedger().summary(location=None)
    assert "model" not in summary
    assert summary["cost"] == {"kind": "unknown"}


@pytest.mark.parametrize(
    "outcomes, expected",
    [
        ([False, False], {"attempted": 2, "failed": 2}),
        ([False, True], {"attempted": 2, "failed": 1}),
    ],
)
def test_live_data_counts_every_internet_tool_and_its_failures(outcomes, expected):
    ledger = TurnLedger()
    for ok in outcomes:
        _call(ledger, "fetch_page", {"url": "https://a.org"}, "Page: A", success=ok)
    _call(ledger, "read_file", {"file_path": "x"}, {"status": "error"}, success=False)
    assert ledger.summary(location=LOCAL)["live_data"] == expected


def _offline(tool, args):
    from gaia.agents.base.offline import offline_refusal

    refusal = offline_refusal(tool, args)
    assert refusal is not None
    return refusal


def test_a_call_internet_off_refused_contacted_nothing_and_did_not_fail():
    ledger = TurnLedger()
    _call(ledger, "search_web", {"query": "q"}, _offline("search_web", {}), False)
    args = {"url": "https://example.org/a"}
    _call(ledger, "fetch_page", args, _offline("fetch_page", args), False)
    summary = ledger.summary(location=LOCAL)
    assert summary["sites"] == []
    assert summary["live_data"] == {"attempted": 0, "failed": 0}


def test_a_call_the_user_denied_contacted_nothing():
    ledger = TurnLedger()
    denied = {"status": "denied", "error": "Tool 'fetch_page' was denied by the user."}
    _call(ledger, "fetch_page", {"url": "https://example.org"}, denied, False)
    assert ledger.summary(location=LOCAL)["sites"] == []


@pytest.mark.parametrize(
    "data",
    [
        {"status": "error", "error": "timed out"},
        {"status": "error", "executed": True, "error": "HTTP 503"},
        "Error: timed out",
    ],
)
def test_a_call_that_ran_and_failed_still_counts_and_names_its_site(data):
    ledger = TurnLedger()
    _call(ledger, "fetch_page", {"url": "https://example.org"}, data, False)
    blocked = {"url": "https://other.example"}
    _call(ledger, "fetch_page", blocked, _offline("fetch_page", blocked), False)
    summary = ledger.summary(location=LOCAL)
    assert summary["sites"] == ["example.org"]
    assert summary["live_data"] == {"attempted": 1, "failed": 1}


# ── Wiring: SSE handler, chat helpers, persistence ──────────────────────


def test_the_sse_handler_feeds_the_ledger_from_its_tool_events():
    handler = SSEOutputHandler()
    handler.print_tool_usage("fetch_page")
    handler.pretty_print_json({"url": "https://www.amd.com/en"}, "Arguments")
    handler.pretty_print_json("Error: timed out")
    handler.print_tool_complete()
    summary = handler.turn_ledger.summary(location=LOCAL)
    assert summary["sites"] == ["amd.com"]
    assert summary["live_data"] == {"attempted": 1, "failed": 1}


def test_turn_location_prefers_the_agents_live_client():
    agent = SimpleNamespace(_inference_location=lambda: FIREWORKS, model_id="x")
    assert _agent_location(agent) is FIREWORKS


def test_an_agent_that_cannot_locate_itself_is_not_called_local():
    assert _agent_location(SimpleNamespace(model_id="Gemma-4-E4B-it-GGUF")) is None


def test_turn_summary_without_an_agent_has_no_location():
    handler = SSEOutputHandler()
    summary = _turn_summary(handler, None, "hi", None)
    assert "model" not in summary


def test_turn_summary_includes_files_the_completion_evidence_saw(tmp_path):
    out = tmp_path / "result.csv"
    out.write_text("a,b", encoding="utf-8")
    agent = SimpleNamespace(
        _inference_location=lambda: LOCAL,
        _completion_evidence=SimpleNamespace(written_files=lambda: [str(out)]),
    )
    summary = _turn_summary(SSEOutputHandler(), agent, "make a csv", {"tokens": 3})
    assert [f["path"] for f in summary["files"]] == [str(out)]


def test_a_summary_that_cannot_be_built_says_why_and_keeps_the_turn(caplog):
    def broken():
        raise KeyError("model")

    agent = SimpleNamespace(_inference_location=broken)
    summary = _turn_summary(SSEOutputHandler(), agent, "hi", None)
    assert summary == {"error": "KeyError: 'model'", "cost": {"kind": "unknown"}}
    assert "Could not build this turn's summary" in caplog.text


def test_done_event_carries_the_turn_summary_only_when_there_is_one():
    assert "turn_summary" not in _done_event(1, "hi", False)
    summary = TurnLedger().summary(location=LOCAL)
    assert _done_event(1, "hi", False, turn_summary=summary)["turn_summary"] == summary


def test_the_turn_summary_survives_a_reload():
    db = ChatDatabase(":memory:")
    session = db.create_session(title="t")
    summary = TurnLedger().summary(location=FIREWORKS)
    msg_id = db.add_message(session["id"], "assistant", "answer", turn_summary=summary)
    db.upsert_message(session["id"], None, "assistant", "plain")
    messages = db.get_messages(session["id"])
    responses = {m["id"]: message_to_response(m) for m in messages}
    restored = responses[msg_id].turn_summary
    assert restored is not None
    assert restored.model.provider_name == "Fireworks AI"
    assert restored.cost.kind == "unknown"
    others = [r for i, r in responses.items() if i != msg_id]
    assert others[0].turn_summary is None
