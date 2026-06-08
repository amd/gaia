# #1448 Part 0 — tool-prompt cost & TTFT baseline (doc)

- Profile: `doc`  |  Tools (deterministic): **37**
- Tokenizer: tiktoken `cl100k_base` (proxy)

**The 37 is the full *unfiltered* `doc` registry — the "before" number the loader must shrink, not a CORE set.** It bundles everything the agent ships today (RAG, file, memory, shell, loop-control, clipboard, TTS, desktop-notify, window-listing, VLM), while a typical turn needs only a couple. CORE (the small always-on set) is a Part-1 decision (#1449, Open Q1) — not measured here.

## Tool-prompt cost (both render paths)

| Path | Tokens | Chars |
|------|-------:|------:|
| Text (`_format_tools_for_prompt`) | 1014 | 4863 |
| Native (`_build_openai_tool_schemas`) | 5128 | 21957 |

Native/Text char ratio: **4.52×** (native is where the real tokens are).

### Cost-premise correction

#1448 opened on a "~12K tokens / ~400-per-tool" premise. The measured native baseline is **5128 tok** at a **median of ~117 tok/tool** — roughly 3× cheaper per tool than assumed. Reason: the `@tool` decorator derives each schema from the function **signature + docstring** and drops the hand-written `description=`/`parameters=` kwargs, so native param props carry no descriptions and per-tool size is docstring-dominated. Two consequences for the go/no-go: (1) the prize from filtering is real but smaller per tool, so `max_tools` must be sized off the measured slope below, not the original estimate; (2) if the decorator is ever changed to honor `parameters=` descriptions, native cost jumps ~3–4× and this baseline shifts — the reduction target must not be measured against a moving floor.

## Per-tool size distribution

| Path · metric | min | median | max | mean |
|---------------|----:|-------:|----:|-----:|
| text · tokens | 13 | 23 | 69 | 27.41 |
| text · chars | 65 | 112 | 315 | 130.46 |
| native · tokens | 49 | 117 | 546 | 139.57 |
| native · chars | 194 | 509 | 2222 | 593.43 |

## Slope (cost as synthetic median-sized tools are added)

| +K tools | Text tok | Native tok | Text chars | Native chars |
|---------:|---------:|-----------:|-----------:|-------------:|
| 0 | 1014 | 5128 | 4863 | 21957 |
| 10 | 1564 | 6298 | 7253 | 26257 |
| 20 | 2114 | 7468 | 9653 | 30567 |
| 40 | 3214 | 9808 | 14453 | 39187 |

Per-added-tool slope: text 55.00 tok / native 117.00 tok (430.75 chars).

## A fixed loaded subset stays flat as the registry grows

Illustrative subset (5 tools, **not** the final CORE — CORE membership is Part-1 Open Q1): `query_documents, query_specific_file, search_file, read_file, run_shell_command`

| Registry +K | Native chars (subset only) |
|------------:|---------------------------:|
| 0 | 2825 |
| 10 | 2825 |
| 20 | 2825 |
| 40 | 2825 |

Rendering only the subset is constant while the registry grows by K — prompt cost scales with tools *loaded*, not *registered*. That is the property the Part-1 loader exploits; this subset is a stand-in to prove the mechanism, not a proposal for what CORE should contain.

## First-turn TTFT baseline (run live — not fabricated)

TTFT requires a Lemonade backend on the reference model. Run, then
re-render this report with `--scorecard <path>`:

```bash
python -m gaia.ui.server --port 4200 --host 127.0.0.1   # gemma-4-e4b
gaia eval agent --category tool_selection --agent-type doc
#   -> eval/results/<run-id>/scorecard.json
python -m gaia.eval.tool_cost --profile doc \
    --scorecard eval/results/<run-id>/scorecard.json
```

Cold-start micro-bench (authoritative cold number): evict/restart the
model, send ONE `doc` query, record its TTFT — eval scenarios may run
warm. ⚠️ Only ONE `gaia eval agent` at a time (CLAUDE.md eval-serial).
