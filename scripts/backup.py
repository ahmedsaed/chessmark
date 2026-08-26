#!/usr/bin/env python3
"""Back the database up, and prove the backup can be restored.

    make backup                 # take one
    make backup ARGS=--verify   # take one, restore it to a scratch database, compare, drop it
    make backup ARGS=--list

A backup nobody has restored is a hypothesis. This restores into a throwaway database and compares
the row counts of every table against the source, because the failure that matters is not "the dump
command exited non-zero" — it is a dump that runs happily for months and turns out to be missing a
schema, or truncated, or written with a client too old for the server.

`pg_dump` runs **inside the Postgres container**, so the version always matches the server and
nothing needs installing on the host. That also means the dump is written to a mounted directory
rather than streamed, which is why `BACKUP_DIR` exists.
"""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The compose service and the credentials it was started with.
CONTAINER = "chessmark-postgres-1"
DEFAULT_USER = "chessmark"
DEFAULT_DB = "chessmark"

DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, AMBER = "\033[38;5;108m", "\033[38;5;167m", "\033[38;5;179m"


def run(command: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=capture)


def docker(*args: str, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["docker", *args], capture=capture)


def table_counts(database: str, user: str) -> dict[str, int]:
    """Row counts per table, which is what a restore has to reproduce.

    Counted rather than trusting `pg_restore`'s exit code: a restore that silently skipped a table
    exits zero and leaves a database that looks fine until the day it is needed.
    """
    sql = (
        "SELECT relname, n_live_tup FROM pg_stat_user_tables "
        "WHERE schemaname = 'public' ORDER BY relname;"
    )
    # `ANALYZE` first: `n_live_tup` is an estimate maintained by the statistics collector, and a
    # freshly restored database has never been analysed, so every count would read zero.
    docker("exec", CONTAINER, "psql", "-U", user, "-d", database, "-c", "ANALYZE;")
    result = docker(
        "exec", CONTAINER, "psql", "-U", user, "-d", database, "-t", "-A", "-F", "|", "-c", sql
    )

    counts: dict[str, int] = {}
    for line in result.stdout.splitlines():
        if "|" not in line:
            continue
        name, _, count = line.partition("|")
        counts[name.strip()] = int(count.strip() or 0)
    return counts


def take(backup_dir: Path, user: str, database: str) -> Path:
    """A compressed custom-format dump, named for the moment it was taken."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"chessmark-{stamp}.dump"

    # Custom format rather than plain SQL: it restores selectively, compresses, and `pg_restore`
    # can list its contents — which is what makes verification possible at all.
    with target.open("wb") as handle:
        subprocess.run(
            ["docker", "exec", CONTAINER, "pg_dump", "-U", user, "-d", database, "-Fc"],
            check=True,
            stdout=handle,
        )

    size = target.stat().st_size
    if size < 1024:
        raise SystemExit(f"{RED}the dump is {size} bytes — that is not a database{OFF}")
    return target


def verify(dump: Path, user: str, database: str) -> bool:
    """Restore into a scratch database and compare every table's row count with the source."""
    scratch = f"{database}_restore_check"
    source = table_counts(database, user)

    print(f"{DIM}restoring into {scratch}…{OFF}")
    docker(
        "exec",
        CONTAINER,
        "psql",
        "-U",
        user,
        "-d",
        "postgres",
        "-c",
        f'DROP DATABASE IF EXISTS "{scratch}";',
    )
    docker(
        "exec",
        CONTAINER,
        "psql",
        "-U",
        user,
        "-d",
        "postgres",
        "-c",
        f'CREATE DATABASE "{scratch}";',
    )

    try:
        with dump.open("rb") as handle:
            restore = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    CONTAINER,
                    "pg_restore",
                    "-U",
                    user,
                    "-d",
                    scratch,
                    "--no-owner",
                    "--no-privileges",
                ],
                stdin=handle,
                text=True,
                capture_output=True,
            )
        if restore.returncode != 0:
            print(f"{RED}pg_restore failed{OFF}\n{restore.stderr[:600]}", file=sys.stderr)
            return False

        restored = table_counts(scratch, user)

        missing = sorted(set(source) - set(restored))
        differing = sorted(
            name for name in source if name in restored and source[name] != restored[name]
        )

        for name in missing:
            print(f"  {RED}missing{OFF}   {name} ({source[name]} rows in the source)")
        for name in differing:
            print(f"  {RED}differs{OFF}   {name}: {source[name]} → {restored[name]}")

        if missing or differing:
            return False

        print(f"  {GREEN}{len(source)} tables restored with matching row counts{OFF}")
        return True
    finally:
        # Dropped whatever happened: a scratch database left lying around is one somebody
        # eventually mistakes for the real thing.
        docker(
            "exec",
            CONTAINER,
            "psql",
            "-U",
            user,
            "-d",
            "postgres",
            "-c",
            f'DROP DATABASE IF EXISTS "{scratch}";',
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=str(REPO_ROOT / "backups"), help="where dumps are kept")
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--verify", action="store_true", help="restore it and compare")
    parser.add_argument("--list", action="store_true", help="show what is kept")
    parser.add_argument("--keep", type=int, default=14, help="how many dumps to retain")
    args = parser.parse_args()

    backup_dir = Path(args.dir)

    if args.list:
        dumps = sorted(backup_dir.glob("chessmark-*.dump"))
        if not dumps:
            print(f"{AMBER}no backups in {backup_dir}{OFF}")
            return 0
        for dump in dumps:
            print(f"  {dump.name}  {dump.stat().st_size / 1_048_576:.1f} MB")
        return 0

    dump = take(backup_dir, args.user, args.database)
    print(f"{BOLD}{dump.name}{OFF}  {dump.stat().st_size / 1_048_576:.1f} MB")

    ok = True
    if args.verify:
        ok = verify(dump, args.user, args.database)
        print(f"{GREEN}verified{OFF}" if ok else f"{RED}VERIFICATION FAILED{OFF}")

    # Retention runs last, so a failed verification never deletes the older backup that might
    # still be good.
    if ok:
        dumps = sorted(backup_dir.glob("chessmark-*.dump"))
        for stale in dumps[: max(len(dumps) - args.keep, 0)]:
            stale.unlink()
            print(f"{DIM}pruned {stale.name}{OFF}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
