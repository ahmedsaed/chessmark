# Changelog

Notable changes, newest first. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Chessmark is in beta.** That is what `0.x` means under SemVer — *"anything MAY change at any
time; the public API SHOULD NOT be considered stable"* — so the version carries it and the tags do
not repeat it. Releases are published normally rather than marked pre-release: there is no stabler
version to point people at, and a repository whose every release is a pre-release advertises no
current version at all.

This file starts at `0.1.0`. Everything before it is in the git history and, where it was a
decision rather than a change, in [docs/adr](docs/adr/) — an ADR is the record of *why*, and this
file is only the record of *what shipped when*.

## [Unreleased]

### Changed

- **Credit is dollars, spent at what each turn actually cost.** A game used to cost one to six
  credits when it started, by the price band of its models. That could not become money: two games
  in one band cost an order of magnitude apart, and a resignation cost as much as a long game. Now
  a balance is US dollars, each model turn is charged its real cost as it is played, and starting a
  paid game needs only a balance above zero. When credit runs out, a game pauses before its next
  turn and resumes once more is added. A person playing a free model needs no credit at all.
  Existing balances were reset to zero. The picker shows each model's price band as `$`–`$$$$`,
  and tournament fields select on it with `--min-tier`/`--max-tier`, replacing
  `--min-credits`/`--max-credits`. `./chessmark credits` grants dollars. The header balance
  refreshes when a model move in your own game is charged, and when you return to the tab. It does
  not poll. (AUTH-10, AUTH-11,
  AUTH-13, [ADR-0052](docs/adr/0052-credit-is-dollars-spent-at-actual-cost.md))
- **Decision models choose what to do with their turn, and are checked before they play.** Resign,
  offer, accept and claim are now one choice beside the move, not four yes/no questions gated at a
  fixed number. The same answer meant different things on different models, so no single gate was
  fair. Ending the game takes a majority of the model's own ranking, and the timeline says when that
  overruled its first choice. A newly listed decision model is asked one request in a turn's shape
  when the catalogue refreshes, and only one that can answer is offered: the Span models, which
  accept only yes/no questions, are recorded as refused rather than seated. The decision harness is
  now `d2`, a new era for decision tournaments. `./chessmark deploy` now restarts the catalogue
  service, which it never had. (AGENT-24, AGENT-26,
  [ADR-0051](docs/adr/0051-a-decision-model-chooses-its-action-and-is-checked-before-it-plays.md))

### Added

- **Research on selling credit without a company**, in
  [docs/PAYMENTS.md](docs/PAYMENTS.md): a merchant of record would let a person sell worldwide and
  be paid out in Egypt. Nothing is built.

### Fixed

- **A tournament's entrant count matches its table.** It counted every entrant ever seated,
  withdrawn ones included, so the Decision Cup said "4 entrants" above a table of two. Both the
  list and the event page now count the rows the standings show: models still in the field, and
  ones that left after playing.

## [0.5.0] — 2026-09-26

**Decision models play, every game has an archive, and a pool can stop.** 39 commits since
`v0.4.0`. OpenRouter's decision models join the leaderboard through a harness of their own, `/games`
files every game behind filters that are each a link, and a pool can be told how many games a pair
owes it. Around those: pages that arrive whole instead of assembling behind skeletons, our own auth
screens with Clerk off every page that does not need it, a lobby rebuilt around a podium and one
real turn, and every public page at 100 for accessibility against production's data.

Three migrations, all additive: `runtime` and `decision_version` columns, a nullable
`games_per_pair`, and a trigram index on `players.display_name`, which creates `pg_trgm`.

### Added

- **A pool can stop.** `--games-per-pair N` gives a pool a target: each pair plays N decided games
  per era, and once every pair has, the pool idles until a new model is admitted. Then it plays
  only the newcomer's pairs. Set it on a running pool with `tournament set <slug> --games-per-pair
  N` (`0` clears it). Abandoned games don't count, and the tournament page says when a pool is idle
  for this reason. (BENCH-14, [ADR-0050](docs/adr/0050-a-pool-saturates-per-pair.md))
- **Decision models play.** OpenRouter's decision models — TypeSafe's Jev 1.13 and Kev 4B today —
  are registered from their own catalogue listing and play through the Decisions API: one request
  per turn with the position and every legal move described, and a probability back for each. They
  resign, offer, accept and claim draws (threefold and fifty moves) as a chat model can, through
  gates measured with `make probe-decisions`. They share the leaderboard, marked with a badge
  wherever a model is named, and get tournaments of their own (`make tournament … --decision`).
  The timeline draws each decision as the moves it weighed, withheld from a person mid-game like
  reasoning. The decision harness has its own version, `d1`, and a game is held only to the
  versions of the harnesses that played in it. `make play`, `make worker` and the browser seed all
  serve decision seats; `--scripted` spends nothing. (AGENT-23..25, BENCH-13,
  [ADR-0049](docs/adr/0049-decision-models-play-through-their-own-harness.md))
- **An archive of every game, at `/games`.** Filter by status, result, how the game ended,
  ranked or not, model against model or a person at the board, a model, a matchup, or an event.
  Search either seat's name or its model's OpenRouter id. Every filtered view is a link. Aborted
  games are harness failures rather than results, so they are hidden until asked for. "Load more"
  appends the next page in place, walked by keyset so a game starting mid-read never repeats a
  row. A filtered link unfurls as that filter, with its own title, description and a card of
  the games it matches. Only the archive and its one-model and one-event views are indexed, and
  the search is described for browsers at `/opensearch.xml`. `GET /games` gains the same filters
  and stays two statements whatever it is asked. The name search is backed by a trigram index,
  which needs `pg_trgm`; the migration creates it. Linked from the header, the lobby, each model
  and each tournament. (UI-12, [ADR-0048](docs/adr/0048-the-archive-filters-on-the-server-and-pages-by-keyset.md))

- **An account menu in the header, and a profile worth visiting** (UI-09, HUMAN-03). The header
  showed a bare `profile` link: neither who held the session nor a way out of it. It is now a
  picture, a name and a menu with the two things a person wants — the profile, and signing out.

  Built from `useUser` and `useClerk`, not from `<UserButton />`. A prebuilt Clerk component
  anywhere puts 285 KiB of `@clerk/ui` on every route, so the menu being ours is what keeps
  `/about` free of it. The balance appears exactly once at any width: the header chip above `sm`,
  the menu below it, because discovering your allowance by being refused is a bad way to learn it
  (ADR-0016) and twice on one screen reads as two numbers.

  `/profile` gained a record and every game behind it, in the shapes the model page already uses —
  a person holding a seat is a player, and the page describing one should not be a different kind
  of page. The record counts **decided games only**: a game the harness stopped is not a draw and
  not a loss (invariant 11), so it is counted apart rather than folded into either, and the four
  columns are asserted to add up to the total. No new endpoint — `/games/mine` already returned
  every field the arithmetic needs, and a second place to compute W/D/L is a second answer.

  **It re-introduces a trap that had been deleted.** The account control is a collapsed disclosure
  that sorts first in the document on every signed-in page, so a selector written against the first
  `aria-expanded="false"` finds it and not the turn it meant. `docs/TESTING.md` carries it again.

- **The auth screens are asserted, not eyeballed** (UI-09). `account.spec.ts` walks a throwaway
  identity through sign-up, the profile, a display name, signing out and signing back in — the two
  bugs above are what it found on the day it was written. A fresh Clerk user rather than the
  suite's own: `signOut` ends the sessions of the *client*, and every context restores the same
  client cookie, so signing out on the shared account would revoke the session the play tests are
  still using. It is deleted again afterwards.

  `auth.setup.ts` does not cover any of this. It drives `window.Clerk` directly, which is the right
  way to get a session for the play tests and goes nowhere near our form.

- **Our own sign-in, sign-up and profile** — and `/profile` is a page the site did not have. Clerk's
  `<UserButton />` knew nothing about credits, spend or games, which are the three things a person
  comes to an account page to check.

- **Lighthouse budgets run in CI** (NFR-12). The ROADMAP gap said "no Lighthouse in this
  environment"; that was stale — Chrome is installed and it runs. So it was run, and Phase 7's
  unverified exit criterion turned out to be **failing on both halves**: performance 87 and
  accessibility 88 against a target of 90.

  **What the suite refuses to assert is the design.** This repository has already deleted two
  wall-clock tests, and ROADMAP says why: a timing assertion that fails on a busy machine and
  passes on a quiet one teaches people to rerun CI, and the next real regression is rerun away with
  it. A Lighthouse performance score is that assertion wearing a different hat. So accessibility,
  best practices, SEO, contrast, accessible names, tap targets and page weight are **asserted** —
  all DOM and network facts, identical on a loaded machine — while the performance score and every
  timing metric are **recorded and uploaded, never gated**.

  It measures a build with **no Clerk**, deliberately: a development tenant is 370 KiB, 55% of the
  page, plus a 1.8 s handshake and a console error, and best practices measured 100 on production
  against 74 locally for reasons entirely outside this repository.

- **A social card for every page** (UI-06). Seven routes had **no `og:image` at all** —
  `/leaderboard`, `/models`, `/about`, `/methodology`, `/play` and both tournament routes unfurled
  as bare text links anywhere they were shared.

  `pageMetadata` did it. Metadata keys are inherited wholesale, so setting `openGraph` replaces the
  parent's whole block — including the `images` that `app/opengraph-image.tsx` injects through the
  file convention. The helper written to stop every page sharing the root's card deleted the card
  instead. `/sign-in` is what identified the mechanism: it sets no metadata, replaced nothing, and
  was the only page besides `/` that kept an image.

  The new cards show the page rather than the site: the leaderboard's is the top five with their
  ratings, a tournament's is its table and what the event cost, a model's is its rating, its record
  and a position from one of its own ranked games — a board only when there is a real game behind
  it, because a card about one player showing a board is read as *that player's* game.

  Three constraints found by hitting them, all now in [FRONTEND.md](docs/FRONTEND.md): a card
  cannot live inside a catch-all segment, so the model's is a Route Handler at `/og/model/<slug>`;
  a config redirect runs before routing and `/leaderboard/:slug → /models/:slug` was serving the
  *models* card for the leaderboard, 200 and valid PNG and the wrong picture; and the two cards
  that already existed had drifted onto different palettes.

  Cards revalidate on one shared clock rather than rendering per request. `site.spec.ts` asserts
  every public route has exactly one `og:image` and that each renders **without following a
  redirect** — following one lands on a perfectly good card and proves nothing.

### Changed

- **"Recent games" is gone from the lobby.** It and the replay row were two answers to one
  question: both listed finished games, one at random with the clean endings and a board playing
  itself, the other in time order as text. The only thing the second carried alone was a game that
  ended badly — a ply cap, a forfeit, an abandonment — and `/leaderboard` already lists every game
  the ranking excluded, grouped by reason and linked. 250px of page, and one fewer band saying
  something the one above it had said.
- **Three questions answered at the foot of the lobby**, each in a sentence with a door to the page
  that owns the long version: whether the games are real, why a model is not on the board, and
  whether the numbers can be checked. Deliberately not an FAQ — `/about` and `/methodology` hold
  nine sections between them, and a second copy on the lobby is one nobody remembers to update. A
  browser test follows all three links, because a strip whose whole design is *not repeating* those
  pages breaks silently the day one is renamed.
- **One real turn, on the front page.** The lobby claimed every request, reasoning trace and tool
  call was recorded and then showed nothing but boards and numbers. It now shows a turn from a
  finished game: what the model thought, how long for and in how many tokens, the tool it called,
  the move the referee refused if there was one, and the move it played. Folded by `foldEvents` —
  the same function the game page's conversation is built from — so it cannot drift into a shape
  the real view never produces.

  **A bounded, cached read of 40 events, which is a measurement rather than a guess**: on
  `21d2867b` the log costs 14 KB at 40 events, 90 KB at 120 and **728 KB** at 300, because
  reasoning text dominates it. `listEvents` follows the cursor to the end and never caches, which
  is right for the game page and wrong for a quotation.

  The first build of this put `</role>` on the front page — two tokens of "reasoning" that were a
  fragment of a provider's own prompt template, picked because the rule was "non-empty". A thought
  now has to be at least 120 characters, and `spotlight.test.ts` has that turn in it.
- **The lobby invites you to play, and says what you are up against.** A section under the
  tournaments: on one side the whole human-versus-model record as a scoreboard, with the caption
  this project would insist on — *one game. provisional, obviously* — and on the other what the
  opponent has been caught doing in ranked games. Every line is measured, so the section is funny
  for exactly as long as the models keep being bad at chess: 375 illegal moves attempted in 19
  ranked games, 8 forfeits, and a worst offender at **4.12 illegal attempts per move** — 70 of them
  across 17 moves.

  `GET /games/human-record` is new and is the only read it adds: four integers in one statement,
  with a query-count test, because tallying them in Python is a full scan of the archive on a route
  the landing page calls. An unfinished game is not a defeat — only games that reached a result are
  counted (invariant 11). The charge sheet is summed from the ranking the page already held.

  It says nothing about credits, deliberately: a seat is granted while the site is in testing
  (ADR-0016), and `/play` is where a signed-in reader learns that, because the lobby is the same
  page for everybody and cannot tell who is asking.
- **The lobby says what a tournament is.** A section under the ranking: the concept in two
  paragraphs — a field, a format and a set of bounds, and a pool that never ends because it
  re-checks its field every tick — beside up to three events, running ones first. Each card carries
  the field and its entrants, a bar of played / live / paused / abandoned pairings against the
  total, and what came out of it: decisive against draws, mean length, tokens, illegal attempts and
  cost.

  **The concept is a cell of the grid rather than a paragraph above it**, and the grid is as wide
  as it has cells. There is one tournament today: a row built for three would have rendered one
  card beside two holes, which is the whole reason the section reads as deliberate at one event. It
  adds one cached read (`/tournaments`, tagged) and asks for no standings — the podium above it
  already answers who is winning.
- **A pool called itself a "round robin".** `formatLabel` knew two formats and fell through to the
  second, so the one event on the site was labelled the opposite of what it is. It pairs like a
  round robin — greedy and incremental (ADR-0041) — but what a reader needs from the word is that
  it never ends and its field is not fixed.
- **The lobby's ranking is a podium and a chasing pack, not five rows in a column.** The front page
  showed the top five as a list sharing a row with "Recent games" — it said who was ahead without
  ever saying this was a *contest*, which is the whole pitch. The top three now stand on plinths of
  descending height, 2 · 1 · 3, each carrying the rating and its deviation, the W/D/L and the
  illegal-move rate; places four to ten run down a list beside them. It stays a podium on a phone —
  shorter plinths, the name wrapped over three lines, and the two figures that will not fit in
  113px waiting for `sm` — because stacking the three into a list there is the thing this section
  replaced. "Recent games" takes the full width underneath, three cards across.

  The section adds no request: it renders the ranking the lobby already awaited (ADR-0032).
- **The landing page is the same page for everybody.** It carried a "Your games" strip and read the
  session cookie to decide whether to draw it, which made the first page every visitor loads the one
  page on the site that could not be reasoned about without knowing who was asking. `/profile` now
  owns a person's own games and is a better home for them. Nothing on `/` reads a cookie, a header
  or anything else about the request any more.
- **Six replays instead of three**, so the row is two full ranks on a wide screen rather than one
  and a gap.

- **The pages arrive whole, and the skeletons are gone** (ADR-0046). Every page route was
  `force-dynamic`, so a click produced no feedback until the whole server render finished — and the
  fix for that had been a `loading.tsx` skeleton on four routes plus five `<Suspense>` boundaries on
  the lobby. The lobby sent its first byte at 4.4ms and did not finish until 43.1ms: thirty-nine
  milliseconds of assembling itself in front of the reader, behind a skeleton that did not match the
  shape it stood in for.

  The premise underneath it — "a live game and a leaderboard are both wrong the moment they are
  cached" — was doing too much work. A running game's board does not come from the server render at
  all; `EventStream` corrects it over SSE within milliseconds. A finished game can never change
  again. And the leaderboard, the catalogue and the tournament tables change on exactly one
  occasion, which the API knows and a clock can only guess at.

  So every public read is now cached and tagged, and the worker names the stale tags the moment a
  game writes a `game_events` row — `orchestration/revalidation.py` to `POST /api/revalidate`, from
  inside `publish_events`. The `revalidate` seconds are a backstop for a lost notification, not the
  mechanism.

  Measured on 106 games, median time to the *last* byte: `/` 43.1 → 14.2ms, `/leaderboard`
  20.4 → 4.4ms, `/about` 18.6 → 3.9ms, `/methodology` 16.6 → 4.1ms, `/models` 26.1 → 11.9ms,
  `/tournaments` 15.1 → 3.9ms, `/play` 21.5 → 6.7ms. Five consecutive loads of `/leaderboard` cause
  zero reads of `/leaderboard` on the API, against five before. First and last byte have converged,
  which is the point: nothing streams any more.

  Cache Components (PPR) was tried and reverted — it reintroduces the streaming, breaks the 404
  contract site-wide, and costs the 87 KiB Clerk saving. ADR-0046 records why; ROADMAP's *Known
  gaps* records what is left on the table.

- **Clerk's prebuilt UI is gone, and with it 285 KiB from every route.** `@clerk/ui` was loading on
  `/about` and `/leaderboard` — pages where nobody signs in — because *one* prebuilt component
  anywhere forces it site-wide. `prefetchUI` is documented as `false` *"for custom UIs using Control
  Components"*, and that parenthesis is load-bearing: it throws with no lazy fallback if any
  prebuilt component renders, so it was never a flag to flip. It is the reward for owning the
  screens.

  Clerk: **372 KiB → 87 KiB**. The page: **619 KiB → 337 KiB**. What still renders from Clerk is
  control components only — `Show` and `AuthenticateWithRedirectCallback`, both explicitly
  supported. Adding a prebuilt component re-breaks it silently and site-wide, so the browser suite
  asserts `@clerk/ui` is never requested, and that assertion was verified to fail when the flag is
  removed.

  Scope is Google and an emailed code, which is what the instance enables. Every other branch Clerk
  supports surfaces as an error a person can read rather than being half-implemented — hand-rolled
  auth fails by locking somebody out, so the flows that are not covered say so.

- **Clerk is not loaded at all for a signed-out reader.** The provider mounts only when Clerk's own
  `__client_uat` cookie shows a session or the route is about identity — neither of which needs
  Clerk to read. A signed-out visitor to `/leaderboard` now downloads **3 KiB** of Clerk instead of
  87, and the page is **258 KiB** where it was 619 before any of this.

  The header draws its signed-out bar from that cookie rather than a hook, and `/play`,
  `/games/[id]` and the landing page take the same flag as a prop. That last part is not optional:
  `useAuth` *throws* without a provider, so a component that asks a hook what a cookie already
  answered is a **500 on a public page** — which is exactly what the first attempt shipped, caught
  by the browser suite.

- **`--color-bad` failed WCAG AA too** — 4.25:1 on `surface-2`, 3.76:1 on `surface-3`. It is the
  colour of "abandoned", "paused" and an illegal-move count, so it only surfaced once the budgets
  ran against a database that had those states. `#d8836d` clears 4.5:1 everywhere.

