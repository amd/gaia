// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// PC Health Agent — LLM-driven PC diagnostician.
// The LLM decides which diagnostic tools to use based on the user's question.
// Tier 1: quick_health_scan (context), Tier 2: deep dives (logs, power,
// processes, disk/registry, network), Tier 3: actions (power plan, gaming).
// No Python, no MCP dependency. Windows-only (Win32 APIs + PowerShell).
//
// Usage:
//   ./pc_health_agent
//   > Why is my laptop slow?
//
// Requirements:
//   - Windows (Win32 APIs and PowerShell for system diagnostics)
//   - LLM server running at http://localhost:8000/api/v1

#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#include <psapi.h>
#include <shlobj.h>
#include <tlhelp32.h>
#endif

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <memory>
#include <queue>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include <gaia/agent.h>
#include <gaia/console.h>
#include <gaia/types.h>

// ---------------------------------------------------------------------------
// ANSI color constants (shared by HealthConsole and TUI helpers)
// ---------------------------------------------------------------------------
namespace color {
    constexpr const char* RESET   = "\033[0m";
    constexpr const char* BOLD    = "\033[1m";
    constexpr const char* DIM     = "\033[2m";
    constexpr const char* ITALIC  = "\033[3m";
    constexpr const char* UNDERLN = "\033[4m";
    constexpr const char* GRAY    = "\033[90m";
    constexpr const char* RED     = "\033[91m";
    constexpr const char* GREEN   = "\033[92m";
    constexpr const char* YELLOW  = "\033[93m";
    constexpr const char* BLUE    = "\033[94m";
    constexpr const char* MAGENTA = "\033[95m";
    constexpr const char* CYAN    = "\033[96m";
    constexpr const char* WHITE   = "\033[97m";
    // Background
    constexpr const char* BG_BLUE = "\033[44m";
}

// ---------------------------------------------------------------------------
// NextStep — parsed from LLM NEXT_STEPS block for dynamic post-diagnosis menu
// ---------------------------------------------------------------------------

struct NextStep {
    std::string text;  // Display-only tip for the user
};

struct ParsedDiagnosis {
    std::string cleanAnswer;
    std::vector<NextStep> nextSteps;
};

/// Parse the NEXT_STEPS: section from the end of an LLM answer.
/// Returns the clean answer (without NEXT_STEPS) and a vector of tip strings.
static ParsedDiagnosis parseNextSteps(const std::string& answer) {
    ParsedDiagnosis result;

    // Case-insensitive search for "NEXT_STEPS:" (find last occurrence)
    std::string upper = answer;
    for (auto& c : upper)
        c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));

    auto pos = upper.rfind("NEXT_STEPS:");
    if (pos == std::string::npos) {
        result.cleanAnswer = answer;
        return result;
    }

    // Everything before NEXT_STEPS: is the clean answer
    result.cleanAnswer = answer.substr(0, pos);
    auto last = result.cleanAnswer.find_last_not_of(" \t\n\r");
    if (last != std::string::npos) result.cleanAnswer.resize(last + 1);

    // Parse bullet lines after NEXT_STEPS:
    std::string block = answer.substr(pos + 11);
    std::istringstream stream(block);
    std::string line;
    constexpr size_t kMaxSteps = 8;

    while (std::getline(stream, line) && result.nextSteps.size() < kMaxSteps) {
        auto start = line.find_first_not_of(" \t\r");
        if (start == std::string::npos) continue;
        line = line.substr(start);
        if (line.size() < 3 || line[0] != '-' || line[1] != ' ') continue;
        line = line.substr(2);
        start = line.find_first_not_of(" \t");
        if (start == std::string::npos) continue;
        std::string text = line.substr(start);
        auto end = text.find_last_not_of(" \t\r\n");
        if (end != std::string::npos) text.resize(end + 1);
        if (!text.empty())
            result.nextSteps.push_back({std::move(text)});
    }

    return result;
}

// ---------------------------------------------------------------------------
// HealthConsole — formatted progress with health-grade rendering
// ---------------------------------------------------------------------------
class HealthConsole : public gaia::OutputHandler {
public:
    void printProcessingStart(const std::string& /*query*/, int /*maxSteps*/,
                              const std::string& /*modelId*/) override {
        std::cout << std::endl;
        planShown_ = false;
        toolsRun_ = 0;
        lastGoal_.clear();
    }

    void printStepHeader(int stepNum, int stepLimit) override {
        stepNum_ = stepNum;
        stepLimit_ = stepLimit;
    }

    void printStateInfo(const std::string& /*message*/) override {}

    void printThought(const std::string& thought) override {
        if (thought.empty()) return;

        // Look for structured FINDING:/DECISION: reasoning format
        auto findingPos = thought.find("FINDING:");
        if (findingPos == std::string::npos) findingPos = thought.find("Finding:");
        auto decisionPos = thought.find("DECISION:");
        if (decisionPos == std::string::npos) decisionPos = thought.find("Decision:");

        if (findingPos != std::string::npos || decisionPos != std::string::npos) {
            if (findingPos != std::string::npos) {
                size_t start = findingPos + 8;
                size_t end = (decisionPos != std::string::npos) ? decisionPos : thought.size();
                std::string text = thought.substr(start, end - start);
                size_t f = text.find_first_not_of(" \t\n\r");
                size_t l = text.find_last_not_of(" \t\n\r");
                if (f != std::string::npos) text = text.substr(f, l - f + 1);

                std::cout << color::GREEN << color::BOLD << "  Finding: "
                          << color::RESET;
                printWrapped(text, 79, 11);
            }
            if (decisionPos != std::string::npos) {
                size_t start = decisionPos + 9;
                std::string text = thought.substr(start);
                size_t f = text.find_first_not_of(" \t\n\r");
                size_t l = text.find_last_not_of(" \t\n\r");
                if (f != std::string::npos) text = text.substr(f, l - f + 1);

                std::cout << color::YELLOW << color::BOLD << "  Decision: "
                          << color::RESET;
                printWrapped(text, 78, 12);
            }
        } else {
            if (toolsRun_ > 0) {
                std::cout << color::BLUE << color::BOLD << "  Analysis: "
                          << color::RESET;
            } else {
                std::cout << color::MAGENTA << "  Thinking: " << color::RESET;
            }
            printWrapped(thought, 78, 12);
        }
    }

    void printGoal(const std::string& goal) override {
        if (goal.empty() || goal == lastGoal_) return;
        lastGoal_ = goal;
        std::cout << std::endl;
        std::cout << color::CYAN << color::ITALIC
                  << "  Goal: " << color::RESET;
        printWrapped(goal, 82, 8);
    }

    void printPlan(const gaia::json& plan, int /*currentStep*/) override {
        if (planShown_ || !plan.is_array()) return;
        planShown_ = true;
        std::cout << color::BOLD << color::CYAN << "  Plan: " << color::RESET;
        for (size_t i = 0; i < plan.size(); ++i) {
            if (i > 0) std::cout << color::GRAY << " -> " << color::RESET;
            if (plan[i].is_object() && plan[i].contains("tool")) {
                std::cout << color::CYAN
                          << plan[i]["tool"].get<std::string>()
                          << color::RESET;
            }
        }
        std::cout << std::endl;
    }

    void printToolUsage(const std::string& toolName) override {
        lastToolName_ = toolName;
        std::cout << std::endl;
        std::cout << color::YELLOW << color::BOLD
                  << "  [Step " << stepNum_ << "] "
                  << toolName << color::RESET << std::endl;
    }

    void printToolComplete() override {
        ++toolsRun_;
    }

    void prettyPrintJson(const gaia::json& data,
                         const std::string& title) override {
        // Show tool arguments
        if (title == "Tool Args" && data.is_object() && !data.empty()) {
            std::string argsStr;
            bool first = true;
            for (auto& [key, val] : data.items()) {
                if (!first) argsStr += ", ";
                argsStr += key + "=";
                if (val.is_string()) argsStr += val.get<std::string>();
                else argsStr += val.dump();
                first = false;
            }
            std::cout << color::GRAY << "      Args: ";
            printWrapped(argsStr, 78, 12);
            std::cout << color::RESET;
            return;
        }

        if (title != "Tool Result" || !data.is_object()) return;

        // Tool tier indicator
        if (data.contains("tool")) {
            std::string toolName = data["tool"].get<std::string>();
            const char* tierLabel = "Scan";
            const char* tierColor = color::CYAN;
            if (toolName == "set_power_plan" ||
                toolName == "optimize_for_gaming" ||
                toolName == "terminate_process") {
                tierLabel = "Action";
                tierColor = color::YELLOW;
            } else if (toolName == "quick_health_scan") {
                tierLabel = "Context";
                tierColor = color::GREEN;
            }
            std::cout << tierColor << "      [" << tierLabel << "] "
                      << color::RESET << std::endl;
        }

        // Show the command that was executed
        if (data.contains("command")) {
            std::string cmd = data["command"].get<std::string>();
            std::cout << color::CYAN << "      Cmd: " << color::RESET
                      << color::GRAY;
            printWrapped(cmd, 79, 11);
            std::cout << color::RESET;
        }

        // Show error if present
        if (data.contains("error")) {
            std::cout << color::RED << color::BOLD << "      Error: "
                      << color::RESET << color::RED
                      << data["error"].get<std::string>()
                      << color::RESET << std::endl;
            return;
        }

        // Show tool output preview
        if (data.contains("output")) {
            std::string output = data["output"].get<std::string>();
            if (output.empty() || output.find("(no output)") != std::string::npos) {
                std::cout << color::GREEN << "      Result: "
                          << color::RESET << color::GRAY << "(no output)"
                          << color::RESET << std::endl;
                return;
            }
            std::cout << color::GREEN << "      Output:" << color::RESET
                      << std::endl;
            printOutputPreview(output);
        }

        // Show status for other tools
        if (data.contains("status")) {
            auto status = data["status"].get<std::string>();
            const char* statusColor = (status == "completed")
                ? color::GREEN : color::YELLOW;
            std::cout << statusColor << "      Status: " << status
                      << color::RESET << std::endl;
        }
    }

    void printError(const std::string& message) override {
        std::cout << color::RED << color::BOLD << "  ERROR: " << color::RESET
                  << color::RED;
        printWrapped(message, 81, 9);
        std::cout << color::RESET;
    }

    void printWarning(const std::string& message) override {
        std::cout << color::YELLOW << "  WARNING: " << color::RESET
                  << message << std::endl;
    }

    void printInfo(const std::string& /*message*/) override {}

    void startProgress(const std::string& /*message*/) override {}

    void stopProgress() override {}

