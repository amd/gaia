// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>

#include <gaia/document_index.h>
#include <gaia/process.h>
#include <gaia/tool_registry.h>

#include <atomic>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <limits>
#include <random>

using namespace gaia;
namespace fs = std::filesystem;

namespace {

struct TempDir {
    fs::path path;
    TempDir() {
        std::mt19937_64 rng(std::random_device{}());
        path = fs::temp_directory_path() / ("gaia-docidx-" + std::to_string(rng()));
        fs::create_directories(path);
    }
    ~TempDir() {
        std::error_code ec;
        fs::remove_all(path, ec);
    }
    std::string write(const std::string& name, const std::string& text) const {
        std::ofstream(path / name) << text;
        return (path / name).string();
    }
};

// Deterministic stand-in embedder: letter-frequency vectors.
std::vector<std::vector<float>> letterEmbed(const std::vector<std::string>& texts) {
    std::vector<std::vector<float>> out;
    for (const auto& t : texts) {
        std::vector<float> v(26, 0.f);
        for (unsigned char c : t) {
            if (std::isalpha(c)) v[std::tolower(c) - 'a'] += 1.f;
        }
        v[0] += 0.001f;  // never all-zero
        out.push_back(std::move(v));
    }
    return out;
}

bool havePdftotext() {
    return ProcessRunner::run("pdftotext -v", 5000).exitCode == 0;
}

const fs::path kNorthwind =
    fs::path(GAIA_REPO_FIXTURES_DIR) / "gaia" / "tiers_documents" / "northwind_annual_report.pdf";

}  // namespace

TEST(DocumentIndexTest, KeywordRetrievalFindsTheRightFile) {
    TempDir d;
    d.write("lease.txt", "The tenant pays rent of $2,400 per month. The lease term is 24 months.");
    d.write("recipe.md", "# Bread\nMix flour, water and yeast. Bake at 230 C for 35 minutes.");
    DocumentIndex idx;
    EXPECT_FALSE(idx.usesEmbeddings());
    idx.indexFile((d.path / "lease.txt").string());
    idx.indexFile((d.path / "recipe.md").string());
    auto hits = idx.query("how much is the monthly rent", 3);
    ASSERT_FALSE(hits.empty());
    EXPECT_NE(hits[0].chunk.file.find("lease.txt"), std::string::npos);
    EXPECT_EQ(hits[0].chunk.page, 0);
    EXPECT_TRUE(idx.query("zebra quantum", 3).empty()) << "no term overlap, no hits";
}

TEST(DocumentIndexTest, ReindexReplacesAFilesChunks) {
    TempDir d;
    const std::string f = d.write("notes.txt", "alpha beta gamma");
    DocumentIndex idx(letterEmbed, "letters");
    idx.indexFile(f);
    d.write("notes.txt", "delta epsilon");
    idx.indexFile(f);
    EXPECT_EQ(idx.files().size(), 1u);
    EXPECT_TRUE(idx.query("alpha", 5).empty() ||
                idx.query("alpha", 5)[0].chunk.text.find("alpha") == std::string::npos);
    auto hits = idx.query("epsilon", 5);
    ASSERT_FALSE(hits.empty());
    EXPECT_NE(hits[0].chunk.text.find("epsilon"), std::string::npos);
}

TEST(DocumentIndexTest, ABadEmbeddingLeavesThePreviousIndexIntact) {
    TempDir d;
    const std::string f = d.write("notes.txt", "alpha beta gamma");
    std::atomic<bool> broken{false};
    DocumentIndex idx(
        [&broken](const std::vector<std::string>& t) {
            auto out = letterEmbed(t);
            if (broken) out[0][0] = std::numeric_limits<float>::quiet_NaN();
            return out;
        },
        "letters");
    idx.indexFile(f);
    d.write("notes.txt", "delta epsilon");
    broken = true;
    EXPECT_THROW(idx.indexFile(f), std::runtime_error);
    broken = false;
    EXPECT_EQ(idx.files().size(), 1u);
    auto hits = idx.query("alpha", 5);
    ASSERT_FALSE(hits.empty());
    EXPECT_NE(hits[0].chunk.text.find("alpha"), std::string::npos);
}

