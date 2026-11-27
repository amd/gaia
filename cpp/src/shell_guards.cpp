// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/shell_guards.h"

#include <algorithm>
#include <cctype>
#include <cstring>
#include <fstream>
#include <regex>
#include <set>

namespace gaia::shellguards {

namespace fs = std::filesystem;

namespace {
bool skippedDir(const std::string& name) {
    return name.empty() || name[0] == '.' || name == "node_modules" || name == "__pycache__";
}

// The largest cut at or before `limit` that does not split a UTF-8 character.
size_t utf8Cut(const std::string& text, size_t limit) {
    if (limit >= text.size()) return text.size();
    while (limit > 0 && (static_cast<unsigned char>(text[limit]) & 0xC0) == 0x80) --limit;
    return limit;
}

std::string lowered(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}
}  // namespace

std::string oneLineBlockRefusal(const std::string& command) {
    static const std::regex inline_(R"(\bpython[\d.]*(?:\.exe)?\s+-c\s+(["']))", std::regex::icase);
    std::smatch m;
    if (!std::regex_search(command, m, inline_)) return "";
    const std::string script = m.suffix().str();
    if (script.find('\n') != std::string::npos) return "";
    static const std::regex block(R"(;\s*(try|for|while|if|with|def|class)\b[^;]*:)");
    std::smatch b;
    if (!std::regex_search(script, b, block)) return "";
    return "Not run: this is a Python syntax error. A '" + b[1].str() +
           "' block cannot follow a ';' on one line. Write the script on several lines, or drop the '" + b[1].str() +
           "' and let any error show.";
}

std::string keepScriptDollars(const std::string& command, const std::string& earlier) {
    static const std::string interpreter = R"((?:^|[\s;&|(])(?:python3?|py|node|perl|ruby)(?:\.exe)?\s)";
    static const std::regex inlineScript(interpreter + R"((?:[^|;&\n"]*?\s)?(?:-[A-Za-z]*[cep]|--eval)\s*")");
    static const std::regex hereScript(interpreter + R"([^|;&\n]*?<<(-?)\s*([A-Za-z_]\w*)[^\n]*\n)");

    // The spans of a command the shell expands as script text.
    const auto scriptSpans = [](const std::string& command) {
        std::vector<std::pair<size_t, size_t>> scripts;
        for (auto it = std::sregex_iterator(command.begin(), command.end(), inlineScript);
             it != std::sregex_iterator(); ++it) {
            const size_t from = static_cast<size_t>(it->position(0) + it->length(0));
            size_t to = from;
            while (to < command.size() && command[to] != '"') to += command[to] == '\\' ? 2 : 1;
            scripts.emplace_back(from, std::min(to, command.size()));
        }
        for (auto it = std::sregex_iterator(command.begin(), command.end(), hereScript);
             it != std::sregex_iterator(); ++it) {
            const size_t from = static_cast<size_t>(it->position(0) + it->length(0));
            // The closing line is the delimiter alone; <<- lets tabs come before it.
            const std::string delimiter = (*it)[2].str();
            size_t close = std::string::npos;
            for (size_t line = from; line < command.size();) {
                size_t text = line;
                if ((*it)[1].length()) {
                    while (text < command.size() && command[text] == '\t') ++text;
                }
                const size_t eol = std::min(command.find('\n', text), command.size());
                if (command.compare(text, eol - text, delimiter) == 0) {
                    close = line;
                    break;
                }
                line = eol + 1;
            }
            scripts.emplace_back(from, close == std::string::npos ? command.size() : close);
        }
        return scripts;
    };
    const auto shellOnly = [&scriptSpans](std::string text) {
        for (const auto& span : scriptSpans(text)) {
            std::fill(text.begin() + static_cast<std::ptrdiff_t>(span.first),
                      text.begin() + static_cast<std::ptrdiff_t>(span.second), ' ');
        }
        return text;
    };
    const auto scripts = scriptSpans(command);
    if (scripts.empty()) return command;

    // Shell text only: an assignment inside a script, this one or an earlier one, is not the shell's.
    const std::string shellText = shellOnly(command) + "\n" + shellOnly(earlier);

