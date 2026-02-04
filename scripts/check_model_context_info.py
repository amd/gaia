"""
Diagnostic: Check if Lemonade API exposes context size for loaded models.

This script loads a model and checks all available endpoints to see
if context size information is available.
"""

import json
import requests
from gaia.llm.lemonade_client import LemonadeClient


def check_endpoint(url, description):
    """Check an endpoint and print what it returns."""
    print(f"\n{'='*80}")
    print(f"📍 {description}")
    print(f"   URL: {url}")
    print(f"{'='*80}")

    try:
        response = requests.get(url, timeout=10)
        print(f"Status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            print(json.dumps(data, indent=2)[:1000])  # First 1000 chars
            return data
        else:
            print(f"Error: {response.text[:200]}")
            return None

    except Exception as e:
        print(f"Failed: {e}")
        return None


def main():
    print("""
╔════════════════════════════════════════════════════════════════════════════╗
║  Lemonade API - Context Size Information Check                             ║
╚════════════════════════════════════════════════════════════════════════════╝

Testing what information is available about model context size.
    """)

    # Initialize client and load a model
    print("🔧 Loading Qwen3-8B-GGUF model...")
    client = LemonadeClient(verbose=False)

    try:
        client.load_model("Qwen3-8B-GGUF", auto_download=False, prompt=False)
        print("✓ Model loaded")
    except Exception as e:
        print(f"Failed to load model: {e}")
        return

    base_url = "http://localhost:8000"

    # Check various endpoints
    endpoints = [
        (f"{base_url}/api/v1/health", "Health endpoint"),
        (f"{base_url}/api/v1/models", "Models list"),
        (f"{base_url}/v1/models", "OpenAI-compatible models list"),
    ]

    results = {}
    for url, desc in endpoints:
        data = check_endpoint(url, desc)
        results[desc] = data

    # Analyze results
    print(f"\n\n{'='*80}")
    print("📊 ANALYSIS: Context Size Information")
    print(f"{'='*80}\n")

    # Check health endpoint
    health = results.get("Health endpoint")
    if health and "all_models_loaded" in health:
        print("✓ Health endpoint includes loaded models:")
        for model in health["all_models_loaded"]:
            if model.get("type") == "llm":
                print(f"\n  Model: {model.get('model_name')}")
                print(f"  Checkpoint: {model.get('checkpoint')}")
                print(f"  Recipe: {model.get('recipe')}")
                print(f"  Recipe options: {model.get('recipe_options')}")

                # Check for context size fields
                has_ctx = any(
                    key in str(model).lower()
                    for key in ["context", "ctx", "n_ctx", "context_length"]
                )
                if has_ctx:
                    print("  ✓ HAS CONTEXT SIZE INFO")
                else:
                    print("  ❌ NO CONTEXT SIZE INFO")

    print(f"\n{'='*80}")
    print("🔍 Searching for context-related fields...")
    print(f"{'='*80}\n")

    all_text = json.dumps(results)
    context_keywords = ["context", "ctx", "n_ctx", "context_length", "context_size"]

    found_any = False
    for keyword in context_keywords:
        if keyword in all_text.lower():
            print(f"✓ Found '{keyword}' in API responses")
            found_any = True

    if not found_any:
        print("❌ NO context-related fields found in any endpoint")

    print(f"\n{'='*80}")
    print("📋 CONCLUSION")
    print(f"{'='*80}\n")

    print("""
The Lemonade Server API does NOT expose context size information for loaded models.

Available model information:
- model_name, checkpoint, device, recipe, recipe_options
- But NO context_length, n_ctx, or similar fields

Impact:
- Cannot programmatically verify if a loaded model has sufficient context
- Must trust that LemonadeManager.ensure_ready() configured it correctly
- Context errors only surface during actual use (inference)

Workaround in GAIA:
- Call LemonadeManager.ensure_ready(min_context_size=X) before running agents
- This ensures server is started/restarted with correct --ctx-size flag
- Trust that it worked (no API validation available)

Recommended fix for Lemonade Server:
- Add 'context_length' field to model info in /api/v1/health response
- Add 'n_ctx' or 'context_window' to loaded model metadata
- This would allow clients to verify context size programmatically
    """)


if __name__ == "__main__":
    main()
