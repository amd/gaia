# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Bundled skills + account-keyed skill-set selection (#2466).

Cold-state discipline: every agent here is built with ``tmp_path``-scoped
databases and a ``SkillManager`` whose user and Claude-import roots point at
empty tmp directories. Nothing reads the developer's ``~/.gaia/skills`` or the
repo's ``.claude/skills``, so a skill only resolves if this package really ships
it — which is the whole claim under test.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from gaia.connectors.providers.microsoft import (
    ACCOUNT_TYPE_PERSONAL,
    ACCOUNT_TYPE_WORK,
)
from gaia.skills import SkillManager
from gaia.skills.errors import SkillSetError

from gaia_agent_email.agent import (
    ACCOUNT_TYPE_SKILL_SETS,
    EmailTriageAgent,
    _locate_agent_manifest,
)
from gaia_agent_email.config import (
    ACCOUNT_TYPE_ENV,
    SKILL_SET_ENV,
    ConfigurationError,
    EmailAgentConfig,
)

_PKG_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST = _PKG_ROOT / "gaia-agent.yaml"
_SKILLS_DIR = _PKG_ROOT / "gaia_agent_email" / "skills"


class _MinimalMailBackend:
    def list_messages(self, *args, **kwargs):
        return []

    def get_message(self, *args, **kwargs):
        return {}


class _MinimalCalendarBackend:
    def list_events(self, *args, **kwargs):
        return []


def _isolated_manager(tmp_path: Path) -> SkillManager:
    """A manager whose only populated root is the package's bundled skills."""
    return SkillManager(
        agent_skill_dirs=EmailTriageAgent.SKILL_DIRS,
        user_skills_root=tmp_path / "cold-user-root" / "skills",
        claude_skill_dirs=[tmp_path / "cold-claude-root" / "skills"],
    )


def _build_agent(tmp_path, monkeypatch, **config_kwargs) -> EmailTriageAgent:
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    monkeypatch.delenv(SKILL_SET_ENV, raising=False)
    monkeypatch.delenv(ACCOUNT_TYPE_ENV, raising=False)
    config = EmailAgentConfig(
        model_id="test-model",
        gmail_backend=_MinimalMailBackend(),
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        start_scheduler=False,
        **config_kwargs,
    )
    manager = _isolated_manager(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
        mock_sdk.return_value = MagicMock()
        with patch.object(
            EmailTriageAgent, "skill_manager", property(lambda self: manager)
        ):
            return EmailTriageAgent(config=config)


# ----------------------------------------------------------------------
# The bundled skills ship, and the manifest matches them
# ----------------------------------------------------------------------


def test_every_declared_skill_is_actually_bundled():
    """A set naming a skill this package does not ship would fail at launch."""
    declared = yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))
    names = {
        name for entries in declared["skill_sets"].values() for name in entries
    } | set(declared.get("skills") or [])

    for name in sorted(names):
        assert (
            _SKILLS_DIR / name / "SKILL.md"
        ).is_file(), f"skill_sets names {name!r} but gaia_agent_email/skills/{name}/SKILL.md is missing"


def test_no_bundled_skill_is_orphaned():
    """A shipped skill no set references is dead weight in the package."""
    declared = yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))
    referenced = {
        name for entries in declared["skill_sets"].values() for name in entries
    } | set(declared.get("skills") or [])
    on_disk = {p.parent.name for p in _SKILLS_DIR.glob("*/SKILL.md")}
    assert on_disk == referenced


def test_bundled_skills_are_instruction_only(tmp_path):
    """v1 ships no new tool or permission surface (#2466 non-goals).

    A skill that declared tools would change the agent's tool count, and one
    that declared a permission would change its connector requirements — both
    belong to later phases.
    """
    manager = _isolated_manager(tmp_path)
    discovered = manager.list_skills()
    assert manager.discovery_errors == {}
    assert len(discovered) == 6

    for summary in discovered:
        skill = manager.load(summary.name)
        assert skill.gaia.tools == [], f"{skill.name} declares tools"
        assert skill.gaia.permissions == [], f"{skill.name} declares permissions"
        assert skill.body.strip(), f"{skill.name} has an empty body"
        assert "Use when" in skill.description, (
            f"{skill.name}'s description carries no trigger phrase, so the model "
            "cannot judge when it applies"
        )


def test_declared_tools_required_all_exist_in_the_agents_registry(tmp_path, monkeypatch):
    """A skill whose recipe names a tool this agent lacks cannot be executed."""
    agent = _build_agent(tmp_path, monkeypatch)
    manager = _isolated_manager(tmp_path)
    registry = agent._tools_registry

    for summary in manager.list_skills():
        skill = manager.load(summary.name)
        missing = [t for t in skill.gaia.tools_required if t not in registry]
        assert not missing, f"{skill.name} requires unregistered tool(s): {missing}"


