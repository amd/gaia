// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// A long-running child process for tests: started with its own environment and
// working folder, stdout/stderr sent to files, and always killed on destruction
// (with everything it started), so a failed assertion can't leak a server that
// hangs CI. Windows puts it in a kill-on-close job; Linux also sets
// PDEATHSIG, so it dies even if the test runner itself is killed.

#pragma once

#include <chrono>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <cwchar>
#include <cwctype>
#else
#include <fcntl.h>
#include <signal.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#ifdef __linux__
#include <sys/prctl.h>
#endif
extern char** environ;
#endif

namespace testsupport {

/// Environment changes for the child: a value sets the variable, nullopt removes it.
using EnvOverrides = std::map<std::string, std::optional<std::string>>;

class ChildProcess {
public:
    ChildProcess(const std::vector<std::string>& argv, const EnvOverrides& env, const std::string& cwd,
                 const std::string& stdoutPath, const std::string& stderrPath) {
        if (argv.empty()) throw std::invalid_argument("ChildProcess needs a program");
        start(argv, env, cwd, stdoutPath, stderrPath);
    }

    ~ChildProcess() { kill(); }

    ChildProcess(const ChildProcess&) = delete;
    ChildProcess& operator=(const ChildProcess&) = delete;

    /// True while the child has not exited.
    bool running() { return !reap(0); }

    /// Wait up to timeoutMs for the child to exit on its own. True when it did.
    bool waitExit(int timeoutMs) { return reap(timeoutMs); }

    /// Exit code once the child has exited, else -1.
    int exitCode() const { return exitCode_; }

    /// Kill the child and everything it started, and reap it.
    void kill() {
#ifdef _WIN32
        if (job_) {
            TerminateJobObject(job_, 1);
            if (process_) WaitForSingleObject(process_, 10000);
            reap(0);
            CloseHandle(job_);
            job_ = nullptr;
        }
        if (process_) {
            CloseHandle(process_);
            process_ = nullptr;
        }
#else
        if (pid_ > 0 && !exited_) {
            ::kill(-pid_, SIGKILL);
            ::kill(pid_, SIGKILL);
            int status = 0;
            if (waitpid(pid_, &status, 0) == pid_) recordStatus(status);
            exited_ = true;
        }
#endif
    }

private:
#ifdef _WIN32
    static std::wstring widen(const std::string& s) {
        if (s.empty()) return L"";
        const int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0);
        std::wstring w(static_cast<size_t>(n), L'\0');
        MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), w.data(), n);
        return w;
    }

    static std::wstring upper(std::wstring s) {
        for (auto& c : s) c = static_cast<wchar_t>(std::towupper(c));
        return s;
    }

    // Quoting that CommandLineToArgvW and the MSVC runtime undo exactly.
    static std::wstring quote(const std::wstring& a) {
        if (!a.empty() && a.find_first_of(L" \t\"") == std::wstring::npos) return a;
        std::wstring out = L"\"";
        size_t slashes = 0;
        for (wchar_t c : a) {
            if (c == L'\\') {
                ++slashes;
                continue;
            }
            if (c == L'"') {
                out.append(slashes * 2 + 1, L'\\');
            } else {
                out.append(slashes, L'\\');
            }
            slashes = 0;
            out += c;
        }
        out.append(slashes * 2, L'\\');
        out += L'"';
        return out;
    }

    static HANDLE openInheritable(const std::wstring& path, bool write) {
        SECURITY_ATTRIBUTES sa{sizeof(SECURITY_ATTRIBUTES), nullptr, TRUE};
        HANDLE h = CreateFileW(path.c_str(), write ? GENERIC_WRITE : GENERIC_READ,
                               FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, &sa,
                               write ? CREATE_ALWAYS : OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (h == INVALID_HANDLE_VALUE) {
            throw std::runtime_error("ChildProcess: can't open a std stream file (error " +
                                     std::to_string(GetLastError()) + ")");
        }
        return h;
    }

    void start(const std::vector<std::string>& argv, const EnvOverrides& env, const std::string& cwd,
               const std::string& stdoutPath, const std::string& stderrPath) {
        std::wstring cmd;
        for (const auto& a : argv) cmd += (cmd.empty() ? L"" : L" ") + quote(widen(a));

        // The parent's environment with the overrides applied, sorted as Windows expects.
        std::map<std::wstring, std::pair<std::wstring, std::wstring>> vars;  // UPPER(name) -> (name, value)
        if (wchar_t* block = GetEnvironmentStringsW()) {
            for (const wchar_t* p = block; *p; p += wcslen(p) + 1) {
                const std::wstring entry(p);
                const size_t eq = entry.find(L'=', 1);  // "=C:=C:\..." names start with '='
                if (eq == std::wstring::npos) continue;
                const std::wstring name = entry.substr(0, eq);
                vars[upper(name)] = {name, entry.substr(eq + 1)};
            }
            FreeEnvironmentStringsW(block);
        }
        for (const auto& [name, value] : env) {
            const std::wstring w = widen(name);
            if (value) {
                vars[upper(w)] = {w, widen(*value)};
            } else {
                vars.erase(upper(w));
            }
        }
        std::wstring envBlock;
        for (const auto& [key, nv] : vars) envBlock += nv.first + L"=" + nv.second + L'\0';
        envBlock += L'\0';

        HANDLE in = openInheritable(L"NUL", false);
        HANDLE out = openInheritable(widen(stdoutPath), true);
        HANDLE err = openInheritable(widen(stderrPath), true);

        // Hand the child exactly these three handles, not every inheritable one we hold.
        SIZE_T size = 0;
        InitializeProcThreadAttributeList(nullptr, 1, 0, &size);
        std::vector<char> attrBuf(size);
        auto attrs = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attrBuf.data());
        HANDLE handles[3] = {in, out, err};
        const bool attrsOk = InitializeProcThreadAttributeList(attrs, 1, 0, &size) &&
                             UpdateProcThreadAttribute(attrs, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, handles,
                                                       sizeof(handles), nullptr, nullptr);

        STARTUPINFOEXW si{};
        si.StartupInfo.cb = sizeof(si);
        si.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        si.StartupInfo.hStdInput = in;
        si.StartupInfo.hStdOutput = out;
        si.StartupInfo.hStdError = err;
        si.lpAttributeList = attrsOk ? attrs : nullptr;

        job_ = CreateJobObjectW(nullptr, nullptr);
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if (job_) SetInformationJobObject(job_, JobObjectExtendedLimitInformation, &limits, sizeof(limits));

        PROCESS_INFORMATION pi{};
        const std::wstring wcwd = widen(cwd);
        const DWORD flags = CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT |
                            (attrsOk ? EXTENDED_STARTUPINFO_PRESENT : 0);
        const BOOL ok = CreateProcessW(nullptr, cmd.data(), nullptr, nullptr, TRUE, flags, envBlock.data(),
                                       wcwd.empty() ? nullptr : wcwd.c_str(), &si.StartupInfo, &pi);
        const DWORD createError = GetLastError();
        if (attrsOk) DeleteProcThreadAttributeList(attrs);
        CloseHandle(in);
        CloseHandle(out);
        CloseHandle(err);
        if (!ok) {
            if (job_) CloseHandle(job_);
            job_ = nullptr;
            throw std::runtime_error("ChildProcess: CreateProcess failed for " + argv[0] + " (error " +
                                     std::to_string(createError) + ")");
        }
        if (!job_ || !AssignProcessToJobObject(job_, pi.hProcess)) {
            TerminateProcess(pi.hProcess, 1);
            CloseHandle(pi.hThread);
            CloseHandle(pi.hProcess);
            if (job_) CloseHandle(job_);
            job_ = nullptr;
            throw std::runtime_error("ChildProcess: can't put the child in a job, so it could outlive the test");
        }
        ResumeThread(pi.hThread);
        CloseHandle(pi.hThread);
        process_ = pi.hProcess;
    }

    bool reap(int timeoutMs) {
        if (exited_) return true;
        if (!process_) return true;
        if (WaitForSingleObject(process_, static_cast<DWORD>(timeoutMs)) != WAIT_OBJECT_0) return false;
        DWORD code = 0;
        GetExitCodeProcess(process_, &code);
        exitCode_ = static_cast<int>(code);
        exited_ = true;
        return true;
    }

    HANDLE process_ = nullptr;
    HANDLE job_ = nullptr;
