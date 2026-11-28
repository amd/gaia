// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// A message as the RFC 5322 text Python's EmailMessage(policy=SMTP) writes.

#pragma once

#include <string>

#include "gaia/mail.h"

namespace gaia::mail::mime {

/// The text for `message`. `boundary` is used only for a multipart message.
std::string text(const OutgoingMessage& message, const std::string& boundary);

/// text() as the unpadded base64url Gmail's `raw` field takes, with a fresh boundary.
std::string raw(const OutgoingMessage& message);

/// `value` opens with an RFC 2047 encoded word, as get_encoded_word judges one before decoding it.
bool startsWithEncodedWord(const std::string& value);

/// `bytes` as base64 (standard alphabet, padded).
std::string base64(const std::string& bytes);

}  // namespace gaia::mail::mime
