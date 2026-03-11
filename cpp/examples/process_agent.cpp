// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Process Analyst — PC Narrator & Process Intelligence (GAIA C++ Agent)
// Two-phase workflow:
//   DETECT: Auto-analyzes system on startup (processes, services, anomalies)
//   ACT:    User takes action referencing labeled findings (A1, B2, C1)
//
// Tools: system_snapshot, list_processes, kill_process, explain_item,
//        restart_process, restart_service, quarantine_item
//
// Usage:
//   ./process_agent
//   [auto-analysis runs, then action menu appears]
//
// Requirements:
//   - Windows (Win32 APIs and PowerShell for system diagnostics)
//   - LLM server running at http://localhost:8000/api/v1

#define NOMINMAX
#include <windows.h>
#include <psapi.h>
#include <tlhelp32.h>
#include <shellapi.h>
#include <shobjidl_core.h>

#include <algorithm>
#include <atomic>
#include <array>
#include <cctype>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <queue>
#include <set>
#include <sstream>
#include <string>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

#include <gaia/agent.h>
#include <gaia/clean_console.h>
#include <gaia/console.h>
#include <gaia/types.h>

namespace color = gaia::color;

// ---------------------------------------------------------------------------
// Shell helper — PowerShell wrapper and input validation
// ---------------------------------------------------------------------------
static std::string runShell(const std::string& command) {
    std::string fullCmd = "powershell -NoProfile -NonInteractive -Command \"& { "
                          + command + " }\" 2>&1";
    std::array<char, 4096> buffer;
    std::string result;

    struct PipeCloser {
        void operator()(FILE* f) const { if (f) _pclose(f); }
    };
    std::unique_ptr<FILE, PipeCloser> pipe(_popen(fullCmd.c_str(), "r"));

    if (!pipe) return R"({"error": "Failed to execute command"})";
    while (fgets(buffer.data(), static_cast<int>(buffer.size()), pipe.get()) != nullptr) {
        result += buffer.data();
    }
    return result.empty()
        ? R"json({"status": "completed", "output": "(no output)"})json"
        : result;
}

static bool isSafeShellArg(const std::string& arg) {
    if (arg.empty()) return false;
    const std::string dangerous = ";|&`$(){}<>\"'\n\r";
    for (char c : arg) {
        if (dangerous.find(c) != std::string::npos) return false;
    }
    return true;
}

// Validate a Windows file path for quarantine operations
static bool isSafePath(const std::string& path) {
    if (path.size() < 3) return false;
    if (!std::isalpha(static_cast<unsigned char>(path[0])) ||
        path[1] != ':' || path[2] != '\\') return false;
    const std::string dangerous = "\"`;|&{}<>$";
    for (char c : path) {
        if (dangerous.find(c) != std::string::npos) return false;
    }
    return true;
}

// ---------------------------------------------------------------------------
// Format bytes as human-readable string
// ---------------------------------------------------------------------------
static std::string formatBytes(uint64_t bytes) {
    const char* units[] = {"B", "KB", "MB", "GB", "TB"};
    int unitIdx = 0;
    auto size = static_cast<double>(bytes);
    while (size >= 1024.0 && unitIdx < 4) {
        size /= 1024.0;
        ++unitIdx;
    }
    char buf[64];
    std::snprintf(buf, sizeof(buf), "%.1f %s", size, units[unitIdx]);
    return buf;
}

// ---------------------------------------------------------------------------
// String conversion helpers
// ---------------------------------------------------------------------------
static std::string wstringToUtf8(const std::wstring& wstr) {
    if (wstr.empty()) return "";
    int size = WideCharToMultiByte(CP_UTF8, 0, wstr.data(),
                                    static_cast<int>(wstr.size()),
                                    nullptr, 0, nullptr, nullptr);
    if (size <= 0) return "";
    std::string result(static_cast<size_t>(size), '\0');
    WideCharToMultiByte(CP_UTF8, 0, wstr.data(),
                        static_cast<int>(wstr.size()),
                        result.data(), size, nullptr, nullptr);
    return result;
}

static std::wstring utf8ToWstring(const std::string& str) {
    if (str.empty()) return L"";
    int size = MultiByteToWideChar(CP_UTF8, 0, str.data(),
                                    static_cast<int>(str.size()),
                                    nullptr, 0);
    if (size <= 0) return L"";
    std::wstring result(static_cast<size_t>(size), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, str.data(),
                        static_cast<int>(str.size()),
                        result.data(), size);
    return result;
}

// ---------------------------------------------------------------------------
// Process kill helpers — shared by kill_process, restart_process, quarantine_item
// ---------------------------------------------------------------------------
struct KillResult {
    int      found       = 0;
    int      terminated  = 0;
    int      failed      = 0;
    uint64_t memoryFreed = 0;
    std::string firstPath;   // exe path of first match (for restart_process relaunch)
};

// ---------------------------------------------------------------------------
// Monitor data types — snapshot for diff-based alerting
// ---------------------------------------------------------------------------

struct MonitorSnapshot {
    std::chrono::system_clock::time_point timestamp;

    // Direct system data (from Win32 APIs — no LLM needed)
    int      memoryUsedPercent = 0;
    uint64_t memoryUsedBytes   = 0;
    std::vector<std::pair<std::string, uint64_t>> topProcesses;  // name, totalMemory

    // LLM-classified data (from processQuery "result" key)
    std::string              healthStatus;       // "Healthy" / "Warning" / "Critical"
    std::string              healthDetail;       // LLM's one-sentence explanation
    std::vector<std::string> suspiciousItems;    // raw lines from C. section
    std::string              rawLlmAnswer;
};

struct MonitorAlert {
    enum class Severity { INFO, WARNING, CRITICAL };
    enum class Type {
        MEMORY_SPIKE,       // system-wide memory jump >10 pp
        MEMORY_SURGE,       // single process >500 MB growth
        NEW_PROCESS,        // new entry in top-20 resource consumers
        PROCESS_GONE,       // left top-20
        NEW_SUSPICIOUS,     // LLM flagged new suspicious item
        HEALTH_CHANGED,     // system health status changed
        BASELINE_COMPLETE,  // first scan finished — baseline captured
        SCAN_COMPLETE,      // subsequent scan finished with no diff alerts
        MONITOR_ERROR       // scan or agent failure
    };

    Severity    severity;
    Type        type;
    std::string title;      // short — also used for balloon notification (max 63 chars)
    std::string detail;     // multiline context for console display
    std::chrono::system_clock::time_point timestamp;
};

// Kill all processes matching exeName (case-insensitive, skips self).
// Captures exe path of the first match during the snapshot phase.
static KillResult killProcessesByName(const std::string& exeName) {
    KillResult kr{};
    DWORD selfPid = GetCurrentProcessId();
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snapshot == INVALID_HANDLE_VALUE) return kr;

    struct MatchInfo { DWORD pid; uint64_t memBytes; };
    std::vector<MatchInfo> matches;

    PROCESSENTRY32W pe;
    pe.dwSize = sizeof(pe);
    if (Process32FirstW(snapshot, &pe)) {
        do {
            std::string nm = wstringToUtf8(pe.szExeFile);
            if (_stricmp(nm.c_str(), exeName.c_str()) == 0 &&
                pe.th32ProcessID != selfPid) {
                uint64_t mem = 0;
                HANDLE hProc = OpenProcess(
                    PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, FALSE, pe.th32ProcessID);
                if (hProc) {
                    PROCESS_MEMORY_COUNTERS pmc;
                    if (K32GetProcessMemoryInfo(hProc, &pmc, sizeof(pmc)))
                        mem = pmc.WorkingSetSize;
                    if (kr.firstPath.empty()) {
                        wchar_t pathBuf[MAX_PATH];
                        DWORD pathLen = MAX_PATH;
                        if (QueryFullProcessImageNameW(hProc, 0, pathBuf, &pathLen))
                            kr.firstPath = wstringToUtf8(std::wstring(pathBuf, pathLen));
                    }
                    CloseHandle(hProc);
                }
                matches.push_back({pe.th32ProcessID, mem});
            }
        } while (Process32NextW(snapshot, &pe));
    }
    CloseHandle(snapshot);

    kr.found = static_cast<int>(matches.size());
    for (auto& m : matches) {
        HANDLE hProc = OpenProcess(PROCESS_TERMINATE, FALSE, m.pid);
        if (hProc) {
            if (TerminateProcess(hProc, 1)) { ++kr.terminated; kr.memoryFreed += m.memBytes; }
            else                             { ++kr.failed; }
            CloseHandle(hProc);
        } else {
            ++kr.failed;
        }
    }
    return kr;
}

// Kill all processes whose full exe path matches filePath (case-insensitive).
// More precise than by-name: only kills the specific file being quarantined.
static KillResult killProcessesByPath(const std::string& filePath) {
    KillResult kr{};
    std::string lowerTarget = filePath;
    for (auto& c : lowerTarget)
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));

    DWORD selfPid = GetCurrentProcessId();
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snapshot == INVALID_HANDLE_VALUE) return kr;

    struct MatchInfo { DWORD pid; uint64_t memBytes; };
    std::vector<MatchInfo> matches;

    PROCESSENTRY32W pe;
    pe.dwSize = sizeof(pe);
    if (Process32FirstW(snapshot, &pe)) {
        do {
            if (pe.th32ProcessID == selfPid) continue;
            HANDLE hProc = OpenProcess(
                PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, FALSE, pe.th32ProcessID);
            if (hProc) {
                wchar_t pathBuf[MAX_PATH];
                DWORD pathLen = MAX_PATH;
                if (QueryFullProcessImageNameW(hProc, 0, pathBuf, &pathLen)) {
                    std::string procPath = wstringToUtf8(std::wstring(pathBuf, pathLen));
                    std::string lowerProc = procPath;
                    for (auto& c : lowerProc)
                        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
                    if (lowerProc == lowerTarget) {
                        uint64_t mem = 0;
                        PROCESS_MEMORY_COUNTERS pmc;
                        if (K32GetProcessMemoryInfo(hProc, &pmc, sizeof(pmc)))
                            mem = pmc.WorkingSetSize;
                        matches.push_back({pe.th32ProcessID, mem});
                        if (kr.firstPath.empty()) kr.firstPath = procPath;
                    }
                }
                CloseHandle(hProc);
            }
        } while (Process32NextW(snapshot, &pe));
    }
    CloseHandle(snapshot);

    kr.found = static_cast<int>(matches.size());
    for (auto& m : matches) {
        HANDLE hProc = OpenProcess(PROCESS_TERMINATE, FALSE, m.pid);
        if (hProc) {
            if (TerminateProcess(hProc, 1)) { ++kr.terminated; kr.memoryFreed += m.memBytes; }
            else                             { ++kr.failed; }
            CloseHandle(hProc);
        } else {
            ++kr.failed;
        }
    }
    return kr;
}

// ---------------------------------------------------------------------------
// Parse PowerShell JSON output with error handling
// ---------------------------------------------------------------------------
static gaia::json parsePsJson(const std::string& output) {
    if (output.empty()) return {{"error", "Empty PowerShell output"}};
    try {
        auto jsonStart = output.find_first_of("{[");
        if (jsonStart == std::string::npos)
            return {{"error", "No JSON in output"}, {"raw", output.substr(0, 500)}};
        return gaia::json::parse(output.substr(jsonStart));
    } catch (...) {
        return {{"error", "Failed to parse PowerShell JSON"}, {"raw", output.substr(0, 500)}};
    }
}

// ---------------------------------------------------------------------------
// System data helpers
// ---------------------------------------------------------------------------

static gaia::json getDiskUsageInfo() {
    gaia::json drives = gaia::json::array();
    wchar_t driveStrings[512];
    DWORD len = GetLogicalDriveStringsW(511, driveStrings);
    wchar_t* p = driveStrings;
    while (*p && (p - driveStrings) < static_cast<ptrdiff_t>(len)) {
        UINT driveType = GetDriveTypeW(p);
        if (driveType == DRIVE_FIXED || driveType == DRIVE_REMOVABLE) {
            ULARGE_INTEGER freeAvail, totalBytes, freeBytes;
            if (GetDiskFreeSpaceExW(p, &freeAvail, &totalBytes, &freeBytes)) {
                wchar_t label[256] = {0};
                wchar_t fsName[64] = {0};
                GetVolumeInformationW(p, label, 255, nullptr, nullptr, nullptr, fsName, 63);
                uint64_t total  = totalBytes.QuadPart;
                uint64_t free   = freeBytes.QuadPart;
                uint64_t used   = total - free;
                double usedPct  = total > 0 ? (used * 100.0 / total) : 0.0;
                drives.push_back({
                    {"drive",        wstringToUtf8(std::wstring(p))},
                    {"label",        wstringToUtf8(std::wstring(label))},
                    {"filesystem",   wstringToUtf8(std::wstring(fsName))},
                    {"total_bytes",  total},
                    {"free_bytes",   free},
                    {"used_bytes",   used},
                    {"used_percent", static_cast<int>(usedPct)},
                    {"total_human",  formatBytes(total)},
                    {"free_human",   formatBytes(free)},
                    {"used_human",   formatBytes(used)},
                });
            }
        }
        p += wcslen(p) + 1;
    }
    return drives;
}

static gaia::json getMemoryInfo() {
    MEMORYSTATUSEX memInfo;
    memInfo.dwLength = sizeof(memInfo);
    GlobalMemoryStatusEx(&memInfo);
    return {
        {"total_bytes",     memInfo.ullTotalPhys},
        {"available_bytes", memInfo.ullAvailPhys},
        {"used_bytes",      memInfo.ullTotalPhys - memInfo.ullAvailPhys},
        {"used_percent",    static_cast<int>(memInfo.dwMemoryLoad)},
        {"total_human",     formatBytes(memInfo.ullTotalPhys)},
        {"available_human", formatBytes(memInfo.ullAvailPhys)},
        {"used_human",      formatBytes(memInfo.ullTotalPhys - memInfo.ullAvailPhys)},
    };
}

