# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A local record of a failed model call, written the moment it fails.

"Couldn't reach the local LLM" says nothing about why: which address the
agent used, whether Lemonade or its llama.cpp / FastFlowLM child was alive,
what the drivers reported. This module saves that, once, at the point of
failure, as a JSON file under ``<GAIA home>/failures/``, so one bug report
carries what a maintainer would otherwise ask for.

Nothing here sends anything anywhere. The only network calls are two short
reads (``/health`` and ``/system-info``) to a Lemonade on this machine; a
remote server is named in the record but never contacted. The user decides
whether to share a record: ``gaia diagnostics`` and the Agent UI's
troubleshooting report bundle them. Secrets are redacted before writing.
"""

from __future__ import annotations

import json
import os
import platform
import re
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from gaia.credential_shapes import REDACTED, TOKEN_SHAPES, URL_USERINFO
from gaia.llm import call_ledger
from gaia.logger import get_logger

log = get_logger(__name__)

#: Records kept; the oldest go first.
KEEP_RECORDS = 10
#: Largest record written; the log tail is trimmed first to fit.
MAX_RECORD_BYTES = 256 * 1024
LOG_TAIL_LINES = 200
_LOG_TAIL_READ_BYTES = 256 * 1024
_MAX_MESSAGE_CHARS = 2000
_PROBE_TIMEOUT_S = 1.5
#: ``/system-info`` enumerates devices, which can take seconds on its first call.
_SYSTEM_INFO_TIMEOUT_S = 5.0

#: ``GF-20261104-192233-3f2a``: what the user quotes in a report.
RECORD_ID_RE = re.compile(r"GF-\d{8}-\d{6}-[0-9a-f]{4}")

#: Process names of the servers a local model call goes through.
_SERVER_PROCESSES = ("lemond", "lemonadeserver", "llama-server", "flm")

#: A field whose name is, or ends in, a secret's name: ``api_key``, ``Authorization``.
#: Not ``max_tokens`` or ``tokens_per_second``, which are counts.
_SECRET_KEY_RE = re.compile(
    r"(?:^|[_\s-])(?:api[_-]?key|key|token|secret|pass(?:word|wd)?"
    r"|auth(?:orization)?|credentials?|cookie)$",
    re.IGNORECASE,
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}"),
    re.compile(
        r"(?i)\b((?:[a-z]+[_-])?(?:api[_-]?key|token|secret|password|passwd))"
        r"([\"']?\s*[:=]\s*[\"']?)[^\s\"',;&]+"
    ),
    re.compile(r"\b(?:sk|pk|ghp|gho|ghs|xox[abp])[-_][A-Za-z0-9_-]{12,}"),
    re.compile(r"\bfw_[A-Za-z0-9]{16,}"),
    re.compile(URL_USERINFO),
    *(re.compile(shape, re.IGNORECASE) for shape in TOKEN_SHAPES),
)


def failures_dir() -> Path:
    from gaia.home import gaia_home

    return gaia_home() / "failures"


# ── Redaction ───────────────────────────────────────────────────────────────


def _home_pattern() -> Optional[re.Pattern]:
    """The user's home folder in any case and with any separator, JSON-escaped too."""
    try:
        home = str(Path.home())
    except RuntimeError:
        return None
    parts = [p for p in re.split(r"[\\/]+", home) if p]
    if len(home) < 4 or not parts:
        return None
    sep = r"(?:\\\\|\\|/)"
    lead = sep if home[:1] in "/\\" else ""
    return re.compile(lead + sep.join(re.escape(p) for p in parts), re.IGNORECASE)


def redact(text: str, known: Iterable[str] = ()) -> str:
    """*text* with secrets and the user's home folder taken out."""
    for value in known:
        if value and len(value) >= 4:
            text = text.replace(value, REDACTED)
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
        elif pattern.groups == 1:
            text = pattern.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
        else:
            text = pattern.sub(REDACTED, text)
    home = _home_pattern()
    return home.sub("~", text) if home else text


