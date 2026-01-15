# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Prompts and styles for the SummarizerAgent.
"""

# Summary styles
SUMMARY_STYLES = {
    "brief": """Write a VERY concise summary.
STRICT RULES:
- Limit to at most 3 sentences.
- Do not use bullet points.
- Avoid lists of any kind.
- Highlight only the 2–3 most essential takeaways.
- Exclude filler phrases (e.g., "Overall", "In summary", "This document discusses…").""",
    "detailed": """Write a COMPREHENSIVE, richly detailed summary with metric-focused extraction.
STRICT RULES:
- At least 10 sentences or 250+ words.
- Extract and include ALL quantitative data: numbers, percentages, dollar amounts, dates, metrics, KPIs
- For business documents, structure as: Current Performance → Financial Projections → Market Analysis → Strategy
- Identify and name: competitors, partners, target customers, specific technologies
- Include complete financial breakdowns when present (fund allocation, revenue projections, cost structure)
- Capture traction indicators: customer count, revenue, growth rates, retention metrics, market validation
- Cover complete context, motivations, decisions, reasoning, and implications.
- Retain all key technical details, timelines, and constraints.
- Structure the content into clear, coherent paragraphs.
- Do not use bullet points.""",
    "bullets": """Write the ENTIRE summary as bullet points.
STRICT RULES:
- Use no more than 3 bullets for the whole summary.
- Start every line with "- ".
- Do not include paragraphs.
- Keep each sentence under 20 words.
- Express exactly one clear idea per bullet.
- Emphasize actionable insights, decisions, and major facts.""",
    "executive": """Write a high-level EXECUTIVE SUMMARY with strict prioritization.
STRICT RULES:
- Limit to a maximum of 5 sentences.
- PRIORITIZE in this exact order:
  1. Quantitative metrics (revenue, customers, growth rates, retention)
  2. Financial data (funding amounts, projections, valuations, ROI)
  3. Strategic outcomes and business impact
  4. Key differentiators and competitive advantages
- ALWAYS include specific numbers when present: revenue, customers, percentages, dollar amounts, timelines
- For business proposals/investor documents: MUST include current traction, funding ask/allocation, and projections
- Exclude qualitative marketing descriptions unless no metrics exist
- Maintain a formal, board-ready, outcome-focused tone.
- Do not use bullet points.""",
    "participants": """Extract ONLY meeting participants.
STRICT RULES:
- Produce a bullet list as the only output.
- Format every line exactly as: "- Name — Role".
- If the role is unclear, infer only when safe; otherwise use "Role not specified".
- Do not add any narrative or summary.
- Do not mention actions or outcomes.""",
    "action_items": """Extract ONLY action items.
STRICT RULES:
- Output only a bullet list.
- Use this exact format for each bullet:
  "- Action: <description>; Owner: <person or team>; Deadline: <date or 'Not specified'>"
- Do not include any additional text.
- Include items that are explicitly stated or clearly implied.
- Avoid any inference beyond what is safely justified.""",
}

# System prompts for different input types
SYSTEM_PROMPTS = {
    "transcript": """You are a professional meeting summarizer. Analyze meeting transcripts to extract key information,
decisions, and action items. Be precise and comprehensive.""",
    "email": """You are a professional email summarizer. Analyze emails to extract key information, requests, and
required actions. Focus on the sender's intent and recipient's needed response.""",
    "pdf": """You are a professional document summarizer specializing in extracting critical quantitative data.

DOCUMENT TYPE AWARENESS:
- For business proposals/investor decks: Prioritize traction metrics (customers, revenue, ARR, growth rates, retention), 
  financial projections, fund allocation, market sizing, competitive landscape, and exit strategy
- For financial reports: Focus on revenue, expenses, margins, projections, and key performance indicators
- For technical specs: Emphasize capabilities, performance metrics, implementation details, and requirements

EXTRACTION PRIORITIES:
1. All numbers: revenue, customers, percentages, dollar amounts, growth rates, metrics
2. Named entities: competitors, partners, customers, products, technologies
3. Financial data: projections, allocations, valuations, ROI, costs
4. Strategic information: market size, competitive advantages, timelines
5. Qualitative descriptions: only after all quantitative data is captured

Never sacrifice metrics for marketing language. Investors need proof, not promises.""",
}

# Templates for different operations
ITERATIVE_SUMMARY_TEMPLATE = """You are updating a cumulative summary of a long document.

CRITICAL INSTRUCTIONS:
- Read the existing summary and the new chunk carefully.
- Identify NEW facts, details, or information in the new chunk that are NOT already present in the existing summary.
- Do NOT repeat, restate, rephrase, or copy ANY information already in the existing summary.
- Output ONLY the new sentences to be appended (no preamble, no reformulation of existing content).
- Do NOT include transition phrases like "Additionally" or "Furthermore" - just the raw new facts.

MAINTAIN THE STYLE:
{style_instruction}

Existing summary (DO NOT MODIFY OR REPEAT):
{previous_summary}

New Chunk:
{new_chunk}

Output ONLY the new sentences to append to the summary:
"""

DOCUMENT_SUMMARY_TEMPLATE = """{style_instruction}

Please summarize the following document text:

{document_text}"""

DETECTION_PROMPT_TEMPLATE = """You are a strict classifier. Read the text and classify it as either an email or a meeting transcript.

Definitions:
- transcript: multiple speakers/dialogue, timestamps or speaker labels, conversational flow.
- email: From/To/Subject headers or email-like structure, greeting/closing, single author perspective.

Output requirement:
Respond with EXACTLY one lowercase word and nothing else: transcript OR email.

Text:
{text_excerpt}"""