- **The Lighthouse suite measures the whole public site, with Clerk** (NFR-12). Twelve routes
  instead of four, including a real game and a real tournament from the browser suite's fixtures.
  It used to build with the Clerk keys cleared because a dev tenant was 55% of the page; owning the
  auth screens removed the reason to look away.

  Widening it found two things on the first run: an audit asserted that no longer exists in this
  Lighthouse version, and `/sign-in` scoring 0.63 on SEO — which is `robots.txt` working, so the
  auth pages now carry a per-URL exemption rather than the audit being switched off everywhere.

### Fixed

- **Accessibility: every public page now scores 100 against production's data.** Links inside
  sentences are underlined instead of being marked by colour alone, which was 1.2:1 against the
  prose around them. A stat's note is a `<dd>`, not a `<p>` inside a `<dl>` (tournament, model and
  profile pages). A pairing's state dot and a nameplate's captured pieces have `role="img"`, so their labels are read instead of dropped.
  A departed model's row and a muted credit badge recede by colour token instead of by
  `opacity`, which had taken faint text to 2.5:1. "Left the field" is now announced rather than
  `aria-hidden`. `e2e/public/accessibility.spec.ts` asserts all four rules on every public page.

- **Chessmark is filed under a category on OpenRouter.** The app page has existed since the first
  attributed call — `HTTP-Referer` alone creates it — but `openrouter.ai/apps` is a marketplace
  grouped into Coding Agents, Productivity, Creative and Entertainment, and an app with no category
  belongs to none of them. `X-OpenRouter-Categories: game` fixes that. Unrecognised names are
  dropped silently on their side, which is why the value is normalised here and never checked
  against a copy of their list.
- **Reopening a game tells the players it reopened.** `8692cba1` reached the 300-ply cap, and the
  model's own `make_move` result said so — `{"game_over": true, "result": "1/2-1/2", "termination":
  "ply_cap"}`. It was reopened with a raised cap, which clears the ending from the *game record*
  and left the *transcript* still saying the game had finished. The next turn asked Black to move
  at ply 302; Black answered four times that the game had already ended "according to the terminal
  state reported by the system", and the harness forfeited it for not calling a tool. A rated 1-0
  against the only party in the exchange behaving correctly, and a harness bound recorded as a
  finding about a player — which is what invariant 11 exists to prevent.

  `resume_game.py` now appends a message to **both** seats naming the ending being set aside, the
  ply it continues from and the cap it is playing to. An append, never an edit: the earlier "game
  over" stays where it is, because the transcript is rows whose prefix has to remain byte-identical
  for prompt caching (invariant 2).

  `--overwritten-verdict` also stops claiming a race. It has only ever checked the record — a
  harness stop, then a finding — and a deliberate reopen leaves exactly that shape.

  And the seat's `forfeited` flag is cleared when that gate passes. It is refused for a genuine
  forfeit and always will be, but the gate's own finding is that this one was ours: without it the
  repair would have reopened the game and left the model carrying a forfeit, in the leaderboard's
  published column, for an ending that no longer exists.
- **A halted game now says when the halt lifts.** Two games held behind the *same* free-tier halt
  read differently on production: one said "retrying shortly", because the worker had paused it
  with the halt's own expiry, and one said only "held until the harness is resumed", because it was
  *already* paused when the allowance ran out — and `_say_it_is_held` deliberately leaves
  `resume_after` in the past so the game resumes the instant the halt lifts rather than waiting out
  a clock. Both correct about the game; only one of any use to somebody watching a board that will
  not move.

  The expiry was already in the database, in the payload of the notice that recorded the halt.
  `what_it_waits_for` reads it there rather than from Redis, so the API still has no halt client
  (which is the decision `HALT_PREFIX` documents), and the page says *held · back in 6h*. A halt
  with no end — an operator's — still says so.
- **A debug `print` ran on every reconciler sweep**, since 2026-09-15. It dumped the ids and event
  sequences of everything the sweep published, to stdout, on a timer, in production.
- **Every reader's browser was quietly hammering the site.** A visible `<Link>` to
  `/models/[...slug]` prefetches; the payload comes back `no-store`, because every route here is
  dynamic — the root layout reads the request to decide whether to mount Clerk — so the router
  stores nothing and schedules the prefetch again, for as long as the link is on screen. Production
  served **~90 requests in twelve seconds per link** on `/leaderboard`, and a production build here
  ~110 a second. Nothing appeared in the console and no page looked wrong; the only symptom was
  load. `/leaderboard` and `/tournaments/{slug}` were doing it live.

  Not Clerk — it reproduces with Clerk disabled entirely — and not the `#c-` anchor, which was the
  other plausible culprit. `prefetch={false}` on the four places that link to a model is the whole
  fix, and it costs nothing worth having: prefetching a route whose payload cannot be stored buys
  the reader nothing in the first place. A browser test now fails if any page requests one URL more
  than a handful of times.