    std::vector<size_t> blanked;  // each $ the shell would turn into nothing
    for (const auto& span : scripts) {
        for (size_t i = span.first; i + 1 < span.second; ++i) {
            if (command[i] == '\\') { ++i; continue; }
            if (command[i] != '$') continue;
            size_t at = i + 1;
            const bool braced = command[at] == '{';
            if (braced) ++at;
            size_t end = at;
            while (end < span.second &&
                   (std::isalnum(static_cast<unsigned char>(command[end])) || command[end] == '_')) ++end;
            const std::string name = command.substr(at, end - at);
            if (name.empty() || std::isdigit(static_cast<unsigned char>(name[0]))) continue;
            // HOME, PATH and the like are the shell's to fill in, and so is a variable the turn set.
            const bool envStyle = std::all_of(name.begin(), name.end(), [](unsigned char c) {
                return std::isupper(c) || std::isdigit(c) || c == '_';
            });
            const bool plainBrace = !braced || (end < command.size() && command[end] == '}');
            if (envStyle && plainBrace) continue;
            // ${name:-default}, ${name%suffix} and the like are shell syntax, not a format.
            if (braced && end < command.size()) {
                const char op = command[end] == ':' && end + 1 < command.size() ? command[end + 1] : command[end];
                if (std::strchr("-=+?%#/", op)) continue;
            }
            const std::regex sets("(^|[\\s;&|(])(?:(?:export\\s+|local\\s+)?" + name + "=|for\\s+" + name +
                                  "\\s|read\\s+(?:-\\w+\\s+)*(?:\\w+\\s+)*" + name + "\\b)");
            if (std::regex_search(shellText, sets)) continue;
            blanked.push_back(i);
            i = end ? end - 1 : i;
        }
    }
    std::sort(blanked.begin(), blanked.end());
    blanked.erase(std::unique(blanked.begin(), blanked.end()), blanked.end());
    std::string fixed = command;
    for (auto at = blanked.rbegin(); at != blanked.rend(); ++at) fixed.insert(*at, 1, '\\');
    return fixed;
}

std::vector<std::string> hostileNames(const std::vector<std::string>& roots) {
    static const std::string shellSyntax = ";&|`$<>()\n";
    std::vector<std::string> names;
    std::error_code ec;
    for (const auto& r : roots) {
        fs::recursive_directory_iterator it(fs::u8path(r), fs::directory_options::skip_permission_denied, ec), end;
        for (int budget = 2000; it != end && !ec && budget-- > 0; it.increment(ec)) {
            const std::string name = it->path().filename().u8string();
            if (it->is_directory(ec) && (it.depth() >= 1 || skippedDir(name))) it.disable_recursion_pending();
            if (name.find_first_of(shellSyntax) != std::string::npos) names.push_back(name);
        }
    }
    return names;
}

std::string unquotedHostileName(const std::string& command, const std::vector<std::string>& names) {
    for (const auto& name : names) {
        for (size_t at = command.find(name); at != std::string::npos; at = command.find(name, at + 1)) {
            // Inside a quoted string the shell reads it as text, wherever the name sits in it.
            char quote = 0;
            for (size_t i = 0; i < at; ++i) {
                const char c = command[i];
                if (quote) {
                    if (c == quote) quote = 0;
                    else if (c == '\\' && quote == '"') ++i;
                } else if (c == '\\') {
                    ++i;
                } else if (c == '\'' || c == '"') {
                    quote = c;
                }
            }
            if (!quote) {
                return "The file name '" + name + "' contains shell syntax, and this command uses it unquoted, so "
                       "the shell would run part of the name as a command. Put the name in single quotes, use a "
                       "glob or find -print0, or use file_read and file_search instead.";
            }
        }
    }
    return "";
}

std::string guardedFileReason(const fs::path& path) {
    // Package managers' lock files pin dependencies; they don't mean another program owns the file.
    static const std::set<std::string> packageLocks = {
        "cargo.lock", "gemfile.lock", "pipfile.lock", "podfile.lock", "flake.lock", "composer.lock",
        "pubspec.lock", "mix.lock", "deno.lock", "yarn.lock", "poetry.lock", "pnpm-lock.yaml", "uv.lock",
        "package.lock", "packages.lock", "bun.lock", "pdm.lock", "conda.lock", "go.sum"};
    static const std::regex heldBy(R"(\b(?:pid|owner|owned|held|locked|process|host)\b)", std::regex::icase);
    std::error_code ec;
    for (const fs::path& lock : {fs::path(path.u8string() + ".lock"), path.parent_path() / (path.stem().u8string() + ".lock")}) {
        if (!fs::is_regular_file(lock, ec) || packageLocks.count(lowered(lock.filename().u8string()))) continue;
        if (fs::file_size(lock, ec) > 1024) continue;  // a process lock is a line or two, not a dependency list
        std::ifstream in(lock);
        std::string first;
        std::getline(in, first);
        if (!first.empty() && !std::regex_search(first, heldBy)) continue;
        return lock.filename().u8string() + " sits beside it" + (first.empty() ? "" : " (" + first.substr(0, utf8Cut(first, 120)) + ")");
    }
    std::ifstream in(path);
    static const std::regex notice(
        R"(\b(?:(?:local )?edits? (?:are|will be|is|get) (?:discarded|overwritten|lost|reverted)|)"
        R"((?:do not|don't) (?:edit|modify)\b|auto-?generated\b[^\n]{0,60}\b(?:do not|don't)|)"
        R"(rewrites? (?:it|this file) on exit))",
        std::regex::icase);
    std::string line;
    for (int i = 0; i < 6 && std::getline(in, line); ++i) {
        std::smatch m;
        if (std::regex_search(line, m, notice)) return "its header says: " + line.substr(0, utf8Cut(line, 160));
    }
    return "";
}

}  // namespace gaia::shellguards