// Query FileVersionInfo description + company for an exe path (pure Win32, no PowerShell).
static void getFileVersionStrings(const std::wstring& path,
                                  std::string& description, std::string& company) {
    DWORD dummy = 0;
    DWORD size = GetFileVersionInfoSizeW(path.c_str(), &dummy);
    if (size == 0) return;
    std::vector<BYTE> buf(size);
    if (!GetFileVersionInfoW(path.c_str(), 0, size, buf.data())) return;
    // Try common English code page first, then neutral
    const wchar_t* subBlocks[] = {
        L"\\StringFileInfo\\040904B0\\FileDescription",
        L"\\StringFileInfo\\040904E4\\FileDescription",
        L"\\StringFileInfo\\000004B0\\FileDescription",
    };
    const wchar_t* companyBlocks[] = {
        L"\\StringFileInfo\\040904B0\\CompanyName",
        L"\\StringFileInfo\\040904E4\\CompanyName",
        L"\\StringFileInfo\\000004B0\\CompanyName",
    };
    for (auto* sb : subBlocks) {
        wchar_t* val = nullptr; UINT len = 0;
        if (VerQueryValueW(buf.data(), sb, reinterpret_cast<void**>(&val), &len) && len > 1) {
            description = wstringToUtf8(std::wstring(val, len - 1));
            break;
        }
    }
    for (auto* sb : companyBlocks) {
        wchar_t* val = nullptr; UINT len = 0;
        if (VerQueryValueW(buf.data(), sb, reinterpret_cast<void**>(&val), &len) && len > 1) {
            company = wstringToUtf8(std::wstring(val, len - 1));
            break;
        }
    }
}

static gaia::json getTopProcesses(int topN = 20) {
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snapshot == INVALID_HANDLE_VALUE)
        return {{"error", "Failed to create process snapshot"}};

    struct ProcInfo {
        std::string name; DWORD pid; DWORD parentPid;
        uint64_t memoryBytes; std::wstring path;
    };
    std::vector<ProcInfo> procs;

    // Build pid->name map for parent resolution (no OpenProcess needed)
    std::unordered_map<DWORD, std::string> pidNameMap;

    PROCESSENTRY32W pe;
    pe.dwSize = sizeof(pe);
    if (Process32FirstW(snapshot, &pe)) {
        do {
            pidNameMap[pe.th32ProcessID] = wstringToUtf8(pe.szExeFile);
            HANDLE hProc = OpenProcess(
                PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, FALSE, pe.th32ProcessID);
            if (hProc) {
                PROCESS_MEMORY_COUNTERS pmc;
                if (K32GetProcessMemoryInfo(hProc, &pmc, sizeof(pmc))) {
                    std::wstring procPath;
                    wchar_t pathBuf[MAX_PATH];
                    DWORD pathLen = MAX_PATH;
                    if (QueryFullProcessImageNameW(hProc, 0, pathBuf, &pathLen))
                        procPath.assign(pathBuf, pathLen);
                    procs.push_back({wstringToUtf8(pe.szExeFile), pe.th32ProcessID,
                                     pe.th32ParentProcessID, pmc.WorkingSetSize, procPath});
                }
                CloseHandle(hProc);
            }
        } while (Process32NextW(snapshot, &pe));
    }
    CloseHandle(snapshot);

    // Group by exe name, accumulating total memory
    struct GroupedProc {
        std::string name;
        uint64_t totalMemory = 0;
        DWORD parentPid = 0;
        std::string parentName;
        std::wstring path;          // path of first instance (for version info)
        gaia::json pids = gaia::json::array();
    };
    std::map<std::string, GroupedProc> groups;

    for (const auto& p : procs) {
        std::string lowerName = p.name;
        for (auto& ch : lowerName) ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));

        auto& g = groups[lowerName];
        if (g.name.empty()) {
            g.name = p.name;
            g.parentPid = p.parentPid;
            g.path = p.path;
            // Resolve parent name
            if (p.parentPid == 0) {
                g.parentName = "System Idle Process";
            } else if (p.parentPid == 4) {
                g.parentName = "System";
            } else {
                auto it = pidNameMap.find(p.parentPid);
                g.parentName = (it != pidNameMap.end()) ? it->second : "";
            }
        }
        g.totalMemory += p.memoryBytes;
        g.pids.push_back(p.pid);
    }

    // Sort groups by total memory descending
    std::vector<GroupedProc*> sorted;
    for (auto& kv : groups) sorted.push_back(&kv.second);
    std::sort(sorted.begin(), sorted.end(),
              [](const GroupedProc* a, const GroupedProc* b) { return a->totalMemory > b->totalMemory; });

    gaia::json result = gaia::json::array();
    int limit = std::min(static_cast<int>(sorted.size()), topN);
    for (int i = 0; i < limit; ++i) {
        const auto* g = sorted[i];
        gaia::json entry = {
            {"name",               g->name},
            {"instance_count",     static_cast<int>(g->pids.size())},
            {"total_memory_bytes", g->totalMemory},
            {"total_memory_human", formatBytes(g->totalMemory)},
            {"pids",               g->pids},
        };
        if (!g->parentName.empty())
            entry["parent_name"] = g->parentName;
        // Add description + company from file version info
        std::string desc, company;
        if (!g->path.empty()) {
            getFileVersionStrings(g->path, desc, company);
        }
        entry["description"] = desc.empty() ? "Unknown" : desc;
        entry["company"]     = company.empty() ? "Unknown" : company;
        entry["path"]        = g->path.empty() ? "" : wstringToUtf8(g->path);

        // Factual flags for LLM classification
        gaia::json flags = gaia::json::array();
        if (desc.empty() || entry["description"] == "Unknown")
            flags.push_back("unknown_description");
        if (company.empty() || entry["company"] == "Unknown")
            flags.push_back("unknown_company");
        std::string lowerPath = entry["path"].get<std::string>();
        for (auto& c : lowerPath)
            c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        if (lowerPath.find("\\temp\\") != std::string::npos ||
            lowerPath.find("\\downloads\\") != std::string::npos ||
            lowerPath.find("\\appdata\\local\\temp\\") != std::string::npos)
            flags.push_back("temp_path");
        entry["flags"] = flags;

        result.push_back(entry);
    }
    return result;
}

// ---------------------------------------------------------------------------
// Format a JSON process-by-memory array as a human-readable string.
// Expects grouped format from getTopProcesses(): instance_count, total_memory_human, parent_name.
// ---------------------------------------------------------------------------
static std::string formatProcessList(const gaia::json& procs) {
    std::string out;
    char buf[160];
    for (const auto& p : procs) {
        std::string nm = p.value("name", std::string("?"));
        if (p.contains("instance_count") && p["instance_count"].is_number()) {
            int count = p["instance_count"].get<int>();
            std::string mem = p.value("total_memory_human", std::string("?"));
            std::string parent = p.value("parent_name", std::string(""));
            if (count > 1) {
                if (!parent.empty()) {
                    std::snprintf(buf, sizeof(buf), "  %-28s x%-3d %s  <- %s\n",
                        nm.c_str(), count, mem.c_str(), parent.c_str());
                } else {
                    std::snprintf(buf, sizeof(buf), "  %-28s x%-3d %s\n",
                        nm.c_str(), count, mem.c_str());
                }
            } else {
                if (!parent.empty()) {
                    std::snprintf(buf, sizeof(buf), "  %-28s      %s  <- %s\n",
                        nm.c_str(), mem.c_str(), parent.c_str());
                } else {
                    std::snprintf(buf, sizeof(buf), "  %-28s      %s\n",
                        nm.c_str(), mem.c_str());
                }
            }
        }
        out += buf;

        // Show path on a second line
        if (p.contains("path") && p["path"].is_string()) {
            std::string path = p["path"].get<std::string>();
            if (!path.empty())
                out += "    path: " + path + "\n";
        }

        // Show company and description on a second line
        if (p.contains("company") || p.contains("description")) {
            std::string co = p.value("company", "");
            std::string de = p.value("description", "");
            if (!co.empty() || !de.empty()) {
                std::string detail;
                if (!de.empty() && de != "Unknown") detail += de;
                if (!co.empty() && co != "Unknown") {
                    if (!detail.empty()) detail += " | ";
                    detail += co;
                }
                if (!detail.empty())
                    out += "    " + detail + "\n";
            }
        }

        // Show flags if present
        if (p.contains("flags") && p["flags"].is_array() && !p["flags"].empty()) {
            out += "    flags: ";
            bool first = true;
            for (const auto& f : p["flags"]) {
                if (!first) out += ", ";
                out += f.get<std::string>();
                first = false;
            }
            out += "\n";
        }
    }
    return out;
}

// ---------------------------------------------------------------------------
// Monitor — Windows notification (toast + Action Center)
// ---------------------------------------------------------------------------
static HWND         g_notifyHwnd = nullptr;
static NOTIFYICONDATAW g_notifyNid{};
static bool          g_notifyActive = false;

static constexpr UINT WM_TRAYICON = WM_APP + 1;

// {7B3A8E1F-4C2D-4F5E-9A1B-3D6E8F2C7A4B}
static const GUID kMonitorGuid =
    {0x7B3A8E1F, 0x4C2D, 0x4F5E, {0x9A,0x1B,0x3D,0x6E,0x8F,0x2C,0x7A,0x4B}};

