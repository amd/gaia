// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/documents.h>
#include <gaia/memory.h>
#include <gaia/rag.h>
#include <gaia/tool_registry.h>

#include "support/fake_embedder.h"

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <utility>
#include <vector>

using namespace gaia;
namespace fs = std::filesystem;

namespace {
const std::string kTiers = std::string(GAIA_REPO_FIXTURES_DIR) + "/gaia/tiers_documents/";
}

TEST(ChunkTextTest, ChunksOverlapAndRespectTheSize) {
    std::string text;
    for (int i = 0; i < 200; ++i) text += "Sentence number " + std::to_string(i) + " talks about water meters. ";
    const auto chunks = chunkText(text, 500, 100);
    ASSERT_GT(chunks.size(), 5u);
    for (const auto& c : chunks) EXPECT_LE(c.size(), 500u);
    // Consecutive chunks share text.
    const std::string tail = chunks[0].substr(chunks[0].size() - 40);
    EXPECT_NE(chunks[1].find(tail.substr(tail.find(' ') + 1)), std::string::npos);
    EXPECT_TRUE(chunkText("   \n ", 500, 100).empty());
}

TEST(DocumentIndexTest, IndexesAPdfAndCitesPages) {
    DocumentIndex idx(gaia_test::fakeEmbed);
    json r = idx.indexDocument(kTiers + "northwind_annual_report.pdf");
    ASSERT_EQ(r["status"], "success") << r.dump(2);
    EXPECT_EQ(r["num_pages"], 60);
    EXPECT_GE(r["num_chunks"].get<int>(), 60);  // at least one chunk per page
    EXPECT_TRUE(idx.indexDocument(kTiers + "northwind_annual_report.pdf")["already_indexed"].get<bool>());

    json q = idx.queryDocuments("treatment plant operations meter replacement");
    ASSERT_EQ(q["status"], "success") << q.dump(2);
    ASSERT_FALSE(q["chunks"].empty());
    EXPECT_TRUE(q["chunks"][0].contains("page"));
    EXPECT_NE(q["instruction"].get<std::string>().find("Cite the page"), std::string::npos);
}

TEST(DocumentIndexTest, QuerySpecificFileStaysInThatFile) {
    DocumentIndex idx(gaia_test::fakeEmbed);
    idx.indexDocument(kTiers + "harbor_lease.pdf");
    idx.indexDocument(kTiers + "supplier_audit.pdf");
    json q = idx.queryDocuments("audit score critical findings", "supplier_audit.pdf");
    ASSERT_EQ(q["status"], "success") << q.dump(2);
    for (const auto& c : q["chunks"]) {
        EXPECT_NE(c["source_file"].get<std::string>().find("supplier_audit.pdf"), std::string::npos);
    }
    EXPECT_EQ(idx.queryDocuments("x", "never_indexed.pdf")["status"], "error");
    EXPECT_EQ(idx.listIndexed()["count"], 2);
}

TEST(DocumentIndexTest, ScannedAndMissingFilesAreRefusedWithTheReason) {
    DocumentIndex idx(gaia_test::fakeEmbed);
    json scanned = idx.indexDocument(kTiers + "scanned_invoice.pdf");
    EXPECT_EQ(scanned["status"], "error");
    EXPECT_NE(scanned["error"].get<std::string>().find("scanned"), std::string::npos);
    EXPECT_EQ(idx.indexDocument(kTiers + "nope.pdf")["status"], "error");
    EXPECT_EQ(idx.queryDocuments("anything")["status"], "no_documents");
}

TEST(DocumentIndexTest, EmbedderFailureIsAnErrorNotAnEmptyResult) {
    DocumentIndex idx([](const std::vector<std::string>&) -> std::vector<std::vector<float>> {
        throw std::runtime_error("model not installed");
    });
    json r = idx.indexDocument(kTiers + "harbor_lease.pdf");
    EXPECT_EQ(r["status"], "error");
    EXPECT_NE(r["error"].get<std::string>().find("model not installed"), std::string::npos);
}

TEST(DocumentIndexTest, PathResolverConfinesIndexing) {
    DocumentIndex idx(gaia_test::fakeEmbed);
    idx.setPathResolver([](const std::string&) { return std::string(); });
    EXPECT_EQ(idx.indexDocument(kTiers + "harbor_lease.pdf")["status"], "error");
}