TEST(DocumentIndexTest, FolderToolSkipsDependencyAndIgnoredFolders) {
    TempDir d;
    d.write("policy.txt", "Remote work requires manager approval.");
    d.write(".gitignore", "private/");
    fs::create_directories(d.path / "node_modules");
    fs::create_directories(d.path / "private");
    d.write("node_modules/dep.txt", "A vendored dependency readme.");
    d.write("private/secret.txt", "Something the user chose to ignore.");
    DocumentIndex idx;
    ToolRegistry reg;
    idx.registerTools(reg, nullptr);
    json indexed = reg.executeTool("index_document", json{{"path", d.path.string()}});
    ASSERT_EQ(indexed["status"], "success") << indexed.dump();
    ASSERT_EQ(indexed["indexed"].size(), 1u) << indexed.dump();
    EXPECT_NE(indexed["indexed"][0]["file"].get<std::string>().find("policy.txt"),
              std::string::npos);
}

TEST(DocumentIndexTest, FolderToolSaysWhatItSkippedAndFailsWhenThatIsEverything) {
    TempDir d;
    d.write(".gitignore", "drafts/\nsecret.txt\n");
    fs::create_directories(d.path / "build");
    fs::create_directories(d.path / "drafts");
    d.write("build/notes.txt", "Quarterly notes kept in a folder named build.");
    d.write("drafts/a.txt", "An ignored draft.");
    d.write("drafts/b.md", "Another ignored draft.");
    d.write("secret.txt", "An ignored file.");
    d.write("big.txt", std::string((1u << 20) + 1, 'a'));
    DocumentIndex idx;
    ToolRegistry reg;
    idx.registerTools(reg, nullptr);

    json none = reg.executeTool("index_document", json{{"path", d.path.string()}});
    ASSERT_EQ(none["status"], "error") << none.dump();
    EXPECT_EQ(none["skipped_files"], 5) << none.dump();
    EXPECT_EQ(none["skipped"]["ignored_by_gitignore"], 3) << none.dump();
    EXPECT_EQ(none["skipped"]["in_skipped_folder"], 1) << none.dump();
    EXPECT_EQ(none["skipped"]["too_large"], 1) << none.dump();
    const std::string error = none["error"].get<std::string>();
    for (const char* reason : {"3 ignored by .gitignore", "1 in a skipped folder (build)", "1 too large"}) {
        EXPECT_NE(error.find(reason), std::string::npos) << error;
    }

    d.write("policy.txt", "Remote work requires manager approval.");
    json some = reg.executeTool("index_document", json{{"path", d.path.string()}});
    ASSERT_EQ(some["status"], "success") << some.dump();
    EXPECT_EQ(some["indexed"].size(), 1u) << some.dump();
    EXPECT_EQ(some["skipped_files"], 5) << some.dump();
    EXPECT_FALSE(some.contains("error")) << some.dump();

    json byPath = reg.executeTool("index_document", json{{"path", (d.path / "build" / "notes.txt").string()}});
    EXPECT_EQ(byPath["status"], "success") << byPath.dump();
}

TEST(DocumentIndexTest, HybridUsesTheEmbedderForQueriesToo) {
    TempDir d;
    d.write("a.txt", "zzzz zzzz zzzz");
    d.write("b.txt", "aaaa aaaa aaaa");
    std::atomic<int> calls{0};
    DocumentIndex idx(
        [&calls](const std::vector<std::string>& t) {
            ++calls;
            return letterEmbed(t);
        },
        "letters");
    idx.indexFile((d.path / "a.txt").string());
    idx.indexFile((d.path / "b.txt").string());
    auto hits = idx.query("zz", 1);  // no keyword overlap with "zzzz"; vectors find it
    ASSERT_EQ(hits.size(), 1u);
    EXPECT_NE(hits[0].chunk.file.find("a.txt"), std::string::npos);
    EXPECT_EQ(calls.load(), 3);
}

