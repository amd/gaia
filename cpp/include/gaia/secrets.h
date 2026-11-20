// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Credential-shaped text detection, ported from Python's MemoryMixin
// (_CREDENTIAL_PATTERNS and _card_spans). A backstop, not a guarantee: it
// catches known key prefixes, keyword-adjacent secrets, PINs, card security
// codes, SSNs, and card numbers that pass the issuer, grouping and Luhn checks.
// Only ASCII digits are read as card digits.
//
// Also the masking of tool results, ported from Python's credential_shapes:
// provider tokens, URL userinfo, secret query values and credential-file lines.

#pragma once

#include <cstddef>
#include <string>
#include <utility>
#include <vector>

#include "gaia/export.h"
#include "gaia/types.h"

namespace gaia {

/// Sorted, merged [begin, end) byte ranges of credential-shaped text.
GAIA_API std::vector<std::pair<std::size_t, std::size_t>> credentialSpans(const std::string& text);

/// True when @p text carries anything credentialSpans() finds.
GAIA_API bool looksLikeCredential(const std::string& text);

/// @p text with each credential span replaced by "[secret removed]".
GAIA_API std::string maskCredentials(const std::string& text);

/// @p text with URL userinfo, secret query values and provider tokens replaced by "[redacted]".
GAIA_API std::string redactCredentials(const std::string& text);

/// redactCredentials() for text read out of a file: a bare login name ("git@"),
/// a placeholder ("?key={api_key}") and a hyphenated name ("sk-learn-...") stay.
GAIA_API std::string redactFileText(const std::string& text);

/// True when redactFileText() would change @p text.
GAIA_API bool holdsCredentials(const std::string& text);

/// True for a file the agent may not read by name: ".env", "id_rsa", "*.pem" and the like.
GAIA_API bool isCredentialFileName(const std::string& name);

/// @p text with each line a search printed from a credential file ("./.env:3:KEY=...")
/// cut after its file-name prefix and ended with "[credential file content hidden]".
GAIA_API std::string maskCredentialFileLines(const std::string& text);

/// Command output: maskCredentialFileLines() then redactCredentials() on every string in @p value.
GAIA_API json redactToolOutput(json value);

/// File content: redactFileText() on every string in @p value; lines are never hidden.
GAIA_API json redactFileContent(json value);

/// @p value as the model may see it from @p tool: redactToolOutput() for a tool
/// that prints what a command wrote (bash_execute, git_*), else redactFileContent().
GAIA_API json redactToolResult(const std::string& tool, json value);

}  // namespace gaia
