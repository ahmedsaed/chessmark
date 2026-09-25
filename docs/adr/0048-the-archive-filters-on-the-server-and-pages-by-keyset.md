# 0048. The archive filters on the server, and pages by keyset

**Status:** Accepted
**Date:** 2026-09-25

## Context

Every game was reachable only in slices: six on the lobby, a model's last two hundred on its page,
one event's on its tournament page. Nothing listed the archive, and nothing could answer "every
draw between these two models" or "the games a person has played" without a database shell (UI-12).

The site already had one searchable list. `/models` holds the whole catalogue in the browser and
filters it per keystroke with no request at all, and its exit criterion asserts that choice. The
obvious move was to do the same with the games.

Three facts about the archive make that the wrong move here:

* **It grows without bound.** A pool never ends (ADR-0041); every tick adds games. The catalogue
  is ~275 rows that change when the registry is refreshed. The archive is already larger than
  the catalogue and the gap only widens.
* **A game row is heavy.** `GameSummary` carries both seats, their tokens, costs and endpoints.
  Shipping the archive to filter it locally is shipping the archive.
* **A filtered list is something people send each other.** "Here are all the illegal-move
  forfeits" is a link, and a link needs the state to live in the URL.

## Decision

**The filters are query parameters, and the server applies them.** `/games` reads its
`searchParams`, normalises them in `lib/archive.ts`, and makes one cached, tagged read of
`GET /games` (ADR-0046) per distinct filter. The page is complete in one response, as every public
page is; nothing is fetched from the browser.

* **The form is a real `<form method="get">`.** Without JavaScript it submits every field, and
  the page redirects to the canonical spelling. With JavaScript, selects apply on change and the
  search on Enter, as a client navigation to the *same* canonical URL. One address per list:
  `/games?q=&show=played&sort=newest` and `/games` are the same list and must not be two cache
  entries.
* **Search applies on submit, not per keystroke.** Each distinct query is a server read and a cache
  entry. Per keystroke would be nine of each for a nine-letter name.
* **Anything unrecognised is dropped, not forwarded.** The cache is keyed on the query, so an
  unvalidated parameter would let any URL mint a new entry.
* **Aborted games are hidden by default: a page decision, not an API one.** An abort is a harness
  failure, not a result (invariant 11), and they were nearly half the local archive. The API keeps
  returning every status when asked for none, so "how many were aborted" stays answerable.

**Paging is keyset, and the cursor is a game id.** `before=<id>` is "the page after this game in
the current sort", resolved inside the same `SELECT` against the anchor's own `(sort value, id)`.
`after=<id>` walks the other way. The page asks for `PAGE_SIZE + 1` rows and uses the extra one to
learn whether a next page exists.

**Search is backed by a trigram index.** `ix_players_display_name_trgm`, a GIN index with
`gin_trgm_ops` on `players.display_name`, requires `pg_trgm`. That extension ships in contrib with
the official Postgres image and has been *trusted* since Postgres 13. A `%needle%` match cannot use
a B-tree, so without the index every search is a sequential scan of two rows per game ever played.

## Alternatives considered

* **Filter in the browser, as `/models` does.** Rejected for the three reasons above. It is right for
  a bounded list and wrong for one that grows every hour.
* **An `OFFSET` with numbered pages.** Numbered pages need a `COUNT(*)` for every filter. The
  offset itself shifts under the reader: a game that starts between two page loads pushes a row
  from page one onto page two, and the reader sees it twice. The archive is written to while it is
  read, so this would happen routinely.
* **A cursor that encodes the sort value** (`before=2026-09-25T…_<id>`). This makes the client
  know which column each sort uses and how to format a timestamp and a decimal. Resolving the
  anchor inside the query costs nothing extra: `test_the_archive_costs_a_fixed_number_of_queries`
  holds a paged read to the same statement count as the first page.
* **Search only models, not people.** A person's display name is already public on every game
  page they have played. The owner chose to make it searchable too.

## Consequences

* A filtered view is a URL, the back button undoes a filter, and the page works without
  JavaScript.
* The endpoint is two statements whatever it is asked: the page of games, then their seats. Every
  filter is an `EXISTS` inside the first statement, never a join and never a query of its own.
* **The cache holds one entry per filter anyone has asked for**, all tagged `games`, so every
  game that moves expires every one of them. This is cheap because each re-read is a single
  indexed statement. It would stop being cheap if the page started reading something per game.
* **`longest` and `costliest` can repeat or skip a row across pages while a game is being played.**
  A running game's ply count and cost both rise between two page loads, and a keyset cursor over a
  moving value cannot be stable. `newest`, the default, sorts on `created_at`, which never changes.
  Accepted rather than fixed, because the other two sorts are read for finished games.
* **`ENDINGS` in `lib/archive.ts` is a hand-written copy of `Termination`.** It drifted on the day
  it was written, missing six harness endings. `test_the_archive_page_offers_every_termination`
  now reads the TypeScript and fails if the two disagree.
