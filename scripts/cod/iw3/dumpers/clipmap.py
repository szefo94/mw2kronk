"""`clipmap` for IW3: IW5's writer over CoD4's clipMap_t, which keeps a few things differently.

- A clip material (`dmaterial_t`) holds its name inline (`material[64]`) and calls its
  contents `contentFlags`.
- A brush (`cbrush_t`) carries its own `mins`, `maxs` and `contents`; there are no
  `brushContents` and `brushBounds` arrays beside the brush table.
- A collision partition has no `firstVertSegment`: CoD4's soup is addressed by 16-bit
  indices alone, so none is written and collision.py checks the vertex count fits.
- A submodel, a static model and a brush have `mins`/`maxs` (`absmin`/`absmax`) rather
  than one `bounds` (`absBounds`).

Everything else (the soup, the AABB tree, leaf-brush nodes, dynamic entities) has
IW4's member names, so IW5's helpers read it as it is.
"""

from ...iw5.dumpers import clipmap as iw5
from ...iw5.dumpers.common import mid_half, vec3
from ...iw6.dumpers.common import asset_path, write_json


def _box(record, lo, hi):
    """Two corners as the dump's stored Bounds: midpoint and half size."""
    a, b = record.unpack("<3f", lo), record.unpack("<3f", hi)
    return mid_half([(x + y) / 2 for x, y in zip(a, b, strict=True)] + [(y - x) / 2 for x, y in zip(a, b, strict=True)])


def materials(clip):
    out = []
    for m in clip["materials"] or ():
        name = bytes(m.bytes("material")).split(b"\0", 1)[0].decode("latin-1")
        out.append({"name": name, "surfaceFlags": m["surfaceFlags"], "contents": m["contentFlags"]})
    return out


def brushes(walk, clip):
    array = clip["brushes"]
    if array is None:
        return []
    if len(array) != clip["numBrushes"]:
        raise SystemExit(f"clipMap_t.numBrushes is {clip['numBrushes']}, but {len(array)} brushes were loaded")
    out = []
    for brush in array:
        numsides = brush["numsides"]
        axial = brush["axialMaterialNum"]
        entry = {
            "numsides": numsides,
            "axialMaterialNum": [list(axial[:3]), list(axial[3:])],
            "contents": brush["contents"],
            "bounds": _box(brush, "mins", "maxs"),
        }
        sides = []
        first = brush["sides"]
        if first is not None and numsides:
            for side in walk.span(first, numsides):
                jside = {"materialNum": side["materialNum"]}
                plane = side["plane"]
                if plane is not None:
                    face = plane.unpack("<4f")
                    jside["normal"] = list(face[:3])
                    jside["dist"] = face[3]
                sides.append(jside)
        entry["sides"] = sides
        out.append(entry)
    return out


def partitions(clip):
    return [
        {"triCount": part["triCount"] & 0xFF, "borderCount": part["borderCount"] & 0xFF, "firstTri": part["firstTri"]}
        for part in clip["partitions"] or ()
    ]


def submodels(clip):
    out = []
    for model in clip["cmodels"] or ():
        leaf = model["leaf"]
        owned = set()
        if leaf["brushContents"] and clip["leafbrushNodes"] is not None:
            owned = iw5.leaf_brushes(clip, leaf["leafBrushNode"])
        parts = set()
        if clip["aabbTrees"] is not None:
            parts = iw5.aabb_partitions(clip, leaf["firstCollAabbIndex"], leaf["collAabbCount"])
        out.append(
            {
                "bounds": _box(model, "mins", "maxs"),
                "radius": model["radius"],
                **{key: leaf[key] for key in iw5.LEAF_FIELDS},
                "brushes": sorted(owned),
                "partitions": sorted(parts),
            }
        )
    return out


def static_models(clip):
    out = []
    for m in clip["staticModelList"] or ():
        axis = m["invScaledAxis"]
        entry = {"origin": vec3(m["origin"]), "absBounds": _box(m, "absmin", "absmax")}
        model = m["xmodel"]
        if model is not None and model.name:
            entry["model"] = model.name
        entry["invScaledAxis"] = [list(axis[0:3]), list(axis[3:6]), list(axis[6:9])]
        out.append(entry)
    return out


def manifest(walk, asset, out):
    clip = asset.record
    data = {"_game": walk.game, "_type": "clipmap", "_version": 1, "name": asset.name}
    data.update((key, clip[key]) for key in ("planeCount", "numBrushSides", "numLeafBrushes"))
    data.update((key, clip[key]) for key in ("numStaticModels", "numSubModels", "vertCount", "triCount"))
    data.update((key, clip[key]) for key in ("partitionCount", "borderCount", "checksum"))
    data["dynEntCount"] = list(clip["dynEntCount"])
    iw5.soup(clip, out, asset.name, data)
    data["materials"] = materials(clip)
    data["brushes"] = brushes(walk, clip)
    data["partitions"] = partitions(clip)
    data["aabbTrees"] = iw5.aabb_trees(clip)
    data["cmodels"] = submodels(clip)
    data["staticModels"] = static_models(clip)
    data["dynEnts"] = iw5.dynamic_entities(clip)
    return data


def write(walk, out, ctx=None):
    iw5.check_plane_layout(walk.program)
    for asset in walk.by_type("clipmap"):
        data = manifest(walk, asset, out)
        write_json(asset_path(out, f"{asset.name}.clipmap.json"), data)
