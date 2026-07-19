# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fixed contract (#1892): calendar_tools.py must not duplicate
read_tools.DEFAULT_BODY_LIMIT_CHARS under a locally-defined
``_BODY_CHAR_LIMIT`` with a false "matches llm_triage's limit" comment.

After the fix, ``calendar_tools`` imports and uses
``read_tools.DEFAULT_BODY_LIMIT_CHARS`` directly and defines no
module-level ``_BODY_CHAR_LIMIT`` constant of its own.
"""

import pytest


class TestCalendarToolsBodyLimitDedup:
    def test_calendar_tools_defines_no_local_body_char_limit(self):
        import gaia_agent_email.tools.calendar_tools as calendar_tools

        assert not hasattr(calendar_tools, "_BODY_CHAR_LIMIT"), (
            "calendar_tools must not define its own _BODY_CHAR_LIMIT — it "
            "should import read_tools.DEFAULT_BODY_LIMIT_CHARS instead"
        )

    def test_calendar_tools_binds_default_body_limit_chars_from_read_tools(self):
        import gaia_agent_email.tools.calendar_tools as calendar_tools
        from gaia_agent_email.tools.read_tools import DEFAULT_BODY_LIMIT_CHARS

        assert (
            getattr(calendar_tools, "DEFAULT_BODY_LIMIT_CHARS", None)
            == DEFAULT_BODY_LIMIT_CHARS
        ), (
            "calendar_tools must import DEFAULT_BODY_LIMIT_CHARS from "
            "read_tools so there is a single source of truth for the body "
            "truncation limit"
        )

    def test_build_llm_user_prompt_truncates_at_default_body_limit_chars(self):
        """Functional check: a body longer than DEFAULT_BODY_LIMIT_CHARS is
        clipped to exactly that many characters, proving the call site reads
        the shared constant rather than a stale local duplicate."""
        from gaia_agent_email.tools.calendar_tools import _build_llm_user_prompt
        from gaia_agent_email.tools.read_tools import DEFAULT_BODY_LIMIT_CHARS

        long_body = "x" * (DEFAULT_BODY_LIMIT_CHARS + 500)
        prompt = _build_llm_user_prompt("Subject", long_body)

        # The clipped body appears in the prompt; count the run of 'x' chars
        # to confirm truncation happened at exactly DEFAULT_BODY_LIMIT_CHARS.
        run_length = 0
        for ch in prompt:
            if ch == "x":
                run_length += 1
        assert run_length == DEFAULT_BODY_LIMIT_CHARS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
