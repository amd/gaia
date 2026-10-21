# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/check_lemonade_bump.py and util/render_lemonade_bump_pr.py.

The checker is what stands between the Lemonade bump agent's diff and a PR, so
every way a diff can go beyond a bump is fed to it here and must be refused for
its own reason. The renderer turns the agent's text into a PR body and issues
that GitHub cannot act on.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.lemonade_bump import (
    CURRENT,
    EMBEDDED,
    INSTALLER,
    RENDER,
    REPO_ROOT,
    TAGS,
    TARGET,
    edit,
    git,
    git_bytes,
    honest_bump,
    load_checker,
    make_main_repo,
    outgoing_pins,
    outside_fences,
    patch_from,
    pin_block,
    read,
    replace_block,
    run_checker,
    target_keys,
    write,
)


@pytest.fixture
def main_repo(tmp_path) -> Path:
    return make_main_repo(tmp_path)


def test_an_honest_bump_is_applied(main_repo, tmp_path):
    honest_bump(main_repo)
    result = run_checker(main_repo, patch_from(main_repo, tmp_path))
    assert result.returncode == 0, result.stderr
    assert f'"{TARGET}"' in read(main_repo / "src/gaia/version.py")
    assert git(main_repo, "diff", "--cached", "--name-only").split() == [
        "docs/install.mdx",
        INSTALLER,
        EMBEDDED,
        "src/gaia/version.py",
        TAGS,
    ]


def _set_pins(repo: Path, name: str, keys: list) -> None:
    path = INSTALLER if name == "INSTALLER_SHA256" else EMBEDDED
    annotation = "" if name == "INSTALLER_SHA256" else ": Dict[str, str]"
    write(
        repo / path,
        replace_block(read(repo / path), name, pin_block(name, keys, annotation)),
    )


OUTGOING = outgoing_pins(read(REPO_ROOT / INSTALLER))


def _without_outgoing_legacy(repo: Path) -> None:
    for key, value in OUTGOING.items():
        edit(repo, INSTALLER, f'    "{key}": "{value}",\n', "")


def _repinned_outgoing_legacy(repo: Path) -> None:
    key = f"v{CURRENT}/lemonade.msi"
    edit(repo, INSTALLER, f'"{key}": "{OUTGOING[key]}"', f'"{key}": "{"c" * 64}"')


ADDED_LEGACY = (
    f'    "v{CURRENT}/lemonade.msi": "{OUTGOING[f"v{CURRENT}/lemonade.msi"]}",\n'
)