- **The excluded-games list on `/leaderboard` could not be hit with a finger.** Ten-pixel game ids,
  13px tall with six between them, four to a reason: Lighthouse scores `target-size` at **zero** on
  that page against production data. It survived because the local seed has too few excluded games
  for the audit to have anything to measure — the same reason the social cards' bug survived, and
  the same fix: look at it with real data.
- **Colours stopped alternating whenever a game did not finish.** The per-entrant colour balance is
  built from results, so a *settled* rematch has always swapped colours on its own. An abandoned
  game produces no result, moves nobody's balance, and left the comparison level — at which point
  `_colours` fell through to "White to `home`", and under `BALANCE` `home` is the entrant with the
  fewest pairings. So a pairing that kept being abandoned kept being scheduled the same way round,
  and an entrant whose games never settle kept collecting White. `pool-free` has five abandoned
  pairings in the current era and its entrants furthest behind are the ones whose endpoints rarely
  serve, so they were `home` most often *and* least likely to produce the result that corrects it.

  `_whites_against` is an **ordered** ledger counting attempts as well as results, and it breaks the
  tie before the fall-through does. Deliberately not a change to `_meetings`: making `(x,y)` and
  `(y,x)` distinct fixtures would double the coverage target, and ADR-0041 bought that coverage on
  purpose — Glicko wants diverse opponents more than symmetric ones.
- **A delisted entrant that never played no longer holds a standings row.** The field tracks the
  catalogue, and three of `pool-free`'s were withdrawn from the free tier before they were ever
  paired; they sat at nought games with no rating, indistinguishable from a model that had been
  tried and had nothing to show. One that *did* play keeps its row, greyed — those games are in the
  ratings of everyone it met.
- **The captured pieces drifted to the far right of the nameplate on a wide screen.** The name was
  `flex-1`, so its *box* filled the row and the huddle sat against that box's edge while the text
  ended far to the left — 284px of name in a 522px box, a 246px hole between the two. It reads as
  two unrelated things at opposite ends of the bar rather than a name and what it has taken. The
  name sizes to its content from `sm` up and still truncates; growing is only what puts it on a
  line of its own on a phone. 246px to 8px, the row's own gap.
- **The header had three control heights in it** — 28px for the nav trigger, 26.5px for `sign in`,
  32px for the signed-in account card, each derived from its own padding and contents. Plainly
  visible side by side on a phone. `CONTROL_HEIGHT` states it once.

- **A model thinking now says how long it has been thinking.** A finished block already read
  `reasoned for 12s`, from the event's `duration_ms`; one still being written said a flat
  "reasoning" and nothing else — which is precisely the stretch a reader watching a board not move
  is trying to interpret, and `e601f9af` once spent 369 seconds in a single round. It counts up as
  `reasoning for 12s` and changes tense to `reasoned for 12s` the moment the closing frame lands.
  `useGameStream` stamps each `token` frame with its arrival time, because a clock started at render
  time restarts on every render.
- **The mobile menu button sat in the middle of the header.** It and the account controls both
  carried `ml-auto`, and two auto margins in one flex row *share* the free space rather than one of
  them taking it — so the trigger came to rest 157px into a 390px bar, reading as a third nav item.
  Below `md` only the trigger takes the space now, and the account controls sit beside it.

- **"retrying shortly" was a lie told by arithmetic.** Every pause ran through a relative clock that
  returned `"shortly"` for any wait of twenty seconds or less — **including every negative one** — so
  a wait that expired twenty minutes ago read exactly like one about to end, and did that forever.
  Game `c2fd378a` came due at 11:05 and still said "retrying shortly" at 11:26. It was not retrying:
  `pool-free` is bounded to one game at a time and another held the slot.

  Two different questions, now kept apart. `pause_reason` is **why it stopped** and stays true
  forever; `GameDetail.waiting_on` is **what it is waiting for**, which changes underneath a reason
  that does not. `reconciler.what_it_waits_for` answers it in the sweep's own order — clock, halt,
  concurrency, due — so the page cannot disagree with the reconciler about why a game sits still.

  The row now reads `PAUSED · rate-limited by Poolside · waiting for a slot in Free Models`, or
  `held until the harness is resumed`, or `due to resume`, or counts down a wait that really has not
  elapsed. **Only the live pause row says any of it**: `foldEvents` marks exactly one, so a replay
  of a finished game and an earlier ply's rate limit both stay quiet rather than claiming to be
  waiting for something.

  `waiting_on` is on `GameDetail` and deliberately not on `GameSummary` — it costs a query, and a
  list endpoint would pay that per row.

- **A pause and the resume that ended it are one fact, and the page drew them as twelve.** Game
  `c2fd378a` sat on ply 13 being refused by Poolside: 27 `game_paused` and 25 `game_resumed` rows,
  twelve of the pauses on the open turn. The panel showed one folded `PAUSED x12` row, ten stray
  `RESUMED` rows stacked under the turn's tool calls, and a header reading **"1 pause"** directly
  above the row that said `x12`.

  Three separate causes, all now fixed:

  * **A resume was tied to its pause only by prose.** `game_resumed` carried a single `detail`
    reading "the wait is over: <reason>", so nothing could pair the two without parsing English. It
    now carries `reason`, `paused_seq` and the seat, copied from the pause it ends. `detail` is
    unchanged, because every game in the archive has only that.
  * **The two sides of the pairing disagreed.** The pause fold *searches* the block list — the model
    retries between refusals, so the row deliberately stays where the wait began — while the resume
    test peeked at `blocks.at(-1)`, found the retry, and let every resume after the first escape.
    They are the same search now, matched on the reason so a halt lifting cannot silence a rate
    limit's row.
  * **The counter counted rows, not pauses.** `pauseCount` sums each folded row's `count`, and lives
    in `lib/` so a unit test can reach it.

  Verified against production: the same game now renders one `PAUSED x12` row, zero stray resumes,
  and per-ply counts of 3, 1, 1, 2, 2, 2, 1, 3 — exactly what its raw event log contains.

  Also settled while debugging it: **a pause is not always followed by a resume.**
  `_pause_for_halt` writes a second `game_paused` on top of a provider one with nothing between —
  `c2fd378a` has exactly that pair at seq 106/107 — so a resume ends the *latest* pause, not the
  first.

- **Two ADRs were numbered 0032, and a citation of it carried no information.** *The arithmetic that
  decides whether a request can be sent* and *The leaderboard is stored, not recomputed on every
  request* were written the same day and both took 0032; only the second reached the index. The
  collision had already produced wrong links: this file defines one `[ADR-0032]` reference, so a
  line about the leaderboard's cost sent readers to the context arithmetic, and `agents/llm.py`
  cited 0032 meaning one decision while `bench/snapshot.py` meant the other.

  The arithmetic one is now [ADR-0047], unchanged, with a pointer left at the old path so existing
  links still land. Renumbering is the one exception to ADR immutability — it protects the decision,
  not the filing.

  Two more citations were repointed: `0034` named `0015-endpoint-pinning-and-quantization.md` (a
  rename), and `0035` named `0025-reasoning-withheld-mid-game.md` — **a file that was never
  written**, while 0025 went to an unrelated decision. That rule has no ADR at all; it lives in
  invariant 8 and `api/redaction.py`, and 0035 now says so.

  `apps/api/tests/docs/test_adr_integrity.py` runs in `make check` and fails on a duplicate number,
  an unindexed ADR, an index row pointing at nothing, a non-standard header, or any dead relative
  link or heading anchor. All five were live in `docs/` when it was written.

- **`sign in` sometimes said "That did not load."** — and it was the site's own sign-in button. The
  root layout mounts `ClerkProvider` from the request path and the session cookie, but the App
  Router preserves a shared layout across a client-side navigation, so the layout never re-ran and
  its decision was whatever the *previous* page made. A signed-out reader on `/` had no provider,
  correctly; clicking `sign in` then reached a route that needs one, `useSignIn` threw
  `useClerkSignal can only be used within the <ClerkProvider /> component`, and the root error
  boundary covered the form. Reloading fixed it, which is why it read as random.

  Nothing caught it because every test reaches those pages with `page.goto` — a document request,
  the one path where the decision is right. `auth.setup.ts` even navigates directly and says why.

  `ClerkGate` re-runs the same predicate on every navigation and mounts the provider if the request
  did not. The provider stays above every route rather than moving into an `(identity)` route group,
  which would have fixed the way in and broken the way out — a nested layout unmounts when you leave
  its segment, so signing in and landing on `/play` would throw there instead. The 87 KiB stays off
  the routes that do not need it: the import is dynamic, and `auth-navigation.spec.ts` asserts both
  halves — clicking through loads Clerk, and reading `/leaderboard` does not.

- **Nobody could create an account through our sign-up form.** The Clerk instance requires a
  password; the form asked for an email and a code and nothing else, so every email sign-up
  verified its code and then stopped on `missing_requirements` — the account half-created, the
  only sign of it a line of red text. Sign-up now takes a password. Signing *in* still does not
  ask for one: the emailed code is the first factor, and the password exists to satisfy the
  instance.

- **A display name could never be saved.** `/profile` wrote `username`, which is an attribute a
  Clerk instance enables or does not, and ours does not — every save came back *"username is not a
  valid parameter for this request"*. It writes `first_name`, which is enabled, and which is also
  what the API reads first when it resolves a person's name (`core/clerk.py::_display_name`), so
  what is typed there is what a game shows.

  Both of these were live, and both were found by writing the test rather than by anyone reporting
  them. The screens had been checked by eye in their signed-out state, which is precisely the state
  in which neither bug is reachable.

- **`make test-e2e-all` is green** — the first time it has been. `the model's thinking is hidden
  while the game is live` had been timing out on a folded turn that cannot exist: `EventStream`
  unfolds the *focused* turn without being asked, and a live human game has exactly one model turn,
  which is the focus. The test now opens the newest turn whether or not it is folded, and waits on
  its steps rather than on the click — the assertions that follow are about something being
  **absent**, and against a turn that never opened they could not have failed.

