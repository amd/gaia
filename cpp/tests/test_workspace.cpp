// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>

#include <gaia/file_tools.h>
#include <gaia/security.h>
#include <gaia/tool_registry.h>
#include <gaia/workspace.h>

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <memory>
#include <random>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

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

// A folder link at `link` to `target`, which may not exist. A junction on Windows: it needs no privilege.
void makeFolderLink(const fs::path& target, const fs::path& link) {
#ifdef _WIN32
    std::error_code ec;
    const bool made = fs::create_directories(target, ec);
    const std::string cmd = "cmd /c mklink /J \"" + link.string() + "\" \"" + target.string() + "\" >nul 2>&1";
    ASSERT_EQ(std::system(cmd.c_str()), 0) << cmd;
    if (made) fs::remove_all(target, ec);
#else
    std::error_code ec;
    fs::create_directory_symlink(target, link, ec);
    ASSERT_FALSE(ec) << ec.message();
#endif
}

bool sameFile(const std::string& a, const fs::path& b) {
    std::error_code ec;
    return !a.empty() && fs::equivalent(fs::u8path(a), b, ec) && !ec;
}

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
    // "Always allow rm" covers `cd folder && rm x`; the folder must still be one this chat may use.
    EXPECT_NE(ws.checkCommand("cd " + d.other.string() + " && rm notes.txt"), "");
    EXPECT_NE(ws.checkCommand("cd " + d.other.string() + " && gcc -c a.c 2>&1"), "");
    EXPECT_EQ(ws.checkCommand("cd " + d.work.string() + " && gcc -c a.c 2>&1"), "");
    // The shell expands an unquoted wildcard in the top folder into real folders.
    for (const char* globbed : {"rm -rf /*", "cat /h*/me/.ssh/id_rsa", "rm -rf /{home,x}/me", "ls /[a-z]*"}) {
        EXPECT_NE(ws.checkCommand(globbed), "") << globbed;
    }
    EXPECT_EQ(ws.checkCommand("grep -rn '/v[0-9]*/' src"), "") << "quoted, it is a pattern";
    EXPECT_EQ(ws.checkCommand("cat > a.js <<'EOF'\n/* a comment */\nEOF"), "") << "a comment, not a command";
    EXPECT_NE(ws.checkCommand("cat " + (d.other / "secret.txt").string()), "");
    EXPECT_NE(ws.checkCommand("python3 -c \"open('" + (d.other / "f").string() + "')\""), "");
#ifndef _WIN32
    EXPECT_EQ(ws.checkCommand("cat /etc/hosts 2>/dev/null; ls /tmp"), "") << "system dirs pass";
#endif
}

