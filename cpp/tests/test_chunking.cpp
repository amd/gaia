// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/chunking.h>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;
using namespace gaia;

namespace {

/// Estimated tokens, the same 4-chars-per-token rule the splitter uses.
std::size_t tokens(const std::string& s) {
    std::size_t codePoints = 0;
    for (unsigned char c : s) {
        if ((c & 0xC0) != 0x80) ++codePoints;
    }
    return codePoints / 4;
}

std::vector<std::string> words(const std::string& s) {
    std::istringstream in(s);
    std::vector<std::string> out;
    std::string w;
    while (in >> w) out.push_back(w);
    return out;
}

/// A fresh extension per call: registerExtractor() writes to a process-global
/// registry with no removal hook, so reusing one breaks --gtest_repeat.
std::string uniqueExtension() {
    static int counter = 0;
    return ".fakedoc" + std::to_string(++counter);
}

std::string toUpper(const std::string& s) {
    std::string out = s;
    std::transform(out.begin(), out.end(), out.begin(), [](char c) {
        return (c >= 'a' && c <= 'z') ? static_cast<char>(c - 'a' + 'A') : c;
    });
    return out;
}

std::string readFile(const fs::path& p) {
    std::ifstream in(p, std::ios::binary);
    EXPECT_TRUE(in.good()) << "missing fixture: " << p.string();
    std::ostringstream buf;
    buf << in.rdbuf();
    return buf.str();
}

} // namespace

class ChunkingTest : public ::testing::Test {
protected:
    fs::path tempDir_;

    void SetUp() override {
        tempDir_ = fs::temp_directory_path() / "gaia_chunking_test";
        fs::create_directories(tempDir_);
    }

    void TearDown() override {
        std::error_code ec;
        fs::remove_all(tempDir_, ec);
    }

    std::string writeFile(const std::string& name, const std::string& content) {
        fs::path p = tempDir_ / name;
        std::ofstream f(p, std::ios::binary);
        f << content;
        f.close();
        return p.string();
    }
};

// ---------------------------------------------------------------------------
// Splitter basics
// ---------------------------------------------------------------------------

TEST_F(ChunkingTest, EmptyInputProducesNoChunks) {
    EXPECT_TRUE(splitTextIntoChunks("").empty());
    EXPECT_TRUE(splitTextIntoChunks("   \n\n\t  \n").empty());
}

TEST_F(ChunkingTest, SingleChunkWhenTextFitsInBudget) {
    const std::string text = "A short note about retrieval. It fits in one chunk.";
    const auto chunks = splitTextIntoChunks(text, {500, 100});
    ASSERT_EQ(chunks.size(), 1u);
    EXPECT_EQ(chunks[0], text);
}

TEST_F(ChunkingTest, BlankRunsNeverProduceEmptyChunks) {
    const auto chunks = splitTextIntoChunks(
        "First idea.\n\n\n\n   \n\t\n\nSecond idea after a lot of blank space.\n\n\n",
        {8, 2});
    ASSERT_FALSE(chunks.empty());
    for (const auto& chunk : chunks) {
        EXPECT_FALSE(chunk.empty());
        EXPECT_NE(chunk, " ");
    }
}

TEST_F(ChunkingTest, RejectsUnusableConfiguration) {
    EXPECT_THROW(splitTextIntoChunks("text", {0, 0}), std::invalid_argument);
    EXPECT_THROW(splitTextIntoChunks("text", {100, 100}), std::invalid_argument);
    EXPECT_THROW(splitTextIntoChunks("text", {100, 200}), std::invalid_argument);
}

TEST_F(ChunkingTest, ChunksCarryOverlapFromTheirPredecessor) {
    // Paragraphs of roughly 25 tokens each, so several chunks are produced.
    std::string text;
    for (int i = 0; i < 8; ++i) {
        text += "Paragraph " + std::to_string(i) +
                " describes one idea in enough words to consume a measurable slice "
                "of the token budget for this test document.\n\n";
    }

    const ChunkingConfig config{40, 10};
    const auto chunks = splitTextIntoChunks(text, config);
    ASSERT_GT(chunks.size(), 2u);

    for (std::size_t i = 1; i < chunks.size(); ++i) {
        const auto previous = words(chunks[i - 1]);
        const auto current = words(chunks[i]);
        ASSERT_FALSE(previous.empty());
        ASSERT_FALSE(current.empty());

        // The chunk must open with a suffix of the previous chunk's words:
        // count how many leading words of `current` match `previous`'s tail.
        std::size_t overlapWords = 0;
        for (std::size_t n = std::min(previous.size(), current.size()); n > 0; --n) {
            bool same = true;
            for (std::size_t k = 0; k < n; ++k) {
                if (previous[previous.size() - n + k] != current[k]) {
                    same = false;
                    break;
                }
            }
            if (same) {
                overlapWords = n;
                break;
            }
        }
        EXPECT_GT(overlapWords, 0u) << "chunk " << i << " carries no overlap";

        std::string overlapText;
        for (std::size_t k = 0; k < overlapWords; ++k) {
            if (k) overlapText += " ";
            overlapText += current[k];
        }
        // Overlap is trimmed to ~chunkOverlap tokens at a word boundary, so it
        // never grows past the budget (one word of slack for the boundary cut).
        EXPECT_LE(tokens(overlapText), config.chunkOverlap + 8)
            << "overlap of chunk " << i << " is larger than the configured budget";
    }
}

