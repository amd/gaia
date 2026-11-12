// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "stdio_server.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <random>
#include <set>
#include <stdexcept>

#include <gaia/console.h>
#include <gaia/http_client.h>
#include <gaia/lemonade_client.h>
#include <gaia/memory_store.h>

#include "gaia_agent.h"

namespace fs = std::filesystem;

namespace gaia {

namespace {

const char* const kQueryKey = "gaia_query";
const char* const kControlKey = "gaia_control";
const std::string kClearQuery("\0gaia:clear_conversation\0", 25);
const std::string kMemoryDumpQuery("\0gaia:memory_dump\0", 18);
const std::string kWarmUpQuery("\0gaia:warm_up\0", 14);
const char* const kLlmFailure = "Unable to complete task due to LLM error: ";
// The TUI answers within its own 10 minutes; this only fires when nobody can.
constexpr auto kOrphanedConfirmTimeout = std::chrono::minutes(15);
constexpr int kStatusProbeSeconds = 3;
constexpr auto kTokenBatch = std::chrono::milliseconds(30);
constexpr size_t kMaxEventData = 20000;

std::string dumps(const json& j) { return j.dump(-1, ' ', false, json::error_handler_t::replace); }

std::string randomHex(size_t n) {
    static thread_local std::mt19937_64 rng(std::random_device{}());
    static const char* hex = "0123456789abcdef";
    std::string s;
    for (size_t i = 0; i < n; ++i) s.push_back(hex[rng() & 0xF]);
    return s;
}

std::string lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

std::string clip(const std::string& s, size_t n) {
    if (s.size() <= n) return s;
    size_t cut = n;
    while (cut > 0 && (static_cast<unsigned char>(s[cut]) & 0xC0) == 0x80) --cut;
    return s.substr(0, cut) + "…";
}

std::string argOf(const json& args, const char* key) {
    if (args.is_object() && args.contains(key) && args[key].is_string()) return args[key].get<std::string>();
    return "";
}

// One plain line on what a call is doing, for the TUI's activity log.
std::string narrate(const std::string& tool, const json& args) {
    if (tool == "file_read") return "Reading " + argOf(args, "path");
    if (tool == "file_write") return "Writing " + argOf(args, "path");
    if (tool == "file_edit") return "Editing " + argOf(args, "path");
    if (tool == "file_search") return "Searching files";
    if (tool == "bash_execute") return "Running " + clip(argOf(args, "command"), 120);
    if (tool == "web_fetch") return "Reading " + argOf(args, "url");
    if (tool == "remember") return "Saving to memory";
    if (tool == "recall") return "Checking memory";
    if (tool == "load_skill") return "Loading the " + argOf(args, "name") + " skill";
    if (tool == "query_documents") return "Searching the documents";
    if (tool == "index_document") return "Indexing " + argOf(args, "path");
    if (tool == "search_code") return "Searching the code";
    return "";
}

std::string previewOf(const json& result) {
    if (result.is_string()) return clip(result.get<std::string>(), 200);
    if (result.is_object()) {
        for (const char* key : {"error", "message", "output", "stdout", "content"}) {
            if (result.contains(key) && result[key].is_string() && !result[key].get<std::string>().empty()) {
                return clip(result[key].get<std::string>(), 200);
            }
        }
    }
    return clip(dumps(result), 200);
}

// Same as sidecar.cpp's toolRisk and grantLabel.
std::string toolRisk(const std::string& tool) {
    if (tool == "bash_execute") return "execute";
    if (tool == "web_fetch" || tool == "take_screenshot") return "read";
    return "write";
}

std::string grantLabel(const std::string& key) {
    const std::string shell = "bash_execute:";
    if (key.rfind(shell, 0) == 0) {
        const std::string scope = key.substr(shell.size());
        if (scope.rfind("cmd:", 0) == 0) return "Run " + scope.substr(4) + " commands";
        if (scope.rfind("exact:", 0) == 0) return "Run `" + scope.substr(6) + "`";
    }
    return "Use " + key.substr(0, key.find(':'));
}

// The field as a string, "" when absent; a value of another type is a malformed message.
std::string controlString(const json& message, const char* key) {
    if (!message.contains(key) || message[key].is_null()) return "";
    if (!message[key].is_string()) {
        throw std::invalid_argument(std::string("'") + key + "' must be a string, got " + message[key].type_name());
    }
    return message[key].get<std::string>();
}

bool controlFlag(const json& message, const char* key) {
    if (!message.contains(key) || !message[key].is_boolean()) {
        throw std::invalid_argument(std::string("'") + key + "' must be true or false");
    }
    return message[key].get<bool>();
}

std::vector<std::string> splitList(const std::string& s, char sep) {
    std::vector<std::string> out;
    size_t start = 0;
    while (start <= s.size()) {
        const size_t end = s.find(sep, start);
        const std::string part = s.substr(start, end == std::string::npos ? std::string::npos : end - start);
        if (!part.empty()) out.push_back(part);
        if (end == std::string::npos) break;
        start = end + 1;
    }
    return out;
}

#ifdef _WIN32
constexpr char kPathListSep = ';';
#else
constexpr char kPathListSep = ':';
#endif

// Path as a comparable key: the filesystem's own spelling, case-folded on Windows.
std::string pathKey(const fs::path& p) {
    std::error_code ec;
    fs::path real = fs::weakly_canonical(p, ec);
    if (ec) real = p.lexically_normal();
    std::string key = real.generic_u8string();
    while (key.size() > 1 && key.back() == '/' && key[key.size() - 2] != ':') key.pop_back();
#ifdef _WIN32
    key = lower(key);
#endif
    return key;
}

bool isHidden(const std::string& part) { return part.size() > 1 && part[0] == '.' && part != ".."; }

// A home folder or a drive root holds everything the user owns.
bool tooBroad(const fs::path& root) {
    std::error_code ec;
    const fs::path real = fs::weakly_canonical(root, ec);
    if (ec) return true;
    if (!real.has_relative_path()) return true;
    const std::string home = getEnvVar("USERPROFILE", getEnvVar("HOME"));
    return !home.empty() && pathKey(real) == pathKey(fs::u8path(home));
}

// Writing here can change what runs with no approval.
bool landsWhereCodeRuns(const fs::path& resolved) {
    for (const auto& part : resolved) {
        if (lower(part.u8string()) == ".git") return true;
    }
    const std::string parent = pathKey(resolved.parent_path());
    const std::vector<std::string> pathDirs = splitList(getEnvVar("PATH"), kPathListSep);
    for (const auto& dir : pathDirs) {
        if (pathKey(fs::u8path(dir)) == parent) return true;
    }
#ifdef _WIN32
    // cmd.exe runs a git.bat in the current folder before the real git.
    const std::string ext = lower(resolved.extension().u8string());
    const std::vector<std::string> exts = splitList(lower(getEnvVar("PATHEXT", ".COM;.EXE;.BAT;.CMD")), ';');
    if (!ext.empty() && std::find(exts.begin(), exts.end(), ext) != exts.end()) {
        const std::string stem = resolved.stem().u8string();
        for (const auto& dir : pathDirs) {
            for (const auto& e : exts) {
                std::error_code ec;
                if (fs::exists(fs::u8path(dir) / fs::u8path(stem + e), ec)) return true;
            }
        }
    }
#endif
    return false;
}

// Agent activity -> canonical events. Tool args and results arrive through
// prettyPrintJson with their titles, the way the agent loop reports them.
class CanonicalEmitter : public OutputHandler {
public:
    explicit CanonicalEmitter(StdioServer& server) : server_(server) {}