    void printFinalAnswer(const std::string& answer) override {
        if (answer.empty()) return;

        // Extract clean text — LLM sometimes returns raw JSON
        std::string cleanAnswer = answer;
        if (!answer.empty() && answer.front() == '{') {
            try {
                auto j = gaia::json::parse(answer);
                if (j.is_object()) {
                    if (j.contains("answer") && j["answer"].is_string()) {
                        cleanAnswer = j["answer"].get<std::string>();
                    } else if (j.contains("thought") && j["thought"].is_string()) {
                        cleanAnswer = j["thought"].get<std::string>();
                    }
                }
            } catch (...) {}
        }

        // Strip NEXT_STEPS section (REPL parses it independently)
        {
            auto diag = parseNextSteps(cleanAnswer);
            cleanAnswer = diag.cleanAnswer;
        }

        // Parse health grade from answer (first line: "GRADE: X")
        char grade = '\0';
        auto gradePos = cleanAnswer.find("GRADE:");
        if (gradePos == std::string::npos) gradePos = cleanAnswer.find("Grade:");
        if (gradePos != std::string::npos) {
            // Find the letter after "GRADE:"
            size_t letterPos = cleanAnswer.find_first_not_of(" \t", gradePos + 6);
            if (letterPos != std::string::npos) {
                char ch = static_cast<char>(std::toupper(
                    static_cast<unsigned char>(cleanAnswer[letterPos])));
                if (ch >= 'A' && ch <= 'F') {
                    grade = ch;
                }
            }
        }

        std::cout << std::endl;

        // Render health grade banner if found
        if (grade != '\0') {
            const char* gradeColor = gradeToColor(grade);
            std::cout << gradeColor << color::BOLD
                      << "  +------------------+" << color::RESET << std::endl;
            std::cout << gradeColor << color::BOLD
                      << "  |  HEALTH GRADE: " << grade << "  |" << color::RESET << std::endl;
            std::cout << gradeColor << color::BOLD
                      << "  +------------------+" << color::RESET << std::endl;
            std::cout << std::endl;
        }

        std::cout << color::GREEN
                  << "  ========================================================================================"
                  << color::RESET << std::endl;
        std::cout << color::GREEN << color::BOLD
                  << "  Answer" << color::RESET << std::endl;
        std::cout << color::GREEN
                  << "  ========================================================================================"
                  << color::RESET << std::endl;

        // Print each line word-wrapped
        std::string line;
        std::istringstream stream(cleanAnswer);
        while (std::getline(stream, line)) {
            if (line.empty()) {
                std::cout << std::endl;
            } else {
                std::cout << "  ";
                printWrapped(line, 88, 2);
            }
        }
        std::cout << color::GREEN
                  << "  ========================================================================================"
                  << color::RESET << std::endl;
    }

    void printCompletion(int stepsTaken, int /*stepsLimit*/) override {
        std::cout << color::GRAY << "  Completed in " << stepsTaken
                  << " steps" << color::RESET << std::endl;
    }

private:
    static const char* gradeToColor(char grade) {
        switch (grade) {
            case 'A': case 'B': return color::GREEN;
            case 'C': case 'D': return color::YELLOW;
            case 'F':           return color::RED;
            default:            return color::WHITE;
        }
    }

    static void printStyledWord(const std::string& word, const char* prevColor) {
        size_t pos = 0;
        while (pos < word.size()) {
            auto boldStart = word.find("**", pos);
            if (boldStart == std::string::npos) {
                std::cout << word.substr(pos);
                break;
            }
            std::cout << word.substr(pos, boldStart - pos);
            auto boldEnd = word.find("**", boldStart + 2);
            if (boldEnd == std::string::npos) {
                std::cout << word.substr(boldStart);
                break;
            }
            std::cout << color::BOLD << color::WHITE
                      << word.substr(boldStart + 2, boldEnd - boldStart - 2)
                      << color::RESET << prevColor;
            pos = boldEnd + 2;
        }
    }

    static void printWrapped(const std::string& text, size_t width, size_t indent,
                             const char* prevColor = color::RESET) {
        std::string indentStr(indent, ' ');
        std::istringstream words(text);
        std::string word;
        size_t col = 0;
        bool firstWord = true;
        while (words >> word) {
            std::string plain = word;
            size_t p;
            while ((p = plain.find("**")) != std::string::npos)
                plain.erase(p, 2);

            if (!firstWord && col + 1 + plain.size() > width) {
                std::cout << std::endl << indentStr;
                col = 0;
            } else if (!firstWord) {
                std::cout << ' ';
                ++col;
            }
            printStyledWord(word, prevColor);
            col += plain.size();
            firstWord = false;
        }
        std::cout << color::RESET << std::endl;
    }

    void printOutputPreview(const std::string& output) {
        constexpr int kMaxPreviewLines = 10;
        std::istringstream stream(output);
        std::string line;
        int lineCount = 0;
        int totalLines = 0;

        {
            std::istringstream counter(output);
            std::string tmp;
            while (std::getline(counter, tmp)) {
                if (!tmp.empty() && tmp.find_first_not_of(" \t\r\n") != std::string::npos)
                    ++totalLines;
            }
        }

        std::cout << color::GRAY << "      .------------------------------------------------------------------------------------"
                  << color::RESET << std::endl;
        while (std::getline(stream, line) && lineCount < kMaxPreviewLines) {
            if (line.empty() || line.find_first_not_of(" \t\r\n") == std::string::npos)
                continue;
            if (!line.empty() && line.back() == '\r') line.pop_back();
            if (line.size() > 82) line = line.substr(0, 79) + "...";
            std::cout << color::GRAY << "      | " << line << color::RESET
                      << std::endl;
            ++lineCount;
        }
        if (totalLines > kMaxPreviewLines) {
            std::cout << color::GRAY << "      | ... ("
                      << (totalLines - kMaxPreviewLines)
                      << " more lines)" << color::RESET << std::endl;
        }
        std::cout << color::GRAY << "      '------------------------------------------------------------------------------------"
                  << color::RESET << std::endl;
    }

    int stepNum_ = 0;
    int stepLimit_ = 0;
    int toolsRun_ = 0;
    bool planShown_ = false;
    std::string lastToolName_;
    std::string lastGoal_;
};

// ---------------------------------------------------------------------------
// Shell helpers — PowerShell wrapper and input validation
// ---------------------------------------------------------------------------
static std::string runShell(const std::string& command) {
    std::string fullCmd;
#ifdef _WIN32
    fullCmd = "powershell -NoProfile -NonInteractive -Command \"& { "
              + command + " }\" 2>&1";
#else
    fullCmd = command + " 2>&1";
#endif
#ifdef _WIN32
    std::unique_ptr<FILE, decltype(&_pclose)> pipe(
        _popen(fullCmd.c_str(), "r"), _pclose);
#else
    std::unique_ptr<FILE, decltype(&pclose)> pipe(
        popen(fullCmd.c_str(), "r"), pclose);
#endif
    if (!pipe) {
        return R"({"error": "Failed to execute command"})";
    }

    std::array<char, 4096> buffer;
    std::string result;
    while (fgets(buffer.data(), static_cast<int>(buffer.size()), pipe.get()) != nullptr) {
        result += buffer.data();
    }

    if (result.empty()) {
        return R"json({"status": "completed", "output": "(no output)"})json";
    }
    return result;
}

static bool isSafeShellArg(const std::string& arg) {
    if (arg.empty()) return false;
    const std::string dangerous = ";|&`$(){}<>\"'\n\r";
    for (char c : arg) {
        if (dangerous.find(c) != std::string::npos) return false;
    }
    return true;
}

// ---------------------------------------------------------------------------
// Path validation — reject traversal, require drive letter
// ---------------------------------------------------------------------------
static bool isSafePath(const std::string& path) {
    if (path.size() < 3) return false;
    if (path.find("..") != std::string::npos) return false;
    // Require drive letter (e.g., C:\...)
    if (!(std::isalpha(static_cast<unsigned char>(path[0])) &&
          path[1] == ':' && (path[2] == '\\' || path[2] == '/'))) {
        return false;
    }
    return isSafeShellArg(path);
}

// ---------------------------------------------------------------------------
// Format bytes as human-readable string
// ---------------------------------------------------------------------------
static std::string formatBytes(uint64_t bytes) {
    const char* units[] = {"B", "KB", "MB", "GB", "TB"};
    int unitIdx = 0;
    auto size = static_cast<double>(bytes);
    while (size >= 1024.0 && unitIdx < 4) {
        size /= 1024.0;
        ++unitIdx;
    }
    char buf[64];
    std::snprintf(buf, sizeof(buf), "%.1f %s", size, units[unitIdx]);
    return buf;
}

// ---------------------------------------------------------------------------
// Wide string to UTF-8 conversion
// ---------------------------------------------------------------------------
static std::string wstringToUtf8(const std::wstring& wstr) {
    if (wstr.empty()) return "";
    int size = WideCharToMultiByte(CP_UTF8, 0, wstr.data(),
                                    static_cast<int>(wstr.size()),
                                    nullptr, 0, nullptr, nullptr);
    if (size <= 0) return "";
    std::string result(static_cast<size_t>(size), '\0');
    WideCharToMultiByte(CP_UTF8, 0, wstr.data(),
                        static_cast<int>(wstr.size()),
                        result.data(), size, nullptr, nullptr);
    return result;
}

// ---------------------------------------------------------------------------
// UTF-8 string to wide string conversion
// ---------------------------------------------------------------------------
static std::wstring utf8ToWstring(const std::string& str) {
    if (str.empty()) return {};
    int wlen = MultiByteToWideChar(CP_UTF8, 0, str.c_str(),
                                    static_cast<int>(str.size()),
                                    nullptr, 0);
    if (wlen <= 0) return {};
    std::wstring result(static_cast<size_t>(wlen), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, str.c_str(),
                         static_cast<int>(str.size()),
                         result.data(), wlen);
    return result;
}

// ---------------------------------------------------------------------------
// Recursive directory scanner using FindFirstFileExW
// ---------------------------------------------------------------------------
struct DirScanResult {
    uint64_t totalBytes = 0;
    uint64_t fileCount = 0;
};

static DirScanResult scanDirectory(const std::wstring& dirPath, int maxDepth = 10) {
    DirScanResult result;
    if (maxDepth <= 0) return result;

    WIN32_FIND_DATAW fd;
    HANDLE hFind = FindFirstFileExW((dirPath + L"\\*").c_str(),
                                     FindExInfoBasic, &fd,
                                     FindExSearchNameMatch, nullptr,
                                     FIND_FIRST_EX_LARGE_FETCH);
    if (hFind == INVALID_HANDLE_VALUE) return result;

    do {
        if (wcscmp(fd.cFileName, L".") == 0 || wcscmp(fd.cFileName, L"..") == 0)
            continue;
        // Skip reparse points (junctions/symlinks) to avoid infinite loops
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)
            continue;

        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            auto sub = scanDirectory(dirPath + L"\\" + fd.cFileName, maxDepth - 1);
            result.totalBytes += sub.totalBytes;
            result.fileCount += sub.fileCount;
        } else {
            ULARGE_INTEGER fileSize;
            fileSize.HighPart = fd.nFileSizeHigh;
            fileSize.LowPart = fd.nFileSizeLow;
            result.totalBytes += fileSize.QuadPart;
            ++result.fileCount;
        }
    } while (FindNextFileW(hFind, &fd));
    FindClose(hFind);
    return result;
}

// ---------------------------------------------------------------------------
// Get a known folder path as wide string (SHGetKnownFolderPath wrapper)
// ---------------------------------------------------------------------------
static std::wstring getKnownFolder(const KNOWNFOLDERID& folderId) {
    wchar_t* path = nullptr;
    HRESULT hr = SHGetKnownFolderPath(folderId, 0, nullptr, &path);
    if (SUCCEEDED(hr) && path) {
        std::wstring result(path);
        CoTaskMemFree(path);
        return result;
    }
    if (path) CoTaskMemFree(path);
    return {};
}

// ---------------------------------------------------------------------------
// Get environment variable as wide string
// ---------------------------------------------------------------------------
static std::wstring getEnvWide(const wchar_t* varName) {
    wchar_t buf[MAX_PATH];
    DWORD len = GetEnvironmentVariableW(varName, buf, MAX_PATH);
    if (len > 0 && len < MAX_PATH) return std::wstring(buf, len);
    return {};
}

