# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The anomalous-entity list is learned from the data, not from a fixed regex.

Each test generates a log whose ids follow one style (word_-digits, UUIDs,
hex trace ids in JSON fields, key=value tokens, ip:port backends), next to
other id-shaped tokens that every routine line carries (peers, users, client
addresses). Without ``entity_regex`` the tool must pick the id kind that
singles out the incident, say which one and why the others were skipped, and
return a precise list.
"""

from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Set, Tuple

import pytest

from tests.unit.log_triage_scenarios import bound, load_tools

lt = load_tools()

T0 = datetime(2025, 5, 6, tzinfo=timezone.utc).timestamp()
HOURS = 6


@pytest.fixture(autouse=True)
def _skill_call():
    with bound():
        yield


def _iso(ts: float) -> str:
    return (
        datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[
            :-3
        ]
        + "Z"
    )


def _times(rng: random.Random, n: int) -> List[float]:
    return sorted(rng.uniform(T0, T0 + HOURS * 3600) for _ in range(n))


def _write(tmp_path: Path, rows: List[Tuple[float, str]]) -> Path:
    rows.sort()
    path = tmp_path / "app.log"
    path.write_text("\n".join(r for _, r in rows) + "\n", encoding="utf-8")
    return path


def _window(rng: random.Random, n: int) -> List[float]:
    start = T0 + 0.6 * HOURS * 3600
    return sorted(start + rng.uniform(0, 300) for _ in range(n))


def chunk_ids(tmp_path: Path, rng: random.Random) -> Tuple[Path, Set[str]]:
    """``blk_-?\\d+`` chunk ids; every line also names a peer ``ip:port``."""
    peers = [f"10.4.0.{i}:7001" for i in range(1, 21)]
    chunks = [
        f"blk_{rng.choice(['', '-'])}{rng.randint(10**9, 10**12)}" for _ in range(3000)
    ]
    rows = []
    for ts, c in zip(_times(rng, 3000), chunks):
        rows.append(
            (
                ts,
                f"{_iso(ts)} store INFO stored chunk {c} from peer {rng.choice(peers)}",
            )
        )
        ts2 = ts + rng.uniform(1, 30)
        rows.append(
            (
                ts2,
                f"{_iso(ts2)} store INFO replicated chunk {c} to peer {rng.choice(peers)}",
            )
        )
    bad = set(rng.sample(chunks, 40))
    for ts, c in zip(_window(rng, 40), sorted(bad)):
        rows.append(
            (
                ts,
                f"{_iso(ts)} store ERROR checksum mismatch on chunk {c} from peer {rng.choice(peers)}",
            )
        )
    return _write(tmp_path, rows), bad


def request_uuids(tmp_path: Path, rng: random.Random) -> Tuple[Path, Set[str]]:
    """UUID request ids; every line also names a client IP."""
    rows = []
    bad: Set[str] = set()
    for ts in _times(rng, 4000):
        rid = str(uuid.UUID(int=rng.getrandbits(128)))
        rows.append(
            (
                ts,
                f"{_iso(ts)} api INFO request {rid} from 172.20.{rng.randint(0, 3)}.{rng.randint(1, 60)} completed",
            )
        )
    for ts in _window(rng, 30):
        rid = str(uuid.UUID(int=rng.getrandbits(128)))
        bad.add(rid)
        rows.append(
            (
                ts,
                f"{_iso(ts)} api ERROR request {rid} from 172.20.{rng.randint(0, 3)}.{rng.randint(1, 60)} rejected: signature expired",
            )
        )
    return _write(tmp_path, rows), bad


def json_trace_ids(tmp_path: Path, rng: random.Random) -> Tuple[Path, Set[str]]:
    """JSON lines: hex ``trace_id`` per request, ``user_id`` shared by many."""
    users = [f"u{n}" for n in range(150)]
    rows = []
    bad: Set[str] = set()
    for ts in _times(rng, 4000):
        obj = {
            "time": _iso(ts),
            "level": "info",
            "msg": "checkout ok",
            "trace_id": f"{rng.getrandbits(64):016x}",
            "user_id": rng.choice(users),
        }
        rows.append((ts, json.dumps(obj)))
    for ts in _window(rng, 25):
        trace = f"{rng.getrandbits(64):016x}"
        bad.add(trace)
        obj = {
            "time": _iso(ts),
            "level": "error",
            "msg": "inventory lock timeout",
            "trace_id": trace,
            "user_id": rng.choice(users),
        }
        rows.append((ts, json.dumps(obj)))
    path = tmp_path / "app.jsonl"
    rows.sort()
    path.write_text("\n".join(r for _, r in rows) + "\n", encoding="utf-8")
    return path, bad


def session_tokens(tmp_path: Path, rng: random.Random) -> Tuple[Path, Set[str]]:
    """``session=<hex>`` tokens next to ``user=`` names on every line."""
    rows = []
    bad: Set[str] = set()
    for ts in _times(rng, 3000):
        rows.append(
            (
                ts,
                f"{_iso(ts)} auth INFO user=staff{rng.randint(1, 40)} session={rng.getrandbits(40):010x} refreshed",
            )
        )
    for ts in _window(rng, 20):
        sid = f"{rng.getrandbits(40):010x}"
        bad.add(sid)
        rows.append(
            (
                ts,
                f"{_iso(ts)} auth WARN user=staff{rng.randint(1, 40)} session={sid} token replay detected",
            )
        )
    return _write(tmp_path, rows), bad


def backend_ip_port(tmp_path: Path, rng: random.Random) -> Tuple[Path, Set[str]]:
    """One ``ip:port`` backend of eight starts refusing connections."""
    backends = [f"10.9.1.{i}:6379" for i in range(1, 9)]
    rows = []
    for ts in _times(rng, 4000):
        rows.append(
            (
                ts,
                f"{_iso(ts)} cache INFO get ok via {rng.choice(backends)} in {rng.randint(1, 9)} ms",
            )
        )
    for ts in _window(rng, 60):
        rows.append((ts, f"{_iso(ts)} cache ERROR connect to {backends[5]} refused"))
    return _write(tmp_path, rows), {backends[5]}


STYLES = {
    "chunk_ids": (chunk_ids, "blk_N"),
    "request_uuids": (request_uuids, "uuid"),
    "json_trace_ids": (json_trace_ids, "trace_id="),
    "session_tokens": (session_tokens, "session="),
    "backend_ip_port": (backend_ip_port, "ip:port"),
}


@pytest.mark.parametrize("style", sorted(STYLES))
def test_the_id_kind_is_learned_and_the_list_is_precise(style, tmp_path):
    generate, kind = STYLES[style]
    path, truth = generate(tmp_path, random.Random(42))
    res = lt.triage([str(path)])
    ent = res.compact()["entities"]
    assert ent["mode"] == "auto-detected"
    assert [k["kind"] for k in ent["kinds_used"]] == [kind], ent
    flagged = set(res.entities["flagged"])
    precision = len(flagged & truth) / max(1, len(flagged))
    recall = len(flagged & truth) / len(truth)
    assert precision >= 0.9 and recall >= 0.9, (precision, recall, sorted(flagged)[:5])
    assert ent["share"] < 0.3
    assert sorted(flagged) == ent["list"]


def test_shared_tokens_are_skipped_with_a_reason(tmp_path):
    path, _ = chunk_ids(tmp_path, random.Random(1))
    ent = lt.triage([str(path)]).compact()["entities"]
    skipped = {k["kind"]: k["why"] for k in ent["kinds_skipped"]}
    assert "ip:port" in skipped and "routine templates" in skipped["ip:port"]


def test_the_reported_regex_reproduces_the_list(tmp_path):
    path, _ = chunk_ids(tmp_path, random.Random(2))
    auto = lt.triage([str(path)])
    regex = auto.compact()["entities"]["kinds_used"][0]["regex"]
    assert regex == r"(blk_-?\d+)"
    pinned = lt.triage([str(path)], lt.TriageOptions(entity_regex=regex))
    assert set(pinned.entities["flagged"]) == set(auto.entities["flagged"])
    assert pinned.compact()["entities"]["mode"] == "entity_regex"


def test_the_list_is_written_when_output_path_is_given(tmp_path):
    path, truth = session_tokens(tmp_path, random.Random(3))
    out = lt.triage_logs(paths=[str(path)], output_path=str(tmp_path / "bad.txt"))
    assert out["output"]["written"], out["output"]
    assert set((tmp_path / "bad.txt").read_text(encoding="utf-8").split()) == set(
        out["entities"]["list"]
    )
    out = lt.triage_logs(paths=[str(path)], output_path=str(tmp_path / "bad.csv"))
    header = (tmp_path / "bad.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith("entity,kind,templates")


def test_a_long_list_points_at_output_path(tmp_path, monkeypatch):
    monkeypatch.setattr(lt, "OUT_ENTITY_LIST", 10)
    path, _ = chunk_ids(tmp_path, random.Random(4))
    ent = lt.triage([str(path)]).compact()["entities"]
    assert "list" not in ent and "output_path" in ent["full_list"]
    assert len(ent["sample"]) == lt.OUT_ENTITIES


def test_the_timeline_carries_the_exact_burst_bounds(tmp_path):
    path, _ = backend_ip_port(tmp_path, random.Random(5))
    res = lt.triage([str(path)])
    timeline = res.compact()["timeline"]
    burst = res.compact()["bursts"][0]
    events = {e["event"]: e["ts"] for e in timeline}
    assert events["burst 1 starts"] == burst["start"]
    assert events["burst 1 ends"] == burst["end"]
