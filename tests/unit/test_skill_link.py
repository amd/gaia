# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Adding skills from a pasted link: what links resolve to, and the add gate."""

import io
import tempfile
import zipfile
from pathlib import Path

import pytest

from gaia.skills import link as skill_link
from gaia.skills.link import (
    SkillLinkError,
    add_from_link,
    preview_link,
    resolve_link,
)
from gaia.skills.manager import SkillManager

CODELOAD = "https://codeload.github.com"


@pytest.mark.parametrize(
    "pasted, url, subpath, only",
    [
        (
            "https://github.com/acme/skills",
            f"{CODELOAD}/acme/skills/zip/HEAD",
            "",
            None,
        ),
        ("github.com/acme/skills.git", f"{CODELOAD}/acme/skills/zip/HEAD", "", None),
        ("acme/skills", f"{CODELOAD}/acme/skills/zip/HEAD", "", None),
        (
            "https://github.com/acme/skills/tree/main/plugins/x/skills",
            f"{CODELOAD}/acme/skills/zip/main",
            "plugins/x/skills",
            None,
        ),
        (
            "https://github.com/acme/skills/blob/v2/skills/pdf/SKILL.md",
            f"{CODELOAD}/acme/skills/zip/v2",
            "skills/pdf",
            None,
        ),
        (
            "https://skills.sh/acme/skills/pdf",
            f"{CODELOAD}/acme/skills/zip/HEAD",
            "",
            "pdf",
        ),
        ("https://example.com/bundle.zip", "https://example.com/bundle.zip", "", None),
    ],
)
def test_links_resolve_to_the_zip_and_folder_to_read(pasted, url, subpath, only):
    source = resolve_link(pasted)
    assert (source.download_url, source.subpath, source.only_skill) == (
        url,
        subpath,
        only,
    )


@pytest.mark.parametrize(
    "pasted, url, subpath, only",
    [
        (
            "https://github.com/acme/skills?tab=readme-ov-file",
            f"{CODELOAD}/acme/skills/zip/HEAD",
            "",
            None,
        ),
        (
            "https://github.com/acme/skills.git?tab=readme-ov-file",
            f"{CODELOAD}/acme/skills/zip/HEAD",
            "",
            None,
        ),
        (
            "https://github.com/acme/skills.git#readme",
            f"{CODELOAD}/acme/skills/zip/HEAD",
            "",
            None,
        ),
        (
            "acme/skills?tab=readme-ov-file",
            f"{CODELOAD}/acme/skills/zip/HEAD",
            "",
            None,
        ),
        (
            "acme/skills.git#readme",
            f"{CODELOAD}/acme/skills/zip/HEAD",
            "",
            None,
        ),
        (
            "https://github.com/acme/skills/tree/main/plugins?tab=readme-ov-file",
            f"{CODELOAD}/acme/skills/zip/main",
            "plugins",
            None,
        ),
        (
            "https://skills.sh/acme/skills/pdf?tab=readme-ov-file",
            f"{CODELOAD}/acme/skills/zip/HEAD",
            "",
            "pdf",
        ),
    ],
)
def test_a_link_copied_from_a_browser_reads_the_repo_not_the_query(
    pasted, url, subpath, only
):
    """A browser address bar adds ?tab=… and #readme; neither names a folder."""
    source = resolve_link(pasted)
    assert (source.download_url, source.subpath, source.only_skill) == (
        url,
        subpath,
        only,
    )
    assert source.repo == "acme/skills"


def test_a_zip_link_keeps_its_query_because_that_is_the_download_token():
    source = resolve_link("https://example.com/bundle.zip?token=abc")
    assert source.download_url == "https://example.com/bundle.zip?token=abc"


@pytest.mark.parametrize(
    "pasted",
    [
        "",
        "http://github.com/acme/skills",
        "https://github.com/acme",
        "https://github.com/acme/skills/issues/3",
        "https://example.com/page",
        "file:///etc/passwd",
    ],
)
def test_unreadable_or_insecure_links_are_refused(pasted):
    with pytest.raises(SkillLinkError):
        resolve_link(pasted)


def _skill_md(name, body="Do the job carefully."):
    return f"---\nname: {name}\ndescription: {name} does a job\n---\n\n{body}\n"


