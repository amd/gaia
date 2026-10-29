# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fail when any GAIA daemon is still running.

CI runs this after its test steps: a runner starts with no daemon, so one
that is still alive was leaked by a test (#5293).
"""

import sys

from gaia.daemon.strays import find_daemons, format_age


def main() -> int:
    daemons = find_daemons()
    if not daemons:
        print("No GAIA daemon is running.")
        return 0
    print(f"{len(daemons)} GAIA daemon(s) outlived the tests:")
    for daemon in daemons:
        print(
            f"  pid {daemon.pid}, up {format_age(daemon.age_seconds)}, "
            f"home {daemon.home or '(unknown)'}"
        )
    print(
        "A test started a daemon and nothing stopped it. Stop it in that "
        "test's teardown, in a finally."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
