// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>

#include <gaia/chunking.h>
#include <gaia/document_index.h>
#include <gaia/process.h>
#include <gaia/tool_registry.h>

#include <algorithm>
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
#ifdef __APPLE__
    return true;  // a Mac reads PDFs with or without it
#else
    return ProcessRunner::run("pdftotext -v", 5000).exitCode == 0;
#endif
}

#ifndef _WIN32
// Runs with a PATH that holds no pdftotext, as on a PC where poppler was never installed.
struct NoPdftotextOnPath {
    std::string saved = std::getenv("PATH") ? std::getenv("PATH") : "";
    NoPdftotextOnPath() { setenv("PATH", "/nonexistent-gaia-test", 1); }
    ~NoPdftotextOnPath() { setenv("PATH", saved.c_str(), 1); }
};
#endif

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

// S-DOC-12: the 2023 row of a long bond-yield table, asked for by the column
// names typed in ASCII, while the page spells them with an en dash and a curly quote.
TEST(DocumentIndexTest, ATableRowIsFoundByItsColumnNames) {
    TempDir d;
    std::string table = "Table B–42. Bond yields and interest rates, 1953–2023\n"
                        "Corporate bonds (Moody’s)\nYear 3-month 10-year Aaa Baa Prime\n";
    for (int year = 1953; year <= 2023; ++year) {
        table += std::to_string(year) + (year == 2023 ? " 5.08 3.96 4.81 5.86 8.20 5.03 4.31 6.81\n"
                                                      : " 1.93 2.85 3.20 3.74 2.72 3.17 1.99 2.47\n");
    }
    d.write("erp.txt", table);
    for (int i = 0; i < 6; ++i) {
        d.write("prose" + std::to_string(i) + ".txt",
                "In 2023 corporate bond yields rose. Moody's spreads between Baa and Aaa issuers are "
                "discussed in chapter " + std::to_string(i) + " of the report.");
    }
    DocumentIndex idx;
    idx.indexDirectory(d.path.string());
    const auto hits = idx.query("Table B-42 Moody's Baa Aaa corporate bond yields 2023", 5);
    const auto row = std::find_if(hits.begin(), hits.end(), [](const DocumentHit& h) {
        return h.chunk.text.find("2023 5.08 3.96 4.81 5.86") != std::string::npos;
    });
    ASSERT_NE(row, hits.end()) << "the 2023 row is not in the top 5";
    EXPECT_EQ(row->chunk.text.rfind("Table B–42.", 0), 0u) << "the row's chunk carries its column names";
    EXPECT_LE(row->chunk.text.size(), 3000u);
}

