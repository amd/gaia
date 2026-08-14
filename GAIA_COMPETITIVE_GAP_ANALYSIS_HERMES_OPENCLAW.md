# Competitive Gap Analysis Research: "Hermes" and "OpenCLAW"

Research date: 2026-08-13. All claims below are sourced; anything not directly
supported by a fetched/searched source is explicitly marked **could not verify**.

---

## 0. Disambiguation (read this first)

### "Hermes"

There are **two distinct things** using the name "Hermes" from the same organization
(Nous Research), and they are easy to conflate:

1. **Hermes (the model series)** — a family of open-weight LLM fine-tunes (Hermes,
   Hermes 2, Hermes 3, on Llama/Mistral/Qwen bases) built by Nous Research since 2023.
   This is **not an agent framework** — it's the base model, and it can be plugged into
   many different agent harnesses (including Hermes Agent below, but also others).
   Sources: [Nous Research — Hermes 3](https://nousresearch.com/hermes3), [Hermes LLM
   Explained (Fastio)](https://fast.io/resources/hermes-llm/), [Nous-Hermes-13b
   (HuggingFace)](https://huggingface.co/NousResearch/Nous-Hermes-13b).
2. **Hermes Agent** — a separate, newer (launched ~February 2026) open-source
   **autonomous agent framework**, also built by Nous Research, that runs a persistent,
   self-improving agent loop and is explicitly marketed against OpenClaw and Claude
   Code as an "always-on" agent. This is the one that matches your prompt's description
   ("competes with a flagship autonomous agent for long-horizon work").
   Primary sources: [official docs](https://hermes-agent.nousresearch.com/docs/),
   [GitHub — NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).

**Verdict: "Hermes" in this gap analysis should mean Hermes Agent, not the Hermes model
series.** The model series is a component competitors could route to; it is not itself
an agent product and has no agent loop, memory, or autonomy model to compare against
GAIA. No other "Hermes" autonomous-coding-agent product distinct from Nous Research's
was found in search results.

### "OpenCLAW"

Unambiguous once correctly cased: **OpenClaw** (formerly Clawdbot, then briefly
Moltbot) is a single, well-documented open-source project — a self-hosted, local-first
personal AI agent, created by Peter Steinberger (ex-PSPDFKit/Nutrint founder; he
subsequently joined OpenAI in Feb 2026), first shipped November 2025, renamed twice
after a trademark dispute with Anthropic (Jan 27 → "Moltbot", Jan 29–30 → "OpenClaw").
Primary sources: [GitHub — openclaw/openclaw](https://github.com/openclaw/openclaw),
[docs.openclaw.ai](https://docs.openclaw.ai), [Wikipedia —
OpenClaw](https://en.wikipedia.org/wiki/OpenClaw), naming-history writeup:
[Clawgency](https://clawgency.de/en/blog/openclaw-naming-history.html).

No conflicting/alternate "OpenCLAW" project was found — search results consistently
point to this one project. Confidence: **high**.

---

## 1. Hermes (Hermes Agent)

### 1.1 What it is
Self-hosted, model-agnostic, open-source (MIT license) autonomous agent framework
built by Nous Research (the lab behind the Hermes model series, and Nomos/Psyche).
Launched ~February 2026. Runs as a CLI, or as a long-running gateway process reachable
via Telegram/Discord/Slack/WhatsApp/Signal, on infrastructure ranging from a $5 VPS to
a GPU cluster or serverless (Daytona, Modal, Vercel Sandbox).
**Confidence: high** (primary docs + GitHub repo).

Sources: [official docs](https://hermes-agent.nousresearch.com/docs/), [GitHub
repo](https://github.com/NousResearch/hermes-agent).

### 1.2 Agent loop / execution model
A synchronous "AIAgent loop" is the core orchestration engine, wrapped by a gateway,
cron scheduler, and tooling runtime, all backed by SQLite. Execution proceeds in
"turns" (each prompt or tool result advances the loop); the agent can be interrupted
mid-task and redirected. It can spawn isolated **subagents** for parallel workstreams
and write Python scripts that call tools via RPC to collapse multi-step pipelines into
"zero-context-cost" turns.
Sources: [Turing Post — Hermes vs OpenClaw](https://www.turingpost.com/p/hermes),
[official docs](https://hermes-agent.nousresearch.com/docs/).

### 1.3 Memory model
**Persistent across sessions**, layered:
- Persistent core memory file (`MEMORY.md`, roughly ~1.3k tokens) as agent-curated
  "always in context" memory, updated via periodic self-nudges.
- SQLite-backed session history with FTS5 full-text search for cross-session recall,
  summarized by the LLM.
- Optional "Honcho" layer for dialectic user modeling — an evolving model of user
  preferences/characteristics across sessions.
- **Skills as procedural memory** — see 1.5. This is explicitly framed as remembering
  *methods*, not just facts, i.e., it compounds/learns procedures over time, not only
  storing static facts.

No external vector DB / RAG pipeline is required for the built-in memory (SQLite+FTS5
is the default store), though 8+ external memory-provider plugins exist as of April
2026.
Sources: [Turing Post](https://www.turingpost.com/p/hermes), [Claude Market — Hermes
Agent Memory System
Explained](https://www.claudemarket.ai/blog/hermes-agent-memory-system-explained),
[GitHub repo](https://github.com/NousResearch/hermes-agent).

### 1.4 Autonomy model
Not strictly request/response. Autonomous triggers:
- **Built-in cron scheduler** (jobs stored at `~/.hermes/cron/jobs.json`, gateway ticks
  every 60 seconds) — natural-language-defined recurring jobs ("daily reports, nightly
  backups, weekly audits") that run unattended and can deliver results to any connected
  messaging platform.
- **Gateway ticks** on inbound messages across 20+ platforms.
- Because it can run on always-on cloud infra (not tied to a laptop), the cron jobs
  keep firing independent of a user session.

Sources: [official docs](https://hermes-agent.nousresearch.com/docs/), [Turing
Post](https://www.turingpost.com/p/hermes).

### 1.5 Tool / skill system
- ~40–60+ built-in tools (web search, terminal, file ops, image generation, etc. —
  sources disagree on exact count, 40+ vs 60+; treat as approximate).
- **Seven terminal/execution backends**: local, Docker, SSH, Singularity, Modal,
  Daytona, Vercel Sandbox.
- **Autonomous skill creation**: after completing a sufficiently complex task
  (commonly cited threshold: 5+ tool calls), the agent can write a new "skill" — a
  structured doc capturing the procedure, known pitfalls, and verification steps —
  without a human authoring it. This is invoked via a `skill_manage` tool. Skills are
  stored in `~/.hermes/skills/`, auto-indexed, and self-improve further on reuse.
- Skills follow the open **agentskills.io** standard and are shareable via a "Skills
  Hub" registry — i.e., portable/interoperable with other agent tools that speak the
  same skill format (this is the same format OpenClaw's `SKILL.md` also targets — see
  2.5).
- MCP server integration is supported for extending tool surface further.

**This is the single clearest point of difference vs. GAIA to highlight in a gap
analysis**: Hermes Agent's skill system is self-authoring by design (the agent writes
its own skills from experience), not just human-authored and agent-loaded.

Sources: [official docs](https://hermes-agent.nousresearch.com/docs/), [Turing
Post](https://www.turingpost.com/p/hermes), [GitHub
repo](https://github.com/NousResearch/hermes-agent), [AI.cc — Hermes Agent
2026](https://www.ai.cc/blogs/hermes-agent-2026-self-improving-open-source-ai-agent-vs-openclaw-guide/).

### 1.6 Long-horizon task handling
- Sessions are saved and resumable; `/compress` and `/insights` commands provide
  on-demand context summarization/compaction.
- A "context file system" injects project context without repeatedly consuming
  per-turn tokens.
- Subagent spawning + RPC-based tool calls from external Python scripts let long
  pipelines run without ballooning the parent agent's context.
- "Trajectory compression" is mentioned for reducing verbose interaction logs,
  primarily framed as useful for **research/training** export rather than a
  crash-recovery mechanism per se.
- **Could not verify**: an explicit crash-recovery/resumption guarantee (i.e., what
  happens if the gateway process itself dies mid-task) — no source described this in
  detail. The persistent SQLite store implies state survives a process restart, but no
  source confirmed automatic mid-task resumption after a crash.

Sources: [Turing Post](https://www.turingpost.com/p/hermes), [official
docs](https://hermes-agent.nousresearch.com/docs/).

### 1.7 Scale / traction signals (context, not architecture)
Sources disagree on exact star counts depending on the fetch date (140k–230k+ GitHub
stars cited across different articles from mid-2026), 370+ contributors by v0.18.2
(July 2026), MIT license confirmed by both official docs and third-party sources.
Treat exact numbers as approximate/moving targets rather than a fixed fact.

---

## 2. OpenCLAW (OpenClaw)

### 2.1 What it is
Open-source (MIT-licensed core "Gateway"), self-hosted, local-first personal AI agent
framework. Runs as a single persistent daemon on the user's own device/infra and is
driven through messaging channels (WhatsApp, Telegram, Slack, Discord, Signal,
iMessage, Google Chat, Microsoft Teams, Matrix, Zalo, WebChat) plus a CLI/TUI/web
Control UI. Created by Peter Steinberger, first released November 2025 as "Clawdbot,"
renamed "Moltbot" (Jan 27, 2026) then "OpenClaw" (Jan 29–30, 2026) after a trademark
dispute with Anthropic over the "Claw(d)" name. Now stewarded by an "OpenClaw
Foundation." NVIDIA has shipped an enterprise fork called "NemoClaw" per one source
(Composio) — **could not independently verify** that specific claim beyond the one
source.
**Confidence: high** on core identity/history (Wikipedia, GitHub, official docs, and
multiple independent tech-press pieces agree); **lower confidence** on the NVIDIA fork
detail specifically.

Sources: [GitHub — openclaw/openclaw](https://github.com/openclaw/openclaw),
[docs.openclaw.ai](https://docs.openclaw.ai), [Wikipedia](https://en.wikipedia.org/wiki/OpenClaw),
[Clawgency naming history](https://clawgency.de/en/blog/openclaw-naming-history.html),
[Composio — OpenClaw vs Hermes Agent](https://composio.dev/content/openclaw-vs-hermes-agent).

### 2.2 Agent loop / execution model
Single-process Node.js daemon ("Gateway") composed of: channel adapters (normalize
messages from each platform), a session manager, a message queue (serializes runs), an
agent runtime (the core loop), and a control plane (WebSocket API, default port
18789). The loop itself: **input → assemble context → call model → execute tool
calls → repeat → reply** — one source explicitly compares this shape to Claude Code's
loop, "wrapped in a persistent daemon rather than a CLI."
Sources: [Milvus blog — What Is
OpenClaw?](https://milvus.io/blog/openclaw-formerly-clawdbot-moltbot-explained-a-complete-guide-to-the-autonomous-ai-agent.md).

### 2.3 Memory model
File-backed, **persistent across sessions**, stored locally under `~/.openclaw`:
- Conversations/notes stored as Markdown + logs — human-readable and git-compatible
  (you can version-control and diff the agent's memory).
- Workspace-scoped identity file (`SOUL.md`), similar in spirit to Hermes Agent's
  identity file but tied to a specific workspace rather than the whole instance
  (per the Turing Post head-to-head).
- Optional semantic-memory plugins for vector-style retrieval; hybrid retrieval
  indexes both memory files and raw transcripts.
- Skills are **human-authored** by default (not auto-generated from experience the way
  Hermes Agent's are) — loaded from workspace, personal, shared, or plugin scopes.

Whether it "compounds" (learns new procedures on its own over time) is weaker than
Hermes Agent's explicit self-authoring claim: OpenClaw's skill system is described as
"human-crafted, modular instruction packs with workspace-level management," though one
source (Milvus) says the agent *can* draft a new skill file if none exists for a task —
see 2.5 for the tension between these two claims.
Sources: [Turing Post](https://www.turingpost.com/p/hermes), [Milvus
blog](https://milvus.io/blog/openclaw-formerly-clawdbot-moltbot-explained-a-complete-guide-to-the-autonomous-ai-agent.md).

### 2.4 Autonomy model
Confirmed via **primary docs** (docs.openclaw.ai) — this is the strongest-sourced
autonomy claim in this report:
- **Heartbeat**: the Gateway runs a periodic check (default every 30 minutes) per
  heartbeat-enabled agent, owned by an internal "Automations" scheduler. It reads a
  monitoring checklist (heartbeat "scratch"/`HEARTBEAT.md`-style prose) and decides
  whether to act or reply `HEARTBEAT_OK` (no-op).
- **Cron / Automations**: a separate built-in scheduler for recurring/scheduled jobs,
  persisted at `~/.openclaw/cron/jobs.json` (survives restarts), with execution state
  in `~/.openclaw/cron/jobs-state.json`. Jobs can deliver output to a chat channel or a
  webhook endpoint.
- Docs explicitly distinguish the two: heartbeat = one batched periodic monitoring
  pass; automations/cron = independently-scheduled recurring work items.
- Webhooks and incoming messages are additional external triggers.

This is explicitly **not** strictly request/response — it is designed to act
unprompted by default (heartbeat is on by default per multiple sources), governed by
configurable tool-approval gates for higher-risk actions.
Sources (primary): [docs.openclaw.ai/gateway/heartbeat](https://docs.openclaw.ai/gateway/heartbeat),
[docs.openclaw.ai/cli/cron](https://docs.openclaw.ai/cli/cron),
[docs.openclaw.ai/automation/cron-vs-heartbeat](https://docs.openclaw.ai/automation/cron-vs-heartbeat).
Secondary corroboration: [Milvus blog](https://milvus.io/blog/openclaw-formerly-clawdbot-moltbot-explained-a-complete-guide-to-the-autonomous-ai-agent.md).

### 2.5 Tool / skill system
- Skills are `SKILL.md` files: YAML frontmatter + natural-language instructions,
  explicitly described as portable across OpenClaw, Claude Code, and Cursor (i.e. a
  de-facto shared skill format across multiple agent products).
- Distributed via **ClawHub**, a community skill marketplace/registry (also reachable
  via direct URL or community repos). One comparison source (utilo/Brilworks-style
  pieces) puts ClawHub's catalog at 3,200+ skills, the largest of the two.
- **Security caveat, worth flagging in a gap analysis**: one source (Milvus, citing a
  Cisco-attributed audit) claims 26% of audited community ClawHub skills contained
  vulnerabilities, and 230+ malicious uploads were found in one month (February 2026)
  exploiting prompt injection for data exfiltration. **This is a single secondary
  source's claim** — not independently corroborated by a second source in this
  research pass; flag as "reported, not independently verified here" rather than
  established fact. A separately-cited CVE (CVE-2026-25253, cross-site WebSocket
  hijacking, CVSS 8.8, patched in 2026.1.29) was likewise only seen in one source —
  **could not independently verify** against a CVE database in this pass.
- On self-authored skills: sources conflict. Milvus says "if a needed skill doesn't
  exist, the agent can draft one." The Turing Post head-to-head instead frames
  OpenClaw's model as tighter human authorship/control vs. Hermes Agent's fully
  autonomous skill creation. **Treat OpenClaw's self-authoring capability as
  plausible-but-not-strongly-confirmed** — the primary GitHub README fetched here did
  not confirm or deny it explicitly.

Sources: [Milvus blog](https://milvus.io/blog/openclaw-formerly-clawdbot-moltbot-explained-a-complete-guide-to-the-autonomous-ai-agent.md),
[Turing Post](https://www.turingpost.com/p/hermes), [GitHub —
openclaw/openclaw](https://github.com/openclaw/openclaw).

### 2.6 Long-horizon task handling
- Prompt assembly pulls together system instructions, conversation history, tool
  schemas, skills, and memory each turn; deployments commonly need ≥64K context and
  most production use leans on frontier cloud models (Claude, GPT-4-class) rather than
  local models for the primary orchestration turn.
- **Crash recovery**: **not explicitly documented** in any source found. Because
  memory/state is file-based under `~/.openclaw` (Markdown + JSON job state), it's
  reasonable to infer the agent could reconstruct context on restart, but no source
  confirmed an actual mid-task resumption mechanism after a hard crash. **Marking as
  could not verify** rather than asserting it.
- No explicit compaction/sliding-window algorithm was documented in the sources
  fetched — only that large prompts strain smaller local models and that OpenClaw
  routes to cloud providers or local OpenAI-compatible endpoints (Ollama, LM Studio)
  with provider fallback chains for graceful degradation on failure (not specifically
  for context-limit handling).

Sources: [Milvus blog](https://milvus.io/blog/openclaw-formerly-clawdbot-moltbot-explained-a-complete-guide-to-the-autonomous-ai-agent.md),
[GitHub — openclaw/openclaw](https://github.com/openclaw/openclaw).

### 2.7 Scale / traction signals
GitHub stars ~310k–386k depending on source/date (fast-moving number, sources
disagree because they were fetched at different times), 800–1,200+ contributors,
58k–81k forks. MIT license confirmed on the core Gateway by both primary GitHub repo
and third-party sources.

---

## 3. Direct Hermes-Agent-vs-OpenClaw framing found in search results

Multiple independent comparison pieces (not GAIA-authored, third-party) converge on a
consistent three-way framing that is directly useful for a gap analysis:

> "The AI agent landscape in 2026 has fractured into three distinct camps — Claude
> Code says: make me indispensable to your codebase; OpenClaw says: become the
> automation layer of your life; Hermes Agent says: grow into whatever you need, and
> improve every time you use it."
— [MindStudio — Hermes Agent vs. Claude Code vs.
OpenClaw](https://www.mindstudio.ai/blog/hermes-agent-vs-claude-code-vs-openclaw-which-self-improving-ai-agent-right-for-workflow)

Other convergent claims across sources ([Turing
Post](https://www.turingpost.com/p/hermes), [Composio](https://composio.dev/content/openclaw-vs-hermes-agent),
[The New Stack](https://thenewstack.io/persistent-ai-agents-compared/), [utilo](https://utilo.io/en/home/blog/hermes-vs-claude-code-vs-openclaw-2026)):
- Both OpenClaw and Hermes Agent are positioned as **persistent agents that run while
  you sleep**, in contrast to session-scoped coding assistants.
- Claude Code is explicitly described (per these third-party sources, mid-2026 state)
  as having since gained "auto-memory that writes notes to disk across sessions," but
  is still framed as narrower/deeper on pure software engineering rather than
  general life/automation autonomy.
- OpenClaw is generally characterized as broader in messaging-channel reach and skill
  marketplace size (ClawHub, cited 3,200+ skills); Hermes Agent is characterized as
  narrower but deeper on self-improving procedural memory and research/training
  tooling (trajectory export, batch generation, DSPy/GEPA-based skill evolution via a
  companion repo, [hermes-agent-self-evolution](https://github.com/NousResearch/hermes-agent-self-evolution)).

I could not find a source directly comparing either product to **Devin** or
**OpenHands** — see §4.

---

## 4. Historical/contextual notes

### AutoGPT / BabyAGI (2023) — precedent for unprompted, goal-driven agents
- **AutoGPT**: open-source, created by Toran Bruce Richards, released March 2023,
  used GPT-4 to break a user-defined goal into sub-tasks and execute them with minimal
  human intervention; crossed 100k GitHub stars within months — the fastest-growing
  GitHub repo at the time. Source: [BairesDev — The Rise of Autonomous
  Agents](https://www.bairesdev.com/blog/the-rise-of-autonomous-agents-autogpt-agentgpt-and-babyagi/).
- **BabyAGI**: published by Yohei Nakajima, April 2023, a ~140-line Python script
  demonstrating that a loop of LLM calls — task creation, prioritization, execution,
  and generation of new tasks from results — could run indefinitely and autonomously
  from a single objective, backed by a vector memory store. Source: [IBM — What is
  BabyAGI?](https://www.ibm.com/think/topics/babyagi).
- Both are the direct conceptual ancestors of the "runs without you typing a prompt"
  autonomy model both Hermes Agent (cron) and OpenClaw (heartbeat + cron) now
  implement more robustly, with real tool sandboxing, persistent memory, and messaging
  integration that the 2023 wave lacked.

### Devin / OpenHands — general context (not found compared to Hermes/OpenClaw directly)
- **Devin** (Cognition Labs): proprietary, cloud-managed autonomous software engineer;
  as of 2026 reportedly differentiated by long-term project memory features ("Devin
  Wiki", "Devin Search") aimed at multi-week autonomous projects. Source: [OpenHands
  blog — Devin AI
  Alternatives](https://www.openhands.dev/blog/devin-ai-alternatives).
- **OpenHands** (formerly OpenDevin): open-source alternative to Devin — fully
  sandboxed (Docker), model-agnostic (10+ providers), self-hostable, positioned as the
  open/auditable counterpart to Devin's proprietary managed service. Source:
  [OpenHands blog](https://www.openhands.dev/blog/devin-ai-alternatives).
- **Could not verify**: no source in this research pass directly compared Hermes Agent
  or OpenClaw to Devin or OpenHands. They occupy an adjacent but distinct niche
  (software-engineering-task agents vs. general life/automation agents) — a gap
  analysis should not assume a head-to-head exists in public material; if you want
  that specific comparison for the writeup, it would need to be constructed rather
  than cited.

---

## 5. Summary table

| | **Hermes Agent** | **OpenClaw** |
|---|---|---|
| Maker | Nous Research | Peter Steinberger / OpenClaw Foundation |
| License | MIT | MIT (core Gateway) |
| Launched | ~Feb 2026 | Nov 2025 (as Clawdbot) |
| Primary interface | CLI + messaging gateway | CLI/TUI/Web + messaging gateway |
| Memory | SQLite+FTS5, MEMORY.md, optional Honcho user modeling | Markdown files + logs under `~/.openclaw`, optional semantic plugins |
| Compounding memory? | Yes — explicit procedural "skills from experience" | Weaker/contested — skills mostly human-authored, one source claims some self-drafting |
| Autonomy trigger | Cron (`~/.hermes/cron/jobs.json`, 60s gateway tick) | Heartbeat (default 30 min) + separate Cron/Automations, both confirmed in primary docs |
| Tool/skill format | Custom + agentskills.io-compatible, Skills Hub | `SKILL.md` (also used by Claude Code/Cursor), ClawHub registry |
| Self-authoring skills | Confirmed (`skill_manage` tool) | Contested/unclear |
| Subagents | Confirmed (isolated parallel workstreams) | Not documented in sources found |
| Crash/resumption guarantee | Not explicitly documented | Not explicitly documented |
| Compared to Claude Code/Devin/OpenHands | Yes, extensively, vs. Claude Code specifically | Yes, extensively, vs. Claude Code specifically; not vs. Devin/OpenHands |

---

## 6. Confidence & gaps recap

- **High confidence**: identity of both products, licensing, creators, high-level
  architecture (gateway/daemon + tool loop), OpenClaw's heartbeat+cron autonomy model
  (primary docs), Hermes Agent's cron+skill-authoring model (primary docs + repo).
- **Medium confidence**: exact star/contributor counts (moving targets, sources
  disagree by fetch date), OpenClaw's self-authored-skill capability, NVIDIA
  "NemoClaw" fork claim.
- **Could not verify** (explicitly, not guessed): crash/mid-task-resumption mechanics
  for either product; the specific CVE and vulnerability-rate claims for
  ClawHub skills (single-source, unconfirmed); a direct Devin/OpenHands comparison
  to either Hermes Agent or OpenClaw.