TEST(HybridMemoryTest, MeaningAndKeywordsAreFused) {
    MemoryStore m(":memory:");
    m.setEmbedder(gaia_test::fakeEmbed);
    m.remember({{"fact", "Sister Maya lives in Lisbon and visits every spring"}});
    m.remember({{"fact", "Prefers aisle seats on long flights"}, {"category", "preference"}});
    json r = m.recall({{"query", "where does maya live"}});
    ASSERT_EQ(r["status"], "found") << r.dump(2);
    EXPECT_EQ(r["ranking"], "hybrid (meaning + keywords)");
    EXPECT_NE(r["results"][0]["content"].get<std::string>().find("Lisbon"), std::string::npos);
    const auto related = m.relevantTo("planning to visit Maya in Lisbon", 3, 0.2);
    ASSERT_FALSE(related.empty());
    EXPECT_NE(related[0].find("Lisbon"), std::string::npos);
}

TEST(HybridMemoryTest, EmbedderDownIsReportedAndKeywordsStillWork) {
    MemoryStore m(":memory:");
    m.setEmbedder([](const std::vector<std::string>&) -> std::vector<std::vector<float>> {
        throw std::runtime_error("embedder offline");
    });
    m.remember({{"fact", "Car is a blue Subaru"}});
    json r = m.recall({{"query", "subaru"}});
    ASSERT_EQ(r["count"], 1);
    EXPECT_NE(r["ranking"].get<std::string>().find("embedder offline"), std::string::npos);
    EXPECT_TRUE(m.relevantTo("my car").empty());
}

TEST(DocumentIndexTest, RegistersThePythonRagToolNames) {
    DocumentIndex idx(gaia_test::fakeEmbed);
    ToolRegistry reg;
    idx.registerTools(reg);
    for (const char* t : {"index_document", "query_documents", "query_specific_file", "list_indexed_documents", "rag_status"}) {
        EXPECT_TRUE(reg.hasTool(t)) << t;
    }
}

TEST(DocumentIndexTest, DirectorySearchAndSummary) {
    DocumentIndex idx(gaia_test::fakeEmbed);
    json dir = idx.indexDirectory(kTiers, false);
    ASSERT_EQ(dir["status"], "success") << dir.dump(2);
    EXPECT_GE(dir["indexed_files"].size(), 4u);
    EXPECT_FALSE(dir["failed"].empty());  // the scanned invoice and the corrupt PDF say why
    // Exact text, case-insensitive; regex syntax is just text.
    json hits = idx.searchChunks("bluefin bakery");
    ASSERT_EQ(hits["status"], "success");
    EXPECT_GT(hits["total_matches"].get<int>(), 0);
    EXPECT_EQ(hits["matches"][0]["match"], "Bluefin Bakery");
    EXPECT_EQ(idx.searchChunks("(a|a)*b([")["total_matches"], 0);
    EXPECT_EQ(idx.searchChunks("")["status"], "error");

    EXPECT_EQ(idx.summarizeDocument(kTiers + "harbor_lease.pdf", "brief", 0)["status"], "error");  // no summarizer
    std::vector<std::string> calls;
    idx.setSummarizer([&calls](const std::string& instruction, const std::string& text) {
        calls.push_back(instruction);
        return "summary of " + std::to_string(text.size()) + " chars";
    });
    json one = idx.summarizeDocument(kTiers + "harbor_lease.pdf", "brief", 0);
    ASSERT_EQ(one["status"], "success") << one.dump(2);
    EXPECT_EQ(calls.size(), 1u);
    calls.clear();
    json many = idx.summarizeDocument(kTiers + "northwind_annual_report.pdf", "detailed", 3000);
    ASSERT_EQ(many["status"], "success");
    EXPECT_GT(many["sections"].get<int>(), 1);
    EXPECT_EQ(calls.size(), many["sections"].get<std::size_t>() + 1);  // each part, then the combine
}

