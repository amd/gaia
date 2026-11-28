// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Ported from person_approval.sendable_address. Apart from person_approval.cpp so
// that showing a card doesn't link in the IDNA tables.

#include <cstdio>
#include <stdexcept>
#include <string>

#include "gaia/idna.h"
#include "gaia/person_approval.h"
#include "mail_text.h"

namespace gaia::approval {

namespace {

std::string repr(const std::string& s) { return mail::text::pyRepr(json(s)); }

bool isAscii(const std::string& s) {
    for (unsigned char c : s) {
        if (c >= 0x80) return false;
    }
    return true;
}

}  // namespace

std::string sendableAddress(const std::string& address, const std::string& field) {
    for (char32_t cp : mail::text::decode(address)) {
        const std::string one = mail::text::encode(cp);
        if (visible(one, Shown::Name) != one) {
            char code[16];
            std::snprintf(code, sizeof code, "%04X", static_cast<unsigned>(cp));
            throw std::invalid_argument("`" + field + "` has an address with an invisible or control character (U+" +
                                        code + "), which could disguise who receives it: " +
                                        repr(visibleLine(address)) + ". Give the plain address.");
        }
    }
    const size_t at = address.find('@');
    bool shaped = at != std::string::npos && at > 0 && at + 1 < address.size() &&
                  address.find('@', at + 1) == std::string::npos;
    for (char32_t cp : mail::text::decode(address)) {
        if (cp != U'@' && (mail::text::isSpace(cp) || cp == U'<' || cp == U'>' || cp == U'"' || cp == U',' ||
                           cp == U';')) {
            shaped = false;
        }
    }
    if (!shaped) {
        throw std::invalid_argument("`" + field + "` has an address that can't be used: " + repr(address) +
                                    ". Give a plain address like name@example.com.");
    }
    const std::string local = address.substr(0, at);
    const std::string domain = address.substr(at + 1);
    if (!isAscii(local)) {
        throw std::invalid_argument("`" + field + "` has non-ASCII characters before the @ in " + repr(address) +
                                    ", which GAIA can't send to. Check the address with its owner.");
    }
    if (isAscii(domain)) return address;
    try {
        return local + "@" + idna::encodeUts46(mail::text::decode(domain));
    } catch (const idna::IdnaError& e) {
        throw std::invalid_argument("`" + field + "` has a domain that can't be used: " + repr(domain) + " (" +
                                    e.what() + "). Check the spelling.");
    }
}

}  // namespace gaia::approval
