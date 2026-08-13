# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Runtime skill-library tools on the flagship agent.

Two things these tests refuse to take on faith.

**Activation is asserted on the composed system prompt, never on a flag.**
``load_skill`` returning ``{"status": "success"}`` proves a dict was built; only
the skill's body appearing in ``agent.system_prompt`` — and disappearing again
on unload — proves the model will actually see it.

**The install path is exercised, not mocked.** ``install_skill`` runs against a
stand-in hub whose objects come out of the *real* publish path (validate → audit
gate → sign → package), with only the HTTP transport swapped. So the checksum,
the signature, the tier collapse, and the permission ceiling all run for real; a
regression that would let an unsigned or over-permissioned skill install fails
here rather than on a user's machine.

**A refusal is only proven by the thing it refuses.** Each security test is
paired with evidence the danger is real: ``test_the_shared_loader_itself_does_
not_gate_that_import`` shows the base loader importing un-vetted code, which is
why the model-facing tool fails closed on it.

Cold state throughout: skills root, trust store, signing keys, lock file, and
catalog cache all live under ``tmp_path``, so no test can see — or corrupt — the
developer's own ``~/.gaia``.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# parents[0]=tests/ [1]=python/ [2]=gaia/ [3]=agents/ [4]=hub/ [5]=repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent")

from gaia_agent.agent import GaiaAgent, GaiaAgentConfig  # noqa: E402
from gaia_agent.skill_tools import SKILL_LIBRARY_TOOL_NAMES  # noqa: E402

from gaia.skills.manager import SkillManager  # noqa: E402
from gaia.skills.signing import TrustStore  # noqa: E402
from tests.unit.skills_helpers import fake_hub, write_audit_report  # noqa: E402

SKILLS_PROMPT_HEADER = "==== LOADED SKILLS ===="

# Distinctive strings so "is the body in the prompt" is a real assertion and not
# an accidental substring match against the rest of a 40 KB system prompt.
NOTE_TAKER_MARKER = "ZZ-NOTE-TAKER-BODY-MARKER-ZZ"
NEEDS_TOOLS_MARKER = "ZZ-NEEDS-TOOLS-BODY-MARKER-ZZ"
SHELL_RUNNER_MARKER = "ZZ-SHELL-RUNNER-BODY-MARKER-ZZ"
PUBLISHED_MARKER = "ZZ-PUBLISHED-BODY-MARKER-ZZ"
IMPORTED_CODE_MARKER = "ZZ-IMPORTED-CODE-BODY-MARKER-ZZ"
IMPORTED_PROSE_MARKER = "ZZ-IMPORTED-PROSE-BODY-MARKER-ZZ"


def _skill_markdown(
    name: str,
    *,
    description: str,
    marker: str,
    version: str | None = None,
    tier: str | None = None,
    permissions: tuple[str, ...] = (),
    tools_required: tuple[str, ...] = (),
    declared_tools: tuple[str, ...] = (),
) -> str:
    """Author one SKILL.md as text."""
    lines = ["---", f"name: {name}", f"description: {description}"]
    if version:
        lines.append(f"version: {version}")
    lines.append("license: MIT")
    metadata = []
    if tier:
        metadata.append(f"    security_tier: {tier}")
    if permissions:
        metadata.append("    permissions:")
        metadata.extend(f"      - {p}" for p in permissions)
    if tools_required:
        metadata.append("    tools_required:")
        metadata.extend(f"      - {t}" for t in tools_required)
    if declared_tools:
        metadata.append("    tools:")
        for name_ in declared_tools:
            metadata.append(f"      - name: {name_}")
            metadata.append(f"        description: Tool {name_} provided by {name}.")
    if metadata:
        lines += ["metadata:", "  gaia:", *metadata]
    lines += ["---", "", f"# {name}", "", marker, ""]
    return "\n".join(lines)


