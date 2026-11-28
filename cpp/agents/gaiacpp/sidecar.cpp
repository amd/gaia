// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "sidecar.h"

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <deque>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <random>
#include <regex>
#include <set>
#include <thread>

#include <httplib.h>

#include <gaia/console.h>
#include <gaia/document_index.h>
#include <gaia/ignore.h>
#include <gaia/lemonade_client.h>
#include <gaia/memory_store.h>
#include <gaia/security.h>
#include <gaia/shell_guard.h>
#include <gaia/skill_library.h>

#include "bash_tools.h"
#include "gaia_agent.h"
#include "gaia_setup.h"
#include "wire_util.h"

#ifndef GAIACPP_VERSION
#define GAIACPP_VERSION "0.0.0-dev"
#endif

namespace fs = std::filesystem;

namespace gaia {

namespace {

using wire::dumps;
using wire::randomHex;

constexpr const char* kService = "gaia-agent-gaia";
// Every request field and route up to this version is implemented; the Agent
// UI turns features on by it, so it must never run ahead of the code.
constexpr const char* kApiVersion = "2.27";
// The one-shot /query fields of contract 2.32 (the gaia -p flags), advertised apart from the
// version because the contracts between 2.27 and 2.32 are not all implemented.
const std::vector<std::string> kOneShotFields = {"time_limit", "disallowed_tools", "no_web",
                                                 "no_skills", "skip_permissions", "full_access"};
// Shown by the Agent UI's context meter in place of a measurement.
constexpr const char* kNoContextMeasure = "Not available yet: gaiacpp doesn't report what fills its context.";
constexpr auto kKeepalive = std::chrono::seconds(10);
constexpr int kStreamWorkers = 64;
// Every open turn's stream holds a worker; the rest stay free for approvals and cancels.
constexpr size_t kMaxOpenTurns = 32;
constexpr auto kConfirmTimeout = std::chrono::minutes(10);
constexpr size_t kMaxSessions = 256;      // idle chats beyond this are forgotten, oldest first
constexpr size_t kMaxEarlyCancels = 256;
constexpr size_t kMaxBodyBytes = 64u * 1024 * 1024;

std::string frame(const json& ev) { return "data: " + dumps(ev) + "\n\n"; }

bool validSessionId(const std::string& id) {
    static const std::regex re("^[A-Za-z0-9_.:-]{1,128}$");
    return std::regex_match(id, re);
}

// ---- one turn ----

struct Run {
    std::string id;
    std::mutex mu;
    std::condition_variable cv;
    std::deque<std::string> frames;
    bool finished = false;
    std::atomic<bool> cancelled{false};
    std::map<std::string, std::string> decisions;  // confirm_id -> decision ("" while waiting)
    bool unattended = false;     // nobody can answer an approval on this turn
    std::string surface;         // the caller's front-end, "" when it named none
    std::string refusedAction;   // the gated tool an unattended turn stopped at
    bool skipPermissions = false;  // gated tools run unasked; a path outside still isn't granted
    int timeLimit = 0;             // seconds, 0 = none
    std::atomic<bool> timedOut{false};

    // Under the lock, so a waiter between its check and its sleep can't miss it.
    void cancel() {
        {
            std::lock_guard<std::mutex> lock(mu);
            cancelled = true;
        }
        cv.notify_all();
    }

    void push(const json& ev) {
        {
            std::lock_guard<std::mutex> lock(mu);
            frames.push_back(frame(ev));
        }
        cv.notify_all();
    }
    void finish(const json& terminal) {
        {
            std::lock_guard<std::mutex> lock(mu);
            frames.push_back(frame(terminal));
            finished = true;
        }
        cv.notify_all();
    }
};

// Agent console output -> the UI's canonical SSE events.
class SidecarConsole : public OutputHandler {
public:
    void attach(std::shared_ptr<Run> run, bool reasoning) {
        std::lock_guard<std::mutex> lock(mu_);
        run_ = std::move(run);
        reasoning_ = reasoning;
        tools_ = 0;
    }
    void detach() {
        std::lock_guard<std::mutex> lock(mu_);
        run_.reset();
    }
    int toolsUsed() {
        std::lock_guard<std::mutex> lock(mu_);
        return tools_;
    }

    void printProcessingStart(const std::string&, int, const std::string&) override {
        emit({{"type", "status"}, {"message", "Thinking"}});
    }
    void printStepHeader(int step, int limit) override {
        emit({{"type", "status"}, {"message", "Step " + std::to_string(step) + " of " + std::to_string(limit)}});
    }
    void printStateInfo(const std::string& m) override { emit({{"type", "status"}, {"message", m}}); }
    void printThought(const std::string& t) override {
        bool reasoning;
        {
            std::lock_guard<std::mutex> lock(mu_);
            reasoning = reasoning_;
        }
        if (reasoning && !t.empty()) emit({{"type", "reasoning"}, {"delta", t}});
    }
    void printGoal(const std::string&) override {}
    void printPlan(const json&, int) override {}
    void printToolUsage(const std::string& toolName) override {
        std::lock_guard<std::mutex> lock(mu_);
        tool_ = toolName;
        ++tools_;
    }
    void printToolComplete() override {}
    void prettyPrintJson(const json& data, const std::string& title) override {
        std::string tool;
        {
            std::lock_guard<std::mutex> lock(mu_);
            tool = tool_;
        }
        if (title == "Tool Args") {
            emit({{"type", "tool_call"}, {"tool", tool}, {"args", data.is_object() ? data : json::object()}});
        } else if (title == "Tool Result") {
            emit({{"type", "tool_result"}, {"tool", tool}, {"data", data}});
        }
    }
    void printError(const std::string& m) override { emit({{"type", "status"}, {"message", m}}); }
    void printWarning(const std::string& m) override { emit({{"type", "status"}, {"message", m}}); }
    void printInfo(const std::string& m) override { emit({{"type", "status"}, {"message", m}}); }
    void printToolProgress(const std::string& phase, const std::string& m) override {
        emit({{"type", "status"}, {"message", m}, {"phase", phase}});
    }
    void printReasoningProgress(size_t words) override {
        emit({{"type", "status"}, {"phase", "reasoning"}, {"words", words},
              {"message", "Reasoning - " + std::to_string(words) + " words so far"}});
    }
    void startProgress(const std::string&) override {}
    void stopProgress() override {}
    void printFinalAnswer(const std::string&, const UsageStats&) override {}
    void printCompletion(int, int) override {}
    void printStreamToken(const std::string& token) override { emit({{"type", "token"}, {"delta", token}}); }

