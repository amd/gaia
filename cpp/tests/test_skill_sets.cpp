// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Unit tests for skill sets and gaia-agent.yaml wiring (issue #2801).
//
// Mirrors tests/unit/test_skill_sets.py case for case. The grammar and the
// refusal wording are a cross-runtime contract (#2805): a manifest that parses
// in one runtime must parse in the other, and be refused by both for the same
// reason, so these assert the message text and not just the throw.
//
// Every test builds its manifest under a temp directory, so nothing here reads
// the developer's real ~/.gaia or a manifest left over from a previous run.

#include "gaia/skill_sets.h"

#include "gaia/agent.h"

#include <gmock/gmock.h>
#include <gtest/gtest.h>

#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <map>
#include <set>
#include <string>
#include <vector>

namespace fs = std::filesystem;

using gaia::Agent;
using gaia::AgentConfig;
using gaia::parseSkillSets;
using gaia::parseSkillSetsFile;
using gaia::SkillJson;
using gaia::SkillLoader;
using gaia::SkillRef;
using gaia::SkillSets;
using gaia::SkillSetSource;
using gaia::SkillSetError;
using gaia::SkillValidationError;
using ::testing::HasSubstr;

namespace {

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Names of a ref list, in order — what almost every assertion compares.
std::vector<std::string> namesOf(const std::vector<SkillRef>& refs) {
    std::vector<std::string> names;
    for (const SkillRef& ref : refs) names.push_back(ref.name);
    return names;
}

/// The two-set declaration the resolution tests share.
SkillSets twoSets() {
    return parseSkillSets(SkillJson::parse(R"({
        "skill_sets": {
            "personal": ["inbox-triage", "newsletter-digest"],
            "work": ["inbox-triage", "meeting-scheduling"]
        },
        "default_skill_set": "personal"
    })"));
}

/// A temp directory unique to each test, removed on teardown.
class TempDir {
public:
    explicit TempDir(const std::string& label) {
        path_ = fs::temp_directory_path() /
                ("gaia_skill_sets_" + label + "_" +
                 std::to_string(reinterpret_cast<uintptr_t>(this)));
        fs::remove_all(path_);
        fs::create_directories(path_);
    }
    ~TempDir() {
        std::error_code ec;
        fs::remove_all(path_, ec);
    }
    TempDir(const TempDir&) = delete;
    TempDir& operator=(const TempDir&) = delete;

    /// Write a gaia-agent.yaml here and return its path.
    std::string writeManifest(const std::string& body) const {
        const fs::path file = path_ / "gaia-agent.yaml";
        std::ofstream out(file, std::ios::binary);
        out << body;
        out.close();
        return file.string();
    }

    const fs::path& path() const { return path_; }

private:
    fs::path path_;
};

/// Records what a skill set asked for without touching the filesystem.
///
/// Stands in for the SkillManager-backed loader P3.3 (#2800) will install. It
/// is deliberately dumb: the behaviour under test is the *agent's* ownership
/// tracking, not any particular discovery policy.
class FakeSkillLoader : public SkillLoader {
public:
    /// Skills that exist to be found, and the version each declares on disk.
    std::map<std::string, std::string> available;
    /// Currently loaded, in load order.
    std::vector<std::string> loaded;
    /// Every call, in order, for asserting a switch unloaded exactly what it
    /// should have — the state alone cannot distinguish "left alone" from
    /// "unloaded then reloaded".
    std::vector<std::string> calls;

    void offer(const std::string& name, const std::string& version = "") {
        available[name] = version;
    }

    bool loadSkill(const std::string& name) override {
        calls.push_back("load:" + name);
        if (available.count(name) == 0) return false;
        if (!isLoaded(name)) loaded.push_back(name);
        return true;
    }

    bool unloadSkill(const std::string& name) override {
        calls.push_back("unload:" + name);
        const auto it = std::find(loaded.begin(), loaded.end(), name);
        if (it == loaded.end()) return false;
        loaded.erase(it);
        return true;
    }

    bool isLoaded(const std::string& name) const override {
        return std::find(loaded.begin(), loaded.end(), name) != loaded.end();
    }

    std::string loadedVersion(const std::string& name) const override {
        const auto it = available.find(name);
        return it == available.end() ? "" : it->second;
    }

    std::set<std::string> loadedSet() const {
        return std::set<std::string>(loaded.begin(), loaded.end());
    }
};

/// Swallows output but keeps the warnings, so a test can prove a diagnostic
/// was actually emitted rather than trusting that it would be.
class RecordingConsole : public gaia::SilentConsole {
public:
    explicit RecordingConsole(std::vector<std::string>* sink) : sink_(sink) {}
    void printWarning(const std::string& message) override { sink_->push_back(message); }

private:
    std::vector<std::string>* sink_;
};

/// An Agent that loads no tools and can be told what its selector hook returns.
class HarnessAgent : public Agent {
public:
    explicit HarnessAgent(const AgentConfig& config) : Agent(config) { init(); }

    /// What selectSkillSet() answers. Unset means "no opinion".
    std::optional<std::string> selectorAnswer;

protected:
    void registerTools() override {}
    std::optional<std::string> selectSkillSet() const override { return selectorAnswer; }
};

/// A HarnessAgent wired to a fake loader and a manifest, ready to load a set.
struct Harness {
    TempDir dir;
    FakeSkillLoader loader;
    std::vector<std::string> warnings;
    std::unique_ptr<HarnessAgent> agent;

