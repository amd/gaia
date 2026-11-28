// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Calls only a person may approve, and the text that person reads. Ported from
// src/gaia/agents/base/person_approval.py and the prompt keys of call_risk.py.
// What a person approves must be what goes out: text is shown with characters
// that hide or reorder it written as [U+XXXX], and an address is checked and
// shown in the form it is sent.

#pragma once

#include <string>

#include "gaia/export.h"

namespace gaia::approval {

/// Display-only prompt key carrying the risk note of a call. A call that has
/// one is approved once or not at all.
constexpr const char* kRiskNoteArg = "_risk_note";
/// Prompt key marking a call only a person may approve. The registry adds it;
/// front-ends honor it and never show it.
constexpr const char* kNeedsPersonArg = "_needs_person";
/// Display-only prompt key carrying the literal text a person is approving.
constexpr const char* kExactTextArg = "_exact_text";

/// The note a prompt carries when its text holds characters that don't show.
GAIA_API extern const char* const kHiddenNote;

/// How visible() writes a field. Prose keeps its line breaks; Line codes them.
/// Name (a path, URL, id, program or "always allow" label) codes every
/// character kept in prose and every variation selector too, CR included, as
/// Python's visible_strict does; Command does the same to a command's words but
/// keeps its tabs and line breaks.
enum class Shown { Prose, Line, Name, Command };

/// Characters that hide or reorder text written as [U+XXXX], as the Python
/// agent's approval cards show them. Bytes that aren't UTF-8 pass through.
GAIA_API std::string visible(const std::string& text, Shown how = Shown::Prose);

/// person_approval.visible_line.
inline std::string visibleLine(const std::string& text) { return visible(text, Shown::Line); }

/// person_approval.has_unseen: anything a reader can't see, even kept as written.
GAIA_API bool hasUnseen(const std::string& text);

/// person_approval.sendable_address: `address` as it goes on the wire, an
/// international domain in its ASCII (punycode) form. Throws
/// std::invalid_argument naming `field` in Python's words.
GAIA_API std::string sendableAddress(const std::string& address, const std::string& field);

}  // namespace gaia::approval
