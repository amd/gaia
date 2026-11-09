# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Entry of the frozen ``gaia`` executable the apps ship (see freeze.py)."""

import multiprocessing
import os
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from gaia.env import unfreeze_loader_paths

    # The loader read the bundle path at exec; children need the user's.
    unfreeze_loader_paths(os.environ)
    from gaia.frozen import dispatch

    status = dispatch(sys.argv[1:])
    if status is None:
        # The one-line installers link gaia-mcp to this executable too.
        if os.path.basename(sys.argv[0]).lower().startswith("gaia-mcp"):
            from gaia.mcp.mcp_bridge import main
        else:
            from gaia.cli import main

        status = main()
    sys.exit(status)
