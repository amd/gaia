# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A research report's citations are checked against what the agent really read."""

import pytest

from gaia.eval import research_quality as rq

from .conftest import cc_tool, gaia_tool

ABOUT = "https://riscv.org/about/"
FAQ = "https://riscv.org/about/faq/"
UBUNTU = "https://ubuntu.com/blog/risc-v-profiles-why-is-rva23-significant"

ABOUT_PAGE = (
    "Page: About\nURL: https://riscv.org/about/\n\nRISC-V started in May 2010 at "
    "UC Berkeley's Par Lab. The RISC-V Foundation formed in 2015 with 36 "
    "founding members, and moved to Switzerland in March 2020."
)
UBUNTU_PAGE = "Ubuntu 25.10 requires the RVA23 profile; 24.04 LTS stays on RVA20."
SEARCH = f"Web search results\n1. RISC-V FAQ\n   {FAQ}\n   snippet"


def _gaia(*calls, answer=""):
    return {"answer": answer, "conversation": [e for c in calls for e in c]}


def _read_both(answer=""):
    return _gaia(
        gaia_tool("search_web", {"query": "risc-v history"}, SEARCH),
        gaia_tool("fetch_page", {"url": ABOUT}, ABOUT_PAGE),
        gaia_tool("fetch_page", {"url": UBUNTU}, UBUNTU_PAGE),
        answer=answer,
    )


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a,b",
    [
        ("https://riscv.org/about/", "http://www.riscv.org/about"),
        ("https://riscv.org/about#history", "https://riscv.org/about/"),
        ("https://x.org/p?utm_source=a&id=3", "https://x.org/p?id=3"),
        ("https://X.org:443/p", "https://x.org/p"),
        ("https://x.org/p.", "https://x.org/p"),
    ],
)
def test_one_page_has_one_spelling(a, b):
    assert rq.normalize_url(a) == rq.normalize_url(b)


def test_different_pages_stay_different():
    assert rq.normalize_url("https://x.org/a?id=1") != rq.normalize_url(
        "https://x.org/a?id=2"
    )
    assert rq.normalize_url(ABOUT) != rq.normalize_url(FAQ)


def test_a_url_keeps_its_own_parentheses():
    text = "See [Python](https://en.wikipedia.org/wiki/Python_(programming_language))."
    assert rq.cited_urls(text) == [
        "https://en.wikipedia.org/wiki/Python_(programming_language)"
    ]


# ---------------------------------------------------------------------------
# Citations in the report
# ---------------------------------------------------------------------------


def test_inline_links_cite_their_own_sentence():
    report = (
        f"RISC-V began in 2010 ([about]({ABOUT})). Ubuntu 25.10 needs RVA23 "
        f"([Canonical]({UBUNTU})). It is popular."
    )
    found = rq.citations(report)
    assert [c.urls for c in found] == [(ABOUT,), (UBUNTU,)]
    assert found[0].sentence.startswith("RISC-V began in 2010")


def test_numbered_marks_resolve_through_the_sources_list():
    report = (
        "RISC-V began in 2010 [1]. Ubuntu needs RVA23 [2, 1].\n\n"
        f"## Sources\n1. About - {ABOUT}\n2. [Canonical]({UBUNTU})\n"
    )
    found = rq.citations(report)
    assert [c.urls for c in found] == [(ABOUT,), (UBUNTU, ABOUT)]


def test_a_range_of_marks_cites_each_source():
    report = (
        "Many sources agree [1-3].\n\n### References\n"
        f"[1]: {ABOUT}\n[2]: {FAQ}\n[3]: {UBUNTU}\n"
    )
    assert rq.citations(report)[0].urls == (ABOUT, FAQ, UBUNTU)


def test_the_sources_list_is_not_a_claim():
    report = f"Claim [1].\n\n**Sources**\n1. About {ABOUT}\n2. FAQ {FAQ}\n"
    assert len(rq.citations(report)) == 1
    assert rq.has_sources_section(report)


def test_a_body_list_item_is_still_a_claim_when_sources_are_headed():
    report = f"1. RISC-V began in 2010 ([about]({ABOUT})).\n\n## Sources\n1. {ABOUT}\n"
    assert len(rq.citations(report)) == 1


def test_a_uncited_report_has_no_citations():
    assert rq.citations("RISC-V is an open ISA. It began in 2010.") == []
    assert not rq.has_sources_section("No sources here.")


