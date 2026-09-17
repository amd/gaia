# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A turn must not end by announcing work it never started.

Observed on a task-execution benchmark as "1 step, 0 calls": the model replies
"I'll now read the CSV and compute the mean", the loop accepts that as the final
answer, and the task fails having done nothing. The guard is deliberately narrow
— a conversational turn legitimately runs no tools, so future-tense intent is
what separates the two.
"""

import pytest

from gaia.agents.base.agent import _INTENT_WITHOUT_ACTION


class TestIntentDetection:
    @pytest.mark.parametrize(
        "answer",
        [
            "I will now read the CSV and compute the mean.",
            "I'll create the answer file next.",
            "Let me check the config file first.",
            "Let's search for the failing test.",
            "Next steps: run the tests and report.",
            "I need to inspect the diff before deciding.",
            "Here is my plan for the migration.",
        ],
    )
    def test_announcing_an_action_is_caught(self, answer):
        assert _INTENT_WITHOUT_ACTION.search(answer)

    @pytest.mark.parametrize(
        "answer",
        [
            "I read the file and the answer is 42.",
            "The mean failed-build duration is 400 seconds.",
            "Done. Fixed the bug in the parser and the tests pass.",
            "Python 3.11 is required, per requires-python in pyproject.toml.",
            "There are three defects: MD5 hashing, timing comparison, and SQL "
            "built by string formatting.",
        ],
    )
    def test_reporting_completed_work_is_not_caught(self, answer):
        # These end turns legitimately. Flagging them would re-prompt an agent
        # that has already answered, wasting a step on every conversational turn.
        assert not _INTENT_WITHOUT_ACTION.search(answer)
