// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Security Demo — interactive demo of GAIA C++ tool security features.
//
// Four demo modes:
//   1. Tool Policies & Confirmation  — ALLOW / CONFIRM / DENY with interactive TUI
//   2. Argument Validation           — validateArgs callback: sanitize + reject
//   3. Path Validation               — gaia::validatePath() prevents traversal
//   4. Shell Argument Safety         — gaia::isSafeShellArg() blocks metacharacters
//
// Does NOT require a Lemonade server.  Calls executeTool() and security helpers
// directly so you can explore the security system without any server setup.
//
// Usage:
//   ./security_demo
//   Then select a mode from the menu (1–4) or Q to quit.

#include <cctype>
#include <fstream>
#include <iostream>
#include <memory>
#include <string>
#include <vector>

#include <gaia/clean_console.h>
#include <gaia/security.h>
#include <gaia/tool_registry.h>
#include <gaia/types.h>

namespace color = gaia::color;

static constexpr size_t kLineWidth = 88;
static const std::string kLine(kLineWidth, '=');

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

static std::string storePath() {
#ifdef _WIN32
    const char* profile = std::getenv("USERPROFILE");
    std::string home = profile ? profile : "C:\\Users\\Default";
    return home + "\\.gaia\\security\\allowed_tools.json";
#else
    const char* home = std::getenv("HOME");
    return std::string(home ? home : "/tmp") + "/.gaia/security/allowed_tools.json";
#endif
}

static void printBanner(const std::string& title) {
    std::cout << std::endl;
    std::cout << color::CYAN << color::BOLD << "  " << kLine << color::RESET << std::endl;
    std::cout << color::CYAN << color::BOLD << "   " << title << color::RESET << std::endl;
    std::cout << color::CYAN << color::BOLD << "  " << kLine << color::RESET << std::endl;
    std::cout << std::endl;
}

static void printSectionHeader(const std::string& label) {
    std::cout << std::endl;
    std::cout << color::CYAN << "  " << kLine << color::RESET << std::endl;
    std::cout << color::CYAN << color::BOLD << "   " << label << color::RESET << std::endl;
    std::cout << color::CYAN << "  " << kLine << color::RESET << std::endl;
    std::cout << std::endl;
}

static void printCallHeader(int step, const std::string& toolName, gaia::ToolPolicy policy,
                            const std::string& description, const std::string& policyNote) {
    std::string badge;
    const char* badgeColor = color::GRAY;
    switch (policy) {
        case gaia::ToolPolicy::ALLOW:
            badge = "ALLOW";
            badgeColor = color::GREEN;
            break;
        case gaia::ToolPolicy::CONFIRM:
            badge = "CONFIRM";
            badgeColor = color::YELLOW;
            break;
        case gaia::ToolPolicy::DENY:
            badge = "DENY";
            badgeColor = color::RED;
            break;
    }

    std::string stepLabel = "[" + std::to_string(step) + "] ";
    size_t usedLen = 2 + stepLabel.size() + toolName.size() + badge.size();
    size_t pad = (kLineWidth > usedLen + 1) ? (kLineWidth - usedLen) : 1;

    std::cout << std::endl;
    std::cout << color::CYAN << "  " << kLine << color::RESET << std::endl;
    std::cout << "  "
              << color::YELLOW << color::BOLD << stepLabel << color::RESET
              << color::WHITE  << color::BOLD << toolName  << color::RESET
              << std::string(pad, ' ')
              << badgeColor    << color::BOLD << badge     << color::RESET
              << std::endl;
    std::cout << color::CYAN << "  " << kLine << color::RESET << std::endl;

    std::cout << "    " << color::BOLD << "Description:  " << color::RESET
              << color::GRAY << description << color::RESET << std::endl;
    std::cout << "    " << color::BOLD << "Policy:       " << color::RESET
              << color::GRAY << policyNote  << color::RESET << std::endl;
    std::cout << std::endl;
}