    Harness(const std::string& label, const std::string& manifest,
            const std::string& skillSet = "")
        : dir(label) {
        AgentConfig config;
        config.silentMode = true;
        config.skillManifest = dir.writeManifest(manifest);
        config.skillSet = skillSet;
        agent = std::make_unique<HarnessAgent>(config);
        agent->setOutputHandler(std::make_unique<RecordingConsole>(&warnings));
        agent->setSkillLoader(&loader);
    }
};

/// The manifest most Agent tests use: one always-on skill and two sets that
/// overlap on `inbox-triage`.
const char* const kTwoSetManifest = R"(
skills:
  - always-on
skill_sets:
  personal: [inbox-triage, newsletter-digest]
  work: [inbox-triage, meeting-scheduling]
default_skill_set: personal
)";

void offerAll(FakeSkillLoader& loader) {
    for (const char* name : {"always-on", "inbox-triage", "newsletter-digest",
                             "meeting-scheduling", "hand-loaded"}) {
        loader.offer(name);
    }
}

}  // namespace

// ---------------------------------------------------------------------------
// Manifest parsing — the additive grammar
// ---------------------------------------------------------------------------

TEST(SkillSetsParse, NoBlocksIsEmptyAndFalsy) {
    const SkillSets sets = parseSkillSets(SkillJson::parse(R"({"id": "demo"})"));
    EXPECT_FALSE(static_cast<bool>(sets));
    EXPECT_TRUE(sets.empty());
    EXPECT_TRUE(sets.always().empty());
    EXPECT_TRUE(sets.setNames().empty());
    EXPECT_FALSE(sets.defaultSet().has_value());
}

TEST(SkillSetsParse, AcceptsStringAndMappingRefs) {
    const SkillSets sets = parseSkillSets(SkillJson::parse(R"({
        "skills": ["always-on"],
        "skill_sets": {
            "work": [
                "inbox-triage",
                {"name": "meeting-scheduling", "version": ">=0.1.0", "required": false}
            ]
        },
        "default_skill_set": "work"
    })"));

    EXPECT_EQ(namesOf(sets.always()), std::vector<std::string>{"always-on"});
    const std::vector<SkillRef>& work = sets.set("work");
    EXPECT_EQ(namesOf(work),
              (std::vector<std::string>{"inbox-triage", "meeting-scheduling"}));
    EXPECT_TRUE(work[0].required);
    EXPECT_FALSE(work[0].version.has_value());
    EXPECT_FALSE(work[1].required);
    ASSERT_TRUE(work[1].version.has_value());
    EXPECT_EQ(*work[1].version, ">=0.1.0");
}

TEST(SkillSetsParse, PreservesDeclarationOrderOfSets) {
    const SkillSets sets = parseSkillSets(SkillJson::parse(R"({
        "skill_sets": {"work": ["inbox-triage"], "personal": ["newsletter-digest"]},
        "default_skill_set": "work"
    })"));
    EXPECT_EQ(sets.setNames(), (std::vector<std::string>{"work", "personal"}));
}

TEST(SkillSetsParse, SkillsForPrependsTheAlwaysOnList) {
    const SkillSets sets = parseSkillSets(SkillJson::parse(R"({
        "skills": ["always-on"],
        "skill_sets": {"work": ["inbox-triage"]},
        "default_skill_set": "work"
    })"));
    EXPECT_EQ(namesOf(sets.skillsFor("work")),
              (std::vector<std::string>{"always-on", "inbox-triage"}));
    EXPECT_EQ(namesOf(sets.skillsFor(std::nullopt)),
              std::vector<std::string>{"always-on"});
}

TEST(SkillSetsParse, OverlappingSetsAreAllowedAndIndependent) {
    // Two sets naming the same skill is the normal case, not a clash: only a
    // set re-declaring an *always-on* skill is an error.
    const SkillSets sets = twoSets();
    EXPECT_EQ(namesOf(sets.set("personal")),
              (std::vector<std::string>{"inbox-triage", "newsletter-digest"}));
    EXPECT_EQ(namesOf(sets.set("work")),
              (std::vector<std::string>{"inbox-triage", "meeting-scheduling"}));
}

// --- Malformed declarations, mirroring the Python parametrized cases --------

struct MalformedCase {
    /// Identifies the case in ctest output — without it gtest prints the raw
    /// struct bytes and a failure names no rule.
    const char* label;
    const char* json;
    const char* expected;
};

/// CTest names a value-parameterized test after its GetParam() rendering, so
/// this is what makes `ctest -R` able to address one grammar rule by name.
void PrintTo(const MalformedCase& testCase, std::ostream* os) { *os << testCase.label; }

class SkillSetsMalformed : public ::testing::TestWithParam<MalformedCase> {};

TEST_P(SkillSetsMalformed, FailsLoudly) {
    const MalformedCase& testCase = GetParam();
    try {
        parseSkillSets(SkillJson::parse(testCase.json));
        FAIL() << "expected SkillValidationError for: " << testCase.json;
    } catch (const SkillValidationError& error) {
        EXPECT_THAT(std::string(error.what()), HasSubstr(testCase.expected));
    }
}

