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
#: **`CONTEXT_EXCEEDED` joined them too (ADR-0031)**, and the reason is compaction. While the
#: agent had no way to shrink its own history, filling the window was something the model did.
#: Now that it folds its history when the window fills (ADR-0018), hitting the wall means the fold
#: did not keep up — a statement about this harness, not about the weights. It is the same
#: judgement ADR-0019 asks for everywhere else: our ceilings fail a turn, they do not forfeit a
#: model.
HARNESS_TERMINATIONS = frozenset(
    {
        Termination.PLY_CAP,
        Termination.BUDGET_EXCEEDED,
        Termination.ABANDONED,
        Termination.ADJUDICATION,
        Termination.TIMEOUT,
        Termination.TRUNCATED,
        Termination.CONTEXT_EXCEEDED,
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
    }
)


def major(version: str | None) -> str | None:
    """The part of a prompt version that says *what task this is*.

    `v2.1` and `v2` are the same task; `v3` is a different one. See `same_task`.
    """
    if version is None:
        return None
    return version.split(".", 1)[0]


def era(prompt_version: str | None, tool_schema_version: str | None) -> str:
    """The task, as one string: `"v3+v4"`.

    A pool never ends, so it cannot be replaced when the task changes — it has to carry the change
    inside itself. This is the label it carries (ADR-0043): pairings, standings and the matchmaker's
    memory of who has met whom are all scoped to one of these, and bumping either half opens a new
    one on the next tick.

    **Majors only**, so the boundary here is exactly `same_task`'s. A minor bump states the same
    task more conveniently (ADR-0038); splitting an era on one would throw away a round robin to
    record a distinction the leaderboard does not make, and the pool's table and the leaderboard
    would then be able to disagree about what counts.

    `None` on either half is `?`, which is its own era and never equal to a real one — a game from
    before the field existed measured something we cannot name.
    """
    return f"{major(prompt_version) or '?'}+{major(tool_schema_version) or '?'}"


def same_task(played: str | None, current: str | None) -> bool:
    """Whether a version `played` may be rated alongside `current`.

    Applied to the prompt version and to the tool schema version, with the same rule, because the
    question is the same one: did the task change, or only its convenience? (ADR-0042)


    **Two kinds of prompt change, and only one of them invalidates a result.**

    A *major* bump means the task changed: a new rule the model is scored against, a different tool
    surface, a different definition of winning. v1 → v2 was one of those — the automatic draw rules
    were added, and before that a model could lose half a point to a rule it had never been told
    existed (ADR-0020). A rating computed across that boundary measures neither side of it, so the
    older games leave the board.

    A *minor* bump means the same task, stated more conveniently. v2 → v2.1 added the model's own
    colour and the opponent's last move to the turn prompt — **both already free** through
    `get_board` and `get_move_history`, which is the test that makes it minor: no model can now
    know anything it could not have asked for, in the same turn, at no cost (ADR-0038). What
    changed is how many tool calls it takes to be told, and that is not what the leaderboard
    reports.

    Every game keeps its exact version in the record either way, so a reader can always separate
    them; this decides only whether a rating may span the boundary. The distinction is a judgement
    made once per change and written down in an ADR, not inferred here — the code's job is to
    honour it, and the honest risk is that "minor" is always the more convenient answer.
    """
    return major(played) == major(current)


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
    #: The tool surface the game was played under. **Recorded since the registry existed and never
    #: read here** until ADR-0042 — which is exactly how `get_legal_moves` came to hand every seat
    #: a `#` on the mating move while both versions sat still. ADR-0040 removed the `checkmate`
    #: flag and bumped both versions together, so the gap stayed theoretical for one more day.
    tool_schema_version: str | None = None
    #: Per seat: the endpoint pinned, and the endpoints that actually served it.
    pinned_providers: tuple[str | None, ...] = ()
    used_providers: tuple[tuple[str, ...], ...] = ()
    model_slugs: tuple[str, ...] = ()
    trash_talk_enabled: bool = False


def is_floating(model_slug: str) -> bool:
    """A `~vendor/model-latest` alias. Duplicated from `agents.registry` on purpose: this package
    imports nothing, and the rule is two lines."""
    return model_slug.startswith("~") or model_slug.endswith("-latest")


def judge(
    facts: GameFacts,
    *,
    prompt_version: str | None = None,
    tool_schema_version: str | None = None,
) -> Verdict:
    """Decide whether a game may move a rating.

    `prompt_version` and `tool_schema_version` are the versions ratings are currently computed for.
    A game played under an older either measured a different task and is excluded rather than
    silently mixed in (BENCH-04).

    **Both, because the prompt is only half the task** (ADR-0042). The tool surface is the other
    half, and it can change what a model knows without a word of the prompt moving: ADR-0040 took
    the `checkmate` flag out of `get_legal_moves` and left the `#` in the notation, so v3 shipped
    still naming the mating move. Fixing that removes information and changes nothing a prompt
    version could describe. The gap was recorded in ROADMAP's *Known gaps* the day before it bit.
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

    if tool_schema_version is not None and not same_task(
        facts.tool_schema_version, tool_schema_version
    ):
        return Verdict(
            False,
            f"played under tool schema {facts.tool_schema_version}, "
            f"ratings are for {tool_schema_version}",
        )

    if prompt_version is not None and not same_task(facts.prompt_version, prompt_version):
        return Verdict(
            False,
            f"played under prompt {facts.prompt_version}, ratings are for {prompt_version}",
        )

    return RATABLE
