// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>

#include <gaia/security.h>
#include <gaia/tool_registry.h>
#include <gaia/workspace.h>

#include <cstdlib>
#include <filesystem>
#include <memory>
#include <random>

using namespace gaia;
namespace fs = std::filesystem;

namespace {

struct Dirs {
    fs::path base, work, other;
    Dirs() {
        std::mt19937_64 rng(std::random_device{}());
        // Not under /tmp: that is a system directory commands may always use.
        base = fs::current_path() / ("gaia-ws-" + std::to_string(rng()));
        work = base / "work";
        other = base / "other";
        fs::create_directories(work / "sub");
        fs::create_directories(other);
    }
    ~Dirs() {
        std::error_code ec;
        fs::remove_all(base, ec);
    }
};

// The working directory for one test: relative paths resolve against it.
struct InFolder {
    fs::path before = fs::current_path();
    explicit InFolder(const fs::path& folder) { fs::current_path(folder); }
    ~InFolder() {
        std::error_code ec;
        fs::current_path(before, ec);
    }
};

}  // namespace

TEST(WorkspaceTest, ContainsResolvesRelativeAndDotDot) {
    Dirs d;
    Workspace ws({d.work.string()});
    InFolder in(d.work);
    EXPECT_TRUE(ws.contains("notes.txt"));
    EXPECT_TRUE(ws.contains("sub/../notes.txt"));
    EXPECT_TRUE(ws.contains((d.work / "sub" / "x.csv").string()));
    EXPECT_FALSE(ws.contains("../other/x"));
    EXPECT_FALSE(ws.contains((d.other / "x").string()));
    EXPECT_FALSE(ws.contains((d.base / "work-sibling").string())) << "prefix is not containment";
}

TEST(WorkspaceTest, ExtraRootsAreAllowed) {
    Dirs d;
    Workspace ws({d.work.string(), d.other.string()});
    EXPECT_TRUE(ws.contains((d.other / "x").string()));
}

TEST(WorkspaceTest, CmdSwitchesAreNotPathsUnderCmdAndGetAHintUnderBash) {
    Dirs d;
    Workspace ws({d.work.string()});
    const std::string listing = "dir \"" + d.work.string() + "\" /s /b /a:d /-c";
    EXPECT_EQ(ws.checkCommand(listing, false), "") << "cmd.exe switches are not places";
    EXPECT_NE(ws.checkCommand("type " + (d.other / "x.txt").string() + " /a", false), "")
        << "a real path outside is still refused under cmd";
#ifdef _WIN32
    const std::string refused = ws.checkCommand(listing);
    ASSERT_NE(refused, "") << "under bash /s is drive S";
    EXPECT_NE(refused.find("run in bash"), std::string::npos) << refused;
    EXPECT_NE(refused.find("ls -R"), std::string::npos) << refused;
#endif
}

TEST(WorkspaceTest, CommandChecks) {
    Dirs d;
    Workspace ws({d.work.string()});
    EXPECT_EQ(ws.checkCommand("ls -la && python3 script.py"), "");
    EXPECT_EQ(ws.checkCommand("cd " + d.work.string() + " && ls"), "");
    EXPECT_EQ(ws.checkCommand("grep -rn '/api/v1' src"), "") << "a pattern, not a place";
    EXPECT_EQ(ws.checkCommand("curl -s https://example.com/data.json"), "");
    EXPECT_NE(ws.checkCommand("find / -name '*.xlsx'"), "");
    EXPECT_NE(ws.checkCommand("ls /"), "");
    EXPECT_EQ(ws.checkCommand("awk -F, '{gsub(/ /,\"\",$4); t+=$4}' data.csv"), "")
        << "a regex delimiter is not the root folder";
    EXPECT_EQ(ws.checkCommand("python3 -c \"print('a/b'.split('/'))\""), "");
    EXPECT_EQ(ws.checkCommand("python3 - <<'EOF'\nratio = total / count\nEOF"), "")
        << "division in inline code is not the root folder";
    EXPECT_NE(ws.checkCommand("sudo find / -name x"), "");
    EXPECT_NE(ws.checkCommand("cd /tmp && rm -rf / "), "");
    EXPECT_NE(ws.checkCommand("cat " + (d.other / "secret.txt").string()), "");
    EXPECT_NE(ws.checkCommand("python3 -c \"open('" + (d.other / "f").string() + "')\""), "");
#ifndef _WIN32
    EXPECT_EQ(ws.checkCommand("cat /etc/hosts 2>/dev/null; ls /tmp"), "") << "system dirs pass";
#endif
}