INSTANTIATE_TEST_SUITE_P(
    Grammar, SkillSetsMalformed,
    ::testing::Values(
        MalformedCase{"SetsBlockNotAMapping", R"({"skill_sets": ["work"]})", "must be a mapping"},
        MalformedCase{"EmptySet", R"({"skill_sets": {"work": []}, "default_skill_set": "work"})",
                      "is empty"},
        MalformedCase{"MissingDefault", R"({"skill_sets": {"work": ["inbox-triage"]}})",
                      "default_skill_set"},
        MalformedCase{"DefaultNamesUndeclaredSet", R"({"skill_sets": {"work": ["inbox-triage"]},
                          "default_skill_set": "personal"})",
                      "does not name a declared skill set"},
        MalformedCase{"UppercaseSetName", R"({"skill_sets": {"Work": ["inbox-triage"]},
                          "default_skill_set": "Work"})",
                      "invalid set name"},
        MalformedCase{"DuplicateSkillInSet", R"({"skill_sets": {"work": ["inbox-triage", "inbox-triage"]},
                          "default_skill_set": "work"})",
                      "twice"},
        MalformedCase{"SetReDeclaresAlwaysOn", R"({"skills": ["inbox-triage"],
                          "skill_sets": {"work": ["inbox-triage"]},
                          "default_skill_set": "work"})",
                      "already in the always-on"},
        MalformedCase{"UnknownRefKey", R"({"skill_sets": {"work": [{"name": "x", "requird": false}]},
                          "default_skill_set": "work"})",
                      "unrecognized key"},
        MalformedCase{"RequiredNotABool", R"({"skill_sets": {"work": [{"name": "x", "required": "yes"}]},
                          "default_skill_set": "work"})",
                      "must be true or false"},
        MalformedCase{"VersionNotAString", R"({"skill_sets": {"work": [{"name": "x", "version": 1}]},
                          "default_skill_set": "work"})",
                      "must be a non-empty string"},
        MalformedCase{"RefMissingName", R"({"skill_sets": {"work": [{"version": "1.0.0"}]},
                          "default_skill_set": "work"})",
                      "missing a skill name"},
        MalformedCase{"InvalidSkillName", R"({"skill_sets": {"work": ["Inbox_Triage"]},
                          "default_skill_set": "work"})",
                      "not a valid"},
        MalformedCase{"SkillsNotAList", R"({"skills": "inbox-triage"})", "must be a list"},
        // A bare `work:` key with nothing indented under it — the likeliest
        // authoring slip, and it must not validate into a set that loads nothing.
        MalformedCase{"BareSetKeyWithNothingUnderIt", R"({"skill_sets": {"work": null}, "default_skill_set": "work"})",
                      "names no skills"}),
    [](const ::testing::TestParamInfo<MalformedCase>& info) {
        return std::string(info.param.label);
    });

TEST(SkillSetsParse, SetReDeclaringAnAlwaysOnSkillNamesTheOffender) {
    // The acceptance case: the message must name the skill and both blocks, or
    // the author cannot tell which of the two to edit.
    try {
        parseSkillSets(SkillJson::parse(R"({
            "skills": ["mailbox-hygiene", "always-on"],
            "skill_sets": {"work": ["mailbox-hygiene", "meeting-scheduling"]},
            "default_skill_set": "work"
        })"));
        FAIL() << "expected SkillValidationError";
    } catch (const SkillValidationError& error) {
        const std::string message = error.what();
        EXPECT_THAT(message, HasSubstr("skill_sets.work"));
        EXPECT_THAT(message, HasSubstr("mailbox-hygiene"));
        EXPECT_THAT(message, HasSubstr("already in the always-on 'skills:' list"));
        // Only the clashing skill is named, not the innocent one.
        EXPECT_THAT(message, ::testing::Not(HasSubstr("meeting-scheduling")));
    }
}

TEST(SkillSetsParse, ErrorNamesTheManifestFile) {
    const TempDir dir("where");
    const std::string path = dir.writeManifest(
        "skill_sets:\n  work: []\ndefault_skill_set: work\n");
    EXPECT_THAT([&] { parseSkillSetsFile(path); },
                ::testing::ThrowsMessage<SkillValidationError>(HasSubstr(path)));
}

// ---------------------------------------------------------------------------
// Resolution order
// ---------------------------------------------------------------------------

TEST(SkillSetsResolve, ExplicitThenSelectorThenDefault) {
    const SkillSets sets = twoSets();

    const auto byDefault = sets.resolve();
    EXPECT_EQ(byDefault.name, "personal");
    EXPECT_EQ(byDefault.source, SkillSetSource::Default);

    const auto bySelector = sets.resolve(std::nullopt, "work");
    EXPECT_EQ(bySelector.name, "work");
    EXPECT_EQ(bySelector.source, SkillSetSource::Selector);

    const auto byExplicit = sets.resolve("work", "personal");
    EXPECT_EQ(byExplicit.name, "work");
    EXPECT_EQ(byExplicit.source, SkillSetSource::Explicit);
}

TEST(SkillSetsResolve, SourceSpellingMatchesPython) {
    // sets.py's SOURCE_* constants are the wire format for logs and #2805.
    EXPECT_EQ(gaia::toString(SkillSetSource::Explicit), "explicit");
    EXPECT_EQ(gaia::toString(SkillSetSource::Selector), "selector");
    EXPECT_EQ(gaia::toString(SkillSetSource::Default), "default");
    EXPECT_EQ(gaia::toString(SkillSetSource::None), "none");
}

