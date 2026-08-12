# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Guards for the shipped starter skill pack (issue #893).

The pack is the product's first impression and doubles as documentation, so the
bar is higher than "the YAML parses": every skill must validate against the
Phase 1 contract, declare only permissions this phase can honor, and name only
tools that actually exist in the registry. A skill that parses but cannot run is
worse than a missing one.

Every test that can be parametrized iterates the hub's skills lane
(``hub/skills/``) rather than a hardcoded list, so a newly added skill is covered
automatically. The lane is currently the starter pack exactly, so these guards
apply to all of it — including
:func:`test_starter_skill_declares_starter_pack_provenance`. A skill added to the
lane from some other source needs that assertion narrowed first; it will fail
loudly rather than skip.

All roots are ``tmp_path``-scoped and every CLI subprocess runs with ``HOME``
and ``GAIA_CONFIG_DIR`` redirected, so nothing here reads or writes the
developer's real ``~/.gaia/skills`` or ``~/.claude/skills``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from gaia.skills import parse_skill_file, validate_skill
from gaia.skills.format import parse_skill
from gaia.skills.manager import SkillManager
from gaia.skills.permissions import (
    connector_requirements,
    refuse_unbridged_permissions,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
STARTER_ROOT = REPO_ROOT / "hub" / "skills"

#: Marks every skill in the pack, so consumers can group them (issue #893).
PROVENANCE_SOURCE = "starter-pack"


def _starter_dirs() -> list[Path]:
    """Every skill directory in the pack, sorted."""
    return sorted(d for d in STARTER_ROOT.iterdir() if (d / "SKILL.md").is_file())


def _ids(paths: list[Path]) -> list[str]:
    return [p.name for p in paths]


STARTER_DIRS = _starter_dirs()


def test_the_starter_pack_is_not_empty():
    """A globbed suite silently passes when the glob finds nothing."""
    assert STARTER_DIRS, f"No starter skills found under {STARTER_ROOT}"


@pytest.mark.parametrize("skill_dir", STARTER_DIRS, ids=_ids(STARTER_DIRS))
def test_starter_skill_parses_and_validates(skill_dir: Path):
    """The core guard: every shipped skill satisfies the Phase 1 contract.

    ``parse_skill_file`` also enforces ``name`` == directory name.
    """
    skill = parse_skill_file(skill_dir)
    validate_skill(skill, source=str(skill_dir))

    assert skill.name == skill_dir.name
    assert skill.description.strip()
    assert skill.body.strip(), "a starter skill with no procedure teaches nothing"


@pytest.mark.parametrize("skill_dir", STARTER_DIRS, ids=_ids(STARTER_DIRS))
def test_starter_skill_round_trips_byte_identical(skill_dir: Path):
    """Rewriting a skill must not lose the fields GAIA does not model."""
    skill = parse_skill_file(skill_dir)
    assert parse_skill(skill.to_markdown()) == skill


@pytest.mark.parametrize("skill_dir", STARTER_DIRS, ids=_ids(STARTER_DIRS))
def test_starter_skill_declares_starter_pack_provenance(skill_dir: Path):
    """Every skill is attributable to the pack (issue #893)."""
    skill = parse_skill_file(skill_dir)
    provenance = skill.gaia.extra.get("provenance")

    assert provenance is not None, f"{skill.name} is missing metadata.gaia.provenance"
    assert provenance.get("source") == PROVENANCE_SOURCE


@pytest.mark.parametrize("skill_dir", STARTER_DIRS, ids=_ids(STARTER_DIRS))
def test_starter_skill_is_publishable(skill_dir: Path):
    """Versioned and MIT-licensed — the repo's publishing floor."""
    skill = parse_skill_file(skill_dir)

    assert skill.version and skill.version != "0.0.0"
    assert skill.license == "MIT"


@pytest.mark.parametrize("skill_dir", STARTER_DIRS, ids=_ids(STARTER_DIRS))
def test_starter_skill_declares_no_refused_permission(skill_dir: Path):
    """v1 honors connector-bridged domains only.

    A skill declaring ``filesystem``/``shell``/``database``/``desktop``/``env``
    is refused at load, so shipping one would ship a skill that cannot run.
    """
    skill = parse_skill_file(skill_dir)
    refuse_unbridged_permissions(skill.parsed_permissions(), skill_name=skill.name)


@pytest.mark.parametrize("skill_dir", STARTER_DIRS, ids=_ids(STARTER_DIRS))
def test_starter_skill_permissions_resolve_against_the_real_catalog(skill_dir: Path):
    """``mcp:connect:<id>`` must name a connector that actually exists.

    Resolution runs against the live catalog (no injected ids), so a starter
    skill can never point at a connector the user cannot configure.
    """
    skill = parse_skill_file(skill_dir)
    requirements = connector_requirements(
        skill.parsed_permissions(), skill_name=skill.name
    )

    for permission in skill.parsed_permissions():
        if permission.domain == "mcp" and not permission.grants_nothing:
            assert any(
                r.connector_id == permission.scope for r in requirements
            ), f"{skill.name}: {permission} did not resolve to a connector requirement"


# ----------------------------------------------------------------------
# The honesty guard: tools_required must name tools that really exist
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def registry_tool_names() -> frozenset[str]:
    """Tool names the mixins a starter skill may target actually register.

    Registrars are invoked on bare stubs — they only close over ``self`` inside
    the tool bodies — so this is the real registration path, not a copy of a
    name list that could drift.
    """
    from gaia.agents.base.memory import MemoryMixin
    from gaia.agents.base.tools import _TOOL_REGISTRY
    from gaia.agents.tools.browser_tools import BrowserToolsMixin
    from gaia.agents.tools.file_io_tools import FileIOToolsMixin
    from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin
    from gaia.agents.tools.rag_tools import RAGToolsMixin
    from gaia.agents.tools.scratchpad_tools import ScratchpadToolsMixin
    from gaia.agents.tools.shell_tools import ShellToolsMixin

    class _Stub:
        """Enough surface for the registrars.

        ``_memory_store`` must be non-None or memory registration is skipped;
        ``_bookmarks`` is read by the filesystem registrar.
        """

        _memory_store = object()
        _bookmarks = None

        def _get_memory_store(self):
            return self._memory_store

    registrars = [
        (BrowserToolsMixin, "register_browser_tools"),
        (RAGToolsMixin, "register_rag_tools"),
        (ScratchpadToolsMixin, "register_scratchpad_tools"),
        (FileIOToolsMixin, "register_file_io_tools"),
        (FileSystemToolsMixin, "register_filesystem_tools"),
        (ShellToolsMixin, "register_shell_tools"),
        (MemoryMixin, "register_memory_tools"),
    ]

    before = dict(_TOOL_REGISTRY)
    try:
        for mixin, method in registrars:
            getattr(mixin, method)(_Stub())
        names = frozenset(_TOOL_REGISTRY)
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(before)
    return names


def test_registry_fixture_actually_registered_something(registry_tool_names):
    """Guards the guard: an empty set would make the check below vacuous."""
    assert {"search_web", "fetch_page", "query_documents", "recall"} <= (
        registry_tool_names
    )


@pytest.mark.parametrize("skill_dir", STARTER_DIRS, ids=_ids(STARTER_DIRS))
def test_starter_skill_tools_required_are_real_tools(
    skill_dir: Path, registry_tool_names
):
    """Every consumed tool name exists.

    ``tools_required`` is unvalidated by the Phase 1 loader, so a typo would
    ship silently and the skill's procedure would reference a tool the model
    never has. This test is what keeps the pack executable.
    """
    skill = parse_skill_file(skill_dir)
    unknown = sorted(set(skill.gaia.tools_required) - registry_tool_names)

    assert not unknown, (
        f"{skill.name} declares tools_required that no mixin registers: "
        f"{', '.join(unknown)}"
    )


@pytest.mark.parametrize("skill_dir", STARTER_DIRS, ids=_ids(STARTER_DIRS))
def test_starter_skill_body_mentions_the_tools_it_declares(skill_dir: Path):
    """A declared tool the procedure never uses is a stale manifest."""
    skill = parse_skill_file(skill_dir)
    unmentioned = [t for t in skill.gaia.tools_required if t not in skill.body]

    assert not unmentioned, (
        f"{skill.name} declares {', '.join(unmentioned)} in tools_required but "
        "never references them in the procedure"
    )


# ----------------------------------------------------------------------
# Semantic guards on the procedure text
#
# Naming a real tool is not enough — a body can call a real tool with an
# argument the tool rejects, which fails only at runtime in front of a user.
# ----------------------------------------------------------------------

_CATEGORY_ARG = re.compile(r"""category\s*=\s*["']([a-z_]+)["']""")
_SQL_FROM = re.compile(r"\bFROM\s+([A-Za-z_<][\w<>_]*)", re.IGNORECASE)


@pytest.mark.parametrize("skill_dir", STARTER_DIRS, ids=_ids(STARTER_DIRS))
def test_starter_skill_uses_only_real_memory_categories(skill_dir: Path):
    """``remember``/``recall`` reject an unknown category.

    ``remember`` returns a validation error and ``recall`` silently matches
    nothing, so an invented category breaks a memory skill in the one place a
    user would never think to look.
    """
    from gaia.agents.base.memory_store import VALID_CATEGORIES

    skill = parse_skill_file(skill_dir)
    used = set(_CATEGORY_ARG.findall(skill.body))
    unknown = sorted(used - set(VALID_CATEGORIES))

    assert not unknown, (
        f"{skill.name} passes category={unknown} to a memory tool, but the "
        f"store only accepts {sorted(VALID_CATEGORIES)}"
    )


@pytest.mark.parametrize("skill_dir", STARTER_DIRS, ids=_ids(STARTER_DIRS))
def test_starter_skill_scratchpad_sql_uses_the_table_prefix(skill_dir: Path):
    """Scratchpad tables are only reachable through their ``scratch_`` prefix.

    A query without it errors, so example SQL that omits the prefix teaches the
    model the one thing guaranteed to fail.
    """
    from gaia.scratchpad.service import ScratchpadService

    skill = parse_skill_file(skill_dir)
    if "query_data" not in skill.gaia.tools_required:
        pytest.skip("not a scratchpad skill")

    prefix = ScratchpadService.TABLE_PREFIX
    unprefixed = [
        table
        for table in _SQL_FROM.findall(skill.body)
        if not table.lower().startswith(prefix)
    ]

    assert not unprefixed, (
        f"{skill.name} shows SQL selecting FROM {unprefixed} without the "
        f"{prefix!r} prefix; those queries cannot resolve"
    )


# ----------------------------------------------------------------------
# Discovery + loading through the shipped runtime
# ----------------------------------------------------------------------


@pytest.fixture
def installed_pack(tmp_path: Path) -> Path:
    """The whole pack copied into a tmp user root (never ``~/.gaia``)."""
    user_root = tmp_path / "gaia-home" / "skills"
    user_root.mkdir(parents=True)
    for skill_dir in STARTER_DIRS:
        shutil.copytree(skill_dir, user_root / skill_dir.name)
    return user_root


@pytest.fixture
def pack_manager(installed_pack: Path, tmp_path: Path) -> SkillManager:
    """A manager over the tmp pack, with Claude roots pointed at an empty dir."""
    return SkillManager(
        user_skills_root=installed_pack,
        claude_skill_dirs=[tmp_path / "claude-skills"],
    )


def test_every_starter_skill_is_discovered(pack_manager: SkillManager):
    discovered = pack_manager.discover()

    assert not pack_manager.discovery_errors
    assert set(discovered) == {d.name for d in STARTER_DIRS}


@pytest.mark.parametrize("skill_dir", STARTER_DIRS, ids=_ids(STARTER_DIRS))
def test_starter_skill_loads_with_its_body(skill_dir: Path, pack_manager: SkillManager):
    """Level 2 disclosure: the manager returns the full procedure."""
    skill = pack_manager.load(skill_dir.name)

    assert skill.root == "user"
    assert skill.body.strip()


def test_starter_skill_loads_into_an_agent(pack_manager: SkillManager):
    """``Agent.load_skill`` scopes an instruction-only skill's body in.

    Binds the real unbound methods so this covers the shipped code path rather
    than a reimplementation of it.
    """
    from gaia.agents.base.agent import Agent

    class _StubAgent:
        REQUIRED_CONNECTORS: list = []
        SKILL_DIRS: list = []
        _instance_tools = None
        _skill_manager = None
        _loaded_skills = None

        skill_manager = Agent.skill_manager
        loaded_skills = Agent.loaded_skills
        _tools_registry = Agent._tools_registry
        _format_tools_for_prompt = Agent._format_tools_for_prompt
        load_skill = Agent.load_skill
        unload_skill = Agent.unload_skill
        get_skills_system_prompt = Agent.get_skills_system_prompt

        def rebuild_system_prompt(self):
            return None

    agent = _StubAgent()
    skill = agent.load_skill("research-report", manager=pack_manager)

    assert skill.name == "research-report"
    assert "Research Report" in agent.get_skills_system_prompt()
    # network:read bridges to a declared connector requirement.
    assert any(r.connector_id == "network" for r in agent.REQUIRED_CONNECTORS)

    assert agent.unload_skill("research-report") is True


def test_rss_digest_registers_its_declared_tool(pack_manager: SkillManager):
    """The pack's one tool-providing skill really registers its @tool.

    Proves the manifest matches ``tools.py`` — the loader refuses on any
    signature drift, so this is a live contract check, not a formality.
    """
    from gaia.agents.base.tools import _TOOL_REGISTRY
    from gaia.skills.loader import register_skill_tools, unregister_skill_tools

    skill = pack_manager.load("rss-digest")
    before = dict(_TOOL_REGISTRY)
    try:
        registered = register_skill_tools(skill)

        assert "rss-digest/fetch_rss" in registered
        entry = registered["rss-digest/fetch_rss"]
        assert set(entry["parameters"]) == {"url", "max_entries"}
        assert entry["parameters"]["url"]["required"] is True
        assert entry["parameters"]["max_entries"]["required"] is False

        # The tool runs and reports bad input instead of inventing a feed.
        result = entry["function"](url="https://example.invalid/feed", max_entries=0)
        assert "error" in result
    finally:
        unregister_skill_tools("rss-digest")
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(before)


def _load_rss_tools():
    """Import the skill's ``tools.py`` as a module, without registering it."""
    import importlib.util

    path = STARTER_ROOT / "rss-digest" / "tools.py"
    spec = importlib.util.spec_from_file_location("starter_rss_digest_tools", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_RSS_2_0 = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Example Feed</title>
<item><title>First</title><link>https://example.com/1</link>
<pubDate>Mon, 01 Jan 2029 00:00:00 GMT</pubDate>
<description>One</description></item>
<item><title>Second</title><link>https://example.com/2</link></item>
</channel></rss>"""

_ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom Feed</title>
<entry><title>Alpha</title><link href="https://example.com/a"/>
<updated>2029-01-01T00:00:00Z</updated><summary>A</summary></entry>
</feed>"""


def test_fetch_rss_parses_rss_2_0():
    """The RSS branch, exercised without network."""
    result = _load_rss_tools().parse_feed(_RSS_2_0, source="x", max_entries=10)

    assert result["feed_title"] == "Example Feed"
    assert result["count"] == 2
    assert result["entries"][0]["title"] == "First"
    assert result["entries"][0]["link"] == "https://example.com/1"


def test_fetch_rss_parses_atom_and_reads_link_from_href():
    """Atom puts the URL in an attribute, not the element text."""
    result = _load_rss_tools().parse_feed(_ATOM, source="x", max_entries=10)

    assert result["feed_title"] == "Atom Feed"
    assert result["entries"][0]["link"] == "https://example.com/a"


def test_fetch_rss_honors_max_entries():
    result = _load_rss_tools().parse_feed(_RSS_2_0, source="x", max_entries=1)

    assert result["count"] == 1


@pytest.mark.parametrize(
    "payload, expected",
    [
        (b"<html><body>not a feed</body></html>", "no RSS <item>"),
        (b"<rss><channel><title>t</title></channel></rss>", "no RSS <item>"),
        (b"not xml at all <<<", "did not parse"),
        (
            b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "b">]><rss><item/></rss>',
            "DTD",
        ),
    ],
    ids=["html", "empty-feed", "malformed", "doctype"],
)
def test_fetch_rss_fails_loudly_instead_of_returning_an_empty_digest(
    payload: bytes, expected: str
):
    """ "Nothing published" and "I could not read this" must never look alike.

    The DTD case also covers the entity-expansion vector: stdlib ElementTree
    expands entities, so a feed carrying a DTD is refused rather than parsed.
    """
    result = _load_rss_tools().parse_feed(payload, source="x", max_entries=10)

    assert "error" in result, f"expected an error, got {result}"
    assert expected in result["error"]
    assert "entries" not in result


def test_fetch_rss_refuses_a_dtd_hidden_behind_prolog_padding():
    """The DTD guard must not be escapable by padding the prolog.

    A windowed scan of the first N bytes is bypassed by a large leading
    comment; the guard scans the whole prolog instead.
    """
    padded = (
        b'<?xml version="1.0"?>'
        + b"<!-- "
        + b"x" * 8000
        + b" -->"
        + b'<!DOCTYPE l [<!ENTITY a "b">]>'
        + b"<rss><item><title>t</title></item></rss>"
    )

    result = _load_rss_tools().parse_feed(padded, source="x", max_entries=10)

    assert "DTD" in result.get("error", "")


@pytest.mark.parametrize(
    "prolog_markup",
    [
        b"<!-- <a> -->",
        b"<!-- multi\nline <rss> -->",
        b"<?proc <a> ?>",
        b"<!-- unterminated <a>",
    ],
    ids=["comment", "multiline-comment", "processing-instruction", "unterminated"],
)
def test_fetch_rss_refuses_a_dtd_behind_prolog_markup_containing_a_tag(prolog_markup):
    """A comment or PI carrying a `<letter` run must not move the prolog boundary.

    Locating the root by searching for the first `<letter` lands inside such
    markup, leaving a following DOCTYPE outside the scanned prolog and back in
    the hands of ElementTree, which expands entities.
    """
    payload = (
        b'<?xml version="1.0"?>'
        + prolog_markup
        + b'<!DOCTYPE rss [<!ENTITY lol "LOL"><!ENTITY lol2 "&lol;&lol;">]>'
        + b"<rss><channel><item><title>&lol2;</title>"
        + b"<link>http://x</link></item></channel></rss>"
    )

    result = _load_rss_tools().parse_feed(payload, source="x", max_entries=10)

    assert "DTD" in result.get("error", "")
    # And nothing was expanded on the way to that refusal.
    assert "entries" not in result


def test_fetch_rss_still_parses_a_feed_with_an_ordinary_prolog_comment():
    """The stricter prolog scan must not start refusing legitimate feeds."""
    payload = (
        b'<?xml version="1.0"?>'
        b"<!-- generated by an ordinary feed builder -->"
        b"<rss><channel><title>Feed</title><item><title>Hello</title>"
        b"<link>http://x</link></item></channel></rss>"
    )

    result = _load_rss_tools().parse_feed(payload, source="x", max_entries=10)

    assert result.get("error") is None
    assert result["feed_title"] == "Feed"
    assert result["count"] == 1


@pytest.mark.parametrize(
    "description",
    [
        b"&lt;!DOCTYPE html&gt; escaped markup",
        b"<![CDATA[<!DOCTYPE html><p>x</p>]]>",
    ],
    ids=["escaped", "cdata"],
)
def test_fetch_rss_accepts_a_feed_whose_content_embeds_a_doctype(description: bytes):
    """Feeds routinely carry HTML in a description — that is content, not a DTD.

    Scanning the whole payload for ``<!DOCTYPE`` would reject these.
    """
    payload = (
        b'<?xml version="1.0"?><rss><channel><title>T</title><item>'
        b"<title>a</title><description>" + description + b"</description>"
        b"</item></channel></rss>"
    )

    result = _load_rss_tools().parse_feed(payload, source="x", max_entries=10)

    assert "error" not in result
    assert result["count"] == 1


def test_the_guide_consumes_lists_match_the_manifests():
    """The guide names each skill's tools; drift makes it quietly wrong."""
    guide = (REPO_ROOT / "docs" / "guides" / "starter-skills.mdx").read_text(
        encoding="utf-8"
    )
    sections = re.split(r"^### ", guide, flags=re.MULTILINE)[1:]

    documented = {}
    for section in sections:
        name = section.splitlines()[0].strip()
        match = re.search(r"- \*\*Consumes\*\* —(.+?)(?=\n- \*\*|\n\n)", section, re.S)
        if match:
            documented[name] = set(re.findall(r"`([a-z_]+)`", match.group(1)))

    assert documented, "no '**Consumes**' bullets found — did the guide change shape?"

    for skill_dir in STARTER_DIRS:
        skill = parse_skill_file(skill_dir)
        if skill.name not in documented:
            continue
        assert documented[skill.name] == set(skill.gaia.tools_required), (
            f"the guide's Consumes list for {skill.name} disagrees with its "
            f"tools_required: guide={sorted(documented[skill.name])} "
            f"manifest={sorted(skill.gaia.tools_required)}"
        )


def test_the_guides_run_a_skill_snippet_still_type_checks():
    """Pins the API that ``docs/guides/starter-skills.mdx`` tells users to call.

    The guide's only runnable snippet is ``ChatAgent(...).load_skill(name)``
    followed by ``process_query``. Renaming any of those would leave the guide
    quietly wrong, which is worse than no guide at all.
    """
    chat_agent = pytest.importorskip("gaia_agent_chat.agent")

    assert hasattr(chat_agent.ChatAgent, "load_skill")
    assert hasattr(chat_agent.ChatAgent, "unload_skill")
    assert hasattr(chat_agent.ChatAgent, "process_query")
    assert not hasattr(chat_agent.ChatAgent, "query"), (
        "ChatAgent grew a 'query' method — the guide documents 'process_query'; "
        "reconcile the two"
    )
    # The guide claims the default profile is the one that registers the web,
    # RAG, scratchpad, and memory tools these skills consume.
    assert chat_agent.ChatAgentConfig().prompt_profile == "full"


# ----------------------------------------------------------------------
# Forkability — the pack's whole reason to exist
# ----------------------------------------------------------------------


def test_forking_a_starter_skill_produces_a_working_variant(tmp_path: Path):
    """Copy the directory, rename it and ``name``, edit — it still validates.

    This is the acceptance criterion from #893: a fork must be a working
    personal variant, not a broken copy.
    """
    fork_root = tmp_path / "skills"
    fork_root.mkdir()
    fork = fork_root / "gpu-restock-watch"
    shutil.copytree(STARTER_ROOT / "source-watch", fork)

    manifest = fork / "SKILL.md"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "name: source-watch", "name: gpu-restock-watch", 1
        ),
        encoding="utf-8",
    )

    forked = parse_skill_file(fork)
    validate_skill(forked, source=str(fork))

    assert forked.name == "gpu-restock-watch"
    # The fork inherits the parent's contract, provenance included.
    assert forked.gaia.extra["provenance"]["source"] == PROVENANCE_SOURCE
    assert forked.gaia.tools_required == ["fetch_page", "recall", "remember"]

    manager = SkillManager(
        user_skills_root=fork_root, claude_skill_dirs=[tmp_path / "empty"]
    )
    assert "gpu-restock-watch" in manager.discover()


