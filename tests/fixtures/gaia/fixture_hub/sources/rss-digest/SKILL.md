---
name: rss-digest
description: Read an RSS or Atom feed and summarize the newest entries. Use when the user gives a feed URL, asks what a blog or release feed has published lately, or wants a digest of a site that offers a feed.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    permissions:
      - network:read
    requirements:
      python: ">=3.10"
    tools:
      - name: fetch_rss
        description: Fetch an RSS or Atom feed and return its entries as structured data.
        parameters:
          url:
            type: string
            required: true
          max_entries:
            type: integer
            required: false
            default: 10
        returns:
          type: object
        atomic: true
    provenance:
      source: starter-pack
---

# RSS Digest

The pack's one **tool-providing** skill. Every other starter skill is a
procedure over tools the agent already has; this one ships its own code in
`tools.py` and registers it as `rss-digest/fetch_rss`.

It exists to show the shape: a manifest entry per `@tool` function, matching the
signature exactly, plus the `network:read` permission the tool actually uses.

## Procedure

1. **Fetch the feed** with `rss-digest/fetch_rss(url, max_entries=10)`. It
   returns `{"feed_title": ..., "entries": [{"title", "link", "published",
   "summary"}], "count": N}` in feed order — conventionally newest first, but
   the feed decides, so read the `published` values rather than assuming.
2. **If the call returns an `error` key, report it verbatim.** A malformed feed,
   a 404, a blocked host, a feed with no recognizable entries, and a feed
   carrying a DTD are all real answers; an invented digest is not.
3. **Digest the entries.** One line per entry: what it is and why it might
   matter, with its link. Group by theme when more than five entries share one.
4. **Lead with the exception.** If one entry is materially different from the
   usual traffic on this feed — a breaking change, a security release, a
   shutdown notice — put it first and label it.
5. **Say the range you covered** — feed title, entry count, and the oldest date
   included — so the user knows what was and was not read.

## Notes

- The tool follows GAIA's `WebClient`, so private and loopback addresses are
  refused. Point it at a public feed.
- Feeds declaring a DTD are refused rather than parsed: entity expansion is a
  denial-of-service vector, and no real feed needs one.
- RSS 1.0 / RDF feeds are not supported and report an error rather than an empty
  digest — "nothing published" and "I could not read this" must never look alike.
- Entry summaries in feeds are frequently truncated or HTML-laden. Treat them as
  a headline, not the article; say so rather than over-reading a stub.

## Fork this

Copy the directory and edit `tools.py` — that is the whole point of a tool
skill. Parse a JSON API instead of XML, or add a `since` parameter for
date-bounded fetches. Whatever you change in the signature, mirror in
`metadata.gaia.tools`: the loader compares them and refuses the skill if they
disagree, so a stale manifest can never ship.
