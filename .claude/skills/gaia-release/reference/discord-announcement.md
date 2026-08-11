# Discord release announcement — template + format rules

The house format for the `#announcements` Discord post, cut at the end of Phase 6 of the
[`gaia-release`](../SKILL.md) skill. This is the v0.22.0 shape — the format the maintainer
actually posts.

Populate the highlights from the just-shipped `docs/releases/v<version>.mdx`, in the same
voice as the notes: value-prop first, plain, engaging, no fluff, no emoji.

## Template (copy verbatim, fill the bracketed fields)

Reproduce the markdown exactly — the role mention, the backticked version, the fenced
install block, and the `- ` bullets are all load-bearing.

````
@gaia **GAIA v<version> Release**

Hi all, `v<version>` is <one-clause framing — examples: "a patch release with X and Y", "out — focused on X, Y, and Z", "a hotfix for X">. Upgrade in one command:

```
npm install -g @amd-gaia/agent-ui
gaia-ui
```

Currently tested on Strix Halo w/ 32GB+ or Radeon GPU w/ 24GB+ on Windows and Ubuntu.

**What's new in v<version>**

- **<Highlight title>** — <One-sentence what + why, plain English. 1–2 sentences max per bullet.>
- **<Highlight title>** — <...>
- **<Highlight title>** — <...>

The agent can search files, run commands, and use MCP tools — but only after you approve each action.

Agent UI guide: https://amd-gaia.ai/docs/guides/agent-ui
v<version> release notes: https://amd-gaia.ai/docs/releases/v<version>

Note this is a beta release of the UI and the Email Agent, if you notice any bugs or issues please report them here or create an issue!
````

## Format rules

- **Open with the `@gaia` role mention**, then the bold title: `@gaia **GAIA v<version> Release**`.
- The version in the "Hi all" line is **backticked** — `` `v0.22.0` ``.
- The `npm install` / `gaia-ui` lines **go in a fenced code block**, with a blank line before it.
- `**What's new in v<version>**` is **bold with no trailing colon** — it is not a `##` heading.
- Highlights **are `- ` bullets**, each `- **Title** — Description`.
- 3–5 highlights for a patch, 5–7 for a minor. Anything more becomes a wall of text.
- The "agent can search files…" and "Note this is a beta release of the UI and the Email Agent…" sentences are **fixed boilerplate** — copy them verbatim every release.
- First-line framing reflects the release character: patches = "a patch release with X and Y"; minor/major = "out — focused on X, Y, and Z"; hotfixes = "a hotfix for X". Match the actual scope, don't oversell.
- Carry the release notes' beta framing into the announcement where it applies — this channel is where users hit the rough edges first, so don't oversell here.

