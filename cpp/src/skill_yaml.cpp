// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// PyYAML-compatible scalar resolution, YAML -> JSON conversion, and
// Python-style message formatting. Extracted from skill.cpp so the
// gaia-agent.yaml parser (skill_sets.cpp) reads YAML exactly as the SKILL.md
// parser does. See skill_yaml.h.

#include "skill_yaml.h"

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <string>

namespace gaia {
namespace detail {

// ---------------------------------------------------------------------------
// Message helpers
// ---------------------------------------------------------------------------

/// Python's ``type(x).__name__`` for a JSON value.
std::string pyTypeName(const SkillJson& value) {
    switch (value.type()) {
        case SkillJson::value_t::null:            return "NoneType";
        case SkillJson::value_t::string:          return "str";
        case SkillJson::value_t::boolean:         return "bool";
        case SkillJson::value_t::number_integer:
        case SkillJson::value_t::number_unsigned: return "int";
        case SkillJson::value_t::number_float:    return "float";
        case SkillJson::value_t::array:           return "list";
        case SkillJson::value_t::object:          return "dict";
        default:                             return "object";
    }
}

/// Python's ``repr()`` for a scalar JSON value.
std::string pyRepr(const std::string& text) {
    std::string out = "'";
    char buf[8];
    for (char raw : text) {
        const unsigned char c = static_cast<unsigned char>(raw);
        switch (c) {
            case '\\': out += "\\\\"; break;
            case '\'': out += "\\'";  break;
            case '\n': out += "\\n";  break;
            case '\r': out += "\\r";  break;
            case '\t': out += "\\t";  break;
            default:
                if (c < 0x20 || c == 0x7f) {
                    std::snprintf(buf, sizeof(buf), "\\x%02x", c);
                    out += buf;
                } else {
                    out += raw;
                }
        }
    }
    out += "'";
    return out;
}

/// Python's ``str()`` for a JSON value (used where Python interpolates raw).
std::string pyStr(const SkillJson& value) {
    if (value.is_string()) return value.get<std::string>();
    if (value.is_null()) return "None";
    if (value.is_boolean()) return value.get<bool>() ? "True" : "False";
    if (value.is_array() || value.is_object()) {
        // Python separates container items with ", "; json::dump() uses ",".
        std::string out = value.is_array() ? "[" : "{";
        bool first = true;
        for (auto it = value.begin(); it != value.end(); ++it) {
            if (!first) out += ", ";
            first = false;
            if (value.is_object()) out += pyRepr(SkillJson(it.key())) + ": ";
            out += pyRepr(*it);
        }
        out += value.is_array() ? "]" : "}";
        return out;
    }
    return value.dump();
}

std::string pyRepr(const SkillJson& value) {
    if (value.is_string()) return pyRepr(value.get<std::string>());
    return pyStr(value);
}


// ---------------------------------------------------------------------------
// Scalar resolution
//
// These patterns are PyYAML's SafeLoader implicit resolvers (YAML 1.1),
// transcribed verbatim, because format.py resolves scalars with
// ``yaml.safe_load``. Using the YAML 1.2 core schema instead would make the two
// runtimes read different values out of the same bytes — `flag: yes` a string
// here and a bool there, `mode: 0755` 755 here and 493 there.
//
// resolvePlainScalar() and needsQuoting() are exact inverses, which is what
// makes round-trip identity hold.
// ---------------------------------------------------------------------------

const std::regex& nullPattern() {
    static const std::regex re(R"(^(?:~|null|Null|NULL|)$)");
    return re;
}

const std::regex& boolPattern() {
    static const std::regex re(
        R"(^(?:yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE)"
        R"(|on|On|ON|off|Off|OFF)$)");
    return re;
}

const std::regex& intPattern() {
    static const std::regex re(
        R"(^(?:[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*))"
        R"(|[-+]?0x[0-9a-fA-F_]+|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)$)");
    return re;
}

const std::regex& floatPattern() {
    static const std::regex re(
        R"(^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?)"
        R"(|\.[0-9_]+(?:[eE][-+][0-9]+)?)"
        R"(|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*)$)");
    return re;
}

/// PyYAML resolves these to types nlohmann::json cannot hold: an infinity, a
/// NaN, a timestamp, and the `=` / `<<` control tags. We keep the literal text
/// and write it back unquoted, so Python still reads the value it read before.
bool isUnrepresentableScalar(const std::string& text) {
    static const std::regex re(
        R"(^(?:[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)|=|<<)$)"
        R"(|^[0-9]{4}-[0-9]{2}-[0-9]{2}$)"
        R"(|^[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:[Tt]|[ \t]+)[0-9]{1,2})"
        R"(:[0-9]{2}:[0-9]{2}(?:\.[0-9]*)?)"
        R"((?:[ \t]*(?:Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?$)");
    return std::regex_match(text, re);
}

// Internal to this translation unit — not declared in skill_yaml.h.
namespace {

std::string withoutUnderscores(const std::string& text) {
    std::string out;
    out.reserve(text.size());
    for (char c : text) {
        if (c != '_') out += c;
    }
    return out;
}

/// Parse a YAML 1.1 integer: decimal, 0b binary, 0-prefixed octal, 0x hex, or
/// sexagesimal (`12:30` == 750). Returns the literal text when out of range.
SkillJson parseYamlInt(const std::string& raw) {
    const std::string text = withoutUnderscores(raw);
    size_t i = 0;
    bool negative = false;
    if (text[0] == '-' || text[0] == '+') {
        negative = text[0] == '-';
        i = 1;
    }
    const std::string digits = text.substr(i);

    if (digits.find(':') != std::string::npos) {
        long long value = 0;
        size_t start = 0;
        while (start <= digits.size()) {
            const size_t colon = digits.find(':', start);
            const std::string part = digits.substr(
                start, colon == std::string::npos ? std::string::npos : colon - start);
            value = value * 60 + std::strtoll(part.c_str(), nullptr, 10);
            if (colon == std::string::npos) break;
            start = colon + 1;
        }
        return negative ? -value : value;
    }

    int base = 10;
    std::string body = digits;
    if (digits.size() > 2 && digits[0] == '0' && (digits[1] == 'b' || digits[1] == 'B')) {
        base = 2;
        body = digits.substr(2);
    } else if (digits.size() > 2 && digits[0] == '0' &&
               (digits[1] == 'x' || digits[1] == 'X')) {
        base = 16;
        body = digits.substr(2);
    } else if (digits.size() > 1 && digits[0] == '0') {
        base = 8;
        body = digits.substr(1);
    }

    errno = 0;
    char* end = nullptr;
    const long long value = std::strtoll(body.c_str(), &end, base);
    if (errno != 0 || end == nullptr || *end != '\0') {
        return raw;  // out of range — keep the literal rather than truncate
    }
    return negative ? -value : value;
}

SkillJson parseYamlFloat(const std::string& raw) {
    const std::string text = withoutUnderscores(raw);
    if (text.find(':') != std::string::npos) {
        const size_t dot = text.find('.');
        const SkillJson whole = parseYamlInt(text.substr(0, dot));
        const double fraction = std::strtod(("0" + text.substr(dot)).c_str(), nullptr);
        if (!whole.is_number_integer()) return raw;
        const double value = static_cast<double>(whole.get<long long>());
        return value < 0 ? value - fraction : value + fraction;
    }
    errno = 0;
    char* end = nullptr;
    const double value = std::strtod(text.c_str(), &end);
    if (errno != 0 || end == nullptr || *end != '\0') return raw;
    return value;
}

}  // namespace

/// Resolve a *plain* (unquoted) YAML scalar to its JSON type.
SkillJson resolvePlainScalar(const std::string& text) {
    if (isUnrepresentableScalar(text)) return text;
    if (std::regex_match(text, nullPattern())) return nullptr;
    if (std::regex_match(text, boolPattern())) {
        return text == "yes" || text == "Yes" || text == "YES" || text == "true" ||
               text == "True" || text == "TRUE" || text == "on" || text == "On" ||
               text == "ON";
    }
    if (std::regex_match(text, intPattern())) return parseYamlInt(text);
    if (std::regex_match(text, floatPattern())) return parseYamlFloat(text);
    return text;
}

// ---------------------------------------------------------------------------
// YAML -> JSON
// ---------------------------------------------------------------------------

SkillJson yamlToJson(const YAML::Node& node, const std::string& source,
                     const char* what, const char* docsUrl) {
    if (!node.IsDefined() || node.IsNull()) return nullptr;

    if (node.IsSequence()) {
        SkillJson out = SkillJson::array();
        for (const auto& item : node) out.push_back(yamlToJson(item, source, what, docsUrl));
        return out;
    }

    if (node.IsMap()) {
        SkillJson out = SkillJson::object();
        for (const auto& entry : node) {
            // yaml-cpp hands back an empty Scalar() for a null key, which would
            // silently become the "" key. Refuse instead of inventing one.
            if (!entry.first.IsScalar() || entry.first.Scalar().empty()) {
                throw SkillValidationError(
                    source + ": the " + what +
                    " uses an empty, null, or non-scalar YAML key. "
                    "Every key must be a non-empty plain string. See " +
                    docsUrl);
            }
            out[entry.first.Scalar()] = yamlToJson(entry.second, source, what, docsUrl);
        }
        return out;
    }

    if (node.IsScalar()) {
        const std::string tag = node.Tag();
        // yaml-cpp tags an explicitly quoted scalar "!" and a plain one "?".
        if (tag == "?") return resolvePlainScalar(node.Scalar());
        if (tag == "!" || tag == "tag:yaml.org,2002:str") return node.Scalar();
        if (tag.compare(0, 18, "tag:yaml.org,2002:") == 0) {
            return resolvePlainScalar(node.Scalar());
        }
        // A custom tag needs a constructor GAIA does not have; PyYAML refuses
        // these too, so refuse rather than silently dropping the tag on write.
        throw SkillValidationError(
            source + ": the " + what + " uses the unsupported YAML tag '" + tag +
            "'. It is plain YAML — remove the tag. See " + docsUrl);
    }

    return nullptr;
}

}  // namespace detail
}  // namespace gaia
