"""`mapents`: the entity lump as text, `<map>.ents`, where OpenAssetTools puts it,
and its trigger table beside it, `<map>.triggers.json`.

`numEntityChars` counts the terminating NUL, which is not part of the text.
Keys are script-string numbers where the compiler had no name for them;
scripts/cod/entities.py recognises the ones the pipeline needs by their values.

IW4 and IW5 name a trigger's volume `?N` rather than a clipmap brush model `*N`:
`MapEnts.trigger.models[N]`, a run of hulls, each an AABB (midpoint, half size)
cut by a run of slabs (a direction, a midpoint and a half size along it: the
two planes `dir . p = midPoint +- halfSize`), all around the entity's origin.
scripts/cod/triggers.py turns them into boxes. A MapEnts without a `trigger`
member (CoD4's) or with an empty table writes no file.
"""

from ...iw6.dumpers.common import asset_path, write_json
from . import dumper
from .common import mid_half


def trigger_table(ents):
    """MapEnts.trigger as plain data, or None when it has none: {models, hulls, slabs}."""
    if "trigger" not in ents:
        return None
    table = ents["trigger"]
    if not table["count"]:
        return None
    models = table["models"] or ()
    hulls = table["hulls"] or ()
    slabs = table["slabs"] or ()
    return {
        "models": [{"contents": m["contents"], "hullCount": m["hullCount"], "firstHull": m["firstHull"]} for m in models],
        "hulls": [
            {
                "bounds": mid_half(h["bounds"].unpack("<6f")),
                "contents": h["contents"],
                "slabCount": h["slabCount"],
                "firstSlab": h["firstSlab"],
            }
            for h in hulls
        ],
        "slabs": [{"dir": list(s["dir"]), "midPoint": s["midPoint"], "halfSize": s["halfSize"]} for s in slabs],
    }


@dumper("mapents")
def write(walk, out, ctx=None):
    for asset in walk.by_type("mapents"):
        ents = asset.record
        text = ents["entityString"]
        data = bytes(text.bytes()) if text is not None else b""
        with open(asset_path(out, f"{asset.name}.ents"), "wb") as fh:
            fh.write(data[: max(ents["numEntityChars"] - 1, 0)])
        table = trigger_table(ents)
        if table is not None:
            write_json(asset_path(out, f"{asset.name}.triggers.json"), table)
