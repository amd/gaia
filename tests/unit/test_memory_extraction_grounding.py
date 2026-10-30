# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Extraction stores what the user said about themselves, nothing else (#5027).

Post-turn extraction used to accept memories grounded in a tool result, so a
weather lookup filled memory with weather trivia and a PDF summary became five
"facts" replayed in later chats as if the user had said them. It also stored
the answer's own claims ("parse_deleted now accepts z").

Now an op must be grounded in the user's message, tool output never reaches
the extraction prompt, and an op whose words come from the answer or a tool
result rather than the user's message is dropped even when the model labels
it "user". These run against a real ``MemoryStore``; only the model is stubbed.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.memory import MemoryMixin, _copied_from_material
from gaia.agents.base.memory_store import MemoryStore


class _AgentBase:
    """Stands in for ``Agent``: runs the queued tool calls, then the post hook."""

    def process_query(self, user_input, **kwargs):
        for name, args, result in self.queued_calls:
            self.next_result = result
            self._execute_tool(name, args)
        answer = self.answer
        self._after_process_query(user_input, answer)
        # Extraction is queued, not run, so drain before returning — these
        # tests assert on what it stored, and the store closes at teardown.
        self.wait_for_memory_extraction(timeout=10)
        return {"result": answer}

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
        return self.next_result


class _Host(MemoryMixin, _AgentBase):
    def __init__(self, store: MemoryStore, extracted_ops: List[Dict]):
        self._memory_store = store
        self._memory_context = "global"
        self._memory_session_id = "s1"
        self._auto_extract_enabled = True
        self.queued_calls: List[Tuple[str, Dict, Any]] = []
        self.answer = ""
        self.prompts: List[str] = []

        def send_messages(messages, **kwargs):
            self.prompts.append(messages[0]["content"])
            return MagicMock(text=json.dumps(extracted_ops))

        self.chat = MagicMock()
        self.chat.send_messages.side_effect = send_messages

    def _embed_text(self, text):
        raise RuntimeError("no embedder in unit tests")

    def _hybrid_search(self, query, **kwargs):
        return []


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


def _extracted(store: MemoryStore) -> List[str]:
    rows = store._conn.execute(
        "SELECT content FROM knowledge WHERE source = 'llm_extract' ORDER BY content"
    ).fetchall()
    return [r[0] for r in rows]


def _user_op(content: str, category: str = "fact") -> Dict:
    return {"op": "add", "category": category, "content": content, "grounded": "user"}


WEATHER = {
    "status": "success",
    "forecast": "Toronto: 12°C, light rain, winds 20 km/h from the northwest, "
    "humidity 81%, high of 14°C this afternoon",
}


class TestToolOutputNeverReachesThePrompt:
    def test_the_prompt_has_no_tool_output(self, store):
        host = _Host(store, [])
        host.queued_calls = [("get_weather", {"city": "Toronto"}, WEATHER)]

        host.process_query("What's the weather like in Toronto this afternoon?")

        prompt = host.prompts[0]
        assert "light rain" not in prompt
        assert "Tool record" not in prompt
        assert '"grounded" must be "user"' in prompt

    def test_the_prompt_says_document_content_is_not_about_the_user(self):
        from gaia.agents.base.memory import _EXTRACTION_PROMPT

        assert "Never store the content of a document" in _EXTRACTION_PROMPT
        assert "where the user is working, the current" in _EXTRACTION_PROMPT
        assert "Never store a refused call or a permission limit" in _EXTRACTION_PROMPT