// ---------------------------------------------------------------------------
// Registry helper: enumerate value names from a key
// ---------------------------------------------------------------------------
static gaia::json enumRegValues(HKEY hRoot, const wchar_t* subKey,
                                int maxEntries = 500) {
    gaia::json entries = gaia::json::array();
    HKEY hKey = nullptr;
    if (RegOpenKeyExW(hRoot, subKey, 0, KEY_READ, &hKey) != ERROR_SUCCESS)
        return entries;

    wchar_t valueName[512];
    DWORD valueNameLen;
    BYTE dataBuffer[2048];
    DWORD dataSize;
    DWORD type;
    int count = 0;

    for (DWORD i = 0; count < maxEntries; ++i) {
        valueNameLen = 512;
        dataSize = sizeof(dataBuffer);
        LONG ret = RegEnumValueW(hKey, i, valueName, &valueNameLen,
                                  nullptr, &type, dataBuffer, &dataSize);
        if (ret == ERROR_NO_MORE_ITEMS) break;
        if (ret != ERROR_SUCCESS) continue;
        ++count;

        gaia::json entry;
        entry["name"] = wstringToUtf8(std::wstring(valueName, valueNameLen));
        if (type == REG_SZ || type == REG_EXPAND_SZ) {
            auto* wstr = reinterpret_cast<wchar_t*>(dataBuffer);
            size_t wlen = dataSize / sizeof(wchar_t);
            if (wlen > 0 && wstr[wlen - 1] == L'\0') --wlen;
            entry["value"] = wstringToUtf8(std::wstring(wstr, wlen));
        }
        entries.push_back(std::move(entry));
    }
    RegCloseKey(hKey);
    return entries;
}

// ---------------------------------------------------------------------------
// Registry helper: enumerate subkey names from a key
// ---------------------------------------------------------------------------
static std::vector<std::wstring> enumRegSubkeys(HKEY hRoot,
                                                  const wchar_t* subKey,
                                                  int maxKeys = 500) {
    std::vector<std::wstring> keys;
    HKEY hKey = nullptr;
    if (RegOpenKeyExW(hRoot, subKey, 0, KEY_READ, &hKey) != ERROR_SUCCESS)
        return keys;

    wchar_t keyName[256];
    DWORD keyNameLen;
    for (DWORD i = 0; static_cast<int>(i) < maxKeys; ++i) {
        keyNameLen = 256;
        LONG ret = RegEnumKeyExW(hKey, i, keyName, &keyNameLen,
                                  nullptr, nullptr, nullptr, nullptr);
        if (ret == ERROR_NO_MORE_ITEMS) break;
        if (ret != ERROR_SUCCESS) continue;
        keys.emplace_back(keyName, keyNameLen);
    }
    RegCloseKey(hKey);
    return keys;
}

// ---------------------------------------------------------------------------
// Registry helper: read a single string value from a key
// ---------------------------------------------------------------------------
static std::string readRegString(HKEY hRoot, const wchar_t* subKey,
                                  const wchar_t* valueName) {
    HKEY hKey = nullptr;
    if (RegOpenKeyExW(hRoot, subKey, 0, KEY_READ, &hKey) != ERROR_SUCCESS)
        return {};
    wchar_t data[1024];
    DWORD dataSize = sizeof(data);
    DWORD type = 0;
    LONG ret = RegQueryValueExW(hKey, valueName, nullptr, &type,
                                 reinterpret_cast<BYTE*>(data), &dataSize);
    RegCloseKey(hKey);
    if (ret != ERROR_SUCCESS || (type != REG_SZ && type != REG_EXPAND_SZ))
        return {};
    size_t wlen = dataSize / sizeof(wchar_t);
    if (wlen > 0 && data[wlen - 1] == L'\0') --wlen;
    return wstringToUtf8(std::wstring(data, wlen));
}

// ---------------------------------------------------------------------------
// Check if a file exists (wide path)
// ---------------------------------------------------------------------------
static bool fileExistsW(const std::wstring& path) {
    DWORD attrs = GetFileAttributesW(path.c_str());
    return (attrs != INVALID_FILE_ATTRIBUTES &&
            !(attrs & FILE_ATTRIBUTE_DIRECTORY));
}

// ---------------------------------------------------------------------------
// Bloatware list — common pre-installed Windows apps
// ---------------------------------------------------------------------------
static const std::vector<std::string> kBloatwareList = {
    "Microsoft.3DBuilder",
    "Microsoft.BingNews",
    "Microsoft.BingWeather",
    "Microsoft.GamingApp",
    "Microsoft.GetHelp",
    "Microsoft.Getstarted",
    "Microsoft.Messaging",
    "Microsoft.MicrosoftSolitaireCollection",
    "Microsoft.MixedReality.Portal",
    "Microsoft.OneConnect",
    "Microsoft.People",
    "Microsoft.Print3D",
    "Microsoft.SkypeApp",
    "Microsoft.Todos",
    "Microsoft.Wallet",
    "Microsoft.WindowsFeedbackHub",
    "Microsoft.WindowsMaps",
    "Microsoft.WindowsPhone",
    "Microsoft.Xbox.TCUI",
    "Microsoft.XboxApp",
    "Microsoft.XboxGameOverlay",
    "Microsoft.XboxGamingOverlay",
    "Microsoft.XboxIdentityProvider",
    "Microsoft.XboxSpeechToTextOverlay",
    "Microsoft.YourPhone",
    "Microsoft.ZuneMusic",
    "Microsoft.ZuneVideo",
    "Microsoft.PowerAutomateDesktop",
    "MicrosoftTeams",
    "Clipchamp.Clipchamp",
    "king.com.CandyCrushSaga",
    "king.com.CandyCrushSodaSaga",
    "SpotifyAB.SpotifyMusic",
    "Facebook.Facebook",
    "Facebook.Instagram",
    "BytedancePte.Ltd.TikTok",
    "Disney.37853FC22B2CE",
    "Flipboard.Flipboard",
    "ShazamEntertainmentLtd.Shazam",
    "AdobeSystemsIncorporated.AdobePhotoshopExpress",
    "GAMELOFTSA.Asphalt8Airborne",
};

// ---------------------------------------------------------------------------
// Parse PowerShell JSON output with error handling
// ---------------------------------------------------------------------------
static gaia::json parsePsJson(const std::string& output) {
    if (output.empty()) {
        return {{"error", "Empty PowerShell output"}};
    }
    try {
        // PowerShell sometimes prepends warnings/errors before JSON
        // Find the first '{' or '[' to locate the JSON start
        auto jsonStart = output.find_first_of("{[");
        if (jsonStart == std::string::npos) {
            return {{"error", "No JSON in output"},
                    {"raw", output.substr(0, 500)}};
        }
        return gaia::json::parse(output.substr(jsonStart));
    } catch (...) {
        return {{"error", "Failed to parse PowerShell JSON"},
                {"raw", output.substr(0, 500)}};
    }
}

// ---------------------------------------------------------------------------
// Extracted data-gathering functions (from original tool lambdas)
// These are standalone so multiple tools can compose them.
// ---------------------------------------------------------------------------

// Scan all logical drives for disk space usage
static gaia::json getDiskUsageInfo() {
    gaia::json drives = gaia::json::array();
    wchar_t driveStrings[512];
    DWORD len = GetLogicalDriveStringsW(511, driveStrings);
    wchar_t* p = driveStrings;
    while (*p && (p - driveStrings) < static_cast<ptrdiff_t>(len)) {
        UINT driveType = GetDriveTypeW(p);
        if (driveType == DRIVE_FIXED || driveType == DRIVE_REMOVABLE) {
            ULARGE_INTEGER freeAvail, totalBytes, freeBytes;
            if (GetDiskFreeSpaceExW(p, &freeAvail, &totalBytes, &freeBytes)) {
                wchar_t label[256] = {0};
                wchar_t fsName[64] = {0};
                GetVolumeInformationW(p, label, 255, nullptr,
                                       nullptr, nullptr, fsName, 63);
                uint64_t total = totalBytes.QuadPart;
                uint64_t free = freeBytes.QuadPart;
                uint64_t used = total - free;
                double usedPct = total > 0 ? (used * 100.0 / total) : 0.0;
                drives.push_back({
                    {"drive", wstringToUtf8(std::wstring(p))},
                    {"label", wstringToUtf8(std::wstring(label))},
                    {"filesystem", wstringToUtf8(std::wstring(fsName))},
                    {"total_bytes", total},
                    {"free_bytes", free},
                    {"used_bytes", used},
                    {"used_percent", static_cast<int>(usedPct)},
                    {"total_human", formatBytes(total)},
                    {"free_human", formatBytes(free)},
                    {"used_human", formatBytes(used)},
                });
            }
        }
        p += wcslen(p) + 1;
    }
    return drives;
}

// Get system memory info via GlobalMemoryStatusEx
static gaia::json getMemoryInfo() {
    MEMORYSTATUSEX memInfo;
    memInfo.dwLength = sizeof(memInfo);
    GlobalMemoryStatusEx(&memInfo);
    return {
        {"total_bytes", memInfo.ullTotalPhys},
        {"available_bytes", memInfo.ullAvailPhys},
        {"used_bytes", memInfo.ullTotalPhys - memInfo.ullAvailPhys},
        {"used_percent", static_cast<int>(memInfo.dwMemoryLoad)},
        {"total_human", formatBytes(memInfo.ullTotalPhys)},
        {"available_human", formatBytes(memInfo.ullAvailPhys)},
        {"used_human", formatBytes(memInfo.ullTotalPhys - memInfo.ullAvailPhys)},
    };
}

// Get top N processes by memory usage via Toolhelp snapshot
static gaia::json getTopProcesses(int topN = 20) {
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snapshot == INVALID_HANDLE_VALUE) {
        return {{"error", "Failed to create process snapshot"}};
    }

    struct ProcInfo {
        std::string name;
        DWORD pid;
        uint64_t memoryBytes;
    };
    std::vector<ProcInfo> procs;
    int skipped = 0;

    PROCESSENTRY32W pe;
    pe.dwSize = sizeof(pe);
    if (Process32FirstW(snapshot, &pe)) {
        do {
            HANDLE hProc = OpenProcess(
                PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
                FALSE, pe.th32ProcessID);
            if (hProc) {
                PROCESS_MEMORY_COUNTERS pmc;
                if (K32GetProcessMemoryInfo(hProc, &pmc, sizeof(pmc))) {
                    procs.push_back({
                        wstringToUtf8(pe.szExeFile),
                        pe.th32ProcessID,
                        pmc.WorkingSetSize
                    });
                }
                CloseHandle(hProc);
            } else {
                ++skipped;
            }
        } while (Process32NextW(snapshot, &pe));
    }
    CloseHandle(snapshot);

    std::sort(procs.begin(), procs.end(),
              [](const ProcInfo& a, const ProcInfo& b) {
                  return a.memoryBytes > b.memoryBytes;
              });

    gaia::json result = gaia::json::array();
    int limit = std::min(static_cast<int>(procs.size()), topN);
    for (int i = 0; i < limit; ++i) {
        result.push_back({
            {"name", procs[i].name},
            {"pid", procs[i].pid},
            {"memory_bytes", procs[i].memoryBytes},
            {"memory_human", formatBytes(procs[i].memoryBytes)},
        });
    }
    return result;
}

