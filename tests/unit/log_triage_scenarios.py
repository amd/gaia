# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Invented log scenarios with planted ground truth, and a scorer.

Every scenario is generated from a seed: steady background traffic (some of
it ERROR and FATAL on purpose) plus planted incidents whose lines, burst
windows and entity ids are recorded as they are written. Nothing here comes
from a real system or a public dataset.

The scorer compares what ``triage_logs`` reports with what was planted:

- **Template precision** - share of reported anomaly templates whose first,
  peak or last occurrence is a planted line.
- **Template recall** - share of planted incident kinds that at least one
  reported anomaly template points at.
- **Bursts** - a planted burst is found when a reported burst starts and ends
  within the tolerance of it.
- **Entities** - precision and recall of the flagged entity list.
"""

from __future__ import annotations

import importlib.util
import json
import os
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_PATH = REPO_ROOT / "hub" / "skills" / "log-triage" / "tools.py"
_MODULE_NAME = "starter_log_triage_tools"


def load_tools():
    """Import the skill's ``tools.py`` directly, as the loader would."""
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, TOOLS_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def bound(agent: object = None):
    """Run the engine as the agent would: inside a bound skill tool call.

    The default host has no path validator, which the scope checks treat as
    library use with no sandbox declared; pass an object with a
    ``path_validator`` to exercise the real allowed-folders checks.
    """
    from gaia.skills import context as skill_context

    skill = SimpleNamespace(name="log-triage", parsed_permissions=lambda: [])
    return skill_context.bind(agent if agent is not None else SimpleNamespace(), skill)


# ---------------------------------------------------------------------------
# Building files
# ---------------------------------------------------------------------------


@dataclass
class Event:
    ts: float  # true UTC epoch
    file: str
    text: str  # may contain "\n" for multi-line entries
    kind: Optional[str] = None  # planted incident kind, None for background
    entities: Tuple[str, ...] = ()


@dataclass
class Scenario:
    name: str
    files: List[Path]
    planted: Dict[str, Set[Tuple[str, int]]]
    bursts: List[Tuple[float, float]]
    entities: Set[str]
    score_bursts: bool = True
    negative: bool = False
    options: dict = field(default_factory=dict)


def _write(
    tmp: Path, events: List[Event]
) -> Tuple[List[Path], Dict[str, Set[Tuple[str, int]]]]:
    by_file: Dict[str, List[Event]] = {}
    for ev in events:
        by_file.setdefault(ev.file, []).append(ev)
    planted: Dict[str, Set[Tuple[str, int]]] = {}
    paths = []
    for name, evs in sorted(by_file.items()):
        evs.sort(key=lambda e: e.ts)
        path = tmp / name
        line = 0
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            for ev in evs:
                parts = ev.text.split("\n")
                line += 1
                if ev.kind:
                    planted.setdefault(ev.kind, set()).add((name, line))
                line += len(parts) - 1
                f.write("\n".join(parts) + "\n")
        last = max(e.ts for e in evs)
        os.utime(path, (last, last))
        paths.append(path)
    return paths, planted


def _poisson_times(
    rng: random.Random, start: float, end: float, per_hour: float
) -> List[float]:
    out = []
    t = start
    rate = per_hour / 3600.0
    while True:
        t += rng.expovariate(rate)
        if t >= end:
            return out
        out.append(t)


def _iso_ms(
    ts: float, sep: str = " ", comma: bool = True, tz: Optional[int] = None
) -> str:
    offset = timezone(timedelta(seconds=tz)) if tz is not None else timezone.utc
    dt = datetime.fromtimestamp(ts, tz=offset)
    ms = f"{int(dt.microsecond / 1000):03d}"
    text = dt.strftime(f"%Y-%m-%d{sep}%H:%M:%S") + ("," if comma else ".") + ms
    if tz is not None:
        text += dt.strftime("%z")[:3] + ":" + dt.strftime("%z")[3:]
    return text


def _hex(rng: random.Random, n: int = 8) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


