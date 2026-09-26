# Website Admin Dashboard — download instrumentation, analytics, and auth

> **Status:** Not started — scoping only.
> **Depends on:** `workers/agent-hub` (R2-backed binary hosting), the homepage
> stats row (`website/src/data/github.ts`, `website/src/pages/index.astro`)
> that this doc is a follow-up to.

## Why this is separate from the public stats row

The public homepage only shows numbers with a real, public source of truth:
GitHub stars/forks/release-asset download counts, and npm registry downloads
(`website/src/data/github.ts`). Those are honest but incomplete — the
website's own install button (`TuiDownload`) downloads platform binaries from
a Cloudflare R2 bucket via `workers/agent-hub`'s `serveObject`
(`workers/agent-hub/src/index.ts`), which has **zero request tracking today**:
no counter, no Analytics Engine binding, no Logpush. Most visitors install
through that button, not through the GitHub releases page, so the public
stats row necessarily understates real usage. Getting the real number
requires new backend instrumentation and a place to view it — not just new
homepage UI — so it's scoped as its own project rather than folded into the
stats-row PR.

## Scope for a future PR

1. **R2 download instrumentation.** Add a Workers Analytics Engine binding (or
   a lightweight KV/D1 counter) to `workers/agent-hub` that records one event
   per `serveObject` call — path, timestamp, coarse platform if useful. Keep
   it privacy-conscious: no IP storage, no per-user tracking, aggregate counts
   only.
2. **Cloudflare Analytics API access.** A new Cloudflare API token scoped to
   Analytics Engine SQL API read access, kept server-side only and never
   shipped to a public build. Today's Cloudflare secrets in this repo
   (`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `R2_ACCESS_KEY_ID` /
   `R2_SECRET_ACCESS_KEY`, see `.github/workflows/release_components.yml` and
   `util/check_r2_credentials.py`) are scoped to Worker-deploy and R2
   object-storage only — none of them grant analytics read today.
3. **An auth model for the dashboard itself.** None of the existing patterns
   in this repo are multi-user admin auth:
   - `workers/agent-hub`'s bearer-token auth (`workers/agent-hub/src/auth.ts`)
     is a flat shared-secret scheme for machine-to-machine publish calls, not
     a login system.
   - The Agent UI's tunnel auth (`src/gaia/ui/server.py`, bearer + HttpOnly
     cookie bootstrapped from a QR code) is single-device access control, not
     a multi-user session system.

   Recommend evaluating **Cloudflare Access** (zero-app-code SSO in front of a
   Worker route) as the default before building a bespoke login — it avoids
   maintaining a credential store for what is presumably a small, known set
   of internal viewers.

Also worth covering once instrumentation exists: visitor/traffic analytics
for the website itself (page views, referrers) via Cloudflare Web Analytics
or the GraphQL Analytics API — the same missing API scope as (2) applies.

## Non-goals for this doc

Full technical design, data schema, or UI mockups. This is a placeholder to
record the decision to defer the "real" download/traffic numbers behind a
private, authenticated surface, and what it will take to build — not the
design itself.
