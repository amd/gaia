#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Integration tests for the summarizer application via CLI"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Import after sys.path.insert to get correct import
sys.path.insert(0, "src")
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME


class TestSummarizer:
    """Integration tests for the summarizer application"""

    @pytest.fixture
    def data_txt_path(self):
        """Path to data/txt directory"""
        return Path(__file__).parent.parent / "data" / "txt"

    @pytest.fixture
    def test_model(self):
        """Get test model from environment or use default"""
        return os.environ.get("GAIA_TEST_MODEL", DEFAULT_MODEL_NAME)

    def test_summarize_transcript(self, data_txt_path, test_model):
        """Integration test: summarize a real meeting transcript via CLI"""
        # Create temporary output file
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_file:
            output_path = Path(tmp_file.name)

        try:
            input_file = data_txt_path / "test_transcript.txt"

            print("\n" + "=" * 60)
            print("🧪 TESTING TRANSCRIPT SUMMARIZATION VIA CLI")
            print("=" * 60)
            print(f"📄 Input file: {input_file}")
            print(f"📁 Output file: {output_path}")
            print(f"🤖 Model: {test_model}")
            print(f"📝 Styles: executive, participants, action_items")
            print("⏳ Running CLI command...")

            # Run the CLI command
            cmd = [
                sys.executable,
                "-m",
                "gaia.cli",
                "summarize",
                "-i",
                str(input_file),
                "-o",
                str(output_path),
                "--styles",
                "executive",
                "participants",
                "action_items",
                "--model",
                test_model,
                "--no-viewer",  # Disable HTML viewer for testing
            ]

            print(f"🔧 Command: {' '.join(cmd)}")

            # Execute the command with environment variables
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"  # Ensure UTF-8 encoding on Windows
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,  # 2 minute timeout
            )

            print(f"📤 Return code: {result.returncode}")
            if result.stdout:
                print("📤 STDOUT:")
                print(result.stdout)
            if result.stderr:
                print("⚠️ STDERR:")
                print(result.stderr)

            # Check command succeeded
            assert result.returncode == 0, f"CLI command failed: {result.stderr}"

            # Verify output file was created
            assert output_path.exists(), f"Output file not created: {output_path}"

            # Load and verify the JSON output
            with open(output_path, "r", encoding="utf-8") as f:
                summary_result = json.load(f)

            print("✅ Summary generation completed!")
            print("\n" + "-" * 50)
            print("📊 RESULTS OVERVIEW")
            print("-" * 50)

            # Verify structure
            assert "metadata" in summary_result
            assert "summaries" in summary_result
            assert "aggregate_performance" in summary_result
            assert "original_content" in summary_result
            print(f"✓ Result structure: {list(summary_result.keys())}")

            # Check metadata
            assert "input_type" in summary_result["metadata"]
            assert summary_result["metadata"]["input_type"] == "transcript"
            assert summary_result["metadata"]["model"] == test_model
            assert "summary_styles" in summary_result["metadata"]
            print(
                f"✓ Metadata verified - Type: {summary_result['metadata']['input_type']}"
            )

            # Check summaries exist and have content
            assert "executive" in summary_result["summaries"]
            assert "participants" in summary_result["summaries"]
            assert "action_items" in summary_result["summaries"]

            # Verify summaries have actual content (not empty)
            assert len(summary_result["summaries"]["executive"]["text"].strip()) > 0
            assert len(summary_result["summaries"]["participants"]["text"].strip()) > 0
            assert len(summary_result["summaries"]["action_items"]["text"].strip()) > 0

            # Print actual summary content
            print("\n📝 GENERATED SUMMARIES:")
            print("-" * 30)

            for style in ["executive", "participants", "action_items"]:
                summary_text = summary_result["summaries"][style]["text"].strip()
                print(f"\n🔸 {style.upper()} SUMMARY ({len(summary_text)} chars):")
                print(
                    f"   {summary_text[:100]}{'...' if len(summary_text) > 100 else ''}"
                )

            # Check performance stats exist
            assert "aggregate_performance" in summary_result
            assert "total_tokens" in summary_result["aggregate_performance"]
            assert "total_processing_time_ms" in summary_result["aggregate_performance"]
            assert "model_info" in summary_result["aggregate_performance"]

            perf = summary_result["aggregate_performance"]
            print(f"\n⚡ PERFORMANCE STATS:")
            print(f"   • Total tokens: {perf['total_tokens']}")
            print(f"   • Processing time: {perf['total_processing_time_ms']}ms")
            print(f"   • Model: {perf['model_info']['model']}")
            print("=" * 60)

        finally:
            # Clean up temporary file
            if output_path.exists():
                output_path.unlink()

    def test_summarize_email(self, data_txt_path, test_model):
        """Integration test: summarize a real email via CLI"""
        # Add delay to prevent server overload from previous test
        print("⏳ Waiting 3 seconds to prevent server overload...")
        time.sleep(3)

        # Create temporary output file
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_file:
            output_path = Path(tmp_file.name)

        try:
            input_file = data_txt_path / "test_email.txt"

            print("\n" + "=" * 60)
            print("📧 TESTING EMAIL SUMMARIZATION VIA CLI")
            print("=" * 60)
            print(f"📄 Input file: {input_file}")
            print(f"📁 Output file: {output_path}")
            print(f"🤖 Model: {test_model}")
            print(f"📝 Style: brief")
            print(f"📧 Input type: email")
            print("⏳ Running CLI command...")

            # Run the CLI command
            cmd = [
                sys.executable,
                "-m",
                "gaia.cli",
                "summarize",
                "-i",
                str(input_file),
                "-o",
                str(output_path),
                "--styles",
                "brief",
                "--type",
                "email",
                "--model",
                test_model,
                "--no-viewer",  # Disable HTML viewer for testing
            ]

            print(f"🔧 Command: {' '.join(cmd)}")

            # Execute the command with environment variables
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"  # Ensure UTF-8 encoding on Windows
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,  # 2 minute timeout
            )

            print(f"📤 Return code: {result.returncode}")
            if result.stdout:
                print("📤 STDOUT:")
                print(result.stdout)
            if result.stderr:
                print("⚠️ STDERR:")
                print(result.stderr)

            # Check command succeeded
            assert result.returncode == 0, f"CLI command failed: {result.stderr}"

            # Verify output file was created
            assert output_path.exists(), f"Output file not created: {output_path}"

            # Load and verify the JSON output
            with open(output_path, "r", encoding="utf-8") as f:
                summary_result = json.load(f)

            print("✅ Summary generation completed!")
            print("\n" + "-" * 50)
            print("📊 RESULTS OVERVIEW")
            print("-" * 50)

            # Verify single style structure
            assert (
                "summary" in summary_result
            )  # Single style uses "summary" not "summaries"
            assert "performance" in summary_result
            assert "metadata" in summary_result
            assert "original_content" in summary_result
            print(f"✓ Result structure: {list(summary_result.keys())}")

            # Check metadata for single style
            assert summary_result["metadata"]["input_type"] == "email"
            assert summary_result["metadata"]["model"] == test_model
            assert (
                summary_result["metadata"]["summary_style"] == "brief"
            )  # Single style uses "summary_style"
            print(
                f"✓ Metadata verified - Type: {summary_result['metadata']['input_type']}, Style: {summary_result['metadata']['summary_style']}"
            )

            # Verify summary has actual content
            assert len(summary_result["summary"]["text"].strip()) > 0

            # Print actual summary content
            summary_text = summary_result["summary"]["text"].strip()
            print(f"\n📝 GENERATED EMAIL SUMMARY ({len(summary_text)} chars):")
            print("-" * 30)
            print(f"{summary_text}")

            # Check performance stats exist
            assert "performance" in summary_result
            assert "time_to_first_token_ms" in summary_result["performance"]
            assert "tokens_per_second" in summary_result["performance"]

            perf = summary_result["performance"]
            print(f"\n⚡ PERFORMANCE STATS:")
            print(f"   • Total tokens: {perf.get('total_tokens', 'N/A')}")
            print(f"   • Time to first token: {perf['time_to_first_token_ms']}ms")
            print(f"   • Tokens per second: {perf['tokens_per_second']:.2f}")
            print(f"   • Processing time: {perf.get('processing_time_ms', 'N/A')}ms")
            print("=" * 60)

        finally:
            # Clean up temporary file
            if output_path.exists():
                output_path.unlink()

    def test_multiple_styles(self, data_txt_path, test_model):
        """Integration test: verify multiple summary styles work correctly via CLI"""
        # Add delay to prevent server overload from previous tests
        print("⏳ Waiting 5 seconds to prevent server overload...")
        time.sleep(5)

        # Test with fewer styles to avoid server overload
        # Testing 3 styles instead of 6 to prevent LLM server exhaustion
        all_styles = ["brief", "executive", "participants"]

        # Create temporary output file
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_file:
            output_path = Path(tmp_file.name)

        try:
            input_file = data_txt_path / "test_transcript.txt"

            print("\n" + "=" * 60)
            print("🎯 TESTING MULTIPLE STYLES SUMMARIZATION VIA CLI")
            print("=" * 60)
            print(f"📄 Input file: {input_file}")
            print(f"📁 Output file: {output_path}")
            print(f"🤖 Model: {test_model}")
            print(f"📝 Styles ({len(all_styles)}): {', '.join(all_styles)}")
            print("⏳ Running CLI command...")

            # Run the CLI command
            cmd = (
                [
                    sys.executable,
                    "-m",
                    "gaia.cli",
                    "summarize",
                    "-i",
                    str(input_file),
                    "-o",
                    str(output_path),
                    "--styles",
                ]
                + all_styles
                + [
                    "--model",
                    test_model,
                    "--no-viewer",  # Disable HTML viewer for testing
                ]
            )

            print(f"🔧 Command: {' '.join(cmd)}")

            # Execute the command with environment variables
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"  # Ensure UTF-8 encoding on Windows
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180,  # 3 minute timeout for multiple styles
                env=env,
            )

            print(f"📤 Return code: {result.returncode}")
            if result.stdout:
                print("📤 STDOUT:")
                print(result.stdout)
            if result.stderr:
                print("⚠️ STDERR:")
                print(result.stderr)

            # Check command succeeded
            assert result.returncode == 0, f"CLI command failed: {result.stderr}"

            # Verify output file was created
            assert output_path.exists(), f"Output file not created: {output_path}"

            # Load and verify the JSON output
            with open(output_path, "r", encoding="utf-8") as f:
                summary_result = json.load(f)

            print("✅ All summaries generated!")
            print("\n" + "-" * 50)
            print("📊 RESULTS OVERVIEW")
            print("-" * 50)

            # Verify all styles are present in result
            assert "summaries" in summary_result
            print(
                f"✓ Found {len(summary_result['summaries'])} summaries: {list(summary_result['summaries'].keys())}"
            )

            for style in all_styles:
                assert style in summary_result["summaries"]
                assert len(summary_result["summaries"][style]["text"].strip()) > 0

            # Print summary of each style
            print(f"\n📝 GENERATED SUMMARIES ({len(all_styles)} styles):")
            print("-" * 40)

            for style in all_styles:
                summary_text = summary_result["summaries"][style]["text"].strip()
                word_count = len(summary_text.split())
                print(
                    f"\n🔸 {style.upper()} ({len(summary_text)} chars, {word_count} words):"
                )
                # Show first 80 characters of each summary
                preview = summary_text[:80].replace("\n", " ")
                print(f"   {preview}{'...' if len(summary_text) > 80 else ''}")

            # Check metadata shows all styles
            assert summary_result["metadata"]["summary_styles"] == all_styles
            assert summary_result["metadata"]["input_type"] == "transcript"
            print(
                f"\n✓ Metadata verified - Type: {summary_result['metadata']['input_type']}"
            )
            print(f"✓ All {len(all_styles)} styles confirmed in metadata")

            # Show aggregate performance
            if "aggregate_performance" in summary_result:
                perf = summary_result["aggregate_performance"]
                print(f"\n⚡ AGGREGATE PERFORMANCE:")
                print(f"   • Total tokens: {perf.get('total_tokens', 'N/A')}")
                print(
                    f"   • Total processing time: {perf.get('total_processing_time_ms', 'N/A')}ms"
                )
                print(f"   • Model: {perf.get('model_info', {}).get('model', 'N/A')}")

            print("=" * 60)

        finally:
            # Clean up temporary file
            if output_path.exists():
                output_path.unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-vs"])