def _year_start() -> float:
    """Logs are dated in the current year, March, so a yearless format resolves."""
    year = datetime.now(timezone.utc).year
    return datetime(year, 3, 4, 0, 0, 0, tzinfo=timezone.utc).timestamp()


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def hardware_fault_burst(tmp: Path, seed: int = 1) -> Scenario:
    """Rare fault on most nodes for 4 minutes amid a heavy FATAL/ERROR baseline."""
    rng = random.Random(seed)
    t0 = _year_start()
    t1 = t0 + 3.5 * 86400
    nodes = [f"node-{i:02d}" for i in range(1, 41)]
    events: List[Event] = []
    jobs = iter(range(10000, 99999))

    def line(ts, node, level, msg):
        return f"{_iso_ms(ts)} {node} {level} {msg}"

    for ts in _poisson_times(rng, t0, t1, 400):  # routine FATAL, ~10/5min
        node = rng.choice(nodes)
        events.append(
            Event(
                ts,
                "cluster.log",
                line(
                    ts,
                    node,
                    "FATAL",
                    f"rts: kernel terminated for reason {rng.randint(1000, 1100)}",
                ),
            )
        )
    for ts in _poisson_times(rng, t0, t1, 300):
        node = rng.choice(nodes)
        events.append(
            Event(
                ts,
                "cluster.log",
                line(
                    ts,
                    node,
                    "ERROR",
                    f"request timeout after {rng.randint(3000, 9000)} ms to storage-{rng.randint(1, 4)}",
                ),
            )
        )
    for ts in _poisson_times(rng, t0, t1, 250):
        node = rng.choice(nodes)
        events.append(
            Event(
                ts,
                "cluster.log",
                line(ts, node, "WARN", f"cache miss for key 0x{_hex(rng)}"),
            )
        )
    for ts in _poisson_times(rng, t0, t1, 200):
        node = rng.choice(nodes)
        events.append(
            Event(
                ts,
                "cluster.log",
                line(
                    ts,
                    node,
                    "INFO",
                    f"job job-{next(jobs)} started on {node} partition {rng.randint(0, 7)}",
                ),
            )
        )
    for ts in _poisson_times(rng, t0, t1, 80):
        node = rng.choice(nodes)
        events.append(
            Event(
                ts,
                "cluster.log",
                line(ts, node, "INFO", f"heartbeat ok load={rng.random():.2f}"),
            )
        )
    # Sporadic, spread-out errors: intermittent, never an incident.
    for ts in _poisson_times(rng, t0, t1, 0.5):
        node = rng.choice(nodes)
        events.append(
            Event(
                ts,
                "cluster.log",
                line(
                    ts,
                    node,
                    "ERROR",
                    f"ciod: failed to read message prefix on control stream {rng.randint(1, 99)}",
                ),
            )
        )

    fault_start = t0 + 2.4 * 86400 + 777.123
    hit = rng.sample(nodes, 31)
    first = last = None
    entities: Set[str] = set()
    for k in range(120):
        ts = fault_start + rng.uniform(0, 238)
        node = hit[k % len(hit)]
        msg = f"machine check interrupt: uncorrectable ECC error on DIMM {rng.randint(0, 15)} bank {rng.randint(0, 7)}"
        events.append(
            Event(ts, "cluster.log", line(ts, node, "FATAL", msg), kind="ecc_fault")
        )
        first = ts if first is None else min(first, ts)
        last = ts if last is None else max(last, ts)
    for node in hit:
        ts = fault_start + rng.uniform(5, 240)
        job = f"job-{next(jobs)}"
        events.append(
            Event(
                ts,
                "cluster.log",
                line(ts, node, "ERROR", f"{job} aborted: lost {node} during run"),
                kind="job_aborted",
            )
        )
        entities |= {job, node}
        last = max(last, ts)
    paths, planted = _write(tmp, events)
    return Scenario("hardware_fault_burst", paths, planted, [(first, last)], entities)


