// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Unit tests for the SKILL.md parser, writer, and validator (issue #2798).
//
// Mirrors tests/unit/test_skills_format.py case for case: the two runtimes
// read the same skills off the same disk, so a skill that parses in one must
// parse in the other and be refused by both for the same reason.

#include "gaia/skill.h"

#include <gmock/gmock.h>
#include <gtest/gtest.h>

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <functional>
#include <string>
#include <vector>

namespace fs = std::filesystem;
using gaia::Skill;
using gaia::SkillValidationError;
using ::testing::HasSubstr;

namespace {

const char* const kBare = R"(---
name: bare-standard
description: Walk through a production incident postmortem. Use when the user mentions an outage.
---

# Incident Review

1. Establish the timeline.
)";

const char* const kFull = R"(---
name: web-search
description: Search the web via the Brave Search API.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: verified
    permissions:
      - network:read:*.brave.com
    requirements:
      python: ">=3.10"
      dependencies:
        - requests>=2.31
      env_vars:
        - BRAVE_API_KEY
      hardware: {npu: optional}
    tools:
      - name: search_web
        description: Search the web for current information
        parameters:
          query: {type: string, required: true}
          max_results: {type: integer, required: false}
        returns: {type: object}
        atomic: true
    tools_required:
      - remember
  hermes:
    category: research
---

# Web Search

Search first, then fetch the best result.
)";

/// Run `fn` and return the SkillValidationError message it raised.
std::string errorFrom(const std::function<void()>& fn) {
    try {
        fn();
    } catch (const SkillValidationError& exc) {
        return exc.what();
    } catch (const std::exception& exc) {
        ADD_FAILURE() << "expected SkillValidationError, got: " << exc.what();
        return "";
    }
    ADD_FAILURE() << "expected SkillValidationError, nothing was thrown";
    return "";
}

std::string frontmatter(const std::string& fields, const std::string& body = "body") {
    return "---\n" + fields + "---\n\n" + body + "\n";
}

/// A unique scratch directory, removed by the fixture.
class TempDir {
public:
    explicit TempDir(const std::string& label) {
        static int counter = 0;
        path_ = fs::temp_directory_path() /
                ("gaia_skill_test_" + label + "_" + std::to_string(++counter));
        fs::remove_all(path_);
        fs::create_directories(path_);
    }
    ~TempDir() {
        std::error_code ec;
        fs::remove_all(path_, ec);
    }
    TempDir(const TempDir&) = delete;
    TempDir& operator=(const TempDir&) = delete;

    const fs::path& path() const { return path_; }

    /// Write `text` to <dir>/SKILL.md and return the directory.
    fs::path writeSkillDir(const std::string& dirName, const std::string& text) const {
        const fs::path dir = path_ / dirName;
        fs::create_directories(dir);
        std::ofstream out(dir / gaia::SKILL_FILENAME, std::ios::binary);
        out << text;
        return dir;
    }

private:
    fs::path path_;
};

// ---------------------------------------------------------------------------
// Conformance corpus
//
// Discovered from disk rather than hardcoded: tests/fixtures/skills/ has 6
// skills today and tests/fixtures/openclaw_skills/ adds 26 more when PR #2693
// lands. A hardcoded list would silently ignore the new ones.
// ---------------------------------------------------------------------------

bool isSkillCorpusDir(const std::string& name) {
    static const std::string kSuffix = "_skills";
    if (name == "skills") return true;
    return name.size() > kSuffix.size() &&
           name.compare(name.size() - kSuffix.size(), kSuffix.size(), kSuffix) == 0;
}

std::vector<std::string> discoverCorpus() {
    std::vector<std::string> skills;
    std::error_code ec;
    const fs::path fixtures(GAIA_REPO_FIXTURES_DIR);
    for (const auto& corpus : fs::directory_iterator(fixtures, ec)) {
        if (!corpus.is_directory()) continue;
        if (!isSkillCorpusDir(corpus.path().filename().string())) continue;
        for (const auto& skill : fs::directory_iterator(corpus.path(), ec)) {
            if (!skill.is_directory()) continue;
            if (fs::exists(skill.path() / gaia::SKILL_FILENAME)) {
                skills.push_back(skill.path().string());
            }
        }
    }
    std::sort(skills.begin(), skills.end());
    return skills;
}

std::string corpusTestName(const ::testing::TestParamInfo<std::string>& info) {
    fs::path path(info.param);
    std::string name =
        path.parent_path().filename().string() + "_" + path.filename().string();
    for (char& c : name) {
        if (!std::isalnum(static_cast<unsigned char>(c))) c = '_';
    }
    return name;
}

}  // namespace

// ---------------------------------------------------------------------------
// Round-trip identity
// ---------------------------------------------------------------------------

TEST(SkillRoundTrip, BareIsIdentity) {
    const Skill first = gaia::parseSkill(kBare);
    EXPECT_EQ(gaia::parseSkill(gaia::toMarkdown(first)), first);
}

TEST(SkillRoundTrip, FullIsIdentity) {
    const Skill first = gaia::parseSkill(kFull);
    EXPECT_EQ(gaia::parseSkill(gaia::toMarkdown(first)), first);
}

TEST(SkillRoundTrip, IsByteStableAfterOnePass) {
    for (const char* text : {kBare, kFull}) {
        const std::string once = gaia::toMarkdown(gaia::parseSkill(text));
        const std::string twice = gaia::toMarkdown(gaia::parseSkill(once));
        EXPECT_EQ(twice, once);
    }
}

