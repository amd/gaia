# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Lemonade Server's own log, read for the Agent UI's log viewer.

Lemonade writes one log for itself and every backend it runs: llama.cpp's
``llama-server`` and FastFlowLM's ``flm serve`` print into it under the
``Process`` tag. GAIA reads it two ways:

* GAIA's own (embedded) Lemonade: the ``lemond.log`` file GAIA redirects its
  output to. The file outlives the server, so its last lines are there to read
  exactly when the server has stopped answering.
* A Lemonade the user runs: its log stream, a WebSocket on the port
  ``/api/v1/health`` reports as ``websocket_port``. A ``logs.subscribe``
  message with ``after_seq`` answers with a ``logs.snapshot`` of the lines
  after that sequence number.

Every line is redacted (:mod:`gaia.log_reader`) before it leaves this module.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from gaia.log_reader import (
    DEFAULT_LINES,
    MAX_LINES,
    LogRead,
    env_secrets,
    keep_last,
    parse_line,
    read_log_file,
    redact,
    severity_of,
)
from gaia.logger import get_logger

log = get_logger(__name__)

_STREAM_TIMEOUT_S = 4.0


def _ws_url(base_url: str, ws_port: int) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    host = parsed.hostname or "localhost"
    if ":" in host:
        host = f"[{host}]"
    return f"{scheme}://{host}:{ws_port}/logs/stream"


async def read_log_stream(
    base_url: str,
    ws_port: int,
    api_key: Optional[str],
    *,
    after: Optional[int] = None,
    secrets: Iterable[str] = (),
) -> List[Dict[str, Any]]:
    """The ``logs.snapshot`` entries after sequence *after* from Lemonade's stream.

    Raises:
        aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError: The
            stream could not be read; the caller words the reason.
    """
    import aiohttp

    from gaia.llm.lemonade_client import lemonade_auth_headers

    url = _ws_url(base_url, ws_port)
    timeout = aiohttp.ClientTimeout(total=_STREAM_TIMEOUT_S)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.ws_connect(
            url, headers=lemonade_auth_headers(api_key), autoping=False
        ) as ws:
            await ws.send_json(
                {"type": "logs.subscribe", "after_seq": after if after else 0}
            )
            while True:
                message = await asyncio.wait_for(ws.receive(), _STREAM_TIMEOUT_S)
                if message.type != aiohttp.WSMsgType.TEXT:
                    raise ValueError(
                        f"Lemonade's log stream at {url} closed without a snapshot "
                        f"({message.type.name})"
                    )
                payload = json.loads(message.data)
                kind = payload.get("type")
                if kind == "logs.snapshot" or "entries" in payload:
                    return list(payload.get("entries") or [])
                if kind == "error":
                    # The body names what was wrong, never echoes the key.
                    raise ValueError(
                        f"Lemonade's log stream refused the request: "
                        f"{redact(str(payload.get('message') or payload), secrets)}"
                    )


def _stream_lines(entries: List[Dict[str, Any]], secrets: Iterable[str]) -> List[tuple]:
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        seq = entry.get("seq")
        raw = entry.get("line")
        if not isinstance(raw, str):
            raw = " ".join(
                str(entry.get(k) or "") for k in ("timestamp", "severity", "tag")
            )
        line = parse_line(raw, secrets)
        sev = entry.get("severity")
        if isinstance(sev, str):
            line.severity = severity_of(sev) or line.severity
        if isinstance(entry.get("tag"), str):
            line.tag = entry["tag"] or line.tag
        out.append((seq if isinstance(seq, int) else None, line))
    return out


def _known_secrets(api_key: Optional[str]) -> List[str]:
    from gaia.llm.lemonade_client import _read_embedded_lemonade_state

    secrets = [api_key or "", *env_secrets()]
    state = _read_embedded_lemonade_state()
    if state and isinstance(state.get("api_key"), str):
        secrets.append(state["api_key"])
    return [s.strip() for s in secrets if s and s.strip()]


