// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// JSON parsing utilities for agent responses.
// Ported from Python: src/gaia/agents/base/agent.py
//   - _extract_json_from_response()
//   - validate_json_response()
//   - _parse_llm_response()

#pragma once

#include <optional>
#include <string>

#include <nlohmann/json.hpp>

#include "types.h"
#include "gaia/export.h"

namespace gaia {

using json = nlohmann::json;

/// Extract JSON from an LLM response using multiple strategies.
/// Mirrors Python Agent._extract_json_from_response().
///
/// Strategies (in order):
///   1. Extract from ```json ... ``` code blocks
///   2. Bracket-matching to find first complete JSON object
///
/// @param response Raw response string from LLM
/// @return Extracted JSON object, or std::nullopt if extraction failed
GAIA_API std::optional<json> extractJsonFromResponse(const std::string& response);

/// Validate and fix a JSON response string.
/// Mirrors Python Agent.validate_json_response().
///
/// Attempts in order:
///   1. Parse as-is
///   2. Extract from code blocks
///   3. Bracket-matching for first complete JSON object
///   4. Fix common syntax errors (trailing commas, single quotes)
///
/// @param responseText Raw response text
/// @return Validated JSON object
/// @throws std::runtime_error if response cannot be parsed
GAIA_API json validateJsonResponse(const std::string& responseText);

/// Parse an LLM response into a structured ParsedResponse.
/// Mirrors Python Agent._parse_llm_response().
///
/// Handles:
///   - Plain text (conversational) responses
///   - Valid JSON responses
///   - Malformed JSON with fallback extraction
///   - Regex-based field extraction as last resort
///
/// @param response Raw response from LLM
/// @return Parsed response structure
GAIA_API ParsedResponse parseLlmResponse(const std::string& response);

/// Fix common JSON syntax errors.
/// - Remove trailing commas before } or ]
/// - Convert single quotes to double quotes (if no double quotes present)
/// - Strip text before first { or [
///
/// @param text Potentially malformed JSON text
/// @return Fixed text
GAIA_API std::string fixCommonJsonErrors(const std::string& text);

/// Use bracket-matching to extract the first complete JSON object from text.
/// Properly handles nested braces, strings, and escape sequences.
///
/// @param text Text containing JSON
/// @return Extracted JSON string, or empty string if not found
GAIA_API std::string extractFirstJsonObject(const std::string& text);

} // namespace gaia
