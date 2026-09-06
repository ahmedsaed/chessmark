"""The arithmetic that decides whether a request can be sent (ADR-0031, ADR-0032).

Two games sat abandoned through three resumes each, dying **one second** after being reopened. The
one second is the whole diagnosis: a summarising call to a free model averages sixteen seconds, so
nothing was called. The reactive rung had decided there was no room to write a summary and given up
without trying.

It was double-counting. `limit.requested` is what the endpoint counted in the call it refused, and
that total includes the `max_tokens` *we* asked it to reserve for the answer:

    227,440 text + 502 tool + 64,000 output = 291,942     <- what the 400 reported

Handing that on as "how full is the window" charges our own output request against the transcript a
second time, and every number downstream is 64,000 tokens too large.
"""

from __future__ import annotations

import pytest

from chessmark.agents.compaction import (
    FRAMING_TOKENS,
    MIN_USEFUL_COMPLETION,
    SUMMARY_MAX_TOKENS,
    NoRoomToAnswerError,
    Window,
)

#: The refusal that abandoned `29e7f004`, three times, to the token.
CONTEXT = 256_000
TEXT_INPUT = 227_440
TOOL_INPUT = 502
OUR_ASK = 64_000
REPORTED = TEXT_INPUT + TOOL_INPUT + OUR_ASK  # 291,942


def window(**kwargs: int) -> Window:
    return Window(context=CONTEXT, **kwargs)


class TestTheDoubleCount:
    def test_the_reported_total_includes_our_own_output_request(self) -> None:
        """The premise. If this ever stops holding, the correction below is wrong."""
        assert REPORTED == 291_942

    def test_using_the_reported_total_leaves_no_room_to_summarise(self) -> None:
        """What the code did: ask for room to write a summary against a number that already
        contains the answer we asked for. The margin is negative before the summary is considered,
        so `_summarise` returns "" without calling anything, the pass falls back to trim-only, and
        on a transcript already folded to one retained turn there is nothing left to trim."""
        with pytest.raises(NoRoomToAnswerError):
            window().completion_cap(REPORTED, SUMMARY_MAX_TOKENS)

    def test_the_prompt_alone_leaves_ample_room(self) -> None:
        """And what it should have done. The summary needs 2,000 tokens; there are more than ten
        times that free, and the game plays on."""
        prompt = REPORTED - OUR_ASK
        cap = window().completion_cap(prompt, SUMMARY_MAX_TOKENS)

        assert cap == SUMMARY_MAX_TOKENS
        assert CONTEXT - prompt - FRAMING_TOKENS > 10 * SUMMARY_MAX_TOKENS


class TestTheUnmeasuredCall:
    """What to ask for when nothing has been measured.

    Half the window was defended as a bound that "cannot be wrong in the dangerous direction",
    which is true of a game's genuine first call and false of a resumed one carrying a transcript
    of 227,440 tokens. It asked for 64,000 output against a 256,000-token endpoint and the refusal
    abandoned the game.
    """

    def test_it_holds_back_the_reserve_rather_than_half_the_window(self) -> None:
        w = window()
        assert w.unmeasured_cap(OUR_ASK) == w.headroom_needed()
        assert w.unmeasured_cap(OUR_ASK) < CONTEXT // 2

    def test_that_is_a_quarter_of_what_it_used_to_ask_for(self) -> None:
        """The concrete improvement on the window that actually failed: 25,600 rather than
        128,000, so the request is far more likely to fit before anything has been measured."""
        assert window().unmeasured_cap(OUR_ASK) == 25_600

    def test_a_small_request_is_never_inflated(self) -> None:
        """The reserve is a ceiling, not a target. Asking for less than it must stay less."""
        assert window().unmeasured_cap(5_000) == 5_000

    def test_it_never_asks_for_less_than_a_usable_answer(self) -> None:
        """A `max_tokens` too small to answer in is not a smaller request, it is one that cannot
        succeed — the mistake that forfeited a model at ply 5 (ADR-0021)."""
        assert Window(context=8_000).unmeasured_cap(OUR_ASK) >= MIN_USEFUL_COMPLETION

    def test_a_large_window_is_unaffected(self) -> None:
        """A million-token window reserves 100,000, so the endpoint ceiling still binds first and
        nothing changes for the models that were never in trouble."""
        assert Window(context=1_000_000).unmeasured_cap(OUR_ASK) == OUR_ASK


class TestTheFramingMargin:
    def test_it_is_wide_enough_to_absorb_a_tokeniser_disagreement(self) -> None:
        """256 is one thousandth of a 256,000-token window — a rounding error, and the only thing
        standing between a request sized to the exact byte and a 400 that abandons a game. The
        count that binds is taken on the provider's side, after a serialisation we never see."""
        assert FRAMING_TOKENS == 4_096

    def test_a_request_sized_to_the_cap_still_leaves_that_margin(self) -> None:
        prompt = 200_000
        cap = window().completion_cap(prompt, OUR_ASK)

        assert prompt + cap == CONTEXT - FRAMING_TOKENS


class TestTheEndpointCeilingStillBindsFirst:
    """None of the above may re-open ADR-0024: asking for more than an endpoint will emit turns
    every truncation into an unattributable one."""

    def test_a_declared_ceiling_clamps_the_ask(self) -> None:
        assert window(max_completion=32_768).completion_cap(100_000, OUR_ASK) == 32_768

    def test_it_clamps_the_unmeasured_ask_too(self) -> None:
        assert window(max_completion=8_000).completion_cap(None, OUR_ASK) == 8_000