TEST(DocumentIndexTest, APdfsUnreadableGlyphRunsBecomeOneSpace) {
    EXPECT_EQ(DocumentIndex::tidyPdfText("2023 \b��� 5.08\b�� 5.08\nnext\f"),
              "2023 5.08 5.08\nnext\f");
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

#ifndef _WIN32
TEST(DocumentIndexTest, APdfWithoutPdftotextIsReadOnAMacAndExplainedElsewhere) {
    ASSERT_TRUE(fs::exists(kNorthwind)) << kNorthwind;
    NoPdftotextOnPath path;
#ifdef __APPLE__
    const auto pages = DocumentIndex::extractPages(kNorthwind.string());
    ASSERT_GE(pages.size(), 7u);
    const auto seven = std::find_if(pages.begin(), pages.end(), [](const auto& p) { return p.first == 7; });
    ASSERT_NE(seven, pages.end());
    EXPECT_NE(seven->second.find("18.4"), std::string::npos) << seven->second.substr(0, 400);
    TempDir d;
    try {
        DocumentIndex::extractPages(d.write("broken.pdf", "not a pdf"));
        FAIL() << "a broken PDF was read";
    } catch (const MissingDocumentReader&) {
        FAIL() << "a Mac needs no extra program";
    } catch (const std::runtime_error& e) {
        EXPECT_NE(std::string(e.what()).find("Can't read PDF"), std::string::npos) << e.what();
    }
#else
    try {
        DocumentIndex::extractPages(kNorthwind.string());
        FAIL() << "read a PDF with no reader";
    } catch (const MissingDocumentReader& e) {
        const std::string why = e.what();
        EXPECT_EQ(e.program, "pdftotext");
        for (const char* part : {"pdftotext", "not installed", "poppler", "Full-featured", "Agent & model"}) {
            EXPECT_NE(why.find(part), std::string::npos) << part << " in: " << why;
        }
    }
    // The tool's own error is the reason, not "see failed".
    DocumentIndex idx;
    ToolRegistry registry;
    idx.registerTools(registry);
    const json r = registry.executeTool("index_document", {{"path", kNorthwind.string()}});
    EXPECT_EQ(r.value("status", ""), "error");
    EXPECT_NE(r.value("error", "").find("Full-featured"), std::string::npos) << r.dump();
    EXPECT_EQ(r["failed"][0].value("missing_program", ""), "pdftotext");
#endif
}
#endif

TEST(DocumentIndexTest, AMissingPdfReaderSaysWhatToInstallOrUseInstead) {
    const MissingDocumentReader missing("C:/docs/report.pdf", "pdftotext");
    const std::string user = MissingDocumentReader::remedy("report.pdf", "pdftotext");
    for (const char* part : {"report.pdf", "pdftotext", "not installed", "poppler", "Full-featured", "Agent & model",
                             "--agent-program gaiapy"}) {
        EXPECT_NE(user.find(part), std::string::npos) << part << " in: " << user;
    }
    EXPECT_EQ(user.find("Tell the user"), std::string::npos);
    EXPECT_NE(std::string(missing.what()).find("Tell the user"), std::string::npos) << "the model must not improvise";
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

namespace {

// A minimal .docx: one stored zip entry, word/document.xml, with two paragraphs.
std::string minimalDocx() {
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
    return bytes;
}

void expectDocxIsRead(const fs::path& docx) {
    const std::string bytes = minimalDocx();
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

std::uint32_t zipCrc32(const std::string& data) {
    std::uint32_t crc = 0xFFFFFFFFu;
    for (unsigned char b : data) {
        crc ^= b;
        for (int i = 0; i < 8; ++i) crc = (crc & 1u) ? (crc >> 1) ^ 0xEDB88320u : (crc >> 1);
    }
    return ~crc;
}

std::string buildStoredZip(const std::vector<std::pair<std::string, std::string>>& entries) {
    auto put16 = [](std::string& out, std::uint16_t v) {
        out.push_back(static_cast<char>(v & 0xFFu));
        out.push_back(static_cast<char>((v >> 8) & 0xFFu));
    };
    auto put32 = [](std::string& out, std::uint32_t v) {
        for (int i = 0; i < 4; ++i) out.push_back(static_cast<char>((v >> (i * 8)) & 0xFFu));
    };

    std::string body;
    std::string central;
    for (const auto& [name, data] : entries) {
        const std::uint32_t offset = static_cast<std::uint32_t>(body.size());
        const std::uint32_t crc = zipCrc32(data);
        const std::uint32_t size = static_cast<std::uint32_t>(data.size());
        const std::uint16_t nameLen = static_cast<std::uint16_t>(name.size());

        put32(body, 0x04034B50u);
        put16(body, 20);
        put16(body, 0);
        put16(body, 0);
        put16(body, 0);
        put16(body, 0x5C21);
        put32(body, crc);
        put32(body, size);
        put32(body, size);
        put16(body, nameLen);
        put16(body, 0);
        body += name;
        body += data;

        put32(central, 0x02014B50u);
        put16(central, 20);
        put16(central, 20);
        put16(central, 0);
        put16(central, 0);
        put16(central, 0);
        put16(central, 0x5C21);
        put32(central, crc);
        put32(central, size);
        put32(central, size);
        put16(central, nameLen);
        put16(central, 0);
        put16(central, 0);
        put16(central, 0);
        put16(central, 0);
        put32(central, 0);
        put32(central, offset);
        central += name;
    }

    const std::uint32_t cdOffset = static_cast<std::uint32_t>(body.size());
    const std::uint32_t cdSize = static_cast<std::uint32_t>(central.size());
    const std::uint16_t count = static_cast<std::uint16_t>(entries.size());

    std::string eocd;
    put32(eocd, 0x06054B50u);
    put16(eocd, 0);
    put16(eocd, 0);
    put16(eocd, count);
    put16(eocd, count);
    put32(eocd, cdSize);
    put32(eocd, cdOffset);
    put16(eocd, 0);

    return body + central + eocd;
}

std::string writeBinary(const fs::path& file, const std::string& bytes) {
    std::ofstream(file, std::ios::binary).write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    return file.string();
}

}  // namespace

TEST(DocumentIndexTest, AWordFileInAFolderWithASpaceIsRead) {
    TempDir d;
    fs::create_directories(d.path / "my docs");
    expectDocxIsRead(d.path / "my docs" / "halden note.docx");
}

TEST(DocumentIndexTest, AWordFileWithPercentSignsInItsNameIsRead) {
    // cmd.exe would expand %PATH% in the name if it reached the command line.
    TempDir d;
    expectDocxIsRead(d.path / "budget %PATH% 100%.docx");
}

TEST(DocumentIndexTest, XlsxExtractsMultipleSheetsSharedAndInlineStringsNumbersAndEntities) {
    TempDir d;
    const std::string workbook =
        R"(<?xml version="1.0" encoding="UTF-8"?>)"
        R"(<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" )"
        R"(xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">)"
        R"(<sheets>)"
        R"(<sheet name="Budget &amp; Plan" sheetId="1" r:id="rId1"/>)"
        R"(<sheet name="Q4 Breakdown" sheetId="2" r:id="rId2"/>)"
        R"(</sheets></workbook>)";
    const std::string rels =
        R"(<?xml version="1.0" encoding="UTF-8"?>)"
        R"(<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">)"
        R"(<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>)"
        R"(<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="/xl/worksheets/sheet2.xml"/>)"
        R"(</Relationships>)";
    const std::string sharedStrings =
        R"(<?xml version="1.0" encoding="UTF-8"?>)"
        R"(<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">)"
        R"(<si><t>Department</t></si>)"
        R"(<si><t>Allocated</t></si>)"
        R"(<si><t>Approved</t></si>)"
        R"(<si><t>Notes</t></si>)"
        R"(<si><r><t>R&amp;D </t></r><r><t>Core&#x2019;s Team</t></r><rPh sb="0" eb="1"><t>IGNORE_PHONETIC</t></rPh></si>)"
        R"(<si><t>Metric</t></si>)"
        R"(<si><t>Value</t></si>)"
        R"(<si><t>Target Margin</t></si>)"
        R"(</sst>)";
    const std::string sheet1 =
        R"(<?xml version="1.0" encoding="UTF-8"?>)"
        R"(<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>)"
        R"(<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c></row>)"
        R"(<row r="2"><c r="A2" t="s"><v>4</v></c><c r="B2"><f>100000+48250</f><v>148250</v></c><c r="C2" t="b"><v>1</v></c><c r="D2" t="inlineStr"><is><t>Cap &lt; 150k</t></is></c></row>)"
        R"(<row r="3"><c r="A3" t="str"><v>Marketing</v></c><c r="B3"><v>62400</v></c><c r="C3" t="b"><v>0</v></c></row>)"
        R"(</sheetData></worksheet>)";
    const std::string sheet2 =
        R"(<?xml version="1.0" encoding="UTF-8"?>)"
        R"(<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>)"
        R"(<row r="1"><c r="A1" t="s"><v>5</v></c><c r="B1" t="s"><v>6</v></c></row>)"
        R"(<row r="2"><c r="A2" t="s"><v>7</v></c><c r="B2"><v>38.5</v></c></row>)"
        R"(</sheetData></worksheet>)";

    const std::string xlsx = writeBinary(
        d.path / "budget_sheet.xlsx",
        buildStoredZip({{"xl/workbook.xml", workbook},
                        {"xl/_rels/workbook.xml.rels", rels},
                        {"xl/sharedStrings.xml", sharedStrings},
                        {"xl/worksheets/sheet1.xml", sheet1},
                        {"xl/worksheets/sheet2.xml", sheet2}}));

    std::vector<std::pair<int, std::string>> pages;
    try {
        pages = DocumentIndex::extractPages(xlsx);
    } catch (const std::runtime_error& e) {
        if (std::string(e.what()).find("is not installed") != std::string::npos) {
            GTEST_SKIP() << "no zip reader on this machine: " << e.what();
        }
        FAIL() << e.what();
    }
    ASSERT_EQ(pages.size(), 2u);
    EXPECT_EQ(pages[0].first, 0);
    EXPECT_NE(pages[0].second.find("Sheet: Budget & Plan"), std::string::npos) << pages[0].second;
    EXPECT_NE(pages[0].second.find("R&D Core\xE2\x80\x99s Team"), std::string::npos) << pages[0].second;
    EXPECT_EQ(pages[0].second.find("IGNORE_PHONETIC"), std::string::npos) << pages[0].second;
    EXPECT_NE(pages[0].second.find("148250"), std::string::npos) << pages[0].second;
    EXPECT_NE(pages[0].second.find("TRUE"), std::string::npos) << pages[0].second;
    EXPECT_NE(pages[0].second.find("FALSE"), std::string::npos) << pages[0].second;
    EXPECT_NE(pages[0].second.find("Cap < 150k"), std::string::npos) << pages[0].second;
    EXPECT_NE(pages[0].second.find("Marketing"), std::string::npos) << pages[0].second;
    EXPECT_NE(pages[1].second.find("Sheet: Q4 Breakdown"), std::string::npos) << pages[1].second;
    EXPECT_NE(pages[1].second.find("Target Margin"), std::string::npos) << pages[1].second;
    EXPECT_NE(pages[1].second.find("38.5"), std::string::npos) << pages[1].second;

    DocumentIndex idx;
    EXPECT_GE(idx.indexFile(xlsx), 2u);
    auto hits = idx.query("Target Margin Q4 Breakdown", 3);
    ASSERT_FALSE(hits.empty());
    EXPECT_NE(hits[0].chunk.text.find("38.5"), std::string::npos) << hits[0].chunk.text;
}

