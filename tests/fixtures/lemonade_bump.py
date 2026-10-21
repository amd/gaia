# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Scratch repos, patches and step runners for the Lemonade bump workflow tests.

Shared by ``tests/unit/test_check_lemonade_bump.py`` (the checker and renderer)
and ``tests/unit/test_lemonade_bump_workflow_contract.py`` (the workflow), so
both feed the same honest and hostile bumps through the same code.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "lemonade-version-bump.yml"
CHECKER = REPO_ROOT / "util" / "check_lemonade_bump.py"
RENDER = REPO_ROOT / "util" / "render_lemonade_bump_pr.py"
CURRENT, TARGET = "2026.42.1", "2026.43.1"
INSTALLER = "src/gaia/installer/lemonade_installer.py"
EMBEDDED = "src/gaia/llm/lemonade_embedded.py"
TAGS = "tests/fixtures/lemonade/release_tags.txt"
BUMP_FILES = [
    "src/gaia/version.py",
    EMBEDDED,
    INSTALLER,
    TAGS,
    "util/check_lemonade_bump.py",
    "util/check_doc_versions.py",
]
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}
DOC = (
    f"Install Lemonade v{CURRENT} first.\n"
    "It listens on 127.0.0.1:13305 and needs Python 3.10.0.\n"
    f"Email needs Lemonade v{CURRENT}+.\n"
    f"Voice needs Lemonade v{CURRENT} or later.\n"
    "Embeddings arrived in Lemonade v2026.40.0.\n"
    "Then run gaia init.\n"
)


def load_checker():
    spec = importlib.util.spec_from_file_location("check_lemonade_bump", CHECKER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def git_bytes(repo: Path, *args: str, stdin: bytes = None) -> bytes:
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=repo,
        env={**os.environ, **GIT_ENV},
        input=stdin,
        capture_output=True,
        check=True,
    )
    return result.stdout


def git(repo: Path, *args: str) -> str:
    return git_bytes(repo, *args).decode("utf-8")


def write(path: Path, text) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))


def read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def edit(repo: Path, rel: str, old: str, new: str, count: int = 1) -> None:
    text = read(repo / rel)
    assert text.count(old) >= count, f"{old!r} not in {rel}"
    write(repo / rel, text.replace(old, new, count))


def make_main_repo(root: Path) -> Path:
    """A scratch "main" holding the files a bump edits, a doc, a test and a config."""
    repo = root / "main"
    for rel in BUMP_FILES:
        write(repo / rel, (REPO_ROOT / rel).read_bytes().replace(b"\r\n", b"\n"))
    write(repo / "docs" / "install.mdx", DOC)
    write(
        repo / "tests" / "unit" / "test_pins.py",
        "def test_pin():\n    assert PINNED == PUBLISHED\n",
    )
    write(repo / "pyproject.toml", "[project]\nname = 'x'\n")
    git(root, "init", "-q", "-b", "main", str(repo))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "main")
    return repo


def digest(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


def pin_block(name: str, keys: list, annotation: str = "") -> str:
    lines = "".join(f'    "{key}": "{digest(key)}",\n' for key in keys)
    return f"{name}{annotation} = {{\n{lines}}}\n"


def replace_block(text: str, name: str, block: str) -> str:
    pattern = re.compile(rf"^{name}(?::[^=\n]+)? = \{{\n(?:    .*\n)*?\}}\n", re.M)
    assert len(pattern.findall(text)) == 1, name
    return pattern.sub(lambda _: block, text)


def target_keys(name: str, version: str = TARGET) -> list:
    return [key.format(v=version) for key in load_checker().EXACT_PINS[name]]


def honest_bump(repo: Path) -> None:
    """What a correct bump from CURRENT to TARGET changes, and nothing else."""
    edit(repo, "src/gaia/version.py", f'"{CURRENT}"', f'"{TARGET}"')
    installer = replace_block(
        read(repo / INSTALLER),
        "INSTALLER_SHA256",
        pin_block("INSTALLER_SHA256", target_keys("INSTALLER_SHA256")),
    )
    outgoing = [f"v{CURRENT}/lemonade-server-minimal.msi", f"v{CURRENT}/lemonade.msi"]
    legacy_end = installer.index("}\n", installer.index("LEGACY_MSI_SHA256 = {"))
    installer = (
        installer[:legacy_end]
        + "".join(f'    "{key}": "{digest(key)}",\n' for key in outgoing)
        + installer[legacy_end:]
    )
    write(repo / INSTALLER, installer)
    write(
        repo / EMBEDDED,
        replace_block(
            read(repo / EMBEDDED),
            "EMBEDDABLE_SHA256",
            pin_block(
                "EMBEDDABLE_SHA256",
                target_keys("EMBEDDABLE_SHA256"),
                ": Dict[str, str]",
            ),
        ),
    )
    edit(repo, TAGS, f"v{CURRENT}\n", f"v{CURRENT}\nv{TARGET}\n")
    edit(
        repo,
        "docs/install.mdx",
        f"Install Lemonade v{CURRENT}",
        f"Install Lemonade v{TARGET}",
    )


def patch_from(repo: Path, tmp_path: Path, untracked_too: bool = False) -> Path:
    """Diff the edits into a patch, as the agent job does, and reset *repo* to main."""
    if untracked_too:
        git(repo, "add", "-A")
        diff = git_bytes(repo, "diff", "--binary", "--cached")
        git(repo, "reset", "-q")
    else:
        diff = git_bytes(repo, "diff", "--binary")
    patch = tmp_path / "bump.patch"
    patch.write_bytes(diff)
    git(repo, "checkout", "-q", "--", ".")
    git(repo, "clean", "-qfd")
    return patch


def run_checker(repo: Path, patch: Path, target: str = TARGET):
    command = [sys.executable, "-I", str(CHECKER), "--repo", str(repo)]
    command += ["--patch", str(patch), "--target", target]
    return subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", check=False
    )


