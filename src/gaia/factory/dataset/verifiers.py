# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Deterministic checks a candidate harness's proposed action is graded against.

Shipped as code rather than described in prose, so a consumer grades with exactly
the logic that calibrated the dataset.

**Why every record carries the reference's own results.** A verifier is only
informative where the *reference* action passes it. Measured on the first build,
the reference failed its own argument checks 40-47% of the time — partly from
real bugs (``Glob`` patterns were being compiled as regexes; the binary
vocabulary was truncated at 60), partly from things no amount of engineering
fixes: the first use of a binary cannot have been observed earlier, and a file
edited since it was read no longer contains the ``old_string`` the transcript
holds.

So :func:`run_static_checks` is run over the reference at build time and stored
on the record. Where the reference fails, the check says nothing about a
candidate and the consumer drops it — rather than the dataset shipping a grader
that is wrong half the time and calling it a score.
"""

import fnmatch
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence


def normalize_path(value: str) -> str:
    r"""Canonical form for path comparison.

    The corpus is Windows and mixes separators freely; scrubbing then replaces a
    ``C:\Users\name\`` prefix with ``<WORKSPACE>/``, so one file can appear as
    both ``<WORKSPACE>/Work/x/a.md`` and ``<WORKSPACE>/Work\x\a.md``. Compared
    raw, a path the agent had definitely seen looked unseen.
    """
    return value.replace("\\", "/").rstrip("/").lower()


def _ancestors(path: str):
    parts = path.split("/")
    for i in range(len(parts) - 1, 0, -1):
        yield "/".join(parts[:i])


#: Glob metacharacters that are meaningless to a regex engine and vice versa.
_GLOB_TOOLS = frozenset({"Glob"})
_REGEX_TOOLS = frozenset({"Grep"})


@dataclass
class CheckResult:
    """One check's verdict.

    ``applicable`` is False when the check has nothing to look at — a ``Bash``
    call has no ``old_string``. That is different from failing, and merging the
    two would let a harness raise its score by proposing actions no check can
    reach.
    """

    name: str
    applicable: bool
    passed: Optional[bool] = None
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _path_args(arguments: Dict[str, Any]) -> List[str]:
    out = []
    for key in ("file_path", "path", "notebook_path"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            out.append(value)
    return out


def check_paths_known(call: Dict[str, Any], record: Dict[str, Any]) -> CheckResult:
    """Does every path the action names appear in the agent's known vocabulary?"""
    paths = _path_args(call.get("arguments", {}))
    if not paths:
        return CheckResult("paths_known", applicable=False)
    known = {
        normalize_path(p)
        for p in list(record["state"]["known_paths"])
        + list(record["state"]["files_in_context"])
    }
    unknown = []
    for raw in paths:
        norm = normalize_path(raw)
        # A file inside a directory the agent has already seen is plausible, not
        # invented — listing a directory is how the agent learns its contents.
        if norm in known or any(a in known for a in _ancestors(norm)):
            continue
        unknown.append(raw)
    return CheckResult(
        "paths_known",
        applicable=True,
        passed=not unknown,
        detail="" if not unknown else f"not previously seen: {unknown[:3]}",
    )


def check_binaries_known(call: Dict[str, Any], record: Dict[str, Any]) -> CheckResult:
    """Are the shell binaries ones that exist in this environment?

    Checked against the session's own vocabulary *plus* the binaries evidenced
    across the corpus. Session-only was wrong: it flagged the first ``grep`` of a
    run as an unknown program, when grep runs 12,764 times corpus-wide and
    plainly exists. What remains is the signal worth having — a harness naming a
    program this machine does not have.
    """
    if call.get("tool") != "Bash":
        return CheckResult("binaries_known", applicable=False)
    segments = [
        s for s in call.get("shell_segments", []) if s.get("kind") == "substantive"
    ]
    if not segments:
        return CheckResult("binaries_known", applicable=False)
    known = set(record["state"]["observed_binaries"])
    known |= set(record["state"].get("environment_binaries", []))
    unknown = sorted({s["leader"] for s in segments if s["leader"] not in known})
    return CheckResult(
        "binaries_known",
        applicable=True,
        passed=not unknown,
        detail="" if not unknown else f"first use: {unknown[:3]}",
    )


def check_pattern_valid(call: Dict[str, Any], record: Dict[str, Any]) -> CheckResult:
    """Is the search pattern well-formed *in its own dialect*?

    ``Glob`` takes a glob and ``Grep`` takes a regex. Compiling a glob with
    ``re`` rejects every ordinary ``**/*.go``, which is what made this check
    look like an 84% failure rather than a bug in the checker.
    """
    tool = call.get("tool")
    pattern = call.get("arguments", {}).get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return CheckResult("pattern_valid", applicable=False)
    if tool in _GLOB_TOOLS:
        try:
            fnmatch.translate(pattern)
        except (re.error, TypeError) as exc:
            return CheckResult("pattern_valid", True, False, f"bad glob: {exc}")
        return CheckResult("pattern_valid", True, True, "glob")
    if tool in _REGEX_TOOLS:
        try:
            re.compile(pattern)
        except re.error as exc:
            # Grep is ripgrep (Rust regex); Python `re` is an approximation, so
            # a rejection here is reported as a dialect note, not a hard fail.
            return CheckResult(
                "pattern_valid",
                True,
                False,
                f"not valid Python re ({exc}); rg may accept",
            )
        return CheckResult("pattern_valid", True, True, "regex")
    return CheckResult("pattern_valid", applicable=False)


