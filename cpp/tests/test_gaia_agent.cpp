// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>
#include <random>

#include "../agents/gaia/gaia_agent.h"
#include "support/mock_llm_server.h"

using namespace gaia;
namespace fs = std::filesystem;

namespace {

std::string answer(const std::string& text) {
    return json{{"choices", json::array({{{"message", {{"role", "assistant"}, {"content", text}}}}})}}
        .dump();
}

struct Workdir {
    fs::path path;
    Workdir() {
        std::mt19937_64 rng(std::random_device{}());
        path = fs::current_path() / ("gaia-agent-test-" + std::to_string(rng()));
        fs::create_directories(path / "out");
    }
    ~Workdir() {
        std::error_code ec;
        fs::remove_all(path, ec);
    }
};

GaiaAgent::Options optionsFor(const Workdir& w) {
    GaiaAgent::Options o;
    o.skills = false;
    o.webTools = false;
    o.workspaceRoots = {w.path.string()};
    o.memoryPath = ":memory:";
    return o;
}

AgentConfig cfgFor(const bench::MockLlmServer& mock) {
    AgentConfig c;
    c.baseUrl = mock.baseUrl();
    c.modelId = "Gemma-4-E4B-it-GGUF";
    c.silentMode = true;
    c.streaming = false;
    c.maxSteps = 6;
    return c;
}

}  // namespace

TEST(GaiaAgentTest, ClaimedFilesAreFound) {
    auto f = GaiaAgent::claimedFiles(
        "Done. I saved the table to `totals.md` and wrote out/summary.csv. "
        "Also created C:\\Users\\Me\\deck.pptx. The input data.csv was not changed.");
    ASSERT_EQ(f.size(), 3u);
    EXPECT_EQ(f[0], "totals.md");
    EXPECT_EQ(f[1], "out/summary.csv");
    EXPECT_EQ(f[2], "C:\\Users\\Me\\deck.pptx");
    EXPECT_TRUE(GaiaAgent::claimedFiles("The report covers 2025.").empty());
}

TEST(GaiaAgentTest, MissingClaimedFileSendsTheAnswerBackOnce) {
    Workdir w;
    bench::MockLlmServer mock;
    mock.pushResponse(answer("All done: I saved the summary to report.md."));
    mock.pushResponse(answer("Correction: I could not write report.md; here is the summary inline."));
    GaiaAgent agent(cfgFor(mock), optionsFor(w));
    json r = agent.processQuery("summarize");
    EXPECT_EQ(r["result"], "Correction: I could not write report.md; here is the summary inline.");
    const auto bodies = mock.receivedBodies();
    ASSERT_EQ(bodies.size(), 2u);
    EXPECT_NE(bodies[1].find("no such file exists"), std::string::npos);
}

TEST(GaiaAgentTest, ExistingFileInASubfolderIsAccepted) {
    Workdir w;
    std::ofstream(w.path / "out" / "report.md") << "# Report\n";
    bench::MockLlmServer mock;
    mock.pushResponse(answer("Saved report.md in the out folder."));
    GaiaAgent agent(cfgFor(mock), optionsFor(w));
    json r = agent.processQuery("summarize");
    EXPECT_EQ(r["result"], "Saved report.md in the out folder.");
    EXPECT_EQ(mock.receivedBodies().size(), 1u);
}

TEST(GaiaAgentTest, ToolsAreConfinedByDefault) {
    Workdir w;
    bench::MockLlmServer mock;
    GaiaAgent agent(cfgFor(mock), optionsFor(w));
    agent.toolRegistry().setConfirmCallback(
        [](const std::string&, const json&) { return ToolConfirmResult::ALLOW_ONCE; });
    json r = agent.toolRegistry().executeTool("file_read", json{{"path", "/etc/hostname"}});
    EXPECT_EQ(r["status"], "error");
    EXPECT_TRUE(agent.tools().hasTool("query_documents"));
}

TEST(GaiaAgentTest, AnalyzeImageSendsTheImageToTheModel) {
    Workdir w;
    // Smallest valid PNG: 1x1 transparent pixel.
    const unsigned char png[] = {
        0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
        0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4,
        0x89, 0x00, 0x00, 0x00, 0x0A, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9C, 0x63, 0x00, 0x01, 0x00, 0x00,
        0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE,
        0x42, 0x60, 0x82};
    std::ofstream(w.path / "dot.png", std::ios::binary).write(reinterpret_cast<const char*>(png), sizeof png);
    bench::MockLlmServer mock;
    mock.pushResponse(answer("A single transparent pixel."));
    GaiaAgent agent(cfgFor(mock), optionsFor(w));
    json r = agent.toolRegistry().executeTool(
        "analyze_image", json{{"path", (w.path / "dot.png").string()}, {"question", "What is this?"}});
    ASSERT_EQ(r["status"], "success") << r.dump();
    EXPECT_EQ(r["answer"], "A single transparent pixel.");
    const json body = json::parse(mock.receivedBodies().back());
    EXPECT_FALSE(body.contains("tools"));
    const auto& parts = body["messages"][0]["content"];
    bool hasImage = false;
    for (const auto& p : parts) {
        hasImage |= p["type"] == "image_url" &&
                    p["image_url"]["url"].get<std::string>().rfind("data:image/png;base64,", 0) == 0;
    }
    EXPECT_TRUE(hasImage);

    json outside = agent.toolRegistry().executeTool(
        "analyze_image", json{{"path", "/etc/hostname"}, {"question", "?"}});
    EXPECT_EQ(outside["status"], "error");
}
