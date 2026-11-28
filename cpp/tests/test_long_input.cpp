// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A request holding a very long unbroken run (letters, hex, a slash-joined path) must cost time
// in step with its length: checked on the functions that read it, through `gaiacpp -p`, and
// through `gaiacpp serve`, which must also stay stoppable.

#include <gtest/gtest.h>

#include <chrono>
#include <filesystem>
#include <fstream>
#include <map>
#include <regex>
#include <sstream>
#include <string>
#include <thread>

#include <httplib.h>

#ifndef _WIN32
#include <csignal>
#include <unistd.h>
#endif

#include "gaia/agent.h"
#include "gaia/api_server.h"
#include "gaia/process.h"
#include "gaia/types.h"
#include "../agents/gaiacpp/gaia_agent.h"
#include "support/child_process.h"
#include "support/mock_llm_server.h"

using gaia::json;

namespace {

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;

double secondsSince(Clock::time_point start) {
    return std::chrono::duration<double>(Clock::now() - start).count();
}

std::string repeated(const std::string& unit, size_t bytes) {
    std::string out;
    out.reserve(bytes + unit.size());
    while (out.size() < bytes) out += unit;
    out.resize(bytes);
    return out;
}

// Long unbroken runs of several kinds, and ones built to defeat a cut at a fixed run length.
std::map<std::string, std::string> pathologicalShapes(size_t bytes) {
    return {{"letters", std::string(bytes, 'A')},
            {"hex", repeated("0123456789abcdef", bytes)},
            {"path segments", repeated("seg/", bytes)},
            {"dotted name", repeated("a.b-c~d&e", bytes)},
            {"runs of 255", repeated(std::string(255, 'A') + " ", bytes)},
            {"figures", repeated("12,345.67;", bytes)},
            {"words", repeated("lorem ipsum dolor ", bytes)},
            {"multibyte", repeated("\xC3\xA9", bytes)}};
}

std::string finalAnswer(const std::string& text) {
    return json{{"choices", json::array({{{"message", {{"role", "assistant"}, {"content", text}}}}})},
                {"usage", {{"prompt_tokens", 10}, {"completion_tokens", 2}, {"total_tokens", 12}}}}
        .dump();
}

TEST(LongInput, TextForChecksBoundsLengthAndRuns) {
    for (const auto& [name, text] : pathologicalShapes(1 << 20)) {
        const auto start = Clock::now();
        const std::string out = gaia::textForChecks(text);
        EXPECT_LT(secondsSince(start), 0.5) << name;
        EXPECT_LE(out.size(), 64u * 1024 + 1024) << name;
        size_t run = 0, longest = 0;
        for (const char c : out) {
            run = (c == ' ' || c == '\n') ? 0 : run + 1;
            longest = (std::max)(longest, run);
        }
        // A cut waits for a character boundary, so a run may pass the limit by one character.
        EXPECT_LE(longest, 256u + 4) << name;
    }
}

TEST(LongInput, TextForChecksLeavesOrdinaryTextAlone) {
    const std::string request = "Summarise report.md and the totals in data/q3-sales.csv, then write notes.txt.\n\n"
                                "Keep it under 200 words. Caf\xC3\xA9 names stay as written.";
    EXPECT_EQ(gaia::textForChecks(request), request);
    EXPECT_EQ(gaia::textForChecks(""), "");
}

TEST(LongInput, TextForChecksKeepsTheStartAndTheEndOfALongRequest) {
    const std::string text = "FIRST words " + repeated("filler text ", 300000) + " LAST words";
    const std::string out = gaia::textForChecks(text);
    EXPECT_EQ(out.rfind("FIRST words", 0), 0u);
    EXPECT_NE(out.find("LAST words"), std::string::npos);
    EXPECT_LT(out.size(), text.size());
}

TEST(LongInput, TextForChecksNeverSplitsACharacter) {
    for (const size_t limit : {7u, 8u, 9u, 255u, 256u, 257u}) {
        const std::string out = gaia::textForChecks(repeated("\xE2\x82\xAC", 3000), 64 * 1024, limit);
        for (size_t i = 0; i < out.size();) {
            if (out[i] == ' ') {
                ++i;
                continue;
            }
            ASSERT_EQ(out.compare(i, 3, "\xE2\x82\xAC"), 0) << "broken character at " << i << ", limit " << limit;
            i += 3;
        }
    }
}

TEST(LongInput, NamedFilesMatchesThePatternItReplaced) {
    static const std::regex namedFile(
        R"([\w.&~-]+\.(?:md|txt|csv|tsv|xlsx?|docx?|pdf|pptx?|json|ya?ml|log|html?|xml|ini|toml|eml|zip)\b)",
        std::regex::icase);
    for (const std::string text :
         {"open report.md and Q3.Sales-final.XLSX please", "a.b.c.txt.bak notes.txt, data.csv; x.yaml.",
          "no files here", ".md alone", "R&D~plan-v2.docx", "archive.tar.zip.old and b.zipper c.htm d.html",
          "trailing.json", "v1.2.3 then file.toml_x and file.toml-x", "see a.md.b.md.c", "..md ...txt a..md",
          "x.MD y.Yml z.yaml w.ya", "name.log.", "p.pptx q.ppt r.doc s.xls t.eml u.ini v.xml w.tsv x.pdf"}) {
        std::vector<std::string> expected;
        for (auto it = std::sregex_iterator(text.begin(), text.end(), namedFile); it != std::sregex_iterator(); ++it) {
            expected.push_back(it->str());
        }
        EXPECT_EQ(gaia::GaiaAgent::namedFiles(text), expected) << text;
    }
}

TEST(LongInput, NamedFilesIsLinear) {
    for (const auto& [name, text] : pathologicalShapes(1 << 20)) {
        const auto start = Clock::now();
        gaia::GaiaAgent::namedFiles(text);
        EXPECT_LT(secondsSince(start), 0.5) << name;
    }
    const std::string many = repeated("a.md.", 1 << 20);
    const auto start = Clock::now();
    EXPECT_EQ(gaia::GaiaAgent::namedFiles(many).size(), 1u);
    EXPECT_LT(secondsSince(start), 0.5);
}

class LongInputCli : public ::testing::Test {
protected:
    void SetUp() override {
        const auto stamp = Clock::now().time_since_epoch().count();
        dir_ = fs::temp_directory_path() / ("gaiacpp-long-" + std::to_string(stamp));
        fs::create_directories(dir_ / "work");
    }
    void TearDown() override {
        std::error_code ec;
        fs::remove_all(dir_, ec);
    }

