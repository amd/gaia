# UX conventions

What the maintainer asked for, or objected to, while using the original dashboard. The template already does
all of this; keep it when adding pages.

## Glance first

- A **Needs attention** bar at the top lists every amber or red card with the figure that set its colour, and a
  **Going well** bar lists the green ones worth bragging about. The question the page answers is "what needs me
  today?".
- Colour is never alone: every status colour comes with a label and a visible reason, not a hover-only title.
- **One number per card.** Explanations go in the card's (i) bubble: the definition, the bands, and the band's
  source. Median and P90 sit side by side.
- **False alarms are worse than missing data.** Before colouring anything, ask whether it could be red for a
  boring reason (a backlog cleanup raising time-to-close, two days of noise, a workflow meant to fail).

## Information architecture

- Put each metric on the page whose question it answers: Users = who gets it, Growth = how fast it grows
  (rates only), Developers = how work flows, CI = is the build healthy, Evals = how good it is.
- Each metric lives in one place. Duplicates across pages read as contradictions the moment they differ.
- Distributions get histograms (time to close), not a median line over time.
- Keep raw traction numbers off the public website; a public page shows curated figures only.

## Controls

- Ranges 24h, 7d, 30d, 90d, 1y, All; group by Auto, Day, Week, Month. State lives in the URL.
- Every chart obeys the range. Drag to zoom with a reset; legends toggle series; value labels on demand.
- A table view under every chart. Long cells truncate and expand on click.
- Tables sort on every column: ascending, descending, then back to the original order on the third click.
  Sort by value, keep "—" last, support the keyboard and `aria-sort`, and survive redraws. Choose each table's
  default order deliberately (regressions first, most failures first).
- A "Hide maintainers / Hide bots" toggle recomputes every number on the page, so outside contributions can be
  seen on their own.

## Empty and partial states

- An empty chart says why: "starts after the second nightly snapshot", "no cohort has had 90 days yet",
  "collecting since Oct 13".
- Charts whose source keeps limited history say where collected data begins.

## Look

- Minimum 14 px text, even in half-width charts.
- An upright (i) glyph, keyboard reachable, its tooltip hides on focus-out, `aria-label` names the card.
- A categorical palette validated in light and dark; tabular numerals for figures.
- `noindex`, `no-referrer`, no third-party requests from the private page.
