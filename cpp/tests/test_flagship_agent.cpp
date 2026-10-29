// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// End-to-end: the flagship agent against a mock Lemonade, from the request it
// sends to the answer it returns.

#include <gtest/gtest.h>

#include "flagship_agent.h"
#include "support/fake_embedder.h"
#include "support/mock_llm_server.h"

#include <gaia/file_tools.h>
#include <gaia/flagship_config.h>

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iterator>

using namespace gaia;
namespace fs = std::filesystem;

namespace {

std::string toolCall(const std::string& id, const std::string& name, const json& args) {
    json resp = {{"choices",
                  json::array({{{"message",
                                 {{"role", "assistant"},
                                  {"content", nullptr},
                                  {"tool_calls", json::array({{{"id", id},
                                                               {"type", "function"},
                                                               {"function", {{"name", name}, {"arguments", args.dump()}}}}})}}},
                                {"finish_reason", "tool_calls"}}})}};
    return resp.dump();
}

std::string answer(const std::string& text) {
    return json{{"choices", json::array({{{"message", {{"role", "assistant"}, {"content", text}}}}})}}.dump();
}

const char* const kVoiceSkill =
    "---\nname: gaia-voice\ndescription: How GAIA talks.\n---\n\nNever claim work you did not do.\n";
const char* const kDataSkill =
    "---\nname: data-explore\ndescription: Explore CSV files with SQL.\nmetadata:\n  gaia:\n"
    "    tools_required:\n      - query_data\n---\n\nLoad the CSV into a table, then query it.\n";

class FlagshipAgentTest : public ::testing::Test {
protected:
    fs::path root_;
    void SetUp() override {
        root_ = fs::weakly_canonical(fs::temp_directory_path()) / "gaia_flagship_agent_test";
        std::error_code ec;
        fs::remove_all(root_, ec);
        fs::create_directories(root_ / "work");
        FileStateTracker::instance().clear();
    }
    void TearDown() override {
        FileStateTracker::instance().clear();
        std::error_code ec;
        fs::remove_all(root_, ec);
    }

    AgentConfig cfg(const bench::MockLlmServer& mock) {
        AgentConfig c = flagshipConfig();
        c.baseUrl = mock.baseUrl();
        c.modelId = "Gemma-4-E4B-it-GGUF";  // a native tool-calling model
        c.silentMode = true;
        c.streaming = false;
        return c;
    }

    FlagshipOptions opts(bool fast = false) {
        FlagshipOptions o;
        o.fast = fast;
        o.workDir = (root_ / "work").u8string();
        o.allowedPaths = {o.workDir};
        o.memoryDb = ":memory:";
        o.web = false;
        o.sessionId = "test";
        o.embeddedSkills = {{"gaia-voice", kVoiceSkill}, {"data-explore", kDataSkill}};
        o.skipDefaultSkillRoots = true;
        o.embedder = gaia_test::fakeEmbed;
        return o;
    }
};

}  // namespace

TEST_F(FlagshipAgentTest, FlagshipDefaultsMatchPython) {
    AgentConfig c = flagshipConfig();
    EXPECT_EQ(c.maxSteps, 150);
    EXPECT_EQ(c.contextSize, 65536);
    EXPECT_EQ(c.maxTokens, 16384);
    EXPECT_TRUE(c.toolOutputPaging);
    EXPECT_TRUE(c.stripThinking);
    EXPECT_TRUE(c.stepBudgetGuidance);
    EXPECT_EQ(c.malformedToolCallRetries, 3);
    EXPECT_FALSE(c.legacyEnvOverrides);
}

