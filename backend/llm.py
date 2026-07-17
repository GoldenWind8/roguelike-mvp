"""Shared LLM layer: the tier registry (who answers) + the transport (how).

Two layers, and the boundary between them is the point:

  - TIER REGISTRY: callers name a TIER — an abstract capability level
    ("basic" / "standard" / "premium"), never a provider or model. Config
    binds each tier to a concrete model via env vars, so swapping every
    "standard" call in the game onto a better model is a one-line .env edit —
    no code changes, no content migrations.
  - TRANSPORT (`complete`): POST the messages, surface transport failures and
    the empty-completion reasoning-budget trap as LLMError, hand back the raw
    content string.

What stays with each CALLER, on purpose: prompts, parsing, max_tokens,
timeout, and above all what a failure means — dialogue degrades to a canned
line and never blocks the sim; the procgen harness fails loudly into its
status banner. A tier decides who answers, never what a failure means.

Scope note: the transport speaks OpenAI-style `/v1/chat/completions` only —
which today covers the grid, OpenAI, and Gemini's OpenAI-compat endpoint.
A provider with a different wire shape (e.g. Claude's native Messages API)
is the revisit-trigger for a `provider` field on ModelSpec and a dispatch
in `complete_tier`; until one exists, that field would be config nothing
reads.
"""
import os
from dataclasses import dataclass

import httpx

from backend.config import DIALOGUE_MODEL, GRID_API_KEY, GRID_BASE_URL


class LLMError(Exception):
    """The completion never yielded usable content. `empty` marks the
    reasoning-budget case (HTTP 200 but no visible text — the model spent
    max_tokens on hidden thinking), which callers typically log without a
    traceback and cure by raising their token budget."""

    def __init__(self, message: str, *, empty: bool = False):
        super().__init__(message)
        self.empty = empty


# --- tiers: who answers ---------------------------------------------------------

# The closed tier vocabulary. Content references these (an NPC persona says
# tier "premium"), so the set is validated wherever content enters the game
# (persona gate) and should grow rarely and deliberately — every escape hatch
# around it is a future content migration.
TIERS = ("basic", "standard", "premium")
DEFAULT_TIER = "basic"


@dataclass(frozen=True)
class ModelSpec:
    """One concrete model binding: where to call, how to authenticate, which
    model to ask for. Frozen and hashable on purpose — specs key the client
    cache below. Note what is NOT here: max_tokens and timeout are the
    CALLER's knobs (a dialogue line is short and must never stall the sim,
    whatever tier the NPC is), so they ride on each call, not on the spec."""
    base_url: str
    api_key: str
    model: str
    auth_style: str  # "apikey" (the grid) | "bearer" (OpenAI, Gemini compat)

    def headers(self) -> dict:
        if self.auth_style == "bearer":
            return {"Authorization": f"Bearer {self.api_key}"}
        return {"apikey": self.api_key}


def spec_for(tier: str) -> ModelSpec:
    """Resolve a tier to its binding. Every tier defaults to the grid (so an
    unconfigured checkout behaves exactly as before tiers existed); each field
    is overridable per tier from the environment:

        LLM_STANDARD_MODEL=gemini-2.5-flash
        LLM_STANDARD_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
        LLM_STANDARD_API_KEY=...
        LLM_STANDARD_AUTH=bearer

    Reads env at call time, not import time — tests can monkeypatch a binding,
    and a dev editing .env under --reload sees it without hunting down module
    state."""
    if tier not in TIERS:
        raise LLMError(f"unknown tier '{tier}' — valid tiers: {list(TIERS)}")

    def env(field: str, default: str) -> str:
        return os.getenv(f"LLM_{tier.upper()}_{field}", default)

    return ModelSpec(
        base_url=env("BASE_URL", GRID_BASE_URL),
        api_key=env("API_KEY", GRID_API_KEY),
        model=env("MODEL", DIALOGUE_MODEL),  # grid's "auto" unless bound
        auth_style=env("AUTH", "apikey"),
    )


def tier_available(tier: str) -> bool:
    """A tier is callable when its binding has an API key."""
    return bool(spec_for(tier).api_key)


# One long-lived client per distinct binding (connection reuse across calls),
# keyed by the frozen spec itself — rebinding a tier via env transparently
# yields a fresh client. Never closed: they live as long as the process, the
# same lifecycle as a DB engine.
_clients: dict[ModelSpec, httpx.AsyncClient] = {}


def _client_for(spec: ModelSpec) -> httpx.AsyncClient:
    client = _clients.get(spec)
    if client is None:
        client = _clients[spec] = httpx.AsyncClient(
            base_url=spec.base_url, headers=spec.headers())
    return client


async def complete_tier(tier: str, messages: list[dict], *, max_tokens: int,
                        timeout: float, client: httpx.AsyncClient | None = None) -> str:
    """The call every feature makes: resolve the tier, complete on its model.
    `max_tokens` and `timeout` are the caller's task policy (see ModelSpec).
    `client` is the test seam — an injected client (httpx.MockTransport)
    bypasses the cache but still uses the tier's model, so tests can assert
    tier routing without touching the network."""
    spec = spec_for(tier)
    chosen = client if client is not None else _client_for(spec)
    return await complete(chosen, messages, model=spec.model,
                          max_tokens=max_tokens, timeout=timeout)


# --- transport: how it's asked --------------------------------------------------


async def complete(client: httpx.AsyncClient, messages: list[dict], *,
                   model: str, max_tokens: int, timeout: float | None = None) -> str:
    """One chat completion; returns the stripped content string. JSON mode is
    always requested (every current caller wants a JSON envelope and it stops
    grid models drifting into bare prose) — but the RETURN is raw text: weak
    models still occasionally ignore the format, and what to do about that is
    caller policy, not transport. `timeout` rides per-request because the
    cached tier clients are shared by callers with different patience."""
    request_opts = {} if timeout is None else {"timeout": timeout}
    try:
        response = await client.post("/v1/chat/completions", json={
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }, **request_opts)
        response.raise_for_status()
        choice = response.json()["choices"][0]
    except Exception as e:
        raise LLMError(f"LLM call failed: {e}") from e

    content = choice.get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise LLMError(
            f"empty completion (finish_reason={choice.get('finish_reason')})",
            empty=True,
        )
    return content.strip()


def strip_code_fence(content: str) -> str:
    """Remove a single leading/trailing ``` fence (optionally ```json) so the
    JSON inside parses. Leaves un-fenced content untouched. Lives with the
    transport because fenced JSON is a provider quirk, not caller policy."""
    stripped = content.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    lines = lines[1:]  # drop the opening ```/```json line
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
