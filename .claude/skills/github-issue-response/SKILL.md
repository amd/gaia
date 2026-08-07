---
name: github-issue-response
description: "Respond to a GitHub issue, PR comment, discussion reply, or review on amd/gaia. Use when writing any reply that will be posted to GitHub — triaging a bug report, answering a usage question, responding to a feature request, replying to a contributor on a PR, or handling a reported security issue. Covers the security-escalation protocol (never discuss exploit details publicly), when to escalate to @kovtcharov-amd, per-response-type length caps, and the doc-link map for pointing users at the right page."
---

# Responding on GitHub (amd/gaia)

Output style is **not** defined here. Follow `../../../CLAUDE.md` → **"How You Communicate"**:
lead with the finding in plain words, layer `file.py:line` detail underneath, say each point
once. This skill adds only the GitHub-specific protocol on top of that.

Automated PR-review *policy* — severity tiers, the nit cap, skip rules, length caps — lives in
`../../../REVIEW.md`. That file is the single source of truth for review scoring; don't restate
or fork it.

## 🔒 Security — read this first

**Never discuss vulnerability details in public.** No exploit steps, no proof-of-concept, no
technical analysis of how it could be abused.

**Reported in a public issue:** reply with a pointer to a private advisory, tag the maintainer,
and stop.

> Thank you for reporting this. This appears to be a security concern — please open a private
> security advisory instead: https://github.com/amd/gaia/security/advisories/new

Then tag **@kovtcharov-amd**.

**Found during a PR review:** open with `🔒 SECURITY CONCERN: <issue type>`, tag
**@kovtcharov-amd**, name the *class* of problem ("potential command injection") but not how to
exploit it, and suggest the author take it to maintainers privately.

A 🔒 line and the maintainer tag always stay **visible** — never collapse them into a
`<details>` block.

## Escalate to @kovtcharov-amd

Security vulnerabilities · architecture and design decisions · roadmap or timeline questions ·
breaking changes and deprecations · external integration or partnership requests · AMD hardware
specifics · anything you can't resolve from the docs.

**Don't** escalate: questions the docs already answer, simple usage questions, duplicates (just
link the original), or feature requests that need community discussion first.

## Before you reply

1. **Search `docs/` first** — see [`docs/docs.json`](../../../docs/docs.json) for the structure.
2. **Check for duplicates** — link the original instead of answering twice.
3. **Cite real locations only.** Reference `file.py:line` when you've actually found it; never
   guess a line number to look thorough.

## Length caps by response type

| Type | Shape | Cap |
|---|---|---|
| Quick answer | 2–4 sentences, one doc link | — |
| How-to | one short paragraph, minimum viable example, one doc link | ~150 words |
| Bug report | open with "I think this is X" or "I need more info to tell", then ask for specific repro steps | ~200 words |
| Feature request | one sentence on whether it's in scope, then 2–4 bullets on feasibility / existing patterns / next steps | ~200 words |
| Complex discussion | frame the conclusion in 1–2 sentences, *then* go deep | — |

**Never:** walls of unstructured text · repeating what the issue already says · generic advice
that isn't GAIA-specific · opening with a code reference (`Looking at src/gaia/foo.py:123, …`
reads like a diff review — the reader wants the finding before the line number).

## Tone

Professional and friendly. Welcome first-time contributors and guide them gently; assume good
intent even on unclear or duplicate issues; thank people for bug reports and ideas. External
contributors won't know GAIA conventions yet — teach with a concrete example rather than
correcting them abstractly.

## Where things live (for pointing users at the right place)

**Code:**
- In-core agent framework: `src/gaia/agents/` — `base/`, `tools/`, `builder/`, `code_index/`, `registry.py`
- Packaged agents: `hub/agents/<id>/python/` (chat, code, analyst, browser, email, jira, docker, sd, emr, docqa, routing, …)
- CLI: `src/gaia/cli.py` · MCP: `src/gaia/mcp/` · LLM backends: `src/gaia/llm/` (+ `providers/`)
- RAG: `src/gaia/rag/` · Audio: `src/gaia/audio/` · Eval: `src/gaia/eval/` · API: `src/gaia/api/`
- Agent SDK: `src/gaia/chat/` (`AgentSDK`) · Agent UI: `src/gaia/ui/` (backend), `src/gaia/apps/webui/` (frontend)
- Tool mixins: `src/gaia/sd/mixin.py`, `src/gaia/vlm/mixin.py`

**Docs** (external site: https://amd-gaia.ai):
- Getting started: [`docs/setup.mdx`](../../../docs/setup.mdx), [`docs/quickstart.mdx`](../../../docs/quickstart.mdx)
- Guides: [`docs/guides/`](../../../docs/guides/) — chat, browse, analyze, email, talk, code, blender, jira, docker, routing, emr, memory
- SDK reference: [`docs/sdk/`](../../../docs/sdk/) — core concepts, SDKs, infrastructure, mixins
- CLI: [`docs/reference/cli.mdx`](../../../docs/reference/cli.mdx)
- Troubleshooting: [`docs/sdk/troubleshooting.mdx`](../../../docs/sdk/troubleshooting.mdx) · FAQ: [`docs/reference/faq.mdx`](../../../docs/reference/faq.mdx)
- Contributing: [`CONTRIBUTING.md`](../../../CONTRIBUTING.md) · Dev setup: [`docs/reference/dev.mdx`](../../../docs/reference/dev.mdx)

For bugs, also check `tests/` — a related test often reveals the issue or needs updating —
and weigh security implications against [`docs/sdk/security.mdx`](../../../docs/sdk/security.mdx).

## Examples

**✅ Bug report** — diagnosis first, then two concrete asks:

> Looks like RAG initialization didn't complete — that symptom is what happens when GAIA can't
> find a loaded embedding model. Two quick checks:
>
> 1. Did you run `gaia init --profile chat` first?
> 2. Can you share the output of `gaia diagnostics`?
>
> If both look right, paste `gaia chat --debug` output and I'll dig in. Setup walkthrough:
> `docs/guides/chat.mdx`.

**✅ Feature request** — scope call first, then concrete building blocks:

> Interesting idea. GAIA doesn't have built-in Slack integration today, but you could build it
> from existing pieces: the Agent SDK for message handling (`docs/sdk/sdks/chat.mdx`), MCP for
> Slack connectivity (`docs/sdk/infrastructure/mcp.mdx`), following the Jira agent's shape
> (`hub/agents/jira/python/`). Running it on the local LLM backend keeps conversations private.
>
> Interested in contributing it? `CONTRIBUTING.md` has the setup.

**❌ Too generic** — says nothing actionable:

> This looks like a configuration issue. Try checking your configuration and making sure
> everything is set up correctly. Let me know if that helps!

**❌ Too technical** — leads with internals the reporter can't decode:

> The error originates in `src/gaia/rag/sdk.py:145` where `RAGSDK.__init__` invokes
> `_load_embedder`, which raises if `self.config.embedding_model` can't be resolved by the
> Lemonade `/api/v1/models` endpoint…

Lead with "looks like RAG can't reach the Lemonade server", then drop the file references for
whoever picks up the fix.

**❌ Security** — never post this, even though it's accurate:

> The issue is on line 45 where you use `subprocess.call()` with user input. Here's how an
> attacker could exploit it: …

Escalate privately instead.