static LRESULT CALLBACK notifyWndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    if (msg == WM_TRAYICON && lp == NIN_BALLOONUSERCLICK) {
        HWND console = GetConsoleWindow();
        if (console) {
            ShowWindow(console, SW_RESTORE);
            SetForegroundWindow(console);
        }
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

static void initBalloonNotify() {
    if (g_notifyActive) return;

    // Set AppUserModelID — required for Action Center persistence on Win10/11
    SetCurrentProcessExplicitAppUserModelID(L"AMD.GAIA.SystemMonitor");

    // Register window class with our WndProc so we can handle notification clicks
    static const wchar_t* kClassName = L"GAIAMonitorNotify";
    WNDCLASSEXW wc{};
    wc.cbSize        = sizeof(wc);
    wc.lpfnWndProc   = notifyWndProc;
    wc.hInstance      = GetModuleHandle(nullptr);
    wc.lpszClassName  = kClassName;
    RegisterClassExW(&wc);  // OK if already registered

    g_notifyHwnd = CreateWindowExW(0, kClassName, L"", 0,
                                   0, 0, 0, 0,
                                   HWND_MESSAGE, nullptr,
                                   wc.hInstance, nullptr);
    if (!g_notifyHwnd) return;

    ZeroMemory(&g_notifyNid, sizeof(g_notifyNid));
    g_notifyNid.cbSize           = sizeof(g_notifyNid);
    g_notifyNid.hWnd             = g_notifyHwnd;
    g_notifyNid.uID              = 1;
    g_notifyNid.uFlags           = NIF_ICON | NIF_TIP | NIF_GUID | NIF_MESSAGE | NIF_SHOWTIP;
    g_notifyNid.uCallbackMessage = WM_TRAYICON;
    g_notifyNid.guidItem         = kMonitorGuid;
    g_notifyNid.hIcon            = LoadIcon(nullptr, IDI_WARNING);
    wcscpy_s(g_notifyNid.szTip, L"GAIA System Monitor");
    Shell_NotifyIconW(NIM_ADD, &g_notifyNid);

    // Use version 4 for modern notification behavior
    g_notifyNid.uVersion = NOTIFYICON_VERSION_4;
    Shell_NotifyIconW(NIM_SETVERSION, &g_notifyNid);

    g_notifyActive = true;
}

static void showBalloonNotify(const std::string& title, const std::string& body) {
    if (!g_notifyActive) return;
    g_notifyNid.uFlags      = NIF_INFO | NIF_GUID;
    g_notifyNid.dwInfoFlags = NIIF_WARNING;

    std::wstring wTitle = utf8ToWstring(title);
    std::wstring wBody  = utf8ToWstring(body);
    wcsncpy_s(g_notifyNid.szInfoTitle, wTitle.c_str(), 63);
    wcsncpy_s(g_notifyNid.szInfo,      wBody.c_str(), 255);
    Shell_NotifyIconW(NIM_MODIFY, &g_notifyNid);
}

static void cleanupBalloonNotify() {
    if (!g_notifyActive) return;
    Shell_NotifyIconW(NIM_DELETE, &g_notifyNid);
    if (g_notifyHwnd) DestroyWindow(g_notifyHwnd);
    g_notifyHwnd = nullptr;
    g_notifyActive = false;
    UnregisterClassW(L"GAIAMonitorNotify", GetModuleHandle(nullptr));
}

// ---------------------------------------------------------------------------
// ProcessConsole — unified Conclusion formatter
// ---------------------------------------------------------------------------
// Line detection rules (first match wins):
//   1. Blank line          — suppress consecutive blanks
//   2. Section header      — "A. Processes", "B. Services", "C. Suspicious Items"
//                            → bold heading + gray description + blank line
//   3. Item reference      — ^[A-C]\d+: … (e.g. "A4: cpptools-srv.exe")
//                            → bold cyan tag, bold white name
//   4. Key: Value          — first ":" within 20 chars, alpha key
//                            → bold white key, normal white value
//   5. Numbered item       — ^#. … (e.g. "1. chrome.exe …")
//                            → normal printWrapped (inherits **bold** from LLM)
//   6. Default paragraph   — everything else → gray (dimmed) prose
// ---------------------------------------------------------------------------
class ProcessConsole : public gaia::CleanConsole {
public:
    void printFinalAnswer(const std::string& answer) override {
        if (answer.empty()) return;

        std::string cleanAnswer = answer;
        if (!answer.empty() && answer.front() == '{') {
            try {
                auto j = nlohmann::json::parse(answer);
                if (j.is_object()) {
                    if (j.contains("answer") && j["answer"].is_string())
                        cleanAnswer = j["answer"].get<std::string>();
                    else if (j.contains("thought") && j["thought"].is_string())
                        cleanAnswer = j["thought"].get<std::string>();
                }
            } catch (...) {}
        }

        // ---- Conclusion banner ----
        std::cout << std::endl;
        std::cout << color::GREEN
                  << "  ========================================================================================"
                  << color::RESET << std::endl;
        std::cout << color::GREEN << color::BOLD
                  << "  Conclusion" << color::RESET << std::endl;
        std::cout << color::GREEN
                  << "  ========================================================================================"
                  << color::RESET << std::endl;

        // ---- Section header table ----
        struct SectionInfo {
            const char* label;
            const char* description;
        };
        static const SectionInfo kSections[] = {
            {"A. Processes",
             "Top resource consumers grouped by name, sorted by memory and CPU usage."},
            {"B. Services",
             "System services with high memory usage (>200 MB) or error/degraded status."},
            {"C. Suspicious Items",
             "Unsigned binaries, unknown publishers, or processes running from unexpected locations."},
        };

        // ---- Line-by-line rendering ----
        std::istringstream stream(cleanAnswer);
        std::string line;
        bool lastWasBlank = true;

        while (std::getline(stream, line)) {
            if (!line.empty() && line.back() == '\r') line.pop_back();

            // --- Rule 1: Blank line (suppress consecutive) ---
            if (line.empty()) {
                if (!lastWasBlank) { std::cout << std::endl; lastWasBlank = true; }
                continue;
            }

            // --- Rule 2: Section header ---
            const SectionInfo* section = nullptr;
            for (const auto& s : kSections) {
                if (line.rfind(s.label, 0) == 0) { section = &s; break; }
            }
            if (section) {
                if (!lastWasBlank) std::cout << std::endl;
                std::cout << "  " << color::BOLD << color::WHITE
                          << section->label << color::RESET << std::endl;
                std::cout << "  " << color::GRAY
                          << section->description << color::RESET << std::endl;
                std::cout << std::endl;
                lastWasBlank = true;
                continue;
            }

            // --- Rule 3: Item reference title  (A4: cpptools-srv.exe) ---
            if (line.size() >= 4 && line[0] >= 'A' && line[0] <= 'C' &&
                std::isdigit(static_cast<unsigned char>(line[1]))) {
                auto colon = line.find(": ");
                if (colon != std::string::npos && colon <= 4) {
                    if (!lastWasBlank) std::cout << std::endl;
                    std::string tag  = line.substr(0, colon);
                    std::string rest = line.substr(colon + 2);
                    std::cout << "  " << color::BOLD << color::CYAN
                              << tag << color::RESET << "  "
                              << color::BOLD << color::WHITE
                              << rest << color::RESET << std::endl;
                    lastWasBlank = false;
                    continue;
                }
            }

            // --- Rule 4: Key: Value line ---
            if (isKeyValueLine(line)) {
                auto colon = line.find(':');
                std::string key = line.substr(0, colon + 1);   // includes ':'
                std::string val = (colon + 1 < line.size()) ? line.substr(colon + 1) : "";
                std::cout << "  " << color::BOLD << color::WHITE
                          << key << color::RESET;
                if (!val.empty()) {
                    printWrapped(val, 88 - key.size(), 2 + key.size());
                } else {
                    std::cout << std::endl;
                }
                lastWasBlank = false;
                continue;
            }

            // --- Rule 5: Numbered item  (1. chrome.exe …) ---
            if (std::isdigit(static_cast<unsigned char>(line[0]))) {
                auto dot = line.find(". ");
                if (dot != std::string::npos && dot <= 3) {
                    std::cout << "  ";
                    printWrapped(line, 88, 2);
                    lastWasBlank = false;
                    continue;
                }
            }

            // --- Rule 6: Default paragraph (dimmed) ---
            std::cout << "  ";
            printWrapped(line, 88, 2, color::GRAY);
            lastWasBlank = false;
        }

        // ---- Bottom border ----
        std::cout << color::GREEN
                  << "  ========================================================================================"
                  << color::RESET << std::endl;
    }

private:
    /// Detect "Key: Value" lines — first colon within 20 chars, key is
    /// alpha/digits/spaces, char after colon is space or end-of-line.
    static bool isKeyValueLine(const std::string& line) {
        auto p = line.find(':');
        if (p == std::string::npos || p == 0 || p > 20) return false;
        // char after colon must be space or end
        if (p + 1 < line.size() && line[p + 1] != ' ') return false;
        // first char must be alpha
        if (!std::isalpha(static_cast<unsigned char>(line[0]))) return false;
        // key must be only alpha, digit, space
        for (size_t i = 0; i < p; ++i) {
            char c = line[i];
            if (!std::isalpha(static_cast<unsigned char>(c)) &&
                !std::isdigit(static_cast<unsigned char>(c)) && c != ' ')
                return false;
        }
        return true;
    }
};

// ---------------------------------------------------------------------------
// ProcessAgent — Process Analyst
// ---------------------------------------------------------------------------
class ProcessAgent : public gaia::Agent {
public:
    explicit ProcessAgent(const std::string& modelId)
        : Agent(makeConfig(modelId)) {
        setOutputHandler(std::make_unique<ProcessConsole>());
        init();
    }

    explicit ProcessAgent(const gaia::AgentConfig& config)
        : Agent(config) {
        setOutputHandler(std::make_unique<ProcessConsole>());
        init();
    }

protected:
    std::string getSystemPrompt() const override {
        return R"(You are an expert Process Analyst running locally on AMD hardware via the GAIA framework. You make every process, service, and network connection on this PC understandable in plain English. You detect problems, explain what is normal vs. suspicious, and take targeted action.

## ANALYSIS CRITERIA

You receive system data pre-filtered by resource usage (top consumers by memory/CPU). Each process includes factual metadata: company, description, path, and a flags array. YOU classify each item.

### A. Processes (resource consumers)
Select the most significant resource consumers from the data. Consider:
- Memory usage relative to total system RAM
- Instance count (many instances of the same exe = noteworthy)
- Whether the resource usage is expected for that application type
Tag each: [NORMAL] expected usage, [HIGH] unexpectedly high, [SUSPICIOUS] see C

### B. Services (problematic)
Report services that need attention:
- Status is not "OK" (Degraded, Error, etc.)
- Memory > 200 MB for a background service
If no services are problematic: "All services running normally"

### C. Suspicious Items
Flag processes with security concerns. The flags array provides factual signals — use them as INPUT to your reasoning, not as final verdicts:
- unknown_company + unknown_description together = STRONG signal
- temp_path (running from Temp/Downloads) = STRONG signal for non-installers
- unknown_company alone for a non-system process = moderate signal
- EXCEPTIONS: svchost.exe, csrss.exe, smss.exe, lsass.exe, System may show empty company — this is NORMAL for Windows system processes
- A process with NO flags can still be suspicious if its behavior is unusual
If no items are suspicious: "None detected"

## OUTPUT FORMAT

Always print all three headers. Number items. Leave a blank line between groups.

A. Processes
1. name.exe (xN) - X.X GB RAM - Description <- parent.exe [TAG]

B. Services
1. ServiceName (Display Name) - X MB - reason

C. Suspicious Items
1. name.exe - C:\path\to\exe - flags: unknown_company, unknown_description

System Health: [Healthy/Warning/Critical] - [one sentence]

## REASONING PROTOCOL

After EVERY tool result, structure your thought using these exact prefixes:

FINDING: <1-2 sentences: key facts and values from the output>
DECISION: <1 sentence: what to do next and WHY>

## ACTION BEHAVIOR

Users reference items by group letter + item number (e.g., "Explain A3", "Stop B1", "Quarantine C2").

- **Explain**: If no item specified, explain ALL items from A, B, and C sections. If specified (e.g. A3), explain just that one. Use explain_item for full details.
- **Stop**: For A-items use kill_process; for B-items use PowerShell Stop-Service. Explain impact and confirm first.
- **Restart**: For A-items use restart_process (kills and relaunches). For B-items use restart_service. Confirm first.
- **Quarantine**: Before calling quarantine_item, you MUST show the user a confirmation summary and wait for explicit YES:
  1. Call explain_item to get full details if not already available.
  2. Present in Key: Value format: Process, Path, Memory, Publisher, Reason (why suspicious).
  3. Ask: Kill [name] and move it to quarantine? This will terminate the process immediately. Reply yes or no.
  4. Wait for user response. If NO, abort. If YES, call quarantine_item with the full file path.
  After the tool runs, report exactly which processes were killed and the quarantine destination path.

## AVAILABLE TOOLS

- `system_snapshot` — CPU, memory, disk, uptime, top 30 processes by memory (with company, description, path, flags), problematic/high-memory services, connection count.
- `list_processes` — Top 30 by memory (with company, description, flags), top 10 by CPU, background process count.
- `kill_process` — Terminate a process by name. ONLY after user confirmation.
- `explain_item` — Full details on any process or service: path, publisher, signer, description, network connections.
- `restart_process` — Kill and relaunch an application. ONLY after user confirmation.
- `restart_service` — Restart a Windows service. ONLY after user confirmation.
- `quarantine_item` — Move a suspicious file to quarantine. ONLY after user confirmation.

## SAFETY

- Destructive tools (kill_process, restart_process, restart_service, quarantine_item) require explicit user confirmation.
- Never skip the confirmation step.
- After any destructive action, report exactly what was done.

## FINAL ANSWER FORMAT

Only provide an "answer" after ALL tool calls are complete. Use Key: Value lines for structured data (not bullet points or tables).

When explaining an item, use this format:
[Label]: [name]
Description: [what it does]
Company: [publisher]
Path: [full path]
Parent: [parent process]
Signer: [certificate info]
Signature: [Valid/Invalid/Unsigned]
Memory: [amount]
CPU time: [seconds]
Started: [timestamp]
Network: [connection summary or "No active connections"]

When reporting action results (kill, restart, quarantine), use:
Action: [what was done]
Target: [process/service name]
Result: [Success/Failed]

[1 sentence summary of what happened]

## GOAL TRACKING

Always set a short `goal` field (3-6 words) describing your current objective.)";
    }

    void registerTools() override {

        // ==================================================================
        // system_snapshot — System overview with services and connections
        // ==================================================================
        toolRegistry().registerTool(
            "system_snapshot",
            "System overview: CPU load, memory, disk, uptime, top processes by memory "
            "(with company, description, path, and factual flags), problematic services, "
            "and network connection count. Use first during analysis.",
            [](const gaia::json& /*args*/) -> gaia::json {
                gaia::json result;
                result["tool"]          = "system_snapshot";
                result["disk"]          = getDiskUsageInfo();
                result["memory"]        = getMemoryInfo();
                result["top_processes"] = getTopProcesses(30);

                std::string psCmd =
                    "$o=@{}; "
                    "$c=Get-CimInstance Win32_Processor; "
                    "$o.cpu=@{load=$c.LoadPercentage;name=[string]$c.Name;"
                    "curMHz=$c.CurrentClockSpeed;maxMHz=$c.MaxClockSpeed}; "
                    "$os=Get-CimInstance Win32_OperatingSystem; "
                    "$o.uptimeHrs=[math]::Round(((Get-Date)-$os.LastBootUpTime).TotalHours,1); "
                    "$o.totalProcessCount=(Get-Process -EA 0).Count; "
                    "$bg=(Get-Process -EA 0|Where-Object{$_.MainWindowHandle -eq 0}).Count; "
                    "$o.backgroundProcessCount=$bg; "
                    "$o.networkConnectionCount=(Get-NetTCPConnection -State Established -EA 0).Count; "
                    "$psvc=Get-CimInstance Win32_Service -EA 0|Where-Object{"
                    "$_.State -eq 'Running' -and ($_.Status -ne 'OK' -or $_.ExitCode -ne 0)}"
                    "|Select-Object Name,DisplayName,Status,ExitCode -First 10; "
                    "$o.problematicServices=@($psvc|ForEach-Object{@{name=$_.Name;"
                    "displayName=[string]$_.DisplayName;status=[string]$_.Status;"
                    "exitCode=$_.ExitCode}}); "
                    "$hmem=Get-CimInstance Win32_Service -EA 0|Where-Object{"
                    "$_.State -eq 'Running' -and $_.ProcessId -gt 0}|ForEach-Object{"
                    "$proc=Get-Process -Id $_.ProcessId -EA 0; "
                    "if($proc -and $proc.WorkingSet64 -gt 200MB){"
                    "@{name=$_.Name;displayName=[string]$_.DisplayName;"
                    "pid=$_.ProcessId;memMB=[math]::Round($proc.WorkingSet64/1MB)}}}; "
                    "$o.highMemoryServices=@($hmem|Where-Object{$_}|Select-Object -First 10); "
                    "$o|ConvertTo-Json -Depth 3 -Compress";

                auto psData = parsePsJson(runShell(psCmd));
                if (!psData.contains("error")) {
                    result["cpu"]                      = psData.value("cpu", gaia::json::object());
                    result["uptime_hours"]             = psData.value("uptimeHrs", 0.0);
                    result["total_process_count"]      = psData.value("totalProcessCount", 0);
                    result["background_process_count"] = psData.value("backgroundProcessCount", 0);
                    result["network_connection_count"] = psData.value("networkConnectionCount", 0);
                    result["problematic_services"]     = psData.value("problematicServices", gaia::json::array());
                    result["high_memory_services"]     = psData.value("highMemoryServices", gaia::json::array());
                } else {
                    result["powershell_error"] = psData;
                }

                // Build command/output for CleanConsole
                result["command"] = "Win32 API: memory, disk, processes + PowerShell: CPU, services, connections";
                {
                    std::string out;
                    char buf[512];

                    if (result.contains("cpu") && result["cpu"].is_object()) {
                        auto& cpu = result["cpu"];
                        std::snprintf(buf, sizeof(buf), "CPU: %d%% load  |  %s  |  %d / %d MHz\n",
                            cpu.value("load", 0),
                            cpu.value("name", std::string("?")).c_str(),
                            cpu.value("curMHz", 0),
                            cpu.value("maxMHz", 0));
                        out += buf;
                    }

                    {
                        auto& mem = result["memory"];
                        std::snprintf(buf, sizeof(buf), "Memory: %s / %s  (%d%% used)\n",
                            mem.value("used_human", std::string("?")).c_str(),
                            mem.value("total_human", std::string("?")).c_str(),
                            mem.value("used_percent", 0));
                        out += buf;
                    }

                    if (result.contains("uptime_hours")) {
                        std::snprintf(buf, sizeof(buf),
                            "Uptime: %.1f hrs  |  Processes: %d  |  Connections: %d\n",
                            result["uptime_hours"].get<double>(),
                            result.value("total_process_count", 0),
                            result.value("network_connection_count", 0));
                        out += buf;
                    }

                    out += "\nDisk:\n";
                    for (const auto& d : result["disk"]) {
                        std::snprintf(buf, sizeof(buf), "  %s  %d%% used  |  %s free of %s\n",
                            d.value("drive", std::string("?")).c_str(),
                            d.value("used_percent", 0),
                            d.value("free_human", std::string("?")).c_str(),
                            d.value("total_human", std::string("?")).c_str());
                        out += buf;
                    }

                    out += "\nTop 30 by memory:\n";
                    out += formatProcessList(result["top_processes"]);

                    bool hasProblematic = result.contains("problematic_services") &&
                        result["problematic_services"].is_array() &&
                        !result["problematic_services"].empty();
                    bool hasHighMem = result.contains("high_memory_services") &&
                        result["high_memory_services"].is_array() &&
                        !result["high_memory_services"].empty();

                    if (hasProblematic) {
                        out += "\nProblematic services:\n";
                        for (const auto& svc : result["problematic_services"]) {
                            std::snprintf(buf, sizeof(buf), "  %s (%s) - Status: %s, ExitCode: %d\n",
                                svc.value("name", std::string("?")).c_str(),
                                svc.value("displayName", std::string("?")).c_str(),
                                svc.value("status", std::string("?")).c_str(),
                                svc.value("exitCode", 0));
                            out += buf;
                        }
                    }
                    if (hasHighMem) {
                        out += "\nHigh-memory services:\n";
                        for (const auto& svc : result["high_memory_services"]) {
                            std::snprintf(buf, sizeof(buf), "  %s (%s) - PID %d - %d MB\n",
                                svc.value("name", std::string("?")).c_str(),
                                svc.value("displayName", std::string("?")).c_str(),
                                svc.value("pid", 0),
                                svc.value("memMB", 0));
                            out += buf;
                        }
                    }
                    if (!hasProblematic && !hasHighMem) {
                        out += "\nServices: all running normally\n";
                    }

                    result["output"] = out;
                }
                return result;
            }, {});

        // ==================================================================
        // list_processes — Full process list by memory and CPU
        // ==================================================================
        toolRegistry().registerTool(
            "list_processes",
            "Full process list: top 30 by memory (with company, description, flags) "
            "and top 10 by CPU time. Also reports total memory state and background process count.",
            [](const gaia::json& /*args*/) -> gaia::json {
                gaia::json result;
                result["tool"]          = "list_processes";
                result["top_by_memory"] = getTopProcesses(30);
                result["memory"]        = getMemoryInfo();

                std::string psCmd =
                    "$procs=Get-Process -EA 0|Sort-Object CPU -Descending|"
                    "Select-Object -First 10 Name,Id,"
                    "@{N='CpuSec';E={[math]::Round($_.CPU,1)}},"
                    "@{N='MemMB';E={[math]::Round($_.WorkingSet64/1MB)}}; "
                    "$bg=(Get-Process -EA 0|Where-Object{$_.MainWindowHandle -eq 0}).Count; "
                    "@{topCpu=@($procs|ForEach-Object{@{name=$_.Name;pid=$_.Id;"
                    "cpuSec=$_.CpuSec;memMB=$_.MemMB}});"
                    "backgroundCount=$bg}|ConvertTo-Json -Depth 3 -Compress";

                auto psData = parsePsJson(runShell(psCmd));
                if (!psData.contains("error")) {
                    result["top_by_cpu"]       = psData.value("topCpu", gaia::json::array());
                    result["background_count"] = psData.value("backgroundCount", 0);
                }

                // Build command/output for CleanConsole
                result["command"] = "Win32 API: top 30 by memory + PowerShell: top 10 by CPU time";
                {
                    std::string out;
                    char buf[256];

                    {
                        auto& mem = result["memory"];
                        std::snprintf(buf, sizeof(buf), "Memory: %s / %s  (%d%% used)",
                            mem.value("used_human", std::string("?")).c_str(),
                            mem.value("total_human", std::string("?")).c_str(),
                            mem.value("used_percent", 0));
                        out += buf;
                    }
                    if (result.contains("background_count")) {
                        std::snprintf(buf, sizeof(buf), "  |  Background: %d\n",
                            result["background_count"].get<int>());
                        out += buf;
                    } else {
                        out += "\n";
                    }

                    out += "\nTop 30 by memory:\n";
                    out += formatProcessList(result["top_by_memory"]);

                    if (result.contains("top_by_cpu") && result["top_by_cpu"].is_array()) {
                        out += "\nTop 10 by CPU time:\n";
                        for (const auto& p : result["top_by_cpu"]) {
                            std::snprintf(buf, sizeof(buf), "  %-28s  %.1f sec  |  %d MB\n",
                                p.value("name", std::string("?")).c_str(),
                                p.value("cpuSec", 0.0),
                                p.value("memMB", 0));
                            out += buf;
                        }
                    }

                    result["output"] = out;
                }
                return result;
            }, {});

        // ==================================================================
        // kill_process — Terminate a process by name
        // ==================================================================
        toolRegistry().registerTool(
            "kill_process",
            "Terminate a running process by name. Reports instances found, terminated "
            "count, and memory freed. ONLY use after explicit user confirmation.",
            [](const gaia::json& args) -> gaia::json {
                std::string name = args.value("name", "");
                if (name.empty()) {
                    return {{"error",   "Process name is required"},
                            {"tool",    "kill_process"},
                            {"command", "Win32 API: TerminateProcess(?)"},
                            {"output",  "Error: process name is required"}};
                }

                for (char c : name) {
                    if (!std::isalnum(static_cast<unsigned char>(c)) &&
                        c != '.' && c != '-' && c != '_') {
                        return {{"error",   "Invalid process name: " + name},
                                {"tool",    "kill_process"},
                                {"command", "Win32 API: TerminateProcess(" + name + ")"},
                                {"output",  "Error: invalid process name '" + name + "'"}};
                    }
                }

                std::string target = name;
                {
                    std::string lower = target;
                    for (auto& ch : lower)
                        ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
                    if (lower.size() < 4 || lower.substr(lower.size() - 4) != ".exe")
                        target += ".exe";
                }

                KillResult kr = killProcessesByName(target);

                if (kr.found == 0) {
                    return {{"tool",    "kill_process"},
                            {"process", name},
                            {"error",   "Process not found: " + name},
                            {"command", "Win32 API: TerminateProcess(" + target + ")"},
                            {"output",  "Process not found: " + name}};
                }

                int      terminated = kr.terminated;
                int      failed     = kr.failed;
                uint64_t totalFreed = kr.memoryFreed;

                std::string killCmd = "Win32 API: TerminateProcess(" + target + ")";
                std::string killOut = "Found " +
                    std::to_string(kr.found) +
                    (kr.found == 1 ? " instance" : " instances") +
                    " of " + target + ", terminated " + std::to_string(terminated);
                if (failed > 0)
                    killOut += " (" + std::to_string(failed) + " failed)";
                killOut += ", freed " + formatBytes(totalFreed);

                return {
                    {"tool",               "kill_process"},
                    {"process",            name},
                    {"instances_found",    kr.found},
                    {"terminated",         terminated},
                    {"failed",             failed},
                    {"memory_freed_bytes", totalFreed},
                    {"memory_freed_human", formatBytes(totalFreed)},
                    {"status",             failed == 0 ? "completed" : "partial"},
                    {"command",            killCmd},
                    {"output",             killOut}
                };
            },
            {{"name", gaia::ToolParamType::STRING, true,
              "Process name to terminate (e.g., 'chrome.exe')"}});

        // ==================================================================
        // explain_item — Explain any process or service in plain English
        // ==================================================================
        toolRegistry().registerTool(
            "explain_item",
            "Get full details on any process or service: path, publisher/signer, "
            "description, parent process, memory and CPU usage, and active network "
            "connections. The LLM uses this to explain what an item is and whether "
            "it is normal.",
            [](const gaia::json& args) -> gaia::json {
                std::string name = args.value("name", "");
                if (name.empty()) {
                    return {{"error",   "Name is required"},
                            {"tool",    "explain_item"},
                            {"command", "explain_item(?)"},
                            {"output",  "Error: name is required"}};
                }
                // Validate: alphanumeric, dots, hyphens, underscores (service names are clean)
                for (char c : name) {
                    if (!std::isalnum(static_cast<unsigned char>(c)) &&
                        c != '.' && c != '-' && c != '_') {
                        return {{"error",   "Invalid name: " + name},
                                {"tool",    "explain_item"},
                                {"command", "explain_item(" + name + ")"},
                                {"output",  "Error: invalid name '" + name + "'"}};
                    }
                }

                // Ensure .exe for process lookup
                std::string target = name;
                {
                    std::string lower = target;
                    for (auto& ch : lower)
                        ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
                    if (lower.size() < 4 || lower.substr(lower.size() - 4) != ".exe")
                        target += ".exe";
                }

                gaia::json result;
                result["tool"]    = "explain_item";
                result["name"]    = name;

                // Try to find as a process via Toolhelp snapshot
                struct MatchInfo { DWORD pid; DWORD parentPid; uint64_t memBytes; };
                std::vector<MatchInfo> matches;
                std::string parentName;

                {
                    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
                    if (snapshot != INVALID_HANDLE_VALUE) {
                        // Pass 1: find matching processes
                        PROCESSENTRY32W pe;
                        pe.dwSize = sizeof(pe);
                        if (Process32FirstW(snapshot, &pe)) {
                            do {
                                std::string exeName = wstringToUtf8(pe.szExeFile);
                                if (_stricmp(exeName.c_str(), target.c_str()) == 0) {
                                    uint64_t mem = 0;
                                    HANDLE hProc = OpenProcess(
                                        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
                                        FALSE, pe.th32ProcessID);
                                    if (hProc) {
                                        PROCESS_MEMORY_COUNTERS pmc;
                                        if (K32GetProcessMemoryInfo(hProc, &pmc, sizeof(pmc)))
                                            mem = pmc.WorkingSetSize;
                                        CloseHandle(hProc);
                                    }
                                    matches.push_back({pe.th32ProcessID, pe.th32ParentProcessID, mem});
                                }
                            } while (Process32NextW(snapshot, &pe));
                        }

                        // Pass 2: look up parent process name from snapshot
                        if (!matches.empty()) {
                            DWORD parentPid = matches[0].parentPid;
                            // Special PIDs
                            if (parentPid == 0) {
                                parentName = "System Idle Process";
                            } else if (parentPid == 4) {
                                parentName = "System";
                            } else {
                                pe.dwSize = sizeof(pe);
                                if (Process32FirstW(snapshot, &pe)) {
                                    do {
                                        if (pe.th32ProcessID == parentPid) {
                                            parentName = wstringToUtf8(pe.szExeFile);
                                            break;
                                        }
                                    } while (Process32NextW(snapshot, &pe));
                                }
                            }
                        }
                        CloseHandle(snapshot);
                    }
                }

                // Tier 3: CIM fallback for recently-exited parents
                if (!matches.empty() && parentName.empty()) {
                    DWORD parentPid = matches[0].parentPid;
                    std::string cimCmd =
                        "$p=Get-CimInstance Win32_Process -Filter 'ProcessId=" +
                        std::to_string(parentPid) + "' -EA 0; "
                        "if($p){$p.Name}else{''}";
                    std::string cimOut = runShell(cimCmd);
                    // Trim whitespace/newlines
                    while (!cimOut.empty() &&
                           (cimOut.back() == '\r' || cimOut.back() == '\n' || cimOut.back() == ' '))
                        cimOut.pop_back();
                    parentName = cimOut.empty() ? "(exited)" : cimOut;
                }

                // Get full path if found as a process
                std::string processPath;
                if (!matches.empty()) {
                    HANDLE hProc = OpenProcess(
                        PROCESS_QUERY_LIMITED_INFORMATION, FALSE, matches[0].pid);
                    if (hProc) {
                        wchar_t pathBuf[MAX_PATH];
                        DWORD pathLen = MAX_PATH;
                        if (QueryFullProcessImageNameW(hProc, 0, pathBuf, &pathLen))
                            processPath = wstringToUtf8(std::wstring(pathBuf, pathLen));
                        CloseHandle(hProc);
                    }

                    uint64_t totalMem = 0;
                    for (auto& m : matches) totalMem += m.memBytes;
                    result["type"]               = "process";
                    result["instances"]          = static_cast<int>(matches.size());
                    result["pid"]                = matches[0].pid;
                    result["parent_pid"]         = matches[0].parentPid;
                    result["parent_name"]        = parentName.empty() ? "(exited)" : parentName;
                    result["path"]               = processPath.empty() ? "unknown" : processPath;
                    result["memory_bytes"]       = matches[0].memBytes;
                    result["memory_human"]       = formatBytes(matches[0].memBytes);
                    result["total_memory_bytes"] = totalMem;
                    result["total_memory_human"] = formatBytes(totalMem);

                    // Build PID list for network connections
                    std::string pidList;
                    for (size_t i = 0; i < matches.size() && i < 10; ++i) {
                        if (i > 0) pidList += ",";
                        pidList += std::to_string(matches[i].pid);
                    }

                    // PowerShell: file info, signer, CPU, connections
                    std::string pathEscaped = processPath;
                    // Escape single quotes for PowerShell
                    for (size_t pos = 0; (pos = pathEscaped.find('\'', pos)) != std::string::npos; pos += 2)
                        pathEscaped.replace(pos, 1, "''");

                    std::string psCmd =
                        "$o=@{}; "
                        "$path='" + pathEscaped + "'; "
                        "if($path -and (Test-Path $path -EA 0)) {"
                        "  try {"
                        "    $vi=[System.Diagnostics.FileVersionInfo]::GetVersionInfo($path); "
                        "    $o.description=$vi.FileDescription; "
                        "    $o.company=$vi.CompanyName; "
                        "    $o.product=$vi.ProductName; "
                        "    $o.version=$vi.FileVersion"
                        "  } catch {}; "
                        "  try {"
                        "    $sig=Get-AuthenticodeSignature $path -EA 0; "
                        "    if($sig) {"
                        "      $o.signer=[string]$sig.SignerCertificate.Subject; "
                        "      $o.signatureStatus=[string]$sig.Status"
                        "    }"
                        "  } catch {}"
                        "}; "
                        "try {"
                        "  $proc=Get-Process -Id " + std::to_string(matches[0].pid) + " -EA 0; "
                        "  if($proc) { $o.cpuSeconds=[math]::Round($proc.CPU,1); "
                        "  $o.startTime=$proc.StartTime.ToString('yyyy-MM-dd HH:mm:ss') }"
                        "} catch {}; "
                        "try {"
                        "  $pids=@(" + pidList + "); "
                        "  $conns=Get-NetTCPConnection -OwningProcess $pids -EA 0 | "
                        "  Select-Object LocalPort,RemoteAddress,RemotePort,State; "
                        "  $o.connections=@($conns|ForEach-Object{"
                        "    @{localPort=$_.LocalPort;remoteAddr=[string]$_.RemoteAddress;"
                        "    remotePort=$_.RemotePort;state=[string]$_.State}})"
                        "} catch { $o.connections=@() }; "
                        "$o|ConvertTo-Json -Depth 3 -Compress";

                    auto psData = parsePsJson(runShell(psCmd));
                    if (!psData.contains("error")) {
                        // Use safe getter: psData.value() throws if field exists but is null
                        auto safeStr = [&](const char* key) -> std::string {
                            return (psData.contains(key) && psData[key].is_string())
                                ? psData[key].get<std::string>() : "";
                        };
                        auto safeArr = [&](const char* key) -> gaia::json {
                            return (psData.contains(key) && psData[key].is_array())
                                ? psData[key] : gaia::json::array();
                        };
                        result["description"]      = safeStr("description");
                        result["company"]          = safeStr("company");
                        result["product"]          = safeStr("product");
                        result["version"]          = safeStr("version");
                        result["signer"]           = safeStr("signer");
                        result["signature_status"] = safeStr("signatureStatus");
                        result["cpu_seconds"]      = psData.contains("cpuSeconds") && psData["cpuSeconds"].is_number()
                                                       ? psData["cpuSeconds"].get<double>() : 0.0;
                        result["start_time"]       = safeStr("startTime");
                        result["connections"]      = safeArr("connections");
                    }
                } else {
                    // Not found as a process — try as a service
                    std::string psCmd =
                        "$svc=Get-Service -Name '" + name + "' -EA 0; "
                        "if($svc) {"
                        "  $wmi=Get-CimInstance Win32_Service -Filter \"Name='" + name + "'\" -EA 0; "
                        "  @{type='service';name=$svc.Name;"
                        "  displayName=[string]$svc.DisplayName;"
                        "  status=[string]$svc.Status;"
                        "  startType=[string]$svc.StartType;"
                        "  pathName=if($wmi){$wmi.PathName}else{'unknown'};"
                        "  description=if($wmi){$wmi.Description}else{''};"
                        "  startName=if($wmi){$wmi.StartName}else{'unknown'}"
                        "  }|ConvertTo-Json -Compress"
                        "} else { @{error='Not found as process or service'}|ConvertTo-Json -Compress }";

                    auto psData = parsePsJson(runShell(psCmd));
                    if (!psData.contains("error")) {
                        result["type"]         = "service";
                        result["display_name"] = psData.value("displayName",  std::string(""));
                        result["status"]       = psData.value("status",       std::string(""));
                        result["start_type"]   = psData.value("startType",    std::string(""));
                        result["path"]         = psData.value("pathName",     std::string("unknown"));
                        result["description"]  = psData.value("description",  std::string(""));
                        result["start_name"]   = psData.value("startName",    std::string("unknown"));
                        processPath = result["path"].get<std::string>();
                    } else {
                        return {{"tool",    "explain_item"},
                                {"name",    name},
                                {"error",   "'" + name + "' not found as a process or service"},
                                {"command", "Win32 API + PowerShell: explain " + name},
                                {"output",  "Not found: " + name}};
                    }
                }

                // Build command/output for CleanConsole
                result["command"] = "Win32 API + PowerShell: explain " + target;
                {
                    std::string out;
                    char buf[512];

                    if (result.value("type", std::string("")) == "service") {
                        std::snprintf(buf, sizeof(buf), "%s (%s)\n",
                            name.c_str(),
                            result.value("display_name", std::string("")).c_str());
                        out += buf;
                        out += "  Type:        Service\n";
                        if (!result.value("description", std::string("")).empty())
                            out += "  Description: " + result["description"].get<std::string>() + "\n";
                        out += "  Status:      " + result.value("status",     std::string("?")) + "\n";
                        out += "  Start type:  " + result.value("start_type", std::string("?")) + "\n";
                        out += "  Path:        " + result.value("path",       std::string("?")) + "\n";
                        out += "  Account:     " + result.value("start_name", std::string("?")) + "\n";
                    } else {
                        int instCount = result.value("instances", 1);
                        std::snprintf(buf, sizeof(buf), "%s  (PID %d, %d instance%s)\n",
                            target.c_str(),
                            result.value("pid", 0),
                            instCount,
                            instCount == 1 ? "" : "s");
                        out += buf;

                        if (!result.value("description", std::string("")).empty())
                            out += "  Description: " + result["description"].get<std::string>() + "\n";
                        if (!result.value("company", std::string("")).empty())
                            out += "  Company:     " + result["company"].get<std::string>() + "\n";
                        out += "  Path:        " + result.value("path",        std::string("?")) + "\n";
                        out += "  Parent:      " + result.value("parent_name", std::string("?")) + "\n";

                        if (!result.value("signer", std::string("")).empty()) {
                            out += "  Signer:      " + result["signer"].get<std::string>() + "\n";
                            out += "  Signature:   " + result.value("signature_status", std::string("?")) + "\n";
                        }

                        std::snprintf(buf, sizeof(buf), "  Memory:      %s",
                            formatBytes(result.value("memory_bytes", uint64_t(0))).c_str());
                        out += buf;
                        if (instCount > 1) {
                            out += "  (total: " + result.value("total_memory_human", std::string("?")) + ")";
                        }
                        out += "\n";

                        if (result.contains("cpu_seconds")) {
                            std::snprintf(buf, sizeof(buf), "  CPU time:    %.1f sec\n",
                                result["cpu_seconds"].get<double>());
                            out += buf;
                        }

                        if (result.contains("start_time") &&
                            !result["start_time"].get<std::string>().empty()) {
                            out += "  Started:     " + result["start_time"].get<std::string>() + "\n";
                        }

                        if (result.contains("connections") && result["connections"].is_array()) {
                            auto& conns = result["connections"];
                            if (conns.empty()) {
                                out += "  Network:     no active connections\n";
                            } else {
                                std::snprintf(buf, sizeof(buf), "  Network:     %d connection%s\n",
                                    static_cast<int>(conns.size()),
                                    conns.size() == 1 ? "" : "s");
                                out += buf;
                                int shown = 0;
                                for (const auto& c : conns) {
                                    if (shown >= 5) {
                                        std::snprintf(buf, sizeof(buf), "    ... and %d more\n",
                                            static_cast<int>(conns.size()) - 5);
                                        out += buf;
                                        break;
                                    }
                                    std::snprintf(buf, sizeof(buf), "    :%d -> %s:%d  (%s)\n",
                                        c.value("localPort", 0),
                                        c.value("remoteAddr", std::string("?")).c_str(),
                                        c.value("remotePort", 0),
                                        c.value("state", std::string("?")).c_str());
                                    out += buf;
                                    ++shown;
                                }
                            }
                        }
                    }
                    result["output"] = out;
                }
                return result;
            },
            {{"name", gaia::ToolParamType::STRING, true,
              "Process or service name to explain (e.g., 'audiodg.exe', 'Audiosrv')"}});

        // ==================================================================
        // restart_process — Kill and relaunch an application
        // ==================================================================
        toolRegistry().registerTool(
            "restart_process",
            "Force-restart a hung application: terminates all instances and relaunches "
            "from the same executable path. ONLY after explicit user confirmation.",
            [](const gaia::json& args) -> gaia::json {
                std::string name = args.value("name", "");
                if (name.empty()) {
                    return {{"error",   "Process name is required"},
                            {"tool",    "restart_process"},
                            {"command", "restart_process(?)"},
                            {"output",  "Error: process name is required"}};
                }
                for (char c : name) {
                    if (!std::isalnum(static_cast<unsigned char>(c)) &&
                        c != '.' && c != '-' && c != '_') {
                        return {{"error",   "Invalid process name: " + name},
                                {"tool",    "restart_process"},
                                {"command", "restart_process(" + name + ")"},
                                {"output",  "Error: invalid process name"}};
                    }
                }

                std::string target = name;
                {
                    std::string lower = target;
                    for (auto& ch : lower)
                        ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
                    if (lower.size() < 4 || lower.substr(lower.size() - 4) != ".exe")
                        target += ".exe";
                }

                KillResult kr = killProcessesByName(target);

                if (kr.found == 0) {
                    return {{"tool",    "restart_process"},
                            {"process", name},
                            {"error",   "Process not found: " + name},
                            {"command", "restart_process(" + target + ")"},
                            {"output",  "Process not found: " + name}};
                }

                std::string processPath = kr.firstPath;
                if (processPath.empty()) {
                    return {{"tool",    "restart_process"},
                            {"process", name},
                            {"error",   "Could not determine process path for: " + name},
                            {"command", "restart_process(" + target + ")"},
                            {"output",  "Error: cannot determine executable path for " + name}};
                }

                int      terminated = kr.terminated;
                uint64_t totalFreed = kr.memoryFreed;

                // Relaunch
                bool relaunched = false;
                {
                    std::wstring wpath = utf8ToWstring(processPath);
                    STARTUPINFOW si    = {};
                    si.cb             = sizeof(si);
                    si.dwFlags        = STARTF_USESHOWWINDOW;
                    si.wShowWindow    = SW_SHOWNORMAL;
                    PROCESS_INFORMATION pi = {};
                    if (CreateProcessW(
                            wpath.c_str(), nullptr, nullptr, nullptr,
                            FALSE, DETACHED_PROCESS | CREATE_NEW_CONSOLE,
                            nullptr, nullptr, &si, &pi)) {
                        CloseHandle(pi.hProcess);
                        CloseHandle(pi.hThread);
                        relaunched = true;
                    }
                }

                std::string cmd = "Win32 API: TerminateProcess + CreateProcessW(" + target + ")";
                std::string out = "Terminated: " + target +
                    " (" + std::to_string(terminated) + " instance" +
                    (terminated == 1 ? "" : "s") + ", " +
                    formatBytes(totalFreed) + " freed)\n";
                if (relaunched)
                    out += "Relaunched: " + processPath;
                else
                    out += "Relaunch failed: could not start " + processPath;

                return {
                    {"tool",               "restart_process"},
                    {"process",            name},
                    {"path",               processPath},
                    {"instances_killed",   terminated},
                    {"memory_freed_human", formatBytes(totalFreed)},
                    {"relaunched",         relaunched},
                    {"status",             relaunched ? "restarted" : "killed_only"},
                    {"command",            cmd},
                    {"output",             out}
                };
            },
            {{"name", gaia::ToolParamType::STRING, true,
              "Application name to restart (e.g., 'explorer.exe')"}});

        // ==================================================================
        // restart_service — Restart a Windows service
        // ==================================================================
        toolRegistry().registerTool(
            "restart_service",
            "Restart a Windows service by its service name. Reports previous status "
            "and new status. ONLY after explicit user confirmation.",
            [](const gaia::json& args) -> gaia::json {
                std::string name = args.value("name", "");
                if (name.empty()) {
                    return {{"error",   "Service name is required"},
                            {"tool",    "restart_service"},
                            {"command", "Restart-Service(?)"},
                            {"output",  "Error: service name is required"}};
                }
                if (!isSafeShellArg(name)) {
                    return {{"error",   "Invalid service name: " + name},
                            {"tool",    "restart_service"},
                            {"command", "Restart-Service(" + name + ")"},
                            {"output",  "Error: invalid service name '" + name + "'"}};
                }

                std::string psCmd =
                    "$n='" + name + "'; "
                    "$svc=Get-Service -Name $n -EA 0; "
                    "if(-not $svc) { @{error='Service not found: ' + $n}|ConvertTo-Json -Compress; exit }; "
                    "$prev=[string]$svc.Status; "
                    "try { Restart-Service -Name $n -Force -ErrorAction Stop; "
                    "  $new=[string](Get-Service -Name $n).Status; "
                    "  @{name=$n;displayName=[string]$svc.DisplayName;"
                    "  previousStatus=$prev;newStatus=$new;success=$true}"
                    "} catch { "
                    "  @{name=$n;displayName=[string]$svc.DisplayName;"
                    "  previousStatus=$prev;newStatus='Failed';success=$false;"
                    "  error=$_.Exception.Message}"
                    "}|ConvertTo-Json -Compress";

                auto psData = parsePsJson(runShell(psCmd));

                if (psData.contains("error")) {
                    std::string errMsg = psData["error"].get<std::string>();
                    return {{"tool",    "restart_service"},
                            {"service", name},
                            {"error",   errMsg},
                            {"command", "PowerShell: Restart-Service " + name},
                            {"output",  "Error: " + errMsg}};
                }

                bool success     = psData.value("success", false);
                std::string disp = psData.value("displayName",    std::string(name));
                std::string prev = psData.value("previousStatus", std::string("?"));
                std::string next = psData.value("newStatus",      std::string("?"));

                std::string out = "Service: " + name + " (" + disp + ")\n"
                                + "Previous: " + prev + "\n"
                                + "Current:  " + next;

                return {
                    {"tool",            "restart_service"},
                    {"service",         name},
                    {"display_name",    disp},
                    {"previous_status", prev},
                    {"new_status",      next},
                    {"success",         success},
                    {"command",         "PowerShell: Restart-Service -Name " + name + " -Force"},
                    {"output",          out}
                };
            },
            {{"name", gaia::ToolParamType::STRING, true,
              "Service name to restart (e.g., 'Audiosrv', 'wuauserv')"}});

        // ==================================================================
        // quarantine_item — Move suspicious file to quarantine folder
        // ==================================================================
        toolRegistry().registerTool(
            "quarantine_item",
            "Move a suspicious executable to the GAIA quarantine folder "
            "(C:\\ProgramData\\GAIA\\quarantine\\). Automatically kills all running "
            "instances first, then renames with .quarantined extension to block "
            "re-execution. ONLY after explicit user confirmation.",
            [](const gaia::json& args) -> gaia::json {
                std::string path = args.value("path", "");
                if (path.empty()) {
                    return {{"error",   "File path is required"},
                            {"tool",    "quarantine_item"},
                            {"command", "quarantine_item(?)"},
                            {"output",  "Error: file path is required"}};
                }
                if (!isSafePath(path)) {
                    return {{"error",   "Invalid or unsafe file path"},
                            {"tool",    "quarantine_item"},
                            {"command", "quarantine_item(...)"},
                            {"output",  "Error: invalid file path"}};
                }

                // Check file exists
                std::wstring wpath = utf8ToWstring(path);
                DWORD attrs = GetFileAttributesW(wpath.c_str());
                if (attrs == INVALID_FILE_ATTRIBUTES || (attrs & FILE_ATTRIBUTE_DIRECTORY)) {
                    return {{"tool",    "quarantine_item"},
                            {"path",    path},
                            {"error",   "File not found or is a directory: " + path},
                            {"command", "Win32 API: MoveFileExW(" + path + ")"},
                            {"output",  "File not found: " + path}};
                }

                // Get file size
                WIN32_FILE_ATTRIBUTE_DATA fad;
                uint64_t fileSize = 0;
                if (GetFileAttributesExW(wpath.c_str(), GetFileExInfoStandard, &fad)) {
                    fileSize = (static_cast<uint64_t>(fad.nFileSizeHigh) << 32) | fad.nFileSizeLow;
                }

                // Extract filename
                std::string filename = path;
                auto lastSlash = filename.rfind('\\');
                if (lastSlash != std::string::npos) filename = filename.substr(lastSlash + 1);

                // Create quarantine directory (ensure both levels exist)
                CreateDirectoryW(L"C:\\ProgramData\\GAIA", nullptr);
                std::wstring quarantineDir = L"C:\\ProgramData\\GAIA\\quarantine";
                CreateDirectoryW(quarantineDir.c_str(), nullptr);
                // Ignore errors — directories may already exist

                // Build destination path (filename + timestamp + .quarantined)
                SYSTEMTIME st;
                GetLocalTime(&st);
                char dateBuf[16];
                std::snprintf(dateBuf, sizeof(dateBuf), "%04d%02d%02d",
                    st.wYear, st.wMonth, st.wDay);

                std::string destName = filename + "." + dateBuf + ".quarantined";
                std::string destPath = "C:\\ProgramData\\GAIA\\quarantine\\" + destName;
                std::wstring wdest   = utf8ToWstring(destPath);

                // Kill any processes using this file before moving it
                KillResult kr = killProcessesByPath(path);
                std::string killSummary;
                if (kr.terminated > 0) {
                    killSummary = std::to_string(kr.terminated) + " instance" +
                        (kr.terminated == 1 ? "" : "s") + ", " + formatBytes(kr.memoryFreed) + " freed";
                    Sleep(300);  // let OS release file handles
                }

                if (!MoveFileExW(wpath.c_str(), wdest.c_str(), MOVEFILE_COPY_ALLOWED)) {
                    DWORD err = GetLastError();
                    char errBuf[64];
                    std::snprintf(errBuf, sizeof(errBuf), "Win32 error %lu", err);
                    return {{"tool",    "quarantine_item"},
                            {"path",    path},
                            {"error",   std::string("Failed to move file: ") + errBuf},
                            {"command", "Win32 API: MoveFileExW(" + path + ")"},
                            {"output",  std::string("Failed to quarantine: ") + errBuf}};
                }

                std::string out;
                if (!killSummary.empty())
                    out += "Killed:      " + filename + " (" + killSummary + ")\n";
                out += "Quarantined: " + path + "\n"
                     + "Moved to:    " + destPath + "\n"
                     + "File size:   " + formatBytes(fileSize);

                std::string cmd = kr.terminated > 0
                    ? "Win32 API: TerminateProcess + MoveFileExW(" + path + " -> " + destPath + ")"
                    : "Win32 API: MoveFileExW(" + path + " -> " + destPath + ")";

                return {
                    {"tool",            "quarantine_item"},
                    {"original_path",   path},
                    {"quarantine_path", destPath},
                    {"filename",        filename},
                    {"file_size_bytes", fileSize},
                    {"file_size_human", formatBytes(fileSize)},
                    {"killed_summary",  killSummary},
                    {"status",          "quarantined"},
                    {"command",         cmd},
                    {"output",          out}
                };
            },
            {{"path", gaia::ToolParamType::STRING, true,
              "Full path to the suspicious file (e.g., 'C:\\Users\\user\\Temp\\unknown.exe')"}});
    }

private:
    static gaia::AgentConfig makeConfig(const std::string& modelId) {
        gaia::AgentConfig config;
        config.maxSteps = 20;
        config.modelId  = modelId;
        return config;
    }
};

