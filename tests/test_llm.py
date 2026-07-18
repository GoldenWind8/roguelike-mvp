"""The tier registry contract: content names an abstract TIER, config binds
tiers to concrete models, and the binding is swappable from the environment
without touching code or content. These are the invariants that make
"different NPCs on different providers" safe — so they're tested, not assumed."""
import httpx
import pytest

from backend.config import GRID_BASE_URL
from backend.llm import LLMError, TIERS, complete_tier, spec_for, tier_available
from backend.persona import validate_persona
from tests.test_npcs import make_npc, make_persona


def _clear_tier_env(monkeypatch):
    """Remove any real .env tier bindings (the dev machine legitimately has
    some) so these tests observe true defaults, not the developer's config."""
    for tier in TIERS:
        for field in ("MODEL", "BASE_URL", "API_KEY", "AUTH"):
            monkeypatch.delenv(f"LLM_{tier.upper()}_{field}", raising=False)


def test_every_tier_defaults_to_the_grid(monkeypatch):
    # An unconfigured checkout behaves exactly as before tiers existed.
    _clear_tier_env(monkeypatch)
    for tier in TIERS:
        spec = spec_for(tier)
        assert spec.base_url == GRID_BASE_URL
        assert spec.auth_style == "apikey"


def test_env_rebinds_one_tier_without_touching_the_others(monkeypatch):
    _clear_tier_env(monkeypatch)
    monkeypatch.setenv("LLM_STANDARD_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("LLM_STANDARD_BASE_URL", "https://example.test/openai")
    monkeypatch.setenv("LLM_STANDARD_AUTH", "bearer")
    monkeypatch.setenv("LLM_STANDARD_API_KEY", "sk-test")

    standard = spec_for("standard")
    assert standard.model == "gemini-2.5-flash"
    assert standard.base_url == "https://example.test/openai"
    assert standard.headers() == {"Authorization": "Bearer sk-test"}
    # The other tiers keep their grid defaults — bindings are independent.
    assert spec_for("basic").base_url == GRID_BASE_URL
    assert spec_for("premium").base_url == GRID_BASE_URL


def test_unknown_tier_is_refused_with_the_valid_list():
    with pytest.raises(LLMError, match="basic"):
        spec_for("galactic")


def test_tier_available_reflects_the_key(monkeypatch):
    monkeypatch.setenv("LLM_BASIC_API_KEY", "")
    assert not tier_available("basic")
    monkeypatch.setenv("LLM_BASIC_API_KEY", "k")
    assert tier_available("basic")


async def test_complete_tier_asks_for_the_bound_model(monkeypatch):
    # The injected client is the test seam: transport is faked, but the MODEL
    # in the request body must still come from the tier's binding.
    monkeypatch.setenv("LLM_PREMIUM_MODEL", "big-brain-9000")
    seen = {}

    def handler(request):
        import json
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    client = httpx.AsyncClient(base_url="https://grid.test",
                               transport=httpx.MockTransport(handler))
    out = await complete_tier("premium", [{"role": "user", "content": "hi"}],
                              max_tokens=64, timeout=5.0, client=client)
    assert out == "{}"
    assert seen["model"] == "big-brain-9000"


# --- tier is content: the persona gate enforces the closed vocabulary ------------


def test_persona_tier_valid_and_absent_pass():
    validate_persona(make_persona(tier="premium"))
    validate_persona(make_persona())  # absent -> cheapest tier by default


def test_persona_tier_outside_the_vocabulary_fails():
    with pytest.raises(ValueError, match="tier"):
        validate_persona(make_persona(tier="galactic"))


async def test_dialogue_routes_by_the_npcs_tier(monkeypatch):
    # Two NPCs, two tiers, one provider object — each line is answered by the
    # model its tier is bound to. This is the plug-and-play invariant.
    from backend.dialogue import CannedProvider, GridProvider

    monkeypatch.setenv("LLM_BASIC_MODEL", "cheap-chat")
    monkeypatch.setenv("LLM_PREMIUM_MODEL", "big-brain-9000")
    models_asked = []

    def handler(request):
        import json
        models_asked.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"say": "Hm.", "effects": []}'}}]})

    client = httpx.AsyncClient(base_url="https://grid.test",
                               transport=httpx.MockTransport(handler))
    provider = GridProvider(fallback=CannedProvider(), client=client)

    filler = make_npc()                                        # no tier -> basic
    boss = make_npc(persona=make_persona(tier="premium"))
    await provider.reply(filler, "Hero", "hi")
    await provider.reply(boss, "Hero", "hi")
    assert models_asked == ["cheap-chat", "big-brain-9000"]
