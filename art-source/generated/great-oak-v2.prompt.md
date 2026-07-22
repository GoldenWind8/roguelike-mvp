# Great Oak v2 generation record

Generated with the built-in image-generation tool. The production PNG was
created by chroma-key removal from `great-oak-v2-chroma.png`.

## Base generation prompt

Use case: stylized-concept

Asset type: production game object sprite for placement on the existing neutral square grid

Primary request: create the Great Oak of Oakrun as one isolated placeable landmark asset. It is an enormous, ancient English oak with a broad asymmetrical crown, thick gnarled trunk, low heavy branches, a few subtle age scars, and a simple weathered circular timber guard around the trunk. It should feel protective, familiar, and old rather than magical.

Input images: Image 1 is a style, material, palette, and subject reference only. Use the central oak's grounded pastoral character; do not reproduce the surrounding town, roads, buildings, ground, or composition.

Style/medium: polished hand-painted dark-folklore game sprite, grounded pastoral realism, strong readable silhouette, no isometric tile base

Composition/framing: single tree centered, complete crown and trunk visible, front-facing with only a slight elevated view, intended to overlap a roughly 3x3-cell square gameplay footprint; generous padding; readable when downscaled; no surrounding scene

Lighting/mood: soft late-afternoon amber light from upper left, restrained natural shadowing within the tree only

Color palette: natural muted oak greens, warm weathered brown bark, small moss accents; do not use magenta in the subject

Scene/backdrop: perfectly flat solid `#ff00ff` chroma-key background for local background removal

Constraints: background must be one uniform `#ff00ff` color with no gradient, texture, floor plane, scenery, atmospheric haze, cast shadow, contact shadow, reflection, or lighting variation; crisp separated silhouette; no grass patch, dirt base, road, rocks, flowers, people, signs, text, logos, watermark, glowing magic, face in the bark, or fantasy runes

Avoid: isometric tiles, diamond base, painterly background, photorealistic cutout, overly fine leaf noise, perfectly symmetrical crown, enchanted-tree clichés

## Contract correction prompt

Use case: precise-object-edit

Asset type: production game object sprite for the existing one-tile object contract

Input images: Image 1 is the edit target; Image 2 is style and pastoral-material reference only

Primary request: remove only the circular timber guard and all fence pieces from around the Great Oak. Finish the exposed lower trunk and roots naturally so the trunk base forms a compact, narrow anchor that can sit on one logical square tile while the canopy visually overhangs neighbouring tiles.

Constraints: preserve the exact tree identity, crown silhouette, branches, foliage, bark, lighting, palette, scale, framing, and flat solid `#ff00ff` chroma-key background from Image 1; do not redesign or prune the tree; do not add grass, dirt, rocks, flowers, scenery, shadows, people, signs, text, logos, watermark, runes, or magical effects; the background must remain perfectly uniform `#ff00ff` with no gradient or texture; do not use `#ff00ff` in the tree

Avoid: fence remnants, stump-like base, wide ground patch, isometric tile base, cast shadow