TEST(DocumentIndexTest, XlsxFallsBackToNumericWorksheetOrderWithoutWorkbookXml) {
    TempDir d;
    const std::string s10 =
        R"(<?xml version="1.0"?><worksheet><sheetData><row><c t="inlineStr"><is><t>TenthSheetMarker</t></is></c></row></sheetData></worksheet>)";
    const std::string s2 =
        R"(<?xml version="1.0"?><worksheet><sheetData><row><c t="inlineStr"><is><t>SecondSheetMarker</t></is></c></row></sheetData></worksheet>)";
    const std::string xlsx = writeBinary(
        d.path / "minimal.xlsx",
        buildStoredZip({{"xl/worksheets/sheet10.xml", s10}, {"xl/worksheets/sheet2.xml", s2}}));

    const auto pages = DocumentIndex::extractPages(xlsx);
    ASSERT_EQ(pages.size(), 2u);
    EXPECT_NE(pages[0].second.find("SecondSheetMarker"), std::string::npos) << pages[0].second;
    EXPECT_NE(pages[1].second.find("TenthSheetMarker"), std::string::npos) << pages[1].second;
}

TEST(DocumentIndexTest, PptxExtractsSlidesInNumericOrderWithNotesAndTables) {
    TempDir d;
    std::vector<std::pair<std::string, std::string>> entries;
    // Insert slide10 before slide2 to verify numeric slide sorting, not lexicographical.
    for (int n : {10, 9, 8, 7, 6, 5, 4, 3, 2, 1}) {
        std::string xml =
            R"(<?xml version="1.0" encoding="UTF-8"?>)"
            R"(<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" )"
            R"(xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree>)"
            R"(<p:sp><p:txBody><a:p><a:r><a:t>Milestone &amp; Goal #)" +
            std::to_string(n) + R"(</a:t></a:r></a:p></p:txBody></p:sp>)";
        if (n == 3) {
            xml += R"(<p:graphicFrame><a:graphic><a:graphicData><a:tbl>)"
                   R"(<a:tr><a:tc><a:txBody><a:p><a:r><a:t>Region</a:t></a:r></a:p></a:txBody></a:tc>)"
                   R"(<a:tc><a:txBody><a:p><a:r><a:t>ARR</a:t></a:r></a:p></a:txBody></a:tc></a:tr>)"
                   R"(<a:tr><a:tc><a:txBody><a:p><a:r><a:t>EMEA</a:t></a:r></a:p></a:txBody></a:tc>)"
                   R"(<a:tc><a:txBody><a:p><a:r><a:t>$4.2M</a:t></a:r></a:p></a:txBody></a:tc></a:tr>)"
                   R"(</a:tbl></a:graphicData></a:graphic></p:graphicFrame>)";
        }
        xml += R"(</p:spTree></p:cSld></p:sld>)";
        entries.emplace_back("ppt/slides/slide" + std::to_string(n) + ".xml", xml);
    }
    entries.emplace_back(
        "ppt/slides/_rels/slide2.xml.rels",
        R"(<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">)"
        R"(<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide2.xml"/>)"
        R"(</Relationships>)");
    entries.emplace_back(
        "ppt/notesSlides/notesSlide2.xml",
        R"(<?xml version="1.0"?><p:notes xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">)"
        R"(<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Remind investors about Q3 runway.</a:t></a:r></a:p></p:txBody></p:sp>)"
        R"(<p:sp><p:txBody><a:p><a:fld type="slidenum"><a:t>2</a:t></a:fld></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>)");
    // Slide 4 has a notes slide containing only the slide number placeholder — must not add empty notes.
    entries.emplace_back(
        "ppt/notesSlides/notesSlide4.xml",
        R"(<?xml version="1.0"?><p:notes xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">)"
        R"(<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:fld type="slidenum"><a:t>4</a:t></a:fld></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>)");

    const std::string pptx = writeBinary(d.path / "pitch_deck.pptx", buildStoredZip(entries));
    const auto pages = DocumentIndex::extractPages(pptx);
    ASSERT_EQ(pages.size(), 10u);
    for (int i = 0; i < 10; ++i) {
        EXPECT_EQ(pages[i].first, i + 1);
        EXPECT_EQ(pages[i].second.rfind("Slide " + std::to_string(i + 1) + "\n", 0), 0u) << pages[i].second;
        EXPECT_NE(pages[i].second.find("Milestone & Goal #" + std::to_string(i + 1)), std::string::npos)
            << pages[i].second;
    }
    EXPECT_NE(pages[1].second.find("Speaker Notes:"), std::string::npos) << pages[1].second;
    EXPECT_NE(pages[1].second.find("Remind investors about Q3 runway."), std::string::npos) << pages[1].second;
    EXPECT_NE(pages[2].second.find("EMEA | $4.2M"), std::string::npos) << pages[2].second;
    EXPECT_EQ(pages[3].second.find("Speaker Notes:"), std::string::npos) << pages[3].second;

    DocumentIndex idx;
    EXPECT_GE(idx.indexFile(pptx), 10u);
    auto hits = idx.query("Q3 runway investors", 3);
    ASSERT_FALSE(hits.empty());
    EXPECT_EQ(hits[0].chunk.page, 2);
    EXPECT_NE(hits[0].chunk.text.find("Slide 2"), std::string::npos);
}