static void printExecuting() {
    std::cout << "    " << color::GREEN << color::BOLD << ">> Executing..." << color::RESET << std::endl;
    std::cout << std::endl;
}

static void printToolResult(const gaia::json& result, bool denied = false) {
    if (denied) {
        std::cout << "    " << color::RED << color::BOLD << "DENIED" << color::RESET;
        std::string err = result.value("error", "");
        if (!err.empty()) {
            std::cout << "  " << color::GRAY << err << color::RESET;
        }
        std::cout << std::endl;
        return;
    }

    for (auto it = result.begin(); it != result.end(); ++it) {
        std::string key = it.key();
        if (!key.empty()) key[0] = static_cast<char>(std::toupper(static_cast<unsigned char>(key[0])));
        for (char& c : key) {
            if (c == '_') c = ' ';
        }

        std::string val;
        if (it.value().is_string()) {
            val = it.value().get<std::string>();
        } else if (it.value().is_boolean()) {
            val = it.value().get<bool>() ? "true" : "false";
        } else {
            val = it.value().dump();
        }

        std::cout << "    " << color::GREEN << color::BOLD << key << ": "
                  << color::RESET << color::WHITE << val << color::RESET << std::endl;
    }
}

static void printStoreContents() {
    std::string path = storePath();
    std::ifstream f(path);
    if (!f.is_open()) {
        std::cout << "    " << color::GRAY << "(file not found — no tools permanently allowed)"
                  << color::RESET << std::endl;
        return;
    }
    std::string line;
    while (std::getline(f, line)) {
        std::cout << color::GRAY << "    " << line << color::RESET << std::endl;
    }
}

// Print a PASS/FAIL or SAFE/BLOCKED result line.
// passed=true  → green label
// passed=false → red label
static void printCheckResult(const std::string& label, const std::string& value, bool passed) {
    const char* resultColor = passed ? color::GREEN : color::RED;
    const char* resultText  = passed ? "PASS" : "FAIL";

    std::cout << "    " << color::BOLD << label << color::RESET
              << color::GRAY << value << color::RESET
              << "  " << resultColor << color::BOLD << resultText << color::RESET
              << std::endl;
}

// SAFE/BLOCKED variant used by the shell safety mode.
static void printSafetyResult(const std::string& value, bool safe) {
    const char* resultColor = safe ? color::GREEN : color::RED;
    const char* resultText  = safe ? "SAFE" : "BLOCKED";

    std::cout << "    " << color::BOLD << "arg: " << color::RESET
              << color::WHITE << "\"" << value << "\"" << color::RESET
              << "  " << resultColor << color::BOLD << resultText << color::RESET
              << std::endl;
}

static void waitForReturn() {
    std::cout << std::endl;
    std::cout << color::GRAY << "  Press Enter to return to the menu..." << color::RESET;
    std::string dummy;
    std::getline(std::cin, dummy);
}