// ---------------------------------------------------------------------------
// Action menu
// ---------------------------------------------------------------------------
struct ActionEntry {
    const char* label;
    const char* description;
    const char* prompt;
};

static const ActionEntry kActions[] = {
    {
        "Explain",
        "Get details on a process or service  (e.g. 'Explain A3' or just 'Explain' for all)",
        "The user wants to explain items from the analysis. "
        "If no specific item was mentioned, explain ALL items from the A, B, and C sections "
        "by calling explain_item for each one. "
        "If a specific item was mentioned (e.g. A3, B1, C2), explain just that one. "
        "For each item use explain_item to provide full details: what it is, who made it, "
        "where it runs from, its parent process, and whether it is normal or suspicious."
    },
    {
        "Stop",
        "Kill a process or stop a service  (e.g. 'Stop A3' or 'Stop B1')",
        "The user wants to stop something. Ask which item from the analysis (A1, B2, etc.). "
        "For a process or application (A-label), use kill_process. "
        "For a service (B-label), stop it via PowerShell (Stop-Service). "
        "Explain the impact clearly and confirm before proceeding."
    },
    {
        "Restart",
        "Restart an app or service  (e.g. 'Restart B1')",
        "The user wants to restart something. Ask which item — A-label for application, "
        "B-label for service. For an application, use restart_process to kill and relaunch it. "
        "For a service, use restart_service. Explain what will happen and confirm before proceeding."
    },
    {
        "Quarantine",
        "Move a suspicious file to C:\\ProgramData\\GAIA\\quarantine  (e.g. 'Quarantine C1')",
        "The user wants to quarantine suspicious items. For each item in C. Suspicious Items:\n"
        "1. Call explain_item to get full details if not already available.\n"
        "2. Present a confirmation block in Key: Value format:\n"
        "   Process: [name]\n"
        "   Path: [full executable path]\n"
        "   Memory: [RAM usage]\n"
        "   Publisher: [company name or 'Unknown']\n"
        "   Reason: [why this is suspicious — flags, unknown publisher, unusual path, etc.]\n"
        "3. Ask: 'Kill [name] and quarantine this file? "
        "This will terminate the process immediately and move the file to "
        "C:\\ProgramData\\GAIA\\quarantine\\. (yes / no)'\n"
        "4. Wait for user response before proceeding.\n"
        "5. If YES: call quarantine_item with the full path. "
        "Report which processes were killed and where the file was moved.\n"
        "6. If NO: skip this item and move to the next."
    },
    {
        "Reanalyze",
        "Run a fresh system analysis",
        ""  // handled specially in main
    },
    {
        "Monitor",
        "Background scan every N min  (e.g. '6 5' = every 5 min, '6' = every 5 min, '6' again = stop)",
        ""  // handled specially in main
    },
};
static constexpr size_t kActionsSize   = sizeof(kActions) / sizeof(kActions[0]);
static constexpr int    kActionReanalyze = 5;  // 1-based index
static constexpr int    kActionMonitor   = 6;  // 1-based index