def test_the_manifest_is_locatable_from_a_source_checkout():
    assert _locate_agent_manifest() == _MANIFEST
    assert Path(EmailTriageAgent.SKILL_MANIFEST).is_file()


def test_the_manifest_is_locatable_from_an_installed_wheel_layout(monkeypatch):
    """In a wheel there is no directory above the package to read it from.

    ``package-data`` globs cannot reach outside their own package, so the build
    stages a copy INSIDE ``gaia_agent_email/``. If only that copy exists, the
    resolver must still find it — otherwise every ``pip install`` produces an
    agent that cannot be constructed at all.
    """
    import tempfile

    from gaia_agent_email import agent as agent_module

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        in_package = root / "gaia_agent_email" / "gaia-agent.yaml"
        in_package.parent.mkdir(parents=True)
        in_package.write_text(_MANIFEST.read_text(encoding="utf-8"), encoding="utf-8")
        # Only the packaged copy exists — the checkout location does not.
        assert not (root / "gaia-agent.yaml").exists()

        monkeypatch.setattr(
            agent_module,
            "_MANIFEST_CANDIDATES",
            (in_package, root / "gaia-agent.yaml"),
        )
        assert agent_module._locate_agent_manifest() == in_package


def test_the_build_stages_the_manifest_into_the_package(tmp_path):
    """The build_py hook is what makes the wheel layout above exist."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "email_build_hooks", _PKG_ROOT / "_build_hooks.py"
    )
    hooks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hooks)

    class _FakeBuildPy(hooks.build_py):
        def __init__(self, build_lib):
            self.build_lib = str(build_lib)

        def announce(self, msg, level=1):
            pass

    _FakeBuildPy(tmp_path)._stage_agent_manifest()

    staged = tmp_path / "gaia_agent_email" / "gaia-agent.yaml"
    assert staged.is_file()
    assert staged.read_text(encoding="utf-8") == _MANIFEST.read_text(encoding="utf-8")


def test_pyproject_wires_the_build_hook_and_the_skill_package_data():
    """Both halves are required; either one missing breaks a wheel install."""
    text = (_PKG_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'build_py = "_build_hooks.build_py"' in text
    assert "skills/*/SKILL.md" in text


def test_the_frozen_binary_bundles_the_skills_and_the_manifest():
    """The freeze must stage both as data — the import analyzer cannot see them.

    Without this the frozen sidecar starts with an empty bundled root and no
    declared sets, which no unit test running from a checkout would notice.
    """
    # Loaded by path: ``packaging`` resolves to the installed distribution.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "email_freeze", _PKG_ROOT / "packaging" / "freeze.py"
    )
    freeze = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(freeze)

    staged = {(src.name, dest) for src, dest in freeze.ADD_DATA}
    assert ("skills", "gaia_agent_email/skills") in staged
    assert ("gaia-agent.yaml", "gaia_agent_email") in staged
    for source, _dest in freeze.ADD_DATA:
        assert source.exists(), f"freeze stages {source}, which does not exist"


# ----------------------------------------------------------------------
# Selection
# ----------------------------------------------------------------------


def _declared_sets():
    """The manifest's parsed declarations, read the way the framework reads them."""
    from gaia.hub.manifest import parse as parse_manifest

    return parse_manifest(EmailTriageAgent.SKILL_MANIFEST).skill_sets


def test_manifest_declarations():
    sets = _declared_sets()
    assert sets.set_names == ["personal", "work"]
    assert sets.default_set == "personal"
    personal = [ref.name for ref in sets.skills_for("personal")]
    work = [ref.name for ref in sets.skills_for("work")]
    assert personal == ["inbox-triage", "newsletter-digest", "travel-itinerary"]
    assert work == [
        "inbox-triage",
        "meeting-scheduling",
        "action-item-extraction",
        "escalation-routing",
    ]
    # The overlap is the point: sets are not a partition.
    assert set(personal) & set(work) == {"inbox-triage"}


def test_account_type_map_only_names_declared_sets():
    sets = _declared_sets()
    assert set(ACCOUNT_TYPE_SKILL_SETS) == {ACCOUNT_TYPE_PERSONAL, ACCOUNT_TYPE_WORK}
    for account_type, set_name in ACCOUNT_TYPE_SKILL_SETS.items():
        assert set_name in sets.set_names, (
            f"account type {account_type!r} maps to skill set {set_name!r}, "
            "which gaia-agent.yaml does not declare"
        )