def redact_value(value: Any, known: Iterable[str] = ()) -> Any:
    """*value* with every string redacted and every secret-named field blanked."""
    known = list(known)
    if isinstance(value, dict):
        return {
            k: (
                REDACTED
                if isinstance(k, str) and _SECRET_KEY_RE.search(k) and v
                else redact_value(v, known)
            )
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_value(v, known) for v in value]
    if isinstance(value, str):
        return redact(value, known)
    return value


def _redact_url(url: Optional[str]) -> Optional[str]:
    """*url* without any user:password or query, which can carry a key."""
    if not url:
        return url
    try:
        parsed = urlparse(url)
    except ValueError:
        return REDACTED
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return parsed._replace(netloc=netloc, query="", fragment="").geturl()


# ── Classification ──────────────────────────────────────────────────────────


def _chain(exc: BaseException) -> List[BaseException]:
    out, seen = [], set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        out.append(cur)
        cur = cur.__cause__ or cur.__context__
    return out


def failure_kind(exc: BaseException) -> str:
    """``network``, ``timeout``, ``load_failure``, ``backend_crash`` or ``other``."""
    from gaia.llm.providers.lemonade import (
        LemonadeNetworkError,
        LemonadeStreamCutOffError,
        LemonadeUpstreamTimeoutError,
        classify_lemonade_exception,
    )

    chain = _chain(exc)
    names = {type(e).__name__ for e in chain}
    text = " ".join(str(e) for e in chain).lower()
    if (
        "model_load_error" in text
        or "failed to load" in text
        or "load_model" in text
        or any("Load" in n for n in names)
    ):
        return "load_failure"
    if (
        isinstance(exc, LemonadeStreamCutOffError)
        or "failed to start" in text
        or "crashed" in text
        or "exited with" in text
        or "terminated unexpectedly" in text
        or any(isinstance(e, LemonadeStreamCutOffError) for e in chain)
    ):
        return "backend_crash"
    if "ConnectTimeout" in names:
        return "network"
    if "StreamStalledError" in names or any(
        isinstance(e, (TimeoutError, LemonadeUpstreamTimeoutError)) for e in chain
    ):
        return "timeout"
    if any(
        isinstance(e, (ConnectionError, LemonadeNetworkError)) for e in chain
    ) or "ConnectionError" in " ".join(names):
        return "network"
    if "Timeout" in " ".join(names):
        return "timeout"
    classified = classify_lemonade_exception(exc)
    if isinstance(classified, LemonadeUpstreamTimeoutError):
        return "timeout"
    if isinstance(classified, LemonadeNetworkError):
        return "network"
    return "other"


def is_model_call_failure(exc: BaseException) -> bool:
    """Whether *exc* came from a call to Lemonade, not from the agent's own code."""
    import httpx
    import requests

    from gaia.llm.lemonade_client import LemonadeClientError
    from gaia.llm.providers.lemonade import (
        LemonadeError,
        classify_lemonade_exception,
    )

    model_errors = (
        LemonadeError,
        LemonadeClientError,
        requests.RequestException,
        httpx.HTTPError,
    )
    if any(isinstance(e, model_errors) for e in _chain(exc)):
        return True
    return classify_lemonade_exception(exc) is not None


def is_recordable(exc: BaseException) -> bool:
    """Whether *exc* is a fault worth a record, not a refusal the user can fix.

    A model unloaded mid-turn or a shared KV cache outgrown (a resend fixes
    both), too little free memory or disk, a Lemonade too old, an over-long
    conversation, a cloud account out of credit, Local only turning a cloud
    model away or a stop the user asked for each already say exactly what
    happened, and would push a real crash's record out of the ten kept.
    """
    from gaia.llm.lemonade_client import (
        InsufficientDiskSpaceError,
        InsufficientMemoryError,
        LemonadeVersionError,
        ModelDownloadCancelledError,
        ToolCallingProbeStopped,
        UsersServerUnchangedError,
    )
    from gaia.llm.providers.lemonade import (
        LemonadeCloudAccountError,
        LemonadeCloudModelUnavailableError,
        LemonadeContextOverflowError,
        LemonadeKVCacheSharedError,
        LemonadeLocalOnlyError,
        LemonadeModelNotLoadedError,
        classify_lemonade_exception,
    )

    said_why = (
        ModelDownloadCancelledError,
        ToolCallingProbeStopped,
        InsufficientMemoryError,
        InsufficientDiskSpaceError,
        LemonadeVersionError,
        UsersServerUnchangedError,
    )
    if any(isinstance(e, said_why) for e in _chain(exc)):
        return False
    classified = classify_lemonade_exception(exc)
    return not isinstance(
        classified,
        (
            LemonadeCloudAccountError,
            LemonadeCloudModelUnavailableError,
            LemonadeContextOverflowError,
            LemonadeLocalOnlyError,
            LemonadeModelNotLoadedError,
            LemonadeKVCacheSharedError,
        ),
    )