static const char* kAutoAnalysisPrompt =
    "AUTO-SCAN: Perform an automatic system analysis on startup. "
    "This is NOT a user request — the application triggered this automatically.\n\n"
    "Steps:\n"
    "1. Run system_snapshot (system specs, top processes with company/description/flags, services).\n"
    "2. Run list_processes (detailed memory and CPU data with flags).\n\n"
    "Review the data and classify each process yourself:\n"
    "- Check the 'company', 'description', 'path', and 'flags' fields\n"
    "- Processes with flags like 'unknown_company' or 'temp_path' deserve scrutiny\n"
    "- Use your judgment — not all flagged processes are suspicious\n"
    "- Windows system processes (svchost, csrss, smss, lsass) with empty company are NORMAL\n\n"
    "Output format: A/B/C sections as specified in your instructions.\n"
    "Maximum 8 items per section. 1 line each.\n"
    "System Health: [Healthy/Warning/Critical] - [one sentence]\n"
    "The count in your System Health line MUST match the actual number of items in section C.";

// ---------------------------------------------------------------------------
// SystemMonitor — background thread that periodically scans the system,
// compares consecutive snapshots, and pushes alerts to a thread-safe queue.
// The main thread drains and displays alerts before each menu prompt.
// ---------------------------------------------------------------------------
class SystemMonitor {
public:
    explicit SystemMonitor(const std::string& modelId,
                           std::chrono::seconds interval = std::chrono::seconds(300))
        : modelId_(modelId), interval_(interval) {}