TEST(SkillRoundTrip, PreservesForeignMetadataNamespaces) {
    const Skill skill = gaia::parseSkill(kFull);
    EXPECT_EQ(skill.otherMetadata,
              gaia::SkillJson({{"hermes", {{"category", "research"}}}}));
    EXPECT_EQ(gaia::parseSkill(gaia::toMarkdown(skill)).otherMetadata,
              skill.otherMetadata);
}

TEST(SkillRoundTrip, PreservesUnknownTopLevelAndGaiaKeys) {
    const Skill skill = gaia::parseSkill(frontmatter(
        "name: ok\ndescription: d\nvendor-note: keep me\n"
        "metadata:\n  gaia:\n    future_field: 42\n"));
    EXPECT_EQ(skill.extraFields["vendor-note"], "keep me");
    EXPECT_EQ(skill.gaia.extra["future_field"], 42);
    EXPECT_EQ(gaia::parseSkill(gaia::toMarkdown(skill)), skill);
}

TEST(SkillRoundTrip, IgnoresButPreservesStandardExtras) {
    // compatibility / allowed-tools / disallowed-tools parse, survive a write,
    // and never grant anything.
    const Skill skill = gaia::parseSkill(frontmatter(
        "name: ok\ndescription: d\nallowed-tools: Read, Write\n"
        "disallowed-tools: Bash\ncompatibility: \">=1.0\"\n"));
    EXPECT_EQ(skill.extraFields["allowed-tools"], "Read, Write");
    EXPECT_EQ(skill.extraFields["disallowed-tools"], "Bash");
    EXPECT_EQ(skill.extraFields["compatibility"], ">=1.0");
    EXPECT_TRUE(skill.gaia.permissions.empty());
    EXPECT_EQ(gaia::parseSkill(gaia::toMarkdown(skill)), skill);
}

TEST(SkillRoundTrip, AmbiguousScalarsKeepTheirType) {
    // The emitter has to quote a string that would otherwise read back as a
    // number, a bool, or null — otherwise a rewrite silently retypes it.
    const Skill skill = gaia::parseSkill(frontmatter(
        "name: ok\ndescription: d\n"
        "s-numeric: \"1.0\"\ns-int: \"42\"\ns-bool: \"true\"\ns-null: \"null\"\n"
        "s-empty: \"\"\nreal-int: 42\nreal-float: 1.5\nreal-bool: true\nreal-null: ~\n"));
    EXPECT_TRUE(skill.extraFields["s-numeric"].is_string());
    EXPECT_TRUE(skill.extraFields["s-int"].is_string());
    EXPECT_TRUE(skill.extraFields["s-bool"].is_string());
    EXPECT_TRUE(skill.extraFields["real-int"].is_number_integer());
    EXPECT_TRUE(skill.extraFields["real-float"].is_number_float());
    EXPECT_TRUE(skill.extraFields["real-bool"].is_boolean());
    EXPECT_TRUE(skill.extraFields["real-null"].is_null());
    EXPECT_EQ(gaia::parseSkill(gaia::toMarkdown(skill)), skill);
}

TEST(SkillRoundTrip, ExponentialFloatsKeepTheirDot) {
    // ostringstream renders 1e16 as "1e+16", which floatPattern() (like
    // PyYAML's) rejects for want of a dot -- so a rewrite would silently
    // retype the float to a string. PyYAML's represent_float inserts ".0"
    // before the exponent; the emitter has to do the same.
    const Skill skill = gaia::parseSkill(frontmatter(
        "name: ok\ndescription: d\nbig: 1.0e+16\nsmall: 1.0e-16\n"));
    ASSERT_TRUE(skill.extraFields["big"].is_number_float());
    ASSERT_TRUE(skill.extraFields["small"].is_number_float());

    const std::string rewritten = gaia::toMarkdown(skill);
    EXPECT_NE(rewritten.find("1.0e+16"), std::string::npos) << rewritten;
    EXPECT_NE(rewritten.find("1.0e-16"), std::string::npos) << rewritten;

    const Skill reparsed = gaia::parseSkill(rewritten);
    EXPECT_TRUE(reparsed.extraFields["big"].is_number_float()) << rewritten;
    EXPECT_TRUE(reparsed.extraFields["small"].is_number_float()) << rewritten;
    EXPECT_EQ(reparsed, skill);
}

TEST(SkillRoundTrip, MultiLineAndSpecialCharacterValuesSurvive) {
    Skill skill;
    skill.name = "quoting";
    skill.description = "Line one.\nLine two: with a colon — and a # hash.";
    skill.extraFields["odd-key: with colon"] = "  padded  ";
    skill.extraFields["glob"] = "*.brave.com";
    EXPECT_EQ(gaia::parseSkill(gaia::toMarkdown(skill)), skill);
}

TEST(SkillRoundTrip, WriteAndReparseFromDisk) {
    const TempDir tmp("write");
    const Skill skill = gaia::parseSkill(kFull);
    const fs::path dir = tmp.writeSkillDir("web-search", gaia::toMarkdown(skill));
    EXPECT_EQ(gaia::parseSkillFile(dir.string()), skill);
}

// ---------------------------------------------------------------------------
// Scalar resolution
//
// Golden table transcribed from PyYAML's SafeLoader resolvers. format.py reads
// skills with yaml.safe_load, so these are the values the *Python* runtime sees
// for the same bytes — pinned here so a C++-side change cannot drift from it
// without a Python interpreter in the test loop.
// ---------------------------------------------------------------------------

