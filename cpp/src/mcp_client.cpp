// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/mcp_client.h"

#include <chrono>
#include <cstdlib>
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

void StdioTransport::sendNotification(const std::string& method, const json& params) {
    if (!impl_->running) {
        throw std::runtime_error("Transport not connected");
    }
    if (!impl_->isAlive()) {
        impl_->running = false;
        throw std::runtime_error("MCP server process died");
    }
    json message = {{"jsonrpc", "2.0"}, {"method", method}};
    if (!params.empty()) message["params"] = params;
    if (debug_) {
        std::cerr << "[MCP] Notifying: " << message.dump(2) << std::endl;
    }
    impl_->writeLine(message.dump());
}

bool StdioTransport::isConnected() const {
    return impl_->running && impl_->isAlive();
}

// ---- MCPTransport default ----

void MCPTransport::sendNotification(const std::string& method, const json& /*params*/) {
    throw std::logic_error("This MCP transport cannot send notifications (tried '" + method +
                           "'). Override MCPTransport::sendNotification in the transport.");
}

// ---- Env expansion ----

std::string expandMcpEnvVars(const std::string& value, const std::string& context) {
    std::string out;
    out.reserve(value.size());
    size_t i = 0;
    while (i < value.size()) {
        if (value[i] != '$' || i + 1 >= value.size() || value[i + 1] != '{') {
            out.push_back(value[i++]);
            continue;
        }
        const size_t close = value.find('}', i + 2);
        if (close == std::string::npos) {
            throw std::invalid_argument(context + " has an unterminated '${' in \"" + value +
                                        "\". Write it as ${VAR_NAME}.");
        }
        const std::string var = value.substr(i + 2, close - i - 2);
        if (var.empty()) {
            throw std::invalid_argument(context + " has an empty '${}' reference. "
                                        "Write it as ${VAR_NAME}.");
        }
        const char* resolved = std::getenv(var.c_str());  // NOLINT(concurrency-mt-unsafe)
        if (resolved == nullptr) {
            throw std::invalid_argument(
                context + " references the environment variable " + var +
                ", which is not set. Set " + var +
                " before starting GAIA, or replace ${" + var + "} in the MCP config "
                "with the literal value.");
        }
        out += resolved;
        i = close + 1;
    }
    return out;
}

// ---- HttpTransport ----

namespace {

/// Raw JSON-RPC payloads from a server can be large; keep error text readable.
std::string snippet(const std::string& s) {
    constexpr size_t kMax = 300;
    if (s.size() <= kMax) return s;
    size_t cut = kMax;
    while (cut > 0 && (static_cast<unsigned char>(s[cut]) & 0xC0) == 0x80) --cut;
    return s.substr(0, cut) + "... (truncated)";
}

bool startsWith(const std::string& s, const char* prefix) {
    return s.rfind(prefix, 0) == 0;
}

std::string lowerAscii(std::string s) {
    for (char& c : s) c = toAsciiLower(c);
    return s;
}

/// True when `message` is the reply to `requestId`. An error with a null id is
/// the server rejecting the request itself, so it counts as the reply too.
bool isReplyTo(const json& message, int requestId) {
    if (!message.is_object()) return false;
    auto it = message.find("id");
    if (it != message.end() && it->is_number_integer()) {
        return it->get<long long>() == requestId;
    }
    return (it == message.end() || it->is_null()) && message.contains("error") &&
           !message.contains("method");
}

/// Incremental SSE reader: collects `data:` lines into events and keeps the
/// first event that is the reply to the request.
class SseReplyReader {
public:
    SseReplyReader(int requestId, std::string url) : requestId_(requestId), url_(std::move(url)) {}

    /// @return false once the reply has been found (stop reading).
    bool feed(const char* data, size_t len) {
        buffer_.append(data, len);
        size_t pos;
        while (!reply_ && (pos = buffer_.find('\n')) != std::string::npos) {
            std::string line = buffer_.substr(0, pos);
            buffer_.erase(0, pos + 1);
            if (!line.empty() && line.back() == '\r') line.pop_back();
            processLine(line);
        }
        return !reply_.has_value();
    }

