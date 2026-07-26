# Kingdom A Overnight Art Batch

Status: active visual-development batch

## Goal

Build one coherent, reusable art library for Kingdom A: the Bohemian
crownland behind the Red King conspiracy. The kingdom must appear credible,
inhabited, and administratively powerful before it appears sinister.

This batch is visual pre-production. Concept maps guide later room graphs and
JSON layouts; they are not shipped as terrain backgrounds. Production assets
are isolated sprites for the existing neutral-grid renderer.

## Locked references

- Capital anchor:
  `frontend-react/public/art/concepts/kingdom-a/kingdom-a-capital-concept-v1.png`
- Regional anchor:
  `frontend-react/public/art/concepts/kingdom-a/kingdom-a-region-concept-v1.png`
- Global rendering-language reference:
  `frontend-react/public/art/concepts/oakrun-town-square-concept-v1.png`

## Visual bible

- Architecture: Bohemian crownland, late-medieval and early court-scientific;
  steep slate roofs, tall narrow houses, pale worn plaster, fortified palace
  heights, bridges, observatories, workshops, courtyards, and coppered spires.
- Palette: wet charcoal slate, soot-dark timber, pale plaster, aged honey
  stone, oxidized copper green, muted burgundy, and rare deep-red accents.
- Mood: cold silver daylight after rain, warm inhabited windows, restrained
  unease. The state looks functional and dignified, not openly evil.
- Alchemical language: plausible furnaces, alembics, armillary geometry,
  workshops, sealed service doors, and court patronage. No glowing runes or
  generic magic.
- Production view: front-facing or high three-quarter, non-isometric, strong
  silhouette, no tile base.

## Shared production block

Use case: stylized-concept

Input images: Image 1 is the locked Kingdom A capital concept. Use it only as
the style, palette, architecture, material, lighting, and world-consistency
reference.

Style/medium: polished hand-painted grounded dark-folklore game sprite,
matching the reference's realistic material language; readable silhouette;
non-isometric; no tile base.

Lighting/mood: cool overcast silver key light from upper left with restrained
warm reflected light; dignified, functional, slightly uneasy.

Scene/backdrop: perfectly flat solid `#ff00ff` chroma-key background for local
removal.

Constraints: one isolated subject only; complete subject visible; background
uniformly `#ff00ff` with no gradient, texture, floor plane, scenery, cast
shadow, contact shadow, reflection, or haze; generous padding; no attached
ground patch, readable text, logo, watermark, glowing magic, runes, UI, or
unrequested objects; do not use `#ff00ff` in the subject.

Avoid: isometric diamond base, generic high fantasy, gothic-horror theme park,
steampunk machinery, excessive skulls, neon magic, crimson everywhere.

## Queue and acceptance gates

Every production asset is generated individually. A source chroma image is
kept in `art-source/generated/kingdom-a/`; the alpha-cleaned candidate goes in
the matching frontend folder. Do not add an asset to game catalogues merely
because it exists: integration follows human review.

### Phase 1 — concepts and layouts

- [x] `kingdom-a-capital-concept-v1`: visual anchor
- [x] `kingdom-a-region-concept-v1`: regional geography anchor
- [ ] `kingdom-a-capital-district-layout-v1`: playable civic-center room plan
- [ ] `kingdom-a-bridge-town-layout-v1`: reusable settlement layout
- [ ] `kingdom-a-field-site-layout-v1`: concealed research-site layout

### Phase 2 — landmark kit

- [ ] `crown-palace-gate-v1`: 5x4 visual, 4x2 logical
- [ ] `royal-observatory-v1`: 4x4 visual, 3x2 logical
- [ ] `fortified-river-gate-v1`: 4x4 visual, 3x2 logical
- [ ] `court-workshop-exterior-v1`: 4x3 visual, 4x2 logical
- [ ] `crown-hunting-lodge-v1`: 4x3 visual, 4x2 logical
- [ ] `field-site-service-house-v1`: 4x3 visual, 3x2 logical

### Phase 3 — structural and interactive objects

- [ ] `copper-street-lantern-v1`: 1x2 visual, 1x1 logical
- [ ] `crown-notice-kiosk-v1`: 2x2 visual, 2x1 logical
- [ ] `armillary-monument-v1`: 2x3 visual, 2x2 logical
- [ ] `rainwater-cistern-v1`: 2x2 visual, 2x2 logical
- [ ] `sealed-service-door-v1`: 2x2 visual, 2x1 logical
- [ ] `workshop-crate-cluster-v1`: 2x2 visual, 2x2 logical
- [ ] `portable-alchemical-furnace-v1`: 2x2 visual, 1x1 logical
- [ ] `royal-road-marker-v1`: 1x2 visual, 1x1 logical

### Phase 4 — people

- [ ] `crown-guard-world-v1`: 1x2 visual, 1x1 logical
- [ ] `court-clerk-world-v1`: 1x2 visual, 1x1 logical
- [ ] `journeyman-alchemist-world-v1`: 1x2 visual, 1x1 logical
- [ ] `river-porter-world-v1`: 1x2 visual, 1x1 logical
- [ ] `red-order-agent-world-v1`: 1x2 visual, 1x1 logical
- [ ] `mining-valley-guide-world-v1`: 1x2 visual, 1x1 logical

### Phase 5 — threats and evidence

- [ ] `crown-mastiff-world-v1`: 2x1 visual, 1x1 logical
- [ ] `failed-court-homunculus-world-v1`: 1x2 visual, 1x1 logical
- [ ] `field-site-order-crate-v1`: 1x1 visual, 1x1 logical
- [ ] `dated-courier-ledger-v1`: item icon
- [ ] `red-glass-ampoule-v1`: item icon
- [ ] `broken-armillary-key-v1`: item icon

## Overnight operating rules

1. Work in queue order and complete at most two production assets per wake-up.
2. Use the capital concept as the reference for every asset.
3. For layout concepts, use the regional concept as an additional geography
   reference and do not use chroma keying.
4. For production assets, generate a flat-magenta source, copy it into
   `art-source/generated/kingdom-a/`, remove the chroma key with the installed
   image-generation helper, and validate transparent corners and silhouette.
5. If an image contains multiple subjects, scenery, illegible geometry, or a
   non-flat background, retry it once with one targeted correction.
6. Never overwrite a versioned file. Use `v2` for a retry that is retained.
7. Update this checklist after each accepted candidate and record its exact
   prompt in `PROMPTS.md`.
8. Stop after Phase 5 or at 08:00 Africa/Johannesburg, whichever comes first.
9. Do not edit game code, content catalogues, or room JSON during this batch.