struct ScalarCase {
    std::string label;
    std::string yaml;     // the plain scalar as written in the frontmatter
    std::string expected; // its JSON dump after resolution
};

class SkillScalar : public ::testing::TestWithParam<ScalarCase> {};

TEST_P(SkillScalar, ResolvesLikePyYaml) {
    const Skill skill = gaia::parseSkill(
        frontmatter("name: ok\ndescription: d\nprobe: " + GetParam().yaml + "\n"));
    EXPECT_EQ(skill.extraFields["probe"].dump(), GetParam().expected);
    // ...and survives a rewrite unchanged.
    EXPECT_EQ(gaia::parseSkill(gaia::toMarkdown(skill)), skill);
}

INSTANTIATE_TEST_SUITE_P(
    PyYamlParity, SkillScalar,
    ::testing::Values(
        // YAML 1.1 booleans — the 1.2 core schema would call these strings.
        ScalarCase{"Yes", "yes", "true"}, ScalarCase{"No", "no", "false"},
        ScalarCase{"On", "on", "true"}, ScalarCase{"Off", "OFF", "false"},
        ScalarCase{"True", "true", "true"}, ScalarCase{"FalseUpper", "FALSE", "false"},
        // Single letters are NOT booleans in PyYAML, despite the folklore.
        ScalarCase{"BareY", "y", "\"y\""}, ScalarCase{"BareN", "n", "\"n\""},
        // Integers: decimal, leading-zero octal, binary, hex, underscores,
        // sexagesimal.
        ScalarCase{"Decimal", "42", "42"},
        ScalarCase{"Octal", "0755", "493"},
        ScalarCase{"OctalShort", "010", "8"},
        ScalarCase{"Binary", "0b1010", "10"},
        ScalarCase{"Hex", "0x1f", "31"},
        ScalarCase{"Underscores", "1_000", "1000"},
        ScalarCase{"Sexagesimal", "12:30", "750"},
        ScalarCase{"Negative", "-7", "-7"},
        // 0o is a YAML 1.2 spelling PyYAML does not know — it stays a string.
        ScalarCase{"NotOctalPrefix", "0o17", "\"0o17\""},
        // Floats: PyYAML requires the '.' and a signed exponent.
        ScalarCase{"Float", "1.5", "1.5"},
        ScalarCase{"FloatNoFraction", "1.", "1.0"},
        ScalarCase{"LeadingDot", ".5", "0.5"},
        ScalarCase{"ExponentNeedsADot", "1e+20", "\"1e+20\""},
        ScalarCase{"SignedLeadingDotIsAString", "+.5e+3", "\"+.5e+3\""},
        // Null.
        ScalarCase{"Tilde", "~", "null"}, ScalarCase{"NullWord", "NULL", "null"},
        // Types JSON cannot hold keep their literal text and stay unquoted on
        // write, so Python still reads the value it read before.
        ScalarCase{"Infinity", ".inf", "\".inf\""},
        ScalarCase{"NotANumber", ".NaN", "\".NaN\""},
        ScalarCase{"Timestamp", "2024-01-01", "\"2024-01-01\""},
        ScalarCase{"ValueTag", "=", "\"=\""},
        // Out of long long range: keep the literal so nothing is truncated.
        ScalarCase{"HugeInteger", "99999999999999999999",
                   "\"99999999999999999999\""}),
    [](const ::testing::TestParamInfo<ScalarCase>& info) { return info.param.label; });

TEST(SkillScalarEmission, UnrepresentableValuesAreWrittenBackUnquoted) {
    // Quoting .inf or a date would change what PyYAML reads on the next load.
    const Skill skill = gaia::parseSkill(frontmatter(
        "name: ok\ndescription: d\nlimit: .inf\nsince: 2024-01-01\n"));
    const std::string written = gaia::toMarkdown(skill);
    EXPECT_THAT(written, HasSubstr("limit: .inf\n"));
    EXPECT_THAT(written, HasSubstr("since: 2024-01-01\n"));
}

TEST(SkillScalarEmission, StringsThatLookTypedAreQuoted) {
    Skill skill;
    skill.name = "typed";
    skill.description = "d";
    skill.extraFields["a"] = "yes";
    skill.extraFields["b"] = "0755";
    skill.extraFields["c"] = "12:30";
    skill.extraFields["d"] = "~";
    const std::string written = gaia::toMarkdown(skill);
    EXPECT_THAT(written, HasSubstr("a: \"yes\""));
    EXPECT_THAT(written, HasSubstr("b: \"0755\""));
    EXPECT_THAT(written, HasSubstr("c: \"12:30\""));
    EXPECT_THAT(written, HasSubstr("d: \"~\""));
    EXPECT_EQ(gaia::parseSkill(written), skill);
}

TEST(SkillScalarEmission, KeyOrderIsTheAuthorsOrder) {
    const Skill skill = gaia::parseSkill(frontmatter(
        "name: ok\ndescription: d\nzeta: 1\nalpha: 2\nmid: 3\n"));
    const std::string written = gaia::toMarkdown(skill);
    EXPECT_LT(written.find("zeta:"), written.find("alpha:"));
    EXPECT_LT(written.find("alpha:"), written.find("mid:"));
}

