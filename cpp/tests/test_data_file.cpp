// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/data_file.h>

#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

using namespace gaia;
namespace fs = std::filesystem;

namespace {

class DataFileTest : public ::testing::Test {
protected:
    fs::path dir_;
    void SetUp() override {
        dir_ = fs::temp_directory_path() / "gaia_data_file_test";
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
};

}  // namespace

TEST(ParseNumberTest, UnderstandsTheShapesSpreadsheetsUse) {
    double v = 0;
    ASSERT_TRUE(parseNumber("1,234.50", v));
    EXPECT_DOUBLE_EQ(v, 1234.5);
    ASSERT_TRUE(parseNumber("1.234,50", v));
    EXPECT_DOUBLE_EQ(v, 1234.5);
    ASSERT_TRUE(parseNumber("12,5", v));
    EXPECT_DOUBLE_EQ(v, 12.5);
    ASSERT_TRUE(parseNumber("$ 99", v));
    EXPECT_DOUBLE_EQ(v, 99);
    ASSERT_TRUE(parseNumber("(250.00)", v));
    EXPECT_DOUBLE_EQ(v, -250);
    ASSERT_TRUE(parseNumber("\xE2\x82\xAC" "1.000,00", v));  // €1.000,00
    EXPECT_DOUBLE_EQ(v, 1000);
    ASSERT_TRUE(parseNumber("45%", v));
    EXPECT_DOUBLE_EQ(v, 45);
    EXPECT_FALSE(parseNumber("N/A", v));
    EXPECT_FALSE(parseNumber("", v));
    EXPECT_FALSE(parseNumber("12abc", v));
}

TEST_F(DataFileTest, GroupByIsSortedWithTopOneAndShares) {
    const std::string f = write("sales.csv",
                                "date,region,product,revenue\n"
                                "2025-01-05,North,Widget,100\n"
                                "2025-01-20,South,Gadget,300\n"
                                "2025-02-03,North,Gadget,\"1,200\"\n"
                                "2025-03-11,East,Widget,400\n");
    const json r = analyzeDataFile(f, "summary", "", "region", "");
    ASSERT_EQ(r["status"], "success") << r.dump(2);
    EXPECT_EQ(r["row_count"], 4);
    EXPECT_EQ(r["summary"]["revenue"]["sum"], 2000);
    EXPECT_EQ(r["group_by_results"][0]["group"], "North");
    EXPECT_EQ(r["group_by_results"][0]["totals"]["revenue"], 1300);
    EXPECT_DOUBLE_EQ(r["group_by_results"][0]["share_of_total"].get<double>(), 65.0);
    EXPECT_EQ(r["top_1"]["group"], "North");
    EXPECT_EQ(r["top_1"]["value"], 1300);
    EXPECT_EQ(r["column_types"]["date"], "date");
}

TEST_F(DataFileTest, DateRangeQuarterMonthAndRange) {
    const std::string f = write("tx.csv",
                                "Date,Store,Amount\n"
                                "2025-03-31,A,10\n2025-04-01,A,20\n2025-06-30,B,30\n2025-07-01,B,40\n");
    EXPECT_EQ(analyzeDataFile(f, "summary", "", "", "2025-Q2")["summary"]["Amount"]["sum"], 50);
    EXPECT_EQ(analyzeDataFile(f, "summary", "", "", "Q2'25")["row_count"], 2);
    EXPECT_EQ(analyzeDataFile(f, "summary", "", "", "2025-07")["summary"]["Amount"]["sum"], 40);
    EXPECT_EQ(analyzeDataFile(f, "summary", "", "", "2025-04 to 2025-07")["summary"]["Amount"]["sum"], 90);
    EXPECT_EQ(analyzeDataFile(f, "summary", "", "", "next quarter")["status"], "error");
    const json trends = analyzeDataFile(f, "trends", "", "", "");
    ASSERT_TRUE(trends.contains("trends"));
    EXPECT_EQ(trends["trends"]["by_month"].size(), 4u);
    EXPECT_EQ(trends["trends"]["direction"], "up");
}

TEST_F(DataFileTest, SemicolonFileWithGermanDecimals) {
    const std::string f = write("ledger.csv", "Datum;Konto;Betrag\n01.09.2025;Miete;1.250,00\n02.09.2025;Strom;89,90\n");
    const json r = analyzeDataFile(f, "summary", "", "", "");
    ASSERT_EQ(r["status"], "success") << r.dump(2);
    EXPECT_EQ(r["delimiter"], ";");
    EXPECT_DOUBLE_EQ(r["summary"]["Betrag"]["sum"].get<double>(), 1339.9);
    EXPECT_DOUBLE_EQ(r["summary"]["Betrag"]["max"].get<double>(), 1250);
}

TEST_F(DataFileTest, Utf16SurveyDecodes) {
    std::string utf16 = "\xFF\xFE";
    for (char c : std::string("team,score\nRed,7\nBlue,3\n")) {
        utf16.push_back(c);
        utf16.push_back('\0');
    }
    const json r = analyzeDataFile(write("survey.csv", utf16), "summary", "", "team", "");
    ASSERT_EQ(r["status"], "success") << r.dump(2);
    EXPECT_EQ(r["encoding"], "utf-16le");
    EXPECT_DOUBLE_EQ(r["summary"]["score"]["mean"].get<double>(), 5.0);
    EXPECT_EQ(r["group_by_results"].back()["group"], "Blue");
}

TEST_F(DataFileTest, HeaderOnlyAndMessyRowsAreReportedNotHidden) {
    EXPECT_EQ(analyzeDataFile(write("empty.csv", "id,amount\n"), "summary", "", "", "")["row_count"], 0);
    const std::string f = write("messy.csv",
                                "rep,region,amount\n"
                                "Ann,East,100\n"
                                "Bob, east ,N/A\n"
                                "rep,region,amount\n"
                                "Cy,West,50,extra\n"
                                "Total,,150\n");
    const json r = analyzeDataFile(f, "summary", "", "", "");
    ASSERT_EQ(r["status"], "success") << r.dump(2);
    EXPECT_EQ(r["summary"]["amount"]["sum"], 150);
    EXPECT_EQ(r["summary"]["amount"]["unparsed_values"], json::array({"N/A"}));
    EXPECT_EQ(r["summary"]["region"]["similar_values"], json::array({json::array({"East", "east"})}));
    const std::string warnings = r["warnings"].dump();
    EXPECT_NE(warnings.find("repeated header"), std::string::npos);
    EXPECT_NE(warnings.find("different number of fields"), std::string::npos);
    EXPECT_NE(warnings.find("total/subtotal"), std::string::npos);
}

TEST_F(DataFileTest, WorkbookSkipsTitleLinesAndTotalRow) {
    const std::string f =
        std::string(GAIA_REPO_FIXTURES_DIR) + "/../../eval/corpus/real_world/spreadsheets/company_financials_2024.xlsx";
    const json r = analyzeDataFile(f, "summary", "Revenue", "", "");
    ASSERT_EQ(r["status"], "success") << r.dump(2);
    // openpyxl: the four quarters sum to 156,230,000; the FY total row is not counted again.
    EXPECT_EQ(r["summary"]["Revenue"]["sum"], 156230000);
    EXPECT_EQ(r["summary"]["Revenue"]["max"], 47320000);
}

TEST_F(DataFileTest, UnknownColumnsAndTypesFailLoudly) {
    const std::string f = write("a.csv", "x,y\n1,2\n");
    EXPECT_EQ(analyzeDataFile(f, "summary", "", "nope", "")["status"], "error");
    EXPECT_EQ(analyzeDataFile(f, "summary", "zzz", "", "")["status"], "error");
    EXPECT_EQ(analyzeDataFile(f, "spending", "", "", "")["status"], "error");
    EXPECT_EQ(analyzeDataFile(write("old.xls", "x"), "summary", "", "", "")["status"], "error");
}

TEST(ParseNumberTest, UnicodeMinusSignIsNegative) {
    double v = 0;
    ASSERT_TRUE(parseNumber("\xE2\x88\x92" "5.5", v));  // U+2212
    EXPECT_DOUBLE_EQ(v, -5.5);
}

TEST_F(DataFileTest, SingleColumnFileHasAHeaderAndRows) {
    std::string csv = "value\n";
    for (int i = 1; i <= 25; ++i) csv += std::to_string(i) + "\n";
    const json r = analyzeDataFile(write("one.csv", csv), "summary", "", "", "");
    ASSERT_EQ(r["status"], "success") << r.dump(2);
    EXPECT_EQ(r["columns"], json::array({"value"}));
    EXPECT_EQ(r["row_count"], 25);
    EXPECT_EQ(r["summary"]["value"]["sum"], 325);
}

TEST_F(DataFileTest, IndexColumnWithBlankHeaderKeepsTheHeaderRow) {
    // pandas' to_csv() writes its index under an empty header cell.
    const DataTable t = readDataTable(write("pandas.csv", ",value\n0,5\n1,6\n"));
    EXPECT_EQ(t.columns, (std::vector<std::string>{"column_1", "value"}));
    ASSERT_EQ(t.rows.size(), 2u);
    EXPECT_EQ(t.rows[0][1], "5");
}

TEST_F(DataFileTest, TitleLineAboveTheHeaderIsStillSkipped) {
    const DataTable t = readDataTable(write("titled.csv", "Quarterly sales,,\nregion,rep,amount\nEast,Ann,5\n"));
    EXPECT_EQ(t.columns, (std::vector<std::string>{"region", "rep", "amount"}));
    ASSERT_EQ(t.rows.size(), 1u);
}

TEST_F(DataFileTest, RepeatedColumnNamesAreKeptApart) {
    const json r = analyzeDataFile(write("dup.csv", "amount,amount,amount.1\n1,2,3\n4,5,6\n"), "summary", "", "", "");
    ASSERT_EQ(r["status"], "success") << r.dump(2);
    EXPECT_EQ(r["columns"], json::array({"amount", "amount.2", "amount.1"}));
    EXPECT_EQ(r["summary"]["amount"]["sum"], 5);
    EXPECT_EQ(r["summary"]["amount.2"]["sum"], 7);
    EXPECT_NE(r["warnings"].dump().find("amount -> amount.2"), std::string::npos) << r.dump(2);
}

TEST_F(DataFileTest, Utf16LoneSurrogateBecomesReplacementCharacter) {
    std::string utf16 = "\xFF\xFE";
    for (char c : std::string("name,n\nA")) {
        utf16.push_back(c);
        utf16.push_back('\0');
    }
    utf16 += std::string("\x00\xD8", 2);  // a high surrogate with no low one after it
    for (char c : std::string(",1\n")) {
        utf16.push_back(c);
        utf16.push_back('\0');
    }
    const DataTable t = readDataTable(write("lone.csv", utf16));
    ASSERT_EQ(t.rows.size(), 1u);
    EXPECT_EQ(t.rows[0][0], "A\xEF\xBF\xBD");
    EXPECT_EQ(t.rows[0][1], "1");
}

TEST_F(DataFileTest, GroupWithANanTotalSortsLast) {
    const std::string f = write("nan.csv",
                                "team,score\n"
                                "A,1e999\nA,-1e999\n"
                                "B,5\nC,3\nD,4\nE,2\n");
    const json r = analyzeDataFile(f, "summary", "", "team", "");
    ASSERT_EQ(r["status"], "success") << r.dump(2);
    std::vector<std::string> order;
    for (const auto& g : r["group_by_results"]) order.push_back(g["group"]);
    EXPECT_EQ(order, (std::vector<std::string>{"B", "D", "C", "E", "A"}));
}
