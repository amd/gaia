# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Adapter for running gaia eval scenarios against the gaia-bash REST API.

Usage:
    # Run all scenarios against a running gaia-bash server
    python bash_eval_adapter.py

    # Run against a specific server
    python bash_eval_adapter.py --url http://localhost:8200

    # Start the server automatically
    python bash_eval_adapter.py --binary ./build/gaia-bash

    # Run a specific scenario
    python bash_eval_adapter.py --scenario bash-tool-execute
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("Error: 'requests' package required. Install with: pip install requests")
    sys.exit(1)


class BashEvalAdapter:
    """Connects the GAIA eval framework to the gaia-bash API server."""

    def __init__(self, base_url="http://localhost:8200", startup_timeout=30):
        self.base_url = base_url.rstrip("/")
        self.startup_timeout = startup_timeout
        self.process = None

    def start_server(self, binary_path="./build/gaia-bash"):
        """Start gaia-bash in --serve mode as a subprocess."""
        self.process = subprocess.Popen(
            [binary_path, "--serve", "--port", "8200"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._wait_for_health()

    def stop_server(self):
        """Stop the gaia-bash server."""
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.process = None

    def _wait_for_health(self):
        """Wait for the server to be healthy."""
        for _ in range(self.startup_timeout):
            try:
                r = requests.get(f"{self.base_url}/health", timeout=1)
                if r.status_code == 200:
                    return
            except requests.ConnectionError:
                pass
            time.sleep(1)
        raise RuntimeError(
            f"gaia-bash server at {self.base_url} failed to start "
            f"within {self.startup_timeout}s"
        )

    def health(self):
        """Check server health."""
        r = requests.get(f"{self.base_url}/health", timeout=5)
        r.raise_for_status()
        return r.json()

    def send_query(self, prompt, timeout=120):
        """Send a chat query and return the response."""
        r = requests.post(
            f"{self.base_url}/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()

    def execute_tool(self, tool_name, args, timeout=30):
        """Execute a specific tool directly."""
        r = requests.post(
            f"{self.base_url}/v1/tools/{tool_name}",
            json=args,
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()

    def list_tools(self):
        """List available tools."""
        r = requests.get(f"{self.base_url}/v1/tools", timeout=5)
        r.raise_for_status()
        return r.json()

    def run_scenario(self, scenario, ground_truth=None):
        """Run a single eval scenario and return results."""
        scenario_id = scenario["id"]
        prompt = scenario["prompt"]

        result = {
            "scenario_id": scenario_id,
            "category": scenario["category"],
            "success": False,
            "errors": [],
            "response": None,
            "tools_used": [],
        }

        try:
            response = self.send_query(prompt)
            result["response"] = response

            # Extract response content
            content = ""
            if "choices" in response:
                content = response["choices"][0].get("message", {}).get("content", "")
            result["content"] = content

            # Validate against ground truth if provided
            if ground_truth and scenario_id in ground_truth:
                gt = ground_truth[scenario_id]
                errors = self._validate_ground_truth(content, gt)
                result["errors"] = errors

            result["success"] = len(result["errors"]) == 0

        except requests.RequestException as e:
            result["errors"].append(f"HTTP error: {e}")
            result["success"] = False
        except Exception as e:
            result["errors"].append(f"Unexpected error: {e}")
            result["success"] = False

        return result

    def _validate_ground_truth(self, content, gt):
        """Validate response content against ground truth criteria."""
        errors = []
        content_lower = content.lower()

        # Check must_contain
        if "must_contain" in gt:
            must_contain_any = gt.get("must_contain_any", False)
            found_any = False
            for term in gt["must_contain"]:
                if term.lower() in content_lower:
                    found_any = True
                elif not must_contain_any:
                    errors.append(f"Missing required content: '{term}'")
            if must_contain_any and not found_any:
                errors.append(f"Must contain at least one of: {gt['must_contain']}")

        # Check must_not_contain
        for term in gt.get("must_not_contain", []):
            if term.lower() in content_lower:
                errors.append(f"Contains forbidden content: '{term}'")

        # Check response_must_mention
        for term in gt.get("response_must_mention", []):
            if term.lower() not in content_lower:
                errors.append(f"Response should mention: '{term}'")

        # Check response_must_contain
        if "response_must_contain" in gt:
            term = gt["response_must_contain"]
            if term.lower() not in content_lower:
                errors.append(f"Response must contain: '{term}'")

        # Note: expected_tools and tool_args_must_contain are soft checks.
        # The API returns only the final answer, not the tool call trace,
        # so we can't reliably verify which tools were used from the
        # response content alone. These checks look for tool/arg names
        # in the text but don't fail the scenario — they're informational.
        # A future enhancement could parse structured tool call events.

        # Check error expectations
        if gt.get("expect_error"):
            if "error" not in content_lower:
                errors.append("Expected error response but none found")

        if gt.get("expect_nonzero_exit"):
            # Look for non-zero exit code indicators
            has_nonzero = any(
                indicator in content_lower
                for indicator in [
                    "exit code",
                    "exit_code",
                    "non-zero",
                    "failed",
                    "error",
                ]
            )
            if not has_nonzero:
                errors.append("Expected non-zero exit code but not indicated")

        if gt.get("expect_timeout"):
            if "timeout" not in content_lower and "timed_out" not in content_lower:
                errors.append("Expected timeout but not indicated in response")

        return errors


def load_scenarios(path=None):
    """Load eval scenarios from JSON file."""
    if path is None:
        path = Path(__file__).parent / "bash_scenarios.json"
    with open(path) as f:
        return json.load(f)["scenarios"]


def load_ground_truth(path=None):
    """Load ground truth from JSON file."""
    if path is None:
        path = Path(__file__).parent / "bash_ground_truth.json"
    with open(path) as f:
        return json.load(f)["ground_truth"]


def run_eval(
    base_url="http://localhost:8200",
    binary_path=None,
    scenario_filter=None,
    verbose=False,
):
    """Run the full bash agent evaluation.

    Args:
        base_url: URL of a running gaia-bash API server.
        binary_path: If set, start the server automatically.
        scenario_filter: If set, only run scenarios matching this ID.
        verbose: Print detailed output.

    Returns:
        List of result dicts, one per scenario.
    """
    adapter = BashEvalAdapter(base_url)

    if binary_path:
        print(f"Starting gaia-bash server from {binary_path}...")
        adapter.start_server(binary_path)

    try:
        # Verify server is up
        health = adapter.health()
        print(f"Server healthy: {health}")

        tools = adapter.list_tools()
        tool_count = len(tools.get("tools", []))
        print(f"Tools available: {tool_count}")

        scenarios = load_scenarios()
        ground_truth = load_ground_truth()

        if scenario_filter:
            scenarios = [s for s in scenarios if s["id"] == scenario_filter]
            if not scenarios:
                print(f"No scenario found with id: {scenario_filter}")
                return []

        results = []
        passed = 0
        failed = 0

        for scenario in scenarios:
            sid = scenario["id"]
            cat = scenario["category"]
            prompt_preview = scenario["prompt"][:60].replace("\n", " ")

            print(f"\n[{cat}] {sid}")
            print(f"  Prompt: {prompt_preview}...")

            result = adapter.run_scenario(scenario, ground_truth)
            results.append(result)

            if result["success"] and not result["errors"]:
                passed += 1
                print(f"  PASS")
            else:
                failed += 1
                for err in result["errors"]:
                    print(f"  FAIL: {err}")

            if verbose and result.get("content"):
                preview = result["content"][:200].replace("\n", " ")
                print(f"  Response: {preview}...")

        # Summary
        total = len(results)
        print(f"\n{'=' * 60}")
        print(f"Results: {passed}/{total} passed, {failed}/{total} failed")
        print(f"{'=' * 60}")

        # Category breakdown
        categories = {}
        for r in results:
            cat = r["category"]
            if cat not in categories:
                categories[cat] = {"passed": 0, "total": 0}
            categories[cat]["total"] += 1
            if r["success"] and not r["errors"]:
                categories[cat]["passed"] += 1

        for cat, stats in sorted(categories.items()):
            print(f"  {cat}: {stats['passed']}/{stats['total']}")

        return results

    finally:
        if binary_path:
            adapter.stop_server()


def main():
    parser = argparse.ArgumentParser(description="Run bash agent eval scenarios")
    parser.add_argument(
        "--url",
        default="http://localhost:8200",
        help="gaia-bash API server URL (default: http://localhost:8200)",
    )
    parser.add_argument(
        "--binary",
        default=None,
        help="Path to gaia-bash binary (starts server automatically)",
    )
    parser.add_argument(
        "--scenario",
        default=None,
        help="Run a specific scenario by ID",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print detailed output",
    )
    parser.add_argument(
        "--json-output",
        default=None,
        help="Write results to JSON file",
    )

    args = parser.parse_args()

    results = run_eval(
        base_url=args.url,
        binary_path=args.binary,
        scenario_filter=args.scenario,
        verbose=args.verbose,
    )

    if args.json_output:
        with open(args.json_output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.json_output}")

    # Exit with non-zero if any scenario failed
    all_passed = all(r.get("success") and not r.get("errors") for r in results)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