TEST(SkillFailure, UnsupportedYamlTagIsRefused) {
    EXPECT_THAT(errorFrom([] {
                    gaia::parseSkill(
                        frontmatter("name: ok\ndescription: d\nx: !custom hi\n"));
                }),
                HasSubstr("unsupported YAML tag"));
}

TEST(SkillFailure, SequenceKeyIsRefused) {
    EXPECT_THAT(errorFrom([] {
                    gaia::parseSkill(
                        frontmatter("name: ok\ndescription: d\nm:\n  ? [a, b]\n  : v\n"));
                }),
                HasSubstr("non-scalar YAML key"));
}

TEST(SkillFailure, NullKeyIsRefusedRatherThanBecomingTheEmptyKey) {
    EXPECT_THAT(errorFrom([] {
                    gaia::parseSkill(
                        frontmatter("name: ok\ndescription: d\nm:\n  null: a\n"));
                }),
                HasSubstr("empty, null, or non-scalar YAML key"));
}

// ---------------------------------------------------------------------------
// Conformance corpus — every skill on disk parses and round-trips
// ---------------------------------------------------------------------------

class SkillCorpus : public ::testing::TestWithParam<std::string> {};

TEST_P(SkillCorpus, ParsesAndRoundTrips) {
    const Skill skill = gaia::parseSkillFile(GetParam());
    EXPECT_FALSE(skill.name.empty());
    EXPECT_FALSE(skill.description.empty());
    EXPECT_EQ(gaia::parseSkill(gaia::toMarkdown(skill)), skill);
}

TEST_P(SkillCorpus, MetadataOnlyParseKeepsEverythingButTheBody) {
    const Skill full = gaia::parseSkillFile(GetParam());
    const Skill level1 = gaia::parseSkillMetadata(GetParam());
    EXPECT_EQ(level1.name, full.name);
    EXPECT_EQ(level1.description, full.description);
    EXPECT_EQ(level1.gaia, full.gaia);
    EXPECT_EQ(level1.body, "");
}

INSTANTIATE_TEST_SUITE_P(Fixtures, SkillCorpus, ::testing::ValuesIn(discoverCorpus()),
                         corpusTestName);

TEST(SkillCorpusDiscovery, FindsTheOnDiskCorpus) {
    // Guards the discovery itself: an empty corpus would make every
    // parameterized case above vacuously pass.
    EXPECT_GE(discoverCorpus().size(), 6u);
}

// ---------------------------------------------------------------------------
// The bare agentskills.io skill
// ---------------------------------------------------------------------------

TEST(SkillBare, LoadsInstructionOnly) {
    const Skill skill = gaia::parseSkill(kBare);
    EXPECT_TRUE(skill.isInstructionOnly());
    EXPECT_EQ(skill.securityTier(), "experimental");
    EXPECT_EQ(skill.securityTier(), gaia::DEFAULT_SECURITY_TIER);
    EXPECT_TRUE(skill.gaia.tools.empty());
    EXPECT_TRUE(skill.gaia.permissions.empty());
    EXPECT_TRUE(skill.gaia.toolsRequired.empty());
    EXPECT_FALSE(skill.version.has_value());
    EXPECT_THAT(skill.body, HasSubstr("# Incident Review"));
}

TEST(SkillBare, DoesNotGainAGaiaBlockOnWrite) {
    // A standard skill stays standard — GAIA never stamps its defaults into it.
    const std::string written = gaia::toMarkdown(gaia::parseSkill(kBare));
    EXPECT_THAT(written, ::testing::Not(HasSubstr("metadata")));
    EXPECT_THAT(written, ::testing::Not(HasSubstr("security_tier")));
}

TEST(SkillBare, IsConstructibleDirectly) {
    Skill skill;
    skill.name = "hand-made";
    skill.description = "Built in code.";
    EXPECT_TRUE(skill.isInstructionOnly());
    EXPECT_EQ(skill.securityTier(), "experimental");
    EXPECT_EQ(gaia::parseSkill(gaia::toMarkdown(skill)), skill);
}

// ---------------------------------------------------------------------------
// Typed metadata
// ---------------------------------------------------------------------------

TEST(SkillMetadata, GaiaFieldsParseIntoTypedMetadata) {
    const Skill skill = gaia::parseSkill(kFull);
    EXPECT_EQ(skill.securityTier(), "verified");
    EXPECT_EQ(skill.gaia.permissions,
              std::vector<std::string>{"network:read:*.brave.com"});
    ASSERT_TRUE(skill.gaia.requirements.python.has_value());
    EXPECT_EQ(*skill.gaia.requirements.python, ">=3.10");
    EXPECT_EQ(skill.gaia.requirements.envVars,
              std::vector<std::string>{"BRAVE_API_KEY"});
    EXPECT_EQ(skill.gaia.requirements.dependencies,
              std::vector<std::string>{"requests>=2.31"});
    EXPECT_EQ(skill.gaia.requirements.hardware,
              gaia::SkillJson({{"npu", "optional"}}));
    EXPECT_EQ(skill.gaia.toolsRequired, std::vector<std::string>{"remember"});

    ASSERT_EQ(skill.gaia.tools.size(), 1u);
    const gaia::SkillTool& declared = skill.gaia.tools.front();
    EXPECT_EQ(declared.name, "search_web");
    EXPECT_TRUE(declared.atomic);
    EXPECT_EQ(declared.parameters["query"],
              gaia::SkillJson({{"type", "string"}, {"required", true}}));
    EXPECT_EQ(skill.toolNames(), std::vector<std::string>{"search_web"});
    EXPECT_EQ(skill.namespacedToolName("search_web"), "web-search/search_web");
}

