// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// C++ port of src/gaia/skills/format.py. See include/gaia/skill.h.

#include "gaia/skill.h"

#include <yaml-cpp/yaml.h>

#include <algorithm>
#include <cctype>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <map>
#include <regex>
#include <set>
#include <sstream>

namespace fs = std::filesystem;


namespace gaia {
namespace {

// ---------------------------------------------------------------------------
// Message helpers — the error wording is a cross-runtime contract, so these
// reproduce Python's repr()/type-name spelling rather than C++'s.
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
    return value.dump();
}

std::string pyRepr(const SkillJson& value) {
    if (value.is_string()) return pyRepr(value.get<std::string>());
    return pyStr(value);
}

/// Python truthiness — ``data.get(k) or default`` is used all over format.py.
bool isFalsy(const SkillJson& value) {
    switch (value.type()) {
        case SkillJson::value_t::null:            return true;
        case SkillJson::value_t::boolean:         return !value.get<bool>();
        case SkillJson::value_t::string:          return value.get<std::string>().empty();
        case SkillJson::value_t::array:
        case SkillJson::value_t::object:          return value.empty();
        case SkillJson::value_t::number_integer:  return value.get<long long>() == 0;
        case SkillJson::value_t::number_unsigned: return value.get<unsigned long long>() == 0;
        case SkillJson::value_t::number_float:    return value.get<double>() == 0.0;
        default:                             return false;
    }
}

const SkillJson& at(const SkillJson& obj, const char* key) {
    static const SkillJson kNull = nullptr;
    auto it = obj.find(key);
    return it == obj.end() ? kNull : *it;
}

std::string joinStrings(const std::vector<std::string>& items, const char* sep) {
    std::string out;
    for (size_t i = 0; i < items.size(); ++i) {
        if (i) out += sep;
        out += items[i];
    }
    return out;
}

/// Number of Unicode code points in a UTF-8 string — what Python's len() counts.
size_t codePointLength(const std::string& text) {
    size_t count = 0;
    for (char c : text) {
        if ((static_cast<unsigned char>(c) & 0xC0) != 0x80) ++count;
    }
    return count;
}

/// True when `text` is well-formed UTF-8. format.py fails loudly on a decode
/// error; without this the mojibake surfaces later as a confusing YAML error.
bool isValidUtf8(const std::string& text) {
    size_t i = 0;
    while (i < text.size()) {
        const unsigned char c = static_cast<unsigned char>(text[i]);
        size_t extra;
        unsigned int code;
        if (c < 0x80) { ++i; continue; }
        else if ((c & 0xE0) == 0xC0) { extra = 1; code = c & 0x1Fu; }
        else if ((c & 0xF0) == 0xE0) { extra = 2; code = c & 0x0Fu; }
        else if ((c & 0xF8) == 0xF0) { extra = 3; code = c & 0x07u; }
        else return false;

        if (i + extra >= text.size()) return false;
        for (size_t k = 1; k <= extra; ++k) {
            const unsigned char cont = static_cast<unsigned char>(text[i + k]);
            if ((cont & 0xC0) != 0x80) return false;
            code = (code << 6) | (cont & 0x3Fu);
        }
        if (extra == 1 && code < 0x80) return false;         // overlong
        if (extra == 2 && code < 0x800) return false;        // overlong
        if (extra == 3 && code < 0x10000) return false;      // overlong
        if (code > 0x10FFFF) return false;
        if (code >= 0xD800 && code <= 0xDFFF) return false;  // surrogate
        i += extra + 1;
    }
    return true;
}

/// Strip leading and trailing '\n' — Python's ``str.strip("\n")``.
std::string stripNewlines(const std::string& text) {
    size_t begin = text.find_first_not_of('\n');
    if (begin == std::string::npos) return "";
    size_t end = text.find_last_not_of('\n');
    return text.substr(begin, end - begin + 1);
}

std::string stripWhitespace(const std::string& text) {
    const char* ws = " \t\r\n\f\v";
    size_t begin = text.find_first_not_of(ws);
    if (begin == std::string::npos) return "";
    size_t end = text.find_last_not_of(ws);
    return text.substr(begin, end - begin + 1);
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

SkillJson yamlToJson(const YAML::Node& node, const std::string& source) {
    if (!node.IsDefined() || node.IsNull()) return nullptr;

    if (node.IsSequence()) {
        SkillJson out = SkillJson::array();
        for (const auto& item : node) out.push_back(yamlToJson(item, source));
        return out;
    }

    if (node.IsMap()) {
        SkillJson out = SkillJson::object();
        for (const auto& entry : node) {
            // yaml-cpp hands back an empty Scalar() for a null key, which would
            // silently become the "" key. Refuse instead of inventing one.
            if (!entry.first.IsScalar() || entry.first.Scalar().empty()) {
                throw SkillValidationError(
                    source +
                    ": the frontmatter uses an empty, null, or non-scalar YAML key. "
                    "Every key must be a non-empty plain string. See " +
                    FORMAT_DOCS_URL);
            }
            out[entry.first.Scalar()] = yamlToJson(entry.second, source);
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
            source + ": the frontmatter uses the unsupported YAML tag '" + tag +
            "'. Skill frontmatter is plain YAML — remove the tag. See " +
            FORMAT_DOCS_URL);
    }

    return nullptr;
}

// ---------------------------------------------------------------------------
// JSON -> YAML
//
// yaml-cpp parses; the emitter is hand-rolled because round-trip identity
// depends on quoting decisions (a string that would re-read as a number must
// be quoted, a float must keep its '.') that YAML::Emitter does not make.
// ---------------------------------------------------------------------------

bool needsQuoting(const std::string& text) {
    if (text.empty()) return true;
    // Would a plain scalar read back as something other than this string?
    const SkillJson resolved = resolvePlainScalar(text);
    if (!resolved.is_string() || resolved.get<std::string>() != text) return true;

    static const std::string kIndicators = "-?:,[]{}#&*!|>'\"%@`";
    if (kIndicators.find(text.front()) != std::string::npos) return true;
    if (std::isspace(static_cast<unsigned char>(text.front()))) return true;
    if (std::isspace(static_cast<unsigned char>(text.back()))) return true;

    for (size_t i = 0; i < text.size(); ++i) {
        const unsigned char c = static_cast<unsigned char>(text[i]);
        if (c < 0x20 || c == 0x7f) return true;
        if (c == ':' && (i + 1 == text.size() || text[i + 1] == ' ')) return true;
        if (c == '#' && i > 0 && text[i - 1] == ' ') return true;
    }
    return false;
}

std::string doubleQuote(const std::string& text) {
    std::string out = "\"";
    char buf[8];
    for (char raw : text) {
        const unsigned char c = static_cast<unsigned char>(raw);
        switch (c) {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
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
    out += "\"";
    return out;
}

std::string formatDouble(double value) {
    // Shortest representation that reads back bit-identically.
    std::string text;
    for (int precision = 15; precision <= 17; ++precision) {
        std::ostringstream os;
        os.precision(precision);
        os << value;
        text = os.str();
        if (std::strtod(text.c_str(), nullptr) == value) break;
    }
    // Keep it a float on re-read: "1" would come back as an int.
    if (text.find_first_of(".eEn") == std::string::npos) text += ".0";
    return text;
}

std::string emitScalar(const SkillJson& value) {
    if (value.is_null()) return "null";
    if (value.is_boolean()) return value.get<bool>() ? "true" : "false";
    if (value.is_number_unsigned())
        return std::to_string(value.get<unsigned long long>());
    if (value.is_number_integer()) return std::to_string(value.get<long long>());
    if (value.is_number_float()) return formatDouble(value.get<double>());

    const std::string text = value.get<std::string>();
    return needsQuoting(text) ? doubleQuote(text) : text;
}

bool isInlineValue(const SkillJson& value) {
    if (value.is_object() || value.is_array()) return value.empty();
    return true;
}

std::string inlineValue(const SkillJson& value) {
    if (value.is_object()) return "{}";
    if (value.is_array()) return "[]";
    return emitScalar(value);
}

void emitMapping(std::string& out, const SkillJson& obj, size_t indent,
                 bool skipFirstIndent);
void emitSequence(std::string& out, const SkillJson& arr, size_t indent);

void emitAfterKey(std::string& out, const SkillJson& value, size_t indent) {
    if (isInlineValue(value)) {
        out += " " + inlineValue(value) + "\n";
    } else if (value.is_object()) {
        out += "\n";
        emitMapping(out, value, indent + 2, false);
    } else {
        out += "\n";
        emitSequence(out, value, indent + 2);
    }
}

void emitMapping(std::string& out, const SkillJson& obj, size_t indent,
                 bool skipFirstIndent) {
    bool first = true;
    for (const auto& entry : obj.items()) {
        if (!(first && skipFirstIndent)) out += std::string(indent, ' ');
        first = false;
        const std::string key = entry.key();
        out += needsQuoting(key) ? doubleQuote(key) : key;
        out += ":";
        emitAfterKey(out, entry.value(), indent);
    }
}

void emitSequence(std::string& out, const SkillJson& arr, size_t indent) {
    for (const auto& item : arr) {
        out += std::string(indent, ' ');
        if (isInlineValue(item)) {
            out += "- " + inlineValue(item) + "\n";
        } else if (item.is_object()) {
            out += "- ";
            emitMapping(out, item, indent + 2, true);
        } else {
            out += "-\n";
            emitSequence(out, item, indent + 2);
        }
    }
}

std::string emitYaml(const SkillJson& obj) {
    std::string out;
    emitMapping(out, obj, 0, false);
    return out;
}

// ---------------------------------------------------------------------------
// Permission grammar
//
// validate_skill() validates permissions by parsing them, so the grammar has
// to live here too. The Permission type, the connector bridge, and the
// local-capability refusal rule belong to skill_permissions.h (issue #2799);
// this is the schema-level grammar check only.
// ---------------------------------------------------------------------------

const std::map<std::string, std::set<std::string>>& domainLevels() {
    static const std::map<std::string, std::set<std::string>> kLevels = {
        {"database",   {"read", "write", "none"}},
        {"desktop",    {"control", "none"}},
        {"env",        {"read", "none"}},
        {"filesystem", {"read", "write", "none"}},
        {"mcp",        {"connect", "none"}},
        {"network",    {"read", "write", "none"}},
        {"shell",      {"execute", "none"}},
    };
    return kLevels;
}

std::string sortedDomains() {
    std::vector<std::string> names;
    for (const auto& entry : domainLevels()) names.push_back(entry.first);
    return joinStrings(names, ", ");
}

std::string sortedLevels(const std::string& domain) {
    const auto& levels = domainLevels().at(domain);
    return joinStrings(std::vector<std::string>(levels.begin(), levels.end()), ", ");
}

void validatePermission(const std::string& raw, const std::string& skillName) {
    const std::string trimmed = stripWhitespace(raw);
    if (trimmed.empty()) {
        throw SkillValidationError(
            "Skill '" + skillName +
            "' declares an empty permission. Each entry of "
            "metadata.gaia.permissions must be a '<domain>:<level>[:scope]' string, "
            "e.g. 'network:read:*.brave.com'. Grammar: " + FORMAT_DOCS_URL +
            "#permission-model");
    }

    const size_t firstColon = trimmed.find(':');
    if (firstColon == std::string::npos) {
        throw SkillValidationError(
            "Skill '" + skillName + "' declares permission " + pyRepr(raw) +
            ", which is missing its level. Use '<domain>:<level>[:scope]', e.g. "
            "'network:read' or 'mcp:connect:mcp-tavily'. Grammar: " + FORMAT_DOCS_URL +
            "#permission-model");
    }
    const size_t secondColon = trimmed.find(':', firstColon + 1);

    const std::string domain = stripWhitespace(trimmed.substr(0, firstColon));
    const std::string level = stripWhitespace(
        trimmed.substr(firstColon + 1, secondColon == std::string::npos
                                           ? std::string::npos
                                           : secondColon - firstColon - 1));

    if (domainLevels().find(domain) == domainLevels().end()) {
        throw SkillValidationError(
            "Skill '" + skillName + "' declares permission " + pyRepr(raw) +
            " with unknown domain " + pyRepr(domain) + ". Valid domains: " +
            sortedDomains() + ". Grammar: " + FORMAT_DOCS_URL + "#permission-model");
    }

    static const std::regex kToken(R"(^[a-z][a-z0-9_]*$)");
    if (!std::regex_match(level, kToken) ||
        domainLevels().at(domain).count(level) == 0) {
        throw SkillValidationError(
            "Skill '" + skillName + "' declares permission " + pyRepr(raw) +
            " with level " + pyRepr(level) + ", which " + pyRepr(domain) +
            " does not define. Valid levels for " + pyRepr(domain) + ": " +
            sortedLevels(domain) + ". Grammar: " + FORMAT_DOCS_URL +
            "#permission-model");
    }
}

// ---------------------------------------------------------------------------
// Frontmatter split — the hand-written equivalent of format.py's
// _FRONTMATTER_RE. Hand-written because std::regex backtracks catastrophically
// on a lazy [\s\S]*? across a multi-kilobyte skill body.
// ---------------------------------------------------------------------------

/// Strip any leading UTF-8 BOM(s), matching Python's ``text.lstrip("﻿")``.
std::string stripBom(const std::string& text) {
    size_t offset = 0;
    while (text.size() - offset >= 3 && text.compare(offset, 3, "\xEF\xBB\xBF") == 0) {
        offset += 3;
    }
    return offset ? text.substr(offset) : text;
}

bool splitFrontmatter(const std::string& text, std::string& yamlOut,
                      std::string& bodyOut) {
    const size_t n = text.size();
    if (n < 3 || text.compare(0, 3, "---") != 0) return false;

    size_t i = 3;
    while (i < n && (text[i] == ' ' || text[i] == '\t')) ++i;
    if (i < n && text[i] == '\r') ++i;
    if (i >= n || text[i] != '\n') return false;
    const size_t yamlStart = ++i;

    for (size_t p = yamlStart; p < n; ++p) {
        size_t q;
        if (text[p] == '\r' && p + 1 < n && text[p + 1] == '\n') {
            q = p + 2;
        } else if (text[p] == '\n') {
            q = p + 1;
        } else {
            continue;
        }
        if (n - q < 3 || text.compare(q, 3, "---") != 0) continue;

        size_t r = q + 3;
        while (r < n && (text[r] == ' ' || text[r] == '\t')) ++r;

        size_t bodyStart;
        if (r >= n) {
            bodyStart = n;
        } else if (text[r] == '\r' && r + 1 < n && text[r + 1] == '\n') {
            bodyStart = r + 2;
        } else if (text[r] == '\n') {
            bodyStart = r + 1;
        } else {
            continue;
        }

        yamlOut = text.substr(yamlStart, p - yamlStart);
        bodyOut = text.substr(bodyStart);
        return true;
    }
    return false;
}

std::vector<std::string> toStringList(const SkillJson& value) {
    std::vector<std::string> out;
    if (!value.is_array()) return out;
    for (const auto& item : value) out.push_back(pyStr(item));
    return out;
}

}  // namespace

// ---------------------------------------------------------------------------
// SkillRequirements
// ---------------------------------------------------------------------------

bool SkillRequirements::isEmpty() const { return *this == SkillRequirements(); }

bool SkillRequirements::operator==(const SkillRequirements& other) const {
    return model == other.model && context == other.context && python == other.python &&
           dependencies == other.dependencies &&
           nodeDependencies == other.nodeDependencies && envVars == other.envVars &&
           hardware == other.hardware && extra == other.extra;
}

SkillJson SkillRequirements::toJson() const {
    SkillJson out = SkillJson::object();
    if (model) out["model"] = *model;
    if (context) out["context"] = *context;
    if (python) out["python"] = *python;
    if (!dependencies.empty()) out["dependencies"] = dependencies;
    if (!nodeDependencies.empty()) out["node_dependencies"] = nodeDependencies;
    if (!envVars.empty()) out["env_vars"] = envVars;
    if (!hardware.empty()) out["hardware"] = hardware;
    for (const auto& entry : extra.items()) out[entry.key()] = entry.value();
    return out;
}

SkillRequirements SkillRequirements::fromJson(const SkillJson& data,
                                              const std::string& skillName) {
    if (data.is_null()) return SkillRequirements();
    if (!data.is_object()) {
        throw SkillValidationError(
            "Skill '" + skillName + "'"
            ": metadata.gaia.requirements must be a mapping, got " + pyTypeName(data) +
            ". Example: 'requirements: {python: \">=3.10\"}'. See " + FORMAT_DOCS_URL +
            "#the-metadatagaia-namespace");
    }

    for (const char* key : {"dependencies", "node_dependencies", "env_vars"}) {
        const SkillJson& value = at(data, key);
        if (!value.is_null() && !value.is_array()) {
            throw SkillValidationError(
                "Skill '" + skillName + "': metadata.gaia.requirements." + key +
                " must be a list, got " + pyTypeName(value) + ". See " +
                FORMAT_DOCS_URL + "#the-metadatagaia-namespace");
        }
    }

    const SkillJson& rawHardware = at(data, "hardware");
    const SkillJson hardware = isFalsy(rawHardware) ? SkillJson::object() : rawHardware;
    if (!hardware.is_object()) {
        throw SkillValidationError(
            "Skill '" + skillName + "'"
            ": metadata.gaia.requirements.hardware must be a mapping, got " +
            pyTypeName(hardware) + ". See " + FORMAT_DOCS_URL +
            "#the-metadatagaia-namespace");
    }

    SkillRequirements out;
    if (!at(data, "model").is_null()) out.model = pyStr(at(data, "model"));
    if (!at(data, "context").is_null()) out.context = pyStr(at(data, "context"));
    if (!at(data, "python").is_null()) out.python = pyStr(at(data, "python"));
    out.dependencies = toStringList(at(data, "dependencies"));
    out.nodeDependencies = toStringList(at(data, "node_dependencies"));
    out.envVars = toStringList(at(data, "env_vars"));
    out.hardware = hardware;

    static const std::set<std::string> kKnown = {
        "model", "context", "python", "dependencies", "node_dependencies",
        "env_vars", "hardware"};
    for (const auto& entry : data.items()) {
        if (kKnown.count(entry.key()) == 0) out.extra[entry.key()] = entry.value();
    }
    return out;
}

// ---------------------------------------------------------------------------
// SkillTool
// ---------------------------------------------------------------------------

bool SkillTool::operator==(const SkillTool& other) const {
    return name == other.name && description == other.description &&
           parameters == other.parameters && returns == other.returns &&
           atomic == other.atomic;
}

SkillJson SkillTool::toJson() const {
    SkillJson out = SkillJson::object();
    out["name"] = name;
    if (!description.empty()) out["description"] = description;
    out["parameters"] = parameters;
    if (returns) out["returns"] = *returns;
    if (atomic) out["atomic"] = atomic;
    return out;
}

SkillTool SkillTool::fromJson(const SkillJson& data, const std::string& skillName) {
    if (!data.is_object()) {
        throw SkillValidationError(
            "Skill '" + skillName + "'"
            ": each metadata.gaia.tools entry must be a mapping with a 'name', got " +
            pyTypeName(data) + ". See " + FORMAT_DOCS_URL +
            "#the-metadatagaia-namespace");
    }

    const SkillJson& rawName = at(data, "name");
    if (!rawName.is_string() || rawName.get<std::string>().empty()) {
        throw SkillValidationError(
            "Skill '" + skillName + "'"
            ": a metadata.gaia.tools entry is missing its 'name'. Every declared tool "
            "must name the @tool function in " + SKILL_TOOLS_FILENAME +
            " that implements it. See " + FORMAT_DOCS_URL + "#tool-registration");
    }
    const std::string name = rawName.get<std::string>();

    const SkillJson& raw = at(data, "parameters");
    const SkillJson rawParams = isFalsy(raw) ? SkillJson::object() : raw;
    if (!rawParams.is_object()) {
        throw SkillValidationError(
            "Skill '" + skillName + "': tool '" + name + "'"
            " has 'parameters' of type " + pyTypeName(rawParams) +
            "; it must be a mapping of parameter name to {type, required, default}. "
            "See " + std::string(FORMAT_DOCS_URL) + "#the-metadatagaia-namespace");
    }
    for (const auto& entry : rawParams.items()) {
        if (!entry.value().is_object()) {
            throw SkillValidationError(
                "Skill '" + skillName + "': tool '" + name + "'"
                " parameter '" + entry.key() + "'"
                " must be a mapping like '{type: string, required: true}', got " +
                pyTypeName(entry.value()) + ". See " + FORMAT_DOCS_URL +
                "#the-metadatagaia-namespace");
        }
    }

    const SkillJson& returns = at(data, "returns");
    if (!returns.is_null() && !returns.is_object()) {
        throw SkillValidationError(
            "Skill '" + skillName + "': tool '" + name + "'"
            " has 'returns' of type " + pyTypeName(returns) +
            "; it must be a mapping like '{type: object}'. See " + FORMAT_DOCS_URL +
            "#the-metadatagaia-namespace");
    }

    SkillTool out;
    out.name = name;
    const SkillJson& rawDescription = at(data, "description");
    out.description = isFalsy(rawDescription) ? "" : pyStr(rawDescription);
    out.parameters = rawParams;
    if (!returns.is_null()) out.returns = returns;
    out.atomic = !isFalsy(at(data, "atomic"));
    return out;
}

// ---------------------------------------------------------------------------
// GaiaMetadata
// ---------------------------------------------------------------------------

bool GaiaMetadata::isDefault() const { return *this == GaiaMetadata(); }

bool GaiaMetadata::operator==(const GaiaMetadata& other) const {
    return securityTier == other.securityTier && permissions == other.permissions &&
           requirements == other.requirements && tools == other.tools &&
           toolsRequired == other.toolsRequired && extra == other.extra;
}

SkillJson GaiaMetadata::toJson() const {
    SkillJson out = SkillJson::object();
    if (securityTier != DEFAULT_SECURITY_TIER) out["security_tier"] = securityTier;
    if (!permissions.empty()) out["permissions"] = permissions;
    if (!requirements.isEmpty()) out["requirements"] = requirements.toJson();
    if (!tools.empty()) {
        SkillJson entries = SkillJson::array();
        for (const auto& tool : tools) entries.push_back(tool.toJson());
        out["tools"] = entries;
    }
    if (!toolsRequired.empty()) out["tools_required"] = toolsRequired;
    for (const auto& entry : extra.items()) out[entry.key()] = entry.value();
    return out;
}

GaiaMetadata GaiaMetadata::fromJson(const SkillJson& data, const std::string& skillName) {
    if (data.is_null()) return GaiaMetadata();
    if (!data.is_object()) {
        throw SkillValidationError(
            "Skill '" + skillName + "': metadata.gaia must be a mapping, got " +
            pyTypeName(data) +
            ". Omit it entirely for an instruction-only skill. See " + FORMAT_DOCS_URL +
            "#the-metadatagaia-namespace");
    }

    const SkillJson& rawTier = at(data, "security_tier");
    std::string tier = DEFAULT_SECURITY_TIER;
    if (data.find("security_tier") != data.end()) {
        const bool known =
            rawTier.is_string() &&
            std::find(std::begin(SECURITY_TIERS), std::end(SECURITY_TIERS),
                      rawTier.get<std::string>()) != std::end(SECURITY_TIERS);
        if (!known) {
            throw SkillValidationError(
                "Skill '" + skillName + "': metadata.gaia.security_tier is " +
                pyRepr(rawTier) + ", which is not one of " +
                joinStrings(std::vector<std::string>(std::begin(SECURITY_TIERS),
                                                std::end(SECURITY_TIERS)),
                            ", ") +
                ". Omit the field to take the safe default ('" +
                DEFAULT_SECURITY_TIER + "'). See " + FORMAT_DOCS_URL +
                "#security-tiers");
        }
        tier = rawTier.get<std::string>();
    }

    const SkillJson& rawPermissions = at(data, "permissions");
    const SkillJson permissions = isFalsy(rawPermissions) ? SkillJson::array() : rawPermissions;
    if (!permissions.is_array()) {
        throw SkillValidationError(
            "Skill '" + skillName + "'"
            ": metadata.gaia.permissions must be a list of "
            "'<domain>:<level>[:scope]' strings, got " + pyTypeName(permissions) +
            ". See " + FORMAT_DOCS_URL + "#permission-model");
    }

    const SkillJson& rawTools = at(data, "tools");
    const SkillJson tools = isFalsy(rawTools) ? SkillJson::array() : rawTools;
    if (!tools.is_array()) {
        throw SkillValidationError(
            "Skill '" + skillName + "': metadata.gaia.tools must be a list, got " +
            pyTypeName(tools) +
            ". Each entry declares one @tool function this skill provides. See " +
            FORMAT_DOCS_URL + "#tools-vs-tools_required");
    }

    const SkillJson& rawRequired = at(data, "tools_required");
    const SkillJson toolsRequired = isFalsy(rawRequired) ? SkillJson::array() : rawRequired;
    if (!toolsRequired.is_array()) {
        throw SkillValidationError(
            "Skill '" + skillName + "'"
            ": metadata.gaia.tools_required must be a list of registry tool names, got " +
            pyTypeName(toolsRequired) + ". See " + FORMAT_DOCS_URL +
            "#tools-vs-tools_required");
    }

    GaiaMetadata out;
    out.securityTier = tier;
    out.permissions = toStringList(permissions);
    out.requirements = SkillRequirements::fromJson(at(data, "requirements"), skillName);
    for (const auto& entry : tools) out.tools.push_back(SkillTool::fromJson(entry, skillName));
    out.toolsRequired = toStringList(toolsRequired);

    static const std::set<std::string> kKnown = {
        "security_tier", "permissions", "requirements", "tools", "tools_required"};
    for (const auto& entry : data.items()) {
        if (kKnown.count(entry.key()) == 0) out.extra[entry.key()] = entry.value();
    }
    return out;
}

// ---------------------------------------------------------------------------
// Skill
// ---------------------------------------------------------------------------

bool Skill::operator==(const Skill& other) const {
    return name == other.name && description == other.description &&
           body == other.body && license == other.license && version == other.version &&
           gaia == other.gaia && otherMetadata == other.otherMetadata &&
           extraFields == other.extraFields;
}

std::string Skill::directory() const {
    return path.empty() ? std::string() : fs::path(path).parent_path().string();
}

std::vector<std::string> Skill::toolNames() const {
    std::vector<std::string> names;
    names.reserve(gaia.tools.size());
    for (const auto& tool : gaia.tools) names.push_back(tool.name);
    return names;
}

std::string Skill::namespacedToolName(const std::string& toolName) const {
    return name + "/" + toolName;
}

std::string Skill::toolsPath() const {
    const std::string dir = directory();
    return dir.empty() ? std::string()
                       : (fs::path(dir) / SKILL_TOOLS_FILENAME).string();
}

SkillJson Skill::toFrontmatter() const {
    SkillJson out = SkillJson::object();
    out["name"] = name;
    out["description"] = description;
    if (license) out["license"] = *license;
    if (version) out["version"] = *version;

    SkillJson metadata = SkillJson::object();
    const SkillJson gaiaBlock = gaia.toJson();
    if (!gaiaBlock.empty() || !gaia.isDefault()) metadata["gaia"] = gaiaBlock;
    for (const auto& entry : otherMetadata.items()) metadata[entry.key()] = entry.value();
    if (!metadata.empty()) out["metadata"] = metadata;

    for (const auto& entry : extraFields.items()) out[entry.key()] = entry.value();
    return out;
}

std::string toMarkdown(const Skill& skill) {
    const std::string frontmatter = emitYaml(skill.toFrontmatter());
    const std::string body = stripNewlines(skill.body);
    if (body.empty()) return "---\n" + frontmatter + "---\n";
    return "---\n" + frontmatter + "---\n\n" + body + "\n";
}

// ---------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------

Skill parseSkill(const std::string& text, const std::string& source) {
    std::string rawYaml;
    std::string body;
    if (!splitFrontmatter(stripBom(text), rawYaml, body)) {
        throw SkillValidationError(
            source +
            ": no YAML frontmatter found. A SKILL.md must open with a '---' line, the "
            "YAML fields, and a closing '---' line, e.g.:\n"
            "  ---\n  name: my-skill\n  description: What it does and when to use it.\n"
            "  ---\nSee " + FORMAT_DOCS_URL + "#adopted-base-agent-skills-standard");
    }

    YAML::Node root;
    try {
        root = YAML::Load(rawYaml);
    } catch (const YAML::Exception& exc) {
        throw SkillValidationError(
            source + ": the YAML frontmatter is invalid: " + exc.what() +
            ". Fix the YAML syntax (watch for tabs and unquoted ':'). See " +
            FORMAT_DOCS_URL);
    }

    SkillJson frontmatter = yamlToJson(root, source);
    if (frontmatter.is_null()) frontmatter = SkillJson::object();
    if (!frontmatter.is_object()) {
        throw SkillValidationError(
            source + ": the frontmatter must be a YAML mapping of fields, got " +
            pyTypeName(frontmatter) + ". See " + FORMAT_DOCS_URL);
    }

    const SkillJson& rawName = at(frontmatter, "name");
    if (!rawName.is_string() || rawName.get<std::string>().empty()) {
        throw SkillValidationError(
            source +
            ": required field 'name' is missing or not a string. Add "
            "'name: <skill-directory-name>' to the frontmatter. See " + FORMAT_DOCS_URL +
            "#naming");
    }

    const SkillJson& rawDescription = at(frontmatter, "description");
    if (!rawDescription.is_string() || rawDescription.get<std::string>().empty()) {
        throw SkillValidationError(
            source +
            ": required field 'description' is missing or not a string. It is the "
            "trigger signal the model reads to decide relevance — say what the skill "
            "does and when to use it. See " + FORMAT_DOCS_URL +
            "#adopted-base-agent-skills-standard");
    }

    for (const char* key : {"license", "version"}) {
        const SkillJson& value = at(frontmatter, key);
        if (!value.is_null() && !value.is_string()) {
            throw SkillValidationError(
                source + ": field '" + key + "' must be a string, got " +
                pyTypeName(value) +
                ". Quote it if it looks numeric (e.g. version: \"1.0.0\"). See " +
                FORMAT_DOCS_URL);
        }
    }

    const SkillJson& raw = at(frontmatter, "metadata");
    const SkillJson rawMetadata = isFalsy(raw) ? SkillJson::object() : raw;
    if (!rawMetadata.is_object()) {
        throw SkillValidationError(
            source +
            ": field 'metadata' must be a mapping of vendor namespaces (e.g. "
            "'metadata: {gaia: {...}}'), got " + pyTypeName(rawMetadata) + ". See " +
            FORMAT_DOCS_URL + "#the-metadatagaia-namespace");
    }

    Skill skill;
    skill.name = rawName.get<std::string>();
    skill.description = rawDescription.get<std::string>();
    skill.body = stripNewlines(body);
    if (!at(frontmatter, "license").is_null())
        skill.license = at(frontmatter, "license").get<std::string>();
    if (!at(frontmatter, "version").is_null())
        skill.version = at(frontmatter, "version").get<std::string>();
    skill.gaia = GaiaMetadata::fromJson(at(rawMetadata, "gaia"), skill.name);

    for (const auto& entry : rawMetadata.items()) {
        if (entry.key() != "gaia") skill.otherMetadata[entry.key()] = entry.value();
    }
    // `compatibility` / `allowed-tools` / `disallowed-tools` land in extraFields
    // like any other unmodelled key: preserved for round-trip, never consulted.
    static const std::set<std::string> kKnownTopLevel = {
        "name", "description", "license", "version", "metadata"};
    for (const auto& entry : frontmatter.items()) {
        if (kKnownTopLevel.count(entry.key()) == 0)
            skill.extraFields[entry.key()] = entry.value();
    }

    validateSkill(skill, source);
    return skill;
}

namespace {

std::string resolveSkillFile(const std::string& path) {
    std::error_code ec;
    if (fs::is_directory(path, ec)) return (fs::path(path) / SKILL_FILENAME).string();
    return path;
}

}  // namespace

Skill parseSkillFile(const std::string& path, const std::string& root, bool readOnly,
                     bool checkDirectoryName) {
    const std::string skillFile = resolveSkillFile(path);

    std::error_code ec;
    if (!fs::is_regular_file(skillFile, ec)) {
        throw SkillValidationError(
            "No " + std::string(SKILL_FILENAME) + " at " + skillFile +
            ". A skill is a directory whose only required file is " + SKILL_FILENAME +
            ". Create one with 'gaia skill create <name>'. See " + FORMAT_DOCS_URL);
    }

    // fs::path (not std::string) so MSVC routes through _wfopen — the narrow
    // overload uses the ANSI code page and fails on a non-ASCII profile path.
    std::ifstream stream(fs::path(skillFile), std::ios::binary);
    if (!stream) {
        throw SkillValidationError(
            "Could not open " + skillFile +
            ". Check the file's permissions and that the path is readable.");
    }
    std::ostringstream buffer;
    buffer << stream.rdbuf();
    const std::string text = buffer.str();
    if (!isValidUtf8(text)) {
        throw SkillValidationError(
            "Could not read " + skillFile +
            ": the file is not valid UTF-8. Re-save it as UTF-8 (SKILL.md is "
            "always UTF-8). See " + FORMAT_DOCS_URL);
    }

    Skill skill = parseSkill(text, skillFile);
    skill.path = skillFile;
    skill.root = root;
    skill.readOnly = readOnly;

    if (checkDirectoryName) {
        const std::string directoryName = fs::path(skillFile).parent_path().filename().string();
        if (skill.name != directoryName) {
            throw SkillValidationError(
                skillFile + ": frontmatter says name: " + pyRepr(skill.name) +
                " but the directory is named " + pyRepr(directoryName) +
                ". The two must match — rename the directory to '" + skill.name +
                "' or change the frontmatter to 'name: " + directoryName + "'. See " +
                FORMAT_DOCS_URL + "#naming");
        }
    }
    return skill;
}

Skill parseSkillMetadata(const std::string& path, const std::string& root,
                         bool readOnly) {
    Skill skill = parseSkillFile(resolveSkillFile(path), root, readOnly);
    skill.body.clear();
    return skill;
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

void validateSkill(const Skill& skill, const std::string& source) {
    if (codePointLength(skill.name) > MAX_NAME_LENGTH) {
        throw SkillValidationError(
            source + ": name " + pyRepr(skill.name) + " is " +
            std::to_string(codePointLength(skill.name)) + " characters; the limit is " +
            std::to_string(MAX_NAME_LENGTH) +
            ". Shorten it (and its directory name to match). See " + FORMAT_DOCS_URL +
            "#naming");
    }

    static const std::regex kNamePattern(R"(^[a-z0-9]+(-[a-z0-9]+)*$)");
    if (!std::regex_match(skill.name, kNamePattern)) {
        throw SkillValidationError(
            source + ": name " + pyRepr(skill.name) +
            " is not a valid skill name. Use lowercase letters and digits separated by "
            "single hyphens (e.g. 'web-research') — no uppercase, underscores, spaces, "
            "or leading/trailing/consecutive hyphens. See " + FORMAT_DOCS_URL +
            "#naming");
    }

    if (codePointLength(skill.description) > MAX_DESCRIPTION_LENGTH) {
        throw SkillValidationError(
            source + ": description is " + std::to_string(codePointLength(skill.description)) +
            " characters; the limit is " + std::to_string(MAX_DESCRIPTION_LENGTH) +
            ". It is a trigger signal, not documentation — move the detail into the "
            "Markdown body. See " + FORMAT_DOCS_URL +
            "#adopted-base-agent-skills-standard");
    }

    // Official SemVer 2.0.0 pattern (semver.org). '0.0.0' passes and is the
    // reserved way to say "unversioned" when a version must be present.
    static const std::regex kSemver(
        R"(^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*))"
        R"((?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))"
        R"((?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?)"
        R"((?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$)");
    if (skill.version && !std::regex_match(*skill.version, kSemver)) {
        throw SkillValidationError(
            source + ": version " + pyRepr(*skill.version) +
            " is not valid SemVer. Use MAJOR.MINOR.PATCH (e.g. '1.0.0'); omit the "
            "field if the skill is unversioned. See " + FORMAT_DOCS_URL +
            "#field-reference");
    }

    // Parsing the permission strings is the validation — bad grammar throws here.
    for (const auto& permission : skill.gaia.permissions) {
        validatePermission(permission, skill.name);
    }

    std::set<std::string> seen;
    std::set<std::string> duplicates;
    for (const auto& tool : skill.gaia.tools) {
        if (!seen.insert(tool.name).second) duplicates.insert(tool.name);
    }
    if (!duplicates.empty()) {
        throw SkillValidationError(
            source + ": metadata.gaia.tools declares " +
            joinStrings(std::vector<std::string>(duplicates.begin(), duplicates.end()),
                        ", ") +
            " more than once. Each tool name must be unique within a skill. See " +
            FORMAT_DOCS_URL + "#tool-registration");
    }
}

}  // namespace gaia
