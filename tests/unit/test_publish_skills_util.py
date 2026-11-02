# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``util/publish_skills.py`` (release-time skill publish).

The hub is :class:`FakeHub`, filled by the real ``publish_skill`` path, so the
"already published?" comparison runs against bytes a real publish produced. The
``gaia skill publish`` subprocess is an injected runner.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from gaia.skills.publish import publish_skill
from gaia.skills.signing import BUILTIN_TRUSTED_KEYS
from tests.unit.skills_helpers import fake_hub, make_key, write_audit_report

REPO_ROOT = Path(__file__).resolve().parents[2]
UTIL_DIR = REPO_ROOT / "util"
if str(UTIL_DIR) not in sys.path:
    sys.path.insert(0, str(UTIL_DIR))

import publish_skills as ps  # noqa: E402  (path set above)


@pytest.fixture
def hub(tmp_path):
    return fake_hub(tmp_path)


@pytest.fixture
def fetch(hub):
    """FakeHub's fetcher, with a missing object reported as a 404 (None)."""

    def _fetch(url):
        try:
            return hub.fetcher(url)
        except FileNotFoundError:
            return None

    return _fetch


@pytest.fixture
def skills_dir(tmp_path):
    root = tmp_path / "hub-skills"
    root.mkdir()
    for name in ("summarize", "rss-digest"):
        shutil.copytree(REPO_ROOT / "hub" / "skills" / name, root / name)
    return root


def _publisher(tmp_path, hub, key_name):
    keys_root = tmp_path / "keys-root"
    key = make_key(keys_root, name=key_name)
    audit = write_audit_report(tmp_path)

    def _publish(source):
        return publish_skill(
            source,
            token="test-token",
            hub_url=hub.BASE_URL,
            uploader=hub.accept_publish,
            keys_root=keys_root,
            key_name=key_name,
            audit_report=audit,
        )

    return key, _publish


@pytest.fixture
def publish_to_hub(tmp_path, hub, monkeypatch):
    """Publishes signed by a key standing in for the built-in AMD key."""
    key, publish = _publisher(tmp_path, hub, "amd-skills")
    monkeypatch.setitem(
        BUILTIN_TRUSTED_KEYS, key.key_id, {"key_id": key.key_id, "role": "amd"}
    )
    return publish


class Runner:
    def __init__(self, code=0, output="ok"):
        self.code, self.output, self.calls = code, output, []

    def __call__(self, cmd):
        self.calls.append(cmd)
        return self.code, self.output


def _process(directory, hub, fetch, runner, dry_run=False):
    return ps.process_skill(
        directory,
        hub_url=hub.BASE_URL,
        key_name="amd-skills",
        dry_run=dry_run,
        fetch=fetch,
        run=runner,
    )


@pytest.mark.parametrize("dry_run", [False, True])
def test_an_unpublished_skill_is_published_through_the_cli(
    skills_dir, hub, fetch, dry_run
):
    runner = Runner()
    outcome = _process(skills_dir / "summarize", hub, fetch, runner, dry_run)

    assert outcome.status == (ps.STATUS_DRY_RUN if dry_run else ps.STATUS_PUBLISHED)
    (cmd,) = runner.calls
    assert cmd[:4] == ["gaia", "skill", "publish", str(skills_dir / "summarize")]
    assert cmd[cmd.index("--hub-url") + 1] == hub.BASE_URL
    assert cmd[cmd.index("--key-name") + 1] == "amd-skills"
    assert cmd[cmd.index("--publisher") + 1] == "AMD"
    assert ("--dry-run" in cmd) is dry_run


@pytest.mark.parametrize("skill", ["summarize", "rss-digest"])
def test_an_identical_published_version_is_skipped_without_calling_the_cli(
    skills_dir, hub, fetch, publish_to_hub, skill
):
    publish_to_hub(skills_dir / skill)
    # Build noise in the checkout must not read as a content change.
    noise = skills_dir / skill / "__pycache__"
    noise.mkdir(exist_ok=True)
    (noise / "tools.cpython-312.pyc").write_bytes(b"\0")

    runner = Runner()
    outcome = _process(skills_dir / skill, hub, fetch, runner)

    assert outcome.status == ps.STATUS_SKIPPED
    assert runner.calls == []


@pytest.mark.parametrize(
    "change",
    [
        lambda d: (d / "SKILL.md").write_text(
            (d / "SKILL.md").read_text(encoding="utf-8") + "\nOne more step.\n",
            encoding="utf-8",
        ),
        lambda d: (d / "tools.py").write_text("print('new')\n", encoding="utf-8"),
        lambda d: (d / "tools.py").unlink(),
        lambda d: (d / "extra.md").write_text("added\n", encoding="utf-8"),
    ],
    ids=["edited-body", "edited-code", "deleted-file", "added-file"],
)
def test_a_changed_skill_without_a_version_bump_fails(
    skills_dir, hub, fetch, publish_to_hub, change
):
    directory = skills_dir / "rss-digest"
    publish_to_hub(directory)
    change(directory)

    runner = Runner()
    outcome = _process(directory, hub, fetch, runner)

    assert outcome.status == ps.STATUS_FAILED
    assert "Bump 'version:'" in outcome.detail
    assert runner.calls == []


