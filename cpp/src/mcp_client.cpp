// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/mcp_client.h"

#include <chrono>
#include <cstring>
#include <iostream>
#include <set>
#include <sstream>
#include <stdexcept>
#include <thread>

#ifdef _WIN32
#include <windows.h>
#else
#include <cerrno>
#include <fcntl.h>
#include <signal.h>
#include <sys/select.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace gaia {

// ---- MCP tool confirmation classifier ----

namespace {

// Verb tokens that indicate a tool changes state (filesystem, git/GitHub,
// process execution, Slack, SQL, containers). A tool whose name contains any
// of these is gated behind confirmation even if the server claims it is
// read-only — a server-supplied `readOnlyHint` is a claim, not a proof.
//
// Sibling gate in the Python SDK: TOOLS_REQUIRING_CONFIRMATION in
// src/gaia/agents/base/agent.py — keep the two vocabularies aligned.
//
// `start` and `interact` are here because desktop-commander's remote-code-
// execution tools are named `start_process` / `interact_with_process`; `bash`,
// `powershell`, `click` and `type` because shell and desktop-driving tools
// often carry no conventional verb at all.
const std::set<std::string>& mutatingVerbs() {
    static const std::set<std::string> kVerbs = {
        "add",        "alter",      "append",    "apply",       "approve",
        "archive",    "assign",     "bash",      "build",       "cancel",
        "checkout",   "chmod",      "chown",     "clear",       "click",
        "clone",      "close",      "cmd",       "commit",      "compress",
        "copy",       "create",     "decrypt",   "delete",      "deploy",
        "destroy",    "disable",    "dismiss",   "downgrade",   "download",
        "drag",       "drop",       "edit",      "enable",      "encrypt",
        "erase",      "eval",       "exec",      "execute",     "export",
        "extract",    "flush",      "fork",      "format",      "forward",
        "goto",       "grant",      "import",    "insert",      "install",
        "interact",   "invite",     "invoke",    "join",        "keystroke",
        "kick",       "kill",       "launch",    "leave",       "merge",
        "migrate",    "mkdir",      "modify",    "mount",       "move",
        "navigate",   "notify",     "open",      "paste",       "patch",
        "pin",        "post",       "powershell", "press",      "prune",
        "publish",    "pull",       "purge",     "push",        "put",
        "reboot",     "rebase",     "remove",    "rename",      "reopen",
        "replace",    "reply",      "reset",     "restart",     "restore",
        "revert",     "revoke",     "rmdir",     "rollback",    "rotate",
        "run",        "save",       "scale",     "scroll",      "send",
        "set",        "share",      "shell",     "shortcut",    "shutdown",
        "spawn",      "start",      "stop",      "submit",      "subscribe",
        "sudo",       "symlink",    "sync",      "terminate",   "touch",
        "transfer",   "trigger",    "truncate",  "type",        "uninstall",
        "unlink",     "unmount",    "unpin",     "unset",       "unsubscribe",
        "update",     "upgrade",    "upload",    "upsert",      "wipe",
        "write",
    };
    return kVerbs;
}

// A verb this long or longer also matches as a *prefix* ("writefile",
// "deletes"). Shorter verbs are exact-match only — "add"/"set"/"run" as
// prefixes would gate read-only names like `get_address` or `get_settings`.
constexpr size_t kMinPrefixVerbLength = 4;

// ASCII-only classification: gaia_core is embeddable and std::is* are
// locale-sensitive, so a host that calls setlocale must not shift tokenization.
bool isAsciiAlnum(char c) {
    return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
}
bool isAsciiUpper(char c) { return c >= 'A' && c <= 'Z'; }
bool isAsciiLower(char c) { return c >= 'a' && c <= 'z'; }
bool isAsciiDigit(char c) { return c >= '0' && c <= '9'; }
char toAsciiLower(char c) { return isAsciiUpper(c) ? static_cast<char>(c - 'A' + 'a') : c; }

/// Lowercase candidate substrings of a tool name to match verbs against:
/// each separator-delimited segment AND its camelCase sub-words.
///
/// Both are needed. The sub-words catch `writeFile` / `WRITEFile`; the whole
/// segment catches `WRITEfile` and `write2file`, where no local rule can tell
/// where the verb ends ("WRITEfile" splits as "writ" + "efile").
std::vector<std::string> nameCandidates(const std::string& name) {
    std::vector<std::string> candidates;
    std::string segment;   // separator-delimited, e.g. "writefile"
    std::string subToken;  // camelCase sub-word, e.g. "write"

    auto flushSubToken = [&]() {
        if (!subToken.empty()) {
            candidates.push_back(subToken);
            subToken.clear();
        }
    };
    auto flushSegment = [&]() {
        flushSubToken();
        if (!segment.empty()) {
            candidates.push_back(segment);
            segment.clear();
        }
    };

    for (size_t i = 0; i < name.size(); ++i) {
        const char ch = name[i];

        if (!isAsciiAlnum(ch)) { // separator: _, -, ., /, space, non-ASCII byte …
            flushSegment();
            continue;
        }

        if (!subToken.empty()) {
            const char prev = name[i - 1];
            const bool caseBoundary =
                isAsciiUpper(ch) && (isAsciiLower(prev) ||
                                     (isAsciiUpper(prev) && i + 1 < name.size() &&
                                      isAsciiLower(name[i + 1])));
            // A digit boundary splits too, so "write2file" also yields "write".
            const bool digitBoundary = isAsciiDigit(ch) != isAsciiDigit(prev);
            if (caseBoundary || digitBoundary) {
                flushSubToken();
            }
        }

        subToken.push_back(toAsciiLower(ch));
        segment.push_back(toAsciiLower(ch));
    }
    flushSegment();

    return candidates;
}

bool namePromisesMutation(const std::string& name) {
    const std::vector<std::string> candidates = nameCandidates(name);

    // Nothing classifiable (punctuation-only, or an all-non-ASCII name) —
    // the name can't corroborate the server's read-only claim, so distrust it.
    if (candidates.empty()) {
        return true;
    }

    for (const auto& candidate : candidates) {
        for (const auto& verb : mutatingVerbs()) {
            if (candidate == verb) {
                return true;
            }
            if (verb.size() >= kMinPrefixVerbLength && candidate.size() > verb.size() &&
                candidate.compare(0, verb.size(), verb) == 0) {
                return true;
            }
        }
    }
    return false;
}

} // namespace