TEST(SkillSetsResolve, ReturnsOnlyTheSelectedSetsSkills) {
    const SkillSets sets = twoSets();
    EXPECT_EQ(namesOf(sets.resolve("work").skills),
              (std::vector<std::string>{"inbox-triage", "meeting-scheduling"}));
    EXPECT_EQ(namesOf(sets.resolve("personal").skills),
              (std::vector<std::string>{"inbox-triage", "newsletter-digest"}));
}

TEST(SkillSetsResolve, BlankRequestIsTreatedAsUnset) {
    EXPECT_EQ(twoSets().resolve("  ", "work").source, SkillSetSource::Selector);
}

TEST(SkillSetsResolve, UnknownExplicitNameFailsLoudlyListingValidSets) {
    try {
        twoSets().resolve("buisness");
        FAIL() << "expected SkillSetError";
    } catch (const SkillSetError& error) {
        const std::string message = error.what();
        EXPECT_THAT(message, HasSubstr("Skill set 'buisness' is not declared"));
        EXPECT_THAT(message, HasSubstr("Valid sets: personal, work"));
    }
}

TEST(SkillSetsResolve, UnknownSelectorNameFailsLoudlyAndSaysItWasTheSelector) {
    try {
        twoSets().resolve(std::nullopt, "buisness");
        FAIL() << "expected SkillSetError";
    } catch (const SkillSetError& error) {
        const std::string message = error.what();
        EXPECT_THAT(message, HasSubstr("selector returned skill set 'buisness'"));
        EXPECT_THAT(message, HasSubstr("Valid sets: personal, work"));
    }
}

TEST(SkillSetsResolve, UnknownNameNeverFallsBackToTheDefault) {
    // The whole point of the contract: a typo must not quietly launch the
    // default capability bundle.
    const SkillSets sets = twoSets();
    EXPECT_THROW(sets.resolve("buisness"), SkillSetError);
    EXPECT_THROW(sets.resolve(std::nullopt, "buisness"), SkillSetError);
    EXPECT_THROW(sets.skillsFor("buisness"), SkillSetError);
}

TEST(SkillSetsResolve, RequestingASetOnAnAgentWithNoneFailsLoudly) {
    const SkillSets sets =
        parseSkillSets(SkillJson::parse(R"({"skills": ["always-on"]})"));
    EXPECT_THAT([&] { sets.resolve("work"); },
                ::testing::ThrowsMessage<SkillSetError>(
                    HasSubstr("declares no 'skill_sets:' block")));
}

TEST(SkillSetsResolve, NoSetsDeclaredResolvesToTheAlwaysOnList) {
    const auto resolution =
        parseSkillSets(SkillJson::parse(R"({"skills": ["always-on"]})")).resolve();
    EXPECT_FALSE(resolution.name.has_value());
    EXPECT_EQ(resolution.source, SkillSetSource::None);
    EXPECT_EQ(namesOf(resolution.skills), std::vector<std::string>{"always-on"});
}

TEST(SkillSetsResolve, HandBuiltSetsWithoutADefaultFailLoudly) {
    // parseSkillSets rejects this shape; a programmatic caller must too.
    const SkillSets sets({}, {{"work", {}}}, std::nullopt);
    EXPECT_THAT([&] { sets.resolve(); },
                ::testing::ThrowsMessage<SkillSetError>(
                    HasSubstr("No skill set could be resolved")));
}

TEST(SkillSetsResolve, DefaultNamingAnUndeclaredSetSaysSoWasTheDefault) {
    const SkillSets sets({}, {{"work", {}}}, "personal");
    EXPECT_THAT([&] { sets.resolve(); },
                ::testing::ThrowsMessage<SkillSetError>(HasSubstr(
                    "'default_skill_set' names skill set 'personal'")));
}

// ---------------------------------------------------------------------------
// gaia-agent.yaml parsing — against a real manifest
// ---------------------------------------------------------------------------

TEST(SkillSetsManifest, ReadsTheFixtureManifest) {
    const std::string path =
        std::string(GAIA_TEST_FIXTURES_DIR) + "/skill_sets/gaia-agent.yaml";
    ASSERT_TRUE(fs::exists(path)) << path;

    const SkillSets sets = parseSkillSetsFile(path);

    EXPECT_TRUE(static_cast<bool>(sets));
    EXPECT_EQ(namesOf(sets.always()),
              (std::vector<std::string>{"mailbox-hygiene", "incident-review"}));
    // The mapping form survives the YAML round trip.
    EXPECT_FALSE(sets.always()[1].required);
    ASSERT_TRUE(sets.always()[1].version.has_value());
    EXPECT_EQ(*sets.always()[1].version, ">=0.1.0");

    EXPECT_EQ(sets.setNames(), (std::vector<std::string>{"personal", "work"}));
    EXPECT_EQ(sets.defaultSet(), "personal");
    EXPECT_EQ(namesOf(sets.set("work")),
              (std::vector<std::string>{"inbox-triage", "meeting-scheduling",
                                        "escalation-routing"}));
    EXPECT_FALSE(sets.set("work")[2].required);

    // The always-on list leads, then the set, in declaration order.
    EXPECT_EQ(namesOf(sets.resolve().skills),
              (std::vector<std::string>{"mailbox-hygiene", "incident-review",
                                        "inbox-triage", "newsletter-digest"}));
}