TEST(DocumentIndexTest, PptxRespectsPresentationSlideOrder) {
    TempDir d;
    const std::string pres =
        R"(<?xml version="1.0"?><p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">)"
        R"(<p:sldIdLst><p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId1"/></p:sldIdLst></p:presentation>)";
    const std::string rels =
        R"(<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">)"
        R"(<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>)"
        R"(<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide2.xml"/>)"
        R"(</Relationships>)";
    const std::string slide1 =
        R"(<?xml version="1.0"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Second In Deck</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>)";
    const std::string slide2 =
        R"(<?xml version="1.0"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>First In Deck</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>)";
    const std::string pptx = writeBinary(
        d.path / "reordered.pptx",
        buildStoredZip({{"ppt/presentation.xml", pres},
                        {"ppt/_rels/presentation.xml.rels", rels},
                        {"ppt/slides/slide1.xml", slide1},
                        {"ppt/slides/slide2.xml", slide2}}));
    const auto pages = DocumentIndex::extractPages(pptx);
    ASSERT_EQ(pages.size(), 2u);
    EXPECT_EQ(pages[0].first, 1);
    EXPECT_NE(pages[0].second.find("First In Deck"), std::string::npos);
    EXPECT_EQ(pages[1].first, 2);
    EXPECT_NE(pages[1].second.find("Second In Deck"), std::string::npos);
}

