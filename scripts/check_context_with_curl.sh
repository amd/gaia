#!/bin/bash
# Check if Lemonade API exposes context size information using pure curl

echo "╔════════════════════════════════════════════════════════════════════════════╗"
echo "║  Lemonade API Context Size Check (Pure curl)                               ║"
echo "╚════════════════════════════════════════════════════════════════════════════╝"
echo ""

BASE_URL="http://localhost:8000"

# Step 1: Load a model
echo "📝 Step 1: Load Qwen3-8B-GGUF model"
echo "════════════════════════════════════════════════════════════════════════════"
curl -s -X POST "${BASE_URL}/api/v1/models/load" \
  -H "Content-Type: application/json" \
  -d '{"model": "Qwen3-8B-GGUF"}' | python -m json.tool
echo ""
echo ""

# Step 2: Check health endpoint
echo "📝 Step 2: Check /api/v1/health for loaded model info"
echo "════════════════════════════════════════════════════════════════════════════"
curl -s "${BASE_URL}/api/v1/health" | python -m json.tool | grep -A20 "all_models_loaded"
echo ""
echo ""

# Step 3: Check models list
echo "📝 Step 3: Check /api/v1/models for model info"
echo "════════════════════════════════════════════════════════════════════════════"
curl -s "${BASE_URL}/api/v1/models" | python -m json.tool | head -50
echo ""
echo ""

# Step 4: Check specific model info
echo "📝 Step 4: Check /api/v1/models/{model_id} for specific model"
echo "════════════════════════════════════════════════════════════════════════════"
curl -s "${BASE_URL}/api/v1/models/Qwen3-8B-GGUF" | python -m json.tool
echo ""
echo ""

# Step 5: Try llamacpp-specific endpoint (if available)
echo "📝 Step 5: Check /v1/models (OpenAI format)"
echo "════════════════════════════════════════════════════════════════════════════"
curl -s "${BASE_URL}/v1/models" 2>&1 | python -m json.tool 2>&1 | head -30
echo ""
echo ""

# Step 6: Search for context in all responses
echo "╔════════════════════════════════════════════════════════════════════════════╗"
echo "║  SEARCHING FOR CONTEXT-RELATED FIELDS                                      ║"
echo "╚════════════════════════════════════════════════════════════════════════════╝"
echo ""

echo "Checking /api/v1/health for context keywords:"
curl -s "${BASE_URL}/api/v1/health" | grep -i "context\|ctx\|n_ctx\|context_length" && echo "✓ Found" || echo "❌ Not found"
echo ""

echo "Checking /api/v1/models for context keywords:"
curl -s "${BASE_URL}/api/v1/models" | grep -i "context\|ctx\|n_ctx\|context_length" && echo "✓ Found" || echo "❌ Not found"
echo ""

echo "╔════════════════════════════════════════════════════════════════════════════╗"
echo "║  CONCLUSION                                                                 ║"
echo "╚════════════════════════════════════════════════════════════════════════════╝"
echo ""
echo "If no context-related fields found above:"
echo "  → Lemonade API does NOT expose context size"
echo "  → Cannot programmatically verify context after loading"
echo "  → Must rely on LemonadeManager.ensure_ready() to configure correctly"
echo ""
echo "If context fields ARE found:"
echo "  → Update init_command.py to extract and verify context size"
echo "  → Add validation in _test_model_inference() after loading"
echo ""
