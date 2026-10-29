from pathlib import Path

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


def test_release_gate_passes_package_version_to_scorecard_gate():
    text = _read(".github/workflows/release_agent_gaia.yml")
    start = text.index("      - name: Run scorecard gate")
    end = text.index("  #", start)
    gate = text[start:end]
    assert gate.count("--expected-version") == 2
    assert "package.json" in gate


def test_scorecard_heading_matches_front_matter_version():
    card = _read("hub/agents/gaia/npm/SCORECARD.md")
    assert f"Eval Scorecard v{_scorecard_version(card)}" in card


def test_readme_describes_a_harness_only_scorecard_as_non_baseline():
    card = _read("hub/agents/gaia/npm/SCORECARD.md")
    readme = _read("hub/agents/gaia/npm/README.md")
    assert "[`SCORECARD.md`](./SCORECARD.md)" in readme
    if "HARNESS-VALIDATION" in _scorecard_front_matter(card):
        assert "not the product baseline" in readme
