// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/mcp_client.h"

#include <chrono>
#include <cstring>
#include <iostream>
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

// ---- MCPToolSchema ----

ToolInfo MCPToolSchema::toToolInfo(const std::string& serverName) const {
    ToolInfo info;
    info.name = "mcp_" + serverName + "_" + name;
    info.description = "[MCP:" + serverName + "] " + description;
    info.atomic = true;
    info.mcpServer = serverName;
    info.mcpToolName = name;

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

    bool launch(const std::string& cmdLine) {
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

        std::string mutableCmd(cmdLine);
        BOOL ok = CreateProcessA(
            nullptr,
            mutableCmd.data(),
            nullptr, nullptr,
            TRUE, 0,
            nullptr, nullptr,
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

    bool launch(const std::string& cmdLine) {
        int stdinPipe[2], stdoutPipe[2];
        if (pipe(stdinPipe) != 0 || pipe(stdoutPipe) != 0) return false;

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
        ssize_t result = write(stdinFd, data.c_str(), data.size());
        (void)result; // suppress unused warning
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

StdioTransport::~StdioTransport() = default;

StdioTransport::StdioTransport(StdioTransport&& other) noexcept = default;
StdioTransport& StdioTransport::operator=(StdioTransport&& other) noexcept = default;

bool StdioTransport::connect() {
    if (impl_->running) return true;

    // Build command line
    std::string cmdLine;
    if (args_.empty()) {
        cmdLine = command_;
    } else {
        cmdLine = command_;
        for (const auto& arg : args_) {
            cmdLine += " " + arg;
        }
    }

    if (debug_) {
        std::cerr << "[MCP] Starting server: " << cmdLine << std::endl;
    }

    if (!impl_->launch(cmdLine)) {
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

    std::unique_ptr<MCPTransport> transport;
    if (args.empty()) {
        transport = std::make_unique<StdioTransport>(command, timeout, debug);
    } else {
        transport = std::make_unique<StdioTransport>(command, args, timeout, debug);
    }

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
