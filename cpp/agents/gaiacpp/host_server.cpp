// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "host_server.h"

#include <chrono>
#include <cmath>
#include <istream>
#include <memory>
#include <optional>
#include <ostream>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include <gaia/agent.h>
#include <gaia/console.h>
#include <gaia/types.h>

namespace gaia {

namespace {

const char* const kLlmFailure = "Unable to complete task due to LLM error: ";

/// A message the host sent that the engine cannot act on. `code` is stable
/// for a client to switch on; the text says what to send instead.
struct ProtocolError : std::runtime_error {
    ProtocolError(std::string code_, const std::string& what)
        : std::runtime_error(what), code(std::move(code_)) {}
    std::string code;
};

/// The host closed stdin while the engine was waiting for it.
struct HostGone : std::runtime_error {
    HostGone() : std::runtime_error("the host closed the connection") {}
};

std::string dump(const json& j) {
    return j.dump(-1, ' ', false, json::error_handler_t::replace);
}

std::string requireString(const json& j, const char* key, const char* where) {
    if (!j.contains(key) || !j[key].is_string() || j[key].get<std::string>().empty()) {
        throw ProtocolError(std::string("bad_") + where,
                            std::string("`") + where + "` needs a non-empty string `" + key + "`.");
    }
    return j[key].get<std::string>();
}

int positiveInt(const json& j, const char* key, const char* where, int fallback) {
    if (!j.contains(key) || j[key].is_null()) return fallback;
    if (!j[key].is_number_integer() || j[key].get<long long>() <= 0) {
        throw ProtocolError(std::string("bad_") + where,
                            std::string("`") + key + "` in `" + where +
                                "` must be a positive whole number, got " + dump(j[key]) + ".");
    }
    return j[key].get<int>();
}

ToolParamType paramType(const json& schema) {
    const std::string type = schema.is_object() ? schema.value("type", std::string()) : std::string();
    if (type == "string") return ToolParamType::STRING;
    if (type == "integer") return ToolParamType::INTEGER;
    if (type == "number") return ToolParamType::NUMBER;
    if (type == "boolean") return ToolParamType::BOOLEAN;
    if (type == "array") return ToolParamType::ARRAY;
    if (type == "object") return ToolParamType::OBJECT;
    return ToolParamType::UNKNOWN;
}

/// One tool the host declared in `init`.
struct HostTool {
    std::string name;
    std::string description;
    std::vector<ToolParameter> parameters;
};

HostTool parseTool(const json& j) {
    if (!j.is_object()) throw ProtocolError("bad_init", "Each entry of `tools` must be an object.");
    HostTool tool;
    tool.name = requireString(j, "name", "init");
    tool.description = j.value("description", std::string());
    const json schema = j.value("parameters", json::object());
    if (!schema.is_object()) {
        throw ProtocolError("bad_init", "`parameters` of tool `" + tool.name +
                                            "` must be a JSON Schema object.");
    }
    std::set<std::string> required;
    if (schema.contains("required") && schema["required"].is_array()) {
        for (const auto& name : schema["required"]) {
            if (name.is_string()) required.insert(name.get<std::string>());
        }
    }
    const json properties = schema.value("properties", json::object());
    if (!properties.is_object()) {
        throw ProtocolError("bad_init", "`parameters.properties` of tool `" + tool.name +
                                            "` must be an object.");
    }
    for (auto it = properties.begin(); it != properties.end(); ++it) {
        ToolParameter p;
        p.name = it.key();
        p.type = paramType(it.value());
        p.required = required.count(p.name) > 0;
        if (it.value().is_object()) p.description = it.value().value("description", std::string());
        // An array or object needs its shape; a scalar is fully said by its type.
        if (p.type == ToolParamType::ARRAY || p.type == ToolParamType::OBJECT ||
            (it.value().is_object() && it.value().contains("enum"))) {
            p.schema = it.value();
        }
        tool.parameters.push_back(std::move(p));
    }
    return tool;
}

/// What a turn may spend. Zero leaves a limit off.
struct Budget {
    int modelCalls = 0;
    int outputTokens = 0;
    double seconds = 0.0;
};

class HostAgent;

/// Everything one connection needs: the wire, and the turn in progress.
class Session {
public:
    Session(std::istream& in, std::ostream& out) : in_(in), out_(out) {}

    void write(const json& event) {
        out_ << dump(event) << '\n';
        out_.flush();
    }