TEST_F(ChunkingTest, OversizedParagraphIsSplitOnSentenceBoundaries) {
    std::string paragraph;
    for (int i = 0; i < 12; ++i) {
        paragraph += "Sentence number " + std::to_string(i) +
                     " states one complete thought about local retrieval. ";
    }

    const auto chunks = splitTextIntoChunks(paragraph, {30, 5});
    ASSERT_GT(chunks.size(), 1u);

    // Chunks are built from whole sentences, so none ends mid-sentence.
    for (const auto& chunk : chunks) {
        ASSERT_FALSE(chunk.empty());
        EXPECT_EQ(chunk.back(), '.') << "chunk ends mid-sentence: " << chunk;
    }
    // No sentence text is lost: each sentence appears in at least one chunk.
    for (int i = 0; i < 12; ++i) {
        const std::string needle = "Sentence number " + std::to_string(i);
        bool found = false;
        for (const auto& chunk : chunks) {
            if (chunk.find(needle) != std::string::npos) {
                found = true;
                break;
            }
        }
        EXPECT_TRUE(found) << needle << " was dropped";
    }
}

TEST_F(ChunkingTest, ParagraphLargerThanBudgetWithNoSentenceBreakStaysWhole) {
    // No '.', '!' or '?' anywhere, so the sentence splitter cannot subdivide it.
    // Python keeps the oversized run as a single chunk rather than hard-cutting
    // mid-word; C++ must do the same instead of silently truncating.
    std::string run;
    for (int i = 0; i < 60; ++i) {
        run += "token" + std::to_string(i) + " ";
    }
    const std::string paragraph = run.substr(0, run.size() - 1);

    const auto chunks = splitTextIntoChunks(paragraph, {20, 5});
    ASSERT_EQ(chunks.size(), 1u);
    EXPECT_EQ(chunks[0], paragraph);
    EXPECT_GT(tokens(chunks[0]), 20u);
}

// ---------------------------------------------------------------------------
// Sentence splitting
// ---------------------------------------------------------------------------

TEST_F(ChunkingTest, SplitsOnSentenceEndFollowedByCapital) {
    const auto sentences =
        splitIntoSentences("First sentence here. Second one follows! Third one? Done.");
    ASSERT_EQ(sentences.size(), 4u);
    EXPECT_EQ(sentences[0], "First sentence here.");
    EXPECT_EQ(sentences[1], "Second one follows!");
    EXPECT_EQ(sentences[2], "Third one?");
    EXPECT_EQ(sentences[3], "Done.");
}

TEST_F(ChunkingTest, DoesNotSplitOnAbbreviationsOrLowercaseContinuations) {
    const auto abbrev =
        splitIntoSentences("Dr. Ada met Mr. Babbage at noon, e.g. before tea. Then they left.");
    ASSERT_EQ(abbrev.size(), 2u);
    EXPECT_EQ(abbrev[0], "Dr. Ada met Mr. Babbage at noon, e.g. before tea.");
    EXPECT_EQ(abbrev[1], "Then they left.");

    // Python's protection is a plain string substitution, so a sentence that
    // genuinely ends in "etc." keeps the following sentence attached. Ported
    // as-is: diverging here would move chunk boundaries between runtimes.
    const auto trailingAbbrev = splitIntoSentences("They packed maps, food, etc. Then they left.");
    ASSERT_EQ(trailingAbbrev.size(), 1u);

    const auto lowercase = splitIntoSentences("Version 1.2 shipped. version 1.3 did not.");
    ASSERT_EQ(lowercase.size(), 1u);

    EXPECT_TRUE(splitIntoSentences("   ").empty());
}

TEST_F(ChunkingTest, SplitsAcrossNewlineWhitespaceRuns) {
    const auto sentences = splitIntoSentences("A sentence ends here.\n\n  Another begins.");
    ASSERT_EQ(sentences.size(), 2u);
    EXPECT_EQ(sentences[0], "A sentence ends here.");
    EXPECT_EQ(sentences[1], "Another begins.");
}

