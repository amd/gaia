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

from gaia.agents.tools.file_io_tools import FileIOToolsMixin


class _Bare(FileIOToolsMixin):
    """The mixin alone — no agent, no registry, no LLM."""


class TestTheFragmentExists:
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


class TestTheToolDescribesWhatItIsActuallyFor:
    """``edit_file``'s description named ~2% of what agents really edit.

    It read "use this tool for non-Python files like .tsx, .ts, .js, .json" —
    9 of 461 edited files in the session corpus were among those, and the two
    biggest groups, Markdown (207) and Python (124), were unnamed and
    explicitly excluded respectively. A model holding a `.md` or `.go` file had
    a reasonable basis to decide the tool was not for its situation (#3601).
    """

    @staticmethod
    def _description(tool_name: str = "edit_file") -> str:
        from gaia.agents.base.tools import _TOOL_REGISTRY

        saved = dict(_TOOL_REGISTRY)
        try:
            _Bare().register_file_io_tools()
            entry = _TOOL_REGISTRY[tool_name]
            return (entry.get("description") or entry["function"].__doc__ or "").lower()
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved)

    def test_it_names_the_file_types_agents_actually_edit(self):
        text = self._description()
        # The corpus's four biggest groups, none of which it used to mention.
        for extension in (".md", ".py", ".yml", ".go"):
            assert extension in text, f"{extension} unmentioned: {text}"

    def test_it_does_not_exclude_python(self):
        assert "non-python" not in self._description()

    def test_it_says_which_tool_to_use_instead_when_it_defers(self):
        """Naming only what NOT to use leaves a model with nowhere to go."""
        text = self._description()
        assert "edit_python_file" in text

    def test_it_steers_away_from_rewriting_and_shelling_out(self):
        text = self._description()
        assert "write_file" in text
        assert "sed" in text

    def test_write_file_does_not_carry_the_wording_this_removed(self):
        """The sibling edit_file now points the model at (#3601 review).

        "Prefer it over rewriting a file with write_file" routes the model to a
        tool that used to repeat the same 2%-of-edits claim.
        """
        text = self._description("write_file")
        assert "non-python" not in text
        for extension in (".md", ".py", ".yml", ".go"):
            assert extension in text, f"{extension} unmentioned: {text}"

    def test_write_file_says_when_to_use_edit_file_instead(self):
        assert "edit_file" in self._description("write_file")

    def test_write_file_says_which_tool_validates_python(self):
        assert "write_python_file" in self._description("write_file")