    void printProcessingStart(const std::string&, int, const std::string&) override {
        resetStream();
        status("Thinking");
    }
    void printStepHeader(int step, int limit) override {
        if (server_.dev()) status("Step " + std::to_string(step) + " of " + std::to_string(limit));
    }
    void printStateInfo(const std::string& m) override {
        if (server_.dev()) status(m);
    }
    void printThought(const std::string&) override {}
    void printGoal(const std::string&) override {}
    void printPlan(const json&, int) override {}
    void printToolUsage(const std::string& toolName) override {
        flushTokens();
        tool_ = toolName;
    }
    void printToolComplete() override {}
    void prettyPrintJson(const json& data, const std::string& title) override {
        if (title == "Tool Args") {
            json event = {{"type", "tool_call"}, {"tool", tool_}, {"args", data.is_object() ? data : json::object()}};
            const std::string narration = narrate(tool_, data);
            if (!narration.empty()) event["narration"] = narration;
            server_.write(event);
        } else if (title == "Tool Result") {
            json shown = data;
            const std::string dumped = dumps(data);
            if (dumped.size() > kMaxEventData) shown = {{"truncated", true}, {"text", clip(dumped, kMaxEventData)}};
            server_.write({{"type", "tool_result"}, {"tool", tool_}, {"data", shown}, {"preview", previewOf(data)}});
        }
    }
    void printError(const std::string& m) override { status(m); }
    void printWarning(const std::string& m) override { status(m); }
    void printInfo(const std::string& m) override { status(m); }
    void startProgress(const std::string&) override {}
    void stopProgress() override {}
    void printFinalAnswer(const std::string&, const UsageStats&) override {}
    void printCompletion(int, int) override {}
    void printStreamEnd() override {
        if (!inThink_) pending_ += held_;
        flushTokens();
        resetStream();
    }
    void printStreamToken(const std::string& token) override {
        // Reasoning stays out of the answer. A tag may be split across tokens,
        // so a possible tag prefix is held back.
        std::string text = held_ + token;
        held_.clear();
        std::string visible;
        size_t i = 0;
        while (i < text.size()) {
            const std::string marker = inThink_ ? "</think>" : "<think>";
            const size_t at = text.find(marker, i);
            if (at == std::string::npos) {
                size_t keep = 0;
                for (size_t k = std::min(marker.size() - 1, text.size() - i); k > 0; --k) {
                    if (text.compare(text.size() - k, k, marker, 0, k) == 0) {
                        keep = k;
                        break;
                    }
                }
                if (!inThink_) visible += text.substr(i, text.size() - i - keep);
                held_ = text.substr(text.size() - keep);
                break;
            }
            if (!inThink_) visible += text.substr(i, at - i);
            inThink_ = !inThink_;
            i = at + marker.size();
        }
        // A fast model writes hundreds of tokens a second. One event each fills
        // the pipe faster than a UI draws them, and a cancel then waits behind
        // that backlog, so tokens are sent in small batches.
        pending_ += visible;
        if (std::chrono::steady_clock::now() - lastTokens_ >= kTokenBatch) flushTokens();
    }

private:
    void status(const std::string& message) {
        flushTokens();
        if (!message.empty()) server_.write({{"type", "status"}, {"message", message}});
    }
    void flushTokens() {
        if (pending_.empty()) return;
        server_.write({{"type", "token"}, {"delta", pending_}});
        pending_.clear();
        lastTokens_ = std::chrono::steady_clock::now();
    }
    void resetStream() {
        inThink_ = false;
        held_.clear();
        pending_.clear();
    }

