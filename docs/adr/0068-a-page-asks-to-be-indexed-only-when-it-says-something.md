# 0068. A page asks to be indexed only when it says something

**Status:** Accepted
**Date:** 2026-10-18

## Context

On 2026-10-18 Search Console reported 447 pages as *Discovered – currently not indexed* and 20 as
*Crawled – currently not indexed*. The technical side was correct: every page returned 200 with its
content in the HTML, had a canonical, a unique title and description, and `robots.txt` allowed it.

The sitemap was the problem. It submitted 484 URLs:

* **299 model pages, 272 of them for models that had never played.** Each was about 200 words of
  the same template: registry facts, "This model has never played a game here", and the same
  paragraph explaining what a contestant is.
* **172 finished games**, many of them ending in a forfeit within a few plies. In a sample of 40,
  12 ended in an illegal-move or error forfeit, several after 18 plies.

On a new domain with few inbound links, a crawler judges the site from a sample of its pages. A
sample that is mostly near-identical template pages leads it to defer crawling the rest, including
the pages worth reading.

## Decision

**A page is indexed when it says something a listing does not.** Concretely:

* A **model page** is indexed once the model has finished a game. Until then it is
  `noindex, follow` and left out of the sitemap.
* A **game page** is indexed when the game reached **10 plies**. A shorter game is `noindex,
  follow` and left out of the sitemap. The threshold is the owner's.
* The sitemap dates a model by its latest finished game and a tournament by its latest finished
  game (`last_played_at`, `stats.last_game_at`), so `lastmod` says when the page last changed.

One rule decides both the page's `robots` metadata and the sitemap (`lib/indexing.ts`). A sitemap
that submits a page which then says `noindex` is an error in Search Console.

`noindex, follow` rather than `noindex`, so the games and models these pages link to are still
found. The pages still render, link and share. Only their request to be indexed changes.

## Alternatives considered

* **Leave the pages and wait.** Crawl budget on a young site grows with links and with the quality
  Google samples. Waiting does nothing about the second.
* **`noindex` on every game that ended in a forfeit.** Some forfeits are long, interesting games,
  and a model's illegal moves are part of what this site measures. Length is the narrower filter.
* **Use `stats.games > 0` for a model.** The sitemap reads the catalogue, which carries no stats.
  Two different tests would disagree about a model whose only game is still running.

## Consequences

* A model's page becomes indexable on its first finished game. Search engines learn that from the
  sitemap within its hour, or from the page when they next crawl it.
* `GET /models` now carries `last_played_at`, read in one grouped statement however many models
  there are. `test_the_catalogue_costs_a_fixed_number_of_queries` holds that.
* The pages that stay indexed are still thin. A game page is mostly a move list and model ids, and
  a model page is mostly numbers. Adding readable text to them is the next lever and is open
  (ROADMAP *Known gaps*).
