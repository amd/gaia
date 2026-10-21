# Reading the triage_logs result

Read the result in this order.

1. **`checks`** - `ok` must be true (template and level counts add up to the
   parsed entries). If `parsed_share_ok` is false, a file in
   `files_below_parsed_share` was misread: look at its `unparsed_samples`,
   then call again with `timestamp_regex` (and `level_regex` / `host_regex` if
   needed) fitted to those lines. Do not report from a misread file.
2. **`coverage`** - every file with format, first and last timestamp, line,
   entry, continuation (stack-trace) and unparsed counts. `times_in` says
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
5. **`baseline`** - templates steady across the range. Routine, even when they
   say ERROR or FATAL: a platform that logs thousands of FATAL lines a day is
   telling you its normal. Report them once as background; never add them to
   an anomaly or alert total.
6. **`timeline`**, **`clock_offsets`**, **`quiet_hosts`**, **`warnings`** -
   the cross-file order of first occurrences, steady offsets measured on ids
   that two files share (clock skew, a pipeline delay, or close to whole hours:
   a time zone written without an offset), hosts that stopped logging early,
   and everything the tool could not do. Repeat every warning that limits your
   answer. When `clock_offsets` reports a skew, say that cross-file order
   inside that offset is uncertain.

## Next steps

- **Read before the first burst line.** `next_step` names it:
  `read_file(file_path, start_line=line - 30, end_line=line + 10)`. Causes come
  before their symptoms; a flood of timeouts after a hardware fault is the
  effect.
- **Narrow when it helps.** `glob="*.log"` for a mixed folder, `bucket="1min"`
  for a short incident, `entity_regex` for the id the user asked about,
  `output_path` (a new `.csv` or `.txt` for the entity list, `.json` for full
  detail, in a folder you may write) when the user wants the full list.

## Tuning for one platform

Pin `timestamp_regex`, `host_regex` and `entity_regex` to the platform's
format, and keep a list of the templates the team already knows are harmless,
so the ranking starts from the unfamiliar.