def memory_leak(tmp: Path, seed: int = 2) -> Scenario:
    """One service slowly runs out of heap: GC warnings escalate, then OOM."""
    rng = random.Random(seed)
    t0 = _year_start()
    t1 = t0 + 24 * 3600
    services = {
        "orders": "OrderService",
        "payments": "PaymentService",
        "catalog": "CatalogService",
    }
    events: List[Event] = []

    def line(ts, level, thread, cls, msg):
        return f"{_iso_ms(ts, sep='T', comma=False)}Z [{thread}] {level} com.shop.{cls} - {msg}"

    for svc, cls in services.items():
        f = f"{svc}.log"
        for ts in _poisson_times(rng, t0, t1, 600):
            th = f"http-nio-8080-exec-{rng.randint(1, 20)}"
            events.append(
                Event(
                    ts,
                    f,
                    line(
                        ts,
                        "INFO",
                        th,
                        cls,
                        f"Handled GET /api/{svc}/{rng.randint(1, 9999)} in {rng.randint(2, 90)} ms",
                    ),
                )
            )
        for ts in _poisson_times(rng, t0, t1, 30):
            events.append(
                Event(
                    ts,
                    f,
                    line(
                        ts,
                        "WARN",
                        "db-pool-1",
                        cls,
                        f"Slow query took {rng.randint(500, 3000)} ms",
                    ),
                )
            )
        for ts in _poisson_times(rng, t0, t1, 60):
            events.append(
                Event(
                    ts,
                    f,
                    line(
                        ts,
                        "INFO",
                        "metrics",
                        cls,
                        f"heap usage {rng.randint(30, 60)}% of 4096 MB",
                    ),
                )
            )
    for ts in _poisson_times(rng, t0, t1, 40):
        oid = f"order-{rng.randint(100000, 999999)}"
        events.append(
            Event(
                ts,
                "payments.log",
                line(
                    ts,
                    "ERROR",
                    "http-nio-8080-exec-3",
                    "PaymentService",
                    f"Payment declined for {oid}: insufficient funds",
                ),
            )
        )

    leak_start = t0 + 0.45 * (t1 - t0)
    oom_at = t0 + 0.96 * (t1 - t0)
    t = leak_start
    while t < oom_at:
        frac = (t - leak_start) / (oom_at - leak_start)
        rate_per_hour = 2 + 200 * frac**3
        t += rng.expovariate(rate_per_hour / 3600.0)
        if t >= oom_at:
            break
        events.append(
            Event(
                t,
                "orders.log",
                line(
                    t,
                    "WARN",
                    "gc-monitor",
                    "OrderService",
                    f"GC overhead high: pause {int(200 + 3000 * frac)} ms, old gen {int(70 + 29 * frac)}% full",
                ),
                kind="gc_escalation",
            )
        )
    entities: Set[str] = set()
    for k in range(15):
        ts = oom_at + k * 6 + rng.uniform(0, 3)
        req = f"req-{_hex(rng)}"
        trace = (
            line(
                ts,
                "ERROR",
                f"http-nio-8080-exec-{rng.randint(1, 20)}",
                "OrderService",
                f"Request {req} failed",
            )
            + "\njava.lang.OutOfMemoryError: Java heap space"
            + "\n\tat java.util.Arrays.copyOf(Arrays.java:3236)"
            + "\n\tat com.shop.OrderService.load(OrderService.java:212)"
            + "\n\t... 42 more"
        )
        events.append(Event(ts, "orders.log", trace, kind="oom"))
        entities.add(req)
    ts = oom_at + 100
    events.append(
        Event(
            ts,
            "orders.log",
            line(
                ts,
                "INFO",
                "main",
                "OrderService",
                "Service restarting after fatal error",
            ),
        )
    )
    paths, planted = _write(tmp, events)
    return Scenario("memory_leak", paths, planted, [], entities, score_bursts=False)