    ~SystemMonitor() { stop(); }

    SystemMonitor(const SystemMonitor&) = delete;
    SystemMonitor& operator=(const SystemMonitor&) = delete;

    void start() {
        if (running_.load()) return;
        running_.store(true);
        isFirstScan_ = true;
        scanCount_.store(0);
        initBalloonNotify();
        thread_ = std::thread(&SystemMonitor::monitorLoop, this);
    }

    void stop() {
        if (!running_.load()) return;
        running_.store(false);
        cv_.notify_all();
        if (thread_.joinable()) thread_.join();
        cleanupBalloonNotify();
    }

    bool isRunning() const { return running_.load(); }
    int  scanCount()  const { return scanCount_.load(); }
    std::chrono::seconds interval() const { return interval_; }

    std::vector<MonitorAlert> drainAlerts() {
        std::vector<MonitorAlert> result;
        std::lock_guard<std::mutex> lock(alertMutex_);
        while (!alertQueue_.empty()) {
            result.push_back(std::move(alertQueue_.front()));
            alertQueue_.pop();
        }
        return result;
    }

private:
    // Map health status string to ANSI color
    static const char* healthColor(const std::string& status) {
        if (status == "Critical") return color::RED;
        if (status == "Warning")  return color::YELLOW;
        return color::GREEN;
    }

    // Build a short summary string from a snapshot for console/notification output.
    // E.g. "Health: Warning — 2 suspicious: llama-server.exe, svc_helper.exe"
    static std::string snapSummary(const MonitorSnapshot& snap) {
        std::string s = "Health: " + snap.healthStatus;
        if (!snap.healthDetail.empty())
            s += " — " + snap.healthDetail;
        if (!snap.suspiciousItems.empty()) {
            s += "\n           Suspicious: ";
            for (size_t i = 0; i < snap.suspiciousItems.size(); ++i) {
                if (i > 0) s += ", ";
                // Extract name: "1. foo.exe - path..." → "foo.exe"
                auto& item = snap.suspiciousItems[i];
                auto dot = item.find(". ");
                std::string name = (dot != std::string::npos) ? item.substr(dot + 2) : item;
                auto dash = name.find(" - ");
                if (dash != std::string::npos) name = name.substr(0, dash);
                // Trim whitespace
                while (!name.empty() && name.back() == ' ') name.pop_back();
                s += name;
            }
        }
        return s;
    }

