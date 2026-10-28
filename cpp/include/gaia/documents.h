// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Text out of the documents people hand an agent: PDF, DOCX, XLSX and PPTX.
// What read_file pages through when the file is not plain text.
//
// PDF text comes from the page content streams (Flate-compressed or not),
// decoded through each font's ToUnicode map, else WinAnsi. A page that holds
// only images — a scan — has no text here, and is reported as such rather
// than passed off as empty.

#pragma once

#include <map>
#include <string>
#include <vector>

#include "gaia/export.h"

namespace gaia {

struct GAIA_API DocumentText {
    std::string kind;                    ///< "pdf", "docx", "xlsx" or "pptx"
    std::string text;                    ///< all of it, with "--- Page N ---" style section markers
    int sections = 0;                    ///< pages, sheets or slides
    std::vector<int> emptySections;      ///< 1-based pages/slides with no extractable text
    std::vector<std::string> warnings;   ///< parts that could not be read, and why
};

/// One worksheet as a grid of cell texts, row-major, columns aligned by letter.
struct GAIA_API Worksheet {
    std::string name;
    std::vector<std::vector<std::string>> rows;
};

/// Every sheet of an .xlsx workbook, in workbook order.
/// Date-formatted cells read as openpyxl prints them ("2025-01-05 00:00:00").
/// @throws std::runtime_error when the file is not a readable workbook, or its
///         grid spans more cells than GAIA reads as a table.
GAIA_API std::vector<Worksheet> readWorkbook(const std::string& path);

/// The largest image drawn on each of @p pages (1-based) of a PDF, as JPEG or PNG
/// bytes, for reading scanned pages with a vision model. Pages without an image
/// in a supported encoding are absent from the result.
GAIA_API std::map<int, std::string> extractPageImages(const std::string& pdfPath, const std::vector<int>& pages);

/// True for the extensions extractDocumentText() reads (case-insensitive, with the dot).
GAIA_API bool isDocumentExtension(const std::string& extension);

/// @throws std::runtime_error naming the file and what is wrong with it
///         (not a zip, encrypted PDF, no readable text structure).
GAIA_API DocumentText extractDocumentText(const std::string& path);

}  // namespace gaia
