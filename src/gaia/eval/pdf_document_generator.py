# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import argparse
import json
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

from gaia.eval.claude import ClaudeClient
from gaia.eval.config import DEFAULT_CLAUDE_MODEL
from gaia.logger import get_logger


class PDFDocumentGenerator:
    """Generates example PDF documents for testing PDF processing and summarization."""

    def __init__(self, claude_model=None, max_tokens=8192):
        self.log = get_logger(__name__)

        # Initialize Claude client for dynamic content generation
        if claude_model is None:
            claude_model = DEFAULT_CLAUDE_MODEL
        try:
            self.claude_client = ClaudeClient(model=claude_model, max_tokens=max_tokens)
            self.log.info(f"Initialized Claude client with model: {claude_model}")
        except Exception as e:
            self.log.error(f"Failed to initialize Claude client: {e}")
            raise ValueError(
                f"Could not initialize Claude client. Please ensure ANTHROPIC_API_KEY is set. Error: {e}"
            )

        # Document templates with different use cases
        self.document_templates = {
            "technical_spec": {
                "description": "Technical specification document",
                "content_type": "Technical documentation with architecture details",
                "sections": [
                    "Overview",
                    "System Architecture",
                    "Technical Requirements",
                    "API Specifications",
                    "Security Considerations",
                    "Performance Metrics",
                ],
                "context": "A detailed technical specification document for a software system or feature, including architecture diagrams descriptions, API contracts, and implementation details.",
            },
            "business_proposal": {
                "description": "Business proposal and investment pitch",
                "content_type": "Business strategy and financial projections",
                "sections": [
                    "Executive Summary",
                    "Business Opportunity",
                    "Market Analysis",
                    "Solution Overview",
                    "Financial Projections",
                    "Implementation Timeline",
                ],
                "context": "A comprehensive business proposal document outlining a business opportunity, market analysis, solution description, and financial forecasts.",
            },
            "research_report": {
                "description": "Research findings and analysis report",
                "content_type": "Research methodology and findings",
                "sections": [
                    "Abstract",
                    "Introduction",
                    "Methodology",
                    "Results and Analysis",
                    "Discussion",
                    "Conclusions and Recommendations",
                ],
                "context": "An academic or industry research report presenting research methodology, findings, data analysis, and conclusions.",
            },
            "project_plan": {
                "description": "Project management and planning document",
                "content_type": "Project scope, timeline, and deliverables",
                "sections": [
                    "Project Overview",
                    "Objectives and Success Criteria",
                    "Scope and Deliverables",
                    "Timeline and Milestones",
                    "Resource Allocation",
                    "Risk Management",
                ],
                "context": "A comprehensive project plan outlining objectives, scope, timeline, resources, and risk mitigation strategies.",
            },
            "policy_document": {
                "description": "Corporate policy and procedures manual",
                "content_type": "Policies, procedures, and compliance guidelines",
                "sections": [
                    "Purpose and Scope",
                    "Policy Statement",
                    "Roles and Responsibilities",
                    "Procedures and Guidelines",
                    "Compliance and Enforcement",
                    "Review and Updates",
                ],
                "context": "A formal policy document defining corporate standards, procedures, responsibilities, and compliance requirements.",
            },
            "white_paper": {
                "description": "Industry white paper on emerging technology",
                "content_type": "Thought leadership and technical insights",
                "sections": [
                    "Executive Summary",
                    "Industry Challenges",
                    "Technology Overview",
                    "Use Cases and Applications",
                    "Implementation Best Practices",
                    "Future Outlook",
                ],
                "context": "A thought leadership white paper exploring emerging technologies, industry trends, and practical applications.",
            },
            "user_manual": {
                "description": "Product user manual and documentation",
                "content_type": "User instructions and troubleshooting guide",
                "sections": [
                    "Introduction",
                    "Getting Started",
                    "Features and Functionality",
                    "Step-by-Step Instructions",
                    "Troubleshooting",
                    "FAQ and Support",
                ],
                "context": "A comprehensive user manual providing installation instructions, feature explanations, and troubleshooting guidance.",
            },
            "financial_report": {
                "description": "Quarterly financial report and analysis",
                "content_type": "Financial statements and performance analysis",
                "sections": [
                    "Executive Summary",
                    "Financial Highlights",
                    "Revenue Analysis",
                    "Expense Breakdown",
                    "Cash Flow Statement",
                    "Future Outlook",
                ],
                "context": "A quarterly financial report presenting financial performance, key metrics, and business analysis.",
            },
        }

    def _estimate_tokens(self, text):
        """Rough token estimation (approximately 4 characters per token)."""
        return len(text) // 4

    def _generate_document_content_with_claude(self, doc_type, target_tokens):
        """Generate document content using Claude based on document type and target token count."""
        if doc_type not in self.document_templates:
            raise ValueError(f"Unknown document type: {doc_type}")

        template = self.document_templates[doc_type]

        # Create a detailed prompt for Claude
        # Calculate tokens per section for guidance
        num_sections = len(template["sections"]) + 1  # +1 for title/metadata
        tokens_per_section = target_tokens // num_sections

        prompt = f"""Generate realistic professional document content for the following scenario:

Document Type: {template['description']}
Content Type: {template['content_type']}
Context: {template['context']}
Required Sections: {', '.join(template['sections'])}

CRITICAL CONSTRAINT - LENGTH LIMIT:
Target Length: MAXIMUM {target_tokens} tokens (approximately {target_tokens * 4} characters)
Budget per section: Approximately {tokens_per_section} tokens each
YOU MUST NOT EXCEED {target_tokens} tokens total. Keep content concise but professional.

Please create professional document content that includes:

1. **Document Title and Metadata** (Brief - ~{tokens_per_section//2} tokens)
   - Professional title
   - Author(s) or organization
   - Date
   - Version/revision (if applicable)

2. **All Required Sections** (listed above) with (~{tokens_per_section} tokens each):
   - Section headings
   - Professional, concise content for each section
   - Key data, metrics, or technical details as appropriate
   - Professional terminology and tone
   - Be concise while maintaining quality

3. **Content Requirements**:
   - Write in professional business/technical style
   - Include specific details, numbers, dates where relevant
   - Use realistic company names, products, metrics
   - Maintain consistency throughout the document
   - Balance quality with brevity - DO NOT exceed token limit

4. **Formatting**:
   - Use clear section headers
   - Include bullet points or numbered lists where appropriate
   - Maintain professional document structure
   - Use paragraph breaks for readability

REMINDER: Stay within {target_tokens} tokens total. Prioritize meeting the length constraint.

Generate ONLY the document content with the sections listed above."""

        try:
            # Generate the content using Claude with usage tracking
            self.log.info(
                f"Generating {doc_type} document with Claude (target: {target_tokens} tokens)"
            )
            response = self.claude_client.get_completion_with_usage(prompt)

            generated_content = (
                response["content"][0].text
                if isinstance(response["content"], list)
                else response["content"]
            )
            actual_tokens = self._estimate_tokens(generated_content)

            self.log.info(
                f"Generated document: {actual_tokens} tokens (target: {target_tokens})"
            )

            return generated_content, response["usage"], response["cost"]

        except Exception as e:
            self.log.error(f"Error generating document with Claude: {e}")
            raise RuntimeError(f"Failed to generate document for {doc_type}: {e}")

    def _extend_content_with_claude(
        self, base_content, target_tokens, doc_type, current_usage, current_cost
    ):
        """Extend existing content to reach target token count using Claude."""
        current_tokens = self._estimate_tokens(base_content)

        if current_tokens >= target_tokens:
            return base_content, current_usage, current_cost

        needed_tokens = target_tokens - current_tokens
        template = self.document_templates[doc_type]

        extension_prompt = f"""Continue the following professional document to make it approximately {needed_tokens} more tokens longer.

Current document:
{base_content}

Please add more realistic content that:
1. Maintains the same professional tone and context
2. Continues naturally from where it left off
3. Adds approximately {needed_tokens} more tokens of content
4. Includes meaningful details relevant to {template['description']}
5. Can expand existing sections or add additional subsections
6. Maintains professional document format and structure

Generate only the additional content (without repeating the existing content)."""

        try:
            self.log.info(f"Extending document by ~{needed_tokens} tokens")
            response = self.claude_client.get_completion_with_usage(extension_prompt)

            extension_content = (
                response["content"][0].text
                if isinstance(response["content"], list)
                else response["content"]
            )
            extended_content = base_content + "\n\n" + extension_content

            # Combine usage and cost data
            total_usage = {
                "input_tokens": current_usage["input_tokens"]
                + response["usage"]["input_tokens"],
                "output_tokens": current_usage["output_tokens"]
                + response["usage"]["output_tokens"],
                "total_tokens": current_usage["total_tokens"]
                + response["usage"]["total_tokens"],
            }

            total_cost = {
                "input_cost": current_cost["input_cost"]
                + response["cost"]["input_cost"],
                "output_cost": current_cost["output_cost"]
                + response["cost"]["output_cost"],
                "total_cost": current_cost["total_cost"]
                + response["cost"]["total_cost"],
            }

            actual_tokens = self._estimate_tokens(extended_content)
            self.log.info(f"Extended document to {actual_tokens} tokens")

            return extended_content, total_usage, total_cost

        except Exception as e:
            self.log.error(f"Error extending document with Claude: {e}")
            # Return original content if extension fails
            return base_content, current_usage, current_cost

    def _create_pdf_from_content(self, content, output_path, doc_title):
        """Create a PDF file from text content using ReportLab."""
        try:
            # Create PDF document
            doc = SimpleDocTemplate(
                str(output_path),
                pagesize=letter,
                rightMargin=72,
                leftMargin=72,
                topMargin=72,
                bottomMargin=72,
            )

            # Container for the 'Flowable' objects
            story = []

            # Define styles
            styles = getSampleStyleSheet()

            # Custom title style
            title_style = ParagraphStyle(
                "CustomTitle",
                parent=styles["Heading1"],
                fontSize=18,
                textColor=colors.HexColor("#1a1a1a"),
                spaceAfter=30,
                alignment=1,  # Center
                fontName="Helvetica-Bold",
            )

            # Custom heading styles
            heading_style = ParagraphStyle(
                "CustomHeading",
                parent=styles["Heading2"],
                fontSize=14,
                textColor=colors.HexColor("#2a2a2a"),
                spaceAfter=12,
                spaceBefore=12,
                fontName="Helvetica-Bold",
            )

            # Custom body style
            body_style = ParagraphStyle(
                "CustomBody",
                parent=styles["Normal"],
                fontSize=11,
                leading=16,
                textColor=colors.HexColor("#333333"),
                spaceAfter=12,
            )

            # Add title
            story.append(Paragraph(doc_title, title_style))
            story.append(Spacer(1, 0.2 * inch))

            # Process content - split by lines and identify sections
            lines = content.split("\n")
            current_paragraph = []

            for line in lines:
                line = line.strip()

                if not line:
                    # Empty line - end current paragraph if any
                    if current_paragraph:
                        para_text = " ".join(current_paragraph)
                        story.append(Paragraph(para_text, body_style))
                        current_paragraph = []
                    story.append(Spacer(1, 0.1 * inch))
                    continue

                # Check if line is a heading (simple heuristic: short line ending with : or all caps)
                is_heading = False
                if len(line) < 100 and (
                    line.endswith(":")
                    or (line.isupper() and len(line.split()) <= 6)
                    or line.startswith("#")
                ):
                    is_heading = True
                    # Remove markdown # symbols
                    line = line.lstrip("#").strip()
                    # Remove trailing colon for cleaner headings
                    if line.endswith(":"):
                        line = line[:-1]

                if is_heading:
                    # Flush current paragraph
                    if current_paragraph:
                        para_text = " ".join(current_paragraph)
                        story.append(Paragraph(para_text, body_style))
                        current_paragraph = []

                    # Add heading
                    story.append(Paragraph(line, heading_style))
                else:
                    # Add to current paragraph
                    current_paragraph.append(line)

            # Flush any remaining paragraph
            if current_paragraph:
                para_text = " ".join(current_paragraph)
                story.append(Paragraph(para_text, body_style))

            # Build PDF
            doc.build(story)
            self.log.info(f"Created PDF: {output_path}")

        except Exception as e:
            self.log.error(f"Error creating PDF: {e}")
            raise

    def generate_document(self, doc_type, target_tokens=2000):
        """Generate a single PDF document of specified type and approximate token count using Claude."""
        if doc_type not in self.document_templates:
            raise ValueError(f"Unknown document type: {doc_type}")

        template = self.document_templates[doc_type]

        try:
            # Generate document content with Claude
            content, usage, cost = self._generate_document_content_with_claude(
                doc_type, target_tokens
            )
            actual_tokens = self._estimate_tokens(content)

            # If we're significantly under target, try to extend
            if actual_tokens < target_tokens * 0.8:  # If less than 80% of target
                self.log.info(
                    f"Document too short ({actual_tokens} tokens), extending to reach target"
                )
                content, usage, cost = self._extend_content_with_claude(
                    content, target_tokens, doc_type, usage, cost
                )
                actual_tokens = self._estimate_tokens(content)

            # Add metadata
            metadata = {
                "doc_type": doc_type,
                "description": template["description"],
                "content_type": template["content_type"],
                "sections": template["sections"],
                "estimated_tokens": actual_tokens,
                "target_tokens": target_tokens,
                "generated_date": datetime.now().isoformat(),
                "claude_model": self.claude_client.model,
                "claude_usage": usage,
                "claude_cost": cost,
            }

            return content, metadata

        except Exception as e:
            self.log.error(f"Failed to generate document for {doc_type}: {e}")
            raise

    def generate_document_set(self, output_dir, target_tokens=2000, count_per_type=1):
        """Generate a set of PDF documents and save them to the output directory."""
        output_dir = Path(output_dir)
        # Create pdfs subdirectory for organized output
        pdfs_dir = output_dir / "pdfs"
        pdfs_dir.mkdir(parents=True, exist_ok=True)
        output_dir = pdfs_dir  # Use pdfs subdirectory as base

        generated_files = []
        all_metadata = []
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        total_cost = {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}

        for doc_type in self.document_templates.keys():
            for i in range(count_per_type):
                self.log.info(f"Generating {doc_type} document {i+1}/{count_per_type}")

                # Generate document content
                content, metadata = self.generate_document(doc_type, target_tokens)

                # Create filename
                if count_per_type == 1:
                    filename = f"{doc_type}.pdf"
                    txt_filename = f"{doc_type}.txt"
                else:
                    filename = f"{doc_type}_{i+1}.pdf"
                    txt_filename = f"{doc_type}_{i+1}.txt"

                # Create document title
                doc_title = self.document_templates[doc_type]["description"].title()

                # Save PDF file
                pdf_path = output_dir / filename
                self._create_pdf_from_content(content, pdf_path, doc_title)

                # Also save text content for reference
                txt_path = output_dir / txt_filename
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(content)

                # Update metadata with file info
                metadata["pdf_filename"] = filename
                metadata["txt_filename"] = txt_filename
                metadata["pdf_path"] = str(pdf_path)
                metadata["txt_path"] = str(txt_path)
                metadata["file_size_bytes"] = len(content.encode("utf-8"))

                generated_files.append(str(pdf_path))
                all_metadata.append(metadata)

                # Accumulate usage and cost
                usage = metadata["claude_usage"]
                cost = metadata["claude_cost"]
                total_usage["input_tokens"] += usage["input_tokens"]
                total_usage["output_tokens"] += usage["output_tokens"]
                total_usage["total_tokens"] += usage["total_tokens"]
                total_cost["input_cost"] += cost["input_cost"]
                total_cost["output_cost"] += cost["output_cost"]
                total_cost["total_cost"] += cost["total_cost"]

                self.log.info(
                    f"Generated {filename} ({metadata['estimated_tokens']} tokens, ${cost['total_cost']:.4f})"
                )

        # Create summary metadata file
        summary = {
            "generation_info": {
                "generated_date": datetime.now().isoformat(),
                "total_files": len(generated_files),
                "target_tokens_per_file": target_tokens,
                "document_types": list(self.document_templates.keys()),
                "files_per_type": count_per_type,
                "claude_model": self.claude_client.model,
                "total_claude_usage": total_usage,
                "total_claude_cost": total_cost,
            },
            "documents": all_metadata,
        }

        summary_path = output_dir / "pdf_metadata.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

        self.log.info(f"Generated {len(generated_files)} PDF files in {output_dir}")
        self.log.info(
            f"Total cost: ${total_cost['total_cost']:.4f} ({total_usage['total_tokens']:,} tokens)"
        )
        self.log.info(f"Summary metadata saved to {summary_path}")

        return {
            "output_directory": str(output_dir),
            "generated_files": generated_files,
            "metadata_file": str(summary_path),
            "summary": summary,
        }


