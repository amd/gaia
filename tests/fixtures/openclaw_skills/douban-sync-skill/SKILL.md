---
name: douban-sync
description: Export and sync Douban (豆瓣) book/movie/music/game collections to local CSV files (Obsidian-compatible). Use when the user wants to export their Douban reading/watching/listening/gaming history, set up incremental sync via RSS, or manage their Douban data locally.
metadata: {"openclaw": {"requires": {"env": ["DOUBAN_USER"]}, "primaryEnv": "DOUBAN_USER"}}
---

# Douban Sync

Export Douban collections (books, movies, music, games) to CSV and keep them in sync via RSS.

## Two Modes

### 1. Full Export (first time)

Use the browser tool to scrape all collection pages. Requires the user to be logged into Douban.

```
browser → douban.com/people/{USER_ID}/{category}?start=0&sort=time&mode=list
```

Categories and URL paths:
- Books: `book.douban.com/people/{ID}/collect` (读过), `/do` (在读), `/wish` (想读)
- Movies: `movie.douban.com/people/{ID}/collect` (看过), `/do` (在看), `/wish` (想看)
- Music: `music.douban.com/people/{ID}/collect` (听过), `/do` (在听), `/wish` (想听)
- Games: `www.douban.com/people/{ID}/games?action=collect` (玩过), `=do` (在玩), `=wish` (想玩)

Each page shows up to 30 items in list mode (some pages may have fewer due to delisted entries). Paginate with `?start=0,30,60...` — the script uses the paginator's "next" button to determine whether to continue.

**Rate limiting:** Wait 2-3 seconds between pages. If blocked, wait 30 seconds and retry.

**Scripts:**
- `scripts/douban-scraper.mjs` — HTTP-only, no browser needed (may get rate-limited)
- `scripts/douban-browser-scraper.mjs` — via Puppeteer CDP, needs a running browser
- `scripts/douban-extract.mjs` — generates a browser console script for manual extraction

### 2. Incremental Sync (daily, via RSS)

Run `scripts/douban-rss-sync.mjs` — no login needed.

```bash
node scripts/douban-rss-sync.mjs
```

**Setup:** Set environment variables:
- `DOUBAN_USER` (required): Douban user ID
- `DOUBAN_OUTPUT_DIR` (optional): Output root directory, default `~/douban-sync`

**Recommended:** Add a daily cron job for automatic sync.

## Output Format

Four CSV files per user in the output directory:

```
douban-sync/
└── {user_id}/
    ├── 书.csv
    ├── 影视.csv
    ├── 音乐.csv
    └── 游戏.csv
```

CSV columns:
```csv
title,url,date,rating,status,comment
"书名","https://book.douban.com/subject/12345/","2026-01-15","★★★★★","读过","短评内容"
```

- `status`: 读过/在读/想读, 看过/在看/想看, 听过/在听/想听, 玩过/在玩/想玩

## Deduplication

Both full export and RSS sync deduplicate by Douban URL — safe to run multiple times.
