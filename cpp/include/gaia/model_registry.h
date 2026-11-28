// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Model capability registry — the C++ mirror of Python's model table.
//
// SOURCE OF TRUTH: ``MODELS`` in ``src/gaia/llm/lemonade_client.py``.
// This file is a hand-maintained mirror of the ``model_id`` -> ``tool_calling``
// column of that table. When a model is added, removed, or has its
// ``tool_calling`` flag flipped in Python, mirror the change here in the same
// commit. ``tests/unit/test_cpp_model_registry_mirror.py`` fails when the two
// tables differ.

#pragma once

#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

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

/// What the table says about ``modelId``, or nothing when it is not listed.
/// An agent with a server to ask (Agent::processQuery) settles an unlisted
/// model from the server's own catalog instead of assuming "no".
GAIA_API std::optional<bool> knownToolCalling(const std::string& modelId);

/// Whether ``modelId`` runs with thinking on, sent as
/// ``chat_template_kwargs.enable_thinking`` on every request. Mirrors
/// ``thinking`` in Python's ``MODELS`` and matches ids the way Python does:
/// case-insensitive, with or without a ``user.`` prefix. Nothing for a model
/// whose template has no such switch, so the request carries none.
GAIA_API std::optional<bool> knownThinking(const std::string& modelId);

/// The sampling ``modelId``'s card publishes for the mode it runs in (temperature,
/// top_p, top_k, min_p and penalties), or nothing for a model without a profile.
/// Mirrors ``MODEL_SAMPLING_PROFILES`` in Python and picks the mode the way
/// ``CardSampling.select`` does: the ``knownThinking`` choice, else the model's default.
GAIA_API std::optional<nlohmann::json> knownSampling(const std::string& modelId);

/// True for a small model that gets the compact feature set: fewer tools and no
/// prose copy of the tool list, so every step re-reads a shorter prompt. Mirrors
/// ``feature_tier="compact"`` in Python's ``MODELS``, plus the small local models
/// gaiacpp has been measured on. Unlisted models get the full set.
GAIA_API bool isCompactModel(const std::string& modelId);

/// Tools a compact model is not offered: each has a shell or file-tool
/// equivalent, and their schemas cost every step.
GAIA_API const std::vector<std::string>& compactWithheldTools();

} // namespace gaia
