# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A step the user ruled out is never proposed as a tool call (#8803).

Told "do not change permissions and do not use the shell", the flagship wrote
that it wouldn't, then asked to run ``chmod`` in the same turn.
"""

import pytest

from tests.unit.test_profilespec_characterization import build_agent_for_row

pytest.importorskip("gaiapy")

RULE = (
    '- The user ruled a step out ("don\'t use the shell", "don\'t change '
    'permissions") → never call it, not even to ask; say what it blocks and stop.'
)


@pytest.mark.parametrize("profile", ["full", "file", "doc"])
def test_the_tool_rules_forbid_proposing_a_ruled_out_step(profile):
    prompt, _tools = build_agent_for_row(profile=profile)
    assert RULE in prompt
    # Next to the tool-use etiquette, not buried in a later section.
    assert prompt.index(RULE) - prompt.index("**TOOL USAGE:**") < 400
