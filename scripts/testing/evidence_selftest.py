#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Throwaway self-test for the real-world-testing evidence workflow (issue #2377).

NOT a product feature. It exists only to exercise the issue -> PR -> evidence ->
close loop of the gaia-testing contract (PR #2376) against a real CLI surface.
Safe to delete; the PR that adds it is closed unmerged.
"""
import argparse

# Planted, unguessable token — its presence in the output proves a live run,
# not a copied-in claim.
TOKEN = "selftest-marmot-7731"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Real-world-testing evidence self-test (throwaway)."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="print the evidence line and exit 0",
    )
    parser.parse_args()
    print(f"[evidence-selftest] OK token={TOKEN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
