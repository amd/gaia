# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Sequence, Tuple

try:
    import matplotlib.pyplot as plt
except ImportError:  # pragma: no cover - handled at runtime
    plt = None  # type: ignore[assignment]

PROMPT_MARKER = "new prompt"
TOKEN_PATTERN = re.compile(r"n_prompt_tokens\s*=\s*(\d+)")
INPUT_PATTERN = re.compile(r"Input tokens:\s*(\d+)", re.IGNORECASE)
OUTPUT_PATTERN = re.compile(r"Output tokens:\s*(\d+)", re.IGNORECASE)
TTFT_PATTERN = re.compile(r"TTFT\s*\(s\):\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
TPS_PATTERN = re.compile(r"TPS:\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)


def _require_matplotlib() -> Any:
    if plt is None:
        raise RuntimeError(
            "matplotlib is required for perf-vis. "
            "Install with `pip install matplotlib` or `pip install -e .[dev]`."
        )
    return plt


class Metric(str, Enum):
    PROMPT_TOKENS = "prompt_tokens"
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    TTFT = "ttft"
    TPS = "tps"


@dataclass(frozen=True)
class MetricConfig:
    title: str
    y_label: str
    filename: Path


METRIC_CONFIGS: Dict[Metric, MetricConfig] = {
    Metric.PROMPT_TOKENS: MetricConfig(
        title="Prompt token counts",
        y_label="Prompt token count",
        filename=Path("prompt_token_counts.png"),
    ),
    Metric.INPUT_TOKENS: MetricConfig(
        title="Input token counts",
        y_label="Input token count",
        filename=Path("input_token_counts.png"),
    ),
    Metric.OUTPUT_TOKENS: MetricConfig(
        title="Output token counts",
        y_label="Output token count",
        filename=Path("output_token_counts.png"),
    ),
    Metric.TTFT: MetricConfig(
        title="Time to first token (s)",
        y_label="Seconds to first token",
        filename=Path("ttft_seconds.png"),
    ),
    Metric.TPS: MetricConfig(
        title="Tokens per second",
        y_label="Tokens per second",
        filename=Path("tps.png"),
    ),
}


def extract_metrics(lines: Iterable[str]) -> Dict[Metric, List[float]]:
    """Collect telemetry values by metric."""
    values: Dict[Metric, List[float]] = {metric: [] for metric in Metric}
    all_lines = list(lines)
    awaiting_prompt_token = False

    for raw_line in all_lines:
        line = raw_line.strip()
        lower_line = line.lower()

        if not awaiting_prompt_token and PROMPT_MARKER in lower_line:
            awaiting_prompt_token = True

        input_match = INPUT_PATTERN.search(line)
        if input_match:
            values[Metric.INPUT_TOKENS].append(float(input_match.group(1)))

        if awaiting_prompt_token:
            prompt_match = TOKEN_PATTERN.search(line)
            if prompt_match:
                values[Metric.PROMPT_TOKENS].append(float(prompt_match.group(1)))
                awaiting_prompt_token = False

        output_match = OUTPUT_PATTERN.search(line)
        if output_match:
            values[Metric.OUTPUT_TOKENS].append(float(output_match.group(1)))

        ttft_match = TTFT_PATTERN.search(line)
        if ttft_match:
            values[Metric.TTFT].append(float(ttft_match.group(1)))

        tps_match = TPS_PATTERN.search(line)
        if tps_match:
            values[Metric.TPS].append(float(tps_match.group(1)))

    return values


def build_plot(
    series: Sequence[Tuple[str, List[float]]],
    metric_config: MetricConfig,
    output_path: Path,
    show: bool,
) -> None:
    """Create the plot and either save it, display it, or both."""
    plt_mod = _require_matplotlib()
    fig, ax = plt_mod.subplots()

    for log_name, metric_values in series:
        x_values = range(1, len(metric_values) + 1)
        ax.plot(x_values, metric_values, marker="o", linestyle="-", label=log_name)

    ax.set_xlabel("LLM call/inference count")
    ax.set_ylabel(metric_config.y_label)
    title = metric_config.title
    if len(series) == 1:
        title = f"{title} - {series[0][0]}"
    ax.set_title(title)
    ax.grid(True, linestyle="--", alpha=0.4)
    if len(series) > 1:
        ax.legend(title="Log file")
    fig.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)

    if show:
        plt_mod.show()
    else:
        plt_mod.close(fig)


def build_prefill_decode_pies(
    prefill_decode_times: Sequence[Tuple[str, float, float]],
    output_path: Path,
    show: bool,
) -> None:
    """Plot per-log prefill vs decode time split as multiple pies."""
    plt_mod = _require_matplotlib()

    if not prefill_decode_times:
        print(
            "No timing data available to build prefill/decode split.", file=sys.stderr
        )
        return

    slice_colors = ["#4c78a8", "#f58518"]  # Prefill, decode
    pie_outline_colors = plt_mod.get_cmap("tab10").colors
    cols = min(len(prefill_decode_times), 3)
    rows = math.ceil(len(prefill_decode_times) / cols)

    def autopct_with_seconds(values: Sequence[float]) -> Callable[[float], str]:
        def format_pct(pct: float) -> str:
            seconds = pct / 100 * sum(values)
            return f"{pct:.1f}%\n{seconds:.2f}s"

        return format_pct

    fig, axes = plt_mod.subplots(rows, cols, figsize=(4 * cols, 4 * rows))
    axes_flat: List[plt_mod.Axes] = (
        [axes]
        if isinstance(axes, plt_mod.Axes)
        else list(axes.ravel() if hasattr(axes, "ravel") else axes)
    )

    legend_handles = []

    for idx, (log_name, prefill_total, decode_total) in enumerate(prefill_decode_times):
        total_time = prefill_total + decode_total
        if total_time <= 0:
            axes_flat[idx].axis("off")
            continue

        ax = axes_flat[idx]
        sizes = [prefill_total, decode_total]
        outline_color = pie_outline_colors[idx % len(pie_outline_colors)]

        ax.pie(
            sizes,
            labels=["Prefill", "Decode"],
            colors=slice_colors,
            autopct=autopct_with_seconds(sizes),
            startangle=90,
            wedgeprops={"edgecolor": outline_color, "linewidth": 2},
        )
        ax.axis("equal")  # Equal aspect ratio for a true circle.
        ax.set_title(log_name)
        ax.text(
            0,
            0,
            f"Total\n{total_time:.2f}s",
            ha="center",
            va="center",
            fontsize=10,
            weight="bold",
        )

        legend_handles.append(
            plt_mod.Line2D(
                [0],
                [0],
                marker="o",
                color="w",
                markerfacecolor="w",
                markeredgecolor=outline_color,
                markeredgewidth=2,
                markersize=10,
                label=log_name,
            )
        )

    for ax in axes_flat[len(prefill_decode_times) :]:
        ax.axis("off")

    fig.legend(
        handles=legend_handles,
        loc="upper center",
        ncol=min(len(legend_handles), cols),
        title="Log file",
    )
    fig.suptitle("Prefill vs decode time split", y=0.98)
    fig.tight_layout(rect=(0, 0, 1, 0.92))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)

    if show:
        plt_mod.show()
    else:
        plt_mod.close(fig)


