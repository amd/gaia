# oss-dashboard — a Claude Code skill

Builds a private maintainer dashboard for any open-source GitHub repository: who downloads it (GitHub releases,
npm, PyPI, crates.io, Homebrew, Docker Hub, RubyGems, NuGet, conda, Packagist), how fast it and its community grow,
how PRs, issues and CI are doing, and (optionally) eval or benchmark results. It runs as a Cloudflare Worker with a
D1 database behind Cloudflare Access sign-in, and collects every night, so it keeps history GitHub throws away.

You don't run the steps by hand: Claude Code reads `SKILL.md` and drives them. This file is how to install it and
what to expect.

## 1. Install the skill

Unzip into your personal skills folder, so the result is `~/.claude/skills/oss-dashboard/SKILL.md`:

```bash
mkdir -p ~/.claude/skills
unzip oss-dashboard-skill.zip -d ~/.claude/skills/
```

(On Windows that folder is `C:\Users\<you>\.claude\skills\`.) Restart Claude Code; `oss-dashboard` then appears in
its skill list. To share it with a whole team instead, put the folder in a repository's `.claude/skills/`.

## 2. What you need

- **Node 22.5+**, **Python 3.11+**, and the **GitHub CLI** logged in (`gh auth login`) as someone with **maintainer
  access** to the repository. GitHub shows the stargazer list and traffic only to people with access.
- A **Cloudflare account on the Workers paid plan** ($5/month; the free plan cannot run a nightly collection).
- For a URL like `example.org/dashboard`, the domain's DNS on that Cloudflare account. Otherwise the free
  `<name>.<account>.workers.dev` address works too.

## 3. Ask Claude

In Claude Code, in any folder:

> Build a private maintainer dashboard for github.com/OWNER/REPO using the oss-dashboard skill.

Claude will:

1. **Probe the repository** and ask you to confirm a few things: the test workflow, the packages users install,
   who counts as the core team (every GitHub account of each maintainer), and where the dashboard is served.
2. **Scaffold the project** into a new folder and run its tests. If your releases contain files whose names don't
   say what they are, a test fails and Claude adds a rule for them. That is by design: installs are never guessed.
3. **Run it locally on your real data** (`http://127.0.0.1:8787/admin`) and re-count every headline number from
   GitHub (`scripts/audit_numbers.py`). Look at the pages yourself too. You know your project; "that can't be right"
   is the best bug finder.
4. **Deploy**, following the generated project's `README.md`. A few steps only you can do:
   - `npx wrangler login` (opens a browser),
   - create a **read-only fine-grained GitHub token** for the repository (Contents, Metadata, Issues, Pull
     requests, Actions, and Administration: read for traffic) and paste it when `wrangler secret put GITHUB_TOKEN`
     asks. Never paste it into the chat,
   - in the Cloudflare dashboard: turn on **One-time PIN** sign-in, protect the Worker with **Access** (Worker →
     Access → Protect this Worker → All traffic), allow your team's emails, and create a **service token** for scripts.
5. **Verify and backfill the same day**: `scripts/verify_deploy.py` proves anonymous visitors are refused and every
   data source works. Then Claude backfills ~90 days of CI history, which GitHub deletes after that.

Expect an hour or two for the first setup, most of it waiting on the first data collection.

## 4. Day to day

- It collects every night at 03:17 UTC; nothing to run.
- A failing source turns the page footer red and says what to fix. The common one: the GitHub token expired, so
  put its expiry date in your calendar.
- Add or remove people in Cloudflare Access (no redeploy). Change the core team from the Developers page.
- Back up now and then: `npx wrangler d1 export DB --remote --output backup.sql`. Much of this history exists
  nowhere else.

## What's inside

| Path | What |
|------|------|
| `SKILL.md` | The workflow Claude follows |
| `template/` | The Worker: pages, collectors, API, database migrations, ~380 tests |
| `scripts/` | `probe_repo.py`, `scaffold.py`, `audit_numbers.py`, `verify_deploy.py` |
| `references/` | Metric definitions and thresholds with sources, data-source quirks, accuracy checklist, UX rules, publishing checklist, how to extend, eval results format |

## Good to know

- **Private by default.** Only allowlisted emails get in, and the Worker re-checks the sign-in itself. Repository
  traffic is not public data, so keep screenshots and the deployed URL inside the team.
- **Tested** locally end to end on ~24 real repositories (Python, JS/TS monorepos, Rust, Go, Ruby, .NET, PHP, Java,
  Electron/Tauri, Docker); every audited number matched GitHub. It has not yet been run against a live Cloudflare
  deployment by its authors, so let `verify_deploy.py` confirm yours.
- Derived from the GAIA admin dashboard (github.com/amd/gaia, MIT); see `template/NOTICE`.
