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


class TestEstimateTokens:
    """The dual char/word token estimator (#1889).

    ``estimate_tokens(text) == max(len(text) // 4, int(len(text.split()) * 1.3))``,
    with the empty string mapping to 0. These do not exist on the current tip —
    the import is EXPECTED to raise ImportError until #1889's implementation
    lands (red half of red-green TDD).
    """

    def test_empty_string_is_zero(self):
        from gaia_agent_email.context_budget import estimate_tokens

        assert estimate_tokens("") == 0

    def test_char_estimate_dominates_for_dense_text(self):
        from gaia_agent_email.context_budget import estimate_tokens

        # No spaces -> one "word"; chars//4 must win.
        text = "x" * 4000
        assert estimate_tokens(text) == max(4000 // 4, int(1 * 1.3))
        assert estimate_tokens(text) == 1000

    def test_word_estimate_dominates_for_spacey_text(self):
        from gaia_agent_email.context_budget import estimate_tokens

        # Many short words -> words*1.3 must win over chars//4.
        text = " ".join(["a"] * 1000)  # 1000 words, 1999 chars
        expected = max(len(text) // 4, int(1000 * 1.3))
        assert estimate_tokens(text) == expected
        assert estimate_tokens(text) == 1300

    def test_matches_the_exact_formula(self):
        from gaia_agent_email.context_budget import estimate_tokens

        for text in ("hello world", "one", "a b c d e", "z" * 137, ""):
            expected = (
                0 if not text else max(len(text) // 4, int(len(text.split()) * 1.3))
            )
            assert estimate_tokens(text) == expected


class TestThreadBudgetTokens:
    """The usable thread-transcript token budget (#1889).

    ``thread_budget_tokens() == CONTEXT_TARGET_TOKENS - 1536 - 1024 == 13824``.
    """

    def test_concrete_value_is_13824(self):
        from gaia_agent_email.context_budget import thread_budget_tokens

        assert thread_budget_tokens() == 13824

    def test_is_a_positive_int_below_context_target(self):
        from gaia_agent_email.context_budget import (
            CONTEXT_TARGET_TOKENS,
            thread_budget_tokens,
        )

        budget = thread_budget_tokens()
        assert isinstance(budget, int)
        assert budget > 0
        assert budget < CONTEXT_TARGET_TOKENS

    def test_equals_target_minus_the_two_named_reserves(self):
        from gaia_agent_email.context_budget import (
            _RESPONSE_RESERVE_TOKENS,
            _SYSTEM_PROMPT_ALLOWANCE_TOKENS,
            CONTEXT_TARGET_TOKENS,
            thread_budget_tokens,
        )

        assert _SYSTEM_PROMPT_ALLOWANCE_TOKENS == 1536
        assert _RESPONSE_RESERVE_TOKENS == 1024
        assert thread_budget_tokens() == (
            CONTEXT_TARGET_TOKENS
            - _SYSTEM_PROMPT_ALLOWANCE_TOKENS
            - _RESPONSE_RESERVE_TOKENS
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