TEST(DocumentIndexTest, PptxDoesNotAttachNotesSlide1ToSlide1WhenRelsLinkItToSlide2) {
    TempDir d;
    // python-pptx numbers notes slides sequentially starting at notesSlide1.xml even when only slide 2 has notes.
    const std::string slide1 =
        R"(<?xml version="1.0"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Overview Slide</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>)";
    const std::string slide2 =
        R"(<?xml version="1.0"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Traction Slide</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>)";
    const std::string slide1Rels =
        R"(<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>)";
    const std::string slide2Rels =
        R"(<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide1.xml"/></Relationships>)";
    const std::string notes1 =
        R"(<?xml version="1.0"?><p:notes xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Slide 2 private runway note.</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>)";
    const std::string pptx = writeBinary(
        d.path / "sparse_notes.pptx",
        buildStoredZip({{"ppt/slides/slide1.xml", slide1},
                        {"ppt/slides/slide2.xml", slide2},
                        {"ppt/slides/_rels/slide1.xml.rels", slide1Rels},
                        {"ppt/slides/_rels/slide2.xml.rels", slide2Rels},
                        {"ppt/notesSlides/notesSlide1.xml", notes1}}));

    const auto pages = DocumentIndex::extractPages(pptx);
    ASSERT_EQ(pages.size(), 2u);
    EXPECT_EQ(pages[0].second.find("runway"), std::string::npos) << pages[0].second;
    EXPECT_NE(pages[1].second.find("Slide 2 private runway note."), std::string::npos) << pages[1].second;
}