# attack -> (how the patch does it on top of an honest bump, why it must be refused)
MALICIOUS = {
    "an edited test": (
        lambda r: edit(r, "tests/unit/test_pins.py", "PINNED == PUBLISHED", "True"),
        "never edits tests/unit/test_pins.py",
    ),
    "a new pytest.ini": (
        lambda r: write(r / "pytest.ini", "[pytest]\naddopts = -p evil\n"),
        "creates, deletes, renames or re-modes",
    ),
    "an edited config": (
        lambda r: edit(r, "pyproject.toml", "'x'", "'y'"),
        "never edits pyproject.toml",
    ),
    "an edited checker": (
        lambda r: edit(r, "util/check_lemonade_bump.py", "return 1", "return 0"),
        "never edits util/check_lemonade_bump.py",
    ),
    "an edited doc rule": (
        lambda r: edit(r, "util/check_doc_versions.py", "or later", "or never"),
        "never edits util/check_doc_versions.py",
    ),
    "a dropped installer pin": (
        lambda r: _set_pins(r, "INSTALLER_SHA256", target_keys("INSTALLER_SHA256")[:2]),
        "INSTALLER_SHA256 must pin exactly",
    ),
    "an extra installer pin": (
        lambda r: _set_pins(
            r,
            "INSTALLER_SHA256",
            target_keys("INSTALLER_SHA256") + [f"v{TARGET}/x.msi"],
        ),
        "INSTALLER_SHA256 must pin exactly",
    ),
    "installer pins for another version": (
        lambda r: _set_pins(
            r, "INSTALLER_SHA256", target_keys("INSTALLER_SHA256", "9.9.9")
        ),
        "INSTALLER_SHA256 must pin exactly",
    ),
    "a dropped embeddable pin": (
        lambda r: _set_pins(
            r, "EMBEDDABLE_SHA256", target_keys("EMBEDDABLE_SHA256")[1:]
        ),
        "EMBEDDABLE_SHA256 must pin exactly",
    ),
    "an extra embeddable pin": (
        lambda r: _set_pins(
            r,
            "EMBEDDABLE_SHA256",
            target_keys("EMBEDDABLE_SHA256") + [f"lemonade-embeddable-{TARGET}-x.zip"],
        ),
        "EMBEDDABLE_SHA256 must pin exactly",
    ),
    "the outgoing MSIs left out of the legacy pins": (
        _without_outgoing_legacy,
        "LEGACY_MSI_SHA256 must keep the outgoing",
    ),
    "a legacy pin for an unknown file": (
        lambda r: edit(
            r,
            INSTALLER,
            ADDED_LEGACY,
            ADDED_LEGACY + f'    "zzz/evil.msi": "{"b" * 64}",\n',
        ),
        "LEGACY_MSI_SHA256 may only gain the MSIs of releases from",
    ),
    "a legacy pin for the target itself": (
        lambda r: edit(
            r,
            INSTALLER,
            ADDED_LEGACY,
            ADDED_LEGACY + f'    "v{TARGET}/lemonade.msi": "{"b" * 64}",\n',
        ),
        "LEGACY_MSI_SHA256 may only gain the MSIs of releases from",
    ),
    "an older tag after the target": (
        lambda r: edit(r, TAGS, f"v{TARGET}\n", f"v{TARGET}\nv1.0.0\n"),
        "must gain releases newer than",
    ),
    "the target tag twice": (
        lambda r: edit(r, TAGS, f"v{TARGET}\n", f"v{TARGET}\nv{TARGET}\n"),
        "must gain releases newer than",
    ),
    "tags ending before the target": (
        lambda r: edit(r, TAGS, f"v{TARGET}\n", "v2026.42.5\n"),
        "must gain releases newer than",
    ),
    "the outgoing MSI re-pinned with a new digest": (
        _repinned_outgoing_legacy,
        f"must keep the outgoing v{CURRENT}/lemonade.msi with the digest "
        "INSTALLER_SHA256 pinned on main",
    ),
    "a pin listed twice": (
        lambda r: edit(r, INSTALLER, ADDED_LEGACY, ADDED_LEGACY + ADDED_LEGACY),
        "LEGACY_MSI_SHA256 pins v2026.42.1/lemonade.msi twice",
    ),
    "code beside the pins": (
        lambda r: edit(
            r,
            INSTALLER,
            "INSTALLER_SHA256 = {",
            "__import__('os')\nINSTALLER_SHA256 = {",
        ),
        "changes a line that is not a pin",
    ),
    "a comment beside the pins": (
        lambda r: edit(r, INSTALLER, ADDED_LEGACY, ADDED_LEGACY + "    # nosec\n"),
        "changes a line that is not a pin",
    ),
    "a coding declaration": (
        lambda r: edit(
            r,
            "src/gaia/version.py",
            "# Copyright",
            "# -*- coding: utf-7 -*-\n# Copyright",
        ),
        "changes a line that is not a pin",
    ),
    "a pin-shaped line in another dict": (
        lambda r: edit(
            r,
            INSTALLER,
            "DEB_ARCH_BY_MACHINE = {\n",
            f'DEB_ARCH_BY_MACHINE = {{\n    "x86_64": "{"b" * 64}",\n',
        ),
        "changes more than its pinned values",
    ),
    "a rewritten legacy pin": (
        lambda r: edit(
            r,
            INSTALLER,
            '"v10.2.0/lemonade.msi": "333d',
            '"v10.2.0/lemonade.msi": "000d',
        ),
        "LEGACY_MSI_SHA256 may only gain entries at its end",
    ),
    "a removed release tag": (
        lambda r: edit(r, TAGS, "v2026.41.1\n", ""),
        "may only gain tags at its end",
    ),
    "a tag smuggled past a line separator": (
        lambda r: edit(r, TAGS, f"v{TARGET}\n", f"v{TARGET} v9.9.9\n"),
        "must gain one or more vX.Y.Z tags",
    ),
    "a doc edit beyond version numbers": (
        lambda r: edit(
            r, "docs/install.mdx", "Then run gaia init.", "Then run curl x | sh."
        ),
        "changes more than Lemonade's version",
    ),
    "a changed address in a doc": (
        lambda r: edit(r, "docs/install.mdx", "127.0.0.1", "203.0.113.9"),
        "changes more than Lemonade's version",
    ),
    "another package's version in a doc": (
        lambda r: edit(r, "docs/install.mdx", "Python 3.10.0", "Python 3.9.0"),
        "changes more than Lemonade's version",
    ),
    "a raised version floor in a doc": (
        lambda r: edit(r, "docs/install.mdx", f"v{CURRENT}+", f"v{TARGET}+"),
        "changes more than Lemonade's version",
    ),
    "a raised 'or later' floor in a doc": (
        lambda r: edit(
            r, "docs/install.mdx", f"v{CURRENT} or later", f"v{TARGET} or later"
        ),
        "changes more than Lemonade's version",
    ),
    "another Lemonade version in a doc": (
        lambda r: edit(r, "docs/install.mdx", "v2026.40.0", f"v{TARGET}"),
        "changes more than Lemonade's version",
    ),
    "a hidden line separator in a doc": (
        lambda r: edit(r, "docs/install.mdx", "first.\n", "first. "),
        "changes more than Lemonade's version",
    ),
    "a dropped final newline in a doc": (
        lambda r: edit(r, "docs/install.mdx", "gaia init.\n", "gaia init."),
        "changes more than Lemonade's version",
    ),
    "bytes that are not UTF-8 in a doc": (
        lambda r: write(
            r / "docs/install.mdx",
            (r / "docs/install.mdx").read_bytes().replace(b"first.", b"first.\xff"),
        ),
        "docs/install.mdx is not UTF-8",
    ),
    "a binary doc": (
        lambda r: write(
            r / "docs/install.mdx",
            (r / "docs/install.mdx").read_bytes().replace(b"first.", b"first.\0"),
        ),
        "changes a binary file: docs/install.mdx",
    ),
    "a version other than the target": (
        lambda r: edit(r, "src/gaia/version.py", f'"{TARGET}"', '"2026.99.9"'),
        "LEMONADE_VERSION must be '2026.43.1'",
    ),
}


