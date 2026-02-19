#!/usr/bin/env python3
"""
PDF to Images Converter

Extracts pages from PDF and saves as JPEG images for visual inspection.

Usage:
    python util/pdf_to_images.py input.pdf output_dir/
    python util/pdf_to_images.py input.pdf output_dir/ --pages 1-5
    python util/pdf_to_images.py input.pdf output_dir/ --pages 10 --scale 3.0
"""

import argparse
import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    print("Error: PyMuPDF required. Install: pip install pymupdf")
    sys.exit(1)


def parse_page_range(range_str, total_pages):
    """Parse page range string."""
    if range_str.lower() == 'all':
        return list(range(1, total_pages + 1))
    if '-' in range_str:
        start, end = range_str.split('-')
        return list(range(int(start.strip()), int(end.strip()) + 1))
    return [int(range_str)]


def pdf_to_images(pdf_path, output_dir, page_range='all', scale=2.0, quality=85):
    """
    Convert PDF pages to JPEG images.

    Args:
        pdf_path: Input PDF file
        output_dir: Output directory for images
        page_range: Pages to extract ('all', '1-5', '3')
        scale: Resolution scale (2.0 = 2x, higher = better quality)
        quality: JPEG quality 1-100 (default: 85)
    """
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)

    # Validate
    if not pdf_path.exists():
        print(f"Error: PDF not found: {pdf_path}")
        sys.exit(1)

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Open PDF
    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)

    print(f"📄 PDF: {pdf_path.name}")
    print(f"📊 Total pages: {total_pages}")

    # Parse page range
    pages_to_extract = parse_page_range(page_range, total_pages)
    print(f"📋 Extracting pages: {pages_to_extract}")
    print(f"📁 Output: {output_dir}\n")

    # Extract pages
    for page_num in pages_to_extract:
        page_idx = page_num - 1  # 0-indexed

        print(f"   Page {page_num}/{total_pages}...", end=" ")

        # Get page
        page = doc[page_idx]

        # Render at specified scale
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat)

        # Save as JPEG
        output_file = output_dir / f"page_{page_num:04d}.jpg"
        pix.save(str(output_file), "jpeg", jpg_quality=quality)

        # Get file size
        size_kb = output_file.stat().st_size / 1024
        print(f"✓ {pix.width}x{pix.height} ({size_kb:.1f} KB)")

    doc.close()

    print(f"\n✅ Extracted {len(pages_to_extract)} page(s) to {output_dir}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract PDF pages as JPEG images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Extract all pages
  %(prog)s document.pdf images/

  # Extract specific range
  %(prog)s document.pdf images/ --pages 1-10

  # Extract single page with high quality
  %(prog)s document.pdf images/ --pages 5 --scale 3.0 --quality 95

  # Extract for visual inspection
  %(prog)s driver-logs.pdf inspection/ --pages 9-14
        """
    )

    parser.add_argument('input_pdf', help='Input PDF file')
    parser.add_argument('output_dir', help='Output directory for images')
    parser.add_argument('--pages', default='all', help='Page range: "all", "1-10", "5"')
    parser.add_argument('--scale', type=float, default=2.0, help='Resolution scale (default: 2.0)')
    parser.add_argument('--quality', type=int, default=85, help='JPEG quality 1-100 (default: 85)')

    args = parser.parse_args()

    try:
        pdf_to_images(
            args.input_pdf,
            args.output_dir,
            args.pages,
            args.scale,
            args.quality,
        )
    except KeyboardInterrupt:
        print("\n⚠️  Interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
