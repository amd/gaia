// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Image generation, transcription and screen capture against a mock Lemonade:
// what goes over the wire, what lands on disk, and how each failure reads.

#include <gtest/gtest.h>

#include "support/mock_llm_server.h"

#include <gaia/file_tools.h>
#include <gaia/flagship_config.h>
#include <gaia/flagship_tools.h>
#include <gaia/tool_registry.h>

#include <filesystem>
#include <fstream>
#include <sstream>

using namespace gaia;
namespace fs = std::filesystem;

namespace {

std::string wavBytes(int rate, int channels, double seconds) {
    const std::uint32_t frames = static_cast<std::uint32_t>(rate * seconds);
    const std::uint32_t data = frames * channels * 2;
    std::string h = "RIFF";
    auto put32 = [&h](std::uint32_t v) {
        for (int i = 0; i < 4; ++i) h.push_back(static_cast<char>((v >> (8 * i)) & 0xFF));
    };
    auto put16 = [&h](int v) {
        h.push_back(static_cast<char>(v & 0xFF));
        h.push_back(static_cast<char>((v >> 8) & 0xFF));
    };
    put32(36 + data);
    h += "WAVEfmt ";
    put32(16);
    put16(1);
    put16(channels);
    put32(static_cast<std::uint32_t>(rate));
    put32(static_cast<std::uint32_t>(rate * channels * 2));
    put16(channels * 2);
    put16(16);
    h += "data";
    put32(data);
    return h + std::string(data, '\0');
}

const char* const kTranscription = R"({
  "text": " Hello team. Priya here.",
  "language": "en",
  "duration": 1.0,
  "segments": [
    {"start": 0.0, "end": 0.4, "text": " Hello team.", "avg_logprob": -0.1,
     "words": [{"word": " Hello", "start": 0.0, "end": 0.2, "probability": 0.95},
               {"word": " team.", "start": 0.2, "end": 0.4, "probability": 0.31}]},
    {"start": 0.5, "end": 0.9, "text": " Priya here.", "avg_logprob": -0.2,
     "words": [{"word": " Priya", "start": 0.5, "end": 0.7, "probability": 0.9},
               {"word": " here.", "start": 0.7, "end": 0.9, "probability": 0.92}]}
  ]
})";

class MediaToolsTest : public ::testing::Test {
protected:
    fs::path root_;
    bench::MockLlmServer mock_;
    std::vector<std::string> prompts_;
    std::vector<std::string> replies_;
    std::vector<std::string> indexed_;

    void SetUp() override {
        root_ = fs::weakly_canonical(fs::temp_directory_path()) / "gaia_media_tools_test";
        std::error_code ec;
        fs::remove_all(root_, ec);
        fs::create_directories(root_ / "work");
        FileStateTracker::instance().clear();
    }
    void TearDown() override {
        std::error_code ec;
        fs::remove_all(root_, ec);
    }

    FlagshipToolContext ctx() {
        FlagshipToolContext c;
        c.allowedPaths = {(root_ / "work").u8string()};
        c.workDir = (root_ / "work").u8string();
        c.scratchDir = (root_ / "scratch").u8string();
        c.lemonadeUrl = mock_.baseUrl();
        c.imageDir = (root_ / "images").u8string();
        c.transcriptDir = (root_ / "transcripts").u8string();
        c.screenshotDir = (root_ / "screenshots").u8string();
        c.complete = [this](const std::string& prompt) {
            prompts_.push_back(prompt);
            if (replies_.empty()) throw std::runtime_error("no reply scripted");
            std::string r = replies_.front();
            replies_.erase(replies_.begin());
            return r;
        };
        c.indexDocument = [this](const std::string& path) -> json {
            indexed_.push_back(path);
            return {{"status", "success"}};
        };
        return c;
    }

    std::string write(const fs::path& p, const std::string& content) {
        fs::create_directories(p.parent_path());
        std::ofstream(p, std::ios::binary) << content;
        return p.u8string();
    }
    std::string slurp(const fs::path& p) {
        std::ifstream in(p, std::ios::binary);
        std::ostringstream ss;
        ss << in.rdbuf();
        return ss.str();
    }
};

}  // namespace

