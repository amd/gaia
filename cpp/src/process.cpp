// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/process.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <memory>
#include <mutex>
#include <set>
#include <sstream>
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
#   ifdef __APPLE__
#       include <crt_externs.h>
#   else
extern char** environ;
#   endif
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
        // Child process.
        // Its own process group, so a timeout can kill the whole tree. A
        // build or test command spawns children; killing only the shell
        // leaves them running and holding the output pipes open.
        setpgid(0, 0);

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
    setpgid(pid, pid);  // race-free: whichever call wins, the group exists
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
            kill(-pid, SIGKILL);  // the whole group, not just the shell
            kill(pid, SIGKILL);   // backstop if setpgid did not take
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

// ---------------------------------------------------------------------------
// ShellSession
// ---------------------------------------------------------------------------

namespace {

namespace sfs = std::filesystem;

/// Variables that every shell rewrites on its own. Replaying them would make
/// the session drift a little further from the parent on every command.
const std::set<std::string>& volatileEnvNames() {
    static const std::set<std::string> names = {
        "_", "PWD", "OLDPWD", "SHLVL", "PS1", "PS2", "RANDOM", "SECONDS",
        "LINENO", "PROMPT", "CD", "ERRORLEVEL", "CMDCMDLINE", "CMDEXTVERSION",
        "__GAIA_RC", "GAIA_SHELL_STATE",
    };
    return names;
}

bool isValidEnvName(const std::string& name) {
    if (name.empty()) return false;
    if (std::isdigit(static_cast<unsigned char>(name[0]))) return false;
    for (char c : name) {
        if (!(std::isalnum(static_cast<unsigned char>(c)) || c == '_')) {
            return false;
        }
    }
    return true;
}

/// Snapshot the calling process's environment — the baseline a session's
/// overrides are measured against.
std::map<std::string, std::string> captureProcessEnv() {
    std::map<std::string, std::string> out;
#ifdef _WIN32
    LPCH block = GetEnvironmentStringsA();
    if (!block) return out;
    for (LPCH entry = block; *entry != '\0';) {
        const std::string item(entry);
        entry += item.size() + 1;
        const size_t eq = item.find('=');
        if (eq == std::string::npos || eq == 0) continue;  // "=C:" style entries
        out[item.substr(0, eq)] = item.substr(eq + 1);
    }
    FreeEnvironmentStringsA(block);
#else
#   ifdef __APPLE__
    char** env = *_NSGetEnviron();
#   else
    char** env = environ;
#   endif
    for (; env && *env; ++env) {
        const std::string item(*env);
        const size_t eq = item.find('=');
        if (eq == std::string::npos || eq == 0) continue;
        out[item.substr(0, eq)] = item.substr(eq + 1);
    }
#endif
    return out;
}

/// Split one `NAME=VALUE` record. Returns false when the record has no name.
/// Names are not validated here: `ProgramFiles(x86)` is a real Windows
/// variable, and mis-parsing it would corrupt the neighbour it sorts next to.
bool splitEnvRecord(const std::string& record,
                    std::map<std::string, std::string>& out) {
    const size_t eq = record.find('=');
    if (eq == std::string::npos || eq == 0) return false;
    out[record.substr(0, eq)] = record.substr(eq + 1);
    return true;
}

/// Parse the NUL-delimited environment dump the POSIX script emits.
///
/// NUL is the one byte an environment value cannot contain, so the record
/// boundary is unambiguous. Line-delimited `env` output is not: a value
/// containing a newline followed by `SOMETHING=x` is indistinguishable from a
/// second variable, which would let a command's *data* become the session's
/// *configuration*.
std::map<std::string, std::string> parseEnvRecordsNul(const std::string& text) {
    std::map<std::string, std::string> out;
    size_t start = 0;
    while (start < text.size()) {
        const size_t end = text.find('\0', start);
        const size_t stop = (end == std::string::npos) ? text.size() : end;
        splitEnvRecord(text.substr(start, stop - start), out);
        if (end == std::string::npos) break;
        start = end + 1;
    }
    return out;
}

/// Parse cmd.exe `set` output. Windows environment values cannot contain a
/// newline, so one line is exactly one variable and an unparseable line is
/// dropped rather than glued onto its predecessor.
std::map<std::string, std::string> parseEnvLines(const std::string& text) {
    std::map<std::string, std::string> out;
    std::istringstream stream(text);
    std::string line;

    while (std::getline(stream, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        // cmd.exe lists internal "=C:" / "=ExitCode" entries; not variables.
        if (line[0] == '=') continue;
        splitEnvRecord(line, out);
    }
    return out;
}

/// Create a uniquely-named temp file and write `contents` to it.
/// Exclusive creation on both platforms so a pre-planted symlink or file in a
/// shared temp directory cannot be written through.
bool writeTempFile(const std::string& extension,
                   const std::string& contents,
                   std::string& outPath) {
#ifdef _WIN32
    static std::atomic<unsigned> counter{0};

    char tmpDir[MAX_PATH];
    if (GetTempPathA(MAX_PATH, tmpDir) == 0) return false;

    for (int attempt = 0; attempt < 64; ++attempt) {
        std::ostringstream name;
        name << tmpDir << "gaia_shell_" << GetCurrentProcessId() << "_"
             << counter.fetch_add(1) << extension;
        const std::string candidate = name.str();

        HANDLE handle = CreateFileA(candidate.c_str(), GENERIC_WRITE, 0,
                                    nullptr, CREATE_NEW,
                                    FILE_ATTRIBUTE_NORMAL, nullptr);
        if (handle == INVALID_HANDLE_VALUE) continue;

        DWORD written = 0;
        BOOL ok = TRUE;
        if (!contents.empty()) {
            ok = WriteFile(handle, contents.data(),
                           static_cast<DWORD>(contents.size()), &written,
                           nullptr);
        }
        CloseHandle(handle);
        if (!ok) {
            std::remove(candidate.c_str());
            return false;
        }
        outPath = candidate;
        return true;
    }
    return false;
#else
    (void)extension;
    std::error_code ec;
    sfs::path dir = sfs::temp_directory_path(ec);
    if (ec) dir = sfs::path("/tmp");

    std::string tmpl = (dir / "gaia_shell_XXXXXX").string();
    std::vector<char> buf(tmpl.begin(), tmpl.end());
    buf.push_back('\0');

    const int fd = mkstemp(buf.data());
    if (fd < 0) return false;

    size_t offset = 0;
    while (offset < contents.size()) {
        const ssize_t n = write(fd, contents.data() + offset,
                                contents.size() - offset);
        if (n <= 0) {
            close(fd);
            std::remove(buf.data());
            return false;
        }
        offset += static_cast<size_t>(n);
    }
    close(fd);
    outPath = std::string(buf.data());
    return true;
#endif
}

/// Removes the temp files a command needed, however the call unwinds.
class TempFileSet {
public:
    void add(const std::string& path) { paths_.push_back(path); }
    ~TempFileSet() {
        for (const auto& path : paths_) std::remove(path.c_str());
    }

private:
    std::vector<std::string> paths_;
};

std::string readFileContents(const std::string& path) {
    std::ifstream file(path, std::ios::binary);
    if (!file.is_open()) return "";
    std::ostringstream buffer;
    buffer << file.rdbuf();
    return buffer.str();
}

/// Wrap a value in single quotes for POSIX sh.
std::string posixQuote(const std::string& value) {
    std::string out = "'";
    for (char c : value) {
        if (c == '\'') {
            out += "'\\''";
        } else {
            out += c;
        }
    }
    out += "'";
    return out;
}

/// Escape a value for use inside a batch file.
///
/// Only `%` needs escaping. `"` must be left alone: `set "K=V"` takes
/// everything up to the *last* quote on the line, so doubling a quote is not
/// undone — and because the session re-captures and re-emits the value, each
/// command would double it again until the line broke.
std::string batchQuote(const std::string& value) {
    std::string out;
    for (char c : value) {
        if (c == '%') {
            out += "%%";
        } else {
            out += c;
        }
    }
    return out;
}

/// Forward-slash form, which every POSIX shell — including a Git Bash or MSYS
/// shell running on Windows — accepts.
std::string genericPath(const std::string& path) {
    return sfs::path(path).generic_string();
}

const char* kEnvMarker = "---GAIA-ENV---";

} // namespace

struct ShellSession::Impl {
    mutable std::mutex mutex;
    std::string startCwd;
    std::string cwd;
    std::string shell;
    /// True when commands run through a POSIX shell (always on POSIX; on
    /// Windows when a bash/sh was detected). False means a cmd.exe batch file.
    bool posixScript = true;
    std::map<std::string, std::string> baselineEnv;
    std::map<std::string, std::string> overrides;   ///< set or changed by the session
    std::set<std::string> unset;                    ///< removed by the session

