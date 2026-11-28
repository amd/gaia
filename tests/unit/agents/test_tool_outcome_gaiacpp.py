# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""gaiacpp bash_execute failure shapes are classified as failed tool outcomes."""

import pytest

from gaia.agents.base.tool_outcome import (
    ERROR_STATUSES,
    FAILED_STATUSES,
    tool_result_failed,
)


@pytest.mark.parametrize(
    "payload",
    [
        {"stdout": "", "stderr": "No such file", "exit_code": 1, "timed_out": False},
        {"stdout": "", "stderr": "", "exit_code": 127, "timed_out": False},
        {"stdout": "", "stderr": "", "exit_code": -1, "timed_out": False},
        {"stdout": "", "stderr": "", "exit_code": 0, "timed_out": True},
        {
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "cancelled": True,
        },
    ],
)
def test_gaiacpp_bash_execute_failures_report_failed(payload):
    assert tool_result_failed(payload) is True


def test_gaiacpp_bash_execute_zero_exit_succeeds():
    payload = {"stdout": "ok\n", "stderr": "", "exit_code": 0, "timed_out": False}
    assert tool_result_failed(payload) is False


def test_gaiacpp_bash_execute_expected_exit_is_not_failed():
    payload = {
        "stdout": "",
        "stderr": "",
        "exit_code": 1,
        "timed_out": False,
        "expected_exit": "No matches found",
    }
    assert tool_result_failed(payload) is False


def test_denied_confirmation_timeout_respects_failed_statuses():
    payload = {"status": "denied", "timed_out": True, "error": "Approval timed out"}
    assert tool_result_failed(payload, ERROR_STATUSES) is False
    assert tool_result_failed(payload, FAILED_STATUSES) is True