# ── What the machine looked like ────────────────────────────────────────────


def _is_loopback(host: Optional[str]) -> bool:
    return (host or "").strip("[]").lower() in {"localhost", "127.0.0.1", "::1"}


def _probe_json(
    url: str, api_key: Optional[str], timeout: float = _PROBE_TIMEOUT_S
) -> Dict[str, Any]:
    """GET *url* on a local Lemonade; the reply, or why there was none."""
    import requests

    if not _is_loopback(urlparse(url).hostname):
        return {"skipped": "not on this machine; GAIA never contacts it here"}
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    # No proxy from the environment and no redirect: this GET stays on this PC.
    with requests.Session() as session:
        session.trust_env = False
        try:
            resp = session.get(
                url, headers=headers, timeout=timeout, allow_redirects=False
            )
        except requests.RequestException as e:
            return {"error": f"{type(e).__name__}: {e}"}
    try:
        body = resp.json()
    except ValueError:
        body = {"text": resp.text[:500]}
    return {"http_status": resp.status_code, "body": body}


def _health_summary(probe: Dict[str, Any]) -> Dict[str, Any]:
    body = probe.get("body")
    if not isinstance(body, dict):
        return probe
    keep = ("status", "version", "model_loaded", "all_models_loaded", "max_models")
    return {"http_status": probe.get("http_status"), **{k: body.get(k) for k in keep}}


def _system_info_summary(probe: Dict[str, Any]) -> Dict[str, Any]:
    body = probe.get("body")
    if not isinstance(body, dict):
        return probe
    keep = ("OS Version", "Processor", "Physical Memory", "devices", "recipes")
    return {"http_status": probe.get("http_status"), **{k: body.get(k) for k in keep}}


def _embedded_state() -> Dict[str, Any]:
    """GAIA's own Lemonade's pid and port, never its recorded key."""
    from gaia.llm.lemonade_embedded import gaia_home as lemonade_home

    path = lemonade_home() / "lemonade" / "state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"present": False}
    except (OSError, ValueError) as e:
        return {"present": True, "error": f"unreadable: {type(e).__name__}"}
    if not isinstance(state, dict):
        return {"present": True, "error": "not a JSON object"}
    return {
        "present": True,
        "pid": state.get("pid"),
        "port": state.get("port"),
        "version": state.get("version"),
    }


