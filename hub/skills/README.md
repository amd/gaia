# GAIA skills

The hub's **skills** lane. A skill is a reusable capability *any* agent composes —
a procedure written for a model to follow, plus the tools it needs. Unlike an
agent, it does not run on its own, so it is a separate lane with its own package
format and publish contract ([#2467](https://github.com/amd/gaia/issues/2467)).

This directory is the AMD **starter pack** (#893): fourteen worked examples, each
demonstrating a different platform primitive (RAG, scratchpad SQL, memory,
browser, file I/O, image generation). They are meant to be copied and edited,
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

`name` must equal the directory name; a mismatch is a load error, not a warning.

## Use one locally

```bash
gaia skill import hub/skills/research-report   # copy into ~/.gaia/skills/
gaia skill list
gaia skill info research-report --body
```

Imported skills are always stamped `security_tier: experimental`, whatever the
file claims — the tier records what *you* verified, not what the author asserted.

## Publishing

**By pull request only.** The hub's publish endpoint is gated on a
maintainer-held token, so a contributor never handles one; `gaia skill publish`
is what maintainers run *after* a PR merges. Before opening one:

```bash
gaia skill audit ./hub/skills/<name>/    # exit 0 (ALLOW) is what merges cleanly
```

The **Skill Audit (deterministic gate)** check is required on every PR that
touches a skill — `BLOCK` fails outright, `REVIEW` holds for maintainer sign-off.
Merging is not a tier promotion: the audit verdict plus publisher signing decide
the tier.

Contributing your own skill? It goes in
[`skills/community/`](../../skills/community/), not here. Full route in the
[publishing guide](https://amd-gaia.ai/docs/guides/hub-publishing) and the
[skill format spec](https://amd-gaia.ai/docs/spec/agent-skills).
