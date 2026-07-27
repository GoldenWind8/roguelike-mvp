# Drazna Production v1 Prompt Record

Generated with the built-in image-generation workflow on 2026-07-26 and
2026-07-27. Each distinct asset used one generation call. Earlier Drazna city
and character concepts were references for world style or character identity.

## Shared world-sprite prompt

Production world figures use polished hand-painted dark-folklore game art,
grounded late-medieval realism, natural proportions, strong occupational
silhouettes, a neutral three-quarter pose viewed slightly from above, complete
footwear and props, and a bottom-center anchor. Character identity, age, body
type, clothing, and role props are preserved from accepted concepts where
available.

## Shared object and landmark prompt

Production objects and exteriors use coherent late-medieval construction,
slight elevated three-quarter orthographic-like views, complete isolated
silhouettes, readable entrances or interaction surfaces, and bottom-center
anchors. Drazna materials are wet black timber, basalt, slate, rope, tarnished
metal, restrained crimson fabric, and small amber lantern details. No terrain
or water is baked into an asset.

## Shared item-icon prompt

Icons contain exactly one item or one tightly bound bundle, centered in a
square close three-quarter composition with even padding. Materials are
realistic and silhouettes are simplified for readability at approximately
48 pixels. Rarity frames, labels, glow, particles, hands, and environments are
excluded.

## Shared chroma and negative prompt

The backdrop is perfectly flat solid `#ff00ff` for local removal. Magenta is
excluded from the subject. Every prompt prohibits surrounding environment,
ground or floor planes, cast and contact shadows, reflections, text, logos,
watermarks, UI, decorative frames, modern objects, and unrelated subjects.

## Enemy and climax prompts

The five enemy PNGs contain no embedded prompt metadata. The wording below was
reconstructed at acceptance from each authored subject and accepted output and
is the canonical exact subject record. Each block was combined with the shared
world-sprite and chroma/negative language above in built-in ImageGen mode.

### Flood-Hollow Warden

> Flood-Hollow Warden, a tall drowned Drazna sluice guard in overlapping
> blackened iron plates and bundled flood reeds; a conical riveted helm with a
> barred visor completely hides the face; wet rope and chain bind the armor;
> broken reed stakes rise behind both shoulders; one hand carries a short
> practical flood axe and the other an oval iron shield whose central fitting
> is a vertical sluice-pressure gauge; soaked black silt drips from the layered
> hem; full figure, heavy defensive silhouette, no visible skin.

### Drowned Lamplighter / silt-drowned ferryman

> Silt-drowned Drazna ferryman and lamplighter, an older broad bearded quay
> worker with water-plastered hair and a weary human face; patched black
> oilskin smock, waders, rope harness, iron fittings, and soaked practical
> boots; a hooked ferry pole in one hand and a caged amber quay lantern in the
> other; a small netted river weight hangs at the hip; black silt films the
> clothing and drips from the hems; full figure, stooped working posture, not a
> spectral boatman.

### Siltbound Salvager

> Siltbound Undertide salvager, a former Drazna pressure diver in a dented
> riveted copper diving hood with one cracked circular faceplate; tarred coat,
> thick gloves, strapped boots, rope harness, salvage net, and a blackened air
> hose curling high over one shoulder; one hand carries a compact square-headed
> salvage maul; wet black silt clings to every seam and leaks from the broken
> viewport; full figure, weighty industrial silhouette, no fantasy armor.

### Black-Silt Leech Colony

> Black-silt leech colony from the Undertide, a low wide heap of many thick wet
> lamprey-like leeches tangled around broken quay timber, working rope, a
> cracked brass pressure gauge, and one small valve fitting; several round
> toothless sucker mouths face outward while blind pale sensory nodules catch
> the light; oily black silt binds the mass and drips beneath it; one coherent
> grounded colony silhouette, no humanoid body, no gore, no giant single worm.

### Odran Third-Bell, the Sluicebound

