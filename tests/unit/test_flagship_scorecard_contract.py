import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _scorecard_front_matter(card: str) -> str:
    return card.split("---", 2)[1]


def _scorecard_version(card: str) -> str:
    for line in _scorecard_front_matter(card).splitlines():
        if line.startswith("  version: "):
            return line.removeprefix("  version: ").strip()
    raise AssertionError("SCORECARD.md front matter has no agent version")


def _gaia_release_steps() -> list[dict]:
    workflow = yaml.safe_load(_read(".github/workflows/release_agent_gaia.yml"))
    return workflow["jobs"]["scorecard-gate"]["steps"]


def _step(name: str) -> dict:
    for step in _gaia_release_steps():
        if step.get("name") == name:
            return step
    raise AssertionError(f"release_agent_gaia.yml scorecard-gate has no step {name!r}")


DECIDE = "Decide whether the card must match the release version"


def test_gate_uses_the_validated_release_version():
    workflow = yaml.safe_load(_read(".github/workflows/release_agent_gaia.yml"))
    assert "version" in workflow["jobs"]["scorecard-gate"]["needs"]
    env = _step(DECIDE)["env"]
    assert env["EXPECTED_VERSION"] == "${{ needs.version.outputs.version }}"
    gate = _step("Run scorecard gate")["run"]
    assert gate.count("${VERSION_ARGS}") == 2
    assert "steps.version_check.outputs.args" in gate


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _card(version: str) -> str:
    return f"---\nschema_version: 1\nagent:\n  name: GAIA\n  version: {version}\n---\n"


def _bash() -> str | None:
    git = shutil.which("git")
    if git is None:
        return None
    if os.name == "nt":
        # System32 bash.exe is WSL, which can't see this checkout; use Git's.
        root = Path(git).parent
        for candidate in (
            root.parent / "bin" / "bash.exe",
            root.parent.parent / "bin" / "bash.exe",
        ):
            if candidate.is_file():
                return str(candidate)
        return None
    return shutil.which("bash")


@pytest.fixture
def card_repo(tmp_path):
    if _bash() is None:
        pytest.skip("needs git and a POSIX bash")
    repo = tmp_path / "repo"
    card = repo / "hub/agents/gaia/npm/SCORECARD.md"
    card.parent.mkdir(parents=True)
    card.write_text(_card("0.1.1"), encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
        "commit",
        "-q",
        "-m",
        "card",
    )
    _git(repo, "tag", "agent-pkg-gaia-v0.3.6")
    return repo, card


def _decide(repo: Path, prev: str, expected: str = "0.3.7") -> tuple[str, str]:
    step = _step(DECIDE)
    out = repo / "gh_output"
    out.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "PREV": prev,
        "EXPECTED_VERSION": expected,
        "CARD": step["env"]["CARD"],
        "GITHUB_OUTPUT": str(out),
    }
    proc = subprocess.run(
        [_bash(), "-s"],
        input=step["run"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.read_text(encoding="utf-8").strip(), proc.stdout


def _commit_all(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
        "commit",
        "-q",
        "-m",
        "next",
    )


def test_unchanged_card_warns_instead_of_blocking_the_release(card_repo):
    repo, _ = card_repo
    (repo / "other.txt").write_text("x", encoding="utf-8")
    _commit_all(repo)
    args, stdout = _decide(repo, "agent-pkg-gaia-v0.3.6")
    assert args == "args="
    assert "::warning" in stdout and "0.1.1" in stdout and "0.3.7" in stdout


def test_new_card_must_match_the_release_version(card_repo):
    repo, card = card_repo
    card.write_text(_card("0.3.7"), encoding="utf-8")
    _commit_all(repo)
    args, stdout = _decide(repo, "agent-pkg-gaia-v0.3.6")
    assert args == "args=--expected-version 0.3.7"
    assert "::warning" not in stdout


def test_new_card_with_a_stale_version_is_still_checked(card_repo):
    # A regenerated card that forgot to bump its version must not slip through.
    repo, card = card_repo
    card.write_text(_card("0.1.1") + "rerun\n", encoding="utf-8")
    _commit_all(repo)
    args, _ = _decide(repo, "agent-pkg-gaia-v0.3.6")
    assert args == "args=--expected-version 0.3.7"


def test_uncommitted_card_edit_is_checked(card_repo):
    repo, card = card_repo
    card.write_text(_card("0.3.7"), encoding="utf-8")
    args, _ = _decide(repo, "agent-pkg-gaia-v0.3.6")
    assert args == "args=--expected-version 0.3.7"


def test_first_adoption_without_a_previous_tag_is_checked(card_repo):
    repo, _ = card_repo
    args, _ = _decide(repo, "")
    assert args == "args=--expected-version 0.3.7"


def test_unresolvable_previous_tag_still_checks_the_version(card_repo):
    repo, _ = card_repo
    args, stdout = _decide(repo, "agent-pkg-gaia-v9.9.9")
    assert args == "args=--expected-version 0.3.7"
    assert "::warning" not in stdout


def test_scorecard_heading_matches_front_matter_version():
    card = _read("hub/agents/gaia/npm/SCORECARD.md")
    assert f"Eval Scorecard v{_scorecard_version(card)}" in card


def test_readme_describes_a_harness_only_scorecard_as_non_baseline():
    card = _read("hub/agents/gaia/npm/SCORECARD.md")
    readme = _read("hub/agents/gaia/npm/README.md")
    assert "[`SCORECARD.md`](./SCORECARD.md)" in readme
    if "HARNESS-VALIDATION" in _scorecard_front_matter(card):
        assert "harness-validation" in readme
        assert "not a product baseline" in readme