// ---- Image generation ------------------------------------------------------

TEST_F(MediaToolsTest, GenerateImageSendsPythonsRequestAndSavesThePng) {
    mock_.pushRouteResponse("/api/v1/images/generations", 200,
                            json{{"data", json::array({{{"b64_json", base64Encode(std::vector<std::uint8_t>{'P', 'N', 'G', '!'})}}})}}.dump());
    FlagshipTools tools(ctx());
    json r = tools.generateImage({{"prompt", "A red fox, at sunset!"}});
    ASSERT_EQ(r["status"], "success") << r.dump();

    const auto loads = mock_.recorded("/api/v1/load");
    ASSERT_EQ(loads.size(), 1u);
    EXPECT_EQ(json::parse(loads[0].body), (json{{"model_name", "SDXL-Turbo"}}));

    const auto gens = mock_.recorded("/api/v1/images/generations");
    ASSERT_EQ(gens.size(), 1u);
    const json body = json::parse(gens[0].body);
    EXPECT_EQ(body["prompt"], "A red fox, at sunset!");
    EXPECT_EQ(body["model"], "SDXL-Turbo");
    EXPECT_EQ(body["size"], "512x512");
    EXPECT_EQ(body["n"], 1);
    EXPECT_EQ(body["response_format"], "b64_json");
    EXPECT_EQ(body["steps"], 4);
    EXPECT_DOUBLE_EQ(body["cfg_scale"].get<double>(), 1.0);
    ASSERT_TRUE(body["seed"].is_number_integer());
    EXPECT_LE(body["seed"].get<std::uint64_t>(), 0xFFFFFFFFull);

    const fs::path saved = fs::u8path(r["image_path"].get<std::string>());
    EXPECT_EQ(saved.parent_path(), root_ / "images");
    EXPECT_EQ(saved.filename().u8string().rfind("A_red_fox_at_sunset_SDXL-Turbo_", 0), 0u) << saved;
    EXPECT_EQ(slurp(saved), "PNG!");
    EXPECT_EQ(r["model"], "SDXL-Turbo");
    EXPECT_EQ(r["size"], "512x512");
    EXPECT_EQ(r["steps"], 4);
    EXPECT_EQ(r["image_hash"].get<std::string>().size(), 16u);
    EXPECT_TRUE(r.contains("generation_time_s"));

    json history = tools.getGenerationHistory({});
    EXPECT_EQ(history["total_generations"], 1);
    EXPECT_EQ(history["generations"][0]["image_path"], r["image_path"]);
    EXPECT_TRUE(history["generations"][0].contains("created_at"));
}

TEST_F(MediaToolsTest, GenerateImageAppliesEachModelsDefaults) {
    mock_.pushRouteResponse("/api/v1/images/generations", 200, R"({"data":[{"b64_json":"UE5H"}]})");
    FlagshipTools tools(ctx());
    ASSERT_EQ(tools.generateImage({{"prompt", "a cabin"}, {"model", "SDXL-Base-1.0"}, {"seed", 7}})["status"], "success");
    const json body = json::parse(mock_.recorded("/api/v1/images/generations").back().body);
    EXPECT_EQ(body["size"], "1024x1024");
    EXPECT_EQ(body["steps"], 20);
    EXPECT_DOUBLE_EQ(body["cfg_scale"].get<double>(), 7.5);
    EXPECT_EQ(body["seed"], 7);
}

TEST_F(MediaToolsTest, GenerateImagePullsAModelTheCatalogHasNotDownloaded) {
    mock_.setModelsList(R"({"data":[{"id":"SD-Turbo","downloaded":false,"labels":["image"]}]})");
    mock_.pushRouteResponse("/api/v1/pull", 200, R"({"status":"success"})");
    mock_.pushRouteResponse("/api/v1/images/generations", 200, R"({"data":[{"b64_json":"UE5H"}]})");
    FlagshipTools tools(ctx());
    ASSERT_EQ(tools.generateImage({{"prompt", "x"}, {"model", "SD-Turbo"}})["status"], "success");
    const auto pulls = mock_.recorded("/api/v1/pull");
    ASSERT_EQ(pulls.size(), 1u);
    // Built-in models are pulled by name only (#1655).
    EXPECT_EQ(json::parse(pulls[0].body), (json{{"model_name", "SD-Turbo"}}));
}

