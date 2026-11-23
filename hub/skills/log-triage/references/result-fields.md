# Reading the triage_logs result

Read the result in this order.

1. **`checks`** - `ok` must be true (template and level counts add up to the
   parsed entries). If `parsed_share_ok` is false, a file in
   `files_below_parsed_share` was misread: look at its `unparsed_samples`,
   then call again with `timestamp_regex` (and `level_regex` / `host_regex` if
   needed) fitted to those lines. Do not report from a misread file.
   `time_ranked` is false when a file in `files_without_timestamps` gave no
   usable timestamps: its lines were counted but not ranked by time, so no
   anomaly or burst can come from it. Fit `timestamp_regex` to its
   `line_samples`; if the lines truly carry no timestamp, say so.
2. **`coverage`** - every file with format, first and last timestamp, line,
   entry, continuation (stack-trace) and unparsed counts. `undecodable_chars`
   counts characters that were not valid in the file's `encoding` and were
   read as U+FFFD: ids and text on those lines may be damaged, so say so. `times_in` says
   whether times are UTC (offsets in the logs were converted) or as written.
3. **`anomalies`** - templates ranked by severity, then hosts hit, then rarity.
   Each has `why` (short window, new, spike against its own median,
   escalating, many hosts at once), exact `first` / `last` / `peak_at` with
   `file` and `line`, `hosts` and `host_sample`.
4. **`bursts`** - windows where errors and anomaly lines rose far above their
   median: exact `start` / `end`, `lines`, `hosts`, `first_line`, the
   templates found `only_here`, and `vs_quiet_window`: the same-length quiet
   stretch just before, with what `rose` and what `fell_silent` (a heartbeat
   or a host's routine traffic stopping is often the clearest clue).
5. **`baseline`** - templates steady across the range. Report them once as
   background; never add them to an anomaly or alert total.
6. **`persistent_errors`** - baseline templates at ERROR or FATAL, listed when
   nothing is anomalous or the range is under two hours. Each is steady across
   the whole range: either routine, or an outage that covers the whole log.
   The log alone cannot tell. Say which reading the evidence supports (what
   the user reported, whether any stretch of the log is free of the error,
   whether requests still succeed beside it), and ask the user if you cannot.
7. **`timeline`**, **`clock_offsets`**, **`quiet_hosts`**, **`warnings`** -
   the cross-file order of first occurrences, steady offsets measured on ids
   that two files share (clock skew, a pipeline delay, or close to whole hours:
   a time zone written without an offset), hosts that stopped logging early,
   and everything the tool could not do. Repeat every warning that limits your
   answer. When `clock_offsets` reports a skew, say that cross-file order
   inside that offset is uncertain.

## Next steps

- **Read before the first burst line.** `next_step` names it:
  `read_file(file_path, start_line=max(1, line - 30), end_line=line + 10)`.
  Causes come before their symptoms; a flood of timeouts after a hardware fault
  is the effect.
- **Narrow when it helps.** `glob="*.log"` for a mixed folder, `bucket="1min"`
  for a short incident, `entity_regex` for the id the user asked about,
  `output_path` (a new `.csv` or `.txt` for the entity list, `.json` for full
  detail, in a folder you may write) when the user wants the full list.

## Tuning for one platform

Pin `timestamp_regex`, `host_regex` and `entity_regex` to the platform's
format, and keep a list of the templates the team already knows are harmless,
so the ranking starts from the unfamiliar.
