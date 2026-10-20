"""A featured game's quotes are the models' own words, checked against the log (ADR-0070).

Pure: `find_quote` is handed turns, so the rule about what counts as a quote is asserted without a
database. The endpoint tests in `tests/api/test_featured.py` assert it is the rule actually used.
"""

from __future__ import annotations

import pytest

from chessmark.db.digests import TurnText
from chessmark.db.featured import QUOTE_CHARS, FeatureRefusedError, find_quote

#: The real note Claude Opus 5.5 wrote after 31\u2026 Qd8, one move before it was mated. Curly
#: apostrophes, as the model wrote them.
NOTE = (
    "I played 31...Qd8 to bring my queen back to defend the king after White\u2019s Qxg6. "
    "I\u2019m in serious danger now: White\u2019s queen on g6, knight on f5 and rook on g3 all "
    "point at my king, and I made this move without checking White\u2019s replies properly. "
    "Next turn I must check Qh7, Bxh6 and Nxh6 before moving."
)


def turns() -> list[TurnText]:
    return [
        TurnText(ply=61, colour="white", reasoning=["Qxg6 opens the king."], move="Qxg6"),
        TurnText(
            ply=62,
            colour="black",
            reasoning=["I\u2019m checking whether White has   an immediate\nmating idea."],
            output=[NOTE],
            move="Qd8",
        ),
        TurnText(
            ply=63, colour="white", reasoning=["Qg8 seems to confirm checkmate."], move="Qg8#"
        ),
    ]


def test_a_quote_is_stored_as_the_record_has_it_not_as_it_was_typed() -> None:
    """Typed with a straight apostrophe; stored with the model's curly one."""
    quote = find_quote(turns(), "black", "I made this move without checking White's replies")

    assert quote == {
        "text": "I made this move without checking White\u2019s replies",
        "ply": 62,
        "san": "Qd8",
        "source": "output",
    }


def test_the_marks_a_person_adds_when_copying_are_not_part_of_the_quote() -> None:
    quote = find_quote(turns(), "white", "\u201c\u2026Qg8 seems to confirm checkmate.\u201d")

    assert quote["text"] == "Qg8 seems to confirm checkmate."
    assert (quote["ply"], quote["san"], quote["source"]) == (63, "Qg8#", "reasoning")


def test_whitespace_in_the_trace_does_not_stop_a_match() -> None:
    quote = find_quote(turns(), "black", "whether White has an immediate mating idea")

    assert quote["text"] == "whether White has an immediate mating idea"
    assert quote["source"] == "reasoning"


def test_words_the_model_never_wrote_are_refused() -> None:
    with pytest.raises(FeatureRefusedError, match="never wrote that"):
        find_quote(turns(), "black", "I made this move after checking White's replies")


def test_one_side_cannot_be_quoted_saying_the_others_words() -> None:
    """The check is per side. White's words attributed to Black is still a misquote."""
    with pytest.raises(FeatureRefusedError, match="never wrote that"):
        find_quote(turns(), "black", "Qg8 seems to confirm checkmate")


def test_the_latest_turn_that_says_it_is_the_one_quoted() -> None:
    repeated = [
        TurnText(ply=10, colour="white", reasoning=["The king is safe."], move="Kg1"),
        TurnText(ply=12, colour="white", reasoning=["The king is safe."], move="Kh1"),
    ]

    assert find_quote(repeated, "white", "The king is safe.")["ply"] == 12


def test_a_quote_is_a_line_not_a_trace() -> None:
    long = TurnText(ply=1, colour="white", reasoning=["x" * (QUOTE_CHARS + 1)], move="e4")

    with pytest.raises(FeatureRefusedError, match="under"):
        find_quote([long], "white", "x" * (QUOTE_CHARS + 1))


def test_an_empty_quote_is_refused_rather_than_matching_everything() -> None:
    """An empty string is a substring of every trace, so without this it would always "match"."""
    with pytest.raises(FeatureRefusedError, match="empty"):
        find_quote(turns(), "white", "\u201c\u2026\u201d")


def test_a_curly_apostrophe_typed_matches_a_straight_one_written() -> None:
    """The other direction: a phone or an editor curls what the model wrote straight."""
    plain = [TurnText(ply=3, colour="white", reasoning=["It's a complex situation!"], move="Bc4")]

    assert find_quote(plain, "white", "It\u2019s a complex situation!")["text"] == (
        "It's a complex situation!"
    )