    /// End of stream: an event without its terminating blank line still counts.
    void finish() {
        if (reply_) return;
        if (!buffer_.empty()) {
            std::string line = buffer_;
            buffer_.clear();
            if (!line.empty() && line.back() == '\r') line.pop_back();
            processLine(line);
        }
        dispatch();
    }

    const std::optional<json>& reply() const { return reply_; }

private:
    void processLine(const std::string& line) {
        if (line.empty()) {
            dispatch();
            return;
        }
        if (startsWith(line, "data:")) {
            std::string payload = line.substr(5);
            if (!payload.empty() && payload.front() == ' ') payload.erase(0, 1);
            if (!data_.empty()) data_ += '\n';
            data_ += payload;
        }
        // `event:`, `id:`, `retry:` and `:` comments carry no JSON-RPC message.
    }

    void dispatch() {
        if (data_.empty()) return;
        std::string payload;
        payload.swap(data_);
        json message;
        try {
            message = json::parse(payload);
        } catch (const json::parse_error& e) {
            throw std::runtime_error("MCP server at " + url_ +
                                     " sent an SSE event that is not valid JSON (" + e.what() +
                                     "): " + snippet(payload) +
                                     ". Check that the URL points at an MCP endpoint.");
        }
        if (isReplyTo(message, requestId_)) reply_ = std::move(message);
    }

    int requestId_;
    std::string url_;
    std::string buffer_;
    std::string data_;
    std::optional<json> reply_;
};

} // namespace

HttpTransport::HttpTransport(std::string url, HttpHeaders headers, int timeout, bool debug)
    : url_(std::move(url)), timeout_(timeout), debug_(debug) {
    if (!startsWith(url_, "http://") && !startsWith(url_, "https://")) {
        throw std::invalid_argument("MCP HTTP transport needs an http:// or https:// URL, got \"" +
                                    url_ + "\".");
    }
    if (timeout_ <= 0) {
        throw std::invalid_argument("MCP HTTP transport timeout must be > 0 seconds, got " +
                                    std::to_string(timeout_) + ".");
    }
    HttpClientConfig cfg;
    cfg.timeoutSec = timeout_;
    cfg.connectTimeoutSec = timeout_;
    cfg.defaultHeaders = std::move(headers);
    cfg.debug = debug_;
    http_ = std::make_unique<HttpClient>(cfg);
}

HttpTransport::~HttpTransport() = default;

bool HttpTransport::connect() {
    connected_ = true;
    return true;
}

void HttpTransport::disconnect() {
    connected_ = false;
    sessionId_.clear();
    protocolVersion_.clear();
}

bool HttpTransport::isConnected() const {
    return connected_;
}

HttpHeaders HttpTransport::requestHeaders() const {
    HttpHeaders headers = {
        {"Accept", "application/json, text/event-stream"},
        {"Content-Type", "application/json"},
    };
    if (!sessionId_.empty()) headers["Mcp-Session-Id"] = sessionId_;
    if (!protocolVersion_.empty()) headers["MCP-Protocol-Version"] = protocolVersion_;
    return headers;
}

void HttpTransport::rethrowHttpError(const HttpError& e, const std::string& method) {
    const int status = e.status();
    std::string remedy;
    if (status == 404 && !sessionId_.empty()) {
        // Spec: 404 on a session id means the server dropped the session.
        sessionId_.clear();
        protocolVersion_.clear();
        connected_ = false;
        remedy = "The server no longer knows this MCP session; reconnect to start a new one.";
    } else if (status == 401 || status == 403) {
        remedy = "Check the credentials in the server's \"headers\" (e.g. Authorization) "
                 "in the MCP config.";
    } else if (status == 404 || status == 405) {
        remedy = "Check that \"url\" in the MCP config is the server's MCP endpoint.";
    } else if (status >= 500) {
        remedy = "The MCP server failed; check its logs.";
    } else if (status == 0) {
        remedy = "Check that the MCP server is running and that \"url\" in the MCP "
                 "config is correct.";
    } else {
        remedy = "Check the MCP server config for this URL.";
    }
    // e.what() already names the URL and the status.
    throw std::runtime_error("MCP '" + method + "' failed: " + e.what() + " " + remedy);
}

