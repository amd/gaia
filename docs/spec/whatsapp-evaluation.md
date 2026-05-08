# WhatsApp Evaluation - Decision Document

Status: accepted
Date: 2026-05-03

Decision: Defer WhatsApp for v0.20.0 and prioritize shipping a Telegram adapter for Phase 0.
  - If a funded business case requires WhatsApp earlier, implement via the
    WhatsApp Business Cloud API through a partner (Twilio or 360dialog) only.

Summary
- Goal: evaluate WhatsApp as a messaging surface for GAIA and recommend a Phase 1 path (integration/not-ready/deferral).
- Short recommendation: For Phase 1, pursue the WhatsApp Business Cloud API via a partner (Twilio or 360dialog) **only if** we can justify the cost and business verification friction; otherwise defer WhatsApp until post-v0.20.0 and focus on Telegram for Phase 0.

Integration paths (concrete pros/cons)

1) WhatsApp Business Cloud API (Meta-hosted)
  - Pros:
    - Official, supported API with predictable behaviour and low ban risk.
    - Scales to many users and supports media messages, templates, and webhooks.
    - Legal / terms-of-service alignment - suitable for production.
  - Cons:
    - Requires Business Manager verification, a phone number, and template approval for outbound messages.
    - Conversations are template-gated for initial outbound messages (session vs template model) which constrains UX for unsolicited streaming or voice-first flows.
    - Metadata and message content transit Meta servers - privacy implications.

2) WhatsApp Business API via partners (Twilio, 360dialog, MessageBird)
  - Pros:
    - Polished onboarding, SDKs, billing, and delivery guarantees; some partners reduce Meta verification friction.
    - Single integration point with enterprise-grade features (message queues, retries, logging).
  - Cons:
    - Adds third-party vendor costs and another privacy/metadata recipient.
    - Still subject to Meta template rules; partner doesn't eliminate wrapper constraints.

3) whatsapp-web.js (community library driving WhatsApp Web)
  - Pros:
    - Works with personal accounts, quick to prototype, free software (MIT).
    - Enables features not available via Business API (ad-hoc messages, voice notes from personal threads) and can be run locally.
  - Cons:
    - Violates WhatsApp Terms of Service for automated non-official clients; accounts are commonly banned.
    - Unreliable long-term: session invalidation, frequent breakages when WhatsApp updates Web protocol.
    - Metadata still goes to Meta (via Web client) - plus our client holds session credentials.

4) Baileys (reverse-engineered protocol)
  - Pros: low-level control, performant, community-maintained, avoids Puppeteer overhead.
  - Cons: same TOS risk as whatsapp-web.js; higher maintenance; frequent breakage after protocol changes.

Privacy posture (which servers see message data / metadata) — explicit
- Message payloads (bodies and media):
  - Business Cloud API (Meta-hosted): message bodies and media are transmitted to Meta-managed Cloud API endpoints and therefore are visible to Meta. When a partner is used, the partner may also receive or store message payloads when acting as a relay or handling media uploads.
  - Partner integrations (Twilio / 360dialog / MessageBird): partners commonly see message bodies, attachments, and delivery payloads. Their dashboards and APIs may expose message content and delivery logs according to contract.
  - Community drivers (`whatsapp-web.js`, Baileys): these run a local client that proxies traffic to Meta's WhatsApp service (WhatsApp Web). Message traffic still goes to Meta; the local client stores session credentials and may cache media locally.
- Metadata (timestamps, delivery receipts, phone numbers, connection IP/device):
  - Business Cloud API & partners: both Meta and the partner will typically have access to delivery and metadata; partners use this for billing, retries, and debugging.
  - Community drivers: Meta observes metadata via the standard WhatsApp infrastructure; the local host also retains connection-level metadata.
- Local-only data (remains on host unless explicitly forwarded):
  - Agent runtime state, ephemeral conversation context and short-lived caches are local by default. Do not persist message bodies or attachments off-host without explicit legal/PM approval.
  - Session credentials created by `LocalAuth` are stored on the host; they should be treated as secrets.
- Logging and telemetry:
  - Local logs and run artifacts may contain message bodies; treat them as sensitive and minimise body-level logging by default. Use an explicit opt-in (and legal review) before capturing or shipping message transcripts to third parties.

Summary guidance:
- For production, prefer partner-backed Business Cloud API to obtain contractual clarity about who stores or can access message content and retention policies.
- Community drivers are experimental: while session data is local, message traffic still goes through Meta and they are not a privacy-preserving alternative to official APIs.
- Document what exact data (bodies, media, metadata, logs) is persisted where, and require legal sign-off before retaining or sharing anything beyond ephemeral, host-local state.

