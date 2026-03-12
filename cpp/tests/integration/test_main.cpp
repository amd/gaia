// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Custom main() for GAIA C++ integration tests.
// Provides an interactive menu and CLI flags to select test sections.
//
// Interactive menu (no args):
//   tests_integration.exe
//
// CLI flags (for CI / AI assistants):
//   tests_integration.exe --llm
//   tests_integration.exe --mcp
//   tests_integration.exe --wifi
//   tests_integration.exe --health
//   tests_integration.exe --all
//   tests_integration.exe --model Qwen3-4B-Instruct-2507-GGUF
//   tests_integration.exe --url http://localhost:8000/api/v1
//
// GTest passthrough (suppresses menu):
//   tests_integration.exe --gtest_filter=IntegrationMCP*
//   tests_integration.exe --gtest_list_tests

#include <gtest/gtest.h>
#include <gaia/clean_console.h>

#include <cstdlib>
#include <iostream>
#include <string>
#include <utility>
#include <vector>

// Reuse gaia::color from clean_console.h — same colors as wifi/health agents
namespace color = gaia::color;

// ---------------------------------------------------------------------------
// GTest filter patterns for each section
// ---------------------------------------------------------------------------
static const char* kFilterLLM    = "LLMIntegrationTest.*";
static const char* kFilterMCP    = "IntegrationMCP.*";
static const char* kFilterWiFi   = "IntegrationWiFi.*";
static const char* kFilterHealth = "IntegrationHealth*.*";
static const char* kFilterAll    = "*";

// ---------------------------------------------------------------------------
// Menu items — same pattern as wifi_agent kDiagnosticMenu / health_agent kHealthMenu
// Each entry: { label, description, gtest filter }
// ---------------------------------------------------------------------------
struct MenuItem {
    const char* label;
    const char* description;
    const char* filter;
};

static const MenuItem kTestMenu[] = {
    {"LLM tests",    "basic chat, tool calling, multi-step, system prompt  (5 tests)",  kFilterLLM},
    {"MCP tests",    "connection, tool discovery, reconnect, prompt rebuild  (5 tests)", kFilterMCP},
    {"WiFi tests",   "real PowerShell diagnostics + LLM reasoning  (4 tests)",          kFilterWiFi},
    {"Health tests", "LLM + MCP + real PowerShell system health  (3 tests)",            kFilterHealth},
    {"All tests",    "run everything  (17 tests)",                                      kFilterAll},
};
static constexpr size_t kMenuSize = sizeof(kTestMenu) / sizeof(kTestMenu[0]);

// ---------------------------------------------------------------------------
// Windows-safe setenv (works with both MSVC and MinGW)
// ---------------------------------------------------------------------------
static void setEnvVar(const char* name, const std::string& value) {
#ifdef _WIN32
    _putenv_s(name, value.c_str());
#else
    setenv(name, value.c_str(), 1);
#endif
}

// ---------------------------------------------------------------------------
// Check if any --gtest_* flags are present (suppress menu)
// ---------------------------------------------------------------------------
static bool hasGtestFlags(int argc, char** argv) {
    for (int i = 1; i < argc; ++i) {
        std::string arg(argv[i]);
        if (arg.find("--gtest_") == 0) return true;
    }
    return false;
}

// ---------------------------------------------------------------------------
// CLI parsing
// ---------------------------------------------------------------------------
struct CliOptions {
    bool llm    = false;
    bool mcp    = false;
    bool wifi   = false;
    bool health = false;
    bool all    = false;
    std::string model;
    std::string url;
    bool hasSection = false;
};

static CliOptions parseCli(int argc, char** argv) {
    CliOptions opts;
    for (int i = 1; i < argc; ++i) {
        std::string arg(argv[i]);
        if (arg == "--llm")         { opts.llm = true; opts.hasSection = true; }
        else if (arg == "--mcp")    { opts.mcp = true; opts.hasSection = true; }
        else if (arg == "--wifi")   { opts.wifi = true; opts.hasSection = true; }
        else if (arg == "--health") { opts.health = true; opts.hasSection = true; }
        else if (arg == "--all")    { opts.all = true; opts.hasSection = true; }
        else if (arg == "--model" && i + 1 < argc) { opts.model = argv[++i]; }
        else if (arg == "--url"   && i + 1 < argc) { opts.url   = argv[++i]; }
    }
    return opts;
}

