# Storyline: blueprints, slide style, checks

## Pick a blueprint

`list_blueprints` lists them; `outline_from_blueprint(name, slide_count=N)`
returns exactly N slides with a guidance line per slot. Unfilled slots stay
TODO and `verify_deck` fails on them.

| The user wants | Blueprint |
|---|---|
| Quarterly / monthly / annual review, QBR, KPI review | `quarterly_review` |
| Executive summary, briefing, board update | `executive_summary` |
| Pitch, proposal, investor or sales deck | `pitch` |
| Project or programme status, steering committee | `project_status` |
| Training, tutorial, explainer, onboarding | `training` |
| Research, survey or analysis readout | `research_findings` |
| Choosing between options, recommendation | `decision` |
| Roadmap, plan, timeline, launch plan | `roadmap` |
| One slide: 2x2, pyramid, cycle, process, layer stack, canvas | `one_slide_framework` (`variant`) |

## Style rules

- **Action titles**: each title is the slide's takeaway as one sentence,
  about 15 words at most, no full stop ("Revenue grew 12% on West demand",
  not "Revenue"). The titles alone should tell the story.
- One message per slide; aim for 6 bullets or fewer, 12 words each, one
  level of sub-bullets. These density limits are soft: when a slide is too
  full, cut other words or split it. Requested content stays on the slides.
- Answer first: an executive summary states the conclusions later slides
  prove. Decks over 10 slides get section dividers. End with next steps
  (action, owner, date).
- Numbers carry units and periods ("$4.2M, Q3 2026"). Every chart, table and
  KPI slide names its source and period.

## What verify_deck checks

- slide count, and every required item visible on a slide (not only in notes)
- text overflow (rendered with LibreOffice when it is installed)
- overlaps, fonts, contrast, titles and density
- chart values, and pictures used where native charts belong
- sources, alt text, notes (`require_notes=True` when a script is wanted)
- TODO slots left unfilled