    StdioServer& server_;
    std::string tool_;
    bool inThink_ = false;
    std::string held_;
    std::string pending_;
    std::chrono::steady_clock::time_point lastTokens_{};
};

}  // namespace

std::string cloudProviderOf(const std::string& modelId) {
    const size_t dot = modelId.find('.');
    if (dot == std::string::npos || dot + 1 >= modelId.size()) return "";
    const std::string provider = modelId.substr(0, dot);
    return provider == "fireworks" || provider == "amd" ? provider : "";
}

void applyStdioModel(AgentConfig& cfg, const std::string& modelId, NativeToolCalls launchMode) {
    cfg.modelId = modelId;
    // Cloud models behind Lemonade speak native tool calls.
    cfg.nativeToolCalls = modelId.rfind("fireworks.", 0) == 0 ? NativeToolCalls::Always : launchMode;
    // A prompt-JSON model streams its tool calls as text, which is not an answer.
    cfg.streaming = cfg.useNativeToolCalls();
}

std::string workingFolderUtf8() {
    std::error_code ec;
    return fs::current_path(ec).u8string();
}

bool editIsInside(const std::string& target, const std::vector<std::string>& roots) {
    if (target.empty()) return false;
    std::error_code ec;
    const fs::path absolute = fs::absolute(fs::u8path(target), ec);
    if (ec) return false;
    const fs::path resolved = fs::weakly_canonical(absolute, ec);
    if (ec) return false;
    if (landsWhereCodeRuns(resolved)) return false;
    const std::string key = pathKey(resolved);
    for (const auto& r : roots) {
        const fs::path root = fs::u8path(r);
        if (tooBroad(root)) continue;
        const std::string rootKey = pathKey(root);
        if (key.size() <= rootKey.size() + 1 || key.compare(0, rootKey.size(), rootKey) != 0 ||
            key[rootKey.size()] != '/') {
            continue;
        }
        const std::vector<std::string> parts = splitList(key.substr(rootKey.size() + 1), '/');
        return std::none_of(parts.begin(), parts.end(), isHidden);
    }
    return false;
}

const std::vector<std::string>& StdioServer::eventTypes() {
    static const std::vector<std::string> types = {"status", "token", "tool_call", "tool_result",
                                                   "needs_confirmation", "final", "error"};
    return types;
}

const std::vector<std::string>& StdioServer::controlVerbs() {
    static const std::vector<std::string> verbs = {"tool_decision", "full_access", "accept_edits", "cancel",
                                                   "clear_history"};
    return verbs;
}

const std::vector<std::string>& StdioServer::decisions() {
    static const std::vector<std::string> answers = {"allow", "deny", "always", "timeout"};
    return answers;
}

StdioServer::StdioServer(GaiaAgent& agent, std::istream& in, std::ostream& out, StdioOptions options)
    : agent_(agent),
      in_(in),
      out_(out),
      options_(std::move(options)),
      grants_(std::make_shared<AllowedToolsStore>("")),
      fullAccess_(options_.fullAccess),
      acceptEdits_(options_.acceptEdits) {
    if (options_.editRoots.empty()) options_.editRoots.push_back(workingFolderUtf8());
}

StdioServer::~StdioServer() {
    if (reader_.joinable()) reader_.join();
}

std::ostream& StdioServer::log() { return options_.log ? *options_.log : std::cerr; }

void StdioServer::write(const json& event) {
    const std::string type = event.value("type", "");
    const auto& known = eventTypes();
    if (std::find(known.begin(), known.end(), type) == known.end()) {
        throw std::logic_error("gaiacpp --stdio tried to write a '" + type + "' event, which the TUI can't read");
    }
    if (hostGone_) return;
    bool failed = false;
    {
        std::lock_guard<std::mutex> lock(outMutex_);
        try {
            out_ << dumps(event) << "\n";
            out_.flush();
            failed = !out_;
        } catch (const std::ios_base::failure&) {
            failed = true;
        }
    }
    if (failed) hostLeft("a write to stdout failed");
}

void StdioServer::hostLeft(const std::string& why) {
    if (hostGone_.exchange(true)) return;
    {
        std::lock_guard<std::mutex> lock(logMutex_);
        log() << "[gaiacpp] the host left (" << why << "): stopping" << std::endl;
    }
    // A model call or a tool in flight can't be interrupted, and nobody is left to read its result.
    if (options_.exitWhenHostGone) std::_Exit(0);
    std::lock_guard<std::mutex> lock(mutex_);
    cancelInFlightLocked("the host left");
    queue_.clear();
    queue_.push_back({Item::Stop, ""});
    queueCv_.notify_all();
    doneCv_.notify_all();
}

// After stdin closes, notice a host that is gone without waiting for the next write to fail.
void StdioServer::watchHost() {
    if (!options_.hostAlive) return;
    for (;;) {
        {
            std::unique_lock<std::mutex> lock(mutex_);
            if (doneCv_.wait_for(lock, std::chrono::milliseconds(200), [this] { return finished_ || hostGone_; })) {
                return;
            }
        }
        if (!options_.hostAlive()) {
            hostLeft("stdin and stdout are both closed");
            return;
        }
    }
}

json StdioServer::statusEvent() {
    const AgentConfig cfg = agent_.config();
    const std::string provider = cloudProviderOf(cfg.modelId);
    const std::string base = LemonadeClient(LemonadeClientConfig{cfg.baseUrl}).baseUrl();
    json event = {{"type", "status"},
                  {"message", ""},
                  {"model_id", cfg.modelId},
                  {"model_display", cfg.modelId},
                  {"model_backend", provider.empty() ? "lemonade" : provider},
                  {"model_remote", !provider.empty()},
                  {"lemonade_reachable", false},
                  {"lemonade_base_url", base},
                  {"agent_version", options_.agentVersion},
                  {"gaia_version", options_.agentVersion}};
    // Its own short-lived client: a server that doesn't answer must not hold up the first line.
    try {
        HttpClientConfig hc;
        hc.baseUrl = base;
        hc.timeoutSec = kStatusProbeSeconds;
        hc.connectTimeoutSec = kStatusProbeSeconds;
        if (!cfg.apiKey.empty()) hc.defaultHeaders["Authorization"] = "Bearer " + cfg.apiKey;
        HttpClient http(hc);
        const bool lemonade = base.size() >= 7 && base.compare(base.size() - 7, 7, "/api/v1") == 0;
        const json health = json::parse(http.get(lemonade ? "/health" : "/models").body, nullptr, false);
        event["lemonade_reachable"] = true;
        if (health.is_object() && health.contains("version") && health["version"].is_string()) {
            event["lemonade_version"] = health["version"];
        }
    } catch (const std::exception& e) {
        std::lock_guard<std::mutex> lock(logMutex_);
        log() << "[gaiacpp] the model server at " << base << " did not answer: " << e.what() << std::endl;
    }
    return event;
}

void StdioServer::cancelInFlightLocked(const std::string& why) {
    if (!inFlight_) return;
    cancelled_ = true;
    agent_.requestCancel();
    decisionCv_.notify_all();
    std::lock_guard<std::mutex> lock(logMutex_);
    log() << "[gaiacpp] stopping the running turn: " << why << std::endl;
}

void StdioServer::applyControl(const json& message) {
    const std::string verb = controlString(message, kControlKey);
    std::unique_lock<std::mutex> lock(mutex_);
    auto note = [this](const std::string& line) {
        std::lock_guard<std::mutex> logLock(logMutex_);
        log() << "[gaiacpp] " << line << std::endl;
    };
    if (verb == "full_access") {
        fullAccess_ = controlFlag(message, "enabled");
        note(std::string("full access ") + (fullAccess_ ? "ENABLED: approval prompts are off" : "disabled"));
    } else if (verb == "accept_edits") {
        acceptEdits_ = controlFlag(message, "enabled");
        note(std::string("accept edits ") + (acceptEdits_ ? "ENABLED" : "disabled"));
    } else if (verb == "cancel") {
        // A query read but not started yet is the one the host means.
        auto queued = std::find_if(queue_.begin(), queue_.end(),
                                   [](const Item& i) { return i.kind == Item::Query && !i.cancelled; });
        if (inFlight_) {
            cancelInFlightLocked("the host asked to cancel");
        } else if (queued != queue_.end()) {
            queued->cancelled = true;
            note("cancelled a queued query before it started");
        } else {
            note("cancel with no turn running: nothing to stop");
        }
    } else if (verb == "clear_history") {
        queue_.push_back({Item::Clear, ""});
        queueCv_.notify_all();
    } else if (verb == "tool_decision") {
        std::string decision, id;
        try {
            decision = controlString(message, "decision");
            id = controlString(message, "confirm_id");
        } catch (const std::invalid_argument&) {
            // An unreadable answer is not consent: the open approval is denied.
            if (!pendingConfirmId_.empty()) {
                decision_ = "deny";
                decisionCv_.notify_all();
            }
            throw;
        }
        const auto& known = decisions();
        if (std::find(known.begin(), known.end(), decision) == known.end()) {
            note("unknown tool decision '" + decision + "', denying");
            decision = "deny";
        }
        if (pendingConfirmId_.empty()) {
            note("dropped a '" + decision + "' decision: nothing is waiting for one");
        } else if (!id.empty() && id != pendingConfirmId_) {
            note("dropped a '" + decision + "' decision for " + id + ": the open approval is " + pendingConfirmId_);
        } else {
            decision_ = decision;
            decisionCv_.notify_all();
        }
    } else {
        note("ignored unknown control verb '" + verb + "'");
    }
}

void StdioServer::pumpStdin() {
    for (std::string line; std::getline(in_, line);) {
        const size_t first = line.find_first_not_of(" \t\r");
        if (first == std::string::npos) continue;
        line = line.substr(first, line.find_last_not_of(" \t\r") - first + 1);
        if (line.front() == '{') {
            const json msg = json::parse(line, nullptr, false);
            if (msg.is_object() && msg.contains(kControlKey)) {
                try {
                    applyControl(msg);
                } catch (const std::exception& e) {
                    std::lock_guard<std::mutex> lock(logMutex_);
                    log() << "[gaiacpp] rejected a control message: " << e.what() << std::endl;
                }
                continue;
            }
            if (msg.is_object() && msg.contains(kQueryKey) && msg[kQueryKey].is_string()) {
                line = msg[kQueryKey].get<std::string>();
            }
        }
        std::lock_guard<std::mutex> lock(mutex_);
        queue_.push_back({Item::Query, line});
        queueCv_.notify_all();
    }
    // Queries already read still run while stdout has a reader (a one-line
    // pipe gets its answer), but nobody is left to answer an approval.
    if (options_.hostAlive && !options_.hostAlive()) hostLeft("stdin and stdout are both closed");
    {
        std::lock_guard<std::mutex> lock(mutex_);
        inputClosed_ = true;
        decisionCv_.notify_all();
        queue_.push_back({Item::Stop, ""});
        queueCv_.notify_all();
    }
    watchHost();
}

ToolConfirmResult StdioServer::confirm(const std::string& tool, const json& args) {
    const bool shell = tool == "bash_execute";
    // "Always" is only offered where the grant is narrower than the tool: a
    // write to one file must not approve every later write.
    const ToolInfo* info = agent_.tools().findTool(tool);
    const std::string key = info && info->grantScope ? tool + ":" + info->grantScope(args) : "";
    const bool edit = tool == "file_write" || tool == "file_edit";
    // Judged off the lock: it reads the filesystem and PATH.
    const bool acceptable = edit && editIsInside(argOf(args, "path"), options_.editRoots);
    auto note = [this](const std::string& line) {
        std::lock_guard<std::mutex> logLock(logMutex_);
        log() << "[gaiacpp] " << line << std::endl;
    };
    std::unique_lock<std::mutex> lock(mutex_);
    if (fullAccess_) return ToolConfirmResult::ALLOW_ONCE;
    if (acceptEdits_ && acceptable) return ToolConfirmResult::ALLOW_ONCE;
    if (cancelled_ || inputClosed_ || hostGone_) {
        note("denied " + tool + ": " + (cancelled_ ? "the turn was cancelled" : "nobody is left to approve it"));
        return ToolConfirmResult::DENY;
    }
    const std::string confirmId = randomHex(16);
    const std::string summary = shell                  ? "Run: " + argOf(args, "command")
                                : tool == "file_write" ? "Write " + argOf(args, "path")
                                : tool == "file_edit"  ? "Edit " + argOf(args, "path")
                                : tool == "web_fetch"  ? "Fetch " + argOf(args, "url")
                                                       : "Use " + tool + " " + clip(dumps(args), 300);
    json event = {{"type", "needs_confirmation"},
                  {"run_id", runId_},
                  {"action", tool},
                  {"summary", summary},
                  {"confirm_id", confirmId},
                  {"risk", toolRisk(tool)}};
    if (!key.empty()) event["always_scope"] = grantLabel(key);
    pendingConfirmId_ = confirmId;
    decision_.clear();
    lock.unlock();
    write(event);
    lock.lock();
    const bool answered = decisionCv_.wait_for(lock, kOrphanedConfirmTimeout, [this] {
        return !decision_.empty() || cancelled_ || inputClosed_ || hostGone_;
    });
    const std::string decision = decision_;
    pendingConfirmId_.clear();
    decision_.clear();
    if (decision == "allow") return ToolConfirmResult::ALLOW_ONCE;
    if (decision == "always") {
        if (!key.empty()) return ToolConfirmResult::ALWAYS_ALLOW;
        note("'always' was not offered for " + tool + ": allowed this one call");
        return ToolConfirmResult::ALLOW_ONCE;
    }
    if (decision.empty()) {
        note("denied " + tool + ": " +
             (!answered ? "no answer to the approval request in 15 minutes"
                        : cancelled_ ? "the turn was cancelled" : "the host left before answering"));
    } else if (decision == "timeout") {
        note("denied " + tool + ": the host's approval prompt timed out");
    }
    return ToolConfirmResult::DENY;
}

json StdioServer::switchableModels() {
    static const std::set<std::string> notChat = {"embeddings", "image", "reranker", "reranking", "transcription",
                                                  "realtime-transcription", "tts", "audio-generation",
                                                  "voice-design", "classification", "upscaling", "3d"};
    const json catalog = agent_.lemonade().listModels(true);
    if (!catalog.is_object() || !catalog.contains("data") || !catalog["data"].is_array()) {
        throw std::runtime_error("its model list had no 'data' array");
    }
    json ids = json::array();
    for (const auto& m : catalog["data"]) {
        if (!m.is_object() || !m.contains("id") || !m["id"].is_string()) continue;
        const std::string id = m["id"].get<std::string>();
        const bool cloud = !cloudProviderOf(id).empty() || m.value("recipe", "") == "cloud";
        if (!cloud && m.contains("downloaded") && m["downloaded"].is_boolean() && !m["downloaded"].get<bool>()) continue;
        bool chat = true;
        if (m.contains("labels") && m["labels"].is_array()) {
            bool saysChat = false, saysOther = false;
            for (const auto& l : m["labels"]) {
                if (!l.is_string()) continue;
                saysChat = saysChat || l.get<std::string>() == "chat";
                saysOther = saysOther || notChat.count(l.get<std::string>()) > 0;
            }
            chat = saysChat || !saysOther;
        }
        if (chat) ids.push_back(id);
    }
    return ids;
}

json StdioServer::runModelCommand(const std::string& arg) {
    const AgentConfig current = agent_.config();
    json models;
    std::string listError;
    try {
        models = switchableModels();
    } catch (const std::exception& e) {
        listError = "The model server at " + current.baseUrl + " did not list its models (" + e.what() + ").";
    }
    if (arg.empty()) {
        std::string text;
        if (!listError.empty()) {
            text = listError + "\n\n";
        } else {
            text = "**Models this server can run now:**\n";
            for (const auto& id : models) {
                text += "- `" + id.get<std::string>() + "`" + (id == current.modelId ? " ← current" : "") + "\n";
            }
            if (models.empty()) text += "- (none downloaded)\n";
            text += "\n";
        }
        return {{"type", "final"},
                {"answer", text + "Currently running: **" + current.modelId + "**. Switch with `/model <id>`."}};
    }
    if (!listError.empty()) {
        return {{"type", "error"}, {"detail", "Could not switch to " + arg + ". " + listError + " The model was not changed."}};
    }
    std::string target;
    for (const auto& id : models) {
        const std::string name = id.get<std::string>();
        // The server's own spelling: it lists a registered user.X as X, or the other way round.
        if (name == arg) target = name;
        else if (target.empty() && ("user." + name == arg || name == "user." + arg)) target = name;
    }
    if (target.empty()) {
        std::string names;
        for (const auto& id : models) names += (names.empty() ? "" : ", ") + id.get<std::string>();
        return {{"type", "error"},
                {"detail", "Unknown model '" + arg + "'. Models this server can run now: " +
                               (names.empty() ? std::string("(none downloaded)") : names) + "."}};
    }
    AgentConfig cfg = current;
    applyStdioModel(cfg, target, options_.nativeTools);
    agent_.setConfig(cfg);
    agent_.rebuildSystemPrompt();
    write(statusEvent());
    const std::string provider = cloudProviderOf(target);
    return {{"type", "final"},
            {"answer", "Switched to **" + target + "** " +
                           (provider.empty() ? "(runs on this PC)." : "(cloud: chat is sent to " + provider + ").")}};
}

json StdioServer::warmUp() {
    const AgentConfig cfg = agent_.config();
    if (!cloudProviderOf(cfg.modelId).empty()) return {{"type", "final"}, {"answer", "warm_up_skipped"}};
    try {
        write({{"type", "status"}, {"message", "Getting " + cfg.modelId + " ready"}});
        agent_.lemonade().ensureModelLoaded();
    } catch (const std::exception& e) {
        return {{"type", "error"},
                {"detail", std::string("Could not get the model ready ahead of time. Chat still works, but the "
                                       "first answer will take longer. ") + e.what()}};
    }
    return {{"type", "final"}, {"answer", "warmed_up"}};
}

// The store keeps no confidence score and no last-use time, so those keys are
// left out rather than sent as a number that would read as a measurement.
json StdioServer::memoryDump() {
    if (!options_.memory) {
        return {{"available", false}, {"reason", "Memory is off for this session (--no-memory)."}};
    }
    const std::string path = options_.memoryPath.empty() ? MemoryStore::defaultPath() : options_.memoryPath;
    std::error_code ec;
    if (!fs::exists(path, ec)) return {{"available", false}, {"reason", "Nothing has been remembered yet."}};
    MemoryStore store(path);
    const auto entries = store.recall("", "", 300, true);
    json items = json::array(), byCategory = json::object(), byContext = json::object();
    int sensitive = 0;
    std::set<std::string> entities;
    for (const auto& e : entries) {
        const std::string ctx = e.domain.empty() ? "global" : e.domain;
        json item = {{"id", e.id}, {"category", e.category}, {"content", e.content}, {"context", ctx},
                     {"sensitive", e.sensitive}, {"created_at", e.createdAt}, {"updated_at", e.updatedAt}};
        if (!e.entity.empty()) {
            item["entity"] = e.entity;
            entities.insert(e.entity);
        }
        items.push_back(std::move(item));
        byCategory[e.category] = byCategory.value(e.category, 0) + 1;
        byContext[ctx] = byContext.value(ctx, 0) + 1;
        if (e.sensitive) ++sensitive;
    }
    json contexts = json::array();
    for (auto& [ctx, n] : byContext.items()) contexts.push_back({{"context", ctx}, {"count", n}});
    const size_t total = store.count();
    return {{"available", true},
            {"stats", {{"total_knowledge", total}, {"by_category", byCategory}, {"by_context", byContext},
                       {"sensitive_count", sensitive}, {"entity_count", entities.size()}}},
            {"contexts", contexts}, {"shown", items.size()}, {"total", total}, {"items", items}};
}

json StdioServer::runTurn(const std::string& query) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        runId_ = "run-" + std::to_string(++turnCounter_);
    }
    const json result = agent_.processQuery(query);
    const std::string answer = result.value("result", "");
    const std::string status = result.value("status", "success");
    if (status == "cancelled") return {{"type", "final"}, {"answer", ""}};
    if (status == "error" && answer.rfind(kLlmFailure, 0) == 0) {
        return {{"type", "error"},
                {"detail", "The model server at " + agent_.config().baseUrl + " did not answer: " +
                               answer.substr(std::string(kLlmFailure).size())}};
    }
    json terminal = {{"type", "final"}, {"answer", answer}};
    json usage = json::object();
    if (result.value("steps_taken", 0) > 0) usage["steps"] = result["steps_taken"];
    if (result.contains("usage") && result["usage"].is_object()) {
        const json& u = result["usage"];
        if (u.value("prompt_tokens", 0) > 0) {
            usage["input_tokens"] = u["prompt_tokens"];
            usage["cached_tokens"] = u.value("cached_tokens", 0);
        }
        if (u.value("completion_tokens", 0) > 0) usage["tokens"] = u["completion_tokens"];
    }
    if (result.value("tool_calls", 0) > 0) usage["tools_used"] = result["tool_calls"];
    if (!usage.empty()) terminal["usage"] = usage;
    return terminal;
}

