"""Persistent-frontier selection and rising-luck region discovery.

This module decides *what* a frontier exit becomes. Geometry remains in the
validated preset registry, and database persistence belongs to the caller.
All rolls are hash-derived so discovering the same exit in the same world is
stable across retries and restarts.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib


@dataclass(frozen=True)
class RegionCandidate:
    id: str
    label: str
    min_depth: int = 3
    base_weight: int = 1
    required_fact: str | None = None


@dataclass(frozen=True)
class FrontierPressure:
    misses: int = 0
    authored_regions_found: int = 0

    def after_generated_room(self) -> "FrontierPressure":
        return FrontierPressure(
            misses=self.misses + 1,
            authored_regions_found=self.authored_regions_found,
        )

    def after_authored_region(self) -> "FrontierPressure":
        return FrontierPressure(
            misses=0,
            authored_regions_found=self.authored_regions_found + 1,
        )


@dataclass(frozen=True)
class DiscoveryPolicy:
    base_chance: float = 0.04
    chance_per_miss: float = 0.045
    depth_bonus: float = 0.008
    soft_pity_after: int = 10
    soft_pity_bonus: float = 0.08
    hard_pity_after: int = 18
    maximum_chance: float = 0.85


@dataclass(frozen=True)
class ExitOutcome:
    kind: str  # generated_room | authored_region
    roll: float
    chance: float
    region_id: str | None
    next_pressure: FrontierPressure


def authored_region_chance(
    *,
    pressure: FrontierPressure,
    depth: int,
    policy: DiscoveryPolicy = DiscoveryPolicy(),
) -> float:
    if depth < 0 or pressure.misses < 0:
        raise ValueError("depth and misses must be non-negative")
    if pressure.misses >= policy.hard_pity_after:
        return 1.0
    chance = (
        policy.base_chance
        + pressure.misses * policy.chance_per_miss
        + depth * policy.depth_bonus
    )
    if pressure.misses >= policy.soft_pity_after:
        chance += (
            pressure.misses - policy.soft_pity_after + 1
        ) * policy.soft_pity_bonus
    return max(0.0, min(policy.maximum_chance, chance))


def resolve_frontier_exit(
    *,
    world_seed: int,
    exit_key: str,
    depth: int,
    pressure: FrontierPressure,
    candidates: tuple[RegionCandidate, ...] | list[RegionCandidate],
    known_facts: frozenset[str] = frozenset(),
    policy: DiscoveryPolicy = DiscoveryPolicy(),
) -> ExitOutcome:
    """Resolve an unexplored exit once and reproducibly.

    A region is eligible only after its minimum depth and optional world fact.
    If none is eligible, the exit creates another generated room and still
    increments pressure.
    """
    if not exit_key:
        raise ValueError("exit_key must be non-empty")
    eligible = [
        region
        for region in candidates
        if depth >= region.min_depth
        and (region.required_fact is None or region.required_fact in known_facts)
    ]
    chance = authored_region_chance(
        pressure=pressure,
        depth=depth,
        policy=policy,
    ) if eligible else 0.0
    roll = _stable_unit(world_seed, exit_key, depth, pressure.misses)
    if eligible and roll < chance:
        picked = _weighted_region(
            eligible,
            world_seed=world_seed,
            exit_key=exit_key,
            depth=depth,
        )
        return ExitOutcome(
            kind="authored_region",
            roll=roll,
            chance=chance,
            region_id=picked.id,
            next_pressure=pressure.after_authored_region(),
        )
    return ExitOutcome(
        kind="generated_room",
        roll=roll,
        chance=chance,
        region_id=None,
        next_pressure=pressure.after_generated_room(),
    )


@dataclass(frozen=True)
class FrontierRecipe:
    preset: str
    params: dict
    biome: str
    mood_tags: tuple[str, ...]
    encounter_tags: tuple[str, ...]


_BIOME_ARCHETYPES = {
    "amberfall_fields": (
        "pilgrim_road",
        "braided_river",
        "old_battlefield",
        "caravan_remains",
    ),
    "veyr_approach": (
        "rotwood",
        "black_marsh",
        "grave_moor",
        "old_battlefield",
    ),
    "drazna_marches": (
        "braided_river",
        "ravine_crossing",
        "black_marsh",
        "pilgrim_road",
    ),
    "rouvray_lowlands": (
        "grave_moor",
        "braided_river",
        "caravan_remains",
        "pilgrim_road",
    ),
    "deep_frontier": (
        "ravine_crossing",
        "rotwood",
        "grave_moor",
        "caravan_remains",
    ),
}


def frontier_recipe(
    *,
    world_seed: int,
    node_key: str,
    depth: int,
    biome: str,
) -> FrontierRecipe:
    """Choose a reproducible creative room recipe for a frontier node."""
    archetypes = _BIOME_ARCHETYPES.get(biome)
    if archetypes is None:
        raise KeyError(f"unknown frontier biome {biome!r}")
    roll = _stable_u64(world_seed, node_key, depth, biome)
    archetype = archetypes[roll % len(archetypes)]
    entries = 2 + ((roll >> 8) % 3)
    secrets = 1 if depth < 3 else 1 + ((roll >> 12) % 2)
    enemies = min(14, 3 + depth // 2 + ((roll >> 16) % 4))
    chests = min(4, 1 + depth // 6 + ((roll >> 20) % 2))
    return FrontierRecipe(
        preset="frontier_wilds",
        params={
            "archetype": archetype,
            "entries": entries,
            "secrets": secrets,
            "enemies": enemies,
            "chests": chests,
            "barrels": 1 if archetype in {"old_battlefield", "caravan_remains"} else 0,
        },
        biome=biome,
        mood_tags=_mood_tags(archetype, depth),
        encounter_tags=_encounter_tags(archetype, depth),
    )


def _mood_tags(archetype: str, depth: int) -> tuple[str, ...]:
    tags = {
        "pilgrim_road": ("travel", "faith", "abandonment"),
        "braided_river": ("water", "crossing", "isolation"),
        "ravine_crossing": ("height", "exposure", "ambush"),
        "old_battlefield": ("war", "aftermath", "salvage"),
        "rotwood": ("rot", "memory", "dread"),
        "black_marsh": ("rot", "water", "misdirection"),
        "grave_moor": ("dead", "names", "ritual"),
        "caravan_remains": ("travel", "loss", "shelter"),
    }[archetype]
    return (*tags, "deep" if depth >= 8 else "border")


def _encounter_tags(archetype: str, depth: int) -> tuple[str, ...]:
    base = {
        "pilgrim_road": ("travellers", "bandits"),
        "braided_river": ("beasts", "ambush"),
        "ravine_crossing": ("bandits", "falling_hazard"),
        "old_battlefield": ("revenants", "scavengers"),
        "rotwood": ("homunculi", "feral_beasts"),
        "black_marsh": ("homunculi", "lost_travellers"),
        "grave_moor": ("revenants", "grave_robbers"),
        "caravan_remains": ("bandits", "survivors"),
    }[archetype]
    return (*base, "elite" if depth >= 10 else "ordinary")


def _weighted_region(
    candidates: list[RegionCandidate],
    *,
    world_seed: int,
    exit_key: str,
    depth: int,
) -> RegionCandidate:
    total = sum(max(0, candidate.base_weight) for candidate in candidates)
    if total <= 0:
        return sorted(candidates, key=lambda region: region.id)[0]
    value = _stable_u64("region", world_seed, exit_key, depth) % total
    for candidate in sorted(candidates, key=lambda region: region.id):
        weight = max(0, candidate.base_weight)
        if value < weight:
            return candidate
        value -= weight
    return candidates[-1]


def _stable_u64(*parts: object) -> int:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(
        hashlib.blake2b(material, digest_size=8).digest(), "big"
    )


def _stable_unit(*parts: object) -> float:
    return _stable_u64(*parts) / (2**64 - 1)