TEST_F(FlagshipAgentTest, ReadsAFileWithANativeToolCallAndAnswers) {
    std::ofstream(root_ / "work" / "todo.txt") << "buy milk\n";
    bench::MockLlmServer mock;
    mock.pushResponse(toolCall("call_1", "read_file", {{"file_path", "todo.txt"}}));
    mock.pushResponse(answer("Your list says: buy milk."));
    FlagshipAgent agent(cfg(mock), opts());
    agent.loadStartupSkills({});

    json r = agent.processQuery("what's on my todo list?");
    EXPECT_EQ(r["result"], "Your list says: buy milk.");

    const auto bodies = mock.receivedBodies();
    ASSERT_EQ(bodies.size(), 2u);
    const json first = json::parse(bodies[0]);
    // Native tools, the Python names, and the flagship prompt.
    std::set<std::string> names;
    for (const auto& t : first["tools"]) names.insert(t["function"]["name"].get<std::string>());
    for (const char* t : {"read_file", "write_file", "edit_file", "run_python", "remember", "recall",
                          "load_skill", "read_tool_output", "query_data"}) {
        EXPECT_TRUE(names.count(t)) << t;
    }
    EXPECT_FALSE(names.count("search_web"));  // web off in these options
    const std::string system = first["messages"][0]["content"];
    EXPECT_NE(system.find("You are GAIA"), std::string::npos);
    EXPECT_NE(system.find("==== SOURCING ===="), std::string::npos);
    EXPECT_NE(system.find("- data-explore: Explore CSV files with SQL."), std::string::npos);
    EXPECT_NE(system.find("=== MEMORY (Persistent Second Brain) ==="), std::string::npos);
    EXPECT_NE(system.find("==== LOADED SKILLS ====\n--- SKILL: gaia-voice ---"), std::string::npos);
    EXPECT_DOUBLE_EQ(first["temperature"].get<double>(), 0.1);

    // The tool result went back as a role=tool reply carrying the file.
    const json second = json::parse(bodies[1]);
    bool sawResult = false;
    for (const auto& m : second["messages"]) {
        if (m.value("role", "") == "tool" && m.value("tool_call_id", "") == "call_1") {
            sawResult = m["content"].get<std::string>().find("buy milk") != std::string::npos;
        }
    }
    EXPECT_TRUE(sawResult);
}

TEST_F(FlagshipAgentTest, LoadSkillMidTurnKeepsTheSystemPromptAndJoinsItNextTurn) {
    bench::MockLlmServer mock;
    mock.pushResponse(toolCall("c1", "load_skill", {{"name", "data-explore"}}));
    mock.pushResponse(answer("Loaded."));
    mock.pushResponse(answer("Next turn."));
    FlagshipAgent agent(cfg(mock), opts());
    agent.processQuery("explore my sales csv");
    agent.processQuery("and again");
    const auto bodies = mock.receivedBodies();
    ASSERT_EQ(bodies.size(), 3u);
    const json second = json::parse(bodies[1]);
    // Mid-turn the system prompt is unchanged, so the server keeps its cached prefix...
    EXPECT_EQ(json::parse(bodies[0])["messages"][0]["content"], second["messages"][0]["content"]);
    // ...and the skill's instructions reach the model in load_skill's result.
    bool inResult = false;
    for (const auto& m : second["messages"]) {
        if (m.value("role", "") == "tool") {
            inResult = json::parse(m["content"].get<std::string>()).value("instructions", "") ==
                       "Load the CSV into a table, then query it.";
        }
    }
    EXPECT_TRUE(inResult);
    const std::string next = json::parse(bodies[2])["messages"][0]["content"];
    EXPECT_NE(next.find("--- SKILL: data-explore ---\nLoad the CSV into a table"), std::string::npos);
    // History keeps no second copy, which would outlive unload_skill.
    for (const auto& m : json::parse(bodies[2])["messages"]) {
        if (m.value("role", "") != "tool") continue;
        const json r = json::parse(m["content"].get<std::string>());
        EXPECT_FALSE(r.contains("instructions"));
        EXPECT_TRUE(r.contains("instructions_note"));
    }
}