TEST(DocumentIndexTest, CorruptAndPasswordProtectedXlsxAndPptxFailLoudly) {
    TempDir d;
    const std::string corruptXlsx = writeBinary(d.path / "corrupt.xlsx", std::string("PK\x03\x04\x00\x00truncated", 15));
    const std::string corruptPptx = writeBinary(d.path / "corrupt.pptx", std::string("PK\x03\x04\x00\x00truncated", 15));
    const std::string ole2Header("\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" "encrypted", 17);
    const std::string lockedXlsx = writeBinary(d.path / "locked.xlsx", ole2Header);
    const std::string lockedPptx = writeBinary(d.path / "locked.pptx", ole2Header);
    const std::string emptyXlsx = writeBinary(
        d.path / "empty.xlsx",
        buildStoredZip({{"xl/worksheets/sheet1.xml",
                         R"(<?xml version="1.0"?><worksheet><sheetData/></worksheet>)"}}));
    const std::string emptyPptx = writeBinary(
        d.path / "empty.pptx",
        buildStoredZip({{"ppt/slides/slide1.xml",
                         R"(<?xml version="1.0"?><p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree/></p:cSld></p:sld>)"}}));

    EXPECT_THROW(DocumentIndex::extractPages(corruptXlsx), std::runtime_error);
    EXPECT_THROW(DocumentIndex::extractPages(corruptPptx), std::runtime_error);
    EXPECT_THROW(DocumentIndex::extractPages(emptyXlsx), std::runtime_error);
    EXPECT_THROW(DocumentIndex::extractPages(emptyPptx), std::runtime_error);

    for (const std::string& locked : {lockedXlsx, lockedPptx}) {
        try {
            DocumentIndex::extractPages(locked);
            FAIL() << "locked archive should have failed: " << locked;
        } catch (const std::runtime_error& e) {
            EXPECT_NE(std::string(e.what()).find("password-protected"), std::string::npos) << e.what();
        }
    }
}