TEST_F(MediaToolsTest, GenerateImageErrorsNameTheCause) {
    FlagshipTools tools(ctx());
    json bad = tools.generateImage({{"prompt", "x"}, {"model", "Flux"}});
    EXPECT_EQ(bad["status"], "error");
    EXPECT_EQ(bad["error"], "Invalid model 'Flux'. Choose from: ['SD-1.5', 'SD-Turbo', 'SDXL-Base-1.0', 'SDXL-Turbo']");
    EXPECT_NE(tools.generateImage({{"prompt", "x"}, {"size", "640x480"}})["error"].get<std::string>().find("Invalid size '640x480'"),
              std::string::npos);
    EXPECT_EQ(tools.generateImage({{"prompt", ""}})["status"], "error");
    EXPECT_TRUE(mock_.recorded("/api/v1/images/generations").empty());

    mock_.pushRouteResponse("/api/v1/images/generations", 500, R"({"error":{"message":"out of GPU memory"}})");
    json failed = tools.generateImage({{"prompt", "x"}});
    EXPECT_EQ(failed["status"], "error");
    const std::string msg = failed["error"];
    EXPECT_NE(msg.find("Image generation failed for SDXL-Turbo"), std::string::npos) << msg;
    EXPECT_NE(msg.find("500"), std::string::npos) << msg;
    EXPECT_NE(msg.find("out of GPU memory"), std::string::npos) << msg;
    EXPECT_EQ(tools.getGenerationHistory({})["total_generations"], 0);

    mock_.setLoadStatus(500);
    json load = tools.generateImage({{"prompt", "x"}});
    EXPECT_EQ(load["status"], "error");
    EXPECT_NE(load["error"].get<std::string>().find("SDXL-Turbo"), std::string::npos);
}

TEST_F(MediaToolsTest, UnreachableLemonadeSaysSo) {
    FlagshipToolContext c = ctx();
    c.lemonadeUrl = "http://127.0.0.1:1";
    FlagshipTools tools(c);
    json r = tools.generateImage({{"prompt", "x"}});
    EXPECT_EQ(r["status"], "error");
    EXPECT_NE(r["error"].get<std::string>().find("Cannot reach Lemonade Server"), std::string::npos) << r.dump();
}

TEST_F(MediaToolsTest, ListSdModelsMatchesPython) {
    FlagshipTools tools(ctx());
    json r = tools.listSdModels({});
    EXPECT_EQ(r["default_model"], "SDXL-Turbo");
    ASSERT_EQ(r["models"].size(), 4u);
    EXPECT_EQ(r["models"][0]["name"], "SD-Turbo");
    EXPECT_EQ(r["models"][3]["recommended_size"], "1024x1024");
}

// ---- Transcription ---------------------------------------------------------

TEST_F(MediaToolsTest, TranscribeSendsTheWavAsMultipartAndSavesTranscriptAndTimings) {
    mock_.pushRouteResponse("/api/v1/audio/transcriptions", 200, kTranscription);
    FlagshipTools tools(ctx());
    const std::string wav = wavBytes(16000, 1, 1.0);
    const std::string clip = write(root_ / "work" / "clip.wav", wav);
    json r = tools.transcribeMedia({{"file_path", clip}, {"language", "en"}});
    ASSERT_EQ(r["status"], "success") << r.dump();

    const auto sent = mock_.recorded("/api/v1/audio/transcriptions");
    ASSERT_EQ(sent.size(), 1u);
    EXPECT_EQ(sent[0].contentType.rfind("multipart/form-data; boundary=", 0), 0u) << sent[0].contentType;
    EXPECT_EQ(sent[0].fields.at("model"), "Whisper-Large-v3-Turbo");
    EXPECT_EQ(sent[0].fields.at("response_format"), "verbose_json");
    EXPECT_EQ(sent[0].fields.at("language"), "en");
    EXPECT_EQ(sent[0].fileName, "clip.wav");
    EXPECT_EQ(sent[0].fileContentType, "audio/wav");
    EXPECT_EQ(sent[0].fileContent, wav);

    const fs::path transcript = root_ / "transcripts" / "clip.txt";
    EXPECT_EQ(r["transcript_path"], transcript.u8string());
    EXPECT_EQ(slurp(transcript), "Hello team. Priya here.");
    const json timings = json::parse(slurp(fs::u8path(transcript.u8string() + ".timing.json")));
    ASSERT_EQ(timings["segments"].size(), 2u);
    EXPECT_EQ(timings["segments"][1]["text"], "Priya here.");
    EXPECT_TRUE(timings["speakers"].empty());

    EXPECT_EQ(r["language"], "en");
    EXPECT_EQ(r["audio_duration"], "1s");
    EXPECT_EQ(r["segment_count"], 2);
    EXPECT_EQ(r["preview"], "Hello team. Priya here.");
    ASSERT_EQ(r["low_confidence_span_count"], 1);
    EXPECT_EQ(r["low_confidence_spans"][0]["text"], "team.");
    EXPECT_NE(r["next_step"].get<std::string>().find("refine_transcript('" + transcript.u8string() + "')"), std::string::npos);
}