TEST(SkillMetadata, MetadataOnlyParseDropsTheBody) {
    // Progressive disclosure level 1: metadata resident, instructions not.
    const TempDir tmp("level1");
    const fs::path dir = tmp.writeSkillDir("web-search", kFull);
    const Skill skill = gaia::parseSkillMetadata(dir.string());
    EXPECT_EQ(skill.name, "web-search");
    EXPECT_FALSE(skill.description.empty());
    EXPECT_EQ(skill.body, "");
    EXPECT_EQ(skill.toolNames(), std::vector<std::string>{"search_web"});
}

TEST(SkillMetadata, ProvenanceIsRecordedButNotPartOfEquality) {
    const TempDir tmp("provenance");
    const fs::path dir = tmp.writeSkillDir("bare-standard", kBare);
    const Skill fromDisk = gaia::parseSkillFile(dir.string(), "user", true);
    EXPECT_EQ(fromDisk.root, "user");
    EXPECT_TRUE(fromDisk.readOnly);
    EXPECT_EQ(fs::path(fromDisk.directory()), dir);
    EXPECT_EQ(fs::path(fromDisk.toolsPath()), dir / gaia::SKILL_TOOLS_FILENAME);
    // Two copies from different roots are the same Skill — that is what makes
    // round-trip identity meaningful across directories.
    EXPECT_EQ(fromDisk, gaia::parseSkill(kBare));
}

// ---------------------------------------------------------------------------
// Loud failures
// ---------------------------------------------------------------------------

TEST(SkillFailure, MissingFrontmatter) {
    EXPECT_THAT(errorFrom([] { gaia::parseSkill("# Just markdown\n"); }),
                HasSubstr("no YAML frontmatter"));
}

TEST(SkillFailure, InvalidYaml) {
    EXPECT_THAT(
        errorFrom([] { gaia::parseSkill("---\nname: x\n  bad: [indent\n---\n\nbody\n"); }),
        HasSubstr("invalid"));
}

TEST(SkillFailure, FrontmatterIsNotAMapping) {
    EXPECT_THAT(errorFrom([] { gaia::parseSkill("---\n- a\n- b\n---\n\nbody\n"); }),
                HasSubstr("must be a YAML mapping"));
}

TEST(SkillFailure, MissingName) {
    EXPECT_THAT(
        errorFrom([] { gaia::parseSkill(frontmatter("description: something\n")); }),
        HasSubstr("'name' is missing"));
}

TEST(SkillFailure, MissingDescription) {
    EXPECT_THAT(errorFrom([] { gaia::parseSkill(frontmatter("name: thing\n")); }),
                HasSubstr("'description' is missing"));
}

class SkillInvalidName : public ::testing::TestWithParam<std::string> {};

TEST_P(SkillInvalidName, FailsLoudly) {
    const std::string fields = "name: " + GetParam() + "\ndescription: d\n";
    EXPECT_THAT(errorFrom([&] { gaia::parseSkill(frontmatter(fields)); }),
                HasSubstr("not a valid skill name"));
}

INSTANTIATE_TEST_SUITE_P(Names, SkillInvalidName,
                         ::testing::Values("Web-Search", "web_search", "-web", "web-",
                                           "web--search", "web search", "w\xC3\xA9\x62"));

TEST(SkillFailure, OverLongName) {
    const std::string fields = "name: " + std::string(65, 'a') + "\ndescription: d\n";
    EXPECT_THAT(errorFrom([&] { gaia::parseSkill(frontmatter(fields)); }),
                HasSubstr("the limit is 64"));
}

TEST(SkillFailure, OverLongDescription) {
    const std::string fields =
        "name: ok\ndescription: " + std::string(gaia::MAX_DESCRIPTION_LENGTH + 1, 'x') +
        "\n";
    EXPECT_THAT(errorFrom([&] { gaia::parseSkill(frontmatter(fields)); }),
                HasSubstr("the limit is 1024"));
}

TEST(SkillFailure, NameAtTheLimitIsAccepted) {
    const std::string name(gaia::MAX_NAME_LENGTH, 'a');
    EXPECT_EQ(gaia::parseSkill(frontmatter("name: " + name + "\ndescription: d\n")).name,
              name);
}

class SkillBadSemver : public ::testing::TestWithParam<std::string> {};

TEST_P(SkillBadSemver, FailsLoudly) {
    const std::string fields =
        "name: ok\ndescription: d\nversion: \"" + GetParam() + "\"\n";
    EXPECT_THAT(errorFrom([&] { gaia::parseSkill(frontmatter(fields)); }),
                HasSubstr("not valid SemVer"));
}

INSTANTIATE_TEST_SUITE_P(Versions, SkillBadSemver,
                         ::testing::Values("1.0", "v1.0.0", "1.0.0.0", "latest",
                                           "01.0.0"));

class SkillGoodSemver : public ::testing::TestWithParam<std::string> {};

TEST_P(SkillGoodSemver, IsAccepted) {
    const std::string fields =
        "name: ok\ndescription: d\nversion: \"" + GetParam() + "\"\n";
    const Skill skill = gaia::parseSkill(frontmatter(fields));
    ASSERT_TRUE(skill.version.has_value());
    EXPECT_EQ(*skill.version, GetParam());
}