TEST(SkillSetsManifest, ARealShippedManifestWithoutSkillBlocksIsEmpty) {
    // cpp/agents/bash/gaia-agent.yaml declares no skills. Reading it must yield
    // the falsy empty instance, not an error — every existing agent keeps its
    // behaviour exactly.
    const std::string path =
        std::string(GAIA_CPP_SOURCE_DIR) + "/agents/bash/gaia-agent.yaml";
    ASSERT_TRUE(fs::exists(path)) << path;

    const SkillSets sets = parseSkillSetsFile(path);
    EXPECT_FALSE(static_cast<bool>(sets));
    EXPECT_TRUE(sets.always().empty());
    EXPECT_TRUE(sets.setNames().empty());
    EXPECT_EQ(sets.resolve().source, SkillSetSource::None);
}

TEST(SkillSetsManifest, MissingFileFailsLoudlyRatherThanAssumingNoSkills) {
    const TempDir dir("missing");
    const std::string path = (dir.path() / "nope.yaml").string();
    EXPECT_THAT([&] { parseSkillSetsFile(path); },
                ::testing::ThrowsMessage<SkillValidationError>(
                    HasSubstr("may be hiding a 'skills:' block")));
}

TEST(SkillSetsManifest, MalformedYamlFailsLoudly) {
    const TempDir dir("badyaml");
    const std::string path = dir.writeManifest("skills: [unclosed\n");
    EXPECT_THROW(parseSkillSetsFile(path), SkillValidationError);
}

TEST(SkillSetsManifest, EmptyManifestIsEmptyNotAnError) {
    const TempDir dir("empty");
    EXPECT_FALSE(static_cast<bool>(parseSkillSetsFile(dir.writeManifest(""))));
}

TEST(SkillSetsManifest, YamlBoolsResolveLikePyYaml) {
    // `required: no` is a YAML 1.1 bool in PyYAML, so it must be one here too —
    // otherwise the same manifest means different things in the two runtimes.
    const TempDir dir("yamlbool");
    const std::string path = dir.writeManifest(R"(
skill_sets:
  work:
    - name: meeting-scheduling
      required: no
default_skill_set: work
)");
    const SkillSets sets = parseSkillSetsFile(path);
    EXPECT_FALSE(sets.set("work")[0].required);
}

TEST(SkillSetsManifest, FindsTheManifestBesideAndAboveADirectory) {
    const TempDir dir("find");
    const fs::path nested = dir.path() / "bin";
    fs::create_directories(nested);

    // Nothing yet.
    EXPECT_TRUE(gaia::findAgentManifest(nested.string()).empty());

    // One level up (a packaged agent's layout).
    const std::string above = dir.writeManifest("id: demo\n");
    EXPECT_EQ(gaia::findAgentManifest(nested.string()), above);

    // Beside wins over above.
    const fs::path beside = nested / "gaia-agent.yaml";
    std::ofstream(beside, std::ios::binary) << "id: demo\n";
    EXPECT_EQ(gaia::findAgentManifest(nested.string()), beside.string());
}

TEST(SkillSetsManifest, ABareYamlBoolSetNameIsRefusedTheWayPythonRefusesIt) {
    // `on:` is a YAML 1.1 bool, so PyYAML hands sets.py the key `True` and its
    // isinstance(str) guard rejects it. Accepting it here would ship a manifest
    // that launches natively and hard-fails on the Python runtime.
    const TempDir dir("boolkey");
    const std::string path = dir.writeManifest(
        "skill_sets:\n  on: [inbox-triage]\ndefault_skill_set: \"on\"\n");
    try {
        parseSkillSetsFile(path);
        FAIL() << "expected SkillValidationError";
    } catch (const SkillValidationError& error) {
        // Byte-for-byte what sets.py emits for the same file (verified against
        // parse_skill_sets(yaml.safe_load(...), where=" in <path>")), including
        // the resolved `True` rather than the literal `on` and the en-dash in
        // "1–32". This is the #2805 wording contract, asserted whole rather
        // than by substring so a paraphrase cannot slip through.
        EXPECT_EQ(std::string(error.what()),
                  "'skill_sets:' in " + path +
                      " has invalid set name True. Use lowercase letters, digits, "
                      "and internal hyphens (1–32 chars), e.g. 'work'. See "
                      "https://amd-gaia.ai/docs/spec/agent-skills#skill-sets.");
    }
}

TEST(SkillSetsManifest, ABareYamlIntSetNameIsRefused) {
    const TempDir dir("intkey");
    const std::string path =
        dir.writeManifest("skill_sets:\n  5: [inbox-triage]\ndefault_skill_set: \"5\"\n");
    EXPECT_THAT([&] { parseSkillSetsFile(path); },
                ::testing::ThrowsMessage<SkillValidationError>(
                    HasSubstr("invalid set name 5")));
}

