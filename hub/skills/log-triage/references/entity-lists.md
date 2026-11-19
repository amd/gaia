# Lists of anomalous entities

When the user asks for a list of failed items (requests, sessions, jobs,
orders, backends), **deliver the list from `triage_logs` output; do not rebuild it by
hand.** `entities` is built only from anomaly templates, inside each anomaly's
own window, never from baseline errors.

- `entities.list` is the ready list (up to 150 ids). A longer one needs
  `output_path="<folder>/anomalous_entities.csv"` (or `.txt`: a `#` comment
  line, then one id per line). Pass it on the first call when the user asked
  for a file: a second call reads every log again. Write it outside the log
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
  A warning that most entities are flagged means the list is not the
  anomaly: report that instead of the list.
- If you must build a list yourself, use only the anomaly templates the tool
  lists (their `id`s and `template` text), never every WARN or ERROR line,
  and check your share against the tool's.
