// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// gaiacpp reads what GAIA set up: its own model server, its key, and the chosen
// model, from a GAIA home in a fresh temp folder. And a model that isn't on
// disk is downloaded only after a yes.

#include <gtest/gtest.h>

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <map>
#include <optional>
#include <sstream>
#include <string>
#include <thread>

#include <httplib.h>

#ifdef _WIN32
#include <process.h>
#else
#include <unistd.h>
#endif

#include "../agents/gaiacpp/gaia_setup.h"
#include "support/mock_llm_server.h"

using namespace gaia;
namespace fs = std::filesystem;

namespace {

void setEnv(const std::string& name, const std::optional<std::string>& value) {
#ifdef _WIN32
    _putenv_s(name.c_str(), value ? value->c_str() : "");
#else
    if (value) setenv(name.c_str(), value->c_str(), 1);
    else unsetenv(name.c_str());
#endif
}

// Sets variables for one test and puts every one back afterwards.
class ScopedEnv {
public:
    void set(const std::string& name, const std::optional<std::string>& value) {
        if (!saved_.count(name)) {
            const char* old = std::getenv(name.c_str());
            saved_[name] = old ? std::optional<std::string>(old) : std::nullopt;
        }
        setEnv(name, value);
    }
    ~ScopedEnv() {
        for (const auto& [name, value] : saved_) setEnv(name, value);
    }

private:
    std::map<std::string, std::optional<std::string>> saved_;
};

long long ownPid() {
#ifdef _WIN32
    return _getpid();
#else
    return getpid();
#endif
}

class GaiaSetup : public ::testing::Test {
protected:
    void SetUp() override {
        const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
        home_ = fs::temp_directory_path() / ("gaiacpp-setup-" + std::to_string(stamp));
        fs::create_directories(home_ / "lemonade");
        // Nothing from this machine's own GAIA or shell may leak in.
        env_.set("GAIA_HOME", home_.string());
        for (const char* v : {"GAIA_CONFIG_FILE", "GAIA_CONFIG_DIR", "LEMONADE_BASE_URL", "LEMONADE_API_KEY",
                              "GAIA_LEMONADE_EMBEDDED", "GAIA_BASE_URL", "GAIA_API_KEY"}) {
            env_.set(v, std::nullopt);
        }
    }
    void TearDown() override {
        std::error_code ec;
        fs::remove_all(home_, ec);
    }

    void writeConfig(const json& j) { std::ofstream(home_ / "config.json") << j.dump(); }
    void writeState(int port, const std::string& key, long long pid) {
        std::ofstream(home_ / "lemonade" / "state.json")
            << json{{"pid", pid}, {"port", port}, {"api_key", key}, {"version", "10.0.0"}}.dump();
    }
    void installStopped(int lastPort) {
        fs::create_directories(home_ / "lemonade" / "dist" / "10.0.0");
        std::ofstream(home_ / "lemonade" / "dist" / "10.0.0" / "lemond") << "";
        std::ofstream(home_ / "lemonade" / "last_port.json") << json{{"port", lastPort}}.dump();
    }

