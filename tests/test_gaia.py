# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import pytest
from gaia.cli import run_cli, start_servers, stop_servers
from memory_profiler import profile, memory_usage
import matplotlib

matplotlib.use("Agg")  # Use non-interactive backend
import matplotlib.pyplot as plt


@pytest.mark.parametrize(
    "prompt",
    [
        "Who are you in one sentence.",
        "Tell me a short story.",
    ],
)
@pytest.mark.parametrize(
    "model",
    [
        "llama3.2:1b",
        "llama3.2:3b",
        "llama3.1:8b",
    ],
)
def test_model_sweep(benchmark, prompt: str, model: str):
    print(f"\nStarting test_model_sweep with model: {model}...")

    @profile(precision=4)
    def run_prompt():
        return run_cli("prompt", prompt, model=model, agent_name="Chaty")

    # Run benchmark in pedantic mode with 1 round and 1 iteration
    result = benchmark.pedantic(run_prompt, rounds=1, iterations=1)
    print(result)

    # Handle both string and dictionary responses
    response = result.get("response") if isinstance(result, dict) else result
    stats = result.get("stats") if isinstance(result, dict) else None

    # Get detailed memory usage
    mem_usage = memory_usage(
        (run_prompt,), interval=0.1, timeout=None, max_iterations=1
    )

    # Add custom metrics to the benchmark
    benchmark.extra_info["max_memory"] = max(mem_usage)
    benchmark.extra_info["min_memory"] = min(mem_usage)
    benchmark.extra_info["avg_memory"] = sum(mem_usage) / len(mem_usage)
    if stats:
        benchmark.extra_info.update(stats)

    # Add your assertions here
    assert "Error: 500" not in response, "Server returned 500 Internal Server Error"
    assert response is not None, "Response should not be None"
    assert isinstance(response, str), "Response should be a string"
    assert len(response) > 0, "Response should not be empty"

    print("Test completed successfully.")
    print(f"Max memory usage: {max(mem_usage)} MiB")
    print(f"Min memory usage: {min(mem_usage)} MiB")
    print(f"Avg memory usage: {sum(mem_usage) / len(mem_usage)} MiB")

    # Generate a detailed memory profile
    plt.figure(figsize=(10, 5))
    plt.plot(mem_usage)
    plt.title(f"Memory Usage for {model}")
    plt.xlabel("Time")
    plt.ylabel("Memory usage (MiB)")
    plt.savefig(f'memory_profile_{model.replace(":", "_")}.png')
    plt.close()


def test_stats_command(benchmark):
    print("\nStarting test_stats_command...")

    @profile(precision=4)
    def run_stats():
        return run_cli("stats")

    # Run benchmark in pedantic mode with 1 round and 1 iteration
    result = benchmark.pedantic(run_stats, rounds=1, iterations=1)
    print(f"Stats result: {result}")

    # Validate the stats response structure
    assert isinstance(result, dict), "Stats result should be a dictionary"
    assert "stats" in result, "Stats result should contain a 'stats' key"
    assert isinstance(result["stats"], dict), "Stats should be a dictionary"

    # Validate specific stats fields
    stats = result["stats"]
    required_fields = [
        "time_to_first_token",
        "tokens_per_second",
        "input_tokens",
        "output_tokens",
        "decode_token_times",
    ]

    for field in required_fields:
        assert field in stats, f"Stats should contain '{field}'"

    # Type and value checks
    assert isinstance(
        stats["time_to_first_token"], (int, float)
    ), "time_to_first_token should be numeric"
    assert isinstance(
        stats["tokens_per_second"], (int, float)
    ), "tokens_per_second should be numeric"
    assert isinstance(stats["input_tokens"], int), "input_tokens should be an integer"
    assert isinstance(stats["output_tokens"], int), "output_tokens should be an integer"
    assert isinstance(
        stats["decode_token_times"], list
    ), "decode_token_times should be a list"

    # Value range checks
    assert (
        stats["time_to_first_token"] >= 0
    ), "time_to_first_token should be non-negative"
    assert stats["tokens_per_second"] >= 0, "tokens_per_second should be non-negative"
    assert stats["input_tokens"] >= 0, "input_tokens should be non-negative"
    assert stats["output_tokens"] >= 0, "output_tokens should be non-negative"
    assert all(
        t >= 0 for t in stats["decode_token_times"]
    ), "all decode times should be non-negative"

    # Get memory usage for stats command
    mem_usage = memory_usage((run_stats,), interval=0.1, timeout=None, max_iterations=1)

    # Add custom metrics to the benchmark
    benchmark.extra_info["max_memory"] = max(mem_usage)
    benchmark.extra_info["min_memory"] = min(mem_usage)
    benchmark.extra_info["avg_memory"] = sum(mem_usage) / len(mem_usage)

    print("Stats test completed successfully.")
    print(f"Max memory usage: {max(mem_usage)} MiB")
    print(f"Min memory usage: {min(mem_usage)} MiB")
    print(f"Avg memory usage: {sum(mem_usage) / len(mem_usage)} MiB")

    # Generate memory profile
    plt.figure(figsize=(10, 5))
    plt.plot(mem_usage)
    plt.title("Memory Usage for Stats Command")
    plt.xlabel("Time")
    plt.ylabel("Memory usage (MiB)")
    plt.savefig("memory_profile_stats.png")
    plt.close()


if __name__ == "__main__":
    start_servers()
    # Update the pytest.main() call
    pytest.main(
        [
            __file__,
            "--benchmark-json=output.json",
            "-v",  # for verbose output
            "-s",  # to show print statements
            "-k",
            "test_model_sweep[llama3.2:1b-Who",
        ]
    )
    stop_servers()
