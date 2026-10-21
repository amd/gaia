# Lists of anomalous entities

When the user asks for a list of bad items (blocks, requests, sessions, jobs,
backends), **deliver the list from `triage_logs` output; do not rebuild it by
hand.** `entities` is built only from anomaly templates, inside each anomaly's
own window, never from baseline errors.

- `entities.list` is the ready list (up to 150 ids). For a longer one, call
  again with `output_path="<folder>/anomalous_entities.csv"` (or `.txt`, one
  id per line) and give the user that file. Say how many were flagged out of
  `total` (`share`) and which template put each group there (`by_template`).
- `kinds_used` says which id pattern the tool learned from the data (for
  example `blk_N` with `regex` `(blk_-?\d+)`), and `kinds_skipped` why other
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
