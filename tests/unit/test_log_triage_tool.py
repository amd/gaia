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

import pytest

from tests.unit.log_triage_scenarios import TOOLS_PATH, load_tools

lt = load_tools()

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
    tm = lt.parse_timestamp(text)
    assert tm is not None, text
    assert tm.kind == kind
    assert lt.format_ts(tm.epoch) == expected
    assert tm.has_tz is has_tz


def test_syslog_takes_the_year_it_is_given():
    tm = lt.parse_timestamp("Mar  4 10:00:00 host x", year_hint=2023)
    assert lt.format_ts(tm.epoch) == "2023-03-04 10:00:00"


def test_impossible_dates_and_out_of_range_epochs_do_not_parse():
    assert lt.parse_timestamp("2025-13-40 10:00:00 x") is None
    assert lt.parse_timestamp("1234567 x") is None
    assert lt.parse_timestamp_value(5) is None
    assert lt.parse_timestamp_value(1741082400000).epoch == pytest.approx(1741082400)


# ---------------------------------------------------------------------------
# Severity and templates
# ---------------------------------------------------------------------------


def test_levels_and_keyword_fallback():
    assert lt.level_severity("warning") == 2
    assert lt.level_severity("[ERROR]") == 3
    assert lt.level_severity("trace4") == 0
    assert lt.level_severity(40) == 3  # numeric levels follow Python logging: 40 ERROR
    assert lt.level_severity(60) == 4
    assert lt.level_severity("banana") is None
    assert lt.keyword_severity("kernel panic - not syncing") == 4
    assert lt.keyword_severity("connection refused") == 3
    assert lt.keyword_severity("all good") == 1


def test_masking_keeps_structure_and_hides_values():
    a = lt.mask_message(
        "req-a3f9c2d1 from 10.0.0.1:443 took 123 ms reading /var/lib/x/y.db 0xdeadbeef"
    )
    b = lt.mask_message(
        "req-12345678 from 10.9.8.7:80 took 7 ms reading /srv/data/z.db 0x1"
    )
    assert a == b
    assert "<IP>" in a and "<PATH>" in a and "<*>" in a
    uuid = lt.mask_message("job 123e4567-e89b-12d3-a456-426614174000 done")
    assert uuid == "job <UUID> done"
    assert lt.mask_message("see https://example.test/a?b=1 now") == "see <URL> now"


def test_high_cardinality_word_merges_into_one_template(tmp_path):
    users = [f"user{c}" for c in "abcdefghijklmnop"]
    lines = [
        f"{_iso(T0 + i * 30)} app-01 INFO login ok for {users[i % 16]} via sso"
        for i in range(400)
    ]
    res = lt.triage([str(_write(tmp_path / "a.log", lines))])
    assert len(res.an.tpls) == 1
    assert next(iter(res.an.tpls)) == "login ok for <*> via sso"


def test_template_cap_pools_the_rest_and_says_so(tmp_path):
    lines = [
        f"{_iso(T0 + i)} h-1 INFO event{chr(97 + i % 26)}{chr(97 + i // 26 % 26)} happened"
        for i in range(300)
    ]
    path = _write(tmp_path / "a.log", lines)
    res = lt.triage([str(path)], lt.TriageOptions(max_templates=50))
    assert len(res.an.tpls) <= 50
    assert res.an.overflow is not None
    assert any("distinct templates" in w for w in res.warnings)
    assert res.checks["ok"], res.checks


# ---------------------------------------------------------------------------
# Format detection and parsing
# ---------------------------------------------------------------------------


def _formats(tmp_path, name, lines, **opts):
    res = lt.triage([str(_write(tmp_path / name, lines))], lt.TriageOptions(**opts))
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


def test_empty_and_blank_files(tmp_path):
    (tmp_path / "empty.log").write_text("", encoding="utf-8")
    _write(tmp_path / "blank.log", ["", "   ", ""])
    res = lt.triage([str(tmp_path)])
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
    res = lt.triage([str(p) for p in (crlf, bom, utf16, gz)])
    for f in res.an.files:
        assert (f.entries, f.unparsed) == (5, 0), f.name
    assert list(res.an.tpls) == ["café überlast <*> 東京"]


def test_binary_and_non_log_files_are_skipped_with_a_reason(tmp_path):
    (tmp_path / "core.dump").write_bytes(b"\x00\x01\x02" * 100)
    (tmp_path / "pic.png").write_bytes(b"\x89PNG")
    _steady(tmp_path / "app.log", hours=1)
    res = lt.triage([str(tmp_path)])
    assert [f.name for f in res.an.files] == ["app.log"]
    reasons = {Path(s["file"]).name: s["reason"] for s in res.skipped}
    assert reasons["core.dump"] == "binary content"
    assert "not a log format" in reasons["pic.png"]


def test_glob_filters_a_folder(tmp_path):
    _steady(tmp_path / "a.log", hours=1)
    _steady(tmp_path / "b.txt", hours=1)
    res = lt.triage([str(tmp_path)], lt.TriageOptions(glob="*.log"))
    assert [f.name for f in res.an.files] == ["a.log"]


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------