# ---------------------------------------------------------------------------
# What the agent fetched
# ---------------------------------------------------------------------------


def test_fetches_are_read_from_a_gaia_record():
    evidence = rq.tool_evidence(_read_both())
    assert set(evidence.pages) == {rq.normalize_url(ABOUT), rq.normalize_url(UBUNTU)}
    assert evidence.searches == 1 and evidence.fetches == 2
    assert rq.normalize_url(FAQ) in evidence.seen


def test_fetches_are_read_from_a_claude_code_record():
    transcript = {
        "answer": "",
        "events": cc_tool(1, "WebSearch", {"query": "q"}, SEARCH)
        + cc_tool(2, "WebFetch", {"url": ABOUT, "prompt": "p"}, ABOUT_PAGE),
    }
    evidence = rq.tool_evidence(transcript)
    assert list(evidence.pages) == [rq.normalize_url(ABOUT)]
    assert evidence.searches == 1


@pytest.mark.parametrize(
    "result",
    [
        "Error fetching page: 404 Not Found",
        "Error: Invalid extract mode",
        {"status": "error", "error": "timeout"},
        "",
    ],
)
def test_a_failed_fetch_is_not_a_page_read(result):
    evidence = rq.tool_evidence(_gaia(gaia_tool("fetch_page", {"url": ABOUT}, result)))
    assert evidence.pages == {}
    assert evidence.failed_fetches == 1


def test_a_page_read_in_parts_is_one_page():
    transcript = _gaia(
        gaia_tool("fetch_page", {"url": ABOUT}, "part one 2010"),
        gaia_tool("fetch_page", {"url": ABOUT, "offset": 5000}, "part two 2015"),
    )
    page = rq.tool_evidence(transcript).pages[rq.normalize_url(ABOUT)]
    assert "2010" in page and "2015" in page


def test_a_result_given_as_content_blocks_is_read():
    blocks = [{"type": "text", "text": ABOUT_PAGE}]
    evidence = rq.tool_evidence(_gaia(gaia_tool("fetch_page", {"url": ABOUT}, blocks)))
    assert "Par Lab" in evidence.pages[rq.normalize_url(ABOUT)]


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------


def test_figures_skip_urls_marks_and_single_digits():
    sentence = f"It formed in 2015 with 36 members and 3 boards [4] ([a]({ABOUT}))."
    assert rq.figures(sentence) == ["2015", "36"]


def test_figures_keep_decimals_and_drop_thousands_separators():
    assert rq.figures("A 6.5 m mirror cost $9,700 million.") == ["6.5", "9700"]


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

SPEC = rq.ResearchSpec(min_sources=2, min_domains=2)


def test_a_report_citing_what_it_read_passes():
    report = (
        f"RISC-V began in May 2010 at Berkeley ([about]({ABOUT})). Ubuntu 25.10 "
        f"requires RVA23 ([Canonical]({UBUNTU})).\n\n## Sources\n- {ABOUT}\n- {UBUNTU}"
    )
    check = rq.assess(report, _read_both(), SPEC)
    assert check.gate_ok, check.why
    assert (check.cited, check.cited_fetched, check.cited_domains) == (2, 2, 2)
    assert check.unsupported_figures == []
    assert check.sources_section


def test_citing_a_search_result_never_fetched_fails():
    report = (
        f"Began in 2010 ([a]({ABOUT})). RVA23 ([u]({UBUNTU})). Swiss ([faq]({FAQ}))."
    )
    check = rq.assess(report, _read_both(), SPEC)
    assert not check.gate_ok
    assert check.unread == [rq.normalize_url(FAQ)]
    assert "never fetched" in check.why


def test_an_unread_page_is_allowed_when_the_task_allows_it():
    report = (
        f"Began in 2010 ([a]({ABOUT})). RVA23 ([u]({UBUNTU})). Swiss ([faq]({FAQ}))."
    )
    spec = rq.ResearchSpec(min_sources=2, min_domains=2, max_unread=1)
    assert rq.assess(report, _read_both(), spec).gate_ok


def test_citing_a_url_found_in_no_tool_result_is_invented():
    made_up = "https://riscv.org/history-of-risc-v/"
    report = f"Began in 2010 ([a]({ABOUT})), RVA23 ([u]({UBUNTU})), ([h]({made_up}))."
    check = rq.assess(report, _read_both(), SPEC)
    assert not check.gate_ok
    assert check.invented == [rq.normalize_url(made_up)]
    assert "found in no tool result" in check.why


