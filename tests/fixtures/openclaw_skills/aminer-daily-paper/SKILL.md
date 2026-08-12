---
name: aminer-rec5
description: "OpenClaw personalized paper recommendation skill. When the user invokes /aminer-rec5 or /skill aminer-rec5 in Feishu, immediately run the local pipeline under {baseDir}/scripts/, accept aminer_user_id, scholar hints, seed paper titles, papers_file, or free-form topic text, build a unified ResearchProfile, retrieve papers, enrich with AMiner, dispatch Feishu cards, and return NO_REPLY."
homepage: https://github.com/tlysanhuo/aminer-rec
user-invocable: true
disable-model-invocation: false
metadata: { "openclaw": { "emoji": "📚", "requires": { "bins": ["python3"] } } }
---

# aminer-rec5

Use this skill only for explicit `/aminer-rec5` or `/skill aminer-rec5` requests.

## Contract

- Every explicit invocation is a new run.
- Do not answer with status-only text.
- Do not search, install, or repair skills.
- After running `handle_trigger.py`, check `final_response` in the JSON output:
  - `NO_REPLY` → Feishu cards were dispatched successfully. Return exactly `NO_REPLY`.
  - `TEXT` → No Feishu target available. Present the `reply_text` value directly to the user.
  - Any error → Report the `reply_text` or error detail to the user.

## Inputs

- `aminer_user_id`
- `scholar` / `name`
- `org`
- `papers`
- `papers_file`
- `topics`
- `language_sort`: `zh` or `en` — filter results by paper language
- `start_year`: minimum publication year (e.g. 2023)
- `end_year`: maximum publication year (e.g. 2026)
- free-form natural-language interest description

## Execution

```bash
python3 "{baseDir}/scripts/handle_trigger.py" \
  --base-dir "{baseDir}" \
  --text "<original Feishu message>"
```

`handle_trigger.py` is the only supported entrypoint.

## Examples

```
/aminer-rec5 topics: multimodal agents, tool use
/aminer-rec5 topics: LLM reasoning language_sort: en start_year: 2024
/aminer-rec5 scholar: Jie Tang org: Tsinghua University papers: OAG-Bench
/aminer-rec5 aminer_user_id: 696259801cb939bc391d3a37 topics: multimodal
```
