---
title: Phase 2 - Hardware-aware agent runtime/recipe selection (Draft)
internal: true
---

# Phase 2 - Hardware-aware agent runtime/recipe selection (Draft)

Status: DRAFT - internal review only

Why this exists
- Agents need a single, auditable SDK hook for declaring hardware requirements (NPU / iGPU / CPU) so the runtime picks a safe, allow-listed Lemonade recipe (or other runtime) rather than ad-hoc checks per-agent.

What I did in Phase 1 (summary)
- Verified references to FLM in docs and C++ examples, but found no FLM Python module in `src/gaia/` (docs/examples refer to `FLM` as a model backend). See Open Questions below.
- Verified `oga-cpu` and `oga-hybrid` are present in `src/gaia/llm/lemonade_client.py` documentation and in unit tests (`tests/test_lemonade_client.py`).
- Recommended seam: extend `LemonadeManager.ensure_ready()` and add a resolver that maps `Agent.REQUIRED_HARDWARE` → allow-listed Lemonade recipe string. Do not persist hardware to `~/.gaia/` in Phase 1; query runtime at agent startup.

Key citations (Phase 1 evidence)
- `LemonadeClient.get_system_info()` docstring and wrapping code: src/gaia/llm/lemonade_client.py
- `LemonadeManager.ensure_ready()` chokepoint: src/gaia/llm/lemonade_manager.py
- Agents call through `Agent.__init__` into LemonadeManager: src/gaia/agents/base/agent.py
- Provider allow-list precedent: src/gaia/llm/factory.py
- `REQUIRED_CONNECTORS` precedent for declarative ClassVar: src/gaia/agents/base/agent.py
- Hardware-advisor example & test patterns: examples/hardware_advisor_agent.py and tests/test_hardware_advisor_agent.py

Phase 2 proposed implementation (developer-facing)
1. Add `HardwareRequirement` dataclass in `src/gaia/agents/base/agent.py` and a new optional ClassVar `REQUIRED_HARDWARE` on `Agent`.
2. Add an allow-listed mapping `_RECIPE_BY_DEVICE` in `src/gaia/llm/selection.py` (or internal to `lemonade_manager.py`) mapping device-tier strings (e.g., `amd_npu`, `amd_igpu`, `cpu`) to recipe names (`oga-hybrid`, `oga-cpu`, ...). No dynamic imports or execution.
3. Extend `LemonadeManager.ensure_ready()` to accept a `required_hardware` argument (or read from the calling agent) and resolve device → recipe by calling `LemonadeClient.get_system_info()` and walking `devices` in priority order.
4. On no-satisfying-device, raise `HardwareRequirementError` with actionable guidance (agent name, required tier, detected devices).
5. Add unit tests using fixtures under `tests/fixtures/hardware/` for three payloads (NPU+iGPU, iGPU-only, CPU-only) and matrix tests for declared requirements.

Security constraints
- Follow `_PROVIDERS` allow-list pattern in `src/gaia/llm/factory.py`. Do not load code or binary paths from config values.

Open questions (need maintainer / lemonade-specialist answers before Phase 2)
1. FLM status: is FLM (FastFlowLM) a shipped runtime, a Lemonade-internal recipe, or planning shorthand? (I found FLM referenced in docs and C++ examples, but no Python module in `src/gaia/`.)
2. Recipe vocabulary: beyond `oga-cpu` and `oga-hybrid`, does our Lemonade accept `oga-igpu`, `oga-npu`, or other names we must map to? (affects `_RECIPE_BY_DEVICE` table)
3. Scope for `HardwareRequirement`: single-axis `min_device` vs adding memory/VRAM thresholds now? Recommend deferring memory to later.
4. Failure policy: raise vs permissive fallback when `/system-info` is degraded or absent on some platforms.
5. Confirm `devices` payload shape (dict vs list) in live Lemonade; tests must match the real payload shape.

Acceptance criteria for Phase 2
- `HardwareRequirement` dataclass and `Agent.REQUIRED_HARDWARE` implemented.
- `_RECIPE_BY_DEVICE` allow-list added and used for selection.
- Resolver in `LemonadeManager.ensure_ready()` that calls `LemonadeClient.get_system_info()` and returns an allow-listed recipe or raises.
- Unit tests with three payload fixtures and matrix tests cover positive and negative cases.

Next actions for implementer
- Get lemonade-specialist answers for Open Questions 1 and 2.
- Decide failure-mode policy (question 4).
- Implement the dataclass and resolver; add tests and fixtures; run unit tests.

Notes from Phase 1 repo search
- `FLM` appears in docs and C++ examples (e.g., `docs/cpp/overview.mdx`, `cpp/examples/*`) indicating it's a referenced backend name (FastFlowLM) but not present as a Python runtime module in `src/gaia/`.
- `oga-cpu` and `oga-hybrid` appear in `src/gaia/llm/lemonade_client.py` documentation and in `tests/test_lemonade_client.py`.

Maintainers to CC: @kovtcharov-amd (recipe vocabulary, failure policy), lemonade-specialist (confirm recipe/FLM semantics)

-- End Draft --