bool mcpToolRequiresConfirmation(const std::string& toolName, const json& annotations) {
    // An unnamed tool can't be classified — treat it as unproven.
    if (toolName.empty()) {
        return true;
    }

    // No annotations object at all -> the server proved nothing -> confirm.
    if (!annotations.is_object()) {
        return true;
    }

    const auto it = annotations.find("readOnlyHint");
    // A non-boolean readOnlyHint (e.g. the string "true") is not a proof.
    if (it == annotations.end() || !it->is_boolean() || !it->get<bool>()) {
        return true;
    }

    // Read-only claim present — reject it if the name says otherwise.
    return namePromisesMutation(toolName);
}

// ---- MCPToolSchema ----

bool MCPToolSchema::requiresConfirmation() const {
    return mcpToolRequiresConfirmation(name, annotations);
}

ToolInfo MCPToolSchema::toToolInfo(const std::string& serverName) const {
    ToolInfo info;
    info.name = "mcp_" + serverName + "_" + name;
    info.description = "[MCP:" + serverName + "] " + description;
    info.atomic = true;
    info.mcpServer = serverName;
    info.mcpToolName = name;

    // MCP names are server-chosen, so they can never be gated by a static
    // allowlist. Gate here, at the source, so every consumer of toToolInfo()
    // inherits the fail-closed verdict.
    info.policy = requiresConfirmation() ? ToolPolicy::CONFIRM : ToolPolicy::ALLOW;

    // Convert JSON Schema properties to ToolParameter list
    if (inputSchema.contains("properties")) {
        auto required = inputSchema.value("required", json::array());
        for (auto& [paramName, paramSchema] : inputSchema["properties"].items()) {
            ToolParameter param;
            param.name = paramName;
            param.description = paramSchema.value("description", "");

            // Map JSON Schema types to ToolParamType
            std::string typeStr = paramSchema.value("type", "string");
            if (typeStr == "string")       param.type = ToolParamType::STRING;
            else if (typeStr == "integer") param.type = ToolParamType::INTEGER;
            else if (typeStr == "number")  param.type = ToolParamType::NUMBER;
            else if (typeStr == "boolean") param.type = ToolParamType::BOOLEAN;
            else if (typeStr == "array")   param.type = ToolParamType::ARRAY;
            else if (typeStr == "object")  param.type = ToolParamType::OBJECT;

            // Check if parameter is required
            param.required = false;
            for (const auto& req : required) {
                if (req.get<std::string>() == paramName) {
                    param.required = true;
                    break;
                }
            }

            info.parameters.push_back(std::move(param));
        }
    }

    return info;
}