def single_vs_cluster(tmp: Path, seed: int = 3) -> Scenario:
    """Syslog: one host's disk fails (1 host), later the whole fleet loses its DB."""
    rng = random.Random(seed)
    t0 = _year_start()
    t1 = t0 + 2 * 86400
    hosts = [f"web-{i:02d}" for i in range(1, 21)]
    events: List[Event] = []

    def line(ts, host, tag, msg):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return f"{dt.strftime('%b')} {dt.day:2d} {dt.strftime('%H:%M:%S')} {host} {tag}: {msg}"

    sessions = iter(range(0x100000, 0xFFFFFF, 7))
    for ts in _poisson_times(rng, t0, t1, 400):
        h = rng.choice(hosts)
        events.append(
            Event(
                ts,
                "messages",
                line(
                    ts,
                    h,
                    f"app[{rng.randint(1000, 1100)}]",
                    f"session sess-{next(sessions):x} opened for user u{rng.randint(1, 500)}",
                ),
            )
        )
    for ts in _poisson_times(rng, t0, t1, 120):
        h = rng.choice(hosts)
        events.append(
            Event(
                ts,
                "messages",
                line(
                    ts,
                    h,
                    f"sshd[{rng.randint(2000, 9000)}]",
                    f"Accepted publickey for deploy from 10.2.{rng.randint(0, 255)}.{rng.randint(1, 254)} port {rng.randint(30000, 60000)} ssh2",
                ),
            )
        )
    for ts in _poisson_times(rng, t0, t1, 60):
        h = rng.choice(hosts)
        events.append(
            Event(
                ts,
                "messages",
                line(
                    ts,
                    h,
                    f"CRON[{rng.randint(100, 999)}]",
                    "(root) CMD (run-parts /etc/cron.hourly)",
                ),
            )
        )
    for ts in _poisson_times(rng, t0, t1, 30):
        h = rng.choice(hosts)
        events.append(
            Event(
                ts,
                "messages",
                line(
                    ts,
                    h,
                    f"app[{rng.randint(1000, 1100)}]",
                    f"error: payment gateway returned code {rng.choice([402, 409])} for session sess-{next(sessions):x}",
                ),
            )
        )

    entities: Set[str] = set()
    a0 = t0 + 0.3 * (t1 - t0)
    a_first = a_last = None
    for _ in range(40):
        ts = a0 + rng.uniform(0, 600)
        events.append(
            Event(
                ts,
                "messages",
                line(
                    ts,
                    "web-07",
                    "kernel",
                    f"EXT4-fs error (device sdb1): ext4_find_entry:{rng.randint(1400, 1500)}: inode #{rng.randint(2, 9999)}: comm nginx: reading directory lblock 0",
                ),
                kind="disk_single_host",
            )
        )
        a_first = ts if a_first is None else min(a_first, ts)
        a_last = ts if a_last is None else max(a_last, ts)
    for _ in range(12):
        ts = a0 + rng.uniform(30, 600)
        s = f"sess-{next(sessions):x}"
        events.append(
            Event(
                ts,
                "messages",
                line(
                    ts,
                    "web-07",
                    f"app[{rng.randint(1000, 1100)}]",
                    f"session {s} aborted: I/O error writing spool",
                ),
                kind="disk_single_host",
            )
        )
        entities.add(s)
        a_last = max(a_last, ts)
    b0 = t0 + 0.7 * (t1 - t0)
    b_first = b_last = None
    for h in hosts:
        for _ in range(3):
            ts = b0 + rng.uniform(0, 180)
            s = f"sess-{next(sessions):x}"
            events.append(
                Event(
                    ts,
                    "messages",
                    line(
                        ts,
                        h,
                        f"app[{rng.randint(1000, 1100)}]",
                        f"upstream db-01 unreachable, failing session {s}",
                    ),
                    kind="db_cluster",
                )
            )
            entities.add(s)
            b_first = ts if b_first is None else min(b_first, ts)
            b_last = ts if b_last is None else max(b_last, ts)
    paths, planted = _write(tmp, events)
    return Scenario(
        "single_vs_cluster",
        paths,
        planted,
        [(a_first, a_last), (b_first, b_last)],
        entities,
        options={"entity_regex": r"(sess-[0-9a-f]+)"},
    )