    fs::path home_;
    ScopedEnv env_;
};

TEST_F(GaiaSetup, FindsGaiasRunningServerAndItsKey) {
    writeState(41234, "embedded-key", ownPid());
    const GaiaServer s = resolveGaiaServer();
    EXPECT_EQ(s.baseUrl, "http://localhost:41234/api/v1");
    EXPECT_EQ(s.origin.source, ServerSource::GaiaRunning);
    EXPECT_EQ(embeddedKeyFor(s, s.baseUrl), "embedded-key");
    EXPECT_EQ(embeddedKeyFor(s, "http://127.0.0.1:41234/api/v1"), "embedded-key");
}

TEST_F(GaiaSetup, EmbeddedKeyGoesOnlyToThatServer) {
    writeState(41234, "embedded-key", ownPid());
    const GaiaServer s = resolveGaiaServer();
    EXPECT_EQ(embeddedKeyFor(s, "http://localhost:13305/api/v1"), "") << "another port";
    EXPECT_EQ(embeddedKeyFor(s, "https://localhost:41234/api/v1"), "") << "another scheme";
    EXPECT_EQ(embeddedKeyFor(s, "http://example.com:41234/api/v1"), "") << "another host";
    EXPECT_EQ(embeddedKeyFor(s, "http://u@localhost:41234/api/v1"), "");
}

TEST_F(GaiaSetup, DeadServerRecordIsNoServer) {
    writeState(41234, "embedded-key", 0x7ffffffe);
    const GaiaServer s = resolveGaiaServer();
    EXPECT_EQ(s.origin.source, ServerSource::Default);
    EXPECT_EQ(s.baseUrl, "http://localhost:13305/api/v1");
    EXPECT_EQ(embeddedKeyFor(s, "http://localhost:41234/api/v1"), "");
}

TEST_F(GaiaSetup, StoppedGaiaServerResolvesToItsLastPort) {
    installStopped(41999);
    const GaiaServer s = resolveGaiaServer();
    EXPECT_EQ(s.origin.source, ServerSource::GaiaStopped);
    EXPECT_EQ(s.baseUrl, "http://localhost:41999/api/v1");
}

TEST_F(GaiaSetup, UsersLemonadeBaseUrlWins) {
    writeState(41234, "embedded-key", ownPid());
    env_.set("LEMONADE_BASE_URL", "http://192.168.1.5:8000/api/v1");
    const GaiaServer s = resolveGaiaServer();
    EXPECT_EQ(s.origin.source, ServerSource::LemonadeEnv);
    EXPECT_EQ(s.baseUrl, "http://192.168.1.5:8000/api/v1");
    EXPECT_FALSE(s.envKeyForeign);
}

TEST_F(GaiaSetup, GaiasOwnExportFollowsTheLiveState) {
    // env.sh from an earlier start: the port and key moved since.
    writeState(41234, "new-key", ownPid());
    env_.set("LEMONADE_BASE_URL", "http://localhost:40000/api/v1");
    env_.set("GAIA_LEMONADE_EMBEDDED", "1");
    const GaiaServer s = resolveGaiaServer();
    EXPECT_EQ(s.origin.source, ServerSource::GaiaRunning);
    EXPECT_EQ(s.baseUrl, "http://localhost:41234/api/v1");
    EXPECT_TRUE(s.envKeyForeign) << "env.sh's key may be stale";
    EXPECT_TRUE(runningGaiaServer().envKeyForeign);
}

TEST_F(GaiaSetup, AForeignEnvKeyIsDroppedAndAUsersKeyIsKept) {
    env_.set("LEMONADE_API_KEY", "some-key");
    GaiaServer s = runningGaiaServer();
    dropForeignLemonadeKey(s);
    EXPECT_EQ(getEnvVar("LEMONADE_API_KEY"), "some-key");
    s.envKeyForeign = true;
    dropForeignLemonadeKey(s);
    EXPECT_EQ(getEnvVar("LEMONADE_API_KEY"), "");
}

TEST_F(GaiaSetup, ValueTheAppChoseGaiasOwnOverIsIgnored) {
    writeState(41234, "embedded-key", ownPid());
    writeConfig({{"lemonade_env_url_ignored", "http://localhost:13305"}});
    env_.set("LEMONADE_BASE_URL", "http://localhost:13305/api/v1");
    const GaiaServer s = resolveGaiaServer();
    EXPECT_EQ(s.origin.source, ServerSource::GaiaRunning);
    EXPECT_TRUE(s.envKeyForeign) << "that server's key is not GAIA's server's";
}

TEST_F(GaiaSetup, SavedExternalServer) {
    writeState(41234, "embedded-key", ownPid());
    writeConfig({{"lemonade_mode", "external"}, {"lemonade_base_url", "http://10.0.0.2:8000"},
                 {"lemonade_api_key_stored", true}});
    const GaiaServer s = resolveGaiaServer();
    EXPECT_EQ(s.origin.source, ServerSource::SavedExternal);
    EXPECT_EQ(s.baseUrl, "http://10.0.0.2:8000/api/v1");
    EXPECT_TRUE(s.savedKeyElsewhere);
}

TEST_F(GaiaSetup, ExternalWithoutUrlFailsLoudly) {
    writeConfig({{"lemonade_mode", "external"}});
    EXPECT_THROW(resolveGaiaServer(), std::runtime_error);
}

TEST_F(GaiaSetup, ConfigDirOverridesHomeForConfigOnly) {
    const fs::path other = home_ / "elsewhere";
    fs::create_directories(other);
    std::ofstream(other / "config.json") << json{{"default_model", "Qwen3.5-4B-GGUF"}}.dump();
    env_.set("GAIA_CONFIG_DIR", other.string());
    EXPECT_EQ(gaiaConfigFile(), other / "config.json");
    EXPECT_EQ(resolveGaiaModel(), "Qwen3.5-4B-GGUF");
    EXPECT_EQ(gaiaLemonadeDir(), home_ / "lemonade");
}

TEST_F(GaiaSetup, ModelIsDefaultModelThenLastModelThenGemma) {
    EXPECT_EQ(resolveGaiaModel(), kGaiaFallbackModel) << "no config.json";
    writeConfig({{"last_model", "Qwen3.5-2B-GGUF"}});
    EXPECT_EQ(resolveGaiaModel(), "Qwen3.5-2B-GGUF");
    writeConfig({{"default_model", "Qwen3.6-35B-A3B-GGUF"}, {"last_model", "Qwen3.5-2B-GGUF"}});
    EXPECT_EQ(resolveGaiaModel(), "Qwen3.6-35B-A3B-GGUF");
}

TEST_F(GaiaSetup, HybridRunsOnItsCheapModel) {
    writeConfig({{"default_model", "hybrid"}});
    EXPECT_EQ(resolveGaiaModel(), "amd.DeepSeek-V4.1-Flash");
    writeConfig({{"default_model", "hybrid"}, {"hybrid_cheap_model", "amd.Other"}});
    EXPECT_EQ(resolveGaiaModel(), "amd.Other");
}

TEST_F(GaiaSetup, ClaudeDefaultIsNotALemonadeModel) {
    writeConfig({{"default_model", "claude-opus-5-5"}, {"last_model", "Qwen3.5-4B-GGUF"}});
    EXPECT_EQ(resolveGaiaModel(), "Qwen3.5-4B-GGUF");
    writeConfig({{"default_model", "claude-opus-5-5"}});
    EXPECT_EQ(resolveGaiaModel(), kGaiaFallbackModel);
}

TEST_F(GaiaSetup, BrokenConfigFailsLoudly) {
    std::ofstream(home_ / "config.json") << "{not json";
    EXPECT_THROW(resolveGaiaModel(), std::runtime_error);
    writeConfig({{"default_model", 7}});
    EXPECT_THROW(resolveGaiaModel(), std::runtime_error);
}

// ---- the download check, against a mock Lemonade --------------------------

const char* kCatalog = R"({"data":[
    {"id":"Have-GGUF","downloaded":true,"size":2.5},
    {"id":"Missing-GGUF","downloaded":false,"size":6.2},
    {"id":"amd.Cloud","recipe":"cloud","downloaded":false}]})";