Cost model (production)
- Business Cloud API (Meta): Meta pricing varies and often has free tier for limited messages; template messages may have per-message charges in some regions. Expect low per-message cost but operational overhead (verification) and possible per-message charges for large campaigns.
- Partner (Twilio): Typical model is per-message + monthly number fees + optional throughput/queueing. Budget: modest monthly fixed + per-message variable (example: $0.005–$0.05/msg depending on region and message type).
- whatsapp-web.js / Baileys: Zero direct provider fees, but higher maintenance cost and risk (replace suspended accounts, dev time). Not viable for reliable production.

UX implications vs Telegram (side-by-side)
- Message templates + session model (WhatsApp Business) make unsolicited streaming (e.g., long TTS or progressive voice notes) awkward: initial outbound must be a pre-approved template in many cases, while Telegram allows free-form message delivery and bot-initiated rich interactions.
- Voice notes: WhatsApp supports voice messages natively, but streaming partial audio to render progressive playback is not supported by Business Cloud API — we'd need to upload final audio or use ephemeral media sessions. Telegram supports bots sending audio and streaming-like behaviours more easily.
- Presence and discoverability: WhatsApp relies on phone numbers and a business profile; Telegram bots have usernames and deep-linking that are easier for consumer discovery and opt-in.

Recommended Phase 1 path and rationale
- Recommendation: Defer unlimited WhatsApp integration until post-v0.20.0 unless there is a funded business case requiring WhatsApp immediately. If we must ship Phase 1, target the **WhatsApp Business Cloud API via a partner (Twilio or 360dialog)** as the implementation path for production-grade users. Rationale:
  - Official APIs + partner tooling reduce ban/legal risk and provide production SLAs.
  - Although template gating reduces some UX flexibility, a partner + careful conversation design can deliver acceptable UX for business use-cases (support agents, notifications, private assistant flows initiated by users).
  - Avoid community drivers for Phase 1 due to high ban risk and maintenance burden.

Sample conversation (Business Cloud via partner)
User -> GAIA: "Hey, summarize my travel plan for May 11"
GAIA -> (incoming webhook) -> agent resolves intent and replies as a session message (no template needed if user initiated within 24h). If GAIA needs to proactively message later, it uses an approved template: "Your travel summary is ready: [link]".

Setup steps (partner-assisted Phase 1)
1. Create Meta Business Manager, complete verification (documented; can take days).
2. Provision WhatsApp Business account and phone number (via Twilio/360dialog).
3. Configure webhooks to GAIA's API server (TLS endpoint), map partner events to `gaia` adapter.
4. Implement conversation mapping: webhook -> adapter -> agent -> reply via partner API.
5. Submit required message templates for proactive messages.

Phase 1 investigation tasks & effort estimate
- Spike A — whatsapp-web.js prototype (safety spike): 2–3d engineer. Deliverable: short-running prototype, documented ban-rate evidence (public threads & maintainers), and a decision note explaining operational risk.
- Spike B — Business Cloud API free-tier onboarding: 3–5d engineer (mainly procedural). Deliverable: documented steps, expected Meta verification time, sample webhook round-trip, template submission trial.
- Design task — UX mapping vs Telegram: 1–2d PM/Designer to map conversational flows and constraints (template gating, voice notes, streaming). Deliverable: side-by-side UX matrix and concrete recommendations.
- Integration spec — Partner integration adapter + auth + message mapping: 3–5d engineer to draft Phase 1 implementation PR scope and API shapes.
- Total Phase 1 investigation estimate: 2–3 weeks (one engineer + PM part-time).

Risks & mitigations
- Account bans (web.js/Baileys): risk -> avoid for Phase 1.
- Long Meta verification times: mitigation -> use partner onboarding guidance and start process early, budget for delays.
- Privacy concerns: mitigate via docs, opt-in flows, minimal logging, and contractual review with partners.

Decision outcome & next steps
- Primary choice: Defer or build on Business Cloud API via partner. Phase 1 should focus on investigation spikes and a partner-backed adapter if the project has commercial justification.
- Next steps (Phase 1): run Spike A and Spike B in parallel, finalize integration spec, then open implementation PR scoped to partner adapter + tests + docs.

Links and references
- WhatsApp Business Cloud API: https://developers.facebook.com/docs/whatsapp
- whatsapp-web.js: https://github.com/pedroslopez/whatsapp-web.js
- Baileys: https://github.com/WhiskeySockets/Baileys
