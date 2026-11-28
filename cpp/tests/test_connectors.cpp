// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>

#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <random>
#include <thread>

#include <httplib.h>

#include "gaia/connectors.h"

namespace fs = std::filesystem;
using namespace gaia;
using namespace gaia::connectors;

namespace {

const std::string kKey(32, '\x07');
const std::string kKeyHex = [] {
    std::string h;
    for (int i = 0; i < 32; ++i) h += "07";
    return h;
}();
const std::string kGmailRead = "https://www.googleapis.com/auth/gmail.readonly";
const std::string kGmailCompose = "https://www.googleapis.com/auth/gmail.compose";

void setEnv(const char* name, const std::string& value) {
#ifdef _WIN32
    _putenv_s(name, value.c_str());
#else
    if (value.empty()) {
        unsetenv(name);
    } else {
        setenv(name, value.c_str(), 1);
    }
#endif
}

class ConnectorsTest : public ::testing::Test {
protected:
    fs::path home;
    std::string savedHome;
    std::map<std::string, std::string> keychain;
    std::vector<mail::HttpCall> posts;
    HttpResponse tokenAnswer;

    void SetUp() override {
        std::random_device rd;
        home = fs::temp_directory_path() / ("gaia-connectors-" + std::to_string(rd()));
        fs::create_directories(home / ".gaia" / "connectors");
#ifdef _WIN32
        savedHome = getEnvVar("USERPROFILE");
        setEnv("USERPROFILE", home.string());
#else
        savedHome = getEnvVar("HOME");
        setEnv("HOME", home.string());
#endif
        setEnv("GAIA_GOOGLE_CLIENT_ID", "");
        setEnv("GAIA_GOOGLE_CLIENT_SECRET", "");
        resetForTesting();
        keychain[kMasterKeyName()] = kKeyHex;
        setSecretReaderForTesting([this](const std::string& service, const std::string& user) {
            EXPECT_EQ(service, "gaia.connections");
            auto it = keychain.find(user);
            return it == keychain.end() ? std::optional<std::string>() : std::optional<std::string>(it->second);
        });
        tokenAnswer.status = 200;
        tokenAnswer.body = R"({"access_token":"at-1","expires_in":3600})";
        setTokenTransportForTesting([this](const mail::HttpCall& call) {
            posts.push_back(call);
            return tokenAnswer;
        });
    }

    void TearDown() override {
        setSecretReaderForTesting({});
        setTokenTransportForTesting({});
        resetForTesting();
#ifdef _WIN32
        setEnv("USERPROFILE", savedHome);
#else
        setEnv("HOME", savedHome);
#endif
        std::error_code ec;
        fs::remove_all(home, ec);
    }

    static std::string kMasterKeyName() { return "vault-master-key"; }

    void writeFile(const std::string& name, const std::string& text) {
        std::ofstream(home / ".gaia" / "connectors" / name, std::ios::binary) << text;
    }

    void writeVault(const json& entries) {
        writeFile("vault.enc", encryptVaultForTesting(kKey, json{{"v", 1}, {"entries", entries}}.dump()));
    }

    static json googleBlob(const std::string& clientId = "client-1") {
        return json{{"account_email", "me@example.com"},
                    {"account_id", "1084"},
                    {"refresh_token", "rt-1"},
                    {"scopes", {kGmailRead, kGmailCompose}},
                    {"client_id_hash", crc32Hex(clientId)},
                    {"connected_at", 1.0}};
    }

    void connectGoogle(const json& blob = googleBlob()) {
        writeVault({{"google:default", blob.dump()},
                    {"provider:google", json{{"client_id", "client-1"}, {"client_secret", "sec"}}.dump()}});
        writeFile("accounts.json",
                  json{{"version", 1},
                       {"connectors",
                        {{"google",
                          {{"default_slot", "default"},
                           {"accounts", {{{"slot", "default"}, {"account_id", "1084"}, {"state", "ok"}}}}}}}}}
                      .dump());
    }