def access_5xx_spike(tmp: Path, seed: int = 4) -> Scenario:
    """Combined access log: one upstream returns 502 for 15 minutes."""
    rng = random.Random(seed)
    t0 = _year_start()
    t1 = t0 + 86400
    ups = [f"10.0.1.{i}:8080" for i in range(1, 7)]
    bad = ups[3]
    endpoints = [
        ("GET", "/api/products/{n}"),
        ("GET", "/api/cart"),
        ("POST", "/api/checkout"),
        ("GET", "/static/app.{n}.js"),
        ("GET", "/api/users/{n}/orders"),
    ]
    events: List[Event] = []
    s0 = t0 + 0.55 * 86400
    s1 = s0 + 900
    first = last = None

    def line(ts, ip, method, path, status, up):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return (
            f'{ip} - - [{dt.strftime("%d/%b/%Y:%H:%M:%S")} +0000] "{method} {path} HTTP/1.1" {status} '
            f'{rng.randint(200, 9000)} "-" "Mozilla/5.0 (X11)" upstream={up} rt={rng.random():.3f}'
        )

    for ts in _poisson_times(rng, t0, t1, 3000):
        method, path = rng.choice(endpoints)
        path = path.format(n=rng.randint(1, 5000))
        up = rng.choice(ups)
        ip = f"203.0.113.{rng.randint(1, 254)}"
        status = 200 if rng.random() > 0.03 else 404
        kind = None
        if s0 <= ts <= s1 and up == bad:
            status, kind = 502, "upstream_502"
            first = ts if first is None else min(first, ts)
            last = ts if last is None else max(last, ts)
        events.append(
            Event(ts, "access.log", line(ts, ip, method, path, status, up), kind=kind)
        )
    for ts in _poisson_times(rng, t0, t1, 3):  # sporadic, spread-out 500s
        events.append(
            Event(
                ts,
                "access.log",
                line(
                    ts,
                    f"198.51.100.{rng.randint(1, 254)}",
                    "POST",
                    "/api/checkout",
                    500,
                    rng.choice(ups),
                ),
            )
        )
    paths, planted = _write(tmp, events)
    return Scenario("access_5xx_spike", paths, planted, [(first, last)], {bad})


def json_deploy_exception(tmp: Path, seed: int = 5) -> Scenario:
    """JSON lines: after a deploy a new exception appears on every host."""
    rng = random.Random(seed)
    t0 = _year_start()
    t1 = t0 + 12 * 3600
    hosts = [f"api-{i}" for i in range(1, 5)]
    events: List[Event] = []
    deploy = t0 + 0.6 * (t1 - t0)

    def rec(ts, level, host, msg, **extra):
        obj = {
            "ts": _iso_ms(ts, sep="T", comma=False) + "Z",
            "level": level,
            "service": "api",
            "host": host,
            "msg": msg,
        }
        obj.update(extra)
        return json.dumps(obj)

    for ts in _poisson_times(rng, t0, t1, 1500):
        events.append(
            Event(
                ts,
                "app.jsonl",
                rec(
                    ts,
                    "info",
                    rng.choice(hosts),
                    f"request completed in {rng.randint(3, 300)} ms",
                    request_id=_hex(rng, 12),
                    status=200,
                ),
            )
        )
    for ts in _poisson_times(rng, t0, t1, 40):
        events.append(
            Event(
                ts,
                "app.jsonl",
                rec(
                    ts,
                    "warn",
                    rng.choice(hosts),
                    f"retrying upstream call attempt {rng.randint(1, 3)}",
                    request_id=_hex(rng, 12),
                ),
            )
        )
    for ts in _poisson_times(rng, t0, t1, 25):
        events.append(
            Event(
                ts,
                "app.jsonl",
                rec(
                    ts,
                    "error",
                    rng.choice(hosts),
                    "validation failed for field email",
                    request_id=_hex(rng, 12),
                ),
            )
        )
    for i, h in enumerate(hosts):
        ts = deploy + i * 20
        events.append(
            Event(
                ts,
                "app.jsonl",
                rec(ts, "info", h, "deploy finished version 2.4.1"),
                kind="deploy",
            )
        )
    entities: Set[str] = set()
    for ts in _poisson_times(rng, deploy + 120, t1, 50):
        rid = _hex(rng, 12)
        events.append(
            Event(
                ts,
                "app.jsonl",
                rec(
                    ts,
                    "error",
                    rng.choice(hosts),
                    "unhandled exception in checkout handler",
                    request_id=rid,
                    error={"type": "KeyError", "message": "'currency'"},
                ),
                kind="new_exception",
            )
        )
        entities.add(rid)
    paths, planted = _write(tmp, events)
    return Scenario(
        "json_deploy_exception", paths, planted, [], entities, score_bursts=False
    )