TEST_F(MediaToolsTest, TranscribeOmitsLanguageWhenAutoDetecting) {
    mock_.pushRouteResponse("/api/v1/audio/transcriptions", 200, kTranscription);
    FlagshipTools tools(ctx());
    ASSERT_EQ(tools.transcribeMedia({{"file_path", write(root_ / "work" / "a.wav", wavBytes(16000, 1, 0.5))}})["status"], "success");
    EXPECT_EQ(mock_.recorded("/api/v1/audio/transcriptions")[0].fields.count("language"), 0u);
}

TEST_F(MediaToolsTest, SpeakerLabelsAreNeverClaimedWithoutDiarization) {
    mock_.pushRouteResponse("/api/v1/audio/transcriptions", 200, kTranscription);
    FlagshipTools tools(ctx());
    json r = tools.transcribeMedia({{"file_path", write(root_ / "work" / "talk.wav", wavBytes(16000, 1, 1.0))}});
    ASSERT_EQ(r["status"], "success") << r.dump();
    const std::string note = r.value("speaker_identification", "");
    EXPECT_NE(note.find("no speaker labels"), std::string::npos) << note;
    EXPECT_NE(note.find("no speaker diarization"), std::string::npos) << note;
}

TEST_F(MediaToolsTest, SecondTranscribeOfTheSameRecordingReusesTheTranscript) {
    mock_.pushRouteResponse("/api/v1/audio/transcriptions", 200, kTranscription);
    FlagshipTools tools(ctx());
    const std::string clip = write(root_ / "work" / "standup.wav", wavBytes(16000, 1, 1.0));
    ASSERT_EQ(tools.transcribeMedia({{"file_path", clip}})["status"], "success");
    json again = tools.transcribeMedia({{"file_path", clip}});
    EXPECT_EQ(again["status"], "success");
    EXPECT_EQ(again["reused"], true);
    EXPECT_EQ(mock_.recorded("/api/v1/audio/transcriptions").size(), 1u);
}

TEST_F(MediaToolsTest, TranscribeErrorPaths) {
    FlagshipTools tools(ctx());
    json missing = tools.transcribeMedia({{"file_path", (root_ / "work" / "nope.wav").u8string()}});
    EXPECT_EQ(missing["status"], "error");
    EXPECT_NE(missing["error"].get<std::string>().find("No such file"), std::string::npos);

    const std::string clip = write(root_ / "work" / "clip.wav", wavBytes(16000, 1, 0.5));
    json flm = tools.transcribeMedia({{"file_path", clip}, {"model", "whisper-v3-turbo-FLM"}});
    EXPECT_EQ(flm["status"], "error");
    EXPECT_NE(flm["error"].get<std::string>().find("FLM (NPU) backend"), std::string::npos);

    mock_.pushRouteResponse("/api/v1/audio/transcriptions", 404, R"({"error":{"message":"model 'Whisper-Nope' not found"}})");
    json unknown = tools.transcribeMedia({{"file_path", clip}, {"model", "Whisper-Nope"}});
    EXPECT_EQ(unknown["status"], "error");
    const std::string msg = unknown["error"];
    EXPECT_NE(msg.find("404"), std::string::npos) << msg;
    EXPECT_NE(msg.find("Whisper-Nope' not found"), std::string::npos) << msg;
    EXPECT_FALSE(fs::exists(root_ / "transcripts" / "clip.txt"));

    EXPECT_EQ(tools.transcribeMedia({{"file_path", write(root_ / "outside.wav", wavBytes(16000, 1, 0.1))}})["status"], "error");
}