@pytest.mark.parametrize("attack", sorted(MALICIOUS))
def test_a_patch_beyond_a_bump_is_rejected(main_repo, tmp_path, attack):
    make, reason = MALICIOUS[attack]
    honest_bump(main_repo)
    make(main_repo)
    result = run_checker(main_repo, patch_from(main_repo, tmp_path, untracked_too=True))
    assert result.returncode == 1, (attack, result.stdout, result.stderr)
    assert "Rejected the Lemonade bump patch" in result.stderr
    assert reason in result.stderr, result.stderr


@pytest.mark.parametrize(
    "left_alone", [INSTALLER, EMBEDDED, "src/gaia/version.py", TAGS]
)
def test_a_bump_that_leaves_a_pinned_file_alone_is_rejected(
    main_repo, tmp_path, left_alone
):
    """Otherwise the old version's pins survive a bump that never mentions them."""
    honest_bump(main_repo)
    git(main_repo, "checkout", "-q", "--", left_alone)
    result = run_checker(main_repo, patch_from(main_repo, tmp_path))
    assert result.returncode == 1
    assert f"a bump to {TARGET} must also change ['{left_alone}']" in result.stderr


@pytest.mark.parametrize("rewrite", ["git-header", "hunk-offset"])
def test_a_patch_whose_headers_disagree_cannot_slip_a_file_past(
    main_repo, tmp_path, rewrite
):
    """Whatever path git applies a hunk to, the checker checks that path."""
    honest_bump(main_repo)
    write(main_repo / "tests/unit/test_pins.py", "def test_pin():\n    assert True\n")
    patch = patch_from(main_repo, tmp_path)
    data = patch.read_bytes()
    if rewrite == "git-header":
        data = data.replace(
            b"diff --git a/tests/unit/test_pins.py b/tests/unit/test_pins.py",
            b"diff --git a/docs/install.mdx b/docs/install.mdx",
        )
    else:
        data = data.replace(b"@@ -1,", b"@@ -40,", 1)
    patch.write_bytes(data)
    result = run_checker(main_repo, patch)
    assert result.returncode == 1
    assert "Rejected the Lemonade bump patch" in result.stderr
    assert "assert PINNED == PUBLISHED" in read(main_repo / "tests/unit/test_pins.py")