    void emit(const json& ev) {
        std::shared_ptr<Run> run;
        {
            std::lock_guard<std::mutex> lock(mu_);
            run = run_;
        }
        if (run) run->push(ev);
    }

private:
    std::mutex mu_;
    std::shared_ptr<Run> run_;
    std::string tool_;
    bool reasoning_ = false;
    int tools_ = 0;
};

// ---- one conversation ----

struct Session {
    std::string id;
    bool temporary = false;  // no session_id: dropped after its one turn
    std::mutex agentMu;      // held to swap `agent` and by readers on other threads
    std::unique_ptr<GaiaAgent> agent;
    std::unique_lock<std::timed_mutex>* turnLock = nullptr;  // the sidecar-wide turn lock while this chat holds it
    SidecarConsole* console = nullptr;  // owned by agent
    std::atomic<bool> busy{false};
    std::atomic<bool> bypass{false};
    std::shared_ptr<Run> current;
    std::mutex currentMu;
    // Survive an agent rebuild:
    std::shared_ptr<AllowedToolsStore> grants = std::make_shared<AllowedToolsStore>("");  // this chat only
    std::shared_ptr<std::atomic<bool>> internetOff = std::make_shared<std::atomic<bool>>(false);
    std::shared_ptr<std::atomic<bool>> memoryOff = std::make_shared<std::atomic<bool>>(false);
    std::shared_ptr<std::atomic<bool>> memoryPrivate = std::make_shared<std::atomic<bool>>(false);
    std::set<std::string> roots;      // allowed folders and attached files
    std::set<std::string> builtWith;  // roots the agent was built with
    std::set<std::string> approved;   // outside paths the user let in at a card, or full access opened
    std::set<std::string> declined;   // paths the user said no to in declinedRun
    std::string declinedRun;
    std::set<std::string> indexed;    // attached documents already indexed
    std::string workingDirectory;
    // A one-shot run's gaia -p flags, applied when its agent is built.
    bool noWeb = false;
    bool noSkills = false;
    bool fullAccess = false;
    std::vector<std::string> disallowedTools;
    std::shared_ptr<ShellSession> shell;  // this chat's folder and exported variables
    std::string shellDir;                 // the working folder the shell was last put in
    std::chrono::steady_clock::time_point lastUsed = std::chrono::steady_clock::now();
};

std::string grantLabel(const std::string& key) {
    const std::string shell = "bash_execute:";
    if (key.rfind(shell, 0) == 0) {
        const std::string scope = key.substr(shell.size());
        if (scope.rfind("cmd:", 0) == 0) return "Run " + scope.substr(4) + " commands";
        if (scope.rfind("exact:", 0) == 0) return "Run `" + scope.substr(6) + "`";
    }
    return "Use " + key.substr(0, key.find(':'));
}

std::string toolCategory(const ToolInfo& t) {
    if (t.mcpServer) return "mcp";
    if (t.name.find('/') != std::string::npos) return "skill";
    static const std::map<std::string, std::string> known = {
        {"file_read", "file_io"}, {"file_write", "file_io"}, {"file_edit", "file_io"},
        {"file_search", "file_search"}, {"bash_execute", "shell"}, {"env_inspect", "shell"},
        {"web_fetch", "browser"}, {"index_document", "rag"}, {"query_documents", "rag"},
        {"list_indexed_documents", "rag"}, {"index_codebase", "code_index"}, {"search_code", "code_index"},
        {"analyze_image", "vlm"}, {"generate_image", "sd"}, {"transcribe_audio", "audio"},
        {"take_screenshot", "screenshot"}, {"list_skills", "skills"}, {"load_skill", "skills"}};
    auto it = known.find(t.name);
    return it == known.end() ? "agent" : it->second;
}

// The Agent UI's own name for the folder card, as the Python agent raises it.
constexpr const char* kPathAccessPrompt = "allow_path_access";

std::string pathAccessQuestion(const json& args) {
    const std::string path = args.value("path", "");
    const std::string kind = args.value("kind", "");
    if (kind == "folder") return "Allow GAIA to use the folder " + path + " and everything in it for this session?";
    if (kind == "file") return "Allow GAIA to use the file " + path + " for this session?";
    return "Allow GAIA to use " + path + " for this session?";
}

bool sameOrInside(const std::string& path, const std::string& folder) {
    if (path.rfind(folder, 0) != 0) return false;
    return path.size() == folder.size() || path[folder.size()] == '/' || path[folder.size()] == '\\';
}

std::string toolRisk(const std::string& tool) {
    if (tool == "bash_execute") return "execute";
    if (tool == "web_fetch" || tool == "take_screenshot") return "read";
    return "write";
}

Message messageFromJson(const json& m) {
    Message msg;
    const std::string role = m.value("role", "");
    if (role == "user") msg.role = MessageRole::USER;
    else if (role == "assistant") msg.role = MessageRole::ASSISTANT;
    else if (role == "tool") msg.role = MessageRole::TOOL;
    else if (role == "system") msg.role = MessageRole::SYSTEM;
    else throw std::invalid_argument("message role must be user, assistant, system or tool, got '" + role + "'");
    if (m.contains("content") && m["content"].is_string()) msg.content = m["content"].get<std::string>();
    if (m.contains("name") && m["name"].is_string()) msg.name = m["name"].get<std::string>();
    if (m.contains("tool_call_id") && m["tool_call_id"].is_string()) msg.toolCallId = m["tool_call_id"].get<std::string>();
    if (m.contains("tool_calls") && m["tool_calls"].is_array()) {
        for (const auto& tc : m["tool_calls"]) msg.toolCalls.push_back(ToolCall::fromJson(tc));
    }
    return msg;
}

// The model server a turn or a readiness check uses, and the key sent to it.
struct ModelServer {
    std::string baseUrl;
    std::string apiKey;
    ServerOrigin origin;
};

struct ServerDown : std::runtime_error {
    bool nothingAnswered;  // no connection at all, so starting the server is the remedy
    ServerDown(const std::string& m, bool nothing) : std::runtime_error(m), nothingAnswered(nothing) {}
};

// An attached file this PC has no program to read: the turn ends with how to fix that.
struct UnreadableAttachment : std::runtime_error {
    using std::runtime_error::runtime_error;
};

// What the Agent UI reads to offer its own Start button (contract: final.failure).
constexpr const char* kModelServerDown = "model_server_down";

Surface surfaceOf(const std::string& name) { return name == "agent_ui" ? Surface::App : Surface::Terminal; }

struct BadRequest : std::runtime_error {
    int status;
    BadRequest(int s, const std::string& m) : std::runtime_error(m), status(s) {}
};

class Sidecar {
public:
    explicit Sidecar(SidecarOptions opts) : opts_(std::move(opts)) {}

    int run() {
        if (!loopbackName(opts_.host)) {
            throw std::runtime_error("gaiacpp sidecar only listens on this PC: --host must be 127.0.0.1, localhost "
                                     "or ::1, not " + opts_.host + ".");
        }
        token_ = loadToken();
        dropKeychainSecret();
        lemonadeKey_ = getEnvVar("LEMONADE_API_KEY");
        // Shells the agent starts inherit this environment; keep the secrets out of it.
        for (const char* secret : {"GAIA_GAIA_SIDECAR_TOKEN", "GAIA_GAIA_SIDECAR_TOKEN_FILE", "LEMONADE_API_KEY",
                                   "GAIA_HOST_CUSTODY_SECRET", "GAIA_HOST_KEYCHAIN_SECRET_FILE"}) {
#ifdef _WIN32
            _putenv_s(secret, "");
#else
            unsetenv(secret);
#endif
        }
        routes();
        // Each open answer stream holds a worker for the whole turn.
        server_.new_task_queue = [] { return new httplib::ThreadPool(kStreamWorkers); };
        server_.set_payload_max_length(kMaxBodyBytes);
        // A field of the wrong type in a request body is the caller's mistake, not a crash.
        server_.set_exception_handler([](const httplib::Request&, httplib::Response& res, std::exception_ptr ep) {
            try {
                std::rethrow_exception(ep);
            } catch (const json::exception& e) {
                sendDetail(res, 422, std::string("Malformed request body: ") + e.what());
            } catch (const std::exception& e) {
                sendDetail(res, 500, e.what());
            } catch (...) {
                sendDetail(res, 500, "Unknown error");
            }
        });
        int port = opts_.port;
        if (port > 0) {
            if (!server_.bind_to_port(opts_.host, port)) {
                throw std::runtime_error("gaiacpp sidecar can't bind " + opts_.host + ":" + std::to_string(port) +
                                         " (in use?). The daemon picks the port; to run by hand, pass a free --port.");
            }
        } else {
            port = server_.bind_to_any_port(opts_.host);
            if (port <= 0) throw std::runtime_error("gaiacpp sidecar can't bind a port on " + opts_.host);
        }
        std::cerr << "gaiacpp " GAIACPP_VERSION " sidecar (api " << kApiVersion << ") on http://" << opts_.host
                  << ":" << port << std::endl;
        server_.listen_after_bind();
        return 0;
    }

private:
    // ---- startup ----

