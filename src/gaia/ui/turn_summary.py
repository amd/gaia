# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What one chat turn used, wrote, and sent off this PC (#5222).

The Agent UI's side rail reads this summary: the pages and document passages
the answer drew on, the files the turn wrote, where the model ran, which sites
the web tools contacted, and what the turn cost. It is built from the tool
events the SSE handler already sees plus the turn's inference location, so it
describes what happened rather than what the configuration implies.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import urlparse

from gaia.agents.base.verification import EXECUTED_KEY
from gaia.llm.inference_location import InferenceLocation
from gaia.logger import get_logger

logger = get_logger(__name__)

#: Tools that fetch live data from the internet, keyed to the argument naming
#: the address they contact.
LIVE_DATA_TOOLS: Dict[str, Optional[str]] = {
    "search_web": None,
    "fetch_page": "url",
    "fetch_webpage": "url",
    "download_file": "url",
    "browser_open": "url",
}

#: Tools whose result is the page itself, so a success means the page was read.
_PAGE_TOOLS = frozenset({"fetch_page", "fetch_webpage", "browser_open"})

#: Tools that write a file named by their ``file_path`` argument.
WRITE_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "write_markdown_file",
        "write_python_file",
        "edit_python_file",
        "replace_function",
        "update_gaia_md",
    }
)

#: Indexing tools, keyed to the argument naming what they indexed.
_INDEX_TOOLS = {"index_document": "file_path", "index_directory": "directory_path"}

_SHELL_TOOLS = frozenset({"run_shell_command", "run_command"})

#: ``gaia schedule add`` run from the shell creates a scheduled task.
_SCHEDULE_ADD_RE = re.compile(
    r"\bgaia\s+schedule\s+add\b(?:.*?--name[ =](?:\"(?P<name>[^\"]+)\"|'(?P<n2>[^']+)'|(?P<n3>\S+)))?"
)

#: The host the built-in ``search_web`` sends its queries to (``WebClient``).
_SEARCH_HOST = "duckduckgo.com"

#: File types the rail may hand to the default app: nothing that runs code,
#: so no scripts or programs, and no HTML, SVG or XML, which can open in a browser.
OPENABLE_SUFFIXES = frozenset(
    {
        ".md",
        ".txt",
        ".csv",
        ".tsv",
        ".json",
        ".yaml",
        ".yml",
        ".log",
        ".pdf",
        ".docx",
        ".xlsx",
        ".pptx",
        ".odt",
        ".ods",
        ".odp",
        ".rtf",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
    }
)

_MAX_PAGES = 12
_MAX_DOCUMENTS = 12
_MAX_FILES = 30
_PAGE_TITLE_RE = re.compile(r"^Page:\s*(?P<title>.+)$", re.MULTILINE)
_SEARCH_HIT_RE = re.compile(
    r"^\d+\.\s+(?P<title>.+)\n\s+(?P<url>https?://\S+)", re.MULTILINE
)


def is_openable(path: str) -> bool:
    """Whether *path* is a type the default app can open without running code."""
    return Path(path).suffix.lower() in OPENABLE_SUFFIXES


def search_hits(text: str) -> List[Dict[str, str]]:
    """The ``title``/``url`` of each hit in a ``search_web`` text result."""
    return [
        {"title": m.group("title").strip(), "url": m.group("url")}
        for m in _SEARCH_HIT_RE.finditer(text)
    ]


def _search_sources(data: Any) -> Optional[List[Any]]:
    """A ``search_web`` result's hits, from its text or from the summary a
    relayed turn carries instead; None when it is neither."""
    if isinstance(data, str):
        return search_hits(data)
    if isinstance(data, dict) and isinstance(data.get("sources"), list):
        return data["sources"]
    return None


def _payload(data: Any) -> Any:
    """A tool result as a dict when it is JSON, else as given."""
    if isinstance(data, str) and data.lstrip().startswith("{"):
        try:
            return json.loads(data)
        except ValueError:
            return data
    return data


def _host(url: Any) -> Optional[str]:
    if not isinstance(url, str):
        return None
    host = urlparse(url.strip()).hostname
    if not host:
        return None
    return host[4:] if host.startswith("www.") else host


def _ran(call: Mapping[str, Any]) -> bool:
    """Whether the tool body ran, so a refused or denied call contacted nothing."""
    data = call.get("data")
    if not isinstance(data, dict):
        return True
    return data.get(EXECUTED_KEY) is not False and data.get("status") != "denied"


def _web_url(url: Any) -> bool:
    """Whether *url* is an http(s) address the rail may link to."""
    return (
        isinstance(url, str)
        and urlparse(url.strip()).scheme.lower() in ("http", "https")
        and _host(url) is not None
    )


