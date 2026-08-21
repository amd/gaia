# gaia-agent eval fixtures

Deterministic fixtures for the flagship gaia-agent eval suite
(`eval/scenarios/gaia_*`). Everything here is canned — no fixture ever
contacts a live service.

| Path | Serves | Notes |
|---|---|---|
| `fake_gh/` | github-triage skill scenarios | fake `gh` CLI on PATH; see its README |
| `web/` | research-report, price-watch, source-watch, daily-brief | static HTML with planted facts (below) |
| `rss/feed.xml` | rss-digest | RSS 2.0, 4 planted entries |
| `csv/sales.csv` + `csv/ground_truth.json` | gaia_data / data-explore | exact aggregates; regenerate with `csv/_gen_sales.py` |
| `mini_repo/` | gaia_code (code-index) | distinctive symbols; see its README |
| `fixture_hub/` | gaia_skills_lifecycle (search/install) | committed sources only; built + signed per run — see its README |
| `prepare_fixture_hub.py` | per-run hub build | ephemeral `eval-test-publisher` keypair, signs + trust-adds; no key committed |
| `serve_fixtures.py` | HTTP for web/rss/hub | stdlib only |
| `quality_gate_thresholds.json` / `perf_gate_thresholds.json` | eval gates | all `enforce: false` until the first runner baseline |

## Serving

The default is `--port 0`: an **ephemeral** OS-assigned port, printed as
`SERVING http://127.0.0.1:<port>/`. If a scenario needs a fixed port, pick a
free high port — **never 4001, 4200, 8141, or 13305** (reserved by other GAIA
services on dev boxes and the runner).

```bash
python tests/fixtures/gaia/serve_fixtures.py            # ephemeral (default)
# http://127.0.0.1:<port>/web/price_watch.html
# http://127.0.0.1:<port>/rss/feed.xml

# The hub is served from its per-run build output (see fixture_hub/README.md):
python tests/fixtures/gaia/prepare_fixture_hub.py --skills-root <agent skills root>
python tests/fixtures/gaia/serve_fixtures.py --dir tests/fixtures/gaia/fixture_hub/_prepared
# GAIA_HUB_URL=http://127.0.0.1:<port>
```

> Planted values below are placeholders until integration:
> `eval/scenarios/GAIA_FIXTURE_VALUES.md` (owned by the scenario corpus) is
> the authority the coordinator reconciles against.

## Planted facts (ground truth for judges)

**web/research/** (research-report):
- `solid_state_batteries_overview.html` — Zephyr-7 prototype: **412 Wh/kg**
  (March 2026), **91% capacity after 1,200 cycles**, 10→80% charge in
  **14 minutes**, pilot-line yield **62%**, key obstacle **dendrite formation**.
- `solid_state_batteries_market.html` — market **$1.1B (2026) → $8.3B (2031)**,
  CAGR **49.8%**, automotive **71%** of 2031 revenue, **Nimbus Motors 12 GWh
  Ohio plant** (full output 2029).

**web/price_watch.html** — AeroBook 14 price **$1,299.00** (previous list
**$1,449.00**, 10 June 2026).

**web/source_watch_v1.html + web/source_watch_v2.html** — two DISTINCT URLs
carrying two versions of one logical page (a static server can't swap one
URL's content mid-scenario). v1: Release 3.2 scheduled **October 14, 2026**.
v2: postponed to **November 2, 2026** + a new beta section (opens
**October 5, 2026**, capped at 500 testers). The planted diff a source-watch
scenario must detect is the date change and the added section.

**web/daily_brief/** — `local_news.html` (bridge closes **July 27** for six
weeks, +12-minute detour; library open to **21:00 weekdays**, visits +34%) and
`tech_news.html` (3.2 slip, 62% yield). A brief must cite only these facts —
anything else is hallucination.

**rss/feed.xml** — exactly 4 items, guids `fixture-digest-0001..0004`; a
digest must list these entries and no others.

**csv/ground_truth.json** — the discriminating pair: best-selling product *by
units* is **Gadget Mini**, top product *by revenue* is **Widget Pro**.