    // ---------------------------------------------------------------
    // Background thread entry point
    // ---------------------------------------------------------------
    void monitorLoop() {
        try {
            gaia::AgentConfig monCfg;
            monCfg.modelId = modelId_;
            monCfg.temperature = 0.0;  // deterministic scans
            ProcessAgent monitorAgent(monCfg);
            monitorAgent.setOutputHandler(
                std::make_unique<gaia::SilentConsole>(true));

            while (running_.load()) {
                try {
                    // Print scan-start indicator so user knows monitor is alive
                    int scanNum = scanCount_.load() + 1;
                    std::cout << "\n" << color::DIM
                              << "  [Monitor] Scan #" << scanNum << " starting..."
                              << color::RESET << std::endl;
                    std::cout << color::BOLD << "  > " << color::RESET << std::flush;

                    // Collect direct system data
                    MonitorSnapshot snap = takeSnapshot();

                    // Run LLM analysis — include previous health for consistency
                    monitorAgent.clearHistory();
                    std::string prompt = kAutoAnalysisPrompt;
                    if (!isFirstScan_ && !lastSnapshot_.healthStatus.empty()) {
                        prompt += "\n\nPrevious scan result: " + lastSnapshot_.healthStatus + ".";
                        if (!lastSnapshot_.suspiciousItems.empty()) {
                            prompt += " Suspicious:";
                            for (const auto& item : lastSnapshot_.suspiciousItems)
                                prompt += "\n  - " + item;
                        }
                        prompt += "\nIf the same conditions persist, keep the same classification.";
                    }
                    auto r = monitorAgent.processQuery(prompt);
                    std::string answer = r.value("result", "");
                    parseLlmIntoSnapshot(snap, answer);
                    scanCount_.fetch_add(1);

                    if (isFirstScan_) {
                        isFirstScan_ = false;
                        lastSnapshot_ = std::move(snap);

                        // Notify main thread that baseline is captured
                        auto mins = std::chrono::duration_cast<std::chrono::minutes>(interval_).count();
                        pushAlert(MonitorAlert::Severity::INFO,
                                  MonitorAlert::Type::BASELINE_COMPLETE,
                                  "Baseline scan complete",
                                  "Next scan in " + std::to_string(mins) + " min. "
                                  "Health: " + lastSnapshot_.healthStatus);

                        // Fire notification for baseline if not healthy
                        if (lastSnapshot_.healthStatus != "Healthy") {
                            std::string body = lastSnapshot_.healthDetail;
                            if (!lastSnapshot_.suspiciousItems.empty()) {
                                body += " (";
                                for (size_t i = 0; i < lastSnapshot_.suspiciousItems.size(); ++i) {
                                    if (i > 0) body += ", ";
                                    auto& item = lastSnapshot_.suspiciousItems[i];
                                    auto dot = item.find(". ");
                                    std::string nm = (dot != std::string::npos) ? item.substr(dot + 2) : item;
                                    auto dash = nm.find(" - ");
                                    if (dash != std::string::npos) nm = nm.substr(0, dash);
                                    while (!nm.empty() && nm.back() == ' ') nm.pop_back();
                                    body += nm;
                                }
                                body += ")";
                            }
                            showBalloonNotify("Baseline: " + lastSnapshot_.healthStatus, body);
                        }

                        // Print directly so user sees feedback even without typing
                        std::cout << "\n" << healthColor(lastSnapshot_.healthStatus)
                                  << "  [Monitor] Baseline captured. "
                                  << snapSummary(lastSnapshot_)
                                  << "\n           Next scan in " << mins << " min."
                                  << color::RESET << std::endl;
                        std::cout << color::BOLD << "  > " << color::RESET << std::flush;
                    } else {
                        auto alerts = diffSnapshots(lastSnapshot_, snap);

                        // Single notification per scan — only when not healthy
                        if (snap.healthStatus != "Healthy") {
                            std::string body = snap.healthDetail;
                            if (!snap.suspiciousItems.empty()) {
                                body += " (";
                                for (size_t i = 0; i < snap.suspiciousItems.size(); ++i) {
                                    if (i > 0) body += ", ";
                                    auto& item = snap.suspiciousItems[i];
                                    auto dot = item.find(". ");
                                    std::string nm = (dot != std::string::npos) ? item.substr(dot + 2) : item;
                                    auto dash = nm.find(" - ");
                                    if (dash != std::string::npos) nm = nm.substr(0, dash);
                                    while (!nm.empty() && nm.back() == ' ') nm.pop_back();
                                    body += nm;
                                }
                                body += ")";
                            }
                            showBalloonNotify("Health: " + snap.healthStatus, body);
                        }

                        // Push to queue for main thread
                        if (!alerts.empty()) {
                            std::lock_guard<std::mutex> lock(alertMutex_);
                            for (auto& a : alerts)
                                alertQueue_.push(std::move(a));
                        } else {
                            pushAlert(MonitorAlert::Severity::INFO,
                                      MonitorAlert::Type::SCAN_COMPLETE,
                                      "Scan complete — no changes",
                                      "Health: " + snap.healthStatus);
                        }

                        // Print scan result directly (user may not type to drain queue)
                        auto mins = std::chrono::duration_cast<std::chrono::minutes>(interval_).count();
                        std::cout << "\n" << healthColor(snap.healthStatus)
                                  << "  [Monitor] Scan #" << scanCount_.load()
                                  << " done. " << snapSummary(snap)
                                  << "\n           Next scan in " << mins << " min."
                                  << color::RESET << std::endl;
                        std::cout << color::BOLD << "  > " << color::RESET << std::flush;

                        lastSnapshot_ = std::move(snap);
                    }

                } catch (const std::exception& e) {
                    pushAlert(MonitorAlert::Severity::WARNING,
                              MonitorAlert::Type::MONITOR_ERROR,
                              "Monitor scan failed",
                              std::string("Error: ") + e.what());
                }

                // Interruptible sleep with visible countdown + message pump
                {
                    auto total = std::chrono::duration_cast<
                        std::chrono::seconds>(interval_).count();
                    for (long long rem = total; rem > 0 && running_.load(); --rem) {
                        std::cout << "\r" << color::DIM
                                  << "  [Monitor] Next scan in " << rem << "s   "
                                  << color::RESET << std::flush;

                        // Pump notification messages (handles click-to-focus)
                        MSG msg;
                        while (PeekMessageW(&msg, g_notifyHwnd, 0, 0, PM_REMOVE))
                            DispatchMessageW(&msg);

                        std::unique_lock<std::mutex> lock(cvMutex_);
                        if (cv_.wait_for(lock, std::chrono::seconds(1),
                                         [this] { return !running_.load(); }))
                            break;
                    }
                    // Clear the countdown line
                    std::cout << "\r" << std::string(50, ' ')
                              << "\r" << std::flush;
                }
            }
        } catch (const std::exception& e) {
            pushAlert(MonitorAlert::Severity::CRITICAL,
                      MonitorAlert::Type::MONITOR_ERROR,
                      "Monitor failed to start",
                      std::string("Could not create monitor agent: ") + e.what());
            running_.store(false);
        }
    }

    // ---------------------------------------------------------------
    // Collect direct system data via Win32 APIs (thread-safe statics)
    // ---------------------------------------------------------------
    static MonitorSnapshot takeSnapshot() {
        MonitorSnapshot snap;
        snap.timestamp = std::chrono::system_clock::now();

        auto mem = getMemoryInfo();
        snap.memoryUsedPercent = mem.value("used_percent", 0);
        snap.memoryUsedBytes   = mem.value("used_bytes", uint64_t(0));

        auto procs = getTopProcesses(20);
        if (procs.is_array()) {
            for (const auto& p : procs) {
                snap.topProcesses.emplace_back(
                    p.value("name", std::string("?")),
                    p.value("total_memory_bytes", uint64_t(0)));
            }
        }
        return snap;
    }

    // ---------------------------------------------------------------
    // Parse LLM output for health status and suspicious items
    // ---------------------------------------------------------------
    static void parseLlmIntoSnapshot(MonitorSnapshot& snap,
                                     const std::string& rawAnswer) {
        snap.rawLlmAnswer = rawAnswer;

        std::istringstream stream(rawAnswer);
        std::string line;
        bool inSuspicious = false;

        while (std::getline(stream, line)) {
            if (!line.empty() && line.back() == '\r') line.pop_back();

            // Health status — capture level and description after the dash
            if (line.find("System Health:") != std::string::npos) {
                if (line.find("Critical") != std::string::npos)
                    snap.healthStatus = "Critical";
                else if (line.find("Warning") != std::string::npos)
                    snap.healthStatus = "Warning";
                else
                    snap.healthStatus = "Healthy";
                // Extract description after " - " (e.g. "System Health:[Warning] - One high-memory...")
                auto dashPos = line.find(" - ");
                if (dashPos != std::string::npos && dashPos + 3 < line.size())
                    snap.healthDetail = line.substr(dashPos + 3);
                inSuspicious = false;
                continue;
            }

            // C. Suspicious Items section
            if (line.find("C. Suspicious") != std::string::npos) {
                inSuspicious = true;
                continue;
            }

            // End of C section on next header or "System Health"
            if (inSuspicious) {
                if (line.empty() || line.find("System Health") != std::string::npos) {
                    inSuspicious = false;
                    continue;
                }
                // Skip "None detected" lines
                if (line.find("None") != std::string::npos ||
                    line.find("none") != std::string::npos) {
                    inSuspicious = false;
                    continue;
                }
                // Numbered items (e.g. "1. unknown.exe - ...")
                if (!line.empty() &&
                    std::isdigit(static_cast<unsigned char>(line[0]))) {
                    snap.suspiciousItems.push_back(line);
                }
            }
        }

        if (snap.healthStatus.empty()) snap.healthStatus = "Unknown";
    }

    // ---------------------------------------------------------------
    // Compare two snapshots — return alerts for meaningful changes
    // ---------------------------------------------------------------
    static std::vector<MonitorAlert> diffSnapshots(const MonitorSnapshot& prev,
                                                   const MonitorSnapshot& curr) {
        std::vector<MonitorAlert> alerts;
        auto now = std::chrono::system_clock::now();

        // Helper: lowercase a string
        auto toLower = [](std::string s) {
            for (auto& c : s)
                c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
            return s;
        };

        // 1. System memory spike (>10 percentage points)
        int memDelta = curr.memoryUsedPercent - prev.memoryUsedPercent;
        if (memDelta > 10) {
            auto sev = (curr.memoryUsedPercent > 90)
                ? MonitorAlert::Severity::CRITICAL
                : MonitorAlert::Severity::WARNING;
            alerts.push_back({sev, MonitorAlert::Type::MEMORY_SPIKE,
                "Memory usage spike",
                "Memory increased from " + std::to_string(prev.memoryUsedPercent)
                    + "% to " + std::to_string(curr.memoryUsedPercent) + "%",
                now});
        }

        // Build name -> memory maps (lowercased)
        std::map<std::string, uint64_t> prevMem, currMem;
        std::set<std::string> prevNames, currNames;
        for (const auto& [name, mem] : prev.topProcesses) {
            auto lower = toLower(name);
            prevNames.insert(lower);
            prevMem[lower] = mem;
        }
        for (const auto& [name, mem] : curr.topProcesses) {
            auto lower = toLower(name);
            currNames.insert(lower);
            currMem[lower] = mem;
        }

        // 2. New processes in top-20
        for (const auto& name : currNames) {
            if (prevNames.find(name) == prevNames.end()) {
                alerts.push_back({MonitorAlert::Severity::INFO,
                    MonitorAlert::Type::NEW_PROCESS,
                    "New top process: " + name,
                    name + " appeared in top resource consumers",
                    now});
            }
        }

        // 3. Processes that left top-20
        for (const auto& name : prevNames) {
            if (currNames.find(name) == currNames.end()) {
                alerts.push_back({MonitorAlert::Severity::INFO,
                    MonitorAlert::Type::PROCESS_GONE,
                    "Process left top list: " + name,
                    name + " is no longer a top resource consumer",
                    now});
            }
        }

        // 4. Per-process memory surge (>500 MB growth)
        for (const auto& [name, mem] : currMem) {
            auto it = prevMem.find(name);
            if (it != prevMem.end()) {
                int64_t delta = static_cast<int64_t>(mem) -
                                static_cast<int64_t>(it->second);
                if (delta > 500LL * 1024 * 1024) {
                    alerts.push_back({MonitorAlert::Severity::WARNING,
                        MonitorAlert::Type::MEMORY_SURGE,
                        name + " memory surge",
                        name + " memory grew by " +
                            formatBytes(static_cast<uint64_t>(delta)),
                        now});
                }
            }
        }

        // 5. New suspicious items (CRITICAL + balloon)
        std::set<std::string> prevSusp(prev.suspiciousItems.begin(),
                                       prev.suspiciousItems.end());
        for (const auto& item : curr.suspiciousItems) {
            if (prevSusp.find(item) == prevSusp.end()) {
                alerts.push_back({MonitorAlert::Severity::CRITICAL,
                    MonitorAlert::Type::NEW_SUSPICIOUS,
                    "Suspicious item detected",
                    item,
                    now});
            }
        }

        // 6. Health status change
        if (!prev.healthStatus.empty() && !curr.healthStatus.empty() &&
            prev.healthStatus != curr.healthStatus) {
            auto sev = (curr.healthStatus == "Critical")
                ? MonitorAlert::Severity::CRITICAL
                : MonitorAlert::Severity::WARNING;
            alerts.push_back({sev, MonitorAlert::Type::HEALTH_CHANGED,
                "Health: " + prev.healthStatus + " -> " + curr.healthStatus,
                "System health changed from " + prev.healthStatus
                    + " to " + curr.healthStatus,
                now});
        }

        return alerts;
    }

    // ---------------------------------------------------------------
    // Push a single alert to the queue
    // ---------------------------------------------------------------
    void pushAlert(MonitorAlert::Severity sev, MonitorAlert::Type type,
                   const std::string& title, const std::string& detail) {
        std::lock_guard<std::mutex> lock(alertMutex_);
        alertQueue_.push({sev, type, title, detail,
                          std::chrono::system_clock::now()});
    }

    // ---------------------------------------------------------------
    // Data members
    // ---------------------------------------------------------------
    std::string          modelId_;
    std::chrono::seconds interval_;

    std::thread          thread_;
    std::atomic<bool>    running_{false};
    std::mutex           cvMutex_;
    std::condition_variable cv_;

    std::mutex                   alertMutex_;
    std::queue<MonitorAlert>     alertQueue_;

    std::atomic<int>    scanCount_{0};
    bool                isFirstScan_ = true;
    MonitorSnapshot     lastSnapshot_;
};

// ---------------------------------------------------------------------------
// printMonitorAlerts — display alerts drained from the background monitor
// ---------------------------------------------------------------------------
static void printMonitorAlerts(const std::vector<MonitorAlert>& alerts) {
    if (alerts.empty()) return;

    std::cout << std::endl;
    std::cout << color::YELLOW
              << "  ========================================================================================"
              << color::RESET << std::endl;
    std::cout << color::BOLD << color::YELLOW
              << "  Monitor Alerts (" << alerts.size() << ")"
              << color::RESET << std::endl;
    std::cout << color::YELLOW
              << "  ========================================================================================"
              << color::RESET << std::endl;

    for (const auto& a : alerts) {
        const char* sevColor = color::GRAY;
        const char* sevLabel = "INFO";
        if (a.severity == MonitorAlert::Severity::WARNING) {
            sevColor = color::YELLOW;
            sevLabel = "WARNING";
        } else if (a.severity == MonitorAlert::Severity::CRITICAL) {
            sevColor = color::RED;
            sevLabel = "CRITICAL";
        }

        auto tt = std::chrono::system_clock::to_time_t(a.timestamp);
        char timeBuf[16];
        struct tm tmBuf{};
        localtime_s(&tmBuf, &tt);
        std::strftime(timeBuf, sizeof(timeBuf), "%H:%M:%S", &tmBuf);

        std::cout << "  " << color::GRAY << timeBuf << "  "
                  << sevColor << color::BOLD << "[" << sevLabel << "] "
                  << color::RESET << color::WHITE << a.title
                  << color::RESET << std::endl;
        if (!a.detail.empty()) {
            std::cout << "  " << color::GRAY << "         " << a.detail
                      << color::RESET << std::endl;
        }
    }

    std::cout << color::YELLOW
              << "  ========================================================================================"
              << color::RESET << std::endl;
    std::cout << std::endl;
}

