# Phase 1b — Write large_report.md

Write ONE file: `C:\Users\14255\Work\gaia4\eval\corpus\documents\large_report.md`

## Requirements

- **~15,000 words** of realistic audit/compliance report content
- Numbered sections 1 through 75 (each section = roughly one "page")
- **CRITICAL buried fact**: In Section 52, include EXACTLY this sentence verbatim:
  > "Three minor non-conformities were identified in supply chain documentation."
  (This tests deep retrieval — it must appear deep in the document, ~75% through)
- Use realistic-sounding audit/compliance content: ISO standards, process reviews, risk assessments, findings, corrective actions, management responses

## Section structure

- Sections 1-10: Executive Summary, Scope, Methodology, Organization Overview
- Sections 11-25: Process Area Reviews (HR, Finance, IT, Operations, Procurement)
- Sections 26-40: Risk Assessment findings (each section = one risk domain)
- Sections 41-50: Compliance Status by regulatory framework (ISO 9001, ISO 27001, SOC2, GDPR, etc.)
- **Section 51**: Supply Chain Overview
- **Section 52**: Supply Chain Audit Findings — MUST contain:
  `Three minor non-conformities were identified in supply chain documentation.`
  Include 2-3 paragraphs around it describing what the non-conformities were.
- Sections 53-60: Corrective Action Plans
- Sections 61-70: Management Responses
- Sections 71-75: Conclusions and Appendices

## Word count guidance
Each section should be ~150-250 words. With 75 sections at ~200 words each = ~15,000 words total.

## IMPORTANT
- Do NOT use placeholder text like "Lorem ipsum"
- Use realistic names, standards references (ISO 9001:2015, etc.), dates in 2024-2025
- The buried fact in Section 52 must be verbatim: "Three minor non-conformities were identified in supply chain documentation."
- Write the file directly — do not create a generator script
- After writing, verify the file exists and contains the Section 52 text

## After writing
Run this verification:
```
uv run python -c "
content = open(r'C:\Users\14255\Work\gaia4\eval\corpus\documents\large_report.md', encoding='utf-8').read()
words = len(content.split())
has_fact = 'Three minor non-conformities were identified in supply chain documentation' in content
sec52_pos = content.find('## Section 52')
total_pos = len(content)
print(f'Words: {words}')
print(f'Has buried fact: {has_fact}')
print(f'Section 52 at position {sec52_pos} of {total_pos} ({100*sec52_pos//total_pos}% through)')
"
```

The buried fact must be present and Section 52 must be >60% through the document.

Then write `C:\Users\14255\Work\gaia4\eval\results\phase1\phase1_complete.md` with a summary of all Phase 1 files created (see below).

## phase1_complete.md content
Summarize:
- All corpus documents created/verified (list each with word count or row count)
- Adversarial documents created
- manifest.json written
- audit.py created and run
- architecture_audit.json written
- Any issues or adjustments (e.g. Sarah Chen $70,000 instead of spec's $67,200 due to math inconsistency)
- Status: COMPLETE
