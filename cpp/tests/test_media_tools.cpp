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

TEST(MediaToolsTest, ScreenshotIsConfirmGatedAndConfined) {
    ToolInfo t = MediaTools::screenshotTool(
        [](const std::string& p) { return p.rfind("/etc", 0) == 0 ? std::string("Refused") : ""; });
    EXPECT_EQ(t.policy, ToolPolicy::CONFIRM);
    EXPECT_EQ(t.callback(json{{"output_path", "/etc/shot.png"}})["status"], "error");
    EXPECT_EQ(t.callback(json{{"output_path", "shot.jpg"}})["status"], "error");
}

TEST(MediaToolsTest, ScreenshotRefusesAPathThatWouldEndTheShellQuote) {
    Dir d;
    ToolInfo t = MediaTools::screenshotTool();
    for (const char* name : {"a'; echo pwned > pwned.txt; '.png", "a\"b.png", "a`id`.png", "a$(id).png",
                             "a%PATH%.png", "a&b.png", "a|b.png", "a\nb.png"}) {
        const json r = t.callback(json{{"output_path", (d.path / name).string()}});
        ASSERT_EQ(r["status"], "error") << name;
        EXPECT_NE(r["error"].get<std::string>().find("may not contain"), std::string::npos) << r.dump();
    }
    EXPECT_TRUE(fs::is_empty(d.path)) << "nothing ran and nothing was written";
}

TEST(MediaToolsTest, NonAsciiFileNamesAreUtf8) {
    MediaServer s;
    Dir d;
    const fs::path folder = d.path / fs::u8path("caf\xc3\xa9 \xe6\x97\xa5\xe6\x9c\xac");
    fs::create_directories(folder);
    const fs::path audio = folder / fs::u8path("r\xc3\xa9union.wav");
    std::ofstream(audio, std::ios::binary) << std::string(512, '\1');
    ToolRegistry reg;
    reg.setConfirmCallback([](const std::string&, const json&) { return ToolConfirmResult::ALLOW_ONCE; });
    MediaToolsOptions o;
    o.transcribeModel = "Whisper-Base";
    o.imageModel = "SDXL-Turbo";
    o.chatEndpoint = [&s] { return MediaEndpoint{s.url(), ""}; };
    MediaTools::registerAll(reg, o);

    json heard = reg.executeTool("transcribe_audio", json{{"path", audio.u8string()}});
    ASSERT_EQ(heard["status"], "success") << heard.dump();
    EXPECT_EQ(s.lastFileSize, 512u);

    const fs::path image = folder / fs::u8path("dess\xc3\xadn.png");
    json drawn = reg.executeTool("generate_image", json{{"prompt", "a cat"}, {"output_path", image.u8string()}});
    ASSERT_EQ(drawn["status"], "success") << drawn.dump();
    EXPECT_EQ(fs::file_size(image), 7u);
}

