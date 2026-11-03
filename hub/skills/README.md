# GAIA skills

The hub's **skills** lane. A skill is a reusable capability *any* agent composes —
a procedure written for a model to follow, plus the tools it needs. Unlike an
agent, it does not run on its own, so it is a separate lane with its own package
format and publish contract ([#2467](https://github.com/amd/gaia/issues/2467)).

This directory is the AMD **starter pack** (#893): seventeen worked examples, each
demonstrating a different platform primitive (RAG, scratchpad SQL, memory,
browser, file I/O, CLI grants, email, audio and music, image generation, and a
skill that ships its own tool in `rss-digest`). They are meant to be copied and edited,
not used verbatim — see the
[starter skills guide](https://amd-gaia.ai/docs/guides/starter-skills).

## Package format

One directory per skill, flat:

```
hub/skills/
└── <name>/
    ├── SKILL.md     # required — YAML front matter + the procedure body
    ├── tools.py     # optional — Python tools the skill registers
    └── scripts/     # optional — anything else it ships
```

**There is no `gaia-agent.yaml` here.** Agents and components need one because
the hub has to know how to build, install, and run a package. A skill has none of
that surface: `SKILL.md`'s front matter already carries the identity
(`name`, `version`, `description`, `license`) and everything the hub gates on
(`metadata.gaia.security_tier`, `permissions`, `tools_required`, `provenance`).
`gaia skill publish ./<name>/` takes its identity and gates from that front
matter and ships the directory beside it as the bundle — a second manifest would
only create two places for the same facts to disagree.

Keep `name` equal to the directory name. `gaia skill import` installs under the `name` in the file even if the folder is called something else.

## Use one

Every skill here is published to the hub with each GAIA release, signed by the AMD
key GAIA trusts out of the box (v0.25.4 and later). Browse them at https://amd-gaia.ai/hub.

```bash
gaia skill search report
gaia skill install research-report
```

Or copy one from a clone:

```bash
gaia skill import hub/skills/research-report   # copy into ~/.gaia/skills/
gaia skill list
gaia skill info research-report --body
```

Imported skills are always stamped `security_tier: experimental`, whatever the
file claims — the tier records what *you* verified, not what the author asserted.

## Publishing

**By pull request only.** Every final release tag runs
[`release_skills.yml`](../../.github/workflows/release_skills.yml), which signs and
publishes each skill whose `version` is not on the hub yet. Hub versions are
immutable, so **bump `version:` whenever you change a skill** — the release fails
on a published version whose files changed. Before opening a PR:

```bash
gaia skill audit ./hub/skills/<name>/    # exit 0 (ALLOW) is what merges cleanly
```

The **Skill Audit (deterministic gate)** check is required on every PR that
touches a skill — in `hub/skills/`, anything other than `ALLOW` fails it.
Merging is not a tier promotion: the audit verdict plus publisher signing decide
the tier.

Contributing your own skill? It goes **here**, in `hub/skills/<name>/`, by pull
request. Once it merges, the next final release publishes it with the rest. The
walkthrough is [Build and Publish a Skill](https://amd-gaia.ai/docs/guides/building-skills);
the format is in the [skill format spec](https://amd-gaia.ai/docs/spec/agent-skills).
