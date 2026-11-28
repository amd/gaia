// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/media_tools.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <map>
#include <optional>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <vector>

#include "gaia/http_client.h"
#include "gaia/process.h"
#include "gaia/security.h"
#include "gaia/types.h"

namespace fs = std::filesystem;

namespace gaia {

namespace {

constexpr size_t kMaxAudioBytes = 100u * 1024u * 1024u;
constexpr const char* kDefaultImageModel = "SDXL-Turbo";
constexpr int kImageMaxSteps = 100;

struct LegacySdDefaults {
    const char* size;
    int steps;
    double cfgScale;
};

const std::map<std::string, LegacySdDefaults>& legacySdModels() {
    static const std::map<std::string, LegacySdDefaults> kModels = {
        {"SD-1.5", {"512x512", 20, 7.5}},
        {"SD-Turbo", {"512x512", 4, 1.0}},
        {"SDXL-Base-1.0", {"1024x1024", 20, 7.5}},
        {"SDXL-Turbo", {"512x512", 4, 1.0}},
    };
    return kModels;
}

bool isValidLegacySdSize(const std::string& size) {
    return size == "512x512" || size == "768x768" || size == "1024x1024";
}

bool isValidImageSize(const std::string& size) {
    static const std::regex kPattern(R"(^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$)");
    return std::regex_match(size, kPattern);
}

std::string trim(const std::string& s) {
    const size_t b = s.find_first_not_of(" \t\r\n");
    if (b == std::string::npos) return "";
    return s.substr(b, s.find_last_not_of(" \t\r\n") - b + 1);
}

fs::path expandUserPath(const std::string& raw) {
    if (raw == "~" || raw.rfind("~/", 0) == 0 || raw.rfind("~\\", 0) == 0) {
#ifdef _WIN32
        const std::string home = getEnvVar("USERPROFILE");
#else
        const std::string home = getEnvVar("HOME");
#endif
        if (!home.empty()) {
            const fs::path base = fs::u8path(home);
            return raw.size() <= 2 ? base : base / fs::u8path(raw.substr(2));
        }
    }
    return fs::u8path(raw);
}

fs::path gaiaConfigDirPath() {
    const std::string explicitDir = trim(getEnvVar("GAIA_CONFIG_DIR"));
    if (!explicitDir.empty()) return expandUserPath(explicitDir);
    const std::string home = trim(gaiaHome());
    return home.empty() ? fs::path() : expandUserPath(home);
}

fs::path gaiaConfigFilePath() {
    const std::string file = trim(getEnvVar("GAIA_CONFIG_FILE"));
    if (!file.empty()) return expandUserPath(file);
    const fs::path dir = gaiaConfigDirPath();
    return dir.empty() ? fs::path() : dir / "config.json";
}

std::string sanitizeFilenameSegment(const std::string& raw, size_t maxLen, const std::string& fallback) {
    std::string out;
    bool lastUnderscore = false;
    for (unsigned char c : raw) {
        if (out.size() >= maxLen) break;
        if (std::isalnum(c) || c == '-') {
            out.push_back(static_cast<char>(c));
            lastUnderscore = false;
        } else if (c == '_' || std::isspace(c)) {
            if (!out.empty() && !lastUnderscore) {
                out.push_back('_');
                lastUnderscore = true;
            }
        }
    }
    while (!out.empty() && out.back() == '_') out.pop_back();
    return out.empty() ? fallback : out;
}

std::string shortBytesHashHex(const std::string& data) {
    uint64_t h = 14695981039346656037ULL;
    for (unsigned char c : data) {
        h ^= static_cast<uint64_t>(c);
        h *= 1099511628211ULL;
    }
    std::ostringstream os;
    os << std::hex << std::setfill('0') << std::setw(8) << static_cast<uint32_t>(h ^ (h >> 32));
    return os.str();
}

std::string timestampTag() {
    const std::time_t now = std::time(nullptr);
    std::tm tmBuf{};
#ifdef _WIN32
    localtime_s(&tmBuf, &now);
#else
    localtime_r(&now, &tmBuf);
#endif
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y%m%d_%H%M%S", &tmBuf);
    return buf;
}

bool isImageCatalogEntry(const json& entry) {
    if (!entry.is_object()) return false;
    const std::string id = entry.value("id", "");
    if (id.empty()) return false;
    bool hasImageLabel = false;
    bool isUpscaler = false;
    if (entry.contains("labels") && entry["labels"].is_array()) {
        for (const auto& lbl : entry["labels"]) {
            if (!lbl.is_string()) continue;
            const std::string s = lbl.get<std::string>();
            if (s == "image") hasImageLabel = true;
            if (s == "upscaling") isUpscaler = true;
        }
    }
    if (isUpscaler) return false;
    if (legacySdModels().count(id)) return true;
    const std::string recipe = entry.value("recipe", "");
    return hasImageLabel || recipe == "sd-cpp" || recipe == "thenoise";
}

std::string formatImageHttpError(const std::string& model, const MediaEndpoint& ep, const HttpError& e,
                                 const std::string& serverMsg) {
    if (e.status() == 0) {
        return "Image generation with " + model + " failed: could not reach Lemonade at " + ep.baseUrl + " (" +
               e.what() + "). Check that Lemonade is running.";
    }
    if (e.status() == 401 || e.status() == 403) {
        return "Image generation with " + model + " failed (HTTP " + std::to_string(e.status()) +
               "): authentication rejected by " + ep.baseUrl + ". Check LEMONADE_API_KEY.";
    }
    if (e.status() == 404) {
        return "Image generation with " + model + " failed (HTTP 404): " + serverMsg +
               ". Confirm the model is downloaded in Settings > Agent & model or choose another in Settings > Image "
               "creation.";
    }
    return "Image generation with " + model + " failed (HTTP " + std::to_string(e.status()) + "): " + serverMsg;
}

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

ImageSettings MediaTools::loadImageSettingsFromConfig() {
    ImageSettings out;
    const fs::path path = gaiaConfigFilePath();
    std::error_code ec;
    if (path.empty() || !fs::is_regular_file(path, ec)) return out;
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("Cannot read GAIA's settings at " + path.u8string() + ".");
    json cfg;
    try {
        in >> cfg;
    } catch (const std::exception& e) {
        throw std::runtime_error("GAIA's settings at " + path.u8string() + " are not valid JSON (" + e.what() + ").");
    }
    if (!cfg.is_object()) {
        throw std::runtime_error("GAIA's settings at " + path.u8string() + " must hold a JSON object.");
    }
    auto readOptStr = [&](const char* key) -> std::string {
        if (!cfg.contains(key) || cfg[key].is_null()) return "";
        if (!cfg[key].is_string()) {
            throw std::runtime_error("GAIA's settings at " + path.u8string() + ": \"" + key +
                                     "\" must be a string, got " + cfg[key].dump() + ".");
        }
        return trim(cfg[key].get<std::string>());
    };
    out.model = readOptStr("image_model");
    out.size = readOptStr("image_size");
    if (cfg.contains("image_steps") && !cfg["image_steps"].is_null()) {
        if (!cfg["image_steps"].is_number_integer() || cfg["image_steps"].is_boolean()) {
            throw std::runtime_error("GAIA's settings at " + path.u8string() +
                                     ": \"image_steps\" must be a whole number, got " + cfg["image_steps"].dump() + ".");
        }
        const long long steps = cfg["image_steps"].get<long long>();
        if (steps < 1 || steps > kImageMaxSteps) {
            throw std::runtime_error("GAIA's settings at " + path.u8string() + ": \"image_steps\" must be from 1 to " +
                                     std::to_string(kImageMaxSteps) + ", got " + std::to_string(steps) + ".");
        }
        out.steps = static_cast<int>(steps);
    }
    return out;
}

std::string MediaTools::defaultImageOutputDir() {
    const fs::path dir = gaiaConfigDirPath();
    return dir.empty() ? std::string() : (dir / "cache" / "sd" / "images").u8string();
}

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