def test_too_few_sources_fails():
    report = f"RISC-V began in 2010 ([about]({ABOUT}))."
    check = rq.assess(report, _read_both(), SPEC)
    assert not check.gate_ok
    assert "needs 2" in check.why


def test_one_site_is_not_enough_variety():
    other = "https://riscv.org/about/faq/"
    transcript = _gaia(
        gaia_tool("fetch_page", {"url": ABOUT}, ABOUT_PAGE),
        gaia_tool("fetch_page", {"url": other}, "FAQ text"),
    )
    report = f"A ([a]({ABOUT})). B ([b]({other}))."
    check = rq.assess(report, transcript, SPEC)
    assert not check.gate_ok
    assert "span 1 site" in check.why


def test_an_empty_report_fails():
    check = rq.assess("   ", _read_both(), SPEC)
    assert not check.gate_ok and "no report" in check.why


def test_a_figure_missing_from_its_source_is_flagged_not_failed():
    report = (
        f"The Foundation formed in 2016 with 36 members ([about]({ABOUT})). "
        f"Ubuntu 25.10 requires RVA23 ([u]({UBUNTU}))."
    )
    check = rq.assess(report, _read_both(), SPEC)
    assert check.gate_ok
    assert [(f, u) for u, f, _s in check.unsupported_figures] == [
        ("2016", rq.normalize_url(ABOUT))
    ]


# ---------------------------------------------------------------------------
# Evidence for the judge
# ---------------------------------------------------------------------------


def test_the_excerpt_is_the_passage_that_matches_the_claim():
    page = (
        ("Unrelated filler text about other topics. " * 60)
        + ("The RISC-V Foundation formed in 2015 with 36 founding members. ")
        + ("More filler about nothing relevant. " * 60)
    )
    excerpt = rq.best_excerpt(page, "The Foundation formed in 2015 with 36 members.")
    assert "2015 with 36 founding members" in excerpt
    assert len(excerpt) <= rq.EXCERPT_CAP + 2


def test_evidence_pairs_each_claim_with_its_source_or_says_it_was_not_read():
    report = f"Formed in 2015 ([a]({ABOUT})). Swiss since 2020 ([f]({FAQ}))."
    text = rq.citation_evidence(report, _read_both())
    assert "36 founding members" in text
    assert "NOT FETCHED" in text


def test_evidence_is_capped():
    claims = " ".join(f"Claim {n} in 20{n:02d} ([a]({ABOUT}))." for n in range(40))
    text = rq.citation_evidence(claims, _read_both(), cap=1500)
    assert len(text) < 2500
    assert "more claims not shown" in text


def test_a_report_with_no_citations_says_so():
    assert "no citation" in rq.citation_evidence("Plain text.", _read_both())


def test_the_judge_summary_names_every_problem():
    check = rq.assess(
        f"Formed 2016 ([a]({ABOUT})). ([f]({FAQ})). ([x](https://made.up/x)).",
        _read_both(),
        SPEC,
    ).as_dict()
    text = rq.summary_for_judge(check)
    assert "faq" in text and "made.up" in text and "'2016'" in text
    assert "not run" in rq.summary_for_judge({})


# ---------------------------------------------------------------------------
# A report saved to a file
# ---------------------------------------------------------------------------


def test_report_files_are_the_saved_markdown_and_text(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "report.md").write_text("# Report\nbig " * 50, encoding="utf-8")
    (tmp_path / "notes" / "raw.txt").write_text("raw", encoding="utf-8")
    (tmp_path / "data.json").write_text("{}", encoding="utf-8")
    files = rq.collect_report_files(tmp_path)
    assert list(files) == ["report.md", "notes/raw.txt"]
    assert rq.collect_report_files(tmp_path / "missing") == {}


def test_the_report_is_the_reply_then_each_saved_file_once():
    transcript = {
        "answer": "Saved it. Short answer here.",
        rq.REPORT_FILES_KEY: {
            "r.md": f"Full report ([a]({ABOUT})).",
            "dup.md": "Short answer here.",
        },
    }
    text = rq.report_text(transcript)
    assert text.startswith("Saved it.")
    assert "saved report file: r.md" in text
    assert "dup.md" not in text
    assert rq.report_text({"answer": "only"}) == "only"