def _write_skill(root: Path, name: str, text: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(text, encoding="utf-8")
    return directory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    """A cold skill library: bundled skills, an empty user root, one import."""
    root = tmp_path_factory.mktemp("gaia-skill-library")
    bundled = root / "bundled"
    user = root / "user-skills"
    claude = root / "claude-skills"
    bundled.mkdir()
    user.mkdir()
    claude.mkdir()

    _write_skill(
        bundled,
        "note-taker",
        _skill_markdown(
            "note-taker",
            description="Take structured notes from a conversation.",
            marker=NOTE_TAKER_MARKER,
            version="1.0.0",
        ),
    )
    _write_skill(
        bundled,
        "needs-tools",
        _skill_markdown(
            "needs-tools",
            description="A recipe that leans on a tool this agent lacks.",
            marker=NEEDS_TOOLS_MARKER,
            tools_required=("launch_the_missiles",),
        ),
    )
    _write_skill(
        bundled,
        "shell-runner",
        _skill_markdown(
            "shell-runner",
            description="Wants to run shell commands, which v1 cannot sandbox.",
            marker=SHELL_RUNNER_MARKER,
            tier="verified",
            permissions=("shell:execute",),
        ),
    )
    # A read-only .claude/skills import that ships executable code. Nothing in
    # GAIA signed, tiered, or audited it — see the security tests below.
    imported = _write_skill(
        claude,
        "imported-code",
        _skill_markdown(
            "imported-code",
            description="An imported marketplace skill that ships a tools.py.",
            marker=IMPORTED_CODE_MARKER,
            declared_tools=("imported_probe",),
        ),
    )
    (imported / "tools.py").write_text(
        "from gaia.agents.base.tools import tool\n"
        "\n"
        "\n"
        "@tool\n"
        "def imported_probe() -> str:\n"
        '    """Probe registered by an imported skill."""\n'
        '    return "ran"\n',
        encoding="utf-8",
    )
    _write_skill(
        claude,
        "imported-prose",
        _skill_markdown(
            "imported-prose",
            description="An imported marketplace skill that is instructions only.",
            marker=IMPORTED_PROSE_MARKER,
        ),
    )
    return SimpleNamespace(root=root, bundled=bundled, user=user, claude=claude)


@pytest.fixture(scope="module")
def session(library):
    """One :class:`GaiaAgent` pointed at the cold library, plus its clean prompt.

    Module-scoped because constructing the flagship agent is expensive; every
    test restores the loaded set afterwards (see ``_unload_everything``), so the
    prompt returns to ``baseline_prompt`` between tests.
    """
    agent = GaiaAgent(config=GaiaAgentConfig(silent_mode=True))
    agent._skill_manager = SkillManager(
        agent_skill_dirs=[str(library.bundled)],
        user_skills_root=library.user,
        claude_skill_dirs=[library.claude],
    )
    agent.rebuild_system_prompt()

    from gaia_agent.skill_tools import estimate_prompt_tokens

    return SimpleNamespace(
        agent=agent,
        baseline_prompt=agent.system_prompt,
        # The always-on set (gaia-voice, per gaia-agent.yaml) loads during
        # construction, so "clean" is this set — not an empty one.
        baseline_skills=frozenset(agent.loaded_skills),
        baseline_skill_tokens=estimate_prompt_tokens(agent.get_skills_system_prompt()),
        library=library,
    )


@pytest.fixture(autouse=True)
def _unload_everything(session):
    """Leave the session exactly as clean as it was found.

    Restores the BASELINE loaded set rather than an empty one: unloading the
    always-on skill too would leave every later test running against a prompt
    the real agent never has, and would silently invalidate baseline_prompt.
    """
    yield
    for name in list(session.agent.loaded_skills):
        if name not in session.baseline_skills:
            session.agent.unload_skill(name)


def call(session, tool_name: str, **kwargs):
    """Invoke a registered tool the way the agent loop would."""
    return session.agent._tools_registry[tool_name]["function"](**kwargs)


@pytest.fixture
def hub(session, tmp_path, monkeypatch):
    """A stand-in Agent Hub wired in where the network would be.

    Only the transport is replaced: ``publish`` runs the real sign-and-package
    path, and ``install_skill`` runs the real download-verify-gate path against
    the bytes that publish produced.
    """
    from gaia.hub import catalog

    store = fake_hub(tmp_path)
    monkeypatch.setenv("GAIA_HUB_URL", store.BASE_URL)
    # gaia.skills.hub binds fetch_bytes at import time, so both names are patched.
    monkeypatch.setattr("gaia.skills.hub.fetch_bytes", store.fetcher)
    monkeypatch.setattr(catalog, "fetch_bytes", store.fetcher)
    monkeypatch.setattr(
        catalog, "default_cache_path", lambda: tmp_path / "catalog-cache.json"
    )
    # The catalog keeps a 5-minute in-process cache; a stale entry would serve a
    # previous test's index.
    catalog._MEM.raw = None

    keys_root = session.library.user
    audit = write_audit_report(tmp_path)

    def publish(source: Path, *, publisher: str = "acme", **kwargs):
        from gaia.skills.publish import publish_skill

        return publish_skill(
            source,
            token="test-token",
            hub_url=store.BASE_URL,
            uploader=store.accept_publish,
            keys_root=keys_root,
            audit_report=audit,
            publisher=publisher,
            **kwargs,
        )

    def trust(key, *, role: str = "publisher", publisher: str = "acme"):
        trust_store = TrustStore.load(keys_root)
        trust_store.add(
            public_key_b64=base64.b64encode(key.public_bytes).decode("ascii"),
            publisher=publisher,
            role=role,
        )
        trust_store.save()

    def keygen(name: str = "publisher"):
        from gaia.skills.signing import generate_key

        # force: the keys root is module-scoped, so a later test would otherwise
        # trip the "a signing key already exists" guard.
        return generate_key(keys_root, name=name, force=True)

    return SimpleNamespace(
        store=store, publish=publish, trust=trust, keygen=keygen, tmp_path=tmp_path
    )


def _publishable(tmp_path: Path, name: str, **kwargs) -> Path:
    """Author a skill source directory ready for ``publish``."""
    source = tmp_path / "src" / name
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(_skill_markdown(name, **kwargs), encoding="utf-8")
    return source


# ---------------------------------------------------------------------------
# Registration — the tools exist, and they change nothing until asked
# ---------------------------------------------------------------------------


def test_every_declared_tool_is_registered(session):
    missing = [
        n for n in SKILL_LIBRARY_TOOL_NAMES if n not in session.agent._tools_registry
    ]
    assert not missing, (
        f"SKILL_LIBRARY_TOOL_NAMES claims {missing} but they are absent from the "
        "agent's registry — the manifest's tools_count is derived from that tuple, "
        "so a mismatch makes the hub page over-claim."
    )


def test_the_disk_mutating_tools_are_gated_behind_confirmation(session):
    """Writing third-party code to disk asks the human, like any file mutation.

    The gate lives in ``Agent._execute_tool``, which these tests bypass by
    calling the functions directly — so the declaration is asserted here.
    """
    gated = type(session.agent).confirmation_required_tools()
    assert {"install_skill", "remove_skill"} <= gated
    # Read-only tools stay ungated: asking to approve a list is noise.
    assert not ({"list_skills", "skill_status", "search_skill_hub"} & gated)


def test_no_task_skill_loads_by_default(session):
    """Registering the library tools must not pull in a task skill.

    Skill *bodies* are opt-in (#2848): if a recipe nobody asked for is in the
    prompt, the agent is paying for it on every turn.

    The one deliberate exception is gaia-voice, declared always-on in
    gaia-agent.yaml. It is not a recipe — it is the honesty floor ("do not claim
    work you did not do", "do not substitute a near-miss and report success"),
    and those failures corrupt an answer whichever task is running, so it cannot
    be opt-in the way a task skill can. test_only_the_voice_skill_is_always_on
    below pins it to exactly that one skill.
    """
    loaded = set(session.agent.loaded_skills)

    assert loaded <= {"gaia-voice"}, f"a task skill loaded itself: {loaded}"
    assert NOTE_TAKER_MARKER not in session.baseline_prompt
    assert call(session, "skill_status")["loaded_count"] == len(loaded)


def test_only_the_voice_skill_is_always_on(session):
    """Guard the always-on list against quietly growing.

    Every entry here is charged to every prompt of every turn, so this is the
    one skill list that has to be argued for rather than added to.
    """
    assert set(session.agent.loaded_skills) == {"gaia-voice"}


# ---------------------------------------------------------------------------
# Load / unload — asserted on the prompt the model actually receives
# ---------------------------------------------------------------------------


def test_load_injects_the_skill_body_into_the_system_prompt(session):
    result = call(session, "load_skill", name="note-taker")

    assert result["status"] == "success", result
    assert result["already_loaded"] is False
    prompt = session.agent.system_prompt
    assert SKILLS_PROMPT_HEADER in prompt
    assert NOTE_TAKER_MARKER in prompt, (
        "load_skill reported success but the body never reached the composed "
        "system prompt — the model would not see the skill."
    )
    assert result["prompt_tokens_estimate"] > 0


def test_unload_removes_the_body_from_the_system_prompt(session):
    call(session, "load_skill", name="note-taker")
    assert NOTE_TAKER_MARKER in session.agent.system_prompt

    result = call(session, "unload_skill", name="note-taker")

    assert result["status"] == "success", result
    prompt = session.agent.system_prompt
    assert NOTE_TAKER_MARKER not in prompt, (
        "unload_skill reported success but the body is still in the prompt — the "
        "skill was un-flagged, not unloaded."
    )
    # Back to baseline exactly — the strong claim, and the one that catches a
    # partial unload. The skills header and a non-zero estimate legitimately
    # remain: gaia-voice is always-on, so baseline_prompt already contains it.
    assert prompt == session.baseline_prompt
    assert SKILLS_PROMPT_HEADER in prompt, "the always-on voice skill vanished too"
    assert result["prompt_tokens_estimate"] == session.baseline_skill_tokens


def test_loading_twice_is_idempotent(session):
    call(session, "load_skill", name="note-taker")
    second = call(session, "load_skill", name="note-taker")

    assert second["already_loaded"] is True
    assert session.agent.system_prompt.count(NOTE_TAKER_MARKER) == 1


def test_unload_names_what_is_loaded_when_the_skill_is_not(session):
    call(session, "load_skill", name="note-taker")

    result = call(session, "unload_skill", name="never-loaded")

    assert result["status"] == "error"
    assert "never-loaded" in result["error"]
    assert "note-taker" in result["error"], "the error must name what IS loaded"


def test_load_unknown_skill_fails_loudly(session):
    result = call(session, "load_skill", name="no-such-skill")

    assert result["status"] == "error"
    assert result["error_type"] == "SkillNotFoundError"
    assert "no-such-skill" in result["error"]
    # What to do next, and where the search happened.
    assert "gaia skill" in result["error"]
    assert str(session.library.bundled) in result["error"]
    # gaia-voice is always-on, so the claim is that no TASK skill loaded.
    assert set(session.agent.loaded_skills) == session.baseline_skills


# ---------------------------------------------------------------------------
# Security — the refusals must survive being reachable from a tool call
# ---------------------------------------------------------------------------


def test_load_refuses_a_skill_that_asks_for_shell_execute(session):
    """A local-capability permission has no sandbox in v1, so loading refuses.

    The skill even claims ``security_tier: verified`` — a stamp must not buy
    reach that no enforcement layer exists for.
    """
    result = call(session, "load_skill", name="shell-runner")

    assert result["status"] == "error"
    assert result["error_type"] == "SkillPermissionError"
    assert "shell" in result["error"]
    # gaia-voice is always-on, so the claim is that no TASK skill loaded.
    assert set(session.agent.loaded_skills) == session.baseline_skills
    assert SHELL_RUNNER_MARKER not in session.agent.system_prompt


@pytest.mark.parametrize(
    "hostile",
    ["../victim", "..", "note-taker/../../victim", "/etc/passwd", "C:\\Windows", ""],
)
@pytest.mark.parametrize("tool_name", ["remove_skill", "install_skill", "load_skill"])
def test_disk_tools_refuse_a_name_that_is_a_path(session, tool_name, hostile):
    """A model-supplied name must never reach a filesystem join.

    See ``test_the_substrate_deletes_outside_the_skills_root`` for why: the
    delete underneath has no validation of its own.
    """
    result = call(session, tool_name, name=hostile)

    assert result["status"] == "error"
    assert "not a skill name" in result["error"]


def test_the_substrate_deletes_outside_the_skills_root(tmp_path):
    """Documents the flaw the name check compensates for.

    ``gaia.skills.install.remove_skill`` builds ``root / name`` and ``rmtree``s
    it, so a traversing name deletes a directory outside the skills root and
    reports success. Reachable from a chat turn the moment a tool passes the
    model's string through — hence the guard above. If this test starts failing
    because the substrate grew its own validation, drop ``_reject_bad_name``.
    """
    from gaia.skills.install import remove_skill as _hub_remove

    root = tmp_path / "skills"
    root.mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "notes.txt").write_text("payroll", encoding="utf-8")
    manager = SkillManager(user_skills_root=root, include_claude_roots=False)

    _hub_remove("../victim", manager=manager)

    assert not victim.exists()