def test_a_fork_that_forgets_to_rename_is_rejected(tmp_path: Path):
    """The directory/``name`` mismatch fails loudly, with a fixable message."""
    from gaia.skills.errors import SkillValidationError

    fork = tmp_path / "my-watcher"
    shutil.copytree(STARTER_ROOT / "source-watch", fork)

    with pytest.raises(SkillValidationError, match="my-watcher"):
        parse_skill_file(fork)


# ----------------------------------------------------------------------
# The real CLI, end to end
# ----------------------------------------------------------------------


def _gaia(*args: str, home: Path) -> subprocess.CompletedProcess:
    """Run ``gaia <args>`` with every skill root redirected into ``home``."""
    env = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "GAIA_CONFIG_DIR": str(home / ".gaia"),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "GAIA_MEMORY_DISABLED": "1",
    }
    # Windows loads winsock via SystemRoot, and `import asyncio` needs it — a
    # fully hermetic env kills every subprocess here with WinError 10106.
    for name in ("SystemRoot", "SystemDrive"):
        if name in os.environ:
            env[name] = os.environ[name]
    return subprocess.run(
        [sys.executable, "-m", "gaia.cli", *args],
        capture_output=True,
        text=True,
        cwd=home,
        env=env,
        timeout=180,
        check=False,
    )