void StdioServer::runQuery(const std::string& query) {
    json terminal;
    try {
        if (cancelled_) {
            terminal = {{"type", "final"}, {"answer", ""}};
        } else if (query == kClearQuery) {
            agent_.clearHistory();
            terminal = {{"type", "final"}, {"answer", "conversation_cleared"}};
        } else if (query == kMemoryDumpQuery) {
            terminal = {{"type", "final"}, {"answer", dumps(memoryDump())}};
        } else if (query == kWarmUpQuery) {
            terminal = warmUp();
        } else {
            const size_t first = query.find_first_not_of(" \t\r\n");
            const size_t last = query.find_last_not_of(" \t\r\n");
            const std::string trimmed = first == std::string::npos ? "" : query.substr(first, last - first + 1);
            if (trimmed == "/model" || trimmed.rfind("/model ", 0) == 0) {
                const size_t at = trimmed.find_first_not_of(' ', 6);
                terminal = runModelCommand(at == std::string::npos ? "" : trimmed.substr(at));
            } else {
                terminal = runTurn(query);
            }
        }
    } catch (const std::exception& e) {
        terminal = {{"type", "error"}, {"detail", e.what()}};
    } catch (...) {
        // Anything else would end the process and the session with it.
        terminal = {{"type", "error"}, {"detail", "The turn failed with an unknown error"}};
    }
    {
        // Over before its terminal event goes out: a cancel from here on is for a later query.
        std::lock_guard<std::mutex> lock(mutex_);
        inFlight_ = false;
        cancelled_ = false;
    }
    write(terminal);
}

