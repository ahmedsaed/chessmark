"""Value types returned by the LLM gateway."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any


class CostSource(StrEnum):
    """Where a cost figure came from. Recorded so a leaderboard number can be traced."""

    PROVIDER = "provider"
    """OpenRouter reported the real charge. Authoritative — always preferred."""

    COMPUTED = "computed"
    """Derived from returned token counts and registry pricing."""

    UNKNOWN = "unknown"
    """No pricing available. Cost is zero and must not be presented as if it were real."""


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token counts as reported by the provider. Never inferred, never estimated."""

    prompt: int = 0
    completion: int = 0
    reasoning: int = 0
    cached: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion

    @property
    def uncached_prompt(self) -> int:
        """Prompt tokens actually processed. Cached reads are billed differently, or not at all."""
        return max(self.prompt - self.cached, 0)

    @property
    def cache_hit_rate(self) -> float:
        """Fraction of the prompt served from cache. The metric behind NFR-06."""
        return self.cached / self.prompt if self.prompt else 0.0


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """A tool call requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str
    """The unparsed string. Kept because a model that emits malformed JSON is a finding, not noise."""

    parse_error: str | None = None
    """Set when `raw_arguments` was not valid JSON; `arguments` is then empty."""

    @property
    def ok(self) -> bool:
        return self.parse_error is None


@dataclass(frozen=True, slots=True)
class ParsedResponse:
    """A provider response, normalised across differing shapes."""

    content: str | None
    reasoning: str | None
    reasoning_details: list[dict[str, Any]] | None
    """OpenRouter's normalised reasoning blocks, verbatim.

    Opaque on purpose. It carries Anthropic's signatures, Gemini's thought signatures, and
    DeepSeek's `reasoning_content`, and several models **require** the exact sequence back on the
    next request. Reading or reshaping it is how that breaks.
    """

    tool_calls: list[ToolInvocation]
    usage: TokenUsage
    finish_reason: str | None
    model: str | None
    provider: str | None = None
    """Which OpenRouter endpoint served this. The response names the provider but not its
    precision; `model_endpoints` is what turns the name into a quantization."""

    provider_cost_usd: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Completion:
    """One completed provider round-trip, with everything needed to persist an `llm_calls` row."""

    model: str
    provider: str | None
    """The endpoint that actually served the call — recorded so a result can say what precision it
    was played at, not merely what was asked for."""

    content: str | None
    reasoning: str | None
    tool_calls: list[ToolInvocation]
    usage: TokenUsage
    cost_usd: Decimal
    cost_source: CostSource
    latency_ms: int
    finish_reason: str | None
    request: dict[str, Any]
    """Verbatim, redacted (LOG-01)."""

    response: dict[str, Any]
    """Verbatim, redacted."""

    attempts: int = 1
    """How many provider calls this took. >1 means transient failures were retried (AGENT-09)."""

    reasoning_details: list[dict[str, Any]] | None = None
    """Replayed verbatim on the next turn — see `ParsedResponse.reasoning_details`."""

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    def tool_call(self, name: str) -> ToolInvocation | None:
        return next((call for call in self.tool_calls if call.name == name), None)


@dataclass(frozen=True, slots=True)
class RateLimit:
    """A provider declining to serve this right now, and whatever it said about when.

    **Two shapes, one meaning.** A 429 is "come back later"; a provider 404 — `{"code":404,
    "provider_name":"Nvidia"}` with an empty `raw` — is "this endpoint is not serving this model at
    the moment", which is the same fact stated less politely. A game between two free models reached
    ply 55 and 1.17M tokens and was abandoned outright on one, because 404 had been classified as a
    malformed *request*. The request had been fine fifty-five times.

    `status_code` is carried so the reason a reader sees says which happened, rather than telling
    them they were rate-limited when they were not.

    OpenRouter distinguishes two things that arrive as the same status code, and they call for
    different responses:

    * `limit_source: "upstream_provider_shared_pool"` — the provider's own free pool is hot. Ours
      to wait out; the model and the account are fine.
    * an account limit — 20 requests a minute, or the day's allowance. Waiting helps only if we
      also stop making requests.

    `retry_after_seconds` is populated when the provider said. It usually does not: OpenRouter
    sends `Retry-After` only "when every attempted provider returned a retry hint", and a free
    model served by a single endpoint that returned none carries nothing at all. So the absence of
    a hint is the normal case, and a cooldown has to have an opinion of its own.
    """

    provider: str | None = None
    limit_source: str | None = None
    retry_after_seconds: float | None = None
    status_code: int | None = None
    timed_out: bool = False
    """The provider did not answer inside the per-call timeout.

    Not a rate limit and not an error — the endpoint simply is not serving us, which is the same
    conclusion a 429 reaches by a different route. It takes the same path (pause, cool down, come
    back) because that is the cheapest correct response: retrying means waiting the whole timeout
    again, ten more minutes of a worker held against an endpoint that has just failed to answer.

    A per-*turn* clock used to enforce this, and it measured the wrong thing. The turn's remaining
    seconds were handed to each call as its deadline, so a turn with 583 of 600 spent asked for a
    completion in 17 — below a free model's mean latency — and the doomed call was reported as
    `provider call exceeded 17s`. Ten minutes per call, applied where the bound belongs, needs no
    such arithmetic: `max_tool_iterations` already bounds how many calls a turn may make, and
    `max_completion_budget` bounds what it may generate.
    """

    account: bool = False
    """The refusal is about **our account**, not about the model or the endpoint serving it.

    A 402 (out of credits) and a 401 (key rejected) are both this shape: every endpoint will answer
    identically until a person does something, and nothing is wrong with the model we asked for.

    Two consequences, and the second is the one worth stating. The game **pauses** rather than
    burning five job attempts and abandoning — which is what both did, unclassified, and what would
    have ended every game in flight on a paid pool the moment credits ran out. And the endpoint is
    **not cooled down**: resting it would teach the matchmaker something false about a model that
    never failed, and would go on doing so after the account was fixed.
    """

    gated: bool = False
    """The model is not available to us **at all**, and waiting will not change that.

    `thinkingmachines/inkling-small:free` answers 403 *"only available on agentic harnesses — try
    plugging it into a coding agent or productivity app listed on openrouter.ai/apps"*. It is a
    distribution restriction, not a capability check: we *are* an agentic harness, and sending the
    app-attribution headers changes nothing, because the gate is an allow-list of registered apps.

    Nothing in the catalogue predicts it. That model advertises `tools`, a 1M window, `status: 0`
    and 100% uptime — indistinguishable from one that works. So it is discovered by being refused,
    and the only useful response is to stop offering it: 22 pairings in one pool died at ply 0
    against that model, because a generic error taught the matchmaker nothing.
    """

    @property
    def is_upstream_pool(self) -> bool:
        """The provider's shared free pool, rather than a limit on our account.

        False for a 404, which says nothing about a pool — only that one endpoint is not answering,
        so only that endpoint is cooled down.
        """
        return self.limit_source == "upstream_provider_shared_pool"

    def describe(self, model: str) -> str:
        """One line for the page, honest about which failure this was.

        **The account cases name the account, not the model.** "out of credits" attributed to
        `nemotron-3-super` would read as a fact about that model on a page whose whole job is
        publishing facts about models, and it is a fact about us.
        """
        where = self.provider or "its provider"
        if self.account:
            if self.status_code == 402:
                return "our provider account is out of credits (402)"
            return f"our provider credentials were refused ({self.status_code or 401})"
        if self.gated:
            return f"{model} is not available through this API (403)"
        if self.timed_out:
            return f"{where} did not answer in time for {model}"
        if self.status_code == 403:
            return f"{model} was refused by {where} (403)"
        if self.status_code == 404:
            return f"{model} is not being served by {where} right now (404)"
        if self.status_code == 503:
            return f"no endpoint meets the routing pinned for {model} (503)"
        source = f" ({self.limit_source})" if self.limit_source else ""
        return f"{model} rate-limited by {where}{source}"


@dataclass(slots=True)
class LlmError(Exception):
    """A provider call that could not be completed."""

    message: str
    status_code: int | None = None
    retryable: bool = False
    attempts: int = 1
    request: dict[str, Any] = field(default_factory=dict)
    #: Set when the failure was the provider asking us to come back later. Structured rather than
    #: left in `message`, because the orchestrator has to *act* on it — pause this game, cool this
    #: endpoint down — and a decision keyed off a substring search of an error string is a decision
    #: waiting to break the next time a provider rewords its 429.
    rate_limit: RateLimit | None = None
    #: The provider refused the *request*, not the moment — a 400, a context window too small for
    #: what was asked. Distinct from `retryable`, which asks whether to try this call again now;
    #: this asks whether trying it ever, in any shape, could work. Nothing about the request will
    #: differ on a later turn, so a job that requeues it is spending five attempts to be told the
    #: same thing five times.
    request_rejected: bool = False

    def __str__(self) -> str:
        return f"{self.message} (status={self.status_code}, attempts={self.attempts})"


def parse_tool_arguments(raw: str) -> tuple[dict[str, Any], str | None]:
    """Parse a tool-call argument blob, reporting failure rather than raising.

    Malformed arguments are a model failure we want to *measure*, so they travel through the
    system as data instead of blowing up the turn.
    """
    if not raw or not raw.strip():
        return {}, "empty arguments"
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        return {}, f"invalid JSON: {exc}"

    if not isinstance(parsed, dict):
        return {}, f"expected a JSON object, got {type(parsed).__name__}"
    return parsed, None
