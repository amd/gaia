// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Secure Agent — example agent with tool-confirmation policies.
//
// Demonstrates:
//   1. ToolPolicy::ALLOW   — tool runs immediately, no prompt.
//   2. ToolPolicy::CONFIRM — user is prompted before every execution.
//   3. ToolConfirmResult::ALLOW_ONCE  — runs once, prompts again next time.
//   4. ToolConfirmResult::ALWAYS_ALLOW — persisted to ~/.gaia/security/allowed_tools.json.
//   5. ToolConfirmResult::DENY — execution is blocked.
//
// Does NOT require a Lemonade server.  Calls executeTool() directly.
//
// Usage:
//   ./secure_agent
//   Then follow the on-screen prompts (1 / 2 / 3).

#include <cctype>
#include <fstream>
#include <iostream>
#include <memory>
#include <string>

#include <gaia/clean_console.h>
#include <gaia/security.h>
#include <gaia/tool_registry.h>
#include <gaia/types.h>

namespace color = gaia::color;

static constexpr size_t kLineWidth = 88;
static const std::string kLine(kLineWidth, '=');

// ---------------------------------------------------------------------------
// Helpers
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
    // Right-align badge within kLineWidth (2-space indent + text + padding + badge = kLineWidth)
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
        // Capitalize first letter, replace underscores with spaces
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

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int main() {
    printBanner("Secure Agent  |  GAIA C++ Agent Framework  |  Tool Confirmation Demo");

    // --- Set up the AllowedToolsStore (default path) ---
    auto store = std::make_shared<gaia::AllowedToolsStore>();

    std::cout << "  " << color::BOLD << "Persistent store:" << color::RESET
              << "  " << color::GRAY << storePath() << color::RESET << std::endl;

    // --- Set up the ToolRegistry ---
    gaia::ToolRegistry registry;
    registry.setAllowedToolsStore(store);
    registry.setConfirmCallback(gaia::makeStdinConfirmCallback());

    // Register a tool with ALLOW policy — runs immediately, no prompt
    gaia::ToolInfo status_info;
    status_info.name        = "get_status";
    status_info.description = "Returns the current agent status (read-only)";
    status_info.policy      = gaia::ToolPolicy::ALLOW;
    status_info.callback    = [](const gaia::json& /*args*/) -> gaia::json {
        return gaia::json{{"status", "ok"}, {"mode", "secure_agent"}, {"ready", true}};
    };
    registry.registerTool(std::move(status_info));

    // Register a tool with CONFIRM policy — user is prompted before each call
    gaia::ToolInfo dns_info;
    dns_info.name        = "flush_dns_simulate";
    dns_info.description = "Simulate flush of system DNS cache";
    dns_info.policy      = gaia::ToolPolicy::CONFIRM;
    dns_info.callback    = [](const gaia::json& /*args*/) -> gaia::json {
        return gaia::json{{"status", "ok"}, {"message", "DNS cache flushed"}};
    };
    registry.registerTool(std::move(dns_info));

    // --- State-aware branching ---
    bool alreadyAllowed = store->isAlwaysAllowed("flush_dns_simulate");

    if (alreadyAllowed) {
        // -------------------------------------------------------------------
        // Flow B — flush_dns_simulate is already in the always-allowed store.
        // Run all 3 calls without prompts to show that always-allowed bypasses
        // CONFIRM, then tell the user how to reset manually.
        // -------------------------------------------------------------------
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

        // Call 1: ALLOW — unchanged
        printCallHeader(1, "get_status", gaia::ToolPolicy::ALLOW,
                        "Returns the current agent status (read-only)",
                        "ALLOW — runs immediately, no prompt");
        printExecuting();
        auto r0 = registry.executeTool("get_status", {});
        printToolResult(r0);

        // Call 2: CONFIRM — but always-allowed, so no prompt
        printCallHeader(2, "flush_dns_simulate", gaia::ToolPolicy::CONFIRM,
                        "Simulate flush of system DNS cache",
                        "CONFIRM — tool is in always-allowed store, prompt is bypassed");
        printExecuting();
        auto r1 = registry.executeTool("flush_dns_simulate", {});
        bool denied1 = r1.value("status", "") == "error" &&
               r1.value("error", "").find("denied") != std::string::npos;
        printToolResult(r1, denied1);

        // Call 3: same tool again — also bypassed
        printCallHeader(3, "flush_dns_simulate", gaia::ToolPolicy::CONFIRM,
                        "Simulate flush of system DNS cache",
                        "CONFIRM — same result: always-allowed bypasses every subsequent call");
        printExecuting();
        auto r2 = registry.executeTool("flush_dns_simulate", {});
        bool denied2 = r2.value("status", "") == "error" &&
               r2.value("error", "").find("denied") != std::string::npos;
        printToolResult(r2, denied2);

        // Tell the user how to reset — never modify the store automatically
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
        std::cout << "  " << color::GRAY << "Then re-run ./secure_agent." << color::RESET << std::endl;

    } else {
        // -------------------------------------------------------------------
        // Flow A — fresh state.  Walk through all 3 calls with explanations.
        // -------------------------------------------------------------------
        printSectionHeader("Store State (before)");
        printStoreContents();

        // Call 1: ALLOW
        printCallHeader(1, "get_status", gaia::ToolPolicy::ALLOW,
                        "Returns the current agent status (read-only)",
                        "ALLOW — runs immediately, no prompt");
        printExecuting();
        auto r0 = registry.executeTool("get_status", {});
        printToolResult(r0);

        // Call 2: CONFIRM — user will be prompted
        printCallHeader(2, "flush_dns_simulate", gaia::ToolPolicy::CONFIRM,
                        "Simulate flush of system DNS cache",
                        "CONFIRM — you will be prompted before this tool runs");
        printExecuting();
        auto r1 = registry.executeTool("flush_dns_simulate", {});
        bool denied1 = r1.value("status", "") == "error" &&
               r1.value("error", "").find("denied") != std::string::npos;
        printToolResult(r1, denied1);

        // Call 3: behavior depends on what the user chose in Call 2
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
                      << "Re-run ./secure_agent to see Flow B: the always-allowed bypass demo."
                      << color::RESET << std::endl;
        }
    }

    std::cout << std::endl;
    std::cout << color::CYAN << color::BOLD << "  " << kLine << color::RESET << std::endl;
    std::cout << color::CYAN << color::BOLD << "   Secure Agent complete." << color::RESET << std::endl;
    std::cout << color::CYAN << color::BOLD << "  " << kLine << color::RESET << std::endl;
    std::cout << std::endl;

    return 0;
}