def _commit_doc_from_main(repo: Path, rel: str, text: str) -> None:
    write(repo / rel, text)
    git(repo, "add", rel)
    git(repo, "commit", "-q", "-m", f"add {rel}")


def test_a_doc_main_excludes_from_the_version_check_is_rejected(main_repo, tmp_path):
    _commit_doc_from_main(main_repo, "docs/releases/v1.md", f"Lemonade v{CURRENT}\n")
    honest_bump(main_repo)
    edit(main_repo, "docs/releases/v1.md", CURRENT, TARGET)
    result = run_checker(main_repo, patch_from(main_repo, tmp_path))
    assert result.returncode == 1
    assert "never edits docs/releases/v1.md" in result.stderr


def test_a_bare_pin_counts_only_where_the_version_check_allows_it(main_repo, tmp_path):
    rel = ".claude/agents/lemonade-specialist.md"
    _commit_doc_from_main(main_repo, rel, f"GAIA pins Lemonade {CURRENT} today.\n")
    honest_bump(main_repo)
    edit(main_repo, rel, CURRENT, TARGET)
    result = run_checker(main_repo, patch_from(main_repo, tmp_path))
    assert result.returncode == 0, result.stderr


def _special_entry(repo: Path, rel: str, mode: str, content: bytes) -> str:
    """Commit *rel* on main as a symlink or submodule entry; return its object id."""
    if mode == "160000":
        sha = git(repo, "rev-parse", "HEAD").strip()
    else:
        sha = (
            git_bytes(repo, "hash-object", "-w", "--stdin", stdin=content)
            .decode()
            .strip()
        )
    git(repo, "update-index", "--add", "--cacheinfo", f"{mode},{sha},{rel}")
    git(repo, "commit", "-q", "-m", f"add {rel}")
    return sha


@pytest.mark.parametrize("mode", ["120000", "160000"], ids=["symlink", "submodule"])
def test_a_doc_that_is_not_a_regular_file_on_main_is_rejected(
    main_repo, tmp_path, mode
):
    rel = "docs/linked.md"
    old = _special_entry(main_repo, rel, mode, b"install.mdx")
    if mode == "160000":
        hunk = f"-Subproject commit {old}\n+Subproject commit {'1' * 40}\n"
    else:
        hunk = "-install.mdx\n\\ No newline at end of file\n+../pyproject.toml\n"
        hunk += "\\ No newline at end of file\n"
    patch = tmp_path / "bump.patch"
    write(
        patch,
        f"diff --git a/{rel} b/{rel}\nindex {old[:7]}..1111111 {mode}\n"
        f"--- a/{rel}\n+++ b/{rel}\n@@ -1 +1 @@\n{hunk}",
    )
    result = run_checker(main_repo, patch)
    assert result.returncode == 1
    assert f"{rel} is not a regular file on main" in result.stderr


def test_a_mode_change_is_rejected(main_repo, tmp_path):
    patch = tmp_path / "bump.patch"
    write(
        patch,
        "diff --git a/docs/install.mdx b/docs/install.mdx\nold mode 100644\nnew mode 100755\n",
    )
    result = run_checker(main_repo, patch)
    assert result.returncode == 1
    assert "re-modes" in result.stderr