class ModelCheck : public ::testing::Test {
protected:
    void SetUp() override { mock_.setModelsList(kCatalog); }

    std::string check(const std::string& model, bool yes, bool tty, const std::string& answer = "") {
        LemonadeClient client(mock_.baseUrl());
        std::istringstream in(answer);
        DownloadPrompt p{tty, &in, &err_};
        try {
            checkServerAndModel(client, model, {ServerSource::GaiaRunning, "/home/.gaia/lemonade"}, yes, p);
        } catch (const std::exception& e) {
            return e.what();
        }
        return "";
    }

    bench::MockLlmServer mock_;
    std::ostringstream err_;
};

TEST_F(ModelCheck, DownloadedModelPasses) { EXPECT_EQ(check("Have-GGUF", false, false), ""); }

TEST_F(ModelCheck, CloudModelIsLeftToTheServer) { EXPECT_EQ(check("amd.Cloud", false, false), ""); }

TEST_F(ModelCheck, NoTerminalRefusesAndNamesYes) {
    const std::string e = check("Missing-GGUF", false, false);
    EXPECT_NE(e.find("Missing-GGUF is not downloaded (6.2 GB)"), std::string::npos) << e;
    EXPECT_NE(e.find("--yes"), std::string::npos) << e;
    EXPECT_EQ(mock_.loadRequestCount(), 0) << "nothing may load a missing model";
}

TEST_F(ModelCheck, TerminalAsksAndANoDownloadsNothing) {
    const std::string e = check("Missing-GGUF", false, true, "n\n");
    EXPECT_NE(err_.str().find("Missing-GGUF is not downloaded (6.2 GB). Download it now? [y/N]"),
              std::string::npos) << err_.str();
    EXPECT_NE(e.find("Nothing was downloaded"), std::string::npos) << e;
    EXPECT_EQ(err_.str().find("downloading"), std::string::npos);
}