TEST(CodeIndexTest, IndexesSourcesRespectingGitignoreAndSearches) {
    const fs::path repo = fs::temp_directory_path() / "gaia_code_index_test";
    std::error_code ec;
    fs::remove_all(repo, ec);
    fs::create_directories(repo / "src");
    fs::create_directories(repo / "generated");
    std::ofstream(repo / ".gitignore") << "generated/\n";
    std::ofstream(repo / "src" / "retry.py") << "def retry_with_backoff(fn, attempts=3):\n    for i in range(attempts):\n        pass\n";
    std::ofstream(repo / "src" / "util.py") << "def format_bytes(n):\n    return str(n)\n";
    std::ofstream(repo / "generated" / "retry_copy.py") << "def retry_with_backoff(): pass\n";
    CodeIndex code(gaia_test::fakeEmbed, repo.u8string());
    json s = code.search("retry with backoff attempts", "all", 5);  // builds on first use
    ASSERT_EQ(s["status"], "success") << s.dump(2);
    ASSERT_FALSE(s["results"].empty());
    EXPECT_EQ(s["results"][0]["file"], "src/retry.py");
    EXPECT_EQ(s["results"][0]["lines"], "1-3");
    for (const auto& r : s["results"]) EXPECT_EQ(r["file"].get<std::string>().find("generated"), std::string::npos);
    EXPECT_EQ(code.status()["files"], 2);
    EXPECT_EQ(code.search("format bytes", "util", 5)["results"][0]["file"], "src/util.py");
    code.clear();
    EXPECT_FALSE(code.status()["indexed"].get<bool>());
    fs::remove_all(repo, ec);
}

namespace {

class RagTempDir : public ::testing::Test {
protected:
    fs::path dir_;
    void SetUp() override {
        dir_ = fs::temp_directory_path() / "gaia_rag_hostile_test";
        std::error_code ec;
        fs::remove_all(dir_, ec);
        fs::create_directories(dir_);
    }
    void TearDown() override {
        std::error_code ec;
        fs::remove_all(dir_, ec);
    }
    std::string write(const std::string& name, const std::string& bytes) {
        std::ofstream(dir_ / name, std::ios::binary) << bytes;
        return (dir_ / name).u8string();
    }
    static std::string longText(const std::string& word, int sentences) {
        std::string t;
        for (int i = 0; i < sentences; ++i) t += "The " + word + " report line " + std::to_string(i) + " covers water meters. ";
        return t;
    }
};

}  // namespace

TEST(ChunkTextTest, InvalidUtf8StillMovesForward) {
    const std::string text = "x" + std::string(3000, '\x80');
    const auto chunks = chunkText(text, 2000, 400);
    ASSERT_FALSE(chunks.empty());
    std::size_t total = 0;
    for (const auto& c : chunks) total += c.size();
    EXPECT_GE(total, text.size());
}

TEST_F(RagTempDir, FailedAddLeavesTheIndexUsable) {
    bool poison = false;
    DocumentIndex idx([&poison](const std::vector<std::string>& texts) {
        auto v = gaia_test::fakeEmbed(texts);
        // An all-zero vector cannot be normalized, so the index refuses it mid-file.
        if (poison && v.size() > 2) std::fill(v[2].begin(), v[2].end(), 0.0f);
        return v;
    });
    ASSERT_EQ(idx.indexDocument(write("a.txt", longText("alpha", 10)))["status"], "success");
    const std::string b = write("b.txt", longText("bravo", 200));
    poison = true;
    const json failed = idx.indexDocument(b);
    EXPECT_EQ(failed["status"], "error") << failed.dump(2);
    poison = false;
    const json retried = idx.indexDocument(b);
    ASSERT_EQ(retried["status"], "success") << retried.dump(2);
    const json q = idx.queryDocuments("bravo report water meters");
    ASSERT_EQ(q["status"], "success") << q.dump(2);
    for (const auto& c : q["chunks"]) EXPECT_FALSE(c["source_file"].get<std::string>().empty());
    EXPECT_EQ(idx.status()["total_chunks"], 1u + retried["num_chunks"].get<std::size_t>());
}

TEST_F(RagTempDir, EmbedderReturningTooFewVectorsIsAnError) {
    DocumentIndex idx([](const std::vector<std::string>& texts) {
        auto v = gaia_test::fakeEmbed(texts);
        v.pop_back();
        return v;
    });
    const json r = idx.indexDocument(write("short.txt", longText("charlie", 5)));
    EXPECT_EQ(r["status"], "error");
    EXPECT_NE(r["error"].get<std::string>().find("vectors for"), std::string::npos) << r.dump(2);
}