def outside_fences(body: str) -> str:
    """The parts of *body* GitHub renders as Markdown, fences and their text removed.

    As in CommonMark, a line of at least as many backticks as the opening fence
    closes it.
    """
    kept, fence = [], None
    for line in body.splitlines():
        marker = re.match(r"(`{3,})", line)
        if fence is None and marker:
            fence = marker.group(1)
        elif fence is not None and re.fullmatch(rf"`{{{len(fence)},}}\s*", line):
            fence = None
        elif fence is None:
            kept.append(line)
    assert fence is None, "a fence was never closed"
    return "\n".join(kept)


def bash() -> str:
    if os.name == "nt":
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        found = Path(program_files) / "Git" / "bin" / "bash.exe"
        candidate = str(found) if found.exists() else None
    else:
        candidate = shutil.which("bash")
    if not candidate:
        pytest.skip("no bash to run the workflow's step scripts")
    return candidate


FAKE_GH = """#!/usr/bin/env bash
# One call per line: arguments joined by \\x1f, newlines inside them escaped.
printf '%s\\x1f' "${@//$'\\n'/\\\\n}" >> "$GH_LOG"
printf '\\n' >> "$GH_LOG"
case "$1 $2" in
  "issue list") printf '%s' "${FAKE_ISSUE_LIST:-}" ;;
  "pr list") printf '%s' "${FAKE_PR_LIST:-}" ;;
  "issue create")
    n=$(( $(grep -c '^issue.create' "$GH_LOG") + 100 ))
    echo "https://github.com/amd/gaia/issues/$n" ;;
  "api "*) printf '%s' "${FAKE_API_JSON:-}" ;;
esac
"""


def local_remote(tmp_path: Path, url: str) -> dict:
    """Env that makes git send anything for *url* to a fresh local bare repo instead.

    Uses git's own ``insteadOf``, so no request for *url* ever leaves the machine,
    and any other github.com URL goes to a path that does not exist.
    """
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    return {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": f"url.{bare.as_posix()}.insteadOf",
        "GIT_CONFIG_VALUE_0": url,
        "GIT_CONFIG_KEY_1": f"url.{(tmp_path / 'no-such-remote').as_posix()}/.insteadOf",
        "GIT_CONFIG_VALUE_1": "https://github.com/",
    }


def run_step(job_id: str, name: str, cwd: Path, env: dict, tmp_path: Path):
    """Run one step's script under bash with a fake ``gh``; return (result, outputs, gh calls)."""
    steps = [s for s in workflow()["jobs"][job_id]["steps"] if s.get("name") == name]
    assert steps, f"{job_id} has no {name!r} step"
    script = steps[0]["run"]
    assert "${{" not in script, f"{name!r} must read inputs from env"
    bin_dir = tmp_path / "fake-bin"
    write(bin_dir / "gh", FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    output, gh_log = tmp_path / "github_output", tmp_path / "gh.log"
    write(output, "")
    write(gh_log, "")
    full_env = {
        **os.environ,
        **GIT_ENV,
        "GITHUB_OUTPUT": output.as_posix(),
        "GITHUB_STEP_SUMMARY": (tmp_path / "summary").as_posix(),
        "GITHUB_REPOSITORY": "amd/gaia",
        "GITHUB_RUN_ID": "1",
        "RUNNER_TEMP": tmp_path.as_posix(),
        "GH_LOG": gh_log.as_posix(),
        "PATH": os.pathsep.join(
            [str(bin_dir), str(Path(sys.executable).parent), os.environ["PATH"]]
        ),
        **env,
    }
    result = subprocess.run(
        [bash(), "-c", script],
        cwd=cwd,
        env=full_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    calls = [line.split("\x1f")[:-1] for line in read(gh_log).splitlines()]
    return result, read(output), calls