> Odran Third-Bell, the Sluicebound at Gate Seven—not King Odran—a huge
> exhausted older Drazna floodwarden physically bound to a wooden-and-iron
> sluice chain drum strapped across his back; worn floodwarden uniform,
> practical leather, broken iron shoulder plates, rope lashings, and a faded
> Third Bell shield badge; his lined human face remains visible; black silt
> webs one bare forearm into the mechanism; he braces on a long key-topped
> warden staff while a chained rectangular counterweight hangs from the other
> side; full figure, tragic regional-climax silhouette, restrained rather than
> regal or undead-king imagery.

## Enemy source and final paths

| Accepted subject | Chroma source | Transparent final | Visual size |
| --- | --- | --- | --- |
| Flood-Hollow Warden | `art-source/generated/drazna/production/enemies/flood-hollow-warden-v1-chroma.png` | `frontend-react/public/art/world/enemies/drazna/flood-hollow-warden-v1.webp` | `1x2` |
| Drowned Lamplighter / ferryman | `art-source/generated/drazna/production/enemies/silt-drowned-ferryman-v1-chroma.png` | `frontend-react/public/art/world/enemies/drazna/silt-drowned-ferryman-v1.webp` | `1x2` |
| Siltbound Salvager | `art-source/generated/drazna/production/enemies/siltbound-salvager-v1-chroma.png` | `frontend-react/public/art/world/enemies/drazna/siltbound-salvager-v1.webp` | `1x2` |
| Black-Silt Leech Colony | `art-source/generated/drazna/production/enemies/black-silt-leech-colony-v1-chroma.png` | `frontend-react/public/art/world/enemies/drazna/black-silt-leech-colony-v1.webp` | `2x2` |
| Odran Third-Bell / Sluicebound | `art-source/generated/drazna/production/enemies/sluicebound-gate-seven-v1-chroma.png` | `frontend-react/public/art/world/enemies/drazna/sluicebound-gate-seven-v1.webp` | `2x3` |

The requested source backdrop was flat `#ff00ff`. Accepted generated borders
contain small near-magenta variation, so the installed imagegen helper used
the exact conversion flags
`--auto-key border --transparent-threshold 12 --opaque-threshold 220 --soft-matte --despill`.
Sources are kept for reprocessing; only alpha WebP finals ship to the client.
All forty Drazna cutouts total 6,983,714 bytes in delivery form, while
their full-resolution PNG/chroma masters remain here.

## Gate Seven chain drum prompt

> Gate Seven chain drum, an enormous ancient sluice mechanism viewed in a
> readable slightly elevated three-quarter orthographic angle, designed for a
> 3x3 tile presentation. A massive blackened iron drum is wound with thick wet
> chains; torn dark floodwarden uniform cloth and glass-black silt webbing are
> caught in the mechanism; a pale human hand remains fixed around the brass
> emergency pawl, with no full body and no gore. Fourteen deliberate strike
> dents circle the drum, with the last five subtly filled by old memorial wax.
> Rain-dark slate iron, tarnished brass, black lake silt, faint cold blue
> reflections, restrained amber highlights, strong readable silhouette.

| Accepted subject | Chroma source | Transparent final | Visual size |
| --- | --- | --- | --- |
| Gate Seven Chain Drum | `art-source/generated/drazna/production/objects/gate-seven-chain-drum-v1-chroma.png` | `frontend-react/public/art/world/objects/drazna/gate-seven-chain-drum-v1.webp` | `3x3` |

## NPC subjects

- Queen Mara Vey: broken-silver crown, black and restrained-crimson court
  clothing, sealed ledger, guarded authority.
- Ilya Sorn: junior floodwarden, plans, tools, river-key clasp, black-silt
  glove, sympathetic guilt.
- Nera Bell: secular memorial archivist, name tablets, records, chain of
  office, disciplined grief.
- Olek Var: salvage captain, oilskin document case, claim tags, calculating
  charm without pirate styling.
