---
name: log-triage
description: Find what actually went wrong in log files. Use when the user asks for a root cause, an incident summary, anomalies, errors, failures or "what happened" in system, application, server, cluster, device or audit logs. Its triage_logs tool reads every file over its full range, keeps steady baseline noise apart from anomalies, finds bursts with exact first and last timestamps, and lists the hosts and ids an incident hit.
license: MIT
version: 1.2.0
metadata:
  gaia:
    security_tier: community
    tools:
      - name: triage_logs
        description: Parse, template and rank log files; return anomalies, bursts, baseline, coverage, entities and checks, and write the anomalous-entity list with its evidence to output_path.
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
          entity_min_level:
            type: string
            required: false
            default: ""
          entity_max_share:
            type: number
            required: false
            default: 0.0
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
  `level_regex` / `host_regex`) fitted to the file's `unparsed_samples` or
  `line_samples`. If its lines truly carry no timestamp, say that the file
  was counted but not ranked by time.
- **Deliver lists from `triage_logs` output; do not rebuild them by hand.**
  A list of failed requests, sessions or jobs is `entities.list`. When the
  user wants a list file, pass `output_path` on the first call (a new `.csv`
  or `.txt`, outside the log folder or under a name of its own), so the logs
  are read once; `output` returns the path, the count and the `share` of all
  entities. `status: partial` means the file was not written: say so, with
  the `error`. Say how many were flagged out of `total`.
- **A list of most entities is not an anomaly list.** When
  `entity_list_warning` is present (over a third flagged), do not deliver the
  list: call again with `entity_max_share=0.33` (templates rare relative to
  all entities) or `entity_min_level="ERROR"` (error-bearing templates only),
  or report the share itself as the finding.
- **Baseline is not the incident, unless it is the whole log.** Report
  `baseline` templates once as background, never in an anomaly total. Steady
  ERROR or FATAL templates in `persistent_errors` are either routine or an
  outage that covers the whole log: say which reading the evidence supports,
  and ask the user when the log cannot tell.
- **Quote, do not round.** Copy every timestamp, count and host number
  exactly as the result prints it.
- **Base every finding on lines in the logs you were given**; do not fill
  gaps from memory.

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

Example (invented): *"Summary: storage controller resets on 17 of 24 hosts,
2025-03-05 07:18:51 to 07:21:49 UTC, 90 FATAL lines from a template seen
nowhere else in the 2-day log. Impact: 17 jobs aborted. The 1,440 request
timeouts are baseline: steady across the whole range."*

## References (paths relative to this skill's folder)

- `references/result-fields.md` - read when a result field, clock offset,
  quiet host or warning needs interpreting.
- `references/entity-lists.md` - read when the user asks for a list of
  anomalous ids, or the list looks too broad.
- `references/postmortem.md` - read when writing the report, for what each
  section must contain.
- `references/fallback-python.md` - read only when `triage_logs` is not
  available or a file stays unparsed with `timestamp_regex`. It inventories
  with `list_files`, samples with `read_file` and parses with `run_python`.

## Fork this

Pin `timestamp_regex`, `level_regex`, `host_regex` and `entity_regex` in step 1
to your platform's log format, and add the templates your team already knows
are harmless to a baseline list, so the report starts from the unfamiliar.
