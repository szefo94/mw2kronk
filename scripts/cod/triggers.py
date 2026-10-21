"""The map's brush-volume triggers and zones, as yaw-aligned `trigger` boxes for `volumes.world`.

An `info_volume` or `trigger_*` whose model is `*N` has its volume in the
clipmap: `cmodels[N]`'s brushes, stored around the entity's origin, so world =
origin + local. Each **brush** becomes one box (`volumes.fit_box`). One box per
model would overfill a zone that turns a corner (theater's `zombie_trap` 11x)
and overlap its neighbours; one per brush stays within 1.42x of the brushes on
both zombies maps (reverse/t5-zombies/README.md#2-volumes-clipmap-bounds-against-fit_box).

IW4/IW5 name a trigger's volume `?N`, an index into the entity lump's own
trigger table (`MapEnts.trigger`, dumped beside the `.ents` as
`<map>.triggers.json` by scripts/cod/iw5/dumpers/mapents.py). Model N is a run
of hulls, each an AABB cut by a run of slabs (two parallel planes), stored
around the entity's origin as a brush model's brushes are. Each **hull** becomes
one box, as a brush does: MW2's bomb sites, bomb pickups, Sabotage sites and HQ
radio triggers.
"""

import collections
import json
import math
import os

from . import gameplay
from .volumes import brush_owners, fit_box, hull_faces, hull_volume


def table_path(ents_path):
    """Where the mapents dumper wrote the trigger table of the entity lump at `ents_path`."""
    root, _ext = os.path.splitext(ents_path)
    return root + ".triggers.json"


def read_table(ents_path):
    """The trigger table beside an entity-lump dump, or None when the map has none (or no `?N` game)."""
    path = table_path(ents_path) if ents_path else None
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def table_brushes(table, n):
    """Trigger model `?n` of a trigger table as brushes, in the clipmap dump's shape, or None.

    A hull's AABB is a brush's bounds, and each slab is the two planes
    `dir . p = midPoint + halfSize` and `-dir . p = -(midPoint - halfSize)`, its
    extra sides, so `volumes.hull_faces` clips it exactly as it does a brush.
    """
    models = table.get("models") or []
    if not 0 <= n < len(models):
        return None
    model, hulls, slabs = models[n], table.get("hulls") or [], table.get("slabs") or []
    out = []
    for h in hulls[model["firstHull"] : model["firstHull"] + model["hullCount"]]:
        sides = []
        for s in slabs[h["firstSlab"] : h["firstSlab"] + h["slabCount"]]:
            d, mid, half = s["dir"], s["midPoint"], s["halfSize"]
            sides.append({"normal": list(d), "dist": mid + half})
            sides.append({"normal": [-v for v in d], "dist": -(mid - half)})
        out.append({"bounds": h["bounds"], "sides": sides})
    return out


def read_triggers(clip, entities, modes_of, table=None):
    """Every brush-volume trigger in one clipmap dump and trigger table, as boxes, plus a report.

    `entities` are export_metadata.json's (keys named, `id`, `at`); `table` is
    `read_table`'s, for IW4/IW5's `?N` triggers. Returns (triggers, stats,
    targets): a trigger is {name, cls, at, size, yaw, tags, target, modes, use,
    roles, fill}, `at`/`size` engine metres and `yaw` degrees; `targets` is the
    gameplay.Targets that resolved them.
    """
    by_model = collections.defaultdict(list)
    for brush, n in brush_owners(clip).items():
        by_model[n].append(brush)
    brushes = clip.get("brushes", [])
    targets = gameplay.Targets(entities, modes_of)

    out, stats = [], collections.Counter()
    for e in entities:
        if not gameplay.is_brush_volume(e):
            continue
        stats["entities"] += 1
        kind, n = e["model"][0], int(e["model"][1:])
        if kind == "?":
            if table is None:
                stats["skipped: ?N with no trigger table dumped"] += 1
                continue
            owned = table_brushes(table, n)
            if owned is None:
                stats["skipped: ?N past the trigger table"] += 1
                continue
        else:
            owned = [brushes[b] for b in sorted(by_model.get(n, ()))]
        try:
            origin = tuple(float(v) for v in e["origin"].split())
        except (KeyError, ValueError):
            stats["skipped: no origin"] += 1
            continue
        # No volume entity on either zombies map carries non-zero angles, and
        # brushmodels.load places every brush model by its origin alone.
        placement = {"origin": origin, "rotation": None}
        hulls = [hull_faces(b, placement) for b in owned]
        hulls = [h for h in hulls if h]
        if not hulls:
            stats["skipped: no brush with a hull"] += 1
            continue
        tags, target, modes, roles = gameplay.tags_of(e), targets.of(e), modes_of(e), gameplay.roles_of(e)
        for k, faces in enumerate(hulls):
            centre, size, yaw = fit_box(faces)
            box = size[0] * size[1] * size[2]
            out.append(
                {
                    "name": f"t{e['id']}" if len(hulls) == 1 else f"t{e['id']}_{k}",
                    "cls": e["classname"],
                    "at": tuple(round(v, 3) for v in centre),
                    "size": tuple(round(v, 3) for v in size),
                    "yaw": round(math.degrees(yaw), 2),
                    "tags": tags,
                    "target": target,
                    "modes": modes,
                    "use": gameplay.USE_BY_CLASS.get(e["classname"]),
                    "roles": roles,
                    "fill": hull_volume(faces) / box if box > 1e-12 else 0.0,
                }
            )
        stats["exported"] += 1
        if kind == "?":
            stats["from the trigger table"] += 1
        if len(hulls) > 1:
            stats["of several brushes"] += 1
    stats["boxes"] = len(out)
    return out, stats, targets


def report(triggers, stats, targets):
    """Print what came out, the way the other export stages do."""
    if not stats["entities"]:
        print("trigger volumes: no brush-volume trigger in this map")
        return
    table = f", {stats['from the trigger table']:,} from the trigger table" if stats["from the trigger table"] else ""
    print(
        f"trigger volumes: {stats['exported']:,} of {stats['entities']:,} brush-volume entities{table}, "
        f"as {stats['boxes']:,} box(es) ({stats['of several brushes']:,} of several brushes)"
    )
    by_class = collections.Counter(t["cls"] for t in triggers)
    print("  " + ", ".join(f"{cls} {n}" for cls, n in sorted(by_class.items())))
    for key in sorted(k for k in stats if k.startswith("skipped")):
        print(f"  {key}: {stats[key]:,}")
    if triggers:
        fills = sorted(t["fill"] for t in triggers)
        print(f"  box fit (hull / box volume): median {fills[len(fills) // 2]:.2f}, worst {fills[0]:.2f}")
    line = targets.report()
    if line:
        print(f"  {line}")
