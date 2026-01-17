#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Integration tests for the summarizer application via CLI"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pytest

try:
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

# Import after sys.path.insert to get correct import
sys.path.insert(0, "src")
from gaia.agents.summarize.agent import SummarizerAgent


class TestSummarizer:
    """Integration tests for the summarizer application"""

    @staticmethod
    def create_synthetic_pdf(output_path: Path) -> None:
        """
        Create a synthetic PDF with text rendered as images for OCR testing.

        Args:
            output_path: Path where the PDF should be saved
        """
        if not HAS_REPORTLAB:
            pytest.skip("reportlab not installed - required for PDF generation")

        # Create a PDF with images of text (for OCR testing)
        c = canvas.Canvas(str(output_path), pagesize=letter)
        width, height = letter

        # Page 1 - Create image with text
        img1 = Image.new("RGB", (int(width), int(height)), color="white")
        draw = ImageDraw.Draw(img1)

        # Try to use a nice font, fall back to default if not available
        try:
            heading_font = ImageFont.truetype("arial.ttf", 22)
            body_font = ImageFont.truetype("arial.ttf", 14)
        except:
            # Fallback to default font
            heading_font = ImageFont.load_default()
            body_font = ImageFont.load_default()

        # Define margins
        left_margin = 60
        y_position = 70

        # Title
        title_text = "Synthetic Test Document for OCR Extraction"
        draw.text(
            (left_margin + 20, y_position),
            title_text,
            fill="darkblue",
            font=heading_font,
        )
        y_position += 60

        # Introduction
        draw.text(
            (left_margin, y_position),
            "Introduction",
            fill="darkblue",
            font=heading_font,
        )
        y_position += 40

        intro_lines = [
            "This is a synthetically generated PDF document designed to test",
            "OCR extraction and summarization capabilities. The document contains",
            "structured content with multiple sections, paragraphs, and formatting",
            "to verify that the OCR process can accurately extract text from files.",
        ]
        for line in intro_lines:
            draw.text((left_margin, y_position), line, fill="black", font=body_font)
            y_position += 24

        y_position += 20

        # Executive Summary
        draw.text(
            (left_margin, y_position),
            "Executive Summary",
            fill="darkblue",
            font=heading_font,
        )
        y_position += 40

        exec_lines = [
            "This document serves as a test artifact for validating OCR extraction",
            "pipelines. Key objectives include: (1) verifying text extraction accuracy,",
            "(2) testing multi-paragraph processing, (3) validating structured content",
            "handling, and (4) ensuring proper character encoding and special symbol",
            "recognition.",
        ]
        for line in exec_lines:
            draw.text((left_margin, y_position), line, fill="black", font=body_font)
            y_position += 24

        y_position += 20

        # Technical Details
        draw.text(
            (left_margin, y_position),
            "Technical Details",
            fill="darkblue",
            font=heading_font,
        )
        y_position += 40

        tech_lines = [
            "The OCR (Optical Character Recognition) extraction process involves",
            "multiple stages of image processing and text recognition. Modern OCR",
            "systems utilize machine learning models trained on vast datasets to",
            "achieve high accuracy rates across different fonts, sizes, and layouts.",
            "",
            "This test document includes various typographical elements such as",
            "punctuation marks, numbers (1234567890), special characters (@#$%&*),",
            "and different text formatting. The extraction system should preserve all",
            "these elements accurately while maintaining the document's structural",
            "hierarchy.",
        ]
        for line in tech_lines:
            if line:
                draw.text((left_margin, y_position), line, fill="black", font=body_font)
            y_position += 24

        # Save page 1 as temporary image and add to PDF
        temp_img1 = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img1.save(temp_img1.name, "PNG")
        c.drawImage(temp_img1.name, 0, 0, width=width, height=height)
        c.showPage()

        # Page 2 - Create another image with more text
        img2 = Image.new("RGB", (int(width), int(height)), color="white")
        draw = ImageDraw.Draw(img2)

        y_position = 70

        # Testing Methodology
        draw.text(
            (left_margin, y_position),
            "Testing Methodology",
            fill="darkblue",
            font=heading_font,
        )
        y_position += 40

        method_lines = [
            "To validate OCR extraction, the following test cases are essential:",
            "",
            "1. Text Accuracy: Compare extracted text with source content to",
            "   measure accuracy.",
            "2. Layout Preservation: Verify that document structure and formatting",
            "   are maintained.",
            "3. Special Characters: Ensure proper handling of Unicode characters",
            "   and symbols.",
            "4. Multi-page Support: Test extraction across multiple pages with",
            "   varying content.",
            "5. Performance Metrics: Measure processing time and resource",
            "   utilization.",
        ]
        for line in method_lines:
            if line:
                draw.text((left_margin, y_position), line, fill="black", font=body_font)
            y_position += 24

        y_position += 20

        # Expected Results
        draw.text(
            (left_margin, y_position),
            "Expected Results",
            fill="darkblue",
            font=heading_font,
        )
        y_position += 40

        results_lines = [
            "A successful OCR extraction should produce a text output that:",
            "",
            "- Maintains 99%+ accuracy for standard fonts and sizes",
            "- Preserves paragraph breaks and section boundaries",
            "- Correctly interprets punctuation and special characters",
            "- Handles multi-column layouts (if present)",
            "- Extracts text in proper reading order",
        ]
        for line in results_lines:
            if line:
                draw.text((left_margin, y_position), line, fill="black", font=body_font)
            y_position += 24

        y_position += 20

        # Conclusion
        draw.text(
            (left_margin, y_position), "Conclusion", fill="darkblue", font=heading_font
        )
        y_position += 40

        conclusion_lines = [
            "This synthetic PDF document provides a comprehensive test case for",
            "OCR extraction systems. By including varied content types, formatting",
            "styles, and special characters, it enables thorough validation of text",
            "extraction capabilities. The document's multi-page structure also allows",
            "testing of page boundary handling and sequential content processing.",
        ]
        for line in conclusion_lines:
            draw.text((left_margin, y_position), line, fill="black", font=body_font)
            y_position += 24

        y_position += 30
        current_date = datetime.now().strftime("%Y-%m-%d")
        draw.text(
            (left_margin, y_position),
            f"Document generated for testing purposes - Date: {current_date}",
            fill="gray",
            font=body_font,
        )

        # Save page 2 as temporary image and add to PDF
        temp_img2 = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img2.save(temp_img2.name, "PNG")
        c.drawImage(temp_img2.name, 0, 0, width=width, height=height)

        # Save PDF
        c.save()

        # Clean up temporary images
        try:
            Path(temp_img1.name).unlink()
            Path(temp_img2.name).unlink()
        except:
            pass

        print(f"Successfully created synthetic OCR PDF (text as images): {output_path}")

    @pytest.fixture
    def data_txt_path(self) -> Path:
        """Path to data/txt directory"""
        return Path(__file__).parent.parent / "data" / "txt"

    @pytest.fixture
    def data_pdf_path(self) -> Path:
        """Path to data/pdf directory"""
        return Path(__file__).parent.parent / "data" / "pdf"

    @pytest.fixture
    def test_model(self) -> str:
        """Get test model from environment or use default"""
        return os.environ.get("GAIA_TEST_MODEL", SummarizerAgent.DEFAULT_MODEL)

    def test_summarize_transcript(self, data_txt_path, test_model) -> None:
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

    def test_summarize_email(self, data_txt_path, test_model) -> None:
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

    def test_multiple_styles(self, data_txt_path, test_model) -> None:
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

    def test_summarize_directory(self, test_model) -> None:
        """Unit-style test for SummarizerAgent.summarize_directory on a temp dir."""
        # Build a temporary directory with a few small files
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            # Create sample files the directory method will pick up
            (tmp_path / "a.transcript").write_text(
                "Speaker A: Hello there.\nSpeaker B: Hi!\n", encoding="utf-8"
            )
            (tmp_path / "b.email").write_text(
                "From: alice@example.com\nTo: bob@example.com\nSubject: Test\n\nHi Bob,\nCheers, Alice",
                encoding="utf-8",
            )
            (tmp_path / "c.txt").write_text(
                "Some general notes and text for testing.", encoding="utf-8"
            )

            # Import agent directly to call summarize_directory
            sys.path.insert(0, "src")

            agent = SummarizerAgent(
                model=test_model, styles=["brief"], combined_prompt=False
            )

            # Run summarize_directory
            results = agent.summarize_directory(
                tmp_path, styles=["brief"], input_type="auto"
            )

            # Basic assertions
            assert isinstance(results, list)
            assert len(results) == 3  # 3 files created above

            print("\n Summaries from summarize_directory:")
            for idx, res in enumerate(results, start=1):
                meta = res.get("metadata", {})
                style = meta.get("summary_style", "brief")
                input_type = meta.get("input_type", "unknown")
                text = res["summary"]["text"].strip()
                preview = text[:120].replace("\n", " ")
                print(f"  [{idx}] type={input_type}, style={style}, chars={len(text)}")
                print(f"      {preview}{'...' if len(text) > 120 else ''}")

            # Each result should be a structured dict with single-style output
            for res in results:
                assert "metadata" in res
                assert "summary" in res  # single style path
                assert "performance" in res
                assert "original_content" in res
                # Non-empty summary text
                assert len(res["summary"]["text"].strip()) > 0

            # Ensure input types were auto-detected reasonably
            input_types = [r["metadata"]["input_type"] for r in results]
            assert set(input_types).issubset({"transcript", "email", "auto", "txt"})

    def test_invalid_style_raises(self, test_model) -> None:
        """Ensure passing an unsupported style raises a clear ValueError."""

        agent = SummarizerAgent(
            model=test_model, styles=["random"], combined_prompt=False
        )
        content = "Speaker A: Quick status update. Speaker B: Noted."

        with pytest.raises(ValueError) as exc:
            agent.summarize(content, input_type="transcript")

        # Error message should list allowed styles
        assert "Unsupported style" in str(exc.value)

    def test_summarize_synthetic_pdf_ocr(self, data_pdf_path, test_model) -> None:
        """Test OCR extraction on a synthetically generated PDF"""
        if not HAS_REPORTLAB:
            pytest.skip("reportlab not installed - required for PDF generation")

        # Create synthetic PDF
        synthetic_pdf_path = data_pdf_path / "synthetic_test_ocr.pdf"
        print(f"\nCreating synthetic PDF: {synthetic_pdf_path}")
        self.create_synthetic_pdf(synthetic_pdf_path)
        print(f"File size: {synthetic_pdf_path.stat().st_size / 1024:.2f} KB")

        # Run the OCR test on the synthetic PDF
        self._run_pdf_cli_test(
            data_pdf_path,
            test_model,
            pdf_name="synthetic_test_ocr.pdf",
            title="SYNTHETIC PDF OCR SUMMARIZATION",
        )
        print(f"Synthetic PDF retained at: {synthetic_pdf_path}")

    def test_summarize_pdf_ocr(self, data_pdf_path, test_model) -> None:
        self._run_pdf_cli_test(
            data_pdf_path,
            test_model,
            pdf_name="Oil-and-Gas-Activity-Operations-Manual-1-10.pdf",
            title="pdf summarization",
        )

    def _run_pdf_cli_test(
        self, data_pdf_path: Path, test_model: str, pdf_name: str, title: str
    ) -> None:
        # Add delay to prevent server overload from previous tests
        print("Waiting 3 seconds to prevent server overload...")
        time.sleep(3)

        # Create temporary output file
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_file:
            output_path = Path(tmp_file.name)

        input_file = None
        try:
            # Use sample PDF from repo data, copy to temp to satisfy PathValidator
            source_pdf = data_pdf_path / pdf_name
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_pdf:
                tmp_pdf_path = Path(tmp_pdf.name)
            shutil.copyfile(source_pdf, tmp_pdf_path)
            input_file = tmp_pdf_path

            # Ensure allowed paths include repo data and system temp
            try:
                cache_dir = Path.home() / ".gaia" / "cache"
                cache_dir.mkdir(parents=True, exist_ok=True)
                config_file = cache_dir / "allowed_paths.json"
                allowed_paths_data = {"paths": []}
                if config_file.exists():
                    try:
                        with open(config_file, "r", encoding="utf-8") as f:
                            allowed_paths_data = json.load(f) or {"paths": []}
                    except Exception:
                        allowed_paths_data = {"paths": []}
                source_dir = str(source_pdf.parent.resolve())
                system_temp = str(Path(tempfile.gettempdir()).resolve())
                for p in [source_dir, system_temp]:
                    if p not in allowed_paths_data["paths"]:
                        allowed_paths_data["paths"].append(p)
                with open(config_file, "w", encoding="utf-8") as f:
                    json.dump(allowed_paths_data, f, indent=2)
                print(f"🔐 Updated allowed paths cache: {allowed_paths_data['paths']}")
            except Exception as e:
                print(f"⚠️ Failed to update allowed paths cache: {e}")

            print("\n" + "=" * 60)
            print(f"📄 TESTING {title} VIA CLI")
            print("=" * 60)
            print(f"📄 Input file: {input_file}")
            print(f"📁 Output file: {output_path}")
            print(f"🤖 Model: {test_model}")
            print(f"📝 Style: brief")
            print(f"📄 Input type: pdf")
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
                "pdf",
                "--model",
                test_model,
                "--no-viewer",
            ]

            print(f"🔧 Command: {' '.join(cmd)}")

            # Execute the command with environment variables
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"  # Ensure UTF-8 encoding on Windows
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
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

            print("✅ PDF Summary generation completed!")
            print("\n" + "-" * 50)
            print("📊 RESULTS OVERVIEW")
            print("-" * 50)

            # Verify single style structure for PDF
            assert "summary" in summary_result
            assert "performance" in summary_result
            assert "metadata" in summary_result
            assert "original_content" in summary_result
            print(f"✓ Result structure: {list(summary_result.keys())}")

            # Check metadata for single style
            assert summary_result["metadata"]["input_type"] == "pdf"
            assert summary_result["metadata"]["model"] == test_model
            assert summary_result["metadata"]["summary_style"] == "brief"
            print(
                f"✓ Metadata verified - Type: {summary_result['metadata']['input_type']}, Style: {summary_result['metadata']['summary_style']}"
            )

            # Verify summary has actual content
            assert (
                len(summary_result["summary"]["text"].strip()) > 20
            )  # At least 20 chars

        finally:
            # Clean up temporary files
            if output_path.exists():
                output_path.unlink()
            if input_file and Path(input_file).exists():
                try:
                    Path(input_file).unlink()
                except Exception:
                    pass


if __name__ == "__main__":
    pytest.main([__file__, "-vs"])