TEST_F(FlagshipAgentTest, SystemPromptIsTheSameInEveryFolderAndTheFolderRidesWithTheRequest) {
    fs::create_directories(root_ / "other");
    bench::MockLlmServer mock;
    mock.pushResponse(answer("One."));
    mock.pushResponse(answer("Two."));
    mock.pushResponse(answer("Three."));
    FlagshipAgent a(cfg(mock), opts());
    a.loadStartupSkills({});
    a.processQuery("first");
    a.processQuery("second");
    FlagshipOptions o = opts();
    o.workDir = (root_ / "other").u8string();
    o.allowedPaths = {o.workDir};
    FlagshipAgent b(cfg(mock), o);
    b.loadStartupSkills({});
    b.processQuery("third");

    const auto bodies = mock.receivedBodies();
    ASSERT_EQ(bodies.size(), 3u);
    const json first = json::parse(bodies[0]);
    const json second = json::parse(bodies[1]);
    const json other = json::parse(bodies[2]);
    const std::string system = first["messages"][0]["content"];
    EXPECT_EQ(system, other["messages"][0]["content"]);
    EXPECT_EQ(system.find((root_ / "work").u8string()), std::string::npos);

    const std::string ask = first["messages"].back()["content"];
    EXPECT_EQ(ask.rfind("[Context for this request]\n- Working folder: " + (root_ / "work").u8string(), 0), 0u);
    EXPECT_NE(ask.find("[End of context]\n\nfirst"), std::string::npos);
    EXPECT_NE(std::string(other["messages"].back()["content"]).find((root_ / "other").u8string()),
              std::string::npos);
    // Turn 2 starts with turn 1's exact messages, so the server's cache covers all of turn 1.
    const json& msgs = second["messages"];
    ASSERT_EQ(msgs.size(), 4u);
    EXPECT_EQ(msgs[1], first["messages"][1]);
    EXPECT_EQ(msgs[2]["content"], "One.");
    EXPECT_EQ(std::string(msgs.back()["content"]).rfind("[Context for this request]", 0), 0u);
    // The session list still previews the user's own words.
    EXPECT_EQ(stripTurnContext(a.history().front().content), "first");
    EXPECT_EQ(stripTurnContext("plain words"), "plain words");
}

TEST_F(FlagshipAgentTest, OversizedResultIsPagedNotLost) {
    std::string big;
    for (int i = 0; i < 9000; ++i) big += "line " + std::to_string(i) + "\n";
    std::ofstream(root_ / "work" / "big.log") << big;
    bench::MockLlmServer mock;
    mock.pushResponse(toolCall("c1", "read_file", {{"file_path", "big.log"}}));
    mock.pushResponse(answer("ok"));
    AgentConfig c = cfg(mock);
    c.contextSize = 32768;  // threshold 30000 chars
    FlagshipAgent agent(c, opts());
    agent.processQuery("summarize big.log");
    const json second = json::parse(mock.receivedBodies()[1]);
    std::string toolText;
    for (const auto& m : second["messages"]) {
        if (m.value("role", "") == "tool") toolText = m["content"];
    }
    const json view = json::parse(toolText);
    ASSERT_TRUE(view.value("truncated", false)) << toolText.substr(0, 200);
    const json page = agent.toolRegistry().executeTool("read_tool_output", {{"artifact", view["artifact"]}, {"entry", 1}});
    EXPECT_FALSE(page["content"].get<std::string>().empty());
    EXPECT_EQ(page["total_chars"].get<std::size_t>(), view["original_chars"].get<std::size_t>());
}

TEST_F(FlagshipAgentTest, ThinkBlocksNeverReachTheAnswer) {
    bench::MockLlmServer mock;
    mock.pushResponse(answer("<think>the user wants a joke</think>Why did the NPU cross the road?"));
    FlagshipAgent agent(cfg(mock), opts());
    EXPECT_EQ(agent.processQuery("tell me a joke")["result"], "Why did the NPU cross the road?");
}

TEST_F(FlagshipAgentTest, MalformedToolArgumentsGetARetryPrompt) {
    bench::MockLlmServer mock;
    mock.pushResponse(R"({"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"c1","type":"function","function":{"name":"read_file","arguments":"[1,2]"}}]}}]})");
    mock.pushResponse(answer("Recovered."));
    FlagshipAgent agent(cfg(mock), opts());
    EXPECT_EQ(agent.processQuery("read it")["result"], "Recovered.");
    const json second = json::parse(mock.receivedBodies()[1]);
    const std::string last = second["messages"].back()["content"];
    EXPECT_NE(last.find("Your last tool call could not be used"), std::string::npos);
}

