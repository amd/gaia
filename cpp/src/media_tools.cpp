// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/media_tools.h"

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <sstream>

#include "gaia/http_client.h"

namespace fs = std::filesystem;

namespace gaia {

namespace {

constexpr size_t kMaxAudioBytes = 100u * 1024u * 1024u;

std::string lowerExt(const std::string& p) {
    std::string e = fs::path(p).extension().string();
    std::transform(e.begin(), e.end(), e.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return e;
}

std::string audioMime(const std::string& ext) {
    if (ext == ".wav") return "audio/wav";
    if (ext == ".mp3") return "audio/mpeg";
    if (ext == ".m4a" || ext == ".mp4") return "audio/mp4";
    if (ext == ".ogg" || ext == ".opus") return "audio/ogg";
    if (ext == ".flac") return "audio/flac";
    if (ext == ".webm") return "audio/webm";
    return "";
}

HttpClient clientFor(const MediaEndpoint& ep, int timeoutSec) {
    HttpClientConfig cfg;
    cfg.baseUrl = ep.baseUrl;
    cfg.timeoutSec = timeoutSec;
    if (!ep.apiKey.empty()) cfg.defaultHeaders["Authorization"] = "Bearer " + ep.apiKey;
    return HttpClient(cfg);
}

std::string serverMessage(const HttpError& e) {
    try {
        const json body = json::parse(e.body());
        if (body.contains("error")) {
            const json& err = body["error"];
            return err.is_object() ? err.value("message", err.dump()) : err.dump();
        }
    } catch (const std::exception&) {
    }
    return e.what();
}

}  // namespace

std::string MediaTools::base64Decode(const std::string& in) {
    static const std::string chars =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    int val = 0, bits = -8;
    for (unsigned char c : in) {
        if (c == '=' ) break;
        if (std::isspace(c)) continue;
        const size_t pos = chars.find(static_cast<char>(c));
        if (pos == std::string::npos) throw std::invalid_argument("invalid base64 character");
        val = (val << 6) + static_cast<int>(pos);
        bits += 6;
        if (bits >= 0) {
            out.push_back(static_cast<char>((val >> bits) & 0xFF));
            bits -= 8;
        }
    }
    return out;
}

void MediaTools::registerAll(ToolRegistry& registry, MediaToolsOptions options) {
    auto opts = std::make_shared<MediaToolsOptions>(std::move(options));

    if (!opts->transcribeModel.empty()) {
        ToolInfo t;
        t.name = "transcribe_audio";
        t.description =
            "Transcribe a speech recording (wav, mp3, m4a, ogg, flac, webm) to text with Whisper.";
        t.parameters = {{"path", ToolParamType::STRING, true, "Audio file."},
                        {"language", ToolParamType::STRING, false, "ISO code, e.g. en (optional)."}};
        t.policy = ToolPolicy::ALLOW;
        t.callback = [opts](const json& a) -> json {
            const std::string path = a.value("path", "");
            if (opts->checkPath) {
                const std::string why = opts->checkPath(path);
                if (!why.empty()) return {{"status", "error"}, {"error", why}};
            }
            const std::string mime = audioMime(lowerExt(path));
            if (mime.empty()) {
                return {{"status", "error"},
                        {"error", "Not an audio file this tool reads (wav, mp3, m4a, ogg, flac, webm): " + path}};
            }
            std::error_code ec;
            const auto size = fs::file_size(path, ec);
            if (ec) return {{"status", "error"}, {"error", "Can't read " + path + ": " + ec.message()}};
            if (size > kMaxAudioBytes) {
                return {{"status", "error"}, {"error", path + " is over 100 MB; split it first."}};
            }
            std::ifstream in(path, std::ios::binary);
            std::ostringstream data;
            data << in.rdbuf();
            std::vector<HttpMultipartPart> parts = {
                {"file", data.str(), fs::path(path).filename().string(), mime},
                {"model", opts->transcribeModel, "", ""},
                {"response_format", "json", "", ""}};
            if (!a.value("language", "").empty()) parts.push_back({"language", a.value("language", ""), "", ""});
            const MediaEndpoint ep = opts->audioEndpoint ? opts->audioEndpoint() : opts->chatEndpoint();
            try {
                HttpClient http = clientFor(ep, 600);
                const json r = json::parse(http.postMultipart("/audio/transcriptions", parts).body);
                return {{"status", "success"}, {"model", opts->transcribeModel},
                        {"text", r.value("text", "")}};
            } catch (const HttpError& e) {
                return {{"status", "error"},
                        {"error", "Transcription with " + opts->transcribeModel + " at " + ep.baseUrl +
                                  " failed: " + serverMessage(e)}};
            } catch (const std::exception& e) {
                return {{"status", "error"}, {"error", std::string("Transcription failed: ") + e.what()}};
            }
        };
        registry.registerTool(std::move(t));
    }

    if (!opts->imageModel.empty()) {
        ToolInfo g;
        g.name = "generate_image";
        g.description = "Generate an image from a text description and save it as a PNG file.";
        g.parameters = {{"prompt", ToolParamType::STRING, true, "What the image shows."},
                        {"output_path", ToolParamType::STRING, true, "Where to save the .png."},
                        {"size", ToolParamType::STRING, false, "WIDTHxHEIGHT, e.g. 512x512 (default)."}};
        g.policy = ToolPolicy::CONFIRM;  // writes a file
        g.callback = [opts](const json& a) -> json {
            const std::string out = a.value("output_path", "");
            if (opts->checkPath) {
                const std::string why = opts->checkPath(out);
                if (!why.empty()) return {{"status", "error"}, {"error", why}};
            }
            if (lowerExt(out) != ".png") {
                return {{"status", "error"}, {"error", "output_path must end in .png"}};
            }
            const MediaEndpoint ep = opts->chatEndpoint();
            json body = {{"model", opts->imageModel},
                         {"prompt", a.value("prompt", "")},
                         {"size", a.value("size", "512x512")},
                         {"n", 1},
                         {"response_format", "b64_json"}};
            try {
                HttpClient http = clientFor(ep, 600);
                const json r = json::parse(http.post("/images/generations", body.dump()).body);
                const std::string b64 = r.at("data").at(0).value("b64_json", "");
                if (b64.empty()) {
                    return {{"status", "error"}, {"error", "The server returned no image data."}};
                }
                const std::string png = base64Decode(b64);
                std::error_code ec;
                if (fs::path(out).has_parent_path()) fs::create_directories(fs::path(out).parent_path(), ec);
                std::ofstream f(out, std::ios::binary);
                if (!f) return {{"status", "error"}, {"error", "Can't write " + out}};
                f << png;
                return {{"status", "success"}, {"path", out}, {"bytes", png.size()},
                        {"model", opts->imageModel}};
            } catch (const HttpError& e) {
                return {{"status", "error"},
                        {"error", "Image generation with " + opts->imageModel + " failed: " + serverMessage(e)}};
            } catch (const std::exception& e) {
                return {{"status", "error"}, {"error", std::string("Image generation failed: ") + e.what()}};
            }
        };
        registry.registerTool(std::move(g));
    }
}

} // namespace gaia