// ---------------------------------------------------------------------------
// printActionMenu — show available actions with optional monitor status
// ---------------------------------------------------------------------------
static void printActionMenu(bool isMonitoring = false, int scanCount = 0) {
    std::cout << color::CYAN
              << "  ========================================================================================"
              << color::RESET << std::endl;
    std::cout << color::BOLD << "  Available Actions:"
              << color::RESET << std::endl;
    std::cout << std::endl;
    for (size_t i = 0; i < kActionsSize; ++i) {
        std::cout << color::YELLOW << "  [" << (i + 1) << "] "
                  << color::RESET << color::WHITE
                  << kActions[i].label
                  << color::RESET;
        std::cout << color::GRAY << "  — " << kActions[i].description
                  << color::RESET << std::endl;
    }
    std::cout << color::CYAN
              << "  ========================================================================================"
              << color::RESET << std::endl;
    std::cout << color::GRAY
              << "  Shortcuts: '1 A3' = Explain A3,  '2 B1' = Stop B1,  '4 C1' = Quarantine C1"
              << "  |  '6 5' = Monitor every 5 min"
              << color::RESET << std::endl;
    std::cout << color::GRAY
              << "  Or type any question directly."
              << color::RESET << std::endl;
    if (isMonitoring) {
        std::cout << color::GREEN << color::BOLD
                  << "  [Monitor active]" << color::RESET
                  << color::GRAY << " — Background scanning, "
                  << scanCount << " scans completed"
                  << color::RESET << std::endl;
    }
    std::cout << std::endl;
}

// ---------------------------------------------------------------------------
// main — model selection, auto-analysis, then action loop
// Pass model as first arg to skip the interactive prompt:
//   process_agent.exe 1        → GPU (Qwen3-4B-Instruct-2507-GGUF)
//   process_agent.exe 2        → NPU (Qwen3-4B-Instruct-2507-FLM)
//   process_agent.exe <model>  → exact model ID
// ---------------------------------------------------------------------------
int main(int argc, char* argv[]) {
    try {
        // Ensure UTF-8 output so em dashes and other Unicode render correctly
        SetConsoleOutputCP(CP_UTF8);
        SetConsoleCP(CP_UTF8);

        // --- Admin check ---
        {
            bool isAdmin = false;
            HANDLE token = nullptr;
            if (OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
                TOKEN_ELEVATION elevation{};
                DWORD size = sizeof(elevation);
                if (GetTokenInformation(token, TokenElevation, &elevation,
                                        sizeof(elevation), &size)) {
                    isAdmin = elevation.TokenIsElevated != 0;
                }
                CloseHandle(token);
            }
            if (!isAdmin) {
                std::cout << std::endl;
                std::cout << color::YELLOW << color::BOLD
                          << "  WARNING: " << color::RESET
                          << color::YELLOW
                          << "Not running as admin."
                          << color::RESET << std::endl;
                std::cout << color::GRAY
                          << "  Some processes may not be"
                          << std::endl
                          << "  accessible. Right-click your"
                          << std::endl
                          << "  terminal -> Run as administrator"
                          << std::endl
                          << "  for full access."
                          << color::RESET << std::endl;
            }
        }

        // --- Banner ---
        std::cout << std::endl;
        std::cout << color::CYAN << color::BOLD
                  << "  ========================================================================================"
                  << color::RESET << std::endl;
        std::cout << color::CYAN << color::BOLD
                  << "   Process Analyst  |  GAIA C++ Agent Framework  |  Local Inference"
                  << color::RESET << std::endl;
        std::cout << color::CYAN << color::BOLD
                  << "  ========================================================================================"
                  << color::RESET << std::endl;

        // --- Model selection ---
        std::string modelChoice;
        if (argc > 1) {
            modelChoice = argv[1];
        } else {
            std::cout << std::endl;
            std::cout << color::BOLD << "  Select inference backend:"
                      << color::RESET << std::endl;
            std::cout << color::YELLOW << "  [1] " << color::RESET
                      << color::GREEN << "GPU" << color::RESET
                      << color::GRAY << "  - Qwen3-4B-Instruct-2507-GGUF"
                      << color::RESET << std::endl;
            std::cout << color::YELLOW << "  [2] " << color::RESET
                      << color::MAGENTA << "NPU" << color::RESET
                      << color::GRAY << "  - Qwen3-4B-Instruct-2507-FLM"
                      << color::RESET << std::endl;
            std::cout << std::endl;
            std::cout << color::BOLD << "  > " << color::RESET << std::flush;
            if (!std::getline(std::cin, modelChoice)) return 1;
        }

        std::string modelId;
        if (modelChoice == "2" || modelChoice == "npu" || modelChoice == "NPU") {
            modelId = "Qwen3-4B-Instruct-2507-FLM";
            std::cout << color::MAGENTA << "  Using NPU backend: "
                      << color::BOLD << modelId << color::RESET << std::endl;
        } else if (modelChoice == "1" || modelChoice == "gpu" || modelChoice == "GPU"
                   || modelChoice.empty()) {
            modelId = "Qwen3-4B-Instruct-2507-GGUF";
            std::cout << color::GREEN << "  Using GPU backend: "
                      << color::BOLD << modelId << color::RESET << std::endl;
        } else {
            modelId = modelChoice;
            std::cout << color::GREEN << "  Using model: "
                      << color::BOLD << modelId << color::RESET << std::endl;
        }

        ProcessAgent agent(modelId);
        std::unique_ptr<SystemMonitor> monitor;

        // =====================================================================
        // Phase 1: DETECT — Auto-analyze system on startup
        // =====================================================================
        std::cout << std::endl;
        std::cout << color::CYAN << color::BOLD
                  << "  Analyzing your system..."
                  << color::RESET << std::endl;
        std::cout << std::endl;
        {
            auto r = agent.processQuery(kAutoAnalysisPrompt);
            (void)r;
        }

        // =====================================================================
        // Phase 2: ACT — Action menu loop
        // History is preserved across actions so the LLM retains analysis context.
        // clearHistory() is called ONLY on reanalyze.
        // =====================================================================
        std::string userInput;
        std::vector<gaia::Decision> decisions;  // non-empty when LLM awaits yes/no
        while (true) {
            // Drain background monitor alerts before showing menu
            if (monitor && monitor->isRunning()) {
                auto alerts = monitor->drainAlerts();
                printMonitorAlerts(alerts);
            }

            if (!decisions.empty())
                agent.console().printDecisionMenu(decisions);
            else
                printActionMenu(monitor && monitor->isRunning(),
                                monitor ? monitor->scanCount() : 0);

            std::cout << color::BOLD << "  > " << color::RESET << std::flush;
            if (!std::getline(std::cin, userInput)) break;

            if (userInput.empty()) continue;
            if (userInput == "quit" || userInput == "exit" || userInput == "q") break;

            std::string query;

            if (!decisions.empty()) {
                // Map "1"/"2" to decision values, or accept the text directly
                std::string lower = userInput;
                for (auto& c : lower) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));

                bool mapped = false;
                // Numeric selection
                if (userInput.size() <= 2) {
                    try {
                        int choice = std::stoi(userInput);
                        if (choice >= 1 && choice <= static_cast<int>(decisions.size())) {
                            query = decisions[static_cast<size_t>(choice - 1)].value;
                            mapped = true;
                        }
                    } catch (...) {}
                }
                // Text match: "yes", "no", or first-char shorthand "y"/"n"
                if (!mapped) {
                    for (const auto& d : decisions) {
                        std::string dval = d.value;
                        for (auto& c : dval) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
                        if (lower == dval || (!dval.empty() && lower.size() == 1 && lower[0] == dval[0])) {
                            query = d.value;
                            mapped = true;
                            break;
                        }
                    }
                }
                if (!mapped) query = userInput;  // free-form fallback

                std::cout << color::CYAN << "  > " << query << color::RESET << std::endl;
                decisions.clear();

            } else {
                // Normal action menu input

                // Check for "N item" shorthand (e.g. "5 C1" → sends "Quarantine C1")
                auto spacePos = userInput.find(' ');
                bool isShorthand    = false;
                int  shorthandChoice = 0;
                std::string shorthandItem;
                if (spacePos != std::string::npos) {
                    std::string numPart = userInput.substr(0, spacePos);
                    bool allDigits = !numPart.empty() &&
                        std::all_of(numPart.begin(), numPart.end(),
                                    [](unsigned char c) { return std::isdigit(c); });
                    if (allDigits) {
                        try {
                            shorthandChoice = std::stoi(numPart);
                            shorthandItem   = userInput.substr(spacePos + 1);
                            isShorthand     = !shorthandItem.empty() &&
                                              shorthandChoice >= 1 &&
                                              shorthandChoice <= static_cast<int>(kActionsSize);
                        } catch (...) {}
                    }
                }

                bool isNumber = !userInput.empty() &&
                    std::all_of(userInput.begin(), userInput.end(),
                                [](unsigned char c) { return std::isdigit(c); });

                if (isShorthand) {
                    size_t idx = static_cast<size_t>(shorthandChoice - 1);
                    if (shorthandChoice == kActionMonitor) {
                        // "6 N" — start monitor with N-minute interval, or stop if running
                        if (monitor && monitor->isRunning()) {
                            monitor->stop();
                            monitor.reset();
                            std::cout << color::YELLOW
                                      << "  Monitor stopped."
                                      << color::RESET << std::endl;
                        } else {
                            int interval = 5;
                            try { interval = std::stoi(shorthandItem); } catch (...) {}
                            if (interval < 1) interval = 1;
                            if (interval > 60) interval = 60;
                            monitor = std::make_unique<SystemMonitor>(
                                modelId, std::chrono::seconds(interval * 60));
                            monitor->start();
                            std::cout << color::GREEN
                                      << "  Monitor started (scanning every "
                                      << interval << " min). First scan = baseline."
                                      << color::RESET << std::endl;
                        }
                        decisions.clear();
                        continue;
                    }
                    if (shorthandChoice == kActionReanalyze) {
                        // Reanalyze shorthand — ignore item, clear and rerun
                        agent.clearHistory();
                        std::cout << std::endl;
                        std::cout << color::CYAN << color::BOLD
                                  << "  Reanalyzing your system..."
                                  << color::RESET << std::endl;
                        std::cout << std::endl;
                        auto r = agent.processQuery(kAutoAnalysisPrompt);
                        (void)r;
                        decisions.clear();
                        continue;
                    }
                    // Send "Label item" as free-form query (e.g. "Quarantine C1")
                    query = std::string(kActions[idx].label) + " " + shorthandItem;
                    std::cout << color::CYAN << "  > " << query
                              << color::RESET << std::endl;

                } else if (isNumber) {
                    int choice = 0;
                    try { choice = std::stoi(userInput); }
                    catch (const std::out_of_range&) { choice = -1; }

                    if (choice < 1 || choice > static_cast<int>(kActionsSize)) {
                        std::cout << color::RED << "  Invalid selection. Enter 1-"
                                  << kActionsSize << " or type a question."
                                  << color::RESET << std::endl;
                        continue;
                    }

                    size_t idx = static_cast<size_t>(choice - 1);

                    if (choice == kActionMonitor) {
                        if (monitor && monitor->isRunning()) {
                            monitor->stop();
                            monitor.reset();
                            std::cout << color::YELLOW
                                      << "  Monitor stopped."
                                      << color::RESET << std::endl;
                        } else {
                            monitor = std::make_unique<SystemMonitor>(
                                modelId, std::chrono::seconds(300));
                            monitor->start();
                            std::cout << color::GREEN
                                      << "  Monitor started (scanning every 5 min). "
                                      << "First scan = baseline."
                                      << color::RESET << std::endl;
                        }
                        decisions.clear();
                        continue;
                    }

                    if (choice == kActionReanalyze) {
                        // Reanalyze — clear history and re-run analysis
                        agent.clearHistory();
                        std::cout << std::endl;
                        std::cout << color::CYAN << color::BOLD
                                  << "  Reanalyzing your system..."
                                  << color::RESET << std::endl;
                        std::cout << std::endl;
                        auto r = agent.processQuery(kAutoAnalysisPrompt);
                        (void)r;
                        decisions.clear();
                        continue;
                    }

                    // Actions 1–4: send the action prompt, keep history
                    query = kActions[idx].prompt;
                    std::cout << color::CYAN << "  > " << kActions[idx].label
                              << color::RESET << std::endl;

                } else {
                    // Free-form question — keep history, LLM has full context
                    // Special case: "monitor" or "monitor N" keyword
                    std::string lower = userInput;
                    for (auto& c : lower)
                        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
                    if (lower == "monitor" || lower.substr(0, 8) == "monitor ") {
                        if (monitor && monitor->isRunning()) {
                            monitor->stop();
                            monitor.reset();
                            std::cout << color::YELLOW
                                      << "  Monitor stopped."
                                      << color::RESET << std::endl;
                        } else {
                            int interval = 5;
                            if (lower.size() > 8) {
                                try { interval = std::stoi(lower.substr(8)); } catch (...) {}
                            }
                            if (interval < 1) interval = 1;
                            if (interval > 60) interval = 60;
                            monitor = std::make_unique<SystemMonitor>(
                                modelId, std::chrono::seconds(interval * 60));
                            monitor->start();
                            std::cout << color::GREEN
                                      << "  Monitor started (scanning every "
                                      << interval << " min). First scan = baseline."
                                      << color::RESET << std::endl;
                        }
                        decisions.clear();
                        continue;
                    }
                    query = userInput;
                }
            }

            auto r = agent.processQuery(query);
            std::string answer = r.value("result", "");
            decisions = agent.detectPendingDecisions(answer);
        }

        // Clean shutdown of background monitor
        if (monitor) {
            std::cout << color::GRAY << "  Stopping monitor..."
                      << color::RESET << std::endl;
            monitor->stop();
            monitor.reset();
        }

        std::cout << std::endl;
        std::cout << color::GRAY << "  Goodbye!" << color::RESET << std::endl;

    } catch (const std::exception& e) {
        std::cerr << color::RED << color::BOLD << "Fatal error: "
                  << color::RESET << color::RED << e.what()
                  << color::RESET << std::endl;
        return 1;
    }

    return 0;
}
