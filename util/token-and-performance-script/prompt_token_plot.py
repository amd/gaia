#!/usr/bin/env python3
"""
Generate plots of prompt and response telemetry from one or more llama.cpp server logs.

The script scans the logs for prompt tokens, input tokens, output tokens, TTFT, and TPS values,
producing line charts per metric with one series per log file.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import matplotlib.pyplot as plt


PROMPT_MARKER = "new prompt"
TOKEN_PATTERN = re.compile(r"n_prompt_tokens\s*=\s*(\d+)")
INPUT_PATTERN = re.compile(r"Input tokens:\s*(\d+)", re.IGNORECASE)
OUTPUT_PATTERN = re.compile(r"Output tokens:\s*(\d+)", re.IGNORECASE)
TTFT_PATTERN = re.compile(r"TTFT\s*\(s\):\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
TPS_PATTERN = re.compile(r"TPS:\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)


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
    output_path: Path | None,
    show: bool,
) -> None:
    """Create the plot and either save it, display it, or both."""
    fig, ax = plt.subplots()

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

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path)

    if show:
        plt.show()
    else:
        plt.close(fig)


def parse_cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plot telemetry values (prompt/input/output tokens, TTFT, TPS) recorded in "
            "one or more llama.cpp server logs."
        )
    )
    parser.add_argument(
        "log_paths",
        type=Path,
        nargs="+",
        help="One or more paths to llama.cpp server log files.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate plots for prompt tokens, input tokens, output tokens, TTFT, and TPS.",
    )
    parser.add_argument(
        "--prompt-tokens",
        action="store_true",
        dest="prompt_tokens",
        help="Generate a plot for prompt token counts (from n_prompt_tokens).",
    )
    parser.add_argument(
        "--input-tokens",
        action="store_true",
        dest="input_tokens",
        help="Generate a plot for input token counts (from 'Input tokens:' lines).",
    )
    parser.add_argument(
        "--output-tokens",
        action="store_true",
        dest="output_tokens",
        help="Generate a plot for output token counts.",
    )
    parser.add_argument(
        "--ttft",
        action="store_true",
        help="Generate a plot for time to first token (seconds).",
    )
    parser.add_argument(
        "--tps",
        action="store_true",
        help="Generate a plot for tokens per second.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Display the plot window in addition to saving the image.",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Display the plot without saving an image.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_cli()

    if args.all:
        selected_metrics = list(Metric)
    else:
        selected_metrics = [
            metric
            for flag, metric in (
                ("prompt_tokens", Metric.PROMPT_TOKENS),
                ("input_tokens", Metric.INPUT_TOKENS),
                ("output_tokens", Metric.OUTPUT_TOKENS),
                ("ttft", Metric.TTFT),
                ("tps", Metric.TPS),
            )
            if getattr(args, flag)
        ]

    if not selected_metrics:
        selected_metrics = [Metric.PROMPT_TOKENS]

    series_by_metric: Dict[Metric, List[Tuple[str, List[float]]]] = {
        metric: [] for metric in selected_metrics
    }

    for log_path in args.log_paths:
        if not log_path.is_file():
            print(f"error: log file not found: {log_path}", file=sys.stderr)
            return 1

        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as fh:
                metric_values = extract_metrics(fh)
        except OSError as exc:
            print(f"error: failed to read log file {log_path}: {exc}", file=sys.stderr)
            return 1

        for metric in selected_metrics:
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

    for metric in selected_metrics:
        output_path = None if args.no_save else METRIC_CONFIGS[metric].filename
        build_plot(
            series_by_metric[metric],
            metric_config=METRIC_CONFIGS[metric],
            output_path=output_path,
            show=args.show,
        )

        if output_path is not None:
            total_points = sum(len(counts) for _, counts in series_by_metric[metric])
            print(
                f"Saved {METRIC_CONFIGS[metric].title.lower()} plot with "
                f"{total_points} entries from {len(series_by_metric[metric])} log(s) "
                f"to {output_path}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