TEST_F(MediaToolsTest, MediaThatNeedsDecodingUsesFfmpegOrSaysToInstallIt) {
    FlagshipTools tools(ctx());
    // A stereo 44.1 kHz WAV and a non-WAV both need a decode before Lemonade will take them.
    for (const auto& [name, bytes] : {std::pair<std::string, std::string>{"stereo.wav", wavBytes(44100, 2, 0.1)},
                                      {"song.mp3", "not really an mp3"}}) {
        json r = tools.transcribeMedia({{"file_path", write(root_ / "work" / name, bytes)}});
        EXPECT_EQ(r["status"], "error") << name;
        const std::string msg = r["error"];
        if (findExecutable("ffmpeg").empty()) {
            EXPECT_NE(msg.find("ffmpeg is required"), std::string::npos) << msg;
        } else if (name == "song.mp3") {
            EXPECT_NE(msg.find("ffmpeg failed to decode"), std::string::npos) << msg;
        }
    }
}

TEST_F(MediaToolsTest, RefineNamesPauseSplitTurnsAndIndexesTheResult) {
    const fs::path raw = root_ / "transcripts" / "sync.txt";
    write(raw, "Hi all. Thanks Sam. Let's start.");
    write(fs::u8path(raw.u8string() + ".timing.json"),
          json{{"duration", 9.0},
               {"speakers", json::array()},
               {"segments", json::array({{{"start", 0.0}, {"end", 1.0}, {"text", "Hi all."}},
                                         {{"start", 3.0}, {"end", 4.0}, {"text", "Thanks Sam."}},
                                         {{"start", 4.2}, {"end", 5.0}, {"text", "Let's start."}}})}}
              .dump());
    replies_ = {"1: Sam\n2: Priya"};
    FlagshipTools tools(ctx());
    json r = tools.refineTranscript({{"transcript_path", raw.u8string()}});
    ASSERT_EQ(r["status"], "success") << r.dump();
    ASSERT_EQ(prompts_.size(), 1u);
    // Two turns: the 2 s pause splits, the 0.2 s one does not.
    EXPECT_NE(prompts_[0].find("1. Hi all.\n2. [pause 2.0s] Thanks Sam. Let's start."), std::string::npos) << prompts_[0];
    EXPECT_EQ(r["speakers"], (json{"Sam", "Priya"}));
    EXPECT_EQ(r["turns"], 2);
    const fs::path refined = root_ / "transcripts" / "sync.transcript.md";
    EXPECT_EQ(r["refined_path"], refined.u8string());
    const std::string doc = slurp(refined);
    EXPECT_NE(doc.find("# Transcript — sync"), std::string::npos);
    EXPECT_NE(doc.find("Sam: Hi all.\n\nPriya: Thanks Sam. Let's start."), std::string::npos) << doc;
    EXPECT_NE(doc.find("without voice identification"), std::string::npos);
    EXPECT_EQ(indexed_, (std::vector<std::string>{refined.u8string()}));
    EXPECT_EQ(r["indexed"], true);
    EXPECT_NE(r.value("speaker_identification", "").find("no voice separation"), std::string::npos);
}

