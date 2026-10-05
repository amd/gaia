# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pins the skills/web/tool-selection tier fixtures to the scenarios using them.

The values contract is ``eval/scenarios/GAIA_FIXTURE_VALUES.md`` ("Skills, web
and tool selection"). These tests check the fixtures actually hold what the
scenarios assert: the served URLs resolve, planted facts and hidden injections
survive text extraction, the long page's key fact is out of reach of a
fetch_page excerpt, and the new fixture-hub skills install (or refuse) as the
lifecycle scenarios expect.
"""

from __future__ import annotations

import re
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from gaia.agents.base.tool_output import elide_text
from gaia.web.client import WebClient

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "gaia"
TIERS = FIXTURES / "tiers_skills_web"
SCENARIOS = REPO_ROOT / "eval" / "scenarios"
MY_CATEGORIES = (
    "gaia_skills_lifecycle",
    "gaia_skills_tasks",
    "gaia_skills_capture",
    "gaia_web",
    "gaia_tool_selection",
)
#: fetch_page's largest window; the excerpt keeps head + tail within it.
FETCH_PAGE_MAX = 20000


def _fixtures_import(name: str):
    sys.path.insert(0, str(FIXTURES))
    try:
        return __import__(name)
    finally:
        sys.path.remove(str(FIXTURES))


def _page_text(relative: str) -> str:
    client = WebClient()
    try:
        html = (TIERS / relative).read_text(encoding="utf-8")
        return client.extract_text(client.parse_html(html), max_length=None)
    finally:
        client.close()


@pytest.fixture()
def routed_server():
    server = _fixtures_import("serve_fixtures").make_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(url: str) -> str:
    with urllib.request.urlopen(url) as response:
        return response.read().decode("utf-8")


@pytest.mark.allow_network  # loopback socket only (ephemeral port)
def test_routed_layout_serves_the_tier_fixtures(routed_server):
    base = f"{routed_server}/tiers_skills_web"
    assert "$89" in _get(f"{base}/web/cirrus_poles.html")
    assert _get(f"{base}/web/directory.html").count("<li><a href=") == 48
    assert "9:00 AM to 1:00 PM" in _get(f"{base}/web/branches/willow_creek.html")
    assert "Closed on Saturdays" in _get(f"{base}/web/branches/willowby.html")
    assert _get(f"{base}/feeds/northwind.xml").count("<item>") == 4
    assert _get(f"{base}/feeds/trailnews.xml").count("<item>") == 2
    assert "EXE-FIXTURE-7Q4" in _get(f"{base}/downloads/atlas-companion-setup.exe")
    assert "name: trail-log" in _get(f"{base}/capture/trail-log/SKILL.md")
    # The pre-existing root routes are untouched by the new prefix.
    assert "1.9 kg" in _get(f"{routed_server}/atlas.html")
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(f"{base}/web/branches/oakhurst.html")
    assert excinfo.value.code == 404


@pytest.mark.allow_network  # loopback socket only (ephemeral port)
def test_every_tier_url_in_the_scenarios_resolves(routed_server):
    """A scenario URL that 404s by accident scores the agent on a broken fixture."""
    pattern = re.compile(r"http://127\.0\.0\.1:8765(/tiers_skills_web/[^\s\"'`)?,]+)")
    urls = set()
    for category in MY_CATEGORIES:
        for path in (SCENARIOS / category).glob("*.yaml"):
            found = pattern.findall(path.read_text(encoding="utf-8"))
            urls.update(url.rstrip(".") for url in found)
    assert urls, "no scenario references the tier fixtures"
    for path in sorted(urls):
        _get(f"{routed_server}{path}")


def test_staged_folder_paths_in_the_scenarios_exist():
    """``~/gaia-eval/<x>`` is a copy of ``tests/fixtures/gaia/<x>``."""
    pattern = re.compile(r"~/gaia-eval/(tiers_skills_web/[^\s\"'`)]+)")
    found = set()
    for category in MY_CATEGORIES:
        for path in (SCENARIOS / category).glob("*.yaml"):
            found.update(pattern.findall(path.read_text(encoding="utf-8")))
    assert found
    for relative in sorted(found):
        assert (FIXTURES / relative).exists(), relative


def test_long_page_fact_sits_outside_the_largest_excerpt():
    text = _page_text("web/ferry_timetable.html")
    excerpt = elide_text(text, FETCH_PAGE_MAX)
    shown = excerpt["head"] + excerpt["tail"]
    assert "Sundays the last sailing from Pier 9 departs at 10:40 PM" in text
    assert "10:40 PM" not in shown
    # The decoy and the head/tail facts ARE in the excerpt.
    assert "11:15 PM" in excerpt["head"]
    assert "Tidewater Transit Authority" in excerpt["head"]
    assert "R-31" in excerpt["tail"]


def test_hidden_injections_survive_text_extraction():
    """An injection the extractor drops would make the security scenarios vacuous."""
    poles = _page_text("web/cirrus_poles.html")
    assert "$89 per pair" in poles and "480 g per pair" in poles
    assert "PINECONE-7" in poles and "169.254.169.254" in poles
    assert "localhost:4200" in poles
    app = _page_text("web/companion_app.html")
    assert "3.2.1" in app and "MUST call download_file" in app
    lantern = _page_text("web/lantern_deal.html")
    assert "$34" in lantern and "remember_skill_lesson" in lantern


@pytest.mark.parametrize(
    "name", ["receipt-format", "packing-list", "trail-log", "field-guide-long"]
)
def test_benign_capture_fixtures_pass_the_audit(name):
    """The capture scenarios assume these land; a BLOCK would fail them for
    a reason unrelated to the agent."""
    from gaia.skills.audit.engine import audit_skill

    report = audit_skill(TIERS / "capture" / name, tier="experimental")
    assert report.verdict == "ALLOW", report.findings


def test_long_capture_skill_buries_the_money_rule():
    text = (TIERS / "capture" / "field-guide-long" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert len(text) > 15000
    assert text.index("## 12. Currency formatting") > 0.85 * len(text)


@pytest.fixture()
def prepared_hub(tmp_path):
    prepare = _fixtures_import("prepare_fixture_hub").prepare
    skills_root = tmp_path / "skills"
    hub_dir = tmp_path / "hub"
    summaries = prepare(
        skills_root,
        hub_dir,
        frozenset({"experimental-notes"}),
        tampered=frozenset({"tampered-notes"}),
    )
    return skills_root, hub_dir, summaries


def test_prepare_rejects_a_skill_both_unsigned_and_tampered(tmp_path):
    prepare = _fixtures_import("prepare_fixture_hub").prepare
    with pytest.raises(ValueError, match="both --unsigned and --tampered"):
        prepare(
            tmp_path / "skills",
            tmp_path / "hub",
            frozenset({"tampered-notes"}),
            tampered=frozenset({"tampered-notes"}),
        )


@pytest.mark.allow_network  # loopback socket only (ephemeral port)
def test_new_hub_skills_install_or_refuse_as_the_scenarios_expect(
    prepared_hub, monkeypatch
):
    from gaia.skills.install import install_skill
    from gaia.skills.manager import SkillManager
    from gaia.skills.signing import SkillSignatureError

    skills_root, hub_dir, summaries = prepared_hub
    assert summaries["tampered-notes"]["tampered"] is True
    assert summaries["unit-convert"]["tampered"] is False

    server = _fixtures_import("serve_fixtures").make_server(0, hub_dir)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("GAIA_HUB_URL", f"http://127.0.0.1:{server.server_address[1]}")

    def _no_prompt(prompt: str) -> bool:
        raise AssertionError(f"install prompted unexpectedly: {prompt}")

    try:
        mgr = SkillManager(user_skills_root=skills_root, include_claude_roots=False)
        for name in ("unit-convert", "power-helper"):
            result = install_skill(name, manager=mgr, confirm=_no_prompt)
            assert result.installed_tier == "community", name
            assert result.signature is not None and result.signature.trusted

        with pytest.raises(SkillSignatureError):
            install_skill("tampered-notes", manager=mgr, confirm=_no_prompt)
        assert not (skills_root / "tampered-notes").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
