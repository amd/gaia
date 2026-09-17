# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Assert the invariants a built dataset must satisfy, and fail loudly if not.

Every check here exists because the invariant was violated at least once during
development, silently, in a way that produced a plausible-looking dataset:

* two decision points shared a ``step_index``, so a record's own action appeared
  inside its own ``recent_steps``;
* ``tools_available`` was a union over the transcript's whole life, so 4.6% of
  records advertised exactly the tools the record was asking a harness to pick;
* ``arg_hash`` was computed before scrubbing, so 58% of records carried a hash
  that did not describe their own contents;
* a subagent's ``parent_session_id`` held its own file stem rather than its
  parent's id, breaking the replay pointer for a third of the corpus;
* ``episode_id`` was keyed on the session, so one id spanned a parent and its 19
  subagents — grouping by episode merged 20 independent runs into one.

None of those crashes anything. That is exactly why they are asserted rather than
left to a reviewer's eye.
"""

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Tuple

from gaia.factory.harvest.reader import _hash_args


def load_records(dataset: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for side in ("oracle", "pool"):
        path = dataset / side / "records.jsonl"
        if not path.is_file():
            raise FileNotFoundError(
                f"No records at {path}. Build the dataset first with "
                "`python -m gaia.factory.dataset.build`."
            )
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records


def _violations(
    records: List[Dict[str, Any]], dataset: Path
) -> Iterator[Tuple[str, str]]:
    """Yield ``(invariant, detail)`` for every breach found."""
    seen_ids = collections.Counter(r["record_id"] for r in records)
    for rid, n in seen_ids.items():
        if n > 1:
            yield "unique_record_id", f"{rid} appears {n} times"

    # A missing required field is reported, never raised: an audit that crashes
    # on a malformed record tells you less than one that names what is missing.
    for rec in records:
        absent = [f for f in REQUIRED_FIELDS if f not in rec]
        if absent:
            yield "required_fields_present", f"{rec.get('record_id', '?')} missing {absent}"

    episodes: Dict[str, set] = collections.defaultdict(set)
    for rec in records:
        if "episode_id" in rec and "transcript_id" in rec:
            episodes[rec["episode_id"]].add(rec["transcript_id"])
    for episode, transcripts in episodes.items():
        if len(transcripts) > 1:
            yield (
                "episode_id_is_per_transcript",
                f"{episode} spans {len(transcripts)} transcripts",
            )

    steps: Dict[Tuple[str, str], collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    for rec in records:
        steps[(rec["session_id"], rec["transcript_id"])][rec["step_index"]] += 1
    for key, counter in steps.items():
        for step, n in counter.items():
            if n > 1:
                yield "unique_step_index", f"{key[1][:16]} step {step} appears {n} times"

    for rec in records:
        rid = rec["record_id"]

        for prior in rec["state"]["recent_steps"]:
            if prior["step_index"] >= rec["step_index"]:
                yield "no_lookahead_in_state", f"{rid} sees step {prior['step_index']}"
                break

        used = {c["tool"] for c in rec["action"]["calls"]}
        available = set(rec["tools_available"])
        if used and used == available:
            yield "tools_available_does_not_leak", f"{rid} advertises exactly {sorted(used)}"
        missing = used - available
        if missing:
            yield "tools_available_is_a_superset", f"{rid} missing {sorted(missing)}"

        for c in rec["action"]["calls"]:
            if _hash_args(c["arguments"]) != c["arg_hash"]:
                yield "arg_hash_matches_shipped_args", f"{rid} tool {c['tool']}"
                break

        body = {k: v for k, v in rec.items() if k != "record_sha256"}
        digest = hashlib.sha256(
            json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        if digest != rec.get("record_sha256"):
            yield "record_sha256_verifies", rid

        if rec["scope"] == "subagent":
            if rec["parent_session_id"] != rec["session_id"]:
                yield "subagent_parent_is_partition_key", rid
        elif rec["parent_session_id"] is not None:
            yield "main_record_has_no_parent", rid

        if "reference_checks" not in rec:
            yield "reference_checks_present", rid

        # Self-containment: everything a harness is shown must be usable without
        # the private corpus. state.transcript_ref is provenance, not a
        # dependency — a consumer who cannot read the transcripts must still be
        # able to prompt and grade every record.
        if not str(rec.get("goal", "")).strip():
            yield "harness_prompt_is_complete", f"{rid} empty goal"
        elif not str(rec.get("episode_instruction", "")).strip():
            yield "harness_prompt_is_complete", f"{rid} empty episode_instruction"
        elif not rec.get("tools_available"):
            yield "harness_prompt_is_complete", f"{rid} no tools_available"
        elif rec["state"].get("recent_steps") is None:
            yield "harness_prompt_is_complete", f"{rid} no recent_steps"

        # An outcome label inside `state` would hand a harness the answer to
        # "did this work?" before it acts.
        leaked = [k for k in rec["state"] if "outcome" in k or "success" in k]
        if leaked:
            yield "outcome_not_visible_in_state", f"{rid} state has {leaked}"

        if rec["outcome"]["reference_quality"] == "errored":
            if rec["grading_polarity"] != "avoid_reference":
                yield "failed_reference_inverts_polarity", rid
        elif rec["grading_polarity"] != "match_reference":
            yield "succeeded_reference_matches_polarity", rid

        for obs in rec["observation"]:
            for key in ("blob_ref", "file_content_ref", "original_file_ref"):
                ref = obs.get(key)
                if ref and not (dataset / "blobs" / f"{ref}.txt").is_file():
                    yield "blob_refs_resolve", f"{rid} {key}={ref[:12]}"
        for path, entry in rec["state"]["files_in_context"].items():
            if not (dataset / "blobs" / f"{entry['blob']}.txt").is_file():
                yield "blob_refs_resolve", f"{rid} files_in_context {path[:40]}"


#: Fields no record may omit. Everything downstream assumes they exist.
REQUIRED_FIELDS = (
    "record_id",
    "schema_version",
    "session_id",
    "transcript_id",
    "episode_id",
    "message_id",
    "scope",
    "step_index",
    "depth_index",
    "partition",
    "use_case",
    "capability_axes",
    "difficulty",
    "tools_available",
    "grading_polarity",
    "reasoning",
    "state",
    "action",
    "observation",
    "outcome",
    "reference_checks",
    "episode_turn_class",
    "episode_outcome",
    "record_sha256",
)

INVARIANTS = (
    "required_fields_present",
    "unique_record_id",
    "unique_step_index",
    "episode_id_is_per_transcript",
    "no_lookahead_in_state",
    "tools_available_does_not_leak",
    "tools_available_is_a_superset",
    "arg_hash_matches_shipped_args",
    "record_sha256_verifies",
    "subagent_parent_is_partition_key",
    "main_record_has_no_parent",
    "reference_checks_present",
    "harness_prompt_is_complete",
    "outcome_not_visible_in_state",
    "failed_reference_inverts_polarity",
    "succeeded_reference_matches_polarity",
    "blob_refs_resolve",
)


def audit(dataset: Path) -> Dict[str, Any]:
    """Run every invariant. Returns a report; does not raise."""
    records = load_records(dataset)
    counts: collections.Counter = collections.Counter()
    examples: Dict[str, List[str]] = collections.defaultdict(list)
    for name, detail in _violations(records, dataset):
        counts[name] += 1
        if len(examples[name]) < 5:
            examples[name].append(detail)
    return {
        "dataset": str(dataset),
        "records": len(records),
        "clean": not counts,
        "invariants_checked": list(INVARIANTS),
        "violations": dict(counts),
        "examples": {k: v for k, v in examples.items()},
    }


def assert_clean(dataset: Path, log: Callable[[str], None] = print) -> None:
    """Raise unless every invariant holds.

    Called at the end of a build: a dataset that violates these is worse than no
    dataset, because it scores a harness against corrupted state and reports a
    number anyway.
    """
    report = audit(dataset)
    if report["clean"]:
        log(
            f"      audit clean — {len(INVARIANTS)} invariants over {report['records']} records"
        )
        return
    raise SystemExit(
        "Dataset integrity audit FAILED:\n"
        + json.dumps(
            {"violations": report["violations"], "examples": report["examples"]},
            indent=2,
        )
        + "\n\nFix the builder and rebuild. These breaches do not crash anything — "
        "they produce a dataset that silently scores a harness against corrupted state."
    )


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path.home() / ".gaia" / "cache" / "factory" / "dataset",
    )
    args = parser.parse_args(argv)
    report = audit(args.dataset)
    print(json.dumps(report, indent=2))
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