// 0.0.0 is valid SemVer and reserved for an unversioned skill.
INSTANTIATE_TEST_SUITE_P(Versions, SkillGoodSemver,
                         ::testing::Values("0.0.0", "1.0.0", "1.2.3-rc.1",
                                           "1.2.3+build.5"));

TEST(SkillFailure, NumericVersionGetsAQuotingHint) {
    EXPECT_THAT(errorFrom([] {
                    gaia::parseSkill(frontmatter("name: ok\ndescription: d\nversion: 1.0\n"));
                }),
                HasSubstr("Quote it"));
}

TEST(SkillFailure, NameDirectoryMismatch) {
    const TempDir tmp("mismatch");
    const fs::path dir = tmp.writeSkillDir("not-the-name", kBare);
    EXPECT_THAT(errorFrom([&] { gaia::parseSkillFile(dir.string()); }),
                HasSubstr("but the directory is named"));
}

TEST(SkillFailure, NameDirectoryMismatchCanBeWaived) {
    const TempDir tmp("waived");
    const fs::path dir = tmp.writeSkillDir("not-the-name", kBare);
    EXPECT_EQ(gaia::parseSkillFile(dir.string(), "", false, false).name,
              "bare-standard");
}

TEST(SkillFailure, NameDirectoryMatchAccepted) {
    const TempDir tmp("match");
    const fs::path dir = tmp.writeSkillDir("bare-standard", kBare);
    EXPECT_EQ(gaia::parseSkillFile(dir.string()).name, "bare-standard");
}

TEST(SkillFailure, MissingSkillFile) {
    const TempDir tmp("empty");
    fs::create_directories(tmp.path() / "empty");
    EXPECT_THAT(errorFrom([&] { gaia::parseSkillFile((tmp.path() / "empty").string()); }),
                HasSubstr("No SKILL.md"));
}

struct GaiaBlockCase {
    std::string label;
    std::string line;
    std::string expected;
};

class SkillMalformedGaiaBlock : public ::testing::TestWithParam<GaiaBlockCase> {};

TEST_P(SkillMalformedGaiaBlock, FailsLoudly) {
    const std::string fields =
        "name: ok\ndescription: d\nmetadata:\n  gaia:\n    " + GetParam().line + "\n";
    EXPECT_THAT(errorFrom([&] { gaia::parseSkill(frontmatter(fields)); }),
                HasSubstr(GetParam().expected));
}

INSTANTIATE_TEST_SUITE_P(
    GaiaBlock, SkillMalformedGaiaBlock,
    ::testing::Values(
        GaiaBlockCase{"UnknownTier", "security_tier: nuclear", "not one of"},
        GaiaBlockCase{"ScalarPermissions", "permissions: network:read", "must be a list"},
        GaiaBlockCase{"NumericTools", "tools: 3", "must be a list"},
        GaiaBlockCase{"ScalarToolsRequired", "tools_required: query_documents",
                      "must be a list"},
        GaiaBlockCase{"ListRequirements", "requirements: []", "must be a mapping"}),
    [](const ::testing::TestParamInfo<GaiaBlockCase>& info) { return info.param.label; });

TEST(SkillFailure, MetadataIsNotAMapping) {
    EXPECT_THAT(
        errorFrom([] {
            gaia::parseSkill(frontmatter("name: ok\ndescription: d\nmetadata: nope\n"));
        }),
        HasSubstr("must be a mapping of vendor namespaces"));
}

TEST(SkillFailure, RequirementListFieldMistyped) {
    EXPECT_THAT(errorFrom([] {
                    gaia::parseSkill(frontmatter(
                        "name: ok\ndescription: d\nmetadata:\n  gaia:\n"
                        "    requirements:\n      dependencies: requests\n"));
                }),
                HasSubstr("requirements.dependencies must be a list"));
}

struct PermissionCase {
    std::string label;
    std::string permission;
    std::string expected;
};

class SkillMalformedPermission : public ::testing::TestWithParam<PermissionCase> {};

TEST_P(SkillMalformedPermission, FailsLoudly) {
    const std::string fields =
        "name: ok\ndescription: d\nmetadata:\n  gaia:\n    permissions:\n      - " +
        GetParam().permission + "\n";
    EXPECT_THAT(errorFrom([&] { gaia::parseSkill(frontmatter(fields)); }),
                HasSubstr(GetParam().expected));
}

INSTANTIATE_TEST_SUITE_P(
    Permissions, SkillMalformedPermission,
    ::testing::Values(
        PermissionCase{"NoLevel", "networkread", "missing its level"},
        PermissionCase{"UnknownDomain", "teleport:read", "unknown domain"},
        PermissionCase{"UndefinedLevel", "network:teleport", "does not define"},
        PermissionCase{"WrongDomainForLevel", "shell:read", "does not define"}),
    [](const ::testing::TestParamInfo<PermissionCase>& info) { return info.param.label; });

TEST(SkillPermissions, GrammarIsValidatedNotEnforced) {
    // filesystem:write parses — refusing it is the loader's job (issue #2799),
    // not the format layer's.
    const Skill skill = gaia::parseSkill(frontmatter(
        "name: ok\ndescription: d\nmetadata:\n  gaia:\n    permissions:\n"
        "      - filesystem:write:./**\n      - mcp:connect:mcp-tavily\n"
        "      - shell:none\n"));
    EXPECT_EQ(skill.gaia.permissions.size(), 3u);
    EXPECT_EQ(gaia::parseSkill(gaia::toMarkdown(skill)), skill);
}

