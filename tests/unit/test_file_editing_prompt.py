# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The edit tools have to be mentioned in the prompt, or the agent shells out.

Measured on 30 corpus moments whose correct next action was an edit, with the
target file already held: the shipped prompt named the shell seven times with
worked recipes and ``edit_file`` not once. The agent shelled out or re-read
instead of editing on 23 of them. Adding the fragment took working edits from
1 to 6 and shell fallbacks from 19 to 12.

These tests pin the fragment's existence and its discovery, not its wording —
the wording should stay tunable.
"""

import pytest

from gaia.agents.tools.file_io_tools import FileIOToolsMixin


class _Bare(FileIOToolsMixin):
    """The mixin with registered edit tools, without an agent or LLM."""

    _tools_registry = {"edit_file": {}, "edit_python_file": {}}


class TestTheFragmentExists:
    @pytest.mark.parametrize("profile", ["chat", "doc", "file", "full", "data", "web"])
    def test_only_profiles_with_both_edit_tools_advertise_them(self, profile):
        from tests.unit.test_profilespec_characterization import (
            chat_agent_build_context,
        )

        with chat_agent_build_context(profile) as agent:
            agent._register_tools()
            available = {"edit_file", "edit_python_file"}.issubset(
                agent._tools_registry
            )
            assert bool(agent.get_file_editing_system_prompt()) == available

    def test_it_names_the_edit_tools(self):
        text = _Bare().get_file_editing_system_prompt()
        assert "edit_file" in text
        assert "edit_python_file" in text

    def test_it_steers_away_from_rewriting_files_through_the_shell(self):
        text = _Bare().get_file_editing_system_prompt().lower()
        # The failure mode this exists to prevent: sed/awk/heredoc rewrites.
        assert "sed" in text and "awk" in text
        assert "heredoc" in text

    def test_it_says_not_to_re_read_held_content(self):
        text = _Bare().get_file_editing_system_prompt().lower()
        assert "already hold" in text

    def test_it_stays_small(self):
        """It rides on every call; the prompt is already ~15K chars."""
        assert len(_Bare().get_file_editing_system_prompt()) < 800


class TestAutoDiscovery:
    def test_the_name_matches_the_pattern_the_agent_scans_for(self):
        """``_get_mixin_prompts`` collects ``get_*_system_prompt`` off the instance.

        A rename that breaks the pattern silently drops the fragment, and the
        only symptom is the agent quietly going back to shelling out.
        """
        name = "get_file_editing_system_prompt"
        assert name.startswith("get_") and name.endswith("_system_prompt")
        assert callable(getattr(_Bare(), name))

    def test_it_is_reachable_on_an_agent_that_composes_the_mixin(self):
        from gaia.agents.tools.file_io_tools import FileIOToolsMixin as M

        assert hasattr(M, "get_file_editing_system_prompt")