// Scan junk file categories (temp, caches, logs, etc.)
static gaia::json scanJunkCategories() {
    gaia::json categories = gaia::json::array();
    uint64_t grandTotal = 0;

    auto scanCategory = [&](const std::string& name,
                            const std::wstring& path) {
        if (path.empty()) {
            categories.push_back({
                {"name", name}, {"path", ""},
                {"error", "path not found"},
                {"file_count", 0}, {"total_bytes", 0}
            });
            return;
        }
        auto result = scanDirectory(path, 5);
        grandTotal += result.totalBytes;
        categories.push_back({
            {"name", name},
            {"path", wstringToUtf8(path)},
            {"file_count", result.fileCount},
            {"total_bytes", result.totalBytes},
            {"total_human", formatBytes(result.totalBytes)},
        });
    };

    scanCategory("User Temp", getEnvWide(L"TEMP"));
    std::wstring winDir = getEnvWide(L"WINDIR");
    scanCategory("System Temp", winDir.empty() ? L"" : winDir + L"\\Temp");
    scanCategory("Windows Update Cache",
        winDir.empty() ? L"" : winDir + L"\\SoftwareDistribution\\Download");
    scanCategory("Prefetch",
        winDir.empty() ? L"" : winDir + L"\\Prefetch");
    std::wstring localAppData = getKnownFolder(FOLDERID_LocalAppData);
    scanCategory("Crash Dumps",
        localAppData.empty() ? L"" : localAppData + L"\\CrashDumps");
    scanCategory("Error Reports",
        winDir.empty() ? L"" : winDir + L"\\WER\\ReportQueue");
    scanCategory("Thumbnail Cache",
        localAppData.empty() ? L""
            : localAppData + L"\\Microsoft\\Windows\\Explorer");
    scanCategory("Delivery Optimization",
        winDir.empty() ? L""
            : winDir + L"\\ServiceProfiles\\NetworkService\\AppData"
                       L"\\Local\\DeliveryOptimization\\Cache");
    scanCategory("DirectX Shader Cache",
        localAppData.empty() ? L"" : localAppData + L"\\D3DSCache");
    scanCategory("Installer Patch Cache",
        winDir.empty() ? L"" : winDir + L"\\Installer\\$PatchCache$");
    {
        std::wstring sysDrive = getEnvWide(L"SYSTEMDRIVE");
        scanCategory("Windows.old",
            sysDrive.empty() ? L"C:\\Windows.old"
                             : sysDrive + L"\\Windows.old");
    }

    return {{"categories", categories},
            {"grand_total_bytes", grandTotal},
            {"grand_total_human", formatBytes(grandTotal)}};
}

// Scan browser caches (Chrome, Edge, Firefox)
static gaia::json scanBrowserCaches() {
    gaia::json browsers = gaia::json::array();
    uint64_t grandTotal = 0;
    std::wstring localAppData = getKnownFolder(FOLDERID_LocalAppData);
    std::wstring roamingAppData = getKnownFolder(FOLDERID_RoamingAppData);

    auto scanBrowser = [&](const std::string& name,
                           const std::wstring& cachePath) {
        if (cachePath.empty()) {
            browsers.push_back({
                {"name", name}, {"error", "path not found"},
                {"file_count", 0}, {"total_bytes", 0}
            });
            return;
        }
        DWORD attrs = GetFileAttributesW(cachePath.c_str());
        if (attrs == INVALID_FILE_ATTRIBUTES) {
            browsers.push_back({
                {"name", name}, {"path", wstringToUtf8(cachePath)},
                {"error", "not installed or no cache"},
                {"file_count", 0}, {"total_bytes", 0}
            });
            return;
        }
        auto result = scanDirectory(cachePath, 5);
        grandTotal += result.totalBytes;
        browsers.push_back({
            {"name", name},
            {"path", wstringToUtf8(cachePath)},
            {"file_count", result.fileCount},
            {"total_bytes", result.totalBytes},
            {"total_human", formatBytes(result.totalBytes)},
        });
    };

    if (!localAppData.empty()) {
        scanBrowser("Google Chrome",
            localAppData + L"\\Google\\Chrome\\User Data\\Default\\Cache");
        scanBrowser("Microsoft Edge",
            localAppData + L"\\Microsoft\\Edge\\User Data\\Default\\Cache");
    }
    if (!roamingAppData.empty()) {
        std::wstring ffDir = roamingAppData + L"\\Mozilla\\Firefox\\Profiles";
        DWORD attrs = GetFileAttributesW(ffDir.c_str());
        if (attrs != INVALID_FILE_ATTRIBUTES &&
            (attrs & FILE_ATTRIBUTE_DIRECTORY)) {
            auto result = scanDirectory(ffDir, 5);
            grandTotal += result.totalBytes;
            browsers.push_back({
                {"name", "Mozilla Firefox"},
                {"path", wstringToUtf8(ffDir)},
                {"file_count", result.fileCount},
                {"total_bytes", result.totalBytes},
                {"total_human", formatBytes(result.totalBytes)},
            });
        } else {
            browsers.push_back({
                {"name", "Mozilla Firefox"},
                {"error", "not installed or no profiles"},
                {"file_count", 0}, {"total_bytes", 0}
            });
        }
    }

    return {{"browsers", browsers},
            {"grand_total_bytes", grandTotal},
            {"grand_total_human", formatBytes(grandTotal)}};
}