TEST_F(FlagshipAgentTest, BudgetNoticeAndClosingAnswerAtTheStepCap) {
    bench::MockLlmServer mock;
    // Eight distinct calls (no loop detection), then the closing no-tools reply.
    for (int i = 0; i < 8; ++i) {
        mock.pushResponse(toolCall("c" + std::to_string(i), "browse_directory", {{"sort_by", i % 2 ? "size" : "name"}, {"show_hidden", i < 4}}));
    }
    mock.pushResponse(answer("Here is what I found so far."));
    AgentConfig c = cfg(mock);
    c.maxSteps = 8;
    FlagshipAgent agent(c, opts());
    json r = agent.processQuery("look around");
    EXPECT_EQ(r["result"], "Here is what I found so far.");
    const auto bodies = mock.receivedBodies();
    ASSERT_EQ(bodies.size(), 9u);
    bool sawNotice = false;
    for (const auto& b : bodies) sawNotice |= b.find("Step budget:") != std::string::npos;
    EXPECT_TRUE(sawNotice);
    const json closing = json::parse(bodies.back());
    EXPECT_EQ(closing["tool_choice"], "none");
    EXPECT_NE(closing["messages"].back()["content"].get<std::string>().find("You have used all 8 steps"), std::string::npos);
    // Transient guidance is never saved into history.
    for (const auto& m : agent.history()) {
        EXPECT_EQ(m.content.find("Step budget:"), std::string::npos);
        EXPECT_EQ(m.content.find("You have used all"), std::string::npos);
    }
}

TEST_F(FlagshipAgentTest, TurnsAreLoggedForSearchPastConversations) {
    bench::MockLlmServer mock;
    mock.pushResponse(answer("Lisbon in spring is lovely."));
    FlagshipAgent agent(cfg(mock), opts());
    agent.processQuery("I'm planning a trip to Lisbon");
    ASSERT_NE(agent.memory(), nullptr);
    EXPECT_EQ(agent.memory()->searchPastConversations({{"query", "Lisbon"}})["count"], 2);
}

TEST_F(FlagshipAgentTest, FastModeIsConversationOnly) {
    bench::MockLlmServer mock;
    mock.pushResponse(answer("Hey!"));
    FlagshipAgent agent(cfg(mock), opts(/*fast=*/true));
    EXPECT_TRUE(agent.toolRegistry().enabledTools().empty());
    EXPECT_THROW(agent.loadStartupSkills({"data-explore"}), SkillError);
    agent.processQuery("hi");
    const json body = json::parse(mock.receivedBodies()[0]);
    EXPECT_FALSE(body.contains("tools"));
    const std::string system = body["messages"][0]["content"];
    EXPECT_EQ(system.find("==== SKILLS ===="), std::string::npos);
    EXPECT_EQ(system.find("=== MEMORY (Persistent Second Brain) ==="), std::string::npos);
}

