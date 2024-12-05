# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import json
import matplotlib.pyplot as plt
import sys
from collections import defaultdict


def plot_benchmark_results(json_file):
    with open(json_file, "r") as f:
        data = json.load(f)

    benchmarks = data["benchmarks"]
    if not benchmarks:
        print("No benchmark data found.")
        return

    # Prepare data
    prompt_data = defaultdict(lambda: defaultdict(list))
    all_models = set()

    for benchmark in benchmarks:
        model = benchmark["params"]["model"]
        prompt = benchmark["params"]["prompt"]
        ttft = benchmark["extra_info"]["ttft"]
        tokens_per_sec = benchmark["extra_info"]["tokens_per_sec"]

        prompt_data[prompt]["models"].append(model)
        prompt_data[prompt]["ttft"].append(ttft)
        prompt_data[prompt]["tokens_per_sec"].append(tokens_per_sec)
        all_models.add(model)

    # Set up the individual plots
    fig_ttft, ax_ttft = plt.subplots(figsize=(12, 6))
    fig_tps, ax_tps = plt.subplots(figsize=(12, 6))

    # Set up the combined plot
    fig_combined, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 12))
    fig_combined.suptitle("Benchmark Results: TTFT and Tokens per Second")

    # Plot data for each prompt
    for prompt, data in prompt_data.items():
        models = data["models"]
        ttft_values = data["ttft"]
        tokens_per_sec_values = data["tokens_per_sec"]

        # Plot TTFT (individual and combined)
        ax_ttft.plot(models, ttft_values, marker="o", linestyle="-", label=prompt)
        ax1.plot(models, ttft_values, marker="o", linestyle="-", label=prompt)

        # Plot Tokens per Second (individual and combined)
        ax_tps.plot(
            models, tokens_per_sec_values, marker="s", linestyle="-", label=prompt
        )
        ax2.plot(models, tokens_per_sec_values, marker="s", linestyle="-", label=prompt)

    # Set up axes for all plots
    for ax in (ax_ttft, ax_tps, ax1, ax2):
        ax.set_xlabel("Models")
        ax.set_xticks(range(len(all_models)))
        ax.set_xticklabels(sorted(all_models), rotation=45, ha="right")
        ax.legend()

    # Set up individual TTFT plot
    ax_ttft.set_ylabel("TTFT (seconds)")
    ax_ttft.set_title("Time to First Token (TTFT) by Model and Prompt")

    # Set up individual Tokens per Second plot
    ax_tps.set_ylabel("Tokens per Second")
    ax_tps.set_title("Tokens per Second by Model and Prompt")

    # Set up combined plot axes
    ax1.set_ylabel("TTFT (seconds)")
    ax1.set_title("Time to First Token (TTFT) by Model and Prompt")

    ax2.set_ylabel("Tokens per Second")
    ax2.set_title("Tokens per Second by Model and Prompt")

    # Adjust layout and save individual plots
    fig_ttft.tight_layout()
    fig_ttft.savefig("benchmark_results_ttft.png", bbox_inches="tight")

    fig_tps.tight_layout()
    fig_tps.savefig("benchmark_results_tokens_per_sec.png", bbox_inches="tight")

    # Adjust layout and save combined plot
    fig_combined.tight_layout()
    fig_combined.savefig("benchmark_results_combined.png", bbox_inches="tight")

    plt.show()


if __name__ == "__main__":
    """
    Usage: python plot_results.py <path_to_json_file>
    Example: python plot_results.py benchmark_results.json

    This script visualizes benchmark results from a JSON file created by pytest-benchmark.
    It creates two line plots:
    1. Models vs. Time to First Token (TTFT) for all prompts
    2. Models vs. Tokens per Second for all prompts

    The resulting plots will be saved as 'benchmark_results_ttft.png' and
    'benchmark_results_tokens_per_sec.png' in the current directory and displayed
    on screen if running in an interactive environment.
    """
    if len(sys.argv) != 2:
        print("Usage: python plot_results.py <path_to_json_file>")
        sys.exit(1)

    json_file = sys.argv[1]
    plot_benchmark_results(json_file)