// Returns 0 for quit, 1-4 for modes.
static int printMainMenu() {
    std::cout << std::endl;
    std::cout << color::CYAN << color::BOLD << "  " << kLine << color::RESET << std::endl;
    std::cout << color::CYAN << color::BOLD << "   Select a Security Demo Mode" << color::RESET << std::endl;
    std::cout << color::CYAN << color::BOLD << "  " << kLine << color::RESET << std::endl;
    std::cout << std::endl;
    std::cout << "  " << color::YELLOW << color::BOLD << "[1]" << color::RESET
              << color::WHITE << "  Tool Policies & Confirmation" << color::RESET
              << color::GRAY << "  — ALLOW / CONFIRM / DENY, persistent allow-list" << color::RESET << std::endl;
    std::cout << "  " << color::YELLOW << color::BOLD << "[2]" << color::RESET
              << color::WHITE << "  Argument Validation         " << color::RESET
              << color::GRAY << "  — validateArgs callback: sanitize & reject" << color::RESET << std::endl;
    std::cout << "  " << color::YELLOW << color::BOLD << "[3]" << color::RESET
              << color::WHITE << "  Path Validation              " << color::RESET
              << color::GRAY << "  — gaia::validatePath() prevents traversal" << color::RESET << std::endl;
    std::cout << "  " << color::YELLOW << color::BOLD << "[4]" << color::RESET
              << color::WHITE << "  Shell Argument Safety        " << color::RESET
              << color::GRAY << "  — gaia::isSafeShellArg() blocks metacharacters" << color::RESET << std::endl;
    std::cout << "  " << color::YELLOW << color::BOLD << "[Q]" << color::RESET
              << color::WHITE << "  Quit" << color::RESET << std::endl;
    std::cout << std::endl;
    std::cout << "  Choice: ";

    std::string input;
    if (!std::getline(std::cin, input)) return 0; // EOF — treat as quit

    if (input.empty()) return -1; // bare Enter — silently redraw menu
    char ch = static_cast<char>(std::tolower(static_cast<unsigned char>(input[0])));
    if (ch == 'q') return 0;
    if (ch >= '1' && ch <= '4') return ch - '0';
    return -1;
}

// ---------------------------------------------------------------------------
// Mode 1 — Tool Policies & Confirmation
// ---------------------------------------------------------------------------