    std::map<std::string, std::string> isolated() const {
        return {{"GAIACPP_CONFIG", (dir_ / "no-config.json").string()},
                {"GAIA_HOME", (dir_ / "gaia-home").string()},
                {"GAIA_CONFIG_FILE", ""}, {"GAIA_CONFIG_DIR", ""},
                {"LEMONADE_BASE_URL", ""}, {"LEMONADE_API_KEY", ""}, {"GAIA_LEMONADE_EMBEDDED", ""},
                {"GAIA_BASE_URL", ""}, {"GAIA_API_KEY", ""}, {"GAIA_MODEL_ID", ""}};
    }

    gaia::ProcessResult run(const bench::MockLlmServer& mock, const std::string& prompt) {
        std::ofstream(dir_ / "work" / "prompt.txt", std::ios::binary) << prompt;
        std::string bin = GAIACPP_BIN;
        if (bin.find(' ') != std::string::npos) bin = "\"" + bin + "\"";
        const std::string cmd = bin + " --prompt-file prompt.txt -m mock-model --base-url " + mock.baseUrl() +
                                " --output-format json --no-memory --no-skills --no-web";
        return gaia::ProcessRunner::run(cmd, 60000, (dir_ / "work").string(), isolated(), 4u << 20);
    }

    fs::path dir_;
};

// Each check finishes in time in step with the input's length.
TEST_F(LongInputCli, AHeadlessRunAnswersEveryPathologicalShapeQuickly) {
    for (const size_t bytes : {40000u, 200000u}) {
        for (const auto& [name, body] : pathologicalShapes(bytes)) {
            bench::MockLlmServer mock;
            mock.pushResponse(finalAnswer("big-ok"));
            const auto start = Clock::now();
            const gaia::ProcessResult r = run(mock, body + " Reply with exactly: big-ok");
            const double took = secondsSince(start);
            EXPECT_FALSE(r.timedOut) << name << ", " << bytes << " bytes";
            EXPECT_LT(took, 10.0) << name << ", " << bytes << " bytes";
            ASSERT_FALSE(r.stdout_output.empty()) << name << ": " << r.stderr_output;
            const json out = json::parse(r.stdout_output, nullptr, false);
            ASSERT_TRUE(out.is_object()) << name << ": " << r.stdout_output.substr(0, 300);
            ASSERT_FALSE(out.value("is_error", false)) << name << ": " << out.value("result", "").substr(0, 300);
            EXPECT_LT(out.value("cpu_s", 99.0), 2.0) << name << ", " << bytes << " bytes";
        }
    }
}

// The answer is read by the same checks as the request.
TEST_F(LongInputCli, ALongRunInTheModelsAnswerIsAsCheap) {
    for (const auto& [name, body] : pathologicalShapes(200000)) {
        bench::MockLlmServer mock;
        mock.pushResponses(finalAnswer(body), 4);
        const auto start = Clock::now();
        const gaia::ProcessResult r = run(mock, "Print the test pattern.");
        EXPECT_FALSE(r.timedOut) << name;
        EXPECT_LT(secondsSince(start), 10.0) << name;
        const json out = json::parse(r.stdout_output, nullptr, false);
        ASSERT_TRUE(out.is_object()) << name << ": " << r.stderr_output.substr(0, 300);
        ASSERT_FALSE(out.value("is_error", false)) << name << ": " << out.value("result", "").substr(0, 300);
        EXPECT_LT(out.value("cpu_s", 99.0), 2.0) << name;
    }
}

#ifndef _WIN32
// A request held up in a check must not leave `serve` busy or deaf to SIGTERM.
TEST_F(LongInputCli, ServeStaysResponsiveAndStopsOnSigterm) {
    bench::MockLlmServer mock;
    mock.pushResponse(finalAnswer("big-ok"));
    const fs::path out = dir_ / "serve.stdout", err = dir_ / "serve.stderr";
    testsupport::EnvOverrides env = {{"SERVE_KEY", std::string("test-key")}};
    for (const auto& [k, v] : isolated()) env[k] = v;
    testsupport::ChildProcess serve({GAIACPP_BIN, "serve", "--port", "0", "--server-key-env", "SERVE_KEY", "-m",
                                     "mock-model", "--base-url", mock.baseUrl(), "--no-memory", "--no-skills"},
                                    env, (dir_ / "work").string(), out.string(), err.string());
    int port = 0;
    for (int i = 0; i < 300 && port == 0 && serve.running(); ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
        std::ifstream in(out);
        std::string line;
        if (std::getline(in, line)) {
            const json ready = json::parse(line, nullptr, false);
            if (ready.is_object() && ready.contains("url")) {
                const std::string url = ready["url"].get<std::string>();
                port = std::stoi(url.substr(url.rfind(':') + 1));
            }
        }
    }
    ASSERT_GT(port, 0) << "serve never announced its port";

    httplib::Client client("127.0.0.1", port);
    client.set_read_timeout(30);
    const httplib::Headers auth = {{"Authorization", "Bearer test-key"}};
    const std::string body =
        json{{"model", "gaia"},
             {"messages", json::array({{{"role", "user"},
                                        {"content", std::string(200000, 'A') + " Reply with exactly: big-ok"}}})}}
            .dump();
    const auto start = Clock::now();
    const auto res = client.Post("/v1/chat/completions", auth, body, "application/json");
    ASSERT_TRUE(res) << "no reply to the long request";
    EXPECT_EQ(res->status, 200) << res->body.substr(0, 300);
    EXPECT_LT(secondsSince(start), 10.0);

    bool idle = false;
    for (int i = 0; i < 100 && !idle; ++i) {
        const auto health = client.Get("/health", auth);
        idle = health && json::parse(health->body, nullptr, false).value("busy", true) == false;
        if (!idle) std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    EXPECT_TRUE(idle) << "the server is still busy 5 s after it answered";

    ASSERT_TRUE(serve.terminate());
    EXPECT_TRUE(serve.waitExit(5000)) << "serve ignored SIGTERM";
}
#endif

TEST(LongInput, AnOversizedServeRequestGets413WithAReason) {
    class Plain : public gaia::Agent {
    public:
        explicit Plain(const gaia::AgentConfig& cfg) : Agent(cfg) { init(); }

    protected:
        void registerTools() override {}
        std::string getSystemPrompt() const override { return "test"; }
    };
    bench::MockLlmServer mock;
    gaia::AgentConfig cfg;
    cfg.baseUrl = mock.baseUrl();
    cfg.modelId = "mock-model";
    cfg.silentMode = true;
    Plain agent(cfg);
    gaia::ServerOptions opts;
    opts.port = 0;
    opts.maxRequestBytes = 4096;
    gaia::ApiServer server(agent, opts);
    std::thread thread([&server] { server.run(); });
    for (int i = 0; i < 200 && !server.isRunning(); ++i) std::this_thread::sleep_for(std::chrono::milliseconds(10));
    ASSERT_TRUE(server.isRunning());

    httplib::Client client("127.0.0.1", server.port());
    const std::string body =
        json{{"model", "gaia"},
             {"messages", json::array({{{"role", "user"}, {"content", std::string(64 * 1024, 'A')}}})}}
            .dump();
    const auto res = client.Post("/v1/chat/completions", body, "application/json");
    server.stop();
    thread.join();
    ASSERT_TRUE(res) << "the connection dropped instead of answering 413";
    EXPECT_EQ(res->status, 413);
    const json reply = json::parse(res->body, nullptr, false);
    ASSERT_TRUE(reply.is_object()) << "413 with no reason: '" << res->body << "'";
    EXPECT_NE(reply["error"].value("message", "").find("4096 bytes"), std::string::npos) << res->body;
    EXPECT_EQ(mock.requestCount(), 0) << "an oversized request reached the model";
}

}  // namespace