TEST(SkillSetsManifest, ManifestErrorsTalkAboutTheManifestNotFrontmatter) {
    const TempDir dir("nullkey");
    const std::string path = dir.writeManifest("? ~\n: value\nskills: [a]\n");
    try {
        parseSkillSetsFile(path);
        FAIL() << "expected SkillValidationError";
    } catch (const SkillValidationError& error) {
        const std::string message = error.what();
        EXPECT_THAT(message, HasSubstr("agent manifest"));
        EXPECT_THAT(message, ::testing::Not(HasSubstr("frontmatter")));
    }
}

TEST(SkillSetsManifest, LookupBesideTheExecutableRunsOnEveryPlatform) {
    // The only test that exercises the per-platform executable-path code, so it
    // is not merely compiled on Linux/macOS and never run. The test binary has
    // no gaia-agent.yaml beside it, so the expected answer is "none found" —
    // what matters is that the lookup completes and never invents a path.
    const std::string found = gaia::findAgentManifestNearExecutable();
    if (!found.empty()) {
        EXPECT_TRUE(fs::is_regular_file(found)) << found;
        EXPECT_EQ(fs::path(found).filename().string(), "gaia-agent.yaml");
    }
}

// ---------------------------------------------------------------------------
// Agent integration — only the selected set loads
// ---------------------------------------------------------------------------

TEST(AgentSkillSets, LoadsOnlyTheDefaultSetPlusAlwaysOn) {
    Harness harness("default", kTwoSetManifest);
    offerAll(harness.loader);

    const std::vector<std::string> loaded = harness.agent->loadSkillSet();

    EXPECT_EQ(loaded, (std::vector<std::string>{"always-on", "inbox-triage",
                                                "newsletter-digest"}));
    EXPECT_EQ(harness.loader.loadedSet(),
              (std::set<std::string>{"always-on", "inbox-triage", "newsletter-digest"}));
    EXPECT_EQ(harness.loader.loadedSet().count("meeting-scheduling"), 0u);
    EXPECT_EQ(harness.agent->activeSkillSet(), "personal");
}

TEST(AgentSkillSets, ExplicitSkillSetBeatsTheSelectorHook) {
    Harness harness("explicit", kTwoSetManifest, /*skillSet=*/"work");
    offerAll(harness.loader);
    harness.agent->selectorAnswer = "personal";

    harness.agent->loadSkillSet();

    EXPECT_EQ(harness.agent->activeSkillSet(), "work");
    EXPECT_EQ(harness.loader.loadedSet(),
              (std::set<std::string>{"always-on", "inbox-triage", "meeting-scheduling"}));
}

TEST(AgentSkillSets, SelectorHookBeatsTheDefault) {
    Harness harness("selector", kTwoSetManifest);
    offerAll(harness.loader);
    harness.agent->selectorAnswer = "work";

    harness.agent->loadSkillSet();

    EXPECT_EQ(harness.agent->activeSkillSet(), "work");
    EXPECT_EQ(harness.agent->resolveSkillSet().source, SkillSetSource::Selector);
    EXPECT_EQ(harness.loader.loadedSet(),
              (std::set<std::string>{"always-on", "inbox-triage", "meeting-scheduling"}));
}

TEST(AgentSkillSets, AlwaysOnSkillsLoadForEverySet) {
    for (const auto& expected :
         std::vector<std::pair<std::string, std::string>>{
             {"personal", "newsletter-digest"}, {"work", "meeting-scheduling"}}) {
        Harness harness("alwayson", kTwoSetManifest, expected.first);
        offerAll(harness.loader);

        harness.agent->loadSkillSet();

        EXPECT_EQ(harness.loader.loadedSet(),
                  (std::set<std::string>{"always-on", "inbox-triage", expected.second}))
            << "set: " << expected.first;
    }
}

TEST(AgentSkillSets, UnknownSkillSetFailsLoudlyAndLoadsNothing) {
    Harness harness("unknown", kTwoSetManifest, /*skillSet=*/"buisness");
    offerAll(harness.loader);

    EXPECT_THAT([&] { harness.agent->loadSkillSet(); },
                ::testing::ThrowsMessage<SkillSetError>(
                    HasSubstr("Valid sets: personal, work")));

    EXPECT_TRUE(harness.loader.loaded.empty());
    EXPECT_FALSE(harness.agent->activeSkillSet().has_value());
}

TEST(AgentSkillSets, SwitchingSetsUnloadsOnlyThePreviousSetsOwnSkills) {
    Harness harness("switch", kTwoSetManifest);
    offerAll(harness.loader);
    harness.agent->loadSkillSet();
    harness.loader.calls.clear();

    harness.agent->loadSkillSet("work");

    EXPECT_EQ(harness.agent->activeSkillSet(), "work");
    EXPECT_EQ(harness.loader.loadedSet(),
              (std::set<std::string>{"always-on", "inbox-triage", "meeting-scheduling"}));
    // Exactly one unload, and it is the skill only `personal` wanted.
    EXPECT_THAT(harness.loader.calls, ::testing::Contains("unload:newsletter-digest"));
    EXPECT_THAT(harness.loader.calls,
                ::testing::Not(::testing::Contains("unload:always-on")));
    EXPECT_THAT(harness.loader.calls,
                ::testing::Not(::testing::Contains("unload:inbox-triage")));
}

