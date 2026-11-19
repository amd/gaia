#!/usr/bin/env python3
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Say whether a CI runner is on AMD's network, and fail if it can't judge there.

On AMD's network GAIA sends Claude only through the AMD LLM Gateway, so an eval
job there needs a gateway key. This uses GAIA's own detection
(``gaia.llm.amd_network``), reading the runner as it is: the repository secret
arrives as ``GATEWAY_SECRET``, a name detection never reads, so a GitHub-hosted
runner stays "off the network" even when the secret exists.

Writes ``on=true|false`` to ``--github-output``. Exits 1 with a ``::error::``
when the runner is on AMD's network and neither the secret nor a gateway key of
the runner's own is available. Used by ``.github/actions/amd-network``.
"""

from __future__ import annotations

import argparse
import os
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--github-output", help="GITHUB_OUTPUT file to append to")
    parser.add_argument("--what", default="This job", help="Names the job in messages")
    args = parser.parse_args(argv)

    from gaia.llm import gateway
    from gaia.llm.amd_network import current

    verdict = current()
    print(
        f"AMD network: {'yes' if verdict.on_amd_network else 'no'} ({verdict.reason})"
    )
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as out:
            out.write(f"on={'true' if verdict.on_amd_network else 'false'}\n")
    if not verdict.on_amd_network:
        return 0
    if os.environ.get("GATEWAY_SECRET", "").strip():
        print(
            "Claude goes through the AMD LLM Gateway with the LEMONADE_AMD_API_KEY secret."
        )
        return 0
    try:
        _token, source = gateway.resolve_gateway_token_with_source()
    except gateway.GatewayError:
        print(
            f"::error::{args.what} runs on AMD's network, where GAIA sends Claude "
            "(the eval driver and judge) only through the AMD LLM Gateway, but no "
            "gateway key is available: the LEMONADE_AMD_API_KEY repository secret is "
            "empty and the runner has none of its own. Add the LEMONADE_AMD_API_KEY "
            "secret (Settings > Secrets and variables > Actions). See "
            "https://amd-gaia.ai/docs/guides/llm-gateway#on-amds-network-claude-only-goes-through-the-gateway"
        )
        return 1
    print(
        f"Claude goes through the AMD LLM Gateway with {gateway.describe_token_source(source)}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
