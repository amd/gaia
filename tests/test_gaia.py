# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

import pytest
from gaia.cli import run_cli


def start_servers():
    # Start the servers
    print("Starting servers...")
    start_result = run_cli('start')
    assert start_result == f"Servers started successfully."


def stop_servers():
    # Stop the servers
    print("\nStopping servers...")
    stop_result = run_cli('stop')
    assert stop_result == "Servers stopped successfully."


@pytest.mark.parametrize("prompt", [
    "Who are you in one sentence.",
    "Tell me a short story.",
])
@pytest.mark.parametrize("model", [
    "llama3.2:1b",
    "llama3.2:3b",
    "llama3.1:8b",
])
def test_model_sweep(benchmark, prompt: str, model: str):
    print(f"\nStarting test_model_sweep with model: {model}...")

    # Use benchmark to measure the latency of the prompt
    def run_prompt():
        return run_cli('prompt', prompt, model=model)

    # Run benchmark in pedantic mode with 1 round and 1 iteration
    result = benchmark.pedantic(run_prompt, rounds=1, iterations=1)
    response = result.get('response')
    stats = result.get('stats')

    # Add custom metrics to the benchmark
    if stats:
        for key, value in stats.items():
            benchmark.extra_info[key] = value

    # Add your assertions here
    assert response is not None, "Response should not be None"
    assert isinstance(response, str), "Response should be a string"
    assert len(response) > 0, "Response should not be empty"

    print("Test completed successfully.")


if __name__ == "__main__":
    start_servers()
    # Update the pytest.main() call
    pytest.main([
        __file__,
        "--benchmark-json=output.json",
        "-v",  # for verbose output
        "-s",  # to show print statements
        "-k test_model_sweep[llama3.2:1b-Who"
    ])
    stop_servers()