TEST(DocumentIndexTest, PdfChunksCarryPageNumbers) {
    if (!havePdftotext()) GTEST_SKIP() << "pdftotext (poppler-utils) not installed";
    ASSERT_TRUE(fs::exists(kNorthwind)) << kNorthwind;
    DocumentIndex idx;
    EXPECT_GT(idx.indexFile(kNorthwind.string()), 50u);
    auto hits = idx.query("Reservoir B usable storage capacity", 3);
    ASSERT_FALSE(hits.empty());
    EXPECT_EQ(hits[0].chunk.page, 7);
    EXPECT_NE(hits[0].chunk.text.find("18.4"), std::string::npos);
}

TEST(DocumentIndexTest, UnreadableFilesFailLoudly) {
    TempDir d;
    EXPECT_THROW(DocumentIndex::extractPages((d.path / "missing.pdf").string()), std::runtime_error);
    const std::string fake = d.write("fake.docx", "not a zip");
    EXPECT_THROW(DocumentIndex::extractPages(fake), std::runtime_error);
}

TEST(DocumentIndexTest, ToolsIndexFolderQueryAndRespectPathCheck) {
    TempDir d;
    d.write("policy.txt", "Remote work requires manager approval and a signed agreement.");
    d.write("binary.bin", std::string("\0\1\2", 3));
    DocumentIndex idx;
    ToolRegistry reg;
    idx.registerTools(reg, [&d](const std::string& p) {
        return p.find(d.path.string()) == 0 ? "" : std::string("Refused: outside");
    });

    json none = reg.executeTool("query_documents", json{{"query", "remote work"}});
    EXPECT_EQ(none["status"], "error");

    json refused = reg.executeTool("index_document", json{{"path", "/etc"}});
    EXPECT_EQ(refused["status"], "error");

    json indexed = reg.executeTool("index_document", json{{"path", d.path.string()}});
    EXPECT_EQ(indexed["status"], "success") << indexed.dump();
    EXPECT_EQ(indexed["indexed"].size(), 1u) << "only supported files are indexed";
    EXPECT_NE(indexed["retrieval"].get<std::string>().find("keyword only"), std::string::npos);

    json q = reg.executeTool("query_documents", json{{"query", "who approves remote work"}});
    ASSERT_EQ(q["status"], "success");
    ASSERT_FALSE(q["results"].empty());
    EXPECT_NE(q["results"][0]["text"].get<std::string>().find("manager approval"), std::string::npos);
}

TEST(DocumentIndexTest, AFolderIndexSkipsALinkThatLeadsOut) {
    TempDir inside, outside;
    inside.write("policy.txt", "Remote work requires manager approval.");
    const std::string secret = outside.write("credentials.txt", "aws_secret_access_key = abc");
    std::error_code ec;
    std::filesystem::create_symlink(secret, inside.path / "notes.txt", ec);
    if (ec) GTEST_SKIP() << "can't create a symlink here: " << ec.message();
    DocumentIndex idx;
    ToolRegistry reg;
    idx.registerTools(reg, [&inside](const std::string& p) {
        return p.find(inside.path.string()) == 0 ? "" : std::string("Refused: outside");
    });
    json indexed = reg.executeTool("index_document", json{{"path", inside.path.string()}});
    ASSERT_EQ(indexed["status"], "success") << indexed.dump();
    ASSERT_EQ(indexed["indexed"].size(), 1u) << indexed.dump();
    EXPECT_NE(indexed["indexed"][0]["file"].get<std::string>().find("policy.txt"), std::string::npos);
}