TEST_F(RagTempDir, FailedCodeReindexKeepsThePreviousIndexWhole) {
    fs::create_directories(dir_ / "src");
    std::ofstream(dir_ / "src" / "retry.py") << "def retry_with_backoff(fn, attempts=3):\n    pass\n";
    bool poison = false;
    CodeIndex code(
        [&poison](const std::vector<std::string>& texts) {
            auto v = gaia_test::fakeEmbed(texts);
            if (poison) std::fill(v.back().begin(), v.back().end(), 0.0f);
            return v;
        },
        dir_.u8string());
    ASSERT_EQ(code.indexCodebase("")["status"], "success");
    for (int i = 0; i < 5; ++i) std::ofstream(dir_ / "src" / ("m" + std::to_string(i) + ".py")) << "def f" << i << "(): pass\n";
    poison = true;
    EXPECT_EQ(code.indexCodebase("")["status"], "error");
    poison = false;
    EXPECT_EQ(code.status()["files"], 1);
    const json s = code.search("retry with backoff", "all", 5);
    ASSERT_EQ(s["status"], "success") << s.dump(2);
    EXPECT_EQ(s["results"][0]["file"], "src/retry.py");
}

TEST_F(RagTempDir, ScannedPageTextGoesUnderItsRealMarker) {
    // Three pages; the third is a 2x2 gray image, and page 1's text quotes page 3's marker.
    auto stream = [](const std::string& dict, const std::string& data) {
        return "<< " + dict + " /Length " + std::to_string(data.size()) + " >>\nstream\n" + data + "\nendstream";
    };
    std::string pdf = "%PDF-1.4\n";
    for (const auto& [n, body] : std::vector<std::pair<int, std::string>>{
             {1, "<< /Type /Catalog /Pages 2 0 R >>"},
             {2, "<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] >>"},
             {3, "<< /Type /Page /Parent 2 0 R >>"},
             {4, "<< /Type /Page /Parent 2 0 R >>"},
             {5, "<< /Type /Page /Parent 2 0 R /Resources << /XObject << /Im 6 0 R >> >> >>"},
             {6, stream("/Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceGray /BitsPerComponent 8",
                        std::string("\x00\xFF\xFF\x00", 4))}}) {
        pdf += std::to_string(n) + " 0 obj\n" + body + "\nendobj\n";
    }
    pdf += "trailer\n<< /Root 1 0 R >>\n";
    const std::string path = write("scan.pdf", pdf);
    DocumentText doc;
    doc.kind = "pdf";
    doc.sections = 3;
    doc.text = "--- Page 1 ---\nsee --- Page 3 ---\nbelow\n\n--- Page 2 ---\ntwo\n\n--- Page 3 ---\n\n\n";
    doc.emptySections = {3};
    fillScannedPages(path, doc, [](const std::string&, const std::vector<std::string>&) { return std::string("TRANSCRIBED"); });
    EXPECT_TRUE(doc.emptySections.empty());
    const auto real = doc.text.rfind("--- Page 3 ---\n");
    EXPECT_EQ(doc.text.find("TRANSCRIBED"), real + std::string("--- Page 3 ---\n").size()) << doc.text;
}

TEST_F(RagTempDir, TinySummarySectionsAreRefused) {
    DocumentIndex idx(gaia_test::fakeEmbed);
    std::size_t calls = 0;
    idx.setSummarizer([&calls](const std::string&, const std::string&) {
        ++calls;
        return std::string("ok");
    });
    const json r = idx.summarizeDocument(write("words.txt", longText("delta", 300)), "brief", 1);
    EXPECT_EQ(r["status"], "error");
    EXPECT_NE(r["error"].get<std::string>().find("at least"), std::string::npos) << r.dump(2);
    EXPECT_EQ(calls, 0u);
}

TEST_F(RagTempDir, DirectoryPastTheFileCapSaysSo) {
    for (int i = 0; i < 501; ++i) write("note" + std::to_string(i) + ".txt", "Note number " + std::to_string(i) + " about meters.");
    DocumentIndex idx(gaia_test::fakeEmbed);
    const json r = idx.indexDirectory(dir_.u8string(), false);
    ASSERT_EQ(r["status"], "success") << r.dump(2).substr(0, 500);
    EXPECT_EQ(r["indexed_files"].size(), 500u);
    ASSERT_TRUE(r.contains("warnings"));
    EXPECT_NE(r["warnings"].dump().find("more than 500"), std::string::npos);
}