TEST(WorkspaceTest, NetworkSharesAreOutside) {
    Dirs d;
    Workspace ws({d.work.string()});
    EXPECT_NE(ws.checkCommand("cat //server/share/x"), "");
    EXPECT_NE(ws.checkCommand("type \\\\server\\share\\x"), "");
    EXPECT_NE(ws.checkCommand("cp notes.txt \"\\\\server\\share\""), "");
    EXPECT_EQ(ws.checkCommand("curl -s http://localhost:8000//double"), "") << "a URL path, not a share";
#ifdef _WIN32
    // Git Bash spells a drive as /c/.
    EXPECT_NE(ws.checkCommand("cat /c/Users/Public/x.txt"), "");
    EXPECT_NE(ws.checkCommand("cat /cygdrive/c/Users/Public/x.txt"), "");
#else
    EXPECT_NE(ws.checkCommand("cat /proc/1/environ"), "") << "another program's environment";
    EXPECT_EQ(ws.checkCommand("cat /proc/cpuinfo"), "");
#endif
}

namespace {
bool staysInside(const Workspace& ws, const std::string& command, const std::string& cwd) {
    std::vector<std::vector<std::string>> segments;
    if (!splitShellCommand(command, true, segments)) return false;
    return ws.readsStayInside(segments, cwd);
}
}  // namespace

TEST(WorkspaceTest, ReadsStayInside) {
    Dirs d;
    Workspace ws({d.work.string()});
    const std::string cwd = d.work.string();
    for (const char* ok : {"ls", "ls -la sub", "cat sub/../notes.txt", "find . -name '*.md'", "cd sub && ls",
                           "grep -rn /api/v1 sub", "grep --include=*.py -rn x .", "cat notes.txt | wc -l"}) {
        EXPECT_TRUE(staysInside(ws, ok, cwd)) << ok;
    }
    for (const char* no : {"cat ../other/secret.txt", "cat sub/../../other/x", "ls ..", "cd .. && ls",
                           "cd", "cd -", "cd sub && cat ../../other/x", "ls ~root", "ls ~", "ls .*",
                           "cat sub/.?/other/x", "grep -f/etc/passwd x", "grep --file=../other/x y", "ls /"}) {
        EXPECT_FALSE(staysInside(ws, no, cwd)) << no;
    }
    EXPECT_FALSE(staysInside(ws, "cat " + (d.other / "secret.txt").generic_string(), cwd));
    EXPECT_FALSE(staysInside(ws, "ls", d.other.string())) << "the shell has left the chat's folders";
    EXPECT_TRUE(staysInside(ws, "ls", "")) << "no folder yet means the first root";
#ifndef _WIN32
    EXPECT_TRUE(staysInside(ws, "cat /etc/hosts", cwd)) << "system folders are readable";
    EXPECT_FALSE(staysInside(ws, "cat /proc/1/environ", cwd));
#endif
}

