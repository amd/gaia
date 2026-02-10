#!/usr/bin/env python3
"""
PDF Page Extractor

Extracts specific pages or page ranges from a PDF document and saves them to a new file.

Usage:
    python pdf_extract_pages.py input.pdf output.pdf --pages 1-5
    python pdf_extract_pages.py input.pdf output.pdf --pages 1,3,5-7,10
    python pdf_extract_pages.py input.pdf output.pdf --pages 5
"""

import argparse
import sys
from pathlib import Path

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    print("Error: pypdf library not found. Install it with: pip install pypdf")
    sys.exit(1)


def parse_page_range(range_str):
    """
    Parse a page range string into a list of page numbers.

    Supports formats like:
    - "5" -> [5]
    - "1-5" -> [1, 2, 3, 4, 5]
    - "1,3,5" -> [1, 3, 5]
    - "1-3,5,7-9" -> [1, 2, 3, 5, 7, 8, 9]

    Args:
        range_str: String representing page numbers/ranges

    Returns:
        List of page numbers (1-indexed)
    """
    pages = set()

    # Split by commas to handle multiple ranges/numbers
    parts = range_str.split(',')

    for part in parts:
        part = part.strip()

        if '-' in part:
            # Handle range (e.g., "1-5")
            try:
                start, end = part.split('-')
                start = int(start.strip())
                end = int(end.strip())

                if start > end:
                    raise ValueError(f"Invalid range: {part} (start > end)")

                pages.update(range(start, end + 1))
            except ValueError as e:
                raise ValueError(f"Invalid page range '{part}': {e}")
        else:
            # Handle single page number
            try:
                page_num = int(part)
                if page_num < 1:
                    raise ValueError(f"Page numbers must be >= 1, got {page_num}")
                pages.add(page_num)
            except ValueError:
                raise ValueError(f"Invalid page number: '{part}'")

    return sorted(list(pages))


def extract_pages(input_pdf, output_pdf, page_numbers):
    """
    Extract specific pages from a PDF and save to a new file.

    Args:
        input_pdf: Path to input PDF file
        output_pdf: Path to output PDF file
        page_numbers: List of page numbers to extract (1-indexed)
    """
    # Read the input PDF
    reader = PdfReader(input_pdf)
    total_pages = len(reader.pages)

    # Validate page numbers
    invalid_pages = [p for p in page_numbers if p < 1 or p > total_pages]
    if invalid_pages:
        raise ValueError(
            f"Invalid page numbers {invalid_pages}. "
            f"PDF has {total_pages} pages (valid range: 1-{total_pages})"
        )

    # Create a PDF writer
    writer = PdfWriter()

    # Add requested pages (convert from 1-indexed to 0-indexed)
    for page_num in page_numbers:
        page_index = page_num - 1
        writer.add_page(reader.pages[page_index])

    # Write to output file
    with open(output_pdf, 'wb') as output_file:
        writer.write(output_file)

    print(f"✓ Extracted {len(page_numbers)} page(s) from '{input_pdf}'")
    print(f"✓ Saved to '{output_pdf}'")
    print(f"  Pages extracted: {', '.join(map(str, page_numbers))}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract specific pages from a PDF document",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Extract pages 1-5
  %(prog)s input.pdf output.pdf --pages 1-5

  # Extract specific pages
  %(prog)s input.pdf output.pdf --pages 1,3,5,7

  # Extract mixed ranges and individual pages
  %(prog)s input.pdf output.pdf --pages 1-3,5,7-9,15

  # Extract a single page
  %(prog)s input.pdf output.pdf --pages 10

Note: Page numbers are 1-indexed (first page is 1, not 0)
        """
    )

    parser.add_argument(
        'input_pdf',
        type=str,
        help='Path to the input PDF file'
    )

    parser.add_argument(
        'output_pdf',
        type=str,
        help='Path to the output PDF file'
    )

    parser.add_argument(
        '-p', '--pages',
        type=str,
        required=True,
        help='Page range(s) to extract (e.g., "1-5", "1,3,5", "1-3,5,7-9")'
    )

    args = parser.parse_args()

    # Validate input file exists
    input_path = Path(args.input_pdf)
    if not input_path.exists():
        print(f"Error: Input file '{args.input_pdf}' not found")
        sys.exit(1)

    if not input_path.suffix.lower() == '.pdf':
        print(f"Error: Input file must be a PDF (got '{input_path.suffix}')")
        sys.exit(1)

    # Validate output file doesn't exist (or warn)
    output_path = Path(args.output_pdf)
    if output_path.exists():
        response = input(f"Warning: '{args.output_pdf}' already exists. Overwrite? (y/N): ")
        if response.lower() != 'y':
            print("Aborted.")
            sys.exit(0)

    try:
        # Parse page range
        page_numbers = parse_page_range(args.pages)

        if not page_numbers:
            print("Error: No pages specified")
            sys.exit(1)

        # Extract pages
        extract_pages(args.input_pdf, args.output_pdf, page_numbers)

    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error processing PDF: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