// ---- StdioTransport platform impl ----

#ifdef _WIN32

struct StdioTransport::Impl {
    HANDLE childStdinWrite = INVALID_HANDLE_VALUE;
    HANDLE childStdoutRead = INVALID_HANDLE_VALUE;
    PROCESS_INFORMATION procInfo = {};
    bool running = false;

    ~Impl() {
        cleanup();
    }

    void cleanup() {
        if (childStdinWrite != INVALID_HANDLE_VALUE) {
            CloseHandle(childStdinWrite);
            childStdinWrite = INVALID_HANDLE_VALUE;
        }
        if (childStdoutRead != INVALID_HANDLE_VALUE) {
            CloseHandle(childStdoutRead);
            childStdoutRead = INVALID_HANDLE_VALUE;
        }
        if (running) {
            TerminateProcess(procInfo.hProcess, 1);
            WaitForSingleObject(procInfo.hProcess, 5000);
            CloseHandle(procInfo.hProcess);
            CloseHandle(procInfo.hThread);
            running = false;
        }
    }

    bool launch(const std::string& cmdLine,
                const std::map<std::string, std::string>& envVars) {
        SECURITY_ATTRIBUTES sa;
        sa.nLength = sizeof(SECURITY_ATTRIBUTES);
        sa.bInheritHandle = TRUE;
        sa.lpSecurityDescriptor = nullptr;

        HANDLE childStdinRead, childStdoutWrite;

        if (!CreatePipe(&childStdinRead, &childStdinWrite, &sa, 0)) return false;
        SetHandleInformation(childStdinWrite, HANDLE_FLAG_INHERIT, 0);

        if (!CreatePipe(&childStdoutRead, &childStdoutWrite, &sa, 0)) {
            CloseHandle(childStdinRead);
            return false;
        }
        SetHandleInformation(childStdoutRead, HANDLE_FLAG_INHERIT, 0);

        STARTUPINFOA si = {};
        si.cb = sizeof(si);
        si.hStdInput = childStdinRead;
        si.hStdOutput = childStdoutWrite;
        si.hStdError = GetStdHandle(STD_ERROR_HANDLE);
        si.dwFlags |= STARTF_USESTDHANDLES;

        // Build merged environment block if overrides provided
        std::string envBlock;
        LPVOID lpEnv = nullptr;
        if (!envVars.empty()) {
            // Enumerate current process environment and merge overrides
            LPCH envStrings = GetEnvironmentStrings();
            std::map<std::string, std::string> merged;
            if (envStrings) {
                LPCH p = envStrings;
                while (*p) {
                    std::string entry(p);
                    size_t eq = entry.find('=');
                    if (eq != std::string::npos && eq > 0)
                        merged[entry.substr(0, eq)] = entry.substr(eq + 1);
                    p += strlen(p) + 1;
                }
                FreeEnvironmentStrings(envStrings);
            }
            for (const auto& kv : envVars)
                merged[kv.first] = kv.second;
            for (const auto& kv : merged)
                envBlock += kv.first + "=" + kv.second + '\0';
            envBlock += '\0';
            lpEnv = envBlock.empty() ? nullptr : static_cast<LPVOID>(&envBlock[0]);
        }

        std::string mutableCmd(cmdLine);
        BOOL ok = CreateProcessA(
            nullptr,
            mutableCmd.data(),
            nullptr, nullptr,
            TRUE, 0,
            lpEnv, nullptr,
            &si, &procInfo
        );

        CloseHandle(childStdinRead);
        CloseHandle(childStdoutWrite);

        if (!ok) return false;
        running = true;
        return true;
    }

    void writeLine(const std::string& line) {
        std::string data = line + "\n";
        DWORD written;
        WriteFile(childStdinWrite, data.c_str(), static_cast<DWORD>(data.size()), &written, nullptr);
        FlushFileBuffers(childStdinWrite);
    }