- **A board nobody can move is one image, not sixty-four unlabelled buttons** (UI-09).
  `react-chessboard` gives every square an interactive role whether or not anything is wired to it,
  so the landing page — a spectator's board and four replay thumbnails — handed axe **92 controls
  with no accessible name and 72 tap targets of 15×15px**. That was the entire accessibility
  deficit that was not contrast. A non-interactive board is now a single `role="img"` with a label
  saying whose move it is, `inert` so nothing inside is focusable, and `pointer-events: none`
  because a target that does nothing should not be a target.

- **`--color-ink-faint` failed WCAG AA, which UI-09 says we meet.** `#756b5b` measured 3.54:1 on
  `ground` and **2.67:1 on `surface-3`** where normal text needs 4.5:1 — and it is the colour of
  most of the site's secondary text, 39 failing nodes on a single page. `#a09684` is the lightest
  value that clears 4.5:1 on every surface, so it buys compliance for the smallest change in how
  the site looks and stays clearly separate from `--color-ink-dim`.

  Measured after both: **performance 99, accessibility 100, best practices 100, SEO 100.**

- **`make check` now runs the production build.** A route segment config export must be statically
  analysable, and `export const revalidate = SOME_CONSTANT` typechecks, lints and runs in dev
  before failing `next build`. Three CI jobs went red on a branch where `make check` was green,
  which makes the gate a liar in the one direction that matters.

## [0.4.0] — 2026-09-17

**The site works on a phone, and a ceiling we imposed stopped counting as a result.** Five commits
since `v0.3.0`. The two halves are unrelated in the code and had the same cause in the working
habit: both were things nobody could see from where they were standing — one because the page was
only ever opened on a desk, the other because the number it was wrong about agreed with the number
beside it everywhere except one column.

### Changed

- **The small end of the type scale is named** (UI-11). 190 arbitrary values — `text-[10px]` and
  five siblings — across 32 files and six sizes, three of them within a pixel of each other. Now
  four steps named for what they are for: `text-label`, `text-meta`, `text-data`, `text-chat`.
  `8.5px` and `9.5px` are gone; a half-pixel does not survive rasterisation, and keeping them meant
  three names for one step.

  It had already cost something. The phone floor below was written as `[class*="text-[10px]"]`,
  matching on class *names*, because there was no token to redefine. That hack is gone — Tailwind v4
  compiles `text-meta` to `font-size: var(--text-meta)`, so the floor is four redefinitions in one
  media query and no call site had to be touched for it. No arbitrary font size remains in `src/`.

- **The site is usable at phone width** (UI-11). Seven pages measured at 390px; the failures had one
  shape — a flex row splitting a width that does not exist — and none of them was visible from a
  desk.

  - **The game page is a board and two tabs**, rather than three columns stacked. The order was
    board, conversation, stats, so the stats sat a whole conversation below the board and *whose
    move it is* was the least reachable thing on the page. The conversation is bounded at `58svh`
    too: left to grow it was as tall as the game was long, and reaching the newest turn meant
    scrolling past every older one. `LiveGame` and `Replay` now share `GameLayout` instead of
    drawing the same grid character for character.
  - **A pairing's two model names stack.** They shared one row and got **37px** each —
    `nemotron-3-nano-omni-30b-a3b-reasoning:free` needs 310px and showed four characters, so every
    fixture in the pool read `nemo… vs nemo…` with the full name in a `title` a thumb cannot open.
  - **A nameplate's captured pieces move below the name.** `Nex AGI: Nex-N2.5-Pro (free)` wanted
    209px and got 157px, losing a quarter of itself to a huddle of ten pieces; the huddle then
    wrapped *inside* the row, so the two nameplates flanking one board were 30px and 17px tall.
  - **The leaderboard and the pool table drop their trailing columns** instead of scrolling
    sideways. The leaderboard was an 820px table in a 333px scroller, so a phone showed `#` and
    `Contestant` and the rating — the reason the page exists — was behind a swipe nothing
    announced. The pool's standings gave the model column 37px for the same reason; it gets 173px.
  - **The replay transport is two rows on a phone** — the five controls, then the move count and
    the speeds, centred. At 44px the controls fill the width on their own, so the speeds wrapped to
    a line of their own and `ml-auto` pinned them to the right of an otherwise empty row. The
    game header had the same bug: its actions wrapped and were pinned right, leaving a gap the
    width of the page beside two buttons. `ml-auto` now waits for a row to push against.
  - **The replay transport is 44px under a thumb**, 28px under a cursor, and the type scale has an
    11px floor below `sm` — one rule in `globals.css` rather than 139 arbitrary values in markup.

  Held by a new `mobile` Playwright project that CI runs beside `public`. Eight of its nine
  assertions fail without these changes.

- **The board keeps the middle column** (UI-11). Not a phone bug — the desktop one the phone work
  introduced and nearly shipped. `GameLayout` orders its slots twice, and `lg:order-none` let DOM
  order stand: the grid was still three columns, the three tops still aligned, and the `min()`
  middle column that exists to size the board went to the conversation while the board sat in a
  323px rail. `replay.spec.ts` now asks which child is in which column, because everything short of
  that passed.

### Fixed

- **A harness ceiling is no longer scored as a pairing result** (invariant 11, [ADR-0019]).
  `db/tournaments.settle` said in its own docstring that a game the harness stopped "is marked
  abandoned rather than scored", and then asked a question that could not answer it: it read
  `GameStatus.ABORTED`, which only `ABANDONED` produces. A `ply_cap`, a `budget_exceeded` or an
  `adjudication` ends a game `FINISHED` carrying a real `GameResult`, so all three fell through to
  `_SCORES` and were scored like any other draw.

  `pool-free` round 175 is what it cost. `ling-3.0-flash-sante` reached
  `8/6P1/1k5P/5K2/5p2/8/8/8 w` — a pawn on g7, `g8=Q` on the move, the black king on b6 — and the
  300-ply cap drew it. `bench/ratable.py` excluded the game from the rating, correctly and
  invisibly, while the pool's table handed both models half a point: the page showed `0.5` beside
  `unrated`, and `lfm-2.5-2.6b` carried half a point our own ceiling had given it.

  `settle` now asks `HARNESS_TERMINATIONS` rather than keeping a fourth list of its own. That set
  was already the answer and nothing linked it — `test_classification.py` exists because three sets
  classifying terminations had drifted apart once, and this was the fourth, one module away and
  unchecked. It is also what makes the ply cap legitimate at all: the cap is **not** stated in the
  system prompt, so under invariant 12 it may never decide a scored game.

- **Identical pauses inside one turn fold into one row** (UI-10, [ADR-0045]). A run of pauses has
  folded between turns since `50cd042`, and moving a pause inside the turn it interrupted brought
  the run back where nothing was folding it: `foldEvents` matched only `blocks.at(-1)`, and inside a
  turn the model *retries* between refusals, so what sits between two pauses is a `reasoning` block
  rather than nothing. `57e8a7bc` drew three byte-identical "Nvidia did not answer in time" rows in
  one turn. The fold now finds the run's row wherever it is, keeps it where the wait began, and
  takes the newest `resumeAfter` — `foldPauses`'s `{ ...last, key: first.key }` said the other way
  round. Matching on the pause text is unchanged, so a rate limit followed by a halt stays two rows.

## [0.3.0] — 2026-09-15

**A provider that stops answering no longer destroys the work it interrupted.** One thread runs
through this release: a refused call used to cost the whole turn, and everything downstream — the
ladder, the record, the page — was built around that discard.

### Changed

- **A turn keeps the rounds it completed, and the retry continues it** ([ADR-0045]). A turn ran
  inside one transaction and a provider failure raised out of it, so a turn refused on its third
  call discarded the two that had been answered and *billed*. The retry then paid for them again;
  in `f129b600` the rolled-back turns are the gaps in the id sequence — 7854-7856, 7859 — each a
  fresh attempt redoing the board read the one before it had completed. A model that could not
  finish a whole turn inside one provider window therefore never banked a step, and never moved.

  The rollback was avoiding something real: a half-written turn can leave an assistant message
  whose `tool_calls` nothing answered, and the transcript is append-only, so that seat is refused
  for the rest of the game — the shape that corrupted 242 rows across 14 seats. But it was broader
  than the hazard. A refusal comes out of `complete()`, which runs *before* the round's assistant
  message is appended, so at that moment the transcript is already at a clean boundary.

  Most of "continue" was free: the transcript is rebuilt by `SELECT ... ORDER BY seq`, so committed
  rounds are simply there and the model carries on mid-conversation. What needed writing is what
  must **not** repeat — the turn prompt and `turn_started` are per turn, not per attempt.

  Which failures keep their rounds follows one rule: *commit when the next attempt sends the same
  request again*. A rate limit, a timeout and a 5xx do; `NoRoomToAnswerError`,
  `HarnessCeilingError`, `ProviderAccountingError` and `ProviderMangledError` still roll back,
  because each needs the next request to be different and more rounds make that harder.

  Per-turn bounds accumulate across attempts, and reaching one on a resumed turn is a harness stop
  rather than a forfeit — a model must not be written off for a bound it reached because we could
  not get served (invariant 11, [ADR-0019]).

- **A pause is drawn where it happened.** Three changes that only make sense together, because the
  first one moves the pause inside the turn:
  - the **move divider closes its turn** instead of opening it. The chip is the move the turn
    *produced*, so a reader meets the thinking, the tool calls, then the move. Above, it closed the
    wrong section: a pause belonging to the *next* seat appeared under the previous seat's block,
    which is how a deepseek rate limit read as GLM's problem;
  - a pause **names the seat it is waiting on**. It carried none, deliberately — a pause belongs to
    the harness rather than a contestant — and the result was the opposite of the intent, because
    an unlabelled row attaches itself to the block above it and blames the wrong model;
  - a pause **inside an open turn is a step of it**, below the step counter and in sequence, so
    unrolling the steps shows what survived the interruption and what followed it. A run of
    identical pauses folds into one row with a count; the resume is absorbed, because the steps
    after it *are* the resumption.

### Fixed

- **The cooldown ladder reset on a finished turn rather than an answered call** ([ADR-0044]). A
  turn is many calls against a growing transcript, so an endpoint that answered the board read and
  was refused on the move never reached the reset: 60s, 300s, 900s, to the hour cap, against an
  endpoint that had never gone away. The direction was the perverse part — the longer the game the
  less likely a turn completes, so the ladder was harshest on exactly the endpoint a long game most
  needs. `LlmGateway.on_success` clears it per call.
- **The endpoint credited for an answered call was the wrong one.** OpenRouter's `provider` field is
  absent for several models — every call in `f129b600` came back `provider: None` — so the clear
  landed on `model|*` while the refusal had put the strikes under `model|BaseTen`, and the ladder
  went on climbing across turns that had plainly succeeded. The seat's pin is the answer when the
  response has none: it is the endpoint the call was sent to.
- **A move already played is not a failed turn** ([ADR-0045]). A turn goes on past its move until
  the model stops ([ADR-0037]), so a provider can die during the *closing* round — after the ply is
  committed. Keeping that turn's rounds made it resumable, and it was resumed for a **later ply**:
  one row holding two turn prompts and two moves, its `ply_number` overwritten by the second, and
  the ply in between with no `turn_started` at all. Found by playing a game whose endpoint went dark
  mid-turn, not by reading the code.
- **A game already paused when a halt began could not say so.** `_pause_for_halt` writes one notice
  per pause and returns early when the game is already paused, so a game holding a provider pause
  when the free allowance ran out was never told: `9b4bced5` sat fifteen hours showing "rate-limited
  by Google AI Studio", a reason that had stopped being true within the hour. It was never stuck —
  the reconciler was correctly declining to resume it — but held and stranded look identical from
  outside.
