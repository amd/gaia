#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
PDF Formatter for summarizer output
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False


class PDFFormatter:
    """Format summary results as PDF"""

    def __init__(self):
        if not HAS_REPORTLAB:
            raise ImportError(
                "PDF output requires reportlab. Install with: pip install reportlab"
            )

        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()

    def _setup_custom_styles(self):
        """Setup custom paragraph styles"""
        # Title style
        self.styles.add(
            ParagraphStyle(
                name="CustomTitle",
                parent=self.styles["Heading1"],
                fontSize=24,
                textColor=colors.HexColor("#1a1a1a"),
                spaceAfter=30,
                alignment=TA_CENTER,
            )
        )

        # Section header style
        self.styles.add(
            ParagraphStyle(
                name="SectionHeader",
                parent=self.styles["Heading2"],
                fontSize=16,
                textColor=colors.HexColor("#2c3e50"),
                spaceAfter=12,
                spaceBefore=20,
            )
        )

        # Metadata style
        self.styles.add(
            ParagraphStyle(
                name="Metadata",
                parent=self.styles["Normal"],
                fontSize=10,
                textColor=colors.HexColor("#7f8c8d"),
                spaceAfter=6,
            )
        )

    def format_summary_as_pdf(self, result: Dict[str, Any], output_path: Path):
        """Generate PDF from summary result"""
        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=letter,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=18,
        )

        # Build content
        story = []

        # Title
        metadata = result.get("metadata", {})
        input_file = Path(metadata.get("input_file", "Unknown")).name
        story.append(
            Paragraph(f"Summary Report: {input_file}", self.styles["CustomTitle"])
        )
        story.append(Spacer(1, 0.2 * inch))

        # Metadata section
        story.append(Paragraph("Document Information", self.styles["SectionHeader"]))
        meta_items = [
            f"Type: {metadata.get('input_type', 'Unknown').title()}",
            f"Generated: {metadata.get('timestamp', datetime.now().isoformat())}",
            f"Model: {metadata.get('model', 'Unknown')}",
            f"Processing Time: {metadata.get('processing_time_ms', 0)}ms",
        ]

        for item in meta_items:
            story.append(Paragraph(item, self.styles["Metadata"]))

        story.append(Spacer(1, 0.3 * inch))

        # Summaries section
        if "summary" in result:
            # Single style output
            self._add_single_summary(
                story, result["summary"], metadata.get("summary_style", "Summary")
            )
        else:
            # Multiple styles output
            summaries = result.get("summaries", {})
            for style, content in summaries.items():
                self._add_summary_section(story, style, content)

        # Performance section (optional)
        if result.get("performance") or result.get("aggregate_performance"):
            story.append(PageBreak())
            story.append(Paragraph("Performance Metrics", self.styles["SectionHeader"]))

            # Use detailed performance stats from individual LLM calls first
            perf = result.get("performance", {})
            if not perf:
                perf = result.get("aggregate_performance", {})

            # Get model info from metadata or performance data
            metadata = result.get("metadata", {})
            model = metadata.get("model") or perf.get("model_info", {}).get(
                "model", "N/A"
            )
            is_local = metadata.get(
                "use_local_llm", perf.get("model_info", {}).get("local_llm", "N/A")
            )

            perf_data = [
                ["Metric", "Value"],
                ["Model", str(model)],
                ["Local LLM", str(is_local)],
                ["Total Tokens", str(perf.get("total_tokens", "N/A"))],
                [
                    "Prompt Tokens",
                    str(perf.get("prompt_tokens", perf.get("input_tokens", "N/A"))),
                ],
                [
                    "Completion Tokens",
                    str(
                        perf.get("completion_tokens", perf.get("output_tokens", "N/A"))
                    ),
                ],
                ["Time to First Token", f"{perf.get('time_to_first_token_ms', 0)}ms"],
                ["Tokens per Second", f"{perf.get('tokens_per_second', 0):.1f}"],
                [
                    "Processing Time",
                    f"{perf.get('processing_time_ms', perf.get('total_processing_time_ms', 0))}ms",
                ],
            ]

            t = Table(perf_data, colWidths=[3 * inch, 2 * inch])
            t.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, 0), 12),
                        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                        ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                        ("GRID", (0, 0), (-1, -1), 1, colors.black),
                    ]
                )
            )
            story.append(t)

        # Original content (if included)
        if result.get("original_content"):
            content = result["original_content"]

            story.append(PageBreak())
            story.append(Paragraph("Original Content", self.styles["SectionHeader"]))
            story.append(Spacer(1, 0.2 * inch))

            # Split content into paragraphs
            for para in content.split("\n\n"):
                if para.strip():
                    story.append(Paragraph(para.strip(), self.styles["Normal"]))
                    story.append(Spacer(1, 0.1 * inch))

        # Build PDF
        doc.build(story)

    def _add_text_with_newlines(self, story, text):
        """Add text to story, handling newlines by converting to HTML breaks"""
        if not text:
            return

        # Simply replace newlines with HTML line breaks
        formatted_text = text.replace("\n", "<br/>")
        story.append(Paragraph(formatted_text, self.styles["Normal"]))

    def _add_single_summary(self, story, summary_data, style_name):
        """Add a single summary section to the story"""
        story.append(
            Paragraph(
                style_name.replace("_", " ").title(), self.styles["SectionHeader"]
            )
        )

        if "text" in summary_data:
            # Handle newlines by splitting into separate paragraphs
            self._add_text_with_newlines(story, summary_data["text"])
            story.append(Spacer(1, 0.2 * inch))

        if "items" in summary_data:
            for item in summary_data["items"]:
                story.append(Paragraph(f"• {item}", self.styles["Normal"]))
            story.append(Spacer(1, 0.2 * inch))

        if "participants" in summary_data:
            story.append(Paragraph("Participants:", self.styles["Normal"]))
            for participant in summary_data["participants"]:
                story.append(Paragraph(f"• {participant}", self.styles["Normal"]))
            story.append(Spacer(1, 0.2 * inch))

    def _add_summary_section(self, story, style, content):
        """Add a summary section for a specific style"""
        # Format style name
        style_title = style.replace("_", " ").title()
        story.append(Paragraph(style_title, self.styles["SectionHeader"]))

        if isinstance(content, dict):
            if "text" in content:
                # Handle newlines by splitting into separate paragraphs
                self._add_text_with_newlines(story, content["text"])
                story.append(Spacer(1, 0.2 * inch))

            if "items" in content:
                for item in content["items"]:
                    story.append(Paragraph(f"• {item}", self.styles["Normal"]))
                story.append(Spacer(1, 0.2 * inch))

            if "participants" in content:
                for participant in content["participants"]:
                    if isinstance(participant, dict):
                        p_text = f"• {participant.get('name', 'Unknown')}"
                        if participant.get("role"):
                            p_text += f" ({participant['role']})"
                        story.append(Paragraph(p_text, self.styles["Normal"]))
                    else:
                        story.append(
                            Paragraph(f"• {participant}", self.styles["Normal"])
                        )
                story.append(Spacer(1, 0.2 * inch))

            # Email specific fields
            if "sender" in content:
                story.append(
                    Paragraph(f"From: {content['sender']}", self.styles["Normal"])
                )
            if "recipients" in content:
                story.append(
                    Paragraph(
                        f"To: {', '.join(content['recipients'])}", self.styles["Normal"]
                    )
                )
                story.append(Spacer(1, 0.2 * inch))
        else:
            # Simple text content
            self._add_text_with_newlines(story, str(content))
            story.append(Spacer(1, 0.3 * inch))