    /// The next message from the host, or nullopt at end of input.
    std::optional<json> read() {
        std::string line;
        while (std::getline(in_, line)) {
            if (!line.empty() && line.back() == '\r') line.pop_back();
            if (line.find_first_not_of(" \t") == std::string::npos) continue;
            json message = json::parse(line, nullptr, false);
            if (message.is_discarded() || !message.is_object() || !message.contains("type") ||
                !message["type"].is_string()) {
                throw ProtocolError("bad_message",
                                    "Each line must be a JSON object with a string `type`.");
            }
            return message;
        }
        return std::nullopt;
    }

    // ---- the turn in progress ----

    void beginTurn(std::string id, Budget budget) {
        turnId_ = std::move(id);
        budget_ = budget;
        started_ = std::chrono::steady_clock::now();
        modelCalls_ = 0;
        inputTokens_ = cachedTokens_ = outputTokens_ = 0;
        nextCall_ = 0;
        stop_.clear();
        toolCalls_ = json::array();
    }

    double elapsed() const {
        return std::chrono::duration<double>(std::chrono::steady_clock::now() - started_).count();
    }

    /// The limit this turn has reached, or "" while there is room.
    std::string spent() const {
        if (budget_.seconds > 0 && elapsed() >= budget_.seconds) return "deadline";
        if (budget_.modelCalls > 0 && modelCalls_ >= budget_.modelCalls) return "model_calls";
        if (budget_.outputTokens > 0 && outputTokens_ >= budget_.outputTokens) return "output_tokens";
        return "";
    }

    void noteModelCall(const json& line) {
        ++modelCalls_;
        inputTokens_ += line.value("input_tokens", 0);
        cachedTokens_ += line.value("cached_tokens", 0);
        outputTokens_ += line.value("output_tokens", 0);
        write({{"type", "model_call"},
               {"turn", turnId_},
               {"step", line.value("step", 0)},
               {"seconds", line.value("wall_s", 0.0)},
               {"usage",
                {{"input_tokens", line.value("input_tokens", 0)},
                 {"cached_tokens", line.value("cached_tokens", 0)},
                 {"output_tokens", line.value("output_tokens", 0)}}}});
    }

    /// Ask the host to run a tool and wait for its answer.
    json callHostTool(const std::string& name, const json& args) {
        const std::string id = turnId_ + ":" + std::to_string(++nextCall_);
        write({{"type", "tool_request"}, {"id", id}, {"turn", turnId_}, {"name", name}, {"arguments", args}});
        const std::optional<json> answer = read();
        if (!answer) throw HostGone();
        const std::string type = (*answer)["type"].get<std::string>();
        if (type != "tool_result" || answer->value("id", std::string()) != id) {
            throw ProtocolError("bad_message", "Expected `tool_result` for `" + id + "`, got `" + type +
                                                   "`. Answer each `tool_request` before sending "
                                                   "anything else.");
        }
        json result;
        bool failed = false;
        if (answer->contains("error") && (*answer)["error"].is_string()) {
            result = {{"status", "error"}, {"error", (*answer)["error"]}};
            failed = true;
        } else if (answer->contains("result")) {
            result = (*answer)["result"];
        } else {
            throw ProtocolError("bad_message", "`tool_result` for `" + id +
                                                   "` needs `result`, or `error` as a string.");
        }
        toolCalls_.push_back({{"name", name}, {"arguments", args}, {"result", result}, {"failed", failed}});
        return result;
    }

    void stopFor(const std::string& reason) {
        if (stop_.empty()) stop_ = reason;
    }

    const std::string& turnId() const { return turnId_; }
    const std::string& stopReason() const { return stop_; }
    const json& toolCalls() const { return toolCalls_; }
    json usage() const {
        return {{"model_calls", modelCalls_},
                {"input_tokens", inputTokens_},
                {"cached_tokens", cachedTokens_},
                {"output_tokens", outputTokens_}};
    }

private:
    std::istream& in_;
    std::ostream& out_;
    std::string turnId_;
    Budget budget_;
    std::chrono::steady_clock::time_point started_{};
    int modelCalls_ = 0;
    int inputTokens_ = 0, cachedTokens_ = 0, outputTokens_ = 0;
    int nextCall_ = 0;
    std::string stop_;
    json toolCalls_ = json::array();
};

/// The loop with the host's prompt and the host's tools, and none of its own.
class HostAgent : public Agent {
public:
    HostAgent(const AgentConfig& cfg, std::string systemPrompt, std::vector<HostTool> tools,
              Session& session)
        : Agent(cfg), systemPrompt_(std::move(systemPrompt)), hostTools_(std::move(tools)),
          session_(session) {
        // The answer goes to the host on the wire and is printed nowhere else.
        setOutputHandler(std::make_unique<SilentConsole>(true));
        init();
        setStepLogger([this](const json& line) {
            const std::string kind = line.value("kind", std::string());
            if (kind == "llm") {
                session_.noteModelCall(line);
            } else if (kind == "tool") {
                // A tool ran, so another model request follows: stop it here.
                stopIfSpent();
            }
        });
    }

protected:
    std::string getSystemPrompt() const override { return systemPrompt_; }

