# World Object Assets

This is the contract for placing generated art in rooms without turning the
art into game logic. It applies equally to handcrafted and generated rooms.

## The two pieces

A room stores only a placement:

```json
{"type": "covered_carriage", "x": 12, "y": 8}
```

The trusted catalogue in `backend/object_defs.py` supplies everything that a
room is not allowed to invent:

- display name and inspection text;
- logical footprint (the tiles the object occupies);
- whether that footprint blocks movement;
- image path;
- visual size in cells.

If no special values are supplied, an object occupies one tile, blocks
movement, and uses the client's fallback icon. This keeps small props cheap.

## Logical size is not visual size

The logical footprint is an offset mask from the placement's top-left cell.
It is used for collision, overlap checks, interaction range, and click targets.
The image is rendered once, centred over that footprint and aligned to its
bottom edge. It may be taller or wider without silently changing collision.
The visible image is itself an interaction target, so players do not need to
guess which logical anchor cell belongs to a tall tree or building. When an
actor's feet pass behind that visual rectangle without entering its collision
cells, the client temporarily fades the artwork to keep the actor readable.

Examples:

| Object | Logical footprint | Visual size |
|---|---:|---:|
| Chest | 1 x 1 | 1 x 1 fallback |
| Great Oak | 1 x 1 trunk | 4 x 4 canopy |
| Covered carriage | 2 x 2 | 4 x 3 |
| Stone well | 2 x 2 | 2 x 3 |
| Wayfarer's Rest exterior | 5 x 2 | 5 x 4 |

Footprints are masks rather than only width and height, so an L-shaped or
hollow object can be added later without changing the room format.

## Where rules live

- The server expands footprints and rejects out-of-bounds, overlapping, or
  placements that cut off a required entry or actor route before storage.
- `RoomState` keeps static object occupancy separate from the actor grid.
- Movement and actor AI ask the same server-side position check.
- The client receives the expanded occupied cells and presentation metadata.
  It draws the asset, makes both its cells and visible artwork clickable, and
  may fade visual overhang for readability, but never decides whether movement
  is legal from image dimensions.

Replacing an image therefore changes one catalogue entry, not every room.
Changing collision does not require editing or measuring the PNG.

## Handcrafted and generated rooms

There is no separate runtime for handcrafted rooms. Both use the same room
shape, validator, loader, collision, and renderer.

The current procedural and AI room generators are deliberately offered only
the existing one-tile chest and fire-barrel definitions. That is a generation
policy at the generator boundary, not a special case in movement code. We can
later opt a generator into more catalogue entries once it knows how to reserve
and validate their full footprints.

## Adding an object

1. Put the transparent PNG under `frontend-react/public/art/world/`.
2. Add one `ObjectDefinition` in `backend/object_defs.py`.
3. Choose the smallest honest logical footprint; set visual size separately.
4. Place its `type`, `x`, and `y` in a handcrafted room and run validation.

Per-instance collision, arbitrary scaling, rotation, animation, and automatic
footprint detection from pixels are intentionally not part of this first pass.
They can be added at the catalogue/loader seam without changing room movement.