static void runModePolicies(std::shared_ptr<gaia::AllowedToolsStore> store) {
    printBanner("Mode 1  |  Tool Policies & Confirmation");

    std::cout << "  " << color::BOLD << "Persistent store:" << color::RESET
              << "  " << color::GRAY << storePath() << color::RESET << std::endl;

    gaia::ToolRegistry registry;
    registry.setAllowedToolsStore(store);
    registry.setConfirmCallback(gaia::makeStdinConfirmCallback());

    // ALLOW policy tool
    gaia::ToolInfo status_info;
    status_info.name        = "get_status";
    status_info.description = "Returns the current agent status (read-only)";
    status_info.policy      = gaia::ToolPolicy::ALLOW;
    status_info.callback    = [](const gaia::json& /*args*/) -> gaia::json {
        return gaia::json{{"status", "ok"}, {"mode", "secure_agent"}, {"ready", true}};
    };
    registry.registerTool(std::move(status_info));

    // CONFIRM policy tool
    gaia::ToolInfo dns_info;
    dns_info.name        = "flush_dns_simulate";
    dns_info.description = "Simulate flush of system DNS cache";
    dns_info.policy      = gaia::ToolPolicy::CONFIRM;
    dns_info.callback    = [](const gaia::json& /*args*/) -> gaia::json {
        return gaia::json{{"status", "ok"}, {"message", "DNS cache flushed"}};
    };
    registry.registerTool(std::move(dns_info));

    bool alreadyAllowed = store->isAlwaysAllowed("flush_dns_simulate");

    if (alreadyAllowed) {
        // Flow B — already in the always-allowed store
        printSectionHeader("Always-Allowed State Detected");

        std::cout << "  " << color::YELLOW << color::BOLD << "Notice:" << color::RESET
                  << color::WHITE << " flush_dns_simulate is already in your always-allowed store."
                  << color::RESET << std::endl;
        std::cout << "  " << color::GRAY
                  << "You most likely ran this demo before and chose \"Always Allow\" when prompted."
                  << color::RESET << std::endl;
        std::cout << "  " << color::GRAY
                  << "All 3 calls below will execute without any prompts, demonstrating"
                  << color::RESET << std::endl;
        std::cout << "  " << color::GRAY
                  << "that always-allowed tools bypass the CONFIRM policy entirely."
                  << color::RESET << std::endl;

        printSectionHeader("Current Store Contents");
        printStoreContents();

        printCallHeader(1, "get_status", gaia::ToolPolicy::ALLOW,
                        "Returns the current agent status (read-only)",
                        "ALLOW — runs immediately, no prompt");
        printExecuting();
        auto r0 = registry.executeTool("get_status", {});
        printToolResult(r0);

        printCallHeader(2, "flush_dns_simulate", gaia::ToolPolicy::CONFIRM,
                        "Simulate flush of system DNS cache",
                        "CONFIRM — tool is in always-allowed store, prompt is bypassed");
        printExecuting();
        auto r1 = registry.executeTool("flush_dns_simulate", {});
        bool denied1 = r1.value("status", "") == "error" &&
               r1.value("error", "").find("denied") != std::string::npos;
        printToolResult(r1, denied1);

        printCallHeader(3, "flush_dns_simulate", gaia::ToolPolicy::CONFIRM,
                        "Simulate flush of system DNS cache",
                        "CONFIRM — same result: always-allowed bypasses every subsequent call");
        printExecuting();
        auto r2 = registry.executeTool("flush_dns_simulate", {});
        bool denied2 = r2.value("status", "") == "error" &&
               r2.value("error", "").find("denied") != std::string::npos;
        printToolResult(r2, denied2);

        printSectionHeader("How to Reset the Demo");

        std::cout << "  " << color::WHITE
                  << "To see the full interactive demo with confirmation prompts,"
                  << color::RESET << std::endl;
        std::cout << "  " << color::WHITE
                  << "remove flush_dns_simulate from your allowed_tools.json:"
                  << color::RESET << std::endl;
        std::cout << std::endl;
        std::cout << "  " << color::BOLD << "File:  " << color::RESET
                  << color::CYAN << storePath() << color::RESET << std::endl;
        std::cout << std::endl;
        std::cout << "  " << color::GRAY << "Change it to:" << color::RESET << std::endl;
        std::cout << color::GRAY << "    {" << color::RESET << std::endl;
        std::cout << color::GRAY << "      \"allowed_tools\": []," << color::RESET << std::endl;
        std::cout << color::GRAY << "      \"version\": 1" << color::RESET << std::endl;
        std::cout << color::GRAY << "    }" << color::RESET << std::endl;
        std::cout << std::endl;
        std::cout << "  " << color::GRAY << "Then select Mode 1 again." << color::RESET << std::endl;

    } else {
        // Flow A — fresh state
        printSectionHeader("Store State (before)");
        printStoreContents();

        printCallHeader(1, "get_status", gaia::ToolPolicy::ALLOW,
                        "Returns the current agent status (read-only)",
                        "ALLOW — runs immediately, no prompt");
        printExecuting();
        auto r0 = registry.executeTool("get_status", {});
        printToolResult(r0);

        printCallHeader(2, "flush_dns_simulate", gaia::ToolPolicy::CONFIRM,
                        "Simulate flush of system DNS cache",
                        "CONFIRM — you will be prompted before this tool runs");
        printExecuting();
        auto r1 = registry.executeTool("flush_dns_simulate", {});
        bool denied1 = r1.value("status", "") == "error" &&
               r1.value("error", "").find("denied") != std::string::npos;
        printToolResult(r1, denied1);

        bool nowAlwaysAllowed = store->isAlwaysAllowed("flush_dns_simulate");
        printCallHeader(3, "flush_dns_simulate", gaia::ToolPolicy::CONFIRM,
                        "Simulate flush of system DNS cache",
                        nowAlwaysAllowed
                            ? "CONFIRM — tool is now always-allowed, no further prompts"
                            : "CONFIRM — prompting again (you chose Allow Once or Deny)");
        printExecuting();
        auto r2 = registry.executeTool("flush_dns_simulate", {});
        bool denied2 = r2.value("status", "") == "error" &&
               r2.value("error", "").find("denied") != std::string::npos;
        printToolResult(r2, denied2);

        printSectionHeader("Store State (after)");
        printStoreContents();

        if (store->isAlwaysAllowed("flush_dns_simulate")) {
            std::cout << std::endl;
            std::cout << "  " << color::GREEN << "flush_dns_simulate is now permanently allowed."
                      << color::RESET << std::endl;
            std::cout << "  " << color::GRAY
                      << "Select Mode 1 again to see Flow B: the always-allowed bypass demo."
                      << color::RESET << std::endl;
        }
    }

    waitForReturn();
}

