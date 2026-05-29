// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/file_tools.h"

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace gaia {

// ---------------------------------------------------------------------------
// registerAll
// ---------------------------------------------------------------------------

void FileIOTools::registerAll(ToolRegistry& registry) {
    registry.registerTool(fileRead());
    registry.registerTool(fileWrite());
    registry.registerTool(fileEdit());
    registry.registerTool(fileSearch());
}

// ---------------------------------------------------------------------------
// fileRead
// ---------------------------------------------------------------------------

ToolInfo FileIOTools::fileRead() {
    ToolInfo info;
    info.name = "file_read";
    info.description =
        "Read the contents of a file. Optionally specify a line range with "
        "start_line and end_line (1-based, inclusive).";
    info.policy = ToolPolicy::ALLOW;
    info.parameters = {
        {"path", ToolParamType::STRING, /*required=*/true,
         "Absolute or relative path to the file to read"},
        {"start_line", ToolParamType::INTEGER, /*required=*/false,
         "First line to read (1-based, inclusive). Omit to start from the beginning."},
        {"end_line", ToolParamType::INTEGER, /*required=*/false,
         "Last line to read (1-based, inclusive). Omit to read to the end."},
    };
    info.callback = doFileRead;
    return info;
}

json FileIOTools::doFileRead(const json& args) {
    static constexpr size_t kMaxReadBytes = 32 * 1024;

    try {
        std::string path = args.value("path", "");
        if (path.empty()) {
            return json{{"error", "path is required"}};
        }

        std::ifstream file(path);
        if (!file.is_open()) {
            return json{{"error", "Cannot open file: " + path}};
        }

        int startLine = args.value("start_line", 0);
        int endLine = args.value("end_line", 0);

        std::string line;
        std::ostringstream content;
        int lineNumber = 0;
        int linesIncluded = 0;
        size_t bytesRead = 0;
        bool truncated = false;

        while (std::getline(file, line)) {
            ++lineNumber;

            bool inRange = true;
            if (startLine > 0 && lineNumber < startLine) inRange = false;
            if (endLine > 0 && lineNumber > endLine) inRange = false;

            if (inRange) {
                size_t lineBytes = line.size() + (linesIncluded > 0 ? 1 : 0);
                if (bytesRead + lineBytes > kMaxReadBytes) {
                    truncated = true;
                    break;
                }
                if (linesIncluded > 0) content << '\n';
                content << line;
                bytesRead += lineBytes;
                ++linesIncluded;
            }

            // Optimization: stop reading past end_line
            if (endLine > 0 && lineNumber >= endLine) {
                // Count remaining lines for total
                while (std::getline(file, line)) {
                    ++lineNumber;
                }
                break;
            }
        }

        // Count remaining lines if we truncated early
        if (truncated) {
            while (std::getline(file, line)) {
                ++lineNumber;
            }
        }

        std::string result = content.str();
        if (truncated) {
            result += "\n... [output truncated at 32 KB]";
        }

        return json{
            {"content", result},
            {"lines", lineNumber},
            {"path", path},
            {"truncated", truncated},
        };
    } catch (const std::exception& e) {
        return json{{"error", std::string("file_read failed: ") + e.what()}};
    }
}

// ---------------------------------------------------------------------------
// fileWrite
// ---------------------------------------------------------------------------

ToolInfo FileIOTools::fileWrite() {
    ToolInfo info;
    info.name = "file_write";
    info.description =
        "Write content to a file. Creates parent directories if they do not "
        "exist. Overwrites the file if it already exists.";
    info.policy = ToolPolicy::CONFIRM;
    info.parameters = {
        {"path", ToolParamType::STRING, /*required=*/true,
         "Absolute or relative path to the file to write"},
        {"content", ToolParamType::STRING, /*required=*/true,
         "The text content to write to the file"},
    };
    info.callback = doFileWrite;
    return info;
}

json FileIOTools::doFileWrite(const json& args) {
    try {
        std::string path = args.value("path", "");
        if (path.empty()) {
            return json{{"error", "path is required"}};
        }

        if (!args.contains("content") || !args["content"].is_string()) {
            return json{{"error", "content is required and must be a string"}};
        }
        const std::string& content = args["content"].get_ref<const std::string&>();

        // Create parent directories if needed
        fs::path filePath(path);
        if (filePath.has_parent_path()) {
            std::error_code ec;
            fs::create_directories(filePath.parent_path(), ec);
            if (ec) {
                return json{{"error", "Failed to create parent directories: " + ec.message()}};
            }
        }

        std::ofstream file(path, std::ios::binary);
        if (!file.is_open()) {
            return json{{"error", "Cannot open file for writing: " + path}};
        }

        file.write(content.data(), static_cast<std::streamsize>(content.size()));
        if (!file.good()) {
            return json{{"error", "Write failed for: " + path}};
        }
        file.close();

        return json{
            {"success", true},
            {"path", path},
            {"bytes_written", static_cast<int>(content.size())},
        };
    } catch (const std::exception& e) {
        return json{{"error", std::string("file_write failed: ") + e.what()}};
    }
}

// ---------------------------------------------------------------------------
// fileEdit
// ---------------------------------------------------------------------------

ToolInfo FileIOTools::fileEdit() {
    ToolInfo info;
    info.name = "file_edit";
    info.description =
        "Perform surgical string replacement in a file. Finds all occurrences "
        "of old_string and replaces them with new_string.";
    info.policy = ToolPolicy::CONFIRM;
    info.parameters = {
        {"path", ToolParamType::STRING, /*required=*/true,
         "Absolute or relative path to the file to edit"},
        {"old_string", ToolParamType::STRING, /*required=*/true,
         "The exact text to search for and replace"},
        {"new_string", ToolParamType::STRING, /*required=*/true,
         "The text to replace old_string with"},
    };
    info.callback = doFileEdit;
    return info;
}

json FileIOTools::doFileEdit(const json& args) {
    try {
        std::string path = args.value("path", "");
        if (path.empty()) {
            return json{{"error", "path is required"}};
        }

        std::string oldStr = args.value("old_string", "");
        if (oldStr.empty()) {
            return json{{"error", "old_string is required and must not be empty"}};
        }

        std::string newStr = args.value("new_string", "");

        // Read entire file
        std::ifstream inFile(path);
        if (!inFile.is_open()) {
            return json{{"error", "Cannot open file: " + path}};
        }

        std::ostringstream buffer;
        buffer << inFile.rdbuf();
        std::string content = buffer.str();
        inFile.close();

        // Replace all occurrences
        int replacements = 0;
        std::string::size_type pos = 0;
        while ((pos = content.find(oldStr, pos)) != std::string::npos) {
            content.replace(pos, oldStr.size(), newStr);
            pos += newStr.size();
            ++replacements;
        }

        if (replacements == 0) {
            return json{{"error", "old_string not found in file: " + path}};
        }

        // Write back
        std::ofstream outFile(path, std::ios::binary);
        if (!outFile.is_open()) {
            return json{{"error", "Cannot open file for writing: " + path}};
        }

        outFile.write(content.data(), static_cast<std::streamsize>(content.size()));
        if (!outFile.good()) {
            return json{{"error", "Write failed for: " + path}};
        }
        outFile.close();

        return json{
            {"success", true},
            {"path", path},
            {"replacements", replacements},
        };
    } catch (const std::exception& e) {
        return json{{"error", std::string("file_edit failed: ") + e.what()}};
    }
}

// ---------------------------------------------------------------------------
// fileSearch
// ---------------------------------------------------------------------------

ToolInfo FileIOTools::fileSearch() {
    ToolInfo info;
    info.name = "file_search";
    info.description =
        "Search for files by name pattern and/or content. The pattern is matched "
        "against file names using simple glob wildcards (* and ?). Optionally "
        "filter by content_pattern (substring match within file contents).";
    info.policy = ToolPolicy::ALLOW;
    info.parameters = {
        {"pattern", ToolParamType::STRING, /*required=*/true,
         "Glob pattern to match file names (e.g. '*.cpp', 'test_*')"},
        {"path", ToolParamType::STRING, /*required=*/false,
         "Root directory to search in (default: current directory)"},
        {"content_pattern", ToolParamType::STRING, /*required=*/false,
         "Substring to search for within matched files"},
        {"max_results", ToolParamType::INTEGER, /*required=*/false,
         "Maximum number of results to return (default: 50)"},
    };
    info.callback = doFileSearch;
    return info;
}

json FileIOTools::doFileSearch(const json& args) {
    try {
        std::string pattern = args.value("pattern", "");
        if (pattern.empty()) {
            return json{{"error", "pattern is required"}};
        }

        std::string searchPath = args.value("path", ".");
        std::string contentPattern = args.value("content_pattern", "");
        int maxResults = args.value("max_results", 50);
        if (maxResults <= 0) maxResults = 50;

        if (!fs::exists(searchPath)) {
            return json{{"error", "Search path does not exist: " + searchPath}};
        }

        if (!fs::is_directory(searchPath)) {
            return json{{"error", "Search path is not a directory: " + searchPath}};
        }

        json matches = json::array();
        int total = 0;

        std::error_code ec;
        for (auto it = fs::recursive_directory_iterator(searchPath, fs::directory_options::skip_permission_denied, ec);
             it != fs::recursive_directory_iterator(); it.increment(ec)) {
            if (ec) {
                ec.clear();
                continue;
            }

            if (!it->is_regular_file(ec)) continue;
            if (ec) { ec.clear(); continue; }

            std::string filename = it->path().filename().string();

            if (!matchGlob(pattern, filename)) {
                continue;
            }

            // If content_pattern is specified, search within file
            if (!contentPattern.empty()) {
                std::ifstream file(it->path());
                if (!file.is_open()) continue;

                std::string line;
                int lineNum = 0;
                while (std::getline(file, line)) {
                    ++lineNum;
                    if (line.find(contentPattern) != std::string::npos) {
                        ++total;
                        if (static_cast<int>(matches.size()) < maxResults) {
                            json match;
                            match["path"] = it->path().string();
                            match["line"] = lineNum;
                            // Trim context to reasonable length
                            std::string context = line;
                            if (context.size() > 200) {
                                context = context.substr(0, 200) + "...";
                            }
                            match["context"] = context;
                            matches.push_back(std::move(match));
                        }
                    }
                }
            } else {
                // Name match only
                ++total;
                if (static_cast<int>(matches.size()) < maxResults) {
                    json match;
                    match["path"] = it->path().string();
                    matches.push_back(std::move(match));
                }
            }
        }

        return json{
            {"matches", matches},
            {"total", total},
        };
    } catch (const std::exception& e) {
        return json{{"error", std::string("file_search failed: ") + e.what()}};
    }
}

// ---------------------------------------------------------------------------
// matchGlob — simple glob matching (* = any chars, ? = one char)
// ---------------------------------------------------------------------------

bool FileIOTools::matchGlob(const std::string& pattern, const std::string& text) {
    size_t pi = 0, ti = 0;
    size_t starPi = std::string::npos, starTi = 0;

    while (ti < text.size()) {
        if (pi < pattern.size() && (pattern[pi] == '?' || pattern[pi] == text[ti])) {
            ++pi;
            ++ti;
        } else if (pi < pattern.size() && pattern[pi] == '*') {
            starPi = pi;
            starTi = ti;
            ++pi;
        } else if (starPi != std::string::npos) {
            pi = starPi + 1;
            ++starTi;
            ti = starTi;
        } else {
            return false;
        }
    }

    while (pi < pattern.size() && pattern[pi] == '*') {
        ++pi;
    }

    return pi == pattern.size();
}

} // namespace gaia