@pytest.mark.parametrize(
    "account_type, expected_set, expected_skills",
    [
        (
            ACCOUNT_TYPE_PERSONAL,
            "personal",
            {"inbox-triage", "newsletter-digest", "travel-itinerary"},
        ),
        (
            ACCOUNT_TYPE_WORK,
            "work",
            {
                "inbox-triage",
                "meeting-scheduling",
                "action-item-extraction",
                "escalation-routing",
            },
        ),
    ],
)
def test_account_type_selects_its_set_and_nothing_else(
    tmp_path, monkeypatch, account_type, expected_set, expected_skills
):
    agent = _build_agent(tmp_path, monkeypatch, account_type=account_type)

    assert agent.select_skill_set() == expected_set
    assert agent.active_skill_set == expected_set
    assert set(agent.loaded_skills) == expected_skills


def test_the_other_sets_skills_are_absent_from_the_prompt(tmp_path, monkeypatch):
    agent = _build_agent(tmp_path, monkeypatch, account_type=ACCOUNT_TYPE_WORK)
    prompt = agent.system_prompt

    assert "meeting-scheduling" in prompt
    assert "newsletter-digest" not in prompt
    assert "travel-itinerary" not in prompt


def test_explicit_skill_set_overrides_the_account_type(tmp_path, monkeypatch):
    agent = _build_agent(
        tmp_path, monkeypatch, account_type=ACCOUNT_TYPE_WORK, skill_set="personal"
    )
    assert agent.active_skill_set == "personal"
    assert "meeting-scheduling" not in agent.loaded_skills


def test_unknown_skill_set_fails_loudly_listing_the_valid_ones(tmp_path, monkeypatch):
    with pytest.raises(SkillSetError) as excinfo:
        _build_agent(tmp_path, monkeypatch, skill_set="buisness")
    assert "Valid sets: personal, work" in str(excinfo.value)


def test_a_gmail_only_mailbox_resolves_the_default_set(tmp_path, monkeypatch):
    """Gmail carries no Microsoft tenant, so the kind is genuinely unknown.

    The default set must then apply *explicitly* — a work mailbox must never be
    silently treated as personal because the signal was missing.
    """
    monkeypatch.setattr(
        "gaia_agent_email.config.get_connection", lambda provider: None
    )
    agent = _build_agent(tmp_path, monkeypatch)

    assert agent.config.resolve_account_type() is None
    assert agent.select_skill_set() is None
    assert agent.active_skill_set == "personal"  # the manifest's default_skill_set


def test_a_derived_account_type_drives_the_set(tmp_path, monkeypatch):
    """With nothing pinned, the kind recorded on the connection decides."""
    monkeypatch.setattr(
        "gaia_agent_email.config.get_connection",
        lambda provider: (
            {"account_type": ACCOUNT_TYPE_WORK} if provider == "microsoft" else None
        ),
    )
    agent = _build_agent(tmp_path, monkeypatch)

    assert agent.config.resolve_account_type() == ACCOUNT_TYPE_WORK
    assert agent.active_skill_set == "work"


def test_an_explicit_account_type_beats_the_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gaia_agent_email.config.get_connection",
        lambda provider: {"account_type": ACCOUNT_TYPE_WORK},
    )
    config = EmailAgentConfig(account_type=ACCOUNT_TYPE_PERSONAL)
    assert config.resolve_account_type() == ACCOUNT_TYPE_PERSONAL


def test_an_unreadable_connection_store_reports_unknown(monkeypatch):
    """No keyring backend ⇒ unknown kind, not a crash and not a guess."""
    from gaia.connectors.errors import ConnectorsError

    def boom(provider):
        raise ConnectorsError("no keyring backend available")

    monkeypatch.setattr("gaia_agent_email.config.get_connection", boom)
    assert EmailAgentConfig().resolve_account_type() is None


def test_switching_sets_at_runtime_replaces_the_previous_one(tmp_path, monkeypatch):
    agent = _build_agent(tmp_path, monkeypatch, account_type=ACCOUNT_TYPE_PERSONAL)
    assert agent.active_skill_set == "personal"

    agent.load_skill_set("work")

    assert agent.active_skill_set == "work"
    assert "newsletter-digest" not in agent.loaded_skills
    assert "meeting-scheduling" in agent.loaded_skills


# ----------------------------------------------------------------------
# Context budget: skills cost prompt tokens the tool result must give back
# ----------------------------------------------------------------------