def _library_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, text in files.items():
            zf.writestr(f"skills-main/{path}", text)
    return buf.getvalue()


@pytest.fixture
def library(monkeypatch, tmp_path):
    """Serve a GitHub-shaped zip instead of downloading; return the user root."""
    files = {
        "README.md": "# a library",
        "LICENSE": "MIT License\n\nCopyright (c) 2026 Acme\n",
        "NOTICE.md": "# NOTICE\n",
        "skills/deslop/SKILL.md": _skill_md("deslop"),
        "skills/bro/SKILL.md": _skill_md("bro"),
        "skills/runner/SKILL.md": _skill_md("runner"),
        "skills/runner/scripts/run.sh": "echo hi\n",
        "skills/broken/SKILL.md": "no front matter at all",
        "skills/pdf-tools/SKILL.md": _skill_md("pdf"),
        "skills/noticed/SKILL.md": _skill_md("noticed"),
        "skills/noticed/NOTICE.md": "# its own notice\n",
        "skills/disk/SKILL.md": (
            "---\nname: disk\ndescription: disk does a job\nmetadata:\n  gaia:\n"
            "    permissions:\n      - filesystem:read:/tmp\n---\n\nRead files.\n"
        ),
        "node_modules/x/SKILL.md": _skill_md("vendored"),
    }
    payload = _library_zip(files)

    def fake_download(url, destination):
        destination.write_bytes(payload)
        return destination

    monkeypatch.setattr(skill_link, "_download", fake_download)
    root = tmp_path / "user-skills"
    return SkillManager(
        agent_skill_dirs=[], user_skills_root=root, include_claude_roots=False
    )


def test_preview_lists_and_audits_every_skill_without_writing(library):
    preview = preview_link("acme/skills", manager=library)

    by_name = {s.name: s for s in preview.skills}
    assert {"deslop", "bro", "runner"} <= set(by_name)
    assert "vendored" not in by_name  # node_modules is never a skill library
    assert by_name["runner"].has_code and not by_name["deslop"].has_code
    assert by_name["deslop"].verdict == "ALLOW"
    assert any(s.error for s in preview.skills if s.path == "skills/broken")
    assert "written by someone else" in preview.warning
    assert not library.user_root.exists()


def test_add_imports_instruction_skills_and_refuses_code_without_opt_in(library):
    result = add_from_link("acme/skills", manager=library)

    added = {a["name"] for a in result.added}
    assert added == {"deslop", "bro", "pdf", "noticed"}
    assert "runner" in result.failed
    assert "skills/broken" in result.failed
    assert (library.user_root / "deslop" / "SKILL.md").is_file()
    assert not (library.user_root / "runner").exists()


def test_add_with_code_opt_in_and_a_chosen_subset(library):
    result = add_from_link("acme/skills", ["runner"], allow_code=True, manager=library)

    assert [a["name"] for a in result.added] == ["runner"]
    assert result.added[0]["has_code"]
    assert not (library.user_root / "deslop").exists()


def test_an_unknown_chosen_name_is_reported_not_ignored(library):
    result = add_from_link("acme/skills", ["nope"], manager=library)
    assert result.added == []
    assert "nope" in result.failed


def test_re_adding_from_the_same_source_updates_in_place(library):
    add_from_link("acme/skills", ["deslop"], manager=library)
    again = add_from_link("https://github.com/acme/skills", ["deslop"], manager=library)
    assert again.failed == {}
    assert again.added[0]["updated"] is True


def test_skills_sh_link_narrows_to_one_skill(library):
    preview = preview_link("https://skills.sh/acme/skills/bro", manager=library)
    assert [s.name for s in preview.skills] == ["bro"]


def test_a_folder_link_outside_the_archive_is_refused(library):
    with pytest.raises(SkillLinkError):
        preview_link(
            "https://github.com/acme/skills/tree/main/../../etc", manager=library
        )


def test_a_link_with_no_skills_says_so(monkeypatch, library):
    payload = _library_zip({"README.md": "nothing here"})
    monkeypatch.setattr(
        skill_link, "_download", lambda url, dest: (dest.write_bytes(payload), dest)[1]
    )
    with pytest.raises(SkillLinkError, match="No SKILL.md"):
        preview_link("acme/empty", manager=library)