TEST_F(ModelCheck, EofIsANo) {
    EXPECT_NE(check("Missing-GGUF", false, true, "").find("Nothing was downloaded"), std::string::npos);
}

// A yes downloads through /pull, never through a load.
TEST(ModelCheckDownload, AYesPullsByNameAndNeverLoads) {
    for (const bool viaFlag : {true, false}) {
        httplib::Server srv;
        std::atomic<int> pulls{0}, loads{0};
        std::string pulled;
        srv.Get("/api/v1/models", [](const httplib::Request&, httplib::Response& res) {
            res.set_content(kCatalog, "application/json");
        });
        srv.Post("/api/v1/pull", [&](const httplib::Request& req, httplib::Response& res) {
            ++pulls;
            pulled = req.body;
            res.set_content(R"({"status":"success"})", "application/json");
        });
        srv.Post("/api/v1/load", [&](const httplib::Request&, httplib::Response& res) {
            ++loads;
            res.set_content(R"({"status":"ok"})", "application/json");
        });
        const int port = srv.bind_to_any_port("127.0.0.1");
        std::thread t([&srv] { srv.listen_after_bind(); });
        while (!srv.is_running()) std::this_thread::sleep_for(std::chrono::milliseconds(5));

        LemonadeClient client("http://127.0.0.1:" + std::to_string(port) + "/api/v1");
        std::istringstream in(viaFlag ? "" : "y\n");
        std::ostringstream err;
        DownloadPrompt p{!viaFlag, &in, &err};
        EXPECT_NO_THROW(checkServerAndModel(client, "Missing-GGUF", {ServerSource::GaiaRunning, "x"}, viaFlag, p));
        EXPECT_EQ(pulls.load(), 1);
        EXPECT_EQ(json::parse(pulled), json({{"model_name", "Missing-GGUF"}}));
        EXPECT_EQ(loads.load(), 0);
        EXPECT_NE(err.str().find("downloading Missing-GGUF (6.2 GB)"), std::string::npos) << err.str();
        EXPECT_EQ(err.str().find("[y/N]") == std::string::npos, viaFlag) << "--yes asks nothing";
        srv.stop();
        t.join();
    }
}

TEST_F(ModelCheck, UnknownModelIsNamed) {
    const std::string e = check("Nope-GGUF", true, true);
    EXPECT_NE(e.find("Nope-GGUF isn't a model the model server"), std::string::npos) << e;
}

TEST_F(ModelCheck, UnreachableGaiaServerSaysHowToStartGaia) {
    LemonadeClient client("http://127.0.0.1:1/api/v1");
    std::istringstream in;
    DownloadPrompt p{false, &in, &err_};
    try {
        checkServerAndModel(client, "Have-GGUF", {ServerSource::GaiaStopped, "/h/.gaia/lemonade"}, false, p);
        FAIL() << "an unreachable server must stop the run";
    } catch (const std::exception& e) {
        const std::string msg = e.what();
        EXPECT_NE(msg.find("GAIA's model server isn't running"), std::string::npos) << msg;
        // The Agent UI swaps this exact sentence for its own (NATIVE_SERVER_START_HINT in surface_hints.py).
        EXPECT_NE(msg.find("Open GAIA, or run `gaia daemon start`, then try again."), std::string::npos) << msg;
    }
}

// A server whose model list is `status` and `body`, for replies Lemonade never sends.
std::string checkAgainstReply(int status, const std::string& body, const ServerOrigin& origin) {
    httplib::Server srv;
    srv.Get("/api/v1/models", [&](const httplib::Request&, httplib::Response& res) {
        res.status = status;
        res.set_content(body, "application/json");
    });
    const int port = srv.bind_to_any_port("127.0.0.1");
    std::thread t([&srv] { srv.listen_after_bind(); });
    while (!srv.is_running()) std::this_thread::sleep_for(std::chrono::milliseconds(5));
    std::string msg;
    {
        LemonadeClient client("http://127.0.0.1:" + std::to_string(port) + "/api/v1");
        std::istringstream in;
        std::ostringstream err;
        try {
            checkServerAndModel(client, "Have-GGUF", origin, false, DownloadPrompt{false, &in, &err});
        } catch (const std::exception& e) {
            msg = e.what();
        }
    }  // the client's open connection would hold stop() up
    srv.stop();
    t.join();
    return msg;
}