TEST(WorkspaceTest, ConfinedToolsRefuseOutsidePaths) {
    Dirs d;
    auto ws = std::make_shared<const Workspace>(std::vector<std::string>{d.work.string()});
    ToolInfo t;
    t.name = "read";
    t.parameters = {{"path", ToolParamType::STRING, true, ""}};
    int calls = 0;
    t.callback = [&calls](const json&) -> json {
        ++calls;
        return json{{"status", "success"}};
    };
    ToolRegistry reg;
    reg.registerTool(Workspace::confine(t, ws, {"path"}));
    InFolder in(d.work);
    EXPECT_EQ(reg.executeTool("read", json{{"path", "inside.txt"}})["status"], "success");
    json refused = reg.executeTool("read", json{{"path", (d.other / "x").string()}});
    EXPECT_EQ(refused["status"], "error");
    EXPECT_NE(refused["error"].get<std::string>().find("--allow-dir"), std::string::npos);
    EXPECT_EQ(calls, 1);

    ToolInfo sh;
    sh.name = "sh";
    sh.parameters = {{"command", ToolParamType::STRING, true, ""}};
    sh.callback = [&calls](const json&) -> json {
        ++calls;
        return json{{"status", "success"}};
    };
    reg.registerTool(Workspace::confineShell(sh, ws));
    EXPECT_EQ(reg.executeTool("sh", json{{"command", "find / -name x"}})["status"], "error");
    EXPECT_EQ(calls, 1);
}

// Folders and paths are UTF-8 strings, whatever the machine's code page.
TEST(WorkspaceTest, NonAsciiFoldersAreUtf8) {
    Dirs d;
    const fs::path root = d.base / fs::u8path("w\xc3\xb6rk \xe6\x97\xa5\xe6\x9c\xac");
    fs::create_directories(root / "sub");
    const std::string inside = (root / "sub" / fs::u8path("n\xc3\xb6te.txt")).u8string();
    const std::string outside = (d.other / fs::u8path("n\xc3\xb6te.txt")).u8string();

    Workspace named({root.u8string()});
    EXPECT_TRUE(named.contains(inside));
    {
        InFolder in(root);
        EXPECT_TRUE(named.contains("sub/new.txt"));
    }
    EXPECT_FALSE(named.contains(outside));
    EXPECT_EQ(named.checkPath(inside), "");
    EXPECT_NO_THROW(json(named.checkPath(outside)).dump()) << "a refusal is valid UTF-8 JSON";

    // With no folder named, the working directory is the folder.
    const fs::path before = fs::current_path();
    fs::current_path(root);
    Workspace implied({});
    const bool sees = implied.contains(inside);
    const bool refuses = implied.contains(outside);
    fs::current_path(before);
    EXPECT_TRUE(sees);
    EXPECT_FALSE(refuses);
}

TEST(WorkspaceTest, RelativePathsResolveWhereTheToolsOpenThem) {
    Dirs d;
    Workspace ws({d.work.string(), d.other.string()});
    {
        InFolder in(d.other);
        EXPECT_TRUE(ws.contains("x.txt")) << "the working directory is the second root";
        EXPECT_FALSE(ws.contains("../outside.txt"));
    }
    InFolder in(d.base);
    EXPECT_FALSE(ws.contains("notes.txt")) << "it would be opened in the working directory, not in the first root";
    EXPECT_NE(ws.checkPath("notes.txt"), "");
    EXPECT_TRUE(ws.contains("work/notes.txt"));
}

TEST(WorkspaceTest, AnOverlongCommandIsRefusedBeforeItIsScanned) {
    Dirs d;
    Workspace ws({d.work.string()});
    const std::string refused = ws.checkCommand("cat /" + std::string(300000, 'a'));
    EXPECT_NE(refused.find("65536"), std::string::npos) << refused.substr(0, 200);
    EXPECT_EQ(ws.checkCommand("echo " + std::string(1000, 'a')), "");
}

#ifdef _WIN32
TEST(WorkspaceTest, NetworkAndDevicePathsAreRefusedByTheirSpelling) {
    Dirs d;
    Workspace ws({d.work.string()});
    for (const char* path : {"\\\\gaia-no-such-host.invalid\\share\\x.txt", "//gaia-no-such-host.invalid/share/x.txt",
                             "\\\\?\\C:\\Windows\\win.ini", "\\\\.\\C:\\Windows\\win.ini", "\\\\.\\pipe\\gaia"}) {
        EXPECT_FALSE(ws.contains(path)) << path;
        EXPECT_NE(ws.checkPath(path), "") << path;
    }
}
#endif