@pytest.fixture
def cli_home(tmp_path: Path) -> Path:
    """An empty HOME so the CLI starts from true cold state."""
    home = tmp_path / "home"
    home.mkdir()
    return home


def test_cli_starts_from_cold_state_with_no_skills(cli_home: Path):
    """The user's real first run: nothing installed, and it says so."""
    result = _gaia("skill", "list", home=cli_home)

    assert result.returncode == 0, result.stderr
    assert "No skills found" in result.stdout


def test_cli_import_list_info_round_trip(cli_home: Path):
    """The documented flow from the guide, exercised as a user runs it."""
    source = STARTER_ROOT / "research-report"

    imported = _gaia("skill", "import", str(source), home=cli_home)
    assert imported.returncode == 0, imported.stderr
    assert (cli_home / ".gaia" / "skills" / "research-report" / "SKILL.md").is_file()

    listed = _gaia("skill", "list", "--json", home=cli_home)
    assert listed.returncode == 0, listed.stderr
    payload = json.loads(listed.stdout)
    names = {s["name"] for s in payload["skills"]}
    assert "research-report" in names
    assert not payload["errors"]

    info = _gaia("skill", "info", "research-report", "--json", home=cli_home)
    assert info.returncode == 0, info.stderr
    manifest = json.loads(info.stdout)
    assert manifest["permissions"] == ["network:read"]
    assert manifest["frontmatter"]["metadata"]["gaia"]["provenance"] == {
        "source": PROVENANCE_SOURCE
    }
    # Imported skills re-earn trust regardless of the tier they claim.
    assert manifest["security_tier"] == "experimental"


def test_cli_imports_every_starter_skill(cli_home: Path):
    """The whole pack installs together and lists without a single error."""
    for skill_dir in STARTER_DIRS:
        result = _gaia("skill", "import", str(skill_dir), home=cli_home)
        assert result.returncode == 0, f"{skill_dir.name}: {result.stderr}"

    listed = _gaia("skill", "list", "--json", home=cli_home)
    payload = json.loads(listed.stdout)

    assert listed.returncode == 0, listed.stderr
    assert not payload["errors"]
    assert {s["name"] for s in payload["skills"]} == {d.name for d in STARTER_DIRS}
