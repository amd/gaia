# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Explicit live extraction benchmark (no judge or mocked inference).

Run: python scripts/dev/validate_document_extraction.py MODEL OUTPUT_DIRECTORY
Creates synthetic input and an isolated memory DB. Fails unless all 40 source
occurrences survive extraction/export and a later turn retrieves a planted cue
through the existing memory tool. OUTPUT_DIRECTORY must not already exist.
"""

import argparse
import json
import os
import time
from pathlib import Path

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import PathValidator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model")
    parser.add_argument("output_directory")
    parser.add_argument(
        "--unlined",
        action="store_true",
        help="Use an unbroken transcript with an entirely empty extraction page",
    )
    args = parser.parse_args()
    model = args.model
    root = Path(args.output_directory).resolve()
    root.mkdir(parents=True, exist_ok=False)
    base = root
    label = "validation"
    os.chdir(root)
    os.environ["GAIA_PROJECT_ROOT"] = str(root)
    expected = [f"Zephyr-{i:02d}" for i in range(1, 41)]
    blocks = []
    for i, name in enumerate(expected, 1):
        filler = (
            f"At workshop segment {i}, the coach discusses room layout, hydration, and timing. This background discussion teaches no exercise.\n"
            * 15
        )
        blocks.append(
            filler
            + f"Exercise: {name}. Reps: {i+3}. Cue: keep marker violet-{i:02d} visible.\n"
        )
    source = "\n".join(blocks)
    if args.unlined:
        # Exercise a full barren page, not just gaps between nearby items.
        source = (
            "The room opens later. Please wait by the door. " * 150
        ) + source.replace("\n", " ")
    (root / "workshop.txt").write_text(source)

    class TestAgent(MemoryMixin, Agent, FileIOToolsMixin):
        def _get_system_prompt(self):
            return "Use the available tools for document work. For exhaustive extraction use extract_document_items, then save_extracted_items if asked to save. The framework renders the full inventory; do not repeat it in your final answer. Never claim success without evidence."

        def _register_tools(self):
            self.path_validator = PathValidator(allowed_paths=[str(root)])
            self.register_file_io_tools()
            self.register_memory_tools()

        def _make_extraction_chat(self):
            sdk = super()._make_extraction_chat()
            send = sdk.send_messages

            def traced(*args, **kwargs):
                response = send(*args, **kwargs)
                with (root / "map-responses.jsonl").open("a") as stream:
                    stream.write(
                        json.dumps({"response": response.text, "usage": response.usage})
                        + "\n"
                    )
                print("MAP_RESPONSE", len(response.text), flush=True)
                return response

            sdk.send_messages = traced
            return sdk

        def _create_console(self):
            return SilentConsole(auto_approve_gated_tools=True)

        def _forget_errors_for_operation(self, *args):
            pass

        def _auto_store_error(self, *args):
            pass

        def get_memory_system_prompt(self):
            return ""

        def _before_process_query(self, user_input):
            return user_input

    agent = TestAgent(model_id=model, streaming=False, silent_mode=True)
    agent._memory_store = MemoryStore(root / "memory.sqlite")
    agent._memory_session_id = "validation-" + label
    agent._memory_context = "extraction-validation"
    agent._incognito = False
    agent._auto_extract_enabled = False
    agent.register_memory_tools()
    agent.rebuild_system_prompt()
    query = "List every exercise in workshop.txt with fields: name, reps, cue. Save the complete extracted inventory to inventory.json."
    agent._original_user_input = query
    start = time.monotonic()
    result = agent.process_query(query, max_steps=10)
    text = result.get("result", "")
    items = [
        entry
        for entries, _, _ in agent._extraction_ledger.results.values()
        for entry in entries
    ]
    missing = [
        name for name in expected if not any(name in entry.text for entry in items)
    ]
    try:
        saved = (
            json.loads((root / "inventory.json").read_text())
            if (root / "inventory.json").exists()
            else []
        )
    except ValueError:
        saved = []
    if not isinstance(saved, list):
        saved = []
    saved_missing = [
        name
        for name in expected
        if not any(
            name in entry.get("text", "") for entry in saved if isinstance(entry, dict)
        )
    ]
    incorrect_fields = []
    for i, name in enumerate(expected, 1):
        for result_type, records in (
            ("extracted", [e.text for e in items]),
            ("saved", [e.get("text", "") for e in saved if isinstance(e, dict)]),
        ):
            matches = [r for r in records if f"name: {name}" in r]
            if (
                len(matches) != 1
                or f"reps: {i+3};" not in matches[0]
                or f"cue: keep marker violet-{i:02d} visible" not in matches[0]
            ):
                incorrect_fields.append(f"{result_type}:{name}")
    events = agent._memory_store.get_tool_history("extract_document_items")
    recall = agent._memory_store.search_conversations(
        "Zephyr", context="extraction-validation", limit=5
    )
    record = {
        "model": model,
        "seconds": round(time.monotonic() - start, 2),
        "source_chars": len(source),
        "source_newlines": source.count("\n"),
        "fixture": "unlined-with-empty-page" if args.unlined else "lined",
        "expected": 40,
        "extracted": len(items),
        "missing": missing,
        "saved_missing": saved_missing,
        "incorrect_fields": incorrect_fields,
        "saved_count": len(saved),
        "status": result["status"],
        "gaps": result["completion_gaps"],
        "tool_events": events,
        "memory_recalled_inventory": any("Zephyr-40" in str(r) for r in recall),
        "final_answer": text,
        "tools": agent._turn_tool_executions,
    }
    followup = agent.process_query(
        "Use search_past_conversations to find our previous workshop extraction. What was the cue for Zephyr-40? Do not read the source file.",
        # Same budget as the main extraction (line 99): a slower local model
        # can spend several steps just searching before it answers, and a
        # tighter budget here reports the harness's own limit as a product
        # failure (field report, PR #4145).
        max_steps=10,
    )
    record["memory_followup_status"] = followup["status"]
    record["memory_followup_answer"] = followup.get("result", "")
    record["memory_followup_used_search"] = any(
        t.get("tool") == "search_past_conversations"
        or t.get("tool_name") == "search_past_conversations"
        for t in agent._turn_tool_executions
    )
    record["passed"] = (
        result["status"] == "success"
        and len(items) == 40
        and not missing
        and not saved_missing
        and len(saved) == 40
        and not incorrect_fields
        and bool(events)
        and record["memory_recalled_inventory"]
        and record["memory_followup_status"] == "success"
        and "violet-40"
        in record["memory_followup_answer"]
        .replace("\u2011", "-")
        .replace("\u2010", "-")
        and record["memory_followup_used_search"]
    )
    (base / "result.json").write_text(json.dumps(record, indent=2, default=str))
    print(
        "VALIDATION",
        json.dumps(
            {
                k: v
                for k, v in record.items()
                if k not in ("final_answer", "tools", "tool_events")
            },
            indent=2,
        ),
        flush=True,
    )
    raise SystemExit(0 if record["passed"] else 1)


if __name__ == "__main__":
    main()
