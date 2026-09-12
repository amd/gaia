# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Measures how the memory block of the composed system prompt grows with the
number of stored memory entries.

Answers, with real code rather than a manual byte-count of one chat
transcript:

1. Does ``get_memory_system_prompt()`` inject ALL stored entries, or a
   bounded recalled subset? (the crux)
2. What is the empirical relationship between N stored entries and the
   resulting memory-block character count -- linear, capped, or something
   else?
3. At what N does the memory block alone exceed an 8,192-token or a
   32,768-token context budget (chars/4 ~= tokens)?

This is a STATIC measurement: it builds isolated ``MemoryStore`` instances
under ``tmp_path`` and calls ``MemoryMixin.get_memory_system_prompt()``
directly. No Lemonade, no running agent, no network.

Run as a normal pytest module:
    pytest tests/unit/test_memory_prompt_growth.py -v -s

Run standalone to print the full N -> chars table:
    python tests/unit/test_memory_prompt_growth.py
"""

from __future__ import annotations

import random
import sys
from pathlib import Path
from typing import Dict, List

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore

# ---------------------------------------------------------------------------
# Realistic content generation
# ---------------------------------------------------------------------------

#: Categories that a real user's `remember()` calls actually populate.
#: ("system"/"profile" are auto-collected + separately capped; "reminder" is
#: injected via the dynamic per-turn block, not the stable memory prompt.)
USER_CATEGORIES: List[str] = ["fact", "error", "note", "preference", "skill"]

#: Shared vocabulary pool giving each generated entry realistic-looking
#: content words. MemoryStore.store() dedupes on >80% word overlap
#: (Szymkiewicz-Simpson: |intersection| / min(|A|, |B|)), so near-duplicate
#: synthetic content would silently collapse N stored entries into far
#: fewer rows and invalidate the measurement -- see _make_entry() for how
#: collisions are ruled out by construction, not just made unlikely.
_WORD_POOL = [
    "kubernetes",
    "terraform",
    "postgres",
    "redis",
    "grpc",
    "graphql",
    "kafka",
    "docker",
    "vite",
    "react",
    "rust",
    "golang",
    "python",
    "typescript",
    "webpack",
    "nginx",
    "oauth",
    "jwt",
    "prometheus",
    "grafana",
    "elasticsearch",
    "mongodb",
    "sqlite",
    "faiss",
    "pytorch",
    "onnx",
    "npu",
    "ryzen",
    "lemonade",
    "vllm",
    "llama",
    "mistral",
    "qwen",
    "gemma",
    "macos",
    "windows",
    "linux",
    "wsl",
    "vscode",
    "neovim",
    "tmux",
    "zsh",
    "bash",
    "powershell",
    "cloudflare",
    "vercel",
    "netlify",
    "stripe",
    "twilio",
    "datadog",
    "helm",
    "istio",
    "envoy",
    "consul",
    "vault",
    "argo",
    "flux",
    "buildkite",
    "circleci",
    "sentry",
    "pagerduty",
    "airflow",
    "dagster",
    "dbt",
    "snowflake",
    "databricks",
    "spark",
    "flink",
    "pulsar",
    "nats",
    "rabbitmq",
    "celery",
    "gunicorn",
    "uvicorn",
    "fastapi",
    "flask",
    "django",
    "rails",
    "laravel",
    "symfony",
    "spring",
    "quarkus",
    "dotnet",
    "blazor",
    "swift",
    "kotlin",
    "flutter",
    "expo",
    "tailwind",
    "svelte",
    "solidjs",
    "remix",
    "astro",
    "qwik",
    "deno",
    "bun",
    "esbuild",
    "rollup",
    "turborepo",
    "nx",
    "bazel",
    "gradle",
    "maven",
    "cargo",
    "poetry",
    "pipenv",
    "conda",
    "homebrew",
    "chocolatey",
    "winget",
    "scoop",
    "ansible",
    "puppet",
    "chef",
    "packer",
    "vagrant",
    "virtualbox",
    "qemu",
    "proxmox",
    "openstack",
    "rancher",
    "k3s",
    "minikube",
    "kind",
    "eksctl",
    "fargate",
    "lambda",
    "cloudrun",
    "appengine",
    "firebase",
    "supabase",
    "planetscale",
    "neon",
    "turso",
    "clickhouse",
    "duckdb",
    "timescaledb",
    "influxdb",
    "cassandra",
    "dynamodb",
    "cosmosdb",
    "cockroachdb",
    "yugabyte",
    "etcd",
    "zookeeper",
    "keycloak",
    "auth0",
    "okta",
    "cognito",
    "clerk",
    "workos",
    "segment",
    "amplitude",
    "mixpanel",
    "posthog",
    "hotjar",
    "fullstory",
    "launchdarkly",
    "unleash",
    "featbit",
    "opentelemetry",
    "jaeger",
    "zipkin",
    "loki",
    "tempo",
    "victoriametrics",
    "thanos",
    "cortex",
    "argocd",
    "spinnaker",
    "tekton",
    "harness",
    "octopus",
    "teamcity",
    "bamboo",
    "jenkins",
    "gitlab",
    "bitbucket",
    "gitea",
    "forgejo",
    "sourcehut",
    "codeberg",
]

_FACT_PREFIXES = ("Fact:", "Known:", "Context:")
_FACT_TMPL = (
    "{prefix} stack is {a}, {b}, {c}, {d}; deploy via {e}; observability "
    "through {f}, {g}; owned by the {h} team; secondary dependency {i}."
)

_ERROR_PREFIXES = ("Error:", "Incident:", "Bug:")
_ERROR_TMPL = (
    "{prefix} {a} {b} broke on {c}; root cause was {d} misconfigured "
    "alongside {e}; fixed by reinstalling {f}, clearing the {g} cache, "
    "and pinning {h} below the {i} regression."
)

_NOTE_PREFIXES = ("Journal:", "Log:", "Update:")
_NOTE_TMPL = (
    "{prefix} touched {a}, {b}, {c} today; paired with the {d} team on "
    "{e}; also reviewed {f} and {g}, then wrote up notes on {h} for the "
    "{i} retro."
)

_PREFERENCE_PREFIXES = ("Prefers:", "Likes:", "Wants:")
_PREFERENCE_TMPL = (
    "{prefix} {a} > {b} for {c} work; favors {d}, {e}, {f}; commit refs "
    "{g}; avoids {h} for {i}."
)

_SKILL_PREFIXES = ("Learned:", "Gotcha:", "Tip:")
_SKILL_TMPL = (
    "{prefix} {a}+{b} version pin avoids {c} regression; workaround: "
    "{d}, {e}; also seen with {f}+{g}, {h}+{i}."
)

_TEMPLATES: Dict[str, tuple] = {
    "fact": (_FACT_TMPL, _FACT_PREFIXES),
    "error": (_ERROR_TMPL, _ERROR_PREFIXES),
    "note": (_NOTE_TMPL, _NOTE_PREFIXES),
    "preference": (_PREFERENCE_TMPL, _PREFERENCE_PREFIXES),
    "skill": (_SKILL_TMPL, _SKILL_PREFIXES),
}


def _make_entry(category: str, index: int) -> str:
    """Generate one realistic, near-guaranteed-unique memory entry.

    Deterministic per (category, index) so the table is reproducible. Each
    of the 9 content words is fused with this entry's index into a single
    token (e.g. "docker127" -- no separator, so the word-overlap regex's
    ``[^\\w\\s]`` punctuation strip can't split it back apart), so no two
    entries can ever share a content-word token. Only the handful of fixed
    scaffold words (e.g. "stack is", "deploy via") are ever shared between
    two entries of the same category, which keeps every pairwise overlap
    far below MemoryStore's 0.8 word-overlap dedup threshold regardless of
    pool-sampling coincidences. Lands in the ~150-300 char range, matching
    real fact/error/note/preference/skill entries.
    """
    rng = random.Random(f"{category}-{index}")
    tmpl, prefixes = _TEMPLATES[category]
    slots = "abcdefghi"
    words = [f"{w}{index}" for w in rng.sample(_WORD_POOL, k=len(slots))]
    prefix = prefixes[index % len(prefixes)]
    content = tmpl.format(prefix=prefix, **dict(zip(slots, words)))
    return f"{content} [ref:{index:06d}]"


def _seed_entries(store: MemoryStore, n: int, context: str = "global") -> int:
    """Store *n* entries evenly split across USER_CATEGORIES.

    Returns the number of DISTINCT rows actually stored (post-dedup), so a
    caller can confirm dedup did not silently collapse the intended N.
    """
    stored_ids = set()
    for i in range(n):
        category = USER_CATEGORIES[i % len(USER_CATEGORIES)]
        content = _make_entry(category, i)
        kid = store.store(
            category=category,
            content=content,
            confidence=0.5 + (i % 5) * 0.1,
            context=context,
        )
        stored_ids.add(kid)
    return len(stored_ids)


class _MemoryPromptHost(MemoryMixin):
    """Minimal host exposing only what get_memory_system_prompt() reads.

    Deliberately bypasses init_memory() (no embedder, no Lemonade, no FAISS)
    -- _build_stable_memory_prompt() only touches self._memory_store and
    self._memory_context, so this is the smallest correct harness.
    """

    def __init__(self, store: MemoryStore, context: str = "global"):
        self._memory_store = store
        self._memory_context = context


TRUNCATION_MARKER = "\n... (memory truncated)"

#: N values requested for the growth table, plus a few extra points (5, 15,
#: 20) in the pre-saturation region so the elbow where per-category LIMITs
#: (and then the 4000-char hard cap) take over is visible, not just implied
#: by the jump from N=10 to N=25.
N_VALUES: List[int] = [0, 5, 10, 15, 20, 25, 50, 100, 250, 500]

#: Context-window thresholds (tokens) whose crossover point we report.
_TOKEN_THRESHOLDS = [8192, 32768]


def measure_memory_prompt(tmp_path: Path, n: int) -> Dict[str, object]:
    """Seed *n* entries into an isolated store and measure the memory block.

    Returns a dict with n, stored (post-dedup row count), memory_chars,
    approx_tokens, and truncated (bool).
    """
    db_path = tmp_path / f"memory_n{n}.db"
    store = MemoryStore(db_path=db_path)
    try:
        stored = _seed_entries(store, n)
        host = _MemoryPromptHost(store)
        prompt = host.get_memory_system_prompt()
    finally:
        store.close()

    return {
        "n": n,
        "stored": stored,
        "memory_chars": len(prompt),
        "approx_tokens": round(len(prompt) / 4),
        "truncated": TRUNCATION_MARKER.strip() in prompt,
    }


def build_growth_table(tmp_path: Path, n_values: List[int] = N_VALUES) -> List[Dict]:
    return [measure_memory_prompt(tmp_path, n) for n in n_values]


def format_growth_table(rows: List[Dict]) -> str:
    lines = [
        f"{'N':>6} | {'stored':>6} | {'memory_chars':>12} | {'~tokens':>8} | truncated",
        "-" * 60,
    ]
    for r in rows:
        lines.append(
            f"{r['n']:>6} | {r['stored']:>6} | {r['memory_chars']:>12} | "
            f"{r['approx_tokens']:>8} | {r['truncated']}"
        )
    return "\n".join(lines)


# ===========================================================================
# Tests
# ===========================================================================


class TestMemoryPromptGrowth:
    """Empirically characterize memory-block growth vs. stored entry count."""

    def test_zero_entries_has_nonzero_baseline(self, tmp_path):
        """Even with 0 stored entries, the instructions block is present."""
        row = measure_memory_prompt(tmp_path, 0)
        assert row["stored"] == 0
        assert row["memory_chars"] > 0  # instructions text always present

    def test_dedup_did_not_collapse_seeded_entries(self, tmp_path):
        """Sanity: the generator produces N distinct rows, not fewer via dedup."""
        for n in (10, 50, 100):
            db_path = tmp_path / f"dedup_check_{n}.db"
            store = MemoryStore(db_path=db_path)
            try:
                stored = _seed_entries(store, n)
            finally:
                store.close()
            assert stored == n, (
                f"expected {n} distinct rows, got {stored} -- content "
                "generator is producing near-duplicate entries"
            )

    def test_memory_block_is_bounded_not_unbounded(self, tmp_path):
        """The memory block must NOT grow without limit as N grows.

        This is the crux question from the audit: does the composed system
        prompt inject ALL stored memories, or a bounded subset? If this
        assertion ever fails, the 4000-char hard cap in
        MemoryMixin._build_stable_memory_prompt() has regressed and memory
        growth really is unbounded -- treat that as a P0, not a nit.
        """
        small = measure_memory_prompt(tmp_path, 10)
        large = measure_memory_prompt(tmp_path, 500)
        # 500 stored entries must not make the prompt 50x bigger than 10
        # stored entries -- it must be flat (capped), not linear.
        assert large["memory_chars"] < small["memory_chars"] * 5

    def test_memory_block_never_exceeds_hard_cap(self, tmp_path):
        """No N should ever push the memory block past the 4000-char cap."""
        _MARKER = TRUNCATION_MARKER
        for n in N_VALUES:
            row = measure_memory_prompt(tmp_path, n)
            assert row["memory_chars"] <= 4000 + len(_MARKER), (
                f"N={n} produced a {row['memory_chars']}-char memory block, "
                "exceeding the documented 4000-char hard cap"
            )

    def test_growth_saturates_well_below_8k_token_window(self, tmp_path):
        """The memory block alone must stay far under an 8,192-token budget.

        4000 chars ~= 1000 tokens, so even a fully-saturated memory block
        should leave ~7k tokens of an 8k window for tools + conversation.
        """
        row = measure_memory_prompt(tmp_path, 500)
        assert row["approx_tokens"] < 8192

    def test_print_growth_table(self, tmp_path, capsys):
        """Not an assertion -- prints the N -> chars table for humans.

        Run with `-s` to see it: pytest tests/unit/test_memory_prompt_growth.py -k growth_table -s
        """
        rows = build_growth_table(tmp_path)
        print("\n" + format_growth_table(rows))
        print("\n" + format_growth_summary(rows))


# ===========================================================================
# Slope + threshold summary (shared by the pytest report and the CLI)
# ===========================================================================


def format_growth_summary(rows: List[Dict]) -> str:
    """Slope over the still-growing region, plus the two token-budget crossings.

    The slope is fit by simple linear regression over every row up to and
    including the first one that got truncated (or all rows, if the cap was
    never hit) -- i.e. the region where adding entries still visibly grows
    the prompt, before per-category LIMITs and the 4000-char hard cap take
    over and flatten it.
    """
    lines = []

    first_capped = next((i for i, r in enumerate(rows) if r["truncated"]), None)
    growth_region = rows[: first_capped + 1] if first_capped is not None else rows
    if len(growth_region) >= 2:
        xs = [r["n"] for r in growth_region]
        ys = [r["memory_chars"] for r in growth_region]
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        denom = sum((x - mean_x) ** 2 for x in xs)
        slope = (
            sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
            if denom
            else 0.0
        )
        lines.append(
            f"Slope (0->{xs[-1]} entries, pre-saturation fit): "
            f"{slope:.1f} chars/entry"
        )
    saturated_at = growth_region[-1]["n"] if first_capped is not None else None
    if saturated_at is not None:
        lines.append(f"Saturates (hits the 4000-char hard cap) at N<={saturated_at}")
    else:
        lines.append(f"Never hit the 4000-char hard cap up to N={rows[-1]['n']}")

    for threshold in _TOKEN_THRESHOLDS:
        crossing = next((r["n"] for r in rows if r["approx_tokens"] > threshold), None)
        label = f"{threshold}-token window"
        if crossing is None:
            lines.append(f"{label}: never exceeded up to N={rows[-1]['n']}")
        else:
            lines.append(f"{label}: exceeded at N={crossing}")

    return "\n".join(lines)


# ===========================================================================
# Standalone script entry point
# ===========================================================================

if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory(prefix="gaia_memory_growth_") as tmp:
        rows = build_growth_table(Path(tmp))
        print(format_growth_table(rows))
        print("\n" + format_growth_summary(rows))

    sys.exit(0)
