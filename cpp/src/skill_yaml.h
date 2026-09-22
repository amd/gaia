// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Internal helpers shared by the two YAML surfaces of the skills runtime:
// the SKILL.md frontmatter parser (skill.cpp) and the gaia-agent.yaml skill
// declaration parser (skill_sets.cpp).
//
// Both must read the same bytes the same way Python does, so the scalar
// resolution here is PyYAML's SafeLoader (YAML 1.1) and the message helpers
// reproduce Python's repr() / str() / type().__name__ spelling. One copy,
// because two would drift: `required: yes` is a bool in one runtime and a
// string in the other the moment these diverge.
//
// Not a public header — nothing here is installed or part of the SDK surface.

#pragma once

#include <regex>
#include <string>

#include <yaml-cpp/yaml.h>

#include "gaia/skill.h"

namespace gaia {
namespace detail {

// ---------------------------------------------------------------------------
// Message helpers — the error wording is a cross-runtime contract, so these
// reproduce Python's repr()/str()/type-name spelling rather than C++'s.
// ---------------------------------------------------------------------------

/// Python's ``type(x).__name__`` for a JSON value.
std::string pyTypeName(const SkillJson& value);

/// Python's ``repr()`` for a string.
std::string pyRepr(const std::string& text);

/// Python's ``str()`` for a JSON value (used where Python interpolates raw).
std::string pyStr(const SkillJson& value);

/// Python's ``repr()`` for a scalar JSON value.
std::string pyRepr(const SkillJson& value);

// ---------------------------------------------------------------------------
// Scalar resolution
//
// These patterns are PyYAML's SafeLoader implicit resolvers (YAML 1.1),
// transcribed verbatim, because the Python side resolves scalars with
// ``yaml.safe_load``. Using the YAML 1.2 core schema instead would make the two
// runtimes read different values out of the same bytes — `flag: yes` a string
// here and a bool there, `mode: 0755` 755 here and 493 there.
//
// resolvePlainScalar() and skill.cpp's needsQuoting() are exact inverses, which
// is what makes SKILL.md round-trip identity hold.
// ---------------------------------------------------------------------------

const std::regex& nullPattern();
const std::regex& boolPattern();
const std::regex& intPattern();
const std::regex& floatPattern();

/// PyYAML resolves these to types nlohmann::json cannot hold: an infinity, a
/// NaN, a timestamp, and the `=` / `<<` control tags. We keep the literal text
/// and write it back unquoted, so Python still reads the value it read before.
bool isUnrepresentableScalar(const std::string& text);

/// Resolve a *plain* (unquoted) YAML scalar to its JSON type.
SkillJson resolvePlainScalar(const std::string& text);

// ---------------------------------------------------------------------------
// YAML -> JSON
// ---------------------------------------------------------------------------

/// Convert a parsed YAML node to ordered JSON, preserving key order.
///
/// @param source Quoted in error messages.
/// @param what Names the surface being read ("frontmatter", "agent manifest"),
///        so a gaia-agent.yaml error does not talk about SKILL.md frontmatter.
/// @param docsUrl Where the error message points the author next.
/// @throws SkillValidationError on an empty/null/non-scalar key or an
///         unsupported custom tag — the same two refusals PyYAML makes.
SkillJson yamlToJson(const YAML::Node& node, const std::string& source,
                     const char* what = "frontmatter",
                     const char* docsUrl = FORMAT_DOCS_URL);

}  // namespace detail
}  // namespace gaia
