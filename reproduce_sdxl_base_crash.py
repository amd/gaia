"""
Minimal reproduction script for SDXL-Base-1.0 crash in Lemonade Server.

This script isolates the issue to help debug/report the crash.

Prerequisites:
    1. Lemonade Server running: lemonade-server serve
    2. SDXL-Base-1.0 downloaded: lemonade-server pull SDXL-Base-1.0

Usage:
    python reproduce_sdxl_base_crash.py

Expected behavior:
    - Server should generate a 1024x1024 image with 20 steps

Actual behavior:
    - Server crashes or connection resets during generation
"""

import requests
import sys
import time
import base64
from pathlib import Path


def check_server():
    """Check if Lemonade Server is running."""
    try:
        response = requests.get("http://localhost:8000/api/v1/models", timeout=5)
        if response.ok:
            return True
    except Exception:
        pass
    return False


def check_model_downloaded(model_id):
    """Check if model is downloaded."""
    try:
        response = requests.get("http://localhost:8000/api/v1/models", timeout=5)
        if response.ok:
            data = response.json()
            for model in data.get("data", []):
                if model["id"] == model_id:
                    return model.get("downloaded", False)
    except Exception:
        pass
    return False


def load_model(model_id):
    """Load model on server."""
    print(f"Loading model: {model_id}...")
    print("  (This may take 5+ minutes for SDXL-Base-1.0 - 6.6GB)")

    try:
        start = time.time()
        response = requests.post(
            "http://localhost:8000/api/v1/load",
            json={"model_name": model_id},
            timeout=600,  # 10 minutes
        )
        elapsed = time.time() - start

        if response.ok:
            print(f"  ✓ Model loaded in {elapsed:.1f}s")
            return True
        else:
            print(f"  ✗ Load failed: {response.text[:200]}")
            return False
    except requests.exceptions.Timeout:
        print(f"  ✗ Load timed out after 600s")
        return False
    except Exception as e:
        print(f"  ✗ Load error: {e}")
        return False


def generate_image(model_id, prompt, size, steps, cfg_scale):
    """Attempt to generate an image."""
    print(f"\nGenerating image with {model_id}...")
    print(f"  Prompt: {prompt}")
    print(f"  Size: {size}")
    print(f"  Steps: {steps}")
    print(f"  CFG Scale: {cfg_scale}")

    payload = {
        "prompt": prompt,
        "model": model_id,
        "size": size,
        "steps": steps,
        "cfg_scale": cfg_scale,
        "n": 1,
        "response_format": "b64_json",
    }

    print(f"\nSending request to: http://localhost:8000/api/v1/images/generations")
    print("  Waiting for response...", end="", flush=True)

    try:
        start = time.time()
        response = requests.post(
            "http://localhost:8000/api/v1/images/generations",
            json=payload,
            timeout=600,  # 10 minutes for slow generation
        )
        elapsed = time.time() - start

        print(f" response received in {elapsed:.1f}s")

        if response.ok:
            data = response.json()
            img_b64 = data["data"][0]["b64_json"]
            img_bytes = base64.b64decode(img_b64)

            # Save to verify
            output_path = Path(f"reproduction_{model_id}_{size}.png")
            output_path.write_bytes(img_bytes)

            print(f"\n✓ SUCCESS!")
            print(f"  Image saved: {output_path}")
            print(f"  File size: {len(img_bytes):,} bytes")
            print(f"  Generation time: {elapsed:.1f}s")
            return True
        else:
            print(f"\n✗ HTTP Error {response.status_code}")
            print(f"  Response: {response.text[:300]}")
            return False

    except requests.exceptions.Timeout:
        elapsed = time.time() - start
        print(f"\n✗ TIMEOUT after {elapsed:.1f}s")
        print("  Server did not respond within 600 seconds")
        return False

    except requests.exceptions.ConnectionError as e:
        elapsed = time.time() - start
        print(f"\n✗ CONNECTION ERROR after {elapsed:.1f}s")
        print(f"  Error: {e}")
        print("\n  This typically means:")
        print("    - Server crashed during generation")
        print("    - Server ran out of memory (OOM)")
        print("    - Connection reset by server")
        return False

    except Exception as e:
        print(f"\n✗ ERROR: {type(e).__name__}")
        print(f"  {e}")
        return False


def main():
    """Run the reproduction test."""
    print("=" * 70)
    print("SDXL-Base-1.0 Crash Reproduction Script")
    print("=" * 70)
    print()

    # Step 1: Check server
    print("Step 1: Checking Lemonade Server...")
    if not check_server():
        print("  ✗ Server not responding")
        print("\n  Start server with: lemonade-server serve")
        sys.exit(1)
    print("  ✓ Server is running")

    # Step 2: Check model
    model_id = "SDXL-Base-1.0"
    print(f"\nStep 2: Checking if {model_id} is downloaded...")
    if not check_model_downloaded(model_id):
        print(f"  ✗ {model_id} not downloaded")
        print(f"\n  Download with: lemonade-server pull {model_id}")
        sys.exit(1)
    print(f"  ✓ {model_id} is downloaded")

    # Step 3: Load model
    print(f"\nStep 3: Loading {model_id}...")
    if not load_model(model_id):
        print("\n  Model load failed. Cannot proceed with generation test.")
        sys.exit(1)

    # Step 4: Generate image (this is where crash happens)
    print("\nStep 4: Attempting image generation...")
    print("  (This is where the crash typically occurs)")
    print()

    success = generate_image(
        model_id="SDXL-Base-1.0",
        prompt="a simple test image",
        size="1024x1024",  # SDXL native resolution
        steps=20,          # SDXL-Base default
        cfg_scale=7.5,     # SDXL-Base default
    )

    print()
    print("=" * 70)
    if success:
        print("RESULT: Test PASSED - No crash detected")
        print("=" * 70)
    else:
        print("RESULT: Test FAILED - Crash or error occurred")
        print("=" * 70)
        print("\nTo report this issue:")
        print("  1. Check Lemonade Server logs for crash details")
        print("  2. Note your system specs (RAM, GPU, OS)")
        print("  3. Report at: https://github.com/AMD-Lemonade/lemonade-server/issues")
        sys.exit(1)


if __name__ == "__main__":
    main()