TEST(AgentSkillSets, AlwaysOnSkillSurvivesASetSwitch) {
    // The acceptance case, asserted on call history rather than final state:
    // an unload-then-reload would leave the same set loaded but is still a
    // capability gap (and a tool-registry churn) the contract forbids.
    Harness harness("survive", kTwoSetManifest);
    offerAll(harness.loader);
    harness.agent->loadSkillSet("personal");
    harness.loader.calls.clear();

    harness.agent->loadSkillSet("work");

    EXPECT_TRUE(harness.loader.isLoaded("always-on"));
    EXPECT_THAT(harness.loader.calls,
                ::testing::Not(::testing::Contains("unload:always-on")));
}

TEST(AgentSkillSets, SwitchingLeavesAHandLoadedSkillAlone) {
    // A skill loaded outside a set is not a set's to unload.
    Harness harness("handloaded", kTwoSetManifest);
    offerAll(harness.loader);
    harness.agent->loadSkillSet();
    harness.loader.loadSkill("hand-loaded");

    harness.agent->loadSkillSet("work");

    EXPECT_TRUE(harness.loader.isLoaded("hand-loaded"));
    EXPECT_FALSE(harness.loader.isLoaded("newsletter-digest"));
    EXPECT_TRUE(harness.loader.isLoaded("meeting-scheduling"));
}

TEST(AgentSkillSets, MissingRequiredSkillFailsLoudly) {
    Harness harness("required", kTwoSetManifest);
    offerAll(harness.loader);
    harness.loader.available.erase("meeting-scheduling");

    EXPECT_THAT([&] { harness.agent->loadSkillSet("work"); },
                ::testing::ThrowsMessage<SkillSetError>(
                    HasSubstr("meeting-scheduling")));
}

TEST(AgentSkillSets, MissingOptionalSkillIsSkippedWithAWarning) {
    const char* const manifest = R"(
skill_sets:
  work:
    - meeting-scheduling
    - name: not-bundled-anywhere
      required: false
default_skill_set: work
)";
    Harness harness("optional", manifest);
    harness.loader.offer("meeting-scheduling");

    const std::vector<std::string> loaded = harness.agent->loadSkillSet();

    EXPECT_EQ(loaded, std::vector<std::string>{"meeting-scheduling"});
    EXPECT_EQ(harness.agent->activeSkillSet(), "work");
    EXPECT_THAT(harness.warnings,
                ::testing::Contains(HasSubstr("not-bundled-anywhere")));
}

TEST(AgentSkillSets, AFailedSwitchLeavesTheAgentExactlyAsItWas) {
    // All-or-nothing: a half-switched agent lies about what it is carrying.
    const char* const manifest = R"(
skill_sets:
  personal: [inbox-triage, newsletter-digest]
  work: [meeting-scheduling, not-bundled-anywhere]
default_skill_set: personal
)";
    Harness harness("rollback", manifest);
    offerAll(harness.loader);
    harness.agent->loadSkillSet();
    const std::set<std::string> before = harness.loader.loadedSet();
    ASSERT_EQ(before, (std::set<std::string>{"inbox-triage", "newsletter-digest"}));

    EXPECT_THROW(harness.agent->loadSkillSet("work"), SkillSetError);

    // Still the personal set, in full, and nothing from 'work' left behind.
    EXPECT_EQ(harness.agent->activeSkillSet(), "personal");
    EXPECT_EQ(harness.loader.loadedSet(), before);
    EXPECT_FALSE(harness.loader.isLoaded("meeting-scheduling"));

    // And the failure did not corrupt tracking: a later successful switch still
    // retires exactly the personal set.
    harness.loader.offer("not-bundled-anywhere");
    harness.agent->loadSkillSet("work");
    EXPECT_EQ(harness.agent->activeSkillSet(), "work");
    EXPECT_EQ(harness.loader.loadedSet(),
              (std::set<std::string>{"meeting-scheduling", "not-bundled-anywhere"}));
}

TEST(AgentSkillSets, ExplicitRequestOnASetLessAgentIsNeverDiscarded) {
    // An agent with no declarations is falsy; an early return on that would drop
    // the user's --skill-set with no error — the exact silent fallback the spec
    // says cannot happen.
    Harness harness("setless", "id: demo\n", /*skillSet=*/"work");
    EXPECT_THAT([&] { harness.agent->loadSkillSet(); },
                ::testing::ThrowsMessage<SkillSetError>(
                    HasSubstr("declares no 'skill_sets:' block")));

    Harness plain("setless2", "id: demo\n");
    EXPECT_THAT([&] { plain.agent->loadSkillSet("work"); },
                ::testing::ThrowsMessage<SkillSetError>(
                    HasSubstr("declares no 'skill_sets:' block")));
}

TEST(AgentSkillSets, AnAgentWithoutSkillBlocksLoadsNothing) {
    Harness harness("noskills", "id: demo\n");
    EXPECT_TRUE(harness.agent->loadSkillSet().empty());
    EXPECT_TRUE(harness.loader.loaded.empty());
    EXPECT_FALSE(harness.agent->activeSkillSet().has_value());
}