def test_a_new_version_of_a_published_skill_is_published(
    skills_dir, hub, fetch, publish_to_hub
):
    directory = skills_dir / "summarize"
    publish_to_hub(directory)
    skill_md = directory / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    skill_md.write_text(text.replace("version: 1.0.3", "version: 1.0.4"), "utf-8")

    runner = Runner()
    outcome = _process(directory, hub, fetch, runner)

    assert outcome.status == ps.STATUS_PUBLISHED
    assert len(runner.calls) == 1


def test_an_identical_version_signed_by_another_key_stops_the_run(
    tmp_path, skills_dir, hub, fetch
):
    """Skipping it would leave a version every install refuses as experimental."""
    _key, publish = _publisher(tmp_path, hub, "someone-else")
    publish(skills_dir / "summarize")

    runner = Runner()
    with pytest.raises(ps.PublishSkillsError, match="not GAIA's built-in AMD key"):
        _process(skills_dir / "summarize", hub, fetch, runner)
    assert runner.calls == []


def test_one_bad_skill_fails_the_run_but_the_others_still_publish(
    tmp_path, skills_dir, hub, fetch, monkeypatch, capsys
):
    _key, publish = _publisher(tmp_path, hub, "someone-else")
    publish(skills_dir / "summarize")
    monkeypatch.setattr(ps, "require_amd_key", lambda _name: "id")
    runner = Runner()

    rc = ps.main(
        ["--skills-dir", str(skills_dir), "--hub-url", hub.BASE_URL],
        fetch=fetch,
        run=runner,
    )

    assert rc == 1
    assert [cmd[3] for cmd in runner.calls] == [str(skills_dir / "rss-digest")]
    out = capsys.readouterr().out
    assert "rss-digest" in out and "summarize" in out


def test_a_listed_version_whose_artifact_is_gone_stops_the_run(
    skills_dir, hub, fetch, publish_to_hub
):
    directory = skills_dir / "summarize"
    publish_to_hub(directory)
    for url in [u for u in hub.objects if u.endswith(".zip")]:
        del hub.objects[url]

    with pytest.raises(ps.PublishSkillsError, match="missing"):
        _process(directory, hub, fetch, Runner())


def test_a_cli_failure_is_reported_and_fails_the_run(
    skills_dir, hub, fetch, monkeypatch
):
    monkeypatch.setattr(ps, "require_amd_key", lambda _name: "id")
    runner = Runner(code=6, output="audit BLOCK")

    rc = ps.main(
        ["--skills-dir", str(skills_dir), "--hub-url", hub.BASE_URL],
        fetch=fetch,
        run=runner,
    )

    assert rc == 1
    assert len(runner.calls) == 2


def test_a_non_amd_key_is_refused_before_anything_is_fetched(
    tmp_path, skills_dir, monkeypatch, capsys
):
    home = tmp_path / "home"
    make_key(home / ".gaia" / "skills", name="amd-skills")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)

    def no_network(url):
        raise AssertionError(f"fetched {url} before the key check")

    rc = ps.main(["--skills-dir", str(skills_dir)], fetch=no_network, run=Runner())

    assert rc == 1
    assert "not GAIA's built-in AMD skill key" in capsys.readouterr().err


def test_dry_run_does_not_demand_the_amd_key(skills_dir, hub, fetch, monkeypatch):
    def fail(_name):
        raise AssertionError("dry run must not check the signing key")

    monkeypatch.setattr(ps, "require_amd_key", fail)
    runner = Runner()

    rc = ps.main(
        ["--dry-run", "--skills-dir", str(skills_dir), "--hub-url", hub.BASE_URL],
        fetch=fetch,
        run=runner,
    )

    assert rc == 0
    assert all("--dry-run" in cmd for cmd in runner.calls)


def test_only_with_an_unknown_name_fails(skills_dir, hub, fetch, monkeypatch):
    monkeypatch.setattr(ps, "require_amd_key", lambda _name: "id")
    rc = ps.main(
        ["--skills-dir", str(skills_dir), "--hub-url", hub.BASE_URL, "--only", "nope"],
        fetch=fetch,
        run=Runner(),
    )
    assert rc == 1


def test_every_repo_skill_is_discovered():
    found = {p.name for p in ps.discover(ps.DEFAULT_SKILLS_DIR, [])}
    expected = {
        p.parent.name for p in (REPO_ROOT / "hub" / "skills").glob("*/SKILL.md")
    }
    assert found == expected and len(found) >= 17
