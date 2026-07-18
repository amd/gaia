# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for gaia.perf_analysis (regex log parsing + aggregation, issue #1998).

Covers ``extract_metrics`` against realistic llama-server / Lemonade telemetry
log lines (built from the module's own regexes), the malformed/empty-log
paths, the prefill/decode aggregation in ``run_perf_visualization``, and the
plotting functions exercised headlessly via the Agg backend (no display, no
`plt.show()` blocking).
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg", force=True)

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from gaia.perf_analysis import (  # noqa: E402
    METRIC_CONFIGS,
    Metric,
    build_plot,
    build_prefill_decode_pies,
    extract_metrics,
    main,
    parse_cli,
    run_perf_visualization,
)

# ---- extract_metrics: realistic matching lines ----


def _lines(text: str) -> list[str]:
    return text.splitlines()


def test_extract_metrics_single_request_all_fields_on_separate_lines():
    log = """\
slot update_slots: id  0 | task 0 | new prompt, n_ctx_slot = 4096, n_keep = 0
slot update_slots: id  0 | task 0 | n_prompt_tokens = 128
Input tokens: 128
Output tokens: 256
TTFT (s): 0.357
TPS: 53.21
"""
    metrics = extract_metrics(_lines(log))

    assert metrics[Metric.PROMPT_TOKENS] == [128.0]
    assert metrics[Metric.INPUT_TOKENS] == [128.0]
    assert metrics[Metric.OUTPUT_TOKENS] == [256.0]
    assert metrics[Metric.TTFT] == [0.357]
    assert metrics[Metric.TPS] == [53.21]


def test_extract_metrics_marker_and_token_count_on_same_line():
    """llama-server actually logs both on one line: `new prompt, ..., n_prompt_tokens = N`."""
    log = (
        "slot update_slots: id  0 | task 12 | new prompt, n_ctx_slot = 4096, "
        "n_keep = 0, n_prompt_tokens = 64"
    )
    metrics = extract_metrics(_lines(log))

    assert metrics[Metric.PROMPT_TOKENS] == [64.0]


def test_extract_metrics_case_insensitive_labels():
    log = """\
input tokens: 10
OUTPUT TOKENS: 20
ttft (S): 0.1
tps: 99.5
"""
    metrics = extract_metrics(_lines(log))

    assert metrics[Metric.INPUT_TOKENS] == [10.0]
    assert metrics[Metric.OUTPUT_TOKENS] == [20.0]
    assert metrics[Metric.TTFT] == [0.1]
    assert metrics[Metric.TPS] == [99.5]


def test_extract_metrics_multiple_requests_appends_in_order():
    log = """\
slot update_slots: new prompt, n_prompt_tokens = 128
Input tokens: 128
Output tokens: 256
TTFT (s): 0.357
TPS: 53.21
slot update_slots: new prompt, n_prompt_tokens = 512
Input tokens: 512
Output tokens: 64
TTFT (s): 1.204
TPS: 41.09
"""
    metrics = extract_metrics(_lines(log))

    assert metrics[Metric.PROMPT_TOKENS] == [128.0, 512.0]
    assert metrics[Metric.INPUT_TOKENS] == [128.0, 512.0]
    assert metrics[Metric.OUTPUT_TOKENS] == [256.0, 64.0]
    assert metrics[Metric.TTFT] == [0.357, 1.204]
    assert metrics[Metric.TPS] == [53.21, 41.09]


# ---- extract_metrics: empty / malformed input ----


def test_extract_metrics_empty_log_returns_empty_lists_for_every_metric():
    metrics = extract_metrics([])

    assert set(metrics.keys()) == set(Metric)
    assert all(values == [] for values in metrics.values())


def test_extract_metrics_malformed_lines_are_skipped_without_crashing():
    log = """\
Input tokens missing colon 128
TTFT (s) 0.5 missing colon
random unrelated log noise
n_prompt_tokens = 99 with no marker line before it
"""
    metrics = extract_metrics(_lines(log))

    assert metrics[Metric.INPUT_TOKENS] == []
    assert metrics[Metric.TTFT] == []
    # n_prompt_tokens only counts when a "new prompt" marker preceded it.
    assert metrics[Metric.PROMPT_TOKENS] == []


def test_extract_metrics_non_numeric_token_count_does_not_resolve_await():
    """A malformed count after the marker must not silently produce a bogus value,
    and the awaiting flag should persist until a real digit match arrives."""
    log = """\
new prompt marker line
n_prompt_tokens = not-a-number
n_prompt_tokens = 77
"""
    metrics = extract_metrics(_lines(log))

    assert metrics[Metric.PROMPT_TOKENS] == [77.0]


def test_extract_metrics_repeated_marker_before_resolution_is_ignored():
    """Guard: a second 'new prompt' while still awaiting a token count must not
    reset/duplicate — only the eventual n_prompt_tokens match counts once."""
    log = """\
new prompt (first)
new prompt (second, still awaiting)
n_prompt_tokens = 77
"""
    metrics = extract_metrics(_lines(log))

    assert metrics[Metric.PROMPT_TOKENS] == [77.0]


def test_extract_metrics_multi_metric_single_line():
    """All patterns use independent `.search()` calls (no elif), so a single
    line carrying several fields must populate all of them."""
    log = "Input tokens: 5 Output tokens: 6 TTFT (s): 0.2 TPS: 30"
    metrics = extract_metrics(_lines(log))

    assert metrics[Metric.INPUT_TOKENS] == [5.0]
    assert metrics[Metric.OUTPUT_TOKENS] == [6.0]
    assert metrics[Metric.TTFT] == [0.2]
    assert metrics[Metric.TPS] == [30.0]


# ---- run_perf_visualization: aggregation + headless plotting ----


def _write_log(path: Path, requests: list[dict]) -> None:
    lines = []
    for req in requests:
        lines.append(
            f"slot update_slots: new prompt, n_prompt_tokens = {req['prompt']}"
        )
        lines.append(f"Input tokens: {req['input']}")
        lines.append(f"Output tokens: {req['output']}")
        lines.append(f"TTFT (s): {req['ttft']}")
        lines.append(f"TPS: {req['tps']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_run_perf_visualization_end_to_end_creates_plots_and_aggregates(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    log_path = tmp_path / "server.log"
    _write_log(
        log_path,
        [
            {"prompt": 128, "input": 128, "output": 200, "ttft": 0.5, "tps": 50.0},
            {"prompt": 64, "input": 64, "output": 100, "ttft": 0.25, "tps": 40.0},
        ],
    )

    rc = run_perf_visualization([log_path], show=False)

    assert rc == 0
    for metric_config in METRIC_CONFIGS.values():
        assert (tmp_path / metric_config.filename).exists()
    assert (tmp_path / "prefill_decode_split.png").exists()

    out = capsys.readouterr().out
    assert "Saved" in out
    assert (
        "5 entries" not in out
    )  # sanity: not accidentally summing all metrics together
    assert "2 entries from 1 log(s)" in out


def test_run_perf_visualization_missing_file_errors_without_crash(tmp_path, capsys):
    missing = tmp_path / "does-not-exist.log"

    rc = run_perf_visualization([missing], show=False)

    assert rc == 1
    err = capsys.readouterr().err
    assert "log file not found" in err


def test_run_perf_visualization_log_missing_a_metric_errors(
    tmp_path, monkeypatch, capsys
):
    """A log with prompt/input/output tokens but no TTFT/TPS lines must fail loudly,
    not silently plot a partial/empty series."""
    monkeypatch.chdir(tmp_path)
    log_path = tmp_path / "partial.log"
    log_path.write_text(
        "slot update_slots: new prompt, n_prompt_tokens = 10\n"
        "Input tokens: 10\n"
        "Output tokens: 20\n",
        encoding="utf-8",
    )

    rc = run_perf_visualization([log_path], show=False)

    assert rc == 1
    err = capsys.readouterr().err
    assert "were found in the log" in err


def test_run_perf_visualization_computes_prefill_decode_split(tmp_path, monkeypatch):
    """Aggregation: prefill = sum(TTFT); decode = sum(output_tokens / tps) for tps > 0."""
    monkeypatch.chdir(tmp_path)
    log_path = tmp_path / "server.log"
    _write_log(
        log_path,
        [
            {"prompt": 10, "input": 10, "output": 100, "ttft": 1.0, "tps": 50.0},
            {"prompt": 10, "input": 10, "output": 50, "ttft": 0.5, "tps": 25.0},
        ],
    )

    captured_calls = []
    real_build_pies = build_prefill_decode_pies

    def _spy(prefill_decode_times, output_path, show):
        captured_calls.append(prefill_decode_times)
        return real_build_pies(prefill_decode_times, output_path, show)

    import gaia.perf_analysis as perf_analysis_mod

    orig = perf_analysis_mod.build_prefill_decode_pies
    perf_analysis_mod.build_prefill_decode_pies = _spy
    try:
        rc = run_perf_visualization([log_path], show=False)
    finally:
        perf_analysis_mod.build_prefill_decode_pies = orig

    assert rc == 0
    assert len(captured_calls) == 1
    log_name, prefill_total, decode_total = captured_calls[0][0]
    assert log_name == "server.log"
    assert prefill_total == pytest.approx(1.5)  # 1.0 + 0.5
    assert decode_total == pytest.approx(100 / 50.0 + 50 / 25.0)  # 2.0 + 2.0 = 4.0


# ---- plotting functions: headless (Agg), no display ----


def test_build_plot_saves_png_without_display(tmp_path):
    output_path = tmp_path / "prompt_token_counts.png"
    build_plot(
        series=[("run-a.log", [1.0, 2.0, 3.0]), ("run-b.log", [4.0, 5.0])],
        metric_config=METRIC_CONFIGS[Metric.PROMPT_TOKENS],
        output_path=output_path,
        show=False,
    )

    assert output_path.exists()
    assert output_path.stat().st_size > 0


def test_build_plot_show_true_does_not_hang_on_agg_backend(tmp_path):
    """Agg is non-interactive, so show=True must return immediately, not block."""
    output_path = tmp_path / "tps.png"
    build_plot(
        series=[("run-a.log", [10.0])],
        metric_config=METRIC_CONFIGS[Metric.TPS],
        output_path=output_path,
        show=True,
    )

    assert output_path.exists()


def test_build_prefill_decode_pies_saves_png(tmp_path):
    output_path = tmp_path / "prefill_decode_split.png"
    build_prefill_decode_pies(
        prefill_decode_times=[("run-a.log", 1.5, 4.0), ("run-b.log", 0.2, 1.1)],
        output_path=output_path,
        show=False,
    )

    assert output_path.exists()
    assert output_path.stat().st_size > 0


def test_build_prefill_decode_pies_empty_input_prints_message_and_skips(
    tmp_path, capsys
):
    output_path = tmp_path / "prefill_decode_split.png"
    build_prefill_decode_pies(
        prefill_decode_times=[], output_path=output_path, show=False
    )

    assert not output_path.exists()
    err = capsys.readouterr().err
    assert "No timing data available" in err


def test_build_prefill_decode_pies_zero_total_time_slot_is_skipped(tmp_path):
    """A (0, 0) prefill/decode pair must not raise (division by total_time would)."""
    output_path = tmp_path / "prefill_decode_split.png"
    build_prefill_decode_pies(
        prefill_decode_times=[("empty.log", 0.0, 0.0), ("real.log", 1.0, 2.0)],
        output_path=output_path,
        show=False,
    )

    assert output_path.exists()


# ---- CLI dispatch: parse_cli / main() ----


def test_parse_cli_parses_log_paths_and_show_flag():
    args = parse_cli(["a.log", "b.log", "--show"])

    assert args.log_paths == [Path("a.log"), Path("b.log")]
    assert args.show is True


def test_parse_cli_show_defaults_false():
    args = parse_cli(["a.log"])

    assert args.show is False


def test_parse_cli_requires_at_least_one_log_path():
    with pytest.raises(SystemExit) as excinfo:
        parse_cli([])
    assert excinfo.value.code == 2


def test_main_forwards_parsed_log_paths_and_show_to_run_perf_visualization(mocker):
    mock_run = mocker.patch("gaia.perf_analysis.run_perf_visualization", return_value=0)

    rc = main(["x.log", "y.log", "--show"])

    assert rc == 0
    mock_run.assert_called_once_with([Path("x.log"), Path("y.log")], show=True)