bool has(const std::string& text, const std::string& part) { return text.find(part) != std::string::npos; }

// A server that answers is running: telling the user to start it sends them nowhere.
TEST(ServerAnswersWithAnError, ARefusedKeyOfTheUsersNamesItsVariableNotTheDaemon) {
    ServerOrigin origin{ServerSource::GaiaRunning, "/h/.gaia/lemonade"};
    origin.keyVar = "LEMONADE_API_KEY";
    const std::string e = checkAgainstReply(401, R"({"error":"bad key"})", origin);
    EXPECT_TRUE(has(e, "HTTP 401")) << e;
    EXPECT_TRUE(has(e, "refused the key in LEMONADE_API_KEY")) << e;
    EXPECT_TRUE(has(e, "unset LEMONADE_API_KEY")) << e;
    EXPECT_FALSE(has(e, "did not answer")) << e;
    EXPECT_FALSE(has(e, "gaia daemon start")) << e;
}

TEST(ServerAnswersWithAnError, ARefusedKeyOnANamedServerSaysWhereTheAddressCameFrom) {
    ServerOrigin origin{ServerSource::Flag, "--base-url"};
    origin.keyVar = "MY_KEY";
    const std::string e = checkAgainstReply(403, "", origin);
    EXPECT_TRUE(has(e, "HTTP 403")) << e;
    EXPECT_TRUE(has(e, "Set MY_KEY to the key that server expects")) << e;
    EXPECT_TRUE(has(e, "came from --base-url")) << e;
    EXPECT_FALSE(has(e, "gaia daemon")) << e;
}

TEST(ServerAnswersWithAnError, ARefusalWithNoKeySentSaysHowToSendOne) {
    const std::string e = checkAgainstReply(401, "", {ServerSource::LemonadeEnv, "LEMONADE_BASE_URL"});
    EXPECT_TRUE(has(e, "needs a key and none was sent")) << e;
    EXPECT_TRUE(has(e, "GAIA_API_KEY")) << e;
    EXPECT_TRUE(has(e, "--api-key-env")) << e;
    EXPECT_TRUE(has(e, "came from LEMONADE_BASE_URL")) << e;
}

TEST(ServerAnswersWithAnError, GaiasOwnKeyRefusedMeansItsRecordIsStale) {
    ServerOrigin origin{ServerSource::GaiaRunning, "/h/.gaia/lemonade"};
    origin.gaiaKey = true;
    const std::string e = checkAgainstReply(401, "", origin);
    EXPECT_TRUE(has(e, "refused GAIA's own key")) << e;
    EXPECT_TRUE(has(e, "gaia daemon restart")) << e;
}

TEST(ServerAnswersWithAnError, AServerErrorIsReportedWithItsStatusAndTheAddressesSource) {
    const std::string e =
        checkAgainstReply(500, "boom\nline two", {ServerSource::GaiaBaseUrlEnv, "GAIA_BASE_URL"});
    EXPECT_TRUE(has(e, "answered, but not the way GAIA's model server (Lemonade) does: HTTP 500: boom line two")) << e;
    EXPECT_TRUE(has(e, "came from GAIA_BASE_URL")) << e;
    EXPECT_FALSE(has(e, "did not answer")) << e;
    EXPECT_FALSE(has(e, "gaia daemon start")) << e;
}

TEST(ServerAnswersWithAnError, AReplyThatIsNotJsonOrNotAModelListIsNotAServerThatIsDown) {
    for (const char* body : {"<html>router login</html>", R"({"hello":"world"})"}) {
        const std::string e = checkAgainstReply(200, body, {ServerSource::Default, "/h/.gaia/lemonade"});
        EXPECT_TRUE(has(e, "answered, but not the way GAIA's model server (Lemonade) does")) << e;
        EXPECT_TRUE(has(e, "Lemonade's standard port")) << e;
        EXPECT_TRUE(has(e, "gaia init")) << e;
        EXPECT_FALSE(has(e, "No model server answered")) << e;
    }
}