// ---------------------------------------------------------------------------
// Unicode handling — Python's str.isupper()/str.isspace(), not ASCII
// ---------------------------------------------------------------------------

TEST_F(ChunkingTest, NonAsciiCapitalStartsASectionLikeAnAsciiOne) {
    const std::string body =
        "\n\nA line of body text that belongs to the heading above it and runs on long "
        "enough to matter.\n\nAnother paragraph closes the document out.\n";

    const std::string uumlaut = "\xC3\x9C"; // U+00DC LATIN CAPITAL LETTER U WITH DIAERESIS
    const auto ascii = splitTextIntoChunks("Uber die Sache" + body, {20, 5});
    auto unicode = splitTextIntoChunks(uumlaut + "ber die Sache" + body, {20, 5});

    ASSERT_EQ(unicode.size(), ascii.size())
        << "a non-ASCII capital must open a section exactly as an ASCII one does";
    for (std::size_t i = 0; i < unicode.size(); ++i) {
        std::size_t pos = 0;
        while ((pos = unicode[i].find(uumlaut, pos)) != std::string::npos) {
            unicode[i].replace(pos, uumlaut.size(), "U");
            pos += 1;
        }
        EXPECT_EQ(unicode[i], ascii[i]) << "chunk " << i;
    }
}

TEST_F(ChunkingTest, UnicodeWhitespaceSeparatesParagraphs) {
    // The middle line holds only a non-breaking space. Python counts it as
    // whitespace, so the two paragraphs are separate and get joined by a space.
    const std::string nbsp = "\xC2\xA0"; // U+00A0 NO-BREAK SPACE
    const auto chunks =
        splitTextIntoChunks("First part.\n" + nbsp + "\nSecond part.", {500, 100});
    ASSERT_EQ(chunks.size(), 1u);
    EXPECT_EQ(chunks[0], "First part. Second part.");
}

// ---------------------------------------------------------------------------
// Extraction
// ---------------------------------------------------------------------------

TEST_F(ChunkingTest, ExtractsTextMarkdownAndSourceFiles) {
    const auto text = extractFile(writeFile("notes.txt", "  plain text body  \n"));
    EXPECT_EQ(text.text, "plain text body");
    EXPECT_EQ(text.format, TextFormat::PlainText);
    EXPECT_EQ(text.extension, ".txt");

    const auto markdown = extractFile(writeFile("readme.MD", "# Title\n\nBody.\n"));
    EXPECT_EQ(markdown.text, "# Title\n\nBody.");
    EXPECT_EQ(markdown.format, TextFormat::Markdown);
    EXPECT_EQ(markdown.extension, ".md");

    const auto source = extractFile(writeFile("main.cpp", "int main() { return 0; }\n"));
    EXPECT_EQ(source.format, TextFormat::SourceCode);
    EXPECT_EQ(source.text, "int main() { return 0; }");
}

TEST_F(ChunkingTest, DecodesNonUtf8BytesAsLatin1) {
    // 0xE9 is 'é' in latin-1 and invalid on its own in UTF-8.
    const std::string latin1 = std::string("caf\xE9 menu");
    const auto extracted = extractFile(writeFile("menu.txt", latin1));
    EXPECT_EQ(extracted.text, std::string("caf\xC3\xA9 menu"));
}

TEST_F(ChunkingTest, UnsupportedDocumentFormatsFailLoudly) {
    for (const auto& name : {"report.pdf", "memo.docx", "sheet.xlsx", "deck.pptx"}) {
        const std::string path = writeFile(name, "not really a document");
        try {
            extractFile(path);
            FAIL() << name << " was extracted instead of refused";
        } catch (const std::runtime_error& e) {
            const std::string message = e.what();
            EXPECT_NE(message.find("Unsupported file type"), std::string::npos) << message;
            EXPECT_NE(message.find(fs::path(name).extension().string()), std::string::npos)
                << message;
            EXPECT_NE(message.find("supported:"), std::string::npos) << message;
            EXPECT_NE(message.find("registerExtractor"), std::string::npos) << message;
        }
    }
}

TEST_F(ChunkingTest, UnknownExtensionFailsLoudlyButPlainTextEscapeHatchWorks) {
    const std::string path = writeFile("data.weirdext", "some text");
    EXPECT_THROW(extractFile(path), std::runtime_error);
    EXPECT_EQ(extractPlainTextFile(path).text, "some text");

    const std::string csv = writeFile("rows.csv", "a,b\n1,2\n");
    try {
        extractFile(csv);
        FAIL() << "CSV was extracted instead of refused";
    } catch (const std::runtime_error& e) {
        EXPECT_NE(std::string(e.what()).find("extractPlainTextFile"), std::string::npos)
            << e.what();
    }
}