def steady_negative(tmp: Path, seed: int = 6) -> Scenario:
    """Only steady background, including routine ERROR/FATAL and rare chatter."""
    rng = random.Random(seed)
    t0 = _year_start()
    t1 = t0 + 2 * 86400
    hosts = [f"svc-{i:02d}" for i in range(1, 13)]
    events: List[Event] = []
    templates = [
        (500, "INFO", "processed batch batch-{n} with {m} records"),
        (200, "INFO", "user u{m} logged in from 192.168.{a}.{b}"),
        (120, "ERROR", "connection reset by peer while reading from 10.9.{a}.{b}:5432"),
        (60, "FATAL", "worker {m} exited with status 137, supervisor restarting it"),
        (40, "WARN", "queue depth {m} above soft limit"),
        (3, "WARN", "certificate for internal-{a}.example.test expires in {m} days"),
        (2, "INFO", "configuration reloaded by operator"),
        (1, "ERROR", "nightly export skipped: destination busy"),
    ]
    for per_hour, level, tpl in templates:
        for ts in _poisson_times(rng, t0, t1, per_hour):
            msg = tpl.format(
                n=rng.randint(1, 10**6),
                m=rng.randint(1, 999),
                a=rng.randint(0, 255),
                b=rng.randint(1, 254),
            )
            events.append(
                Event(
                    ts,
                    "service.log",
                    f"{_iso_ms(ts)} {rng.choice(hosts)} {level} {msg}",
                )
            )
    paths, planted = _write(tmp, events)
    return Scenario("steady_negative", paths, planted, [], set(), negative=True)


def multi_file_timezones(tmp: Path, seed: int = 7) -> Scenario:
    """Three files, three clock conventions (+02:00, Z, epoch ms), one has 40 s skew."""
    rng = random.Random(seed)
    t0 = _year_start()
    t1 = t0 + 8 * 3600
    events: List[Event] = []
    skew = 40.0

    def gw(ts, level, msg):
        return f"{_iso_ms(ts, sep='T', comma=False, tz=7200)} gw-1 {level} {msg}"

    def db(ts, level, msg):
        return f"{_iso_ms(ts, sep='T', comma=False)}Z db-1 {level} {msg}"

    def wk(ts, level, msg):
        return f"{int((ts + skew) * 1000)} {level} {msg}"

    for ts in _poisson_times(rng, t0, t1, 900):
        events.append(
            Event(
                ts,
                "gateway.log",
                gw(
                    ts,
                    "INFO",
                    f"proxied GET /v1/items/{rng.randint(1, 999)} in {rng.randint(1, 80)} ms",
                ),
            )
        )
    for ts in _poisson_times(rng, t0, t1, 20):
        events.append(
            Event(
                ts,
                "gateway.log",
                gw(
                    ts,
                    "ERROR",
                    f"client 172.16.{rng.randint(0, 255)}.{rng.randint(1, 254)} closed connection early",
                ),
            )
        )
    for ts in _poisson_times(rng, t0, t1, 600):
        events.append(
            Event(
                ts,
                "db.log",
                db(
                    ts,
                    "INFO",
                    f"checkpoint complete: wrote {rng.randint(10, 900)} buffers",
                ),
            )
        )
    for ts in _poisson_times(rng, t0, t1, 15):
        events.append(
            Event(
                ts,
                "db.log",
                db(
                    ts,
                    "WARN",
                    f"autovacuum took {rng.randint(2, 30)} s on table events",
                ),
            )
        )
    for ts in _poisson_times(rng, t0, t1, 400):
        events.append(
            Event(
                ts,
                "worker.log",
                wk(
                    ts,
                    "INFO",
                    f"job {rng.randint(1, 99999)} done in {rng.randint(10, 900)} ms",
                ),
            )
        )

    s0 = t0 + 0.5 * (t1 - t0) + 13.0
    entities: Set[str] = set()
    first = last = None
    for _ in range(25):
        txn = f"txn-{rng.randint(10**6, 10**7)}"
        ts = s0 + rng.uniform(0, 120)
        events.append(
            Event(
                ts,
                "db.log",
                db(ts, "ERROR", f"deadlock detected, {txn} rolled back"),
                kind="deadlock",
            )
        )
        ts2 = ts + rng.uniform(0.5, 3)
        events.append(
            Event(
                ts2,
                "gateway.log",
                gw(ts2, "ERROR", f"upstream db timeout for {txn}"),
                kind="gw_timeout",
            )
        )
        ts3 = ts + rng.uniform(1, 5)
        events.append(
            Event(
                ts3,
                "worker.log",
                wk(ts3, "ERROR", f"job failed after rollback of {txn}"),
                kind="worker_failed",
            )
        )
        entities.add(txn)
        first = ts if first is None else min(first, ts)
        last = max(last or 0, ts2, ts3 + skew)
    paths, planted = _write(tmp, events)
    return Scenario("multi_file_timezones", paths, planted, [(first, last)], entities)


