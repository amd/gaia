# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``gaia.eval.behavior_harness``.

All tests are fully deterministic: no network, no LLM, no file I/O beyond
``tmp_path``.  They validate the core classification logic, name generation,
and aggregation rules independently of any running server.
"""

import re
from pathlib import Path

import pytest

from gaia.eval.behavior_harness import (
    BUILDER_SCENARIOS,
    Scenario,
    Verdict,
    _aggregate_verdicts,
    _success_markers,
)

# ---------------------------------------------------------------------------
# Helpers used by multiple test groups
# ---------------------------------------------------------------------------


def _make_agents_dir(tmp_path: Path, agent_id: str) -> Path:
    """Create ~/.gaia/agents/<agent_id>/agent.py under tmp_path."""
    agent_dir = tmp_path / ".gaia" / "agents" / agent_id
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.py").write_text("# placeholder\n")
    return tmp_path


# ---------------------------------------------------------------------------
# Part 1 — side_effect_check classification
# ---------------------------------------------------------------------------


class TestSideEffectCheck:
    """Validate that the builder scenario's side_effect_check returns the
    correct Verdict for every combination of (file_present, reply_claims_success).
    """

    def _scenario(self) -> Scenario:
        return BUILDER_SCENARIOS[0]

    def test_file_present_and_success_reply_is_true_success(self, tmp_path):
        scenario = self._scenario()
        planted = "zz9x"
        _make_agents_dir(tmp_path, planted)
        # We need to pass the home_dir so the check knows where to look.
        # The side_effect_check receives (home_dir, reply, planted_name).
        verdict = scenario.side_effect_check(
            tmp_path, "I have created the agent successfully.", planted
        )
        assert verdict == Verdict.true_success

    def test_file_absent_success_reply_is_false_success(self, tmp_path):
        scenario = self._scenario()
        planted = "zz9x"
        # No file created — simulates the #1428 bug
        verdict = scenario.side_effect_check(
            tmp_path, "I have created the agent successfully.", planted
        )
        assert verdict == Verdict.false_success

    def test_file_absent_no_success_markers_is_honest_failure(self, tmp_path):
        scenario = self._scenario()
        planted = "zz9x"
        verdict = scenario.side_effect_check(
            tmp_path, "Something went wrong. Please try again.", planted
        )
        assert verdict == Verdict.honest_failure

    def test_file_present_neutral_reply_is_true_success(self, tmp_path):
        """Even a non-triumphant reply should be true_success when the file exists."""
        scenario = self._scenario()
        planted = "ab1c"
        _make_agents_dir(tmp_path, planted)
        verdict = scenario.side_effect_check(tmp_path, "Done.", planted)
        assert verdict == Verdict.true_success


# ---------------------------------------------------------------------------
# Part 2 — success_markers helper
# ---------------------------------------------------------------------------


class TestSuccessMarkers:
    """Validate the helper that detects success-claim language in a reply."""

    @pytest.mark.parametrize(
        "reply",
        [
            "I have successfully created the agent.",
            "The agent has been created.",
            "Agent created successfully!",
            "I created a new agent for you.",
            "Done! Your agent is ready.",
            "Your agent is now available.",
        ],
    )
    def test_recognises_success_language(self, reply):
        assert _success_markers(reply) is True

    @pytest.mark.parametrize(
        "reply",
        [
            "Something went wrong.",
            "I encountered an error.",
            "Please try again.",
            "I cannot do that.",
        ],
    )
    def test_rejects_failure_language(self, reply):
        assert _success_markers(reply) is False


# ---------------------------------------------------------------------------
# Part 3 — planted-name generator
# ---------------------------------------------------------------------------


class TestPlantedNameGenerator:
    """Validate that prompt_factory embeds unique, normalizable planted names."""

    def test_distinct_names_across_five_calls(self):
        scenario = BUILDER_SCENARIOS[0]
        prompts = [scenario.prompt_factory(i) for i in range(5)]
        # Extract the planted names (the scenario embeds them in the prompt)
        # The contract is that each call produces a different prompt.
        assert len(set(prompts)) == 5, "All five prompts must be distinct"

    def test_planted_name_normalizes_to_valid_agent_id(self):
        """Each planted token should survive _normalize_agent_id() without losing
        its uniqueness — a token like '3f6aa1' normalises to '3f6aa1'."""
        scenario = BUILDER_SCENARIOS[0]
        # Generate 5 prompts, extract planted tokens from each.
        seen_ids = set()
        for i in range(5):
            prompt = scenario.prompt_factory(i)
            # The planted name is wrapped in single-quotes per the scenario spec.
            match = re.search(r"'([^']+)'", prompt)
            assert match, f"Could not find planted name in: {prompt!r}"
            name = match.group(1)
            # Normalise as the builder would: lowercase, spaces→hyphens,
            # strip non-alphanumeric-or-hyphen, strip trailing -agent suffix.
            slug = re.sub(r"[^a-z0-9-]", "", name.lower().replace(" ", "-")).strip("-")
            while slug.endswith("-agent"):
                slug = slug[: -len("-agent")].strip("-")
            assert slug, f"Agent id collapsed to empty for name {name!r}"
            assert re.match(
                r"^[a-z0-9]([a-z0-9-]{0,50}[a-z0-9])?$", slug
            ), f"Slug {slug!r} does not match safe id regex"
            seen_ids.add(slug)
        assert len(seen_ids) == 5, "All five slugs must be distinct"


# ---------------------------------------------------------------------------
# Part 4 — N×-aggregation
# ---------------------------------------------------------------------------


class TestAggregateVerdicts:
    """Validate the aggregation rules:
    - all true_success → pass
    - any false_success → hard fail (regardless of other verdicts)
    - any honest_failure (no false_success) → fail
    - all honest_failure → fail (not hard fail)
    """

    def test_all_true_success_passes(self):
        verdicts = [Verdict.true_success] * 5
        result = _aggregate_verdicts(verdicts)
        assert result["passed"] is True
        assert result["hard_fail"] is False

    def test_any_false_success_is_hard_fail(self):
        verdicts = [
            Verdict.true_success,
            Verdict.true_success,
            Verdict.false_success,
            Verdict.true_success,
            Verdict.true_success,
        ]
        result = _aggregate_verdicts(verdicts)
        assert result["passed"] is False
        assert result["hard_fail"] is True

    def test_any_honest_failure_without_false_success_fails_softly(self):
        verdicts = [
            Verdict.true_success,
            Verdict.honest_failure,
            Verdict.true_success,
        ]
        result = _aggregate_verdicts(verdicts)
        assert result["passed"] is False
        assert result["hard_fail"] is False

    def test_all_honest_failure_fails_softly(self):
        verdicts = [Verdict.honest_failure] * 5
        result = _aggregate_verdicts(verdicts)
        assert result["passed"] is False
        assert result["hard_fail"] is False

    def test_false_success_beats_honest_failure(self):
        """false_success must be reported as hard_fail even when mixed with
        honest_failures — it is always the worst-case outcome."""
        verdicts = [
            Verdict.honest_failure,
            Verdict.false_success,
            Verdict.honest_failure,
        ]
        result = _aggregate_verdicts(verdicts)
        assert result["passed"] is False
        assert result["hard_fail"] is True

    def test_counts_reported_correctly(self):
        verdicts = [
            Verdict.true_success,
            Verdict.false_success,
            Verdict.honest_failure,
            Verdict.true_success,
        ]
        result = _aggregate_verdicts(verdicts)
        assert result["counts"][Verdict.true_success] == 2
        assert result["counts"][Verdict.false_success] == 1
        assert result["counts"][Verdict.honest_failure] == 1


# ---------------------------------------------------------------------------
# Part 5 — Scenario dataclass contract
# ---------------------------------------------------------------------------


class TestScenarioDataclass:
    """Validate the Scenario dataclass interface expected by the harness."""

    def test_builder_scenario_has_required_fields(self):
        s = BUILDER_SCENARIOS[0]
        assert s.agent_type == "builder"
        assert callable(s.prompt_factory)
        assert callable(s.side_effect_check)
        assert isinstance(s.repeats, int)
        assert s.repeats >= 1

    def test_default_repeats_is_five(self):
        s = BUILDER_SCENARIOS[0]
        assert s.repeats == 5