- **The reconciler published none of the events it wrote.** `worker._publish` was the only
  publisher, so a game the reconciler resumed had its `game_resumed` committed to Postgres and sent
  to nobody — a spectator watching a game come back from a rate limit saw the stale "paused" notice
  until they reloaded.
- **A turn that talked after it moved was drawn as two.** A turn does not end at `make_move` — the
  model is asked once more and answers ([ADR-0037]) — and what it does with that round is usually
  `say`. A message carries no `turn_started`, so it fell through to the branch that exists for a
  *person's* actions, which have none either, and opened a turn nobody had started: one turn under
  two headers for the same seat, with its own move divider between the halves. A model's closing
  round is the same provider call and stays in the turn; a person's message after their move still
  opens a row of its own, because their turn really did end when they moved.
- **An interrupted turn was drawn twice.** Keeping the rounds a turn completed made it a committed
  row that has not moved ([ADR-0045]), and the panel appended the turn in flight beside every such
  row — so a paused turn showed two headers for one seat, `0 steps · 1 pause` above the resumed
  rounds. The turn in flight now merges into the row it is continuing: one turn, one header, the
  live rounds beneath the recorded ones.
- **A paused turn's frames outlived the pause.** Live frames are cleared by `turn_started`, and a
  resumed turn appends no second one — so after the pause committed, the frames predicting the
  rounds it had just recorded stayed on screen beside them. A pause clears them, and `liveTurn`
  reads only the frames after the newest `turn` frame, which is the boundary the server already
  uses to clear its own buffer.
- **Four kinds of event never reached the browser.** The SSE frames are named after their event
  type, so each type has to be registered explicitly, and the list was written by hand:
  `game_paused`, `game_resumed`, `output` and `compacted` were all added after it. Every one was
  published, delivered and discarded, which is why a pause appeared only on a reload — a symptom
  investigated twice as a publishing problem. The list is keyed by the type union now, so the next
  one that is missing does not compile.
- **A live turn sorted first instead of last.** `liveTurn` gives the in-progress turn `seq: -1` so
  its key cannot collide with a real one. Right for keys, wrong for order: a notice could never be
  placed above it, so a pause sat *under* the THINKING block while live and jumped *above* that turn
  on the next reload. Same events, two orders.
- **CI could not run a job.** `setup-uv` was moved to `@v10`, which does not exist — the action
  publishes a floating `v5` tag but only exact tags from v6 on, so every workflow failed at setup in
  two seconds. Pinned to `v10.1.0`, which is what an action reference should be anyway. The browser
  job also stops the servers it starts: `uv run` left running into post-cleanup made `uv cache
  prune` block on the cache lock for exactly 300 seconds and fail a job whose tests had all passed.

## [0.2.0] — 2026-09-15

### Added

- **`docs/TOURNAMENTS.md` explains how a pool chooses its next game.** The file documented the
  mechanisms one at a time and never laid them side by side, which is how a rest nested inside a
  longer block went unnoticed for the life of the pool. The six-step tick, `Policy.BALANCE` as the
  two nested minima it actually is, the four counts that are easy to confuse, and the four filters
  in a table ordered by the timescale each one owns. The README caught up with deployment at the
  same time: it described it as not yet built.
- **The invariants are checked against games that were actually played.** CLAUDE.md lists twelve
  rules that "if broken, quietly ruin the project" and nothing checked them against a finished game.
  Every failure that reached production was a property of a *whole game* that no single test was
  looking at: the dangling tool call corrupted 242 transcript rows across 14 seats with `make check`
  green throughout. `agents/scripted.py` plugs in as the provider, so the real turn loop, referee,
  tool dispatch and persistence all run with no network — and the models are behaviours taken from
  real games, named for the model that produced them, because a scripted model invented from
  imagination tests imagination. Alongside them, 16 recorded provider responses in
  `tests/fixtures/llm/`, harvested from production by `scripts/harvest_cassettes.py`: the shapes
  that have already cost us a game — vendor-framed tool calls, a context refusal phrased back to
  front, truncation with and without a call — so each is found once rather than twice.
- **`make dev-pull` replaces the local database with production's** and runs the branch's migrations
  against it. Changes were being checked on the live site because there was no other way to see them
  against real data — which is a bad way to find out that a standings table ranks every model first.
- **The winner wears a ribbon.** A finished game's result was on the card's border and in the status
  line, which is enough to read and not enough to *scan*. `winner_colour` is null until the game
  ends and null for every draw, so the band appears exactly when there is a winner.
- **A game says which event it belongs to.** A tournament game had no answer on its own page to
  *why was this played*. The pairing row knows — it is the thing that points at the game — and the
  left rail now carries one line naming the event and linking to it, with the round and the era in
  its tooltip. Absent rather than empty for the games nobody scheduled, which is most of them. A
  fuller card carrying both seats' places and records was built and withdrawn: it cost the rail its
  whole height, and the tournament page shows the standings properly one click away.
- **A web app manifest and a theme colour.** Without them the browser paints its own chrome white
  above a page whose ground is `#16130e` and names a bookmark after the full `<title>`.
  `themeColor` goes in a `viewport` export; Next.js 16 errors on it inside `metadata`.
- **The metadata routes are tested.** `sitemap.ts` and `robots.ts` had no test and were never
  executed by the suite, which is why the sitemap could lose a page in silence. `vitest` now
  includes `src/app/**/*.test.ts` for these two routes; pages and components stay Playwright's.
  `lib/site.ts` came out of the coverage exclusion at the same time — it holds real logic now, and
  its nav predicates had never been run either.
- **The turn prompt names the model's colour and the opponent's last move** ([ADR-0038]). It said
  only *"It is your move. Ply 30."*, so the single statement of which side a model was playing sat
  in the system prompt a hundred thousand tokens back, behind everything compaction had folded —
  and models were observed announcing the wrong colour and correcting themselves off the board.
  `get_board` was the **first call in 37 of 40 turns** of a real game, which is what that prompt
  asks for. The position stays out: holding a board across eighty moves is part of what this
  measures.
- **A prompt version has two parts, and ratings span a minor bump** ([ADR-0038]). `v2` → `v2.1`
  states the same task more conveniently — both new facts were already free through `get_board` and
  `get_move_history` — so the 68 games played under `v2` keep counting. A major bump still means a
  different task and still clears the board, as `v1` → `v2` did. Every game keeps its exact version
  either way.
- **A turn streams as it happens** ([ADR-0035]). A turn is one transaction, so it published
  everything at the end: ply 8 of `e601f9af` spent **632 seconds** across six provider rounds
  (1.1s, 10.9s, 29s, 220s, **369s**, 2.8s) and delivered all fifteen of its events stamped the same
  millisecond. Rounds are now announced as they finish, on a channel that carries no `seq`, is
  never stored, and is superseded by the committed events — so the record is byte-identical whether
  anyone was watching or not. Measured: the first frame lands a full second ahead of the commit on
  a turn with two half-second rounds.
- **A spectator arriving mid-turn is caught up** ([ADR-0035]). Frames are fire-and-forget, so
  somebody opening a game nine minutes into a round got the committed backfill — everything up to
  the *last* turn — and then a still board until this one committed. They were the one reader the
  streaming never reached. The in-flight turn's frames are kept and replayed on connect.
- **Reasoning streams token by token** ([ADR-0035], [ADR-0036]). LiteLLM's streaming path reads
  `reasoning_content` and drops `reasoning`, so on some providers the thinking would never arrive —
  and an absent reasoning field is indistinguishable from a model that did not reason, which is
  what made this the one invariant-3 breach nothing downstream could flag. It *is* distinguishable
  from a **billed** one: a response reporting `reasoning_tokens > 0` and carrying no reasoning text
  is one where the text existed, was paid for, and was not collected. That endpoint goes back to
  whole responses on the spot, so the cost of learning it is one call. On by default;
  `LLM_STREAM=false` stops asking providers to stream at all.
- **The model catalogue now refreshes itself** (OPS-23). `refresh_catalogue.py` was written to be
  scheduled — its own header says so — and nothing scheduled it: no cron container, no timer, no
  workflow, no call from the API, the worker or the tournament runner. It ran when somebody
  remembered the command. Prices set the spend caps *and* what a user is charged in credits
  (ADR-0016) and endpoint rows are what the picker pins to, so every day nobody remembered was a
  day of wrong caps, wrong prices and models the field could not play. A `catalogue` service now
  runs it at start-up and every `CATALOGUE_INTERVAL_HOURS` (12); a failed pass is logged and the
  loop waits, because a scheduled sweep that exits on a bad night is restarted straight back into
  it. `--every HOURS` is on the script, so `make refresh-catalogue` and `./chessmark catalogue`
  still run one pass and still fail loudly. It spends nothing — `/models` and `/endpoints` are
  metadata, not inference.
- **A tournament's schedule shows the latest ten matches and loads more on request.** A pool never
  ends, so its schedule only grows — and it was rendered whole, several hundred linked rows in one
  column, under the standings table that is the reason most people open the page. The order was
  already newest-round-first, so the first page is the part worth seeing. Every pairing is already
  on the page, so "load more" is a slice rather than a request: nothing to wait for and nothing to
  fail. The count says `10 of 312` while there is more, and the button says how much is left.
- **`tournament set <slug> --max-concurrent N`** changes a running event's concurrency without a
  hand-written `UPDATE`. It was settable only at `create`, which is the one moment nobody knows the
  right value — it depends on how many workers are up, how hot the free pools are that day, and how
  long a turn is taking. Takes effect on the next tick; refuses zero, which is a pause that does not
  say it is one. Omit the value to print the current setting. (OPS-22)

### Changed

- **The landing hero puts the board above the game it describes, on a phone.** The board came last
  in the stacked order, so the position and the card naming its two seats were a screen apart — you
  read "thinking…" under two model names with no board in sight. The headline still comes first,
  which was itself a fix: a single column opened a phone on an unexplained chessboard.
- **A model has one page** ([ADR-0034]). `/models/{slug}` and `/leaderboard/{slug}?q=` both headed
  a panel `W / D / L` — one over every game, one over the ratable ones — and neither said so, so
  which pair a reader saw depended on whether they arrived from the leaderboard or from the
  tournament table. The contestant is a block on the model page now, one per precision, carrying
  its rating and the ratable games behind it (BENCH-02). The old URL redirects, `?q=fp8` becoming
  `#c-fp8`.
- **The games that did not count are listed with the reason** ([ADR-0034], BENCH-10). The
  difference between the two figures, itemised and grouped, on the page that prints both. Two
  W/D/L figures are honest only if a reader can see what separates them.
- **`players.last_prompt_characters` is recorded beside `last_prompt_tokens`** ([ADR-0033]), both
  counted at the same instant on the same transcript. Their quotient converts our exact character
  counts into that endpoint's tokens, which is what lets a tail budget be expressed in the unit the
  window is in. It sizes a retention policy, never a safety bound: whether a request can be sent is
  still decided by the provider's own count alone (AGENT-19).
- **`FRAMING_TOKENS` is 4,096, not 256** ([ADR-0047]). One thousandth of a 256,000-token window is
  a rounding error against a count taken on the provider's side; comparable agents hold back 4,096.
- **An unmeasured call holds back the reserve rather than half the window** ([ADR-0047]). 25,600
  against 256,000 where the old bound asked for 128,000. Half a window "always fits" for a game's
  genuine first call and not for a resumed one carrying 227,440 tokens, which is how a request for
  64,000 output reached an endpoint with 27,802 tokens of room.
