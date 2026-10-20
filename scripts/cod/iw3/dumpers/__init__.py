"""IW3's writer per dump kind: IW5's writers, reading IW3's records by member name.

CoD4's assets are IW4's ancestors, and most members keep IW4's names, so IW5's
writers (already IW4's) read them as they are. Two kinds have writers of their
own here because CoD4 lays them out flatter: `gfxworld` (no `GfxWorldDraw`, and
`mins`/`maxs` for bounds) and `clipmap` (inline clip material names, a brush's
own contents and bounds, no vertex segments). The shared writers carry the
smaller differences by member: a weapon whose asset is its WeaponDef, a rawfile
that is always plain, IWI v6 archives and the loadDef's byte of flags
(iw5/dumpers/weapon.py, rawfile.py, image.py).

**Effects and sound aliases are not written yet**: writing them is new output
and a decision of its own (docs/cod/games.md). They are registered as partial
kinds holding no fields: a dump writes the zone listing and nothing else, and
stages 12 and 13 skip. The PCM behind the loaded aliases (`loadedsound`) is
written.
"""

from ...iw5.dumpers import (
    Context,
    image,
    mapents,
    material,
    rawfile,
    sound,
    weapon,
    xanim,
    xmodel,
)
from . import clipmap, gfxworld

__all__ = ["COMPLETE", "Context", "DUMPERS", "PARTS"]


def nothing(walk, out, ctx=None):
    """A kind this backend dumps no file of (see above); the listing is written beside it."""


DUMPERS = {
    "gfxworld": gfxworld.write,
    "clipmap": clipmap.write,
    "mapents": mapents.write,
    "rawfile": rawfile.write,
    "xmodel": xmodel.write,
    "material": material.write,
    "image": image.write,
    "xanim": xanim.write,
    "weapon": weapon.write,
    "loadedsound": sound.write_pcm,
    "fx": nothing,
    "sound": nothing,
}
PARTS = {"fx": frozenset(), "sound": frozenset()}  # kind -> the top-level fields a partial writer writes
COMPLETE = set(DUMPERS) - set(PARTS)