TEST(WorkspaceTest, ACommandNamingALinkOutOfTheFolderIsRefused) {
    Dirs d;
    Workspace ws({d.work.string()});
    InFolder in(d.work);
#ifdef _WIN32
    const std::string make = "cmd /c mklink /J linked \"" + d.other.string() + "\" >nul 2>&1";
    ASSERT_EQ(std::system(make.c_str()), 0);
#else
    std::error_code ec;
    fs::create_directory_symlink(d.other, "linked", ec);
    ASSERT_FALSE(ec) << ec.message();
#endif
    EXPECT_NE(ws.checkCommand("cat linked/private.txt"), "");
    EXPECT_NE(ws.checkCommand("cd linked && ls"), "");
    EXPECT_NE(ws.checkCommand("python -c \"print(open('linked/private.txt').read())\""), "");
    EXPECT_EQ(ws.checkCommand("ls sub && cat notes.txt"), "");
    EXPECT_EQ(ws.checkCommand("linked/python -m pytest && linked/pip list"), "") << "a linked program may be run";
    fs::remove("linked");
}

#ifndef _WIN32
TEST(WorkspaceTest, ALinkToAFileNotThereYetIsJudgedByItsTarget) {
    Dirs d;
    Workspace ws({d.work.string()});
    std::error_code ec;
    fs::create_symlink(d.other / "new.txt", d.work / "out.md", ec);
    ASSERT_FALSE(ec) << ec.message();
    fs::create_symlink(fs::path("sub") / "new.txt", d.work / "in.md", ec);
    ASSERT_FALSE(ec) << ec.message();
    EXPECT_NE(ws.checkPath((d.work / "out.md").string()), "") << "a write would land outside the folder";
    EXPECT_EQ(ws.checkPath((d.work / "in.md").string()), "");
}
#endif

#ifndef _WIN32
TEST(WorkspaceTest, AWildcardOrLinkFollowingReadAsksWhenALinkLeadsOut) {
    Dirs d;
    Workspace ws({d.work.string()});
    const std::string cwd = d.work.string();
    for (const char* ok : {"cat *", "grep KEY *.txt", "grep -R x .", "find -L . -name x", "ls -L sub", "cat sub/*"}) {
        EXPECT_TRUE(staysInside(ws, ok, cwd)) << ok << " (no link in the folder yet)";
    }
    std::error_code ec;
    fs::create_symlink(d.other / "secret.txt", d.work / "env", ec);
    ASSERT_FALSE(ec) << ec.message();
    fs::create_directory_symlink(d.other, d.work / "sub" / "deps", ec);
    ASSERT_FALSE(ec) << ec.message();
    for (const char* asks : {"cat *", "grep KEY *", "grep -R x .", "grep -rR x sub", "find -L . -name x", "du -L",
                             "ls -RL", "cat sub/*", "cat */notes.txt"}) {
        EXPECT_FALSE(staysInside(ws, asks, cwd)) << asks;
    }
    for (const char* ok : {"cat notes.txt", "grep -rn x sub", "find . -name x", "ls -la", "grep -L x notes.txt"}) {
        EXPECT_TRUE(staysInside(ws, ok, cwd)) << ok << " (follows no link)";
    }
}
#endif

TEST(WorkspaceTest, GitBashDevicePathsAreNotFiles) {
    for (const char* device : {"/dev/clipboard", "/dev/sda1", "/dev/mem", "/proc/registry/HKEY_CURRENT_USER/x",
                               "/proc/1/environ", "/dev/conin"}) {
        EXPECT_TRUE(Workspace::isGitBashDevicePath(device)) << device;
    }
    for (const char* fine : {"/dev/null", "/dev/stdin", "/dev/fd/3", "/dev/urandom", "/c/Users/me/notes.txt",
                             "/tmp/x", "dev/clipboard"}) {
        EXPECT_FALSE(Workspace::isGitBashDevicePath(fine)) << fine;
    }
#ifdef _WIN32
    Dirs d;
    Workspace ws({d.work.string()});
    EXPECT_NE(ws.checkCommand("cat /dev/clipboard"), "");
    EXPECT_NE(ws.checkCommand("cat /proc/registry/HKEY_CURRENT_USER/Environment/PATH"), "");
#endif
}
