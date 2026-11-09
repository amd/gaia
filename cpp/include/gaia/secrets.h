// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Credential-shaped text detection, ported from Python's MemoryMixin
// (_CREDENTIAL_PATTERNS and _card_spans). A backstop, not a guarantee: it
// catches known key prefixes, keyword-adjacent secrets, PINs, card security
// codes, SSNs, and card numbers that pass the issuer, grouping and Luhn checks.
// Only ASCII digits are read as card digits.

#pragma once

#include <cstddef>
#include <string>
#include <utility>
#include <vector>

#include "gaia/export.h"

namespace gaia {

/// Sorted, merged [begin, end) byte ranges of credential-shaped text.
GAIA_API std::vector<std::pair<std::size_t, std::size_t>> credentialSpans(const std::string& text);

/// True when @p text carries anything credentialSpans() finds.
GAIA_API bool looksLikeCredential(const std::string& text);

/// @p text with each credential span replaced by "[secret removed]".
GAIA_API std::string maskCredentials(const std::string& text);

}  // namespace gaia