    /// Generate the per-command script that restores state, invokes the
    /// command file, and reports the resulting cwd + environment to
    /// `stateFile`.
    ///
    /// The command lives in its own file and is sourced (`.` / `call`) rather
    /// than pasted in here. Pasting lets a command ending in a line
    /// continuation, or an unterminated heredoc, swallow the framework's own
    /// bookkeeping lines — which both corrupts the reported exit code and
    /// leaks internals into what the model reads back.
    ///
    /// stdin is `/dev/null`: an interactive command (`git commit` opening an
    /// editor, `sudo`, `npm login`) would otherwise sit until the timeout.
    std::string buildScript(const std::string& commandFile,
                            const std::string& stateFile) const {
        std::ostringstream s;
        if (posixScript) {
            s << "cd " << posixQuote(genericPath(cwd)) << " || exit 127\n";
            for (const auto& name : unset) {
                if (!isValidEnvName(name)) continue;
                s << "unset " << name << "\n";
            }
            for (const auto& kv : overrides) {
                if (!isValidEnvName(kv.first)) continue;
                s << kv.first << "=" << posixQuote(kv.second) << "; export "
                  << kv.first << "\n";
            }
            s << ". " << posixQuote(genericPath(commandFile))
              << " < /dev/null\n";
            s << "__gaia_rc=$?\n";
            // awk's ENVIRON gives NUL-delimited records; `env` output cannot
            // be parsed unambiguously (see parseEnvRecordsNul).
            s << "{ pwd; printf '%s\\n' " << posixQuote(kEnvMarker)
              << "; awk 'BEGIN { for (k in ENVIRON) printf \"%s=%s%c\", k, "
                 "ENVIRON[k], 0 }'; } > "
              << posixQuote(genericPath(stateFile)) << " 2>/dev/null\n";
            s << "exit $__gaia_rc\n";
        } else {
            s << "@echo off\r\n";
            s << "cd /d \"" << batchQuote(cwd) << "\"\r\n";
            s << "if errorlevel 1 exit /b 127\r\n";
            for (const auto& name : unset) {
                if (!isValidEnvName(name)) continue;
                s << "set \"" << name << "=\"\r\n";
            }
            for (const auto& kv : overrides) {
                if (!isValidEnvName(kv.first)) continue;
                s << "set \"" << kv.first << "=" << batchQuote(kv.second)
                  << "\"\r\n";
            }
            s << "call \"" << commandFile << "\" <nul\r\n";
            s << "set __GAIA_RC=%ERRORLEVEL%\r\n";
            s << "> \"" << stateFile << "\" (\r\n";
            s << "  cd\r\n";
            s << "  echo " << kEnvMarker << "\r\n";
            s << "  set\r\n";
            s << ")\r\n";
            s << "exit /b %__GAIA_RC%\r\n";
        }
        return s.str();
    }