// ---------------------------------------------------------------------------
// Mode 2 — Argument Validation
// ---------------------------------------------------------------------------

static void runModeArgValidation() {
    printBanner("Mode 2  |  Argument Validation");

    std::cout << "  " << color::BOLD << "Why this matters:" << color::RESET << std::endl;
    std::cout << "  " << color::GRAY
              << "The LLM controls what arguments it passes to your tools. A prompt injection"
              << color::RESET << std::endl;
    std::cout << "  " << color::GRAY
              << "or confused model could supply malicious paths, oversized payloads, or"
              << color::RESET << std::endl;
    std::cout << "  " << color::GRAY
              << "unexpected types. The validateArgs callback intercepts arguments before the"
              << color::RESET << std::endl;
    std::cout << "  " << color::GRAY
              << "tool callback runs — and before the user sees a confirmation prompt —"
              << color::RESET << std::endl;
    std::cout << "  " << color::GRAY
              << "giving you a chance to sanitize or reject them."
              << color::RESET << std::endl;
    std::cout << std::endl;

    // Set up a local registry — no confirm callback needed for this demo
    gaia::ToolRegistry registry;

    gaia::ToolInfo cfg_info;
    cfg_info.name        = "update_config";
    cfg_info.description = "Update a named configuration key";
    cfg_info.policy      = gaia::ToolPolicy::ALLOW;
    cfg_info.validateArgs = [](const std::string& /*name*/, const gaia::json& args) -> gaia::json {
        std::string key   = args.value("key", "");
        std::string value = args.value("value", "");

        if (key.empty()) {
            throw std::invalid_argument("key must not be empty");
        }
        if (key.find("..") != std::string::npos) {
            throw std::invalid_argument("key contains path traversal sequence: " + key);
        }
        if (value.size() > 256) {
            throw std::invalid_argument("value exceeds 256-character limit");
        }

        // Sanitize: trim leading and trailing whitespace from value
        size_t start = value.find_first_not_of(" \t\r\n");
        size_t end   = value.find_last_not_of(" \t\r\n");
        std::string trimmed = (start == std::string::npos) ? "" : value.substr(start, end - start + 1);

        gaia::json sanitized = args;
        sanitized["value"] = trimmed;
        return sanitized;
    };
    cfg_info.callback = [](const gaia::json& args) -> gaia::json {
        return gaia::json{{"status", "ok"}, {"key", args.value("key", "")},
                          {"value", args.value("value", "")}};
    };
    registry.registerTool(std::move(cfg_info));

    // --- Test 1: valid call with whitespace value (will be trimmed) ---
    printSectionHeader("Test 1 — Valid call with whitespace padding");

    gaia::json args1 = {{"key", "theme"}, {"value", "  dark  "}};
    std::cout << "  " << color::BOLD << "Input args:" << color::RESET << std::endl;
    std::cout << "    " << color::GRAY << args1.dump() << color::RESET << std::endl;
    std::cout << std::endl;

    auto r1 = registry.executeTool("update_config", args1);
    bool ok1 = r1.value("status", "") == "ok";
    if (ok1) {
        std::cout << "  " << color::BOLD << "Result (value trimmed by validateArgs):" << color::RESET << std::endl;
        printToolResult(r1);
    } else {
        std::cout << "  " << color::RED << color::BOLD << "Unexpected error: "
                  << color::RESET << r1.dump() << std::endl;
    }
    printCheckResult("  Outcome: ", ok1 ? "accepted and sanitized" : "unexpected failure", ok1);

    // --- Test 2: rejected call — key contains path traversal ---
    printSectionHeader("Test 2 — Rejected call (path traversal in key)");

    gaia::json args2 = {{"key", "../etc/passwd"}, {"value", "malicious"}};
    std::cout << "  " << color::BOLD << "Input args:" << color::RESET << std::endl;
    std::cout << "    " << color::GRAY << args2.dump() << color::RESET << std::endl;
    std::cout << std::endl;

    auto r2 = registry.executeTool("update_config", args2);
    bool rejected2 = r2.value("status", "") == "error";
    if (rejected2) {
        std::cout << "  " << color::BOLD << "Error returned to LLM:" << color::RESET << std::endl;
        std::cout << "    " << color::RED << r2.value("error", r2.dump()) << color::RESET << std::endl;
        std::cout << std::endl;
        std::cout << "    " << color::RED << color::BOLD << "BLOCKED" << color::RESET
                  << color::GRAY << "  (tool did not run)" << color::RESET << std::endl;
    } else {
        std::cout << "    " << color::RED << color::BOLD
                  << "ERROR: tool ran when it should have been rejected"
                  << color::RESET << std::endl;
    }

    waitForReturn();
}