    std::string readLine(int timeoutMs) {
        std::string line;
        char ch;
        DWORD bytesRead;
        const DWORD pollIntervalMs = 10;
        DWORD elapsed = 0;

        while (true) {
            DWORD available = 0;
            if (!PeekNamedPipe(childStdoutRead, nullptr, 0, nullptr, &available, nullptr)) {
                break; // pipe broken or closed
            }
            if (available > 0) {
                if (!ReadFile(childStdoutRead, &ch, 1, &bytesRead, nullptr) || bytesRead == 0) {
                    break;
                }
                if (ch == '\n') break;
                if (ch != '\r') line += ch;
            } else {
                if (elapsed >= static_cast<DWORD>(timeoutMs)) {
                    throw std::runtime_error("MCP server read timeout after " +
                                             std::to_string(timeoutMs / 1000) + "s");
                }
                Sleep(pollIntervalMs);
                elapsed += pollIntervalMs;
            }
        }
        return line;
    }

    bool isAlive() const {
        if (!running) return false;
        DWORD exitCode;
        GetExitCodeProcess(procInfo.hProcess, &exitCode);
        return exitCode == STILL_ACTIVE;
    }
};

#else // POSIX

struct StdioTransport::Impl {
    pid_t pid = -1;
    int stdinFd = -1;
    int stdoutFd = -1;
    bool running = false;

    ~Impl() {
        cleanup();
    }

    void cleanup() {
        if (stdinFd >= 0) { close(stdinFd); stdinFd = -1; }
        if (stdoutFd >= 0) { close(stdoutFd); stdoutFd = -1; }
        if (running && pid > 0) {
            kill(pid, SIGTERM);
            int status;
            // Wait up to 5 seconds
            for (int i = 0; i < 50; ++i) {
                if (waitpid(pid, &status, WNOHANG) != 0) break;
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
            }
            // Force kill if still alive
            if (waitpid(pid, &status, WNOHANG) == 0) {
                kill(pid, SIGKILL);
                waitpid(pid, &status, 0);
            }
            running = false;
        }
    }

    bool launch(const std::string& cmdLine,
                const std::map<std::string, std::string>& envVars) {
        int stdinPipe[2], stdoutPipe[2];
        if (pipe(stdinPipe) != 0) return false;
        if (pipe(stdoutPipe) != 0) {
            close(stdinPipe[0]); close(stdinPipe[1]);
            return false;
        }

        pid = fork();
        if (pid < 0) {
            close(stdinPipe[0]); close(stdinPipe[1]);
            close(stdoutPipe[0]); close(stdoutPipe[1]);
            return false;
        }

        if (pid == 0) {
            // Child
            dup2(stdinPipe[0], STDIN_FILENO);
            dup2(stdoutPipe[1], STDOUT_FILENO);
            close(stdinPipe[0]); close(stdinPipe[1]);
            close(stdoutPipe[0]); close(stdoutPipe[1]);

            // Apply environment overrides before exec
            for (const auto& kv : envVars)
                setenv(kv.first.c_str(), kv.second.c_str(), 1 /*overwrite*/);

            execl("/bin/sh", "sh", "-c", cmdLine.c_str(), nullptr);
            _exit(127);
        }

        // Parent
        close(stdinPipe[0]);
        close(stdoutPipe[1]);
        stdinFd = stdinPipe[1];
        stdoutFd = stdoutPipe[0];
        running = true;
        return true;
    }

    void writeLine(const std::string& line) {
        std::string data = line + "\n";
        const char* buf = data.c_str();
        size_t remaining = data.size();
        while (remaining > 0) {
            ssize_t written = write(stdinFd, buf, remaining);
            if (written < 0) {
                if (errno == EINTR) continue;
                break; // unrecoverable write error
            }
            buf += written;
            remaining -= static_cast<size_t>(written);
        }
    }

    std::string readLine(int timeoutMs) {
        std::string line;
        char ch;
        auto deadline = std::chrono::steady_clock::now() +
                        std::chrono::milliseconds(timeoutMs);

        while (true) {
            auto now = std::chrono::steady_clock::now();
            if (now >= deadline) {
                throw std::runtime_error("MCP server read timeout after " +
                                         std::to_string(timeoutMs / 1000) + "s");
            }
            auto remaining = std::chrono::duration_cast<std::chrono::microseconds>(
                deadline - now).count();

            fd_set readfds;
            FD_ZERO(&readfds);
            FD_SET(stdoutFd, &readfds);

            struct timeval tv;
            tv.tv_sec  = remaining / 1000000;
            tv.tv_usec = remaining % 1000000;

            int ret = select(stdoutFd + 1, &readfds, nullptr, nullptr, &tv);
            if (ret < 0) {
                if (errno == EINTR) continue; // interrupted by signal, retry
                break; // unexpected select error
            }
            if (ret == 0) {
                throw std::runtime_error("MCP server read timeout after " +
                                         std::to_string(timeoutMs / 1000) + "s");
            }

            ssize_t n = read(stdoutFd, &ch, 1);
            if (n <= 0) break;
            if (ch == '\n') break;
            if (ch != '\r') line += ch;
        }
        return line;
    }