json HttpTransport::sendRequest(const std::string& method, const json& params) {
    if (!connected_) {
        throw std::runtime_error("MCP HTTP transport for " + url_ + " is not connected");
    }
    if (method == "initialize") {
        // A new session starts without the previous one's headers.
        sessionId_.clear();
        protocolVersion_.clear();
    }

    const int id = requestId_++;
    const json request = {{"jsonrpc", "2.0"}, {"id", id}, {"method", method}, {"params", params}};
    if (debug_) {
        std::cerr << "[MCP] POST " << url_ << ": " << request.dump(2) << std::endl;
    }

    std::string raw;
    SseReplyReader sse(id, url_);
    HttpResponse response;
    try {
        response = http_->postStreaming(
            url_, request.dump(),
            [&](const char* data, size_t len) {
                raw.append(data, len);
                return sse.feed(data, len);
            },
            requestHeaders(), timeout_, timeout_);
    } catch (const HttpError& e) {
        rethrowHttpError(e, method);
    }

    const std::string contentType = lowerAscii(response.header("Content-Type"));
    json reply;
    if (contentType.find("text/event-stream") != std::string::npos) {
        sse.finish();
        if (!sse.reply()) {
            throw std::runtime_error(
                "MCP server at " + url_ + " closed the SSE stream without answering '" +
                method + "' (request id " + std::to_string(id) +
                "). Resuming a stream with Last-Event-ID is not supported.");
        }
        reply = *sse.reply();
    } else if (contentType.find("application/json") != std::string::npos) {
        json body;
        try {
            body = json::parse(raw);
        } catch (const json::parse_error& e) {
            throw std::runtime_error("MCP server at " + url_ + " answered '" + method +
                                     "' with invalid JSON (" + e.what() + "): " + snippet(raw));
        }
        if (body.is_array()) {
            for (auto& message : body) {
                if (isReplyTo(message, id)) {
                    reply = std::move(message);
                    break;
                }
            }
        } else if (isReplyTo(body, id)) {
            reply = std::move(body);
        }
        if (reply.is_null()) {
            throw std::runtime_error("MCP server at " + url_ + " answered '" + method +
                                     "' without a JSON-RPC reply to request id " +
                                     std::to_string(id) + ": " + snippet(raw));
        }
    } else {
        throw std::runtime_error(
            "MCP server at " + url_ + " answered '" + method + "' with HTTP " +
            std::to_string(response.status) + " and Content-Type \"" +
            response.header("Content-Type") +
            "\"; expected application/json or text/event-stream. Check that \"url\" in "
            "the MCP config is the server's MCP endpoint.");
    }

    if (method == "initialize") {
        sessionId_ = response.header("Mcp-Session-Id");
        const json result = reply.value("result", json::object());
        if (result.is_object() && result.contains("protocolVersion") &&
            result["protocolVersion"].is_string()) {
            protocolVersion_ = result["protocolVersion"].get<std::string>();
        }
    }

    if (debug_) {
        std::cerr << "[MCP] Received: " << reply.dump(2) << std::endl;
    }
    return reply;
}

