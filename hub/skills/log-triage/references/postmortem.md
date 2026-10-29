# Report shape: a blameless postmortem

Describe systems and events, not people. In this order:

1. **Summary** - what happened, in one or two sentences, with the exact window.
2. **Impact** - in user terms: which requests, jobs, sessions or hosts failed,
   how many (`lines`, `hosts`, flagged `entities`), for how long.
3. **Detection** - the first signal and when it appeared (`first` of the
   earliest anomaly, the burst `first_line`), and what would have caught it.
4. **Timeline** - UTC when `times_in` says so; the lines before the first
   anomaly, then the sequence across files. Use the `timeline` entries and
   each burst's `start` and `end` exactly as the tool printed them (same
   digits, same milliseconds); never round, re-derive or estimate them.
5. **Contributing factors** - plural. The trigger and what let it spread or
   last, each with its evidence, worded as likely unless the log states it.
   Name symptoms as symptoms.
6. **Mitigation and recovery** - what the logs show ended it (a restart, a
   failover, the burst `end`); if nothing in the logs shows recovery, say so.
7. **Action items** - each with an owner taken from the user's context, or
   marked "owner: to assign"; never invent a person.
8. **Confidence** - high, medium or low, and why: parse coverage, clock
   offsets, files or hosts with no data, a cause inferred rather than logged.

Close with **Baseline** (the top routine templates and why they are routine)
and **Limits** (unparsed lines, gaps, warnings).

Example (invented): *"Summary: disk controller faults on 31 of 40 nodes,
2024-03-06 13:13:00 to 13:16:58 UTC, 120 FATAL lines from a template seen
nowhere else in the 3.5-day log. Impact: 31 jobs aborted. The 3,002 request
timeouts are baseline: steady across the whole range, about 9 per 15 minutes."*