    /// Absorb the cwd and environment the command left behind.
    void absorbState(const std::string& stateText) {
        const size_t markerPos = stateText.find(kEnvMarker);
        if (markerPos == std::string::npos) return;

        std::string cwdLine = stateText.substr(0, markerPos);
        while (!cwdLine.empty() &&
               (cwdLine.back() == '\n' || cwdLine.back() == '\r')) {
            cwdLine.pop_back();
        }
        // The shell is the authority on where it ended up. On Windows with a
        // Git Bash shell this is an MSYS path (`/c/...`) that the Win32 API
        // does not recognise but the next script — run by the same shell —
        // does, so it is taken as reported rather than validated away.
        if (!cwdLine.empty()) cwd = cwdLine;

        size_t envStart = markerPos + std::strlen(kEnvMarker);
        while (envStart < stateText.size() &&
               (stateText[envStart] == '\n' || stateText[envStart] == '\r')) {
            ++envStart;
        }
        const std::string envText = stateText.substr(envStart);
        const auto captured = posixScript ? parseEnvRecordsNul(envText)
                                          : parseEnvLines(envText);
        if (captured.empty()) return;

        overrides.clear();
        unset.clear();
        for (const auto& kv : captured) {
            if (volatileEnvNames().count(kv.first)) continue;
            auto base = baselineEnv.find(kv.first);
            if (base == baselineEnv.end() || base->second != kv.second) {
                overrides[kv.first] = kv.second;
            }
        }
        for (const auto& kv : baselineEnv) {
            if (volatileEnvNames().count(kv.first)) continue;
            if (captured.find(kv.first) == captured.end()) {
                unset.insert(kv.first);
            }
        }
    }
};

ShellSession::ShellSession(const std::string& startCwd, const std::string& shell)
    : impl_(new Impl()) {
    std::error_code ec;
    sfs::path start = startCwd.empty() ? sfs::current_path(ec)
                                       : sfs::path(startCwd);
    if (!startCwd.empty()) {
        sfs::path resolved = sfs::weakly_canonical(start, ec);
        if (!ec) start = resolved;
    }
    if (start.empty()) start = sfs::path(".");
    impl_->cwd = start.string();
    impl_->startCwd = impl_->cwd;
    impl_->baselineEnv = captureProcessEnv();

#ifdef _WIN32
    // A POSIX shell is used when one was named (the bash agent detects Git
    // Bash / MSYS / WSL); otherwise commands run as a cmd.exe batch script.
    impl_->shell = shell;
    impl_->posixScript = !shell.empty();
#else
    impl_->shell = shell.empty() ? std::string("/bin/sh") : shell;
    impl_->posixScript = true;
#endif
}

ShellSession::~ShellSession() = default;
ShellSession::ShellSession(ShellSession&&) noexcept = default;
ShellSession& ShellSession::operator=(ShellSession&&) noexcept = default;

ShellSession& ShellSession::shared() {
    static ShellSession session;
    return session;
}

ProcessResult ShellSession::run(const std::string& command,
                                int timeoutMs,
                                size_t maxOutputBytes) {
    ProcessResult result;

    if (command.empty()) {
        result.exitCode = -1;
        result.stderr_output = "Empty command";
        return result;
    }

    std::lock_guard<std::mutex> lock(impl_->mutex);

    // Removed however this function exits, including on a throw.
    TempFileSet temps;

    const char* scriptExt = impl_->posixScript ? ".sh" : ".cmd";
    const std::string tempError =
        "ShellSession: could not create a temporary file in the system temp "
        "directory. Check that it exists and is writable.";

    std::string stateFile;
    if (!writeTempFile(".state", "", stateFile)) {
        result.exitCode = -1;
        result.stderr_output = tempError;
        return result;
    }
    temps.add(stateFile);

    // The command gets its own file so it cannot splice into the script's
    // bookkeeping; on Windows it must be a .cmd for `call` to accept it.
    std::string commandFile;
    if (!writeTempFile(scriptExt, command + "\n", commandFile)) {
        result.exitCode = -1;
        result.stderr_output = tempError;
        return result;
    }
    temps.add(commandFile);

    std::string scriptFile;
    if (!writeTempFile(scriptExt, impl_->buildScript(commandFile, stateFile),
                       scriptFile)) {
        result.exitCode = -1;
        result.stderr_output = tempError;
        return result;
    }
    temps.add(scriptFile);

    const std::string invocation =
        impl_->posixScript
            ? impl_->shell + " " + posixQuote(genericPath(scriptFile))
            : "\"" + scriptFile + "\"";

    // No cwd/env arguments: the script applies both inside the child, so the
    // calling process is never mutated and concurrent sessions cannot collide.
    result = ProcessRunner::run(invocation, timeoutMs, "", {}, maxOutputBytes);

    impl_->absorbState(readFileContents(stateFile));

    return result;
}

std::string ShellSession::cwd() const {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    return impl_->cwd;
}

bool ShellSession::setCwd(const std::string& dir) {
    std::error_code ec;
    sfs::path resolved = sfs::weakly_canonical(sfs::path(dir), ec);
    if (ec) resolved = sfs::path(dir);
    if (!sfs::is_directory(resolved, ec)) return false;

    std::lock_guard<std::mutex> lock(impl_->mutex);
    impl_->cwd = resolved.string();
    return true;
}

std::map<std::string, std::string> ShellSession::environment() const {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    return impl_->overrides;
}

void ShellSession::setEnv(const std::string& name, const std::string& value) {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    impl_->overrides[name] = value;
    impl_->unset.erase(name);
}

void ShellSession::reset() {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    impl_->cwd = impl_->startCwd;
    impl_->overrides.clear();
    impl_->unset.clear();
}

} // namespace gaia