// ---------------------------------------------------------------------------
// Build filter from multiple sections: "Suite1.*:Suite2.*"
// ---------------------------------------------------------------------------
static std::string buildFilter(const CliOptions& opts) {
    if (opts.all) return kFilterAll;

    std::vector<std::string> filters;
    if (opts.llm)    filters.push_back(kFilterLLM);
    if (opts.mcp)    filters.push_back(kFilterMCP);
    if (opts.wifi)   filters.push_back(kFilterWiFi);
    if (opts.health) filters.push_back(kFilterHealth);

    if (filters.empty()) return kFilterAll;

    std::string combined;
    for (size_t i = 0; i < filters.size(); ++i) {
        if (i > 0) combined += ":";
        combined += filters[i];
    }
    return combined;
}

// ---------------------------------------------------------------------------
// Interactive menu — same visual style as wifi_agent / health_agent
// ---------------------------------------------------------------------------
static std::string showMenu() {
    // Banner
    std::cout << std::endl;
    std::cout << color::CYAN << color::BOLD
              << "  ========================================================================================"
              << color::RESET << std::endl;
    std::cout << color::CYAN << color::BOLD
              << "   Integration Tests  |  GAIA C++ Agent Framework  |  LLM + MCP + WiFi + Health"
              << color::RESET << std::endl;
    std::cout << color::CYAN << color::BOLD
              << "  ========================================================================================"
              << color::RESET << std::endl;
    std::cout << std::endl;

    // Menu items — same layout as printDiagnosticMenu() / printHealthMenu()
    std::cout << color::CYAN
              << "  ========================================================================================"
              << color::RESET << std::endl;
    for (size_t i = 0; i < kMenuSize; ++i) {
        size_t num = i + 1;
        std::cout << color::YELLOW << "  [" << num << "] "
                  << color::RESET << color::WHITE
                  << kTestMenu[i].label
                  << color::RESET << color::GRAY
                  << "  - " << kTestMenu[i].description
                  << color::RESET << std::endl;
    }
    std::cout << color::CYAN
              << "  ========================================================================================"
              << color::RESET << std::endl;
    std::cout << color::GRAY
              << "  CLI: --llm --mcp --wifi --health --all  |  --model <id>  --url <base-url>"
              << color::RESET << std::endl;
    std::cout << std::endl;
    std::cout << color::BOLD << "  > " << color::RESET << std::flush;

    std::string input;
    if (!std::getline(std::cin, input)) return kFilterAll;

    // Map selection to filter
    bool isNumber = !input.empty() &&
        std::all_of(input.begin(), input.end(),
                    [](unsigned char c) { return std::isdigit(c); });
    if (isNumber) {
        int choice = 0;
        try { choice = std::stoi(input); }
        catch (...) { choice = -1; }
        if (choice >= 1 && choice <= static_cast<int>(kMenuSize)) {
            size_t idx = static_cast<size_t>(choice - 1);
            std::cout << color::CYAN << "  > "
                      << kTestMenu[idx].label
                      << color::RESET << std::endl;
            return kTestMenu[idx].filter;
        }
        std::cout << color::RED << "  Invalid selection. Running all tests."
                  << color::RESET << std::endl;
    }

    return kFilterAll;
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------
int main(int argc, char** argv) {
    // Check for GTest flags BEFORE InitGoogleTest consumes them from argv
    bool gtestFlagsPresent = hasGtestFlags(argc, argv);

    ::testing::InitGoogleTest(&argc, argv);

    // Parse our custom flags (after GTest consumes --gtest_*)
    CliOptions opts = parseCli(argc, argv);

    // Set env vars from CLI if provided
    if (!opts.model.empty()) {
        setEnvVar("GAIA_CPP_TEST_MODEL", opts.model);
    }
    if (!opts.url.empty()) {
        setEnvVar("GAIA_CPP_BASE_URL", opts.url);
    }

    // Determine filter
    std::string filter;

    if (gtestFlagsPresent) {
        // User/CTest passed --gtest_filter or --gtest_list_tests — don't override
        filter = "";
    } else if (opts.hasSection) {
        // CLI flags: --llm, --mcp, etc.
        filter = buildFilter(opts);
    } else {
        // Interactive menu
        filter = showMenu();
    }

    // Apply filter (only if we chose one — skip if user passed --gtest_filter)
    if (!filter.empty()) {
        ::testing::GTEST_FLAG(filter) = filter;
    }

    return RUN_ALL_TESTS();
}
