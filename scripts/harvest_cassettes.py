#!/usr/bin/env python3
"""Collect real provider responses from production and write them as cassettes.

    make harvest-cassettes                     # see what it would take, change nothing
    make harvest-cassettes ARGS=--write
    make harvest-cassettes ARGS="--write --games 60"

**The shapes we get bitten by are the ones nobody imagined.** `agents/scripted.py` exercises the
whole turn loop without a provider, which is what makes the suite free and fast — and it can only
produce shapes somebody thought to write. Every parsing failure that reached production was a shape
nobody thought to write:

* `dots-3-note-preview` framing its tool call in `<dots_function_call>`, which matched neither
  existing rule, and forfeited two games for markup its own endpoint failed to parse.
* Nex AGI phrasing a context-length refusal as *"The request is N tokens long"* where the one regex
  we had expected *"maximum context length is N"*, so the rung that exists to rescue that turn
  abstained and a game was abandoned at ply 43.
* `nemotron-3.5-lightning` returning `finish_reason: stop` with 44 tokens of multilingual noise,
  three times byte-identical, after 131 competent plies.

None of those could have been invented. All of them are now recorded, so each is found once rather
than twice — which is the whole ambition here. This does not *prevent* the next unknown shape; it
stops the known ones coming back.

**Read from the public API**, the same `/turns/{id}/raw` a reader uses, so nothing is exposed that
the site does not already publish. The stored request holds `model`, `tools`, `messages` and the
routing block — no credentials — and invariant 3 keeps it verbatim.

Shapes, not games. One cassette per distinct response *shape* rather than per turn: a hundred
recordings of a model calling `make_move` normally prove one thing a hundred times.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "apps" / "api" / "tests" / "fixtures" / "llm"

API = "https://api.chessmark.server.ahmedsaed.me"

DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, AMBER = "\033[38;5;108m", "\033[38;5;167m", "\033[38;5;179m"

#: How many turns of one game to look at.
#:
#: Walking every turn of every game is ~1,500 requests for thirty games, and it finds its last new
#: shape in the first few dozen: a model that answers one way answers that way all game. The
#: interesting turns cluster at the **ends** — illegal moves and empty replies while a model finds
#: its feet, truncations and forfeits once the transcript is long — so the sample takes both ends
#: and one from the middle rather than a prefix.
TURNS_PER_GAME = 6


#: A request carries the whole transcript, which at ply 105 is a megabyte of JSON and none of it is
#: what the cassette is for — `replay()` reads the response and nothing else. The request is kept
#: for a reader, trimmed to the parts that explain the response, and says so.
REQUEST_KEEP = ("model", "max_tokens", "tool_choice", "extra_body")
KEEP_LAST_MESSAGES = 2


def sample(turns: list[Any], count: int) -> list[Any]:
    """Both ends of a game and one from the middle."""
    if len(turns) <= count:
        return turns
    half = count // 2
    middle = len(turns) // 2
    return turns[:half] + turns[middle : middle + 1] + turns[-(count - half - 1) :]


def get(path: str) -> Any:
    with urllib.request.urlopen(f"{API}{path}", timeout=90) as response:
        return json.load(response)


def shape_of(call: dict[str, Any]) -> tuple[str, ...]:
    """What kind of response this is, for deduplication.

    Deliberately coarse. The point is to end up with one of each *kind* — reasoning with a tool
    call, prose with none, a truncation, an empty content with calls — not one per model per turn.
    The vendor is part of it because the shapes differ by vendor, which is the entire reason the
    normaliser exists.
    """
    message = ((call.get("response") or {}).get("choices") or [{}])[0].get("message") or {}
    calls = message.get("tool_calls") or []
    return (
        str(call.get("model_slug", "")).split("/")[0],
        f"finish={call.get('finish_reason')}",
        f"calls={min(len(calls), 2)}",
        "reasoning" if (call.get("reasoning_text") or message.get("reasoning_content")) else "-",
        "content" if (message.get("content") or "").strip() else "-",
    )


def interesting(call: dict[str, Any]) -> str | None:
    """A name for a shape that has already cost us a game, or `None` for an ordinary one.

    These are hunted by hand rather than left to the shape key, because each is a *failure* we have
    paid for and a cassette named `nvidia_finish_stop_calls0` would not say so to the next reader.
    """
    message = ((call.get("response") or {}).get("choices") or [{}])[0].get("message") or {}
    content = str(message.get("content") or "")
    calls = message.get("tool_calls") or []

    if "<dots_function_call>" in content or "<tool_call>" in content or "<invoke " in content:
        return "mangled_markup_in_prose"
    if call.get("finish_reason") == "length" and not calls:
        return "truncated_before_any_tool_call"
    if call.get("finish_reason") == "length" and calls:
        return "truncated_after_a_tool_call"
    if len(calls) > 1:
        return "several_tool_calls_at_once"
    if calls and not content.strip() and (call.get("reasoning_tokens") or 0) > 0:
        return "reasoning_then_tool_call_no_prose"
    return None


def trim_request(request: dict[str, Any]) -> dict[str, Any]:
    kept = {key: request[key] for key in REQUEST_KEEP if key in request}
    messages = request.get("messages") or []
    kept["messages"] = messages[-KEEP_LAST_MESSAGES:]
    kept["_trimmed"] = (
        f"{len(messages)} messages in the real request; the last {KEEP_LAST_MESSAGES} are kept. "
        "The response is verbatim — that is what `replay()` returns and what the normaliser is "
        "tested against."
    )
    return kept


def note_for(call: dict[str, Any], game: str, turn: int, why: str | None) -> str:
    detail = f"Recorded from production game {game[:8]}, turn {turn}, "
    detail += f"{call.get('model_slug')} on {call.get('provider') or 'an unnamed endpoint'}. "
    if why:
        detail += "This shape has already cost us a game — see `harvest_cassettes.py` for which. "
    return detail + (
        f"finish_reason={call.get('finish_reason')}, "
        f"{call.get('completion_tokens')} completion tokens, "
        f"{call.get('reasoning_tokens')} of them reasoning."
    )


def harvest_games(ids: list[str]) -> dict[str, dict[str, Any]]:
    """Every turn of named games, keeping only the shapes worth a name.

    The general walk samples six turns a game and stops when the shapes dry up, which is right for
    a survey and wrong for "the thing that broke last night is in game X". `27df21c2` is the case:
    the dots markup that forfeited two games happened once, in a game too old for the window, and
    it is the single most valuable recording of the lot.
    """
    out: dict[str, dict[str, Any]] = {}
    for game_id in ids:
        for turn in get(f"/games/{game_id}/turns"):
            for call in get(f"/games/{game_id}/turns/{turn['id']}/raw"):
                if not call.get("response") or call.get("error"):
                    continue
                name = interesting(call)
                if name is None or name in out:
                    continue
                out[name] = {
                    "name": name,
                    "model": call.get("model_slug"),
                    "source": "live",
                    "note": note_for(call, game_id, turn["id"], name),
                    "request": trim_request(call.get("request") or {}),
                    "response": call["response"],
                }
                print(f"  {GREEN}{name:44}{OFF} {DIM}{call.get('model_slug')}{OFF}", flush=True)
    return out


def harvest(limit: int, per_shape: int, patience: int = 8) -> dict[str, dict[str, Any]]:
    """Walk recent games until the shapes stop arriving.

    `patience` games without a new shape ends it. A corpus is not improved by a hundredth recording
    of a model calling `make_move` normally, and every request here is one the public site serves to
    anybody — worth being brief about.
    """
    games = get(f"/games?limit={limit}")
    seen: dict[tuple[str, ...], int] = {}
    out: dict[str, dict[str, Any]] = {}
    quiet = 0

    for game in games:
        if quiet >= patience:
            print(f"{DIM}no new shape in {patience} games; stopping{OFF}")
            break
        before = len(out)
        if game.get("status") not in {"finished", "aborted", "paused"}:
            continue
        try:
            turns = get(f"/games/{game['id']}/turns")
        except urllib.error.HTTPError:
            continue

        for turn in sample(turns, TURNS_PER_GAME):
            try:
                calls = get(f"/games/{game['id']}/turns/{turn['id']}/raw")
            except urllib.error.HTTPError:
                continue

            for call in calls:
                if not call.get("response") or call.get("error"):
                    continue

                why = interesting(call)
                shape = shape_of(call)
                if why is None:
                    if seen.get(shape, 0) >= per_shape:
                        continue
                    name = "_".join(p for p in shape if p != "-").replace("=", "")
                else:
                    name = why
                if name in out:
                    continue

                seen[shape] = seen.get(shape, 0) + 1
                out[name] = {
                    "name": name,
                    "model": call.get("model_slug"),
                    "source": "live",
                    "note": note_for(call, game["id"], turn["id"], why),
                    "request": trim_request(call.get("request") or {}),
                    "response": call["response"],
                }
                print(f"  {GREEN}{name:44}{OFF} {DIM}{call.get('model_slug')}{OFF}", flush=True)

        quiet = 0 if len(out) > before else quiet + 1

    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the cassettes")
    parser.add_argument("--games", type=int, default=40, help="how many recent games to walk")
    parser.add_argument(
        "--game",
        action="append",
        help="one game id, repeatable — for recording the shapes from a game that just broke "
        "rather than whatever is recent. Every turn of it is read, not a sample.",
    )
    parser.add_argument(
        "--per-shape", type=int, default=1, help="how many recordings of one ordinary shape to keep"
    )
    args = parser.parse_args()

    if args.game:
        print(f"{DIM}reading {len(args.game)} named game(s) on {API}…{OFF}")
        found = harvest_games(args.game)
    else:
        print(f"{DIM}walking the last {args.games} games on {API}…{OFF}")
        found = harvest(args.games, args.per_shape)
    existing = {path.stem for path in FIXTURES.glob("*.json")}
    fresh = {name: body for name, body in found.items() if name not in existing}

    print(f"\n{len(found)} shapes, {len(fresh)} of them new")
    if not args.write:
        print(f"{AMBER}dry run; pass --write to record them{OFF}")
        return 0

    for name, body in fresh.items():
        (FIXTURES / f"{name}.json").write_text(
            json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    print(f"{GREEN}wrote {len(fresh)} cassettes to {FIXTURES}{OFF}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
