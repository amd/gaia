// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "stdio_server.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <thread>

#include <gaia/console.h>
#include <gaia/flagship_config.h>
#include <gaia/flagship_tools.h>

#include "flagship_agent.h"

namespace fs = std::filesystem;

namespace gaia {

namespace {

const char* const kQueryKey = "gaia_query";
const char* const kControlKey = "gaia_control";
const char* const kClearSentinel = "\x00gaia:clear_conversation\x00";
const char* const kMemoryDumpSentinel = "\x00gaia:memory_dump\x00";
const char* const kWarmUpSentinel = "\x00gaia:warm_up\x00";
// The TUI answers within its own deadline; this only fires when nobody can.
constexpr auto kOrphanedConfirmTimeout = std::chrono::minutes(15);
constexpr std::size_t kMaxEventData = 20000;

std::string sentinel(const char* s) {
    // The sentinels start with NUL, so they are built with an explicit length.
    std::size_t n = 1;
    while (s[n] != '\0') ++n;
    return std::string(s, n + 1);
}

std::string clip(const std::string& s, std::size_t n) {
    if (s.size() <= n) return s;
    std::size_t cut = n;
    while (cut > 0 && (static_cast<unsigned char>(s[cut]) & 0xC0) == 0x80) --cut;
    return s.substr(0, cut) + "…";
}

std::string argOf(const json& args, const char* key) {
    if (args.is_object() && args.contains(key) && args[key].is_string()) return args[key].get<std::string>();
    return "";
}

// One plain line on what a call is doing, for the activity log.
std::string narrate(const std::string& tool, const json& args) {
    if (tool == "read_file") return "Reading " + argOf(args, "file_path");
    if (tool == "write_file") return "Writing " + argOf(args, "file_path");
    if (tool == "edit_file") return "Editing " + argOf(args, "file_path");
    if (tool == "search_file" || tool == "find_files") {
        return "Looking for " + (argOf(args, "file_pattern") + argOf(args, "query"));
    }
    if (tool == "search_file_content") return "Searching files for \"" + argOf(args, "pattern") + "\"";
    if (tool == "browse_directory") return "Listing " + argOf(args, "directory_path");
    if (tool == "run_shell_command") return "Running " + clip(argOf(args, "command"), 120);
    if (tool == "run_python") return "Running Python";
    if (tool == "search_web") return "Searching the web for \"" + argOf(args, "query") + "\"";
    if (tool == "fetch_webpage" || tool == "fetch_page") return "Reading " + argOf(args, "url");
    if (tool == "remember") return "Saving to memory";
    if (tool == "recall" || tool == "search_past_conversations") return "Checking memory";
    if (tool == "load_skill") return "Loading the " + argOf(args, "name") + " skill";
    if (tool == "query_data") return "Querying the data";
    return "";
}

// How the host labels a prompt: what the call can do, not its tool family.
std::string riskOf(const std::string& tool) {
    if (tool == "run_shell_command" || tool == "run_python") return "execute";
    if (tool == "write_file" || tool == "edit_file" || tool == "download_file") return "write";
    return "read";
}

std::string previewOf(const json& result) {
    if (result.is_string()) return clip(result.get<std::string>(), 200);
    if (!result.is_object()) return clip(result.dump(), 200);
    for (const char* key : {"error", "display_message", "message", "output", "content", "stdout"}) {
        if (result.contains(key) && result[key].is_string() && !result[key].get<std::string>().empty()) {
            return clip(result[key].get<std::string>(), 200);
        }
    }
    return clip(result.dump(), 200);
}

// Agent activity → canonical events. Tool args and results arrive through
// prettyPrintJson with their titles, the way the base loop reports them.
class CanonicalEmitter : public OutputHandler {
public:
    explicit CanonicalEmitter(StdioServer& server) : server_(server) {}

