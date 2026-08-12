---
name: recommendations
description: Recommend films, books, games, restaurants, or gear using what the user has already said they like and dislike, plus a web search for current options. Use when the user asks what to watch, read, play, buy, or try next.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    permissions:
      - network:read
    tools_required:
      - recall
      - search_web
      - fetch_page
      - remember
    provenance:
      source: starter-pack
---

# Recommendations

A recommendation is only worth more than a list if it uses something the
recommender knows about *this* person. Memory is what makes it personal; search
is what keeps it current.

## Procedure

1. **Recall taste before searching.**
   `recall(query="<category> preferences", limit=20)` and again for dislikes.
   Pull both what they liked and, more importantly, what they bounced off — a
   dislike is a sharper signal than a like.
2. **If memory is empty, ask two questions**, not ten: one thing in this
   category they loved, and one they gave up on. Then continue.
3. **Find current candidates** with `search_web(query)`. Recommend from what
   exists now, not from a stale training-set memory of "recent" releases. Use
   `fetch_page(url)` on a promising list or review to get real detail.
4. **Rank by predicted fit**, not popularity. For each of 3–5 picks, give:
   - the pick,
   - **one sentence on why it fits *them*** — naming the specific prior taste it
     connects to,
   - one honest caveat ("slow first hour", "the sequel is weaker").
5. **Include one deliberate stretch pick** and label it as such. A list that only
   confirms known taste teaches the user nothing.
6. **Record the outcome.** When the user reacts, store it:
   `remember(fact="<category>: liked/disliked <title> — <reason>", category="preference")`.
   This is the step that makes the next run better; skipping it makes the skill
   a search wrapper.

## Rules

- Never recommend something you cannot name a concrete reason for.
- If the user rejects a pick, do not re-suggest it later — that is what step 6
  prevents.
- Say when you are unsure. "I think you'll like this, but it's a stretch from
  what I know" is more useful than false confidence.

## Fork this

Change the category and the taste dimensions in step 4 — for restaurants,
cuisine, noise level, and price band; for gear, budget, use case, and brand
loyalty. The recall-search-rank-record loop is unchanged.