TEST_F(FlagshipAgentTest, MediaToolsAreRegisteredAndTheirSkillsListed) {
    bench::MockLlmServer mock;
    FlagshipOptions o = opts();
    o.embeddedSkills.push_back({"image-gen", R"(---
name: image-gen
description: Draw pictures.
metadata:
  gaia:
    tools_required:
      - generate_image
      - list_sd_models
      - get_generation_history
---

Draw.
)"});
    o.embeddedSkills.push_back({"transcribe-meeting", R"(---
name: transcribe-meeting
description: Transcribe a meeting.
metadata:
  gaia:
    tools_required:
      - transcribe_media
      - refine_transcript
      - summarize_document
      - query_documents
      - remember
---

Go.
)"});
    FlagshipAgent agent(cfg(mock), o);
    for (const char* t : {"generate_image", "list_sd_models", "get_generation_history", "transcribe_media",
                          "refine_transcript", "take_screenshot"}) {
        EXPECT_TRUE(agent.toolRegistry().hasTool(t)) << t;
    }
    const std::string catalog = agent.skillManager().catalogPrompt();
    EXPECT_NE(catalog.find("image-gen"), std::string::npos) << catalog;
    EXPECT_NE(catalog.find("transcribe-meeting"), std::string::npos) << catalog;

    FlagshipAgent fast(cfg(mock), opts(/*fast=*/true));
    EXPECT_FALSE(fast.toolRegistry().hasTool("generate_image"));
    EXPECT_FALSE(fast.toolRegistry().hasTool("take_screenshot"));
}

TEST_F(FlagshipAgentTest, RefineTranscriptAsksTheSessionModelAndIndexesTheResult) {
    bench::MockLlmServer mock;
    mock.pushResponse(answer("1: Speaker A"));
    FlagshipAgent agent(cfg(mock), opts());
    const fs::path raw = root_ / "work" / "call.txt";
    std::ofstream(raw, std::ios::binary) << "Hello there.";
    std::ofstream(fs::u8path(raw.u8string() + ".timing.json"), std::ios::binary)
        << R"({"segments":[{"start":0,"end":1,"text":"Hello there."}],"speakers":[]})";
    json r = agent.toolbelt().refineTranscript({{"transcript_path", raw.u8string()}});
    ASSERT_EQ(r["status"], "success") << r.dump();
    ASSERT_EQ(mock.receivedBodies().size(), 1u);
    const json body = json::parse(mock.receivedBodies()[0]);
    EXPECT_EQ(body["model"], "Gemma-4-E4B-it-GGUF");
    EXPECT_DOUBLE_EQ(body["temperature"].get<double>(), 0.2);
    EXPECT_EQ(body["max_completion_tokens"], 4096);
    ASSERT_EQ(body["messages"].size(), 1u);
    EXPECT_EQ(body["messages"][0]["role"], "user");
    EXPECT_NE(body["messages"][0]["content"].get<std::string>().find("1. Hello there."), std::string::npos);
    EXPECT_EQ(r["speakers"], (json{"Speaker A"}));
    EXPECT_EQ(r["indexed"], true) << r.dump();
}

TEST_F(FlagshipAgentTest, RefineTranscriptReportsAnEmptyModelReply) {
    bench::MockLlmServer mock;
    mock.pushResponse(R"({"choices":[{"message":{"role":"assistant","content":""},"finish_reason":"length"}]})");
    FlagshipAgent agent(cfg(mock), opts());
    const fs::path raw = root_ / "work" / "call.txt";
    std::ofstream(raw, std::ios::binary) << "Hello there.";
    std::ofstream(fs::u8path(raw.u8string() + ".timing.json"), std::ios::binary)
        << R"({"segments":[{"start":0,"end":1,"text":"Hello there."}],"speakers":[]})";
    json r = agent.toolbelt().refineTranscript({{"transcript_path", raw.u8string()}});
    EXPECT_EQ(r["status"], "error");
    EXPECT_NE(r["error"].get<std::string>().find("finish_reason='length'"), std::string::npos) << r.dump();
}

TEST_F(FlagshipAgentTest, HistoryStubsOldToolOutputToFitTheWindow) {
    // Four ~10K-char (paged) results overflow a 16K window's 8K-token history budget.
    bench::MockLlmServer mock;
    for (int i = 0; i < 4; ++i) {
        const std::string name = "huge" + std::to_string(i) + ".txt";
        std::ofstream(root_ / "work" / name) << std::string(40000, 'h');
        mock.pushResponse(toolCall("c" + std::to_string(i), "read_file", {{"file_path", name}}));
    }
    mock.pushResponse(answer("It's all h."));
    AgentConfig c = cfg(mock);
    c.contextSize = 16384;
    FlagshipAgent agent(c, opts());
    agent.processQuery("what's in huge.txt?");
    bool stubbed = false;
    for (const auto& m : agent.history()) {
        stubbed |= m.content.find("Left out of the conversation history") != std::string::npos;
        EXPECT_LT(m.content.size(), 30000u);
    }
    EXPECT_TRUE(stubbed);
    // The question and the answer survive.
    EXPECT_EQ(stripTurnContext(agent.history().front().content), "what's in huge.txt?");
    EXPECT_EQ(agent.history().back().content, "It's all h.");
}

TEST_F(FlagshipAgentTest, UnknownStartupSkillIsALoudError) {
    bench::MockLlmServer mock;
    FlagshipAgent agent(cfg(mock), opts());
    EXPECT_THROW(agent.loadStartupSkills({"no-such-skill"}), SkillError);
}

namespace {
std::string cutOff(const std::string& text) {
    return json{{"choices", json::array({{{"message", {{"role", "assistant"}, {"content", text}}}, {"finish_reason", "length"}}})}}.dump();
}
std::string lastUserMessage(const std::string& body) {
    const json b = json::parse(body);
    for (auto it = b["messages"].rbegin(); it != b["messages"].rend(); ++it) {
        if ((*it)["role"] == "user") return (*it)["content"].get<std::string>();
    }
    return "";
}
}  // namespace

TEST_F(FlagshipAgentTest, NarratedNextStepIsSentBack) {
    bench::MockLlmServer mock;
    mock.pushResponse(answer("I found the folder. Let me check the config file next."));
    mock.pushResponse(answer("The port is 8080."));
    FlagshipAgent agent(cfg(mock), opts());
    EXPECT_EQ(agent.processQuery("what port does the app use?")["result"], "The port is 8080.");
    EXPECT_NE(lastUserMessage(mock.receivedBodies()[1]).find("called no tools"), std::string::npos);
}

TEST_F(FlagshipAgentTest, ToolMarkupWrittenAsTextIsSentBack) {
    bench::MockLlmServer mock;
    mock.pushResponse(answer("Sure: {\"tool\": \"read_file\", \"tool_args\": {\"file_path\": \"a.txt\"}}"));
    mock.pushResponse(answer("Done."));
    FlagshipAgent agent(cfg(mock), opts());
    EXPECT_EQ(agent.processQuery("hello")["result"], "Done.");
    EXPECT_NE(lastUserMessage(mock.receivedBodies()[1]).find("tool call written out as text"), std::string::npos);
}

TEST_F(FlagshipAgentTest, AnswerAboutAnUnopenedWorkspaceFileMustLookFirst) {
    std::ofstream(root_ / "work" / "sales.csv") << "region,revenue\nwest,10\n";
    bench::MockLlmServer mock;
    mock.pushResponse(answer("West had the most revenue."));
    mock.pushResponse(toolCall("c1", "read_file", {{"file_path", "sales.csv"}}));
    mock.pushResponse(answer("West: 10."));
    FlagshipAgent agent(cfg(mock), opts());
    // The guard resolves names against the process's working folder.
    const auto saved = fs::current_path();
    fs::current_path(root_ / "work");
    const json r = agent.processQuery("which region is top in sales.csv?");
    fs::current_path(saved);
    EXPECT_EQ(r["result"], "West: 10.");
    EXPECT_NE(lastUserMessage(mock.receivedBodies()[1]).find("You answered without opening anything"), std::string::npos);
}

TEST_F(FlagshipAgentTest, CutOffReplyContinuesThenGivesUpLoudly) {
    bench::MockLlmServer mock;
    mock.pushResponse(cutOff("The analysis shows that"));
    mock.pushResponse(cutOff("and furthermore the"));
    mock.pushResponse(cutOff("so in conclusion"));
    FlagshipAgent agent(cfg(mock), opts());
    const std::string result = agent.processQuery("analyze everything")["result"];
    EXPECT_NE(result.find("kept getting cut off"), std::string::npos) << result;
    EXPECT_EQ(mock.receivedBodies().size(), 3u);
    EXPECT_NE(lastUserMessage(mock.receivedBodies()[1]).find("cut off at the output-token limit"), std::string::npos);
}

TEST_F(FlagshipAgentTest, OrdinaryAnswersPassStraightThrough) {
    bench::MockLlmServer mock;
    mock.pushResponse(answer("Paris is the capital of France. Let me know if you need more."));
    FlagshipAgent agent(cfg(mock), opts());
    EXPECT_EQ(agent.processQuery("capital of france?")["result"], "Paris is the capital of France. Let me know if you need more.");
    EXPECT_EQ(mock.receivedBodies().size(), 1u);
}

namespace {

class ScopedEnv {
public:
    ScopedEnv(const char* name, const std::string& value) : name_(name), saved_(getEnvVar(name)) { set(value); }
    ~ScopedEnv() { set(saved_); }

private:
    void set(const std::string& value) {
#ifdef _WIN32
        _putenv_s(name_, value.c_str());
#else
        if (value.empty()) unsetenv(name_); else setenv(name_, value.c_str(), 1);
#endif
    }
    const char* name_;
    std::string saved_;
};

}  // namespace

TEST_F(FlagshipAgentTest, PersistedAlwaysAllowIsIgnoredAndNeverWritten) {
    const fs::path home = root_ / "home";
    const fs::path store = home / ".gaia" / "security" / "allowed_tools.json";
    fs::create_directories(store.parent_path());
    const std::string persisted = R"({"version":1,"allowed_tools":["write_file","run_shell_command"]})";
    std::ofstream(store) << persisted;
    ScopedEnv profile("USERPROFILE", home.u8string());
    ScopedEnv posixHome("HOME", home.u8string());

    bench::MockLlmServer mock;
    FlagshipAgent agent(cfg(mock), opts());
    int asked = 0;
    ToolConfirmResult answer = ToolConfirmResult::DENY;
    agent.setToolConfirmCallback([&](const std::string&, const json&) {
        ++asked;
        return answer;
    });

    const std::string target = (root_ / "work" / "a.txt").u8string();
    agent.toolRegistry().executeTool("write_file", {{"file_path", target}, {"content", "x"}});
    EXPECT_EQ(asked, 1);  // the file's "always" did not skip the prompt
    EXPECT_FALSE(fs::exists(target));

    // "Always" on the shell allows that call only.
    answer = ToolConfirmResult::ALWAYS_ALLOW;
    asked = 0;
    agent.toolRegistry().executeTool("run_shell_command", {{"command", "echo one"}});
    agent.toolRegistry().executeTool("run_shell_command", {{"command", "echo two"}});
    EXPECT_EQ(asked, 2);

    // An edit grant lasts the session and never reaches the file.
    asked = 0;
    agent.toolRegistry().executeTool("write_file", {{"file_path", target}, {"content", "1"}});
    agent.toolRegistry().executeTool("write_file", {{"file_path", target}, {"content", "2"}});
    EXPECT_EQ(asked, 1);
    std::ifstream in(store);
    const std::string onDisk((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    EXPECT_EQ(onDisk, persisted);
}

TEST_F(FlagshipAgentTest, UnreadableEmbeddedServerRecordIsAnError) {
    const fs::path gaiaHome = root_ / "gaia-home";
    fs::create_directories(gaiaHome / "lemonade");
    std::ofstream(gaiaHome / "lemonade" / "state.json") << "{not json";
    ScopedEnv home("GAIA_HOME", gaiaHome.u8string());
    ScopedEnv url("LEMONADE_BASE_URL", "");
    try {
        resolveLemonadeEndpoint();
        FAIL() << "a corrupt state.json must not fall back to the default port";
    } catch (const std::runtime_error& e) {
        EXPECT_NE(std::string(e.what()).find("state.json"), std::string::npos) << e.what();
    }
    // An explicit URL does not depend on the record.
    ScopedEnv explicitUrl("LEMONADE_BASE_URL", "http://127.0.0.1:9");
    EXPECT_EQ(resolveLemonadeEndpoint().source, "env");
}