    void printStepHeader(int, int) override {}
    void printStateInfo(const std::string&) override {}
    void printThought(const std::string&) override {}
    void printGoal(const std::string&) override {}
    void printPlan(const json&, int) override {}
    void printToolUsage(const std::string& toolName) override { tool_ = toolName; }
    void printToolComplete() override {}
    void prettyPrintJson(const json& data, const std::string& title) override {
        if (title == "Tool Args") {
            lastArgs_ = data;
            server_.write({{"type", "tool_call"}, {"tool", tool_}, {"args", data}, {"narration", narrate(tool_, data)}});
        } else if (title == "Tool Result") {
            json shown = data;
            const std::string dumped = data.dump(-1, ' ', false, json::error_handler_t::replace);
            if (dumped.size() > kMaxEventData) shown = {{"truncated", true}, {"text", clip(dumped, kMaxEventData)}};
            server_.write({{"type", "tool_result"}, {"tool", tool_}, {"data", shown}, {"preview", previewOf(data)}});
        }
    }
    void printError(const std::string& message) override { status(message); }
    void printWarning(const std::string& message) override { status(message); }
    void printInfo(const std::string&) override {}
    void startProgress(const std::string&) override {}
    void stopProgress() override {}
    void printFinalAnswer(const std::string&, const UsageStats&) override {}
    void printCompletion(int, int) override {}
    void printProcessingStart(const std::string&, int, const std::string&) override {
        resetStream();
        server_.reassertCancel();
    }
    void printStreamEnd() override {
        if (!inThink_ && !held_.empty()) server_.write({{"type", "token"}, {"delta", held_}});
        resetStream();
    }
    void printStreamToken(const std::string& token) override {
        // Reasoning stays out of the transcript, as in the non-streamed answer. A tag
        // may be split across tokens, so a possible tag prefix is held back.
        std::string text = held_ + token;
        held_.clear();
        std::string visible;
        std::size_t i = 0;
        while (i < text.size()) {
            const std::string marker = inThink_ ? "</think>" : "<think>";
            const auto at = text.find(marker, i);
            if (at == std::string::npos) {
                std::size_t keep = 0;
                for (std::size_t k = std::min(marker.size() - 1, text.size() - i); k > 0; --k) {
                    if (text.compare(text.size() - k, k, marker, 0, k) == 0) {
                        keep = k;
                        break;
                    }
                }
                // A stray closing tag with no opening one: drop the tag itself.
                if (!inThink_) {
                    std::string rest = text.substr(i, text.size() - i - keep);
                    for (auto c = rest.find("</think>"); c != std::string::npos; c = rest.find("</think>")) rest.erase(c, 8);
                    visible += rest;
                }
                held_ = text.substr(text.size() - keep);
                break;
            }
            if (!inThink_) visible += text.substr(i, at - i);
            inThink_ = !inThink_;
            i = at + marker.size();
        }
        if (!visible.empty()) server_.write({{"type", "token"}, {"delta", visible}});
    }

private:
    void status(const std::string& message) { server_.write({{"type", "status"}, {"message", message}}); }

    StdioServer& server_;
    std::string tool_;
    json lastArgs_;
    bool inThink_ = false;
    std::string held_;

