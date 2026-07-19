# Changelog — `gaia-agent-email`

All notable changes to the GAIA Email Triage agent package are recorded here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/); the REST
contract version is tracked separately as
`gaia_agent_email.contract.SCHEMA_VERSION` (see `CONTRACT.md`).

## [Unreleased]

### Changed

- **Daemon-supervised scheduling (V2-15, #2156).** When the GAIA daemon spawns
  the sidecar it sets `GAIA_DAEMON_SUPERVISED=1`; in that mode the sidecar's two
  embedded clocks — the daily `BriefingScheduler` (#1918) and the one-shot
  `EmailJobScheduler` polling thread (#1919) — no longer start. The daemon owns
  a single reconciled clock and drives those jobs itself, so a scheduled brief
  or send now fires even with the web UI and CLI closed, and can no longer be
  silently killed when an idle sidecar is reaped.

  This is **additive and gated by supervision context, not a deletion**: a
  standalone `gaia-agent-email serve`, a bare integrator, or a
  `CustodyProvider` deployment never sees the env var and keeps both embedded
  clocks live exactly as before. The frozen `/v1/email/*` REST contract and
  `SCHEMA_VERSION` are unchanged.

### Added

- `gaia_agent_email.supervision.is_daemon_supervised()` — detects the daemon
  supervision handshake (the env-var name is owned by core in
  `gaia.daemon.constants`, so daemon and sidecar can never drift).
- `gaia_agent_email.daemon_migration` — adapter that lifts the embedded clocks'
  jobs (pending `schedule_store` one-shots + the enabled daily briefing) into
  the daemon clock **exactly once** via the core reconciler's migration ledger,
  and asserts no job is silently dropped in the process.