TEST(WorkspaceTest, DoubleSlashInCodeIsNotAPlace) {
    Dirs d;
    Workspace ws({d.work.string()});
    for (const char* fine : {
             "cat > ipmask.go << 'EOF'\npackage pflag\n\n// -- net.IPMask value\nfunc f() {} // trailing\nEOF",
             "sed -i '' '3s/$/ \\/\\/ x/; 4a\\\n// Reject pure IPv6 masks' ipmask.go",
             "python3 -c \"print(7 // 2)  # floor\"",
             "grep -rn \"// TODO\" src",
             "grep -c '//' ipmask.go",
             "grep -rn // src",
             "echo \"// generated\" > gen.js",
             "node -e \"fetch('https://example.com//x')\"",
         }) {
        EXPECT_EQ(ws.checkCommand(fine), "") << fine;
    }
#ifndef _WIN32
    for (const char* fine : {"cat > a.go <<'EOF'\n//go:build linux\n//TODO later\nEOF", "sed -e '//d' a.txt",
                             "curl -s \"https://example.com/a?next=//c\""}) {
        EXPECT_EQ(ws.checkCommand(fine), "") << fine;
    }
    EXPECT_NE(ws.checkCommand("cat //Users/Shared/x"), "") << "//Users is /Users";
#endif
    // The root, however many slashes spell it.
    for (const char* root : {"ls //", "find // -name x", "ls \"//\"", "rm -rf ///", "grep -r secret //",
                             "grep -re secret //", "grep -r -f pats //", "cd /tmp && ls // ; echo done"}) {
        EXPECT_NE(ws.checkCommand(root), "") << root;
    }
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

// An attached file is granted alone: reading it from its folder is fine, its neighbours are not.
TEST(WorkspaceTest, ReadsOfAGrantedFileStayInsideFromItsFolder) {
    Dirs d;
    const fs::path uploads = d.other / "uploads";
    fs::create_directories(uploads / "nested");
    std::ofstream(uploads / "report.txt") << "revenue";
    std::ofstream(uploads / "theirs.txt") << "another chat's upload";
    const std::string report = fs::canonical(uploads / "report.txt").generic_string();
    const std::string theirs = fs::canonical(uploads / "theirs.txt").generic_string();
    Workspace ws({d.work.string(), report});
    const std::string up = uploads.string(), work = d.work.string();
    for (const std::string& ok : std::vector<std::string>{
             "grep -n revenue report.txt", "wc -l report.txt", "head -5 " + report,
          "cat report.txt | grep -n revenue | head -3", "grep -c 'net income' report.txt", "pwd"}) {
        EXPECT_TRUE(staysInside(ws, ok, up)) << ok;
    }
    EXPECT_TRUE(staysInside(ws, "grep -n revenue " + report, work));
    // A POSIX shell reads a backslash as an escape, so the cd names the folder with forward slashes.
    const std::string upSlash = uploads.generic_string();
    EXPECT_TRUE(staysInside(ws, "cd " + upSlash + " && grep -n revenue report.txt && wc -l report.txt", work));
    for (const std::string& no : std::vector<std::string>{
             "grep -n revenue theirs.txt", "wc -l theirs.txt", "cat " + theirs, "ls", "ls .", "find . -name x",
          "find -name x", "grep -rn revenue", "grep -rn revenue nested", "cat *.txt", "cat report.txt theirs.txt",
          "du -sh", "cd nested && cat x", "ls --ignore=report.txt", "du -a --exclude=report.txt",
          "grep -r -f report.txt", "grep -rn revenue " + report}) {
        EXPECT_FALSE(staysInside(ws, no, up)) << no;
    }
    for (const std::string& no : std::vector<std::string>{
             "cd " + up + " && cat theirs.txt", "cd " + up + " && ls", "cd " + up + " && grep -rn revenue",
          "cd " + up + " && cat *"}) {
        EXPECT_FALSE(staysInside(ws, no, work)) << no;
    }
    EXPECT_FALSE(staysInside(ws, "ls", d.other.string())) << "an outside folder is not listed";
}

TEST(WorkspaceTest, TerminalRefusalNamesAllowDir) {
    Dirs d;
    Workspace ws({d.work.string()});
    const std::string why = ws.checkPath((d.other / "x").string());
    const std::string tail = "). Work inside them. If the user needs another folder, say so: they can start "
                             "the agent with --allow-dir <folder>.";
    ASSERT_GE(why.size(), tail.size()) << why;
    EXPECT_EQ(why.substr(why.size() - tail.size()), tail) << why;
}

TEST(WorkspaceTest, JudgeNamesThePlaceToAskAboutAndAddRootOpensIt) {
    Dirs d;
    Workspace ws({d.work.string()});
    const std::string outside = (d.other / "x").string();
    EXPECT_TRUE(ws.judgePath((d.work / "inside.txt").string()).refusal.empty());
    EXPECT_TRUE(ws.judgePath((d.work / "inside.txt").string()).askPath.empty());

    const Workspace::Outside file = ws.judgePath(outside);
    EXPECT_FALSE(file.refusal.empty());
    EXPECT_EQ(file.askPath, fs::weakly_canonical(d.other / "x").u8string());
    const Workspace::Outside command = ws.judgeCommand("ls " + d.other.string());
    EXPECT_FALSE(command.refusal.empty());
    EXPECT_EQ(command.askPath, fs::canonical(d.other).u8string());
    EXPECT_TRUE(ws.judgeCommand("ls //server/share").askPath.empty()) << "a network share is not asked about";

    ws.addRoot(file.askPath);
    EXPECT_TRUE(ws.checkPath(outside).empty());
    EXPECT_FALSE(ws.checkPath((d.other / "y").string()).empty()) << "only the path that was added opens";
    ws.addRoot(command.askPath);
    EXPECT_TRUE(ws.checkCommand("ls " + d.other.string()).empty());
}

TEST(WorkspaceTest, RemedyReplacesTheAllowDirAdvice) {
    Dirs d;
    Workspace ws({d.work.string()});
    ws.setRemedy("Tell the user.");
    const std::string why = ws.checkPath((d.other / "x").string());
    EXPECT_EQ(why.find("--allow-dir"), std::string::npos) << why;
    EXPECT_NE(why.find("Work inside them. Tell the user."), std::string::npos) << why;
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

#ifdef _WIN32
// An 8.3 alias names the same folder as its long name, so it is refused the same way.
TEST(WorkspaceTest, AShortNameForAnOutsideFolderIsRefused) {
    Dirs d;
    Workspace ws({d.work.string()});
    const fs::path outside = d.base / "outside-folder-with-a-long-name";
    fs::create_directories(outside);
    std::ofstream(outside / "secret.txt") << "CANARY";
    wchar_t buf[MAX_PATH];
    const DWORD n = GetShortPathNameW((outside / "secret.txt").wstring().c_str(), buf, MAX_PATH);
    ASSERT_GT(n, 0u);
    ASSERT_LT(n, static_cast<DWORD>(MAX_PATH));
    const std::string shortPath = fs::path(buf).u8string();
    EXPECT_FALSE(ws.contains(shortPath)) << shortPath;
    EXPECT_NE(ws.checkPath(shortPath), "") << shortPath;
    EXPECT_NE(ws.checkCommand("type \"" + shortPath + "\""), "") << shortPath;
    // checkCommand leaves `..` to the read check, which judges a relative short name like its long name.
    const std::string up = (fs::path("..") / fs::path(buf).parent_path().filename() / "secret.txt").generic_u8string();
    const std::string upLong = (fs::path("..") / outside.filename() / "secret.txt").generic_u8string();
    EXPECT_FALSE(staysInside(ws, "cat " + up, d.work.string())) << up;
    EXPECT_FALSE(staysInside(ws, "cat " + upLong, d.work.string())) << upLong;
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


TEST(WorkspaceTest, AWriteThroughADanglingLinkInAnyPartOfThePathIsJudgedByItsTarget) {
    Dirs d;
    Workspace ws({d.work.string()});
    makeFolderLink(d.other / "not-yet", d.work / "gone");
    ASSERT_FALSE(fs::exists(d.work / "gone")) << "the link dangles";
    EXPECT_NE(ws.checkPath((d.work / "gone" / "new.txt").string()), "");
    EXPECT_NE(ws.checkPath((d.work / "gone" / "a" / "b" / "new.txt").string()), "") << "the folders would be made outside";
    EXPECT_NE(ws.checkPath((d.work / "fresh" / ".." / "gone" / "new.txt").string()), "")
        << "a folder not there yet doesn't hide a link after `..`";
    const std::string landing = resolveWriteTarget((d.work / "gone" / "a" / "new.txt").string());
    ASSERT_FALSE(landing.empty());
    EXPECT_EQ(fs::u8path(landing).filename().u8string(), "new.txt");
    EXPECT_TRUE(sameFile(fs::u8path(landing).parent_path().parent_path().parent_path().u8string(), d.other))
        << landing << " is under " << d.other;

    // A link inside a linked folder, the inner one dangling: nested junctions on Windows.
    fs::create_directories(d.other / "inner");
    makeFolderLink(d.other, d.work / "outer");
    makeFolderLink(d.base / "far", d.other / "inner" / "next");
    EXPECT_NE(ws.checkPath((d.work / "outer" / "inner" / "next" / "new.txt").string()), "");
    EXPECT_NE(ws.checkPath((d.work / "outer" / "new.txt").string()), "");

    // A link that stays inside is still inside.
    makeFolderLink(d.work / "sub", d.work / "alias");
    EXPECT_EQ(ws.checkPath((d.work / "alias" / "new.txt").string()), "");

    std::error_code ec;
    for (const fs::path& link : {d.work / "gone", d.work / "outer", d.other / "inner" / "next", d.work / "alias"}) {
        fs::remove(link, ec);
    }
}

TEST(WorkspaceTest, ResolveWriteTargetAgreesWithWhereTheSystemPutsTheFile) {
    Dirs d;
    fs::create_directories(d.other / "deep");
    makeFolderLink(d.other / "deep", d.work / "up");
    // `..` after a link: POSIX climbs from where the link leads, Windows from the link's own folder.
    const fs::path through = d.work / "up" / ".." / "landed.txt";
    const std::string predicted = resolveWriteTarget(through.string());
    ASSERT_FALSE(predicted.empty());
    { std::ofstream(through) << "x"; }
    const fs::path posixPlace = d.other / "landed.txt", windowsPlace = d.work / "landed.txt";
    ASSERT_NE(fs::exists(posixPlace), fs::exists(windowsPlace)) << "the write landed in exactly one place";
    EXPECT_TRUE(sameFile(predicted, fs::exists(posixPlace) ? posixPlace : windowsPlace)) << predicted;
    Workspace ws({d.work.string()});
    EXPECT_EQ(ws.contains(through.string()), fs::exists(windowsPlace));
    std::error_code ec;
    fs::remove(d.work / "up", ec);
}

#ifndef _WIN32
TEST(WorkspaceTest, AChainOfRelativeLinksAndALoopAreFollowedOrRefused) {
    Dirs d;
    Workspace ws({d.work.string()});
    fs::create_symlink("hop", d.work / "chain.md");
    fs::create_symlink(fs::path("..") / "other" / "chained.txt", d.work / "hop");
    EXPECT_NE(ws.checkPath((d.work / "chain.md").string()), "");
    fs::create_symlink("loop-b", d.work / "loop-a");
    fs::create_symlink("loop-a", d.work / "loop-b");
    EXPECT_EQ(resolveWriteTarget((d.work / "loop-a").string()), "") << "a loop leads nowhere";
    EXPECT_NE(ws.checkPath((d.work / "loop-a" / "x.txt").string()), "");
}
#endif

// A link that leads nowhere (here, to itself) is outside every folder, not read as the path it spells.
TEST(WorkspaceTest, ALinkThatCannotBeFollowedIsOutside) {
    Dirs d;
    Workspace ws({d.work.string()});
    InFolder in(d.work);
    const fs::path loop = d.work / "loop";
#ifdef _WIN32
    const std::string make = "cmd /c mklink /J \"" + loop.string() + "\" \"" + loop.string() + "\" >nul 2>&1";
    ASSERT_EQ(std::system(make.c_str()), 0) << make;
#else
    std::error_code made;
    fs::create_symlink("loop", loop, made);
    ASSERT_FALSE(made) << made.message();
#endif
    ASSERT_EQ(resolveWriteTarget((loop / "x.txt").string()), "") << "a link to itself leads nowhere";
    const std::string cwd = d.work.string();
    for (const char* through : {"cat loop/x.txt", "cat loop", "ls ./loop/a/../b"}) {
        EXPECT_NE(ws.checkCommand(through), "") << through;
        EXPECT_FALSE(staysInside(ws, through, cwd)) << through;
    }
    EXPECT_FALSE(ws.contains((loop / "x.txt").string()));
    for (const char* fine : {"cat sub/x.txt", "ls sub"}) {
        EXPECT_EQ(ws.checkCommand(fine), "") << fine;
        EXPECT_TRUE(staysInside(ws, fine, cwd)) << fine;
    }
    std::error_code ec;
    fs::remove(loop, ec);
}

TEST(WorkspaceTest, ALinkMadeAfterTheCheckIsCaughtWhenTheWriteRuns) {
    Dirs d;
    auto ws = std::make_shared<const Workspace>(std::vector<std::string>{d.work.string()});
    const ToolInfo write = Workspace::confine(FileIOTools::fileWrite(), ws, {"path"});
    const fs::path target = d.work / "late" / "new.txt";
    EXPECT_EQ(ws->checkPath(target.string()), "") << "nothing there yet: inside";
    makeFolderLink(d.other / "late", d.work / "late");
    const json result = write.callback({{"path", target.u8string()}, {"content", "x"}});
    ASSERT_TRUE(result.contains("error")) << result.dump();
    EXPECT_NE(result["error"].get<std::string>().find("Refused"), std::string::npos) << result.dump();
    EXPECT_FALSE(fs::exists(d.other / "late" / "new.txt"));
    std::error_code ec;
    fs::remove(d.work / "late", ec);
}
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
#else
TEST(WorkspaceTest, AJunctionThatLeadsOutMakesAWalkingReadAsk) {
    Dirs d;
    Workspace ws({d.work.string()});
    const std::string cwd = d.work.string();
    EXPECT_TRUE(staysInside(ws, "grep -R x .", cwd)) << "no junction yet";
    const fs::path junction = d.work / "sub" / "deps";
    // A junction needs no privilege, unlike a Windows symlink.
    const std::string mklink = "cmd /c mklink /J \"" + junction.string() + "\" \"" + d.other.string() + "\" >nul";
    ASSERT_EQ(std::system(mklink.c_str()), 0) << mklink;
    for (const char* asks : {"grep -R x .", "cat sub/*"}) {
        EXPECT_FALSE(staysInside(ws, asks, cwd)) << asks;
    }
    EXPECT_TRUE(staysInside(ws, "cat notes.txt", cwd));
    std::error_code ec;
    fs::remove(junction, ec);  // the junction, not what it points at
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

TEST(WorkspaceTest, AVeryLongWordInACommandIsCheckedWithoutACrash) {
    Dirs d;
    Workspace ws({d.work.string()});
    EXPECT_NO_THROW(ws.checkCommand("cat /" + std::string(60000, 'a')));
    EXPECT_NE(ws.checkCommand("cat " + d.other.generic_string() + "/" + std::string(60000, 'a')), "")
        << "a path outside the folder, however long";
    EXPECT_EQ(ws.checkCommand("echo " + std::string(60000, 'a')), "");
    EXPECT_NE(ws.checkCommand("dir \\\\" + std::string(60000, 'h') + "\\share"), "");
    // The hand-written scan finds what the pattern did.
#ifndef _WIN32  // under Git Bash /etc is inside Git's own folder
    EXPECT_NE(ws.checkCommand("cat \"/etc/../home/x/secret\""), "");
#endif
    EXPECT_NE(ws.checkCommand("cp a.txt ~/elsewhere"), "");
    EXPECT_NE(ws.checkCommand("type C:\\Windows\\win.ini,D:\\x"), "");
    EXPECT_EQ(ws.checkCommand("awk '{gsub(/ /, \"\"); print}' notes.txt"), "");
    EXPECT_EQ(ws.checkCommand("curl https://example.com/a/b"), "") << "the tail of a URL is not a path";
}
