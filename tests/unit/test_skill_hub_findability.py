# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Can a plain request find the right hub skill?

Every skill under ``hub/skills/`` is published, through the real publish path,
to a :class:`FakeHub`; then ``search_skills`` runs the short phrases a user or
the agent would type. A description edit that makes a skill unfindable fails
here, before it ships. No LLM and no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.skills.hub import search_skills
from tests.unit.skills_helpers import make_marketplace

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIRS = sorted(
    d for d in (REPO_ROOT / "hub" / "skills").iterdir() if (d / "SKILL.md").is_file()
)

#: Plain queries per skill; the skill must rank in the top 3 for each.
QUERIES: dict[str, list[str]] = {
    "check-in": ["check in", "what was I working on"],
    "coding": ["fix a bug", "refactor code", "make a test pass"],
    "daily-brief": ["morning briefing", "what do I need to know today"],
    "data-explore": ["csv totals", "spreadsheet breakdown"],
    "document-brief": ["contract", "what does this PDF say"],
    "file-ops": ["edit a file", "rename file"],
    "github-triage": ["github issues", "github notifications"],
    "image-gen": ["draw a picture", "generate an image"],
    "inbox-triage": ["inbox", "unread mail"],
    "music-gen": ["music", "sound effects"],
    "price-watch": ["track price", "price drop"],
    "recommendations": ["what to watch", "book recommendations"],
    "research-report": ["research report with sources", "literature scan"],
    "rss-digest": ["summarizing RSS feeds", "atom feed"],
    "source-watch": ["monitor a website", "watch a URL for changes"],
    "summarize": ["summarize a pdf", "key decisions"],
    "transcribe-meeting": ["meeting notes", "transcribe a recording"],
}


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    market = make_marketplace(tmp_path_factory.mktemp("findability"))
    market.keygen()
    for skill_dir in SKILL_DIRS:
        market.publish(skill_dir, key_name="publisher")
    return market


def _search(hub, query: str) -> list[str]:
    found = search_skills(
        query,
        base_url=hub.hub.BASE_URL,
        fetcher=hub.hub.fetcher,
        cache_path=hub.tmp_path / "catalog-cache.json",
        force=True,
    )
    return [entry["id"] for entry in found]


def test_every_hub_skill_has_queries():
    assert set(QUERIES) == {d.name for d in SKILL_DIRS}


def test_the_published_catalogue_lists_every_hub_skill(hub):
    assert sorted(_search(hub, "")) == sorted(d.name for d in SKILL_DIRS)


@pytest.mark.parametrize(
    "skill,query", [(s, q) for s, qs in QUERIES.items() for q in qs]
)
def test_plain_query_finds_the_skill_near_the_top(hub, skill, query):
    results = _search(hub, query)
    assert skill in results[:3], (
        f"{query!r} should find {skill} in the top 3; got {results[:4]}. If a "
        f"description changed, make sure it still says what users ask for."
    )