TEST_F(MediaToolsTest, RefineErrorPaths) {
    FlagshipTools tools(ctx());
    EXPECT_NE(tools.refineTranscript({{"transcript_path", (root_ / "transcripts" / "none.txt").u8string()}})["error"]
                  .get<std::string>()
                  .find("Run transcribe_media first"),
              std::string::npos);

    const fs::path raw = root_ / "transcripts" / "bare.txt";
    write(raw, "words with no timings");
    json noTimings = tools.refineTranscript({{"transcript_path", raw.u8string()}});
    EXPECT_EQ(noTimings["status"], "error");
    EXPECT_NE(noTimings["error"].get<std::string>().find("No segment timings"), std::string::npos);

    write(fs::u8path(raw.u8string() + ".timing.json"),
          R"({"segments":[{"start":0,"end":1,"text":"Hello."}],"speakers":[]})");
    json failed = tools.refineTranscript({{"transcript_path", raw.u8string()}});  // the model call throws
    EXPECT_EQ(failed["status"], "error");
    EXPECT_NE(failed["error"].get<std::string>().find("Speaker identification failed: no reply scripted"), std::string::npos);
    EXPECT_FALSE(fs::exists(root_ / "transcripts" / "bare.transcript.md"));
}

// ---- Screenshot ------------------------------------------------------------

TEST(PngEncoderTest, WritesAValidHeaderAndDimensions) {
    std::vector<std::uint8_t> rgb(3 * 4 * 2, 0x7F);
    const std::string png = encodePng(rgb, 4, 2);
    ASSERT_GT(png.size(), 33u);
    EXPECT_EQ(png.substr(0, 8), std::string("\x89PNG\r\n\x1a\n", 8));
    EXPECT_EQ(png.substr(12, 4), "IHDR");
    EXPECT_EQ(static_cast<unsigned char>(png[19]), 4u);  // width, big-endian
    EXPECT_EQ(static_cast<unsigned char>(png[23]), 2u);  // height
    EXPECT_THROW(encodePng(rgb, 5, 2), std::runtime_error);
}

TEST_F(MediaToolsTest, TakeScreenshotCapturesTheScreenToAPng) {
#ifndef _WIN32
    GTEST_SKIP() << "screen capture is Windows-only in this build";
#else
    FlagshipTools tools(ctx());
    json r = tools.takeScreenshot({{"output_path", (root_ / "work" / "shot.png").u8string()}});
    if (r["status"] == "error" && r["error"].get<std::string>().find("no desktop") != std::string::npos) {
        GTEST_SKIP() << "no interactive desktop in this session: " << r["error"];
    }
    ASSERT_EQ(r["status"], "success") << r.dump();
    EXPECT_GT(r["width"].get<int>(), 0);
    EXPECT_GT(r["height"].get<int>(), 0);
    const std::string png = slurp(root_ / "work" / "shot.png");
    ASSERT_GT(png.size(), 100u);
    EXPECT_EQ(png.substr(0, 8), std::string("\x89PNG\r\n\x1a\n", 8));

    json defaulted = tools.takeScreenshot({});
    ASSERT_EQ(defaulted["status"], "success");
    EXPECT_EQ(fs::u8path(defaulted["file_path"].get<std::string>()).parent_path(), root_ / "screenshots");
#endif
}

TEST_F(MediaToolsTest, TakeScreenshotRefusesPathsOutsideTheAllowedFolders) {
    FlagshipTools tools(ctx());
    json r = tools.takeScreenshot({{"output_path", (root_ / "elsewhere" / "shot.png").u8string()}});
    EXPECT_EQ(r["status"], "error");
    EXPECT_NE(r["error"].get<std::string>().find("outside the folders"), std::string::npos);
}

// ---- Registration ----------------------------------------------------------

TEST_F(MediaToolsTest, RegistersMediaToolsUnderPythonNamesWithoutConfirmGates) {
    FlagshipTools tools(ctx());
    ToolRegistry reg;
    tools.registerAll(reg);
    for (const char* t : {"generate_image", "list_sd_models", "get_generation_history", "transcribe_media",
                          "refine_transcript", "take_screenshot"}) {
        ASSERT_TRUE(reg.hasTool(t)) << t;
        EXPECT_EQ(reg.findTool(t)->policy, ToolPolicy::ALLOW) << t;  // none is gated in Python
    }

    FlagshipToolContext offline = ctx();
    offline.lemonadeUrl.clear();
    FlagshipTools noLemonade(offline);
    ToolRegistry bare;
    noLemonade.registerAll(bare);
    EXPECT_TRUE(bare.hasTool("take_screenshot"));
    EXPECT_FALSE(bare.hasTool("generate_image"));
    EXPECT_FALSE(bare.hasTool("transcribe_media"));
}