def main():
    """Command line interface for PDF document generation."""
    parser = argparse.ArgumentParser(
        description="Generate example PDF documents using Claude AI for testing PDF processing and summarization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate one PDF of each type with ~2000 tokens
  python -m gaia.eval.pdf_document_generator -o ./output/pdfs

  # Generate larger PDFs (~4000 tokens each)
  python -m gaia.eval.pdf_document_generator -o ./output/pdfs --target-tokens 4000

  # Generate multiple PDFs per type
  python -m gaia.eval.pdf_document_generator -o ./output/pdfs --count-per-type 3

  # Generate specific document types only
  python -m gaia.eval.pdf_document_generator -o ./output/pdfs --doc-types technical_spec white_paper

  # Generate small PDFs for quick testing
  python -m gaia.eval.pdf_document_generator -o ./test_pdfs --target-tokens 1000

  # Use different Claude model
  python -m gaia.eval.pdf_document_generator -o ./output/pdfs --claude-model claude-3-opus-20240229
        """,
    )

    parser.add_argument(
        "-o",
        "--output-dir",
        type=str,
        required=True,
        help="Output directory for generated PDF files",
    )
    parser.add_argument(
        "--target-tokens",
        type=int,
        default=2000,
        help="Target token count per document (approximate, default: 2000)",
    )
    parser.add_argument(
        "--count-per-type",
        type=int,
        default=1,
        help="Number of PDFs to generate per document type (default: 1)",
    )
    parser.add_argument(
        "--doc-types",
        nargs="+",
        choices=[
            "technical_spec",
            "business_proposal",
            "research_report",
            "project_plan",
            "policy_document",
            "white_paper",
            "user_manual",
            "financial_report",
        ],
        help="Specific document types to generate (default: all types)",
    )
    parser.add_argument(
        "--claude-model",
        type=str,
        default=None,
        help=f"Claude model to use for document generation (default: {DEFAULT_CLAUDE_MODEL})",
    )

    args = parser.parse_args()

    try:
        generator = PDFDocumentGenerator(claude_model=args.claude_model)
    except Exception as e:
        print(f"❌ Error initializing PDF document generator: {e}")
        print("Make sure ANTHROPIC_API_KEY is set in your environment.")
        return 1

    try:
        # Filter document types if specified
        original_templates = None
        if args.doc_types:
            # Temporarily filter the templates
            original_templates = generator.document_templates.copy()
            generator.document_templates = {
                k: v
                for k, v in generator.document_templates.items()
                if k in args.doc_types
            }

        result = generator.generate_document_set(
            output_dir=args.output_dir,
            target_tokens=args.target_tokens,
            count_per_type=args.count_per_type,
        )

        print("✅ Successfully generated PDF documents")
        print(f"  Output directory: {result['output_directory']}")
        print(f"  Generated files: {len(result['generated_files'])}")
        print(f"  Metadata file: {result['metadata_file']}")

        # Show summary stats
        summary = result["summary"]
        generation_info = summary["generation_info"]

        # Calculate actual document content tokens (not API tokens)
        total_content_tokens = sum(
            doc["estimated_tokens"] for doc in summary["documents"]
        )
        avg_content_tokens = (
            total_content_tokens / len(summary["documents"])
            if summary["documents"]
            else 0
        )

        # API tokens (input + output to Claude)
        total_llm_tokens = generation_info["total_claude_usage"]["total_tokens"]
        total_cost = generation_info["total_claude_cost"]["total_cost"]
        avg_cost = total_cost / len(summary["documents"]) if summary["documents"] else 0

        print(f"\n📊 Content Metrics:")
        print(
            f"  Target tokens per document: {generation_info['target_tokens_per_file']:,}"
        )
        print(f"  Actual content tokens (avg): {avg_content_tokens:.0f}")
        print(f"  Total content tokens: {total_content_tokens:,}")

        print(f"\n💰 API Usage:")
        print(f"  Total token count (input+output): {total_llm_tokens:,}")
        print(f"  Total cost: ${total_cost:.4f}")
        print(f"  Average cost per file: ${avg_cost:.4f}")

        print(f"\n🔧 Generation Details:")
        print(f"  Document types: {', '.join(generation_info['document_types'])}")
        print(f"  Claude model: {generation_info['claude_model']}")

        # Restore original templates if they were filtered
        if args.doc_types and original_templates is not None:
            generator.document_templates = original_templates

    except Exception as e:
        print(f"❌ Error generating PDFs: {e}")
        import traceback

        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
