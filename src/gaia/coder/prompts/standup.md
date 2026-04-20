<!--
Canonical prompt for §15.8 P10 — daily/weekly standup composition.

Model: Claude Opus 4.7, temperature 0.3 (a *touch* of variety on prose —
the §15.8 header calls this out explicitly), max 2000 output tokens.
Fires: daily 09:00 EM-local and weekly Friday 17:00 (§4.4).

The `<gaia_md_persona_section>` tag is a cacheable prefix segment
(§6.6 prompt caching); the per-turn slots below it are non-cacheable
suffixes.
-->
Compose gaia-coder's {{daily|weekly}} standup for {{em_handle}}.

<window_start>{{iso8601}}</window_start><window_end>{{iso8601}}</window_end>
<tasks_completed>{{list_from_tasks_db}}</tasks_completed>
<tasks_in_progress>{{list}}</tasks_in_progress>
<tasks_blocked>{{list_with_blocker}}</tasks_blocked>
<open_questions>{{list}}</open_questions>
<guardrail_trips>{{count_with_types}}</guardrail_trips>
<trust_tier>{{current_tier}}</trust_tier>
<scorecards>{{section_10_metrics_snapshot}}</scorecards>
<learnings_candidates>{{top_3_from_learnings_log}}</learnings_candidates>

Follow persona from GAIA.md. Required sections:
- Yesterday (daily) / This week (weekly) — 3-5 bullets, one per completed
  task with PR URL + verdict
- Today / Next week — 3-5 planned bullets
- Blocked — list with blocker per item (omit if empty)
- For the EM — open questions (omit if empty)
- Flags — guardrail trips, concealment check ("none this window" if clean)
- [Weekly only] Learnings — top 3 from log with promotion proposal

Hard rules (Pass 5 applies):
- No "Certainly!" / trailing summaries / AI-disclosure
- Lead every bullet with artifact (PR URL / issue #)
- Cite file:line where relevant
- Sign off with persona_name (or "— gaia-coder")

Return a single Markdown string.