// ---------------------------------------------------------------------------
// Mode 3 — Path Validation
// ---------------------------------------------------------------------------

static void runModePathValidation() {
    printBanner("Mode 3  |  Path Validation");

    std::cout << "  " << color::BOLD << "Why this matters:" << color::RESET << std::endl;
    std::cout << "  " << color::GRAY
              << "Tools that accept file paths from the LLM are vulnerable to path traversal"
              << color::RESET << std::endl;
    std::cout << "  " << color::GRAY
              << "attacks. An argument like ../../etc/passwd can escape a sandboxed directory."
              << color::RESET << std::endl;
    std::cout << "  " << color::GRAY
              << "validatePath() canonicalizes both paths using the OS (resolving .., symlinks"
              << color::RESET << std::endl;
    std::cout << "  " << color::GRAY
              << "on POSIX) and verifies that the requested path is inside the base directory."
              << color::RESET << std::endl;
    std::cout << std::endl;

    printSectionHeader("Test Cases");

    // Platform-specific paths
#ifdef _WIN32
    const std::string base     = "C:\\Temp";
    const std::string safe1    = "C:\\Temp";
    const std::string traversal = "C:\\Temp\\..\\Windows\\System32\\drivers\\etc\\hosts";
    const std::string outside  = "C:\\Windows\\System32\\drivers\\etc\\hosts";
#else
    const std::string base     = "/tmp";
    const std::string safe1    = "/tmp";
    const std::string traversal = "/tmp/../etc/passwd";
    const std::string outside  = "/etc/passwd";
#endif

    struct TestCase {
        std::string base;
        std::string requested;
        std::string label;
    };

    std::vector<TestCase> cases = {
        { base, safe1,    "identical path (base == requested)" },
        { base, traversal,"path with .. traversal"             },
        { base, outside,  "path outside base directory"        },
    };

    for (size_t i = 0; i < cases.size(); ++i) {
        const auto& tc = cases[i];
        bool result = gaia::validatePath(tc.base, tc.requested);

        std::cout << "  " << color::BOLD << "[" << (i + 1) << "] " << tc.label << color::RESET << std::endl;
        std::cout << "    " << color::GRAY << "base:      " << tc.base      << color::RESET << std::endl;
        std::cout << "    " << color::GRAY << "requested: " << tc.requested << color::RESET << std::endl;

        const char* outcomeColor = result ? color::GREEN : color::RED;
        const char* outcomeText  = result ? "PASS (inside base)" : "FAIL (outside base or unresolvable)";
        std::cout << "    " << outcomeColor << color::BOLD << outcomeText << color::RESET << std::endl;
        std::cout << std::endl;
    }

    waitForReturn();
}

