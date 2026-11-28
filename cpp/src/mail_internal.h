// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Helpers the Gmail and Graph backends share. Most of them reproduce the
// Python idiom the backends were written in (`d.get(k) or default`, `str()`,
// `repr()`, `str.strip()`), so the two runtimes agree on odd inputs too.

#pragma once

#include <string>
#include <utility>
#include <vector>

#include "gaia/mail.h"

namespace gaia::mail::detail {

using Params = std::vector<std::pair<std::string, std::string>>;

/// `obj[key]`, or null when `obj` is not an object or has no such key.
const json& field(const json& obj, const char* key);

/// Python truthiness of a JSON value.
bool truthy(const json& value);

/// `value` when it is a string, else "".
std::string textOf(const json& value);

/// An ASCII letter or digit, or one of `extra`.
bool isWordChar(char c, const char* extra);

/// `str(value)` for a JSON scalar, as Python prints it.
std::string pyStr(const json& value);

/// `repr(value)` for a JSON scalar, as Python prints it.
std::string pyRepr(const json& value);

/// `int(value)` when Python would accept it; false otherwise.
bool pyInt(const json& value, long long& out);

/// `str.strip()`, `str.lstrip()` and `str.isspace()` over Unicode whitespace.
std::string pyStrip(const std::string& s);
std::string pyLstrip(const std::string& s);
bool pyIsSpace(const std::string& s);

/// `urllib.parse.quote(s, safe="")`.
std::string percentEncode(const std::string& s);

/// Appends code point `cp` to `out` as UTF-8.
void appendUtf8(std::string& out, long cp);

/// `bytes.decode("utf-8", errors="replace")`.
std::string utf8Replace(const std::string& bytes);

/// The parsed body of a successful answer; MailboxError when it is not JSON.
json parseAnswer(const HttpResponse& response, const std::string& provider, const std::string& where);

/// A copy of `err` with `connectors` as both what to connect and what to reconnect.
template <typename E>
E withConnector(E err, const char* connector) {
    err.connect = {connector};
    err.reconnect = {connector};
    return err;
}

}  // namespace gaia::mail::detail