    if (opts->enableImageTools || !opts->imageModel.empty()) {
        auto readSavedSettings = [opts]() -> ImageSettings {
            if (opts->imageSettings) return opts->imageSettings();
            if (opts->enableImageTools) return MediaTools::loadImageSettingsFromConfig();
            return ImageSettings{};
        };

        ToolInfo listTool;
        listTool.name = "list_sd_models";
        listTool.description =
            "List downloaded image generation models (SD.cpp and TheNoise) available on Lemonade, "
            "along with the configured default image model.";
        listTool.policy = ToolPolicy::ALLOW;
        listTool.callback = [opts, readSavedSettings](const json&) -> json {
            ImageSettings saved;
            try {
                saved = readSavedSettings();
            } catch (const std::exception& e) {
                return {{"status", "error"}, {"error", e.what()}};
            }
            const std::string defaultModel =
                !opts->imageModel.empty() ? opts->imageModel : (!saved.model.empty() ? saved.model : kDefaultImageModel);
            const MediaEndpoint ep = opts->chatEndpoint();
            try {
                HttpClient http = clientFor(ep, 30);
                const json catalog = json::parse(http.get("/models").body);
                if (!catalog.is_object() || !catalog.contains("data") || !catalog["data"].is_array()) {
                    return {{"status", "error"},
                            {"error", "Lemonade at " + ep.baseUrl + " returned an unexpected /models response."}};
                }
                json models = json::array();
                for (const auto& entry : catalog["data"]) {
                    if (!isImageCatalogEntry(entry)) continue;
                    const std::string id = entry.value("id", "");
                    json item = {{"name", id}};
                    if (entry.contains("recipe") && entry["recipe"].is_string()) {
                        item["recipe"] = entry["recipe"];
                    }
                    if (entry.contains("image_defaults") && entry["image_defaults"].is_object()) {
                        item["image_defaults"] = entry["image_defaults"];
                    }
                    if (const auto it = legacySdModels().find(id); it != legacySdModels().end()) {
                        item["recommended_size"] = it->second.size;
                        item["recommended_steps"] = it->second.steps;
                    }
                    models.push_back(std::move(item));
                }
                return {{"status", "success"}, {"models", std::move(models)}, {"default_model", defaultModel}};
            } catch (const HttpError& e) {
                return {{"status", "error"}, {"error", formatImageHttpError("image models", ep, e, serverMessage(e))}};
            } catch (const std::exception& e) {
                return {{"status", "error"},
                        {"error", std::string("Could not list image models from Lemonade: ") + e.what()}};
            }
        };
        registry.registerTool(std::move(listTool));

        ToolInfo g;
        g.name = "generate_image";
        g.description =
            "Generate an image from a text description with a Lemonade image model and save it as a PNG file. "
            "Only for pictures; never for slides, presentations, documents or charts.";
        g.parameters = {
            {"prompt", ToolParamType::STRING, true, "Text description of the image to generate."},
            {"model", ToolParamType::STRING, false,
             "Exact image model ID from list_sd_models(). Omit to use the configured image model."},
            {"size", ToolParamType::STRING, false,
             "Optional WIDTHxHEIGHT (e.g. 512x512, 1024x1024); omit for model or configured default."},
            {"steps", ToolParamType::INTEGER, false,
             "Optional generation steps (1-100); omit for model or configured default."},
            {"cfg_scale", ToolParamType::NUMBER, false, "Optional guidance scale; omit for model default."},
            {"seed", ToolParamType::INTEGER, false, "Optional integer seed for reproducibility."},
            {"output_path", ToolParamType::STRING, false,
             "Optional .png path to save to; omit to save into GAIA's generated images folder for inline preview."},
        };
        g.policy = ToolPolicy::CONFIRM;
        // Saving into GAIA's own generated-images cache needs no approval; an explicit workspace path does.
        g.needsConfirm = [](const json& a) {
            return a.contains("output_path") && a["output_path"].is_string() &&
                   !trim(a["output_path"].get<std::string>()).empty();
        };
        g.callback = [opts, readSavedSettings](const json& a) -> json {
            const std::string prompt = trim(a.value("prompt", ""));
            if (prompt.empty()) {
                return {{"status", "error"}, {"error", "prompt must not be empty."}};
            }

            const std::string explicitOut =
                a.contains("output_path") && a["output_path"].is_string() ? trim(a["output_path"].get<std::string>())
                                                                          : std::string();
            fs::path explicitOutPath;
            if (!explicitOut.empty()) {
                if (explicitOut.find('\0') != std::string::npos) {
                    return {{"status", "error"}, {"error", "output_path contains a NUL byte."}};
                }
                if (opts->checkPath) {
                    const std::string why = opts->checkPath(explicitOut);
                    if (!why.empty()) return {{"status", "error"}, {"error", why}};
                }
                if (lowerExt(explicitOut) != ".png") {
                    return {{"status", "error"}, {"error", "output_path must end in .png"}};
                }
                std::error_code absEc;
                explicitOutPath = fs::absolute(fs::u8path(explicitOut), absEc);
                if (absEc) {
                    return {{"status", "error"},
                            {"error", "Can't resolve output_path " + explicitOut + ": " + absEc.message()}};
                }
            }

            ImageSettings saved;
            try {
                saved = readSavedSettings();
            } catch (const std::exception& e) {
                return {{"status", "error"}, {"error", e.what()}};
            }

            const std::string requestedModel =
                a.contains("model") && a["model"].is_string() ? trim(a["model"].get<std::string>()) : std::string();
            const std::string configuredDefault =
                !opts->imageModel.empty() ? opts->imageModel : (!saved.model.empty() ? saved.model : kDefaultImageModel);
            const std::string model = !requestedModel.empty() ? requestedModel : configuredDefault;

            const bool useSavedParams =
                opts->imageModel.empty() && !saved.model.empty() && (requestedModel.empty() || requestedModel == saved.model);

            std::string size =
                a.contains("size") && a["size"].is_string() ? trim(a["size"].get<std::string>()) : std::string();
            if (size.empty() && useSavedParams) size = saved.size;

            std::optional<int> steps;
            if (a.contains("steps") && !a["steps"].is_null()) {
                if (!a["steps"].is_number_integer() || a["steps"].is_boolean()) {
                    return {{"status", "error"}, {"error", "steps must be an integer from 1 to 100."}};
                }
                const long long rawSteps = a["steps"].get<long long>();
                if (rawSteps < 1 || rawSteps > kImageMaxSteps) {
                    return {{"status", "error"},
                            {"error", "steps must be from 1 to " + std::to_string(kImageMaxSteps) + ", got " +
                                          std::to_string(rawSteps) + "."}};
                }
                steps = static_cast<int>(rawSteps);
            } else if (useSavedParams && saved.steps > 0) {
                steps = saved.steps;
            }

            std::optional<double> cfgScale;
            if (a.contains("cfg_scale") && !a["cfg_scale"].is_null()) {
                if (!a["cfg_scale"].is_number() || a["cfg_scale"].is_boolean()) {
                    return {{"status", "error"}, {"error", "cfg_scale must be a number."}};
                }
                cfgScale = a["cfg_scale"].get<double>();
            }

            std::optional<int64_t> seed;
            if (a.contains("seed") && !a["seed"].is_null()) {
                if (!a["seed"].is_number_integer() || a["seed"].is_boolean()) {
                    return {{"status", "error"}, {"error", "seed must be an integer."}};
                }
                seed = a["seed"].get<int64_t>();
            }

            const auto legacyIt = legacySdModels().find(model);
            const bool isLegacySd = legacyIt != legacySdModels().end();
            if (isLegacySd) {
                if (size.empty()) size = legacyIt->second.size;
                if (!steps.has_value()) steps = legacyIt->second.steps;
                if (!cfgScale.has_value()) cfgScale = legacyIt->second.cfgScale;
                if (!isValidLegacySdSize(size)) {
                    return {{"status", "error"},
                            {"error", "Invalid size '" + size + "' for " + model +
                                          ". Choose from: 512x512, 768x768, 1024x1024."}};
                }
            } else if (!size.empty() && !isValidImageSize(size)) {
                return {{"status", "error"}, {"error", "Invalid size '" + size + "'. Expected WIDTHxHEIGHT."}};
            }

            fs::path outputDir;
            if (explicitOut.empty()) {
                const std::string dirStr =
                    opts->imageOutputDir ? opts->imageOutputDir() : MediaTools::defaultImageOutputDir();
                if (dirStr.empty()) {
                    return {{"status", "error"},
                            {"error",
                             "Cannot determine generated images folder. Set GAIA_HOME or pass an explicit output_path."}};
                }
                outputDir = fs::u8path(dirStr);
                std::error_code dirEc;
                fs::create_directories(outputDir, dirEc);
                if (dirEc) {
                    return {{"status", "error"},
                            {"error", "Cannot create generated images folder " + dirStr + ": " + dirEc.message()}};
                }
            }

            const MediaEndpoint ep = opts->chatEndpoint();
            // For non-legacy models, verify download status up front so we never trigger an unintended download.
            if (!isLegacySd) {
                try {
                    HttpClient probe = clientFor(ep, 30);
                    const json catalog = json::parse(probe.get("/models").body);
                    bool found = false;
                    if (catalog.is_object() && catalog.contains("data") && catalog["data"].is_array()) {
                        for (const auto& entry : catalog["data"]) {
                            if (isImageCatalogEntry(entry) && entry.value("id", "") == model &&
                                (!entry.contains("downloaded") || entry.value("downloaded", false))) {
                                found = true;
                                break;
                            }
                        }
                    }
                    if (!found) {
                        if (!saved.model.empty() && model == saved.model) {
                            return {{"status", "error"},
                                    {"error", model +
                                                  ", the image model chosen in GAIA's image settings, is not downloaded "
                                                  "on this Lemonade. Choose another model in Settings > Image creation, "
                                                  "or download " +
                                                  model + " in Settings > Agent & model."}};
                        }
                        return {{"status", "error"},
                                {"error", "Image model '" + model +
                                              "' is not downloaded on this Lemonade. Call list_sd_models() to pick a "
                                              "downloaded model."}};
                    }
                } catch (const HttpError& e) {
                    return {{"status", "error"}, {"error", formatImageHttpError(model, ep, e, serverMessage(e))}};
                } catch (const std::exception& e) {
                    return {{"status", "error"},
                            {"error", "Could not verify image model '" + model + "' on Lemonade: " + e.what()}};
                }
            }

            json body = {
                {"model", model},
                {"prompt", prompt},
                {"n", 1},
                {"response_format", "b64_json"},
            };
            if (!size.empty()) body["size"] = size;
            if (steps.has_value()) body["steps"] = *steps;
            if (cfgScale.has_value()) body["cfg_scale"] = *cfgScale;
            if (seed.has_value()) body["seed"] = *seed;

            const auto start = std::chrono::steady_clock::now();
            try {
                HttpClient http = clientFor(ep, 900);
                const json r = json::parse(http.post("/images/generations", body.dump()).body);
                if (!r.is_object() || !r.contains("data") || !r["data"].is_array() || r["data"].empty() ||
                    !r["data"][0].is_object()) {
                    return {{"status", "error"}, {"error", "The server reply had no image data."}};
                }
                const std::string b64 = r["data"][0].value("b64_json", "");
                if (b64.empty()) {
                    return {{"status", "error"}, {"error", "The server returned no image data."}};
                }
                std::string png;
                try {
                    png = base64Decode(b64);
                } catch (const std::exception& e) {
                    return {{"status", "error"},
                            {"error", std::string("The server returned invalid base64 image data: ") + e.what()}};
                }
                if (png.empty()) {
                    return {{"status", "error"}, {"error", "The server returned empty image data."}};
                }

                fs::path targetPath;
                if (!explicitOut.empty()) {
                    targetPath = explicitOutPath;
                    std::error_code parentEc;
                    if (targetPath.has_parent_path()) fs::create_directories(targetPath.parent_path(), parentEc);
                } else {
                    const std::string safePrompt = sanitizeFilenameSegment(prompt, 40, "image");
                    const std::string safeModel = sanitizeFilenameSegment(model, 60, "model");
                    const std::string filename =
                        safePrompt + "_" + safeModel + "_" + timestampTag() + "_" + shortBytesHashHex(png) + ".png";
                    std::error_code canonEc;
                    const fs::path canonDir = fs::canonical(outputDir, canonEc);
                    const fs::path baseDir = canonEc ? fs::absolute(outputDir) : canonDir;
                    targetPath = fs::weakly_canonical(baseDir / filename, canonEc);
                    if (canonEc || targetPath.parent_path() != baseDir) {
                        return {{"status", "error"}, {"error", "Unsafe generated image output path."}};
                    }
                }

                std::ofstream f(targetPath, std::ios::binary);
                if (!f) {
                    return {{"status", "error"}, {"error", "Can't write " + targetPath.u8string()}};
                }
                f.write(png.data(), static_cast<std::streamsize>(png.size()));
                f.close();
                std::error_code sizeEc;
                if (f.fail() || !fs::is_regular_file(targetPath, sizeEc) || fs::file_size(targetPath, sizeEc) != png.size()) {
                    fs::remove(targetPath, sizeEc);
                    return {{"status", "error"},
                            {"error", "Writing " + targetPath.u8string() +
                                          " failed (the disk may be full); no image was saved."}};
                }

                const double elapsed =
                    std::round(std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count() * 10.0) /
                    10.0;
                const std::string absStr = targetPath.u8string();
                json result = {
                    {"status", "success"},
                    {"image_path", absStr},
                    {"path", absStr},
                    {"file_path", absStr},
                    {"prompt", prompt},
                    {"model", model},
                    {"bytes", png.size()},
                    {"generation_time_s", elapsed},
                    {"display_message", "Saved " + absStr},
                };
                if (!size.empty()) result["size"] = size;
                if (steps.has_value()) result["steps"] = *steps;
                if (seed.has_value()) result["seed"] = *seed;
                return result;
            } catch (const HttpError& e) {
                return {{"status", "error"}, {"error", formatImageHttpError(model, ep, e, serverMessage(e))}};
            } catch (const std::exception& e) {
                return {{"status", "error"}, {"error", std::string("Image generation failed: ") + e.what()}};
            }
        };
        registry.registerTool(std::move(g));
    }
}

} // namespace gaia
