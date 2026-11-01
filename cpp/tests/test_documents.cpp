// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Document text extraction against the repo's real fixtures. Expected page
// counts and phrases are what the Python runtime's pypdf / python-docx /
// openpyxl read from the same files.

#include <gtest/gtest.h>
#include <gaia/documents.h>

#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <utility>
#include <vector>

using namespace gaia;

namespace {

const std::string kTiers = std::string(GAIA_REPO_FIXTURES_DIR) + "/gaia/tiers_documents/";
const std::string kRepo = std::string(GAIA_REPO_FIXTURES_DIR) + "/../../";

std::string page(const DocumentText& d, int n) {
    const std::string marker = "--- Page " + std::to_string(n) + " ---\n";
    const auto a = d.text.find(marker);
    if (a == std::string::npos) return "";
    const auto b = d.text.find("--- Page " + std::to_string(n + 1) + " ---", a);
    return d.text.substr(a + marker.size(), b == std::string::npos ? std::string::npos : b - a - marker.size());
}

}  // namespace

TEST(DocumentsTest, LeaseHasFivePagesAndItsHeading) {
    const DocumentText d = extractDocumentText(kTiers + "harbor_lease.pdf");
    EXPECT_EQ(d.kind, "pdf");
    EXPECT_EQ(d.sections, 5);
    EXPECT_TRUE(d.emptySections.empty());
    EXPECT_NE(page(d, 1).find("COMMERCIAL LEASE AGREEMENT"), std::string::npos) << page(d, 1).substr(0, 300);
    EXPECT_NE(page(d, 1).find("Bluefin Bakery"), std::string::npos);
}

TEST(DocumentsTest, SixtyPageReportKeepsPageNumbersForCitation) {
    const DocumentText d = extractDocumentText(kTiers + "northwind_annual_report.pdf");
    EXPECT_EQ(d.sections, 60);
    EXPECT_TRUE(d.emptySections.empty());
    EXPECT_NE(page(d, 1).find("NORTHWIND MUNICIPAL WATER AUTHORITY"), std::string::npos);
    EXPECT_GT(d.text.size(), 60000u);  // pypdf reads 69,917 characters
}

TEST(DocumentsTest, ScannedPageIsReportedAsHavingNoText) {
    const DocumentText d = extractDocumentText(kTiers + "scanned_invoice.pdf");
    EXPECT_EQ(d.sections, 1);
    EXPECT_EQ(d.emptySections, std::vector<int>{1});
    bool warned = false;
    for (const auto& w : d.warnings) warned |= w.find("scanned") != std::string::npos;
    EXPECT_TRUE(warned);
}

TEST(DocumentsTest, ScannedPageImageBecomesAPng) {
    // The fixture's scan is 1700x2200 gray pixels under ASCII85+Flate, inside a form XObject.
    const auto images = extractPageImages(kTiers + "scanned_invoice.pdf", {1, 2});
    ASSERT_EQ(images.size(), 1u);
    const std::string& png = images.at(1);
    ASSERT_GT(png.size(), 1000u);
    EXPECT_EQ(png.substr(1, 3), "PNG");
}

TEST(DocumentsTest, CorruptPdfFailsLoudlyOrSaysWhatItSkipped) {
    try {
        const DocumentText d = extractDocumentText(kTiers + "malformed/board_minutes_corrupt.pdf");
        EXPECT_FALSE(d.warnings.empty() && d.text.find_first_not_of(" \n-Page0123456789") == std::string::npos)
            << "a damaged file must not read as a clean, empty document";
    } catch (const std::runtime_error& e) {
        EXPECT_NE(std::string(e.what()).find("board_minutes_corrupt.pdf"), std::string::npos);
    }
}

TEST(DocumentsTest, PdfFromAnotherWriterWithCompositeFonts) {
    const DocumentText d = extractDocumentText(kRepo + "docs/spec/agent-memory-architecture.pdf");
    EXPECT_EQ(d.sections, 47);
    EXPECT_NE(page(d, 1).find("Memory v2 Architecture"), std::string::npos) << page(d, 1).substr(0, 300);
}

