// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>

#include <gaia/document_index.h>
#include <gaia/process.h>
#include <gaia/tool_registry.h>

#include <atomic>
#include <filesystem>
#include <fstream>
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