def parse_cli(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plot telemetry values (prompt/input/output tokens, TTFT, TPS) "
            "recorded in one or more llama.cpp server logs."
        )
    )
    parser.add_argument(
        "log_paths",
        type=Path,
        nargs="+",
        help="One or more paths to llama.cpp server log files.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Display the plot window in addition to saving the image.",
    )
    return parser.parse_args(argv)


def run_perf_visualization(log_paths: Sequence[Path], show: bool = False) -> int:
    """Process log files and generate performance plots."""
    try:
        _ = _require_matplotlib()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    metrics = list(Metric)
    series_by_metric: Dict[Metric, List[Tuple[str, List[float]]]] = {
        metric: [] for metric in metrics
    }
    prefill_decode_times: List[Tuple[str, float, float]] = []

    for log_path in log_paths:
        if not log_path.is_file():
            print(f"error: log file not found: {log_path}", file=sys.stderr)
            return 1

        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as fh:
                metric_values = extract_metrics(fh)
        except OSError as exc:
            print(f"error: failed to read log file {log_path}: {exc}", file=sys.stderr)
            return 1

        for metric in metrics:
            values = metric_values.get(metric, [])
            if not values:
                print(
                    f"No {METRIC_CONFIGS[metric].title.lower()} were found in the log: "
                    f"{log_path}",
                    file=sys.stderr,
                )
                return 1

            log_name = log_path.name or str(log_path)
            series_by_metric[metric].append((log_name, values))

        prefill_total_time = sum(metric_values[Metric.TTFT])
        decode_total_time = sum(
            output_tokens / tps
            for output_tokens, tps in zip(
                metric_values[Metric.OUTPUT_TOKENS], metric_values[Metric.TPS]
            )
            if tps > 0
        )
        prefill_decode_times.append(
            (log_path.name or str(log_path), prefill_total_time, decode_total_time)
        )

    for metric in metrics:
        build_plot(
            series_by_metric[metric],
            metric_config=METRIC_CONFIGS[metric],
            output_path=METRIC_CONFIGS[metric].filename,
            show=show,
        )

        total_points = sum(len(counts) for _, counts in series_by_metric[metric])
        print(
            f"Saved {METRIC_CONFIGS[metric].title.lower()} plot with "
            f"{total_points} entries from {len(series_by_metric[metric])} log(s) "
            f"to {METRIC_CONFIGS[metric].filename}"
        )

    prefill_decode_path = Path("prefill_decode_split.png")
    build_prefill_decode_pies(
        prefill_decode_times=prefill_decode_times,
        output_path=prefill_decode_path,
        show=show,
    )
    if prefill_decode_times:
        print(
            f"Saved prefill vs decode time split pies for "
            f"{len(prefill_decode_times)} log(s) to {prefill_decode_path}"
        )

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_cli(argv)
    return run_perf_visualization(args.log_paths, show=args.show)


if __name__ == "__main__":
    raise SystemExit(main())
