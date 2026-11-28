// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The Python string behaviour the mail screens depend on (str.lower, re's
// \w \d \s and IGNORECASE, splitlines, html.unescape, email.utils address
// parsing), reproduced from tables util/gen_cpp_mail_screens.py reads off the
// Python runtime. Indexes and lengths are code points, as in Python.

#pragma once

#include <string>
#include <vector>

#include "gaia/types.h"

namespace gaia::mail::text {

/// Code points. An invalid UTF-8 byte b decodes to 0xDC00 + b and encodes back
/// to b, like Python's surrogateescape, so no input byte is lost.
using Cps = std::u32string;

Cps decode(const std::string& utf8);
std::string encode(const Cps& cps);
std::string encode(char32_t cp);

bool isWord(char32_t cp);   // re \w
bool isDigit(char32_t cp);  // re \d
bool isSpace(char32_t cp);  // re \s, str.isspace

/// Whether `cp` matches the pattern character `pattern` under re.IGNORECASE.
bool sameIgnoreCase(char32_t cp, char pattern);
/// Whether the pattern literal `literal` (ASCII) matches `text` at `at` under re.IGNORECASE.
bool matchesIgnoreCase(const Cps& text, size_t at, const char* literal);
/// re's \b at `at`.
bool wordBoundary(const Cps& text, size_t at);

std::string lower(const std::string& text);  // str.lower
std::string strip(const std::string& text);  // str.strip()
std::vector<std::string> split(const std::string& text);       // str.split()
std::vector<std::string> splitlines(const std::string& text);  // str.splitlines()
std::string htmlUnescape(const std::string& text);              // html.unescape
/// repr() of a JSON value the way Python prints the same value.
std::string pyRepr(const json& value);

struct Address {
    std::string name;
    std::string addr;
};
/// email.utils.getaddresses([field]) and parseaddr(field), strict mode.
std::vector<Address> getaddresses(const std::string& field);
Address parseaddr(const std::string& field);

/// untrusted_text helpers: the ASCII letters a code point reads as for marker
/// names, which bracket it reads as ('<', '>' or 0), and whether it is
/// invisible inside a bracket run.
const char* markerLetters(char32_t cp);
char fenceSide(char32_t cp);
bool fenceRunInvisible(char32_t cp);

/// Python truthiness of a JSON value; a missing key is null.
bool truthy(const json& value);
/// `obj.get(key) or ""` for a field that must be text. A truthy non-string
/// throws std::invalid_argument, where Python would fail calling a str method.
std::string textField(const json& obj, const char* key);

}  // namespace gaia::mail::text
