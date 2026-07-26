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

- [ ] `queen_mara_vey` — High Crown; ruler carrying the original cover-up
- [ ] `ilya_sorn` — Crown Sluice; junior floodwarden who opened the channel
- [ ] `nera_bell` — Birch Heights; keeper of the erased census
- [ ] `olek_var` — Lantern Quays / Undertide; salvage captain and intermediary
- [ ] `pava_mirek` — Walking Ward; roofwright and community organizer
- [ ] `vasko_mirek` — Undertide; missing diver carrying evidence
- [ ] `vesna_korr` — Undertide; Low Lantern organizer controlling dry routes
- [ ] `alin_vey` — High Crown / Birch Heights; royal heir demanding disclosure

## Interactive objects

- [ ] `drazna_ferry_skiff` — transport and rescue; logical `3x1`, visual `4x2`
- [ ] `amber_quay_stall` — Lantern Quays vendor; logical `2x2`, visual `3x3`
- [ ] `sluice_control_wheel` — water-level interaction; logical `2x1`, visual `2x3`
- [ ] `house_of_names_rack` — memorial clue surface; logical `2x1`, visual `2x3`
- [ ] `roofwright_scaffold` — repair and traversal; logical `3x1`, visual `4x3`
- [ ] `salvage_crane` — Mud Crown access; logical `2x2`, visual `3x4`
- [ ] `low_lantern_cache` — criminal loot container; logical `1x1`, visual `2x2`
- [ ] `floodline_memorial` — Birch Heights clue landmark; logical `2x1`, visual `2x3`

## Inventory and quest-item icons

Shared contract: square icon, isolated object, no rarity frame or label.

- [ ] `odrans_black_key` — opens the forbidden lower sluice
- [ ] `missing_census_tablet` — evidence for Nera's Uncounted Dead thread
- [ ] `sealed_ledger_page` — evidence held by Olek
- [ ] `low_lantern_token` — access marker for Undertide routes
- [ ] `drowned_silver_ring` — identifies one missing diver
- [ ] `floodwarden_repair_kit` — required for Ilya's pressure-station repairs
- [ ] `smoked_eel_blackbread` — regional food and hunger item
- [ ] `black_silt_sample` — early physical evidence of the rot

## Major landmarks

- [ ] `palace_of_still_water` — High Crown; logical `6x3`, visual `7x6`
- [ ] `crown_sluice_gatehouse` — High Crown / Undertide; logical `6x2`, visual `7x5`
- [ ] `drowned_bell_tower` — Lantern Quays; logical `2x2`, visual `3x6`
- [ ] `house_of_names` — Birch Heights; logical `5x2`, visual `5x4`
- [ ] `eel_and_ember_inn` — Lantern Quays; logical `5x2`, visual `5x4`
- [ ] `walking_bridge_houses` — Walking Ward; logical `7x2`, visual `8x4`
- [ ] `dry_dock_entrance` — Undertide; logical `4x2`, visual `4x3`
- [ ] `birch_stair_memorial_arch` — Birch Heights; logical `4x1`, visual `4x4`

## Story connections

- The missing expedition connects Ilya, Nera, Olek, Pava, and Vasko.
- The black key, ledger page, and census tablet collectively prove Odran's
  deliberate flood.
- Vesna controls access to the Dry Dock and Low Lantern cache.
- Alin wants the evidence made public; Mara fears disclosure before the sluice
  is repaired.
- The repair kit, control wheel, crane, scaffold, and skiff support actual
  traversal and rescue interactions rather than being decorative clutter.
- The silt sample carries the wider black-rot story into Drazna.
