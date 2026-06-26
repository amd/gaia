---
name: "integrate-hub-agent"
description: "Embeds one of this repo's pre-built hub agents (under hub/agents/) into a developer's own application. Use when a developer wants to integrate, add, embed, or wire a GAIA hub agent into their app, project, or codebase — e.g. 'integrate a hub agent into my app', 'add the <name> agent to my project', 'embed a GAIA agent', 'how do I use the email/analyst/code agent in my code'. Not for authoring a new agent (use gaia-agent-builder) or releasing/publishing one (use agent-hub-release)."
---

# Integrating a GAIA Hub Agent

This repo's `hub/` ships pre-built agents, each packaged for a developer to drop into
**their own** application. The work: learn the layout and the two integration shapes,
pin down **which** agent they mean, then **hand off to that agent's own guidance** —
where the real, current, agent-specific steps live.

The recipe is **agent-agnostic.** Any agent named below (e.g. npm `agent-email`) is a
labeled example of its shape, never the subject. Discover the real agent set live —
never trust a hardcoded list.

## Step 1 — Discover the current agents

Hub agents are grouped by runtime: `hub/agents/<runtime>/<name>/`. **List the tree
now** rather than assuming — the set changes:

```bash
ls hub/agents/                 # the runtimes (currently: npm, python, cpp)
ls hub/agents/npm/             # npm agents (e.g. agent-email)
ls hub/agents/python/          # python agents (e.g. analyst, code, jira, summarize, …)
ls hub/agents/cpp/             # cpp agents (may be empty)
```

Treat whatever the listing returns as authoritative. An empty runtime directory just
means no agents of that shape ship today. `hub/agents/python/README.md` is a useful
catalog of the python set and the example agents (`hello-world`, `word-count`,
`doc-search`) if the developer is unsure what's available.

## Step 2 — Identify which agent the developer wants

Pin down a single `<runtime>/<name>` before integrating:

- If the developer named an agent ("the email agent", "analyst"), match it against the
  listing. Three names can diverge — watch for it: the friendly name, the **directory**
  (`<name>`), and the registry **`id`** in `gaia-agent.yaml` need not match (the
  `analyst` directory registers as id `data`). The directory is how you find the
  package; the **`id`** is what you pass when invoking a python agent (Step 3).
- The same capability can ship in more than one runtime (the email agent is npm
  `agent-email` **and** python `email`). Confirm the runtime, since it picks the shape:
  a JS/TS/Electron app wants the npm package; a Python app wants the python package.
- If they described a capability ("triage my inbox", "analyze a CSV"), skim candidate
  agents' `gaia-agent.yaml` `description`/`tags` (python) or `package.json`
  `description` (npm) to find the match, then confirm with the developer.

## Step 3 — Know the two integration shapes

They are genuinely different. Use the shape to set expectations, but the agent's own
docs (Step 4) are the source of truth for exact calls.

### npm agents (`hub/agents/npm/<name>/`)

The package is a **client for a local native sidecar**. Shape:

1. `npm install` the package (it is typically ESM-only — `import`, not `require`).
2. Fetch + **SHA-256-verify** the platform binary (a build-time step), then **spawn
   it as a local HTTP sidecar** and wait for health.
3. Call the package's **typed TypeScript client** methods against the running sidecar.
4. **Shut the sidecar down** on exit (packages may also auto-reap on process exit).

No Python, no separate GAIA install — the frozen binary carries the agent. The sidecar
usually serves **same-origin only**, so an Electron renderer drives it via main-process
IPC, not a cross-origin fetch. The agent still needs a local model backend (Lemonade
Server) for inference. Reference example: npm `agent-email`.

### python agents (`hub/agents/python/<name>/`)

The package is a **GAIA framework plugin**, not a sidecar. Shape:

1. `pip install gaia-agent-<id>` (it depends on the published `amd-gaia` wheel).
2. Installing **auto-registers** the agent into the GAIA registry via the
   `gaia.agent` entry-point group (declared in `pyproject.toml`) — no hardcoded list,
   no manual wiring. The registry discovers it on import.
3. Invoke it through GAIA per the agent's `gaia-agent.yaml` manifest and `README.md`.
   The manifest declares the `id`, the `models` it expects, and which `interfaces`
   it exposes (`cli` / `api_server` / `mcp_server` / `pipe`) — drive it through whichever
   the app uses, plus any required env/config. For **in-process** embedding, the base
   `Agent` entry point is `process_query(text) -> dict`; construct the agent directly
   (its package exports the class + a `*Config`) or resolve it by id with the registry —
   which must be populated first: `r = AgentRegistry(); r.discover(); r.create_agent("<id>")`
   (`discover()` scans the `gaia.agent` entry points; without it the registry is empty
   and `create_agent` raises `ValueError`). Confirm the exact entry against the agent's
   own source (Step 4) rather than assuming — config knobs vary per agent.

The npm sidecar/binary lifecycle (fetch-binary, spawn, shutdown, typed HTTP client)
**does not apply here** — don't describe a python agent as a sidecar. Depth varies by
agent; describe only what the agent's own artifacts support rather than padding it to
match npm's lifecycle.

## Step 4 — Route to the agent's own guidance (do this, don't summarize from memory)

Per-agent integration guidance exists **unevenly**. **Check for a per-agent
`SKILL.md` and branch** — never assume one is there, and never rely on it being
auto-discovered as a child skill; you must Read it explicitly.

**Branch A — the agent ships its own `SKILL.md`:**

```bash
ls hub/agents/<runtime>/<name>/SKILL.md
```

If present, **Read `hub/agents/<runtime>/<name>/SKILL.md` and carry out its
instructions as the integration steps.** That file is itself a skill addressed to
you, the assistant — it is the authoritative, agent-specific recipe, not something to
merely mention to the developer. Follow its steps directly, and follow any deeper
file it points to (e.g. a `SPEC.md` next to it) when its steps call for that detail.
The npm `agent-email` package is the current working example: its `SKILL.md`
(frontmatter `name: integrate-agent-email`) cascades into a `SPEC.md` for the full
contract.

**Branch B — no per-agent `SKILL.md`:**

Read the agent's own artifacts **in this priority order** and **synthesize** the
integration steps from them (don't just list the files back to the developer):

1. **`README.md`** — the integrator-facing overview: install command, what it does,
   any usage snippet.
2. **Runtime manifest** — `gaia-agent.yaml` (and/or `pyproject.toml`) for python,
   `package.json` for npm: the canonical `id`/package name, declared `models`,
   exposed `interfaces`, dependencies, and entry points.
3. **Source entry point** — the package's entry module (python: `gaia_agent_<id>/__init__.py`
   `build_registration()` → `agent.py`; npm: the `main`/`exports` entry in
   `package.json`): the ground truth when README and manifest leave a gap.

Synthesize these into concrete steps shaped per Step 3 (install → register/configure →
invoke for python; install → start sidecar → call client → shut down for npm). Most
python agents are currently in this branch.

## Checklist

- [ ] Listed `hub/agents/` live — did not trust any hardcoded agent list.
- [ ] Pinned a single `<runtime>/<name>` and confirmed the runtime with the developer.
- [ ] Set expectations with the correct shape (npm sidecar vs. python framework plugin).
- [ ] Checked for `hub/agents/<runtime>/<name>/SKILL.md`; if present, **Read and
      executed it** (and any file it references); if absent, read README → manifest →
      source and synthesized the steps.
