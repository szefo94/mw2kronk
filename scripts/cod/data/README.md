# Generated layouts

The tables the importer walks a zone with. Each file is generated, never edited by
hand, and shipped so that an export needs no build step. Every file here is
derived from GPL-3 sources: the struct headers, ZoneCode and field tables of
[OpenAssetTools](https://github.com/Laupetin/OpenAssetTools) for the 32-bit
games, and the headers of [x64-zt](https://github.com/Joelrau/x64-zt) for the
64-bit ones. None of them comes from a game's own files.

| File                            | What it is                                                                                    | Read by                         |
| ------------------------------- | --------------------------------------------------------------------------------------------- | ------------------------------- |
| `<g>/zonecode.json`             | The load program: every struct, the order the game loads its members, and each asset's loader | `zonecode/program.py`           |
| `<g>/weapon_fields.json`        | OAT's weapon field table: each infostring key, the member it reads, and its type              | `iw5/dumpers/weapon.py`         |
| `iw5/attachment_enums.json`     | The enum values an IW5 attachment's fields are written as                                     | `iw5/dumpers/attachment.py`     |
| `t5/`, `t6/material_names.json` | The texture-slot and shader-constant names a material's hashes are matched against            | `t5/`, `t6/dumpers/material.py` |
| `iw6/layout.json`               | Ghosts' PC struct layouts: each member's offset, size and type                                | `iw6/zone.py`                   |
| `s1/layout_pc.json`             | Advanced Warfare's PC struct layouts, the same rows                                           | `s1/walkers.py`                 |

`<g>` is `iw3` (CoD4), `iw4` (MW2), `iw5` (MW3), `t5` (Black Ops) or `t6` (Black Ops II).

## Overriding

`$ZONECODE_<G>` (e.g. `$ZONECODE_IW5`) names another `zonecode.json`; that game's
other tables are then read from beside it. `$IW6_LAYOUT` and `$S1_LAYOUT` name
other layouts.

## Rebuilding

Each generator writes straight into this folder. Their READMEs say what each
needs, which is the source checkouts named above and a compiler, never a game.

| Files                        | Generator                                      |
| ---------------------------- | ---------------------------------------------- |
| `iw4/`, `iw5/`, `t5/`, `t6/` | `reverse/x86-zone/build.sh [iw4\|iw5\|t5\|t6]` |
| `iw3/`                       | `reverse/x86-zone/layout/zonecode.py iw3` and `infostring.py iw3`, from OAT at `06a121f7` (see below) |
| `iw6/layout.json`            | `reverse/iw6-zone/layout/layout.sh`            |
| `s1/layout_pc.json`          | `reverse/s1-zone/layout/layout.sh`             |

A regenerated file should be byte-identical to the one here unless its source
changed. Commit the two together.

`iw3/` was generated without `build.sh`'s checks: its compiled layout and the
struct-test and `*_load_db.cpp` comparisons need g++ in `-m32` mode and an OAT
build, which a Mac has neither of. What stands in for them is the walk itself:
every CoD4 zone the pipeline reads (the four exported maps, `common_mp.ff`,
`localized_common_mp.ff`, `ui_mp.ff`, `code_post_gfx_mp.ff`) is walked to the
last byte, each block's high-water mark the size its XFile header states. CoD4's
weapon table is relative to `WeaponDef` itself (no `WeaponFullDef`); its paths are
written as `weapDef.<path>`.

## What is not here

Two more tables exist and are deliberately not shipped: Ghosts'
`durango_types.json` and Advanced Warfare's X360 `layout.json`. Both are derived
from development builds' debug symbols, so each user supplies their own and names
it in `cod.config.json` (`iw6_durango_types`, `s1_layout_x360`). `.gitignore`
keeps both out of the repo. See
[docs/cod/README.md](../../../docs/cod/README.md#ghosts-and-advanced-warfare).
