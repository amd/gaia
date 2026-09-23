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
from dataclasses import dataclass, field
from typing import Any, Callable

WRITE_TOOLS = frozenset(
    {
        "write_file",
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
_SAVE_REQUEST = re.compile(r"\b(?:save|write|export|store)\b", re.I)
_NOT_REQUEST = re.compile(
    r"\b(?:do not|don't|never|without|how (?:do|can|would)|explain how|"
    r"show me how|if|could you explain)\b",
    re.I,
)
# Quoting allows spaces; bare paths are scanned as tokens, never suffix matches.
_TARGET = re.compile(r"`([^`\n]+)`|\"([^\"\n]+)\"|'([^'\n]+)'|([^\s`\"'<>]+)")
_DESTINATION = re.compile(r"\b(?:to|into|at|as)\s+", re.I)


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
    value = next(g for g in match.groups() if g is not None).strip().rstrip(".,;:)")
    if not value or "://" in value or "@" in value:
        return None
    quoted = any(match.group(i) is not None for i in (1, 2, 3))
    suffix = value.rsplit(".", 1)[-1].lower()
    if (
        quoted
        or "/" in value
        or "\\" in value
        or (
            "." in value
            and suffix.isalpha()
            and suffix not in {"com", "org", "net", "io", "ai", "dev", "app"}
        )
    ):
        return value
    return None


def destination_paths(text: str) -> list[str]:
    """Read a destination noun phrase, stopping at the next action."""
    # A later 'email it to me' must not replace the save's destination.
    clause = re.split(
        r"\s+(?:and|then)\s+(?=(?:compare|read|email|send|check|show|summarize|"
        r"use|return|tell|report)\b)",
        text,
        maxsplit=1,
        flags=re.I,
    )[0]
    destinations = list(_DESTINATION.finditer(clause))
    scan = clause[destinations[-1].end() :] if destinations else clause
    paths = []
    for match in _TARGET.finditer(scan):
        path = _path_token(match)
        if path is not None:
            paths.append(path)
        elif paths and match.group().lower().strip(",") not in {"and", "or"}:
            break
    return list(dict.fromkeys(paths))


def save_obligations(query: str) -> tuple[list[str], bool]:
    """Explicit save instructions; advisory/negated instructions aren't tasks."""
    paths = []
    requested = False
    for sentence in re.split(r"(?<=[.!?])\s+|\n", _FENCES.sub("", query)):
        action = _SAVE_REQUEST.search(sentence)
        if not action or _NOT_REQUEST.search(sentence[: action.end()]):
            continue
        tail = sentence[action.end() :]
        found = destination_paths(tail)
        if action.group().lower() == "write" and not _DESTINATION.search(tail):
            first = _TARGET.search(tail)
            if not first or _path_token(first) is None:
                found = []
        # 'write a poem' requests an answer, not a disk side effect.
        if (
            found
            or re.search(r"\b(?:save|export|store)\b", action.group(), re.I)
            or re.search(r"\b(?:file|disk)\b", tail, re.I)
        ):
            requested = True
            paths.extend(found)
    return list(dict.fromkeys(paths)), requested


@dataclass
class FileEvidence:
    path: str
    written: int = 0
    direct: bool = False
    observed: bool = False
    ranges: list[tuple[int, int]] = field(default_factory=list)
    end: int | None = None

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

    def key(self, path: str, root: str | None = None) -> str:
        base = root or self.root
        if ntpath.isabs(path) and ("\\" in path or ntpath.splitdrive(path)[0]):
            return ntpath.normcase(ntpath.normpath(path))
        if ntpath.splitdrive(base)[0]:
            return ntpath.normcase(ntpath.normpath(ntpath.join(base, path)))
        return os.path.normcase(
            os.path.abspath(os.path.join(base, os.path.expanduser(path)))
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
                            paths.add(self.key(value))
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
            key = self.key(path, args.get("project_dir"))
            self.removed.discard(key)
            self.files[key] = FileEvidence(
                key,
                self.sequence if successful else 0,
                tool in WRITE_TOOLS or tool in _EXEC_TOOLS,
            )

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
        if not isinstance(path, str):
            return
        key = self.key(path, args.get("project_dir"))
        item = self.files.get(key)
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
        start = raw.get("offset", 0)
        end = start + len(content)
        total = None if raw.get("next_offset") is not None else end
        item.page(start, end, total)

    def gaps(self, answer: str, claims_file_write: Callable[[str], bool]) -> list[str]:
        required = {self.key(path) for path in self.requested}
        claim_without_path = False
        for sentence in re.split(r"(?<=[.!?])\s+|\n", _FENCES.sub("", answer)):
            if claims_file_write(sentence):
                paths = destination_paths(sentence)
                required.update(self.key(path) for path in paths)
                claim_without_path |= not paths
        gaps = self.cleanup_gaps(answer)
        for path in sorted(required):
            if path not in self.files or not self.files[path].written:
                reason = self.uninspectable.get(path)
                gaps.append(
                    f"Could not inspect `{path}`: {reason}"
                    if reason
                    else f"No successful write to `{path}` is recorded for this turn."
                )
        if (
            not required
            and (self.save_requested or claim_without_path)
            and not any(item.direct and item.written for item in self.files.values())
        ):
            gaps.append(
                "The requested output file has no recorded successful write; an earlier side-effect file does not fulfill that save."
            )
        for item in self.files.values():
            # Only the requested/claimed artifacts and deliberate file edits need
            # readback; an incidental transcript is not an output obligation.
            data_output = (
                item.direct
                and os.path.splitext(item.path)[1].lower() not in _CODE_SUFFIXES
            )
            needs_text_readback = (
                os.path.splitext(item.path)[1].lower() not in _BINARY_SUFFIXES
            )
            if (
                needs_text_readback
                and (data_output or item.path in required)
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
            match = re.search(r"\b(?:removed|deleted|cleaned up)\s+", sentence, re.I)
            if not match:
                continue
            tail = sentence[match.end() :]
            first = _TARGET.search(tail)
            is_file = re.match(
                r"(?:the |all |my |temporary |scratch |temp )*(?:files?|artifacts?)\b",
                tail,
                re.I,
            )
            if not is_file and (not first or _path_token(first) is None):
                continue
            paths = destination_paths(tail)
            if not paths:
                gaps.append(
                    "The cleanup claim names no concrete files whose removal can be verified. Name the removed paths or omit that claim."
                )
            for path in paths:
                if self.key(path) not in self.removed:
                    gaps.append(
                        f"No removal of `{self.key(path)}` was observed this turn."
                    )
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