    bool isAlive() const {
        if (!running || pid <= 0) return false;
        int status;
        return waitpid(pid, &status, WNOHANG) == 0;
    }
};

#endif

// ---- StdioTransport ----

StdioTransport::StdioTransport(const std::string& command, int timeout, bool debug)
    : command_(command), timeout_(timeout), debug_(debug), impl_(std::make_unique<Impl>()) {}

StdioTransport::StdioTransport(const std::string& command, const std::vector<std::string>& args,
                               int timeout, bool debug)
    : command_(command), args_(args), timeout_(timeout), debug_(debug),
      impl_(std::make_unique<Impl>()) {}

StdioTransport::StdioTransport(const std::string& command, const std::vector<std::string>& args,
                               const std::map<std::string, std::string>& env,
                               int timeout, bool debug)
    : command_(command), args_(args), envVars_(env), timeout_(timeout), debug_(debug),
      impl_(std::make_unique<Impl>()) {}

StdioTransport::~StdioTransport() = default;

StdioTransport::StdioTransport(StdioTransport&& other) noexcept = default;
StdioTransport& StdioTransport::operator=(StdioTransport&& other) noexcept = default;

bool StdioTransport::connect() {
    if (impl_->running) return true;

    // Build command line, quoting arguments that contain spaces
    auto quoteArg = [](const std::string& arg) -> std::string {
        if (arg.find(' ') != std::string::npos) {
            return "\"" + arg + "\"";
        }
        return arg;
    };
    std::string cmdLine;
    if (args_.empty()) {
        cmdLine = command_;
    } else {
        cmdLine = command_;
        for (const auto& arg : args_) {
            cmdLine += " " + quoteArg(arg);
        }
    }

    if (debug_) {
        std::cerr << "[MCP] Starting server: " << cmdLine << std::endl;
    }

    if (!impl_->launch(cmdLine, envVars_)) {
        return false;
    }

    // Brief pause to catch immediate crashes
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    if (!impl_->isAlive()) {
        impl_->cleanup();
        return false;
    }

    return true;
}

void StdioTransport::disconnect() {
    impl_->cleanup();
}

json StdioTransport::sendRequest(const std::string& method, const json& params) {
    if (!impl_->running) {
        throw std::runtime_error("Transport not connected");
    }

    if (!impl_->isAlive()) {
        impl_->running = false;
        throw std::runtime_error("MCP server process died");
    }

    // Build JSON-RPC request
    json request = {
        {"jsonrpc", "2.0"},
        {"id", requestId_++},
        {"method", method},
        {"params", params}
    };

    if (debug_) {
        std::cerr << "[MCP] Sending: " << request.dump(2) << std::endl;
    }

    // Send request
    impl_->writeLine(request.dump());

    // Read response (blocking up to timeout_ seconds)
    std::string responseLine = impl_->readLine(timeout_ * 1000);
    if (responseLine.empty()) {
        if (!impl_->isAlive()) {
            impl_->running = false;
            throw std::runtime_error("MCP server process died while waiting for response");
        }
        throw std::runtime_error("Server closed connection");
    }

    try {
        json response = json::parse(responseLine);
        if (debug_) {
            std::cerr << "[MCP] Received: " << response.dump(2) << std::endl;
        }
        return response;
    } catch (const json::parse_error& e) {
        throw std::runtime_error(std::string("Invalid JSON response from MCP server: ") + e.what());
    }
}

bool StdioTransport::isConnected() const {
    return impl_->running && impl_->isAlive();
}

// ---- MCPClient ----

MCPClient::MCPClient(const std::string& name, std::unique_ptr<MCPTransport> transport, bool debug)
    : name_(name), transport_(std::move(transport)), debug_(debug) {}

MCPClient MCPClient::fromCommand(const std::string& name, const std::string& command,
                                  int timeout, bool debug) {
    auto transport = std::make_unique<StdioTransport>(command, timeout, debug);
    return MCPClient(name, std::move(transport), debug);
}

