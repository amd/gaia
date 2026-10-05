# Skills, web and tool-selection tier fixtures

Served by `../serve_fixtures.py` at `http://127.0.0.1:8765/tiers_skills_web/...`
and staged by `../stage_eval_env.py` to `~/gaia-eval/tiers_skills_web/`.

The planted values — and which scenario depends on each — are recorded in
`eval/scenarios/GAIA_FIXTURE_VALUES.md` under "Skills, web and tool selection".
Change a value there and in the scenarios together.

- `web/` — pages: hidden prompt injections, a long page whose key fact sits in
  the middle, a 48-link directory with a near-duplicate decoy branch.
- `feeds/` — RSS feeds for rss-digest, one carrying an injected entry.
- `downloads/` — a fake `.exe` (plain text, cannot run) for download scenarios.
- `capture/` — SKILL.md sources for `capture_skill` (URL and folder).

`web/ferry_timetable.html` and `web/directory.html` are generated filler; edit
the planted `<p>` lines, not the filler, and keep the Sunday fact near the
middle of the extracted text (a unit test pins its position).
