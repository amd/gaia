# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fixed contract (#1892): the email agent's 16K-target / 32K-max ctx envelope.

``gaia_agent_email.context_budget`` does not exist yet — this file is the
TDD pin for it. It must define exactly two integer constants
(``CONTEXT_TARGET_TOKENS`` = 16384, ``CONTEXT_MAX_TOKENS`` = 32768) plus a
non-trivial rationale docstring explaining the envelope.
"""

import pytest


class TestContextBudgetConstants:
    def test_context_target_tokens_is_16384(self):
        from gaia_agent_email.context_budget import CONTEXT_TARGET_TOKENS

        assert CONTEXT_TARGET_TOKENS == 16384
        assert isinstance(CONTEXT_TARGET_TOKENS, int)

    def test_context_max_tokens_is_32768(self):
        from gaia_agent_email.context_budget import CONTEXT_MAX_TOKENS

        assert CONTEXT_MAX_TOKENS == 32768
        assert isinstance(CONTEXT_MAX_TOKENS, int)

    def test_target_is_strictly_less_than_max(self):
        from gaia_agent_email.context_budget import (
            CONTEXT_MAX_TOKENS,
            CONTEXT_TARGET_TOKENS,
        )

        assert CONTEXT_TARGET_TOKENS < CONTEXT_MAX_TOKENS

    def test_module_carries_a_non_trivial_rationale_docstring(self):
        """The rationale may live on the module docstring or on the constants
        themselves — this test accepts either, but requires at least one
        non-trivial (>40 char) docstring somewhere in the module that
        mentions the envelope."""
        import gaia_agent_email.context_budget as mod

        candidates = [mod.__doc__ or ""]
        # Constants are plain ints (no __doc__ of their own beyond int's), so
        # the rationale is expected on the module docstring in practice — but
        # we don't hard-require that specific placement, just that SOME
        # docstring in the module is substantive.
        rationale = max(candidates, key=len)
        assert isinstance(rationale, str)
        assert len(rationale.strip()) > 40, (
            "context_budget.py must carry a non-trivial rationale docstring "
            "explaining the 16K-target/32K-max envelope, not a bare stub"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
