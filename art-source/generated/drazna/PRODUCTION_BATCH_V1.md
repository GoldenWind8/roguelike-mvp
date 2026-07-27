# Drazna Production Asset Batch v1

Purpose: establish a reusable visual library for the five surviving districts
of Drazna. Every asset supports at least one location, NPC, interaction, or
thread in the Low Water story.

All final game assets use isolated silhouettes with transparent backgrounds.
The generated chroma-key sources remain under `art-source/generated/drazna/`;
production cutouts go under `frontend-react/public/art/`.

## Shared visual language

- Grounded hand-painted dark-folklore game art
- Late-medieval Baltic and Carpathian echoes without copying a real culture
- Peat black, lake blue-gray, bone linen, tarnished silver, restrained crimson,
  and amber lantern light
- Wet black timber, basalt, slate, waxed cloth, rope, and practical ironwork
- Slight three-quarter elevation matching the neutral orthogonal game grid
- No baked terrain, text, UI, cast shadows, modern objects, or generic magical
  effects

## NPC world sprites

Shared contract: logical footprint `1x1`, visual footprint `1x2`, bottom-center.

- [x] `queen_mara_vey` — High Crown; ruler carrying the original cover-up
- [x] `ilya_sorn` — Crown Sluice; junior floodwarden who opened the channel
- [x] `nera_bell` — Birch Heights; keeper of the erased census
- [x] `olek_var` — Lantern Quays / Undertide; salvage captain and intermediary
- [x] `pava_mirek` — Walking Ward; roofwright and community organizer
- [x] `vasko_mirek` — Undertide; missing diver carrying evidence
- [x] `vesna_korr` — Undertide; Low Lantern organizer controlling dry routes
- [x] `alin_vey` — High Crown / Birch Heights; royal heir demanding disclosure

## Enemy and climax sprites

Shared contract: isolated enemy silhouettes, bottom-center anchor. Ordinary
humanoids use visual footprint `1x2`; the colony and regional climax expand
only their visual presentation, not the neutral grid language.

- [x] `flood_hollow_warden` — reed-and-iron Crown Sluice guard; visual `1x2`
- [x] `drowned_lamplighter` / silt-drowned ferryman — Lantern Quays route
  keeper carrying the last amber light; visual `1x2`
- [x] `siltbound_salvager` — Undertide pressure diver with a salvage maul;
  visual `1x2`
- [x] `black_silt_leech_colony` — low, wide pressure-gauge infestation;
  visual `2x2`
- [x] `sluicebound_gate_seven` — Odran Third-Bell, the Gate Seven regional
  climax; visual `2x3`

## Interactive objects

- [x] `drazna_ferry_skiff` — transport and rescue; logical `3x1`, visual `4x2`
- [x] `amber_quay_stall` — Lantern Quays vendor; logical `2x2`, visual `3x3`
- [x] `sluice_control_wheel` — water-level interaction; logical `2x1`, visual `2x3`
- [x] `house_of_names_rack` — memorial clue surface; logical `2x1`, visual `2x3`
- [x] `roofwright_scaffold` — repair and traversal; logical `3x1`, visual `4x3`
- [x] `salvage_crane` — Mud Crown access; logical `2x2`, visual `3x4`
- [x] `low_lantern_cache` — criminal loot container; logical `1x1`, visual `2x2`
- [x] `floodline_memorial` — Birch Heights clue landmark; logical `2x1`, visual `2x3`
- [x] `gate_seven_chain_drum` — evidence-gated regional climax mechanism;
  logical `3x2`, visual `3x3`
- [x] `crown_ledger_plinth` — Palace and High Crown civic archive focal
  object; logical `1x1`, visual `2x2`
- [x] `first_rot_memorial` — black-glass monument to the first verified
  public record, not an origin claim; logical `1x1`, visual `2x3`

## Inventory and quest-item icons

Shared contract: square icon, isolated object, no rarity frame or label.

- [x] `odrans_black_key` — environmental evidence of authority over the
  forbidden lower sluice; not currently a usable inventory key
- [x] `missing_census_tablet` — evidence for Nera's Uncounted Dead thread
- [x] `sealed_ledger_page` — evidence held by Olek
- [x] `low_lantern_token` — access marker for Undertide routes
- [x] `drowned_silver_ring` — identifies one missing diver
- [x] `floodwarden_repair_kit` — required for Ilya's pressure-station repairs
- [x] `smoked_eel_blackbread` — regional food and hunger item
- [x] `black_silt_sample` — early physical evidence of the rot

## Major landmarks

- [x] `palace_of_still_water` — High Crown; logical `6x3`, visual `7x6`
- [x] `crown_sluice_gatehouse` — High Crown / Undertide; logical `6x2`, visual `7x5`
- [x] `drowned_bell_tower` — Lantern Quays; logical `2x2`, visual `3x6`
- [x] `house_of_names` — Birch Heights; logical `5x2`, visual `5x4`
- [x] `eel_and_ember_inn` — Lantern Quays; logical `5x2`, visual `5x4`
- [x] `walking_bridge_houses` — Walking Ward; logical `7x2`, visual `8x4`
- [x] `dry_dock_entrance` — Undertide; logical `4x2`, visual `4x3`
- [x] `birch_stair_memorial_arch` — Birch Heights; logical `4x1`, visual `4x4`

## Story connections

- The missing expedition connects Ilya, Nera, Olek, Pava, and Vasko.
- The black key, ledger page, and census tablet collectively establish the
  omitted closure crew and a late flood order. They do not settle who caused
  the flooding or where the black rot began.
- Vesna controls access to the Dry Dock and Low Lantern cache.
- Alin wants the evidence made public; Mara fears disclosure before the sluice
  is repaired.
- The repair kit, control wheel, crane, scaffold, and skiff support actual
  traversal and rescue interactions rather than being decorative clutter.
- The silt sample carries the wider black-rot story into Drazna.
- The Sluicebound preserves testimony about Gate Seven and the timing of the
  flood order. His evidence predates Drazna's public First Scar record without
  proving that the rot began in Drazna.

## Production QA

- Built-in ImageGen mode with one call per distinct asset
- Prompts requested flat `#ff00ff` or `#00ff00` chroma backdrops. Generated
  borders retain small near-key variations, so conversion uses the
  border-derived key rather than assuming byte-exact source pixels.
- Chroma sources are retained under
  `art-source/generated/drazna/production/<category>/`; final cutouts live
  under the matching Drazna directories in `frontend-react/public/art/`.
- Backgrounds were removed with the installed imagegen chroma-key helper using
  `--auto-key border --transparent-threshold 12 --opaque-threshold 220 --soft-matte --despill`.
- All 40 production images validate as alpha WebP with transparent corners
  and nonempty subject bounds. Their delivery files total 6,983,714 bytes;
  full-resolution PNG/chroma masters remain available for reprocessing.
- No opaque chroma-key residue detected
- NPCs checked at the live `54x108` pixel `1x2`-cell presentation
- Items checked near live hotbar scale
- Objects and landmarks checked against their suggested visual footprints on
  the live 54-pixel grid
- Metadata records paths, footprints, districts, and story-role tags

These are accepted production assets. Runtime registration remains explicit in
the actor, enemy, object, building, and item catalogs; accepting a cutout alone
does not silently create game logic.