def diurnal_negative(tmp: Path, seed: int = 8) -> Scenario:
    """Access log with a day/night traffic cycle, steady 5xx and a long tail.

    Rates swing 4x between night and day, a few 500s and 404s happen all the
    time, and dozens of rarely requested paths each appear a handful of times.
    None of it is an incident.
    """
    rng = random.Random(seed)
    t0 = _year_start()
    t1 = t0 + 3 * 86400
    events: List[Event] = []

    def line(ts, path, status, method="GET"):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        ip = f"192.0.2.{rng.randint(1, 254)}"
        return (
            f'{ip} - - [{dt.strftime("%d/%b/%Y:%H:%M:%S")} +0000] "{method} {path} HTTP/1.1" {status} '
            f'{rng.randint(100, 5000)} "-" "agent/{rng.randint(1, 9)}"'
        )

    import math

    t = t0
    peak_per_hour = 2400.0
    while t < t1:
        hour = (t - t0) / 3600.0 % 24
        rate = peak_per_hour * (
            0.25 + 0.75 * (0.5 - 0.5 * math.cos(2 * math.pi * (hour - 3) / 24))
        )
        t += rng.expovariate(rate / 3600.0)
        r = rng.random()
        if r < 0.004:
            events.append(Event(t, "access.log", line(t, "/api/search", 500)))
        elif r < 0.03:
            events.append(
                Event(t, "access.log", line(t, f"/img/{rng.randint(1, 900)}.png", 404))
            )
        else:
            events.append(
                Event(
                    t,
                    "access.log",
                    line(
                        t,
                        rng.choice(
                            ["/", "/api/feed", f"/api/item/{rng.randint(1, 10**5)}"]
                        ),
                        200,
                    ),
                )
            )
    rare_paths = [
        f"/help/{word}"
        for word in (
            "about",
            "terms",
            "privacy",
            "contact",
            "careers",
            "press",
            "faq",
            "status",
            "legal",
            "sitemap",
            "brand",
            "team",
        )
    ]
    for p in rare_paths:
        for _ in range(rng.randint(1, 5)):
            ts = rng.uniform(t0, t1)
            events.append(Event(ts, "access.log", line(ts, p, 200)))
    paths, planted = _write(tmp, events)
    return Scenario("diurnal_negative", paths, planted, [], set(), negative=True)


