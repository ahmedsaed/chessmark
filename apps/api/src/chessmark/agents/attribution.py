"""App attribution: telling OpenRouter who is calling (LOG-08 neighbour).

Two headers, both optional, both documented as being *"for rankings on openrouter.ai"*:
`HTTP-Referer` identifies the app and is what creates the app page at all, and
`X-OpenRouter-Title` names it — required for a `localhost` referer to be tracked, ignored for a
real domain that can be read from the URL. `X-Title` is the older spelling and still accepted; the
newer name is sent because it is the one the docs now prefer.

**What this is not.** It does not unlock a gated model, and it was worth establishing that rather
than assuming it, because `thinkingmachines/inkling-small:free` refuses with 403 *"only available
on agentic harnesses — try plugging it into a coding agent or productivity app listed on
openrouter.ai/apps"*, which reads exactly like a header we were failing to send. It is not. The
endpoint was probed with no headers, with `HTTP-Referer` + `X-Title`, with `X-OpenRouter-Title`,
and with `X-OpenRouter-Categories: agents`: identically 403 every time. The *paid* variant of the
same model answered on the first attempt with no headers at all, which places the gate on the free
distribution rather than on our client, and `openrouter.ai/apps` is a usage leaderboard — "largest
public apps and agents opting into usage tracking" — not a list one applies to. See AGENT-18 and
`worker._disable_gated` for what we do instead.

So attribution is sent for its own sake: an app page, per-model analytics, and our usage counted
as ours rather than as an anonymous key. It carries nothing about a request — no prompt, no model,
no game — which is why it can ride on every call unconditionally.

`X-OpenRouter-Categories` is the third header and the only one that changes anything a *visitor*
sees: the app page exists on the strength of the referer alone, but `openrouter.ai/apps` files its
listings by category, and an app without one is in no section of the marketplace. See
`Settings.app_categories` for what is recognised and why it is not validated here.

**Only alongside a real credential.** A scripted gateway has no `api_key` and reaches no provider,
so headers on its requests would appear in recorded fixtures and in the byte-comparison a cassette
does, for a call that never leaves the process. `LlmGateway` therefore attaches these only when it
is actually holding a key.
"""

from __future__ import annotations

from urllib.parse import urlparse

from chessmark.core.config import Settings, get_settings


def _usable(url: str) -> str:
    """`url` with any trailing slash removed, or empty if it names nobody.

    A referer OpenRouter cannot resolve is worse than none — it creates an app page titled after
    whatever placeholder was left in the file. So a scheme and a host are both required, which
    `rstrip("/")` alone does not give: it turns a bare `https://` into `https:`, which is not empty
    and not a URL. Two spellings of one address would also be two app pages with the usage split
    between them, hence the trailing slash.
    """
    trimmed = url.strip().rstrip("/")
    parsed = urlparse(trimmed)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return trimmed


def attribution_headers(settings: Settings | None = None) -> dict[str, str]:
    """The headers to send, or an empty mapping if we cannot name ourselves honestly."""
    settings = settings or get_settings()

    url = _usable(settings.app_url or "")
    if not url:
        # The web front end's own origin. In production that is the real domain; locally it is
        # `http://localhost:3010`, which OpenRouter tracks only when a title accompanies it — and
        # one always does.
        url = next((u for u in map(_usable, settings.cors_origins) if u), "")

    if not url:
        return {}

    headers = {"HTTP-Referer": url}
    title = (settings.app_title or "").strip()
    if title:
        headers["X-OpenRouter-Title"] = title

    categories = _categories(settings.app_categories or "")
    if categories:
        headers["X-OpenRouter-Categories"] = categories
    return headers


def _categories(configured: str) -> str:
    """The category list, normalised to what OpenRouter accepts.

    Lowercase and hyphenated is their format; **two is their per-request limit**, and sending more
    is not an error, it is a request where some of them do not count. Taking the first two here
    means the one that matters is the one written first, rather than whichever survived.

    Unrecognised names are dropped on their side without complaint, which is why this does not
    validate against a list: a copy of theirs in this file would go stale the first time they add a
    category, and would then reject a *correct* value — worse than passing a wrong one through to
    be ignored.
    """
    names = [name.strip().lower() for name in configured.split(",")]
    return ",".join([name for name in names if name][:2])