def known_secrets() -> tuple:
    """GAIA's Lemonade keys and secret env values, and a note if one was unreadable.

    A credential store that won't answer, or a config file that won't parse,
    must not hide GAIA's logs: those lines are still redacted by shape, and the
    note says the saved key wasn't.
    """
    from gaia.config import GaiaConfigError
    from gaia.llm.lemonade_choice import LemonadeChoiceError
    from gaia.llm.lemonade_client import resolve_lemonade_api_key

    try:
        return _known_secrets(resolve_lemonade_api_key(None)), None
    except (LemonadeChoiceError, GaiaConfigError) as e:
        log.warning("Log viewer: saved Lemonade key unreadable: %s", e)
        return _known_secrets(None), (
            f"The saved Lemonade key could not be read ({e}), so it is "
            "removed only where it looks like a key."
        )


async def read_lemonade_log(
    *, after: Optional[str] = None, limit: int = DEFAULT_LINES
) -> LogRead:
    """The log of the Lemonade GAIA uses now, or why it can't be read.

    Raises:
        LemonadeChoiceError, GaiaConfigError: GAIA's Lemonade choice could not
            be read (an unreadable config or credential store).
    """
    from gaia.llm import lemonade_choice as choice
    from gaia.llm.lemonade_client import (
        LemonadeClient,
        LemonadeClientError,
        resolve_lemonade_api_key,
        resolve_lemonade_base_url,
    )
    from gaia.llm.lemonade_embedded import EmbeddedLemonade
    from gaia.llm.model_server_info import server_owner

    limit = max(1, min(int(limit), MAX_LINES))
    info = choice.describe()
    base_url = resolve_lemonade_base_url(info.get("base_url"))
    api_key = resolve_lemonade_api_key(None, base_url=base_url)
    secrets = _known_secrets(api_key)

    def answering() -> tuple:
        try:
            health = LemonadeClient(base_url=base_url, verbose=False).health_check(
                timeout=3
            )
            return True, health if isinstance(health, dict) else {}, None
        except LemonadeClientError as e:
            return False, {}, redact(str(e), secrets)

    if info["mode"] == choice.MODE_GAIA:
        embedded = EmbeddedLemonade()
        offset = int(after) if after and after.isdigit() else None
        result = await asyncio.to_thread(
            read_log_file,
            embedded.log_path,
            after=offset,
            limit=limit,
            secrets=secrets,
        )
        result.owner, result.base_url = "gaia", base_url
        if after is None:
            # Only on a fresh read: a live tail must not poll the server each time.
            result.server_answering = (await asyncio.to_thread(answering))[0]
        return result

    owner = server_owner(base_url, None)
    result = LogRead(owner=owner, base_url=base_url)
    ok, health, error = await asyncio.to_thread(answering)
    result.server_answering = ok
    if not ok:
        result.reason = "remote" if owner == "remote" else "not_running"
        result.detail = error
        return result
    ws_port = health.get("websocket_port")
    if not isinstance(ws_port, int) or isinstance(ws_port, bool) or ws_port <= 0:
        result.reason = "no_stream"
        result.detail = (
            f"Lemonade {health.get('version') or ''} at {base_url} reports no "
            "log stream (websocket_port)."
        ).replace("  ", " ")
        return result
    seq_after = int(after) if after and after.isdigit() else None
    import aiohttp

    try:
        entries = await read_log_stream(
            base_url, ws_port, api_key, after=seq_after, secrets=secrets
        )
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as e:
        result.reason = "remote" if owner == "remote" else "stream_failed"
        result.detail = redact(
            f"{_ws_url(base_url, ws_port)}: {type(e).__name__}: {e}", secrets
        )
        log.info("Lemonade log stream unreadable: %s", result.detail)
        return result
    pairs = _stream_lines(entries, secrets)
    lines = [line for _, line in pairs]
    result.lines, result.dropped = keep_last(lines, limit)
    seqs = [seq for seq, _ in pairs if seq is not None]
    result.cursor = str(max(seqs)) if seqs else (after or None)
    result.source = "stream"
    result.available = True
    return result
