"""Reaction markers in chat-app replies."""

import pytest

from gaia.messaging.expression import is_single_emoji, split_reaction


@pytest.mark.parametrize("emoji", ["👍", "❤️", "🎉", "👍🏽", "👨‍💻", "1️⃣", "🇨🇦", "✅"])
def test_single_emoji_accepted(emoji):
    assert is_single_emoji(emoji)


@pytest.mark.parametrize("text", ["", "ok", ":thumbsup:", "👍👍", "a👍", "<:gaia:123>"])
def test_anything_else_rejected(text):
    assert not is_single_emoji(text)


def test_last_line_marker_is_split_off():
    assert split_reaction("Done.\n[react: ✅]") == ("✅", "Done.")
    assert split_reaction("[REACT:👍]  ") == ("👍", "")


def test_marker_mid_answer_stays_text():
    text = "Write [react: 👍] on the last line.\nThat's it."
    assert split_reaction(text) == (None, text)


def test_a_shortcode_marker_becomes_its_emoji():
    assert split_reaction("Sure.\n[react: :+1:]") == ("👍", "Sure.")
    assert split_reaction("[react: :tada:]") == ("🎉", "")
    assert split_reaction("[react: thumbs up]") == ("👍", "")


def test_an_unreadable_marker_still_acknowledges_and_never_shows():
    """A garbled marker meant "react"; posting it, or "(no answer)", would not."""
    assert split_reaction("[react: ok]") == ("👍", "")
    assert split_reaction("Sure.\n[react: <:gaia:123>]") == ("👍", "Sure.")


def test_no_marker_unchanged():
    assert split_reaction("Just text 🙂") == (None, "Just text 🙂")


def test_inline_emoji_moves_onto_the_users_message():
    from gaia.messaging.expression import take_reaction

    assert take_reaction("Nice \u2014 congrats! \U0001f389 Glad it landed.") == (
        "\U0001f389",
        "Nice \u2014 congrats! Glad it landed.",
    )


def test_an_explicit_reaction_wins_and_inline_emoji_are_still_removed():
    from gaia.messaging.expression import take_reaction

    assert take_reaction("Great \U0001f600 news\n[react: \u2705]") == (
        "\u2705",
        "Great news",
    )


def test_arrows_and_code_are_left_alone():
    from gaia.messaging.expression import take_reaction

    text = "Open Settings \u2192 Connectors and run `echo \U0001f389`."
    assert take_reaction(text) == (None, text)


@pytest.mark.parametrize(
    "text",
    [
        "Tests: ✓ unit ✗ lint",
        "☐ buy milk\n☑ call mom",
        "Rated ★★★",
        "Go to Settings ➜ Discord",
        "⚠ This deletes the folder.",
    ],
)
def test_text_symbols_are_not_emoji(text):
    from gaia.messaging.expression import take_reaction

    assert take_reaction(text) == (None, text)


def test_a_one_letter_word_before_an_emoji_gets_no_full_stop():
    from gaia.messaging.expression import take_reaction

    assert take_reaction("I ❤️ Python")[1] == "I Python"


@pytest.mark.parametrize(
    "text",
    [
        "- top\n  - nested\n    - deeper",
        "1. a\n   continued",
        "Line one  \nhard break",
        "hey  there\n\tindented",
    ],
)
def test_spacing_no_emoji_touched_is_kept(text):
    from gaia.messaging.expression import take_reaction

    assert take_reaction(text) == (None, text)


@pytest.mark.parametrize(
    "text",
    [
        "- ✅ tests pass\n- ❌ lint fails",
        "| check | result |\n|---|---|\n| unit | ✅ |\n| lint | ❌ |",
        "1. ✅\n2. ❌",
        "✅ unit\n❌ lint",
        "See https://example.com/🎉 for details",
        "Run this:\n```\nprint('🎉')",
    ],
)
def test_marks_that_carry_meaning_are_kept(text):
    from gaia.messaging.expression import take_reaction

    assert take_reaction(text) == (None, text)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("🎉 Congrats on the job!", "Congrats on the job!"),
        ("Congrats!\n\n🎉🎉", "Congrats!"),
        ("Wow 🎉🎉 yay", "Wow yay"),
        ("great 🎉, really", "great, really"),
        ("- nested\n  - deeper 🎉", "- nested\n  - deeper"),
    ],
)
def test_decoration_moves_to_the_reaction(text, expected):
    from gaia.messaging.expression import take_reaction

    assert take_reaction(text) == ("🎉", expected)