    void resetStream() {
        inThink_ = false;
        held_.clear();
    }
};

}  // namespace

StdioServer::StdioServer(FlagshipAgent& agent, std::istream& in, std::ostream& out, bool fullAccess,
                         bool acceptEdits, std::string agentVersion)
    : agent_(agent),
      in_(in),
      out_(out),
      agentVersion_(std::move(agentVersion)),
      fullAccess_(fullAccess),
      acceptEdits_(acceptEdits) {}

void StdioServer::write(const json& event) {
    std::lock_guard<std::mutex> lock(outMutex_);
    out_ << event.dump(-1, ' ', false, json::error_handler_t::replace) << "\n";
    out_.flush();
}

json StdioServer::statusEvent() {
    const AgentConfig cfg = agent_.config();
    json event = {{"type", "status"},
                  {"message", ""},
                  {"model_id", cfg.modelId},
                  {"model_display", cfg.modelId},
                  {"model_backend", "lemonade"},
                  {"model_remote", false},
                  {"lemonade_base_url", cfg.baseUrl},
                  {"agent_version", agentVersion_},
                  {"gaia_version", agentVersion_ + "-native"}};
    const LemonadeHealth health = agent_.lemonade().healthCheck();
    event["lemonade_reachable"] = health.running;
    if (health.raw.is_object() && health.raw.contains("version") && health.raw["version"].is_string()) {
        event["lemonade_version"] = health.raw["version"];
    }
    return event;
}

namespace {

// The field as a string, "" when absent; a value of another type is a malformed message.
std::string controlString(const json& message, const char* key) {
    if (!message.contains(key) || message[key].is_null()) return "";
    if (!message[key].is_string()) {
        throw std::invalid_argument(std::string("'") + key + "' must be a string, got " + message[key].type_name());
    }
    return message[key].get<std::string>();
}

bool controlFlag(const json& message, const char* key) {
    if (!message.contains(key) || message[key].is_null()) return false;
    if (!message[key].is_boolean()) {
        throw std::invalid_argument(std::string("'") + key + "' must be true or false, got " + message[key].type_name());
    }
    return message[key].get<bool>();
}

}  // namespace

void StdioServer::applyControl(const json& message) {
    const std::string verb = controlString(message, kControlKey);
    std::unique_lock<std::mutex> lock(mutex_);
    if (verb == "full_access" || verb == "bypass") {
        // `bypass` is the retired spelling; an older host gets full access OFF.
        const bool enabled = controlFlag(message, "enabled");
        fullAccess_ = verb == "full_access" && enabled;
    } else if (verb == "accept_edits") {
        acceptEdits_ = controlFlag(message, "enabled");
    } else if (verb == "cancel") {
        if (turnActive_) {
            cancelPending_ = true;
            agent_.requestCancel();
            if (!pendingConfirmId_.empty()) decision_ = "deny";
            decisionCv_.notify_all();
        }
    } else if (verb == "clear_history") {
        queue_.push_back({Item::Clear, ""});
        queueCv_.notify_all();
    } else if (verb == "tool_decision") {
        std::string decision;
        std::string id;
        try {
            decision = controlString(message, "decision");
            id = controlString(message, "confirm_id");
        } catch (const std::invalid_argument&) {
            // An unreadable answer is not consent: the open prompt is denied.
            if (!pendingConfirmId_.empty()) {
                decision_ = "deny";
                decisionCv_.notify_all();
            }
            throw;
        }
        if (decision.empty()) decision = "deny";
        if (decision != "allow" && decision != "deny" && decision != "always" && decision != "timeout") {
            std::cerr << "[gaia-agent] unknown tool decision '" << decision << "', denying\n";
            decision = "deny";  // an unreadable decision is not consent
        }
        if (pendingConfirmId_.empty()) {
            std::cerr << "[gaia-agent] dropped a '" << decision << "' decision: nothing is waiting for one\n";
        } else if (!id.empty() && id != pendingConfirmId_) {
            std::cerr << "[gaia-agent] dropped a decision for " << id << ": the open prompt is " << pendingConfirmId_ << "\n";
        } else {
            decision_ = decision;
            decisionCv_.notify_all();
        }
    } else {
        std::cerr << "[gaia-agent] ignored unknown control verb '" << verb << "'\n";
    }
}

void StdioServer::rejectControl(const std::string& why) {
    std::cerr << "[gaia-agent] rejected a control message: " << why << "\n";
    bool busy;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        busy = turnActive_;
    }
    const std::string message = "Ignored a malformed control message: " + why;
    // An error event is a turn's terminal event, so a running turn hears it as a status line.
    write(busy ? json{{"type", "status"}, {"message", message}} : json{{"type", "error"}, {"detail", message}});
}

void StdioServer::pumpStdin() {
    for (std::string line; std::getline(in_, line);) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        if (line.front() == '{') {
            json msg = json::parse(line, nullptr, false);
            if (!msg.is_discarded() && msg.is_object()) {
                if (msg.contains(kControlKey)) {
                    try {
                        applyControl(msg);
                    } catch (const std::exception& e) {
                        rejectControl(e.what());
                    }
                    continue;
                }
                if (msg.contains(kQueryKey) && msg[kQueryKey].is_string()) {
                    line = msg[kQueryKey].get<std::string>();
                }
            }
        }
        std::lock_guard<std::mutex> lock(mutex_);
        queue_.push_back({Item::Query, line});
        queueCv_.notify_all();
    }
    // The host is gone: drop queued turns, stop whatever is running and let the loop end.
    std::lock_guard<std::mutex> lock(mutex_);
    queue_.clear();
    if (turnActive_) {
        cancelPending_ = true;
        agent_.requestCancel();
        if (!pendingConfirmId_.empty()) decision_ = "deny";
        decisionCv_.notify_all();
    }
    queue_.push_back({Item::Stop, ""});
    queueCv_.notify_all();
}

