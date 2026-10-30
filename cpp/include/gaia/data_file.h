// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// analyze_data_file: exact totals, group-bys and date filters over CSV, TSV
// and XLSX, so the model reads numbers instead of adding them up itself.
// Port of the Python tool in src/gaia/agents/tools/file_tools.py; same
// arguments and the same result keys (summary, group_by_results, top_1, ...).

#pragma once

#include <string>
#include <vector>

#include "gaia/export.h"
#include "gaia/types.h"

namespace gaia {

/// A parsed table: header names and string cells.
struct GAIA_API DataTable {
    std::vector<std::string> columns;
    std::vector<std::vector<std::string>> rows;
    std::string delimiter;       ///< "," ";" "\t" "|" for text files, empty for xlsx
    std::string encoding;        ///< "utf-8", "utf-16le", "utf-16be", "cp1252"
    std::vector<std::string> warnings;
};

/// Read a CSV/TSV/TXT (delimiter and encoding sniffed) or the first sheet of
/// an XLSX that has data.
/// @throws std::runtime_error naming the file when it cannot be read as a table.
GAIA_API DataTable readDataTable(const std::string& path);

/// A cell as a number: thousands separators, currency symbols, a percent sign
/// and accounting parentheses are understood; "1.234,56" reads as 1234.56.
/// @return false when the text is not a number.
GAIA_API bool parseNumber(const std::string& text, double& out);

/// The tool itself. @p analysisType is summary | trends | full;
/// @p columns, @p groupBy, @p dateRange as in the Python tool.
GAIA_API json analyzeDataFile(const std::string& path, const std::string& analysisType,
                              const std::string& columns, const std::string& groupBy,
                              const std::string& dateRange);

}  // namespace gaia
