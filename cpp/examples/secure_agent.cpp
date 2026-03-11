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

#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <string>

#include <gaia/security.h>
#include <gaia/tool_registry.h>
#include <gaia/types.h>

namespace fs = std::filesystem;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static std::string defaultStoreDir() {
#ifdef _WIN32
    const char* profile = std::getenv("USERPROFILE");
    std::string home = profile ? profile : "C:\\Users\\Default";
    return home + "\\.gaia\\security";
#else
    const char* home = std::getenv("HOME");
    return std::string(home ? home : "/tmp") + "/.gaia/security";
#endif
}

static std::string storePath() {
#ifdef _WIN32
    return defaultStoreDir() + "\\allowed_tools.json";
#else
    return defaultStoreDir() + "/allowed_tools.json";
#endif
}

static void printStoreContents() {
    std::string path = storePath();
    std::ifstream f(path);
    if (!f.is_open()) {
        std::cout << "  (file not found: " << path << ")\n";
        return;
    }
    std::cout << "  " << path << ":\n";
    std::string line;
    while (std::getline(f, line)) {
        std::cout << "    " << line << "\n";
    }
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int main() {
    std::cout << "\n=== GAIA Secure Agent ===\n\n";

    // --- Set up the AllowedToolsStore (default path) ---
    auto store = std::make_shared<gaia::AllowedToolsStore>();

    std::cout << "Persistent store: " << storePath() << "\n";

    // Show current store contents before we start
    std::cout << "\n[Before] Store contents:\n";
    printStoreContents();

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
        std::cout << "\n  >> Tool executed: get_status\n";
        return gaia::json{{"status", "ok"}, {"mode", "secure_agent"}, {"ready", true}};
    };

    registry.registerTool(std::move(status_info));

    // Register a tool with CONFIRM policy — user is prompted before each call
    gaia::ToolInfo dns_info;
    dns_info.name        = "flush_dns";
    dns_info.description = "Flush the system DNS cache";
    dns_info.policy      = gaia::ToolPolicy::CONFIRM;
    dns_info.callback    = [](const gaia::json& /*args*/) -> gaia::json {
        std::cout << "\n  >> Tool executed: DNS cache flushed.\n";
        return gaia::json{{"status", "ok"}, {"message", "DNS cache flushed"}};
    };

    registry.registerTool(std::move(dns_info));

    std::cout << "\n--- Call 0: ALLOW tool (runs immediately, no prompt) ---\n";
    auto r0 = registry.executeTool("get_status", {});
    std::cout << "  Result: " << r0.dump() << "\n";

    std::cout << "\n--- Call 1: CONFIRM tool (you will be prompted) ---\n";
    auto r1 = registry.executeTool("flush_dns", {});
    if (r1.contains("status") && r1["status"] == "error") {
        std::cout << "  Result: DENIED (" << r1.value("error", "") << ")\n";
    } else {
        std::cout << "  Result: " << r1.dump() << "\n";
    }

    std::cout << "\n--- Call 2: CONFIRM tool again ---\n";
    if (store->isAlwaysAllowed("flush_dns")) {
        std::cout << "flush_dns is in the always-allowed store — no prompt.\n";
    } else {
        std::cout << "Calling flush_dns again (not always-allowed yet).\n";
    }
    auto r2 = registry.executeTool("flush_dns", {});
    if (r2.contains("status") && r2["status"] == "error") {
        std::cout << "  Result: DENIED (" << r2.value("error", "") << ")\n";
    } else {
        std::cout << "  Result: " << r2.dump() << "\n";
    }

    // --- Show final store state ---
    std::cout << "\n[After] Store contents:\n";
    printStoreContents();

    if (store->isAlwaysAllowed("flush_dns")) {
        std::cout << "\nflush_dns is now permanently allowed.\n";
        std::cout << "To reset: run  gaia cache clear  or delete " << storePath() << "\n";
    }

    std::cout << "\n=== Secure Agent complete ===\n\n";
    return 0;
}