TEST_F(ChunkingTest, BinaryContentIsRefusedNotIndexedAsGarbage) {
    const std::string path =
        writeFile("blob.txt", std::string("PK\x03\x04", 4) + std::string("\0\0binary", 8));
    try {
        extractFile(path);
        FAIL() << "binary content was extracted";
    } catch (const std::runtime_error& e) {
        EXPECT_NE(std::string(e.what()).find("binary"), std::string::npos) << e.what();
    }
}

TEST_F(ChunkingTest, MissingFileOrDirectoryFailsLoudly) {
    EXPECT_THROW(extractFile((tempDir_ / "nope.txt").string()), std::runtime_error);

    // A directory named like a text file must not extract as empty text.
    fs::create_directories(tempDir_ / "folder.txt");
    EXPECT_THROW(extractFile((tempDir_ / "folder.txt").string()), std::runtime_error);
}

TEST_F(ChunkingTest, RegisteredExtractorHandlesOutOfScopeFormats) {
    EXPECT_THROW(registerExtractor("pdf", [](const std::string&) { return std::string(); }),
                 std::invalid_argument);
    EXPECT_THROW(registerExtractor(".pdf", nullptr), std::invalid_argument);

    // The registry is process-global with no unregister hook, so each run needs
    // its own extension or a repeated/shuffled run would see the previous one.
    const std::string ext = uniqueExtension();
    EXPECT_FALSE(isSupportedExtension(ext));
    registerExtractor("." + toUpper(ext.substr(1)), [](const std::string& path) {
        return "extracted from " + fs::path(path).filename().string();
    });
    EXPECT_TRUE(isSupportedExtension(ext)) << "extension match must be case-insensitive";

    const std::string name = "thing" + ext;
    const auto extracted = extractFile(writeFile(name, "ignored"));
    EXPECT_EQ(extracted.text, "extracted from " + name);

    const auto all = supportedExtensions();
    EXPECT_NE(std::find(all.begin(), all.end(), ext), all.end());
    EXPECT_NE(std::find(all.begin(), all.end(), ".md"), all.end());
    EXPECT_EQ(std::find(all.begin(), all.end(), ".pdf"), all.end());
}

TEST_F(ChunkingTest, RegisteredExtractorFailurePropagates) {
    const std::string ext = uniqueExtension();
    registerExtractor(ext, [](const std::string&) -> std::string {
        throw std::runtime_error("backend unavailable");
    });

    const std::string path = writeFile("thing" + ext, "ignored");
    try {
        extractFile(path);
        FAIL() << "extractor failure was swallowed";
    } catch (const std::runtime_error& e) {
        EXPECT_STREQ(e.what(), "backend unavailable");
    }
}

TEST_F(ChunkingTest, ChunkFileExtractsAndSplits) {
    std::string body;
    for (int i = 0; i < 10; ++i) {
        body += "Paragraph " + std::to_string(i) +
                " holds a sentence long enough to matter for the budget.\n\n";
    }
    const auto chunks = chunkFile(writeFile("doc.md", body), {30, 8});
    EXPECT_GT(chunks.size(), 1u);
}

// ---------------------------------------------------------------------------
// Cross-runtime parity
//
// tests/fixtures/chunking/parity_expected.json holds the chunks produced by
// RAGSDK._split_text_into_chunks (src/gaia/rag/sdk.py) for the same fixture
// documents. If this test fails, the two runtimes will disagree about chunk
// boundaries and an index built by one is no longer comparable to the other's.
// ---------------------------------------------------------------------------

TEST_F(ChunkingTest, ChunkBoundariesMatchPythonRagSdk) {
    const fs::path fixtures = fs::path(GAIA_TEST_FIXTURES_DIR) / "chunking";
    const auto expected = nlohmann::json::parse(readFile(fixtures / "parity_expected.json"));

    ASSERT_TRUE(expected.contains("cases"));
    ASSERT_FALSE(expected["cases"].empty());

    for (const auto& testCase : expected["cases"]) {
        const std::string file = testCase["file"].get<std::string>();
        const ChunkingConfig config{testCase["chunk_size"].get<std::size_t>(),
                                    testCase["chunk_overlap"].get<std::size_t>()};
        const auto expectedChunks = testCase["chunks"].get<std::vector<std::string>>();

        const std::string text = extractFile((fixtures / file).string()).text;
        const auto actual = splitTextIntoChunks(text, config);

        const std::string label = file + " (size=" + std::to_string(config.chunkSize) +
                                  ", overlap=" + std::to_string(config.chunkOverlap) + ")";
        ASSERT_EQ(actual.size(), expectedChunks.size()) << label;
        for (std::size_t i = 0; i < actual.size(); ++i) {
            EXPECT_EQ(actual[i], expectedChunks[i]) << label << " chunk " << i;
        }
    }
}