SCENARIOS: Dict[str, Callable[[Path], Scenario]] = {
    "diurnal_negative": diurnal_negative,
    "hardware_fault_burst": hardware_fault_burst,
    "memory_leak": memory_leak,
    "single_vs_cluster": single_vs_cluster,
    "access_5xx_spike": access_5xx_spike,
    "json_deploy_exception": json_deploy_exception,
    "steady_negative": steady_negative,
    "multi_file_timezones": multi_file_timezones,
}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass
class Score:
    name: str
    reported: int
    template_precision: float
    template_recall: float
    bursts_found: Optional[float]
    burst_precision: Optional[float]
    entity_precision: float
    entity_recall: float
    false_templates: List[str]
    missed_kinds: List[str]
    burst_errors_s: List[Tuple[float, float]]

    def row(self) -> str:
        def f(x):
            return "  - " if x is None else f"{x:4.2f}"

        return (
            f"{self.name:24s} anomalies={self.reported:3d} tplP={f(self.template_precision)} "
            f"tplR={f(self.template_recall)} burstR={f(self.bursts_found)} burstP={f(self.burst_precision)} "
            f"entP={f(self.entity_precision)} entR={f(self.entity_recall)}"
        )


def score(sc: Scenario, result, tolerance_s: float = 60.0) -> Score:
    files = result.an.files
    planted_all = set().union(*sc.planted.values()) if sc.planted else set()
    tp = 0
    false_templates = []
    covered: Set[str] = set()
    for t in result.anomalies:
        locs = {t.first_loc, t.last_loc, t.m.get("peak_loc")}
        named = {(files[loc[0]].name, loc[1]) for loc in locs if loc}
        if named & planted_all:
            tp += 1
            for kind, lines in sc.planted.items():
                if named & lines:
                    covered.add(kind)
        else:
            false_templates.append(t.key)
    reported = len(result.anomalies)
    if sc.negative:
        precision = 1.0 if reported == 0 else 0.0
        recall = 1.0
    else:
        precision = tp / reported if reported else 0.0
        recall = len(covered) / len(sc.planted) if sc.planted else 1.0
    bursts_found = burst_precision = None
    errors: List[Tuple[float, float]] = []
    if sc.score_bursts or sc.negative:
        matched_reported = set()
        found = 0
        for start, end in sc.bursts:
            best = None
            for i, b in enumerate(result.bursts):
                ds, de = abs(b["start"] - start), abs(b["end"] - end)
                if (
                    ds <= tolerance_s
                    and de <= tolerance_s
                    and (best is None or ds + de < best[1])
                ):
                    best = (i, ds + de, ds, de)
            if best:
                found += 1
                matched_reported.add(best[0])
                errors.append((round(best[2], 3), round(best[3], 3)))
        bursts_found = (
            found / len(sc.bursts) if sc.bursts else (1.0 if not result.bursts else 0.0)
        )
        burst_precision = (
            (len(matched_reported) / len(result.bursts)) if result.bursts else 1.0
        )
    flagged = set(result.entities["flagged"])
    if sc.entities:
        ent_p = len(flagged & sc.entities) / len(flagged) if flagged else 0.0
        ent_r = len(flagged & sc.entities) / len(sc.entities)
    else:
        ent_p = ent_r = 1.0 if not flagged else 0.0
    return Score(
        sc.name,
        reported,
        precision,
        recall,
        bursts_found,
        burst_precision,
        ent_p,
        ent_r,
        false_templates,
        sorted(set(sc.planted) - covered),
        errors,
    )


def run(sc: Scenario):
    tools = load_tools()
    opts = tools.TriageOptions(**sc.options)
    with bound():
        return tools.triage([str(p) for p in sc.files], opts)


if __name__ == "__main__":  # pragma: no cover - manual report
    import tempfile

    for name, gen in SCENARIOS.items():
        with tempfile.TemporaryDirectory() as d:
            sc = gen(Path(d))
            res = run(sc)
            s = score(sc, res)
            print(s.row())
            for ft in s.false_templates[:5]:
                print("   FP:", ft[:120])
            if s.missed_kinds:
                print("   missed:", s.missed_kinds)
            if s.burst_errors_s:
                print("   burst errors (s):", s.burst_errors_s)