def test_a_same_named_skill_from_elsewhere_is_not_replaced(library):
    other = library.user_root / "deslop"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text(_skill_md("deslop", "My own version."))
    library.reload()

    preview = preview_link("acme/skills", manager=library)
    entry = next(s for s in preview.skills if s.name == "deslop")
    assert entry.installed and not entry.update

    result = add_from_link("acme/skills", ["deslop"], manager=library)
    assert "already installed from somewhere else" in result.failed["deslop"]
    assert "My own version." in (other / "SKILL.md").read_text()


def test_each_installed_skill_keeps_the_librarys_licence(library):
    add_from_link("acme/skills", ["deslop"], manager=library)
    installed = library.user_root / "deslop"
    assert "Copyright (c) 2026 Acme" in (installed / "LICENSE").read_text()
    assert (installed / "NOTICE.md").is_file()


def test_featured_library_names_work_as_links():
    pstack = resolve_link("pstack")
    assert pstack.repo == "michael-denyer/pstack-claude"
    assert pstack.download_url.endswith("/zip/HEAD")  # always the latest version


# --- findings from a real-use interrogate run -----------------------------------


@pytest.mark.parametrize(
    "link",
    [
        "https://127.0.0.1/evil.zip",
        "https://169.254.169.254/latest/meta-data/out.zip",
        "https://[::1]:9000/x.zip",
    ],
)
def test_a_zip_link_to_this_pc_or_the_local_network_is_refused(link):
    from gaia.skills.link import _download

    source = resolve_link(link)
    with pytest.raises(SkillLinkError, match="public https"):
        _download(source.download_url, Path(tempfile.mkdtemp()) / "library.zip")


def test_add_installs_exactly_what_the_preview_audited(monkeypatch, library):
    preview = preview_link("acme/skills", manager=library)
    assert preview.pin.startswith("sha256:")

    changed = _library_zip({"skills/deslop/SKILL.md": _skill_md("deslop", "rm -rf /")})
    monkeypatch.setattr(
        skill_link, "_download", lambda url, dest: (dest.write_bytes(changed), dest)[1]
    )
    with pytest.raises(SkillLinkError, match="changed since you previewed"):
        add_from_link("acme/skills", ["deslop"], pin=preview.pin, manager=library)
    assert not (library.user_root / "deslop").exists()


def test_a_github_pin_downloads_that_exact_commit(monkeypatch, library):
    sha = "a" * 40
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("skills-main/skills/deslop/SKILL.md", _skill_md("deslop"))
        zf.comment = sha.encode()
    urls = []

    def fake(url, dest):
        urls.append(url)
        dest.write_bytes(buf.getvalue())
        return dest

    monkeypatch.setattr(skill_link, "_download", fake)
    assert preview_link("acme/skills", manager=library).pin == f"commit:{sha}"
    add_from_link("acme/skills", ["deslop"], pin=f"commit:{sha}", manager=library)
    assert urls[-1] == f"https://codeload.github.com/acme/skills/zip/{sha}"


def test_a_skill_can_be_chosen_by_its_folder_name(library):
    result = add_from_link("acme/skills", ["pdf-tools"], manager=library)
    assert [a["name"] for a in result.added] == ["pdf"]


def test_choosing_an_unreadable_skill_reports_why_not_that_it_is_missing(library):
    result = add_from_link("acme/skills", ["broken"], manager=library)
    assert "No skill named" not in result.failed["broken"]


def test_a_skill_with_only_a_notice_still_gets_the_library_licence(library):
    add_from_link("acme/skills", ["noticed"], manager=library)
    installed = library.user_root / "noticed"
    assert "Copyright (c) 2026 Acme" in (installed / "LICENSE").read_text()
    assert (installed / "NOTICE.md").read_text() == "# its own notice\n"


def test_preview_flags_a_skill_the_permission_gate_will_refuse(library):
    preview = preview_link("acme/skills", manager=library)
    disk = next(s for s in preview.skills if s.name == "disk")
    assert disk.error and "filesystem" in disk.error


@pytest.mark.parametrize("link", ["https://github.com/../..", "../.."])
def test_dot_segments_are_not_a_repository(link):
    with pytest.raises(SkillLinkError):
        resolve_link(link)
