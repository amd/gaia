// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/repl.h>
#include <gaia/agent.h>
#include <gaia/session.h>

#include <filesystem>
#include <string>
#include <vector>

using namespace gaia;
namespace fs = std::filesystem;

// ---------------------------------------------------------------------------
// Mock Agent — minimal subclass for REPL testing (no real LLM calls)
// ---------------------------------------------------------------------------

class ReplMockAgent : public Agent {
public:
    explicit ReplMockAgent(const AgentConfig& config = {}) : Agent(config) {
        init();
    }

    // Track whether clearHistory was called
    bool historyClearCalled = false;

    // Override clearHistory to track calls (clearHistory is non-virtual, so
    // we track via a tool or direct observation). Instead, we verify through
    // the /clear command's behavior.

protected:
    void registerTools() override {
        // Register a simple echo tool for testing
        toolRegistry().registerTool("echo", "Echo the input",
            [](const json& args) -> json {
                return json{{"echoed", args.value("message", "")}};
            },
            {});
    }

    std::string getSystemPrompt() const override {
        return "You are a test agent for REPL testing.";
    }
};

// ---------------------------------------------------------------------------
// Test fixture
// ---------------------------------------------------------------------------

class ReplRunnerTest : public ::testing::Test {
protected:
    AgentConfig config;
    std::unique_ptr<ReplMockAgent> agent;
    std::unique_ptr<ReplRunner> repl;

    void SetUp() override {
        config.silentMode = true;
        agent = std::make_unique<ReplMockAgent>(config);
        repl = std::make_unique<ReplRunner>(*agent);
    }
};

// ---------------------------------------------------------------------------
// 1. Built-in commands are registered on construction
// ---------------------------------------------------------------------------

TEST_F(ReplRunnerTest, BuiltinCommandsRegistered) {
    EXPECT_TRUE(repl->hasCommand("/clear"));
    EXPECT_TRUE(repl->hasCommand("/help"));
    EXPECT_TRUE(repl->hasCommand("/model"));
    EXPECT_TRUE(repl->hasCommand("/history"));
    EXPECT_TRUE(repl->hasCommand("/exit"));
    EXPECT_EQ(repl->commandCount(), 5u);
}

// ---------------------------------------------------------------------------
// 2. addCommand registers a custom command
// ---------------------------------------------------------------------------

TEST_F(ReplRunnerTest, AddCustomCommand) {
    EXPECT_FALSE(repl->hasCommand("/lint"));

    bool called = false;
    repl->addCommand("/lint", "Run linter",
        [&called](const std::string& /*args*/, Agent& /*agent*/) {
            called = true;
        });

    EXPECT_TRUE(repl->hasCommand("/lint"));
    EXPECT_EQ(repl->commandCount(), 6u);  // 5 built-in + 1 custom
}

// ---------------------------------------------------------------------------
// 3. tryDispatchCommand — slash command is dispatched
// ---------------------------------------------------------------------------

TEST_F(ReplRunnerTest, DispatchSlashCommand) {
    bool called = false;
    std::string receivedArgs;

    repl->addCommand("/test", "Test command",
        [&](const std::string& args, Agent& /*agent*/) {
            called = true;
            receivedArgs = args;
        });

    bool dispatched = repl->tryDispatchCommand("/test hello world");
    EXPECT_TRUE(dispatched);
    EXPECT_TRUE(called);
    EXPECT_EQ(receivedArgs, "hello world");
}

// ---------------------------------------------------------------------------
// 4. tryDispatchCommand — non-command returns false
// ---------------------------------------------------------------------------

TEST_F(ReplRunnerTest, DispatchNonCommand) {
    bool dispatched = repl->tryDispatchCommand("What is the weather?");
    EXPECT_FALSE(dispatched);
}

// ---------------------------------------------------------------------------
// 5. tryDispatchCommand — empty input returns false
// ---------------------------------------------------------------------------

TEST_F(ReplRunnerTest, DispatchEmptyInput) {
    bool dispatched = repl->tryDispatchCommand("");
    EXPECT_FALSE(dispatched);
}

// ---------------------------------------------------------------------------
// 6. tryDispatchCommand — unknown command handled gracefully (returns true)
// ---------------------------------------------------------------------------

TEST_F(ReplRunnerTest, DispatchUnknownCommand) {
    // Unknown commands are still recognized as command attempts (starts with /)
    // but print a message. They return true to prevent sending to LLM.
    bool dispatched = repl->tryDispatchCommand("/foobar");
    EXPECT_TRUE(dispatched);
}

// ---------------------------------------------------------------------------
// 7. tryDispatchCommand — /clear calls agent.clearHistory()
// ---------------------------------------------------------------------------

TEST_F(ReplRunnerTest, ClearCommandDispatch) {
    // /clear should not throw and should complete without error
    bool dispatched = repl->tryDispatchCommand("/clear");
    EXPECT_TRUE(dispatched);
}

// ---------------------------------------------------------------------------
// 8. tryDispatchCommand — /model with no args shows current model
// ---------------------------------------------------------------------------

TEST_F(ReplRunnerTest, ModelCommandNoArgs) {
    bool dispatched = repl->tryDispatchCommand("/model");
    EXPECT_TRUE(dispatched);
}

// ---------------------------------------------------------------------------
// 9. tryDispatchCommand — /model with arg changes model
// ---------------------------------------------------------------------------

TEST_F(ReplRunnerTest, ModelCommandWithArgs) {
    bool dispatched = repl->tryDispatchCommand("/model Qwen3-8B-GGUF");
    EXPECT_TRUE(dispatched);

    // Verify the model was changed
    EXPECT_EQ(agent->config().modelId, "Qwen3-8B-GGUF");
}