TEST(SkillFailure, DuplicateToolNames) {
    EXPECT_THAT(errorFrom([] {
                    gaia::parseSkill(frontmatter(
                        "name: ok\ndescription: d\nmetadata:\n  gaia:\n    tools:\n"
                        "      - name: dup\n        parameters: {}\n"
                        "      - name: dup\n        parameters: {}\n"));
                }),
                HasSubstr("more than once"));
}

TEST(SkillFailure, ToolEntryWithoutName) {
    EXPECT_THAT(errorFrom([] {
                    gaia::parseSkill(frontmatter(
                        "name: ok\ndescription: d\nmetadata:\n  gaia:\n    tools:\n"
                        "      - description: nameless\n"));
                }),
                HasSubstr("missing its 'name'"));
}

TEST(SkillFailure, ToolParameterIsNotAMapping) {
    EXPECT_THAT(errorFrom([] {
                    gaia::parseSkill(frontmatter(
                        "name: ok\ndescription: d\nmetadata:\n  gaia:\n    tools:\n"
                        "      - name: t\n        parameters:\n          query: string\n"));
                }),
                HasSubstr("must be a mapping like"));
}

TEST(SkillFailure, MessagesNameWhatToDoAndWhereToLook) {
    // The fail-loudly rule: every message points at a fix and at a doc.
    const std::string message =
        errorFrom([] { gaia::parseSkill(frontmatter("name: Bad_Name\ndescription: d\n")); });
    EXPECT_THAT(message, HasSubstr("web-research"));  // what a good name looks like
    EXPECT_THAT(message, HasSubstr("skill-format"));  // where to look next
}

TEST(SkillFailure, HardwareIsNotAMapping) {
    EXPECT_THAT(errorFrom([] {
                    gaia::parseSkill(frontmatter(
                        "name: ok\ndescription: d\nmetadata:\n  gaia:\n"
                        "    requirements:\n      hardware: [npu]\n"));
                }),
                HasSubstr("requirements.hardware must be a mapping"));
}

TEST(SkillFailure, NodeDependenciesMistyped) {
    EXPECT_THAT(errorFrom([] {
                    gaia::parseSkill(frontmatter(
                        "name: ok\ndescription: d\nmetadata:\n  gaia:\n"
                        "    requirements:\n      node_dependencies: left-pad\n"));
                }),
                HasSubstr("requirements.node_dependencies must be a list"));
}

TEST(SkillFailure, ToolEntryIsNotAMapping) {
    EXPECT_THAT(errorFrom([] {
                    gaia::parseSkill(frontmatter(
                        "name: ok\ndescription: d\nmetadata:\n  gaia:\n    tools:\n"
                        "      - 3\n"));
                }),
                HasSubstr("must be a mapping with a 'name'"));
}

TEST(SkillFailure, ToolParametersBlockIsNotAMapping) {
    EXPECT_THAT(errorFrom([] {
                    gaia::parseSkill(frontmatter(
                        "name: ok\ndescription: d\nmetadata:\n  gaia:\n    tools:\n"
                        "      - name: t\n        parameters: 3\n"));
                }),
                HasSubstr("has 'parameters' of type int"));
}

TEST(SkillFailure, ToolReturnsIsNotAMapping) {
    EXPECT_THAT(errorFrom([] {
                    gaia::parseSkill(frontmatter(
                        "name: ok\ndescription: d\nmetadata:\n  gaia:\n    tools:\n"
                        "      - name: t\n        returns: object\n"));
                }),
                HasSubstr("has 'returns' of type str"));
}

TEST(SkillFailure, EmptyPermissionString) {
    EXPECT_THAT(errorFrom([] {
                    gaia::parseSkill(frontmatter(
                        "name: ok\ndescription: d\nmetadata:\n  gaia:\n"
                        "    permissions:\n      - \"\"\n"));
                }),
                HasSubstr("declares an empty permission"));
}

TEST(SkillFailure, ExplicitNullSecurityTierIsRefused) {
    // Python's .get(key, default) only defaults on a *missing* key, so an
    // explicit `security_tier:` with no value is an error, not the default.
    EXPECT_THAT(errorFrom([] {
                    gaia::parseSkill(frontmatter(
                        "name: ok\ndescription: d\nmetadata:\n  gaia:\n"
                        "    security_tier:\n"));
                }),
                HasSubstr("not one of"));
}

TEST(SkillAccepts, DescriptionAtTheLimit) {
    const std::string description(gaia::MAX_DESCRIPTION_LENGTH, 'x');
    EXPECT_EQ(
        gaia::parseSkill(frontmatter("name: ok\ndescription: " + description + "\n"))
            .description.size(),
        gaia::MAX_DESCRIPTION_LENGTH);
}

TEST(SkillAccepts, LimitsCountCharactersNotBytes) {
    // A 1024-character CJK description is 3072 bytes and must still be accepted,
    // exactly as format.py accepts it.
    std::string description;
    for (size_t i = 0; i < gaia::MAX_DESCRIPTION_LENGTH; ++i) description += "\xE6\xBC\xA2";
    const Skill skill =
        gaia::parseSkill(frontmatter("name: ok\ndescription: " + description + "\n"));
    EXPECT_EQ(skill.description.size(), gaia::MAX_DESCRIPTION_LENGTH * 3);
    EXPECT_EQ(gaia::parseSkill(gaia::toMarkdown(skill)), skill);
}

