#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Integration tests for VLM client with Lemonade server.

These are REAL tests that call the actual VLM model - no mocks!
"""

import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pypdf import PdfReader

from gaia.llm.vlm_client import VLMClient
from gaia.rag.pdf_utils import count_images_in_page, extract_images_from_page_pymupdf


def test_vlm_availability():
    """Test 1: Check if VLM model is available on Lemonade."""
    print("\n" + "=" * 60)
    print("TEST 1: VLM Model Availability")
    print("=" * 60)

    vlm = VLMClient(auto_load=False)

    available = vlm.check_availability()

    if available:
        print("✅ PASS: VLM model is available on Lemonade")
        print(f"   Model: {vlm.vlm_model}")
        return True
    else:
        print("❌ FAIL: VLM model not available")
        print(f"   Expected: {vlm.vlm_model}")
        print("\n   Run: lemonade-server serve")
        print("   Then verify model is in list")
        return False


def test_vlm_loading():
    """Test 2: Load VLM model."""
    print("\n" + "=" * 60)
    print("TEST 2: VLM Model Loading")
    print("=" * 60)

    vlm = VLMClient(auto_load=True, fallback_model=None)

    loaded = vlm._ensure_vlm_loaded()

    if loaded:
        print("✅ PASS: VLM model loaded successfully")
        print(f"   Model: {vlm.vlm_model}")
        print(f"   Status: {vlm.vlm_loaded}")
        return True
    else:
        print("❌ FAIL: Could not load VLM model")
        return False


def test_image_extraction_from_pdf():
    """Test 3: Extract images from actual PDF."""
    print("\n" + "=" * 60)
    print("TEST 3: Image Extraction from PDF")
    print("=" * 60)

    # Use relative path from project root
    pdf_path = "./data/pdf/Oil-and-Gas-Activity-Operations-Manual-1-10.pdf"

    if not os.path.exists(pdf_path):
        print(f"❌ SKIP: Test PDF not found: {pdf_path}")
        print(f"   Current directory: {os.getcwd()}")
        return False

    reader = PdfReader(pdf_path)
    total_pages = len(reader.pages)

    print(f"PDF: {Path(pdf_path).name}")
    print(f"Pages: {total_pages}")
    print()

    images_found = []

    for i, page in enumerate(reader.pages, 1):
        # Count images
        has_images, count = count_images_in_page(page)

        if has_images:
            # Extract images using PyMuPDF (more reliable)
            images = extract_images_from_page_pymupdf(pdf_path, page_num=i)

            print(f"Page {i}: {count} image(s) detected")
            for img_idx, img_data in enumerate(images, 1):
                print(
                    f"   Image {img_idx}: {img_data['width']}x{img_data['height']}, "
                    f"{img_data['size_kb']:.1f}KB, {img_data['format']}"
                )

                images_found.append({"page": i, "image_num": img_idx, "data": img_data})

    print(f"\n📊 Total images extracted: {len(images_found)} from {total_pages} pages")

    if images_found:
        print("✅ PASS: Images extracted successfully")
        return images_found
    else:
        print("⚠️  WARNING: No images found in PDF")
        return []


def test_vlm_extraction_on_real_image():
    """Test 4: Use VLM to extract text from a real PDF image."""
    print("\n" + "=" * 60)
    print("TEST 4: VLM Text Extraction from Real Image")
    print("=" * 60)

    # First extract images from PDF
    print("Step 1: Extracting images from PDF...")
    images_found = test_image_extraction_from_pdf()

    if not images_found:
        print("❌ SKIP: No images to test VLM with")
        return False

    # Use first image for testing
    test_image = images_found[0]
    page_num = test_image["page"]
    img_num = test_image["image_num"]
    img_data = test_image["data"]

    print(f"\nStep 2: Testing VLM on Page {page_num}, Image {img_num}")
    print(f"   Dimensions: {img_data['width']}x{img_data['height']}")
    print(f"   Size: {img_data['size_kb']:.1f}KB")

    # Initialize VLM
    vlm = VLMClient(
        vlm_model="Qwen2.5-VL-7B-Instruct-GGUF",
        fallback_model="Qwen3-0.6B-GGUF",
        auto_load=True,
    )

    # Extract text from image
    try:
        print("\nStep 3: Calling VLM to extract text...")
        extracted_text = vlm.extract_from_image(
            image_bytes=img_data["image_bytes"], image_num=img_num, page_num=page_num
        )

        print(f"\n✅ VLM Extraction Results:")
        print("=" * 60)
        print(extracted_text[:500])
        if len(extracted_text) > 500:
            print(f"... ({len(extracted_text)} total characters)")
        print("=" * 60)

        # Check if it looks like markdown
        has_headers = "#" in extracted_text
        has_tables = "|" in extracted_text
        has_bullets = "-" in extracted_text or "*" in extracted_text

        print("\n📊 Content Analysis:")
        print(f"   Length: {len(extracted_text)} characters")
        print(f"   Has headers: {has_headers}")
        print(f"   Has tables: {has_tables}")
        print(f"   Has bullets: {has_bullets}")

        # Cleanup - reload fallback model
        print("\nStep 4: Cleaning up (reloading fallback model)...")
        vlm.cleanup()

        if len(extracted_text) > 10:
            print("\n✅ PASS: VLM extracted content from image")
            return True
        else:
            print("\n❌ FAIL: VLM returned very little content")
            return False

    except Exception as e:
        print(f"\n❌ FAIL: VLM extraction error: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_vlm_batch_extraction():
    """Test 5: Extract from multiple images (batch test)."""
    print("\n" + "=" * 60)
    print("TEST 5: Batch VLM Extraction (All Images in PDF)")
    print("=" * 60)

    # Use relative path from project root
    pdf_path = "./data/pdf/Oil-and-Gas-Activity-Operations-Manual-1-10.pdf"

    if not os.path.exists(pdf_path):
        print(f"❌ SKIP: Test PDF not found: {pdf_path}")
        print(f"   Current directory: {os.getcwd()}")
        return False

    reader = PdfReader(pdf_path)

    # Use context manager for auto cleanup
    with VLMClient(
        vlm_model="Qwen2.5-VL-7B-Instruct-GGUF", fallback_model="Qwen3-0.6B-GGUF"
    ) as vlm:

        total_images_processed = 0
        pages_with_extractions = []

        for i, page in enumerate(reader.pages, 1):
            # Check for images
            has_imgs, count = count_images_in_page(page)

            if not has_imgs:
                print(f"Page {i}: No images, skipping")
                continue

            # Extract images using PyMuPDF
            images = extract_images_from_page_pymupdf(pdf_path, page_num=i)

            if not images:
                print(f"Page {i}: Images detected but extraction failed")
                continue

            print(f"\nPage {i}: Processing {len(images)} image(s)...")

            # Extract text from each image
            results = vlm.extract_from_page_images(images, page_num=i)

            for result in results:
                total_images_processed += 1
                img_num = result["image_num"]
                text_len = len(result["text"])

                print(f"   Image {img_num}: Extracted {text_len} chars")

                # Show preview
                preview = result["text"][:100].replace("\n", " ")
                print(f"   Preview: {preview}...")

            pages_with_extractions.append(
                {"page": i, "num_images": len(images), "results": results}
            )

    # Summary
    print("\n" + "=" * 60)
    print("BATCH EXTRACTION SUMMARY")
    print("=" * 60)
    print(f"Total images processed: {total_images_processed}")
    print(f"Pages with extractions: {len(pages_with_extractions)}")

    # Check for vision statement
    print("\n🔍 Searching for 'vision' in extracted content...")
    for page_data in pages_with_extractions:
        for result in page_data["results"]:
            if "vision" in result["text"].lower():
                print(
                    f"\n⭐ FOUND 'vision' on Page {page_data['page']}, Image {result['image_num']}:"
                )
                # Find the relevant excerpt
                text = result["text"]
                idx = text.lower().find("vision")
                excerpt_start = max(0, idx - 50)
                excerpt_end = min(len(text), idx + 150)
                excerpt = text[excerpt_start:excerpt_end]
                print(f"   ...{excerpt}...")

    if total_images_processed > 0:
        print("\n✅ PASS: Batch extraction completed")
        return True
    else:
        print("\n⚠️  WARNING: No images were processed")
        return False


def run_all_tests():
    """Run all VLM integration tests."""
    print("\n" + "=" * 80)
    print("VLM INTEGRATION TEST SUITE")
    print("=" * 80)
    print("\nThese tests call the REAL VLM model via Lemonade - no mocks!")
    print("Ensure Lemonade server is running with Qwen2.5-VL-7B-Instruct-GGUF loaded.")
    print("=" * 80)

    results = {}

    # Test 1: Availability
    results["availability"] = test_vlm_availability()
    if not results["availability"]:
        print("\n❌ VLM not available - stopping tests")
        return results

    # Test 2: Loading
    results["loading"] = test_vlm_loading()
    if not results["loading"]:
        print("\n❌ VLM loading failed - stopping tests")
        return results

    # Test 3: Image extraction
    images = test_image_extraction_from_pdf()
    results["image_extraction"] = len(images) > 0 if images else False

    # Test 4: Single VLM extraction
    results["vlm_extraction"] = test_vlm_extraction_on_real_image()

    # Test 5: Batch extraction
    results["batch_extraction"] = test_vlm_batch_extraction()

    # Summary
    print("\n" + "=" * 80)
    print("TEST RESULTS SUMMARY")
    print("=" * 80)

    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {test_name}")

    total = len(results)
    passed = sum(1 for v in results.values() if v)

    print(f"\n{passed}/{total} tests passed")
    print("=" * 80)

    return results


if __name__ == "__main__":
    try:
        results = run_all_tests()
        sys.exit(0 if all(results.values()) else 1)

    except KeyboardInterrupt:
        print("\n\n⚠️  Tests interrupted by user")
        sys.exit(130)

    except Exception as e:
        print(f"\n❌ Test suite failed with exception: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