// ---------------------------------------------------------------------------
// 10. tryDispatchCommand — /help lists commands (smoke test)
// ---------------------------------------------------------------------------

TEST_F(ReplRunnerTest, HelpCommandDispatch) {
    bool dispatched = repl->tryDispatchCommand("/help");
    EXPECT_TRUE(dispatched);
}

// ---------------------------------------------------------------------------
// 11. tryDispatchCommand — /history without store prints message
// ---------------------------------------------------------------------------

TEST_F(ReplRunnerTest, HistoryWithoutStore) {
    // Should not throw even without a session store
    bool dispatched = repl->tryDispatchCommand("/history");
    EXPECT_TRUE(dispatched);
}

// ---------------------------------------------------------------------------
// 12. tryDispatchCommand — /history with store lists sessions
// ---------------------------------------------------------------------------

TEST_F(ReplRunnerTest, HistoryWithStore) {
    fs::path storeDir = fs::temp_directory_path() / "gaia_repl_test_history";
    fs::remove_all(storeDir);

    auto store = std::make_shared<SessionStore>(storeDir.string());
    repl->setSessionStore(store);

    bool dispatched = repl->tryDispatchCommand("/history");
    EXPECT_TRUE(dispatched);

    fs::remove_all(storeDir);
}

// ---------------------------------------------------------------------------
// 13. tryDispatchCommand — /exit sets exit flag
// ---------------------------------------------------------------------------

TEST_F(ReplRunnerTest, ExitCommandDispatch) {
    bool dispatched = repl->tryDispatchCommand("/exit");
    EXPECT_TRUE(dispatched);
    // exitRequested_ is private, but we verify the command was dispatched
}

// ---------------------------------------------------------------------------
// 14. addCommand — overwrite an existing command
// ---------------------------------------------------------------------------

TEST_F(ReplRunnerTest, OverwriteExistingCommand) {
    bool customCalled = false;

    repl->addCommand("/clear", "Custom clear",
        [&customCalled](const std::string& /*args*/, Agent& /*agent*/) {
            customCalled = true;
        });

    repl->tryDispatchCommand("/clear");
    EXPECT_TRUE(customCalled);
    // Command count should not increase (overwrite, not add)
    EXPECT_EQ(repl->commandCount(), 5u);
}

// ---------------------------------------------------------------------------
// 15. Command args are trimmed
// ---------------------------------------------------------------------------

TEST_F(ReplRunnerTest, CommandArgsTrimmed) {
    std::string receivedArgs;

    repl->addCommand("/test", "Test",
        [&receivedArgs](const std::string& args, Agent& /*agent*/) {
            receivedArgs = args;
        });

    repl->tryDispatchCommand("/test   padded args   ");
    EXPECT_EQ(receivedArgs, "padded args");
}

// ---------------------------------------------------------------------------
// 16. Command with no args passes empty string
// ---------------------------------------------------------------------------

TEST_F(ReplRunnerTest, CommandNoArgsPasses) {
    std::string receivedArgs = "NOT_CALLED";

    repl->addCommand("/test", "Test",
        [&receivedArgs](const std::string& args, Agent& /*agent*/) {
            receivedArgs = args;
        });

    repl->tryDispatchCommand("/test");
    // When there's no space after the command name, the callback is still called
    // with an empty string for args.
    EXPECT_EQ(receivedArgs, "");
}

// ---------------------------------------------------------------------------
// 17. setSessionStore and setResumeId — basic setter smoke test
// ---------------------------------------------------------------------------

TEST_F(ReplRunnerTest, SessionStoreSetters) {
    fs::path storeDir = fs::temp_directory_path() / "gaia_repl_test_setters";
    fs::remove_all(storeDir);

    auto store = std::make_shared<SessionStore>(storeDir.string());
    repl->setSessionStore(store);
    repl->setResumeId("test-session-123");

    // No crash — setters work
    SUCCEED();

    fs::remove_all(storeDir);
}

// ---------------------------------------------------------------------------
// 18. setShowBanner — setter works
// ---------------------------------------------------------------------------

TEST_F(ReplRunnerTest, SetShowBanner) {
    repl->setShowBanner(false);
    repl->setShowBanner(true);
    SUCCEED();  // No crash
}

// ---------------------------------------------------------------------------
// 19. Custom prompt in constructor
// ---------------------------------------------------------------------------

TEST(ReplRunnerStandaloneTest, CustomPrompt) {
    AgentConfig config;
    config.silentMode = true;
    ReplMockAgent agent(config);
    ReplRunner repl(agent, ">> ");

    // Verify built-in commands still registered with custom prompt
    EXPECT_TRUE(repl.hasCommand("/clear"));
    EXPECT_EQ(repl.commandCount(), 5u);
}

// ---------------------------------------------------------------------------
// 20. Multiple custom commands
// ---------------------------------------------------------------------------

TEST_F(ReplRunnerTest, MultipleCustomCommands) {
    int lintCalls = 0;
    int reviewCalls = 0;
    int deployCalls = 0;

    repl->addCommand("/lint", "Run linter",
        [&lintCalls](const std::string&, Agent&) { ++lintCalls; });
    repl->addCommand("/review", "Code review",
        [&reviewCalls](const std::string&, Agent&) { ++reviewCalls; });
    repl->addCommand("/deploy", "Deploy",
        [&deployCalls](const std::string&, Agent&) { ++deployCalls; });

    EXPECT_EQ(repl->commandCount(), 8u);  // 5 built-in + 3 custom

    repl->tryDispatchCommand("/lint");
    repl->tryDispatchCommand("/review");
    repl->tryDispatchCommand("/deploy");
    repl->tryDispatchCommand("/lint");

    EXPECT_EQ(lintCalls, 2);
    EXPECT_EQ(reviewCalls, 1);
    EXPECT_EQ(deployCalls, 1);
}
