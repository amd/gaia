# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A package manager's exit code is put into words.

``winget install Git.Git`` failed with exit ``-1978335138`` and nothing else a
person could read, so the model guessed at the cause and the user saw only
"Running command failed".
"""

import subprocess

import pytest

from gaia import package_manager_errors
from gaia.skills import binary_setup
from gaia.skills.binary_setup import InstallPlan, SetupError

CERTIFICATE = -1978335138  # 0x8A15005E as Windows hands it to Python


def test_the_code_winget_returned_in_the_field_is_explained():
    meaning = package_manager_errors.explain("winget", CERTIFICATE)

    assert "certificate" in meaning
    assert "--source winget" in meaning


@pytest.mark.parametrize(
    "program", ["winget", "winget.exe", "WINGET.EXE", "C:\\Windows\\winget.exe"]
)
def test_the_program_may_be_spelled_as_a_path_or_with_its_extension(program):
    assert package_manager_errors.explain(program, CERTIFICATE)


def test_the_unsigned_spelling_of_a_code_reads_the_same():
    assert package_manager_errors.explain(
        "winget", 0x8A15005E
    ) == package_manager_errors.explain("winget", CERTIFICATE)


@pytest.mark.parametrize(
    "program, code",
    [
        ("winget", 0),  # success needs no explaining
        ("winget", None),  # killed before it exited
        ("winget", 1),  # a code the table does not know
        ("git", CERTIFICATE),  # a number means nothing outside its program
    ],
)
def test_nothing_is_made_up_for_a_code_gaia_cannot_read(program, code):
    assert package_manager_errors.explain(program, code) is None


@pytest.mark.parametrize("code", [0x8A150061, 0x8A15002B, -1978335135])
def test_already_installed_is_not_a_failure(code):
    assert package_manager_errors.nothing_to_do("winget.exe", code)


@pytest.mark.parametrize(
    "program, code", [("winget", CERTIFICATE), ("winget", 0), ("git", 0x8A150061)]
)
def test_a_real_failure_is_still_one(program, code):
    assert not package_manager_errors.nothing_to_do(program, code)


def test_a_failed_install_says_what_the_code_means(monkeypatch):
    plan = InstallPlan(
        binary="gh",
        argv=("winget", "install", "--id", "GitHub.cli", "--exact"),
        docs_url="https://cli.github.com",
    )
    monkeypatch.setattr(
        binary_setup,
        "_run",
        lambda argv, timeout: subprocess.CompletedProcess(argv, CERTIFICATE, "", ""),
    )

    with pytest.raises(SetupError) as failure:
        binary_setup.run_install(plan)

    assert "certificate" in str(failure.value)
    assert "https://cli.github.com" in str(failure.value)