TEST(DocumentIndexTest, FolderIndexIncludesXlsxAndPptx) {
    TempDir d;
    writeBinary(
        d.path / "numbers.xlsx",
        buildStoredZip({{"xl/worksheets/sheet1.xml",
                         R"(<?xml version="1.0"?><worksheet><sheetData><row><c t="inlineStr"><is><t>Revenue</t></is></c><c><v>91000</v></c></row></sheetData></worksheet>)"}}));
    writeBinary(
        d.path / "slides.pptx",
        buildStoredZip({{"ppt/slides/slide1.xml",
                         R"(<?xml version="1.0"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Roadmap Launch Q2</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>)"}}));
    DocumentIndex idx;
    ToolRegistry reg;
    idx.registerTools(reg, nullptr);
    const json res = reg.executeTool("index_document", json{{"path", d.path.string()}});
    ASSERT_EQ(res["status"], "success") << res.dump();
    EXPECT_EQ(res["indexed"].size(), 2u) << res.dump();
}

TEST(DocumentIndexTest, UnsupportedOfficeFormatsReportPlainEnglishErrors) {
    TempDir d;
    const std::string xls = d.write("legacy_budget.xls", std::string("\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1", 8));
    const std::string ppt = d.write("legacy_deck.ppt", std::string("\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1", 8));
    const std::string doc = d.write("legacy_note.doc", std::string("\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1", 8));
    const std::string bin = d.write("blob.bin", std::string("\x00\x01\x02\x03", 4));

    for (const auto& [file, expectedFormat] :
         std::vector<std::pair<std::string, std::string>>{{xls, "Excel"}, {ppt, "PowerPoint"}, {doc, "legacy Word"}, {bin, "binary"}}) {
        try {
            DocumentIndex::extractPages(file);
            FAIL() << "unsupported file should have failed: " << file;
        } catch (const std::runtime_error& e) {
            const std::string msg = e.what();
            EXPECT_NE(msg.find(expectedFormat), std::string::npos) << msg;
            EXPECT_NE(msg.find("Excel (.xlsx)"), std::string::npos) << msg;
            EXPECT_NE(msg.find("PowerPoint (.pptx)"), std::string::npos) << msg;
            EXPECT_EQ(msg.find("registerExtractor"), std::string::npos) << "leaked C++ SDK jargon: " << msg;
            EXPECT_EQ(msg.find("NUL bytes"), std::string::npos) << "leaked binary NUL jargon: " << msg;
        }
    }
}

TEST(DocumentIndexTest, AReaderTheAppRegisteredForAnUnsupportedFormatStillRuns) {
    // .epub is on the unsupported list and no other test registers it (the registry is process-global).
    registerExtractor(".epub", [](const std::string&) { return std::string("Chapter one text 6621"); });
    TempDir d;
    const std::string book = d.write("novel.epub", "PK binary");
    const auto pages = DocumentIndex::extractPages(book);
    ASSERT_EQ(pages.size(), 1u);
    EXPECT_EQ(pages[0].second, "Chapter one text 6621");
}
