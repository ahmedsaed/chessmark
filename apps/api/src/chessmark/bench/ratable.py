"""Which games may affect a rating (BENCH-03).

The most consequential file in Phase 12, and almost none of it is arithmetic. A rating is only as
defensible as its exclusions, and this project has spent a lot of the day discovering results that
look like measurements and are not.

**A game counts only if both models were genuinely tested and the result is reproducible.** Four
kinds of thing fail that, and all four exist in the games already recorded:

* **The harness stopped it.** `ply_cap`, `budget_exceeded`, `abandoned` — our ceiling, our budget,
  our provider. Recording those as draws would put operational decisions into the standings, and
  they are not rare: two of them turned out to be hiding a resignation and a checkmate one ply
  away, found by raising the budget and playing on.
* **The endpoint drifted.** A seat served by two providers measures a blend nothing can reproduce
  (ADR-0015). One 80-ply game did exactly this.
* **The contestant is not stable.** A floating `~model-latest` alias points at different weights
  over time, so a rating computed across it rates nothing in particular.
* **It was not a ranked configuration.** Trash talk, a persona, or a non-current prompt version all
  change what is being measured (BENCH-03, TALK-03).

A forfeit **does** count. `illegal_move_forfeit` and `error_forfeit` are the benchmark's whole
subject: a model that cannot operate its tools has lost, and hiding that would make the leaderboard
flatter and less true.
"""

from __future__ import annotations

from dataclasses import dataclass

from chessmark.game import Termination

#: Endings that say nothing about either model. Excluded from ratings — not deleted, and still
#: visible on the site, because "we stopped this game" is a fact worth being able to read.
#:
#: **`TIMEOUT` belongs here and was missed.** AGENT-17 took it out of `FORFEIT_TERMINATIONS` and
#: made it resumable, on the finding that it measured the provider and not the player — the same
#: model on two endpoints got two verdicts, and one lost a game at ply 1 having never been served a
#: completion. That change did not reach this module, so a timed-out game stopped being called a
#: forfeit and went on counting toward the rating anyway. Four sets classify a termination and
#: nothing linked them; `tests/bench/test_classification.py` now does.
#: **`TRUNCATED` joined them (ADR-0024)**, for the reason `TIMEOUT` did: it measured the endpoint.
#: An endpoint's `max_completion_tokens` is a fact about the host, the same model on a host with a
#: larger one is not cut off, and a routing lottery is not a finding.
HARNESS_TERMINATIONS = frozenset(
    {
        Termination.PLY_CAP,
        Termination.BUDGET_EXCEEDED,
        Termination.ABANDONED,
        Termination.ADJUDICATION,
        Termination.TIMEOUT,
        Termination.TRUNCATED,
    }
)

#: Endings that are a finding about a player, and therefore count.
#:
#: Forfeits are here on purpose. Agentic reliability *is* the measurement: a model that ran out of
#: illegal-move retries, or replied without ever calling a tool, has failed at the task. Excluding
#: those would leave a leaderboard that only measures chess.
RATED_TERMINATIONS = frozenset(
    {
        Termination.CHECKMATE,
        Termination.STALEMATE,
        Termination.THREEFOLD_REPETITION,
        Termination.FIFTY_MOVE_RULE,
        Termination.INSUFFICIENT_MATERIAL,
        Termination.RESIGNATION,
        Termination.AGREED_DRAW,
        # The hard backstops are real chess results: a fivefold repetition or seventy-five moves
        # without progress is a draw under the rules, not a ceiling we imposed. A model that draws
        # a won game by shuffling has told us something true about itself (ADR-0020).
        Termination.FIVEFOLD_REPETITION,
        Termination.SEVENTY_FIVE_MOVE_RULE,
        Termination.ILLEGAL_MOVE_FORFEIT,
        Termination.ERROR_FORFEIT,
        Termination.CONTEXT_EXCEEDED,
    }
)


@dataclass(frozen=True, slots=True)
class Verdict:
    """Whether a game counts, and — when it does not — a sentence saying why.

    The reason is not decoration. A methodology page that says "some games are excluded" is asking
    to be disbelieved; one that can show the count and the reason per game is not.
    """

    ratable: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ratable


RATABLE = Verdict(True)


@dataclass(frozen=True, slots=True)
class GameFacts:
    """Everything needed to judge a game, with nothing needed to fetch it.

    A plain record rather than an ORM row so the rules can be tested exhaustively without a
    database — the same reason `game/` is pure.
    """

    is_ranked: bool
    termination: Termination | None
    prompt_version: str | None
    #: Per seat: the endpoint pinned, and the endpoints that actually served it.
    pinned_providers: tuple[str | None, ...] = ()
    used_providers: tuple[tuple[str, ...], ...] = ()
    model_slugs: tuple[str, ...] = ()
    trash_talk_enabled: bool = False


def is_floating(model_slug: str) -> bool:
    """A `~vendor/model-latest` alias. Duplicated from `agents.registry` on purpose: this package
    imports nothing, and the rule is two lines."""
    return model_slug.startswith("~") or model_slug.endswith("-latest")


def judge(facts: GameFacts, *, prompt_version: str | None = None) -> Verdict:
    """Decide whether a game may move a rating.

    `prompt_version` is the version ratings are currently computed for. A game played under an
    older prompt measured a different task and is excluded rather than silently mixed in (BENCH-04).
    """
    if not facts.is_ranked:
        return Verdict(False, "not a ranked game")

    if facts.trash_talk_enabled:
        # Belt and braces: `create_match` already forces this off for ranked games, and a ranked
        # game that somehow has it on is a bug we should not average into the standings (TALK-03).
        return Verdict(False, "trash talk was enabled")

    if facts.termination is None:
        return Verdict(False, "game has not finished")

    if facts.termination in HARNESS_TERMINATIONS:
        return Verdict(False, f"stopped by the harness ({facts.termination})")

    if facts.termination not in RATED_TERMINATIONS:
        # Fail closed. A termination nobody has classified must not quietly count.
        return Verdict(False, f"unclassified termination ({facts.termination})")

    for slug in facts.model_slugs:
        if is_floating(slug):
            return Verdict(False, f"{slug} is a floating alias and has no stable identity")

    for pinned, used in zip(facts.pinned_providers, facts.used_providers, strict=False):
        if len(used) > 1:
            return Verdict(False, f"served by more than one endpoint ({', '.join(sorted(used))})")
        if pinned is not None and used and used[0] != pinned:
            return Verdict(False, f"pinned to {pinned} but served by {used[0]}")

    if prompt_version is not None and facts.prompt_version != prompt_version:
        return Verdict(
            False,
            f"played under prompt {facts.prompt_version}, ratings are for {prompt_version}",
        )

    return RATABLE