    void registerTools() override {
        for (const HostTool& tool : hostTools_) {
            ToolInfo info;
            info.name = tool.name;
            info.description = tool.description;
            info.parameters = tool.parameters;
            info.policy = ToolPolicy::ALLOW;  // the host decides what its own tools may do
            const std::string name = tool.name;
            info.callback = [this, name](const json& args) -> json {
                if (stopIfSpent()) {
                    return {{"status", "error"}, {"error", "Not run: this turn's budget is spent."}};
                }
                return session_.callHostTool(name, args);
            };
            toolRegistry().registerTool(std::move(info));
        }
    }

private:
    bool stopIfSpent() {
        const std::string reason = session_.spent();
        if (reason.empty()) return false;
        session_.stopFor(reason);
        requestCancel();
        return true;
    }

    std::string systemPrompt_;
    std::vector<HostTool> hostTools_;
    Session& session_;
};

MessageRole roleOf(const json& message) {
    const std::string role = message.value("role", std::string());
    if (role == "user") return MessageRole::USER;
    if (role == "assistant") return MessageRole::ASSISTANT;
    throw ProtocolError("bad_turn", "A message's `role` must be \"user\" or \"assistant\", got " +
                                        dump(message.value("role", json())) +
                                        ". Tool calls of earlier turns are not sent back.");
}

std::unique_ptr<HostAgent> buildAgent(const json& init, Session& session) {
    if (init.value("protocol", 0) != kHostProtocol) {
        throw ProtocolError("bad_init", "`init` must carry \"protocol\": " + std::to_string(kHostProtocol) +
                                            ", got " + dump(init.value("protocol", json())) + ".");
    }
    AgentConfig cfg;
    cfg.modelId = requireString(init, "model", "init");
    cfg.baseUrl = requireString(init, "base_url", "init");
    cfg.apiKeyEnv = init.value("api_key_env", std::string());
    if (init.contains("api_key")) {
        throw ProtocolError("bad_init", "`init` must not carry `api_key`. Put the key in an environment "
                                        "variable of this process and name it in `api_key_env`.");
    }
    cfg.maxSteps = positiveInt(init, "max_steps", "init", 20);
    cfg.maxTokens = positiveInt(init, "max_output_tokens", "init", 4096);
    cfg.contextSize = positiveInt(init, "context_size", "init", 32768);
    cfg.temperature = init.value("temperature", 0.2);
    cfg.nativeToolCalls =
        init.value("native_tools", true) ? NativeToolCalls::Always : NativeToolCalls::Never;
    cfg.responseMode = ResponseMode::Conversational;
    cfg.silentMode = true;
    cfg.streaming = false;
    cfg.maxHistoryMessages = 0;      // the host sends the history it wants each turn
    cfg.stepBudgetNotices = false;   // nothing is added to the host's messages
    cfg.proseToolList = false;       // the prompt goes out as the host wrote it
    cfg.llmRetries = init.contains("retries") && init["retries"].is_number_integer()
                         ? init["retries"].get<int>()
                         : 0;
    if (cfg.llmRetries < 0) throw ProtocolError("bad_init", "`retries` must be 0 or more.");

    std::vector<HostTool> tools;
    std::set<std::string> names;
    const json declared = init.value("tools", json::array());
    if (!declared.is_array()) throw ProtocolError("bad_init", "`tools` must be an array.");
    for (const auto& entry : declared) {
        HostTool tool = parseTool(entry);
        if (!names.insert(tool.name).second) {
            throw ProtocolError("bad_init", "Tool `" + tool.name + "` is declared twice.");
        }
        tools.push_back(std::move(tool));
    }
    return std::make_unique<HostAgent>(cfg, init.value("system_prompt", std::string()), std::move(tools),
                                       session);
}

Budget parseBudget(const json& turn) {
    Budget budget;
    if (!turn.contains("budget") || turn["budget"].is_null()) return budget;
    const json& b = turn["budget"];
    if (!b.is_object()) throw ProtocolError("bad_turn", "`budget` must be an object.");
    budget.modelCalls = positiveInt(b, "model_calls", "turn", 0);
    budget.outputTokens = positiveInt(b, "output_tokens", "turn", 0);
    if (b.contains("seconds") && !b["seconds"].is_null()) {
        if (!b["seconds"].is_number() || b["seconds"].get<double>() <= 0) {
            throw ProtocolError("bad_turn", "`budget.seconds` must be a positive number.");
        }
        budget.seconds = b["seconds"].get<double>();
    }
    return budget;
}

json finalEvent(const Session& session, const json& result) {
    const std::string engineStatus = result.value("status", std::string("error"));
    const std::string engineStop = result.value("stop_reason", std::string());
    std::string text = result.value("result", std::string());
    std::string status = "success";
    std::string stop = "answered";
    json failure = nullptr;
    if (!session.stopReason().empty()) {
        status = "incomplete";
        stop = session.stopReason();
        text.clear();
    } else if (engineStatus == "error") {
        status = "failed";
        stop = "model_call";
        std::string message = text;
        if (message.rfind(kLlmFailure, 0) == 0) message = message.substr(std::string(kLlmFailure).size());
        failure = {{"kind", "model_call"}, {"message", message}};
    } else if (engineStatus == "cancelled") {
        status = "cancelled";
        stop = "cancelled";
    } else if (engineStatus == "incomplete") {
        status = "incomplete";
        stop = engineStop == "step_limit" ? "max_steps"
               : engineStop == "loop"     ? "repeated_calls"
               : engineStop == "empty"    ? "no_answer"
               : engineStop.empty()       ? "incomplete"
                                          : engineStop;
    }
    return {{"type", "final"},
            {"turn", session.turnId()},
            {"status", status},
            {"stop_reason", stop},
            {"text", text},
            {"tool_calls", session.toolCalls()},
            {"usage", session.usage()},
            {"seconds", std::round(session.elapsed() * 1000) / 1000},
            {"failure", failure}};
}

void runTurn(HostAgent& agent, Session& session, const json& turn) {
    const std::string id = requireString(turn, "id", "turn");
    if (!turn.contains("messages") || !turn["messages"].is_array() || turn["messages"].empty()) {
        throw ProtocolError("bad_turn", "`turn` needs `messages`: the conversation so far, ending "
                                        "with the user's new message.");
    }
    std::vector<Message> history;
    for (const auto& m : turn["messages"]) {
        if (!m.is_object() || !m.contains("content") || !m["content"].is_string()) {
            throw ProtocolError("bad_turn", "Each message needs `role` and a string `content`.");
        }
        Message message;
        message.role = roleOf(m);
        message.content = m["content"].get<std::string>();
        history.push_back(std::move(message));
    }
    if (history.back().role != MessageRole::USER) {
        throw ProtocolError("bad_turn", "The last message of a turn must be the user's.");
    }
    const std::string input = history.back().content;
    history.pop_back();
    const Budget budget = parseBudget(turn);

    session.beginTurn(id, budget);
    agent.setHistory(std::move(history));
    const json result = agent.processQuery(input);
    session.write(finalEvent(session, result));
}

}  // namespace

int runHostServer(std::istream& in, std::ostream& out, const HostOptions& options) {
    Session session(in, out);
    std::unique_ptr<HostAgent> agent;
    auto fail = [&](const std::string& code, const std::string& message) {
        session.write({{"type", "error"}, {"code", code}, {"message", message}});
    };
    try {
        const std::optional<json> init = session.read();
        if (!init) return 0;
        if ((*init)["type"].get<std::string>() != "init") {
            throw ProtocolError("bad_init", "The first message must be `init`.");
        }
        agent = buildAgent(*init, session);
        session.write({{"type", "ready"}, {"protocol", kHostProtocol}, {"version", options.engineVersion}});
    } catch (const ProtocolError& e) {
        fail(e.code, e.what());
        return 2;
    } catch (const std::exception& e) {
        fail("bad_init", e.what());
        return 2;
    }

    while (true) {
        try {
            const std::optional<json> message = session.read();
            if (!message) return 0;
            const std::string type = (*message)["type"].get<std::string>();
            if (type == "shutdown") return 0;
            if (type != "turn") {
                throw ProtocolError("bad_message", "Expected `turn` or `shutdown`, got `" + type + "`.");
            }
            runTurn(*agent, session, *message);
        } catch (const HostGone&) {
            return 0;
        } catch (const ProtocolError& e) {
            // The host's own mistake: say what to send, and keep serving.
            fail(e.code, e.what());
        } catch (const std::exception& e) {
            fail("engine_error", e.what());
        }
    }
}

}  // namespace gaia