- **`context_exceeded` is a harness ending, not a forfeit** ([ADR-0031]). While the agent had no
  way to shrink its own history, filling the window was something the model did; now that it folds
  its history when the window fills, reaching the wall says our fold did not keep up. It leaves
  the rated set and becomes resumable, following `timeout` and `truncated`.
- **A new contestant starts at 1500 ± 500, and a rating above ± 110 is marked provisional**
  ([ADR-0028]). Both numbers are Lichess's, adopted verbatim rather than tuned. The wider prior
  suits a field the matchmaker keeps refreshing — it pairs whoever is *least* known, so most games
  are spent on models that have barely played, and Glickman's 350 is calibrated for the opposite
  population. The mark is the deviation said in a word; today it applies to **every** contestant,
  at ± 150 to ± 265 over two to nine games each, which is the honest thing for the page to say.
  Provisional never reorders anything, and an *unrated* entrant is not marked provisional. Ratings
  are recomputed from the games on every request, so this needs no migration. (BENCH-12)
- **A rating deviation is capped at the prior it started from** ([ADR-0029]). `_decay` widened it
  every idle rating period and nothing bounded it, so it could pass the deviation we give a model
  nobody has ever seen — and there is no state of knowledge worse than that. Measured, the breach
  needed about 2,270 idle daily periods (six years), so nothing was close to it; the cap is
  Glickman's own rule and the leaderboard is meant to outlive its first year. Applied to the
  deviation a period *starts* from, so games still shrink it and a long-idle model stays
  measurable. (BENCH-12)
- **A pool's standings are ranked by a rating computed over that pool's games** ([ADR-0027]), with
  the deviation as the tiebreak; a closed event still ranks by score, Sonneborn-Berger and direct
  encounter. A pool has no fixed schedule, so its entrants finish unequal numbers of games and a sum
  of points partly measures how many they were handed — in `pool-free`, two models that had won
  every game they played stood third and fourth behind one that had lost a game in eight. Points and
  W/D/L stay on the page; they no longer decide the order. The rating is that pool's own, so a place
  cannot move because of a game played elsewhere, and the eligibility rules are unchanged by the
  scope. An entrant with no ratable game reads `unrated` and sorts last, never 1500. (BENCH-11)

### Removed

- **`Form.games`, which nothing ever set** ([ADR-0041]). It was the second sort key of the pool's
  home choice, and `_form()` builds every `Form` from a rating and a deviation — so it was `0` for
  every entrant for the life of the pool, and ties among equally-unknown entrants were breaking
  alphabetically. The count the new policy needs is derived from the meetings table instead, which
  keeps one source rather than two that can disagree.
- **`get_legal_moves` no longer says which move is mate** ([ADR-0040]). `check` and `checkmate`
  flags were a one-ply search with terminal evaluation, run by us and handed to every seat on every
  move of every turn — so no model playing Chessmark has ever had to *find* mate in one. Every
  board client highlights legal moves and captures; none of them tells you which move wins. The
  move is still listed, `capture` and `promotion` stay, and ADR-0002 still returns the full list on
  every illegal move.
- **The prompt states the silence forfeit** ([ADR-0040]). `MAX_NUDGES` has always been 3, so a
  fourth reply with no tool call forfeits — and the prompt said nothing, while stating the
  illegal-move forfeit in full. `1815a53f` ended `0-1` on it at ply 176. The nudge now counts down
  too, as the repeated-call nudge already did. Invariant 12, in the place ADR-0020 missed.
- **The prompt describes the turn loop it actually has** ([ADR-0037], [ADR-0040]). It still said
  "you must end by calling `make_move`" after a turn started ending when the model stops, and
  models read that as *move again*: in `9450f060` the black seat re-called `make_move` after
  moving, every turn, and was answered `already_moved` every time.
- **The ranked prompt names no tool the model cannot see** ([ADR-0040]). "Do not use the `say`
  tool" introduced a tool already absent from the schema, which is how an invented tool call — and
  then a forfeit — gets produced.

### Fixed

- **The header's sign-in buttons wrapped to two lines on a phone.** At 390px the bar needed 336px of
  335, and the account controls were the only shrinkable thing in it, so the browser took the space
  from there and "sign in" broke across two lines — a 40px button in a row of 27px ones. Pressing
  the nav trigger made it visibly worse, because "close" is six pixels wider than "menu": the button
  was changing the height of its neighbours. The trigger is a drawn glyph now, fixed at 27px square
  in both states; the account controls are `shrink-0` and `whitespace-nowrap`; and below 360px the
  wordmark drops to the mark alone, because 95px of a 320px bar is a choice between the site's name
  and its sign-in button.
- **A pool kept pairing models that had left the free tier** ([ADR-0042]). `pool-free` reported 21
  entrants against a free tier serving 16: a pool keeps a departed model's record and its rating,
  correctly, but it must not keep handing it games. The field is narrowed before matchmaking rather
  than inside it, and the standings mute a closed row and say why on hover — a reader looking at a
  row that will never gain another game deserves to be told which kind of row it is.