// Scan registry health across 7 categories
static gaia::json scanRegistryHealth() {
    gaia::json categoriesArr = gaia::json::array();
    int totalInvalid = 0;

    // 1. SharedDLLs
    {
        auto values = enumRegValues(HKEY_LOCAL_MACHINE,
            L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\SharedDLLs", 500);
        int invalid = 0;
        gaia::json invalidEntries = gaia::json::array();
        for (auto& entry : values) {
            if (!entry.contains("name")) continue;
            std::string path = entry["name"].get<std::string>();
            if (!fileExistsW(utf8ToWstring(path))) {
                ++invalid;
                if (invalid <= 20)
                    invalidEntries.push_back({{"path", path}});
            }
        }
        totalInvalid += invalid;
        categoriesArr.push_back({
            {"name", "SharedDLLs"},
            {"total_entries", values.size()},
            {"invalid_entries", invalid},
            {"sample_invalid", invalidEntries}
        });
    }

    // 2. App Paths
    {
        auto subkeys = enumRegSubkeys(HKEY_LOCAL_MACHINE,
            L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths", 500);
        int invalid = 0;
        gaia::json invalidEntries = gaia::json::array();
        for (auto& sk : subkeys) {
            std::wstring fullKey =
                L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + sk;
            std::string exePath = readRegString(
                HKEY_LOCAL_MACHINE, fullKey.c_str(), nullptr);
            if (exePath.empty()) continue;
            if (!fileExistsW(utf8ToWstring(exePath))) {
                ++invalid;
                if (invalid <= 20)
                    invalidEntries.push_back({
                        {"app", wstringToUtf8(sk)},
                        {"path", exePath}
                    });
            }
        }
        totalInvalid += invalid;
        categoriesArr.push_back({
            {"name", "App Paths"},
            {"total_entries", subkeys.size()},
            {"invalid_entries", invalid},
            {"sample_invalid", invalidEntries}
        });
    }

    // 3. COM/CLSID (sample first 200)
    {
        auto subkeys = enumRegSubkeys(HKEY_CLASSES_ROOT, L"CLSID", 200);
        int invalid = 0;
        gaia::json invalidEntries = gaia::json::array();
        for (auto& clsid : subkeys) {
            std::wstring inprocKey = L"CLSID\\" + clsid + L"\\InprocServer32";
            std::string dllPath = readRegString(
                HKEY_CLASSES_ROOT, inprocKey.c_str(), nullptr);
            if (dllPath.empty()) {
                std::wstring localKey = L"CLSID\\" + clsid + L"\\LocalServer32";
                dllPath = readRegString(
                    HKEY_CLASSES_ROOT, localKey.c_str(), nullptr);
            }
            if (dllPath.empty()) continue;
            if (dllPath.size() >= 2 && dllPath.front() == '"') {
                auto endQuote = dllPath.find('"', 1);
                if (endQuote != std::string::npos)
                    dllPath = dllPath.substr(1, endQuote - 1);
            }
            auto spacePos = dllPath.find(' ');
            if (spacePos != std::string::npos)
                dllPath = dllPath.substr(0, spacePos);
            if (!fileExistsW(utf8ToWstring(dllPath))) {
                ++invalid;
                if (invalid <= 10)
                    invalidEntries.push_back({
                        {"clsid", wstringToUtf8(clsid)},
                        {"path", dllPath}
                    });
            }
        }
        totalInvalid += invalid;
        categoriesArr.push_back({
            {"name", "COM/CLSID"},
            {"total_entries", subkeys.size()},
            {"invalid_entries", invalid},
            {"note", "Sampled first 200 CLSIDs"},
            {"sample_invalid", invalidEntries}
        });
    }

    // 4. Uninstall
    {
        const wchar_t* uninstallPaths[] = {
            L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
            L"SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
        };
        int totalEntries = 0;
        int invalid = 0;
        gaia::json invalidEntries = gaia::json::array();
        for (auto* uPath : uninstallPaths) {
            auto subkeys = enumRegSubkeys(HKEY_LOCAL_MACHINE, uPath, 500);
            for (auto& sk : subkeys) {
                ++totalEntries;
                std::wstring fullKey = std::wstring(uPath) + L"\\" + sk;
                std::string installLoc = readRegString(
                    HKEY_LOCAL_MACHINE, fullKey.c_str(), L"InstallLocation");
                if (!installLoc.empty()) {
                    DWORD attrs = GetFileAttributesW(
                        utf8ToWstring(installLoc).c_str());
                    if (attrs == INVALID_FILE_ATTRIBUTES) {
                        ++invalid;
                        std::string displayName = readRegString(
                            HKEY_LOCAL_MACHINE, fullKey.c_str(), L"DisplayName");
                        if (invalid <= 20)
                            invalidEntries.push_back({
                                {"app", displayName.empty()
                                    ? wstringToUtf8(sk) : displayName},
                                {"install_location", installLoc}
                            });
                    }
                }
            }
        }
        totalInvalid += invalid;
        categoriesArr.push_back({
            {"name", "Uninstall"},
            {"total_entries", totalEntries},
            {"invalid_entries", invalid},
            {"sample_invalid", invalidEntries}
        });
    }

    // 5. Run keys
    {
        const std::pair<HKEY, const wchar_t*> runPaths[] = {
            {HKEY_CURRENT_USER,
             L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run"},
            {HKEY_CURRENT_USER,
             L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\RunOnce"},
            {HKEY_LOCAL_MACHINE,
             L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run"},
            {HKEY_LOCAL_MACHINE,
             L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\RunOnce"},
        };
        int totalEntries = 0;
        int invalid = 0;
        gaia::json invalidEntries = gaia::json::array();
        for (auto& [hRoot, rPath] : runPaths) {
            auto values = enumRegValues(hRoot, rPath, 200);
            for (auto& entry : values) {
                ++totalEntries;
                if (!entry.contains("value")) continue;
                std::string cmdLine = entry["value"].get<std::string>();
                std::string exePath;
                if (!cmdLine.empty() && cmdLine.front() == '"') {
                    auto endQuote = cmdLine.find('"', 1);
                    if (endQuote != std::string::npos)
                        exePath = cmdLine.substr(1, endQuote - 1);
                } else {
                    auto spPos = cmdLine.find(' ');
                    exePath = (spPos != std::string::npos)
                        ? cmdLine.substr(0, spPos) : cmdLine;
                }
                if (exePath.empty()) continue;
                if (!fileExistsW(utf8ToWstring(exePath))) {
                    ++invalid;
                    if (invalid <= 20)
                        invalidEntries.push_back({
                            {"name", entry.value("name", "")},
                            {"path", exePath}
                        });
                }
            }
        }
        totalInvalid += invalid;
        categoriesArr.push_back({
            {"name", "Run Keys"},
            {"total_entries", totalEntries},
            {"invalid_entries", invalid},
            {"sample_invalid", invalidEntries}
        });
    }

    // 6. Fonts
    {
        auto values = enumRegValues(HKEY_LOCAL_MACHINE,
            L"SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Fonts", 500);
        std::wstring fontsDir = getEnvWide(L"WINDIR");
        if (!fontsDir.empty()) fontsDir += L"\\Fonts\\";
        int invalid = 0;
        gaia::json invalidEntries = gaia::json::array();
        for (auto& entry : values) {
            if (!entry.contains("value")) continue;
            std::string fontFile = entry["value"].get<std::string>();
            if (fontFile.empty()) continue;
            std::wstring wFontFile = utf8ToWstring(fontFile);
            std::wstring fullPath;
            if (wFontFile.size() >= 2 && wFontFile[1] == L':') {
                fullPath = wFontFile;
            } else {
                fullPath = fontsDir + wFontFile;
            }
            if (!fileExistsW(fullPath)) {
                ++invalid;
                if (invalid <= 20)
                    invalidEntries.push_back({
                        {"font", entry.value("name", "")},
                        {"file", fontFile}
                    });
            }
        }
        totalInvalid += invalid;
        categoriesArr.push_back({
            {"name", "Fonts"},
            {"total_entries", values.size()},
            {"invalid_entries", invalid},
            {"sample_invalid", invalidEntries}
        });
    }

    // 7. Sound Events
    {
        auto subkeys = enumRegSubkeys(HKEY_CURRENT_USER,
            L"AppEvents\\Schemes\\Apps\\.Default", 200);
        int totalEntries = 0;
        int invalid = 0;
        gaia::json invalidEntries = gaia::json::array();
        for (auto& eventName : subkeys) {
            std::wstring currentKey =
                L"AppEvents\\Schemes\\Apps\\.Default\\" +
                eventName + L"\\.Current";
            std::string wavPath = readRegString(
                HKEY_CURRENT_USER, currentKey.c_str(), nullptr);
            if (wavPath.empty()) continue;
            ++totalEntries;
            if (!fileExistsW(utf8ToWstring(wavPath))) {
                ++invalid;
                if (invalid <= 10)
                    invalidEntries.push_back({
                        {"event", wstringToUtf8(eventName)},
                        {"path", wavPath}
                    });
            }
        }
        totalInvalid += invalid;
        categoriesArr.push_back({
            {"name", "Sound Events"},
            {"total_entries", totalEntries},
            {"invalid_entries", invalid},
            {"sample_invalid", invalidEntries}
        });
    }

    return {{"categories", categoriesArr},
            {"total_invalid", totalInvalid}};
}

// Find largest files on a given path using min-heap
static gaia::json findLargestFiles(const std::string& startPath = "C:\\",
                                    int topN = 20, int minSizeMb = 100) {
    if (!isSafePath(startPath)) {
        return {{"error", "Invalid path: " + startPath}};
    }

    uint64_t minBytes = static_cast<uint64_t>(minSizeMb) * 1024ULL * 1024ULL;
    std::wstring wStartPath = utf8ToWstring(startPath);

    using FileEntry = std::pair<uint64_t, std::wstring>;
    std::priority_queue<FileEntry, std::vector<FileEntry>,
                        std::greater<FileEntry>> topFiles;

    const std::vector<std::wstring> skipDirs = {
        L"$Recycle.Bin", L"System Volume Information",
        L"$WinREAgent", L"Recovery",
    };

    std::vector<std::pair<std::wstring, int>> dirStack;
    dirStack.push_back({wStartPath, 10});

    while (!dirStack.empty()) {
        auto [dir, depth] = dirStack.back();
        dirStack.pop_back();
        if (depth <= 0) continue;

        WIN32_FIND_DATAW fd;
        HANDLE hFind = FindFirstFileExW(
            (dir + L"\\*").c_str(), FindExInfoBasic, &fd,
            FindExSearchNameMatch, nullptr, FIND_FIRST_EX_LARGE_FETCH);
        if (hFind == INVALID_HANDLE_VALUE) continue;

        do {
            if (wcscmp(fd.cFileName, L".") == 0 ||
                wcscmp(fd.cFileName, L"..") == 0)
                continue;
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)
                continue;

            std::wstring fullPath = dir + L"\\" + fd.cFileName;
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
                bool skip = false;
                for (auto& sd : skipDirs) {
                    if (_wcsicmp(fd.cFileName, sd.c_str()) == 0) {
                        skip = true;
                        break;
                    }
                }
                if (!skip) dirStack.push_back({fullPath, depth - 1});
            } else {
                ULARGE_INTEGER fileSize;
                fileSize.HighPart = fd.nFileSizeHigh;
                fileSize.LowPart = fd.nFileSizeLow;
                uint64_t sz = fileSize.QuadPart;
                if (sz >= minBytes) {
                    topFiles.push({sz, fullPath});
                    if (static_cast<int>(topFiles.size()) > topN)
                        topFiles.pop();
                }
            }
        } while (FindNextFileW(hFind, &fd));
        FindClose(hFind);
    }

    std::vector<FileEntry> sorted;
    while (!topFiles.empty()) {
        sorted.push_back(topFiles.top());
        topFiles.pop();
    }
    std::sort(sorted.begin(), sorted.end(),
              [](const FileEntry& a, const FileEntry& b) {
                  return a.first > b.first;
              });

    gaia::json files = gaia::json::array();
    for (auto& [sz, path] : sorted) {
        files.push_back({
            {"path", wstringToUtf8(path)},
            {"size_bytes", sz},
            {"size_human", formatBytes(sz)},
        });
    }
    return {{"files", files}, {"file_count", files.size()}};
}

// Get startup programs from registry Run keys + scheduled tasks
static gaia::json getStartupPrograms() {
    gaia::json runKeys = gaia::json::array();
    const std::pair<HKEY, const wchar_t*> paths[] = {
        {HKEY_CURRENT_USER,
         L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run"},
        {HKEY_CURRENT_USER,
         L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\RunOnce"},
        {HKEY_LOCAL_MACHINE,
         L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run"},
        {HKEY_LOCAL_MACHINE,
         L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\RunOnce"},
    };
    const char* hiveNames[] = {
        "HKCU\\Run", "HKCU\\RunOnce", "HKLM\\Run", "HKLM\\RunOnce"
    };
    for (int i = 0; i < 4; ++i) {
        auto values = enumRegValues(paths[i].first, paths[i].second, 100);
        for (auto& entry : values) {
            gaia::json item;
            item["name"] = entry.value("name", "");
            item["command"] = entry.value("value", "");
            item["hive"] = hiveNames[i];
            runKeys.push_back(std::move(item));
        }
    }

    std::string taskCmd =
        "Get-ScheduledTask | Where-Object { $_.State -eq 'Ready' } | "
        "Select-Object -First 30 TaskName, TaskPath, State | "
        "ConvertTo-Json -Compress";
    std::string taskOutput = runShell(taskCmd);
    gaia::json scheduledTasks = gaia::json::array();
    try {
        auto parsed = gaia::json::parse(taskOutput);
        if (parsed.is_array()) scheduledTasks = parsed;
        else if (parsed.is_object()) scheduledTasks.push_back(parsed);
    } catch (...) {
        scheduledTasks = {{{"error", "Failed to parse scheduled tasks"}}};
    }

    return {{"run_keys", runKeys},
            {"run_key_count", runKeys.size()},
            {"scheduled_tasks", scheduledTasks},
            {"scheduled_task_count", scheduledTasks.size()}};
}

// Detect bloatware by comparing AppX packages against known list
static gaia::json getBloatwareInfo() {
    std::string cmd =
        "Get-AppxPackage | Select-Object Name, Publisher, Version | "
        "ConvertTo-Json -Compress";
    std::string output = runShell(cmd);

    gaia::json allPackages;
    try {
        allPackages = gaia::json::parse(output);
        if (!allPackages.is_array()) {
            if (allPackages.is_object()) {
                gaia::json arr = gaia::json::array();
                arr.push_back(allPackages);
                allPackages = arr;
            } else {
                return {{"error", "Failed to enumerate AppX packages"}};
            }
        }
    } catch (...) {
        return {{"error", "Failed to parse AppX package list"}};
    }

    gaia::json foundBloatware = gaia::json::array();
    for (auto& pkg : allPackages) {
        if (!pkg.contains("Name")) continue;
        std::string pkgName = pkg["Name"].get<std::string>();
        for (auto& bloat : kBloatwareList) {
            if (pkgName.find(bloat) != std::string::npos) {
                foundBloatware.push_back({
                    {"name", pkgName},
                    {"publisher", pkg.value("Publisher", "")},
                    {"version", pkg.value("Version", "")},
                });
                break;
            }
        }
    }

    return {{"found", foundBloatware},
            {"bloatware_count", foundBloatware.size()},
            {"total_packages_checked", allPackages.size()}};
}

// ---------------------------------------------------------------------------
// PCHealthAgent — LLM decides the diagnostic path based on user's question
// ---------------------------------------------------------------------------
class PCHealthAgent : public gaia::Agent {
public:
    explicit PCHealthAgent(const std::string& modelId)
        : Agent(makeConfig(modelId)) {
        setOutputHandler(std::make_unique<HealthConsole>());
        init();
    }

protected:
    std::string getSystemPrompt() const override {
        return R"prompt(You are an expert PC diagnostician running locally on AMD hardware via the GAIA framework. All processing stays on-device — zero data leaves the machine.

You diagnose system issues like a skilled technician: gather context first, form a hypothesis, then investigate specifically. Focus on WHY something is happening, not just WHAT the numbers say.

IMPORTANT: Be concise. Keep FINDING and DECISION to 1-2 sentences each. No filler words.

== DIAGNOSTIC APPROACH ==
YOU DECIDE what tools to use based on the user's question:
- Vague or broad questions ("why is my laptop slow?", "run a checkup"):
  Start with quick_health_scan() to get a system-wide snapshot.
  Then select deep-dive tools based on what you find.
- Specific questions ("why is my WiFi slow?", "my fan is loud"):
  Go directly to the relevant tool(s). Skip the quick scan.
- Full health checkup requests:
  Run quick_health_scan(), then ALL Tier 2 tools, then provide a grade.
- Action requests ("optimize for gaming", "switch to high performance"):
  Check current state first, explain what you will change, then act.

== REASONING PROTOCOL ==
After EVERY tool result, output exactly:
  FINDING: <what the data reveals — 1-2 sentences>
  DECISION: <what to investigate next or conclude — 1 sentence>

== TOOL TIERS ==
Tier 1 — Context Scan (always safe, fast):
  quick_health_scan — System snapshot: power, CPU, memory, disk, WiFi, uptime, event log summary

Tier 2 — Deep Dives (read-only, safe):
  scan_recent_logs(focus) — Windows Event Logs. focus: all, wifi, disk, crashes
  power_and_thermal_analysis — Power plan, CPU freq, thermal throttling, battery health
  process_analysis — Top processes by CPU/RAM, startup programs, background apps
  disk_and_registry_health — Storage breakdown, junk files, caches, registry health
  network_diagnostics — WiFi signal/speed, DNS latency, ping, VPN detection

Tier 3 — Actions (modifies system state):
  set_power_plan(plan) — Switch: balanced, high_performance, battery_saver
  optimize_for_gaming — High perf mode + game mode + identify background processes
  terminate_process(name) — Kill a running process by name. Only when user confirms.

== SAFETY ==
- Tier 1 and 2 tools are READ-ONLY and always safe to run.
- Tier 3 tools MODIFY system state. Only use when user explicitly asks for action.
- terminate_process is destructive — only use when user explicitly confirms they want to kill a process.
- Always report what was changed after running a Tier 3 tool.
- NEVER delete, modify, or move files yourself. Only report findings and recommendations.

== REASONING EXAMPLES ==
- CPU throttled + on battery -> "Power plan is limiting CPU to save battery"
- WiFi signal weak + adapter power saving -> "WiFi adapter in power-saving mode on battery"
- RAM at 90% + Chrome 40 processes -> "Chrome using most RAM across 40 tabs"
- 12 WiFi disconnects in logs -> "WiFi keeps dropping — 12 disconnects in 2 hours"
- High CPU load + loud fan -> check which process is driving CPU, check thermal throttling

== CROSS-CORRELATION ==
After gathering data, connect the dots:
- Link power state to performance (battery -> throttled CPU -> slow)
- Link disk usage to junk totals and large files
- Link memory pressure to top processes
- Link startup items to bloatware
- Explain how issues compound (e.g., battery + hotel WiFi + VPN = slow)

== FINAL ANSWER ==
Provide a clear diagnosis organized as:
- Root cause (if identified) — the single biggest factor
- Key findings from each area investigated
- Health grade (A-F) ONLY if a comprehensive checkup was performed

IMPORTANT: Do NOT include numbered recommendations or action items in the diagnosis body.
All actionable recommendations go in the NEXT_STEPS section below — that is the ONLY
place where the user sees things they can act on. The diagnosis should explain what you
found, not what to do about it.

Grade criteria (when applicable):
  A — Excellent: Low resource usage, clean system, no issues
  B — Good: Minor items, generally healthy
  C — Fair: Noticeable issues, some cleanup needed
  D — Poor: Significant problems, action recommended
  F — Critical: System in trouble, urgent attention needed

If assigning a grade, your final answer MUST begin with "GRADE: X" on the first line.

== SUGGESTED NEXT STEPS ==
After your diagnosis, suggest 2-4 practical tips the user can act on. Add at the END of your answer:

NEXT_STEPS:
- <direct instruction or observation with context>

Rules:
- Each tip is a clear, direct statement — not a question, not an offer to help
- Use imperative language: "Terminate X", "Close Y", "Switch to Z"
- Include context: why and what benefit (e.g., "— frees ~2 GB of RAM")
- Name specific processes, apps, or settings — not vague advice
- Only include NEXT_STEPS when you found actionable issues
- Omit NEXT_STEPS if the system is healthy

Example:
NEXT_STEPS:
- Terminate llama-server.exe if not in use — it is consuming 4.2 GB of RAM
- Typeless and iCloud Photos are running in the background — close them if unneeded
- Switch to Balanced power plan to reduce fan noise and heat

== PERSONALITY ==
Calm, knowledgeable, never alarmist. Like a good mechanic who explains what they found and what to do about it, in plain language.)prompt";
    }

    void registerTools() override {
        // ==================================================================
        // TIER 1: quick_health_scan — System snapshot for context
        // ==================================================================
        toolRegistry().registerTool(
            "quick_health_scan",
            "Fast system snapshot: power source/plan, battery %, CPU load and "
            "throttle status, memory usage %, disk free % per drive, WiFi "
            "signal/speed, uptime, and event log error counts from last 24h. "
            "Start here for vague questions to understand the full context.",
            [](const gaia::json& /*args*/) -> gaia::json {
                gaia::json result;
                result["tool"] = "quick_health_scan";

                // Disk info via Win32 (instant)
                result["disk"] = getDiskUsageInfo();

                // Memory info via Win32 (instant)
                result["memory"] = getMemoryInfo();

                // Power, CPU, battery, uptime, WiFi, event log counts via PS
                std::string psCmd =
                    "$o=@{}; "
                    "$o.plan=(powercfg /getactivescheme) -replace '.*\\((.+)\\).*','$1'; "
                    "$b=Get-CimInstance Win32_Battery -EA 0; "
                    "if($b){$o.bat=@{pct=$b.EstimatedChargeRemaining;"
                    "charging=$($b.BatteryStatus -eq 2)}}else{$o.bat=$null}; "
                    "$c=Get-CimInstance Win32_Processor; "
                    "$o.cpu=@{load=$c.LoadPercentage;name=[string]$c.Name;"
                    "curMHz=$c.CurrentClockSpeed;maxMHz=$c.MaxClockSpeed}; "
                    "$os=Get-CimInstance Win32_OperatingSystem; "
                    "$o.upHrs=[math]::Round(((Get-Date)-$os.LastBootUpTime).TotalHours,1); "
                    "$w=netsh wlan show interfaces 2>$null; "
                    "$o.wifi=@{}; "
                    "$m=$w|Select-String 'Signal\\s*:\\s*(\\d+)%'; "
                    "if($m){$o.wifi.signal=[int]$m.Matches.Groups[1].Value}; "
                    "$m=$w|Select-String 'Receive rate.*:\\s*(\\S+)'; "
                    "if($m){$o.wifi.speed=$m.Matches.Groups[1].Value}; "
                    "$m=$w|Select-String '\\bSSID\\s*:\\s*(.+)'; "
                    "if($m){$o.wifi.ssid=$m.Matches.Groups[1].Value.Trim()}; "
                    "$o.logs=@{"
                    "sysErr=(Get-WinEvent -FilterHashtable @{LogName='System';"
                    "Level=@(1,2);StartTime=(Get-Date).AddHours(-24)} -EA 0|Measure-Object).Count;"
                    "appErr=(Get-WinEvent -FilterHashtable @{LogName='Application';"
                    "Level=@(1,2);StartTime=(Get-Date).AddHours(-24)} -EA 0|Measure-Object).Count}; "
                    "$o|ConvertTo-Json -Depth 3 -Compress";

                auto psData = parsePsJson(runShell(psCmd));
                if (!psData.contains("error")) {
                    result["power_plan"] = psData.value("plan", "Unknown");
                    result["battery"] = psData.value("bat", gaia::json(nullptr));
                    result["cpu"] = psData.value("cpu", gaia::json::object());
                    result["uptime_hours"] = psData.value("upHrs", 0.0);
                    result["wifi"] = psData.value("wifi", gaia::json::object());
                    result["event_log_24h"] = psData.value("logs", gaia::json::object());
                } else {
                    result["powershell_error"] = psData;
                }

                return result;
            }, {}, true );  // atomic=true for fast context scan

        // ==================================================================
        // TIER 2: scan_recent_logs — Deep dive into Windows Event Logs
        // ==================================================================
        toolRegistry().registerTool(
            "scan_recent_logs",
            "Scan Windows Event Logs from the last 2 hours. The 'focus' parameter "
            "targets specific log sources: 'all' (System + Application errors), "
            "'wifi' (WLAN disconnect/reconnect events), 'disk' (storage errors), "
            "'crashes' (blue screens, unexpected shutdowns). Stats tell you WHAT, "
            "logs tell you WHY.",
            [](const gaia::json& args) -> gaia::json {
                std::string focus = args.value("focus", "all");

                // Whitelist focus values
                if (focus != "all" && focus != "wifi" &&
                    focus != "disk" && focus != "crashes") {
                    return {{"error", "Invalid focus. Use: all, wifi, disk, crashes"}};
                }

                std::string psCmd;
                if (focus == "wifi") {
                    psCmd =
                        "$evts=Get-WinEvent -ProviderName "
                        "'Microsoft-Windows-WLAN-AutoConfig' "
                        "-MaxEvents 50 -EA 0 | "
                        "Where-Object { $_.TimeCreated -gt (Get-Date).AddHours(-2) } | "
                        "Select-Object -First 30 TimeCreated,Id,LevelDisplayName,Message; "
                        "$r=@($evts|ForEach-Object{@{time=$_.TimeCreated.ToString('HH:mm:ss');"
                        "id=$_.Id;level=$_.LevelDisplayName;"
                        "msg=$_.Message.Substring(0,[Math]::Min(200,$_.Message.Length))}});"
                        "@{focus='wifi';events=$r;count=$r.Count}|ConvertTo-Json -Depth 3 -Compress";
                } else if (focus == "disk") {
                    psCmd =
                        "$evts=Get-WinEvent -FilterHashtable @{LogName='System';"
                        "ProviderName='disk','Ntfs','volmgr','volsnap';"
                        "StartTime=(Get-Date).AddHours(-2)} -MaxEvents 30 -EA 0; "
                        "$r=@($evts|ForEach-Object{@{time=$_.TimeCreated.ToString('HH:mm:ss');"
                        "id=$_.Id;level=$_.LevelDisplayName;"
                        "msg=$_.Message.Substring(0,[Math]::Min(200,$_.Message.Length))}});"
                        "@{focus='disk';events=$r;count=$r.Count}|ConvertTo-Json -Depth 3 -Compress";
                } else if (focus == "crashes") {
                    psCmd =
                        "$evts=Get-WinEvent -FilterHashtable @{LogName='System';"
                        "Id=41,1001,6008;StartTime=(Get-Date).AddHours(-24)} "
                        "-MaxEvents 20 -EA 0; "
                        "$r=@($evts|ForEach-Object{@{time=$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss');"
                        "id=$_.Id;level=$_.LevelDisplayName;"
                        "msg=$_.Message.Substring(0,[Math]::Min(200,$_.Message.Length))}});"
                        "@{focus='crashes';events=$r;count=$r.Count}|ConvertTo-Json -Depth 3 -Compress";
                } else {
                    // "all" — System + Application errors/warnings
                    psCmd =
                        "$evts=@(); "
                        "$evts+=Get-WinEvent -FilterHashtable @{LogName='System';"
                        "Level=@(1,2,3);StartTime=(Get-Date).AddHours(-2)} "
                        "-MaxEvents 25 -EA 0; "
                        "$evts+=Get-WinEvent -FilterHashtable @{LogName='Application';"
                        "Level=@(1,2,3);StartTime=(Get-Date).AddHours(-2)} "
                        "-MaxEvents 25 -EA 0; "
                        "$r=@($evts|Sort-Object TimeCreated -Descending|Select-Object -First 50|"
                        "ForEach-Object{@{time=$_.TimeCreated.ToString('HH:mm:ss');"
                        "id=$_.Id;level=$_.LevelDisplayName;log=$_.LogName;"
                        "msg=$_.Message.Substring(0,[Math]::Min(200,$_.Message.Length))}});"
                        "@{focus='all';events=$r;count=$r.Count}|ConvertTo-Json -Depth 3 -Compress";
                }

                auto psData = parsePsJson(runShell(psCmd));
                psData["tool"] = "scan_recent_logs";
                return psData;
            },
            {{"focus", gaia::ToolParamType::STRING, false,
              "Log focus: all, wifi, disk, crashes (default: all)"}});

        // ==================================================================
        // TIER 2: power_and_thermal_analysis — Power plan + thermals
        // ==================================================================
        toolRegistry().registerTool(
            "power_and_thermal_analysis",
            "Deep dive into power management: active power plan, CPU frequency "
            "vs maximum (throttle detection), thermal zone temperature, battery "
            "health and charge status. Use when investigating slow performance "
            "or overheating.",
            [](const gaia::json& /*args*/) -> gaia::json {
                std::string psCmd =
                    "$o=@{}; "
                    "$o.plan=(powercfg /getactivescheme) -replace '.*\\((.+)\\).*','$1'; "
                    "$c=Get-CimInstance Win32_Processor; "
                    "$o.cpu=@{curMHz=$c.CurrentClockSpeed;maxMHz=$c.MaxClockSpeed;"
                    "freqPct=[math]::Round($c.CurrentClockSpeed/$c.MaxClockSpeed*100);"
                    "load=$c.LoadPercentage}; "
                    "try{$t=Get-CimInstance -Namespace root/wmi "
                    "-ClassName MSAcpi_ThermalZoneTemperature -EA Stop; "
                    "$o.thermal=@{tempC=[math]::Round(($t[0].CurrentTemperature-2732)/10,1);"
                    "critC=[math]::Round(($t[0].CriticalTripPoint-2732)/10,1)}}"
                    "catch{$o.thermal=@{error='Requires admin or not supported'}}; "
                    "$b=Get-CimInstance Win32_Battery -EA 0; "
                    "if($b){$o.battery=@{pct=$b.EstimatedChargeRemaining;"
                    "status=$b.BatteryStatus;estMin=$b.EstimatedRunTime}}; "
                    "try{$perf=(Get-Counter "
                    "'\\Processor Information(_Total)\\% Processor Performance' "
                    "-EA Stop).CounterSamples[0].CookedValue; "
                    "$o.throttled=$perf -lt 90;"
                    "$o.perfPct=[math]::Round($perf)}"
                    "catch{$o.throttled=$null}; "
                    "$o|ConvertTo-Json -Depth 3 -Compress";

                auto psData = parsePsJson(runShell(psCmd));
                psData["tool"] = "power_and_thermal_analysis";
                return psData;
            }, {} );

        // ==================================================================
        // TIER 2: process_analysis — Top processes + startup programs
        // ==================================================================
        toolRegistry().registerTool(
            "process_analysis",
            "Analyze running processes: top 10 by memory, top 10 by CPU time, "
            "startup programs with registry location, scheduled tasks, background "
            "process count. Detects runaway processes using excessive resources.",
            [](const gaia::json& /*args*/) -> gaia::json {
                gaia::json result;
                result["tool"] = "process_analysis";

                // Top processes by memory (Win32 API)
                result["top_by_memory"] = getTopProcesses(10);

                // System memory stats
                result["memory"] = getMemoryInfo();

                // Startup programs (registry + scheduled tasks)
                result["startup"] = getStartupPrograms();

                // Top by CPU time + background count via PowerShell
                std::string psCmd =
                    "$procs=Get-Process|Sort-Object CPU -Descending|"
                    "Select-Object -First 10 Name,Id,"
                    "@{N='CpuSec';E={[math]::Round($_.CPU,1)}},"
                    "@{N='MemMB';E={[math]::Round($_.WorkingSet64/1MB)}}; "
                    "$bg=(Get-Process|Where-Object{$_.MainWindowHandle -eq 0}).Count; "
                    "@{topCpu=@($procs|ForEach-Object{@{name=$_.Name;pid=$_.Id;"
                    "cpuSec=$_.CpuSec;memMB=$_.MemMB}});"
                    "backgroundCount=$bg}|ConvertTo-Json -Depth 3 -Compress";
                auto psData = parsePsJson(runShell(psCmd));
                if (!psData.contains("error")) {
                    result["top_by_cpu"] = psData.value("topCpu", gaia::json::array());
                    result["background_count"] = psData.value("backgroundCount", 0);
                }

                return result;
            }, {} );

        // ==================================================================
        // TIER 2: disk_and_registry_health — Storage + registry deep dive
        // ==================================================================
        toolRegistry().registerTool(
            "disk_and_registry_health",
            "Comprehensive storage and registry analysis: disk usage per drive, "
            "junk files across 11 categories, browser cache sizes, top 10 largest "
            "files over 50 MB, registry health across 7 categories, and bloatware "
            "detection. Use when investigating disk space or system cleanup.",
            [](const gaia::json& /*args*/) -> gaia::json {
                gaia::json result;
                result["tool"] = "disk_and_registry_health";

                // Disk overview
                result["drives"] = getDiskUsageInfo();

                // Junk files
                auto junk = scanJunkCategories();
                result["junk_files"] = junk["categories"];
                result["junk_total_human"] = junk["grand_total_human"];
                result["junk_total_bytes"] = junk["grand_total_bytes"];

                // Browser caches
                auto browser = scanBrowserCaches();
                result["browser_caches"] = browser["browsers"];
                result["browser_total_human"] = browser["grand_total_human"];

                // Top 10 largest files over 50 MB
                auto large = findLargestFiles("C:\\", 10, 50);
                result["large_files"] = large["files"];

                // Registry health
                auto registry = scanRegistryHealth();
                result["registry"] = registry["categories"];
                result["registry_total_invalid"] = registry["total_invalid"];

                // Bloatware
                result["bloatware"] = getBloatwareInfo();

                return result;
            }, {} );

        // ==================================================================
        // TIER 2: network_diagnostics — WiFi, DNS, latency, VPN
        // ==================================================================
        toolRegistry().registerTool(
            "network_diagnostics",
            "Network deep dive: WiFi adapter details (signal, speed, channel, "
            "radio type, power-saving mode), DNS response time, ping latency "
            "to 8.8.8.8, VPN adapter detection, default gateway. Use when "
            "investigating slow WiFi or connectivity issues.",
            [](const gaia::json& /*args*/) -> gaia::json {
                std::string psCmd =
                    "$o=@{}; "
                    "$w=netsh wlan show interfaces 2>$null; "
                    "$o.wifi=@{}; "
                    "$m=$w|Select-String 'Signal\\s*:\\s*(\\d+)%'; "
                    "if($m){$o.wifi.signal=[int]$m.Matches.Groups[1].Value}; "
                    "$m=$w|Select-String 'Receive rate.*:\\s*(\\S+)'; "
                    "if($m){$o.wifi.speedMbps=$m.Matches.Groups[1].Value}; "
                    "$m=$w|Select-String 'Channel\\s*:\\s*(\\d+)'; "
                    "if($m){$o.wifi.channel=[int]$m.Matches.Groups[1].Value}; "
                    "$m=$w|Select-String 'Radio type\\s*:\\s*(.+)'; "
                    "if($m){$o.wifi.radio=$m.Matches.Groups[1].Value.Trim()}; "
                    "$m=$w|Select-String 'State\\s*:\\s*(.+)'; "
                    "if($m){$o.wifi.state=$m.Matches.Groups[1].Value.Trim()}; "
                    "$m=$w|Select-String '\\bSSID\\s*:\\s*(.+)'; "
                    "if($m){$o.wifi.ssid=$m.Matches.Groups[1].Value.Trim()}; "
                    "try{$o.dnsMs=[math]::Round((Measure-Command{"
                    "Resolve-DnsName google.com -EA Stop}).TotalMilliseconds)}"
                    "catch{$o.dnsMs=-1}; "
                    "$p=Test-Connection 8.8.8.8 -Count 3 -EA 0; "
                    "if($p){$o.ping=@{avgMs=[math]::Round(($p|Measure-Object "
                    "ResponseTime -Average).Average);loss=3-$p.Count}}"
                    "else{$o.ping=@{error='unreachable'}}; "
                    "$vpn=Get-NetAdapter|Where-Object{"
                    "$_.InterfaceDescription -match 'VPN|TAP|WireGuard|Tunnel'}; "
                    "$o.vpnActive=($vpn|Where-Object{$_.Status -eq 'Up'}).Count -gt 0; "
                    "$gw=(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -EA 0).NextHop; "
                    "$o.gateway=$gw; "
                    "$o|ConvertTo-Json -Depth 3 -Compress";

                auto psData = parsePsJson(runShell(psCmd));
                psData["tool"] = "network_diagnostics";
                return psData;
            }, {} );

        // ==================================================================
        // TIER 3: set_power_plan — Switch power plan (action)
        // ==================================================================
        toolRegistry().registerTool(
            "set_power_plan",
            "Switch the Windows power plan. Options: 'balanced', "
            "'high_performance', 'battery_saver'. Reports the active plan "
            "after switching. Only use when the user explicitly requests it.",
            [](const gaia::json& args) -> gaia::json {
                std::string plan = args.value("plan", "");

                // Map friendly names to well-known GUIDs
                std::string guid;
                if (plan == "balanced")
                    guid = "381b4222-f694-41f0-9685-ff5bb260df2e";
                else if (plan == "high_performance")
                    guid = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c";
                else if (plan == "battery_saver")
                    guid = "a1841308-3541-4fab-bc81-f71556f20b4a";
                else
                    return {{"error", "Invalid plan. Use: balanced, "
                             "high_performance, battery_saver"}};

                if (!isSafeShellArg(guid)) {
                    return {{"error", "Invalid GUID"}};
                }

                std::string psCmd =
                    "powercfg /setactive " + guid + "; "
                    "$active=(powercfg /getactivescheme) -replace '.*\\((.+)\\).*','$1'; "
                    "@{status='completed';plan=$active;requested='" + plan + "'}|"
                    "ConvertTo-Json -Compress";

                auto psData = parsePsJson(runShell(psCmd));
                psData["tool"] = "set_power_plan";
                return psData;
            },
            {{"plan", gaia::ToolParamType::STRING, true,
              "Power plan: balanced, high_performance, battery_saver"}});

        // ==================================================================
        // TIER 3: optimize_for_gaming — Combined gaming optimization (action)
        // ==================================================================
        toolRegistry().registerTool(
            "optimize_for_gaming",
            "Gaming optimization: sets High Performance power plan, enables Game "
            "Mode, checks GPU driver info, lists top memory/CPU consumers that "
            "could be closed. Only use when user explicitly requests gaming "
            "optimization.",
            [](const gaia::json& /*args*/) -> gaia::json {
                gaia::json result;
                result["tool"] = "optimize_for_gaming";

                // 1. Set high performance + enable game mode + GPU info
                std::string psCmd =
                    "powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c; "
                    "$o=@{}; "
                    "$o.powerPlan=(powercfg /getactivescheme) "
                    "-replace '.*\\((.+)\\).*','$1'; "
                    "Set-ItemProperty -Path "
                    "'HKCU:\\Software\\Microsoft\\GameBar' "
                    "-Name 'AutoGameModeEnabled' -Value 1 "
                    "-Type DWord -EA 0; "
                    "$o.gameMode='enabled'; "
                    "$gpu=Get-CimInstance Win32_VideoController|"
                    "Select-Object Name,DriverVersion,DriverDate; "
                    "$o.gpu=@($gpu|ForEach-Object{@{name=$_.Name;"
                    "driver=$_.DriverVersion;"
                    "date=$_.DriverDate.ToString('yyyy-MM-dd')}});"
                    "$o|ConvertTo-Json -Depth 3 -Compress";

                auto psData = parsePsJson(runShell(psCmd));
                if (!psData.contains("error")) {
                    result["power_plan"] = psData.value("powerPlan", "Unknown");
                    result["game_mode"] = psData.value("gameMode", "unknown");
                    result["gpu"] = psData.value("gpu", gaia::json::array());
                } else {
                    result["powershell_error"] = psData;
                }

                // 2. Top processes (candidates to close)
                result["top_processes"] = getTopProcesses(10);

                // 3. Current memory state
                result["memory"] = getMemoryInfo();

                return result;
            }, {} );

        // ==================================================================
        // TIER 3: terminate_process — Kill a process by name (action)
        // ==================================================================
        toolRegistry().registerTool(
            "terminate_process",
            "Terminate a running process by name. Reports how many instances "
            "were found and killed, and memory freed. Only use when the user "
            "explicitly requests process termination.",
            [](const gaia::json& args) -> gaia::json {
                std::string name = args.value("name", "");
                if (name.empty()) {
                    return {{"error", "Process name is required"},
                            {"tool", "terminate_process"}};
                }

                // Validate: only alphanumeric, dots, hyphens, underscores
                for (char c : name) {
                    if (!std::isalnum(static_cast<unsigned char>(c)) &&
                        c != '.' && c != '-' && c != '_') {
                        return {{"error", "Invalid process name: " + name},
                                {"tool", "terminate_process"}};
                    }
                }

                // Ensure .exe suffix
                std::string target = name;
                {
                    std::string lower = target;
                    for (auto& ch : lower)
                        ch = static_cast<char>(
                            std::tolower(static_cast<unsigned char>(ch)));
                    if (lower.size() < 4 ||
                        lower.substr(lower.size() - 4) != ".exe") {
                        target += ".exe";
                    }
                }

                DWORD selfPid = GetCurrentProcessId();

                HANDLE snapshot = CreateToolhelp32Snapshot(
                    TH32CS_SNAPPROCESS, 0);
                if (snapshot == INVALID_HANDLE_VALUE) {
                    return {{"error", "Failed to create process snapshot"},
                            {"tool", "terminate_process"}};
                }

                struct MatchInfo { DWORD pid; uint64_t memBytes; };
                std::vector<MatchInfo> matches;

                PROCESSENTRY32W pe;
                pe.dwSize = sizeof(pe);
                if (Process32FirstW(snapshot, &pe)) {
                    do {
                        std::string exeName = wstringToUtf8(pe.szExeFile);
                        if (_stricmp(exeName.c_str(), target.c_str()) == 0 &&
                            pe.th32ProcessID != selfPid) {
                            uint64_t mem = 0;
                            HANDLE hProc = OpenProcess(
                                PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
                                FALSE, pe.th32ProcessID);
                            if (hProc) {
                                PROCESS_MEMORY_COUNTERS pmc;
                                if (K32GetProcessMemoryInfo(
                                        hProc, &pmc, sizeof(pmc))) {
                                    mem = pmc.WorkingSetSize;
                                }
                                CloseHandle(hProc);
                            }
                            matches.push_back({pe.th32ProcessID, mem});
                        }
                    } while (Process32NextW(snapshot, &pe));
                }
                CloseHandle(snapshot);

                if (matches.empty()) {
                    return {{"tool", "terminate_process"},
                            {"process", name},
                            {"error", "Process not found: " + name}};
                }

                int terminated = 0;
                int failed = 0;
                uint64_t totalFreed = 0;

                for (auto& m : matches) {
                    HANDLE hProc = OpenProcess(
                        PROCESS_TERMINATE, FALSE, m.pid);
                    if (hProc) {
                        if (TerminateProcess(hProc, 1)) {
                            ++terminated;
                            totalFreed += m.memBytes;
                        } else {
                            ++failed;
                        }
                        CloseHandle(hProc);
                    } else {
                        ++failed;
                    }
                }

                return {
                    {"tool", "terminate_process"},
                    {"process", name},
                    {"instances_found", matches.size()},
                    {"terminated", terminated},
                    {"failed", failed},
                    {"memory_freed_bytes", totalFreed},
                    {"memory_freed_human", formatBytes(totalFreed)},
                    {"status", failed == 0 ? "completed" : "partial"}
                };
            },
            {{"name", gaia::ToolParamType::STRING, true,
              "Process name to terminate (e.g., 'chrome.exe')"}});
    }

private:
    static gaia::AgentConfig makeConfig(const std::string& modelId) {
        gaia::AgentConfig config;
        config.maxSteps = 25;
        config.contextSize = 32768;
        config.modelId = modelId;
        return config;
    }
};

// ---------------------------------------------------------------------------
// Health scan menu — maps numbered selections to pre-written prompts
// ---------------------------------------------------------------------------
static const std::pair<std::string, std::string> kHealthMenu[] = {
    {"Why is my laptop slow?",
     "My laptop feels slow. Start with a quick health scan to understand the "
     "overall system state, then investigate the most likely cause based on "
     "what you find."},
    {"Run a full health checkup",
     "Run a comprehensive health checkup of this PC. Start with a quick health "
     "scan, then run all deep-dive diagnostics (processes, disk and registry, "
     "power and thermal, network). Provide a complete diagnosis with a health "
     "grade A-F and prioritized recommendations."},
    {"Optimize for gaming",
     "I want to optimize this PC for gaming. Check the current system state, "
     "then run the gaming optimization to set high performance mode, enable "
     "game mode, and identify background processes that could be closed."},
    {"Why is my WiFi slow?",
     "My WiFi connection is slow. Run network diagnostics to check signal "
     "strength, DNS response time, and latency. Also check recent WiFi-related "
     "event logs for disconnect patterns."},
    {"My fan is loud / laptop is hot",
     "My fan is running loud and my laptop feels hot. Run power and thermal "
     "analysis to check CPU temperature, throttling, and power plan. Also "
     "check which processes are using the most CPU."},
    {"What's eating my disk space?",
     "I'm running out of disk space. Run disk and registry health to get a "
     "full breakdown of storage usage including junk files, browser caches, "
     "and the largest unnecessary files."},
    {"What's using all my memory?",
     "My system memory usage is very high. Run process analysis to identify "
     "the top memory consumers, detect runaway processes, and review startup "
     "programs."},
};
static constexpr size_t kMenuSize = sizeof(kHealthMenu) / sizeof(kHealthMenu[0]);

static void printStandardMenuItems() {
    for (size_t i = 0; i < kMenuSize; ++i) {
        std::cout << color::YELLOW << "  [" << (i + 1) << "] "
                  << color::RESET << color::WHITE
                  << kHealthMenu[i].first
                  << color::RESET << std::endl;
    }
}

static void printHealthMenu() {
    std::cout << color::CYAN
              << "  ========================================================================================"
              << color::RESET << std::endl;
    std::cout << color::BOLD << "  What can I help with?"
              << color::RESET << std::endl;
    std::cout << std::endl;
    printStandardMenuItems();
    std::cout << color::CYAN
              << "  ========================================================================================"
              << color::RESET << std::endl;
    std::cout << color::GRAY
              << "  Or describe your problem in your own words. Type 'quit' to exit."
              << color::RESET << std::endl;
    std::cout << std::endl;
}

static void printPostDiagnosisMenu(const std::vector<NextStep>& steps) {
    std::cout << std::endl;
    std::cout << color::CYAN
              << "  ========================================================================================"
              << color::RESET << std::endl;
    std::cout << color::BOLD << "  Tips:"
              << color::RESET << std::endl;
    std::cout << std::endl;

    for (const auto& step : steps) {
        std::cout << color::GREEN << "    - " << color::RESET
                  << step.text << std::endl;
    }

    std::cout << std::endl;
    std::cout << color::BOLD << "  What next?"
              << color::RESET << std::endl;
    std::cout << std::endl;
    printStandardMenuItems();
    std::cout << color::CYAN
              << "  ========================================================================================"
              << color::RESET << std::endl;
    std::cout << color::GRAY
              << "  Or describe your problem in your own words. Type 'quit' to exit."
              << color::RESET << std::endl;
    std::cout << std::endl;
}

// ---------------------------------------------------------------------------
// main — model selection + interactive loop with health scan menu
// ---------------------------------------------------------------------------
int main() {
    try {
        // --- Admin check ---
#ifdef _WIN32
        {
            bool isAdmin = false;
            HANDLE token = nullptr;
            if (OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
                TOKEN_ELEVATION elevation{};
                DWORD size = sizeof(elevation);
                if (GetTokenInformation(token, TokenElevation, &elevation,
                                        sizeof(elevation), &size)) {
                    isAdmin = elevation.TokenIsElevated != 0;
                }
                CloseHandle(token);
            }
            if (!isAdmin) {
                std::cout << std::endl;
                std::cout << color::YELLOW << color::BOLD
                          << "  WARNING: " << color::RESET
                          << color::YELLOW
                          << "Not running as admin."
                          << color::RESET << std::endl;
                std::cout << color::GRAY
                          << "  Some system directories may"
                          << std::endl
                          << "  not be accessible. Right-click"
                          << std::endl
                          << "  your terminal -> Run as"
                          << std::endl
                          << "  administrator for full access."
                          << color::RESET << std::endl;
            }
        }
#endif

        // --- Banner ---
        std::cout << std::endl;
        std::cout << color::CYAN << color::BOLD
                  << "  ========================================================================================"
                  << color::RESET << std::endl;
        std::cout << color::CYAN << color::BOLD
                  << "   PC Health Agent  |  GAIA C++ Agent Framework  |  Local Inference"
                  << color::RESET << std::endl;
        std::cout << color::CYAN << color::BOLD
                  << "  ========================================================================================"
                  << color::RESET << std::endl;

        // --- Model selection ---
        std::cout << std::endl;
        std::cout << color::BOLD << "  Select inference backend:"
                  << color::RESET << std::endl;
        std::cout << color::YELLOW << "  [1] " << color::RESET
                  << color::GREEN << "GPU" << color::RESET
                  << color::GRAY << "  - Qwen3-4B-Instruct-2507-GGUF"
                  << color::RESET << std::endl;
        std::cout << color::YELLOW << "  [2] " << color::RESET
                  << color::MAGENTA << "NPU" << color::RESET
                  << color::GRAY << "  - Qwen3-4B-Instruct-2507-FLM"
                  << color::RESET << std::endl;
        std::cout << std::endl;
        std::cout << color::BOLD << "  > " << color::RESET << std::flush;

        std::string modelChoice;
        std::getline(std::cin, modelChoice);

        std::string modelId;
        if (modelChoice == "2") {
            modelId = "Qwen3-4B-Instruct-2507-FLM";
            std::cout << color::MAGENTA << "  Using NPU backend: "
                      << color::BOLD << modelId << color::RESET << std::endl;
        } else {
            modelId = "Qwen3-4B-Instruct-2507-GGUF";
            std::cout << color::GREEN << "  Using GPU backend: "
                      << color::BOLD << modelId << color::RESET << std::endl;
        }

        PCHealthAgent agent(modelId);

        std::cout << std::endl;
        std::cout << color::GREEN << color::BOLD << "  Ready!"
                  << color::RESET << std::endl;
        std::cout << std::endl;

        // --- Interactive loop with health scan menu ---
        std::string userInput;
        std::vector<NextStep> pendingTips;

        while (true) {
            if (!pendingTips.empty()) {
                printPostDiagnosisMenu(pendingTips);
            } else {
                printHealthMenu();
            }
            std::cout << color::BOLD << "  > " << color::RESET << std::flush;
            std::getline(std::cin, userInput);

            if (userInput.empty()) continue;
            if (userInput == "quit" || userInput == "exit" || userInput == "q")
                break;

            std::string query;
            bool isNewTopic = false;

            // Numbered menu selection [1]-[7] → new topic
            if (userInput.size() == 1 && userInput[0] >= '1' &&
                userInput[0] <= '0' + static_cast<char>(kMenuSize)) {
                size_t idx = static_cast<size_t>(userInput[0] - '1');
                query = kHealthMenu[idx].second;
                isNewTopic = true;
                std::cout << color::CYAN << "  > "
                          << kHealthMenu[idx].first
                          << color::RESET << std::endl;
            }
            // Free-form text → follow-up
            else {
                query = userInput;
            }

            // New topics start with a clean slate
            if (isNewTopic) {
                agent.clearHistory();
            }

            auto result = agent.processQuery(query);

            // Parse NEXT_STEPS from diagnosis for tips display
            std::string answer = result.value("result", "");
            auto parsed = parseNextSteps(answer);
            pendingTips = std::move(parsed.nextSteps);
        }

        std::cout << std::endl;
        std::cout << color::GRAY << "  Goodbye!" << color::RESET << std::endl;

    } catch (const std::exception& e) {
        std::cerr << color::RED << color::BOLD << "Fatal error: "
                  << color::RESET << color::RED << e.what()
                  << color::RESET << std::endl;
        return 1;
    }

    return 0;
}
