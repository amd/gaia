---
name: log-triage
description: Find what actually went wrong in log files. Use when the user asks for a root cause, an incident summary, anomalies, errors, failures or "what happened" in system, application, server, cluster, device or audit logs. Its triage_logs tool reads every file over its full range, keeps steady baseline noise apart from anomalies, finds bursts with exact first and last timestamps, and lists the hosts and ids an incident hit.
license: MIT
version: 1.1.0
metadata:
  gaia:
    security_tier: community
    tools:
      - name: triage_logs
        description: Parse, template and rank log files; return anomalies, bursts, baseline, coverage, entities and checks.
        parameters:
          paths:
            type: array
            required: true
          glob:
            type: string
            required: false
            default: ""
          timestamp_regex:
            type: string
            required: false
            default: ""
          level_regex:
            type: string
            required: false
            default: ""
          host_regex:
            type: string
            required: false
            default: ""
          entity_regex:
            type: string
            required: false
            default: ""
          bucket:
            type: string
            required: false
            default: ""
          top_n:
            type: integer
            required: false
            default: 10
          output_path:
            type: string
            required: false
            default: ""
        returns:
          type: object
    tools_required:
      - list_files
      - read_file
      - run_python
    provenance:
      source: starter-pack
---

# Log Triage

The loudest message in a log is usually routine. The incident is the rare,
severe message that hit many hosts at once, often in a few minutes of a
multi-day file. Counting by volume finds the noise; this skill finds the
incident.

## Hard rules

- **Call `log-triage/triage_logs` first.** It reads every file over its full
  range (streaming, read-only, any size, `.gz` included). Never read a large
  log into the conversation instead: you will see the start and report it as
  the whole.
- **Do not report from a misread file.** `checks.ok` must be true. If
  `parsed_share_ok` is false, call again with `timestamp_regex` (and
  `level_regex` / `host_regex`) fitted to the file's `unparsed_samples`.
- **Deliver lists from `triage_logs` output; do not rebuild them by hand.**
  A list of bad blocks, requests, sessions or jobs is `entities.list`. For
  more than 150 ids call again with
  `output_path="<folder>/anomalous_entities.csv"` and give the user that file.
  Say how many were flagged out of `total`.
- **Baseline is not the incident.** `baseline` templates are routine even at
  ERROR or FATAL. Report them once as background, never in an anomaly total.
- **Quote, do not round.** Copy every timestamp, count and host number
  exactly as the result prints it.
- **Use only the logs you were given**: not a public dataset you recognise,
  not memory, not a labels or expected-results file beside the logs (mention
  such a file and leave it unused).

## Procedure

1. `triage_logs(paths=[folder or files])`. Add `glob="*.log"` for a mixed
   folder, `bucket="1min"` for a short incident, `entity_regex` for the id the
   user asked about.
2. Check `checks`, then `coverage`: every file, its first and last timestamp,
   and `times_in` (UTC or as written).
3. Read `anomalies` (ranked by severity, hosts hit, rarity) and `bursts`
   (exact `start` / `end`; `vs_quiet_window` shows what `rose` and what
   `fell_silent`).
4. Read the lines before the first burst line with the `read_file` call in
   `next_step`. Causes come before their symptoms.
5. Write a blameless postmortem: Summary, Impact, Detection, Timeline,
   Contributing factors, Mitigation, Action items (owner from the user's
   context, else "owner: to assign"), Confidence. Close with Baseline and
   Limits, and repeat every warning that limits the answer.

Example (invented): *"Summary: disk controller faults on 31 of 40 nodes,
2024-03-06 13:13:00 to 13:16:58 UTC, 120 FATAL lines from a template seen
nowhere else in the 3.5-day log. Impact: 31 jobs aborted. The 3,002 request
timeouts are baseline: steady across the whole range."*

## References (paths relative to this skill's folder)

- `references/result-fields.md` - read when a result field, clock offset,
  quiet host or warning needs interpreting.
- `references/entity-lists.md` - read when the user asks for a list of
  anomalous ids, or the list looks too broad.
- `references/postmortem.md` - read when writing the report, for what each
  section must contain.
- `references/fallback-python.md` - read only when `triage_logs` is not
  available or a file stays unparsed with `timestamp_regex`.