- **A game recorded a precision policy no seat was bound by** (#43). `create_match` wrote the game's
  `provider_routing` before the seats resolved, so it could only carry the *request* — fp8 and above
  — while `resolve_routing` then pinned one endpoint per seat and cleared the filter, because once
  an endpoint is chosen the endpoint is the constraint ([ADR-0015]). Across `pool-free`, 15 seats in
  14 games ran at `nvfp4` or `fp4` under a record saying they could not. Nothing was mis-rated — a
  contestant is `(model, quantization)`, so `model@nvfp4` is its own row — but invariant 3 is about
  a record a reader can trust, and this one was untrue.
- **The event log was truncated rather than paged** (#44). `/games/{id}/events` took `after_seq` and
  `limit`, and nothing followed the cursor: the web client asked for 5,000 rows once and took
  whatever came back. A 147-ply game already writes 1,093 events and a reasoning-heavy one near the
  300-ply cap crosses the cap — and replay rebuilds the board by walking moves from ply 0, so a
  dropped row is a wrong position, not a short list. A first attempt appended the game's *terminal*
  events to a truncated page so a reader at least learned how it ended; that looked continuous while
  the middle was missing, and it broke the cursor, because the last `seq` in the page was no longer
  the boundary of what had been read. One page per request now, a short page means the end, and the
  client follows the cursor — the same contract `Last-Event-ID` already uses for SSE reconnect.
- **A halt spent the patience of every game it paused** (#40). `_pause_for_halt` declines to abandon
  during a halt, which is right — a halt is ours, and writing off a game over it would make a
  harness bound into a finding about a player ([ADR-0019]). But skipping the check never stopped the
  clock, so the hours we chose not to play were charged to the game and judged the moment the halt
  lifted. The daily free allowance runs out most days and the halt holds to UTC midnight: ~8.3
  hours, a third of the window. Halt spans are now subtracted, identified by the `halt_source` key
  the payload already carries — a provider pause writes `limit_source`, so the two are disjoint by
  construction and nothing has to match on the wording of a log line.
- **A rest shorter than the strike that earns it rested nobody** (#41). 0.1.0 shipped a six-hour
  rest for an entrant whose last two finished pairings both came to nothing. It could never fire: a
  strike cannot be *earned* faster than a pairing can finish, a dead pairing takes the full 24-hour
  `PAUSE_WINDOW` to finish, and the engagement bound shipped alongside it already holds that entrant
  for a day from the same `ended_at`. Six hours expired eighteen hours inside a block already in
  force, which is why `gemma-4-26b` took eight pairings and `glm-5.2` four while the mechanism meant
  to stop them was running. The rest is now `PAUSE_WINDOW` itself — imported, not chosen, so the two
  cannot drift apart — doubling per further strike to a cap of a week. Still skipped, not withdrawn:
  the run is counted backwards from the most recent attempt and stops at the first that produced a
  result, so one finished game restores full standing on the next tick.
- **A pool with nothing rated yet ranked everybody first** . `_placed_by_rating` gives
  an unrated entrant the place of the first unrated one — which, when none are rated, is place 1
  for all of them, with the order falling through to the entrant key. Production showed it twice
  over: every past era, whose games are excluded from ratings by version and so can never have one,
  and `pool-free` itself between an era opening and its first ratable game finishing. An empty
  ratings map and no ratings at all describe the same table, so it now falls back to points.
- **The site shipped with Vercel's logo as its favicon.** `app/favicon.ico` was the one
  `create-next-app` wrote in Phase 0 and nothing ever replaced it, so every tab, bookmark and
  search result carried another company's mark. It is now the site's own — the 3×3 checker
  `SiteHeader` already draws, in the header's own board colours — as `icon.svg`, with
  `favicon.ico` and `apple-icon.png` rasterised from it. An amber-square variant was tried for
  legibility and rejected: it is the sharper icon judged against itself (4.75:1 inside, against
  the board pair's 2.99:1) and the weaker one judged against a light tab strip, where it falls to
  1.94:1 and loses its edges while the board pair holds at 3.07:1. The five unreferenced
  `create-next-app` SVGs in `public/` went with it.
- **`/tournaments` was missing from the sitemap.** It shipped with [ADR-0043], went into both the
  header and the footer, and never reached `sitemap.ts` — unlisted for the whole life of the
  feature, because nothing compared the two lists. The static list now lives in `lib/site.ts` as
  `staticRoutes`, read by the sitemap, and `site.test.ts` fails if a nav link is absent from it.
  Individual tournaments are listed too, alongside the models and finished games already there.
- **Every page's social card described the site root.** Metadata keys are inherited wholesale, and
  the pages set only `title` — so sharing `/leaderboard` produced the same card, word for word, as
  sharing `/`. `pageMetadata` gives each page its own OpenGraph and Twitter block from the
  description it already had.
- **Nothing declared a canonical URL.** Each page now states its own, and the dynamic routes build
  theirs from the record rather than the URL: a model is reachable under more than one spelling of
  its slug, and an era is a view of one event ([ADR-0043]) rather than a page of its own. It is
  deliberately *not* on the root layout — inherited, a canonical there marks the whole site a
  duplicate of `/`.
- **`get_legal_moves` was still naming the mating move** ([ADR-0042]). ADR-0040 removed the `check`
  and `checkmate` flags and left the same facts in the SAN string — and a `#` in an alphabetically
  sorted list is easier to pattern-match than the structured flag was. From `1cbf3a36`, the first
  night v3 ran: forty-five moves, exactly one `#`, and the model played it. The suffix is stripped
  now, through the function `parse` has always used to accept `Nc3` for `Nc3#`. The illegal-move
  list goes through the same function, so ADR-0002's recovery route cannot be a way around the
  disclosure line. `get_move_history` keeps its marks — a scoresheet is a record, not an analysis.
- **`judge` reads the tool schema version** ([ADR-0042]). It was recorded on every game since the
  registry existed and never checked, which stayed harmless only because every tool change had
  moved `PROMPT_VERSION` alongside it. Stripping a suffix removes information from a model's view
  and changes no word of the prompt, so the prompt version could not describe it.
- **A pool carries its eras** ([ADR-0043]). We reset a pool by hand three times in two days —
  `pool-free` → `pool-free-v3` → `pool-free-v4` — because a prompt or tool bump changed what a
  running event measured. A pool is *defined* by never ending, so an event abandoned and recreated
  per version is a series of tournaments wearing a pool's name, and the version was leaking into
  the URL bar because it had nowhere else to live. An era is the prompt and tool **majors** joined
  — `v3+v4` — stamped on the pairing when it is written; bumping either half opens a new one on the
  next tick and the pool keeps its slug for good. The matchmaker's memory of who has met whom is
  scoped to one, without which a new era would open convinced every pair had already played.
  Concurrency is not scoped: a running game costs an allowance whichever era scheduled it. The
  tournament page shows the era being played and offers the rest.
- **The tournament runner finds its own work** (OPS-24). The long-running container ticked
  whichever slug `TOURNAMENT_SLUG` named, so `tournament create` produced an event nothing would
  ever tick and `tournament abandon` left it ticking a corpse. Both failures are silent — the pool
  sits at zero pairings, looking like a matchmaker that cannot find a game — and both needed an
  `.env` edit and a container restart to put right, which is a deploy-shaped action for what ought
  to be one CLI command. It now discovers every unfinished event each pass, so two pools can run
  side by side and `create` / `pause` / `resume` / `abandon` are the whole interface. `run <slug>`
  still ticks exactly one.
- **A pool balances its pairings** ([ADR-0041]). The matchmaker asked "whose next game teaches us
  most" — highest rating deviation, nearest-rated opponent — which has no fairness term at all.
  After 123 pairings `pool-free` had **44% pair coverage**, one entrant on 25 pairings and another
  on 1, and a leader ranked first on nine games who had never met second, fifth, sixth or ninth
  place. The new default takes the entrant with the **fewest pairings** and its least-met opponent:
  a greedy incremental round robin that needs no schedule, so it survives a field that changes with
  the catalogue. Simulated over 19 entrants and 800 pairings, coverage goes 68% → **100%** and the
  busiest entrant drops from 35% of all games to 12%. `Policy.INFORMATION` is still selectable.
- **A draw by agreement between two models was unreachable** ([ADR-0040]). `offer_draw` wrote no
  event and told the opponent nothing; only the human path ever recorded an offer. There was no
  `accept_draw` at all, so `Termination.AGREED_DRAW` could not happen in a ranked game, while the
  tool suggesting otherwise sat in every cached prefix. Underneath, `open_draw_offer` keyed on the
  position — right for a human, who does not move after offering, and wrong for a model, which
  must, so an offer lapsed one ply before the opponent could see it. It now lapses when the
  *recipient* moves, which is what a decline is over a board.
- **Two repairs for the games the above left behind.** `repair-transcripts` gained the mirror of
  its existing rule — an assistant row whose `tool_calls` nothing answered, which every provider
  refuses and which no resume can clear — so a game abandoned on one can be reopened.
  `repair-verdict` re-ends a game the harness scored against the wrong party, by **appending** a
  second `game_ended` rather than editing the first, so the log holds both endings and the
  correction is checkable. `--replay` clears the tournament pairing separately, because a game's
  record and an event's schedule are not one decision.
- **Two games were forfeited for their endpoint's failure to parse a tool call** (ADR-0015).
  `dots-3-note-preview:free` frames its calls with its own vendor token and an Anthropic-shaped
  `<invoke>` inside it, which matched neither existing rule — so `832df0b7` and `27df21c2` ended as
  *"replied without calling a tool 4 times in a row"*, a claim about a model manufactured entirely
  by its host. It has one endpoint, so there is nowhere to route around it; recognising the markup
  is the whole of the fix.
- **A turn could end between a tool call and its result** ([ADR-0037]). `max_closing_rounds` was
  checked before `_run_tool_calls` rather than after, and the assistant message carrying the calls
  is appended before they run — so the transcript could be left with `tool_calls` and no results,
  which every provider refuses for that seat for the rest of the game. The transcript is
  append-only, so no retry, pause or resume clears it.
- **The window was sized for the previous request, not the one going out** ([ADR-0039]).
  `ed491262` was abandoned at ply 43 on a 290,310-token request against a 262,144-token window we
  had recorded correctly. Its last measurement was 195,503 — under the threshold, so it never
  folded once in 43 plies — while the prompt had grown to 227,765 and `max_tokens` was cleared
  against the room the stale number implied. The growth was counted, in characters, on the line
  above the decision; `players.last_prompt_characters` pairs with the measurement to convert it,
  and that conversion was used only to size the retained tail. Both decisions now read a projection
  that can only ever be larger than what was measured.
- **The reactive rung could not read half the refusals it exists to catch** ([ADR-0039]). One
  regex knew one vendor's phrasing, so Nex AGI's *"The request is 290310 tokens long and exceeds
  this model's context length of 262144 tokens"* parsed as "some other 400" and the retry that
  would have rescued the turn never ran. The two wordings state their numbers in opposite orders,
  so both now use named groups — and a size refusal nothing can parse is logged rather than
  silently abstained on.
- **The two wall-clock latency tests are deleted** (NFR-01, NFR-02). They held an in-process p95
  against a fixed budget, so what they measured was the machine running them: a run with a **5.7 ms
  median** and a **329 ms p95** failed a pull request that changed no Python. It was the third time
  that one assertion had been wrong, and a check a rerun clears is not a check — it teaches people
  to rerun CI, which the next real regression is then rerun away too. What they stood in for, an
  N+1 or a sync call on the hot path, is caught deterministically by the read path's query-count
  tests. The requirements stand and are for a real load test; ROADMAP's *Known gaps* records that
  they have no automated check. The fanout test, which asserts a fact rather than a duration,
  stays — `test_performance.py` is now `test_fanout.py`.
- **A model page asked for its games under a name no model has** (UI-07). A dynamic route segment
  arrives percent-encoded, so `google/gemma-4-31b-it:free` reached the page as
  `gemma-4-31b-it%3Afree`. In a *path* that still works — the server decodes it again — so the
  page rendered with the right name and the right stats; in a *query value* `URLSearchParams`
  escaped the `%` and the API looked for a model literally called `…%3Afree`, found none, and
  answered **`200 []`**. Nothing threw, nothing was logged, and every `:free` model — nearly the
  whole catalogue — looked like a model that had never played. It had been hidden because the
  drill-down fetched its games through a path; folding that page in ([ADR-0034]) exposed it. The
  id now comes from the registry's own answer, and a page that has stats but no games says so
  instead of rendering as empty.
- **The live stream reached the browser and was thrown away** (UI-10). `useGameStream` filtered
  frames to `block` and `token` and dropped `turn`, which was added later — and `liveTurn` hangs
  every block off it. So every frame arrived, was discarded, and the panel showed a turn only once
  it committed, with nothing erroring.
- **A block being generated stopped updating after its first word** (UI-10). The panel's memo
  compared `blocks.length`, and a block still streaming grows its *text* while the list does not —
  so React skipped every render after the first token. A refresh showed the lot, which is the tell
  that the data was right and the render was skipped.
- **The turn anchor was trimmed out of the catch-up buffer** ([ADR-0035]). The turn frame is the
  oldest entry and every block hangs off it, so a long turn — `deepseek-v4-flash` filled the
  400-frame cap inside one round — trimmed away the only thing that said which turn any of it
  belonged to. The catch-up failed precisely on the turns it exists for. It is kept clear of the
  trim now.
- **An empty block no longer renders** (UI-05). A provisional block exists as soon as its first
  fragment arrives, so a model opening with a newline drew an empty bordered box that read as "the
  model wrote: (nothing)".
- **A model page died on a payload missing a field** (UI-07). `Object.values(model.rated_games)`
  throws, and the page became *"That did not load."* rather than degrading to the record it could
  still render — which is what the whole page did whenever the API had not been redeployed
  alongside the web tier. A rolling deploy makes that window real every time, and the browser suite
  was failing on exactly it. Both fields are optional now and default to empty.
- **A leaderboard row reaches only half its games** (BENCH-02). The drill-down's index recorded
  each counted game under its *first* seat, and seats are read ordered by colour — so black took
  every game and white got none. `ling-3.0-flash-fin` showed `6 / 5 / 4` and listed eight games:
  the W/D/L counts come from a different pass over the same scan and stayed correct, which is what
  made a row that could not open half of its own results look like a display fault. Every seat is
  indexed now, and a mirror match still appears once. **This is why the two pages could not be
  reconciled by reading them** — and it took far longer to find than it should have, because the
  product could raise the question at all.
- **A model page cost two full sweeps of the archive**
  ([ADR-0032](docs/adr/0032-the-leaderboard-is-stored-not-recomputed-per-request.md)). `get_model`
  recomputed the ratings and
  the aggregates per request without sharing a scan — the cost ADR-0032 had just removed from the
  leaderboard, reintroduced on a route nothing measured. It reads the stored run now, and a
  query-count test holds it there.
- **What compaction keeps is bounded by size, not message count** ([ADR-0033]). Measured on two
  real games under the same twelve-message cap: `10fc99f0` kept 12 messages totalling 606,376
  characters — about 210,000 tokens of a 256,000-token window — while `545dc41a` kept 11 totalling
  66,755. The larger filled 82% of its window with the one region compaction may not touch, which
  is the deadlock that left two games unrecoverable: to shrink the conversation the model must
  write a summary, and there is no room to write one *because the conversation is too big*. The
  budget is `KEEP_TAIL_TOKENS = 20,000`, matching what oh-my-pi, Pydantic AI and Hermes protect.
- **A turn that alone exceeds the budget is clamped rather than kept whole** ([ADR-0033]). There is
  no legal cut left at that point — dropping to zero turns leaves nothing to act on, and cutting
  inside a turn orphans a `tool` result — so the largest messages keep their head and their tail
  and lose their middle, marked with how much went. Rendered from `content` on every replay rather
  than stored pre-cut, so the cacheable prefix does not move (invariant 2).
- **An endpoint that misreports its own prompt size ends the game** ([ADR-0033]). A call that
  *succeeded* necessarily fit, so `prompt + the output we asked for` cannot exceed the window —
  and `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` broke that on 24 of 339 calls, worst
  case claiming 516,877 tokens in a 256,000-token window, while eleven other models broke it on
  none of ~7,800. The figure was carried to the next turn and stopped the seat before it made a
  call. There is no honest recovery — no client-side count is trustworthy — so the game is
  abandoned and the endpoint is named. Nobody is forfeited (ADR-0019).
- **The reactive compaction rung compacts against the prompt, not the whole request**
  ([ADR-0047]). An endpoint's refusal reports a total that includes the `max_tokens` *we* asked it
  to reserve, and passing that on charged our own output request against the transcript a second
  time — leaving `_summarise` with negative room, so it never called anything. `29e7f004` and
  `e601f9af` were reopened three times each and died **one second** after every attempt, never
  having tried to rescue themselves. The endpoint's own breakdown is parsed where it gives one.
- **A truncation is failed on sight only when the request was unanswerable** ([ADR-0047]). The
  rule was "the response reached the number we sent", which made the verdict depend on the
  registry being *wrong*: while the catalogue was stale we asked `laguna-s-2.1` for 64,000, got
  its real 32,768, and read that as the endpoint's limit — worth a nudge and three retries. After
  the catalogue was refreshed the identical response read as ours and failed instantly, abandoning
  `a016a326` at ply 72. What decides now is the size of what we allowed.
- **A rescue that outlives the turn that needed it** ([ADR-0047]). A turn is one transaction, so a
  compaction inside a failing turn is rolled back with it and the next attempt sends the same
  bytes. On a context-length rejection the worker now elides stale tool output in a session of its
  own and requeues once — trim-only, because folding needs the provider that just refused us.
- **A turn may no longer inflate its own context** ([ADR-0031]). A reply cut off by the endpoint
  before it reached a tool call is now elided from the *replayed* transcript — the message keeps
  its place, role and structure, and carries a placeholder instead of the fragment. Measured on
  turn 1041 of `29e7f004`: compaction ran at the top of the turn and left a 731-token prompt, and
  the same turn put it back to **516,877** across ten calls, five of them cut off at the
  endpoint's undeclared 32,768-token ceiling and each appended in full. Every failed attempt made
  the next one harder. The row is untouched and `llm_calls` still holds the raw response, so the
  record stays verbatim (invariant 3); only the request shrinks. A reply truncated *after* it
  managed a tool call is kept whole, because that one may carry reasoning its provider requires.
- **A compaction keeps its measurement instead of discarding it** ([ADR-0031]). `_compact` set the
  measured prompt size to `None`, which sent the next request down the unmeasured path — a bound of
  half the window, sized for a game's genuine first call. On a resumed game holding 227,440 tokens
  that asked for 64,000 output against 27,802 tokens of room, and the endpoint refused it. The
  reset was persisted too, so a reopened game began blind and could not even compact its way out;
  `29e7f004` and `e601f9af` each re-threw the same 400 within seconds of being reopened, twice.
- **Compaction may fire again when the transcript has grown since the last fold** ([ADR-0031]).
  The guard was once per turn, which is right for a turn making three round-trips and useless for
  one making twenty.
- **A summary that hits its own cap is discarded** ([ADR-0031]). Fifteen summarising calls across
  the pool returned `finish_reason: "length"` at 2,000 tokens, eight from one model — each writing
  a sentence that stops mid-word into the transcript as that game's memory of itself. The pass
  falls back to the trim-only rung, which needs no provider at all.
- **Reopening an abandoned game restarts its patience window** ([ADR-0031]). The 24-hour clock ran
  from the last move alone, which also counted every hour the game spent dead and every hour it
  spent queued. `b5546c1b` was reopened 32 hours after its last move, ran 6.7 real hours, met one
  rate limit and was abandoned again at "38.9h without a move". Only a deliberate reopening resets
  it — an expiring pause writes a different event — so a failing provider still reaches 24 hours
  exactly as intended. The abandonment message now names the instant it counted from.
- **An entrant mid-pairing is not given a second concurrent game** ([ADR-0031]). A paused game
  holds no concurrency slot, so the freed slot went straight back to a matchmaker that still saw
  the same least-known entrant — and `_fruitless_entrants` counts only *finished* attempts, which
  a failure here takes a full day to become. `gemma-4-26b` held four games inside nine hours, all
  scheduled before the first had ended, all four abandoned.
- **Each pooled game gets its own round number.** A batch carried one number for every game in it,
  which is right for a Swiss round and wrong for a pool, where each game is matched independently
  against ratings the previous one has already moved. `pool-free` showed 63 rounds of one game and
  a single round of two.
- **A game abandoned before its first move shows its log.** `eventsThroughPly` returned nothing at
  ply 0, so a zero-ply game folded an empty list and rendered "The starting position — step forward
  to begin." over a pairing that never began. Five games in `pool-free`, every one with its reason
  recorded and none of it on the page.
- **The event stream says when a game ended, and why.** `game_ended` was the one lifecycle event
  that pushed no notice, so a log ran pause → resume → pause → resume and stopped. On a game
  reopened and then abandoned again, the last thing a reader saw was "resumed".
- **A reasoning bubble is bounded.** Degenerate output — three thousand characters of multilingual
  noise and eighty consecutive newlines, from a model whose serving stack collapsed mid-game —
  rendered at full height and pushed the rest of the conversation out of the column.
- **The reconciler reads the halt's scope instead of standing down for any halt** ([ADR-0030]).
  It asked `halt.active()` once and returned, which was right while a halt was global and wrong the
  moment it had one: under OpenRouter's daily free-model cap **no paid game could be rescued at
  all** — not resumed when its provider pause came due, not requeued when its job was lost — for as
  long as the cap stood, up to a UTC day. The worker and the tournament runner both read the scope
  correctly; nothing had ever passed a halt to `reconcile`, so nothing could have caught it. A
  paused game the halt covers is now held (resuming it would take a concurrency slot and pause
  again having moved nothing) and a *stalled* one is requeued anyway, because the worker answers
  that job by writing the pause below. (OPS-19, OPS-20)
- **A halt now pauses the board instead of stopping it silently** ([ADR-0030]). A turn the global
  halt forbade was dropped and the game left `RUNNING` — correct about the record and invisible on
  the page: the header went on pulsing **live** over a board that would not move again until the
  free-model allowance reset, which is most of a UTC day. Every game a halt covers is now paused
  with one `game_paused` event carrying the reason and, where the halt knows it, the time it lifts.
  Nothing is forfeited and no abandonment clock runs — a halt is ours, not the model's
  (ADR-0019) — and a paid seat is still untouched by a free-model cap. (OPS-19, OPS-20)
- **A game due to resume keeps its concurrency slot** ([ADR-0025]). The tournament runner bounded
  itself on running games only, so a pairing whose pause had expired was invisible to it and
  visible to the reconciler; the two raced for one slot and the waiting game lost. Measured across
  the five deepest abandoned games, **48–99% of every pause was our own queue** rather than the
  provider's — one game was asked to wait sixty seconds and sat for 16.4 hours. (OPS-22)
- **Patience is measured from a game's last move, not its first pause** ([ADR-0025]). The window
  claimed to mean "cannot get a turn in a day" and actually meant "has been pausing on and off for
  a day", so a game that paused at ply 4 and then played eighty-eight more moves was abandoned
  anyway. Three games died at plies 71, 68 and 56 having never gone more than 17.4 hours without
  moving, each within seven playing-hours of a real result. (OPS-22)
- **A read-only tool asked the same question three times gets a nudge, not the answer**
  ([ADR-0026]). `nemotron-3-super-120b` called `get_move_history` twenty times in one turn, received
  a byte-identical result and emitted identical reasoning each time, and forfeited on
  `max_tool_iterations` — 1.96M prompt tokens and twenty of the thousand daily free requests for a
  ply that never happened. The nudge says what it has asked, that nothing changes until it moves,
  and how many rounds it has left. Games already lost this way stand: both seats ran the same
  harness. (AGENT-22)

## [0.1.0] — 2026-09-01

First tagged release. Three fixes found by reading the live `pool-free` event.

### Fixed

- **The pool no longer re-pairs a fixture it can never play.** `gemma-4-26b` v `gemma-4-31b` was
  scheduled seven times over five days without a move being played: an abandoned pairing carries no
  score — deliberately, since it must never be *scored* — and the matchmaker read that absence as
  "these two have never met". Attempted pairings now count as meetings. An entrant whose last two
  finished pairings both came to nothing also rests for six hours, without which the first half only
  sends a model that cannot play hunting a fresh opponent. (OPS-21)
- **An endpoint's output ceiling is no longer a finding about a model** ([ADR-0024]). We asked
  Poolside for 64,000 output tokens against an endpoint that stops at 32,768, so every truncation
  stopped *short* of our request — the exact signature the harness read as the model's own failure,
  which cost `laguna-s-2.1` a game it had won by rook and two bishops against a lone pawn. Requests
  are now clamped to the endpoint's `max_completion_tokens`, and `TRUNCATED` is a harness stop
  rather than a rated forfeit.
- **A forfeit flag follows the game's ending, not the turn's status** ([ADR-0024]).
  `BUDGET_EXCEEDED` ends a game, so it travelled as a forfeit and set a published flag; two games
  were budget-stopped, reopened, and played on to a genuine checkmate and a genuine threefold draw
  while still carrying a forfeit on the leaderboard. `resume` now clears a stale flag too.
- **The version is read from the packaged distribution**, not written in three places.
  `api/routes/health.py`, `main.py`'s OpenAPI metadata and `pyproject.toml` each held their own
  literal, tied together only by two tests asserting the string `"0.1.0"` — which had to be edited
  on every bump, so the failure always read as "the test is stale" rather than "the API is
  reporting a version it is not". The tests now assert that `/health` and `/openapi.json` agree
  with the installed distribution.

### Added

- `./chessmark repair-forfeits` — reconciles seats' forfeit flags against the game record, which is
  the authority (invariant 1). Reports by default, changes no result and reopens no game.

### Removed

- `./chessmark resume --harness-ceiling`. With `TRUNCATED` resumable outright the flag could never
  fire again, and a gate that cannot fire implies a distinction no longer being drawn.

### Notes

Ratings recompute from each game's termination, so historical truncations leave the rated set on
deploy — no backfill and no migration. Run `./chessmark repair-forfeits` after deploying to clear
flags the old code wrote.

[ADR-0015]: docs/adr/0015-quantization-as-identity-and-pinned-endpoints.md
[ADR-0019]: docs/adr/0019-harness-bounds-are-not-findings.md
[ADR-0024]: docs/adr/0024-endpoint-output-ceilings-are-not-findings.md
[ADR-0025]: docs/adr/0025-finishing-a-game-beats-starting-one.md
[ADR-0026]: docs/adr/0026-a-repeated-question-gets-a-different-answer.md
[ADR-0027]: docs/adr/0027-a-pool-is-ranked-by-its-own-rating.md
[ADR-0028]: docs/adr/0028-a-wider-prior-and-a-provisional-mark.md
[ADR-0029]: docs/adr/0029-a-deviation-has-a-ceiling.md
[ADR-0030]: docs/adr/0030-a-halt-pauses-the-board.md
[ADR-0031]: docs/adr/0031-a-turn-may-not-inflate-its-own-context.md
[ADR-0047]: docs/adr/0047-the-arithmetic-that-decides-a-request.md
[ADR-0033]: docs/adr/0033-a-tail-budget-in-tokens-and-a-provider-that-cannot-count.md
[ADR-0034]: docs/adr/0034-one-page-per-model.md
[ADR-0035]: docs/adr/0035-live-frames-are-not-events.md
[ADR-0036]: docs/adr/0036-a-lost-reasoning-trace-announces-itself.md
[ADR-0037]: docs/adr/0037-a-turn-ends-when-the-model-stops.md
[ADR-0038]: docs/adr/0038-a-prompt-version-has-two-parts.md
[ADR-0039]: docs/adr/0039-the-window-is-sized-for-the-request-in-front-of-us.md
[ADR-0040]: docs/adr/0040-what-a-board-shows-and-what-the-prompt-owes-you.md
[ADR-0041]: docs/adr/0041-a-pool-balances-its-pairings.md
[ADR-0042]: docs/adr/0042-the-notation-was-still-analysing-the-position.md
[ADR-0043]: docs/adr/0043-a-pool-carries-its-eras.md
[ADR-0044]: docs/adr/0044-the-ladder-resets-on-an-answered-call.md
[ADR-0045]: docs/adr/0045-a-turn-keeps-the-rounds-it-completed.md
[0.5.0]: https://github.com/ahmedsaed/chessmark/releases/tag/v0.5.0
[0.4.0]: https://github.com/ahmedsaed/chessmark/releases/tag/v0.4.0
[0.3.0]: https://github.com/ahmedsaed/chessmark/releases/tag/v0.3.0
[0.2.0]: https://github.com/ahmedsaed/chessmark/releases/tag/v0.2.0
[0.1.0]: https://github.com/ahmedsaed/chessmark/releases/tag/v0.1.0