def test_load_refuses_code_from_an_ungated_claude_import(session):
    """A model-issued load must not import Python nothing in GAIA vetted.

    ``.claude/skills`` is read-only and never passes through
    ``gaia skill install``, so no signature, tier, or audit applies to it — yet
    the shared loader has no tier gate (see the companion test below). The tool
    fails closed; the human can still opt in with ``gaia skill import``.
    """
    result = call(session, "load_skill", name="imported-code")

    assert result["status"] == "error"
    assert result["error_type"] == "SkillPermissionError"
    assert "imported_probe" in result["error"]
    assert "gaia skill import" in result["error"]
    # gaia-voice is always-on, so the claim is that no TASK skill loaded.
    assert set(session.agent.loaded_skills) == session.baseline_skills
    assert IMPORTED_CODE_MARKER not in session.agent.system_prompt
    assert "imported-code/imported_probe" not in session.agent._tools_registry


def test_the_shared_loader_itself_does_not_gate_that_import(session):
    """Documents the gap the tool above compensates for.

    ``Agent.load_skill`` refuses un-bridged permissions but never consults the
    security tier, so it loads ``experimental`` code from a read-only import
    root without complaint. If this test starts failing because the base loader
    grew a tier gate, delete ``_refuse_ungated_code`` — the duplicate refusal
    would then be the bug.
    """
    skill = session.agent.load_skill("imported-code")
    try:
        assert skill.security_tier == "experimental"
        assert skill.root == "claude-import"
        assert "imported-code/imported_probe" in session.agent._tools_registry
    finally:
        session.agent.unload_skill("imported-code")


