# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Per-turn file provenance and delivered readback evidence.

This records tool facts, not filesystem guesses or model self-attestation.
Reading every byte establishes observation, not semantic extraction recall.
"""

from __future__ import annotations

import ast
import json
import ntpath
import os
import re
import stat
import time
from dataclasses import dataclass, field
from typing import Any, Callable

WRITE_TOOLS = frozenset(
    {
        "write_file",
        "save_extracted_items",
        "write_markdown_file",
        "write_python_file",
        "edit_file",
        "edit_python_file",
        "replace_function",
    }
)
READ_TOOLS = frozenset({"read_file", "read_python_file", "read_markdown_file"})
SIDE_EFFECT_PATHS = {
    "transcribe_media": ("transcript_path",),
    "refine_transcript": ("refined_path", "output_path"),
    "take_screenshot": ("file_path", "output_path", "path"),
    "text_to_speech": ("file_path", "output_path", "audio_path"),
    "generate_image": ("file_path", "output_path", "image_path"),
}
_EXEC_TOOLS = frozenset({"run_python", "execute_python_file", "run_shell_command"})
_UNOBSERVABLE = object()
_BINARY_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".ico",
        ".tiff",
        ".wav",
        ".mp3",
        ".ogg",
        ".flac",
        ".m4a",
        ".mp4",
        ".mov",
        ".webm",
        ".pdf",
        ".docx",
        ".xlsx",
        ".pptx",
        ".zip",
        ".gz",
        ".tar",
        ".bin",
    }
)
_CODE_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".go",
        ".rs",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".java",
        ".cs",
        ".rb",
        ".sh",
        ".ps1",
        ".swift",
        ".kt",
    }
)
_FENCES = re.compile(r"```.*?```", re.DOTALL)
# The verb must be an instruction, never a noun ("the store") or a question topic.
_SAVE_REQUEST = re.compile(
    r"(?:^|\band\b|\bthen\b|[,;:]|\b(?:can|could|would|will)\s+you\b|\byou\s+to\b|"
    r"\bplease\b)\s*(?:(?:please|also|now|just|then|and|kindly|ok(?:ay)?|so),?\s+)*"
    r"\b(save|write|export|store|put|copy|extract|create|make|generate)\b",
    re.I,
)
# These verbs store by themselves; the others name a target only via "to/into".
_STORAGE_VERBS = frozenset({"save", "export", "store"})
_CREATE_VERBS = frozenset({"create", "make", "generate"})
_OUTPUT_PREPOSITION = re.compile(r"\b(?:to|into)\s+", re.I)
_PUT_PREPOSITION = re.compile(r"\b(?:in|into)\s+", re.I)
_NAMED_FILE = re.compile(
    r"^\s*(?:(?:a|an|the|new)\s+)?(?:[\w-]+\s+)?file\s+(?:called\s+|named\s+)?",
    re.I,
)
# An explicit file or disk object, before any relative clause describing code.
_FILE_OBJECT = re.compile(
    r"^\s*(?:(?:a|an|the|new|this|that|it|them)\s+)*(?:[\w-]+\s+)?(?:file|disk)\b|"
    r"\b(?:to|into|onto|on|in)\s+(?:(?:a|an|the|new|this|that|my|your)\s+)*"
    r"(?:[\w-]+\s+)?(?:file|disk|folder|directory)\b",
    re.I,
)
_RELATIVE_CLAUSE = re.compile(r"\b(?:that|which|who|so that)\b", re.I)
_CONDITION = re.compile(r"^\s*(?:once|when|after|before|if|whenever)\b[^,]*,", re.I)
# "a function that saves to x" describes code, not something done this turn.
_CODE_BEHAVIOUR = re.compile(
    r"\b(?:that|which)\s+(?:\w+\s+)?(?:saves|writes|stores|exports|outputs|creates)\b.*$",
    re.I,
)
_FROM_COLLECTION = re.compile(
    r"\bfrom\s+(?:the|your|my|this|our)\s+(?:[\w-]+\s+)?(?:index|knowledge base|"
    r"list|summary|context|plan|library|queue|results?|inputs?|documents?)\b",
    re.I,
)
# "I've gone ahead and saved x", not "I think downloads are saved to x": no
# be-verb or clause break may sit between the speaker and the save verb.
_FIRST_PERSON = re.compile(
    r"\b(?:I|we)(?:'ve|'d)?(?:\s+(?!(?:am|is|are|was|were|be|been|being)\b)"
    r"[\w-]+(?<!'s)){0,4}?\s+(?:saved|wrote|written|stored|exported|created|"
    r"put|generated|made|copied)\b",
    re.I,
)
_EARLIER = re.compile(
    r"\b(?:previous|last|earlier|prior)\s+(?:session|conversation|turn|time|chat)\b|"
    r"\b(?:earlier|yesterday)\b",
    re.I,
)
# "Write a guide to X" is a topic; writing *the summary* to X is a save.
_TOPIC_OBJECT = re.compile(
    r"\b(?:guide|intro(?:duction)?|tutorial|primer|letter|e-?mail|message|reply|"
    r"response|answer|ode|poem|apology|note|essay|story|song)s?\s*$",
    re.I,
)
# A bare file name followed by a plain word is modifying it, not naming a file.
_MODIFIER = re.compile(
    r"\s+(?!(?:and|or|then|to|into|in|on|at|as|with|for|from|now|please|too|so|"
    r"because|if|when|but|instead|not|which|that|using|via|during|about|by|"
    r"where|while|here|below|above|file|document)\b)[a-z][a-z-]*\b"
)
_NEGATED_TARGET = re.compile(r"\b(?:not|instead of|rather than)\s*$", re.I)
_NOT_REQUEST = re.compile(
    r"\b(?:do not|don't|never|without|how (?:do|can|would)|explain how|"
    r"show me how|if|could you explain)\b",
    re.I,
)
# Quoting allows spaces; bare paths are scanned as tokens, never suffix matches.
_TARGET = re.compile(r"`([^`\n]+)`|\"([^\"\n]+)\"|'([^'\n]+)'|([^\s`\"'<>]+)")
_DESTINATION = re.compile(r"\b(?:to|into|in|at|as)\s+", re.I)
# Words allowed between a destination preposition and its path.
_LEAD = re.compile(
    r"^\s*(?:(?:a|an|the|this|that|your|my|new|file|folder|directory|called|named)"
    r"\s+)*",
    re.I,
)


def _payload(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _path_token(match: re.Match) -> str | None:
    value = next(g for g in match.groups() if g is not None).strip()
    value = value.lstrip("([").rstrip(".,;:)]?!")
    if not value or "://" in value or "@" in value or "\x00" in value:
        return None
    quoted = any(match.group(i) is not None for i in (1, 2, 3))
    suffix = value.rsplit(".", 1)[-1].lower()
    # Dotted initials (U.S, e.g) are abbreviations, not file names.
    abbreviation = all(len(part) == 1 for part in value.split("."))
    if (
        quoted
        or "/" in value
        or "\\" in value
        or (
            "." in value
            and suffix.isalpha()
            and not abbreviation
            and suffix not in {"com", "org", "net", "io", "ai", "dev", "app"}
        )
    ):
        return value
    return None


def _scan_paths(text: str, immediate: bool, modifiers: bool = False) -> list[str]:
    """Paths listed in *text*; with *immediate*, only when they open it.

    A bare name followed by a plain word modifies that word ("a Node.js app");
    that is checked unless a destination preposition already anchored *text*.
    """
    if immediate:
        text = text[_LEAD.match(text).end() :]
    paths = []
    for match in _TARGET.finditer(text):
        path = _path_token(match)
        if (
            path is not None
            and (modifiers or not immediate)
            and match.group(4)
            and _MODIFIER.match(text[match.end() :])
        ):
            path = None  # "a Three.js scene": the name describes another noun
        if path is not None:
            paths.append(path)
        elif immediate and not paths:
            return []
        elif paths and match.group().lower().strip(",") not in {"and", "or"}:
            break
    return paths


def destination_paths(text: str, prepositions: re.Pattern = _DESTINATION) -> list[str]:
    """Read a destination noun phrase, stopping at the next action."""
    # A later 'email it to me' must not replace the save's destination.
    clause = re.split(
        r"\s+(?:and|then)\s+(?=(?:compare|read|email|send|check|show|summarize|"
        r"use|return|tell|report)\b)|\s[—–-]\s|;",
        text,
        maxsplit=1,
        flags=re.I,
    )[0]
    destinations = [
        match
        for match in prepositions.finditer(clause)
        if not _NEGATED_TARGET.search(clause[: match.start()])
    ]
    if not destinations:
        if prepositions is not _DESTINATION:
            return []
        return list(dict.fromkeys(_scan_paths(clause, immediate=False)))
    # The last preposition that names a path: "to clean logs/app.log" names none.
    for destination in reversed(destinations):
        paths = _scan_paths(clause[destination.end() :], immediate=True)
        if paths:
            return list(dict.fromkeys(paths))
    return []


def _instructions(query: str):
    """(verb, text after it) for each non-negated storage instruction."""
    for sentence in re.split(r"(?<=[.!?])\s+|\n", _FENCES.sub("", query)):
        sentence = sentence.strip()
        for action in _SAVE_REQUEST.finditer(sentence):
            if not _NOT_REQUEST.search(sentence[: action.end()]):
                yield action.group(1).lower(), sentence[action.end() :]


def save_obligations(query: str) -> tuple[list[str], bool]:
    """Explicit save instructions; advisory/negated instructions aren't tasks.

    A request counts only when it names a file, folder or disk: "save me some
    time" and "store this in memory" are not saves, and a pathless "save it" is
    checked through the answer's own save claim instead.
    """
    paths = []
    requested = False
    for verb, tail in _instructions(query):
        if verb in _STORAGE_VERBS:
            found = destination_paths(tail)
        elif verb == "write" and _NAMED_FILE.match(tail):
            found = _scan_paths(tail[_NAMED_FILE.match(tail).end() :], immediate=True)
        elif verb in _CREATE_VERBS:
            # "Create notes.md", "make a file called x.md", "generate it in x.md".
            named = _NAMED_FILE.match(tail)
            found = _scan_paths(tail[named.end() :] if named else tail, True, True)
        else:
            found = destination_paths(
                tail, _PUT_PREPOSITION if verb == "put" else _OUTPUT_PREPOSITION
            )
            target = _OUTPUT_PREPOSITION.search(tail)
            if (
                verb == "write"
                and target
                and _TOPIC_OBJECT.search(tail[: target.start()])
            ):
                found = []
        file_object = verb in _STORAGE_VERBS | {"write"} and _FILE_OBJECT.search(
            _RELATIVE_CLAUSE.split(tail)[0]
        )
        if found or file_object:
            requested = True
            paths.extend(found)
    return list(dict.fromkeys(paths)), requested


def save_instructed(query: str) -> bool:
    """Whether the user asked for any save, even without naming where."""
    return any(verb in _STORAGE_VERBS for verb, _ in _instructions(query)) or bool(
        save_obligations(query)[1]
    )


@dataclass
class FileEvidence:
    path: str
    written: int = 0
    direct: bool = False
    observed: bool = False
    ranges: list[tuple[int, int]] = field(default_factory=list)
    end: int | None = None
    # Found by modification time, not reported by a tool: it proves only its path.
    inferred: bool = False

    def page(self, start: int, end: int, total: int | None) -> None:
        self.ranges.append((start, end))
        if total is not None:
            self.end = total
        covered = 0
        for left, right in sorted(self.ranges):
            if left > covered:
                break
            covered = max(covered, right)
        self.observed = self.end is not None and covered >= self.end


def _normalize_key(path: str, base: str) -> str:
    """One identity per file for Windows and POSIX paths, shared by every ledger."""
    if ntpath.isabs(path) and ("\\" in path or ntpath.splitdrive(path)[0]):
        return ntpath.normcase(ntpath.normpath(path))
    if ntpath.splitdrive(base)[0]:
        return ntpath.normcase(ntpath.normpath(ntpath.join(base, path)))
    # Write tools report resolved paths; macOS /tmp and /var are symlinks.
    return os.path.normcase(
        os.path.realpath(os.path.join(base, os.path.expanduser(path)))
    )


class CompletionEvidence:
    """Evidence for this turn, with no reads outside the tool permission boundary."""

    def __init__(self, query: str, root: str | None):
        self.root = root or os.getcwd()
        self.files: dict[str, FileEvidence] = {}
        self.archives: dict[
            str, tuple[str, int, FileEvidence, int, int, int | None]
        ] = {}
        self.sequence = 0
        self.removed: set[str] = set()
        self.uninspectable: dict[str, str] = {}
        self.requested, self.save_requested = save_obligations(query)
        self.instructed = save_instructed(query)
        self.disk_tool_ran = False
        self.exec_windows: list[tuple[int, int, bool]] = []
        self._exec_started = 0

    def key(self, path: str, root: str | None = None) -> str:
        return _normalize_key(
            path, root if isinstance(root, str) and root else self.root
        )

    def _stamp(self, path: str) -> tuple | None | object:
        try:
            value = os.stat(path)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except (OSError, ValueError) as error:
            self.uninspectable[path] = str(error)
            return _UNOBSERVABLE
        if not stat.S_ISREG(value.st_mode):
            return None
        return (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    def snapshot(self, tool: str, args: dict, validator=None) -> dict:
        """Metadata only, for concrete executor targets within the read boundary."""
        if tool not in _EXEC_TOOLS:
            return {}
        self._exec_started = time.time_ns()
        paths = set(self.files) | {self.key(p) for p in self.requested}
        code = args.get("code")
        if isinstance(code, str):
            try:
                tree = ast.parse(code)
            except SyntaxError:
                tree = None
            if tree is not None:
                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        value = node.value
                        if (
                            "\n" not in value
                            and len(value) < 4096
                            and (
                                "/" in value
                                or "\\" in value
                                or os.path.splitext(value)[1] in _CODE_SUFFIXES
                                or re.search(
                                    r"\.(?:csv|tsv|json|txt|md|yaml|yml)$", value
                                )
                            )
                        ):
                            try:
                                paths.add(self.key(value))
                            except ValueError:  # embedded NUL: never a path
                                continue
        snapshots = {}
        for path in paths:
            if validator is not None:
                allowed, _ = validator.validate_read(path, prompt_user=False)
            else:
                try:
                    root = os.path.realpath(self.root)
                    allowed = os.path.commonpath((root, os.path.realpath(path))) == root
                except (ValueError, OSError):
                    allowed = False
            if allowed:
                snapshots[path] = self._stamp(path)
        return snapshots

    def record(
        self,
        tool: str,
        args: dict,
        result: Any,
        successful: bool,
        before=None,
        executed: bool = True,
    ) -> None:
        self.sequence += 1
        if not executed:
            return
        if tool in WRITE_TOOLS or tool in SIDE_EFFECT_PATHS or tool in _EXEC_TOOLS:
            self.disk_tool_ran = True
        if tool in _EXEC_TOOLS:
            self.exec_windows.append((self._exec_started, time.time_ns(), successful))
        for path, old in (before or {}).items():
            current = self._stamp(path)
            if current is _UNOBSERVABLE or old is _UNOBSERVABLE:
                item = self.files.get(path)
                if item is not None:
                    self.files[path] = FileEvidence(
                        path,
                        self.sequence if successful and item.written else 0,
                        item.direct,
                    )
                continue
            if current != old:
                # Even a failed subprocess may have changed bytes before failing.
                self.files.pop(path, None)
                self.removed.discard(path)
                if current is None and successful:
                    self.removed.add(path)
                if current is not None:
                    self.files[path] = FileEvidence(
                        path, self.sequence if successful else 0, True
                    )
        data = _payload(result)
        paths = []
        if tool in WRITE_TOOLS:
            path = (
                data.get("file_path")
                or data.get("path")
                or args.get("file_path")
                or args.get("path")
            )
            if isinstance(path, str):
                paths.append(path)
        elif tool in SIDE_EFFECT_PATHS and not data.get("reused"):
            paths.extend(
                data[k] for k in SIDE_EFFECT_PATHS[tool] if isinstance(data.get(k), str)
            )
        elif tool in _EXEC_TOOLS:
            # An executor can report concrete outputs; running arbitrary code alone
            # is not evidence that any particular file was written.
            paths.extend(
                data[k]
                for k in ("file_path", "output_path")
                if isinstance(data.get(k), str)
            )
        for path in paths:
            if "\x00" in path:
                continue
            key = self.key(path, args.get("project_dir"))
            self.removed.discard(key)
            self.files[key] = FileEvidence(
                key,
                self.sequence if successful else 0,
                tool in WRITE_TOOLS or tool in _EXEC_TOOLS,
            )

    def _written_by_executor(self, key: str) -> FileEvidence | None:
        """A file last modified during a successful shell or Python run."""
        stamp = self._stamp(key)
        if not isinstance(stamp, tuple):
            return None
        during = [
            ok for start, end, ok in self.exec_windows if start <= stamp[3] <= end
        ]
        if not during or not all(during):
            return None
        self.files[key] = FileEvidence(key, self.sequence, True, inferred=True)
        return self.files[key]

    def delivered(self, tool: str, args: dict, original: Any, delivered: Any) -> None:
        data = _payload(delivered)
        if tool == "read_tool_output":
            archive = self.archives.get(args.get("artifact"))
            if not archive or not isinstance(data.get("content"), str):
                return
            key, version, pages, source_start, source_end, source_total = archive
            item = self.files.get(key)
            if item is None or item.written != version:
                return
            start = data.get("offset", 0)
            pages.page(start, start + len(data["content"]), data.get("total_chars"))
            if pages.observed:
                item.page(source_start, source_end, source_total)
            return
        if tool not in READ_TOOLS:
            return
        raw = _payload(original)
        path = raw.get("file_path") or args.get("file_path") or args.get("path")
        if not isinstance(path, str) or "\x00" in path:
            return
        key = self.key(path, args.get("project_dir"))
        item = self.files.get(key) or self._written_by_executor(key)
        if item is None or not item.written or raw.get("is_binary"):
            return
        content = raw.get("content")
        if not isinstance(content, str):
            return
        start = raw.get("offset", 0)
        end = start + len(content)
        total = None if raw.get("next_offset") is not None else end
        if data.get("artifact") and data.get("continuation"):
            self.archives[data["artifact"]] = (
                key,
                item.written,
                FileEvidence(key),
                start,
                end,
                total,
            )
            return
        if data.get("truncated") or data.get("content") != content:
            return
        item.page(start, end, total)

    def gaps(self, answer: str, claims_file_write: Callable[[str], bool]) -> list[str]:
        required = {self.key(path) for path in self.requested}
        claim_without_path = False
        # With no save asked for and nothing written, "files are saved to
        # ~/Downloads" is information, not a report of this turn's work.
        checkable = self.instructed or self.disk_tool_ran
        for sentence in re.split(r"(?<=[.!?])\s+|\n", _FENCES.sub("", answer)):
            if _EARLIER.search(sentence):
                continue
            sentence = _CODE_BEHAVIOUR.sub("", _CONDITION.sub("", sentence))
            if not checkable and not _FIRST_PERSON.search(sentence):
                continue
            if claims_file_write(sentence):
                paths = destination_paths(sentence)
                required.update(self.key(path) for path in paths)
                claim_without_path |= not paths
        gaps = self.cleanup_gaps(answer)

        def inside(path: str, folder: str) -> bool:
            return any(path.startswith(folder.rstrip("/\\") + sep) for sep in "/\\")

        def requested(path: str) -> bool:
            # A named folder is fulfilled by the files written inside it.
            return path in required or any(inside(path, f) for f in required)

        for path in sorted(required):
            written_inside = any(
                item.written and inside(key, path) for key, item in self.files.items()
            )
            if not written_inside and (
                path not in self.files or not self.files[path].written
            ):
                reason = self.uninspectable.get(path)
                gaps.append(
                    f"Could not inspect `{path}`: {reason}"
                    if reason
                    else f"No successful write to `{path}` is recorded for this turn."
                )
        if (
            not required
            and (self.save_requested or claim_without_path)
            and not any(
                item.direct and item.written and not item.inferred
                for item in self.files.values()
            )
        ):
            gaps.append(
                "The requested output file has no recorded successful write; an earlier side-effect file does not fulfill that save."
            )
        pathless_save = not required and (self.save_requested or claim_without_path)
        for item in self.files.values():
            # Requested or claimed outputs need readback; an ordinary edit does not.
            data_output = (
                pathless_save
                and item.direct
                and os.path.splitext(item.path)[1].lower() not in _CODE_SUFFIXES
            )
            needs_text_readback = (
                os.path.splitext(item.path)[1].lower() not in _BINARY_SUFFIXES
            )
            if (
                needs_text_readback
                and (data_output or requested(item.path))
                and not item.observed
            ):
                gaps.append(
                    f"`{item.path}` has not been read back completely after its latest write."
                )
        return gaps

    def cleanup_gaps(self, answer: str) -> list[str]:
        """A deletion claim needs a concrete path observed disappearing."""
        gaps = []
        for sentence in re.split(r"(?<=[.!?])\s+|\n", _FENCES.sub("", answer)):
            if re.search(
                r"\b(?:not|never|unable|couldn't|didn't|would|should|if)\b",
                sentence,
                re.I,
            ):
                continue
            # A report of this turn's action, not "deleted files go to the bin".
            claim = re.search(
                r"\b(?:I|I've|I have|we|we've|we have|successfully|also)\s+"
                r"(?:\w+\s+)?(?:removed|deleted|cleaned up)\s+",
                sentence,
                re.I,
            )
            terse = (
                None
                if claim
                else re.match(r"\s*(?:removed|deleted|cleaned up)\s+", sentence, re.I)
            )
            match = claim or terse
            if not match:
                continue
            tail = sentence[match.end() :]
            # Removing a file from an index or a list leaves it on disk.
            if _FROM_COLLECTION.search(tail):
                continue
            first = _TARGET.search(tail)
            # A terse "Deleted x" report must name a path, not a category.
            if terse and (not first or first.start() or _path_token(first) is None):
                continue
            is_file = re.match(
                r"(?:the |all |my |temporary |scratch |temp )*(?:files?|artifacts?)\b",
                tail,
                re.I,
            )
            if not is_file and (not first or _path_token(first) is None):
                continue
            if first and first.group(4) and _path_token(first):
                after = tail[first.end() :].split(None, 1)
                # "removed README.md references": the path modifies another noun.
                if after and after[0].strip(".,;:!?").lower() not in {
                    "",
                    "and",
                    "or",
                    "from",
                    "in",
                    "at",
                    "to",
                    "as",
                    "too",
                }:
                    continue
            paths = destination_paths(tail)
            if not paths:
                gaps.append(
                    "The cleanup claim names no concrete files whose removal can be verified. Name the removed paths or omit that claim."
                )
            for path in paths:
                key = self.key(path)
                # An absent, untouched path already agrees with the claim.
                if key not in self.removed and (
                    key in self.files or os.path.lexists(key)
                ):
                    gaps.append(f"No removal of `{key}` was observed this turn.")
        return gaps


def incomplete_answer(gaps: list[str]) -> str:
    """Framework-owned result; never repeat the unsupported candidate answer."""
    return (
        "I couldn't verify completion.\n\n"
        + "\n".join(f"- {gap}" for gap in gaps)
        + (
            "\n\nThe task is incomplete. Complete the missing work and read back the output before relying on it."
        )
    )