- Pava Mirek: powerful roofwright, apron, measuring rule, hammer, crimson
  wrist strip, community leadership.
- Vasko Mirek: young Undertide diver, waxed coat, rope harness, canvas air
  hood, census tablet, exhausted determination.
- Vesna Korr: Low Lantern route keeper, clouded eye, practical key ring,
  knotted route cord, shuttered lantern.
- Alin Vey: reformist royal heir, lake-blue mantle, court documents, no crown
  or weapon.

## Interactive-object subjects

- Narrow Drazna ferry skiff
- Weatherproof Amber Quay vendor stall
- Sluice handwheel and chain mechanism
- House of Names public tablet rack
- Three-bay roofwright scaffold
- Compact hand-winched salvage crane
- Covered Low Lantern cache
- Banded floodline memorial
- Crown ledger plinth beneath a wrought-metal canopy

### Crown ledger plinth prompt

Generated with the built-in image-generation workflow on 2026-07-27. The
Palace of Still Water exterior was supplied as a style reference only.

> A freestanding dark-stone crown-ledger plinth holding one large open flood
> ledger beneath a narrow wrought-metal canopy, with a small red-glass
> drainage channel built into the base. The severe, water-worn civic archive
> uses slate-black stone, tarnished iron, restrained burgundy cloth tabs, and
> tiny warm candle accents. It is isolated in a polished realistic
> dark-fantasy game-asset render with a readable compact silhouette and no
> people, room, floor, cast shadow, text, logo, or watermark.

| Accepted subject | Chroma source | Transparent final | Visual size |
| --- | --- | --- | --- |
| Crown Ledger Plinth | `art-source/generated/drazna/production/objects/crown-ledger-plinth-v1-chroma.png` | `frontend-react/public/art/world/objects/drazna/crown-ledger-plinth-v1.webp` | `2x2` |

The source used a flat `#00ff00` backdrop. The installed helper sampled the
border and used `--transparent-threshold 12 --opaque-threshold 220
--soft-matte --despill`.

### Memorial of the First Public Record prompt

Generated with the built-in image-generation workflow on 2026-07-27. The
floodline memorial and Palace of Still Water exterior were supplied as style
and material references only.

> A broad cracked slab of smoke-black glass in a low basalt civic frame,
> carrying shallow abstract name-cuts, one visibly removed-and-recut final
> name area, a thin old floodline, empty tablet hooks, and three extinguished
> votive cups. The isolated monument uses a polished realistic dark-fantasy
> game-asset treatment, restrained weathering, and a bottom-center anchor. It
> contains no readable text, date, origin symbol, crown, arrow, people,
> religious iconography, logo, or watermark.

| Accepted subject | Chroma source | Transparent final | Visual size |
| --- | --- | --- | --- |
| Memorial of the First Public Record | `art-source/generated/drazna/production/objects/first-record-memorial-v1-chroma.png` | `frontend-react/public/art/world/objects/drazna/first-record-memorial-v1.webp` | `2x3` |

The source used a flat `#00ff00` backdrop. The installed helper sampled the
border and used `--transparent-threshold 12 --opaque-threshold 220
--soft-matte --despill`.

## Item subjects

- Odran's asymmetric black floodgate key
- Silt-stained missing census tablet
- Oilskin-wrapped sealed ledger page
- Shuttered-lantern access token
- Drowned family signet ring
- Rolled floodwarden repair kit
- Smoked-eel blackbread travel meal
- Sealed ceramic black-silt sample jar

## Landmark subjects

- Palace of Still Water above three sealed sluice arches
- Crown Sluice gatehouse with hoist towers and chain drums
- Drowned Bell civic tower with visible waterline staining
- Secular House of Names memorial archive
- Eel and Ember Inn on a reinforced quay foundation
- Inhabited Walking Bridge housing segment
- Concealed Dry Dock retaining-wall entrance
- Birch Stair memorial arch with an open passage
