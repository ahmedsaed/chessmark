#!/usr/bin/env python3
"""Bring production's data down and put it in the local database.

    make dev-pull                        # pull, restore, migrate — the whole thing
    make dev-pull ARGS=--full            # transcripts and raw payloads too
    make dev-pull ARGS="--from backups/prod-20260914T090000Z.dump"
    make dev-pull ARGS=--keep-schema     # skip the migration step

The server is read from `CHESSMARK_SSH` in `.env`, so the command takes no arguments and there is
nothing to remember. It lives in `make` rather than `./chessmark` because `./chessmark` is the
*server's* entry point — it combines `docker-compose.prod.yml`, and running it on a development
machine would start the production stack beside the local one.

**Because the alternative is checking on production**, which is what we have been doing. The era
dropdown was built, deployed, looked at, found to be the wrong shape, and rebuilt — on the live
site, in front of whoever happened to be reading it. Every one of those steps is cheaper locally
and none of them was, because there was no way to get real data onto a laptop.

Three things this does that a seeded fixture cannot:

* **A migration runs against real rows.** `alembic upgrade head` is the last step deliberately, so
  the branch's migration meets production's data before production does. The era backfill reads
  every game's `prompt_version`; a fixture with four games would not have told us whether
  `pool-free` sorts into the eras we expect.
* **The pages get their real shapes.** 123 pairings, 21 entrants, a model with 25 games and one
  with 1 — the distributions that made the matchmaking imbalance visible at all.
* **The data is the bug report.** "It shows the old era" is a sentence; a local database where it
  shows the old era is a test.

**It drops the local database.** That is the point and it is stated here rather than discovered:
everything in the local `chessmark` database is replaced by what production has.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"

#: The Postgres container, which is named the same on both sides — one compose file, one name.
CONTAINER = "chessmark-postgres-1"
DEFAULT_DB = "chessmark"
DEFAULT_USER = "chessmark"

#: Tables whose *data* is skipped unless `--full`.
#:
#: The schema is always dumped, so nothing is missing and no foreign key is violated — these rows
#: reference games rather than the other way round. What they hold is every raw request and
#: response, every transcript row and every live event, which is the overwhelming majority of the
#: bytes and is needed by exactly two pages: the replay and the turn inspector. Pulling them turns
#: a snapshot from seconds into minutes for data most work does not touch.
HEAVY = ("llm_calls", "transcript_messages", "game_events", "tool_calls")

#: What replaces anything that identifies a person.
#:
#: Production holds real emails and Clerk ids. A dump on a laptop is a dump that can be attached to
#: an issue, committed by accident, or read by whoever borrows the machine — so the scrub happens
#: here, on the way in, rather than being remembered later. Deterministic from the row id, so a
#: user is still recognisably one user across a restore.
SCRUB = """
UPDATE users SET
    email = 'user-' || left(id::text, 8) || '@example.invalid',
    clerk_user_id = 'user_local_' || left(id::text, 8),
    display_name = COALESCE(display_name, 'Player ' || left(id::text, 8));