def test_a_refused_search_is_counted_apart_from_one_that_answered():
    refused = {"status": "error", "error": "Web search is temporarily unavailable"}
    transcript = _gaia(
        gaia_tool("search_web", {"query": "a"}, refused),
        gaia_tool("search_web", {"query": "b"}, SEARCH),
        gaia_tool("search_web", {"query": "c"}, 'No results found for: "c"'),
    )
    evidence = rq.tool_evidence(transcript)
    assert (evidence.searches, evidence.failed_searches) == (3, 1)
    check = rq.assess("", transcript, SPEC).as_dict()
    assert check["failed_searches"] == 1
    assert "3 (1 refused or failed)" in rq.summary_for_judge(check)


# ---------------------------------------------------------------------------
# Review findings
# ---------------------------------------------------------------------------


def test_a_percent_encoded_url_and_its_decoded_spelling_are_one_page():
    encoded = "https://en.wikipedia.org/wiki/CRISPR%E2%80%93Cas9"
    decoded = "https://en.wikipedia.org/wiki/CRISPR–Cas9"
    assert rq.normalize_url(encoded) == rq.normalize_url(decoded)
    transcript = _gaia(
        gaia_tool("fetch_page", {"url": encoded}, "Cas9 was described in 2012."),
        gaia_tool("fetch_page", {"url": ABOUT}, ABOUT_PAGE),
    )
    report = f"Cas9 in 2012 ([w]({decoded})). Began 2010 ([a]({ABOUT}))."
    check = rq.assess(report, transcript, SPEC)
    assert check.gate_ok, check.why
    assert check.invented == [] and check.unread == []


def test_a_fetched_page_is_seen_even_when_its_text_never_repeats_the_url():
    page = "https://example.org/über"
    transcript = {
        "answer": "",
        "events": cc_tool(1, "WebFetch", {"url": page, "prompt": "p"}, "Summary text."),
    }
    evidence = rq.tool_evidence(transcript)
    assert rq.normalize_url(page) in evidence.seen
    assert rq.normalize_url(page) in evidence.pages


def test_a_sentence_starting_with_sources_does_not_open_the_sources_list():
    report = (
        f"Answer ([a]({ABOUT})).\n\n"
        f"Sources disagree on the year, citing 2019 ([u]({UBUNTU})).\n\n"
        f"The chip uses 28 nm ([f]({FAQ})).\n\n## Sources\n- {ABOUT}\n"
    )
    assert len(rq.citations(report)) == 3


@pytest.mark.parametrize(
    "line",
    [
        "## Sources",
        "### References and further reading",
        "**Sources**",
        "**Sources:**",
        "Sources:",
        "Bibliography",
    ],
)
def test_sources_headings_are_recognised(line):
    assert rq.has_sources_section(f"Body.\n\n{line}\n- {ABOUT}")


def test_a_bold_heading_ends_the_sources_list():
    report = (
        f"## Sources\n1. {ABOUT}\n\n**Appendix**\n\nRVA23 is required ([u]({UBUNTU}))."
    )
    assert [c.urls for c in rq.citations(report)] == [(UBUNTU,)]


def test_markdown_footnotes_resolve_to_their_sources():
    report = f"RISC-V began in 2010.[^1]\n\n[^1]: {ABOUT}\n"
    assert [c.urls for c in rq.citations(report)] == [(ABOUT,)]


def test_a_binary_page_is_not_a_page_read():
    binary = (
        "This URL returns binary content (application/pdf, size: 9000).\n"
        "Use download_file to save it locally for analysis."
    )
    evidence = rq.tool_evidence(_gaia(gaia_tool("fetch_page", {"url": ABOUT}, binary)))
    assert evidence.pages == {} and evidence.failed_fetches == 1


def test_a_claude_code_summary_that_starts_with_error_is_still_a_read():
    transcript = {
        "answer": "",
        "events": cc_tool(1, "WebFetch", {"url": ABOUT}, "Errors in Python are..."),
    }
    assert rq.normalize_url(ABOUT) in rq.tool_evidence(transcript).pages


def test_a_source_entry_may_put_its_url_on_the_next_line():
    report = (
        "RISC-V began in 2010 [1]. Ubuntu needs RVA23 [2].\n\n## Sources\n"
        f"1. **RISC-V International, About**\n   {ABOUT}\n"
        f"2. Canonical blog\n\n3. Unrelated\n   {UBUNTU}\n"
    )
    assert [c.urls for c in rq.citations(report)] == [(ABOUT,)]