void HttpTransport::sendNotification(const std::string& method, const json& params) {
    if (!connected_) {
        throw std::runtime_error("MCP HTTP transport for " + url_ + " is not connected");
    }
    json message = {{"jsonrpc", "2.0"}, {"method", method}};
    if (!params.empty()) message["params"] = params;
    if (debug_) {
        std::cerr << "[MCP] POST " << url_ << ": " << message.dump(2) << std::endl;
    }

    HttpResponse response;
    try {
        response = http_->post(url_, message.dump(), requestHeaders(), timeout_, timeout_);
    } catch (const HttpError& e) {
        rethrowHttpError(e, method);
    }

    // Spec answer is 202 with no body; a JSON-RPC error body is a rejection.
    if (response.body.find_first_not_of(" \t\r\n") == std::string::npos) return;
    json reply = json::parse(response.body, nullptr, /*allow_exceptions=*/false);
    if (reply.is_object() && reply.contains("error")) {
        const json& error = reply["error"];
        const std::string detail =
            error.is_object() ? error.value("message", error.dump()) : error.dump();
        throw std::runtime_error("MCP server at " + url_ + " rejected '" + method +
                                 "' (HTTP " + std::to_string(response.status) + "): " + detail);
    }
}

// ---- MCPClient ----

// Same version the Python client requests (src/gaia/mcp/protocol.py).
constexpr const char* kMcpProtocolVersion = "2025-11-25";

MCPClient::MCPClient(const std::string& name, std::unique_ptr<MCPTransport> transport, bool debug)
    : name_(name), transport_(std::move(transport)), debug_(debug) {}

MCPClient MCPClient::fromCommand(const std::string& name, const std::string& command,
                                  int timeout, bool debug) {
    auto transport = std::make_unique<StdioTransport>(command, timeout, debug);
    return MCPClient(name, std::move(transport), debug);
}

MCPClient MCPClient::fromConfig(const std::string& name, const json& config,
                                 int timeout, bool debug) {
    if (!config.is_object()) {
        throw std::invalid_argument("MCP server '" + name + "' config must be a JSON object");
    }

    std::string type;
    if (config.contains("type")) {
        if (!config["type"].is_string()) {
            throw std::invalid_argument("MCP server '" + name + "': \"type\" must be a string");
        }
        type = config["type"].get<std::string>();
        if (type != "stdio" && type != "http" && type != "streamable-http") {
            throw std::invalid_argument(
                "MCP server '" + name + "' uses the '" + type + "' transport, which GAIA's C++ "
                "MCP client does not speak. Use \"stdio\" or \"http\" (Streamable HTTP).");
        }
    } else if (config.contains("url")) {
        if (config.contains("command")) {
            throw std::invalid_argument(
                "MCP server '" + name + "' has both \"command\" and \"url\"; set \"type\" to "
                "\"stdio\" or \"http\" to say which transport to use.");
        }
        type = "http";
    } else {
        type = "stdio";
    }

    if (type == "http" || type == "streamable-http") {
        if (!config.contains("url") || !config["url"].is_string() ||
            config["url"].get<std::string>().empty()) {
            throw std::invalid_argument(
                "MCP server '" + name + "' is an HTTP server but has no \"url\". Add the "
                "server's MCP endpoint, e.g. {\"type\": \"http\", \"url\": "
                "\"http://localhost:8000/mcp\"}.");
        }
        HttpHeaders headers;
        if (config.contains("headers")) {
            if (!config["headers"].is_object()) {
                throw std::invalid_argument("MCP server '" + name +
                                            "': \"headers\" must be an object of strings");
            }
            for (const auto& kv : config["headers"].items()) {
                if (!kv.value().is_string()) {
                    throw std::invalid_argument("MCP server '" + name + "': headers[\"" +
                                                kv.key() + "\"] must be a string");
                }
                headers[kv.key()] = expandMcpEnvVars(
                    kv.value().get<std::string>(),
                    "MCP server '" + name + "' header \"" + kv.key() + "\"");
            }
        }
        auto transport = std::make_unique<HttpTransport>(config["url"].get<std::string>(),
                                                         std::move(headers), timeout, debug);
        return MCPClient(name, std::move(transport), debug);
    }

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
            {"protocolVersion", kMcpProtocolVersion},
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

        transport_->sendNotification("notifications/initialized");

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