MCPClient MCPClient::fromConfig(const std::string& name, const json& config,
                                 int timeout, bool debug) {
    if (!config.contains("command")) {
        throw std::invalid_argument("Config must include 'command' field");
    }

    std::string command = config["command"].get<std::string>();
    std::vector<std::string> args;
    if (config.contains("args")) {
        for (const auto& arg : config["args"]) {
            args.push_back(arg.get<std::string>());
        }
    }

    std::map<std::string, std::string> envVars;
    if (config.contains("env") && config["env"].is_object()) {
        for (const auto& kv : config["env"].items()) {
            envVars[kv.key()] = kv.value().get<std::string>();
        }
    }

    auto transport = std::make_unique<StdioTransport>(command, args, envVars, timeout, debug);

    return MCPClient(name, std::move(transport), debug);
}

MCPClient::~MCPClient() {
    disconnect();
}

MCPClient::MCPClient(MCPClient&&) noexcept = default;
MCPClient& MCPClient::operator=(MCPClient&&) noexcept = default;

bool MCPClient::connect() {
    lastError_.clear();

    try {
        if (!transport_->connect()) {
            lastError_ = "Failed to establish transport connection to '" + name_ + "'";
            return false;
        }
    } catch (const std::exception& e) {
        lastError_ = std::string("Transport error for '") + name_ + "': " + e.what();
        return false;
    }

    try {
        // Send initialize request
        json response = transport_->sendRequest("initialize", {
            {"protocolVersion", "1.0.0"},
            {"clientInfo", {
                {"name", "GAIA C++ MCP Client"},
                {"version", "0.1.0"}
            }},
            {"capabilities", json::object()}
        });

        if (response.contains("error")) {
            auto error = response["error"];
            lastError_ = "Initialization failed: " + error.value("message", "Unknown error");
            return false;
        }

        auto result = response.value("result", json::object());
        serverInfo_ = result.value("serverInfo", json::object());

        if (debug_) {
            std::cerr << "[MCP] Connected to '" << name_ << "' - "
                      << serverInfo_.value("name", "Unknown") << std::endl;
        }
        return true;

    } catch (const std::exception& e) {
        lastError_ = std::string("Error during initialization: ") + e.what();
        disconnect();
        return false;
    }
}

void MCPClient::disconnect() {
    if (transport_) {
        transport_->disconnect();
    }
    cachedTools_.reset();
}

bool MCPClient::isConnected() const {
    return transport_ && transport_->isConnected();
}

std::vector<MCPToolSchema> MCPClient::listTools(bool refresh) {
    if (cachedTools_.has_value() && !refresh) {
        return cachedTools_.value();
    }

    json response = transport_->sendRequest("tools/list");

    if (response.contains("error")) {
        return {};
    }

    auto result = response.value("result", json::object());
    auto toolsData = result.value("tools", json::array());

    std::vector<MCPToolSchema> tools;
    for (const auto& toolJson : toolsData) {
        MCPToolSchema tool;
        tool.name = toolJson.value("name", "");
        tool.description = toolJson.value("description", "");
        tool.inputSchema = toolJson.value("inputSchema", json::object());

        // A missing or non-object `annotations` degrades to {} — which the
        // classifier reads as "unproven", not as "trusted".
        json rawAnnotations = toolJson.value("annotations", json::object());
        if (rawAnnotations.is_object()) {
            tool.annotations = std::move(rawAnnotations);
        }

        tools.push_back(std::move(tool));
    }

    cachedTools_ = tools;
    return tools;
}

json MCPClient::callTool(const std::string& toolName, const json& arguments) {
    if (!isConnected()) {
        throw std::runtime_error("Not connected to MCP server '" + name_ + "'");
    }

    if (debug_) {
        std::cerr << "[MCP] Calling tool: " << toolName << std::endl;
        std::cerr << "[MCP] Arguments: " << arguments.dump(2) << std::endl;
    }

    json response = transport_->sendRequest("tools/call", {
        {"name", toolName},
        {"arguments", arguments}
    });

    if (response.contains("error")) {
        auto error = response["error"];
        return json{{"error", error.value("message", "Unknown error")}};
    }

    json result = response.value("result", json::object());

    if (debug_) {
        std::cerr << "[MCP] Tool " << toolName << " completed." << std::endl;
    }

    return result;
}

} // namespace gaia
