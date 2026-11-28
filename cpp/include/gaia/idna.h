// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// idna.encode(domain, uts46=True) from the Python idna package, with the
// unicodedata.normalize("NFC") it relies on, shared by every tool that
// checks an address's domain. Tables: src/idna_tables.inc.

#pragma once

#include "gaia/export.h"

#include <stdexcept>
#include <string>

namespace gaia::idna {

/// idna.IDNAError; what() is the exception's str() in Python.
struct GAIA_API IdnaError : std::runtime_error {
    using std::runtime_error::runtime_error;
};

/// idna.encode(domain, uts46=True): the domain in ASCII. Throws IdnaError.
GAIA_API std::string encodeUts46(const std::u32string& domain);

/// unicodedata.normalize("NFC", text).
GAIA_API std::u32string nfc(const std::u32string& text);

}  // namespace gaia::idna