def test_loaded_skills_shrink_the_triage_envelope_budget(tmp_path, monkeypatch):
    """A skill body is prompt text the post-tool turn re-reads.

    Regression guard: at the pinned 16,384-token envelope, adding the personal
    set's skill bodies to the system prompt without taking them back out of the
    tool-result budget pushed a limit-12 triage request to 16,602 tokens and the
    run 400'd with ``context_length_exceeded``. Measured on hardware, not
    theorised.
    """
    from gaia_agent_email.context_budget import (
        envelope_budget_tokens,
        skill_prompt_tokens,
    )

    agent = _build_agent(tmp_path, monkeypatch, account_type=ACCOUNT_TYPE_WORK)
    cost = skill_prompt_tokens(agent)

    assert cost > 0, "the work set loads four skill bodies; they cannot be free"
    assert envelope_budget_tokens(extra_fixed_tokens=cost) == (
        envelope_budget_tokens() - cost
    )


def test_the_whole_post_tool_turn_fits_the_window_with_skills_loaded(
    tmp_path, monkeypatch
):
    """The behavioural claim, not the arithmetic identity.

    Reproduces the shape of the real 400: system prompt (with the work set's
    bodies) + a condensed triage envelope + the response reserve must all fit
    ``CONTEXT_TARGET_TOKENS``. Sizing the envelope against a fixed cost that
    predates skills is what pushed a limit-12 request to 16,602 tokens against a
    16,384 window.

    Sized at ``DEFAULT_INBOX_SCAN_CEILING`` — the per-call ceiling a real
    interactive triage can reach. (Well beyond it the envelope's ``grouped``
    verdict map alone exceeds the budget and the condenser cannot trim it; that
    floor is independent of skills — it is identical with zero skill cost — so it
    is not this change's to fix.)
    """
    from gaia_agent_email.config import DEFAULT_INBOX_SCAN_CEILING
    from gaia_agent_email.context_budget import (
        CONTEXT_TARGET_TOKENS,
        _RESPONSE_RESERVE_TOKENS,
        envelope_budget_tokens,
        estimate_tokens,
        estimate_tokens_json,
        skill_prompt_tokens,
    )
    from gaia_agent_email.tools.triage_condense import condense_triage_result

    agent = _build_agent(tmp_path, monkeypatch, account_type=ACCOUNT_TYPE_WORK)
    cost = skill_prompt_tokens(agent)

    # A triage result well over the envelope budget, so the condenser must act.
    n = DEFAULT_INBOX_SCAN_CEILING
    result = {
        "results": [
            {
                "id": f"{i:032x}",
                "subject": f"Subject line number {i} with some realistic length",
                "from": f"sender{i}@example.invalid",
                "category": "FYI",
                "rationale": "A rationale sentence of the kind the classifier emits.",
            }
            for i in range(n)
        ],
        "grouped": {f"{i:032x}": "FYI" for i in range(n)},
        "total": n,
    }

    condensed = condense_triage_result(result, extra_fixed_tokens=cost)
    envelope_tokens = estimate_tokens_json(json.dumps(condensed, default=str))

    assert envelope_tokens <= envelope_budget_tokens(extra_fixed_tokens=cost)

    # The prompt the model re-reads on the post-tool turn, end to end.
    prompt_tokens = estimate_tokens(agent.system_prompt)
    total = prompt_tokens + envelope_tokens + _RESPONSE_RESERVE_TOKENS
    assert total <= CONTEXT_TARGET_TOKENS, (
        f"post-tool turn would be ~{total} tokens against a "
        f"{CONTEXT_TARGET_TOKENS} window (prompt {prompt_tokens} + envelope "
        f"{envelope_tokens} + reserve {_RESPONSE_RESERVE_TOKENS})"
    )


def test_the_skill_cost_estimate_is_pessimistic(tmp_path, monkeypatch):
    """A budget SUBTRACTION must never under-count.

    ``estimate_tokens``' chars//4 prose ratio under-counts real Markdown by
    roughly 2x (the module's own measured figure is ~2.1 chars/token). Crediting
    the envelope only half of what the skills actually cost is how the turn
    overflows anyway.
    """
    from gaia_agent_email.context_budget import estimate_tokens, skill_prompt_tokens

    agent = _build_agent(tmp_path, monkeypatch, account_type=ACCOUNT_TYPE_WORK)
    fragment = agent.get_skills_system_prompt()

    assert skill_prompt_tokens(agent) >= estimate_tokens(fragment)
    assert skill_prompt_tokens(agent) >= int(len(fragment) / 2.1)


