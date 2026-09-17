# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""An answer must not claim to have written a file that is not there.

This is the worst failure an agent has, because it is indistinguishable from
success: the step count looks healthy, no tool reported an error, and the
summary is confident and specific. Observed shape — the agent writes a parser
script, narrates running it, and stops without ever executing it.
"""

import pytest

from gaia.agents.base.verification import claimed_written_files, unwritten_claims


class TestClaimDetection:
    @pytest.mark.parametrize(
        "answer,expected",
        [
            ("Done. Wrote a parser and generated `orders.json`.", ["orders.json"]),
            ("Wrote the answer to `answer.txt`.", ["answer.txt"]),
            ("The triage is written to `triage.md`:", ["triage.md"]),
            ("`report.csv` has been created.", ["report.csv"]),
        ],
    )
    def test_detects_a_claim_to_have_produced_a_file(self, answer, expected):
        assert claimed_written_files(answer) == expected

    @pytest.mark.parametrize(
        "answer",
        [
            "See `config.py` for the setting.",
            "`parse.py` defines the helper you asked about.",
            "The bug is in `utils.py` line 40.",
        ],
    )
    def test_merely_naming_a_file_is_not_a_claim(self, answer):
        # Otherwise every answer that cites a file gets a warning nobody reads.
        assert claimed_written_files(answer) == []


class TestMissingFiles:
    def test_a_written_file_that_exists_is_not_flagged(self, tmp_path):
        (tmp_path / "answer.txt").write_text("content")
        assert unwritten_claims("Wrote `answer.txt`.", str(tmp_path)) == []

    def test_a_claimed_file_that_is_absent_is_flagged(self, tmp_path):
        assert unwritten_claims("Wrote `answer.txt`.", str(tmp_path)) == ["answer.txt"]

    def test_an_empty_file_counts_as_missing(self, tmp_path):
        # "generated orders.json" plus a zero-byte file is the same broken
        # promise as no file at all.
        (tmp_path / "orders.json").write_text("")
        assert unwritten_claims("Generated `orders.json`.", str(tmp_path)) == [
            "orders.json"
        ]


class TestRequestedOutputs:
    """A request that names an output file must produce one.

    The phantom-write check reads the answer, so it cannot see a silent
    omission: told to put a number in answer.txt, the agent replied "400" and
    created nothing. Correct arithmetic, no artefact, and nothing in the answer
    to contradict.
    """

    def test_a_requested_file_that_was_written_is_not_flagged(self, tmp_path):
        from gaia.agents.base.verification import missing_requested_outputs

        (tmp_path / "answer.txt").write_text("400")
        assert (
            missing_requested_outputs("write that number to answer.txt", str(tmp_path))
            == []
        )

    def test_a_requested_file_that_was_skipped_is_flagged(self, tmp_path):
        from gaia.agents.base.verification import missing_requested_outputs

        assert missing_requested_outputs(
            "Work out the mean and write just that number to answer.txt.",
            str(tmp_path),
        ) == ["answer.txt"]

    def test_a_request_naming_no_output_file_is_ignored(self, tmp_path):
        from gaia.agents.base.verification import missing_requested_outputs

        # Reading a file is not a request to create one; flagging it would warn
        # on every question that happens to mention a filename.
        assert (
            missing_requested_outputs("Fix config.py so tests pass", str(tmp_path))
            == []
        )