ToolConfirmResult StdioServer::confirm(const std::string& tool, const json& args) {
    const bool isEdit = tool == "write_file" || tool == "edit_file";
    // An edit's "always" covers the one file it was offered for, as in Python's write_file:<path>.
    std::string grantKey, grantLabel;
    std::string target;
    if (isEdit) {
        const std::string raw = argOf(args, "file_path");
        target = raw.empty() ? "" : agent_.toolbelt().resolveAllowed(raw);
        if (!target.empty()) {
            fs::path norm = fs::u8path(target).lexically_normal();
            std::string key = norm.u8string();
#ifdef _WIN32
            for (auto& ch : key) ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
#endif
            grantKey = tool + ":" + key;
            grantLabel = tool + " on " + norm.filename().u8string();
        }
    }
    // Judged off the lock: it reads the filesystem and PATH.
    const bool insideWorkspace = !target.empty() && editIsInside(target, {agent_.options().workDir});
    std::unique_lock<std::mutex> lock(mutex_);
    if (fullAccess_) return ToolConfirmResult::ALLOW_ONCE;
    if (!grantKey.empty() && sessionGrants_.count(grantKey)) return ToolConfirmResult::ALLOW_ONCE;
    if (acceptEdits_ && insideWorkspace) return ToolConfirmResult::ALLOW_ONCE;
    const std::string confirmId = "c" + std::to_string(++confirmCounter_);
    std::string summary;
    if (tool == "run_shell_command") {
        summary = argOf(args, "command");
        const std::string wd = argOf(args, "working_directory");
        summary += "\nin " + (wd.empty() ? agent_.options().workDir : wd);
    } else if (tool == "run_python") {
        summary = clip(argOf(args, "code"), 600);
    } else if (isEdit) {
        summary = (tool == "write_file" ? "Write " : "Edit ") + argOf(args, "file_path");
    } else {
        summary = tool + " " + clip(args.dump(), 300);
    }
    json event = {{"type", "needs_confirmation"},
                  {"run_id", runId_},
                  {"action", tool},
                  {"summary", summary},
                  {"confirm_id", confirmId},
                  {"risk", riskOf(tool)},
                  {"args", args}};
    // "Always" is only offered where the grant names one file.
    if (!grantKey.empty()) event["always_scope"] = grantLabel;
    pendingConfirmId_ = confirmId;
    decision_.clear();
    lock.unlock();
    write(event);
    lock.lock();
    const bool answered = decisionCv_.wait_for(lock, kOrphanedConfirmTimeout, [this] { return !decision_.empty(); });
    const std::string decision = answered ? decision_ : "timeout";
    pendingConfirmId_.clear();
    decision_.clear();
    if (decision == "always" && !grantKey.empty()) sessionGrants_.insert(grantKey);
    return (decision == "allow" || decision == "always") ? ToolConfirmResult::ALLOW_ONCE : ToolConfirmResult::DENY;
}

void StdioServer::reassertCancel() {
    // processQuery clears the cancel flag as it starts; a cancel that arrived
    // between accepting the turn and that point must still stop it.
    std::lock_guard<std::mutex> lock(mutex_);
    if (cancelPending_) agent_.requestCancel();
}

void StdioServer::runModelCommand(const std::string& arg) {
    if (arg.empty()) {
        std::string list = "Currently running: **" + agent_.config().modelId + "**. Switch with `/model <id>`.";
        try {
            const json models = agent_.lemonade().listModels();
            if (models.contains("data") && models["data"].is_array()) {
                list = "Models on this Lemonade:\n";
                for (const auto& m : models["data"]) {
                    const std::string id = m.value("id", "");
                    list += "- `" + id + "`" + (id == agent_.config().modelId ? " (current)" : "") + "\n";
                }
                list += "\nCurrently running: **" + agent_.config().modelId + "**. Switch with `/model <id>`.";
            }
        } catch (const std::exception& e) {
            list += "\n(Lemonade did not list its models: " + std::string(e.what()) + ")";
        }
        write({{"type", "final"}, {"answer", list}});
        return;
    }
    std::vector<std::string> available;
    try {
        const json models = agent_.lemonade().listModels();
        if (!models.contains("data") || !models["data"].is_array()) {
            throw std::runtime_error("its model list had no 'data' array");
        }
        for (const auto& m : models["data"]) {
            if (m.is_object() && m.contains("id") && m["id"].is_string()) available.push_back(m["id"].get<std::string>());
        }
    } catch (const std::exception& e) {
        write({{"type", "error"},
               {"detail", "Could not switch to " + arg + ": Lemonade at " + agent_.config().baseUrl +
                              " did not list its models (" + e.what() + "). The model was not changed."}});
        return;
    }
    if (std::find(available.begin(), available.end(), arg) == available.end()) {
        std::string names;
        for (const auto& id : available) names += (names.empty() ? "" : ", ") + id;
        write({{"type", "error"},
               {"detail", "Unknown Lemonade model '" + arg + "'. Downloaded models: " +
                              (names.empty() ? std::string("(none)") : names) + "."}});
        return;
    }
    AgentConfig cfg = agent_.config();
    cfg.modelId = arg;
    cfg.nativeToolCalls = flagshipNativeToolCalls(arg);
    // The stdio transport streams exactly when the model calls tools natively.
    cfg.streaming = cfg.useNativeToolCalls();
    agent_.setConfig(cfg);
    write(statusEvent());
    write({{"type", "final"}, {"answer", "Switched to **" + arg + "** (local, through Lemonade)."}});
}

