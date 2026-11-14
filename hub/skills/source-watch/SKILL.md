---
name: source-watch
description: Check a web page or feed for something worth telling the user about, remembering what was already reported so nothing repeats. Use when the user asks to watch, monitor, or check a URL for changes, new items, or a condition being met.
license: MIT
version: 1.1.1
metadata:
  gaia:
    security_tier: community
    permissions:
      - network:read
    tools_required:
      - fetch_page
      - recall
      - remember
    provenance:
      source: starter-pack
---

# Source Watch

The generic watcher. Fetch a source, decide whether anything matches, suppress
what was already reported, and say something only when there is news. Every
other watcher in this pack is a fork of this one.

## Configure

The user supplies these; ask for any that are missing before the first run.

| Setting | Meaning | Example |
|---|---|---|
| Source | The URL to fetch | `https://example.com/releases` |
| Match | What counts as interesting | "any version above 3.0" |
| Memory key | Where seen items are recorded | `watch:example-releases` |

## Procedure

1. **Recall what was already reported.**
   `recall(query="<memory key>", limit=50)`. Add every item you already
   reported earlier in this conversation — those count as seen even when
   memory holds nothing. This is what stops the watcher telling the user the
   same thing every run.
2. **Fetch the source.** `fetch_page(url)`. If the fetch fails, report the
   failure and stop — an empty page is not "no news", and treating it as such is
   how a watcher silently dies.
3. **Extract the candidate items.** Titles, versions, prices, listings —
   whatever the source is a list of. Keep a stable identifier per item so step 4
   can compare across runs.
4. **Apply the match**, then drop every item whose identifier already appeared in
   step 1.
5. **Report or stay quiet.** If nothing new matched, say "nothing new" in one
   line. Do not pad a quiet run into a paragraph. When something did match,
   give each item as a link to its page (`[title](url)`), falling back to the
   source URL when the item has no page of its own. Report before any memory
   write.
6. **Record what you reported — only if the user asked you to remember or
   keep track of it.** Memory refuses page text the user did not ask to save,
   and "watch" or "check" alone is not that ask, so otherwise skip this step;
   step 1 still dedupes within this conversation. When they did ask, save only
   the identifier, never a summary copied from the page:
   `remember(fact="<memory key>: <identifier>", category="fact")` for each new
   item.

A refused or failed `remember` never replaces the report. Give the report
anyway and say in one line that it was not saved. Do not retry the save or
stop to ask about it.

## Honest limits

- **This runs when you ask it to.** GAIA's scheduler can run a recurring
  *prompt* (`gaia schedule add --cron ... --prompt ...`), but it cannot yet run a
  *skill* on a schedule — `--skill` is rejected at creation time. Until that is
  wired, "watching" means you invoke the skill and it checks.
- Remembering across conversations needs memory enabled and the user's ask to
  keep track. Without both, the watcher dedupes only within this
  conversation and repeats itself in a new one — say so rather than pretending
  it de-duplicated.
- Fetch only pages you are allowed to poll, and do not hammer a source.

## Fork this

Copy the directory, rename it and its `name` field, then specialize step 3
(what an item is) and step 4 (what counts as interesting). `price-watch` in this
pack is exactly that fork.