def test_the_bundled_skill_bodies_stay_within_their_prompt_budget(tmp_path):
    """Cap the per-set prompt cost so a future edit can't quietly refill the ctx.

    The envelope the triage tool has left is ``6144 - <set cost>``; a set costing
    more than ~1,500 tokens starves the bulk-triage result envelope on the
    16K-ctx hardware this agent targets.
    """
    from gaia.hub.manifest import parse as parse_manifest
    from gaia_agent_email.context_budget import estimate_tokens

    manager = _isolated_manager(tmp_path)
    bodies = {s.name: manager.load(s.name).body for s in manager.list_skills()}
    sets = parse_manifest(EmailTriageAgent.SKILL_MANIFEST).skill_sets

    for name in sets.set_names:
        cost = sum(
            estimate_tokens(bodies[ref.name]) for ref in sets.skills_for(name)
        )
        assert cost <= 1500, (
            f"skill set {name!r} costs ~{cost} prompt tokens, over the 1500 cap "
            "— trim the SKILL.md bodies or shrink the set"
        )


# ----------------------------------------------------------------------
# Config + env plumbing
# ----------------------------------------------------------------------


def test_skill_set_env_var_populates_the_config(monkeypatch):
    monkeypatch.setenv(SKILL_SET_ENV, "work")
    assert EmailAgentConfig().skill_set == "work"
    monkeypatch.setenv(SKILL_SET_ENV, "   ")
    assert EmailAgentConfig().skill_set is None


def test_account_type_env_var_populates_the_config(monkeypatch):
    monkeypatch.setenv(ACCOUNT_TYPE_ENV, "WORK")
    assert EmailAgentConfig().account_type == ACCOUNT_TYPE_WORK


def test_a_malformed_account_type_env_var_fails_loudly(monkeypatch):
    monkeypatch.setenv(ACCOUNT_TYPE_ENV, "corporate")
    with pytest.raises(ConfigurationError, match="not a valid account type"):
        EmailAgentConfig()


def test_a_malformed_account_type_field_fails_validation():
    with pytest.raises(ConfigurationError, match="account_type must be one of"):
        EmailAgentConfig(account_type="corporate").validate()


# ----------------------------------------------------------------------
# The --skill-set flag
# ----------------------------------------------------------------------


def test_skill_set_flag_exports_the_env_var(monkeypatch):
    """The flag has to survive into the per-request agent sessions.

    The app is built at import time and sessions are constructed per request, so
    an argparse value that stayed in ``main()``'s locals would be silently
    ignored.
    """
    import os

    from gaia_agent_email import server

    monkeypatch.delenv(SKILL_SET_ENV, raising=False)

    with patch("uvicorn.run") as run:
        assert server.main(["serve", "--skill-set", "work", "--port", "8199"]) == 0
    assert run.called
    assert os.environ[SKILL_SET_ENV] == "work"
    assert EmailAgentConfig().skill_set == "work"


def test_skill_set_flag_rejects_an_undeclared_name(capsys):
    from gaia_agent_email import server

    with pytest.raises(SystemExit):
        server.main(["serve", "--skill-set", "buisness"])
    assert "Valid sets: personal, work" in capsys.readouterr().err


def test_skill_set_flag_help_lists_the_declared_sets():
    from gaia_agent_email.server import _declared_skill_sets

    assert _declared_skill_sets() == ["personal", "work"]


def test_an_invalid_env_var_is_rejected_at_startup_like_the_flag(monkeypatch, capsys):
    """The docs call the env var equivalent to the flag — so it must validate.

    Left unchecked, ``GAIA_EMAIL_SKILL_SET=buisness`` started a healthy-looking
    sidecar whose every session then raised on construction.
    """
    from gaia_agent_email import server

    monkeypatch.setenv(SKILL_SET_ENV, "buisness")
    with pytest.raises(SystemExit):
        server.main(["serve"])
    assert "Valid sets: personal, work" in capsys.readouterr().err


def test_a_valid_env_var_is_accepted_at_startup(monkeypatch):
    from gaia_agent_email import server

    monkeypatch.setenv(SKILL_SET_ENV, "work")
    with patch("uvicorn.run") as run:
        assert server.main(["serve", "--port", "8198"]) == 0
    assert run.called


def test_an_unreadable_manifest_cannot_wave_a_requested_set_through(monkeypatch, capsys):
    """Validation must fail loudly, not degrade to "accept anything".

    The help-text helper swallows read errors by design; using it to validate
    would mean a build with an unreadable manifest accepts any name.
    """
    from gaia_agent_email import server

    def boom():
        raise RuntimeError("manifest unreadable")

    monkeypatch.setattr(server, "_read_declared_skill_sets", boom)
    with pytest.raises(SystemExit):
        server.main(["serve", "--skill-set", "work"])
    assert "could not be read" in capsys.readouterr().err