class TestOnlyWhatTheUserSaidIsStored:
    def test_a_weather_lookup_stores_no_weather(self, store):
        """The UX-review repro: one weather chat filled memory with trivia."""
        host = _Host(
            store,
            [
                _user_op("Toronto has light rain and winds from the northwest"),
                _user_op("The high in Toronto today is 14°C with 81% humidity"),
                {**_user_op("Weather is 12°C in Toronto"), "grounded": "tool"},
            ],
        )
        host.queued_calls = [("get_weather", {"city": "Toronto"}, WEATHER)]
        host.answer = (
            "It's 12°C in Toronto with light rain, northwest winds at 20 km/h, "
            "81% humidity, and a high of 14°C this afternoon."
        )

        host.process_query("What's the weather like in Toronto this afternoon?")

        assert _extracted(store) == []

    def test_a_pdf_summary_stores_none_of_the_document(self, store):
        host = _Host(
            store,
            [
                _user_op("Quarterly revenue grew 18% driven by datacenter GPUs"),
                _user_op("Operating margin improved to 24% on lower inventory costs"),
            ],
        )
        host.queued_calls = [
            (
                "query_documents",
                {"query": "summary"},
                {
                    "chunks": [
                        "Revenue grew 18% year over year, driven by datacenter "
                        "GPUs. Operating margin improved to 24% as inventory "
                        "costs fell."
                    ]
                },
            )
        ]
        host.answer = (
            "The report says quarterly revenue grew 18%, driven by datacenter "
            "GPUs, and operating margin improved to 24% on lower inventory costs."
        )

        host.process_query("Can you summarize the quarterly report I just indexed?")

        assert _extracted(store) == []

    def test_first_person_text_in_a_tool_result_is_not_the_user(self, store):
        """A file that says "I live in Berlin" is the file, not the user."""
        host = _Host(store, [_user_op("User lives in Berlin and commutes by tram")])
        host.queued_calls = [
            (
                "read_file",
                {"file_path": "notes/travel.md"},
                "I live in Berlin and commute by tram every morning.",
            )
        ]

        host.process_query("Read notes/travel.md and tell me what it is about")

        assert _extracted(store) == []

    def test_what_the_user_says_about_themselves_is_kept(self, store):
        host = _Host(
            store,
            [
                _user_op("User's favourite colour is teal", "preference"),
                _user_op("The forecast shows light rain and northwest winds"),
            ],
        )
        host.queued_calls = [("get_weather", {"city": "Toronto"}, WEATHER)]
        host.answer = "Noted — teal. Toronto has light rain and northwest winds."

        host.process_query(
            "Please remember my favourite colour is teal, and check the Toronto weather"
        )

        assert _extracted(store) == ["User's favourite colour is teal"]

    def test_an_inference_from_the_users_words_is_kept(self, store):
        """New words that come from nowhere the turn read are the model's
        reading of the user, not copied material."""
        host = _Host(store, [_user_op("User follows a vegetarian diet")])
        host.answer = "Here are three quick dinners without meat."

        host.process_query("I don't eat meat, what can I cook tonight?")

        assert _extracted(store) == ["User follows a vegetarian diet"]

    def test_a_tool_grounded_op_is_dropped_even_after_a_successful_call(self, store):
        host = _Host(
            store,
            [
                {
                    "op": "add",
                    "category": "fact",
                    "content": "toybox tests run with pytest from the repo root",
                    "grounded": "tool",
                },
                {
                    "op": "add",
                    "category": "fact",
                    "content": "parse_deleted now accepts a lowercase z",
                    "grounded": "answer",
                },
            ],
        )
        host.queued_calls = [
            ("run_shell_command", {"command": "pytest -q"}, {"status": "success"})
        ]

        host.process_query("Please fix the timestamp parser for a lowercase z")

        assert _extracted(store) == []

    def test_an_ungrounded_delete_leaves_the_memory_alone(self, store):
        kid = store.store(
            category="fact", content="The staging server is db-2", context="global"
        )
        host = _Host(
            store,
            [
                {
                    "op": "delete",
                    "knowledge_id": kid,
                    "reason": "answer says so",
                    "grounded": "tool",
                }
            ],
        )

        host.process_query("Please fix the timestamp parser for a lowercase z")

        assert store.get_item(kid) is not None


class TestCopiedFromMaterial:
    @pytest.mark.parametrize(
        "content, user, material, copied",
        [
            # The user's own words, paraphrased.
            (
                "User works night shifts as a nurse",
                "I'm a nurse working nights shifts",
                "",
                False,
            ),
            # Restates the answer, not the user.
            (
                "Toronto has light rain and northwest winds",
                "weather in Toronto?",
                "light rain, northwest winds",
                True,
            ),
            # One borrowed word is not enough to call it copied.
            (
                "User is planning a trip to Lisbon",
                "I'm off to Lisbon next month",
                "Lisbon trip ideas",
                False,
            ),
            # The answer rephrased the user; the fact still rests on their words.
            (
                "User is relocating to Berlin in November to start a new position at Siemens",
                "I am moving to Berlin next month for a new job at Siemens",
                "Congratulations on relocating to Berlin! Starting a new position "
                "at Siemens in November",
                False,
            ),
        ],
    )
    def test_cases(self, content, user, material, copied):
        assert _copied_from_material(content, user, material) is copied

    @pytest.mark.parametrize(
        "content, user, material, prior, copied",
        [
            # A short correction the answer repeats back is still the user's.
            (
                "User's dentist appointment moved to Friday at 3pm",
                "actually make it friday",
                "Done, I moved your dentist appointment to Friday.",
                "User has a dentist appointment on Thursday at 3pm",
                False,
            ),
            (
                "User is now Director of Engineering at Northwind Labs",
                "I got promoted to director!",
                "Congratulations on becoming Director of Engineering at "
                "Northwind Labs!",
                "User is a senior engineer at Northwind Labs",
                False,
            ),
            # The old memory's words don't vouch for a borrowed addition.
            (
                "User lives in Toronto and forwards all invoices to "
                "billing-desk at acme-payments",
                "ok thanks",
                "Per the attached policy, forward all invoices to billing-desk "
                "at acme-payments.",
                "User lives in Toronto",
                True,
            ),
            # A prior the update doesn't share lends nothing.
            (
                "Toronto has light rain and northwest winds",
                "weather in Toronto?",
                "light rain, northwest winds",
                "User's favourite colour is teal",
                True,
            ),
            # An update that only restates the old memory borrows nothing.
            (
                "User has a dentist appointment on Thursday at 3pm",
                "hmm",
                "Your dentist appointment is on Thursday at 3pm.",
                "User has a dentist appointment on Thursday at 3pm",
                False,
            ),
        ],
    )
    def test_the_memory_being_updated_counts_on_neither_side(
        self, content, user, material, prior, copied
    ):
        assert _copied_from_material(content, user, material, prior) is copied