"""

DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, AMBER = "\033[38;5;108m", "\033[38;5;167m", "\033[38;5;179m"


def ssh_host() -> str | None:
    """The server, from the environment or `.env`.

    Read from the file rather than requiring an export, because a command that needs a variable set
    in the shell first is a command with two steps — and the second one is the one nobody
    remembers. `.env` is where every other local setting already lives, and it is gitignored, so
    the address does not end up in the repository.
    """
    from_env = os.environ.get("CHESSMARK_SSH")
    if from_env:
        return from_env

    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "CHESSMARK_SSH":
            # Both `source .env` and Compose's `env_file` strip quotes, so this does too.
            return value.strip().strip("\"'") or None
    return None


def say(message: str) -> None:
    print(message, flush=True)


def die(message: str) -> None:
    print(f"{RED}{message}{OFF}", file=sys.stderr)
    raise SystemExit(1)


def local_psql(sql: str, *, database: str = "postgres") -> str:
    """Run SQL against the local Postgres container."""
    result = subprocess.run(
        [
            "docker",
            "exec",
            CONTAINER,
            "psql",
            "-U",
            DEFAULT_USER,
            "-d",
            database,
            "-t",
            "-A",
            "-c",
            sql,
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def pull(host: str, destination: Path, *, full: bool) -> Path:
    """Stream a custom-format dump off the server.

    `pg_dump` runs **inside the server's container**, so its version matches the server's and
    nothing has to be installed on either host — the same reasoning `backup.py` uses. Credentials
    are read from inside the container too, so there is no second copy to drift.
    """
    excludes = "" if full else " ".join(f"--exclude-table-data={name}" for name in HEAVY)
    remote = (
        f"docker exec {CONTAINER} sh -c "
        f'\'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc {excludes}\''
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    say(
        f"{DIM}pulling from {host}{'' if full else ' (without transcripts and raw payloads)'}…{OFF}"
    )
    with destination.open("wb") as out:
        pulled = subprocess.run(["ssh", host, remote], stdout=out, stderr=subprocess.PIPE)
    if pulled.returncode != 0:
        destination.unlink(missing_ok=True)
        die(f"pulling the dump failed:\n{pulled.stderr.decode()[:800]}")

    # A dump that ran happily and wrote nothing is the failure worth catching (`backup.py`).
    if destination.stat().st_size < 1024:
        destination.unlink(missing_ok=True)
        die("the dump is empty — is the server's stack up?")

    size = destination.stat().st_size / 1_048_576
    say(f"  {GREEN}{destination}{OFF}  {size:.1f} MB")
    return destination


def restore(dump: Path) -> None:
    """Replace the local database with the dump.

    Dropped and recreated rather than restored over: `pg_restore --clean` leaves whatever the dump
    does not mention, so a table this branch added and production does not have would survive and
    look like it had been migrated.
    """
    say(f"{AMBER}dropping the local {DEFAULT_DB} database{OFF}")
    local_psql(f'DROP DATABASE IF EXISTS "{DEFAULT_DB}" WITH (FORCE);')
    local_psql(f'CREATE DATABASE "{DEFAULT_DB}" OWNER "{DEFAULT_USER}";')

    say(f"{DIM}restoring…{OFF}")
    with dump.open("rb") as source:
        restored = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                CONTAINER,
                "pg_restore",
                "-U",
                DEFAULT_USER,
                "-d",
                DEFAULT_DB,
                "--no-owner",
                "--no-privileges",
            ],
            stdin=source,
            capture_output=True,
            text=False,
        )
    # `pg_restore` warns about extensions and ownership it cannot reproduce and exits non-zero for
    # it, which is not a failed restore. The row counts below are what decides.
    if restored.returncode != 0:
        say(f"{DIM}pg_restore reported:{OFF} {restored.stderr.decode()[:400]}")


def scrub() -> None:
    say(f"{DIM}scrubbing anything that identifies a person…{OFF}")
    subprocess.run(
        [
            "docker",
            "exec",
            CONTAINER,
            "psql",
            "-U",
            DEFAULT_USER,
            "-d",
            DEFAULT_DB,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            SCRUB,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def migrate() -> None:
    """Run this branch's migrations against production's rows.

    **The reason to do this locally at all.** A migration meets real data here or it meets it on
    production, and only one of those can be undone by typing the command again.
    """
    say(f"{DIM}alembic upgrade head…{OFF}")
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=API_ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        die(f"the migration failed against production's data:\n{result.stderr[-1200:]}")
    for line in result.stderr.splitlines():
        if "Running upgrade" in line:
            say(f"  {GREEN}{line.split('INFO')[-1].strip()}{OFF}")


def summarise() -> None:
    rows = local_psql(
        """
        SELECT 'games', count(*) FROM games
        UNION ALL SELECT 'tournaments', count(*) FROM tournaments
        UNION ALL SELECT 'pairings', count(*) FROM tournament_games
        UNION ALL SELECT 'users', count(*) FROM users
        ORDER BY 1
        """,
        database=DEFAULT_DB,
    )
    say("")
    for line in rows.splitlines():
        name, count = line.split("|")
        say(f"  {count:>7}  {DIM}{name}{OFF}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        default=None,
        help="the server, as ssh reaches it. Defaults to CHESSMARK_SSH from the environment or .env",
    )
    parser.add_argument(
        "--from", dest="existing", type=Path, help="restore a dump already on disk instead"
    )
    parser.add_argument(
        "--full", action="store_true", help="include transcripts, raw payloads and the event log"
    )
    parser.add_argument(
        "--keep-schema",
        action="store_true",
        help="do not run migrations afterwards — the database stays exactly as production has it",
    )
    args = parser.parse_args()

    if args.existing is not None:
        dump = args.existing
        if not dump.exists():
            die(f"no dump at {dump}")
    else:
        host = args.host or ssh_host()
        if not host:
            die(
                "no server to pull from. Add CHESSMARK_SSH to .env — for example\n"
                "    CHESSMARK_SSH=ahmed@cloud-server\n"
                "or pass --host."
            )
        at = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        dump = pull(host, REPO_ROOT / "backups" / f"prod-{at}.dump", full=args.full)

    restore(dump)
    scrub()
    if not args.keep_schema:
        migrate()
    summarise()

    say(f"\n{GREEN}local database is production's{OFF}{DIM} — make api, make web{OFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
