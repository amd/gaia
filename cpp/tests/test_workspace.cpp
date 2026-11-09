// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>

#include <gaia/security.h>
#include <gaia/tool_registry.h>
#include <gaia/workspace.h>

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

}  // namespace

TEST(WorkspaceTest, ContainsResolvesRelativeAndDotDot) {
    Dirs d;
    Workspace ws({d.work.string()});
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