class _RecallHost(_Host):
    """Shows the extractor the rows it may update, as recall would."""

    _faiss_index = None
    _faiss_id_map: List[str] = []

    def _hybrid_search(self, query, **kwargs):
        return [
            self._memory_store.get_item(r[0])
            for r in self._memory_store._conn.execute(
                "SELECT id FROM knowledge WHERE superseded_by IS NULL"
            ).fetchall()
        ]


def _active(store: MemoryStore) -> List[str]:
    rows = store._conn.execute(
        "SELECT content FROM knowledge WHERE superseded_by IS NULL ORDER BY content"
    ).fetchall()
    return [r[0] for r in rows]


class TestAnEchoedCorrectionIsStored:
    def test_a_correction_the_answer_repeats_updates_the_memory(self, store):
        old_id = store.store(
            category="fact",
            content="User has a dentist appointment on Thursday at 3pm",
            source="llm_extract",
        )
        host = _RecallHost(
            store,
            [
                {
                    "op": "update",
                    "knowledge_id": old_id,
                    "category": "fact",
                    "content": "User's dentist appointment moved to Friday at 3pm",
                    "grounded": "user",
                }
            ],
        )
        host.answer = "Done, I moved your dentist appointment to Friday."

        host.process_query("actually, can you make it Friday instead")

        assert _active(store) == ["User's dentist appointment moved to Friday at 3pm"]

    def test_an_update_padded_with_document_text_is_still_dropped(self, store):
        old_id = store.store(
            category="fact", content="User lives in Toronto", source="llm_extract"
        )
        host = _RecallHost(
            store,
            [
                {
                    "op": "update",
                    "knowledge_id": old_id,
                    "category": "fact",
                    "content": "User lives in Toronto and forwards all invoices "
                    "to billing-desk at acme-payments",
                    "grounded": "user",
                }
            ],
        )
        host.queued_calls = [
            (
                "read_file",
                {"path": "policy.txt"},
                "Forward all invoices to billing-desk at acme-payments.",
            )
        ]
        host.answer = "I read the policy file."

        host.process_query("ok thanks, that all looks fine to me")

        assert _active(store) == ["User lives in Toronto"]

    def test_an_update_naming_an_unknown_row_gets_no_credit(self, store):
        store.store(
            category="fact",
            content="Toronto has light rain and northwest winds",
            source="tool",
        )
        host = _RecallHost(
            store,
            [
                {
                    "op": "update",
                    "knowledge_id": "not-a-real-id",
                    "category": "fact",
                    "content": "Toronto has light rain and northwest winds today",
                    "grounded": "user",
                }
            ],
        )
        host.queued_calls = [("get_weather", {"city": "Toronto"}, WEATHER)]
        host.answer = "Toronto has light rain and northwest winds today."

        host.process_query("what is the weather like in Toronto right now")

        assert _active(store) == ["Toronto has light rain and northwest winds"]