// Another program on GAIA's last port: GAIA is still the thing to start, and the reply is shown.
TEST(ServerAnswersWithAnError, AnotherProgramOnGaiasLastPortIsNamed) {
    const std::string e = checkAgainstReply(404, "nope", {ServerSource::GaiaStopped, "/h/.gaia/lemonade"});
    EXPECT_TRUE(has(e, "another program answered at its last address")) << e;
    EXPECT_TRUE(has(e, "HTTP 404: nope")) << e;
    EXPECT_TRUE(has(e, "gaia daemon start")) << e;
}

// The sidecar's key, on every platform: nothing local goes to another machine.
TEST_F(GaiaSetup, ANamedServersKey) {
    writeState(8123, "gaia-own-key", ownPid());
    const GaiaServer own = runningGaiaServer();
    const std::string remote = "http://models.example.com/api/v1";
    const std::string local = "http://127.0.0.1:9000/api/v1";
    const std::string gaias = "http://localhost:8123/api/v1";
    ServerOrigin o;

    EXPECT_EQ(keyForNamedServer(own, remote, "local-key", o), "") << "the local Lemonade key left this PC";
    EXPECT_EQ(o.keyVar, "");
    EXPECT_EQ(keyForNamedServer(own, remote, "", o), "") << "GAIA's own key left this PC";
    EXPECT_FALSE(o.gaiaKey);
    EXPECT_EQ(keyForNamedServer(own, local, "local-key", o), "local-key");
    EXPECT_EQ(o.keyVar, "LEMONADE_API_KEY");
    EXPECT_EQ(keyForNamedServer(own, gaias, "", o), "gaia-own-key");
    EXPECT_TRUE(o.gaiaKey);
    EXPECT_EQ(keyForNamedServer(own, local, "", o), "") << "GAIA's key is for GAIA's port only";

    GaiaServer foreign = own;
    foreign.envKeyForeign = true;
    EXPECT_EQ(keyForNamedServer(foreign, gaias, "stale-export", o), "gaia-own-key");

    env_.set("GAIA_API_KEY", "for-that-server");
    EXPECT_EQ(keyForNamedServer(own, remote, "local-key", o), "for-that-server");
    EXPECT_EQ(o.keyVar, "GAIA_API_KEY");
    EXPECT_FALSE(o.gaiaKey);
    EXPECT_EQ(keyForNamedServer(own, local, "local-key", o), "for-that-server") << "the key the user named wins";
}

TEST_F(GaiaSetup, TheKeySourceIsNamedTheWayTheKeyIsChosen) {
    AgentConfig cfg;
    cfg.baseUrl = "http://127.0.0.1:9/api/v1";
    ServerOrigin o;
    noteKeySource(o, cfg, false);
    EXPECT_EQ(o.keyVar, "") << "no key set";
    env_.set("LEMONADE_API_KEY", "local");
    noteKeySource(o, cfg, false);
    EXPECT_EQ(o.keyVar, "LEMONADE_API_KEY");
    cfg.baseUrl = "http://example.com/api/v1";
    noteKeySource(o, cfg, false);
    EXPECT_EQ(o.keyVar, "") << "the local Lemonade key is never sent to another host";
    env_.set("GAIA_API_KEY", "mine");
    noteKeySource(o, cfg, false);
    EXPECT_EQ(o.keyVar, "GAIA_API_KEY");
    cfg.apiKeyEnv = "MY_KEY";
    noteKeySource(o, cfg, false);
    EXPECT_EQ(o.keyVar, "MY_KEY");
    noteKeySource(o, cfg, true);
    EXPECT_TRUE(o.gaiaKey);
    EXPECT_EQ(o.keyVar, "");
}

TEST_F(ModelCheck, NothingFoundNeverSaysToStartAServerOnTheStandardPort) {
    LemonadeClient client("http://127.0.0.1:1/api/v1");
    std::istringstream in;
    DownloadPrompt p{false, &in, &err_};
    try {
        checkServerAndModel(client, "Have-GGUF", {ServerSource::Default, "/h/.gaia/lemonade"}, false, p);
        FAIL();
    } catch (const std::exception& e) {
        const std::string msg = e.what();
        EXPECT_NE(msg.find("gaia init"), std::string::npos) << msg;
        EXPECT_EQ(msg.find("lemonade-server"), std::string::npos) << msg;
        EXPECT_EQ(msg.find("Check that the service is running"), std::string::npos) << msg;
    }
}

}  // namespace