def test_timestamp_regex_with_components_and_level_host_overrides(tmp_path):
    lines = [
        f"250304 1000{i:02d} 77 host=n{i % 3} sev:E block blk_{1000 + i} lost"
        for i in range(20)
    ]
    opts = dict(
        timestamp_regex=r"^(?P<year>\d{2})(?P<month>\d{2})(?P<day>\d{2}) (?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})",
        level_regex=r"sev:(?P<level>\w+)",
        host_regex=r"host=(\w+)",
        entity_regex=r"(blk_\d+)",
    )
    res, f = _formats(tmp_path, "a.log", lines, **opts)
    assert f.format == "custom" and f.entries == 20 and f.unparsed == 0
    assert lt.format_ts(f.first) == "2025-03-04 10:00:00"
    tpl = next(iter(res.an.tpls.values()))
    assert set(tpl.hosts) == {"n0", "n1", "n2"}
    assert len(tpl.ents) == 20 and "blk_1000" in tpl.ents


def test_invalid_options_fail_loudly():
    with pytest.raises(ValueError, match="entity_regex"):
        lt.triage([], lt.TriageOptions(entity_regex="(unclosed"))
    with pytest.raises(ValueError, match="bucket"):
        lt.triage([], lt.TriageOptions(bucket="soon"))
    assert (
        lt.parse_bucket("5min") == 300
        and lt.parse_bucket("1h") == 3600
        and lt.parse_bucket("30") == 30
    )


def test_explicit_bucket_is_used(tmp_path):
    res = lt.triage(
        [str(_steady(tmp_path / "a.log", hours=2))], lt.TriageOptions(bucket="1min")
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
    res = lt.triage([str(_steady(tmp_path / "a.log", extra=extra))])
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


def test_spike_of_a_routine_error_is_an_anomaly_with_a_peak(tmp_path):
    t = T0 + 3 * 3600
    extra = [
        (t + k, f"{_iso(t + k)} app-01 ERROR cache backend timeout after 500 ms")
        for k in range(60)
    ]
    res = lt.triage([str(_steady(tmp_path / "a.log", extra=extra))])
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
    res = lt.triage([str(_steady(tmp_path / "a.log", extra=extra + early))])
    flagged = set(res.entities["flagged"])
    assert {f"sess-{k:04x}9" for k in range(60)} <= flagged
    assert not any(e.startswith("sess-ff") for e in flagged)
    # Request ids of the INFO baseline are never flagged.
    assert not any(e.startswith("req-") for e in flagged)


def test_flagging_most_entities_triggers_the_baseline_warning(tmp_path):
    rng = random.Random(3)
    lines = []
    for i in range(300):
        ts = T0 + i * 60
        lines.append(f"{_iso(ts)} h-{i % 4} INFO opened job-{i}")
    burst_t = T0 + 200 * 60
    for i in range(200):  # one short template that touches most jobs
        lines.append(
            f"{_iso(burst_t + i * 0.5)} h-{rng.randint(0, 3)} ERROR job-{i} lost lease"
        )
    lines.sort()
    res = lt.triage([str(_write(tmp_path / "a.log", lines))])
    assert res.entities[
        "dropped_templates"
    ], "a template covering most entities must be left out"
    assert any("describes the baseline" in w for w in res.warnings)


def test_counts_add_up(tmp_path):
    res = lt.triage([str(_steady(tmp_path / "a.log"))])
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
    res = lt.triage([str(a), str(b)])
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
    res = lt.triage([str(a), str(b)])
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
    res = lt.triage([str(a), str(b)])
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
    res = lt.triage([str(path)])
    assert res.an.entries == n and res.checks["ok"]
    assert len(res.an.tpls) <= 3
    tpl = max(res.an.tpls.values(), key=lambda t: t.count)
    assert len(tpl.cells) <= lt.MAX_BUCKETS
    assert len(json.dumps(res.compact(), default=str)) < 12_000


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
    assert payload["generator"] == lt.OUTPUT_MARKER and payload["templates"]
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


@pytest.mark.parametrize(
    "name,needle",
    [
        ("notes.txt", ".json"),
        ("a.log", "input logs"),
        ("../outside.json", "inside a folder"),
    ],
)
def test_tool_refuses_bad_output_paths(tmp_path, name, needle):
    logs = tmp_path / "logs"
    logs.mkdir()
    _steady(logs / "a.log", hours=1)
    target = logs / name
    if name == "a.log":
        target = logs / "a.log"
    out = _call(paths=[str(logs)], output_path=str(target))
    assert out["status"] == "success"
    assert not out["output"]["written"] and needle in out["output"]["error"]


def test_tool_refuses_secret_files(tmp_path):
    secret = tmp_path / ".env"
    secret.write_text("TOKEN=x\n", encoding="utf-8")
    out = _call(paths=[str(secret)])
    assert out["status"] == "error" and "blocked" in out["error"].lower()


def test_tool_honours_gaia_allowed_paths(tmp_path, monkeypatch):
    inside = tmp_path / "in"
    outside = tmp_path / "out"
    inside.mkdir()
    outside.mkdir()
    _steady(outside / "a.log", hours=1)
    monkeypatch.setenv("GAIA_ALLOWED_PATHS", str(inside))
    monkeypatch.setattr(lt, "_SCOPE_VALIDATOR", None)
    out = _call(paths=[str(outside / "a.log")])
    assert out["status"] == "error" and "GAIA_ALLOWED_PATHS" in out["error"]
    monkeypatch.setattr(lt, "_SCOPE_VALIDATOR", None)


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
