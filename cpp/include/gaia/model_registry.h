// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Model capability registry — the C++ mirror of Python's model table.
//
// SOURCE OF TRUTH: ``MODELS`` in ``src/gaia/llm/lemonade_client.py``.
// This file is a hand-maintained mirror of the ``model_id`` -> ``tool_calling``
// column of that table. When a model is added, removed, or has its
// ``tool_calling`` flag flipped in Python, mirror the change here in the same
// commit. ``ModelGateTest.MirrorsThePythonModelTableExactly`` in
// ``cpp/tests/test_native_tool_calls.cpp`` pins the current contents so a silent
// drift shows up as a test edit in review.

#pragma once

#include <string>
#include <vector>

#include "gaia/export.h"

namespace gaia {

/// One row of the mirrored capability table.
struct ModelCapability {
    std::string modelId;
    bool toolCalling;
};

/// The mirrored table, in the same order as Python's ``MODELS``.
GAIA_API const std::vector<ModelCapability>& knownModels();

/// Return true when ``modelId`` is known to support native OpenAI
/// ``tools`` / ``tool_calls`` against the configured server.
///
/// Ports Python's ``is_tool_calling_model`` with ONE deliberate divergence:
/// Python returns ``True`` for an unknown model id (its Tier-0 testing covered
/// every Lemonade GGUF build). The C++ framework is documented to run against
/// *any* OpenAI-compatible server — llama.cpp, Ollama, vLLM — where an
/// unrecognised model id says nothing about tool-calling support. Guessing
/// "yes" there sends a ``tools`` array to a server that may reject it or
/// silently ignore it. Unknown therefore resolves to ``false``: the model keeps
/// the prompt-JSON path that works everywhere, and a caller who knows better
/// opts in explicitly with ``AgentConfig::nativeToolCalls =
/// NativeToolCalls::Always``.
GAIA_API bool isToolCallingModel(const std::string& modelId);

} // namespace gaia