TEST(SkillAccepts, OmittedAndFalsyBlocksTakeTheDefaults) {
    // format.py leans on `data.get(k) or default` in a dozen places; these are
    // the falsy inputs that must be accepted rather than refused.
    for (const char* fields : {"name: ok\ndescription: d\nmetadata:\n",
                               "name: ok\ndescription: d\nmetadata:\n  gaia:\n",
                               "name: ok\ndescription: d\nmetadata:\n  gaia:\n"
                               "    permissions:\n    tools:\n    tools_required:\n"
                               "    requirements:\n"}) {
        const Skill skill = gaia::parseSkill(frontmatter(fields));
        EXPECT_EQ(skill.securityTier(), gaia::DEFAULT_SECURITY_TIER) << fields;
        EXPECT_TRUE(skill.gaia.permissions.empty()) << fields;
        EXPECT_TRUE(skill.gaia.tools.empty()) << fields;
        EXPECT_TRUE(skill.gaia.requirements.isEmpty()) << fields;
    }
}

TEST(SkillAccepts, UnknownRequirementKeysArePreserved) {
    const Skill skill = gaia::parseSkill(frontmatter(
        "name: ok\ndescription: d\nmetadata:\n  gaia:\n    requirements:\n"
        "      python: \">=3.10\"\n      gpu_vram_gb: 8\n"));
    EXPECT_EQ(skill.gaia.requirements.extra["gpu_vram_gb"], 8);
    EXPECT_EQ(gaia::parseSkill(gaia::toMarkdown(skill)), skill);
}

TEST(SkillIgnoredKeys, EveryIgnoredStandardKeyIsPreservedAndGrantsNothing) {
    std::string fields = "name: ok\ndescription: d\n";
    for (const char* key : gaia::IGNORED_STANDARD_KEYS) {
        fields += std::string(key) + ": Read\n";
    }
    const Skill skill = gaia::parseSkill(frontmatter(fields));
    for (const char* key : gaia::IGNORED_STANDARD_KEYS) {
        EXPECT_EQ(skill.extraFields[key], "Read") << key;
    }
    EXPECT_TRUE(skill.gaia.permissions.empty());
    EXPECT_EQ(gaia::parseSkill(gaia::toMarkdown(skill)), skill);
}

// ---------------------------------------------------------------------------
// Encoding tolerance
// ---------------------------------------------------------------------------

TEST(SkillEncoding, CrlfFrontmatterParses) {
    std::string crlf;
    for (const char* p = kBare; *p; ++p) {
        if (*p == '\n') crlf += '\r';
        crlf += *p;
    }
    // Python reads SKILL.md in text mode, so CRLF never reaches its parser. A
    // Windows-authored skill has to give both runtimes the same body, not just
    // one that happens to contain the same substring.
    EXPECT_EQ(gaia::parseSkill(crlf), gaia::parseSkill(kBare));
    EXPECT_EQ(gaia::parseSkill(crlf).body,
              "# Incident Review\n\n1. Establish the timeline.");
}

TEST(SkillEncoding, CrlfDoesNotLeakIntoMultiLineScalars) {
    // The body is not the only place a carriage return can survive — a literal
    // block in the frontmatter carries it into the field value itself.
    const std::string lf =
        "---\nname: ok\ndescription: |\n  Line one.\n  Line two.\n---\n\nbody\n";
    std::string crlf;
    for (char c : lf) {
        if (c == '\n') crlf += '\r';
        crlf += c;
    }
    EXPECT_EQ(gaia::parseSkill(crlf), gaia::parseSkill(lf));
    EXPECT_EQ(gaia::parseSkill(crlf).description.find('\r'), std::string::npos);
}

TEST(SkillEncoding, LeadingBomIsTolerated) {
    const Skill skill = gaia::parseSkill("\xEF\xBB\xBF" + std::string(kBare));
    EXPECT_EQ(skill.name, "bare-standard");
}

TEST(SkillEncoding, BomAndCrlfTogetherParse) {
    std::string crlf = "\xEF\xBB\xBF";
    for (const char* p = kBare; *p; ++p) {
        if (*p == '\n') crlf += '\r';
        crlf += *p;
    }
    EXPECT_EQ(gaia::parseSkill(crlf).name, "bare-standard");
}

TEST(SkillEncoding, TrailingWhitespaceOnTheDelimiterIsTolerated) {
    EXPECT_EQ(gaia::parseSkill("--- \nname: ok\ndescription: d\n---\t\n\nbody\n").name,
              "ok");
}

TEST(SkillEncoding, FrontmatterWithoutABodyParses) {
    const Skill skill = gaia::parseSkill("---\nname: ok\ndescription: d\n---\n");
    EXPECT_EQ(skill.body, "");
    EXPECT_EQ(gaia::parseSkill(gaia::toMarkdown(skill)), skill);
}

TEST(SkillEncoding, NonUtf8FileIsRefusedWithTheReason) {
    const TempDir tmp("latin1");
    // 0xE9 alone is not valid UTF-8 — format.py raises on the decode error.
    const fs::path dir =
        tmp.writeSkillDir("mojibake",
                          "---\nname: mojibake\ndescription: caf\xE9\n---\n\nbody\n");
    EXPECT_THAT(errorFrom([&] { gaia::parseSkillFile(dir.string()); }),
                HasSubstr("not valid UTF-8"));
}
