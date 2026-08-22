"""Offline measurement of the ADAPTIVE-SKILLS overlay's prompt cost (#2674).

**Contacts no model.** Derived from ``.perf/offline_prefill.py`` in the
prefill-cost worktree (``claudia/task-25e62f25``) and uses its stubbing
verbatim, so the two measurements are comparable: the embedder is faked so
memory v2 initialises, and every HTTP verb is armed to raise.

That script measures the agent's *fixed* prefill with no skill loaded. This one
measures the thing it cannot see: what a loaded skill costs, and what the
learned overlay adds to or removes from it. Three states, same agent, same
tokenizer:

    authored        the skill exactly as shipped
    overlay ON      the same skill with approved learned changes resolved in
    append-only     what a naive "Learned adjustments" block would have cost

Token counts are tiktoken cl100k_base — the proxy ``src/gaia/eval/tool_cost.py``
pins its baseline with. Absolute Gemma counts differ; deltas and ratios do not.

The memory store is redirected to a throwaway file: a measurement must never
write learned deltas into the developer's real ``~/.gaia/memory.db``.

Usage (from the worktree root):
    PYTHONPATH="<wt>\\src;<wt>\\hub\\agents\\chat\\python;<wt>\\hub\\agents\\gaia\\python" \
        python .perf/overlay_prefill.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import tiktoken

_ENC = tiktoken.get_encoding("cl100k_base")

SKILL = "github-triage"


def tok(s: str) -> int:
    return len(_ENC.encode(s, disallowed_special=()))


def _fake_embed(_self, _text, **_kw):
    v = np.zeros(768, dtype=np.float32)
    v[0] = 1.0
    return v


def _fake_embed_batch(_self, texts, **_kw):
    return np.tile(_fake_embed(None, ""), (len(list(texts)), 1))


class NetworkContacted(AssertionError):
    """Raised if anything here would reach out over HTTP."""


def _no_network(*a, **kw):
    raise NetworkContacted(
        f"HTTP call attempted during an offline measurement: {a[:2]}. "
        "Lemonade is banned; this must not contact it."
    )


INBOX_PROCEDURE = """## Procedure

1. **Pull what landed on you.** Default to the user's own inbox, not a
   repository backlog.

   ```bash
   gh search issues --involves @me --state open --limit 30 --json number,title,repository
   ```

2. **Find what is blocking you.** PRs awaiting your review first, then issues
   assigned to you.

3. **Rank by what unblocks other people**, then by severity and reach.

4. **Say what to do next** — one concrete action per item, not a summary.