class TestGroundingGoesDarkLoudly:
    """Grounding fails closed, so a model that drops the field stores nothing.

    That is indistinguishable from "nothing worth storing" unless it says so.
    """

    UNLABELLED = [
        {"op": "add", "category": "fact", "content": "alpha"},
        {"op": "add", "category": "fact", "content": "beta", "grounded": "vibes"},
    ]
    USER_TEXT = "Please remember that I prefer tabs over spaces in Python"

    def test_every_write_dropped_for_grounding_warns(self, store, caplog):
        host = _Host(store, self.UNLABELLED)

        with caplog.at_level("WARNING", logger="gaia.agents.base.memory"):
            host.process_query(self.USER_TEXT)

        assert _extracted(store) == []
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("all 2 proposed writes" in m for m in warnings), warnings

    def test_one_good_write_among_unlabelled_ones_stays_quiet(self, store, caplog):
        host = _Host(
            store, self.UNLABELLED + [_user_op("User prefers tabs", "preference")]
        )

        with caplog.at_level("WARNING", logger="gaia.agents.base.memory"):
            host.process_query(self.USER_TEXT)

        assert _extracted(store) == ["User prefers tabs"]
        assert not any(
            "extraction stored nothing" in r.getMessage() for r in caplog.records
        )


class TestToolRecordStaysBounded:
    def test_only_the_newest_calls_are_kept(self, store):
        from gaia.agents.base.memory import EXTRACTION_TOOL_RECORD_MAX_CALLS as CAP

        host = _Host(store, [])
        host.queued_calls = [
            ("read_file", {"file_path": f"f{i}.py"}, {"status": "success"})
            for i in range(CAP + 5)
        ]

        host.process_query("Please read every file in the project folder")

        assert len(host._turn_tool_record) == CAP
        assert "f5.py" in host._turn_tool_record[0]["text"]

    def test_a_huge_result_is_truncated_without_splitting_all_of_it(self, store):
        from gaia.agents.base.memory import (
            EXTRACTION_TOOL_RECORD_DETAIL_CHARS as DETAIL,
        )

        host = _Host(store, [])
        huge = "word " * 400_000  # ~2 MB
        host.queued_calls = [("read_file", {"file_path": "big.py"}, huge)]

        host.process_query("Please read big.py and summarize it for me")

        text = host._turn_tool_record[0]["text"]
        assert len(text) <= 2 * DETAIL + 1
        assert "word word" in text


def test_bookkeeping_failure_never_replaces_the_tool_result(store, monkeypatch):
    """A crash in the turn record must not turn a good call into a failure."""
    host = _Host(store, [])
    host.queued_calls = [("read_file", {"file_path": "a.py"}, {"status": "success"})]
    monkeypatch.setattr(
        _Host,
        "_record_tool_call",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bookkeeping exploded")),
    )

    host.process_query("Please read a.py and summarize it for me")  # must not raise


def test_consolidation_keeps_the_users_facts_not_the_documents(store):
    """Old sessions are distilled later; the answer's document summary in them
    is not the user either."""
    from datetime import datetime, timedelta

    turns = [
        ("user", "I'm the finance lead, summarize the quarterly report I indexed"),
        (
            "assistant",
            "Quarterly revenue grew 18%, driven by datacenter GPUs, and operating "
            "margin improved to 24% on lower inventory costs.",
        ),
    ] * 3
    for role, text in turns:
        store.store_turn("s-old", role, text)
    old = (datetime.now().astimezone() - timedelta(days=30)).isoformat()
    with store._lock:
        store._conn.execute("UPDATE conversations SET timestamp = ?", (old,))
        store._conn.commit()
    host = _Host(
        store,
        {
            "summary": "The user asked for a summary of the quarterly report.",
            "knowledge": [
                {"category": "fact", "content": "User is the finance lead"},
                {
                    "category": "fact",
                    "content": "Quarterly revenue grew 18% driven by datacenter GPUs",
                },
            ],
        },
    )

    host.consolidate_old_sessions()

    facts = [
        r[0]
        for r in store._conn.execute(
            "SELECT content FROM knowledge WHERE source = 'consolidation' "
            "AND category = 'fact'"
        )
    ]
    assert facts == ["User is the finance lead"]


DOC_CHUNKS = {
    "status": "success",
    "chunks": [
        "Q3 regional sales: APAC 8,610 units, EMEA 7,200 units.",
        "Facilities: spare keys are kept at Desk 17C on the second floor.",
        "The Orion team mascot is Zephyr-Quill, a teal heron.",
    ],
}


