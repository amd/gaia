# Spike B - WhatsApp Business Cloud API onboarding trial

Status: in-progress
Owner: engineering / PM
Start date: 2026-05-03

Objective
- Attempt the Business Cloud API free-tier onboarding and verify webhook round-trip, template submission, and expected Meta business verification friction.

Success criteria
- Able to register a test WhatsApp Business Account (or provision via partner sandbox), register a webhook, and send/receive a test message.
- Document time-to-verify for Business Manager and template approval steps.

How to run the trial (high level)
1. Create a Meta Business Manager account and begin verification (requires legal company info).
2. Option A: Use direct Business Cloud API sandbox (if available) — follow Meta docs.
3. Option B (recommended for Phase 1): Provision a partner sandbox (Twilio/360dialog) with a test number and follow their webhook docs.
4. Configure a TLS endpoint to receive webhooks from partner / Meta and map to GAIA adapter.

Notes
- Meta verification can take days; partners often streamline onboarding and provide sandbox/test numbers.
- Templates must be submitted and approved for proactive outbound messages; this step can also take time.

Deliverables
- `docs/spikes/whatsapp-cloudapi.md` (this doc, updated with findings)
- Short onboarding log with screenshots and time estimates.