namespace {

// Minimal valid 1x1 PNG, base64-encoded.
constexpr const char* kTinyPngB64 =
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";

struct ScopedEnvVar {
    std::string name;
    std::string prev;
    bool hadPrev = false;
    ScopedEnvVar(const std::string& n, const std::string& val) : name(n) {
        if (const char* cur = std::getenv(n.c_str())) {
            hadPrev = true;
            prev = cur;
        }
        set(val);
    }
    ~ScopedEnvVar() {
        if (hadPrev) set(prev);
        else unset();
    }
    void set(const std::string& v) {
#ifdef _WIN32
        _putenv_s(name.c_str(), v.c_str());
#else
        setenv(name.c_str(), v.c_str(), 1);
#endif
    }
    void unset() {
#ifdef _WIN32
        _putenv_s(name.c_str(), "");
#else
        unsetenv(name.c_str());
#endif
    }
};

struct ImageMockServer {
    httplib::Server srv;
    std::thread t;
    int port = 0;
    std::string lastAuth;
    json lastRequest;
    int generateCalls = 0;
    int generateStatus = 200;
    std::string generateBody = std::string(R"({"data":[{"b64_json":")") + kTinyPngB64 + R"("}]})";
    json modelsBody = {
        {"data",
         json::array({
             {{"id", "Qwen3.6-35B-A3B-GGUF"}, {"recipe", "llamacpp"}, {"labels", json::array({"llm"})}},
             {{"id", "RealESRGAN-x4plus"}, {"recipe", "sd-cpp"}, {"labels", json::array({"image", "upscaling"})}},
             {{"id", "SDXL-Turbo"}, {"recipe", "sd-cpp"}, {"labels", json::array({"image"})}, {"downloaded", true}},
             {{"id", "Z-Image-Turbo"},
              {"recipe", "thenoise"},
              {"labels", json::array({"image"})},
              {"downloaded", true},
              {"image_defaults", {{"width", 1024}, {"height", 1024}, {"steps", 9}}}},
             {{"id", "Flux-2-Klein-4B"},
              {"recipe", "sd-cpp"},
              {"labels", json::array({"image"})},
              {"downloaded", false}},
         })}};

    ImageMockServer() {
        srv.Get("/api/v1/models", [this](const httplib::Request& req, httplib::Response& res) {
            lastAuth = req.get_header_value("Authorization");
            res.set_content(modelsBody.dump(), "application/json");
        });
        srv.Post("/api/v1/images/generations", [this](const httplib::Request& req, httplib::Response& res) {
            ++generateCalls;
            lastAuth = req.get_header_value("Authorization");
            lastRequest = json::parse(req.body, nullptr, false);
            res.status = generateStatus;
            res.set_content(generateBody, "application/json");
        });
        port = srv.bind_to_any_port("127.0.0.1");
        t = std::thread([this] { srv.listen_after_bind(); });
        while (!srv.is_running()) std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    ~ImageMockServer() {
        srv.stop();
        t.join();
    }
    std::string url() const { return "http://127.0.0.1:" + std::to_string(port) + "/api/v1"; }
};

}  // namespace

TEST(MediaToolsTest, GenerateImageReadsConfigDefaultsSavesPngAndSupportsOverrides) {
    ImageMockServer s;
    Dir home;
    ScopedEnvVar envHome("GAIA_HOME", home.path.u8string());
    ScopedEnvVar envCfgDir("GAIA_CONFIG_DIR", "");
    ScopedEnvVar envCfgFile("GAIA_CONFIG_FILE", "");
    std::ofstream(home.path / "config.json")
        << json{{"image_model", "Z-Image-Turbo"}, {"image_size", "1024x1024"}, {"image_steps", 9}}.dump();

    ToolRegistry reg;
    // No confirm callback: default cache-folder generation must not require confirmation.
    MediaToolsOptions o;
    o.enableImageTools = true;
    o.chatEndpoint = [&s] { return MediaEndpoint{s.url(), "lemon-secret"}; };
    MediaTools::registerAll(reg, o);
    ASSERT_TRUE(reg.hasTool("generate_image"));
    ASSERT_TRUE(reg.hasTool("list_sd_models"));

    json r = reg.executeTool("generate_image", json{{"prompt", "a sunset over mountains"}});
    ASSERT_EQ(r["status"], "success") << r.dump();
    EXPECT_EQ(s.lastAuth, "Bearer lemon-secret");
    EXPECT_EQ(s.lastRequest["model"], "Z-Image-Turbo");
    EXPECT_EQ(s.lastRequest["size"], "1024x1024");
    EXPECT_EQ(s.lastRequest["steps"], 9);
    EXPECT_EQ(s.lastRequest["response_format"], "b64_json");

    const std::string saved = r.at("image_path").get<std::string>();
    EXPECT_EQ(r.at("path").get<std::string>(), saved);
    EXPECT_EQ(r.at("file_path").get<std::string>(), saved);
    const fs::path savedPath = fs::u8path(saved);
    EXPECT_TRUE(savedPath.is_absolute());
    EXPECT_EQ(savedPath.parent_path(), fs::canonical(home.path / "cache" / "sd" / "images"));
    ASSERT_TRUE(fs::is_regular_file(savedPath));
    std::ifstream in(savedPath, std::ios::binary);
    std::string bytes((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    ASSERT_GE(bytes.size(), 8u);
    EXPECT_EQ(bytes.substr(0, 4), "\x89PNG");

    // Explicit args override saved config; switching to a different model uses that model's defaults.
    json r2 = reg.executeTool(
        "generate_image",
        json{{"prompt", "a lighthouse"}, {"model", "SDXL-Turbo"}, {"size", "768x768"}, {"steps", 6}, {"seed", 42}});
    ASSERT_EQ(r2["status"], "success") << r2.dump();
    EXPECT_EQ(s.lastRequest["model"], "SDXL-Turbo");
    EXPECT_EQ(s.lastRequest["size"], "768x768");
    EXPECT_EQ(s.lastRequest["steps"], 6);
    EXPECT_EQ(s.lastRequest["seed"], 42);
}

TEST(MediaToolsTest, GenerateImageFailsLoudlyOnMissingModelHttpErrorsBadBase64AndTraversal) {
    ImageMockServer s;
    Dir home;
    ScopedEnvVar envHome("GAIA_HOME", home.path.u8string());
    ScopedEnvVar envCfgDir("GAIA_CONFIG_DIR", "");
    ScopedEnvVar envCfgFile("GAIA_CONFIG_FILE", "");

    ToolRegistry reg;
    MediaToolsOptions o;
    o.enableImageTools = true;
    o.chatEndpoint = [&s] { return MediaEndpoint{s.url(), "k"}; };
    o.checkPath = [&home](const std::string& p) {
        return p.rfind(home.path.u8string(), 0) == 0 && p.find("..") == std::string::npos
                   ? ""
                   : std::string("Refused: outside allowed folders");
    };
    MediaTools::registerAll(reg, o);

    // Non-downloaded non-legacy model fails loudly before calling /images/generations.
    json missing = reg.executeTool("generate_image", json{{"prompt", "cat"}, {"model", "Flux-2-Klein-4B"}});
    EXPECT_EQ(missing["status"], "error");
    EXPECT_NE(missing["error"].get<std::string>().find("not downloaded"), std::string::npos);
    EXPECT_EQ(s.generateCalls, 0);

    // Path traversal / outside path refused (and requires confirmation when explicit output_path is set).
    reg.setConfirmCallback([](const std::string&, const json&) { return ToolConfirmResult::ALLOW_ONCE; });
    json trav = reg.executeTool("generate_image",
                                json{{"prompt", "cat"}, {"output_path", (home.path / ".." / "escape.png").u8string()}});
    EXPECT_EQ(trav["status"], "error");
    EXPECT_EQ(s.generateCalls, 0);

    // HTTP 404 fails loudly.
    s.generateStatus = 404;
    s.generateBody = R"({"error":{"message":"model not found"}})";
    json notFound = reg.executeTool("generate_image", json{{"prompt", "cat"}});
    EXPECT_EQ(notFound["status"], "error");
    EXPECT_NE(notFound["error"].get<std::string>().find("404"), std::string::npos);

    // HTTP 500 fails loudly.
    s.generateStatus = 500;
    s.generateBody = R"({"error":{"message":"backend crashed"}})";
    json err500 = reg.executeTool("generate_image", json{{"prompt", "cat"}});
    EXPECT_EQ(err500["status"], "error");
    EXPECT_NE(err500["error"].get<std::string>().find("500"), std::string::npos);
    EXPECT_NE(err500["error"].get<std::string>().find("backend crashed"), std::string::npos);

    // Invalid base64 fails loudly and writes nothing.
    s.generateStatus = 200;
    s.generateBody = R"({"data":[{"b64_json":"not*valid*base64"}]})";
    json badB64 = reg.executeTool("generate_image", json{{"prompt", "cat"}});
    EXPECT_EQ(badB64["status"], "error");
    EXPECT_NE(badB64["error"].get<std::string>().find("base64"), std::string::npos);

    // Unreachable endpoint fails loudly.
    ToolRegistry regDown;
    MediaToolsOptions down;
    down.enableImageTools = true;
    down.chatEndpoint = [] { return MediaEndpoint{"http://127.0.0.1:1/api/v1", ""}; };
    MediaTools::registerAll(regDown, down);
    json unreachable = regDown.executeTool("generate_image", json{{"prompt", "cat"}});
    EXPECT_EQ(unreachable["status"], "error");
    EXPECT_NE(unreachable["error"].get<std::string>().find("could not reach Lemonade"), std::string::npos);

    // Malformed config.json fails loudly instead of silently falling back.
    std::ofstream(home.path / "config.json") << "{bad json";
    json badCfg = reg.executeTool("generate_image", json{{"prompt", "cat"}});
    EXPECT_EQ(badCfg["status"], "error");
    EXPECT_NE(badCfg["error"].get<std::string>().find("not valid JSON"), std::string::npos);
}

TEST(MediaToolsTest, ListSdModelsFiltersImageModelsAndReportsSavedDefault) {
    ImageMockServer s;
    Dir home;
    ScopedEnvVar envHome("GAIA_HOME", home.path.u8string());
    ScopedEnvVar envCfgDir("GAIA_CONFIG_DIR", "");
    ScopedEnvVar envCfgFile("GAIA_CONFIG_FILE", "");
    std::ofstream(home.path / "config.json") << json{{"image_model", "Z-Image-Turbo"}}.dump();

    ToolRegistry reg;
    MediaToolsOptions o;
    o.enableImageTools = true;
    o.chatEndpoint = [&s] { return MediaEndpoint{s.url(), "tok"}; };
    MediaTools::registerAll(reg, o);

    json r = reg.executeTool("list_sd_models", json::object());
    ASSERT_EQ(r["status"], "success") << r.dump();
    EXPECT_EQ(r["default_model"], "Z-Image-Turbo");
    std::vector<std::string> names;
    for (const auto& m : r["models"]) names.push_back(m["name"].get<std::string>());
    EXPECT_NE(std::find(names.begin(), names.end(), "SDXL-Turbo"), names.end());
    EXPECT_NE(std::find(names.begin(), names.end(), "Z-Image-Turbo"), names.end());
    EXPECT_EQ(std::find(names.begin(), names.end(), "Flux-2-Klein-4B"), names.end());
    EXPECT_EQ(std::find(names.begin(), names.end(), "Qwen3.6-35B-A3B-GGUF"), names.end());
    EXPECT_EQ(std::find(names.begin(), names.end(), "RealESRGAN-x4plus"), names.end());
}
