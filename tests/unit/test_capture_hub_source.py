# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Skills published on the GAIA hub are installed, never captured or imported.

``gaia skill install`` checks a hub skill's signature; capture and import do
not. So capture refuses a URL on the hub (configured or public) and any skill
whose name the hub catalog lists, and ``gaia skill import`` refuses hub URLs
through the same check. A name whose hub install failed verification this
session is refused too.

Each refusal is paired with the near-miss it must NOT catch, so the guard is
proven not to block unrelated captures.

Cold state throughout: skills roots, lock, and catalog cache live under
``tmp_path``; the catalog comes from a stub or an injected fetcher.
"""

from __future__ import annotations

import argparse
import json
import zipfile
from types import SimpleNamespace

import pytest

from gaia.skills import capture as capture_mod
from gaia.skills.capture import (
    SkillCaptureError,
    capture_skill,
    failed_verification_message,
    import_bundle,
    published_on_hub_message,
)
from gaia.skills.capture import published_skill_names as _real_published_skill_names
from gaia.skills.capture import (
    refuse_hub_url,
)
from gaia.skills.errors import SkillError
from gaia.skills.hub import SkillHubError, SkillSearchResult
from tests.unit.skills_helpers import isolated_manager

PUBLISHED = "rss-digest"


def _markdown(name: str, *, raw_name_line: str | None = None) -> str:
    return "\n".join(
        [
            "---",
            raw_name_line or f"name: {name}",
            "description: A test skill.",
            'version: "1.0.0"',
            "---",
            "",
            "# Notes",
            "",
            "Summarize the notes.",
            "",
        ]
    )


@pytest.fixture
def manager(tmp_path):
    return isolated_manager(tmp_path)


@pytest.fixture(autouse=True)
def _public_hub_only(monkeypatch):
    monkeypatch.delenv("GAIA_HUB_URL", raising=False)


@pytest.fixture
def catalog(monkeypatch):
    """The real name check over a stub catalog; ``entries`` is editable per test."""
    state = SimpleNamespace(
        entries=[
            {"id": PUBLISHED, "name": PUBLISHED, "type": "skill"},
            {"id": "github-triage", "name": "GitHub Triage", "type": "skill"},
        ],
        error=None,
        calls=0,
    )

    def _search(query: str = "", **_kwargs):
        state.calls += 1
        if state.error is not None:
            raise state.error
        return SkillSearchResult(entries=list(state.entries))

    monkeypatch.setattr(
        capture_mod, "published_skill_names", _real_published_skill_names
    )
    monkeypatch.setattr("gaia.skills.hub.search_skills", _search)
    return state


class _Client:
    """Stands in for WebClient; records fetches and can report a redirect."""

    def __init__(self, content: bytes, *, final_url: str | None = None):
        self.calls: list[str] = []
        self._content = content
        self._final_url = final_url

    def get(self, url: str, **_kwargs):
        self.calls.append(url)
        return SimpleNamespace(
            content=self._content,
            headers={"Content-Type": "text/plain"},
            url=self._final_url or url,
        )


def _nothing_landed(manager) -> bool:
    root = manager.user_root
    return not root.exists() or not any(p.is_dir() for p in root.iterdir())


# ---------------------------------------------------------------------------
# URLs on the hub
# ---------------------------------------------------------------------------

HUB_URLS = [
    "https://hub.amd-gaia.ai",
    "https://hub.amd-gaia.ai/",
    "https://HUB.AMD-GAIA.AI/skills/rss-digest/1.0.0/SKILL.md",
    "https://hub.amd-gaia.ai:443/skills/rss-digest/1.0.0/SKILL.md",
    "http://hub.amd-gaia.ai/skills/rss-digest/1.0.0/SKILL.md",
    "http://hub.amd-gaia.ai:80/skills/rss-digest/1.0.0/SKILL.md",
    "https://hub.amd-gaia.ai:8443/skills/rss-digest/1.0.0/SKILL.md",
    "https://hub.amd-gaia.ai/skills/rss-digest/1.0.0/rss-digest-1.0.0.zip",
    "https://hub.amd-gaia.ai/skills/rss-digest/manifest.json?download=1#top",
    "https://hub.amd-gaia.ai./skills/rss-digest/1.0.0/SKILL.md",
    "https://user:secret@hub.amd-gaia.ai/skills/rss-digest/1.0.0/SKILL.md",
    "https://hub.amd-gaia.ai/x/../skills/rss-digest/1.0.0/SKILL.md",
    "https://hub.amd-gaia.ai/%73kills/rss-digest/1.0.0/SKILL.md",
    "https://evil.example\\@hub.amd-gaia.ai/skills/rss-digest/1.0.0/SKILL.md",
    "https://hub.amd-gaia.ai\\@evil.example/",
    "  https://hub.amd-gaia.ai/skills/rss-digest/1.0.0/SKILL.md  ",
    "https://hub.amd-gaia.ai:99999/skills/rss-digest/1.0.0/SKILL.md",
    "https://hub.amd-gaia.ai:abc/skills/rss-digest/1.0.0/SKILL.md",
]

NOT_HUB_URLS = [
    # The real host is evil.example; the hub name is only userinfo.
    "https://hub.amd-gaia.ai@evil.example/skills/rss-digest/1.0.0/SKILL.md",
    "https://evil.example/?u=hub.amd-gaia.ai",
    "https://evil.example/hub.amd-gaia.ai/skills/rss-digest/SKILL.md",
    "https://hub.amd-gaia.ai.evil.example/skills/rss-digest/SKILL.md",
    "https://nothub.amd-gaia.ai/skills/rss-digest/SKILL.md",
    "https://amd-gaia.ai/docs/spec/agent-skills",
]


@pytest.mark.parametrize("url", HUB_URLS)
def test_hub_urls_are_refused_before_any_fetch(url, manager):
    client = _Client(_markdown("meeting-notes").encode("utf-8"))

    with pytest.raises(SkillCaptureError, match="GAIA hub") as raised:
        capture_skill(url, manager=manager, web_client=client)

    assert client.calls == [], "a hub URL must be refused before it is fetched"
    assert "gaia skill install" in str(raised.value)
    assert _nothing_landed(manager)


def test_a_hub_skill_url_names_the_skill_in_the_refusal():
    with pytest.raises(SkillCaptureError) as raised:
        refuse_hub_url("https://hub.amd-gaia.ai/skills/rss-digest/1.0.0/SKILL.md")

    assert str(raised.value) == published_on_hub_message(PUBLISHED)
    assert str(raised.value) == (
        "rss-digest is published on the GAIA hub. Install it with `gaia skill "
        "install rss-digest` (or ask GAIA to install it), which checks its "
        "signature."
    )


@pytest.mark.parametrize("url", NOT_HUB_URLS)
def test_lookalike_urls_are_not_treated_as_the_hub(url, manager):
    refuse_hub_url(url)  # no raise

    client = _Client(_markdown("meeting-notes").encode("utf-8"))
    result = capture_skill(url, manager=manager, web_client=client)

    assert client.calls == [url]
    assert result.name == "meeting-notes"


def test_a_redirect_onto_the_hub_is_refused_and_writes_nothing(manager):
    client = _Client(
        _markdown("meeting-notes").encode("utf-8"),
        final_url="https://hub.amd-gaia.ai/skills/rss-digest/1.0.0/SKILL.md",
    )

    with pytest.raises(SkillCaptureError, match="rss-digest is published"):
        capture_skill("https://short.example/r/abc", manager=manager, web_client=client)
    assert _nothing_landed(manager)


class TestConfiguredHub:
    BASE = "http://127.0.0.1:8765/fixture_hub"

    @pytest.fixture(autouse=True)
    def _configured(self, monkeypatch):
        monkeypatch.setenv("GAIA_HUB_URL", self.BASE)

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8765/fixture_hub",
            "http://127.0.0.1:8765/fixture_hub/skills/rss-digest/1.0.0/SKILL.md",
            "http://127.0.0.1:8765/FIXTURE_HUB/skills/rss-digest/1.0.0/SKILL.md",
            "https://127.0.0.1:8765/fixture_hub/index.json",
            "http://127.0.0.1:8765/capture/../fixture_hub/skills/x/1.0.0/SKILL.md",
            # The public hub stays refused under an override.
            "https://hub.amd-gaia.ai/skills/rss-digest/1.0.0/SKILL.md",
        ],
    )
    def test_refused(self, url):
        with pytest.raises(SkillCaptureError, match="GAIA hub"):
            refuse_hub_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            # Same server, outside the hub's path: an ordinary capture source.
            "http://127.0.0.1:8765/capture/meeting-notes/SKILL.md",
            "http://127.0.0.1:8765/fixture_hub_other/skills/x/SKILL.md",
            "http://127.0.0.1:9000/fixture_hub/skills/x/SKILL.md",
            "http://localhost:8765/elsewhere/SKILL.md",
        ],
    )
    def test_not_refused(self, url):
        refuse_hub_url(url)

    def test_trailing_slash_and_default_port_match(self, monkeypatch):
        monkeypatch.setenv("GAIA_HUB_URL", "https://hub.example.com/")
        with pytest.raises(SkillCaptureError, match="my-skill is published"):
            refuse_hub_url("https://HUB.example.com:443/skills/my-skill/1.0.0/SKILL.md")
        refuse_hub_url("https://hub.example.com:8443/skills/my-skill/1.0.0/SKILL.md")


# ---------------------------------------------------------------------------
# Names the hub publishes
# ---------------------------------------------------------------------------


class TestPublishedName:
    def test_pasted_text_with_a_hub_name_is_refused(self, manager, catalog):
        with pytest.raises(SkillCaptureError) as raised:
            capture_skill(_markdown(PUBLISHED), manager=manager)

        assert str(raised.value) == published_on_hub_message(PUBLISHED)
        assert _nothing_landed(manager)

    @pytest.mark.parametrize(
        "line",
        [
            "name:   rss-digest   ",
            "name: 'rss-digest'",
            'name: "rss-digest"',
            "name: RSS-Digest",
            "name: rss-digest\t",
        ],
    )
    def test_front_matter_spelling_variants_never_land(self, manager, catalog, line):
        with pytest.raises(SkillError):
            capture_skill(_markdown(PUBLISHED, raw_name_line=line), manager=manager)
        assert _nothing_landed(manager)

    def test_catalog_spelling_variants_still_match(self, manager, catalog):
        catalog.entries = [{"id": "  RSS-Digest ", "type": "skill"}]

        with pytest.raises(SkillCaptureError, match="rss-digest is published"):
            capture_skill(_markdown(PUBLISHED), manager=manager)

    def test_renaming_on_capture_does_not_get_around_it(self, manager, catalog):
        with pytest.raises(SkillCaptureError, match="rss-digest is published"):
            capture_skill(_markdown(PUBLISHED), name="my-copy", manager=manager)
        with pytest.raises(SkillCaptureError, match="rss-digest is published"):
            capture_skill(_markdown("my-notes"), name=PUBLISHED, manager=manager)
        assert _nothing_landed(manager)

    def test_a_folder_source_with_a_hub_name_is_refused(
        self, manager, catalog, tmp_path
    ):
        folder = tmp_path / "src" / "anything"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(_markdown(PUBLISHED), encoding="utf-8")

        with pytest.raises(SkillCaptureError, match="rss-digest is published"):
            capture_skill(str(folder), manager=manager)
        assert _nothing_landed(manager)

    def test_a_zip_from_another_host_with_a_hub_name_is_refused(
        self, manager, catalog, tmp_path
    ):
        archive = tmp_path / "bundle.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr(f"{PUBLISHED}/SKILL.md", _markdown(PUBLISHED))
        client = _Client(archive.read_bytes())

        with pytest.raises(SkillCaptureError, match="rss-digest is published"):
            capture_skill(
                "https://mirror.example/bundle.zip", manager=manager, web_client=client
            )
        assert _nothing_landed(manager)

    @pytest.mark.parametrize(
        "name", ["rss-digest-notes", "my-rss-digest", "rss", "github-triage-2"]
    )
    def test_unrelated_names_still_capture(self, manager, catalog, name):
        result = capture_skill(_markdown(name), manager=manager)

        assert result.name == name
        assert (manager.user_root / name / "SKILL.md").is_file()

    def test_a_display_title_does_not_shadow_a_real_name(self, manager, catalog):
        # "GitHub Triage" is a title; only the id is a skill name.
        result = capture_skill(_markdown("github"), manager=manager)
        assert result.name == "github"

    def test_an_unreadable_catalog_refuses_loudly(self, manager, catalog):
        catalog.error = SkillHubError("no network and no cache")

        with pytest.raises(SkillCaptureError) as raised:
            capture_skill(_markdown("meeting-notes"), manager=manager)

        message = str(raised.value)
        assert "Could not read the GAIA hub catalog" in message
        assert "GAIA_HUB_URL" in message
        assert "no network and no cache" in message
        assert _nothing_landed(manager)


def test_the_offline_catalog_cache_answers_when_the_hub_is_down(
    manager, monkeypatch, tmp_path
):
    """No mocked search: the real catalog loader falls back to its disk cache."""
    from gaia.hub import catalog

    monkeypatch.setattr(
        capture_mod, "published_skill_names", _real_published_skill_names
    )
    cache = tmp_path / "catalog-cache.json"
    cache.write_text(
        json.dumps(
            {
                "generated_at": "2026-10-01T00:00:00+00:00",
                "agents": [{"id": PUBLISHED, "type": "skill"}],
            }
        ),
        encoding="utf-8",
    )

    def _down(url, *_a, **_k):
        raise ConnectionError(f"offline: {url}")

    monkeypatch.setattr(catalog, "fetch_bytes", _down)
    monkeypatch.setattr(catalog, "default_cache_path", lambda: cache)
    monkeypatch.setattr(catalog._MEM, "raw", None)

    with pytest.raises(SkillCaptureError, match="rss-digest is published"):
        capture_skill(_markdown(PUBLISHED), manager=manager)
    assert capture_skill(_markdown("meeting-notes"), manager=manager).name == (
        "meeting-notes"
    )

    cache.unlink()
    with pytest.raises(SkillCaptureError, match="Could not read the GAIA hub catalog"):
        capture_skill(_markdown("other-notes"), manager=manager)


# ---------------------------------------------------------------------------
# Failed verification earlier in the session
# ---------------------------------------------------------------------------


class TestFailedVerification:
    def test_refused_without_consulting_the_catalog(self, manager, monkeypatch):
        def _no_catalog():
            raise AssertionError("the session refusal needs no catalog read")

        monkeypatch.setattr(capture_mod, "published_skill_names", _no_catalog)

        with pytest.raises(SkillCaptureError) as raised:
            capture_skill(
                _markdown("odd-notes"),
                manager=manager,
                failed_verification={"odd-notes"},
            )
        assert str(raised.value) == failed_verification_message("odd-notes")
        assert _nothing_landed(manager)

    def test_case_and_rename_variants(self, manager):
        with pytest.raises(SkillCaptureError, match="failed signature verification"):
            capture_skill(
                _markdown("odd-notes"),
                name="fresh-name",
                manager=manager,
                failed_verification=[" ODD-NOTES "],
            )
        with pytest.raises(SkillCaptureError, match="failed signature verification"):
            capture_skill(
                _markdown("fresh-name"),
                name="odd-notes",
                manager=manager,
                failed_verification=["odd-notes"],
            )

    def test_other_names_are_unaffected(self, manager):
        result = capture_skill(
            _markdown("meeting-notes"),
            manager=manager,
            failed_verification={"odd-notes"},
        )
        assert result.name == "meeting-notes"

    def test_message_is_final_and_plain(self):
        message = failed_verification_message("odd-notes")
        for route in ("fetch", "capture", "download", "rewrite"):
            assert route in message
        assert "report it" in message


# ---------------------------------------------------------------------------
# gaia skill import refuses hub URLs
# ---------------------------------------------------------------------------


class TestImport:
    """``gaia skill import`` is a terminal step: it refuses hub URLs, not names.

    Importing a local folder by hand (``gaia skill import hub/skills/<name>``)
    is a documented flow, so the catalog-name check stays on capture only.
    """

    def test_import_of_a_local_folder_with_a_hub_name_still_works(
        self, manager, catalog, tmp_path
    ):
        folder = tmp_path / "src" / PUBLISHED
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(_markdown(PUBLISHED), encoding="utf-8")

        result = import_bundle(folder, origin=str(folder), manager=manager)
        assert result.name == PUBLISHED
        assert catalog.calls == 0

    @pytest.mark.parametrize(
        "url",
        [
            "https://hub.amd-gaia.ai/skills/rss-digest/1.0.0/rss-digest-1.0.0.zip",
            "https://HUB.amd-gaia.ai:443/skills/rss-digest/1.0.0/rss-digest-1.0.0.zip",
        ],
    )
    def test_cli_refuses_a_hub_url_before_downloading(
        self, manager, monkeypatch, capsys, url
    ):
        import requests

        from gaia.skills import cli as skills_cli

        def _no_fetch(*_a, **_k):
            raise AssertionError("a hub URL must be refused before downloading")

        monkeypatch.setattr(requests, "get", _no_fetch)
        monkeypatch.setattr(skills_cli, "_manager", lambda: manager)
        parser = argparse.ArgumentParser(prog="gaia")
        skills_cli.add_subparser(parser.add_subparsers(dest="action"))

        rc = skills_cli.handle(parser.parse_args(["skill", "import", url]))

        assert rc != skills_cli.EXIT_OK
        assert "gaia skill install rss-digest" in capsys.readouterr().err
        assert _nothing_landed(manager)

    @staticmethod
    def _import_through_redirect(monkeypatch, manager, *, typed, final, name):
        """Run ``gaia skill import <typed>`` with the download landing on *final*."""
        import io

        import requests

        from gaia.skills import cli as skills_cli

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr(f"{name}/SKILL.md", _markdown(name))
        payload = buffer.getvalue()
        state = SimpleNamespace(calls=[], closed=False)

        class _Response:
            url = final

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                return iter([payload])

            def close(self):
                state.closed = True

        def _get(url, **_kwargs):
            state.calls.append(url)
            return _Response()

        monkeypatch.setattr(requests, "get", _get)
        monkeypatch.setattr(skills_cli, "_manager", lambda: manager)
        parser = argparse.ArgumentParser(prog="gaia")
        skills_cli.add_subparser(parser.add_subparsers(dest="action"))
        rc = skills_cli.handle(parser.parse_args(["skill", "import", typed]))
        return rc, state

    def test_cli_refuses_a_redirect_onto_the_hub(self, manager, monkeypatch, capsys):
        from gaia.skills import cli as skills_cli

        rc, state = self._import_through_redirect(
            monkeypatch,
            manager,
            typed="https://short.example/r/abc",
            final="https://hub.amd-gaia.ai/skills/rss-digest/1.0.0/rss-digest-1.0.0.zip",
            name=PUBLISHED,
        )

        assert state.calls == ["https://short.example/r/abc"]
        assert rc != skills_cli.EXIT_OK
        assert "gaia skill install rss-digest" in capsys.readouterr().err
        assert state.closed
        assert _nothing_landed(manager)

    def test_cli_imports_through_a_redirect_elsewhere(self, manager, monkeypatch):
        from gaia.skills import cli as skills_cli

        rc, state = self._import_through_redirect(
            monkeypatch,
            manager,
            typed="https://short.example/r/abc",
            final="https://cdn.example/bundles/meeting-notes.zip",
            name="meeting-notes",
        )

        assert rc == skills_cli.EXIT_OK
        assert not state.closed
        assert (manager.user_root / "meeting-notes" / "SKILL.md").is_file()


def test_the_unit_suite_stubs_the_catalog_by_default():
    """Guards the conftest stub: without it every capture test would need a hub."""
    assert capture_mod.published_skill_names() == frozenset()
