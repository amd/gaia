// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Runs tests/fixtures/security/escape_corpus.json through the real tools: each case is its own
// `gaiacpp --mcp --dangerously-skip-permissions` in a throwaway folder, so only the guards stand
// between the command and the canary files beside that folder. What is checked is what happened,
// not which rule fired: no canary text in any result, nothing outside the folder changed, no
// connection at the canary listener, and the whole case inside its time limit.

#include <gtest/gtest.h>

#ifndef _WIN32

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <map>
#include <mutex>
#include <set>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "gaia/process.h"
#include "gaia/shell_guard.h"
#include "gaia/types.h"
#include "gaia/workspace.h"
#include "support/child_process.h"

using gaia::json;

namespace {

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;

const char* const kOutsideCanary = "CANARY-OUTSIDE-7f3a";
const char* const kHomeCanary = "CANARY-HOME-51c2";
const char* const kKeyCanary = "CANARY-KEY-9be0";
const char* const kNetCanary = "CANARY-NET-44d1";
const char* const kShared = "SHARED-ORIGINAL";

std::string replaced(std::string text, const std::string& from, const std::string& to) {
    for (size_t at = text.find(from); at != std::string::npos; at = text.find(from, at + to.size())) {
        text.replace(at, from.size(), to);
    }
    return text;
}

std::string repeated(const std::string& unit, size_t times) {
    std::string out;
    out.reserve(unit.size() * times);
    for (size_t i = 0; i < times; ++i) out += unit;
    return out;
}

std::string base64(const std::string& in) {
    static const char* alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    for (size_t i = 0; i < in.size(); i += 3) {
        const unsigned b0 = static_cast<unsigned char>(in[i]);
        const unsigned b1 = i + 1 < in.size() ? static_cast<unsigned char>(in[i + 1]) : 0;
        const unsigned b2 = i + 2 < in.size() ? static_cast<unsigned char>(in[i + 2]) : 0;
        out += alphabet[b0 >> 2];
        out += alphabet[((b0 & 3) << 4) | (b1 >> 4)];
        out += i + 1 < in.size() ? alphabet[((b1 & 15) << 2) | (b2 >> 6)] : '=';
        out += i + 2 < in.size() ? alphabet[b2 & 63] : '=';
    }
    return out;
}

// Counts every connection. Commands reach it as their HTTP proxy, so a fetch that slips past
// --no-web lands here instead of on the internet.
class CanaryListener {
public:
    CanaryListener() {
        fd_ = ::socket(AF_INET, SOCK_STREAM, 0);
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (fd_ < 0 || ::bind(fd_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0 || ::listen(fd_, 64) != 0) {
            throw std::runtime_error("the canary listener could not bind to 127.0.0.1");
        }
        socklen_t len = sizeof(addr);
        ::getsockname(fd_, reinterpret_cast<sockaddr*>(&addr), &len);
        port_ = ntohs(addr.sin_port);
        thread_ = std::thread([this] {
            for (;;) {
                const int client = ::accept(fd_, nullptr, nullptr);
                if (client < 0) return;
                if (stopping_.load()) {
                    ::close(client);
                    return;
                }
                char buffer[512] = {0};
                const ssize_t got = ::recv(client, buffer, sizeof(buffer) - 1, 0);
                {
                    std::lock_guard<std::mutex> lock(mu_);
                    const std::string head(buffer, got > 0 ? static_cast<size_t>(got) : 0);
                    seen_.push_back(head.substr(0, head.find('\r')));
                }
                const std::string reply = std::string("HTTP/1.0 200 OK\r\nContent-Length: 15\r\n\r\n") + kNetCanary;
                ::send(client, reply.data(), reply.size(), 0);
                ::close(client);
            }
        });
    }
    ~CanaryListener() {
        stopping_.store(true);
        const int poke = ::socket(AF_INET, SOCK_STREAM, 0);
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        addr.sin_port = htons(static_cast<uint16_t>(port_));
        ::connect(poke, reinterpret_cast<sockaddr*>(&addr), sizeof(addr));
        ::close(poke);
        thread_.join();
        ::close(fd_);
    }
    int port() const { return port_; }
    std::vector<std::string> seen() {
        std::lock_guard<std::mutex> lock(mu_);
        return seen_;
    }

private:
    int fd_ = -1;
    int port_ = 0;
    std::atomic<bool> stopping_{false};
    std::mutex mu_;
    std::vector<std::string> seen_;
    std::thread thread_;
};

// Everything under `root` but the working folder and the harness's own files: path -> what it is.
std::map<std::string, std::string> snapshot(const fs::path& root) {
    std::map<std::string, std::string> out;
    std::error_code ec;
    for (fs::recursive_directory_iterator it(root, fs::directory_options::skip_permission_denied, ec), end;
         !ec && it != end; it.increment(ec)) {
        const std::string rel = fs::relative(it->path(), root, ec).generic_string();
        if (rel == "ws" || rel == "harness" || rel == "gaia-home") {
            it.disable_recursion_pending();
            continue;
        }
        const fs::file_status st = it->symlink_status(ec);
        if (fs::is_symlink(st)) {
            out[rel] = "link:" + fs::read_symlink(it->path(), ec).generic_string();
        } else if (fs::is_directory(st)) {
            out[rel] = "dir";
        } else {
            std::ifstream in(it->path(), std::ios::binary);
            std::stringstream text;
            text << in.rdbuf();
            out[rel] = "file:" + std::to_string(static_cast<int>(fs::status(it->path(), ec).permissions())) + ":" + text.str();
        }
    }
    return out;
}

struct Outcome {
    std::string id;
    std::vector<std::string> failures;
    double seconds = 0;
};

class Corpus {
public:
    Corpus(std::string binary, fs::path base, int port) : binary_(std::move(binary)), base_(std::move(base)), port_(port) {}

