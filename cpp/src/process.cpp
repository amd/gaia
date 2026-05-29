// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/process.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#ifdef _WIN32
#   ifndef WIN32_LEAN_AND_MEAN
#       define WIN32_LEAN_AND_MEAN
#   endif
#   ifndef NOMINMAX
#       define NOMINMAX
#   endif
#   include <windows.h>
#   include <direct.h>
#   include <io.h>
#else
#   include <cerrno>
#   include <csignal>
#   include <cstdlib>
#   include <fcntl.h>
#   include <sys/types.h>
#   include <sys/wait.h>
#   include <unistd.h>
#endif

namespace gaia {

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

namespace {

/// Read up to maxBytes from a FILE*, returning the captured data.
std::string readStream(FILE* stream, size_t maxBytes) {
    if (!stream) return "";

    std::string output;
    std::array<char, 4096> buffer;
    size_t totalRead = 0;

    while (totalRead < maxBytes) {
        size_t toRead = std::min(buffer.size(),
                                 maxBytes - totalRead);
        size_t bytesRead = std::fread(buffer.data(), 1, toRead, stream);
        if (bytesRead == 0) break;
        output.append(buffer.data(), bytesRead);
        totalRead += bytesRead;
    }

    // Drain remaining data so the process doesn't block on a full pipe,
    // but don't store it.
    while (std::fread(buffer.data(), 1, buffer.size(), stream) > 0) {
        // discard
    }

    return output;
}

#ifdef _WIN32

/// Save current working directory (Windows).
std::string saveCwd() {
    char buf[MAX_PATH];
    if (_getcwd(buf, sizeof(buf))) {
        return std::string(buf);
    }
    return "";
}

/// Change working directory (Windows). Returns true on success.
bool changeCwd(const std::string& dir) {
    return _chdir(dir.c_str()) == 0;
}

#else

/// Save current working directory (POSIX).
std::string saveCwd() {
    char buf[4096];
    if (getcwd(buf, sizeof(buf))) {
        return std::string(buf);
    }
    return "";
}

/// Change working directory (POSIX). Returns true on success.
bool changeCwd(const std::string& dir) {
    return chdir(dir.c_str()) == 0;
}

#endif

/// Set environment variables for the current process.
/// Returns the previous values so they can be restored.
std::map<std::string, std::string> setEnvVars(
        const std::map<std::string, std::string>& env) {
    std::map<std::string, std::string> previous;
    for (const auto& kv : env) {
#ifdef _WIN32
        // Save previous value (use getenv — _dupenv_s is MSVC-only, unavailable in MinGW)
        const char* oldVal = std::getenv(kv.first.c_str());
        if (oldVal) {
            previous[kv.first] = std::string(oldVal);
        } else {
            previous[kv.first] = "";  // mark as absent
        }
        _putenv_s(kv.first.c_str(), kv.second.c_str());
#else
        const char* oldVal = std::getenv(kv.first.c_str());
        if (oldVal) {
            previous[kv.first] = std::string(oldVal);
        } else {
            previous[kv.first] = "";  // mark as absent
        }
        setenv(kv.first.c_str(), kv.second.c_str(), 1);
#endif
    }
    return previous;
}

/// Restore environment variables to their previous values.
void restoreEnvVars(const std::map<std::string, std::string>& previous,
                    const std::map<std::string, std::string>& env) {
    for (const auto& kv : env) {
        auto it = previous.find(kv.first);
        if (it != previous.end() && !it->second.empty()) {
            // Restore previous value
#ifdef _WIN32
            _putenv_s(kv.first.c_str(), it->second.c_str());
#else
            setenv(kv.first.c_str(), it->second.c_str(), 1);
#endif
        } else {
            // Variable was not set before — unset it
#ifdef _WIN32
            _putenv_s(kv.first.c_str(), "");
#else
            unsetenv(kv.first.c_str());
#endif
        }
    }
}

// ---------------------------------------------------------------------------
// Simple (no-timeout) execution via popen
// ---------------------------------------------------------------------------

ProcessResult runSimple(const std::string& command, size_t maxOutputBytes) {
    ProcessResult result;

    // Build command that captures stderr to a temp file so we can read it
    // separately. stdout comes through the pipe.
    std::string stderrFile;
    std::string fullCmd;

#ifdef _WIN32
    // Use a temp file for stderr capture
    char tmpPath[MAX_PATH];
    char tmpFile[MAX_PATH];
    GetTempPathA(MAX_PATH, tmpPath);
    GetTempFileNameA(tmpPath, "gaia", 0, tmpFile);
    stderrFile = tmpFile;
    fullCmd = command + " 2>\"" + stderrFile + "\"";
#else
    // mkstemp for safe temp file creation
    char tmpTemplate[] = "/tmp/gaia_stderr_XXXXXX";
    int fd = mkstemp(tmpTemplate);
    if (fd >= 0) {
        close(fd);
        stderrFile = tmpTemplate;
    }
    fullCmd = command + " 2>\"" + stderrFile + "\"";
#endif

    struct PipeCloser {
        void operator()(FILE* f) const {
#ifdef _WIN32
            if (f) _pclose(f);
#else
            if (f) pclose(f);
#endif
        }
    };

    std::unique_ptr<FILE, PipeCloser> pipe(
#ifdef _WIN32
        _popen(fullCmd.c_str(), "r")
#else
        popen(fullCmd.c_str(), "r")
#endif
    );

    if (!pipe) {
        result.exitCode = -1;
        result.stderr_output = "Failed to execute command: " + command;
        // Clean up temp file
        if (!stderrFile.empty()) std::remove(stderrFile.c_str());
        return result;
    }

    // Read stdout
    result.stdout_output = readStream(pipe.get(), maxOutputBytes);

    // Get exit code
    int status;
#ifdef _WIN32
    status = _pclose(pipe.release());
    result.exitCode = status;
#else
    status = pclose(pipe.release());
    if (WIFEXITED(status)) {
        result.exitCode = WEXITSTATUS(status);
    } else {
        result.exitCode = -1;
    }
#endif

    // Read stderr from temp file
    if (!stderrFile.empty()) {
        FILE* errFile = std::fopen(stderrFile.c_str(), "r");
        if (errFile) {
            result.stderr_output = readStream(errFile, maxOutputBytes);
            std::fclose(errFile);
        }
        std::remove(stderrFile.c_str());
    }

    return result;
}

// ---------------------------------------------------------------------------
// Timeout execution via CreateProcess (Windows) / fork+exec (POSIX)
// ---------------------------------------------------------------------------

#ifdef _WIN32

ProcessResult runWithTimeout(const std::string& command,
                             int timeoutMs,
                             size_t maxOutputBytes) {
    ProcessResult result;

    // Create pipes for stdout and stderr
    SECURITY_ATTRIBUTES sa;
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;
    sa.lpSecurityDescriptor = nullptr;

    HANDLE stdoutReadH = nullptr, stdoutWriteH = nullptr;
    HANDLE stderrReadH = nullptr, stderrWriteH = nullptr;

    if (!CreatePipe(&stdoutReadH, &stdoutWriteH, &sa, 0) ||
        !CreatePipe(&stderrReadH, &stderrWriteH, &sa, 0)) {
        result.exitCode = -1;
        result.stderr_output = "Failed to create pipes";
        return result;
    }

    // Ensure read handles are not inherited
    SetHandleInformation(stdoutReadH, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(stderrReadH, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOA si;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdOutput = stdoutWriteH;
    si.hStdError = stderrWriteH;
    si.hStdInput = GetStdHandle(STD_INPUT_HANDLE);

    PROCESS_INFORMATION pi;
    ZeroMemory(&pi, sizeof(pi));

    // Build command line: cmd /c <command>
    std::string cmdLine = "cmd /c " + command;
    // CreateProcessA needs a mutable char*
    std::vector<char> cmdBuf(cmdLine.begin(), cmdLine.end());
    cmdBuf.push_back('\0');

    BOOL created = CreateProcessA(
        nullptr,
        cmdBuf.data(),
        nullptr,
        nullptr,
        TRUE,          // inherit handles
        0,             // creation flags
        nullptr,       // use parent environment
        nullptr,       // use parent working directory
        &si,
        &pi
    );

    // Close the write ends of the pipes — the child owns them now
    CloseHandle(stdoutWriteH);
    CloseHandle(stderrWriteH);

    if (!created) {
        CloseHandle(stdoutReadH);
        CloseHandle(stderrReadH);
        result.exitCode = -1;
        result.stderr_output = "CreateProcess failed for: " + command;
        return result;
    }

    // Read stdout and stderr from pipes using file descriptors
    // Convert HANDLEs to FILE* for readStream()
    int stdoutFd = _open_osfhandle(reinterpret_cast<intptr_t>(stdoutReadH), 0);
    int stderrFd = _open_osfhandle(reinterpret_cast<intptr_t>(stderrReadH), 0);

    FILE* stdoutFile = nullptr;
    FILE* stderrFile = nullptr;

    if (stdoutFd >= 0) stdoutFile = _fdopen(stdoutFd, "r");
    if (stderrFd >= 0) stderrFile = _fdopen(stderrFd, "r");

    // Read pipes in background threads while waiting for process with timeout.
    // This avoids deadlock: reading before waiting blocks if child keeps stdout
    // open; waiting before reading loses output if pipe buffer fills.
    std::string capturedStdout, capturedStderr;

    std::thread convergentStdout([&]() {
        capturedStdout = readStream(stdoutFile, maxOutputBytes);
    });
    std::thread convergentStderr([&]() {
        capturedStderr = readStream(stderrFile, maxOutputBytes);
    });

    // Wait for process with timeout
    DWORD waitResult = WaitForSingleObject(pi.hProcess,
                                           static_cast<DWORD>(timeoutMs));

    if (waitResult == WAIT_TIMEOUT) {
        result.timedOut = true;
        TerminateProcess(pi.hProcess, 1);
        WaitForSingleObject(pi.hProcess, 5000);  // wait for termination
        result.exitCode = -1;
    } else {
        DWORD exitCodeDw = 0;
        GetExitCodeProcess(pi.hProcess, &exitCodeDw);
        result.exitCode = static_cast<int>(exitCodeDw);
    }

    // Wait for reader threads to finish (process is dead, pipes will EOF)
    convergentStdout.join();
    convergentStderr.join();

    result.stdout_output = std::move(capturedStdout);
    result.stderr_output = std::move(capturedStderr);

    if (stdoutFile) std::fclose(stdoutFile);
    else CloseHandle(stdoutReadH);

    if (stderrFile) std::fclose(stderrFile);
    else CloseHandle(stderrReadH);

    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);

    return result;
}

#else  // POSIX

ProcessResult runWithTimeout(const std::string& command,
                             int timeoutMs,
                             size_t maxOutputBytes) {
    ProcessResult result;

    // Create pipes for stdout and stderr
    int stdoutPipe[2];
    int stderrPipe[2];

    if (pipe(stdoutPipe) != 0 || pipe(stderrPipe) != 0) {
        result.exitCode = -1;
        result.stderr_output = "Failed to create pipes";
        return result;
    }

    pid_t pid = fork();

    if (pid < 0) {
        // Fork failed
        close(stdoutPipe[0]); close(stdoutPipe[1]);
        close(stderrPipe[0]); close(stderrPipe[1]);
        result.exitCode = -1;
        result.stderr_output = "Fork failed: " + std::string(strerror(errno));
        return result;
    }

    if (pid == 0) {
        // Child process
        close(stdoutPipe[0]);  // close read end
        close(stderrPipe[0]);  // close read end

        dup2(stdoutPipe[1], STDOUT_FILENO);
        dup2(stderrPipe[1], STDERR_FILENO);

        close(stdoutPipe[1]);
        close(stderrPipe[1]);

        execl("/bin/sh", "sh", "-c", command.c_str(), static_cast<char*>(nullptr));
        _exit(127);  // exec failed
    }

    // Parent process
    close(stdoutPipe[1]);  // close write end
    close(stderrPipe[1]);  // close write end

    // Set read ends to non-blocking for timeout-aware reading
    fcntl(stdoutPipe[0], F_SETFL, O_NONBLOCK);
    fcntl(stderrPipe[0], F_SETFL, O_NONBLOCK);

    // Poll for output and timeout
    auto startTime = std::chrono::steady_clock::now();
    bool processFinished = false;

    std::string stdoutBuf;
    std::string stderrBuf;
    std::array<char, 4096> readBuf;

    while (!processFinished) {
        // Check timeout
        auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - startTime).count();

        if (elapsed >= timeoutMs) {
            result.timedOut = true;
            kill(pid, SIGKILL);
            waitpid(pid, nullptr, 0);
            break;
        }

        // Try reading stdout
        if (stdoutBuf.size() < maxOutputBytes) {
            ssize_t n = read(stdoutPipe[0], readBuf.data(),
                            std::min(readBuf.size(),
                                     maxOutputBytes - stdoutBuf.size()));
            if (n > 0) {
                stdoutBuf.append(readBuf.data(), static_cast<size_t>(n));
            }
        }

        // Try reading stderr
        if (stderrBuf.size() < maxOutputBytes) {
            ssize_t n = read(stderrPipe[0], readBuf.data(),
                            std::min(readBuf.size(),
                                     maxOutputBytes - stderrBuf.size()));
            if (n > 0) {
                stderrBuf.append(readBuf.data(), static_cast<size_t>(n));
            }
        }

        // Check if child has exited
        int status = 0;
        pid_t w = waitpid(pid, &status, WNOHANG);
        if (w == pid) {
            processFinished = true;
            if (WIFEXITED(status)) {
                result.exitCode = WEXITSTATUS(status);
            } else if (WIFSIGNALED(status)) {
                result.exitCode = -1;
            }
        } else {
            // Brief sleep to avoid busy-waiting
            usleep(1000);  // 1ms
        }
    }

    // Final reads to drain any remaining data
    while (true) {
        ssize_t n = read(stdoutPipe[0], readBuf.data(), readBuf.size());
        if (n <= 0) break;
        if (stdoutBuf.size() < maxOutputBytes) {
            size_t space = maxOutputBytes - stdoutBuf.size();
            stdoutBuf.append(readBuf.data(),
                            std::min(static_cast<size_t>(n), space));
        }
    }
    while (true) {
        ssize_t n = read(stderrPipe[0], readBuf.data(), readBuf.size());
        if (n <= 0) break;
        if (stderrBuf.size() < maxOutputBytes) {
            size_t space = maxOutputBytes - stderrBuf.size();
            stderrBuf.append(readBuf.data(),
                            std::min(static_cast<size_t>(n), space));
        }
    }

    close(stdoutPipe[0]);
    close(stderrPipe[0]);

    result.stdout_output = std::move(stdoutBuf);
    result.stderr_output = std::move(stderrBuf);

    return result;
}

#endif  // _WIN32

}  // anonymous namespace

// ---------------------------------------------------------------------------
// ProcessRunner public API
// ---------------------------------------------------------------------------

ProcessResult ProcessRunner::run(
        const std::string& command,
        int timeoutMs,
        const std::string& cwd,
        const std::map<std::string, std::string>& env,
        size_t maxOutputBytes) {

    // Handle empty command
    if (command.empty()) {
        ProcessResult result;
        result.exitCode = -1;
        result.stderr_output = "Empty command";
        return result;
    }

    // Save and change working directory if requested
    std::string originalCwd;
    if (!cwd.empty()) {
        originalCwd = saveCwd();
        if (!changeCwd(cwd)) {
            ProcessResult result;
            result.exitCode = -1;
            result.stderr_output = "Failed to change to directory: " + cwd;
            return result;
        }
    }

    // Set environment variables
    std::map<std::string, std::string> previousEnv;
    if (!env.empty()) {
        previousEnv = setEnvVars(env);
    }

    // Run the command
    ProcessResult result;
    if (timeoutMs > 0) {
        result = runWithTimeout(command, timeoutMs, maxOutputBytes);
    } else {
        result = runSimple(command, maxOutputBytes);
    }

    // Restore environment variables
    if (!env.empty()) {
        restoreEnvVars(previousEnv, env);
    }

    // Restore working directory
    if (!originalCwd.empty()) {
        changeCwd(originalCwd);
    }

    return result;
}

std::string ProcessRunner::runOrThrow(
        const std::string& command,
        int timeoutMs,
        const std::string& cwd) {
    ProcessResult result = run(command, timeoutMs, cwd);

    if (result.timedOut) {
        throw std::runtime_error(
            "Command timed out after " + std::to_string(timeoutMs) +
            "ms: " + command);
    }

    if (result.exitCode != 0) {
        std::string msg = "Command failed with exit code " +
                          std::to_string(result.exitCode) + ": " + command;
        if (!result.stderr_output.empty()) {
            msg += "\nstderr: " + result.stderr_output;
        }
        throw std::runtime_error(msg);
    }

    return result.stdout_output;
}

} // namespace gaia
