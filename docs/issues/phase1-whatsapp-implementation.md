# Phase 1: WhatsApp Integration — Implementation Proposal

Status: draft
Related: docs/spec/whatsapp-evaluation.md, issue #889 (Telegram Phase 0)

Objective
- Produce a scoped Phase 1 implementation that validates feasibility and produces a production-ready adapter design for WhatsApp using the chosen path.

Decision (from evaluation)
- Preferred production path: WhatsApp Business Cloud API via a partner (Twilio or 360dialog). If no commercial funding / business justification, defer until after v0.20.0.

Scope (what this PR will do)
- Implement a `whatsapp` messaging adapter for GAIA that:
  - Registers a partner-backed transport (Twilio/360dialog) with config-driven credentials.
  - Translates incoming webhooks into GAIA adapter events (message, media, reaction, read receipts).
  - Sends replies via partner API including media uploads.
  - Handles template message sending for proactive outbound workflows.
  - Includes unit tests for adapter mapping and an integration smoke test (manual-run) guide.

Out of scope
- Supporting community drivers (`whatsapp-web.js`, Baileys) in the same PR; these remain experimental spikes.
- Automating Meta Business verification or partner account creation.

Phase 1 tasks (concrete)
1) Onboarding docs & credentials (owner: PM) - start Meta Business verification and partner account setup; document expected timelines. (3–7d external)
2) Adapter skeleton (owner: eng) - implement webhook handling, auth, and event mappings; unit tests. (3–5d)
3) Template workflow (owner: eng) - implement template send path and test harness for approved templates. (2–4d)
4) Media handling (owner: eng) - media upload/download flow via partner. (2–4d)
5) Integration spec & docs (owner: eng/pm) - docs/spec/whatsapp-evaluation.md link, configuration docs, runbook for rate-limiting and errors. (2d)
6) Spikes (parallel):
   - Spike A: whatsapp-web.js prototype (safety spike) - 2–3d, document ban-rate and operational risks.
   - Spike B: Business Cloud API free-tier onboarding trial - 3–5d, document verification friction and webhook round-trip.

Acceptance criteria
- Adapter code checked in under `src/gaia/` with unit tests.
- Documentation updated: `docs/spec/whatsapp-evaluation.md` and adapter README with setup steps.
- Spike reports for A/B filed under `docs/spikes/whatsapp-webjs.md` and `docs/spikes/whatsapp-cloudapi.md` (manual deliverables).

Risks
- Long external wait times (Meta verification) - mitigate by starting verification early and running local integration tests with partner sandbox numbers.
- Privacy contractual review required before storing message logs - involve legal.

Estimates
- Implementation (adapter + tests + docs): 2–3 engineer-weeks.
- Spikes & onboarding: 1–2 engineer-weeks (parallelizable).

Next steps
- Confirm path (partner vs. defer). If confirmed, assign engineer and PM, start partner onboarding, and open a scoped implementation PR with the adapter skeleton.
