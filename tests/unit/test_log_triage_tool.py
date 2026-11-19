# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the ``log-triage`` skill's ``triage_logs`` tool.

Each parsing and analysis stage is tested on small invented logs: timestamp
layouts and time zones, format detection, multi-line entries, malformed and
empty input, encodings, templating, classification, bursts, entities, the
output file rules and the skill's registration.
"""

from __future__ import annotations

import gzip
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from gaia.security import PathValidator
from tests.unit.log_triage_scenarios import TOOLS_PATH, bound, load_engine, load_tools

lt = load_tools()
engine = load_engine()


@pytest.fixture(autouse=True)
def _skill_call():
    """Every engine call runs inside a bound skill call, as under the agent."""
    with bound():
        yield


def _scoped(*allowed: Path):
    """A host whose allowed folders are *allowed*; out-of-scope asks get "no"."""
    validator = PathValidator(allowed_paths=[str(a) for a in allowed])
    validator.set_access_prompt(lambda path: False)
    return bound(SimpleNamespace(path_validator=validator))


T0 = datetime(2025, 3, 4, 0, 0, 0, tzinfo=timezone.utc).timestamp()


def _iso(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S") + f",{int(dt.microsecond / 1000):03d}"


def _write(path: Path, lines, newline: str = "\n", encoding: str = "utf-8") -> Path:
    path.write_bytes((newline.join(lines) + newline).encode(encoding))
    return path


def _steady(path: Path, hours: float = 6, per_min: int = 2, extra=()) -> Path:
    """A plain ISO log with steady INFO/ERROR background and optional extras."""
    rng = random.Random(7)
    rows = []
    t = T0
    while t < T0 + hours * 3600:
        host = f"app-{rng.randint(1, 6):02d}"
        if rng.random() < 0.8:
            rows.append(
                (
                    t,
                    f"{_iso(t)} {host} INFO served request req-{rng.getrandbits(32):08x} in {rng.randint(1, 99)} ms",
                )
            )
        else:
            rows.append(
                (
                    t,
                    f"{_iso(t)} {host} ERROR cache backend timeout after {rng.randint(100, 900)} ms",
                )
            )
        t += 60.0 / per_min
    rows.extend(extra)
    rows.sort(key=lambda r: r[0])
    return _write(path, [r[1] for r in rows])


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,kind,expected,has_tz",
    [
        ("2025-03-04 10:00:00,123 x", "iso", "2025-03-04 10:00:00.123", False),
        ("2025-03-04T10:00:00.5Z x", "iso", "2025-03-04 10:00:00.500", True),
        ("2025-03-04T12:00:00+02:00 x", "iso", "2025-03-04 10:00:00", True),
        ("2025-03-04 05:00:00 -0500 x", "iso", "2025-03-04 10:00:00", True),
        ("2025/03/04 10:00:00 [error]", "iso", "2025-03-04 10:00:00", False),
        ("20250304T100000Z x", "compact", "2025-03-04 10:00:00", True),
        ("04/Mar/2025:12:00:00 +0200", "clf", "2025-03-04 10:00:00", True),
        ("04-Mar-2025 10:00:00.250 x", "dmy", "2025-03-04 10:00:00.250", False),
        (
            "Tue Mar 04 10:00:00.123456 2025] x",
            "ctime",
            "2025-03-04 10:00:00.123",
            False,
        ),
        ("Mar 4, 2025 10:00:00 PM x", "mdy_name", "2025-03-04 22:00:00", False),
        ("3/4/2025 10:00:00 AM x", "us", "2025-03-04 10:00:00", False),
        ("1741082400 x", "epoch", "2025-03-04 10:00:00", True),
        ("1741082400123 x", "epoch", "2025-03-04 10:00:00.123", True),
    ],
)
def test_timestamp_layouts(text, kind, expected, has_tz):
    tm = engine.parsing.parse_timestamp(text)
    assert tm is not None, text
    assert tm.kind == kind
    assert engine.parsing.format_ts(tm.epoch) == expected
    assert tm.has_tz is has_tz


def test_syslog_takes_the_year_it_is_given():
    tm = engine.parsing.parse_timestamp("Mar  4 10:00:00 host x", year_hint=2023)
    assert engine.parsing.format_ts(tm.epoch) == "2023-03-04 10:00:00"


def test_impossible_dates_and_out_of_range_epochs_do_not_parse():
    assert engine.parsing.parse_timestamp("2025-13-40 10:00:00 x") is None
    assert engine.parsing.parse_timestamp("1234567 x") is None
    assert engine.parsing.parse_timestamp_value(5) is None
    assert engine.parsing.parse_timestamp_value(1741082400000).epoch == pytest.approx(
        1741082400
    )


# ---------------------------------------------------------------------------
# Severity and templates
# ---------------------------------------------------------------------------


def test_levels_and_keyword_fallback():
    assert engine.parsing.level_severity("warning") == 2
    assert engine.parsing.level_severity("[ERROR]") == 3
    assert engine.parsing.level_severity("trace4") == 0
    assert (
        engine.parsing.level_severity(40) == 3
    )  # numeric levels follow Python logging: 40 ERROR
    assert engine.parsing.level_severity(60) == 4
    assert engine.parsing.level_severity("banana") is None
    assert engine.parsing.keyword_severity("all good") == 1


@pytest.mark.parametrize(
    "message,severity",
    [
        ("fatal: could not bind port 8080", 4),
        ("worker panicked while draining the queue", 4),
        ("index file corrupted, rebuilding", 4),
        ("renderer crashed with exit code 3", 4),
        ("import aborted after 3 attempts", 4),
        ("segfault in image filter", 4),
        ("out of memory while sorting", 4),
        ("OutOfMemoryError in batch job", 4),
        ("oom killer stopped the cache", 4),
        ("2 errors while saving", 3),
        ("payment failed for order 7", 3),
        ("unhandled exception in handler", 3),
        ("upstream timed out", 3),
        ("read timeout after 30 s", 3),
        ("connection refused", 3),
        ("permission denied for /srv/data", 3),
        ("host unreachable", 3),
        ("Traceback (most recent call last):", 3),
        ("warning: disk at 91%", 2),
        ("retrying in 5 s", 2),
        ("slow response from search", 2),
        ("running in degraded mode", 2),
        ("option --fast is deprecated", 2),
    ],
)
def test_keyword_severity_for_lines_without_a_level(message, severity):
    assert engine.parsing.keyword_severity(message) == severity


def test_masking_keeps_structure_and_hides_values():
    a = engine.parsing.mask_message(
        "req-a3f9c2d1 from 10.0.0.1:443 took 123 ms reading /var/lib/x/y.db 0xdeadbeef"
    )
    b = engine.parsing.mask_message(
        "req-12345678 from 10.9.8.7:80 took 7 ms reading /srv/data/z.db 0x1"
    )
    assert a == b
    assert "<IP>" in a and "<PATH>" in a and "<*>" in a
    uuid = engine.parsing.mask_message("job 123e4567-e89b-12d3-a456-426614174000 done")
    assert uuid == "job <UUID> done"
    assert (
        engine.parsing.mask_message("see https://example.test/a?b=1 now")
        == "see <URL> now"
    )


def test_high_cardinality_word_merges_into_one_template(tmp_path):
    users = [f"user{c}" for c in "abcdefghijklmnop"]
    lines = [
        f"{_iso(T0 + i * 30)} app-01 INFO login ok for {users[i % 16]} via sso"
        for i in range(400)
    ]
    res = engine.report.triage([str(_write(tmp_path / "a.log", lines))])
    assert len(res.an.tpls) == 1
    assert next(iter(res.an.tpls)) == "login ok for <*> via sso"


def test_template_cap_pools_the_rest_and_says_so(tmp_path):
    lines = [
        f"{_iso(T0 + i)} h-1 INFO event{chr(97 + i % 26)}{chr(97 + i // 26 % 26)} happened"
        for i in range(300)
    ]
    path = _write(tmp_path / "a.log", lines)
    res = engine.report.triage(
        [str(path)], engine.analysis.TriageOptions(max_templates=50)
    )
    assert len(res.an.tpls) <= 50
    assert res.an.overflow is not None
    assert any("distinct templates" in w for w in res.warnings)
    assert res.checks["ok"], res.checks


# ---------------------------------------------------------------------------
# Format detection and parsing
# ---------------------------------------------------------------------------


def _formats(tmp_path, name, lines, **opts):
    res = engine.report.triage(
        [str(_write(tmp_path / name, lines))], engine.analysis.TriageOptions(**opts)
    )
    return res, res.an.files[0]


def test_json_lines_with_nested_fields(tmp_path):
    lines = [
        json.dumps(
            {
                "@timestamp": "2025-03-04T10:00:0%dZ" % i,
                "log": {"level": "error"},
                "host": {"name": "web-1"},
                "message": f"payment failed for order {i}",
                "trace_id": f"t{i}",
            }
        )
        for i in range(5)
    ]
    res, f = _formats(tmp_path, "a.jsonl", lines)
    assert f.format == "json" and f.entries == 5 and f.unparsed == 0
    tpl = next(iter(res.an.tpls.values()))
    assert tpl.sev == 3 and "web-1" in tpl.hosts
    assert {"t0", "t4"} <= set(tpl.ents)


def test_syslog_rfc5424_and_rfc3164(tmp_path):
    lines = [
        f"<{8 * 1 + 3}>1 2025-03-04T10:00:0{i}.000Z host-{i} sshd 123 - - Failed password for root"
        for i in range(5)
    ]
    res, f = _formats(tmp_path, "a.log", lines)
    assert f.format == "syslog5424" and f.entries == 5
    tpl = next(iter(res.an.tpls.values()))
    assert tpl.sev == 3 and tpl.key.startswith("sshd: ")
    lines = [
        f"Mar  4 10:00:0{i} host-{i} CRON[12{i}]: (root) CMD (run-parts /etc/cron.hourly)"
        for i in range(5)
    ]
    res, f = _formats(tmp_path, "b.log", lines)
    assert f.format == "syslog" and f.entries == 5 and f.year_inferred
    assert any("carry no year" in w for w in res.warnings)


def test_syslog_year_rolls_over_at_new_year(tmp_path):
    lines = ["Dec 31 23:59:58 h-1 app: tick", "Jan  1 00:00:02 h-1 app: tick"]
    res, f = _formats(tmp_path, "a.log", lines)
    assert f.last - f.first == pytest.approx(4, abs=1)


def test_access_log_status_is_severity_and_path_is_templated(tmp_path):
    lines = [
        f'10.0.0.{i} - - [04/Mar/2025:10:00:0{i} +0000] "GET /api/items/{100 + i}?x=1 HTTP/1.1" '
        f'{502 if i % 2 else 200} 512 "-" "curl/8" upstream=10.1.1.{i % 2}:80'
        for i in range(6)
    ]
    res, f = _formats(tmp_path, "access.log", lines)
    assert f.format == "access" and f.entries == 6
    keys = sorted(res.an.tpls)
    assert keys == ["GET /api/items/<*> 200", "GET /api/items/<*> 502"]
    bad = res.an.tpls["GET /api/items/<*> 502"]
    assert bad.sev == 3 and set(bad.ents) == {"10.1.1.1:80"}


def test_apache_and_nginx_error_logs(tmp_path):
    lines = [
        f"[Tue Mar 04 10:00:0{i}.123456 2025] [proxy:error] [pid 42{i}] [client 10.0.0.{i}:5000] AH01114: backend down"
        for i in range(5)
    ]
    res, f = _formats(tmp_path, "error_log", lines)
    assert f.format == "apache_error" and f.entries == 5
    assert next(iter(res.an.tpls.values())).sev == 3
    lines = [
        f"2025/03/04 10:00:0{i} [crit] 12#0: *{i} connect() failed (111: Connection refused)"
        for i in range(5)
    ]
    res, f = _formats(tmp_path, "nginx_error.log", lines)
    assert f.entries == 5 and f.unparsed == 0
    assert next(iter(res.an.tpls.values())).sev == 4


def test_glog_and_logfmt(tmp_path):
    lines = [
        f"E0304 10:00:0{i}.123456    1234 server.go:{40 + i}] dial tcp: i/o timeout"
        for i in range(5)
    ]
    res, f = _formats(tmp_path, "a.log", lines)
    assert f.format == "glog" and next(iter(res.an.tpls.values())).sev == 3
    lines = [
        f'ts=2025-03-04T10:00:0{i}Z level=warn host=db-{i} msg="slow query" duration={i}s user_id=u{i}'
        for i in range(5)
    ]
    res, f = _formats(tmp_path, "b.log", lines)
    assert f.format == "logfmt" and f.entries == 5
    tpl = next(iter(res.an.tpls.values()))
    assert tpl.key == "slow query" and tpl.sev == 2 and len(tpl.hosts) == 5


def test_log4j_stack_traces_join_their_entry_and_name_the_exception(tmp_path):
    lines = []
    for i in range(4):
        lines += [
            f"2025-03-04 10:00:0{i},000 ERROR [main] com.acme.Pool - Request failed",
            "java.lang.IllegalStateException: pool exhausted",
            "\tat com.acme.Pool.get(Pool.java:42)",
            "Caused by: java.net.SocketTimeoutException: read timed out",
            "\t... 12 more",
        ]
    lines.append("2025-03-04 10:00:09,000 INFO [main] com.acme.Pool - Recovered")
    res, f = _formats(tmp_path, "app.log", lines)
    assert f.entries == 5 and f.continuation == 16 and f.unparsed == 0
    keys = list(res.an.tpls)
    assert any(k.endswith("| java.lang.IllegalStateException") for k in keys)


def test_python_traceback_is_continuation(tmp_path):
    lines = [
        "2025-03-04 10:00:00,000 ERROR worker crashed",
        "Traceback (most recent call last):",
        '  File "w.py", line 3, in <module>',
        "ValueError: bad input",
        "2025-03-04 10:00:01,000 INFO worker restarted",
    ]
    res, f = _formats(tmp_path, "a.log", lines)
    assert f.entries == 2 and f.continuation == 3 and f.unparsed == 0
    assert any("ValueError" in k for k in res.an.tpls)


def test_mixed_formats_in_one_file(tmp_path):
    lines = []
    for i in range(30):
        lines.append(f"2025-03-04T10:00:{i:02d}Z app-1 INFO plain line {i}")
        lines.append(
            json.dumps(
                {
                    "time": f"2025-03-04T10:01:{i:02d}Z",
                    "level": "error",
                    "msg": "json line",
                }
            )
        )
    res, f = _formats(tmp_path, "mixed.log", lines)
    assert f.entries == 60 and f.unparsed == 0
    assert f.secondary, "the second format should be tried on lines the first rejects"


def test_malformed_lines_are_counted_sampled_and_warned(tmp_path):
    lines = [f"2025-03-04 10:00:{i:02d},000 h INFO ok" for i in range(40)]
    lines[5] = "@@@ garbage line one"
    lines[17] = "%%% garbage line two"
    lines[30] = "garbage three"
    res, f = _formats(tmp_path, "a.log", lines)
    assert f.unparsed == 3 and f.entries == 37
    assert [n for n, _ in f.unparsed_samples] == [6, 18]
    assert res.checks["ok"]
    assert not res.checks["parsed_share_ok"]
    assert res.checks["files_below_parsed_share"] == ["a.log"]
    assert any("did not parse" in w for w in res.warnings)


def test_no_timestamps_falls_back_to_plain_and_says_so(tmp_path):
    lines = ["starting up", "ERROR could not open socket", "shutting down"] * 10
    res, f = _formats(tmp_path, "plain.txt", lines)
    assert f.format == "plain" and f.entries == 30
    assert any("no timestamp format" in w for w in res.warnings)
    assert res.checks["ok"]
    # Not ranked by time, so the caller is sent back to fix the format.
    checks = res.compact()["checks"]
    assert not checks["parsed_share_ok"] and not checks["time_ranked"]
    assert checks["files_below_parsed_share"] == ["plain.txt"]
    assert checks["files_without_timestamps"] == ["plain.txt"]
    row = res.compact()["coverage"]["files"][0]
    assert row["line_samples"][0] == {"line": 1, "text": "starting up"}


def test_json_lines_without_a_time_field_are_not_time_ranked(tmp_path):
    lines = [json.dumps({"level": "error", "msg": f"boom {i}"}) for i in range(20)]
    res, f = _formats(tmp_path, "a.jsonl", lines)
    assert f.format == "json" and f.no_timestamp == 20
    assert not res.checks["time_ranked"] and not res.checks["parsed_share_ok"]
    assert any("carry no timestamp" in w for w in res.warnings)


def test_level_before_the_timestamp(tmp_path):
    lines = [
        f"{'ERROR' if i % 10 == 0 else 'INFO'} 2025-03-04 10:{i // 60:02d}:{i % 60:02d} "
        f"[worker-{i % 3}] {'connection refused' if i % 10 == 0 else 'job done'}"
        for i in range(120)
    ]
    res, f = _formats(tmp_path, "a.log", lines)
    assert f.format == "timestamped"
    assert (f.entries, f.unparsed, f.no_timestamp) == (120, 0, 0)
    assert engine.parsing.format_ts(f.first) == "2025-03-04 10:00:00"
    assert engine.parsing.format_ts(f.last) == "2025-03-04 10:01:59"
    assert dict(res.an.level_totals) == {"ERROR": 12, "INFO": 108}
    assert sorted(res.an.tpls) == [
        "[worker-<*>] connection refused",
        "[worker-<*>] job done",
    ]
    assert res.checks["parsed_share_ok"] and res.checks["time_ranked"]


def test_level_and_logger_name_before_the_timestamp(tmp_path):
    lines = [
        f"{'WARNING' if i % 5 == 0 else 'INFO'}:app.worker:2025-03-04 10:00:{i:02d},250:"
        f"{'queue is slow' if i % 5 == 0 else 'handled job'} {i}"
        for i in range(60)
    ]
    res, f = _formats(tmp_path, "a.log", lines)
    assert f.format == "timestamped"
    assert (f.entries, f.unparsed, f.no_timestamp) == (60, 0, 0)
    assert engine.parsing.format_ts(f.first) == "2025-03-04 10:00:00.250"
    assert dict(res.an.level_totals) == {"WARNING": 12, "INFO": 48}
    assert sorted(res.an.tpls) == [
        "app.worker handled job <*>",
        "app.worker queue is slow <*>",
    ]


def test_host_before_the_timestamp(tmp_path):
    lines = [
        f"web-0{i % 3} [2025-03-04T10:00:{i:02d}Z] ERROR upstream reset"
        for i in range(30)
    ]
    res, f = _formats(tmp_path, "a.log", lines)
    assert (f.entries, f.unparsed) == (30, 0)
    tpl = res.an.tpls["upstream reset"]
    assert set(tpl.hosts) == {"web-00", "web-01", "web-02"} and tpl.sev == 3


def test_a_timestamp_mentioned_in_a_few_messages_is_not_the_line_time(tmp_path):
    lines = []
    for i in range(40):
        lines.append(f"worker {i % 4} picked up a batch")
        if i % 5 == 0:
            lines.append(f"next run at 2025-03-04 10:00:{i:02d}")
    res, f = _formats(tmp_path, "a.log", lines)
    assert f.format == "plain" and f.unparsed == 0
    assert not res.checks["time_ranked"]


def _event_export(path: Path) -> Path:
    """An event viewer CSV export: named columns, then an unnamed message."""
    lines = ["Level,Date and Time,Source,Event ID,Task Category"]
    for i in range(30):
        when = f"3/4/2025 10:{i:02d}:00 AM"
        if i % 10 == 0:
            lines.append(
                f'Error,{when},Backup Agent,4101,None,"Backup of volume {i} stopped.'
            )
            lines.append('The target ""vault"" did not answer."')
        else:
            lines.append(
                f"Information,{when},Backup Agent,4100,None,Backup of volume {i} finished."
            )
    return _write(path, lines, newline="\r\n")


def test_event_viewer_csv_export(tmp_path):
    res = engine.report.triage([str(_event_export(tmp_path / "events.csv"))])
    f = res.an.files[0]
    assert f.format == "csv"
    assert (f.entries, f.header, f.continuation, f.unparsed) == (30, 1, 3, 0)
    assert f.no_timestamp == 0
    assert engine.parsing.format_ts(f.first) == "2025-03-04 10:00:00"
    assert engine.parsing.format_ts(f.last) == "2025-03-04 10:29:00"
    assert dict(res.an.level_totals) == {"ERROR": 3, "INFORMATION": 27}
    assert sorted(res.an.tpls) == [
        "[Backup Agent] Backup of volume <*> finished.",
        "[Backup Agent] Backup of volume <*> stopped.",
    ]
    assert res.checks["ok"] and res.checks["parsed_share_ok"]
    assert res.compact()["coverage"]["files"][0]["header_lines"] == 1


def test_csv_export_with_named_columns_and_semicolons(tmp_path):
    lines = ["TimeCreated;LevelDisplayName;MachineName;Id;Message"]
    lines += [
        f"2025-03-04 10:00:{i:02d};Warning;desk-{i % 2};{300 + i % 2};Sync took {i} s"
        for i in range(20)
    ]
    res, f = _formats(tmp_path, "export.csv", lines)
    assert f.format == "csv" and (f.entries, f.header, f.unparsed) == (20, 1, 0)
    tpl = res.an.tpls["Sync took <*> s"]
    assert tpl.sev == 2 and set(tpl.hosts) == {"desk-0", "desk-1"}


def test_empty_and_blank_files(tmp_path):
    (tmp_path / "empty.log").write_text("", encoding="utf-8")
    _write(tmp_path / "blank.log", ["", "   ", ""])
    res = engine.report.triage([str(tmp_path)])
    assert {f.name for f in res.an.files} == {"empty.log", "blank.log"}
    assert res.an.entries == 0 and res.checks["ok"]
    assert res.compact()["anomalies"] == []


def test_crlf_bom_unicode_utf16_and_gzip(tmp_path):
    lines = [
        f"2025-03-04 10:00:0{i},000 h-1 WARN café überlast {i} 東京" for i in range(5)
    ]
    crlf = _write(tmp_path / "crlf.log", lines, newline="\r\n")
    bom = tmp_path / "bom.log"
    bom.write_bytes(b"\xef\xbb\xbf" + ("\n".join(lines) + "\n").encode("utf-8"))
    utf16 = _write(tmp_path / "u16.log", lines, encoding="utf-16")
    gz = tmp_path / "old.log.gz"
    with gzip.open(gz, "wt", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    res = engine.report.triage([str(p) for p in (crlf, bom, utf16, gz)])
    for f in res.an.files:
        assert (f.entries, f.unparsed) == (5, 0), f.name
    assert list(res.an.tpls) == ["café überlast <*> 東京"]


def test_binary_and_non_log_files_are_skipped_with_a_reason(tmp_path):
    (tmp_path / "core.dump").write_bytes(b"\x00\x01\x02" * 100)
    (tmp_path / "pic.png").write_bytes(b"\x89PNG")
    _steady(tmp_path / "app.log", hours=1)
    res = engine.report.triage([str(tmp_path)])
    assert [f.name for f in res.an.files] == ["app.log"]
    reasons = {Path(s["file"]).name: s["reason"] for s in res.skipped}
    assert reasons["core.dump"] == "binary content"
    assert "not a log format" in reasons["pic.png"]


def test_glob_filters_a_folder(tmp_path):
    _steady(tmp_path / "a.log", hours=1)
    _steady(tmp_path / "b.txt", hours=1)
    res = engine.report.triage(
        [str(tmp_path)], engine.analysis.TriageOptions(glob="*.log")
    )
    assert [f.name for f in res.an.files] == ["a.log"]


def _per_host_folders(root: Path, sub: str = "") -> None:
    for n in (1, 2, 3):
        folder = root / f"node{n}" / sub
        folder.mkdir(parents=True)
        _write(
            folder / "app.log",
            [f"{_iso(T0 + i * 60 + n)} INFO tick {i}" for i in range(30)],
        )


@pytest.mark.parametrize("sub", ["", "var/log"])
def test_one_folder_per_host_with_the_same_file_name(tmp_path, sub):
    _per_host_folders(tmp_path, sub)
    below = f"{sub}/" if sub else ""
    res = engine.report.triage([str(tmp_path)])
    assert set(res.an.hosts) == {"node1", "node2", "node3"}
    out = res.compact()
    assert out["coverage"]["hosts"] == 3
    assert [f["file"] for f in out["coverage"]["files"]] == [
        f"node{n}/{below}app.log" for n in (1, 2, 3)
    ]


def test_rotated_files_in_one_folder_stay_one_host(tmp_path):
    for name in ("app.log", "app.log.1"):
        _write(
            tmp_path / name, [f"{_iso(T0 + i * 60)} INFO tick {i}" for i in range(30)]
        )
    res = engine.report.triage([str(tmp_path)])
    assert set(res.an.hosts) == {"app"}
    assert [f.name for f in res.an.files] == ["app.log", "app.log.1"]


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------


def test_timestamp_regex_with_components_and_level_host_overrides(tmp_path):
    lines = [
        f"D04.03.25 T10h00m{i:02d}s host=n{i % 3} sev:E parcel order-{1000 + i} lost"
        for i in range(20)
    ]
    opts = dict(
        timestamp_regex=r"^D(?P<day>\d{2})\.(?P<month>\d{2})\.(?P<year>\d{2}) T(?P<hour>\d{2})h(?P<minute>\d{2})m(?P<second>\d{2})s",
        level_regex=r"sev:(?P<level>\w+)",
        host_regex=r"host=(\w+)",
        entity_regex=r"(order-\d+)",
    )
    res, f = _formats(tmp_path, "a.log", lines, **opts)
    assert f.format == "custom" and f.entries == 20 and f.unparsed == 0
    assert engine.parsing.format_ts(f.first) == "2025-03-04 10:00:00"
    tpl = next(iter(res.an.tpls.values()))
    assert set(tpl.hosts) == {"n0", "n1", "n2"}
    assert len(tpl.ents) == 20 and "order-1000" in tpl.ents


def test_invalid_options_fail_loudly():
    with pytest.raises(ValueError, match="entity_regex"):
        engine.report.triage(
            [], engine.analysis.TriageOptions(entity_regex="(unclosed")
        )
    with pytest.raises(ValueError, match="bucket"):
        engine.report.triage([], engine.analysis.TriageOptions(bucket="soon"))
    assert (
        engine.analysis.parse_bucket("5min") == 300
        and engine.analysis.parse_bucket("1h") == 3600
        and engine.analysis.parse_bucket("30") == 30
    )


REGEX_ARGS = ["timestamp_regex", "level_regex", "host_regex", "entity_regex"]


@pytest.mark.parametrize("arg", REGEX_ARGS)
def test_a_regex_that_does_not_compile_is_a_tool_error_naming_it(tmp_path, arg):
    path = _steady(tmp_path / "a.log", hours=1)
    out = _call(paths=[str(path)], **{arg: "(?P<ts>[0-9"})
    assert out["status"] == "error"
    assert out["error"].startswith(f"{arg} is not a valid regular expression")
    assert "coverage" not in out


@pytest.mark.parametrize("arg", REGEX_ARGS)
@pytest.mark.parametrize(
    "pattern",
    [
        r"(\w+\s*)+$",
        r"(?P<ts>(\d+)*)x",
        r"((a+)+)b",
        r"(\d+,?)+!",
        r"(\w+\w+)+!",
    ],
)
def test_a_repeated_group_that_repeats_inside_is_refused(tmp_path, arg, pattern):
    path = _steady(tmp_path / "a.log", hours=1)
    out = _call(paths=[str(path)], **{arg: pattern})
    assert out["status"] == "error"
    assert out["error"].startswith(f"{arg} repeats a group")
    assert "[\\w\\s]+" in out["error"]


def test_a_slow_regex_is_stopped_by_the_probe_before_streaming(tmp_path, monkeypatch):
    """Not nested, so only timing finds it: each 'a' can pair up or stand alone."""
    monkeypatch.setattr(engine.parsing, "REGEX_PROBE_BUDGET_S", 0.05)
    path = _steady(tmp_path / "a.log", hours=1)
    streamed = []
    monkeypatch.setattr(
        engine.analysis.Analyzer, "run_file", lambda self, p, ctx: streamed.append(p)
    )
    out = _call(paths=[str(path)], entity_regex=r"(a|aa)+$")
    assert out["status"] == "error"
    assert out["error"].startswith("entity_regex is too slow")
    assert streamed == [], "the log must not be streamed with that pattern"


@pytest.mark.parametrize(
    "pattern",
    [
        r"(req-[0-9a-f]+)",
        r"(?:\d+\.)+\d+",
        r"(\d{1,3}\.){3}\d{1,3}",
        r"(?P<entity>[A-Za-z]+-\d+)(?:\s|$)",
        r"((?:\s+\S+)*)$",
        r"((?:[0-9a-f]{2}:){5}[0-9a-f]{2})",
    ],
)
def test_ordinary_regexes_are_accepted(tmp_path, pattern):
    path = _steady(tmp_path / "a.log", hours=1)
    out = _call(paths=[str(path)], entity_regex=pattern)
    assert out["status"] == "success", out.get("error")


def test_explicit_bucket_is_used(tmp_path):
    res = engine.report.triage(
        [str(_steady(tmp_path / "a.log", hours=2))],
        engine.analysis.TriageOptions(bucket="1min"),
    )
    assert res.an.bucket_s == 60
    assert res.compact()["coverage"]["bucket"] == "1min"


# ---------------------------------------------------------------------------
# Classification, bursts, entities
# ---------------------------------------------------------------------------


def test_steady_errors_are_baseline_and_a_short_fault_is_an_anomaly(tmp_path):
    extra = [
        (
            T0 + 4 * 3600 + k * 7,
            f"{_iso(T0 + 4 * 3600 + k * 7)} app-0{k % 5 + 1} FATAL disk controller reset on bay {k % 3}",
        )
        for k in range(20)
    ]
    res = engine.report.triage([str(_steady(tmp_path / "a.log", extra=extra))])
    by_key = {t.key: t for t in res.an.tpls.values()}
    assert by_key["cache backend timeout after <*> ms"].cls == "baseline"
    fault = by_key["disk controller reset on bay <*>"]
    assert fault.cls == "anomaly"
    assert any("short window" in r for r in fault.reasons)
    assert len(res.bursts) == 1
    burst = res.bursts[0]
    assert burst["start"] == pytest.approx(T0 + 4 * 3600)
    assert burst["end"] == pytest.approx(T0 + 4 * 3600 + 19 * 7)
    assert res.loc(burst["start_loc"])["file"] == "a.log"


def _refused_every_third_line(path: Path) -> Path:
    """30 minutes, one line every 3 s, every third one the same ERROR."""
    lines = []
    for i in range(600):
        ts = _iso(T0 + i * 3)
        if i % 3 == 0:
            lines.append(f"{ts} app-01 ERROR db connection refused")
        else:
            lines.append(f"{ts} app-01 INFO served request {i}")
    return _write(path, lines)


def test_an_error_that_lasts_the_whole_log_is_reported_as_persistent(tmp_path):
    out = _call(paths=[str(_refused_every_third_line(tmp_path / "app.log"))])
    assert out["anomalies"] == [] and out["bursts"] == []
    (row,) = out["persistent_errors"]
    assert row["template"] == "db connection refused"
    assert (row["count"], row["severity"]) == (200, "ERROR")
    assert row["first"]["line"] == 1 and row["last"]["line"] == 598
    assert row["reading"] == engine.limits.PERSISTENT_READING
    assert "1 persistent error template(s)" in out["headline"]
    assert "outage that covers the whole log" in out["headline"]
    assert "app.log:1" in out["next_step"]
    assert "outage that covers the whole log" in out["next_step"]


def test_routine_errors_beside_an_incident_in_a_long_log_stay_background(tmp_path):
    extra = [
        (
            T0 + 4 * 3600 + k * 7,
            f"{_iso(T0 + 4 * 3600 + k * 7)} app-0{k % 5 + 1} FATAL disk controller reset on bay {k % 3}",
        )
        for k in range(20)
    ]
    res = engine.report.triage([str(_steady(tmp_path / "a.log", extra=extra))])
    assert res.anomalies and any(t.sev >= 3 for t in res.baseline)
    out = res.compact()
    assert out["persistent_errors"] == []
    assert "persistent" not in out["headline"]


def test_a_long_log_with_only_steady_errors_reports_them_as_persistent(tmp_path):
    out = engine.report.triage([str(_steady(tmp_path / "a.log"))]).compact()
    assert out["anomalies"] == []
    assert [r["template"] for r in out["persistent_errors"]] == [
        "cache backend timeout after <*> ms"
    ]


def test_spike_of_a_routine_error_is_an_anomaly_with_a_peak(tmp_path):
    t = T0 + 3 * 3600
    extra = [
        (t + k, f"{_iso(t + k)} app-01 ERROR cache backend timeout after 500 ms")
        for k in range(60)
    ]
    res = engine.report.triage([str(_steady(tmp_path / "a.log", extra=extra))])
    tpl = res.an.tpls["cache backend timeout after <*> ms"]
    assert tpl.cls == "anomaly"
    assert any(r.startswith("spike") for r in tpl.reasons)
    assert abs(tpl.m["peak_ts"] - t) < res.an.bucket_s


def test_entities_come_only_from_the_anomaly_window(tmp_path):
    t = T0 + 3 * 3600
    extra = [
        (
            t + k,
            f"{_iso(t + k)} app-01 ERROR cache backend timeout after 500 ms for sess-{k:04x}9",
        )
        for k in range(60)
    ]
    early = [
        (
            T0 + 600 + k * 300,
            f"{_iso(T0 + 600 + k * 300)} app-02 ERROR cache backend timeout after 500 ms for sess-ff{k:02x}9",
        )
        for k in range(5)
    ]
    res = engine.report.triage([str(_steady(tmp_path / "a.log", extra=extra + early))])
    flagged = set(res.entities["flagged"])
    assert {f"sess-{k:04x}9" for k in range(60)} <= flagged
    assert not any(e.startswith("sess-ff") for e in flagged)
    # Request ids of the INFO baseline are never flagged.
    assert not any(e.startswith("req-") for e in flagged)


def _lease_log(tmp_path, *, narrow: bool):
    """300 jobs open routinely; one short template touches 200 of them, and
    (when ``narrow``) another touches 6 jobs only."""
    rng = random.Random(3)
    lines = []
    for i in range(300):
        lines.append(f"{_iso(T0 + i * 60)} h-{i % 4} INFO opened job-{i}")
    burst_t = T0 + 200 * 60
    for i in range(200):
        lines.append(
            f"{_iso(burst_t + i * 0.5)} h-{rng.randint(0, 3)} ERROR job-{i} lost lease"
        )
    if narrow:
        for i in range(6):
            lines.append(
                f"{_iso(burst_t + 30 + i)} h-1 FATAL job-{250 + i} corrupted checkpoint"
            )
    lines.sort()
    return engine.report.triage([str(_write(tmp_path / "a.log", lines))])


def test_a_broad_anomaly_template_is_left_out_of_the_entity_list(tmp_path):
    res = _lease_log(tmp_path, narrow=True)
    assert res.entities["dropped_templates"], "the broad template must be left out"
    assert set(res.entities["flagged"]) == {f"job-{250 + i}" for i in range(6)}
    view = res.compact()["entities"]
    assert "describe the baseline" in view["narrowed"]
    assert view["list"] == sorted(f"job-{250 + i}" for i in range(6))


def test_a_list_that_is_mostly_baseline_says_so(tmp_path):
    res = _lease_log(tmp_path, narrow=False)
    assert len(res.entities["flagged"]) == 200
    assert any("small minority" in w for w in res.warnings)
    assert res.entities["implausible"], "200 of 300 is over a third"


def _mostly_flagged_log(tmp_path) -> Path:
    """300 jobs: a short WARN template names 200 of them and nothing else does,
    a short FATAL template names 6 more, and 94 only ever open routinely."""
    lines = [
        f"{_iso(T0 + i * 60)} h-{i % 4} INFO heartbeat ok seq {i}" for i in range(360)
    ]
    lines += [
        f"{_iso(T0 + i * 200)} h-{i % 4} INFO opened job-{206 + i}" for i in range(94)
    ]
    burst_t = T0 + 200 * 60
    lines += [
        f"{_iso(burst_t + i * 0.5)} h-{i % 4} WARN job-{i} lost lease, retrying"
        for i in range(200)
    ]
    lines += [
        f"{_iso(burst_t + 30 + i)} h-1 FATAL job-{200 + i} corrupted checkpoint"
        for i in range(6)
    ]
    lines.sort()
    return _write(tmp_path / "a.log", lines)


def test_a_list_of_most_entities_is_reported_as_implausible(tmp_path):
    """Two thirds flagged is not an anomaly list, even when no routine line
    names those ids: the result must say so where it cannot be missed."""
    path = _mostly_flagged_log(tmp_path)
    out = _call(paths=[str(path)], output_path=str(tmp_path / "ids.csv"))
    ent = out["entities"]
    assert (ent["flagged"], ent["total"]) == (206, 300)
    assert ent["plausible"] is False
    warning = out["entity_list_warning"]
    assert warning.startswith("IMPLAUSIBLE ENTITY LIST: 206 of 300 entities (69%)")
    assert "not an anomaly criterion" in warning
    assert "entity_max_share=0.33" in warning and 'entity_min_level="ERROR"' in warning
    # The broadest template is named, and the warning leads the list.
    assert ent["broadest_template"]["entities"] == 200
    assert ent["broadest_template"]["id"] in warning
    assert out["warnings"][0].startswith("IMPLAUSIBLE ENTITY LIST")
    # The file the caller asked for exists, but is not presented as the answer.
    assert out["output"]["written"] and out["output"]["plausible"] is False
    assert out["output"]["share"] == ent["share"] == round(206 / 300, 4)
    step = out["next_step"]
    assert step.startswith("The entity list holds 206 of 300 entities (69%)")
    assert "do not recompute it with your own script" in step
    assert "entity_max_share=0.33" in step


@pytest.mark.parametrize(
    "tighten", [{"entity_min_level": "error"}, {"entity_max_share": 0.33}]
)
def test_either_tightening_argument_leaves_the_rare_severe_ids(tmp_path, tighten):
    path = _mostly_flagged_log(tmp_path)
    out = _call(paths=[str(path)], **tighten)
    ent = out["entities"]
    assert ent["list"] == sorted(f"job-{200 + i}" for i in range(6))
    assert ent["plausible"] is True and "entity_list_warning" not in out
    ((name, used),) = ent["filters"].items()
    assert name in tighten and len(used["templates_left_out"]) == 1


@pytest.mark.parametrize(
    "bad,needle",
    [
        ({"entity_min_level": "loud"}, "entity_min_level 'loud' is not a level"),
        ({"entity_max_share": 33}, "entity_max_share 33 is not a share"),
        ({"entity_max_share": -0.1}, "entity_max_share -0.1 is not a share"),
    ],
)
def test_a_tightening_argument_that_means_nothing_is_an_error(tmp_path, bad, needle):
    path = _steady(tmp_path / "a.log", hours=1)
    out = _call(paths=[str(path)], **bad)
    assert out["status"] == "error" and needle in out["error"]


def _short_fault_log(tmp_path) -> Path:
    t = T0 + 3 * 3600
    extra = [
        (t + k, f"{_iso(t + k)} app-01 FATAL raid array degraded on sess-{k:03d}a")
        for k in range(30)
    ]
    return _steady(tmp_path / "a.log", extra=extra)


def test_the_written_list_carries_the_evidence_and_is_the_deliverable(tmp_path):
    import csv

    _short_fault_log(tmp_path)
    target = tmp_path / "out" / "ids.csv"
    out = _call(paths=[str(tmp_path / "a.log")], output_path=str(target))
    output, ent = out["output"], out["entities"]
    assert output["written"] and output["path"] == str(target)
    assert (output["flagged"], output["total"]) == (30, ent["total"])
    assert output["share"] == ent["share"] and output["plausible"] is True
    # Session ids occur only in the fault's own lines, so all of them are
    # flagged by construction: that is said, and it is not called implausible.
    assert ent["share"] == 1.0 and "not of a known population" in ent["share_note"]
    assert "entity_list_warning" not in out
    with open(target, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["entity"] for r in rows] == sorted(f"sess-{k:03d}a" for k in range(30))
    t = T0 + 3 * 3600
    for row in rows:
        assert row["severity"] == "FATAL" and row["also_in_routine"] == "no"
        assert row["templates"] and "raid array degraded" in row["template_text"]
        assert float(row["template_share"]) == round(30 / ent["total"], 4)
        seen_from, seen_to = (
            datetime.fromisoformat(row[c].replace("Z", "+00:00"))
            .replace(tzinfo=timezone.utc)
            .timestamp()
            for c in ("seen_from", "seen_to")
        )
        assert seen_from <= t + 30 and seen_to >= t and seen_to > seen_from
    # next_step hands the file over instead of leaving the caller to rebuild it.
    assert out["next_step"].startswith(f"{target} is the anomalous-entity list (30 of")
    assert "Do not recompute the list with your own script" in out["next_step"]
    assert "read_file" in out["next_step"], "the context step is still there"


def test_the_json_output_lists_each_entity_with_its_evidence(tmp_path):
    _short_fault_log(tmp_path)
    target = tmp_path / "t.json"
    out = _call(paths=[str(tmp_path / "a.log")], output_path=str(target))
    assert out["output"]["flagged"] == 30
    listed = json.loads(target.read_text(encoding="utf-8"))["entities"]["list"]
    assert len(listed) == 30
    assert listed[0]["severity"] == "FATAL" and listed[0]["also_in_routine"] is False
    assert listed[0]["seen_from"] and listed[0]["template_share"] > 0


def test_without_an_output_path_next_step_names_the_way_to_the_file(tmp_path):
    _short_fault_log(tmp_path)
    out = _call(paths=[str(tmp_path / "a.log")])
    assert "output" not in out
    step = out["next_step"]
    assert "entities.list is the complete list" in step
    assert "output_path='<new file>.csv'" in step
    assert "Do not rebuild the list with your own script" in step


def test_the_tool_description_carries_the_list_rules():
    """The model reads the description on every call; SKILL.md only once."""
    from gaia.agents.base.tools import _schema_description

    text = " ".join(_schema_description(lt.triage_logs.__doc__).split())
    assert "never rebuild the list with your own script" in text
    assert "output_path" in text and "more than a third" in text


def test_bytes_that_do_not_decode_are_counted_and_reported(tmp_path):
    good = [f"{_iso(T0 + i)} h-1 INFO job done, item {i}" for i in range(40)]
    raw = ("\n".join(good) + "\n").encode("utf-8")
    # Three lines in a single-byte encoding: five bytes that are not UTF-8.
    for i, text in (
        (3, "caf\xe9 ge\xf6ffnet"),
        (9, "se\xf1al"),
        (20, "na\xefve \xfcber"),
    ):
        raw += f"{_iso(T0 + 100 + i)} h-1 WARN {text}\n".encode("latin-1")
    (tmp_path / "legacy.log").write_bytes(raw)
    clean = _write(tmp_path / "clean.log", good)
    out = _call(paths=[str(tmp_path / "legacy.log"), str(clean)])
    rows = {f["file"]: f for f in out["coverage"]["files"]}
    assert rows["legacy.log"]["undecodable_chars"] == 5
    assert rows["legacy.log"]["undecodable_lines"] == 3
    assert rows["legacy.log"]["encoding"] == "utf-8"
    assert "undecodable_chars" not in rows["clean.log"]
    (warning,) = [w for w in out["warnings"] if "U+FFFD" in w]
    assert warning.startswith(
        "legacy.log: 5 character(s) on 3 line(s) (first: line 41) are not valid utf-8"
    )


def test_counts_add_up(tmp_path):
    res = engine.report.triage([str(_steady(tmp_path / "a.log"))])
    c = res.checks
    assert c["ok"] and c["template_count_sum"] == c["entries"] == c["level_count_sum"]
    f = res.an.files[0]
    assert f.entries + f.continuation + f.unparsed + f.blank == f.lines


# ---------------------------------------------------------------------------
# Time zones and clocks across files
# ---------------------------------------------------------------------------


def test_offsets_are_normalised_to_utc(tmp_path):
    a = _write(
        tmp_path / "a.log",
        [f"2025-03-04T12:00:0{i}+02:00 a-1 ERROR boom {i}" for i in range(3)],
    )
    b = _write(
        tmp_path / "b.log",
        [f"2025-03-04T05:00:0{i}-05:00 b-1 ERROR boom {i}" for i in range(3)],
    )
    res = engine.report.triage([str(a), str(b)])
    assert res.an.files[0].first == res.an.files[1].first
    assert res.utc
    assert any("several UTC offsets" in w for w in res.warnings)


def test_naive_file_shifted_by_hours_is_flagged(tmp_path):
    def lines(shift_h: float, host: str):
        out = []
        for i in range(120):
            ts = T0 + i * 60 + shift_h * 3600
            out.append(f"{_iso(ts)} {host} INFO tick {i}")
        for k in range(10):
            ts = T0 + 3600 + k * 3 + shift_h * 3600
            out.append(f"{_iso(ts)} {host} ERROR replication stream broken for txn-{k}")
        return sorted(out)

    a = _write(tmp_path / "a.log", lines(0, "a-1"))
    b = _write(tmp_path / "b.log", lines(2, "b-1"))
    res = engine.report.triage([str(a), str(b)])
    assert any(
        "time zones" in w or "time zone" in w for w in res.warnings
    ), res.warnings
    assert res.clock and res.clock[0]["median_offset_s"] == pytest.approx(7200, abs=60)


def test_clock_skew_between_files_is_measured_on_shared_ids(tmp_path):
    def lines(skew: float, host: str):
        return [
            f"{_iso(T0 + k * 30 + skew)} {host} INFO handled txn-{1000 + k}"
            for k in range(50)
        ]

    a = _write(tmp_path / "a.log", lines(0, "a-1"))
    b = _write(tmp_path / "b.log", lines(37, "b-1"))
    res = engine.report.triage([str(a), str(b)])
    assert res.clock == [
        {
            "file": "b.log",
            "later_than": "a.log",
            "median_offset_s": 37.0,
            "spread_s": 0.0,
            "shared_ids": 50,
        }
    ]


# ---------------------------------------------------------------------------
# Big input stays bounded
# ---------------------------------------------------------------------------


def test_large_generated_file_is_fully_read_in_bounded_structures(tmp_path):
    rng = random.Random(11)
    path = tmp_path / "big.log"
    n = 120_000
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n):
            ts = T0 + i * 0.5
            f.write(
                f"{_iso(ts)} node-{rng.randint(1, 50):02d} INFO op {rng.getrandbits(48):x} took {rng.randint(1, 999)} ms id={rng.random()}\n"
            )
    res = engine.report.triage([str(path)])
    assert res.an.entries == n and res.checks["ok"]
    assert len(res.an.tpls) <= 3
    tpl = max(res.an.tpls.values(), key=lambda t: t.count)
    assert len(tpl.cells) <= engine.limits.MAX_BUCKETS
    assert len(json.dumps(res.compact(), default=str)) < 12_000


def _days_of_ticks(path: Path, n: int = 60_000, step: float = 5.0) -> Path:
    """One line every ``step`` seconds: 60,000 lines cover about 3.5 days."""
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for i in range(n):
            f.write(f"{_iso(T0 + i * step)} app-01 INFO tick {i}\n")
    return path


def test_a_multi_day_gz_keeps_its_buckets_bounded_while_streaming(tmp_path):
    """A .gz is sampled from its head, so its first bucket is far too small."""
    path = _days_of_ticks(tmp_path / "days.log.gz")
    an = engine.analysis.Analyzer(engine.analysis.TriageOptions())
    ((_, ctx, _info),) = an.prepare([path])
    assert an.bucket_s == 5  # the head sample covers about half an hour
    sizes = []
    real_coarsen = an._coarsen

    def coarsen():
        sizes.append(max(len(t.cells) for t in an.tpls.values()))
        real_coarsen()

    an._coarsen = coarsen
    an.run_file(path, ctx)
    sizes.append(max(len(t.cells) for t in an.tpls.values()))
    assert len(sizes) > 1, "the buckets were never widened"
    assert max(sizes) <= engine.limits.MAX_BUCKETS + 1
    an.finalize()
    assert an.bucket_s == engine.analysis.choose_bucket(60_000 * 5.0) == 600
    (tpl,) = an.tpls.values()
    assert sum(cell[0] for cell in tpl.cells.values()) == 60_000
    assert len(tpl.cells) == 500


def test_a_multi_day_gz_gives_the_same_result_as_the_plain_file(tmp_path):
    gz = _days_of_ticks(tmp_path / "days.log.gz", n=20_000, step=15.0)
    plain = tmp_path / "plain" / "days.log"
    plain.parent.mkdir()
    with gzip.open(gz, "rt", encoding="utf-8") as src:
        plain.write_text(src.read(), encoding="utf-8", newline="\n")
    a = engine.report.triage([str(gz)])
    b = engine.report.triage([str(plain)])
    assert a.an.bucket_s == b.an.bucket_s
    assert a.checks["ok"] and a.an.entries == b.an.entries == 20_000
    assert [t.cls for t in a.an.tpls.values()] == [t.cls for t in b.an.tpls.values()]
    assert a.compact()["baseline"] == b.compact()["baseline"]


def test_a_fixed_bucket_that_is_far_too_small_is_widened_and_says_so(tmp_path):
    path = _days_of_ticks(tmp_path / "days.log.gz", n=20_000, step=15.0)
    res = engine.report.triage([str(path)], engine.analysis.TriageOptions(bucket="1s"))
    assert res.an.bucket_s > 1 and res.checks["ok"]
    assert (
        max(len(t.cells) for t in res.an.tpls.values())
        <= 20 * engine.limits.MAX_BUCKETS
    )
    assert any("widened to" in w for w in res.warnings)


def test_a_file_with_no_line_breaks_is_never_read_whole(tmp_path, monkeypatch):
    """A one-line export is cut at the line cap, not loaded into memory."""
    path = tmp_path / "export.json"
    record = '{"time": "2025-03-04T10:00:00Z", "level": "error", "msg": "boom"}'
    path.write_text("[" + ",".join([record] * 60_000) + "]", encoding="utf-8")
    assert path.stat().st_size > 3_000_000
    largest = 0
    real_open = engine.reading._open_text

    def open_and_watch(p):
        f = real_open(p)
        real_readline = f.readline

        def readline(size=-1):
            nonlocal largest
            assert size is not None and size > 0, "an unbounded readline"
            text = real_readline(size)
            largest = max(largest, len(text))
            return text

        f.readline = readline
        return f

    monkeypatch.setattr(engine.reading, "_open_text", open_and_watch)
    monkeypatch.setattr(engine.analysis, "_open_text", open_and_watch)
    res = engine.report.triage([str(path)])
    f = res.an.files[0]
    assert largest <= engine.limits.READ_CHUNK_CHARS
    assert (f.lines, f.entries, f.long_lines) == (1, 1, 1)
    assert res.checks["ok"]
    assert any("one long line" in w for w in res.warnings)
    assert res.compact()["coverage"]["files"][0]["long_lines_cut"] == 1


def test_an_over_long_line_is_cut_and_the_next_line_still_counts(tmp_path):
    lines = [f"{_iso(T0 + i)} h-1 INFO ok {i}" for i in range(10)]
    lines[4] = f"{_iso(T0 + 4)} h-1 ERROR dump " + "x" * 200_000
    res, f = _formats(tmp_path, "a.log", lines)
    assert (f.lines, f.entries, f.unparsed, f.long_lines) == (10, 10, 0, 1)
    assert res.checks["ok"]
    assert any("1 line(s) longer than 8192" in w for w in res.warnings)


# ---------------------------------------------------------------------------
# The tool: scope, output and registration
# ---------------------------------------------------------------------------


def _call(**kwargs):
    entry = lt.triage_logs  # @tool returns the function unchanged
    return entry(**kwargs)


def test_tool_returns_a_compact_result(tmp_path):
    _steady(tmp_path / "a.log", hours=2)
    out = _call(paths=[str(tmp_path)])
    assert out["status"] == "success"
    for key in (
        "headline",
        "coverage",
        "anomalies",
        "bursts",
        "baseline",
        "entities",
        "checks",
        "warnings",
    ):
        assert key in out
    assert out["coverage"]["files"][0]["file"] == "a.log"


def test_tool_accepts_a_single_string_path(tmp_path):
    path = _steady(tmp_path / "a.log", hours=1)
    assert _call(paths=str(path))["status"] == "success"


def test_tool_reports_missing_inputs():
    out = _call(paths=["Z:/definitely/not/here.log"])
    assert out["status"] == "error" and "not found" in out["error"]


def test_tool_writes_json_and_csv_beside_the_logs_and_never_overwrites(tmp_path):
    t = T0 + 3 * 3600
    extra = [
        (t + k, f"{_iso(t + k)} app-01 FATAL raid array degraded on sess-{k:03d}a")
        for k in range(30)
    ]
    _steady(tmp_path / "a.log", extra=extra)
    before = (tmp_path / "a.log").read_bytes()
    out = _call(paths=[str(tmp_path)], output_path=str(tmp_path / "triage.json"))
    assert out["output"]["written"], out["output"]
    payload = json.loads((tmp_path / "triage.json").read_text(encoding="utf-8"))
    assert payload["generator"] == engine.limits.OUTPUT_MARKER and payload["templates"]
    out = _call(paths=[str(tmp_path)], output_path=str(tmp_path / "bad.csv"))
    rows = (tmp_path / "bad.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("entity,") and len(rows) == 31
    # The earlier output is not mistaken for a log on the next run.
    assert [f["file"] for f in out["coverage"]["files"]] == ["a.log"]
    again = _call(paths=[str(tmp_path)], output_path=str(tmp_path / "triage.json"))
    assert (
        not again["output"]["written"]
        and "never overwrites" in again["output"]["error"]
    )
    assert (tmp_path / "a.log").read_bytes() == before


def test_its_own_txt_list_is_not_read_back_as_a_log(tmp_path):
    t = T0 + 3 * 3600
    extra = [
        (t + k, f"{_iso(t + k)} app-01 FATAL raid array degraded on sess-{k:03d}a")
        for k in range(30)
    ]
    _steady(tmp_path / "a.log", extra=extra)
    first = _call(paths=[str(tmp_path)], output_path=str(tmp_path / "bad.txt"))
    assert first["output"]["written"] and first["output"]["rows"] == 30
    second = _call(paths=[str(tmp_path)])
    assert [f["file"] for f in second["coverage"]["files"]] == ["a.log"]
    assert second["coverage"]["hosts"] == first["coverage"]["hosts"]
    assert second["coverage"]["entries"] == first["coverage"]["entries"]
    skipped = {Path(s["file"]).name: s["reason"] for s in second["coverage"]["skipped"]}
    assert skipped == {"bad.txt": "an earlier triage_logs output"}


def test_a_users_own_txt_list_is_still_read(tmp_path):
    _write(
        tmp_path / "notes.txt", [f"{_iso(T0 + i)} h-1 INFO ok {i}" for i in range(5)]
    )
    out = _call(paths=[str(tmp_path)])
    assert [f["file"] for f in out["coverage"]["files"]] == ["notes.txt"]


def _assert_not_written(out, needle):
    """The failure is at the top level, not only nested under ``output``."""
    assert out["status"] == "partial"
    assert out["error"].startswith("output_path was not written: ")
    assert needle in out["error"] and "Call again" in out["fix"]
    assert out["output"] == {
        "written": False,
        "error": out["error"].split(": ", 1)[1],
    }
    assert out["coverage"]["entries"], "the analysis itself is still returned"


@pytest.mark.parametrize("name", ["triage.json", "bad.csv", "ids.txt"])
def test_a_file_created_after_the_check_is_still_not_overwritten(
    tmp_path, monkeypatch, name
):
    """Another writer can create the output between the check and the write."""
    _steady(tmp_path / "a.log", hours=1)
    target = tmp_path / name
    real_refusal = lt._output_refusal

    def check_then_race(out, inputs):
        refusal = real_refusal(out, inputs)
        target.write_text("someone else's file\n", encoding="utf-8")
        return refusal

    monkeypatch.setattr(lt, "_output_refusal", check_then_race)
    out = _call(paths=[str(tmp_path / "a.log")], output_path=str(target))
    _assert_not_written(out, "never overwrites")
    assert target.read_text(encoding="utf-8") == "someone else's file\n"


@pytest.mark.parametrize(
    "name,needle",
    [("notes.md", ".json"), ("a.log", "input logs")],
)
def test_tool_refuses_bad_output_paths(tmp_path, name, needle):
    _steady(tmp_path / "a.log", hours=1)
    out = _call(paths=[str(tmp_path)], output_path=str(tmp_path / name))
    _assert_not_written(out, needle)


def test_a_written_output_keeps_status_success(tmp_path):
    _steady(tmp_path / "a.log", hours=1)
    out = _call(paths=[str(tmp_path)], output_path=str(tmp_path / "t.json"))
    assert out["status"] == "success" and out["output"]["written"]
    assert "error" not in out and "fix" not in out


def test_tool_reads_and_writes_only_inside_the_agents_folders(tmp_path):
    logs = tmp_path / "logs"
    other = tmp_path / "other"
    logs.mkdir()
    other.mkdir()
    _steady(logs / "a.log", hours=1)
    _steady(other / "b.log", hours=1)
    with _scoped(logs):
        out = _call(paths=[str(logs), str(other)], output_path=str(other / "t.json"))
        assert [f["file"] for f in out["coverage"]["files"]] == ["a.log"]
        assert any(Path(s["file"]).name == "other" for s in out["coverage"]["skipped"])
        assert out["status"] == "partial" and not out["output"]["written"]
        assert not (other / "t.json").exists()
        refused = _call(paths=[str(other / "b.log")])
        assert refused["status"] == "error"
        inside = _call(paths=[str(logs)], output_path=str(logs / "t.json"))
        assert inside["output"]["written"], inside["output"]


def test_tool_refuses_secret_files(tmp_path):
    secret = tmp_path / ".env"
    secret.write_text("TOKEN=x\n", encoding="utf-8")
    with _scoped(tmp_path):
        out = _call(paths=[str(secret)])
    assert out["status"] == "error" and "blocked" in out["error"].lower()


def test_engine_refuses_to_run_outside_a_skill_call(tmp_path):
    from gaia.skills.context import _ACTIVE, SkillContextError

    path = _steady(tmp_path / "a.log", hours=1)
    token = _ACTIVE.set(None)
    try:
        with pytest.raises(SkillContextError):
            engine.report.triage([str(path)])
    finally:
        _ACTIVE.reset(token)


def test_skill_manifest_declares_the_tool_and_it_registers():
    """The loader refuses a manifest that disagrees with tools.py."""
    from gaia.skills.loader import register_skill_tools, unregister_skill_tools
    from gaia.skills.manager import SkillManager

    manager = SkillManager(agent_skill_dirs=[str(TOOLS_PATH.parents[1])])
    skill = manager.load("log-triage")
    assert skill.tool_names == ["triage_logs"]
    try:
        registered = register_skill_tools(skill)
        assert set(registered) == {"log-triage/triage_logs"}
        params = registered["log-triage/triage_logs"]["parameters"]
        assert params["paths"]["type"] == "array" and params["paths"]["required"]
        assert not params["output_path"]["required"]
    finally:
        unregister_skill_tools("log-triage")


def test_skill_md_body_stays_lean_with_rules_and_linked_references():
    import re

    skill_dir = Path(TOOLS_PATH).parent
    body = (skill_dir / "SKILL.md").read_text(encoding="utf-8").split("\n---\n", 2)[-1]
    assert len(body) // 4 <= 1200
    assert "do not rebuild them by hand" in body
    assert "Baseline is not the incident" in body
    assert "`persistent_errors`" in body
    refs = re.findall(r"`(references/[\w-]+\.md)`", body)
    assert len(refs) >= 4
    for ref in refs:
        assert (skill_dir / ref).is_file(), ref
    fallback = (skill_dir / "references" / "fallback-python.md").read_text(
        encoding="utf-8"
    )
    assert "assert share >= 0.95" in fallback