// A folder link needs no privilege (a junction on Windows), so this one never skips.
TEST(DocumentIndexTest, AFolderIndexDoesNotWalkIntoAFolderLink) {
    TempDir inside, outside;
    inside.write("policy.txt", "Remote work requires manager approval.");
    outside.write("credentials.txt", "aws_secret_access_key = abc");
    fs::create_directories(outside.path / "deeper");
    std::ofstream(outside.path / "deeper" / "more.txt") << "token = xyz";
    const fs::path link = inside.path / "linked";
#ifdef _WIN32
    const std::string make = "cmd /c mklink /J \"" + link.string() + "\" \"" + outside.path.string() + "\" >nul 2>&1";
    ASSERT_EQ(std::system(make.c_str()), 0) << make;
#else
    std::error_code made;
    fs::create_directory_symlink(outside.path, link, made);
    ASSERT_FALSE(made) << made.message();
#endif
    // A link to a folder inside is not walked either: it could loop back on itself.
    fs::create_directories(inside.path / "sub");
    inside.write("sub/notes.txt", "Approvals are logged weekly.");
    const fs::path loop = inside.path / "sub" / "circular";
#ifdef _WIN32
    const std::string makeLoop = "cmd /c mklink /J \"" + loop.string() + "\" \"" + inside.path.string() + "\" >nul 2>&1";
    ASSERT_EQ(std::system(makeLoop.c_str()), 0) << makeLoop;
#else
    fs::create_directory_symlink(inside.path, loop, made);
    ASSERT_FALSE(made) << made.message();
#endif
    DocumentIndex idx;
    ToolRegistry reg;
    // A checker that reads only the spelling, so the walk alone must keep out of the link.
    idx.registerTools(reg, [&inside](const std::string& p) {
        return p.find(inside.path.string()) == 0 ? "" : std::string("Refused: outside");
    });
    json indexed = reg.executeTool("index_document", json{{"path", inside.path.string()}});
    ASSERT_EQ(indexed["status"], "success") << indexed.dump();
    EXPECT_EQ(indexed["indexed"].size(), 2u) << indexed.dump();
    EXPECT_EQ(indexed.dump().find("credentials.txt"), std::string::npos) << indexed.dump();
    EXPECT_EQ(indexed.dump().find("more.txt"), std::string::npos) << indexed.dump();
    EXPECT_EQ(indexed.dump().find("circular"), std::string::npos) << indexed.dump();
    // The links first, so cleaning up cannot reach through them.
    std::error_code ec;
    fs::remove(link, ec);
    fs::remove(loop, ec);
}

TEST(DocumentIndexTest, AWordFileInAFolderWithASpaceIsRead) {
    // A minimal .docx: one stored zip entry, word/document.xml, with two paragraphs.
    static const char* const kDocxHex =
        "504b03041400000000000000215cee71b67b050100000501000011000000776f72642f646f63756d656e742e786d6c3c3f78"
        "6d6c2076657273696f6e3d22312e30223f3e3c773a646f63756d656e7420786d6c6e733a773d22687474703a2f2f73636865"
        "6d61732e6f70656e786d6c666f726d6174732e6f72672f776f726470726f63657373696e676d6c2f323030362f6d61696e22"
        "3e3c773a626f64793e3c773a703e3c773a723e3c773a743e54686520616772656564206275646765742069732034382c3235"
        "30206575726f732e3c2f773a743e3c2f773a723e3c2f773a703e3c773a703e3c773a723e3c773a743e546865206f776e6572"
        "206973204d61726974204f6b73616e656e2e3c2f773a743e3c2f773a723e3c2f773a703e3c2f773a626f64793e3c2f773a64"
        "6f63756d656e743e504b010214001400000000000000215cee71b67b05010000050100001100000000000000000000008001"
        "00000000776f72642f646f63756d656e742e786d6c504b050600000000010001003f000000340100000000";
    std::string bytes;
    for (size_t i = 0; kDocxHex[i] != '\0' && kDocxHex[i + 1] != '\0'; i += 2) {
        bytes.push_back(static_cast<char>(std::stoi(std::string(kDocxHex + i, 2), nullptr, 16)));
    }
    TempDir d;
    fs::create_directories(d.path / "my docs");
    const fs::path docx = d.path / "my docs" / "halden note.docx";
    std::ofstream(docx, std::ios::binary).write(bytes.data(), static_cast<std::streamsize>(bytes.size()));

    std::string text;
    try {
        for (const auto& page : DocumentIndex::extractPages(docx.string())) text += page.second + "\n";
    } catch (const std::runtime_error& e) {
        if (std::string(e.what()).find("is not installed") != std::string::npos) {
            GTEST_SKIP() << "no zip reader on this machine: " << e.what();
        }
        FAIL() << e.what();
    }
    EXPECT_NE(text.find("The agreed budget is 48,250 euros."), std::string::npos) << text;
    EXPECT_NE(text.find("The owner is Marit Oksanen."), std::string::npos) << text;
}