class TestDocumentAnswersAreNotAboutTheUser:
    """Questions answered from indexed documents stored the answers as facts
    "about you", replayed in every later chat."""

    @pytest.mark.parametrize(
        "question, answer, memory",
        [
            (
                "How many units did APAC sell in Q3?",
                "APAC sold 8,610 units in Q3.",
                "APAC sold 8,610 units in Q3.",
            ),
            (
                "Where are the spare keys kept?",
                "The spare keys are kept at Desk 17C.",
                "Spare keys are kept at Desk 17C.",
            ),
            (
                "What is the Orion team mascot called?",
                "The Orion team mascot is Zephyr-Quill, a teal heron.",
                "The Orion team mascot is named Zephyr-Quill (a teal heron).",
            ),
            # Not a question, and it names the subject: the tool check catches it.
            (
                "Tell me about the Orion team mascot from my docs",
                "Their mascot is a heron.",
                "The Orion team mascot is named Zephyr-Quill (a teal heron).",
            ),
        ],
    )
    def test_a_document_answer_is_not_stored(self, store, question, answer, memory):
        host = _Host(store, [_user_op(memory)])
        host.queued_calls = [("query_documents", {"query": question}, DOC_CHUNKS)]
        host.answer = answer

        host.process_query(question)

        assert _extracted(store) == []

    def test_a_fact_the_user_states_beside_a_document_question_is_kept(self, store):
        host = _Host(
            store,
            [
                _user_op("User manages the APAC sales region"),
                _user_op("APAC sold 8,610 units in Q3"),
            ],
        )
        host.queued_calls = [("query_documents", {"query": "APAC Q3"}, DOC_CHUNKS)]
        host.answer = "APAC sold 8,610 units in Q3."

        host.process_query(
            "I manage the APAC sales region. How many units did we sell?"
        )

        assert _extracted(store) == ["User manages the APAC sales region"]


class TestUserStatements:
    @pytest.mark.parametrize(
        "text, kept",
        [
            ("Where are the spare keys kept?", ""),
            ("What’s the Orion mascot?", ""),
            ("What should I cook tonight?", "What should I cook tonight?"),
            ("How about Friday instead?", "How about Friday instead?"),
            ("can you make it Friday?", "can you make it Friday?"),
            ("I moved to Berlin. What's the weather there?", "I moved to Berlin."),
        ],
    )
    def test_information_questions_are_left_out(self, text, kept):
        from gaia.agents.base.memory import _user_statements

        assert _user_statements(text) == kept

    def test_a_question_does_not_vouch_for_its_answer(self):
        assert _copied_from_material(
            "Spare keys are kept at Desk 17C",
            "Where are the spare keys kept?",
            "The spare keys are kept at Desk 17C.",
        )


class TestLiftedFromTools:
    @pytest.mark.parametrize(
        "content, user, tool_text, lifted",
        [
            ("APAC sold 8610 units", "APAC sales?", "APAC: 8610 units", True),
            ("Keys are at Desk 17C", "keys?", "keys at Desk 17C", True),
            ("User lives in Berlin", "I live in Berlin", "Berlin: 12C, rain", False),
            ("User prefers teal", "I like teal", "", False),
        ],
    )
    def test_cases(self, content, user, tool_text, lifted):
        from gaia.agents.base.memory import _lifted_from_tools

        assert _lifted_from_tools(content, user, tool_text) is lifted

    def test_a_personal_detail_only_a_tool_supplied_is_dropped(self):
        """Intended: memory holds what the user said, even about themselves.

        The time came from their calendar, not their words, so it is not
        remembered; the calendar stays the source of truth for it.
        """
        from gaia.agents.base.memory import _lifted_from_tools

        assert _lifted_from_tools(
            "User has a dentist appointment Friday 3pm",
            "check my calendar for the dentist",
            "Dentist Fri 3pm",
        )


def test_consolidation_drops_answers_to_document_questions(store):
    """A chat distilled weeks later must not turn its answers into facts."""
    from datetime import datetime, timedelta

    turns = [
        ("user", "Where are the spare keys kept?"),
        ("assistant", "The spare keys are kept at Desk 17C on the second floor."),
        ("user", "I'm the office manager on the second floor."),
        ("assistant", "Got it."),
    ] * 2
    for role, text in turns:
        store.store_turn("s-docs", role, text)
    old = (datetime.now().astimezone() - timedelta(days=30)).isoformat()
    with store._lock:
        store._conn.execute("UPDATE conversations SET timestamp = ?", (old,))
        store._conn.commit()
    host = _Host(
        store,
        {
            "summary": "",
            "knowledge": [
                {"category": "fact", "content": "Spare keys are kept at Desk 17C"},
                {"category": "fact", "content": "User is the office manager"},
            ],
        },
    )

    host.consolidate_old_sessions()

    facts = [
        r[0]
        for r in store._conn.execute(
            "SELECT content FROM knowledge WHERE source = 'consolidation' "
            "AND category = 'fact'"
        )
    ]
    assert facts == ["User is the office manager"]