def check_old_string_present(
    call: Dict[str, Any],
    record: Dict[str, Any],
    read_blob: Optional[Callable[[str], str]] = None,
) -> CheckResult:
    """Does an edit's ``old_string`` occur in the file content the record holds?

    Fails legitimately when the file was edited after it was read, or read with
    an offset so only a slice is held.
    """
    if call.get("tool") not in ("Edit", "MultiEdit"):
        return CheckResult("old_string_present", applicable=False)
    arguments = call.get("arguments", {})
    old = arguments.get("old_string")
    paths = _path_args(arguments)
    if not isinstance(old, str) or not old or not paths or read_blob is None:
        return CheckResult("old_string_present", applicable=False)
    entry = record["state"]["files_in_context"].get(paths[0])
    if not entry:
        return CheckResult(
            "old_string_present", applicable=False, detail="file content not held"
        )
    content = read_blob(entry["blob"])
    if content is None:
        return CheckResult(
            "old_string_present", applicable=False, detail="blob unreadable"
        )
    present = old in content
    return CheckResult(
        "old_string_present",
        applicable=True,
        passed=present,
        detail="" if present else "held content is stale or partial",
    )


STATIC_CHECKS = ("paths_known", "binaries_known", "pattern_valid", "old_string_present")


def run_static_checks(
    calls: Sequence[Dict[str, Any]],
    record: Dict[str, Any],
    read_blob: Optional[Callable[[str], str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Run every static check over an action's calls, worst-case per check.

    A decision point dispatching several tools passes a check only if every
    applicable call passes it — one bad path in a 3-wide dispatch is a bad
    action.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for name in STATIC_CHECKS:
        results = []
        for call in calls:
            if name == "paths_known":
                results.append(check_paths_known(call, record))
            elif name == "binaries_known":
                results.append(check_binaries_known(call, record))
            elif name == "pattern_valid":
                results.append(check_pattern_valid(call, record))
            else:
                results.append(check_old_string_present(call, record, read_blob))
        applicable = [r for r in results if r.applicable]
        if not applicable:
            out[name] = CheckResult(name, applicable=False).to_dict()
        else:
            failed = [r for r in applicable if r.passed is False]
            out[name] = CheckResult(
                name,
                applicable=True,
                passed=not failed,
                detail=failed[0].detail if failed else "",
            ).to_dict()
    return out


def grade(
    record: Dict[str, Any],
    proposed_calls: Sequence[Dict[str, Any]],
    read_blob: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    """Score one candidate action against one record.

    Two things this deliberately does **not** do: produce a single number, and
    call agreement "accuracy". The reference is what one strong harness did, not
    what was optimal, and on ``avoid_reference`` records reproducing it is the
    wrong answer.
    """
    reference_tools = [c["tool"] for c in record["action"]["calls"]]
    proposed_tools = [c.get("tool") for c in proposed_calls]
    exact = sorted(proposed_tools) == sorted(reference_tools)
    families = {c["family"] for c in record["action"]["calls"]}
    same_family = bool(proposed_tools) and all(
        _family_of(t) in families for t in proposed_tools
    )

    reference_hashes = {c["arg_hash"] for c in record["action"]["calls"]}
    checks = run_static_checks(proposed_calls, record, read_blob)
    informative = {
        name: record.get("reference_checks", {}).get(name, {}).get("passed") is True
        for name in STATIC_CHECKS
    }

    polarity = record["grading_polarity"]
    if polarity == "avoid_reference":
        from gaia.factory.harvest.reader import _hash_args

        repeated = any(
            _hash_args(c.get("arguments", {})) in reference_hashes
            for c in proposed_calls
        )
        credited = (not repeated) and all(
            checks[n]["passed"] is not False for n in STATIC_CHECKS
        )
        verdict = {"repeated_failing_action": repeated, "credited": credited}
    else:
        verdict = {
            "tool_exact": exact,
            "tool_same_family": same_family,
            "credited": exact,
        }

    return {
        "record_id": record["record_id"],
        "polarity": polarity,
        "width_reference": record["action"]["width"],
        "width_proposed": len(proposed_calls),
        "checks": checks,
        "checks_informative": informative,
        **verdict,
    }


def _family_of(tool: Optional[str]) -> str:
    from gaia.factory.harvest.reader import tool_family

    return tool_family(tool or "unknown")
