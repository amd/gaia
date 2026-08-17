---
name: daily-brief
description: Compose a short briefing from several sources at once — headlines, tracked topics, and the user's own open items — as one digest instead of separate answers. Use when the user asks for a briefing, a digest, a rundown, or "what do I need to know today".
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    permissions:
      - network:read
    tools_required:
      - search_web
      - fetch_page
      - recall
    provenance:
      source: starter-pack
---

# Daily Brief

The composition example: several independent sources collapsed into one thing
worth reading. The hard part is not gathering — it is cutting.

## Configure

The user picks the sections. A good default:

| Section | Source |
|---|---|
| Headlines | `search_web` on the user's 2–3 tracked topics |
| Tracked pages | `fetch_page` on specific URLs they follow |
| Their own items | `recall` of open commitments and reminders |

## Procedure

1. **Gather each section independently.** Run the searches and fetches for every
   configured section before writing anything.
2. **Tolerate partial failure.** If one source is unreachable, produce the brief
   without it and add a one-line "couldn't reach X" note. A brief that fails
   entirely because one feed was down is useless.
3. **Cut hard.** Three items per section, maximum. If nothing in a section is
   worth reading, drop the section and say "nothing new" — do not pad.
4. **Write it in this shape:**
   - **One line up top** — the single thing that matters most today.
   - **Per section** — up to three bullets, each one sentence, each with its
     link.
   - **Your items** — open commitments from memory, with dates.
   - **Gaps** — sources that failed, in one line.
5. **Keep it under 250 words.** A brief that takes ten minutes to read is not a
   brief.

## Honest limits

- **It is not delivered to you — you ask for it.** The flagship version of this
  idea (arrives at 7am as a voice note on Telegram) needs three things GAIA does
  not have yet: skill-aware scheduling, text-to-speech as an agent tool, and
  Telegram voice-note support. See the guide for what would unblock it.
- Headlines come from `search_web` (DuckDuckGo), not a curated news API, so
  coverage is uneven. Say where a claim came from.
- The "your items" section needs memory enabled; drop it silently if not.

## Fork this

Swap the sections for your morning: a status page you fetch, a repo's releases
page, and your own task list. The gather-tolerate-cut-write skeleton is the
reusable part.
