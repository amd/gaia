# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Guards the "no in-process CLI" invariant for the email agent package.

Since #2191 the only supported way to run a query is the REST sidecar:
``gaia-agent-email serve`` exposes ``/query``, and callers relay to it. The
package used to also carry ``gaia_agent_email/cli.py``, an in-process
argparse entry point that nothing imported and no console script exposed —
dead code whose docstring still advertised a caller that had stopped calling
it. It was deleted; these tests keep it deleted.

If a future change needs an in-process entry point, that is a contract
decision (it bypasses the sidecar's auth, trust gate, and SSE translation) —
delete these tests deliberately rather than letting the module creep back.
"""

from __future__ import annotations

import re
from pathlib import Path

EMAIL_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = EMAIL_ROOT / "gaia_agent_email"


def test_in_process_cli_module_is_absent():
    """``gaia_agent_email/cli.py`` must not exist (#2191)."""
    assert not (PACKAGE_ROOT / "cli.py").exists(), (
        "gaia_agent_email/cli.py is back. The query path is the REST sidecar "
        "(`gaia-agent-email serve` -> POST /query); an in-process CLI bypasses "
        "caller auth, the trust gate, and SSE translation. See #2191."
    )


def test_no_module_imports_the_in_process_cli():
    """Nothing in the package may import a ``cli`` submodule."""
    offenders = []
    pattern = re.compile(r"^\s*(from\s+\.?cli\s+import|import\s+gaia_agent_email\.cli)")
    for path in PACKAGE_ROOT.rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if pattern.match(line):
                offenders.append(f"{path.relative_to(EMAIL_ROOT)}:{lineno}: {line!r}")
    assert not offenders, (
        "These modules import an in-process CLI that should not exist:\n"
        + "\n".join(offenders)
    )


def test_only_console_script_is_the_sidecar_server():
    """``[project.scripts]`` exposes the sidecar and nothing else."""
    pyproject = (EMAIL_ROOT / "pyproject.toml").read_text()
    section = re.search(
        r"^\[project\.scripts\]\s*$(.*?)(?=^\[|\Z)",
        pyproject,
        re.MULTILINE | re.DOTALL,
    )
    assert section, "[project.scripts] section missing from pyproject.toml"
    entries = dict(
        re.findall(r'^\s*([\w.-]+)\s*=\s*"([^"]+)"', section.group(1), re.MULTILINE)
    )
    assert entries == {"gaia-agent-email": "gaia_agent_email.server:main"}, (
        "Unexpected console scripts: "
        f"{entries}. The sidecar server is the only supported entry point (#2191)."
    )