    void grant(const std::vector<std::string>& scopes) {
        writeFile("grants.json", json{{"google", {{"installed:gaia", scopes}}}}.dump());
    }
};

TEST_F(ConnectorsTest, Crc32MatchesZlib) {
    EXPECT_EQ(crc32Hex(""), "00000000");
    EXPECT_EQ(crc32Hex("hello"), "3610a686");
}

TEST_F(ConnectorsTest, OpensAVaultThePythonConnectorsWrote) {
    // vault.encrypt_envelope(bytes([7] * 32), {"google:default": '{"a":1}'})
    const std::string hex =
        "4741494156310164898fc3a7f506cd020dab6a85ce64a7f9ef369916fdb9a776500caf2ea7667a0a99074d2a077e475e972ed4593396c2"
        "cd2153042f9420932628d4cba5b9da94f91628743d695ccde8388137";
    std::string blob;
    for (size_t i = 0; i < hex.size(); i += 2) blob += static_cast<char>(std::stoi(hex.substr(i, 2), nullptr, 16));
    writeFile("vault.enc", blob);
    writeFile("accounts.json", R"({"version":1,"connectors":{}})");
    keychain["google:default"] = "never read: the vault has it";
    const auto conn = getConnection("google");
    ASSERT_TRUE(conn.has_value()) << "the blob came out of the vault";
    EXPECT_EQ(conn->error, "configuration") << "no OAuth client is set up";
    keychain.erase("google:default");
    writeFile("grants.json", json{{"google", {{"installed:gaia", {kGmailRead}}}}}.dump());
    setEnv("GAIA_GOOGLE_CLIENT_ID", "client-1");
    try {
        getAccessToken("google", {kGmailRead}, "installed:gaia");
        FAIL();
    } catch (const ConnectorsError& e) {
        // The blob decrypted ({"a":1}) and carries no client hash, so the tripwire fires.
        EXPECT_EQ(e.kind(), "AuthRequiredError");
        EXPECT_NE(std::string(e.what()).find("no longer valid"), std::string::npos);
    }
    setEnv("GAIA_GOOGLE_CLIENT_ID", "");
}

TEST_F(ConnectorsTest, ReadsTheDefaultAccountFromTheVault) {
    connectGoogle();
    const auto conn = getConnection("google");
    ASSERT_TRUE(conn.has_value());
    EXPECT_EQ(conn->accountEmail, "me@example.com");
    EXPECT_EQ(conn->accountId, "1084");
    EXPECT_TRUE(conn->error.empty());
    EXPECT_EQ(conn->scopes, (std::vector<std::string>{kGmailRead, kGmailCompose}));
    EXPECT_FALSE(getConnection("microsoft").has_value());
}

TEST_F(ConnectorsTest, AConnectionWithNoIndexEntryStillCountsUnderTheLegacySlot) {
    connectGoogle();
    writeFile("accounts.json", R"({"version":1,"connectors":{}})");
    ASSERT_TRUE(getConnection("google").has_value());
}

TEST_F(ConnectorsTest, AnExpiredSignInIsNotAConnection) {
    connectGoogle();
    writeFile("accounts.json",
              R"({"version":1,"connectors":{"google":{"default_slot":"default","accounts":[{"slot":"default","state":"reauth_required"}]}}})");
    EXPECT_FALSE(getConnection("google").has_value());
}

TEST_F(ConnectorsTest, ReadsAChunkedLegacyKeychainItemWhenTheVaultLacksIt) {
    const std::string blob = googleBlob().dump();
    const std::string a = blob.substr(0, 40), b = blob.substr(40);
    keychain["google:default"] = "gaia-chunked-v2:2:" + crc32Hex(blob);
    keychain["google:default#0"] = a;
    keychain["google:default#1"] = b;
    keychain["provider:google"] = json{{"client_id", "client-1"}}.dump();
    const auto conn = getConnection("google");
    ASSERT_TRUE(conn.has_value());
    EXPECT_EQ(conn->accountEmail, "me@example.com");
    keychain["google:default"] = "gaia-chunked-v2:2:deadbeef";  // a torn rewrite
    EXPECT_FALSE(getConnection("google").has_value());
}

TEST_F(ConnectorsTest, AClientRotatedSinceSignInIsNotAConnection) {
    connectGoogle(googleBlob("another-client"));
    EXPECT_FALSE(getConnection("google").has_value());
}

TEST_F(ConnectorsTest, AnInterruptedForwardReadsAsBusy) {
    connectGoogle();
    writeVault({{"google:default", googleBlob().dump()},
                {"provider:google", json{{"client_id", "client-1"}}.dump()},
                {"recovery:forward:google", R"({"account_slot":"default"})"}});
    const auto conn = getConnection("google");
    ASSERT_TRUE(conn.has_value());
    EXPECT_EQ(conn->error, "busy");
    EXPECT_TRUE(conn->scopes.empty());
}

TEST_F(ConnectorsTest, NoOAuthClientReadsAsConfiguration) {
    connectGoogle();
    writeVault({{"google:default", googleBlob().dump()}});
    const auto conn = getConnection("google");
    ASSERT_TRUE(conn.has_value());
    EXPECT_EQ(conn->error, "configuration");
}

TEST_F(ConnectorsTest, AWrongVaultKeyIsAnActionableError) {
    connectGoogle();
    keychain["vault-master-key"] = std::string(64, 'a');
    try {
        getConnection("google");
        FAIL() << "expected an error";
    } catch (const ConnectorsError& e) {
        EXPECT_NE(std::string(e.what()).find("Could not decrypt the credential vault"), std::string::npos);
    }
}

TEST_F(ConnectorsTest, RefreshesWithTheStoredClientAndCachesTheToken) {
    connectGoogle();
    grant({kGmailRead, kGmailCompose});
    EXPECT_EQ(getAccessToken("google", {kGmailRead}, "installed:gaia"), "at-1");
    ASSERT_EQ(posts.size(), 1u);
    EXPECT_EQ(posts[0].method, "POST");
    EXPECT_EQ(posts[0].url, "https://oauth2.googleapis.com/token");
    EXPECT_EQ(posts[0].headers.at("Content-Type"), "application/x-www-form-urlencoded");
    EXPECT_EQ(posts[0].body, "grant_type=refresh_token&refresh_token=rt-1&client_id=client-1&client_secret=sec");
    EXPECT_EQ(getAccessToken("google", {kGmailRead}, "installed:gaia"), "at-1");
    EXPECT_EQ(posts.size(), 1u);
}

TEST_F(ConnectorsTest, NoGrantIsRefusedBeforeAnyNetworkCall) {
    connectGoogle();
    grant({kGmailRead});
    try {
        getAccessToken("google", {kGmailRead, kGmailCompose}, "installed:gaia");
        FAIL() << "expected an error";
    } catch (const ConnectorsError& e) {
        EXPECT_EQ(e.kind(), "AuthRequiredError");
        EXPECT_NE(std::string(e.what()).find("Agent 'installed:gaia' has no grant for google"), std::string::npos);
    }
    EXPECT_TRUE(posts.empty());
}

TEST_F(ConnectorsTest, AScopeTheConnectionLacksNamesTheWholeList) {
    connectGoogle();
    grant({kGmailRead, "https://www.googleapis.com/auth/calendar.events"});
    try {
        getAccessToken("google", {"https://www.googleapis.com/auth/calendar.events"}, "installed:gaia");
        FAIL() << "expected an error";
    } catch (const ConnectorsError& e) {
        const std::string m = e.what();
        EXPECT_NE(m.find("lacks required scopes (https://www.googleapis.com/auth/calendar.events)"), std::string::npos);
        EXPECT_NE(m.find(kGmailCompose), std::string::npos) << "the remedy keeps what the connection has";
    }
}

TEST_F(ConnectorsTest, NoScopesIsRefused) {
    connectGoogle();
    grant({kGmailRead});
    try {
        getAccessToken("google", {}, "installed:gaia");
        FAIL();
    } catch (const ConnectorsError& e) {
        EXPECT_EQ(e.kind(), "ConfigurationError");
    }
}

TEST_F(ConnectorsTest, ARevokedRefreshTokenIsReportedNotCleared) {
    connectGoogle();
    grant({kGmailRead});
    tokenAnswer.status = 400;
    tokenAnswer.body = R"({"error":"invalid_grant"})";
    try {
        getAccessToken("google", {kGmailRead}, "installed:gaia");
        FAIL();
    } catch (const ConnectorsError& e) {
        EXPECT_EQ(e.kind(), "ConnectionRevokedError");
    }
    EXPECT_TRUE(getConnection("google").has_value()) << "Python stays the one writer";
}

TEST_F(ConnectorsTest, MicrosoftRefreshesAgainstTheConsumersTenant) {
    writeVault({{"microsoft:default",
                 json{{"account_email", "me@outlook.com"},
                      {"refresh_token", "rt-m"},
                      {"scopes", {"https://graph.microsoft.com/Mail.Read"}},
                      {"client_id_hash", crc32Hex("ms-client")},
                      {"tenant", "consumers"}}
                     .dump()},
                {"provider:microsoft", json{{"client_id", "ms-client"}, {"client_secret", ""}}.dump()}});
    writeFile("grants.json", json{{"microsoft", {{"installed:gaia", {"https://graph.microsoft.com/Mail.Read"}}}}}.dump());
    EXPECT_EQ(getAccessToken("microsoft", {"https://graph.microsoft.com/Mail.Read"}, "installed:gaia"), "at-1");
    ASSERT_EQ(posts.size(), 1u);
    EXPECT_EQ(posts[0].url, "https://login.microsoftonline.com/consumers/oauth2/v2.0/token");
    EXPECT_EQ(posts[0].body, "grant_type=refresh_token&refresh_token=rt-m&client_id=ms-client");
}

TEST_F(ConnectorsTest, ReadsTheKeychainThroughTheDaemonAndDeletesItsCredential) {
    setSecretReaderForTesting({});
    httplib::Server daemon;
    std::string seenAuth, seenBody;
    daemon.Post("/host/v1/keychain/get", [&](const httplib::Request& req, httplib::Response& res) {
        seenAuth = req.get_header_value("Authorization");
        seenBody = req.body;
        const json body = json::parse(req.body);
        const std::string user = body.value("username", "");
        auto it = keychain.find(user);
        res.set_content(json{{"value", it == keychain.end() ? json(nullptr) : json(it->second)}}.dump(),
                        "application/json");
    });
    const int port = daemon.bind_to_any_port("127.0.0.1");
    std::thread serving([&] { daemon.listen_after_bind(); });
    daemon.wait_until_ready();

    const fs::path secretFile = home / "keychain-secret";
    std::ofstream(secretFile) << "s3cret";
    setEnv("GAIA_HOST_KEYCHAIN_SECRET_FILE", secretFile.string());
    setEnv("GAIA_HOST_CUSTODY_URL", "http://127.0.0.1:" + std::to_string(port) + "/");
    setEnv("PYTHON_KEYRING_BACKEND", "");
    EXPECT_TRUE(installDaemonKeychainFromEnv());
    EXPECT_FALSE(fs::exists(secretFile));
    EXPECT_TRUE(getEnvVar("GAIA_HOST_KEYCHAIN_SECRET_FILE").empty());

    connectGoogle();
    EXPECT_TRUE(getConnection("google").has_value());
    EXPECT_EQ(seenAuth, "Bearer s3cret");
    EXPECT_EQ(json::parse(seenBody).value("service", ""), "gaia.connections");

    setEnv("GAIA_HOST_CUSTODY_URL", "");
    daemon.stop();
    serving.join();
}

TEST_F(ConnectorsTest, ADaemonCredentialWithoutTheDaemonUrlFailsLoudly) {
    const fs::path secretFile = home / "keychain-secret";
    std::ofstream(secretFile) << "s3cret";
    setEnv("GAIA_HOST_KEYCHAIN_SECRET_FILE", secretFile.string());
    setEnv("GAIA_HOST_CUSTODY_URL", "");
    setEnv("PYTHON_KEYRING_BACKEND", "");
    EXPECT_THROW(installDaemonKeychainFromEnv(), ConnectorsError);
    EXPECT_FALSE(fs::exists(secretFile)) << "the credential never outlives the read";
}

}  // namespace
