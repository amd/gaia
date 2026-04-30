# gaia-lite eval progress

## Status
**Phase 3 (real_world) — corpus authored. 19 synthetic documents generated, paths verified, awaiting eval run.**

## Pre-flight
- Branch: `feature/mac-4b-default` @ `995753ff` (7 fix commits landed)
- Backend: port 4200 healthy, Qwen3.5-4B-GGUF @ 32K ctx
- Audit: clean
- Universe: 54 declared, 35 already PASS, 19 newly RUNNABLE

## Phase 1+2 (recap)
27 -> 35 PASS / 35 active (100%) after 7 reliability fixes — see git log.

## Phase 3 — real_world corpus
- [x] Audited 19 scenario YAMLs — extracted every doc path + ground-truth fact
- [x] Wrote `eval/corpus/gen_real_world.py` — single-source-of-truth generator
- [x] Generated 19 documents (15 .txt + 4 .xlsx), 60 KB total
- [x] Paths match scenario YAMLs exactly (set diff = empty)
- [x] XLSX extraction via `RAGSDK._extract_text_from_xlsx` verified — every required fact appears in the flattened text (SKUs, $156,230,000, 142 employees, 0.043 unemployment, etc.)
- [x] Lint passes (black + isort)
- [ ] Eval run pending (`gaia eval agent --agent-type gaia-lite --category real_world`)
- [ ] Phase 2 fix-loop on any FAILs

## Corpus structure
- `eval/corpus/real_world/financial/` — 3 docs (Alphabet 10-K, Fed Nov 2024, Treasury FY24)
- `eval/corpus/real_world/legal/` — 4 docs (Apache 2.0, GDPR Art. 17, GitHub ToS, MIT)
- `eval/corpus/real_world/government/` — 2 docs (BLS Dec 2025, NIST CSF 2.0)
- `eval/corpus/real_world/scientific/` — 2 docs (Attention paper, CDC flu 2023-24)
- `eval/corpus/real_world/technical/` — 4 docs (Python 3.11, RPi4, RFC 7231, USB 2.0)
- `eval/corpus/real_world/spreadsheets/` — 4 .xlsx (company fin, dept budget, product inv, BLS labor)

## Commits landed previously (7)
- `739f4545` malformed tool_call envelope recovery
- `42935b5e` ctx overflow trim+retry + macOS list_windows
- `6e98296b` smarter ctx shrink (stub old tool results)
- `13b3bedb` retryable ctx overflow on n_ctx mismatch
- `33963b74` re-raise wrong-ctx so chat helper reloads
- `4f06713a` pre-parsed dict args in native tool_call
- `995753ff` probe Lemonade ctx when exception text loses n_ctx

## Cumulative
- PASS: 35 / 35 active = 100% (real_world still SKIP until next run)
- Spend so far: ~$5
- Wall time so far: ~4 hours

## Stop conditions
Primary target: 51/54 (95%) full-suite pass rate after real_world run.