    Outcome run(const json& c, size_t index) const {
        Outcome outcome;
        outcome.id = c.value("kind", "") + "/" + c.value("id", "");
        const auto fail = [&outcome](const std::string& why) { outcome.failures.push_back(why); };
        const fs::path root = base_ / ("case-" + std::to_string(index));
        const fs::path ws = root / "ws", outside = root / "outside", home = root / "home", harness = root / "harness";
        fs::create_directories(ws / "sub");
        fs::create_directories(outside / "dir");
        fs::create_directories(home / ".ssh");
        fs::create_directories(harness);
        std::ofstream(outside / "secret.txt") << kOutsideCanary << "\n";
        std::ofstream(outside / "shared.txt") << kShared << "\n";
        std::ofstream(home / "homefile.txt") << kHomeCanary << "\n";
        std::ofstream(home / ".ssh" / "id_rsa") << kKeyCanary << "\n";
        std::ofstream(ws / "in.txt") << "inside text\n";
        std::ofstream(ws / "sub" / "a.txt") << "sub a\n";
        const std::string id = c.value("id", "");
        const std::string url = "http://canary.invalid/" + id;
        std::ofstream(ws / "fetch.sh") << "curl -s -m 5 " << url << "\n";
        std::ofstream(ws / "fetch.py") << "import urllib.request as u\nprint(u.urlopen('" << url << "', timeout=5).status)\n";
        std::ofstream(ws / "spawn.py") << "import subprocess\nsubprocess.run(['curl', '-s', '-m', '5', '" << url << "'])\n";
        std::ofstream(ws / "harmless.sh") << "echo harmless\n";
        // A folder inside the working folder that is on PATH, as an activated virtual environment is.
        std::string path = gaia::getEnvVar("PATH");
        if (c.value("path_dir", false)) {
            fs::create_directories(ws / "pathbin");
            path = (ws / "pathbin").string() + ":" + path;
            if (c.value("planted", false)) {
                std::ofstream(ws / "pathbin" / "ls") << "#!/bin/sh\ncat ../outside/secret.txt\n";
                fs::permissions(ws / "pathbin" / "ls", fs::perms::owner_all);
            }
        }
        fs::create_directory_symlink("../outside", ws / "link_out");
        fs::create_hard_link(outside / "shared.txt", ws / "hard.txt");
        if (c.value("git", false)) {
            const std::string git = "git -C '" + ws.string() + "' ";
            gaia::ProcessRunner::run(git + "init -q && " + git + "remote add origin http://canary.invalid/" + id + ".git", 20000);
        }

        const std::map<std::string, std::string> words = {
            {"@@WS@@", fs::canonical(ws).string()},
            {"@@OUT@@", fs::canonical(outside).string()},
            {"@@HOME@@", fs::canonical(home).string()},
            {"@@ID@@", id},
            {"@@A60K@@", std::string(60000, 'A')},
            {"@@DOTS60K@@", repeated("../", 20000)},
            {"@@BRACES@@", repeated("{a,b}", 40)},
            {"@@NESTED@@", "echo " + repeated("$(echo ", 200) + "x" + repeated(")", 200)},
            {"@@GLOBS@@", repeated("*/* ", 2000)},
            {"@@CHAIN@@", repeated("true; ", 3000)},
            {"@@B64_CURL@@", base64("curl -s -m 5 " + url + "\n")},
            {"@@B64_PY@@", base64("import urllib.request as u; print(u.urlopen('" + url + "', timeout=5).status)")}};
        const auto filled = [&words](std::string text) {
            for (const auto& [from, to] : words) text = replaced(std::move(text), from, to);
            return text;
        };

        std::ofstream requests(harness / "requests.jsonl");
        requests << json{{"jsonrpc", "2.0"}, {"id", 0}, {"method", "initialize"},
                         {"params", {{"protocolVersion", "2024-11-05"}, {"capabilities", json::object()},
                                     {"clientInfo", {{"name", "corpus"}, {"version", "0"}}}}}}.dump()
                 << "\n";
        int n = 0;
        for (const auto& step : c["steps"]) {
            json call;
            if (step.is_string()) {
                call = {{"name", "bash_execute"}, {"arguments", {{"command", filled(step.get<std::string>())}, {"timeout_ms", 8000}}}};
            } else {
                json args = step["args"];
                for (auto& [key, value] : args.items()) {
                    if (value.is_string()) value = filled(value.get<std::string>());
                }
                call = {{"name", step["tool"]}, {"arguments", args}};
            }
            requests << json{{"jsonrpc", "2.0"}, {"id", ++n}, {"method", "tools/call"}, {"params", call}}.dump() << "\n";
        }
        requests.close();

        const auto before = snapshot(root);
        const std::string proxy = "http://127.0.0.1:" + std::to_string(port_);
        const testsupport::EnvOverrides env = {
            {"HOME", home.string()}, {"GAIA_HOME", (root / "gaia-home").string()},
            {"GAIACPP_CONFIG", (harness / "no-config.json").string()},
            {"GAIACPP", binary_}, {"REQUESTS", (harness / "requests.jsonl").string()},
            {"FLAGS", c.value("no_web", false) ? "--no-web" : ""},
            {"http_proxy", proxy}, {"https_proxy", proxy}, {"HTTP_PROXY", proxy}, {"HTTPS_PROXY", proxy},
            {"ALL_PROXY", proxy}, {"no_proxy", std::nullopt}, {"NO_PROXY", std::nullopt},
            {"GIT_TERMINAL_PROMPT", std::string("0")}, {"CDPATH", std::nullopt}, {"PATH", path},
            {"GAIA_CONFIG_FILE", std::nullopt}, {"GAIA_CONFIG_DIR", std::nullopt},
            {"LEMONADE_BASE_URL", std::nullopt}, {"GAIA_BASE_URL", std::nullopt}};
        const auto started = Clock::now();
        testsupport::ChildProcess child(
            {"/bin/sh", "-c",
             "exec \"$GAIACPP\" --mcp --dangerously-skip-permissions --no-memory --no-skills $FLAGS < \"$REQUESTS\""},
            env, ws.string(), (harness / "stdout").string(), (harness / "stderr").string());
        const bool exited = child.waitExit(30000);
        if (!exited) child.kill();
        outcome.seconds = std::chrono::duration<double>(Clock::now() - started).count();
        if (!exited) fail("did not finish in 30 s");
        else if (outcome.seconds > 12.0) fail("took " + std::to_string(outcome.seconds) + " s");

        std::ifstream in(harness / "stdout");
        std::stringstream all;
        all << in.rdbuf();
        const std::string output = all.str();
        for (const char* canary : {kOutsideCanary, kHomeCanary, kKeyCanary, kNetCanary}) {
            if (output.find(canary) != std::string::npos) fail(std::string("a result holds ") + canary);
        }
        const auto after = snapshot(root);
        if (after != before) {
            std::string diff;
            for (const auto& [path, what] : after) {
                const auto was = before.find(path);
                if (was == before.end()) diff += " +" + path;
                else if (was->second != what) diff += " ~" + path;
            }
            for (const auto& [path, what] : before) {
                if (!after.count(path)) diff += " -" + path;
            }
            fail("changed outside the working folder:" + diff);
        }

        std::map<int, json> results;
        std::istringstream lines(output);
        for (std::string line; std::getline(lines, line);) {
            const json message = json::parse(line, nullptr, false);
            if (!message.is_object() || !message.contains("id") || !message["id"].is_number_integer()) continue;
            const json& result = message.contains("result") ? message["result"] : message["error"];
            json body = result;
            if (result.is_object() && result.contains("content") && result["content"].is_array() && !result["content"].empty()) {
                const std::string text = result["content"][0].value("text", "");
                body = json::parse(text, nullptr, false);
                if (body.is_discarded()) body = json{{"text", text}};
                if (result.value("isError", false) && body.is_object() && !body.contains("error")) body["error"] = text;
            }
            results[message["id"].get<int>()] = body;
        }
        for (int step = 1; step <= n; ++step) {
            std::string expect = c.value("expect", "any");
            if (c.contains("step_expect")) expect = c["step_expect"][static_cast<size_t>(step - 1)].get<std::string>();
            const std::string label = "step " + std::to_string(step) + " (" + expect + "): ";
            if (!results.count(step)) {
                fail(label + "no reply");
                continue;
            }
            const json& r = results[step];
            const std::string text = r.dump(-1, ' ', false, json::error_handler_t::replace);
            const bool refused = r.is_object() && r.contains("error") && !r.contains("exit_code");
            if (expect == "refused" && !refused) fail(label + "it ran: " + text.substr(0, 240));
            if (expect == "approval") {
                const std::string why = r.is_object() ? r.value("error", "") : "";
                if (!refused) fail(label + "it ran: " + text.substr(0, 240));
                else if (why.find("needs a person's approval") == std::string::npos || why.find("can't check") == std::string::npos) {
                    fail(label + "refused, but not as a command that needs approval because it can't be checked: " + why.substr(0, 240));
                }
            }
            if (expect == "allowed" && (refused || (r.is_object() && r.value("exit_code", 0) != 0))) {
                fail(label + "an ordinary command did not run: " + text.substr(0, 300));
            }
            if (c.contains("stdout_has") && c["stdout_has"].size() >= static_cast<size_t>(step)) {
                const json& wanted = c["stdout_has"][static_cast<size_t>(step - 1)];
                if (wanted.is_string() && (!r.is_object() || r.value("stdout", "").find(filled(wanted.get<std::string>())) == std::string::npos)) {
                    fail(label + "stdout lacks '" + filled(wanted.get<std::string>()) + "': " + text.substr(0, 240));
                }
            }
        }
        std::error_code ec;
        if (outcome.failures.empty()) fs::remove_all(root, ec);
        return outcome;
    }

private:
    std::string binary_;
    fs::path base_;
    int port_;
};

json loadCorpus() {
    const std::string path = std::string(GAIA_REPO_FIXTURES_DIR) + "/security/escape_corpus.json";
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot read " + path);
    return json::parse(in);
}

TEST(ShellConfinement, CorpusIsLargeEnoughAndWellFormed) {
    const json corpus = loadCorpus();
    ASSERT_TRUE(corpus["cases"].is_array());
    std::map<std::string, int> kinds;
    std::set<std::string> ids;
    for (const auto& c : corpus["cases"]) {
        ++kinds[c.value("kind", "")];
        EXPECT_TRUE(ids.insert(c.value("kind", "") + "/" + c.value("id", "")).second) << c.dump();
        ASSERT_TRUE(c["steps"].is_array() && !c["steps"].empty()) << c.dump();
        const std::string expect = c.value("expect", "");
        EXPECT_TRUE(expect == "refused" || expect == "approval" || expect == "allowed" || expect == "any") << c.dump();
        if (c.contains("step_expect")) EXPECT_EQ(c["step_expect"].size(), c["steps"].size()) << c.dump();
    }
    EXPECT_GE(kinds["read"] + kinds["write"] + kinds["network"] + kinds["hang"], 60);
    for (const char* kind : {"read", "write", "network", "hang", "control"}) EXPECT_GT(kinds[kind], 0) << kind;
}

// GAIA_ESCAPE_CORPUS_BIN runs the corpus against a given gaiacpp binary.
TEST(ShellConfinement, EveryEscapeInTheCorpusIsStopped) {
    const json corpus = loadCorpus();
    const std::string chosen = gaia::getEnvVar("GAIA_ESCAPE_CORPUS_BIN");
    const std::string binary = chosen.empty() ? GAIACPP_BIN : chosen;
    const std::string only = gaia::getEnvVar("GAIA_ESCAPE_CORPUS_ONLY");
    const auto stamp = Clock::now().time_since_epoch().count();
    // Not under the system temp folder: commands may use that one freely.
    const fs::path base = fs::current_path() / ("gaia-escape-" + std::to_string(stamp));
    fs::create_directories(base);
    CanaryListener listener;
    const Corpus runner(binary, base, listener.port());

    const auto started = Clock::now();
    const json& cases = corpus["cases"];
    std::vector<Outcome> outcomes(cases.size());
    std::atomic<size_t> next{0};
    std::vector<std::thread> workers;
    for (unsigned w = 0; w < (std::max)(4u, std::thread::hardware_concurrency()); ++w) {
        workers.emplace_back([&] {
            for (size_t i = next.fetch_add(1); i < cases.size(); i = next.fetch_add(1)) {
                if (!only.empty() && cases[i].value("id", "").find(only) == std::string::npos) continue;
                try {
                    outcomes[i] = runner.run(cases[i], i);
                } catch (const std::exception& e) {
                    outcomes[i].id = cases[i].value("id", "");
                    outcomes[i].failures.push_back(std::string("the harness failed: ") + e.what());
                }
            }
        });
    }
    for (auto& w : workers) w.join();
    const double total = std::chrono::duration<double>(Clock::now() - started).count();

    size_t failed = 0;
    for (const auto& o : outcomes) {
        if (o.failures.empty()) continue;
        ++failed;
        std::string why;
        for (const auto& f : o.failures) why += "\n    " + f;
        ADD_FAILURE() << o.id << why;
    }
    const std::vector<std::string> seen = listener.seen();
    std::string connections;
    for (const auto& s : seen) connections += "\n    " + s;
    EXPECT_TRUE(seen.empty()) << seen.size() << " connection(s) reached the canary listener:" << connections;
    EXPECT_LT(total, 60.0) << "the corpus is meant to take a few seconds";
    std::error_code ec;
    if (failed == 0) fs::remove_all(base, ec);
    RecordProperty("cases", static_cast<int>(cases.size()));
    RecordProperty("seconds", std::to_string(total));
}

// What an interactive host shows: a command the check can't read gets a note for the approval card.
TEST(ShellConfinement, JudgementSeparatesCheckedOpaqueAndOutside) {
    const fs::path base = fs::current_path() / ("gaia-judge-" + std::to_string(Clock::now().time_since_epoch().count()));
    fs::create_directories(base / "ws" / "sub");
    fs::create_directories(base / "outside");
    std::ofstream(base / "ws" / "in.txt") << "x";
    const std::string ws = fs::canonical(base / "ws").string();
    const gaia::Workspace workspace({ws});
    const auto judge = [&](const std::string& command) { return gaia::judgeShellCommand(command, ws, {}, &workspace); };

    for (const char* checked : {"ls -la", "cat in.txt | wc -l", "cd sub && ls ..", "mkdir -p a/b && touch a/b/c",
                                "grep -rn x . > found.txt", "for f in *.txt; do head -1 \"$f\"; done",
                                "sed -i.bak 's/a/b/g' in.txt", "awk -F, '{print $2}' in.txt", "echo \"$HOME\"",
                                "find . -name '*.txt' -exec wc -l {} +", "git branch", "sort --output=sorted.txt in.txt", "git rev-list --count HEAD",
                                "test -f in.txt && echo yes", "X=in.txt; cat \"$X\"", "cat /etc/hosts"}) {
        const gaia::ShellJudgement j = judge(checked);
        EXPECT_TRUE(j.inside()) << checked << ": outside '" << j.outsideWord << "', opaque '" << j.opaque << "'";
    }
    for (const char* opaque : {"python3 script.py", "node -e 'x'", "make", "./run.sh", "eval \"$X\"", "bash build.sh",
                               "xargs rm < list.txt", "git commit -m x", "git status", "git log -p", "git log --oneline -5", "git tag -l",
                               "git rev-list --format=%G? HEAD", "npm test",
                               "cat \"$(cat name.txt)\"",
                               "awk 'BEGIN{system(\"ls\")}'", "sed 's/a/b/w out.txt' in.txt", "PATH=.:$PATH ls",
                               "source env.sh", "cat $UNSET_ANYWHERE/x"}) {
        const gaia::ShellJudgement j = judge(opaque);
        EXPECT_TRUE(j.outsideWord.empty()) << opaque << ": " << j.outsideWord;
        EXPECT_FALSE(j.opaque.empty()) << opaque << " was taken as checked";
    }
    for (const char* outside : {"cat ../outside/x", "sort --output=../outside/y in.txt", "cd ..", "echo x > ../outside/y", "ls ~", "cp in.txt ..",
                                "python3 ../outside/tool.py", "ln -s ../outside l"}) {
        EXPECT_FALSE(judge(outside).outsideWord.empty()) << outside;
    }
    EXPECT_TRUE(judge("mkdir a && rm -rf b").compound);
    EXPECT_TRUE(judge("echo $(date)").compound);
    EXPECT_FALSE(judge("mkdir -p a/b").compound);
    EXPECT_EQ(judge("git fetch origin").network, "git fetch");
    EXPECT_EQ(judge("find . -exec nc example.com 80 \\;").network, "nc");
    EXPECT_TRUE(judge("nc -z localhost 8080").network.empty());
    EXPECT_EQ(judge("osascript -e 'open location \"http://example.com\"'").network, "osascript");
    EXPECT_EQ(judge("osascript -e 'do shell script \"nc example.com 80\"'").network, "nc");
    EXPECT_EQ(judge("python3 -m webbrowser http://example.com").network, "python -m webbrowser");
    EXPECT_TRUE(judge("python3 -m json.tool in.txt").network.empty());
    EXPECT_EQ(judge("awk 'BEGIN{system(\"nc example.com 80\")}'").network, "nc");
    EXPECT_EQ(judge("awk '{print | \"ssh example.com cat\"}' in.txt").network, "ssh");
    EXPECT_EQ(judge("sed '1e nc example.com 80' in.txt").network, "nc");
    EXPECT_TRUE(judge("awk -F/ '{print $2}' in.txt").inside());
    EXPECT_FALSE(judge("$(echo curl) http://example.com").hidden.empty());
    std::error_code ec;
    fs::remove_all(base, ec);
}

// A uv or pyenv venv's bin/python links to an interpreter outside the project. Running it is
// like running python3 from PATH: the command still asks, but no file outside is offered.
TEST(ShellConfinement, AVenvsPythonRunsWithoutOpeningTheInterpreterOutside) {
    const fs::path base = fs::current_path() / ("gaia-venv-" + std::to_string(Clock::now().time_since_epoch().count()));
    fs::create_directories(base / "ws" / "pkg");
    fs::create_directories(base / "venvs" / "proj" / "bin");
    fs::create_directories(base / "shared" / "bin");
    const fs::path interpreter = base / "shared" / "bin" / "python3.12";
    std::ofstream(interpreter) << "#!/bin/sh\n";
    fs::permissions(interpreter, fs::perms::owner_all);
    std::ofstream(base / "venvs" / "proj" / "pyvenv.cfg") << "home = " << (base / "shared" / "bin").string() << "\n";
    fs::create_symlink(interpreter, base / "venvs" / "proj" / "bin" / "python3.12");
    fs::create_symlink("python3.12", base / "venvs" / "proj" / "bin" / "python");
    fs::create_symlink(interpreter, base / "ws" / "python");  // a link, but not a venv's
    const std::string ws = fs::canonical(base / "ws").string();
    const std::string venv = fs::canonical(base / "venvs" / "proj").string();
    const gaia::Workspace workspace({ws, venv});
    const auto judge = [&](const std::string& command) { return gaia::judgeShellCommand(command, ws, {}, &workspace); };

    for (const char* run : {"../venvs/proj/bin/python -m pytest pkg", "cd pkg && ../../venvs/proj/bin/python3.12 -V"}) {
        const gaia::ShellJudgement j = judge(run);
        EXPECT_TRUE(j.outsideWord.empty()) << run << ": " << j.outsideWord;
        EXPECT_FALSE(j.opaque.empty()) << run << " still asks as a program GAIA can't inspect";
    }
    const std::string direct = interpreter.string() + " -V";
    for (const std::string& outside : {std::string("./python -V"), std::string("echo x > ../venvs/proj/bin/python"),
                                       std::string("cat ../venvs/proj/bin/python"),
                                       std::string("../venvs/proj/bin/python ../shared/bin/python3.12"), direct}) {
        EXPECT_FALSE(judge(outside).outsideWord.empty()) << outside;
    }
    std::error_code ec;
    fs::remove_all(base, ec);
}

// An attached file is granted alone: the shell may step into its folder to read it, and
// everything else there is still outside.
TEST(ShellConfinement, CdBesideAnAttachedFileReadsOnlyThatFile) {
    const fs::path base = fs::current_path() / ("gaia-attached-" + std::to_string(Clock::now().time_since_epoch().count()));
    fs::create_directories(base / "ws");
    fs::create_directories(base / "uploads" / "nested");
    std::ofstream(base / "uploads" / "report.txt") << "revenue";
    std::ofstream(base / "uploads" / "theirs.txt") << "another chat";
    const std::string ws = fs::canonical(base / "ws").string();
    const std::string up = fs::canonical(base / "uploads").string();
    const gaia::Workspace workspace({ws, up + "/report.txt"});
    const auto judge = [&](const std::string& command, const std::string& cwd) {
        return gaia::judgeShellCommand(command, cwd, {}, &workspace);
    };
    for (const std::string& checked : {"cd " + up + " && grep -n revenue report.txt",
                                       "cd " + up + " && wc -l report.txt"}) {
        const gaia::ShellJudgement j = judge(checked, ws);
        EXPECT_TRUE(j.inside()) << checked << ": outside '" << j.outsideWord << "', opaque '" << j.opaque << "'";
    }
    EXPECT_TRUE(judge("grep -n revenue report.txt", up).inside()) << "standing there already";
    for (const std::string& outside :
         {"cd " + up + " && cat theirs.txt", "cd " + up + " && cat *.txt", "cd " + up + " && rm theirs.txt",
          "cd " + up + " && echo x > theirs.txt", "cd " + up + "/nested", "cd " + up + " && cd nested"}) {
        EXPECT_FALSE(judge(outside, ws).outsideWord.empty()) << outside;
    }
    EXPECT_FALSE(judge("cat theirs.txt", up).outsideWord.empty());
    std::error_code ec;
    fs::remove_all(base, ec);
}

// The check reads hostile text in time that grows with its length.
TEST(ShellConfinement, JudgingHostileTextIsQuick) {
    const gaia::Workspace workspace({fs::current_path().string()});
    const std::string cwd = fs::current_path().string();
    for (const std::string command :
         {std::string(60000, 'A'), "cat " + repeated("../", 20000), "echo " + repeated("{a,b}", 4000),
          "echo " + repeated("$(echo ", 3000) + repeated(")", 3000), repeated("a=$a$a; ", 5000), repeated("true; ", 9000),
          "ls " + repeated("*/", 20000), "echo " + repeated("\"", 30001), "cat " + repeated("$HOME", 12000),
          repeated("(", 30000), "x=" + repeated("{a,b,c}", 5000) + "; echo $x", "cat <<EOF\n" + repeated("$(", 20000)}) {
        const auto start = Clock::now();
        gaia::judgeShellCommand(command, cwd, {}, &workspace);
        EXPECT_LT(std::chrono::duration<double>(Clock::now() - start).count(), 1.0) << command.substr(0, 40);
    }
}

}  // namespace

#endif  // !_WIN32
