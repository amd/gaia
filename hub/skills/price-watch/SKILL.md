---
name: price-watch
description: Check product pages for a price drop, comparing against the lowest price seen before and alerting only on a new low. Use when the user asks to watch a price, track a deal, or tell them when something gets cheaper.
license: MIT
version: 1.0.0
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

## Procedure

For each product URL:

1. **Recall the history.** `recall(query="price:<product-slug>", limit=20)` and
   take the lowest price previously recorded.
2. **Fetch the page.** `fetch_page(url)`.
3. **Extract the current price.** Pull the number and its currency. Watch for
   the traps: a struck-through list price next to the real one, a subscription
   price next to the one-time price, and a per-unit price on a multipack. If you
   cannot find a price with confidence, say so — do **not** guess, and do not
   record a guessed number, because a wrong low poisons every future comparison.
4. **Compare.** Alert when the current price is below the recorded low (or below
   the configured floor). Otherwise report the current price and the delta in
   one line.
5. **Record the observation.**
   `remember(fact="price:<product-slug>: <amount> <currency> on <YYYY-MM-DD> — <url>", category="fact")`.
   Record every observation, not only the lows, so the history is a real series.

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