def _embedded_key() -> str:
    """GAIA's own Lemonade key, only so it can be redacted wherever it shows."""
    from gaia.llm.lemonade_embedded import gaia_home as lemonade_home

    try:
        state = json.loads(
            (lemonade_home() / "lemonade" / "state.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return ""
    key = state.get("api_key") if isinstance(state, dict) else None
    return key if isinstance(key, str) else ""


def _processes() -> List[Dict[str, Any]]:
    """Lemonade and its backends now running: pid, name, uptime. No cmdlines."""
    import psutil

    now = time.time()
    found = []
    # Names only across every process: asking each for its start time and
    # status took 30-50 s on Windows. Those are read for the few matches.
    for proc in psutil.process_iter(["pid", "name"]):
        info = proc.info
        name = (info.get("name") or "").lower()
        stem = name[:-4] if name.endswith(".exe") else name
        if stem not in _SERVER_PROCESSES:
            continue
        entry: Dict[str, Any] = {"pid": info.get("pid"), "name": info.get("name")}
        try:
            entry["status"] = proc.status()
            entry["uptime_s"] = round(now - proc.create_time(), 1)
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            entry["status"] = type(e).__name__
        found.append(entry)
    return found


def _pid_alive(pid: Any) -> Optional[bool]:
    if not isinstance(pid, int):
        return None
    import psutil

    return psutil.pid_exists(pid)


def _lemond_log_tail(known: Iterable[str]) -> Dict[str, Any]:
    """The last lines of GAIA's own Lemonade log, where llama.cpp and FLM print too."""
    from gaia.llm.lemonade_embedded import gaia_home as lemonade_home

    path = lemonade_home() / "lemonade" / "lemond.log"
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            fh.seek(max(0, size - _LOG_TAIL_READ_BYTES))
            raw = fh.read()
    except FileNotFoundError:
        return {"source": "lemond.log", "missing": True, "lines": []}
    except OSError as e:
        return {
            "source": "lemond.log",
            "error": f"{type(e).__name__}: {e}",
            "lines": [],
        }
    lines = raw.decode("utf-8", errors="replace").splitlines()[-LOG_TAIL_LINES:]
    return {
        "source": "lemond.log",
        "size_bytes": size,
        "lines": [redact(line[:2000], known) for line in lines],
    }


def _versions(agent: Optional[str]) -> Dict[str, Any]:
    from gaia.version import LEMONADE_VERSION, __version__

    out: Dict[str, Any] = {
        "gaia": __version__,
        "lemonade_pinned": LEMONADE_VERSION,
        "python": platform.python_version(),
    }
    if agent:
        out["agent"] = agent
    return out


def _os_info() -> Dict[str, Any]:
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "kernel": platform.release(),
        "machine": platform.machine(),
        "frozen": bool(getattr(sys, "frozen", False)),
    }


def _endpoints(agent_base_url: Optional[str]) -> Dict[str, Any]:
    """Where the agent sent the call, and where GAIA expects Lemonade to listen."""
    from gaia.llm.lemonade_client import configured_lemonade_url

    configured = configured_lemonade_url()
    embedded = _embedded_state()
    if configured:
        host = urlparse(configured).hostname
        mode = "installed" if _is_loopback(host) else "remote"
    else:
        mode = "embedded" if embedded.get("present") else "embedded (not started)"
    listens = None
    if mode.startswith("embedded") and isinstance(embedded.get("port"), int):
        listens = f"http://127.0.0.1:{embedded['port']}/api/v1"
    elif configured:
        listens = configured
    agent_url = _redact_url(agent_base_url)
    return {
        "mode": mode,
        "agent_base_url": agent_url,
        "lemonade_url": _redact_url(listens),
        "agent_matches_lemonade": (
            None
            if not (agent_url and listens)
            else agent_url.rstrip("/") == (_redact_url(listens) or "").rstrip("/")
        ),
        "embedded": {**embedded, **_lemond_liveness(embedded.get("pid"))},
    }


def _lemond_liveness(pid: Any) -> Dict[str, Any]:
    """Whether GAIA's own lemond is alive, and how it ended when it isn't."""
    alive = _pid_alive(pid)
    if alive is not False:
        return {"pid_alive": alive}
    from gaia.llm.lemonade_embedded import _describe_exit_code, _exit_details

    code, when = _exit_details(pid)
    return {
        "pid_alive": False,
        "exit": _describe_exit_code(code),
        "exited_at": (
            datetime.fromtimestamp(when).astimezone().isoformat(timespec="seconds")
            if when
            else None
        ),
    }


def _npu_summary(system_info: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The NPU and FastFlowLM backend as Lemonade reports them, read off ``/system-info``."""
    if not isinstance(system_info, dict):
        return None
    devices = system_info.get("devices") or {}
    recipes = system_info.get("recipes") or {}
    npu = devices.get("amd_npu") if isinstance(devices, dict) else None
    flm = recipes.get("flm") if isinstance(recipes, dict) else None
    if npu is None and flm is None:
        return None
    return {"device": npu, "flm_backend": flm}


def _error(exc: BaseException, known: Iterable[str]) -> Dict[str, Any]:
    from gaia.llm.providers.lemonade import LemonadeError

    known = list(known)
    chain = _chain(exc)
    typed = next((e for e in chain if isinstance(e, LemonadeError)), None)
    out: Dict[str, Any] = {
        "class": f"{type(exc).__module__}.{type(exc).__qualname__}",
        "chain": [type(e).__name__ for e in chain],
        "message": redact(str(exc), known)[:_MAX_MESSAGE_CHARS],
        "causes": [redact(str(e), known)[:500] for e in chain[1:4]],
    }
    if typed is not None:
        out["typed"] = type(typed).__name__
        if typed.payload:
            out["payload"] = redact_value(typed.payload, known)
    return out


def _previous_lemond_pid(directory: Path) -> Optional[int]:
    records = _records(directory)
    if not records:
        return None
    try:
        last = json.loads(records[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = last.get("endpoints", {}).get("embedded", {}).get("pid")
    return pid if isinstance(pid, int) else None


def _section(name: str, build, errors: Dict[str, str]):
    """One part of the record; a part that can't be read says why, in *errors*."""
    try:
        return build()
    except Exception as e:  # noqa: BLE001 - each part of a fault report is data
        errors[name] = f"{type(e).__name__}: {e}"
        return None


# ── Writing ─────────────────────────────────────────────────────────────────


def _records(directory: Path) -> List[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.glob("GF-*.json") if RECORD_ID_RE.fullmatch(p.stem)
    )


def _new_id() -> str:
    return f"GF-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


def _fit(record: Dict[str, Any]) -> bytes:
    """*record* as JSON no larger than :data:`MAX_RECORD_BYTES`."""
    blob = json.dumps(record, indent=2, default=str).encode("utf-8")
    lines = (record.get("logs") or {}).get("lines") or []
    while len(blob) > MAX_RECORD_BYTES and lines:
        del lines[: max(1, len(lines) // 4)]
        record["logs"]["trimmed"] = True
        blob = json.dumps(record, indent=2, default=str).encode("utf-8")
    for key in ("system_info", "health", "processes", "extra"):
        if len(blob) <= MAX_RECORD_BYTES:
            break
        record[key] = {"trimmed": "over the record size limit"}
        blob = json.dumps(record, indent=2, default=str).encode("utf-8")
    if len(blob) > MAX_RECORD_BYTES:
        error = record.get("error") or {}
        error["message"] = str(error.get("message", ""))[:500]
        error.pop("payload", None)
        error["causes"] = []
        blob = json.dumps(record, indent=2, default=str).encode("utf-8")
    return blob


def record_failure(
    exc: BaseException,
    *,
    where: str,
    kind: Optional[str] = None,
    model: Optional[str] = None,
    recipe: Optional[str] = None,
    device: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    agent: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Save what GAIA saw when *exc* failed a model call; returns the record's id.

    *where* names the surface (``agent_turn``, ``ui_chat``, ``setup``,
    ``sidecar_start``). *base_url* and *api_key* are what the caller sent:
    the address is recorded and the key only as "sent or not". Returns None,
    after logging why, when the record can't be built or written: a report
    about an error must never replace the error the user actually hit.
    """
    try:
        return _record(
            exc,
            where=where,
            kind=kind,
            model=model,
            recipe=recipe,
            device=device,
            base_url=base_url,
            api_key=api_key,
            agent=agent,
            extra=extra,
        )
    except Exception:  # noqa: BLE001 - the boundary: the user's own error goes on
        log.exception("Couldn't save a failure record for %s", type(exc).__name__)
        return None


def _record(
    exc: BaseException,
    *,
    where: str,
    kind: Optional[str],
    model: Optional[str],
    recipe: Optional[str],
    device: Optional[str],
    base_url: Optional[str],
    api_key: Optional[str],
    agent: Optional[str],
    extra: Optional[Dict[str, Any]],
) -> Optional[str]:
    known = [api_key or "", os.environ.get("LEMONADE_API_KEY", ""), _embedded_key()]
    errors: Dict[str, str] = {}
    directory = failures_dir()
    record_id = _new_id()

    endpoints = _section("endpoints", lambda: _endpoints(base_url), errors) or {}
    endpoints["api_key_sent"] = bool(api_key)
    probe_base = (base_url or endpoints.get("lemonade_url") or "").rstrip("/")
    health = system_info = None
    if probe_base:
        health = _section(
            "health",
            lambda: _health_summary(_probe_json(f"{probe_base}/health", api_key)),
            errors,
        )
        # A server that didn't answer /health won't answer this either.
        if health and health.get("http_status"):
            system_info = _section(
                "system_info",
                lambda: _system_info_summary(
                    _probe_json(
                        f"{probe_base}/system-info", api_key, _SYSTEM_INFO_TIMEOUT_S
                    )
                ),
                errors,
            )
    previous_pid = _section(
        "previous_record", lambda: _previous_lemond_pid(directory), errors
    )
    now_pid = (endpoints.get("embedded") or {}).get("pid")
    record: Dict[str, Any] = {
        "id": record_id,
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "where": where,
        "kind": kind or failure_kind(exc),
        "versions": _versions(agent),
        "os": _os_info(),
        "model": {"id": model, "recipe": recipe, "device": device},
        "endpoints": endpoints,
        "health": health,
        "system_info": system_info,
        "processes": _section("processes", _processes, errors),
        "lemonade_restarted_since_last_record": (
            None
            if previous_pid is None or not isinstance(now_pid, int)
            else previous_pid != now_pid
        ),
        "error": _error(exc, known),
        # What this process sent: the load request and why its window, the chat
        # request's size, and how each ended.
        "requests": _section("requests", call_ledger.snapshot, errors),
        "logs": _section("logs", lambda: _lemond_log_tail(known), errors),
    }
    npu = _npu_summary(system_info)
    if npu:
        record["npu"] = npu
    if extra:
        record["extra"] = extra
    if errors:
        record["unreadable"] = errors
    record = redact_value(record, known)

    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{record_id}.json").write_bytes(_fit(record))
        for old in _records(directory)[:-KEEP_RECORDS]:
            old.unlink(missing_ok=True)
    except OSError as e:
        log.warning("Couldn't save the failure record in %s: %s", directory, e)
        return None
    log.info("Saved failure record %s (%s, %s)", record_id, where, record["kind"])
    return record_id


def record_local_failure(
    exc: BaseException,
    *,
    where: str,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    **kwargs: Any,
) -> Optional[str]:
    """:func:`record_failure` for a call made to GAIA's configured Lemonade.

    For callers that went through ``LemonadeClient()``: the address it was
    given (*base_url*), else the one it resolved, and that server's key are
    recorded. Returns None for a refusal :func:`is_recordable` rules out.
    """
    try:
        recordable = is_recordable(exc)
    except Exception:  # noqa: BLE001 - the boundary, as in record_failure
        log.exception("Couldn't classify %s for a failure record", type(exc).__name__)
        return None
    if not recordable:
        return None
    from gaia.llm.lemonade_client import (
        resolve_lemonade_api_key,
        resolve_lemonade_base_url,
    )

    # The config that names the address may be what broke; the record says so.
    errors: Dict[str, str] = {}
    given = base_url
    base_url = _section(
        "resolve_base_url", lambda: resolve_lemonade_base_url(given), errors
    )
    api_key = (
        _section(
            "resolve_api_key",
            lambda: resolve_lemonade_api_key(base_url=base_url),
            errors,
        )
        if base_url
        else None
    )
    if errors:
        kwargs["extra"] = {**(kwargs.get("extra") or {}), "unresolved": errors}
    return record_failure(
        exc, where=where, model=model, base_url=base_url, api_key=api_key, **kwargs
    )


def record_note(record_id: Optional[str]) -> str:
    """The line a failed answer ends with, naming the record; empty without one."""
    if not record_id:
        return ""
    return (
        f"A problem report for this was saved on this PC as {record_id}; "
        "nothing was sent anywhere."
    )


def recent_records(limit: int = KEEP_RECORDS) -> List[Path]:
    return _records(failures_dir())[-limit:]