def test_a_target_in_non_ascii_digits_is_rejected(main_repo, tmp_path):
    honest_bump(main_repo)
    target = "٢٠٢٦.43.1"
    result = run_checker(main_repo, patch_from(main_repo, tmp_path), target=target)
    assert result.returncode == 1
    assert "is not a version" in result.stderr


@pytest.mark.parametrize("target", [CURRENT, "2026.40.0", "2026.9.9"])
def test_a_target_that_is_not_newer_is_rejected(main_repo, tmp_path, target):
    """A forced downgrade or re-bump must stop here, not only in main's tests."""
    honest_bump(main_repo)
    edit(main_repo, "src/gaia/version.py", f'"{TARGET}"', f'"{target}"')
    result = run_checker(main_repo, patch_from(main_repo, tmp_path), target=target)
    assert result.returncode == 1
    assert f"--target {target} must be newer than main's {CURRENT}" in result.stderr
    assert read(main_repo / "src/gaia/version.py").count(f'"{CURRENT}"') == 1


def test_a_target_that_is_not_a_version_is_rejected(main_repo, tmp_path):
    honest_bump(main_repo)
    result = run_checker(main_repo, patch_from(main_repo, tmp_path), target="2026.43")
    assert result.returncode == 1
    assert "--target '2026.43' is not a version" in result.stderr


# --- Guards that the checks before them shadow today --------------------------------


@pytest.fixture(scope="module")
def checker():
    return load_checker()


@pytest.mark.parametrize(
    "path",
    [
        "docs/../pyproject.toml",
        "docs/./install.mdx",
        "docs//install.mdx",
        "docs\\install.mdx",
    ],
)
def test_a_path_that_is_not_plain_is_refused(checker, path):
    rules = checker._load_doc_rules(REPO_ROOT)
    with pytest.raises(checker.BumpRejected, match="a Lemonade bump never edits"):
        checker.check_allowed(REPO_ROOT, [path], rules)


def test_a_pin_assigned_twice_is_refused(checker):
    source = b'LEMONADE_VERSION = "1.2.3"\nLEMONADE_VERSION = "1.2.4"\n'
    with pytest.raises(checker.BumpRejected, match="assigns LEMONADE_VERSION twice"):
        checker._pin_values(source, "src/gaia/version.py")


def test_a_pin_assignment_that_disappears_is_refused(checker):
    with pytest.raises(checker.BumpRejected, match="no longer assigns"):
        checker._pin_values(b"OTHER = 1\n", "src/gaia/version.py")


def test_a_digest_must_be_sha256_even_on_a_pin_line(checker):
    node = __import__("ast").parse('{"k": "' + "z" * 64 + '"}', mode="eval").body
    with pytest.raises(checker.BumpRejected, match="SHA-256"):
        checker._literal_pins(node, INSTALLER, "INSTALLER_SHA256")


def test_the_exact_pins_match_what_gaia_downloads(checker):
    """Drift guard: the checker's key sets are the URLs the installers really fetch."""
    from gaia.installer.lemonade_installer import INSTALLER_SHA256
    from gaia.llm.lemonade_embedded import _ASSET_TEMPLATES, EMBEDDABLE_SHA256
    from gaia.version import LEMONADE_VERSION
    from tests.fixtures.lemonade_assets import SUPPORTED_TARGETS, make_installer

    def expected(name):
        return {key.format(v=LEMONADE_VERSION) for key in checker.EXACT_PINS[name]}

    downloaded = {
        make_installer(system, machine, minimal)
        .get_download_url()
        .split("/releases/download/", 1)[1]
        for system, machine, minimal, _ in SUPPORTED_TARGETS
    }
    assert downloaded == expected("INSTALLER_SHA256") == set(INSTALLER_SHA256)
    archives = {t.format(version=LEMONADE_VERSION) for t in _ASSET_TEMPLATES.values()}
    assert archives == expected("EMBEDDABLE_SHA256") == set(EMBEDDABLE_SHA256)