def test_the_gate_sees_a_tools_block_added_after_discovery_cached(session, library):
    """The gate must read the SKILL.md on disk, not the cached frontmatter.

    ``list_skills`` → ``load_skill`` is the documented sequence, so a skill that
    grows a ``tools:`` block between the two would otherwise be waved through on
    a stale cache and then have its code imported.
    """
    call(session, "list_skills")  # fills the discovery cache: no tools declared
    target = library.claude / "imported-prose" / "SKILL.md"
    original = target.read_text(encoding="utf-8")
    target.write_text(
        _skill_markdown(
            "imported-prose",
            description="An imported marketplace skill that is instructions only.",
            marker=IMPORTED_PROSE_MARKER,
            declared_tools=("late_probe",),
        ),
        encoding="utf-8",
    )
    try:
        result = call(session, "load_skill", name="imported-prose")

        assert result["status"] == "error"
        assert "late_probe" in result["error"]
        # gaia-voice is always-on, so the claim is that no TASK skill loaded.
        assert set(session.agent.loaded_skills) == session.baseline_skills
    finally:
        target.write_text(original, encoding="utf-8")
        session.agent.skill_manager.reload()


def test_instruction_only_imports_still_load(session):
    """The refusal is scoped to executable code, not to imported prose."""
    result = call(session, "load_skill", name="imported-prose")

    assert result["status"] == "success", result
    assert IMPORTED_PROSE_MARKER in session.agent.system_prompt