void StdioServer::runQuery(const std::string& query) {
    try {
        runQueryOrThrow(query);
    } catch (const std::exception& e) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            turnActive_ = false;
        }
        write({{"type", "error"}, {"detail", e.what()}});
    }
}

void StdioServer::runQueryOrThrow(const std::string& query) {
    if (query == sentinel(kClearSentinel)) {
        agent_.clearHistory();
        write({{"type", "final"}, {"answer", "conversation_cleared"}});
        return;
    }
    if (query == sentinel(kMemoryDumpSentinel)) {
        json payload = agent_.memory() ? agent_.memory()->dump()
                                       : json{{"available", false},
                                              {"reason", "Memory is off for this session (--no-memory, --fast or "
                                                         "GAIA_MEMORY_DISABLED)."}};
        write({{"type", "final"}, {"answer", payload.dump()}});
        return;
    }
    if (query == sentinel(kWarmUpSentinel)) {
        try {
            agent_.lemonade().ensureModelLoaded();
            write({{"type", "final"}, {"answer", "warmed_up"}});
        } catch (const std::exception& e) {
            std::cerr << "[gaia-agent] warm-up skipped: " << e.what() << "\n";
            write({{"type", "final"}, {"answer", "warm_up_skipped"}});
        }
        return;
    }
    if (query == "/model" || query.rfind("/model ", 0) == 0) {
        std::string arg = query.size() > 6 ? query.substr(7) : "";
        arg.erase(0, arg.find_first_not_of(' '));
        runModelCommand(arg);
        return;
    }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        turnActive_ = true;
        cancelPending_ = false;
        runId_ = "run-" + std::to_string(std::chrono::system_clock::now().time_since_epoch().count());
    }
    json terminal;
    try {
        const json result = agent_.processQuery(query);
        if (result.contains("error")) {
            // The model never answered: a failed turn, not an answer.
            write({{"type", "status"}, {"message", result.value("result", "")}});
            terminal = {{"type", "error"}, {"detail", result["error"].get<std::string>()}};
            std::lock_guard<std::mutex> lock(mutex_);
            turnActive_ = false;
            write(terminal);
            return;
        }
        terminal = {{"type", "final"}, {"answer", result.value("result", "")}};
        json usage = json::object();
        if (result.value("steps_taken", 0) > 0) usage["steps"] = result["steps_taken"];
        if (result.contains("usage")) {
            const json& u = result["usage"];
            if (u.value("prompt_tokens", 0) > 0) usage["input_tokens"] = u["prompt_tokens"];
            if (u.value("completion_tokens", 0) > 0) usage["tokens"] = u["completion_tokens"];
        }
        if (!usage.empty()) terminal["usage"] = usage;
    } catch (const std::exception& e) {
        terminal = {{"type", "error"}, {"detail", e.what()}};
    }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        turnActive_ = false;
    }
    write(terminal);
}

int StdioServer::run() {
    agent_.setOutputHandler(std::make_unique<CanonicalEmitter>(*this));
    agent_.setToolConfirmCallback([this](const std::string& tool, const json& args) { return confirm(tool, args); });
    write(statusEvent());
    std::thread reader([this] { pumpStdin(); });
    for (;;) {
        Item item;
        {
            std::unique_lock<std::mutex> lock(mutex_);
            queueCv_.wait(lock, [this] { return !queue_.empty(); });
            item = queue_.front();
            queue_.pop_front();
        }
        if (item.kind == Item::Stop) break;
        if (item.kind == Item::Clear) {
            try {
                agent_.clearHistory();
            } catch (const std::exception& e) {
                write({{"type", "status"}, {"message", std::string("Could not clear the conversation: ") + e.what()}});
            }
            continue;
        }
        runQuery(item.text);
    }
    reader.join();
    return 0;
}

}  // namespace gaia
