#!/bin/sh

# find_pdfs.sh
# Finds all PDF files starting from the current directory.

set -euo pipefail

# Define the starting directory. Using '.' searches the current directory and all subdirectories.
START_DIR="."

echo "Searching for PDF files starting from: $START_DIR"

# Use find to locate files ending in .pdf.
# -type f: ensures only files are matched.
# -iname: performs a case-insensitive match for '.pdf'.
find "$START_DIR" -type f -iname "*.pdf"