# 0057. Credit sales are a switch an operator flips, and it starts closed

**Status:** Accepted
**Date:** 2026-09-29

## Context

Under ADR-0055 and ADR-0056, credit was on sale exactly when Paddle's settings and OpenRouter's
management key were all in `.env`. That tied two separate questions together: whether selling is
*possible*, and whether we *want* it happening right now.

* **Paddle's settings can't go on the server before the live account is verified.** If they did,
  `/credit` would offer Buy on an account that can't take payments. But without them, the page shows
  no tax estimate, because the preview price is only offered while selling is configured. So until
  verification, the page couldn't show what an amount actually comes to where the visitor lives.
* **Pausing sales meant editing `.env` and restarting**, which is slow and awkward on a running
  server. Reasons to pause will come: a run of refunds to look into, a pricing bug, OpenRouter
  misbehaving.

The model-call halt (`./chessmark halt`, OPS-19) already showed how to do this: a state in Redis,
flipped at runtime by a command.

## Decision

**Sales are a switch, separate from configuration.** `./chessmark sales open` and
`./chessmark sales pause "reason"` flip it; `./chessmark sales` and `./chessmark status` say which
it is, and why.

**The switch starts closed.** No stored value means closed, and so does a value that can't be read.
So the first deploy with Paddle's keys takes no money until somebody opens sales, and a Redis that
lost its data stops sales rather than resuming them. This is the opposite of the model-call halt,
where an unreadable value means "keep running". For money, the safe failure is "we stopped
selling", never "we sold when we meant not to".

**A pause stops new checkouts, nothing else:**
- `POST /credit/checkout` refuses with a 503 before OpenRouter is asked or anything is reserved.
- `GET /credit/availability` answers `paused`, and the page shows "not on sale right now" where Buy
  would be.
- The breakdown and the tax estimate still show.
- Paddle's webhook still credits purchases already paid for. A buyer who was halfway through
  checkout when the pause came has paid, and the money has moved.

The API checks the switch itself, not only the page. The page's answer can be a minute old, and a
tab opened before the pause must still be refused.

**Why not a setting in `.env`?** A setting has to be edited and read at startup, so using it means a
restart. It also can't be flipped from `./chessmark` without editing files on the server.

## Consequences

* **Going live is now three steps:** put the keys on the server whenever they exist, deploy, and
  run `./chessmark sales open` once Paddle has verified the account. Until that last step, the page
  shows the real breakdown with the tax estimate, and nothing can be bought.
* **Sales depend on Redis keeping its data.** It runs with `appendonly`, so a restart keeps the
  switch. A Redis that lost its data closes sales until somebody reopens them, and
  `./chessmark status` shows it as paused.
* `/credit/options` still says only whether selling is *configured*. It is cached, and the switch
  changes at runtime, so the switch's state is in `/credit/availability`, which is read live.
