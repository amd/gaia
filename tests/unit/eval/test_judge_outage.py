# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A judge the eval could not reach must report as an outage, not a bad score.

The regression these guard: the email drafting eval ran for 21 minutes, then
died inside the judge with a raw ``anthropic.BadRequestError`` whose message was
"Your credit balance is too low". Nothing reported that as a billing problem —
the log just ended in an SDK traceback, and the run looked like the agent had
failed. The rejection strings below are the real ones the API and the CLI
returned, not paraphrases.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gaia.eval.judge_outage import (
    JudgeOutageError,
    classify_judge_exception,
    judge_completion_text,
    run_with_outage_guard,
)

# The verbatim body Anthropic returned on the failing Email Triage Eval run.
CREDIT_BALANCE_400 = (
    "Error code: 400 - {'type': 'error', 'error': {'type': "
    "'invalid_request_error', 'message': 'Your credit balance is too low to "
    "access the Anthropic API. Please go to Plans & Billing to upgrade or "
    "purchase credits.'}}"
)

# The verbatim text the CI preflight probe got from the same exhausted account.
WEEKLY_LIMIT = "You've hit your weekly limit · resets Sep 23, 8am (America/Los_Angeles)"


class _StubAPIError(Exception):
    """Stands in for ``anthropic.APIStatusError`` — same duck type, no SDK.

    ``classify_judge_exception`` reads ``status_code``/``message``/``body`` off
    whatever it is handed, so the tests must not need the ``[eval]`` extras
    installed to exercise the classifier.
    """

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class TestClassifyRealRejections:
    def test_credit_balance_400_is_a_quota_outage(self):
        outage = classify_judge_exception(_StubAPIError(CREDIT_BALANCE_400, 400))

        assert outage is not None
        assert outage.kind == "quota"
        assert outage.status_code == 400

    def test_weekly_limit_is_a_quota_outage(self):
        """#4080's account exhaustion also speaks this dialect.

        Worth its own case because the phrase shares no substring with the
        credit-balance one — a classifier matched only to "credit balance"
        passes the test above and still misses the live CI failure.
        """
        outage = classify_judge_exception(_StubAPIError(WEEKLY_LIMIT))

        assert outage is not None
        assert outage.kind == "quota"

    @pytest.mark.parametrize("status", [402, 429])
    def test_quota_status_codes_need_no_phrase(self, status):
        outage = classify_judge_exception(_StubAPIError("", status))

        assert outage is not None
        assert outage.kind == "quota"

    @pytest.mark.parametrize("status", [401, 403])
    def test_credential_status_codes_are_not_quota(self, status):
        """A rejected key and an empty wallet need different people to fix them."""
        outage = classify_judge_exception(_StubAPIError("", status))

        assert outage is not None
        assert outage.kind == "credential"

    def test_invalid_api_key_text_is_a_credential_outage(self):
        outage = classify_judge_exception(
            _StubAPIError("authentication_error: invalid x-api-key", 400)
        )

        assert outage is not None
        assert outage.kind == "credential"

    def test_message_names_the_outage_and_the_fix(self):
        outage = classify_judge_exception(_StubAPIError(CREDIT_BALANCE_400, 400))

        # The report is wrapped for a CI log pane, so phrases straddle lines.
        message = " ".join(outage.message().split())
        assert "INFRASTRUCTURE OUTAGE, NOT a quality regression" in message
        assert "admin-settings/usage" in message
        assert "credit balance is too low" in message


class TestClassifierDoesNotOverreach:
    def test_a_real_malformed_request_is_not_an_outage(self):
        """The 400 that means "your request is wrong" must still fail loudly.

        Swallowing it as a billing problem would send the reader to their
        finance team for a bug in this repo.
        """
        exc = _StubAPIError(
            "Error code: 400 - temperature: Extra inputs are not permitted", 400
        )

        assert classify_judge_exception(exc) is None

    def test_an_unrelated_exception_is_not_an_outage(self):
        assert classify_judge_exception(RuntimeError("judge prompt was empty")) is None

    def test_ambiguous_text_still_reports_as_an_outage(self):
        """Matching both signatures is unresolvable, but it is never a score.

        The one thing that must hold either way is that nothing was measured.
        """
        outage = classify_judge_exception(
            _StubAPIError("unauthorized: your credit balance is too low")
        )

        assert outage is not None
        assert outage.kind == "unclassified"


class TestOutageIsNotAValueError:
    def test_not_a_valueerror(self):
        """Load-bearing, not a style choice.

        ``judge_drafts`` and ``judge_briefings`` both catch ``ValueError`` to
        turn an unparseable verdict into an ``ERRORED`` scorecard row. If this
        ever subclassed ``ValueError``, an outage would silently become a
        scorecard full of failures — exactly the "reads as a model regression"
        bug these tests exist to prevent.
        """
        outage = JudgeOutageError("quota", "detail")

        assert isinstance(outage, RuntimeError)
        assert not isinstance(outage, ValueError)


# ---------------------------------------------------------------------------
# The call wrapper
# ---------------------------------------------------------------------------


class TestJudgeCompletionText:
    def test_flattens_text_blocks_on_success(self):
        client = SimpleNamespace(
            get_completion=lambda prompt: [
                SimpleNamespace(text="{"),
                SimpleNamespace(text='"approved": true}'),
            ]
        )

        assert judge_completion_text(client, "p") == '{"approved": true}'

    def test_raises_outage_chained_to_the_sdk_error(self):
        original = _StubAPIError(CREDIT_BALANCE_400, 400)
        client = SimpleNamespace(get_completion=MagicMock(side_effect=original))

        with pytest.raises(JudgeOutageError) as excinfo:
            judge_completion_text(client, "p")

        assert excinfo.value.kind == "quota"
        # __cause__ keeps the SDK traceback for anyone who needs the raw error.
        assert excinfo.value.__cause__ is original

    def test_reraises_an_unrecognised_error_untouched(self):
        boom = RuntimeError("connection reset")
        client = SimpleNamespace(get_completion=MagicMock(side_effect=boom))

        with pytest.raises(RuntimeError) as excinfo:
            judge_completion_text(client, "p")

        assert excinfo.value is boom


# ---------------------------------------------------------------------------
# What the CI log actually prints
# ---------------------------------------------------------------------------


class TestRunWithOutageGuard:
    def test_prints_the_outage_and_exits_nonzero(self, capsys):
        def main():
            raise JudgeOutageError("quota", CREDIT_BALANCE_400, 400)

        assert run_with_outage_guard(main) == 1

        err = " ".join(capsys.readouterr().err.split())
        assert "JUDGE QUOTA EXHAUSTED" in err
        assert "INFRASTRUCTURE OUTAGE, NOT a quality regression" in err
        assert "What to do:" in err
        # No traceback — the whole point is that the reader does not get one.
        assert "Traceback" not in err

    def test_passes_through_a_normal_exit_code(self):
        assert run_with_outage_guard(lambda: 0) == 0
        assert run_with_outage_guard(lambda: 1) == 1

    def test_does_not_swallow_other_failures(self):
        def main():
            raise RuntimeError("the corpus fixture is missing")

        with pytest.raises(RuntimeError, match="corpus fixture"):
            run_with_outage_guard(main)