TEST(DocumentsTest, ManualWithManyPages) {
    const DocumentText d = extractDocumentText(kRepo + "data/pdf/Oil-and-Gas-Activity-Operations-Manual-1-10.pdf");
    EXPECT_EQ(d.sections, 10);
    EXPECT_NE(page(d, 1).find("VERSION 1.43"), std::string::npos) << page(d, 1).substr(0, 300);
}

TEST(DocumentsTest, ThousandPageHandbookIsQuick) {
    const auto t0 = std::chrono::steady_clock::now();
    const DocumentText d = extractDocumentText(kRepo + "eval/corpus/documents/safety_handbook_large.pdf");
    EXPECT_EQ(d.sections, 1666);
    EXPECT_NE(page(d, 1).find("synthetic safety handbook"), std::string::npos);
    EXPECT_LT(std::chrono::steady_clock::now() - t0, std::chrono::seconds(30));
}

TEST(DocumentsTest, WordDocumentParagraphs) {
    const DocumentText d = extractDocumentText(kTiers + "orion_onboarding.docx");
    EXPECT_EQ(d.kind, "docx");
    EXPECT_NE(d.text.find("Orion Robotics - New Hire Onboarding Guide\n"), std::string::npos);
    EXPECT_NE(d.text.find("$1,200 laptop stipend"), std::string::npos);
}

TEST(DocumentsTest, WorkbookSheetsAndCells) {
    const DocumentText d = extractDocumentText(std::string(GAIA_REPO_FIXTURES_DIR) + "/xlsx/lab_results.xlsx");
    EXPECT_EQ(d.kind, "xlsx");
    EXPECT_GE(d.sections, 2);
    EXPECT_NE(d.text.find("--- Sheet: Cholesterol ---"), std::string::npos);
    EXPECT_NE(d.text.find("A1=Lipid panel 2026"), std::string::npos) << d.text.substr(0, 300);
    EXPECT_NE(d.text.find("B2=LDL"), std::string::npos);
    EXPECT_NE(d.text.find("--- Sheet: Notes ---"), std::string::npos);
    EXPECT_NE(d.text.find("Target LDL under 100"), std::string::npos);
}

TEST(DocumentsTest, SlidesInOrderAndEmptySlideNoted) {
    const DocumentText d = extractDocumentText(std::string(GAIA_TEST_FIXTURES_DIR) + "/documents/review.pptx");
    EXPECT_EQ(d.sections, 3);
    EXPECT_LT(d.text.find("Quarterly Review"), d.text.find("Risks"));
    EXPECT_NE(d.text.find("Supply chain & logistics"), std::string::npos);
    EXPECT_EQ(d.emptySections, std::vector<int>{3});
}

TEST(DocumentsTest, NonDocumentsAreRefusedByName) {
    EXPECT_FALSE(isDocumentExtension(".txt"));
    EXPECT_TRUE(isDocumentExtension(".PDF"));
    EXPECT_THROW(extractDocumentText(kTiers + "does_not_exist.pdf"), std::runtime_error);
}

// Writes each fixture's extracted text to $GAIA_DUMP_DOCS_DIR for a side-by-side
// comparison with the Python extractors. A no-op unless that variable is set.
TEST(DocumentsTest, DumpForComparison) {
    const char* dir = std::getenv("GAIA_DUMP_DOCS_DIR");
    if (!dir || !*dir) GTEST_SKIP() << "set GAIA_DUMP_DOCS_DIR to dump extracted text";
    for (const std::string rel : {"gaia/tiers_documents/harbor_lease.pdf", "gaia/tiers_documents/northwind_annual_report.pdf",
                                  "gaia/tiers_documents/supplier_audit.pdf", "../../data/pdf/Oil-and-Gas-Activity-Operations-Manual-1-10.pdf",
                                  "../../docs/spec/agent-memory-architecture.pdf", "../../docs/testing/email-agent-linux-test.pdf"}) {
        const DocumentText d = extractDocumentText(std::string(GAIA_REPO_FIXTURES_DIR) + "/" + rel);
        const std::string name = rel.substr(rel.rfind('/') + 1);
        std::ofstream(std::string(dir) + "/" + name + ".txt", std::ios::binary) << d.text;
    }
}

