"""
Sweep through SD models, settings, and resolutions to evaluate quality.

Tests all combinations of:
- Models: SD-1.5, SD-Turbo, SDXL-Base-1.0, SDXL-Turbo
- Resolutions: 512x512, 1024x1024
- Settings: Model-specific defaults (steps, cfg_scale)

Output:
- Images: sd_model_sweep_results/*.png
- JSON report: sd_model_sweep_results/report.json (machine-readable)
- Markdown report: sd_model_sweep_results/report.md (human-readable with image gallery)

Each filename format: {model}_{size}_{prompt_snippet}.png
Example: SDXL-Turbo_512x512_a_beautiful_mountain_landsc.png

Usage:
    python test_sd_model_sweep.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError

# Test prompts
TEST_PROMPTS = [
    "a beautiful mountain landscape, photorealistic, detailed, 4k",
    "a red apple on white background, simple, minimal",
    "cyberpunk city at night, neon lights, detailed",
]

# Model configurations
MODELS = [
    {"id": "SD-1.5", "sizes": ["512x512"]},
    {"id": "SD-Turbo", "sizes": ["512x512"]},
    {"id": "SDXL-Base-1.0", "sizes": ["512x512", "1024x1024"]},
    {"id": "SDXL-Turbo", "sizes": ["512x512", "1024x1024"]},
]


def test_model_combination(client, model_id, size, prompt, output_dir):
    """Test a single model/size/prompt combination."""
    # Get model-specific defaults
    defaults = LemonadeClient.SD_MODEL_DEFAULTS.get(model_id, {})
    steps = defaults.get("steps", 20)
    cfg_scale = defaults.get("cfg_scale", 7.5)

    print(f"\n  Model: {model_id}")
    print(f"  Size: {size}")
    print(f"  Steps: {steps}, CFG: {cfg_scale}")
    print(f"  Prompt: {prompt[:50]}...")

    try:
        # Load model first
        print("  Loading model...", end="", flush=True)
        try:
            client.load_model(model_id, auto_download=True, prompt=False, timeout=600)
            print(" ✓")
        except LemonadeClientError as e:
            if "already loaded" in str(e).lower():
                print(" (already loaded)")
            else:
                print(f" ✗ {e}")
                return None

        # Generate image
        print("  Generating...", end="", flush=True)
        start = time.time()

        result = client.generate_image(
            prompt=prompt,
            model=model_id,
            size=size,
            steps=steps,
            cfg_scale=cfg_scale,
            timeout=600,
        )

        elapsed = time.time() - start

        # Save image
        import base64

        img_b64 = result["data"][0]["b64_json"]
        img_bytes = base64.b64decode(img_b64)

        # Create filename
        safe_prompt = prompt[:30].replace(" ", "_").replace(",", "")
        filename = f"{model_id}_{size}_{safe_prompt}.png"
        filepath = output_dir / filename
        filepath.write_bytes(img_bytes)

        print(f" ✓ {elapsed:.1f}s")
        print(f"  Saved: {filepath.name}")
        print(f"  Size: {len(img_bytes):,} bytes")

        return {
            "model": model_id,
            "size": size,
            "prompt": prompt,
            "steps": steps,
            "cfg_scale": cfg_scale,
            "time": elapsed,
            "file_size": len(img_bytes),
            "filepath": str(filepath),
            "filename": filename,
        }

    except LemonadeClientError as e:
        print(f" ✗ {e}")
        return None
    except Exception as e:
        print(f" ✗ Error: {e}")
        return None


def main():
    """Run the model sweep."""
    print("=" * 70)
    print("SD MODEL QUALITY SWEEP")
    print("=" * 70)

    # Create output directory
    output_dir = Path("sd_model_sweep_results")
    output_dir.mkdir(exist_ok=True)
    print(f"\nOutput directory: {output_dir}")

    # Initialize client
    client = LemonadeClient(verbose=False)

    # Check server
    try:
        available_models = client.list_sd_models()
        print(f"Available SD models: {[m['id'] for m in available_models]}")
    except Exception as e:
        print(f"Error: Cannot connect to Lemonade Server: {e}")
        print("Make sure it's running: lemonade-server serve")
        sys.exit(1)

    # Run sweep
    results = []
    total_tests = sum(len(m["sizes"]) * len(TEST_PROMPTS) for m in MODELS)
    test_num = 0

    for model_config in MODELS:
        model_id = model_config["id"]

        print(f"\n{'=' * 70}")
        print(f"MODEL: {model_id}")
        print(f"{'=' * 70}")

        for size in model_config["sizes"]:
            for prompt in TEST_PROMPTS:
                test_num += 1
                print(f"\n[{test_num}/{total_tests}]", end="")

                result = test_model_combination(
                    client, model_id, size, prompt, output_dir
                )
                if result:
                    results.append(result)

    # Save detailed JSON report
    import json
    from datetime import datetime

    report_data = {
        "test_date": datetime.now().isoformat(),
        "total_tests": total_tests,
        "successful": len(results),
        "failed": total_tests - len(results),
        "output_directory": str(output_dir),
        "results": results,
    }

    json_report_path = output_dir / "report.json"
    with open(json_report_path, "w") as f:
        json.dump(report_data, f, indent=2)

    # Generate Markdown report
    md_lines = [
        "# SD Model Quality Sweep Report",
        "",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Total Tests:** {total_tests}",
        f"**Successful:** {len(results)}",
        f"**Failed:** {total_tests - len(results)}",
        "",
        "## Results",
        "",
        "| Image | Model | Size | Steps | CFG | Time | File Size |",
        "|-------|-------|------|-------|-----|------|-----------|",
    ]

    for r in results:
        row = (
            f"| ![{r['filename']}]({r['filename']}) | "
            f"{r['model']} | {r['size']} | {r['steps']} | "
            f"{r['cfg_scale']} | {r['time']:.1f}s | "
            f"{r['file_size']:,} bytes |"
        )
        md_lines.append(row)

    md_lines.extend(
        [
            "",
            "## Summary by Model",
            "",
            "| Model | Images | Avg Time |",
            "|-------|--------|----------|",
        ]
    )

    for model_config in MODELS:
        model_id = model_config["id"]
        model_results = [r for r in results if r["model"] == model_id]
        if model_results:
            avg_time = sum(r["time"] for r in model_results) / len(model_results)
            md_lines.append(f"| {model_id} | {len(model_results)} | {avg_time:.1f}s |")

    md_lines.extend(
        [
            "",
            "## Test Configuration",
            "",
            "**Prompts:**",
        ]
    )
    for i, prompt in enumerate(TEST_PROMPTS, 1):
        md_lines.append(f"{i}. {prompt}")

    md_lines.extend(
        [
            "",
            "**Model Defaults:**",
            "- SD-1.5: 512x512, 20 steps, CFG 7.5",
            "- SD-Turbo: 512x512, 4 steps, CFG 1.0",
            "- SDXL-Base-1.0: 1024x1024, 20 steps, CFG 7.5",
            "- SDXL-Turbo: 512x512, 4 steps, CFG 1.0",
        ]
    )

    md_report_path = output_dir / "report.md"
    with open(md_report_path, "w") as f:
        f.write("\n".join(md_lines))

    # Summary to console
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    print(f"\nTotal tests: {total_tests}")
    print(f"Successful: {len(results)}")
    print(f"Failed: {total_tests - len(results)}")

    if results:
        print(f"\nGeneration times:")
        for r in results:
            print(f"  {r['model']:20s} {r['size']:10s} {r['time']:6.1f}s")

        print(f"\nAll images saved to: {output_dir}/")
        print(f"Detailed report: {json_report_path}")
        print(f"Markdown report: {md_report_path}")

        # Group by model
        print(f"\nBy Model:")
        for model_config in MODELS:
            model_id = model_config["id"]
            model_results = [r for r in results if r["model"] == model_id]
            if model_results:
                avg_time = sum(r["time"] for r in model_results) / len(model_results)
                print(
                    f"  {model_id:20s} {len(model_results)} images, avg {avg_time:.1f}s"
                )


if __name__ == "__main__":
    main()