// ---------------------------------------------------------------------------
// Mode 4 — Shell Argument Safety
// ---------------------------------------------------------------------------

static void runModeShellSafety() {
    printBanner("Mode 4  |  Shell Argument Safety");

    std::cout << "  " << color::BOLD << "Why this matters:" << color::RESET << std::endl;
    std::cout << "  " << color::GRAY
              << "If your tool builds a shell command string from LLM-supplied arguments,"
              << color::RESET << std::endl;
    std::cout << "  " << color::GRAY
              << "a single semicolon can turn a filename into arbitrary code execution."
              << color::RESET << std::endl;
    std::cout << "  " << color::GRAY
              << "isSafeShellArg() rejects strings containing any shell metacharacter,"
              << color::RESET << std::endl;
    std::cout << "  " << color::GRAY
              << "ensuring the argument is safe to interpolate into a command string."
              << color::RESET << std::endl;
    std::cout << std::endl;

    printSectionHeader("Test Cases");

    struct ShellCase {
        std::string arg;
        bool expectSafe;
        std::string note;
    };

    std::vector<ShellCase> cases = {
        { "eth0",                      true,  "plain interface name"         },
        { "192.168.1.1",               true,  "IP address"                   },
        { "wlan0",                     true,  "wireless interface name"       },
        { "eth0; rm -rf /",            false, "semicolon + destructive cmd"   },
        { "$(cat /etc/passwd)",        false, "command substitution"          },
        { "file.txt | mail attacker",  false, "pipe to another command"       },
        { "",                          false, "empty string"                  },
    };

    for (const auto& tc : cases) {
        bool safe = gaia::isSafeShellArg(tc.arg);

        const char* resultColor = safe ? color::GREEN : color::RED;
        const char* resultText  = safe ? "SAFE" : "BLOCKED";

        std::cout << "  " << resultColor << color::BOLD << resultText << color::RESET
                  << "  " << color::WHITE << "\"" << tc.arg << "\"" << color::RESET
                  << "  " << color::GRAY << "(" << tc.note << ")" << color::RESET << std::endl;
    }

    std::cout << std::endl;
    std::cout << "  " << color::BOLD << "Blocked characters include:" << color::RESET << std::endl;
    std::cout << "  " << color::GRAY
              << "space, tab, newline, ; | & < > $ ` \" ' ! { } ( ) [ ] ~ * ? # ^ % ="
              << color::RESET << std::endl;
    std::cout << "  " << color::GRAY
              << "Note: backslash (\\) is NOT blocked — it is a path separator on Windows."
              << color::RESET << std::endl;
    std::cout << "  " << color::GRAY
              << "For POSIX-only commands, add an explicit backslash check in validateArgs."
              << color::RESET << std::endl;

    waitForReturn();
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int main() {
    printBanner("Security Demo  |  GAIA C++ Agent Framework  |  Tool Security Features");

    // Shared store for Mode 1 (Flow A/B detection persists across re-entries)
    auto store = std::make_shared<gaia::AllowedToolsStore>();

    while (true) {
        int choice = printMainMenu();

        if (choice == 0) break;

        switch (choice) {
            case  1: runModePolicies(store);  break;
            case  2: runModeArgValidation();  break;
            case  3: runModePathValidation(); break;
            case  4: runModeShellSafety();    break;
            case -1: break; // bare Enter — redraw menu silently
            default:
                std::cout << "  " << color::GRAY
                          << "Unknown choice — enter 1, 2, 3, 4, or Q."
                          << color::RESET << std::endl;
                break;
        }
    }

    std::cout << std::endl;
    std::cout << color::CYAN << color::BOLD << "  " << kLine << color::RESET << std::endl;
    std::cout << color::CYAN << color::BOLD << "   Secure Agent complete." << color::RESET << std::endl;
    std::cout << color::CYAN << color::BOLD << "  " << kLine << color::RESET << std::endl;
    std::cout << std::endl;

    return 0;
}
