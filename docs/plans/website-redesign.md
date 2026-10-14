# amd-gaia.ai redesign: work backwards from the reader

**Status:** implemented on `kalin/website-starfield-rc`. The binding rules now live in
[The website](../spec/gaia-design-language.mdx#the-website) section of the design
language; this page records who the site is for and why the direction was chosen.

amd-gaia.ai went flat in #4186: the animated star chart was deleted under a "no
decorative motion" rule, and the gold-on-night palette became graphite and copper.
The page read as a docs index rather than a product. Release candidates were also
invisible in "Recent releases", and the RC download sat at the very foot of the page.

## Who lands on amd-gaia.ai

Ranked by how much the landing page has to serve them.

| # | Reader | Arrives from | Wants in the first 10 seconds | What convinces them |
|---|---|---|---|---|
| 1 | **AI PC owner / enthusiast**: has a Ryzen AI laptop or Strix Halo box, curious what the NPU is for | AMD marketing, Reddit, YouTube, a launch post | "What does this do on *my* machine, and is it free?" | Seeing it run locally: private, offline, no token bill. One obvious download. |
| 2 | **Developer evaluating a local agent stack** | GitHub, Hacker News, docs search | "Is this real, maintained, and can I script it?" | Live proof of momentum (stars, release cadence, RCs). A one-line install. |
| 3 | **Early adopter / tester**: wants the next build before it ships | Discord, release notes, GitHub | "Where's the latest RC?" | The RC directly under the download, and in the release list. |
| 4 | **OEM / enterprise evaluator**: judging whether GAIA is credible enough to bundle | AMD partner channels | "Is this an AMD-grade product?" | AMD brand, polish, restraint. |

What follows for the page: one message and one download, the RC as a quiet line under
it, live proof (stats and a release stream that includes RCs), and exactly one ambient
motion layer that stays behind the text. Nothing should overwhelm a first-time reader.

## How the direction was chosen

Throwaway HTML templates carried the same content so they compared like for like:

- **Constellation** (the old gold star chart, refined): rejected as a step back.
- **Retro starship console** (a TNG-era look): the favourite, but dropped. Its frame
  and peach/lavender palette are the signature of a Paramount-owned interface, which
  is a takedown and brand risk for AMD.
- **Viewscreen** (stars streaking toward the viewer, ice blue): kept the motion, but
  too bland on its own.
- **Viewscreen in AMD brand**: chosen. Black ground, white type, AMD red, AMD orange
  for RCs only, and Antonio for display type (tall condensed caps, the most "starship"
  of the free faces tried; Saira Extra Condensed, Michroma and Oxanium were the
  alternatives).

Decisions made along the way:

- **Red is website-only.** The Agent UI and the TUI keep the shared copper; changing
  them is a separate, larger decision.
- **Dark-only.** The starfield is the identity, and it does not survive a white ground.
- **Brand check before launch.** AMD's public brand guidelines are from 2018; the
  current set is on the partner portal. Production should use AMD's licensed Klavika
  webfont if Brand provides it (Titillium Web stands in for it).