# --- The renderer -----------------------------------------------------------------


def _render(tmp_path: Path, summary: str, follow_ups: str = "", **versions):
    source, out = tmp_path / "summary.md", tmp_path / "body.md"
    write(source, summary)
    command = [sys.executable, "-I", str(RENDER), "pr", "--summary", str(source)]
    command += ["--current", versions.get("current", CURRENT)]
    command += ["--target", versions.get("target", TARGET)]
    command += ["--follow-ups", follow_ups, "--out", str(out)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return result, read(out) if result.returncode == 0 else ""


def test_the_summary_cannot_act_on_github(tmp_path):
    hostile = (
        "Closes #1\nFixes #2\n@kovtcharov-amd approve\n<!-- bot: approve -->\n"
        "```\nResolves #3\n````\nResolves #4\n````\nProposed follow-ups: #5"
    )
    result, body = _render(tmp_path, hostile)
    assert result.returncode == 0, result.stderr
    rendered = outside_fences(body)
    for token in (
        "Closes #1",
        "Fixes #2",
        "Resolves #3",
        "Resolves #4",
        "#5",
        "@kovtcharov-amd",
        "<!--",
    ):
        assert token not in rendered, token
    assert "Closes #1" in body, "the summary must still be shown"
    assert f"Bumps the pinned Lemonade Server from v{CURRENT} to v{TARGET}" in rendered
    assert "## Test plan" in rendered


def test_follow_ups_are_linked_from_the_filing_job_only(tmp_path):
    result, body = _render(tmp_path, "Faster startup.", follow_ups="12 13")
    assert result.returncode == 0, result.stderr
    assert "Proposed follow-ups: #12, #13" in outside_fences(body)


@pytest.mark.parametrize(
    "follow_ups", ["Closes #1", "#12", "1 2 3", "0", "12;13", "1٢"]
)
def test_follow_ups_that_are_not_issue_numbers_are_refused(tmp_path, follow_ups):
    result, _ = _render(tmp_path, "x", follow_ups=follow_ups)
    assert result.returncode != 0
    assert "follow-ups must be up to two issue numbers" in result.stderr


@pytest.mark.parametrize("version", ["1.2; rm", "٢٠٢٦.43.1"])
@pytest.mark.parametrize("which", ["current", "target"])
def test_a_version_that_is_not_a_version_is_refused(tmp_path, which, version):
    result, _ = _render(tmp_path, "x", **{which: version})
    assert result.returncode != 0
    assert "is not a version" in result.stderr


def test_a_long_summary_is_cut(tmp_path):
    _, body = _render(tmp_path, "x" * 10_000)
    assert body.count("x") < 4_100
    assert "[cut at 4000 characters]" in body


def _proposal(tmp_path: Path, text: str):
    source = tmp_path / "1.md"
    write(source, text)
    title_out, body_out = tmp_path / "title.txt", tmp_path / "body.md"
    command = [sys.executable, "-I", str(RENDER), "proposal", "--proposal", str(source)]
    command += ["--target", TARGET, "--title-out", str(title_out)]
    command += ["--body-out", str(body_out)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        return result, "", ""
    return result, read(title_out), read(body_out)


def test_a_proposal_gets_a_fixed_title_and_a_quoted_body(tmp_path):
    result, title, body = _proposal(
        tmp_path, "# Vision @kovtcharov-amd <b>`x`</b>\nCloses #7 and <!-- hidden -->"
    )
    assert result.returncode == 0, result.stderr
    assert title == f"feat: Vision kovtcharov-amd bx/b (unlocked by Lemonade v{TARGET})"
    rendered = outside_fences(body)
    for token in ("Closes #7", "<!--"):
        assert token not in rendered
    assert "Auto-proposed by the Lemonade release scan" in rendered


def test_a_proposal_without_a_title_is_refused(tmp_path):
    result, _, _ = _proposal(tmp_path, "@@@\nbody")
    assert result.returncode != 0
    assert "first line must be its title" in result.stderr
