// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/media_tools.h"

#include <algorithm>
#include <cctype>
#include <stdexcept>
#include <ctime>
#include <vector>
#include <filesystem>
#include <fstream>
#include <sstream>

#include "gaia/http_client.h"
#include "gaia/process.h"

namespace fs = std::filesystem;

namespace gaia {

namespace {

constexpr size_t kMaxAudioBytes = 100u * 1024u * 1024u;

std::string lowerExt(const std::string& p) {
    std::string e = fs::u8path(p).extension().u8string();
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
    // Unsigned: the accumulator keeps shifting old bits out the top, which is
    // undefined for a signed int once an image is more than a few characters.
    unsigned int val = 0;
    int bits = -8;
    for (unsigned char c : in) {
        if (c == '=' ) break;
        if (std::isspace(c)) continue;
        const size_t pos = chars.find(static_cast<char>(c));
        if (pos == std::string::npos) throw std::invalid_argument("invalid base64 character");
        val = (val << 6) + static_cast<unsigned int>(pos);
        bits += 6;
        if (bits >= 0) {
            out.push_back(static_cast<char>((val >> bits) & 0xFF));
            bits -= 8;
        }
    }
    return out;
}

ToolInfo MediaTools::screenshotTool(std::function<std::string(const std::string&)> checkPath) {
    ToolInfo t;
    t.name = "take_screenshot";
    t.description =
        "Capture the whole screen to a PNG file. Use analyze_image on the file to read it.";
    t.parameters = {{"output_path", ToolParamType::STRING, false,
                     "Where to save the .png (default: screenshot-<time>.png here)."}};
    t.policy = ToolPolicy::CONFIRM;
    t.callback = [checkPath](const json& a) -> json {
        std::string out = a.value("output_path", "");
        if (out.empty()) out = "screenshot-" + std::to_string(std::time(nullptr)) + ".png";
        if (checkPath) {
            const std::string why = checkPath(out);
            if (!why.empty()) return {{"status", "error"}, {"error", why}};
        }
        if (lowerExt(out) != ".png") return {{"status", "error"}, {"error", "output_path must end in .png"}};
        std::error_code absEc;
        const fs::path absPath = fs::absolute(fs::u8path(out), absEc);
        if (absEc) return {{"status", "error"}, {"error", "Can't resolve output_path " + out + ": " + absEc.message()}};
        const std::string abs = absPath.u8string();
        // The capture program is started through a shell, where these would end the quoted path.
#ifdef _WIN32
        const std::string unsafe = "\"`$%;&|<>";  // a ' is doubled for PowerShell below
#else
        const std::string unsafe = "'\"`$%;&|<>";
#endif
        for (const unsigned char c : abs) {
            if (c < 0x20 || c == 0x7F || unsafe.find(static_cast<char>(c)) != std::string::npos) {
                return {{"status", "error"},
                        {"error", "The full path of output_path (" + abs + ") holds a quote, a control character "
                                  "or one of ` $ % ; & | < >, which a screenshot path may not contain. Save to a "
                                  "path without them."}};
            }
        }
        std::vector<std::string> attempts;
#ifdef _WIN32
        std::string ps = abs;
        for (size_t i = 0; (i = ps.find('\'', i)) != std::string::npos; i += 2) ps.insert(i, "'");
        // Windows' own PowerShell by full path: cmd.exe would run a powershell.bat in the working folder first.
        std::string powershell;
        try {
            powershell = systemProgramPath("WindowsPowerShell\\v1.0\\powershell.exe");
        } catch (const std::runtime_error& e) {
            return {{"status", "error"}, {"error", e.what()}};
        }
        if (powershell.find(' ') != std::string::npos) {
            return {{"status", "error"}, {"error", "Windows is installed in a folder with a space (" + powershell +
                                                       "), which the screenshot command can't start PowerShell from."}};
        }
        attempts.push_back(
            powershell + " -NoProfile -Command \"Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
            "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen; "
            "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height; "
            "$g=[System.Drawing.Graphics]::FromImage($bmp); "
            "$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size); $bmp.Save('" + ps + "')\"");
#elif defined(__APPLE__)
        attempts.push_back("screencapture -x '" + abs + "'");
#else
        attempts = {"grim '" + abs + "'", "gnome-screenshot -f '" + abs + "'", "scrot -o '" + abs + "'",
                    "import -window root '" + abs + "'"};
#endif
        std::string why;
        for (const auto& cmd : attempts) {
            const ProcessResult r = ProcessRunner::run(cmd, 30000);
            std::error_code ec;
            if (r.exitCode == 0 && fs::exists(absPath, ec) && fs::file_size(absPath, ec) > 0) {
                return {{"status", "success"}, {"path", out}, {"bytes", fs::file_size(absPath, ec)}};
            }
            if (r.exitCode != 127) why += (why.empty() ? "" : "; ") + cmd.substr(0, cmd.find(' ')) + ": " +
                                          r.stderr_output.substr(0, 160);
        }
        return {{"status", "error"},
                {"error", why.empty() ? "No screenshot tool is installed (grim, gnome-screenshot, scrot "
                                        "or ImageMagick), or there is no display."
                                      : "Screenshot failed: " + why}};
    };
    return t;
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
            const auto size = fs::file_size(fs::u8path(path), ec);
            if (ec) return {{"status", "error"}, {"error", "Can't read " + path + ": " + ec.message()}};
            if (size > kMaxAudioBytes) {
                return {{"status", "error"}, {"error", path + " is over 100 MB; split it first."}};
            }
            std::ifstream in(fs::u8path(path), std::ios::binary);
            if (!in.is_open()) return {{"status", "error"}, {"error", "Can't open " + path + " for reading."}};
            std::ostringstream data;
            data << in.rdbuf();
            std::vector<HttpMultipartPart> parts = {
                {"file", data.str(), fs::u8path(path).filename().u8string(), mime},
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
                const fs::path outPath = fs::u8path(out);
                if (outPath.has_parent_path()) fs::create_directories(outPath.parent_path(), ec);
                std::ofstream f(outPath, std::ios::binary);
                if (!f) return {{"status", "error"}, {"error", "Can't write " + out}};
                f.write(png.data(), static_cast<std::streamsize>(png.size()));
                f.close();
                if (f.fail()) {
                    fs::remove(outPath, ec);
                    return {{"status", "error"},
                            {"error", "Writing " + out + " failed (the disk may be full); no image was saved."}};
                }
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