class TurnLedger:
    """Collects one turn's tool calls as the SSE handler sees them."""

    def __init__(self) -> None:
        self._calls: List[Dict[str, Any]] = []

    def tool_started(self, tool: str) -> None:
        self._calls.append({"tool": tool, "args": {}, "done": False})

    def tool_args(self, args: Mapping[str, Any]) -> None:
        if self._calls and not self._calls[-1]["done"]:
            self._calls[-1]["args"] = dict(args)

    def tool_result(self, data: Any, success: bool) -> None:
        if not self._calls or self._calls[-1]["done"]:
            return
        call = self._calls[-1]
        call.update(done=True, success=success, data=_payload(data))

    # ── Aggregation ─────────────────────────────────────────────────────

    def _finished(self) -> Iterable[Dict[str, Any]]:
        return (c for c in self._calls if c["done"])

    def _pages(self) -> List[Dict[str, Any]]:
        read: Dict[str, Dict[str, Any]] = {}
        hits: Dict[str, Dict[str, Any]] = {}
        for call in self._finished():
            if not call["success"]:
                continue
            tool, data = call["tool"], call["data"]
            if tool in _PAGE_TOOLS:
                url = call["args"].get("url")
                if isinstance(data, dict):
                    url = data.get("url") or url
                if not _web_url(url):
                    continue
                title = data.get("title") if isinstance(data, dict) else None
                if title is None and isinstance(data, str):
                    match = _PAGE_TITLE_RE.search(data)
                    title = match.group("title").strip() if match else None
                read.setdefault(url, {"url": url, "title": title, "kind": "page"})
            elif tool == "search_web":
                for hit in _search_sources(data) or []:
                    url = hit.get("url") if isinstance(hit, dict) else None
                    if not _web_url(url):
                        continue
                    title = hit.get("title")
                    hits.setdefault(
                        url,
                        {
                            "url": url,
                            "title": title if isinstance(title, str) else None,
                            "kind": "search_result",
                        },
                    )
        # Search hits stand in only when the agent opened no page.
        pages = list(read.values()) or list(hits.values())
        return pages[:_MAX_PAGES]

    def _documents(self, display_names: Mapping[str, str]) -> List[Dict[str, Any]]:
        docs: Dict[str, Dict[str, Any]] = {}
        seen: set = set()
        for call in self._finished():
            data = call["data"]
            if not call["success"] or not isinstance(data, dict):
                continue
            chunks = data.get("chunks")
            if not isinstance(chunks, list):
                continue
            for chunk in chunks:
                if not isinstance(chunk, dict) or not chunk.get("source_file"):
                    continue
                path = str(chunk["source_file"])
                base = Path(path).name
                entry = docs.setdefault(
                    path,
                    {
                        "name": display_names.get(base, base),
                        "path": path,
                        "pages": [],
                        "passages": 0,
                    },
                )
                page = chunk.get("page")
                # Two queries in one turn often return the same passage.
                body = chunk.get("content", chunk.get("chunk_index"))
                body = body if isinstance(body, (str, int)) else None
                key = (path, page if isinstance(page, int) else None, body)
                if body is None or key not in seen:
                    seen.add(key)
                    entry["passages"] += 1
                if isinstance(page, int) and page not in entry["pages"]:
                    entry["pages"].append(page)
        for entry in docs.values():
            entry["pages"].sort()
        return list(docs.values())[:_MAX_DOCUMENTS]

    def _sites(self) -> List[str]:
        sites: List[str] = []
        for call in self._finished():
            tool = call["tool"]
            if tool not in LIVE_DATA_TOOLS or not _ran(call):
                continue
            arg = LIVE_DATA_TOOLS[tool]
            if arg is None:
                # Only the built-in search (text result) has a known backend.
                searched = _search_sources(call["data"]) is not None
                host = _SEARCH_HOST if searched else None
            else:
                host = _host(call["args"].get(arg))
            if host and host not in sites:
                sites.append(host)
        return sites

    def _written(self) -> List[str]:
        paths: List[str] = []
        for call in self._finished():
            if call["tool"] not in WRITE_TOOLS or not call["success"]:
                continue
            path = call["args"].get("file_path")
            data = call["data"]
            if isinstance(data, dict) and isinstance(data.get("file_path"), str):
                path = data["file_path"]
            if isinstance(path, str) and os.path.isabs(path):
                paths.append(path)
        return paths

    def _produced(self) -> List[Dict[str, Any]]:
        """Things the turn made besides files: images, indexed documents,
        memories and schedules, for the session panel's Outputs (#5277)."""
        out: List[Dict[str, Any]] = []
        for call in self._finished():
            tool, args, data = call["tool"], call["args"], call["data"]
            status = data.get("status") if isinstance(data, dict) else None
            if not call["success"] or status in ("error", "skipped"):
                continue
            if tool == "generate_image" and isinstance(data, dict):
                path = data.get("image_path")
                if isinstance(path, str):
                    out.append(
                        {
                            "kind": "image",
                            "name": Path(path).name,
                            "path": path,
                            "detail": args.get("prompt"),
                        }
                    )
            elif tool in _INDEX_TOOLS:
                path = args.get(_INDEX_TOOLS[tool])
                if isinstance(path, str) and path:
                    path = os.path.expanduser(path)
                    out.append(
                        {"kind": "document", "name": Path(path).name, "path": path}
                    )
            elif tool == "remember" and isinstance(args.get("fact"), str):
                out.append(
                    {
                        "kind": "memory",
                        "name": args["fact"][:120],
                        "detail": args.get("category"),
                        "ref": (
                            data.get("knowledge_id") if isinstance(data, dict) else None
                        ),
                    }
                )
            elif tool == "schedule_send":
                out.append(
                    {
                        "kind": "schedule",
                        "name": str(args.get("subject") or "Scheduled email"),
                        "detail": args.get("send_at"),
                    }
                )
            elif tool in _SHELL_TOOLS:
                command = args.get("command")
                match = (
                    _SCHEDULE_ADD_RE.search(command)
                    if isinstance(command, str)
                    else None
                )
                if match:
                    out.append(
                        {
                            "kind": "schedule",
                            "name": match.group("name")
                            or match.group("n2")
                            or match.group("n3")
                            or "Scheduled task",
                            "detail": command[:200],
                        }
                    )
        return out[:_MAX_FILES]

    def live_data(self) -> Dict[str, int]:
        calls = [
            c for c in self._finished() if c["tool"] in LIVE_DATA_TOOLS and _ran(c)
        ]
        return {
            "attempted": len(calls),
            "failed": sum(1 for c in calls if not c["success"]),
        }

    def summary(
        self,
        *,
        location: Optional[InferenceLocation],
        written_files: Iterable[str] = (),
        display_names: Optional[Mapping[str, str]] = None,
        usage: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """The turn summary the side rail renders.

        ``location`` is where this turn's model actually ran; ``None`` when it
        could not be determined, which the rail reports rather than assuming
        this PC. ``written_files`` are paths the agent's own completion
        evidence saw written (scripts included); ``display_names`` maps an
        upload copy's file name to the name the user attached it under.
        """
        documents = self._documents(display_names or {})
        files: List[Dict[str, Any]] = []
        seen = set()
        for path in [*written_files, *self._written()]:
            key = os.path.normcase(os.path.normpath(path))
            if key in seen or not os.path.isfile(path):
                continue
            seen.add(key)
            files.append(
                {
                    "path": path,
                    "name": Path(path).name,
                    "openable": is_openable(path),
                    "mtime": os.path.getmtime(path),
                }
            )
        out: Dict[str, Any] = {
            "pages": self._pages(),
            "documents": documents,
            "files": files[:_MAX_FILES],
            "sites": self._sites(),
            "outputs": self._produced(),
            "live_data": self.live_data(),
            "cost": _cost(location, usage or {}),
        }
        if location is not None:
            out["model"] = {
                "remote": location.remote,
                "provider": location.provider,
                "provider_name": location.display,
                "name": location.model_name,
            }
        return out


def _cost(
    location: Optional[InferenceLocation], usage: Mapping[str, Any]
) -> Dict[str, Any]:
    """What the turn cost: nothing on this PC, a list-price estimate in the cloud."""
    if location is None:
        return {"kind": "unknown"}
    if not location.remote:
        return {"kind": "local"}
    # Only Fireworks publishes a rate card GAIA carries.
    if location.provider != "fireworks":
        return {"kind": "unknown"}
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("tokens")
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return {"kind": "unknown"}
    cached = usage.get("cached_tokens")
    cached = cached if isinstance(cached, int) else 0
    # Lazy: the rate card lives with the benchmark's metering code.
    from gaia.eval.bench.metering import price

    usd = price(location.model, input_tokens, cached, output_tokens)
    if usd is None:
        return {"kind": "unknown"}
    return {"kind": "estimate", "usd": round(usd, 6)}


def upload_display_names(message: str) -> Dict[str, str]:
    """Upload copy file name -> the name the user attached it as (#5183).

    The composer links each attachment as ``[original.pdf](<uploads>/<uuid>.pdf)``,
    so the link label is the only place the original name survives.
    """
    from gaia.ui.utils import uploads_dir

    root = os.path.normcase(str(uploads_dir().resolve())) + os.sep
    names: Dict[str, str] = {}
    for match in re.finditer(
        r"\[(?P<label>[^\]\n]+)\]\((?P<target>[^)\s]+)\)", message
    ):
        target = match.group("target")
        base = re.split(r"[\\/]", target)[-1]
        # A Windows link may use either slash and any letter case.
        local = os.path.normcase(os.path.normpath(target)).startswith(root)
        if local or target.startswith("/api/files/uploads/"):
            names[base] = match.group("label").strip()
    return names
