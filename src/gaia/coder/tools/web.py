# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``WebToolsMixin`` — fetch a URL and return its rendered text.

Daily-driver use cases this enables:

* "go look up the Anthropic prompt-caching docs and tell me whether we
  should use a 5-minute or 1-hour TTL"
* "fetch <https://github.com/amd/gaia/issues/123> and summarise"
* "compare what we do in `tools/cli.py` against the Claude Code
  documented behaviour at <…>"

Implementation notes:

* HTML → plain text via :class:`bs4.BeautifulSoup` (already a transitive
  dep of :mod:`gaia.eval.claude`). Markdown / JSON / TXT pass through
  unmodified. Anything else returns a header ``"(binary; <n> bytes)"``
  so the model knows not to try to read the body.
* :func:`urllib.request.urlopen` rather than :mod:`requests` to keep the
  coder's dep surface minimal — the stdlib client is fine for the
  simple GETs this tool does.
* 30-second connect+read timeout. Long enough for slow docs sites,
  short enough to not hang the REPL.
* Output truncated at the dispatcher's ``MAX_OUTPUT_CHARS`` cap (100KB
  by default). The dispatcher truncation marker tells the model to ask
  for a narrower fetch if it needs more.
* Fail loudly: any error from :func:`urlopen` or the parser is raised,
  not swallowed. The :class:`ToolDispatcher` converts the exception to
  an ``is_error=True`` ``tool_result`` so the model can recover.
"""

from __future__ import annotations

import logging
import re
import socket
import urllib.error
import urllib.request
from typing import Optional, TypedDict
from urllib.parse import urlparse

from gaia.agents.base.tools import tool

logger = logging.getLogger(__name__)


class FetchResult(TypedDict):
    """Result of :meth:`WebToolsMixin.fetch_url`."""

    url: str
    status: int
    content_type: str
    bytes_received: int
    text: str
    truncated: bool


_USER_AGENT: str = "gaia-coder/0.1 (+https://github.com/amd/gaia)"
_TIMEOUT_S: float = 30.0
_MAX_BYTES: int = 5 * 1024 * 1024  # 5MB transport cap
_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})


class WebToolsMixin:
    """Mixin providing the ``fetch_url`` tool for the daily-driver REPL.

    Composed by :class:`gaia.coder.agent.Agent`. Registers exactly one
    tool — keeping the surface tight; richer browsing (redirects we
    don't follow, JS rendering, auth) is out of scope for v1. The model
    can shell out to ``curl`` via ``run_cli_command`` for anything this
    tool refuses.
    """

    def register_web_tools(self) -> None:
        """Register ``fetch_url`` in the agent tool registry."""

        @tool
        def fetch_url(url: str, max_chars: Optional[int] = None) -> FetchResult:
            """Fetch a URL and return its content as text.

            HTML is converted to plain text via BeautifulSoup so the
            model gets readable prose rather than markup. JSON / Markdown
            / plain text pass through. Binary content (images, PDFs,
            archives) returns a header ``"(binary; <n> bytes)"`` so the
            model knows it cannot read the body.

            Args:
                url: Fully-qualified ``http://`` or ``https://`` URL.
                max_chars: Optional cap on text length.

            Returns:
                FetchResult with status, content-type, bytes received,
                text, and a ``truncated`` flag.

            Raises:
                ValueError: scheme not http/https or URL has no host.
                urllib.error.URLError: transport failure.
                socket.timeout: connect/read timeout.
            """
            parsed = urlparse(url)
            if parsed.scheme not in _ALLOWED_SCHEMES:
                raise ValueError(
                    f"fetch_url: scheme {parsed.scheme!r} not allowed; "
                    f"must be one of {sorted(_ALLOWED_SCHEMES)}. "
                    "(Use read_file for local paths.)"
                )
            if not parsed.netloc:
                raise ValueError(f"fetch_url: URL {url!r} has no host")

            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": (
                        "text/html,application/xhtml+xml,application/json,"
                        "text/markdown,text/plain;q=0.9,*/*;q=0.5"
                    ),
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as response:
                    status = int(getattr(response, "status", 200))
                    raw_content_type = (
                        response.headers.get("Content-Type", "") or ""
                    ).lower()
                    body = response.read(_MAX_BYTES + 1)
            except (urllib.error.URLError, socket.timeout) as e:
                logger.warning("fetch_url(%r) failed: %s", url, e)
                raise

            content_type = raw_content_type.split(";", 1)[0].strip() or "unknown"
            truncated_at_transport = len(body) > _MAX_BYTES
            if truncated_at_transport:
                body = body[:_MAX_BYTES]

            text = _extract_text(body, content_type)
            if max_chars is not None and len(text) > max_chars:
                text = text[:max_chars] + (
                    f"\n\n…[truncated to {max_chars} chars; "
                    "ask for a narrower fetch or a different URL]"
                )

            return {
                "url": url,
                "status": status,
                "content_type": content_type,
                "bytes_received": len(body),
                "text": text,
                "truncated": truncated_at_transport
                or (max_chars is not None and len(text) >= max_chars),
            }


def _extract_text(body: bytes, content_type: str) -> str:
    """Render ``body`` as plain text given its MIME ``content_type``."""
    if content_type in ("text/html", "application/xhtml+xml"):
        try:
            from bs4 import BeautifulSoup  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "fetch_url: HTML extraction needs bs4. Install with: "
                'uv pip install -e ".[eval]"'
            ) from exc
        soup = BeautifulSoup(body, "html.parser")
        # Drop scripts/styles before extracting text — otherwise a
        # single minified bundle can outweigh actual prose 100:1.
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text

    text_like = (
        content_type.startswith("text/")
        or content_type
        in (
            "application/json",
            "application/xml",
            "application/javascript",
            "application/x-yaml",
            "application/yaml",
        )
        or content_type.endswith("+json")
        or content_type.endswith("+xml")
    )
    if text_like:
        return body.decode("utf-8", errors="replace")

    return f"(binary; {len(body)} bytes; content-type={content_type!r})"


__all__ = ["FetchResult", "WebToolsMixin"]
