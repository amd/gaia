// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>

#include <gaia/media_tools.h>
#include <gaia/tool_registry.h>

#include <chrono>
#include <filesystem>
#include <fstream>
#include <random>
#include <thread>

#include "support/mock_llm_server.h"

using namespace gaia;
namespace fs = std::filesystem;

namespace {

struct MediaServer {
    httplib::Server srv;
    std::thread t;
    int port = 0;
    std::string lastModel, lastFileName, lastFileType, lastAuth;
    size_t lastFileSize = 0;
    MediaServer() {
        srv.Post("/api/v1/audio/transcriptions", [this](const httplib::Request& req, httplib::Response& res) {
            lastAuth = req.get_header_value("Authorization");
            lastModel = req.get_file_value("model").content;
            const auto f = req.get_file_value("file");
            lastFileName = f.filename;
            lastFileType = f.content_type;
            lastFileSize = f.content.size();
            res.set_content(R"({"text":"hello from the meeting"})", "application/json");
        });
        srv.Post("/api/v1/images/generations", [this](const httplib::Request& req, httplib::Response& res) {
            lastModel = json::parse(req.body).value("model", "");
            // "PNGDATA" base64-encoded
            res.set_content(R"({"data":[{"b64_json":"UE5HREFUQQ=="}]})", "application/json");
        });
        port = srv.bind_to_any_port("127.0.0.1");
        t = std::thread([this] { srv.listen_after_bind(); });
        while (!srv.is_running()) std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    ~MediaServer() {
        srv.stop();
        t.join();
    }
    std::string url() const { return "http://127.0.0.1:" + std::to_string(port) + "/api/v1"; }
};

struct Dir {
    fs::path path;
    Dir() {
        std::mt19937_64 rng(std::random_device{}());
        path = fs::temp_directory_path() / ("gaia-media-" + std::to_string(rng()));
        fs::create_directories(path);
    }
    ~Dir() {
        std::error_code ec;
        fs::remove_all(path, ec);
    }
};

}  // namespace

TEST(MediaToolsTest, RegisteredOnlyWhenConfigured) {
    ToolRegistry reg;
    MediaTools::registerAll(reg, MediaToolsOptions{});
    EXPECT_FALSE(reg.hasTool("transcribe_audio"));
    EXPECT_FALSE(reg.hasTool("generate_image"));
}

TEST(MediaToolsTest, TranscribeUploadsTheFileAsMultipart) {
    MediaServer s;
    Dir d;
    std::ofstream(d.path / "standup.wav", std::ios::binary) << std::string(2048, '\1');
    ToolRegistry reg;
    MediaToolsOptions o;
    o.transcribeModel = "Whisper-Base";
    o.chatEndpoint = [&s] { return MediaEndpoint{s.url(), "k-123"}; };
    MediaTools::registerAll(reg, o);
    json r = reg.executeTool("transcribe_audio", json{{"path", (d.path / "standup.wav").string()}});
    ASSERT_EQ(r["status"], "success") << r.dump();
    EXPECT_EQ(r["text"], "hello from the meeting");
    EXPECT_EQ(s.lastModel, "Whisper-Base");
    EXPECT_EQ(s.lastFileName, "standup.wav");
    EXPECT_EQ(s.lastFileType, "audio/wav");
    EXPECT_EQ(s.lastFileSize, 2048u);
    EXPECT_EQ(s.lastAuth, "Bearer k-123");

    json bad = reg.executeTool("transcribe_audio", json{{"path", (d.path / "notes.txt").string()}});
    EXPECT_EQ(bad["status"], "error");
}

TEST(MediaToolsTest, GenerateImageWritesTheDecodedPng) {
    MediaServer s;
    Dir d;
    ToolRegistry reg;
    reg.setConfirmCallback([](const std::string&, const json&) { return ToolConfirmResult::ALLOW_ONCE; });
    MediaToolsOptions o;
    o.imageModel = "SDXL-Turbo";
    o.chatEndpoint = [&s] { return MediaEndpoint{s.url(), ""}; };
    o.checkPath = [&d](const std::string& p) {
        return p.rfind(d.path.string(), 0) == 0 ? "" : std::string("Refused: outside");
    };
    MediaTools::registerAll(reg, o);
    const std::string out = (d.path / "art" / "cat.png").string();
    json r = reg.executeTool("generate_image", json{{"prompt", "a cat"}, {"output_path", out}});
    ASSERT_EQ(r["status"], "success") << r.dump();
    std::ifstream in(out, std::ios::binary);
    std::string content((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    EXPECT_EQ(content, "PNGDATA");
    EXPECT_EQ(s.lastModel, "SDXL-Turbo");
    EXPECT_EQ(reg.executeTool("generate_image", json{{"prompt", "x"}, {"output_path", "/etc/x.png"}})["status"],
              "error");
}

TEST(MediaToolsTest, Base64Decode) {
    EXPECT_EQ(MediaTools::base64Decode("aGVsbG8="), "hello");
    EXPECT_EQ(MediaTools::base64Decode("aGVs\nbG8h"), "hello!");
    EXPECT_THROW(MediaTools::base64Decode("not*valid"), std::invalid_argument);
}
