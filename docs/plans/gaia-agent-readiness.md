# Readiness assessment: `gaia-agent`, the generic skill-programmable native agent

Scoping review of [#2804](https://github.com/amd/gaia/issues/2804), the Phase 5 milestone
deliverable of [`cpp-framework-parity.md`](cpp-framework-parity.md). Assessed against `main` at
`b38f5e60` (2026-08-12), after the day's C++ merges.

**Verdict: no-go to start today.** Four of the six declared dependencies are unstarted, one
undeclared dependency ([#2791](https://github.com/amd/gaia/issues/2791), embeddings) is the root
of the entire retrieval half, and the security spine the design rests on — refusing skills the
C++ runtime cannot honor — does not exist in C++ in any form. Roughly 60% of the substrate
`gaia-agent` composes is real and good; the missing 40% is on the critical path, not the margin.

---

## 1. Dependency status

Declared by #2804, plus one it omits.

| Dep | Issue | Issue state | Code on `main` | Real status |
|---|---|---|---|---|
| P2.2 RAG SDK + `RagTools` | [#2796](https://github.com/amd/gaia/issues/2796) | OPEN | none — no `rag.h`, no `RagTools` | **Unstarted** |
| P2.3 Code index + `CodeIndexTools` | [#2797](https://github.com/amd/gaia/issues/2797) | OPEN | none — no `code_index.h` | **Unstarted** |
| P3.3 Skill load / inject | [#2800](https://github.com/amd/gaia/issues/2800) | OPEN | none — no `skill_manager.h`, no `Agent::loadSkill` | **Unstarted** |
| P3.4 Skill sets | [#2801](https://github.com/amd/gaia/issues/2801) | OPEN | none yet; no PR open | **In flight** (sibling agent) |
| P4.3 MCP registry | [#2808](https://github.com/amd/gaia/issues/2808) | CLOSED | `mcp_registry.{h,cpp}`, `Agent::connectMcpServerById`, `test_mcp_registry.cpp` | **Done** |
| Skill discovery | [#2800](https://github.com/amd/gaia/issues/2800) | OPEN | none | **Unstarted** (same issue as P3.3) |
| **P3.2 skill permissions** | [#2799](https://github.com/amd/gaia/issues/2799) | OPEN | none — no `skill_permissions.h` | **Unstarted — and #2804 does not declare it** |
| **P1.2 embeddings** | [#2791](https://github.com/amd/gaia/issues/2791) | OPEN | none — `lemonade_client` has no embeddings call | **Unstarted — and #2804 does not declare it** |

Two corrections to the issue's own dependency list:

- **#2804 conflates P3.3 and skill discovery.** Both are #2800; there is no separate discovery
  issue. Not a problem, just a miscount — it is one PR-sized unit, not two.
- **#2804 omits #2799 and #2791**, which are both genuinely blocking. #2799 owns the refusal rule
  that section 4 below shows is the highest-risk gap. #2791 is the only embeddings call anywhere
  in `cpp/`, so it gates both #2796 and #2797 — half the fixed toolbelt.

**What did land today** (substrate #2804 composes, all verified present):
`VectorIndex` ([#2807](https://github.com/amd/gaia/pull/2807)),
`HttpClient` ([#2809](https://github.com/amd/gaia/pull/2809)),
`Database` ([#2816](https://github.com/amd/gaia/pull/2816)),
native tool calling ([#2821](https://github.com/amd/gaia/pull/2821)),
chunking ([#2822](https://github.com/amd/gaia/pull/2822)),
the TUI ([#2825](https://github.com/amd/gaia/pull/2825)),
the `SKILL.md` parser ([#2824](https://github.com/amd/gaia/pull/2824)),
toolbelt hardening ([#2823](https://github.com/amd/gaia/pull/2823)),
MCP tool confirmation gating ([#2851](https://github.com/amd/gaia/pull/2851)),
and CRLF normalization ([#2906](https://github.com/amd/gaia/pull/2906)).

---

## 2. What already exists that #2804 can build on

Every claim in #2804 about existing wiring was checked against source.

**Verified true:**

- `Agent::connectMcpServer(name, config)` exists (`cpp/src/agent.cpp:564`) and calls
  `rebuildSystemPrompt()` at line 613, after registering the server's tools. ✅
- `~Agent()` calls `disconnectAllMcp()` (`cpp/src/agent.cpp:218-220`). ✅
- `Agent::connectMcpServerById(id)` and the two-arg registry overload exist, so `mcp:connect:<id>`
  has something to resolve against. `MCPRegistry::require()` throws naming the id and the paths
  searched — no silent skip. ✅

**`cpp/agents/bash/` as a structural template** — it is a complete, working precedent:
`main.cpp`, `bash_agent.{h,cpp}`, `bash_tools.{h,cpp}`, `api_server.{h,cpp}`, `mcp_server.{h,cpp}`,
a `gaia-agent.yaml` packaging manifest, and an `eval/` directory with a scenario adapter and a
ground-truth file. `cpp/agents/generic/` can mirror this file-for-file. The CLI modes #2804 asks
for (TUI, `--print`, `--serve`, `--mcp`, `--resume`, `--list-sessions`) are all backed by shipped
components: `tui_app`, `repl`, `session`, `console`, `json_event_handler`.

**Fixed toolbelt — half of it exists:**

| Tool pack | Status |
|---|---|
| `FileIOTools` | Present, `registerAll(ToolRegistry&)` (`cpp/include/gaia/file_tools.h:140`) |
| `GitTools` | Present, `registerAll(ToolRegistry&)` (`cpp/include/gaia/git_tools.h:35`) |
| `CodeIndexTools` | **Absent** — #2797 |
| `RagTools` | **Absent** — #2796 |
| `shell_execute` | **Absent from core.** `bash_execute` exists but lives in `cpp/agents/bash/bash_tools.cpp`, agent-private. Promoting it to a shared pack is unowned work — see section 3. |

**`ToolRegistry`** has `registerTool`, `removeTool`, `setEnabled`, `hasTool`, `clear`. `removeTool`
makes skill unload mechanically possible. It has **no snapshot/restore**, which #2800's
"rollback on failed load, no partial loads" requirement needs — small, but it belongs to #2800.

---

## 3. Critical-path ordering

The chain has one long pole (retrieval) and one short-but-blocking pole (skills), and they are
independent until they meet at #2804.

````
  #2791 embeddings ──┬──▶ #2796 RAG SDK + RagTools ──────────┐
                     └──▶ #2797 code index + CodeIndexTools ─┤
                                                             ├──▶ #2804 gaia-agent
  #2824 SKILL.md parser (DONE) ──▶ #2799 permissions ──┐     │
                                                       ├─────┤
                          #2800 SkillManager + inject ─┴──┐  │
                                                          │  │
                          #2801 skill sets ───────────────┴──┘
                          (in flight — see inversion below)

  #2808 MCP registry (DONE) ──────────────────────────────────▶ #2804
````

**Must land before #2804 can start, in order:**

1. **#2791 embeddings** — no dependents can begin without it. Longest lead time on the retrieval side.
2. **#2799 skill permissions** — the refusal spine. Nothing about the skills path is safe to ship
   without it (section 4).
3. **#2796 and #2797** in parallel, once #2791 is in.
4. **#2800 SkillManager** — needs #2799's gate to run before registration.
5. **#2801 skill sets** — needs #2800.

**Dependency inversion to flag now:** #2801 is being implemented today, but it declares a
dependency on P3.1–P3.3, and #2799 and #2800 are both unstarted. The sibling agent is building
skill *sets* on top of a skill *manager* that does not exist. That work will either stub
`loadSkill`/`unloadSkill` or block. Worth resolving before it produces a merge that #2800 has to
unpick.

**On the critical path and NOT tracked by any issue:**

- **`shell_execute` as a shared core tool pack.** #2804's toolbelt lists it, but it exists only as
  `bash_execute` inside `cpp/agents/bash/`. Promoting it — into `cpp/include/gaia/shell_tools.h`
  or equivalent, under `CONFIRM` policy — is unowned. Small (a lift-and-shift plus tests), but
  nobody owns it.
- **The `scripts/` refusal.** Section 4. No issue names it, in either runtime.
- **`ToolRegistry` snapshot/restore** for #2800's rollback guarantee. Implied by #2800's acceptance
  criteria but not in its scope text.
- **The naming decision.** #2804 says "settle the name in this issue, not at package time," and it
  is still unsettled. `gaia-agent` collides conceptually with the existing
  `gaia agent {export|import}` subcommand; `gaia-native` is the stated fallback. This is free to
  decide now and expensive to change after packaging, signing, and eval baselines exist.

---

## 4. The two hard design constraints — currently unenforced

**This is the highest-risk finding in the assessment.** Both refusals that #2804 and plan
decisions 5 and 8 treat as the security spine are **entirely absent from the C++ runtime today.**
The parser that landed in #2824 does not refuse either case — it accepts both, cleanly.

### Constraint A — a skill shipping `tools.py` must be refused

**Not enforced.** `cpp/src/skill.cpp` parses `metadata.gaia.tools` into a `std::vector<SkillTool>`
and validates it happily. `validateSkill()` (`cpp/src/skill.cpp:1155`) checks permission grammar
and duplicate tool names and nothing else. `Skill::toolsPath()` even *returns the path to the
skill's `tools.py`* — the header models the Python tool channel as a legitimate, supported shape.
One validation message reads "must name the `@tool` function in `tools.py`", which is the parser
telling authors this channel works.

That is correct behavior for a *parser* — #2824's job is round-trip fidelity, and refusing at
parse time would break the shared-directory contract. The refusal belongs in the **loader**, which
is #2800, and #2800 does say so. But the loader does not exist, so today the refusal path is
**unbuilt end to end.** Per plan decision 5 this affects 9 of the 10 starter skills and all 6 email
skills — i.e. nearly every skill on disk would load into `gaia-agent` and instruct the model to
call tools that will never exist.

### Constraint B — a skill shipping `scripts/` for execution must be refused

**Not enforced, and worse, not even specified.** Grepping `cpp/` for `scripts` returns nothing.
`shell:execute` is a *grammatically valid* permission in the C++ parser today —
`domainLevels()` (`cpp/src/skill.cpp:519-527`) lists `{"shell", {"execute", "none"}}`, so
`validatePermission()` accepts `shell:execute` without complaint. Nothing downstream rejects it,
because nothing downstream exists.

Python has the enforcement this ports from: `refuse_unbridged_permissions()` in
`src/gaia/skills/permissions.py:139`, with `CONNECTOR_BRIDGED_DOMAINS = {network, mcp}` and
everything else refused. #2799 is the C++ port and is unstarted. `cpp/src/skill.cpp:513-515`
already carries a comment pointing at #2799 as the refusal's home — the parser author knew, and
deliberately scoped it out.

The `scripts/` case specifically is **not named by #2799, #2800, or any Python code.** Python's
`audit_gate.py` *scans* `scripts/` for the marketplace publish gate, but no loader in either
runtime refuses a skill for carrying one. #2804's prose is the only place this refusal is written
down.

### Why this ranks as the top risk

`gaia-agent`'s entire value proposition is that it loads any `SKILL.md` found on disk across three
discovery roots, one of which (`~/.claude/skills/`) is populated by tools GAIA does not control.
Without both refusals, that is exactly the malware delivery mechanism #2804 warns about. The
refusals are not a hardening pass to add after the agent works — **they are a precondition for the
agent existing at all.**

**Recommended action before any #2804 work:**

1. Promote #2799 to a declared, blocking dependency of #2804.
2. File the `scripts/` refusal as explicit scope. It needs a home in **both** runtimes — Python's
   loader does not refuse it either, so shipping it C++-only would create the cross-runtime
   verdict divergence [#2805](https://github.com/amd/gaia/issues/2805) exists to prevent.
3. Add both refusals to #2805's conformance corpus as fixtures, so "refused with a message naming
   why" is a measured claim.

---

## 5. MCP refcounting

**No refcounting exists.** `Agent::disconnectMcpServer(name)` (`cpp/src/agent.cpp:694`)
unconditionally disconnects the client and erases it from `mcpClients_`. There is no notion of
which skill asked for a server, so the "disconnect only when no other loaded skill still needs it"
requirement has nothing to build on. The refcount map and its ownership are net-new work inside
#2804.

Two adjacent defects surface once skill unload becomes real, and both should be fixed in the same
PR as the refcount:

- **Disconnect leaves the server's tools registered.** `connectMcpServer()` registers a
  `ToolInfo` per MCP tool whose callback closes over the server name and routes through
  `callMcpTool()`. `disconnectMcpServer()` removes the *client* but never calls
  `ToolRegistry::removeTool()`. After a skill unload the tools stay in the registry and in the
  system prompt, and each call returns `{"error": "MCP server '<name>' not found"}`. The model is
  told it has a capability it does not have — the same confidently-wrong failure mode the
  `tools.py` refusal exists to prevent, arrived at from the other direction.
- **`mcpServerConfigs_` is never erased on disconnect**, so the `callMcpTool()` →
  `reconnectMcpServer()` auto-reconnect path can silently resurrect a server that a skill unload
  deliberately tore down.

Net: MCP lifecycle-on-unload is the largest piece of genuinely new engineering inside #2804 —
more than the CLI, more than the toolbelt wiring.

---

## 6. Effort estimate and decomposition

### Estimate

| Scope | Estimate |
|---|---|
| Blocking dependency work (#2791, #2799, #2796, #2797, #2800, #2801) | 6 PRs, the bulk of the remaining milestone |
| #2804 itself, once unblocked | **5 PRs / ~2 agent-weeks**, ~2000–2500 lines including tests |

#2804 is mostly *composition* — the hard primitives are elsewhere. Its two genuinely novel pieces
are MCP refcounting-on-unload (section 5) and the refusal-reporting UX in `--list-skills`.

### Proposed decomposition

Five PRs. G1 and G4 are independent and can start the moment their upstreams land; G2 and G3 are
the serialized middle.

````
  #2799 ──┐
  #2800 ──┼──▶ G1 skeleton + toolbelt ──┬──▶ G2 skill lifecycle ──▶ G3 MCP refcount ──┐
  #2801 ──┘                             │                                             ├──▶ G5 eval + package
  #2796 ──┤                             └──▶ G4 --list-skills / refusal UX ────────────┘
  #2797 ──┘                                  (needs G2 for "loaded"; testable earlier)
  #2808 ──┘  (done)
````

**G1 — Binary skeleton and fixed toolbelt.** `cpp/agents/generic/` mirroring `cpp/agents/bash/`:
`main.cpp`, `generic_agent.{h,cpp}`, `api_server`, `mcp_server`, `gaia-agent.yaml`. Register
`FileIOTools`, `GitTools`, `CodeIndexTools`, `RagTools`, plus the promoted `shell_execute` under
`CONFIRM`. CLI modes matching `gaia-bash`, no skill flags yet.
*Blocked by:* #2796, #2797, and the unowned `shell_execute` promotion.

**G2 — Skill lifecycle wiring.** `--skill-set`, `--skill`, discovery across the three roots, load
the active set, inject bodies through the byte-exact prompt contract. `tools_required` checked and
warned about, never gating. Refusals surfaced as errors, not logs.
*Blocked by:* G1, #2799, #2800, #2801.

**G3 — MCP refcounting on skill load/unload.** The refcount map keyed by server id; unload
disconnects only at zero. Fixes the two adjacent defects in section 5 (deregister tools on
disconnect, drop the stored config). Tests must cover the two-skills-one-server case explicitly
and assert no MCP subprocess outlives the binary.
*Blocked by:* G2.

**G4 — `--list-skills` and refusal reporting.** Discovered per root, loaded, shadowed, and refused
**with the reason**. This is the user-facing surface of the section 4 refusals; it deserves its own
PR and its own review, not a subsection of G2.
*Blocked by:* G2 for "loaded"; the refusal-formatting half can be built against #2799 alone.

**G5 — Bundled skill sets, eval adapter, packaging.** The `coding` and `research` sets, an eval
adapter following `cpp/agents/bash/eval/bash_eval_adapter.py` with a committed baseline, and
`cpp/packaging/package_agents.py` wiring for all three platforms. Owns the cold-cache
index-then-answer acceptance criterion.
*Blocked by:* G3, G4. Overlaps [#2815](https://github.com/amd/gaia/issues/2815) (coding skill set
and `gaia-bash` supersession gate) — settle which issue owns the `coding` set before both start.

**Parallelism ceiling: two agents.** The chain G1 → G2 → G3 is inherently serial, so a third agent
has nothing to do until G4 opens up. Throwing more agents at #2804 will not compress it; throwing
them at #2791/#2799/#2796/#2797 will.

---

## Recommendation

**No-go on starting #2804 now; go on unblocking it.** The substrate that merged today is real and
well-shaped, and the two structural claims #2804 makes about existing wiring both check out —
`connectMcpServer()` does rebuild the prompt, `~Agent()` does disconnect. But four of six declared
dependencies are unstarted, two more blocking dependencies are undeclared (#2791, #2799), and the
security spine the whole design rests on is unbuilt: the C++ runtime today accepts a skill shipping
`tools.py` and accepts a skill declaring `shell:execute`, with no refusal path anywhere. Starting
#2804 against that state means either building the refusals inside it — which balloons the scope
and buries a security gate inside a feature PR — or shipping a binary that loads arbitrary
instructions from three discovery roots with nothing saying no. Point the available agents at
#2791 and #2799 now; #2804 becomes a clean, well-decomposed 5-PR composition once they and the
retrieval pair land.

**Blocking dependencies:**

- [#2791](https://github.com/amd/gaia/issues/2791) — embeddings on `LemonadeClient`. Undeclared by
  #2804; roots the entire retrieval half.
- [#2799](https://github.com/amd/gaia/issues/2799) — skill permissions, connector-bridged vs
  refused. Undeclared by #2804; **the highest-risk gap.**
- [#2796](https://github.com/amd/gaia/issues/2796) — RAG SDK and `RagTools`.
- [#2797](https://github.com/amd/gaia/issues/2797) — code index and `CodeIndexTools`.
- [#2800](https://github.com/amd/gaia/issues/2800) — `SkillManager` discovery, load/unload, prompt
  injection. Also owns the `tools.py` refusal.
- [#2801](https://github.com/amd/gaia/issues/2801) — skill sets. In flight, but ahead of its own
  #2799/#2800 dependencies.
- *Untracked:* the `scripts/` refusal (both runtimes), `shell_execute` promotion to a shared core
  tool pack, `ToolRegistry` snapshot/restore for load rollback, and the `gaia-agent` vs
  `gaia-native` naming decision.