#else
    void start(const std::vector<std::string>& argv, const EnvOverrides& env, const std::string& cwd,
               const std::string& stdoutPath, const std::string& stderrPath) {
        std::map<std::string, std::string> vars;
        for (char** e = environ; e && *e; ++e) {
            const std::string entry(*e);
            const size_t eq = entry.find('=');
            if (eq != std::string::npos) vars[entry.substr(0, eq)] = entry.substr(eq + 1);
        }
        for (const auto& [name, value] : env) {
            if (value) {
                vars[name] = *value;
            } else {
                vars.erase(name);
            }
        }
        // Everything the child needs is built before fork(): only async-signal-safe calls after it.
        std::vector<std::string> envStore;
        for (const auto& [k, v] : vars) envStore.push_back(k + "=" + v);
        std::vector<char*> envp, args;
        for (auto& s : envStore) envp.push_back(s.data());
        envp.push_back(nullptr);
        std::vector<std::string> argStore(argv);
        for (auto& s : argStore) args.push_back(s.data());
        args.push_back(nullptr);
        const std::string outPath = stdoutPath, errPath = stderrPath, dir = cwd;

        const pid_t pid = fork();
        if (pid < 0) throw std::runtime_error("ChildProcess: fork failed");
        if (pid == 0) {
            setpgid(0, 0);
#ifdef __linux__
            prctl(PR_SET_PDEATHSIG, SIGKILL);
#endif
            const int in = open("/dev/null", O_RDONLY);
            const int out = open(outPath.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
            const int err = open(errPath.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
            if (in < 0 || out < 0 || err < 0) _exit(125);
            dup2(in, 0);
            dup2(out, 1);
            dup2(err, 2);
            if (!dir.empty() && chdir(dir.c_str()) != 0) _exit(126);
            execve(args[0], args.data(), envp.data());
            _exit(127);
        }
        setpgid(pid, pid);  // also here, so a kill right after start reaches the group
        pid_ = pid;
    }

    void recordStatus(int status) {
        if (WIFEXITED(status)) exitCode_ = WEXITSTATUS(status);
        else if (WIFSIGNALED(status)) exitCode_ = 128 + WTERMSIG(status);
    }

    bool reap(int timeoutMs) {
        if (exited_ || pid_ <= 0) return true;
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeoutMs);
        for (;;) {
            int status = 0;
            const pid_t r = waitpid(pid_, &status, WNOHANG);
            if (r == pid_) {
                recordStatus(status);
                exited_ = true;
                return true;
            }
            if (std::chrono::steady_clock::now() >= deadline) return false;
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
    }

    pid_t pid_ = -1;
#endif
    bool exited_ = false;
    int exitCode_ = -1;
};

}  // namespace testsupport