def test_install_refuses_an_unsigned_skill(session, hub, tmp_path):
    """An unattested skill installs only by explicit human opt-in at a terminal."""
    source = _publishable(
        tmp_path,
        "loose-cannon",
        description="Unsigned skill published straight to experimental.",
        marker=PUBLISHED_MARKER,
        version="1.0.0",
        tier="experimental",
    )
    hub.publish(source, unsigned=True)

    result = call(session, "install_skill", name="loose-cannon")

    assert result["status"] == "error"
    assert result["error_type"] == "SkillInstallError"
    assert "--allow-experimental" in result["error"]
    assert not (session.library.user / "loose-cannon").exists()


def test_install_refuses_a_dangerous_grant_without_a_human(session, hub, tmp_path):
    """``network:write`` at ``community`` needs consent the model cannot give.

    The confirmer this tool passes always says no, so the interactive prompt in
    ``gaia.skills.install`` can never be answered by the chat stream.
    """
    key = hub.keygen()
    hub.trust(key)
    source = _publishable(
        tmp_path,
        "egress-happy",
        description="Signed skill that wants unrestricted outbound egress.",
        marker=PUBLISHED_MARKER,
        version="1.0.0",
        tier="community",
        permissions=("network:write:*.example.com",),
    )
    hub.publish(source)

    result = call(session, "install_skill", name="egress-happy")

    assert result["status"] == "error"
    assert result["error_type"] == "SkillInstallError"
    assert "network:write" in result["error"]
    assert not (session.library.user / "egress-happy").exists()


