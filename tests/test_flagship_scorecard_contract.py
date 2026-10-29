from pathlib import Path


def test_release_gate_passes_package_version_to_scorecard_gate():
    text = Path(".github/workflows/release_agent_gaia.yml").read_text(encoding="utf-8")
    start = text.index("      - name: Run scorecard gate")
    end = text.index("  # ── Stage 1c:", start)
    gate = text[start:end]
    assert gate.count("--expected-version") == 2
    assert "package.json" in gate


def test_scorecard_heading_matches_front_matter_version():
    card = Path("hub/agents/gaia/npm/SCORECARD.md").read_text(encoding="utf-8")
    assert "version: 0.1.1" in card
    assert "Eval Scorecard v0.1.1" in card


def test_readme_does_not_present_harness_card_as_product_baseline():
    readme = Path("hub/agents/gaia/npm/README.md").read_text(encoding="utf-8")
    assert "not the product baseline" in readme
