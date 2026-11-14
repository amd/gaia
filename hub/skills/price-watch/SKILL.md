---
name: price-watch
description: Check product pages for a price drop, comparing against the lowest price seen before and alerting only on a new low. Use when the user asks to watch a price, track a deal, or tell them when something gets cheaper.
license: MIT
version: 1.0.2
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

# Price Watch

A fork of `source-watch` where the "item" is a product and "interesting" means
cheaper than it has ever been. It shows the shape worth copying: memory holds a
running extreme, not just a list of things already seen.

## Configure

- **Products** — a list of product URLs the user cares about.
- **Memory key** — `price:<product-slug>`, one per product.
- **Floor** (optional) — alert only below this price regardless of history.
  Memory key `price-floor:<product-slug>`.

## Procedure

Pick the path that matches the user's message.

### Watch — the user's message states a price

"Watch this, the lowest I've seen is $949" sets a baseline; it does not ask
for a check. Save exactly the user's number and confirm. Do **not** fetch the
page: recording today's price here would replace the user's baseline, and the
next check would compare the page against itself.

- lowest seen or current price →
  `remember(fact="price:<product-slug>: <amount> <currency> on <YYYY-MM-DD> (stated by the user) — <url>", category="fact")`
- target price →
  `remember(fact="price-floor:<product-slug>: <amount> <currency> — <url>", category="fact")`

Never save a price the user did not state. If they also ask for a check in
the same message, save first, then run the check below.

### Check — the user asks for the current price

1. **Recall the baseline.** `recall(query="price:<product-slug>", limit=20)`
   and take the lowest price recorded. Also
   `recall(query="price-floor:<product-slug>")` for a target.
2. **Fetch the page.** `fetch_page(url)`.
3. **Extract the current price.** Pull the number and its currency. Watch for
   the traps: a struck-through list price next to the real one, a subscription
   price next to the one-time price, and a per-unit price on a multipack. If
   you cannot find a price with confidence, say so — do **not** guess.
4. **Report the comparison.** A new low is a current price below the recorded
   low (or below the floor); report it in the format below. Otherwise give the
   current price, the standing low, and the delta in one line. Report before
   any memory write.
5. **Update the low — only if it is lower.**
   `remember(fact="price:<product-slug>: <amount> <currency> on <YYYY-MM-DD> — <url>", category="fact")`.
   Never record a price that is not a new low, or a guessed one: a wrong low
   poisons every future comparison.

A refused or failed `remember` never replaces the report. Give the report
anyway and say in one line that the price was not saved. Do not stop to ask
about saving instead of answering.

## Report format

> **<product>** — now **<price>**, previously **<old low>** (<date>). Down
> <delta>. <url>

## Honest limits

- Runs on demand, not on a schedule — the scheduler cannot run skills yet
  (see `source-watch` for the detail).
- Many retailers vary price by region, login state, or bot detection. The price
  a fetched page shows may not be the price the user is offered. Say which page
  you read.
- Needs memory enabled for the history to survive between runs.

## Fork this

Swap "lowest price" for any running extreme — highest rating, largest discount,
shortest lead time — and the same three steps (recall the extreme, extract the
current value, alert on a new record) carry over unchanged.