    static std::string loadToken() {
        const std::string file = getEnvVar("GAIA_GAIA_SIDECAR_TOKEN_FILE");
        if (!file.empty()) {
            std::ifstream in(file, std::ios::binary);
            if (!in) throw std::runtime_error("Can't read the sidecar token file " + file);
            std::string t((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
            while (!t.empty() && (t.back() == '\n' || t.back() == '\r' || t.back() == ' ')) t.pop_back();
            if (t.empty()) throw std::runtime_error("The sidecar token file " + file + " is empty");
            return t;
        }
        const std::string env = getEnvVar("GAIA_GAIA_SIDECAR_TOKEN");
        if (env.empty()) {
            throw std::runtime_error(
                "No sidecar token: the GAIA daemon passes GAIA_GAIA_SIDECAR_TOKEN_FILE. To run by hand, "
                "set GAIA_GAIA_SIDECAR_TOKEN.");
        }
        return env;
    }

    // The daemon hands over a keychain secret file the agent must remove; gaiacpp doesn't use it.
    static void dropKeychainSecret() {
        const std::string f = getEnvVar("GAIA_HOST_KEYCHAIN_SECRET_FILE");
        if (!f.empty()) {
            std::error_code ec;
            fs::remove(f, ec);
            if (ec) std::cerr << "gaiacpp: could not remove the keychain secret file " << f << ": " << ec.message() << std::endl;
        }
    }

    // ---- request checks ----

    static void sendJson(httplib::Response& res, int status, const json& body) {
        res.status = status;
        res.set_content(dumps(body), "application/json");
    }
    static void sendDetail(httplib::Response& res, int status, const std::string& detail) {
        sendJson(res, status, {{"detail", detail}});
    }
    static bool loopbackName(std::string h) {
        std::transform(h.begin(), h.end(), h.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        if (!h.empty() && h[0] == '[') h = h.substr(1, h.find(']') - 1);
        else h = h.substr(0, h.find(':'));
        return h == "127.0.0.1" || h == "localhost" || h == "::1";
    }
    bool admit(const httplib::Request& req, httplib::Response& res, bool needToken) {
        if (!loopbackName(req.get_header_value("Host"))) {
            sendDetail(res, 400, "Host must be a loopback name");
            return false;
        }
        const std::string origin = req.get_header_value("Origin");
        if (!origin.empty()) {
            const size_t s = origin.find("://");
            if (s == std::string::npos || !loopbackName(origin.substr(s + 3))) {
                sendDetail(res, 403, "Origin not allowed");
                return false;
            }
        }
        if (!needToken) return true;
        const std::string auth = req.get_header_value("Authorization");
        const std::string want = "Bearer " + token_;
        unsigned char diff = auth.size() == want.size() ? 0 : 1;
        for (size_t i = 0; i < std::min(auth.size(), want.size()); ++i) diff |= auth[i] ^ want[i];
        if (diff) {
            sendDetail(res, 401, "Missing or wrong sidecar token");
            return false;
        }
        return true;
    }

    // ---- sessions ----

    std::shared_ptr<Session> findSession(const std::string& id) {
        std::lock_guard<std::mutex> lock(sessionsMu_);
        auto it = sessions_.find(id);
        return it == sessions_.end() ? nullptr : it->second;
    }

    std::shared_ptr<Session> session(const std::string& id) {
        std::lock_guard<std::mutex> lock(sessionsMu_);
        auto it = sessions_.find(id);
        if (it != sessions_.end()) {
            it->second->lastUsed = std::chrono::steady_clock::now();
            return it->second;
        }
        while (sessions_.size() >= kMaxSessions) {
            auto oldest = sessions_.end();
            for (auto c = sessions_.begin(); c != sessions_.end(); ++c) {
                if (c->second->busy.load()) continue;
                if (oldest == sessions_.end() || c->second->lastUsed < oldest->second->lastUsed) oldest = c;
            }
            if (oldest == sessions_.end()) break;  // every chat is mid-turn
            sessions_.erase(oldest);
        }
        auto s = std::make_shared<Session>();
        s->id = id;
        sessions_[id] = s;
        return s;
    }

    void dropSession(const std::string& id) {
        std::lock_guard<std::mutex> lock(sessionsMu_);
        sessions_.erase(id);
    }

    // Read on every use: GAIA's own server takes a new port and key when it restarts.
    ModelServer modelServer() const {
        ModelServer m;
        GaiaServer own;
        // The Agent constructor honours these too.
        std::string named = getEnvVar("GAIA_BASE_URL");
        const char* namedBy = "GAIA_BASE_URL";
        if (named.empty()) {
            named = getEnvVar("GAIA_CPP_BASE_URL");
            namedBy = "GAIA_CPP_BASE_URL";
        }
        if (named.empty()) {
            own = resolveGaiaServer();
            m.baseUrl = own.baseUrl;
            m.origin = own.origin;
        } else {
            own = runningGaiaServer();  // the named address may still be GAIA's own server
            m.baseUrl = named;
            m.origin = {ServerSource::GaiaBaseUrlEnv, namedBy};
        }
        m.apiKey = keyForNamedServer(own, m.baseUrl, lemonadeKey_, m.origin);
        return m;
    }

    std::string defaultModel() const { return opts_.defaultModel.empty() ? resolveGaiaModel() : opts_.defaultModel; }

    // Build (or rebuild, when the chat's folders grew) the session's agent.
    // Only called while the session holds its busy flag.
    void ensureAgent(Session& s, const ModelServer& server) {
        if (s.agent && s.builtWith == s.roots) {
            AgentConfig cfg = s.agent->config();
            cfg.baseUrl = server.baseUrl;
            cfg.apiKey = server.apiKey;
            s.agent->setConfig(cfg);
            return;
        }
        AgentConfig cfg;
        cfg.baseUrl = server.baseUrl;
        cfg.modelId = defaultModel();
        cfg.contextSize = 32768;
        cfg.maxTokens = localMaxOutputTokens(cfg.contextSize);
        cfg.maxTokensFromWindow = true;  // GAIA_MAX_TOKENS still sets it
        cfg.replyLimitRemedy = GaiaAgent::kChatReplyLimitRemedy;
        cfg.temperature = 0.2;
        cfg.silentMode = true;
        cfg.streaming = true;
        cfg.maxHistoryMessages = 0;  // the UI fits history to the window
        cfg.responseMode = ResponseMode::Conversational;
        cfg.apiKey = server.apiKey;
        GaiaAgent::Options o;
        o.maxHelpers = opts_.maxHelpers;
        if (s.roots.empty()) {
            // Roots are UTF-8; the environment's narrow strings are the code page on Windows.
#ifdef _WIN32
            const wchar_t* profile = _wgetenv(L"USERPROFILE");
            const fs::path home = profile ? fs::path(profile) : fs::path();
#else
            const fs::path home = fs::u8path(getEnvVar("HOME"));
#endif
            o.workspaceRoots = {(home.empty() ? fs::current_path() : home).u8string()};
        } else {
            o.workspaceRoots.assign(s.roots.begin(), s.roots.end());
        }
        o.workspaceRoots.insert(o.workspaceRoots.end(), s.approved.begin(), s.approved.end());
        // As gaiacpp -p applies the same flags.
        o.webTools = !s.noWeb;
        o.webOff = s.noWeb;
        o.skills = !s.noSkills;
        o.fullAccess = s.fullAccess;
        o.internetOff = s.internetOff;
        o.memoryOff = s.memoryOff;
        o.memoryPrivate = s.memoryPrivate;
        if (!s.shell) s.shell = BashTools::newSession();
        o.shell = s.shell;
        Session* raw = &s;
        auto agent = std::make_unique<GaiaAgent>(cfg, o);
        agent->setPathAccessPrompt([this, raw](const std::string& path) { return askForPath(*raw, path); });
        auto console = std::make_unique<SidecarConsole>();
        SidecarConsole* consolePtr = console.get();
        agent->setOutputHandler(std::move(console));
        agent->setAllowedToolsStore(s.grants);
        agent->setToolConfirmCallback(
            [this, raw](const std::string& tool, const json& args) { return confirm(*raw, tool, args); });
        // Stop holds even when it lands before the run resets its own flag.
        agent->setCancelCheck([raw] {
            std::lock_guard<std::mutex> lock(raw->currentMu);
            return raw->current && raw->current->cancelled.load();
        });
        std::lock_guard<std::mutex> lock(s.agentMu);
        s.agent = std::move(agent);
        s.console = consolePtr;
        s.builtWith = s.roots;
        s.indexed.clear();  // a new agent has a new document index
    }

    // The folder card: true when this chat may now use `path`. Runs on the turn's thread.
    bool askForPath(Session& s, const std::string& path) {
        // Full access opens outside paths without asking, as it does on the Python agent.
        bool granted = s.bypass.load();
        if (!granted) {
            std::string runId;
            {
                std::lock_guard<std::mutex> lock(s.currentMu);
                if (s.current) runId = s.current->id;
            }
            if (runId != s.declinedRun) {
                s.declinedRun = runId;
                s.declined.clear();
            }
            // One card per place per turn: a no is not asked again for what is inside it.
            for (const auto& d : s.declined) {
                if (sameOrInside(path, d)) return false;
            }
            json args = {{"path", path}};
            std::error_code ec;
            if (fs::is_directory(fs::u8path(path), ec)) args["kind"] = "folder";
            else if (fs::is_regular_file(fs::u8path(path), ec)) args["kind"] = "file";
            granted = confirm(s, kPathAccessPrompt, args) == ToolConfirmResult::ALLOW_ONCE;
            if (!granted) s.declined.insert(path);
        }
        if (granted) s.approved.insert(path);
        return granted;
    }

    // Park the agent thread until the UI decides.
    ToolConfirmResult confirm(Session& s, const std::string& tool, const json& args) {
        if (s.bypass.load()) return ToolConfirmResult::ALLOW_ONCE;
        std::shared_ptr<Run> run;
        {
            std::lock_guard<std::mutex> lock(s.currentMu);
            run = s.current;
        }
        if (!run) return ToolConfirmResult::UNATTENDED;
        if (run->skipPermissions && tool != kPathAccessPrompt) return ToolConfirmResult::ALLOW_ONCE;
        if (run->unattended) {
            // Parking here would wait on a card nobody will ever see: end the turn instead.
            if (run->refusedAction.empty()) run->refusedAction = tool;
            run->cancel();
            return ToolConfirmResult::UNATTENDED;
        }
        const std::string confirmId = randomHex(16);
        {
            std::lock_guard<std::mutex> lock(run->mu);
            run->decisions[confirmId] = "";
        }
        const bool shell = tool == "bash_execute";
        const bool pathAccess = tool == kPathAccessPrompt;
        // "Always" only where the grant is narrower than the tool: one file write must not approve every later one.
        const ToolInfo* info = s.agent ? s.agent->tools().findTool(tool) : nullptr;
        // A line of several commands gets no "always": a label for it reads as covering less than it does.
        // One program's grant (`cmd:`) is the exception: `cd folder && one command`, where cd never asks.
        const std::string scope = info && info->grantScope ? info->grantScope(args) : "";
        const bool chained = shell && scope.rfind("cmd:", 0) != 0 &&
                             judgeShellCommand(args.value("command", ""), "", {}, nullptr).compound;
        const std::string key = info && info->grantScope && !chained ? tool + ":" + scope : "";
        json ev = {{"type", "needs_confirmation"},
                   {"run_id", run->id},
                   {"action", tool},
                   {"summary", shell ? "Run: " + args.value("command", "")
                                     : pathAccess ? pathAccessQuestion(args) : "Use " + tool},
                   {"confirm_id", confirmId},
                   {"args", args}};
        if (!pathAccess) {
            ev["risk"] = toolRisk(tool);
            ev["tool_label"] = tool;
        }
        if (!key.empty()) {
            ev["always_scope"] = key;
            ev["always_label"] = grantLabel(key);
        }
        if (!s.workingDirectory.empty()) ev["prompt_context"] = {{"working_directory", s.workingDirectory}};
        // The card's own risk line: this command was not checked, and can reach outside the chat's folders.
        const std::string unchecked = shell && s.agent ? s.agent->shellApprovalNote(args.value("command", "")) : "";
        if (!unchecked.empty()) ev["prompt_context"]["risk_note"] = unchecked;
        run->push(ev);
        // Other chats may run while this one waits for its user.
        if (s.agent) s.agent->releaseNpu();
        if (s.turnLock) s.turnLock->unlock();
        std::string d;
        bool answered = false;
        {
            std::unique_lock<std::mutex> lock(run->mu);
            answered = run->cv.wait_for(lock, kConfirmTimeout,
                                        [&] { return run->cancelled.load() || !run->decisions[confirmId].empty(); });
            d = run->decisions[confirmId];
            run->decisions.erase(confirmId);
        }
        if (!answered) {
            run->push({{"type", "status"},
                       {"message", pathAccess ? "No answer to the folder request in 10 minutes, so " +
                                                    args.value("path", "") + " stays closed."
                                              : "No answer to the approval request in 10 minutes, so " + tool +
                                                    " was not run."}});
        }
        if (s.turnLock) {
            // Stopped while other chats hold the sidecar: don't wait a whole turn to find out.
            ++approvalsReturning_;
            while (!s.turnLock->try_lock_for(std::chrono::milliseconds(100))) {
                if (run->cancelled) {
                    --approvalsReturning_;
                    return ToolConfirmResult::UNATTENDED;
                }
            }
            --approvalsReturning_;
            // Another chat may have moved the process; the shell keeps its own folder.
            std::error_code ec;
            if (!s.shellDir.empty()) fs::current_path(s.shellDir, ec);
        }
        if (d == "allow") return ToolConfirmResult::ALLOW_ONCE;
        if (d == "always") {
            if (!key.empty()) return ToolConfirmResult::ALWAYS_ALLOW;
            if (!pathAccess) {
                run->push({{"type", "status"},
                           {"message", "'Always' isn't offered for " + tool + ", so only this call was allowed."}});
            }
            return ToolConfirmResult::ALLOW_ONCE;
        }
        // Only an answered "deny" is the user's no; a timeout or a stop is nobody's.
        return d == "deny" ? ToolConfirmResult::DENY : ToolConfirmResult::UNATTENDED;
    }

    std::shared_ptr<Run> findRun(const std::string& id) {
        std::lock_guard<std::mutex> lock(runsMu_);
        auto it = runs_.find(id);
        return it == runs_.end() ? nullptr : it->second;
    }

    // A route under /v1/gaia/sessions/{id}/... : 400 bad id, 404 unknown session.
    std::shared_ptr<Session> sessionFor(const std::string& sid, httplib::Response& res) {
        if (!validSessionId(sid)) {
            sendDetail(res, 400, "Invalid session id");
            return nullptr;
        }
        auto s = findSessionOrRun(sid);
        if (!s) sendDetail(res, 404, "No session " + sid);
        return s;
    }

    // A turn sent without a session id is addressed by its run id while it runs.
    std::shared_ptr<Session> findSessionOrRun(const std::string& id) {
        if (auto s = findSession(id)) return s;
        return findRun(id) ? findSession("~" + id) : nullptr;
    }

    json grantsBody(Session& s, const std::string& sid) {
        json list = json::array();
        for (const auto& key : s.grants->allAllowed()) list.push_back({{"key", key}, {"label", grantLabel(key)}});
        return {{"session_id", sid}, {"grants", list}};
    }

    // ---- routes ----

    void routes() {
        server_.Get("/health", [this](const httplib::Request& req, httplib::Response& res) {
            if (!admit(req, res, false)) return;
            sendJson(res, 200, {{"status", "ok"}, {"service", kService}});
        });
        server_.Get("/version", [this](const httplib::Request& req, httplib::Response& res) {
            if (!admit(req, res, false)) return;
            sendJson(res, 200, {{"apiVersion", kApiVersion}, {"agentVersion", GAIACPP_VERSION},
                                {"capabilities", capabilities()}});
        });
        server_.Get("/v1/gaia/version", [this](const httplib::Request& req, httplib::Response& res) {
            if (!admit(req, res, false)) return;
            sendJson(res, 200, {{"apiVersion", kApiVersion}, {"version", GAIACPP_VERSION}, {"agent", "gaia"},
                                {"capabilities", capabilities()}});
        });
        server_.Get("/v1/gaia/init", [this](const httplib::Request& req, httplib::Response& res) {
            if (!admit(req, res, true)) return;
            std::string baseUrl, model, down;
            try {
                const ModelServer server = modelServer();
                LemonadeClient lc(server.baseUrl);
                lc.setApiKey(server.apiKey);
                baseUrl = lc.baseUrl();
                model = defaultModel();
                down = serverDownReason(lc, server.origin);
            } catch (const std::exception& e) {
                down = e.what();  // GAIA's settings can't be read
            }
            const bool up = down.empty();
            json body = {{"ready", up},
                         {"lemonade", {{"reachable", up}, {"base_url", baseUrl}, {"version", nullptr},
                                       {"min_version", nullptr}, {"compatible", up ? json(true) : json(nullptr)}}},
                         {"model", {{"id", model}, {"present", up}, {"loadable", up ? json(true) : json(nullptr)},
                                    {"ctx_size", nullptr}}},
                         {"hint", up ? json(nullptr) : json(down)}};
            sendJson(res, up ? 200 : 503, body);
        });
        server_.Get("/v1/gaia/memory", [this](const httplib::Request& req, httplib::Response& res) {
            if (!admit(req, res, true)) return;
            memoryDump(req, res);
        });
        // The Memory page's edits to gaiacpp's own store; a caller finds them by `store.capabilities`.
        server_.Post("/v1/gaia/memory", [this](const httplib::Request& req, httplib::Response& res) {
            if (!admit(req, res, true)) return;
            memoryWrite(res, [&](MemoryStore& store) -> json {
                const json body = memoryBody(req);
                const std::string id =
                    store.remember(stringField(body, "content"), body.value("category", "fact"),
                                   body.value("context", "global") == "global" ? "" : body.value("context", ""),
                                   stringField(body, "entity"), body.value("sensitive", false));
                return {{"status", "created"}, {"id", id}};
            });
        });
        server_.Put(R"(/v1/gaia/memory/([^/]+))", [this](const httplib::Request& req, httplib::Response& res) {
            if (!admit(req, res, true)) return;
            const std::string id = req.matches[1];
            memoryWrite(res, [&](MemoryStore& store) -> json {
                if (!store.update(id, stringField(memoryBody(req), "content"))) throw BadRequest(404, "No memory with id " + id);
                return {{"status", "updated"}, {"id", id}};
            });
        });
        server_.Delete(R"(/v1/gaia/memory/([^/]+))", [this](const httplib::Request& req, httplib::Response& res) {
            if (!admit(req, res, true)) return;
            const std::string id = req.matches[1];
            memoryWrite(res, [&](MemoryStore& store) -> json {
                if (!store.forget(id)) throw BadRequest(404, "No memory with id " + id);
                return {{"status", "deleted"}, {"id", id}};
            });
        });
        server_.Get("/v1/gaia/skills", [this](const httplib::Request& req, httplib::Response& res) {
            if (!admit(req, res, true)) return;
            skillsList(res);
        });
        server_.Post("/v1/gaia/query", [this](const httplib::Request& req, httplib::Response& res) {
            if (!admit(req, res, true)) return;
            try {
                query(req, res);
            } catch (const BadRequest& e) {
                sendDetail(res, e.status, e.what());
            }
        });
        server_.Post(R"(/v1/gaia/query/([^/]+)/cancel)", [this](const httplib::Request& req, httplib::Response& res) {
            if (!admit(req, res, true)) return;
            const std::string id = req.matches[1];
            if (!validSessionId(id)) return sendDetail(res, 400, "Invalid run id");
            auto run = findRun(id);
            if (run) {
                // The agent's cancel check reads this flag, so this alone stops the run.
                run->cancel();
            } else {
                std::lock_guard<std::mutex> lock(runsMu_);
                if (earlyCancels_.insert(id).second) earlyCancelOrder_.push_back(id);
                // Oldest first: the newest is the one whose query is about to arrive.
                while (earlyCancels_.size() > kMaxEarlyCancels && !earlyCancelOrder_.empty()) {
                    earlyCancels_.erase(earlyCancelOrder_.front());
                    earlyCancelOrder_.pop_front();
                }
            }
            sendJson(res, 200, {{"run_id", id}, {"cancelled", run != nullptr}});
        });
        server_.Post(R"(/v1/gaia/query/([^/]+)/tool_decision)", [this](const httplib::Request& req, httplib::Response& res) {
            if (!admit(req, res, true)) return;
            const std::string id = req.matches[1];
            auto run = findRun(id);
            if (!run) return sendDetail(res, 404, "No run " + id);
            json body = json::parse(req.body, nullptr, false);
            const std::string decision = body.is_object() ? body.value("decision", "") : "";
            const std::string confirmId = body.is_object() ? body.value("confirm_id", "") : "";
            if (decision != "allow" && decision != "deny" && decision != "always" && decision != "timeout") {
                return sendDetail(res, 422, "decision must be allow, deny, always or timeout");
            }
            {
                std::lock_guard<std::mutex> lock(run->mu);
                auto it = run->decisions.end();
                if (confirmId.empty()) {
                    // Without an id, the answer is for the one approval waiting.
                    for (auto d = run->decisions.begin(); d != run->decisions.end(); ++d) {
                        if (d->second.empty()) it = d;
                    }
                } else {
                    it = run->decisions.find(confirmId);
                }
                if (it == run->decisions.end() || !it->second.empty()) {
                    return sendDetail(res, 409, "No pending approval" + (confirmId.empty() ? "" : " " + confirmId));
                }
                // A late yes for an approval that already closed must not approve the next one.
                if (confirmId.empty() && decision != "deny" && decision != "timeout") {
                    return sendDetail(res, 422, "Approving needs the confirm_id of the approval card");
                }
                it->second = decision;
            }
            run->cv.notify_all();
            sendJson(res, 200, {{"run_id", id}, {"decision", decision}, {"delivered", true}});
        });
        server_.Post(R"(/v1/gaia/query/([^/]+)/respond)", [this](const httplib::Request& req, httplib::Response& res) {
            if (!admit(req, res, true)) return;
            sendDetail(res, 404, "This agent asks no questions mid-turn");
        });
        // 409 tells the UI to send the text as the next message instead.
        server_.Post(R"(/v1/gaia/query/([^/]+)/followup)", [this](const httplib::Request& req, httplib::Response& res) {
            if (!admit(req, res, true)) return;
            const std::string id = req.matches[1];
            if (!findRun(id)) return sendDetail(res, 404, "No run " + id + " in flight");
            sendDetail(res, 409, "gaiacpp takes messages between turns, not during one");
        });
        server_.Post(R"(/v1/gaia/sessions/([^/]+)/bypass)", [this](const httplib::Request& req, httplib::Response& res) {
            if (!admit(req, res, true)) return;
            const std::string sid = req.matches[1];
            if (!validSessionId(sid)) return sendDetail(res, 400, "Invalid session id");
            json body = json::parse(req.body, nullptr, false);
            if (!body.is_object() || !body.contains("enabled") || !body["enabled"].is_boolean()) {
                return sendDetail(res, 422, "enabled must be true or false");
            }
            const bool on = body["enabled"].get<bool>();
            auto s = findSessionOrRun(sid);
            // The UI sends it again once the session's first turn has started.
            if (!s) return sendDetail(res, 404, "No session " + sid);
            s->bypass = on;
            sendJson(res, 200, {{"session_id", sid}, {"enabled", on}});
        });
        server_.Post(R"(/v1/gaia/sessions/([^/]+)/internet)", [this](const httplib::Request& req, httplib::Response& res) {
            if (!admit(req, res, true)) return;
            const std::string sid = req.matches[1];
            auto s = sessionFor(sid, res);
            if (!s) return;
            json body = json::parse(req.body, nullptr, false);
            if (!body.is_object() || !body.contains("internet_off") || !body["internet_off"].is_boolean()) {
                return sendDetail(res, 422, "internet_off must be true or false");
            }
            s->internetOff->store(body["internet_off"].get<bool>());
            sendJson(res, 200, {{"session_id", sid}, {"internet_off", s->internetOff->load()}});
        });
        server_.Get(R"(/v1/gaia/sessions/([^/]+)/grants)", [this](const httplib::Request& req, httplib::Response& res) {
            if (!admit(req, res, true)) return;
            const std::string sid = req.matches[1];
            auto s = sessionFor(sid, res);
            if (!s) return;
            sendJson(res, 200, grantsBody(*s, sid));
        });
        server_.Delete(R"(/v1/gaia/sessions/([^/]+)/grants)", [this](const httplib::Request& req, httplib::Response& res) {
            if (!admit(req, res, true)) return;
            const std::string sid = req.matches[1];
            auto s = sessionFor(sid, res);
            if (!s) return;
            if (req.has_param("key")) {
                const std::string key = req.get_param_value("key");
                if (!s->grants->isAlwaysAllowed(key)) return sendDetail(res, 404, "No grant " + key);
                s->grants->removeAlwaysAllowed(key);
            } else {
                s->grants->clearAll();
            }
            sendJson(res, 200, grantsBody(*s, sid));
        });
        server_.Get(R"(/v1/gaia/sessions/([^/]+)/tools)", [this](const httplib::Request& req, httplib::Response& res) {
            if (!admit(req, res, true)) return;
            const std::string sid = req.matches[1];
            auto s = sessionFor(sid, res);
            if (!s) return;
            json tools = json::array();
            {
                std::lock_guard<std::mutex> lock(s->agentMu);
                if (!s->agent) return sendDetail(res, 404, "Session " + sid + " has no agent yet");
                for (const auto& [name, info] : s->agent->tools().allTools()) {
                    tools.push_back({{"name", name}, {"description", info.description}, {"category", toolCategory(info)}});
                }
            }
            sendJson(res, 200, {{"session_id", sid}, {"tools", tools}});
        });
    }

    // The turn's last word when it stopped at a gated tool nobody could approve.
    static json unattendedRefusal(const Run& run) {
        const bool path = run.refusedAction == kPathAccessPrompt;
        std::string answer;
        if (run.surface == "cli") {
            // Worded as the Python agent words it: gaia -p passes these flags to either.
            const std::string remedy =
                path ? "Re-run with --full-access to grant paths outside the current folder for this run (it "
                       "also turns the shell guardrails off)."
                     : "Re-run with --dangerously-skip-permissions to let this run use gated tools without "
                       "asking, or --full-access to also turn the shell guardrails off.";
            answer = "I stopped before running '" + run.refusedAction +
                     "' because it needs your approval, and gaia -p has nobody to ask. " + remedy;
        } else {
            answer = "I stopped before running '" + run.refusedAction +
                     "' because it needs your explicit approval, and this request has nobody to ask: it "
                     "named no session, or said it can't answer questions. Send it with a session_id from a "
                     "caller that answers needs_confirmation, or perform the action directly.";
        }
        return {{"type", "final"}, {"failed", true}, {"answer", answer}, {"failure", answer}};
    }

    // The final of a turn its time limit stopped: not a failure, but not the whole answer either.
    static json timeLimitFinal(const Run& run) {
        return {{"type", "final"},
                {"answer", "Stopped at the " + std::to_string(run.timeLimit) +
                               "-second time limit (--time-limit) before the turn finished."},
                {"time_limit_reached", true},
                {"context_error", kNoContextMeasure}};
    }

    static json capabilities() { return {{"one_shot_fields", kOneShotFields}}; }

    // A one-shot field: absent or null reads as false; anything but a boolean is the caller's mistake.
    static bool flagField(const json& body, const char* key) {
        if (!body.contains(key) || body[key].is_null()) return false;
        if (!body[key].is_boolean()) throw BadRequest(422, std::string(key) + " must be true or false");
        return body[key].get<bool>();
    }

    // Withhold the tools a one-shot's disallowed_tools globs match, as the Python agent's deny_tools does.
    static std::vector<std::string> withholdTools(GaiaAgent& agent, const std::vector<std::string>& patterns) {
        std::set<std::string> names;
        for (const auto& [name, info] : agent.tools().allTools()) names.insert(name);
        std::vector<std::string> unmatched, withheld;
        for (const auto& p : patterns) {
            bool any = false;
            for (const auto& n : names) {
                if (!globMatch(p, n)) continue;
                any = true;
                if (std::find(withheld.begin(), withheld.end(), n) == withheld.end()) withheld.push_back(n);
            }
            if (!any) unmatched.push_back(p);
        }
        if (!unmatched.empty()) {
            std::string list, all;
            for (const auto& u : unmatched) list += (list.empty() ? "" : ", ") + u;
            for (const auto& n : names) all += (all.empty() ? "" : ", ") + n;
            throw BadRequest(400, "disallowed_tools: No tool matches " + list + ". This agent's tools: " + all + ".");
        }
        for (const auto& n : withheld) agent.toolRegistry().removeTool(n);
        return withheld;
    }

    // What a caller may do with this store, and whose it is: the Python agent keeps another.
    static json memoryStoreInfo() {
        return {{"agent", "gaiacpp"},
                {"path", MemoryStore::defaultPath()},
                {"capabilities", json::array({"search", "create", "edit", "delete"})}};
    }

    static json memoryBody(const httplib::Request& req) {
        json body = json::parse(req.body, nullptr, false);
        if (!body.is_object()) throw BadRequest(422, "Body must be a JSON object");
        return body;
    }

    static std::string stringField(const json& body, const char* key) {
        if (!body.contains(key) || body[key].is_null()) return "";
        if (!body[key].is_string()) throw BadRequest(422, std::string(key) + " must be a string");
        return body[key].get<std::string>();
    }

    // One change to the store: the store's own refusal (empty or credential-shaped text, unknown category) is a 422.
    void memoryWrite(httplib::Response& res, const std::function<json(MemoryStore&)>& change) {
        try {
            MemoryStore store(MemoryStore::defaultPath());
            sendJson(res, 200, change(store));
        } catch (const BadRequest& e) {
            sendDetail(res, e.status, e.what());
        } catch (const std::invalid_argument& e) {
            sendDetail(res, 422, e.what());
        } catch (const std::exception& e) {
            sendDetail(res, 500, std::string("gaiacpp could not change its memory: ") + e.what());
        }
    }

    void memoryDump(const httplib::Request& req, httplib::Response& res) {
        try {
            const std::string path = MemoryStore::defaultPath();
            std::error_code ec;
            if (!fs::exists(path, ec)) {
                return sendJson(res, 200, {{"available", false}, {"reason", "Nothing has been remembered yet."},
                                           {"store", memoryStoreInfo()}});
            }
            MemoryStore store(path);
            int limit = 300;
            if (req.has_param("limit")) {
                try {
                    limit = std::stoi(req.get_param_value("limit"));
                } catch (const std::exception&) {
                    return sendDetail(res, 422, "limit must be a whole number from 1 to 1000");
                }
                if (limit < 1 || limit > 1000) return sendDetail(res, 422, "limit must be a whole number from 1 to 1000");
            }
            // `q` is a keyword search; without it the newest entries come first.
            const auto entries =
                store.recall(req.get_param_value("q"), req.get_param_value("category"), limit, true);
            json items = json::array(), byCategory = json::object(), byContext = json::object();
            int sensitive = 0;
            for (const auto& e : entries) {
                items.push_back({{"id", e.id}, {"category", e.category}, {"content", e.content},
                                 {"entity", e.entity.empty() ? json(nullptr) : json(e.entity)},
                                 {"context", e.domain.empty() ? "global" : e.domain}, {"confidence", nullptr},
                                 {"sensitive", e.sensitive}, {"created_at", e.createdAt},
                                 {"updated_at", e.updatedAt}, {"last_used", nullptr}});
                byCategory[e.category] = byCategory.value(e.category, 0) + 1;
                const std::string ctx = e.domain.empty() ? "global" : e.domain;
                byContext[ctx] = byContext.value(ctx, 0) + 1;
                if (e.sensitive) ++sensitive;
            }
            json contexts = json::array();
            for (auto& [ctx, n] : byContext.items()) contexts.push_back({{"context", ctx}, {"count", n}});
            const size_t total = store.count();
            sendJson(res, 200, {{"available", true},
                                {"stats", {{"total_knowledge", total}, {"by_category", byCategory},
                                           {"by_context", byContext}, {"sensitive_count", sensitive},
                                           {"entity_count", nullptr}, {"avg_confidence", nullptr}}},
                                {"contexts", contexts}, {"shown", items.size()}, {"total", total},
                                {"items", items}, {"store", memoryStoreInfo()}});
        } catch (const std::exception& e) {
            sendDetail(res, 500, std::string("Failed to build the memory dump: ") + e.what());
        }
    }

    void skillsList(httplib::Response& res) {
        try {
            SkillLibrary lib;
            json skills = json::array();
            for (const auto& [name, s] : lib.skills()) {
                skills.push_back({{"name", s.name}, {"description", s.description},
                                  {"version", s.version ? json(*s.version) : json(nullptr)},
                                  {"tier", s.securityTier()}, {"tools", s.toolNames()}, {"path", s.root},
                                  {"always_on", false}, {"source", s.root.empty() ? json(nullptr) : json(s.root)}});
            }
            json invalid = json::object();
            for (const auto& f : lib.failures()) {
                const size_t sep = f.find(": ");
                invalid[f.substr(0, sep)] = sep == std::string::npos ? "" : f.substr(sep + 2);
            }
            sendJson(res, 200, {{"skills", skills}, {"invalid", invalid}});
        } catch (const std::exception& e) {
            sendDetail(res, 500, std::string("Failed to list skills: ") + e.what());
        }
    }

    static std::vector<std::string> absolutePaths(const json& body, const char* field) {
        std::vector<std::string> out;
        if (!body.contains(field) || body[field].is_null()) return out;
        if (!body[field].is_array()) throw BadRequest(422, std::string(field) + " must be a list of paths");
        for (const auto& p : body[field]) {
            if (!p.is_string() || p.get<std::string>().empty() || !fs::path(p.get<std::string>()).is_absolute()) {
                throw BadRequest(422, std::string(field) + " must be absolute paths; got " + dumps(p));
            }
            if (std::find(out.begin(), out.end(), p.get<std::string>()) == out.end()) out.push_back(p.get<std::string>());
        }
        return out;
    }

    void query(const httplib::Request& req, httplib::Response& res) {
        json body = json::parse(req.body, nullptr, false);
        if (!body.is_object()) throw BadRequest(422, "Body must be a JSON object");
        const std::string text = body.value("query", "");
        const std::string runId = body.value("run_id", "");
        if (text.empty()) throw BadRequest(422, "query must be a non-empty string");
        if (runId.empty()) throw BadRequest(422, "run_id is required");
        if (!validSessionId(runId)) throw BadRequest(422, "run_id may hold letters, digits and _ . : - only");
        if (!body.contains("context") || !body["context"].is_array()) throw BadRequest(422, "context must be a list");
        const bool hasTurns = body.contains("turns") && !body["turns"].is_null();
        if (hasTurns && !body["turns"].is_array()) throw BadRequest(422, "turns must be a list of turns");
        if (hasTurns && !body["context"].empty()) throw BadRequest(422, "send turns or context, not both");
        const std::string provider = body.value("provider", "");
        if (!provider.empty() && provider != "lemonade" && provider != "local") {
            throw BadRequest(400, "gaiacpp runs models through Lemonade only; provider '" + provider + "' isn't supported");
        }
        const std::string sid = body.contains("session_id") && body["session_id"].is_string()
                                    ? body["session_id"].get<std::string>() : "";
        if (!sid.empty() && !validSessionId(sid)) throw BadRequest(400, "Invalid session_id");
        const auto documents = absolutePaths(body, "documents");
        const auto allowed = absolutePaths(body, "allowed_paths");
        std::string workdir;
        if (body.contains("working_directory") && !body["working_directory"].is_null()) {
            if (!body["working_directory"].is_string() || !fs::path(body["working_directory"].get<std::string>()).is_absolute()) {
                throw BadRequest(422, "working_directory must be an absolute path");
            }
            workdir = body["working_directory"].get<std::string>();
        }
        const std::string memory = body.contains("memory") && body["memory"].is_string() ? body["memory"].get<std::string>() : "";
        if (!memory.empty() && memory != "on" && memory != "private" && memory != "off") {
            throw BadRequest(422, "memory must be on, private or off");
        }
        const bool undoRequested = body.contains("changes_turn_id") && body["changes_turn_id"].is_string();
        if (undoRequested && sid.empty()) throw BadRequest(422, "changes_turn_id needs a session_id");
        const bool reasoning = body.value("reasoning", false);
        const std::string model = body.value("model", "");
        const int maxSteps = body.value("max_steps", 0);

        // The gaia -p flags: one run only, so a chat can never inherit them.
        int timeLimit = 0;
        if (body.contains("time_limit") && !body["time_limit"].is_null()) {
            const json& t = body["time_limit"];
            if (!t.is_number_integer() || t.get<long long>() < 1 || t.get<long long>() > 86400LL * 365) {
                throw BadRequest(422, "time_limit must be a whole number of seconds, 1 or more");
            }
            timeLimit = static_cast<int>(t.get<long long>());
        }
        std::vector<std::string> disallowed;
        if (body.contains("disallowed_tools") && !body["disallowed_tools"].is_null()) {
            if (!body["disallowed_tools"].is_array()) throw BadRequest(422, "disallowed_tools must be a list of tool globs");
            for (const auto& p : body["disallowed_tools"]) {
                std::string g = p.is_string() ? p.get<std::string>() : "";
                const size_t a = g.find_first_not_of(" \t\r\n");
                g = a == std::string::npos ? "" : g.substr(a, g.find_last_not_of(" \t\r\n") - a + 1);
                if (g.empty()) throw BadRequest(422, "disallowed_tools entries must be non-empty tool globs.");
                if (std::find(disallowed.begin(), disallowed.end(), g) == disallowed.end()) disallowed.push_back(g);
            }
        }
        const bool noWeb = flagField(body, "no_web");
        const bool noSkills = flagField(body, "no_skills");
        const bool skipPermissions = flagField(body, "skip_permissions");
        const bool fullAccess = flagField(body, "full_access");
        {
            std::vector<std::string> sent;
            if (!disallowed.empty()) sent.push_back("disallowed_tools");
            if (fullAccess) sent.push_back("full_access");
            if (noSkills) sent.push_back("no_skills");
            if (noWeb) sent.push_back("no_web");
            if (skipPermissions) sent.push_back("skip_permissions");
            if (timeLimit) sent.push_back("time_limit");
            if (!sent.empty() && !sid.empty()) {
                std::string list;
                for (const auto& f : sent) list += (list.empty() ? "" : ", ") + f;
                throw BadRequest(422, list + " apply to one run only, so they need a one-shot request: drop "
                                             "session_id. A chat's full access is /sessions/{id}/bypass.");
            }
        }

        // The UI sends the conversation every turn.
        std::vector<Message> history;
        try {
            if (hasTurns) {
                for (const auto& turn : body["turns"]) {
                    if (!turn.is_array()) throw std::invalid_argument("each turn must be a list of messages");
                    for (const auto& m : turn) history.push_back(messageFromJson(m));
                }
            } else {
                for (const auto& m : body["context"]) {
                    Message msg = messageFromJson(m);
                    if (msg.role == MessageRole::USER || msg.role == MessageRole::ASSISTANT) {
                        if (!msg.content.empty()) history.push_back(std::move(msg));
                    }
                }
            }
        } catch (const std::exception& e) {
            throw BadRequest(422, e.what());
        }

        auto run = std::make_shared<Run>();
        run->id = runId;
        // As on the Python agent: a card needs a chat to hold the answer and a caller who can give one.
        run->unattended = sid.empty() || (body.contains("can_answer_questions") &&
                                          body["can_answer_questions"].is_boolean() &&
                                          !body["can_answer_questions"].get<bool>());
        if (body.contains("surface") && body["surface"].is_string()) run->surface = body["surface"].get<std::string>();
        run->skipPermissions = skipPermissions || fullAccess;
        run->timeLimit = timeLimit;
        {
            std::lock_guard<std::mutex> lock(runsMu_);
            if (runs_.count(runId)) throw BadRequest(409, "Duplicate run_id " + runId);
            if (runs_.size() >= kMaxOpenTurns) {
                throw BadRequest(503, "gaiacpp already has " + std::to_string(kMaxOpenTurns) +
                                          " turns running or waiting. Send this again when one of them finishes.");
            }
            runs_[runId] = run;
            if (earlyCancels_.erase(runId)) run->cancelled = true;
        }
        // '~' is not a session id character, so a one-turn key can never name a real chat.
        auto s = session(sid.empty() ? "~" + runId : sid);
        bool expected = false;
        if (!s->busy.compare_exchange_strong(expected, true)) {
            {
                std::lock_guard<std::mutex> lock(runsMu_);
                runs_.erase(runId);
            }
            throw BadRequest(409, "This session is already running a turn");
        }
        s->temporary = sid.empty();
        if (s->temporary) {
            s->noWeb = noWeb;
            s->noSkills = noSkills;
            s->fullAccess = fullAccess;
            s->disallowedTools = disallowed;
            s->bypass = fullAccess;
            if (skipPermissions || fullAccess) {
                std::cerr << "turn " << runId << ": "
                          << (fullAccess ? "full access ENABLED" : "gated tools pre-approved")
                          << " for this one-shot run (shell gates " << (fullAccess ? "off" : "on") << ")"
                          << std::endl;
            }
        }
        // Folder scope only grows within a chat; an attached file is granted as that file.
        for (const auto& p : allowed) s->roots.insert(p);
        for (const auto& d : documents) s->roots.insert(d);
        if (!workdir.empty()) s->workingDirectory = workdir;
        if (!memory.empty()) {
            s->memoryOff->store(memory == "off");
            s->memoryPrivate->store(memory == "private");
        }
        if (body.contains("internet_off") && body["internet_off"].is_boolean()) {
            s->internetOff->store(body["internet_off"].get<bool>());
        }
        {
            json folders = json::array();
            for (const auto& r : s->roots) folders.push_back(r);
            std::cerr << "turn " << runId << " session " << s->id << " folders=" << dumps(folders)
                      << " cwd=" << dumps(s->workingDirectory) << " memory_off=" << s->memoryOff->load()
                      << " internet_off=" << s->internetOff->load() << std::endl;
        }

        try {
        std::thread([this, s, run, history = std::move(history), text, model, maxSteps, documents, reasoning,
                     undoRequested, hasTurns]() mutable {
            // One chat works at a time: the working folder is process-wide. A
            // chat waiting on its user lets go of it (see confirm()).
            std::unique_lock<std::timed_mutex> turnLock(turnsMu_, std::defer_lock);
            // A turn stopped while it waits behind another chat's turn gives its place up.
            for (;;) {
                // A chat whose user has just answered an approval goes ahead of turns not yet started.
                if (approvalsReturning_.load() > 0) {
                    std::this_thread::sleep_for(std::chrono::milliseconds(20));
                } else if (turnLock.try_lock_for(std::chrono::milliseconds(100))) {
                    break;
                }
                if (run->cancelled) break;
            }
            const bool started = turnLock.owns_lock();
            if (started) s->turnLock = &turnLock;
            const auto t0 = std::chrono::steady_clock::now();
            json terminal;
            std::vector<std::string> withheld;
            if (!started) {
                terminal = {{"type", "final"}, {"answer", ""}};
            } else try {
                {
                    // A new agent reads project skills from where the process stands: the last chat's folder.
                    std::error_code moveEc;
                    const std::string folder = chatFolder(*s);
                    if (!folder.empty()) fs::current_path(folder, moveEc);
                }
                const ModelServer server = modelServer();
                {
                    LemonadeClient probe(server.baseUrl);
                    probe.setApiKey(server.apiKey);
                    bool nothing = false;
                    const std::string down = serverDownReason(probe, server.origin, surfaceOf(run->surface), &nothing);
                    if (!down.empty()) throw ServerDown(down, nothing);
                }
                ensureAgent(*s, server);
                if (s->temporary) {
                    if (s->noWeb) withheld.push_back("web_fetch");
                    if (s->noSkills && !SkillLibrary().skills().empty()) {
                        withheld.insert(withheld.end(), {"list_skills", "load_skill"});
                    }
                    if (!s->disallowedTools.empty()) {
                        for (const auto& n : withholdTools(*s->agent, s->disallowedTools)) {
                            if (std::find(withheld.begin(), withheld.end(), n) == withheld.end()) withheld.push_back(n);
                        }
                    }
                }
                {
                    std::lock_guard<std::mutex> lock(s->currentMu);
                    s->current = run;
                }
                s->console->attach(run, reasoning);
                if (run->cancelled) throw std::runtime_error("cancelled");
                const std::string folderProblem = enterWorkingDirectory(*s);
                if (!folderProblem.empty()) throw std::runtime_error(folderProblem);
                if (undoRequested) {
                    s->console->emit({{"type", "status"},
                                      {"message", "Undo may not work for this turn: gaiacpp doesn't record file changes yet."}});
                }
                const std::string unreadable = indexDocuments(*s, documents);
                if (!unreadable.empty()) throw UnreadableAttachment(unreadable);
                AgentConfig cfg = s->agent->config();
                if (!model.empty()) cfg.modelId = model;
                // Cloud models behind Lemonade speak native tool calls.
                cfg.nativeToolCalls =
                    cfg.modelId.rfind("fireworks.", 0) == 0 ? NativeToolCalls::Always : NativeToolCalls::Auto;
                s->agent->setConfig(cfg);
                s->agent->rebuildSystemPrompt();  // memory may have been switched
                s->agent->setHistory(history);
                const size_t before = s->agent->history().size();
                if (run->cancelled) throw std::runtime_error("cancelled");
                // gaia -p --time-limit: a stop at the limit, which also ends a model call or command in flight.
                std::mutex limitMu;
                std::condition_variable limitCv;
                bool turnOver = false;
                std::thread limiter;
                if (run->timeLimit > 0) {
                    limiter = std::thread([&] {
                        std::unique_lock<std::mutex> lock(limitMu);
                        if (!limitCv.wait_for(lock, std::chrono::seconds(run->timeLimit), [&] { return turnOver; })) {
                            run->timedOut = true;
                            run->cancel();
                        }
                    });
                }
                struct StopLimiter {
                    std::mutex& mu;
                    std::condition_variable& cv;
                    bool& over;
                    std::thread& t;
                    void stop() {
                        {
                            std::lock_guard<std::mutex> lock(mu);
                            over = true;
                        }
                        cv.notify_all();
                        if (t.joinable()) t.join();
                    }
                    ~StopLimiter() { stop(); }
                } stopLimiter{limitMu, limitCv, turnOver, limiter};
                json r = s->agent->processQuery(text, maxSteps);
                stopLimiter.stop();  // a finished answer is never relabelled as stopped at the limit
                const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
                json usage = {{"steps", r.value("steps_taken", 0)}, {"tools_used", s->console->toolsUsed()},
                              {"elapsed", elapsed}};
                if (r.contains("usage") && r["usage"].is_object()) {
                    // "tokens" is what the model wrote: the UI prices it at the output rate.
                    usage["tokens"] = r["usage"].value("completion_tokens", 0);
                    usage["input_tokens"] = r["usage"].value("prompt_tokens", 0);
                    usage["cached_tokens"] = r["usage"].value("cached_tokens", 0);
                }
                if (run->timedOut) {
                    terminal = timeLimitFinal(*run);
                    terminal["usage"] = usage;
                } else if (run->cancelled) {
                    terminal = {{"type", "final"}, {"answer", ""}};
                } else {
                    terminal = {{"type", "final"}, {"answer", r.value("result", "")}, {"usage", usage},
                                {"context_error", kNoContextMeasure}};
                    if (r.value("status", "success") == "error") {
                        terminal["failed"] = true;
                        terminal["failure"] = r.value("error", r.value("result", "The turn failed"));
                        // The server may have gone mid-turn: say so, not the transport's generic advice.
                        LemonadeClient probe(server.baseUrl);
                        probe.setApiKey(server.apiKey);
                        bool nothing = false;
                        const std::string down =
                            serverDownReason(probe, server.origin, surfaceOf(run->surface), &nothing);
                        if (!down.empty()) {
                            terminal["answer"] = down;
                            terminal["failure"] = nothing ? std::string(kModelServerDown) : down;
                        }
                    }
                    if (hasTurns) {
                        // Before this turn the history was exactly what the UI sent; the rest is this turn.
                        const auto all = s->agent->history();
                        json msgs = json::array();
                        for (size_t i = std::min(before, all.size()); i < all.size(); ++i) msgs.push_back(all[i].toJson());
                        terminal["model_messages"] = msgs;
                    }
                }
            } catch (const UnreadableAttachment& e) {
                terminal = run->cancelled ? json{{"type", "final"}, {"answer", ""}}
                                          : json{{"type", "final"}, {"answer", e.what()}, {"failed", true},
                                                 {"failure", e.what()}};
            } catch (const ServerDown& e) {
                // Same shape as a turn the model server failed mid-way.
                terminal = run->cancelled ? json{{"type", "final"}, {"answer", ""}}
                                          : json{{"type", "final"}, {"answer", e.what()}, {"failed", true},
                                                 {"failure", e.nothingAnswered ? std::string(kModelServerDown)
                                                                               : std::string(e.what())}};
            } catch (const BadRequest& e) {
                // Found once the agent is built, so it ends the stream rather than refusing the request.
                terminal = {{"type", "error"}, {"detail", e.what()}, {"status", e.status}};
            } catch (const std::exception& e) {
                terminal = run->timedOut  ? timeLimitFinal(*run)
                           : run->cancelled ? json{{"type", "final"}, {"answer", ""}}
                                            : json{{"type", "error"}, {"detail", e.what()}, {"status", 500}};
            } catch (...) {
                // Anything else would end the process and every other chat with it.
                terminal = {{"type", "error"}, {"detail", "The turn failed with an unknown error"}, {"status", 500}};
            }
            if (!run->refusedAction.empty()) terminal = unattendedRefusal(*run);
            if (!withheld.empty() && terminal.value("type", "") == "final") terminal["withheld_tools"] = withheld;
            if (s->console) s->console->detach();
            {
                std::lock_guard<std::mutex> lock(s->currentMu);
                s->current.reset();
            }
            s->turnLock = nullptr;
            if (turnLock.owns_lock()) turnLock.unlock();
            {
                std::lock_guard<std::mutex> lock(runsMu_);
                runs_.erase(run->id);  // the stream keeps its own reference
            }
            if (s->temporary) dropSession(s->id);
            s->busy = false;
            run->finish(terminal);
        }).detach();
        } catch (const std::system_error& e) {
            {
                std::lock_guard<std::mutex> lock(runsMu_);
                runs_.erase(runId);
            }
            if (s->temporary) dropSession(s->id);
            s->busy = false;
            throw BadRequest(503, std::string("Can't start a thread for this turn: ") + e.what());
        }

        res.set_header("Cache-Control", "no-cache");
        res.set_chunked_content_provider(
            "text/event-stream; charset=utf-8", [this, run, s](size_t, httplib::DataSink& sink) {
                auto last = std::chrono::steady_clock::now();
                auto stopRun = [&] { run->cancel(); };  // the client went away: stop the run
                for (;;) {
                    std::deque<std::string> out;
                    bool done = false;
                    {
                        std::unique_lock<std::mutex> lock(run->mu);
                        run->cv.wait_for(lock, std::chrono::milliseconds(200),
                                         [&] { return !run->frames.empty() || run->finished; });
                        out.swap(run->frames);
                        done = run->finished;
                    }
                    for (const auto& f : out) {
                        if (!sink.write(f.data(), f.size())) {
                            stopRun();
                            return false;
                        }
                        last = std::chrono::steady_clock::now();
                    }
                    if (done) {
                        sink.done();
                        return true;
                    }
                    if (std::chrono::steady_clock::now() - last > kKeepalive) {
                        static const std::string ka = ": keepalive\n\n";
                        if (!sink.write(ka.data(), ka.size())) {
                            stopRun();
                            return false;
                        }
                        last = std::chrono::steady_clock::now();
                    }
                }
            });
    }

    // Move the process and the shell to this chat's working folder: the one it
    // named, else its first folder, else home, so no chat starts in another's.
    // Returns what went wrong, or "".
    static std::string chatFolder(const Session& s) {
        std::string dir = s.workingDirectory;
        if (dir.empty()) {
            for (const auto& r : s.roots) {
                std::error_code ec;
                if (fs::is_directory(r, ec)) {
                    dir = r;
                    break;
                }
            }
        }
        if (dir.empty()) dir = getEnvVar("USERPROFILE", getEnvVar("HOME"));
        // With no home either, the folder the sidecar started in, never the last chat's.
        static const std::string started = [] {
            std::error_code ec;
            return fs::current_path(ec).u8string();
        }();
        if (dir.empty()) dir = started;
        return dir;
    }

    static std::string enterWorkingDirectory(Session& s) {
        const std::string dir = chatFolder(s);
        if (dir.empty()) return "";
        std::error_code ec;
        fs::current_path(dir, ec);
        if (ec) return "Can't use " + dir + " as the working folder: " + ec.message();
        // The shell moves only when the chat's folder changes, so a `cd` lasts between turns.
        if (dir != s.shellDir) {
            if (!s.shell->setCwd(dir)) return "Can't use " + dir + " as the working folder: not a folder";
            s.shellDir = dir;
        }
        return "";
    }

    // Attached documents are indexed before the turn, each once per agent. Returns what to tell the
    // user when one can't be read on this PC at all; the model would only guess its way around that.
    std::string indexDocuments(Session& s, const std::vector<std::string>& documents) {
        std::vector<std::string> pending;
        for (const auto& d : documents) {
            if (!s.indexed.count(d)) pending.push_back(d);
        }
        if (pending.empty()) return "";
        std::string unreadable;
        json names = json::array();
        for (const auto& d : pending) names.push_back(fs::path(d).filename().string());
        s.console->emit({{"type", "tool_call"}, {"tool", "index_documents"}, {"args", {{"documents", names}}}});
        json results = json::array();
        for (const auto& d : pending) {
            json r = s.agent->toolRegistry().executeTool("index_document", {{"path", d}});
            results.push_back({{"document", fs::path(d).filename().string()}, {"result", r}});
            if (r.value("status", "") != "error" && !r.contains("error")) s.indexed.insert(d);
            if (!unreadable.empty() || !r.contains("failed") || !r["failed"].is_array()) continue;
            for (const auto& f : r["failed"]) {
                if (f.is_object() && f.contains("missing_program")) {
                    unreadable = MissingDocumentReader::remedy(fs::path(d).filename().string(),
                                                               f.value("missing_program", ""));
                    break;
                }
            }
        }
        s.console->emit({{"type", "tool_result"}, {"tool", "index_documents"}, {"data", results}});
        return unreadable;
    }

    SidecarOptions opts_;
    std::string token_;
    httplib::Server server_;
    std::mutex sessionsMu_;
    std::map<std::string, std::shared_ptr<Session>> sessions_;
    std::string lemonadeKey_;
    std::mutex runsMu_;
    std::map<std::string, std::shared_ptr<Run>> runs_;
    std::set<std::string> earlyCancels_;
    std::deque<std::string> earlyCancelOrder_;
    std::timed_mutex turnsMu_;
    std::atomic<int> approvalsReturning_{0};  // chats waiting to take turnsMu_ back after an approval
};

}  // namespace

int runSidecar(const SidecarOptions& options) {
    Sidecar s(options);
    return s.run();
}

} // namespace gaia