# ---------------------------------------------------------------------------
# Install → load → remove, end to end against the real trust path
# ---------------------------------------------------------------------------


def test_install_then_load_a_signed_community_skill(session, hub, tmp_path):
    key = hub.keygen()
    hub.trust(key)
    source = _publishable(
        tmp_path,
        "trusted-brief",
        description="Signed community skill that reads one API and summarizes.",
        marker=PUBLISHED_MARKER,
        version="1.2.0",
        tier="community",
        permissions=("network:read:*.example.com",),
    )
    hub.publish(source)

    installed = call(session, "install_skill", name="trusted-brief")

    assert installed["status"] == "success", installed
    assert installed["version"] == "1.2.0"
    assert installed["security_tier"] == "community"
    assert (session.library.user / "trusted-brief" / "SKILL.md").is_file()
    # Installing is not activating.
    assert PUBLISHED_MARKER not in session.agent.system_prompt

    loaded = call(session, "load_skill", name="trusted-brief")

    assert loaded["status"] == "success", loaded
    assert PUBLISHED_MARKER in session.agent.system_prompt

    removed = call(session, "remove_skill", name="trusted-brief")

    assert removed["status"] == "success", removed
    assert removed["unloaded_from_session"] is True
    assert not (session.library.user / "trusted-brief").exists()
    assert PUBLISHED_MARKER not in session.agent.system_prompt


def test_install_honours_a_version_pin(session, hub, tmp_path):
    key = hub.keygen()
    hub.trust(key)
    for version in ("1.0.0", "2.0.0"):
        source = _publishable(
            tmp_path / version,
            "pinned-skill",
            description="Signed community skill published at two versions.",
            marker=PUBLISHED_MARKER,
            version=version,
            tier="community",
        )
        hub.publish(source)

    result = call(session, "install_skill", name="pinned-skill", version="1.0.0")

    assert result["status"] == "success", result
    assert result["version"] == "1.0.0"
    call(session, "remove_skill", name="pinned-skill")


def test_install_reports_an_unsatisfiable_pin(session, hub, tmp_path):
    key = hub.keygen()
    hub.trust(key)
    source = _publishable(
        tmp_path,
        "narrow-skill",
        description="Signed community skill published at a single version.",
        marker=PUBLISHED_MARKER,
        version="1.0.0",
        tier="community",
    )
    hub.publish(source)

    result = call(session, "install_skill", name="narrow-skill", version="^9.0")

    assert result["status"] == "error"
    assert result["error_type"] == "SkillNotFoundError"
    assert "1.0.0" in result["error"], "the error must list what IS published"


def test_remove_refuses_an_agent_bundled_skill(session):
    result = call(session, "remove_skill", name="note-taker")

    assert result["status"] == "error"
    assert "agent-bundled" in result["error"]
    assert (session.library.bundled / "note-taker" / "SKILL.md").is_file()


# ---------------------------------------------------------------------------
# tools_required is advisory — so the gap has to be reported, loudly
# ---------------------------------------------------------------------------