int StdioServer::run() {
    int code = 0;
    std::string failure;
    try {
        agent_.setOutputHandler(std::make_unique<CanonicalEmitter>(*this));
        agent_.setAllowedToolsStore(grants_);
        agent_.setToolConfirmCallback(
            [this](const std::string& tool, const json& args) { return confirm(tool, args); });
        // A cancel that lands before the agent resets its own flag still holds.
        agent_.setCancelCheck([this] { return cancelled_.load(); });
        if (options_.fullAccess) {
            std::lock_guard<std::mutex> lock(logMutex_);
            log() << "[gaiacpp] full access ENABLED at launch: approval prompts are off" << std::endl;
        }
        write(statusEvent());
        reader_ = std::thread([this] { pumpStdin(); });
        for (;;) {
            Item item;
            {
                std::unique_lock<std::mutex> lock(mutex_);
                queueCv_.wait(lock, [this] { return !queue_.empty(); });
                item = queue_.front();
                queue_.pop_front();
                // In the same step as leaving the queue, so a cancel always finds the query.
                if (item.kind == Item::Query) {
                    inFlight_ = true;
                    cancelled_ = item.cancelled;
                }
            }
            if (item.kind == Item::Stop) break;
            if (item.kind == Item::Clear) {
                agent_.clearHistory();
                continue;
            }
            runQuery(item.text);
        }
    } catch (const std::exception& e) {
        failure = e.what();
    } catch (...) {
        failure = "unknown error";
    }
    if (!failure.empty()) {
        code = 1;
        {
            std::lock_guard<std::mutex> lock(logMutex_);
            log() << "[gaiacpp] stopped on an internal error: " << failure << std::endl;
        }
        bool owed;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            owed = inFlight_;
            inFlight_ = false;
        }
        // The query in flight still gets its one terminal event.
        if (owed) {
            try {
                write({{"type", "error"}, {"detail", "gaiacpp stopped on an internal error: " + failure}});
            } catch (const std::exception& e) {
                std::lock_guard<std::mutex> lock(logMutex_);
                log() << "[gaiacpp] could not report it on stdout: " << e.what() << std::endl;
            }
        }
    }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        finished_ = true;
    }
    doneCv_.notify_all();
    // After an error or with the host gone, stdin may never close: the reader
    // is left to the destructor and the caller decides whether to wait for it.
    if (code == 0 && !hostGone_ && reader_.joinable()) reader_.join();
    return code;
}

}  // namespace gaia