5. **Draft, do not send.** Show the comment or label change and wait."""


def main() -> None:
    import requests

    from gaia.agents.base.memory import MemoryMixin
    from gaia.llm.lemonade_manager import LemonadeManager

    with patch.object(MemoryMixin, "_embed_text", _fake_embed), patch.object(
        MemoryMixin, "_embed_texts_batch", _fake_embed_batch, create=True
    ), patch.object(
        MemoryMixin, "_get_embedder", lambda self: object()
    ), patch.object(
        LemonadeManager, "ensure_ready", staticmethod(lambda *a, **k: None)
    ), patch.object(
        requests, "post", _no_network
    ), patch.object(
        requests, "get", _no_network
    ), patch.object(
        requests.Session, "request", _no_network
    ):
        from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

        from gaia.agents.base.memory_store import MemoryStore
        from gaia.agents.base.skill_deltas import (
            KIND_DROP_SECTION,
            KIND_REPLACE_SECTION,
        )
        from gaia.skills.sections import find_section, parse_sections

        agent = GaiaAgent(config=GaiaAgentConfig(silent_mode=True))
        if getattr(agent, "_memory_store", None) is None:
            raise SystemExit(
                "ABORT: memory v2 did not initialise; the embedder stub failed "
                "and every number below would be wrong."
            )

        tmp = Path(tempfile.mkdtemp(prefix="overlay-prefill-"))
        agent._memory_store = MemoryStore(db_path=tmp / "memory.db")

        agent.load_skill(SKILL)
        skill = agent.loaded_skills[SKILL]
        base_body = skill.body

        print("=" * 72)
        print("ADAPTIVE-SKILLS OVERLAY COST (offline, no model contacted)")
        print("=" * 72)
        print(f"skill                  : {SKILL} v{skill.version}")
        print(f"memory v2 initialised  : True")
        print(f"scope                  : {agent.learned_skill_scope()}")
        print()

        # --- state 1: authored, no deltas ---------------------------------
        agent._effective_skill_cache = None
        authored_frag = agent.get_skills_system_prompt()
        authored_sp = agent.system_prompt

        # --- state 2: overlay ON ------------------------------------------
        sections = parse_sections(base_body)
        proc = find_section(sections, "procedure")
        fork = find_section(sections, "fork-this")

        d1 = agent._memory_store.put_delta(
            base_name=SKILL,
            scope=agent.learned_skill_scope(),
            kind=KIND_REPLACE_SECTION,
            anchor_section="procedure",
            anchor_digest=proc.digest,
            payload={"body": INBOX_PROCEDURE},
            provenance={"source": "user_instruction"},
        )
        agent._memory_store.approve_delta(d1)
        if fork is not None:
            d2 = agent._memory_store.put_delta(
                base_name=SKILL,
                scope=agent.learned_skill_scope(),
                kind=KIND_DROP_SECTION,
                anchor_section="fork-this",
                anchor_digest=fork.digest,
                payload={},
                provenance={"source": "user_instruction"},
            )
            agent._memory_store.approve_delta(d2)

        agent._effective_skill_cache = None
        agent.rebuild_system_prompt()
        overlay_frag = agent.get_skills_system_prompt()
        overlay_sp = agent.system_prompt

        # --- state 3: what append-only would have cost --------------------
        appended = base_body + "\n\n## Learned adjustments\n\n" + INBOX_PROCEDURE

        # --- state 4: the off-switch floor --------------------------------
        agent._learned_skills_enabled = False
        agent._effective_skill_cache = None
        off_frag = agent.get_skills_system_prompt()

        base_t = tok(base_body)
        over_t = tok(overlay_frag) - (tok(authored_frag) - tok(base_body))
        app_t = tok(appended)

        print("--- skill body, resolved ---")
        print(f"authored base          : {base_t:>7,} tok")
        print(f"overlay ON (replace)   : {over_t:>7,} tok   {over_t - base_t:+,} "
              f"({100 * (over_t - base_t) / base_t:+.1f}%)")
        print(f"append-only (rejected) : {app_t:>7,} tok   {app_t - base_t:+,} "
              f"({100 * (app_t - base_t) / base_t:+.1f}%)")
        print()

        print("--- skills prompt fragment (what actually ships) ---")
        af, of = tok(authored_frag), tok(overlay_frag)
        print(f"authored               : {af:>7,} tok")
        print(f"overlay ON             : {of:>7,} tok   {of - af:+,}")
        print()

        print("--- FULL system prompt ---")
        asp, osp = tok(authored_sp), tok(overlay_sp)
        print(f"authored               : {asp:>7,} tok")
        print(f"overlay ON             : {osp:>7,} tok   {osp - asp:+,} "
              f"({100 * (osp - asp) / asp:+.2f}%)")
        print()

        print("--- off-switch floor (--no-learned-skills) ---")
        identical = off_frag == authored_frag
        print(f"byte-identical to authored : {identical}   <- must be True")
        if not identical:
            raise SystemExit("ABORT: the off-switch did not restore the base bytes.")

        print()
        print("=" * 72)
        print(f"OVERLAY COST PER TURN: {of - af:+,} tok "
              f"(budget asked by task-25e62f25: <= +500)")
        print("Paid only on turns where this skill is active; an inactive loaded")
        print("skill collapses to a menu line and its overlay costs nothing.")
        print("=" * 72)


if __name__ == "__main__":
    main()
