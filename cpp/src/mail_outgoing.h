// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// src/gaia/agents/tools/_email/outgoing.py: building, checking and showing a
// message on its way out; mail_mime.h encodes it. A ValueError there is
// std::invalid_argument here, with the same wording.

#pragma once

#include <string>

#include "gaia/mail.h"

namespace gaia::mail::outgoing {

/// `message` made safe to show and send, or throws naming the field at fault.
/// Idempotent, so a saved draft can be re-read and compared.
OutgoingMessage checked(OutgoingMessage message);

/// A comma-separated address list, or throws naming what is wrong.
Recipients parseRecipients(const std::string& raw, const std::string& field);

void requireRecipient(const OutgoingMessage& message);

OutgoingMessage newMessage(const std::string& to, const std::string& subject, const std::string& body,
                           const std::string& cc, const std::string& bcc);

/// A threaded reply to `source`, sent where the mail standard says to.
OutgoingMessage replyMessage(const SourceMessage& source, const std::string& body, const std::string& ownAddress,
                             bool replyAll);

/// `source`'s text under a forwarding header. Its attachments are named, not sent.
OutgoingMessage forwardMessage(const SourceMessage& source, const std::string& to, const std::string& note,
                               const std::string& cc, const std::string& bcc);

/// The message as a person approves it: every recipient, then the whole body.
std::string renderPreview(const OutgoingMessage& message);

/// The fields a tool result reports, so the model repeats real values.
json describe(const OutgoingMessage& message);

}  // namespace gaia::mail::outgoing
