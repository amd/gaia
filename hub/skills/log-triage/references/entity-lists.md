# Lists of anomalous entities

When the user asks for a list of failed items (requests, sessions, jobs,
orders, backends), **deliver the list from `triage_logs` output; do not rebuild it by
hand.** `entities` is built only from anomaly templates, inside each anomaly's
own window, never from baseline errors.

- `entities.list` is the ready list (up to 150 ids). A longer one needs
  `output_path="<folder>/anomalous_entities.csv"` (or `.txt`: a `#` comment
  line, then one id per line). The CSV has one row per id with its evidence:
  `templates` and `template_text` (the anomaly templates that name it),
  `severity` (the worst of them), `seen_from` / `seen_to` (time buckets),
  `also_in_routine` (yes when routine templates name it too) and
  `template_share` (the share of all entities its rarest template touches).
  `output` returns `path`, `flagged`, `total`, `share` and `plausible`; use
  that file as the deliverable, or filter its rows. Pass it on the first call
  when the user asked for a file: a second call reads every log again. Write it outside the log
  folder, or under a name of its own. The tool never overwrites a file, and
  on later runs it skips the files it wrote.
- If `status` is `partial`, the analysis is complete but the file was not
  written; `error` says why and `fix` what to do. Tell the user.
- Say how many were flagged out of `total` (`share`) and which template put
  each group there (`by_template`).
- `kinds_used` says which id pattern the tool learned from the data (for
  example `req-H` with `regex` `(req-[0-9a-fA-F]+)`), and `kinds_skipped` why other
  id-shaped tokens (peers, users, client addresses) were left out. If the user
  means a different id, call again with `entity_regex` set to that kind's
  `regex`.
- If `narrowed` is present, the broad anomaly templates were left out because
  they matched the baseline; the list comes from the narrower ones. Say so.
- `entity_list_warning` (and `plausible: false`) means more than a third of
  all entities are flagged. A criterion that matches most entities is not an
  anomaly criterion: it describes normal operation. Do not deliver that list.
  `broadest_template` names the template that touches the most entities.
  Tighten with the tool, not by hand: `entity_max_share=0.33` leaves out
  templates that are not rare relative to all entities, and
  `entity_min_level="ERROR"` keeps error-bearing templates only; `filters`
  then says which templates each left out. If the incident really touched
  that share, report the share as the finding.
- If you must build a list yourself, use only the anomaly templates the tool
  lists (their `id`s and `template` text), never every WARN or ERROR line,
  and check your share against the tool's.
