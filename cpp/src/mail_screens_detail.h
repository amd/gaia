// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The pieces of the phishing screen and the waiting-on-you scan that the
// parity tests compare one by one against the Python modules.

#pragma once

#include <cstdint>
#include <set>
#include <string>
#include <vector>

#include "gaia/mail.h"
#include "gaia/types.h"

namespace gaia::mail::detail {

// phishing.py / untrusted_text.py
std::vector<std::string> assessMessage(const std::string& subject, const std::string& sender,
                                       const std::string& body);
std::string neutralizeMarkerNames(std::string body, const std::vector<std::string>& names);
std::string stripUntrustedFieldMarkers(std::string body);

// waiting.py
bool hasDirectAskSignal(const std::string& subjectLower, const std::string& bodyLower);
bool hasMeetingTimeSignal(const std::string& subjectLower, const std::string& bodyLower);
bool isMeetingRequest(const std::string& subject, const std::string& body);
bool isConfidentlyPromotional(const std::string& subject, const std::string& body,
                              const std::vector<std::string>& labelIds);
bool isOptOutReply(const std::string& bodyLower);
bool isSubstantiveText(const std::string& text);
bool isAutomatedSender(const std::string& sender);
std::string normalizeForSuppression(const std::string& addr);
std::string senderAddress(const std::string& rendered);
std::vector<std::string> recipientAddresses(const std::string& rendered);
std::string plainBody(const json& message);
std::set<std::string> quotedLines(const std::vector<json>& messages);
std::string stripQuotedText(const std::string& body, const std::set<std::string>& earlier);
std::string newText(const json& message, const std::set<std::string>& earlier);
bool isAutoReply(const json& message);
int64_t receivedMs(const json& message);
/// detectWaitingOnYou with the clock passed in, as Python's `now_ms`.
json detectWaitingOnYouAt(ReadBackend& backend, int maxInbox, int minAgeHours, int64_t nowMs);

}  // namespace gaia::mail::detail
