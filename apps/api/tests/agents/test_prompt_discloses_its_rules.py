"""A rule that decides a game must be stated in the prompt (invariant 12, ADR-0040).

ADR-0020 established this the hard way: a model a queen and a knight up drew by threefold at ply
100, having never been told the rule existed. The prompt was fixed for the draw rules and the same
class of omission was left standing one paragraph above.

`MAX_NUDGES` is 3, so a **fourth** reply with no tool call forfeits the game. The prompt stated the
illegal-move forfeit in full — *"if you fail more than 5 times in a single turn, you forfeit"* — and
said nothing whatever about silence. `1815a53f` ended `0-1` on it at ply 176, rated, against a model
that had played 131 competent plies first.

These are content assertions on a versioned artefact, which is exactly the point: if someone edits
the disclosure away, `PROMPT_VERSION` and this file should both have to move.
"""

from __future__ import annotations

from chessmark.agents import prompts
from chessmark.agents.tools import ToolName
from chessmark.game import Colour


def system_prompt(*, trash_talk_enabled: bool = False) -> str:
    return prompts.build_system_prompt(
        colour=Colour.WHITE,
        opponent="black-model",
        max_illegal_retries=5,
        max_nudges=3,
        trash_talk_enabled=trash_talk_enabled,
    )


class TestTheSilenceForfeitIsDisclosed:
    def test_the_prompt_says_that_silence_forfeits(self) -> None:
        prompt = system_prompt().lower()

        assert "without calling any tool" in prompt
        assert "forfeit" in prompt

    def test_it_names_the_number_it_is_enforced_at(self) -> None:
        """A threshold nobody can see is not a disclosure. It is interpolated from `MAX_NUDGES`
        rather than typed, so the prompt cannot drift away from the code that enforces it."""
        assert "3 attempts" in prompt_with_nudges(3)
        assert "5 attempts" in prompt_with_nudges(5)

    def test_the_nudge_says_what_it_costs(self) -> None:
        """The repeated-call nudge has always named the stake; this one sat in front of the forfeit
        that actually ended a game and did not."""
        assert "2 more attempts" in prompts.nudge_prompt(remaining=2)
        assert "1 more attempt" in prompts.nudge_prompt(remaining=1), "and it counts in English"


def prompt_with_nudges(max_nudges: int) -> str:
    return prompts.build_system_prompt(
        colour=Colour.WHITE,
        opponent="black-model",
        max_illegal_retries=5,
        max_nudges=max_nudges,
        trash_talk_enabled=False,
    )


class TestTheDrawRulesAreDisclosed:
    def test_a_draw_by_agreement_names_both_tools(self) -> None:
        """Added in v3 along with `accept_draw`. Offering has always been in the schema; accepting
        had no tool, so the ending was unreachable between two models (ADR-0040)."""
        prompt = system_prompt()

        assert f"`{ToolName.OFFER_DRAW}`" in prompt
        assert f"`{ToolName.ACCEPT_DRAW}`" in prompt

    def test_the_claimable_pair_is_still_disclosed(self) -> None:
        """ADR-0020's half of this, which must not be lost while editing around it."""
        prompt = system_prompt().lower()

        assert "threefold" in prompt
        assert "fifty-move" in prompt
        assert f"`{ToolName.CLAIM_DRAW}`" in system_prompt()


class TestItNamesNoToolTheModelCannotSee:
    def test_a_ranked_prompt_does_not_mention_say(self) -> None:
        """It used to say "do not use the `say` tool; it is disabled" — but `say` is already absent
        from a ranked game's schema, so that sentence introduced a tool the model could not see.
        That is how an invented tool call gets made, and an invented tool call is a step toward a
        forfeit.

        Matched as a **backticked tool reference**, not the bare word: the prompt says "say a
        sentence about your plan" elsewhere, and an assertion that cannot tell that from naming the
        tool would fire on ordinary prose.
        """
        assert f"`{ToolName.SAY}`" not in system_prompt(trash_talk_enabled=False)

    def test_an_unranked_prompt_still_explains_it(self) -> None:
        assert f"`{ToolName.SAY}`" in system_prompt(trash_talk_enabled=True)


class TestTheMoveInstructionMatchesTheHarness:
    def test_it_does_not_tell_a_model_to_end_its_turn_by_moving(self) -> None:
        """ADR-0037 made a turn end when the model stops, not when it moves, and the prompt still
        said *"you must end by calling `make_move`"*. Models read that as "keep going, then move
        again": across `9450f060` the black seat called `make_move`, got its `fen`, read the board
        and called `make_move` again — every turn, answered `already_moved` every time."""
        prompt = system_prompt()

        assert "end by calling" not in prompt
        assert "already_moved" in prompt, "and it says what a second call actually does"