TEST(AgentSkillSets, TwoAgentsKeepTheirOwnSets) {
    Harness first("percfg1", kTwoSetManifest, "personal");
    Harness second("percfg2", kTwoSetManifest, "work");
    offerAll(first.loader);
    offerAll(second.loader);

    first.agent->loadSkillSet();
    second.agent->loadSkillSet();

    EXPECT_EQ(first.agent->activeSkillSet(), "personal");
    EXPECT_EQ(second.agent->activeSkillSet(), "work");
    EXPECT_TRUE(first.loader.isLoaded("newsletter-digest"));
    EXPECT_FALSE(first.loader.isLoaded("meeting-scheduling"));
    EXPECT_TRUE(second.loader.isLoaded("meeting-scheduling"));
    EXPECT_FALSE(second.loader.isLoaded("newsletter-digest"));
}

TEST(AgentSkillSets, ASwitchIsNotSticky) {
    Harness harness("sticky", kTwoSetManifest);
    offerAll(harness.loader);
    harness.agent->loadSkillSet("work");
    ASSERT_EQ(harness.agent->activeSkillSet(), "work");

    // A later bare call re-runs the full resolution and returns to the default.
    harness.agent->loadSkillSet();

    EXPECT_EQ(harness.agent->activeSkillSet(), "personal");
    EXPECT_FALSE(harness.loader.isLoaded("meeting-scheduling"));
}

TEST(AgentSkillSets, SkillSetLoadedTracksOnlyWhatTheSetOwns) {
    Harness harness("ownership", kTwoSetManifest);
    offerAll(harness.loader);
    harness.agent->loadSkillSet();
    harness.loader.loadSkill("hand-loaded");

    EXPECT_THAT(harness.agent->skillSetLoaded(),
                ::testing::ElementsAre("always-on", "inbox-triage",
                                       "newsletter-digest"));
    EXPECT_THAT(harness.agent->skillSetLoaded(),
                ::testing::Not(::testing::Contains("hand-loaded")));
}

// ---------------------------------------------------------------------------
// Version pins (#2864) — declared, unenforced, never silent
// ---------------------------------------------------------------------------

TEST(AgentSkillSets, ADeclaredVersionPinIsReportedNotSilentlyIgnored) {
    const char* const manifest = R"(
skill_sets:
  work:
    - name: meeting-scheduling
      version: ">=2.0.0"
default_skill_set: work
)";
    Harness harness("pin", manifest);
    harness.loader.offer("meeting-scheduling", "1.0.0");

    harness.agent->loadSkillSet();

    ASSERT_FALSE(harness.warnings.empty());
    const std::string warning = harness.warnings.front();
    EXPECT_THAT(warning, HasSubstr("meeting-scheduling"));
    EXPECT_THAT(warning, HasSubstr(">=2.0.0"));   // the pin
    EXPECT_THAT(warning, HasSubstr("1.0.0"));     // what is actually on disk
    EXPECT_THAT(warning, HasSubstr("does not yet enforce"));
}

TEST(AgentSkillSets, APinOnASkillDeclaringNoVersionStillWarns) {
    const char* const manifest = R"(
skill_sets:
  work:
    - name: meeting-scheduling
      version: ">=2.0.0"
default_skill_set: work
)";
    Harness harness("pinnoversion", manifest);
    harness.loader.offer("meeting-scheduling", "");

    harness.agent->loadSkillSet();

    ASSERT_FALSE(harness.warnings.empty());
    EXPECT_THAT(harness.warnings.front(), HasSubstr("the skill declares none"));
}

TEST(AgentSkillSets, NoPinMeansNoVersionWarning) {
    Harness harness("nopin", kTwoSetManifest);
    offerAll(harness.loader);

    harness.agent->loadSkillSet();

    for (const std::string& warning : harness.warnings) {
        EXPECT_THAT(warning, ::testing::Not(HasSubstr("version pin")));
    }
}

// ---------------------------------------------------------------------------
// The no-loader state (pre-#2800)
// ---------------------------------------------------------------------------

TEST(AgentSkillSets, ResolvingWithoutALoaderWarnsRatherThanLoadingSilently) {
    Harness harness("noloader", kTwoSetManifest);
    harness.agent->setSkillLoader(nullptr);

    const std::vector<std::string> loaded = harness.agent->loadSkillSet();

    EXPECT_TRUE(loaded.empty());
    EXPECT_EQ(harness.agent->activeSkillSet(), "personal");
    EXPECT_THAT(harness.warnings,
                ::testing::Contains(HasSubstr("no skill loader is installed")));
}

TEST(AgentSkillSets, AnUndeclaredNameStillThrowsWithoutALoader) {
    Harness harness("noloaderbad", kTwoSetManifest, /*skillSet=*/"buisness");
    harness.agent->setSkillLoader(nullptr);
    EXPECT_THROW(harness.agent->loadSkillSet(), SkillSetError);
}

TEST(AgentSkillSets, DetachingTheLoaderMidSessionRefusesTheNextSwitch) {
    // Otherwise the agent would report the new set while the old set's skills
    // stayed registered in the detached loader, unreachable and unretirable.
    Harness harness("detached", kTwoSetManifest);
    offerAll(harness.loader);
    harness.agent->loadSkillSet("personal");

    harness.agent->setSkillLoader(nullptr);

    EXPECT_THAT([&] { harness.agent->loadSkillSet("work"); },
                ::testing::ThrowsMessage<SkillSetError>(HasSubstr("has been detached")));
    // Unchanged: still reporting, and still carrying, the personal set.
    EXPECT_EQ(harness.agent->activeSkillSet(), "personal");
    EXPECT_TRUE(harness.loader.isLoaded("newsletter-digest"));
}