// ---------------------------------------------------------------------------
// Hostile and unusual files, built in the test.
// ---------------------------------------------------------------------------

namespace {

namespace fs = std::filesystem;

using PdfObjects = std::vector<std::pair<int, std::string>>;

std::string pdfStream(const std::string& dict, const std::string& data) {
    return "<< " + dict + " /Length " + std::to_string(data.size()) + " >>\nstream\n" + data + "\nendstream";
}

std::string pdfFile(const PdfObjects& objects) {
    std::string s = "%PDF-1.4\n";
    for (const auto& [n, body] : objects) s += std::to_string(n) + " 0 obj\n" + body + "\nendobj\n";
    return s + "trailer\n<< /Root 1 0 R >>\n%%EOF\n";
}

const std::string kCatalog = "<< /Type /Catalog /Pages 2 0 R >>";
const std::string kFont = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>";

std::string pageDict(const std::string& contents, const std::string& extraResources = "") {
    return "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 3 0 R >> " + extraResources + " >> /Contents " +
           contents + " >>";
}

std::string showText(const std::string& text) { return "BT /F1 12 Tf 72 700 Td (" + text + ") Tj ET"; }

void put16(std::string& s, unsigned v) {
    s.push_back(static_cast<char>(v & 0xFF));
    s.push_back(static_cast<char>((v >> 8) & 0xFF));
}
void put32(std::string& s, unsigned long v) {
    put16(s, static_cast<unsigned>(v & 0xFFFF));
    put16(s, static_cast<unsigned>((v >> 16) & 0xFFFF));
}

unsigned long crc32(const std::string& data) {
    unsigned long crc = 0xFFFFFFFFul;
    for (unsigned char c : data) {
        crc ^= c;
        for (int k = 0; k < 8; ++k) crc = (crc >> 1) ^ (0xEDB88320ul & (0ul - (crc & 1ul)));
    }
    return crc ^ 0xFFFFFFFFul;
}

// A zip archive with every part stored uncompressed.
std::string zipFile(const std::vector<std::pair<std::string, std::string>>& parts) {
    std::string out, central;
    for (const auto& [name, data] : parts) {
        const unsigned long offset = static_cast<unsigned long>(out.size());
        const unsigned long crc = crc32(data);
        put32(out, 0x04034b50);
        put16(out, 20);
        put16(out, 0);
        put16(out, 0);
        put32(out, 0);
        put32(out, crc);
        put32(out, static_cast<unsigned long>(data.size()));
        put32(out, static_cast<unsigned long>(data.size()));
        put16(out, static_cast<unsigned>(name.size()));
        put16(out, 0);
        out += name + data;
        put32(central, 0x02014b50);
        put16(central, 20);
        put16(central, 20);
        put16(central, 0);
        put16(central, 0);
        put32(central, 0);
        put32(central, crc);
        put32(central, static_cast<unsigned long>(data.size()));
        put32(central, static_cast<unsigned long>(data.size()));
        put16(central, static_cast<unsigned>(name.size()));
        put32(central, 0);  // extra and comment lengths
        put32(central, 0);  // disk and internal attributes
        put32(central, 0);  // external attributes
        put32(central, offset);
        central += name;
    }
    const unsigned long cdOffset = static_cast<unsigned long>(out.size());
    out += central;
    put32(out, 0x06054b50);
    put32(out, 0);
    put16(out, static_cast<unsigned>(parts.size()));
    put16(out, static_cast<unsigned>(parts.size()));
    put32(out, static_cast<unsigned long>(central.size()));
    put32(out, cdOffset);
    put16(out, 0);
    return out;
}

std::string workbook(const std::string& sheetXml, const std::string& sheetsXml = "<sheet name=\"S\" sheetId=\"1\" r:id=\"rId1\"/>",
                     const std::string& stylesXml = "") {
    std::vector<std::pair<std::string, std::string>> parts = {
        {"xl/workbook.xml", "<workbook><sheets>" + sheetsXml + "</sheets></workbook>"},
        {"xl/_rels/workbook.xml.rels",
         "<Relationships><Relationship Id=\"rId1\" Target=\"worksheets/sheet1.xml\"/></Relationships>"},
        {"xl/worksheets/sheet1.xml", "<worksheet><sheetData>" + sheetXml + "</sheetData></worksheet>"}};
    if (!stylesXml.empty()) parts.emplace_back("xl/styles.xml", stylesXml);
    return zipFile(parts);
}

class HostileDocumentsTest : public ::testing::Test {
protected:
    fs::path dir_;
    void SetUp() override {
        dir_ = fs::temp_directory_path() / "gaia_hostile_documents_test";
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
    static std::size_t count(const std::string& hay, const std::string& needle) {
        std::size_t n = 0;
        for (auto at = hay.find(needle); at != std::string::npos; at = hay.find(needle, at + 1)) ++n;
        return n;
    }
    static bool warned(const DocumentText& d, const std::string& what) {
        for (const auto& w : d.warnings) {
            if (w.find(what) != std::string::npos) return true;
        }
        return false;
    }
};

}  // namespace

TEST_F(HostileDocumentsTest, DeepPageTreeDoesNotExhaustTheStack) {
    const int depth = 100000;
    PdfObjects objs = {{1, "<< /Type /Catalog /Pages 10 0 R >>"}, {3, kFont}, {4, pdfStream("", showText("Bottom"))}};
    for (int k = 0; k < depth; ++k) objs.emplace_back(10 + k, "<< /Type /Pages /Kids [" + std::to_string(11 + k) + " 0 R] >>");
    objs.emplace_back(10 + depth, pageDict("4 0 R"));
    const DocumentText d = extractDocumentText(write("deep.pdf", pdfFile(objs)));
    EXPECT_EQ(d.sections, 1);
    EXPECT_NE(page(d, 1).find("Bottom"), std::string::npos) << d.text.substr(0, 200);
}

TEST_F(HostileDocumentsTest, FormThatDrawsItselfIsDrawnOnceAndNamed) {
    const std::string image = pdfStream("/Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceGray /BitsPerComponent 8",
                                        std::string("\x00\xFF\xFF\x00", 4));
    const std::string form = pdfStream(
        "/Type /XObject /Subtype /Form /BBox [0 0 100 100] /Resources << /Font << /F1 3 0 R >> /XObject << /X 6 0 R /Im 7 0 R >> >>",
        showText("Loop") + " /X Do /X Do /X Do /X Do");
    const std::string path = write("loop.pdf", pdfFile({{1, kCatalog},
                                                        {2, "<< /Type /Pages /Kids [8 0 R] >>"},
                                                        {3, kFont},
                                                        {4, pdfStream("", "/X Do")},
                                                        {6, form},
                                                        {7, image},
                                                        {8, pageDict("4 0 R", "/XObject << /X 6 0 R >>")}}));
    const DocumentText d = extractDocumentText(path);
    EXPECT_EQ(count(d.text, "Loop"), 1u) << d.text;
    EXPECT_TRUE(warned(d, "draws itself"));
    const auto images = extractPageImages(path, {1});
    ASSERT_EQ(images.size(), 1u);
    EXPECT_EQ(images.at(1).substr(1, 3), "PNG");
}

TEST_F(HostileDocumentsTest, FormFanOutStopsAtABudgetAndSaysSo) {
    // Each form draws the next 30 times: 30^3 leaf draws unless something stops it.
    PdfObjects objs = {{1, kCatalog}, {2, "<< /Type /Pages /Kids [8 0 R] >>"}, {3, kFont}, {4, pdfStream("", "/N Do")},
                       {8, pageDict("4 0 R", "/XObject << /N 20 0 R >>")}};
    for (int k = 20; k <= 23; ++k) {
        std::string draws;
        for (int i = 0; i < 30; ++i) draws += " /N Do";
        objs.emplace_back(k, pdfStream("/Type /XObject /Subtype /Form /Resources << /Font << /F1 3 0 R >> /XObject << /N " +
                                           std::to_string(k + 1) + " 0 R >> >>",
                                       k == 23 ? showText("Leaf") : draws));
    }
    const auto t0 = std::chrono::steady_clock::now();
    const DocumentText d = extractDocumentText(write("fanout.pdf", pdfFile(objs)));
    EXPECT_LT(std::chrono::steady_clock::now() - t0, std::chrono::seconds(10));
    EXPECT_TRUE(warned(d, "form XObjects")) << ::testing::PrintToString(d.warnings);
    EXPECT_NE(d.text.find("Leaf"), std::string::npos);
}

TEST_F(HostileDocumentsTest, StreamWordInsideAStringDoesNotSwallowLaterObjects) {
    const DocumentText d = extractDocumentText(write("strings.pdf", pdfFile({{1, kCatalog},
                                                                            {2, "<< /Type /Pages /Kids [8 0 R] >>"},
                                                                            {7, "<< /Title (Data streams) >>"},
                                                                            {8, pageDict("4 0 R")},
                                                                            {3, kFont},
                                                                            {4, pdfStream("", showText("Visible"))}})));
    EXPECT_EQ(d.sections, 1);
    EXPECT_NE(page(d, 1).find("Visible"), std::string::npos) << d.text;
}

TEST_F(HostileDocumentsTest, ManyObjectsWithoutStreamsScanQuickly) {
    // Every object used to search the rest of the file for "stream".
    PdfObjects objs = {{1, kCatalog}, {2, "<< /Type /Pages /Kids [8 0 R] >>"}, {3, kFont},
                       {4, pdfStream("", showText("Fast"))}, {8, pageDict("4 0 R")}};
    for (int k = 0; k < 200000; ++k) objs.emplace_back(100 + k, "<< /N " + std::to_string(k) + " >>");
    objs.emplace_back(99, pdfStream("", std::string(64, ' ')));
    const auto t0 = std::chrono::steady_clock::now();
    const DocumentText d = extractDocumentText(write("many.pdf", pdfFile(objs)));
    EXPECT_LT(std::chrono::steady_clock::now() - t0, std::chrono::seconds(10));
    EXPECT_NE(page(d, 1).find("Fast"), std::string::npos);
}

TEST_F(HostileDocumentsTest, RepeatedContentStreamHitsTheDocumentBudget) {
    std::string refs;
    for (int i = 0; i < 300; ++i) refs += "4 0 R ";
    const std::string path = write("repeat.pdf", pdfFile({{1, kCatalog},
                                                          {2, "<< /Type /Pages /Kids [8 0 R] >>"},
                                                          {3, kFont},
                                                          {4, pdfStream("", std::string(2 * 1024 * 1024, ' '))},
                                                          {8, pageDict("[" + refs + "]")}}));
    try {
        extractDocumentText(path);
        FAIL() << "a file that repeats one stream past the budget must fail";
    } catch (const std::runtime_error& e) {
        EXPECT_NE(std::string(e.what()).find("MB in total"), std::string::npos) << e.what();
    }
}

TEST_F(HostileDocumentsTest, ObjectStreamInALaterUpdateReplacesTheOriginal) {
    const std::string newer = pageDict("5 0 R");
    const std::string header = "8 0 ";
    const std::string objStm = pdfStream("/Type /ObjStm /N 1 /First " + std::to_string(header.size()), header + newer);
    const DocumentText d = extractDocumentText(write("update.pdf", pdfFile({{1, kCatalog},
                                                                           {2, "<< /Type /Pages /Kids [8 0 R] >>"},
                                                                           {3, kFont},
                                                                           {4, pdfStream("", showText("Old"))},
                                                                           {5, pdfStream("", showText("New"))},
                                                                           {8, pageDict("4 0 R")},
                                                                           {30, objStm}})));
    EXPECT_NE(page(d, 1).find("New"), std::string::npos) << d.text;
    EXPECT_EQ(page(d, 1).find("Old"), std::string::npos) << d.text;
}

TEST_F(HostileDocumentsTest, KidsAndContentsMayBeReferencesToArrays) {
    const DocumentText d = extractDocumentText(write("indirect.pdf", pdfFile({{1, kCatalog},
                                                                             {2, "<< /Type /Pages /Kids 9 0 R /Count 2 >>"},
                                                                             {9, "[8 0 R 11 0 R]"},
                                                                             {3, kFont},
                                                                             {4, pdfStream("", showText("One"))},
                                                                             {5, pdfStream("", showText("Two"))},
                                                                             {6, pdfStream("", showText("Three"))},
                                                                             {12, "[4 0 R 5 0 R]"},
                                                                             {8, pageDict("12 0 R")},
                                                                             {11, pageDict("6 0 R")}})));
    ASSERT_EQ(d.sections, 2) << d.text;
    EXPECT_NE(page(d, 1).find("One"), std::string::npos) << d.text;
    EXPECT_NE(page(d, 1).find("Two"), std::string::npos) << d.text;
    EXPECT_NE(page(d, 2).find("Three"), std::string::npos) << d.text;
}

TEST_F(HostileDocumentsTest, PagesPastTheCapAreReported) {
    PdfObjects objs = {{1, kCatalog}, {3, kFont}};
    std::string kids;
    for (int k = 0; k < 5001; ++k) {
        kids += std::to_string(100 + k) + " 0 R ";
        objs.emplace_back(100 + k, "<< /Type /Page /Parent 2 0 R >>");
    }
    objs.emplace_back(2, "<< /Type /Pages /Kids [" + kids + "] >>");
    const DocumentText d = extractDocumentText(write("long.pdf", pdfFile(objs)));
    EXPECT_EQ(d.sections, 5000);
    EXPECT_TRUE(warned(d, "more than 5000 pages")) << ::testing::PrintToString(d.warnings);
}

TEST_F(HostileDocumentsTest, ToUnicodeKeepsCharactersBeyondTheBasicPlane) {
    const std::string cmap =
        "begincodespacerange\n<00> <FF>\nendcodespacerange\n"
        "1 beginbfchar\n<03> <D83D>\nendbfchar\n"
        "1 beginbfrange\n<01> <02> <D83DDE00>\nendbfrange\n";
    const DocumentText d = extractDocumentText(write(
        "emoji.pdf", pdfFile({{1, kCatalog},
                              {2, "<< /Type /Pages /Kids [8 0 R] >>"},
                              {3, "<< /Type /Font /Subtype /Type1 /BaseFont /X /ToUnicode 6 0 R >>"},
                              {6, pdfStream("", cmap)},
                              {4, pdfStream("", "BT /F1 12 Tf (\\001\\002\\003) Tj ET")},
                              {8, pageDict("4 0 R")}})));
    // U+1F600, U+1F601, then U+FFFD for the unpaired high surrogate.
    EXPECT_NE(page(d, 1).find("\xF0\x9F\x98\x80\xF0\x9F\x98\x81\xEF\xBF\xBD"), std::string::npos) << page(d, 1);
}

TEST_F(HostileDocumentsTest, WordAlternateContentIsReadOnce) {
    const std::string xml =
        "<w:document><w:body><w:p><w:r><w:t>Before</w:t></w:r></w:p>"
        "<w:p><w:r><mc:AlternateContent><mc:Choice Requires=\"wps\"><w:drawing><w:txbxContent><w:p><w:r><w:t>Boxed</w:t>"
        "</w:r></w:p></w:txbxContent></w:drawing></mc:Choice><mc:Fallback><w:pict><w:txbxContent><w:p><w:r><w:t>Boxed</w:t>"
        "</w:r></w:p></w:txbxContent></w:pict></mc:Fallback></mc:AlternateContent></w:r></w:p>"
        "<w:p><w:r><w:t>After</w:t></w:r></w:p></w:body></w:document>";
    const DocumentText d = extractDocumentText(write("alt.docx", zipFile({{"word/document.xml", xml}})));
    EXPECT_EQ(count(d.text, "Boxed"), 1u) << d.text;
    EXPECT_NE(d.text.find("After"), std::string::npos);
}

TEST_F(HostileDocumentsTest, WorkbookDatesReadAsDatesLikeOpenpyxl) {
    const std::string styles =
        "<styleSheet><numFmts count=\"1\"><numFmt numFmtId=\"164\" formatCode=\"yyyy\\-mm\\-dd\\ hh:mm\"/></numFmts>"
        "<cellStyleXfs count=\"1\"><xf numFmtId=\"14\"/></cellStyleXfs>"
        "<cellXfs count=\"4\"><xf numFmtId=\"0\"/><xf numFmtId=\"14\"/><xf numFmtId=\"164\"/><xf numFmtId=\"4\"/></cellXfs></styleSheet>";
    const std::string sheet =
        "<row r=\"1\"><c r=\"A1\" s=\"1\"><v>45658</v></c><c r=\"B1\" s=\"2\"><v>45658.5</v></c><c r=\"C1\"><v>45658</v></c>"
        "<c r=\"D1\" s=\"1\"><v>0.5</v></c><c r=\"E1\" s=\"1\"><v>59</v></c><c r=\"F1\" s=\"1\"><v>61</v></c>"
        "<c r=\"G1\" s=\"3\"><v>1234.5</v></c></row>";
    const DocumentText d = extractDocumentText(write("dates.xlsx", workbook(sheet, "<sheet name=\"S\" sheetId=\"1\" r:id=\"rId1\"/>", styles)));
    EXPECT_NE(d.text.find("A1=2025-01-01 00:00:00\tB1=2025-01-01 12:00:00\tC1=45658\tD1=12:00:00\t"
                          "E1=1900-02-28 00:00:00\tF1=1900-03-01 00:00:00\tG1=1234.5"),
              std::string::npos)
        << d.text;
}

TEST_F(HostileDocumentsTest, WorkbookCellsWithoutAReferenceKeepTheirColumns) {
    const std::string sheet =
        "<row><c t=\"inlineStr\"><is><t>a</t></is></c><c><v>2</v></c><c r=\"E1\"><v>5</v></c><c><v>6</v></c></row>"
        "<row><c><v>7</v></c></row>";
    const std::string path = write("norefs.xlsx", workbook(sheet));
    const DocumentText d = extractDocumentText(path);
    EXPECT_NE(d.text.find("A1=a\tB1=2\tE1=5\tF1=6\nA2=7\n"), std::string::npos) << d.text;
    const auto sheets = readWorkbook(path);
    ASSERT_EQ(sheets.size(), 1u);
    ASSERT_EQ(sheets[0].rows.size(), 2u);
    EXPECT_EQ(sheets[0].rows[0], (std::vector<std::string>{"a", "2", "", "", "5", "6"}));
}

TEST_F(HostileDocumentsTest, WorkbookListingOneSheetManyTimesReadsItOnce) {
    std::string sheets;
    for (int i = 0; i < 3000; ++i) sheets += "<sheet name=\"S" + std::to_string(i) + "\" sheetId=\"1\" r:id=\"rId1\"/>";
    const DocumentText d = extractDocumentText(write("repeat.xlsx", workbook("<row r=\"1\"><c r=\"A1\"><v>1</v></c></row>", sheets)));
    EXPECT_EQ(d.sections, 1);
    EXPECT_EQ(count(d.text, "A1=1"), 1u);
    EXPECT_TRUE(warned(d, "same data as an earlier sheet"));
}

TEST_F(HostileDocumentsTest, WorkbookGridPastTheCellBudgetFailsLoudly) {
    // Every row's only cell is in the last column, so each row would be 16384 wide.
    std::string sheet;
    for (int r = 1; r <= 700; ++r) sheet += "<row r=\"" + std::to_string(r) + "\"><c r=\"XFD" + std::to_string(r) + "\"><v>1</v></c></row>";
    const std::string path = write("wide.xlsx", workbook(sheet));
    try {
        readWorkbook(path);
        FAIL() << "a grid past the cell budget must fail";
    } catch (const std::runtime_error& e) {
        EXPECT_NE(std::string(e.what()).find("cells"), std::string::npos) << e.what();
    }
}