def test_load_surfaces_a_tools_required_gap(session):
    """The loader loads anyway; the model must be told before it follows the recipe."""
    result = call(session, "load_skill", name="needs-tools")

    assert result["status"] == "success"
    assert result["unmet_tools_required"] == ["launch_the_missiles"]
    assert "launch_the_missiles" in result["warning"]
    assert NEEDS_TOOLS_MARKER in session.agent.system_prompt


# ---------------------------------------------------------------------------
# Discovery and status
# ---------------------------------------------------------------------------


def test_list_skills_reports_origin_tier_and_loaded_state(session):
    call(session, "load_skill", name="note-taker")

    result = call(session, "list_skills")

    assert result["status"] == "success"
    by_name = {entry["name"]: entry for entry in result["skills"]}
    assert {"note-taker", "needs-tools", "shell-runner"} <= set(by_name)
    assert by_name["note-taker"]["root"] == "agent-bundled"
    assert by_name["note-taker"]["loaded"] is True
    assert by_name["needs-tools"]["loaded"] is False
    assert by_name["shell-runner"]["permissions"] == ["shell:execute"]
    assert str(session.library.bundled) in result["roots_searched"]


def test_list_skills_surfaces_a_folder_that_failed_to_parse(session, library):
    """A malformed skill is visibly broken, never silently missing."""
    broken = library.bundled / "broken-skill"
    broken.mkdir()
    (broken / "SKILL.md").write_text("no frontmatter here", encoding="utf-8")
    try:
        result = call(session, "list_skills")

        assert "invalid" in result
        assert any("broken-skill" in path for path in result["invalid"])
        assert "warning" in result
    finally:
        import shutil

        shutil.rmtree(broken)
        session.agent.skill_manager.reload()


def test_skill_status_prices_the_loaded_set(session):
    """The price must track the loaded set, measured against the always-on floor.

    gaia-voice loads at construction, so "before" is that baseline rather than
    zero. Asserting the delta is the stronger claim anyway: it catches a status
    that reports a constant, which a fixed-zero check never would.
    """
    before = call(session, "skill_status")
    assert before["loaded_count"] == len(session.baseline_skills)
    assert before["total_prompt_tokens_estimate"] == session.baseline_skill_tokens
    assert "note-taker" in before["installed_not_loaded"]

    call(session, "load_skill", name="note-taker")
    after = call(session, "skill_status")

    assert after["loaded_count"] == len(session.baseline_skills) + 1
    assert "note-taker" in {entry["name"] for entry in after["loaded"]}
    assert (
        after["total_prompt_tokens_estimate"] > before["total_prompt_tokens_estimate"]
    )
    assert "note-taker" not in after["installed_not_loaded"]


# ---------------------------------------------------------------------------
# Hub search
# ---------------------------------------------------------------------------


def test_search_skill_hub_lists_published_skills(session, hub, tmp_path):
    key = hub.keygen()
    hub.trust(key)
    source = _publishable(
        tmp_path,
        "searchable-skill",
        description="Signed community skill that should show up in search.",
        marker=PUBLISHED_MARKER,
        version="1.0.0",
        tier="community",
    )
    hub.publish(source)

    result = call(session, "search_skill_hub", query="searchable")

    assert result["status"] == "success", result
    assert [entry["name"] for entry in result["results"]] == ["searchable-skill"]
    assert result["results"][0]["installed"] is False
    assert result["results"][0]["version"] == "1.0.0"
    assert result["results"][0]["security_tier"] == "community"
    assert "warning" not in result, "a live catalog must not be flagged as stale"


def test_search_skill_hub_fails_loudly_when_the_hub_is_unreachable(
    session, tmp_path, monkeypatch
):
    """No network and no cache returns an error, never an empty result list."""
    from gaia.hub import catalog

    def _offline(url):
        raise ConnectionError(f"no route to {url}")

    monkeypatch.setattr(catalog, "fetch_bytes", _offline)
    monkeypatch.setattr(
        catalog, "default_cache_path", lambda: tmp_path / "empty-cache.json"
    )
    catalog._MEM.raw = None

    result = call(session, "search_skill_hub", query="anything")

    assert result["status"] == "error"
    assert result["error_type"] == "SkillHubError"
    assert "GAIA_HUB_URL" in result["error"]
