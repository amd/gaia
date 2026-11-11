# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a dictation says: the form it picks, the text it speaks, the script it asks for.

Every pronunciation case below is one the Kokoro voice was heard to misread
(transcribed back with Whisper) before the rule existed.
"""

import pytest

from gaia.audio import dictation
from gaia.audio.dictation import (
    AS_WRITTEN,
    LISTENING,
    Block,
    DictationError,
    choose_form,
    document_text,
    listening_reasons,
    markdown_blocks,
    plain_blocks,
    rewrite_for_listening,
    rewrite_parts,
    select_pages,
    speakable,
    speech_pieces,
    too_long_error,
)

PROSE = (
    "[Page 1]\nThe Long Walk\nIt rained all morning, and the path by the river "
    "was\nslick with leaves. Nobody else was out.\n\n[Page 2]\nBy noon the sky "
    "had cleared."
)

# A table as pypdf extracts it: one cell per line.
REPORT = (
    "[Page 1]\nQuarterly Review\n1. Sales\nTable 1 lists units sold.\nProduct\n"
    "Q2 units\nQ3 units\nSourdough\n2,140\n3,480\nTrays\n0\n420\n"
    "Margin improved to 14%.1 See https://example.com/q3 (ref. HSB-Q3-2026).\n"
    "1 Based on the ledger."
)


# ── Choosing the form ────────────────────────────────────────────────────────


def test_plain_prose_is_read_as_written():
    assert choose_form(PROSE) == (AS_WRITTEN, [])


def test_a_report_is_rewritten_and_says_why():
    form, reasons = choose_form(REPORT)
    assert form == LISTENING
    assert reasons == [
        "tables",
        "footnotes",
        "web or email addresses",
        "reference codes",
    ]


@pytest.mark.parametrize(
    "text,reason",
    [
        ("Rows:\nNorth 12 40 7\nSouth 9 31 4\n", "tables"),
        ("| a | b |\n| 1 | 2 |\n", "tables"),
        ("Figure 3 shows the trend.\nFigure 3. Weekly sales.", "charts or figures"),
        ("It grew[1] again.", "footnotes"),
        ("Write to jane@example.com today.", "web or email addresses"),
        ("Order part QX-4471-B now.", "reference codes"),
    ],
)
def test_each_signal_is_enough_on_its_own(text, reason):
    assert listening_reasons(text) == [reason]


def test_ordinary_hyphens_and_numbers_are_not_signals():
    text = "A well-known, 30-year-old shop sold 12 loaves and 3 pies in 2026."
    assert listening_reasons(text) == []


@pytest.mark.parametrize(
    "text",
    ["COVID-19 spread fast.", "An A-1 rating.", "Chapter 12-B is short.", "X-ray it."],
)
def test_a_hyphenated_word_with_a_number_is_not_a_reference_code(text):
    assert listening_reasons(text) == []


@pytest.mark.parametrize("code", ["QX-4471-B", "HSB-Q3-2026", "INV-00123", "ISO-9001"])
def test_reference_codes_are_recognised(code):
    assert listening_reasons(f"See {code} for details.") == ["reference codes"]


@pytest.mark.parametrize("run", ["A-", "1.", "1,2", "a.b@", "$1,2", "*a", "x", "| 1 "])
def test_text_with_no_spaces_does_not_stall_the_rules(run):
    """A PDF can extract as one unbroken run; every rule must stay linear."""
    import time

    junk = run * (dictation.MAX_DICTATION_CHARS // len(run))
    started = time.monotonic()
    listening_reasons(junk)
    speech_pieces(plain_blocks(junk))
    speech_pieces(markdown_blocks(junk))
    assert time.monotonic() - started < 5


def test_an_explicit_form_is_kept_and_still_reports_what_it_found():
    assert choose_form(REPORT, "as_written")[0] == AS_WRITTEN
    assert choose_form(REPORT, "as_written")[1]
    assert choose_form(PROSE, "listening") == (LISTENING, [])


def test_an_unknown_form_is_refused():
    with pytest.raises(DictationError, match="auto, as_written, listening"):
        choose_form(PROSE, "summary")


# ── As written ───────────────────────────────────────────────────────────────


def test_page_marks_go_and_a_sentence_runs_across_the_page_break():
    blocks = plain_blocks(
        "[Page 1]\nThe path was slick and the\n\n[Page 2]\nriver was high. Done."
    )
    assert blocks == [Block("The path was slick and the river was high. Done.")]


def test_headings_stand_alone_and_wrapped_lines_join():
    assert plain_blocks(PROSE) == [
        Block("The Long Walk", heading=True),
        Block(
            "It rained all morning, and the path by the river was slick with "
            "leaves. Nobody else was out."
        ),
        Block("By noon the sky had cleared."),
    ]


def test_a_word_hyphenated_across_lines_is_rejoined():
    assert plain_blocks("It was an extra-\nordinary day.")[0].text == (
        "It was an extraordinary day."
    )


def test_table_cells_are_one_run_not_a_heading_each():
    blocks = plain_blocks(REPORT)
    assert [b.text for b in blocks if b.heading] == ["Quarterly Review", "1. Sales"]
    cells = "Product, Q2 units, Q3 units, Sourdough, 2,140, 3,480, Trays, 0, 420."
    assert Block(cells) in blocks


def test_a_page_number_at_the_edge_goes_but_a_bare_number_cell_stays():
    text = "[Page 1]\nIntro text here.\nName\nJan\nFeb\nRolls\n0\n7\n3\n\n[Page 2]\n2\nMore text."
    spoken = " ".join(b.text for b in plain_blocks(text))
    assert "Rolls, 0, 7, 3." in spoken
    assert spoken.endswith("More text.")
    assert " 2 More" not in spoken


def test_bullet_glyphs_are_not_read_out():
    # \x7f and U+F0B7 are what PDF bullet glyphs extract as.
    text = (
        "Options considered:\n\x7f Add a second till.\n Open earlier.\n- Close later."
    )
    assert plain_blocks(text) == [
        Block("Options considered: Add a second till. Open earlier. Close later.")
    ]


def test_markdown_becomes_headings_and_plain_sentences():
    script = (
        "# Title\n\nSome **bold** and *soft* text with a [link](https://x.test).\n"
        "wrapped line.\n\n- first item\n1. second item\n\n---\n\n## Next"
    )
    assert markdown_blocks(script) == [
        Block("Title", heading=True),
        Block("Some bold and soft text with a link. wrapped line."),
        Block("first item"),
        Block("second item"),
        Block("Next", heading=True),
    ]


# ── Saying it clearly ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "written,spoken",
    [
        # Heard as "three. five gigahertz" and "one. two million".
        ("It runs at 3.5 GHz.", "It runs at 3 point 5 GHz."),
        ("A p-value of 0.05.", "A p-value of 0 point 0 5."),
        ("It cost $1.2 million.", "It cost 1 point 2 million dollars."),
        ("Worth $3 billion.", "Worth 3 billion dollars."),
        ("Sales of $237.50 an hour.", "Sales of 237 dollars and 50 cents an hour."),
        ("Only $12.00 each.", "Only 12 dollars each."),
        ("It is $8,400 in all.", "It is $8,400 in all."),
        ("It is 1.5x faster.", "It is 1 point 5 times faster."),
        ("See 237.50 x 8 hours.", "See 237 point 5 0 times 8 hours."),
        # Heard as "v zero. twenty-five. four" and "hash".
        ("Released as v0.25.4.", "Released as version 0 point 25 point 4."),
        ("See section 4.2.1 there.", "See section 4 point 2 point 1 there."),
        ("Fixed in #6494.", "Fixed in number 6494."),
        # Footnote marks were read out as numbers.
        ("Margins rose.[1] Sales grew[12] too.", "Margins rose. Sales grew too."),
        ("in lost sales.1 A replacement", "in lost sales. A replacement"),
        ("It grew¹ again.", "It grew again."),
        # Heard as "E G", "A U G" and "P fourteen".
        ("Fruit, e.g. apples.", "Fruit, for example, apples."),
        ("Late, i.e., never.", "Late, that is, never."),
        ("Failed on Aug. 4 and Sep 9.", "Failed on August 4 and September 9."),
        ("See p. 14 and pp. 4-9.", "See page 14 and pages 4-9."),
        ("Table No. 4 and Fig. 2.", "Table number 4 and Figure 2."),
        ("Cats vs. dogs.", "Cats versus dogs."),
        ("Heard as one two: 1/2 a cup.", "Heard as one two: one half a cup."),
        ("About 10–12 weeks.", "About 10 to 12 weeks."),
        ("Salt & pepper, ~30% more.", "Salt and pepper, about 30% more."),
        ("See https://example.com/q3 now.", "See example.com/q3 now."),
        ("Ping 192.168.1.20 first.", "Ping 192 dot 168 dot 1 dot 20 first."),
    ],
)
def test_forms_the_voice_misreads_are_respelled(written, spoken):
    assert speakable(written) == spoken


@pytest.mark.parametrize(
    "text",
    [
        "Sold 3,480 loaves, up 18% from 2,140.",
        "Open at 6:30 a.m. on 10.11.2026.",
        "Released on 2026-11-10.",
        "Mar and Jan went home. Call 555-0100.",
        "The end. 2 more followed.",
    ],
)
def test_what_already_reads_right_is_left_alone(text):
    assert speakable(text) == text


# ── Speech pieces ────────────────────────────────────────────────────────────


def test_a_heading_opens_its_own_piece_and_ends_with_a_stop():
    blocks = [
        Block("Intro words."),
        Block("Summary", heading=True),
        Block("First point. Second point."),
        Block("Why?", heading=True),
        Block("Because."),
    ]
    assert speech_pieces(blocks) == [
        "Intro words.",
        "Summary. First point. Second point.",
        "Why? Because.",
    ]


def test_long_prose_is_split_at_sentences_without_losing_any():
    sentences = [f"Sentence number {n} is here." for n in range(40)]
    pieces = speech_pieces([Block(" ".join(sentences))], limit=200)
    assert all(len(piece) <= 200 for piece in pieces)
    assert " ".join(pieces) == " ".join(sentences)


def test_nothing_to_say_makes_no_pieces():
    assert speech_pieces([Block("¹"), Block("  ")]) == []


# ── Pages ────────────────────────────────────────────────────────────────────

THREE_PAGES = "[Page 1]\nOne.\n\n[Page 2]\nTwo.\n\n[Page 3]\nThree."


def test_a_page_range_keeps_only_those_pages():
    assert (
        select_pages(THREE_PAGES, "2-3", "a.pdf")
        == "[Page 2]\nTwo.\n\n[Page 3]\nThree."
    )
    assert select_pages(THREE_PAGES, "1", "a.pdf") == "[Page 1]\nOne."


@pytest.mark.parametrize(
    "pages,message",
    [
        ("two", "one page or a range"),
        ("3-1", "count up from 1"),
        ("0", "count up from 1"),
        ("9", "has 3 pages"),
    ],
)
def test_a_bad_page_range_says_what_is_wrong(pages, message):
    with pytest.raises(DictationError, match=message):
        select_pages(THREE_PAGES, pages, "a.pdf")


def test_pages_on_a_document_without_pages_is_refused():
    with pytest.raises(DictationError, match="no page numbers"):
        select_pages("Just text.", "1-2", "notes.txt")


def test_a_document_too_long_for_one_run_names_a_range_that_fits():
    page = "word " * 5_000
    text = "\n\n".join(f"[Page {n}]\n{page}" for n in range(1, 7))
    error = str(too_long_error(text, "book.pdf"))
    assert "about 3.3 hours of audio, longer than one dictation makes" in error
    assert "pages='1-4'" in error
    # The range it names is one the tool then accepts.
    assert len(select_pages(text, "1-4", "book.pdf")) <= dictation.MAX_DICTATION_CHARS
    assert len(select_pages(text, "1-5", "book.pdf")) > dictation.MAX_DICTATION_CHARS


def test_a_later_range_that_is_still_too_long_starts_from_its_own_first_page():
    page = "word " * 5_000
    text = "\n\n".join(f"[Page {n}]\n{page}" for n in range(1, 13))
    rest = select_pages(text, "5-12", "book.pdf")
    assert "pages='5-8'" in str(too_long_error(rest, "book.pdf"))


def test_too_long_text_without_pages_asks_which_part():
    error = str(too_long_error("word " * 60_000, "book.txt"))
    assert "pages=" not in error
    assert "which part" in error


# ── Listening rewrite ────────────────────────────────────────────────────────


def test_whole_pages_are_grouped_into_parts_under_the_limit():
    text = "\n\n".join(f"[Page {n}]\n" + ("word " * 100) for n in range(1, 7))
    parts = rewrite_parts(text, limit=1100)
    assert len(parts) == 3
    assert all(len(part) <= 1100 for part in parts)
    assert sum(part.count("word") for part in parts) == 600


def test_one_oversized_page_is_cut_between_lines():
    lines = [f"Line {n} of the page." for n in range(200)]
    parts = rewrite_parts("\n".join(lines), limit=500)
    assert all(len(part) <= 500 for part in parts)
    assert "\n".join(parts).split("\n") == lines


def test_each_part_is_rewritten_in_order_and_told_where_the_script_stands():
    text = "\n\n".join(f"[Page {n}]\n" + ("word " * 100) for n in range(1, 4))
    asked = []

    def ask(system, prompt):
        asked.append((system, prompt))
        return f"## Part {len(asked)}\n\nScript for part {len(asked)}."

    seen = []
    script = rewrite_for_listening(
        text, "report", ask, lambda i, n: seen.append((i, n)), limit=600
    )
    assert seen == [(1, 3), (2, 3), (3, 3)]
    assert all(system == dictation.LISTENING_SYSTEM_PROMPT for system, _ in asked)
    assert 'part 1 of 3 of the document "report"' in asked[0][1]
    assert "Open with one sentence" in asked[0][1]
    assert "Script for part 1." in asked[1][1]
    assert "no greeting" in asked[1][1]
    assert script == (
        "## Part 1\n\nScript for part 1.\n\n## Part 2\n\nScript for part 2.\n\n"
        "## Part 3\n\nScript for part 3.\n"
    )


def test_an_empty_reply_fails_the_rewrite_instead_of_leaving_a_gap():
    replies = iter(["Good start.", "   "])
    text = "[Page 1]\n" + "word " * 100 + "\n\n[Page 2]\n" + "word " * 100
    with pytest.raises(DictationError, match="part 2 of 2"):
        rewrite_for_listening(text, "report", lambda s, p: next(replies), limit=600)


# ── Reading the document ─────────────────────────────────────────────────────


def test_text_and_markdown_files_are_read_as_they_are(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("# Title\n\nBody.", encoding="utf-8")
    assert document_text(str(notes)) == ("# Title\n\nBody.", {}, True)
    plain = tmp_path / "notes.txt"
    plain.write_text("Body.", encoding="utf-8")
    assert document_text(str(plain)) == ("Body.", {}, False)


def test_an_unsupported_or_empty_file_is_refused_by_name(tmp_path):
    slides = tmp_path / "deck.pptx"
    slides.write_bytes(b"PK")
    with pytest.raises(DictationError, match=r"deck\.pptx is not a document GAIA"):
        document_text(str(slides))
    empty = tmp_path / "empty.txt"
    empty.write_text("  \n", encoding="utf-8")
    with pytest.raises(DictationError, match="no text to dictate"):
        document_text(str(empty))
