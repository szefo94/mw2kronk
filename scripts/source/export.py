"""Export a Source map (CS:S, CS:GO) as a world folder Vibe on Duty 2 loads like a mw2kronk CoD export.

Writes, under `<out>/<slug>/`:
  world.world            the shell with the map's own baked lightmap (`game: css|csgo`)
  assets/world.glb       brush faces and displacements, one primitive per material, lightmap UVs in TEXCOORD_1
  assets/lightmap.png    every face's style-0 luxels in one atlas, sRGB of (linear luxel x LIGHTMAP_STORE)
  assets/collision.glb   `solid` (stops players and bullets) and `clip0x10000` (players only) meshes from the
                         map's brushes, displacements, solid brush entities and the props' own .phy hulls
  assets/props/*.glb     static props and prop_dynamic/prop_physics models, LOD 0
  props.world            their placements (engine metres, XYZ Euler degrees)
  ladder.world           CONTENTS_LADDER brushes as oriented boxes
  assets/minimap.png     the game's radar overview, turned so the image's up is +X as CoD's compass maps are
  assets/surfaces.json   each material's $surfaceprop as an MW2 surface (tools/build-surface-data.py merges it)
  export_metadata.json   spawns (T -> axis, CT -> allies, all start spawns), lighting keys, minimap corners

Engine frame as everywhere in mw2kronk: Source (x, y, z) inches -> (x, z, -y) metres. The 3D skybox's area is
left out (its miniature would stand beside the map); the 2D sky becomes the background colour.
"""

import io
import json
import math
import os
import re
import time

from PIL import Image

from . import bsp as B
from . import mdl, phy, vtf
from .fs import GameFS, game_config
from .glb import Glb

INCH = 0.0254
LIGHTMAP_STORE = 0.5  # linear luxel x this, then sRGB: room for luxels up to 2.0 before they clip
WORLD_TEX, PROP_TEX = 1024, 512

SKIP_FLAGS = B.SURF_NODRAW | B.SURF_SKY | B.SURF_SKY2D | B.SURF_HINT | B.SURF_SKIP
# brush entities drawn with the world, and how they collide ('solid', 'clip' or None)
BRUSH_CLASSES = {
    "func_brush": "solid",
    "func_wall": "solid",
    "func_wall_toggle": "solid",
    "func_detail": "solid",
    "func_lod": "solid",
    "func_door": "solid",
    "func_door_rotating": "solid",
    "func_movelinear": "solid",
    "func_rotating": "solid",
    "func_physbox": "solid",
    "func_physbox_multiplayer": "solid",
    "func_breakable": "solid",
    "func_button": "solid",
    "func_tracktrain": "solid",
    "func_breakable_surf": "clip",
    "func_illusionary": None,
}
PROP_CLASSES = (
    "prop_dynamic",
    "prop_dynamic_override",
    "prop_physics",
    "prop_physics_multiplayer",
    "prop_physics_override",
    "prop_static",
)
# Source $surfaceprop -> the MW2 surface whose impact sounds stand in (src/v2/data/surfaces.json's names)
SURFACEPROPS = [
    (r"glass", "glass"),
    (r"concrete|cinder|plaster_concrete", "concrete"),
    (r"brick", "brick"),
    (r"plaster|drywall", "plaster"),
    (r"asphalt|tarmac", "asphalt"),
    (r"sand", "sand"),
    (r"gravel", "gravel"),
    (r"mud", "mud"),
    (r"dirt|soil|clay", "dirt"),
    (r"grass", "grass"),
    (r"foliage|leaves|hay|straw", "foliage"),
    (r"rock|stone|boulder|cliff|granite", "rock"),
    (r"water|slosh|wade|puddle", "water"),
    (r"wood|crate|plank|pallet|furniture", "wood"),
    (r"rubber|tire", "rubber"),
    (r"carpet|rug", "carpet"),
    (r"cardboard|paper", "paper"),
    (r"plastic", "plastic"),
    (r"tile|ceramic|porcelain|pottery", "ceramic"),
    (r"cloth|fabric|upholstery|flesh_soft", "cloth"),
    (r"cushion|mattress|couch", "cushion"),
    (r"snow", "snow"),
    (r"ice", "ice"),
    (r"flesh|alienflesh|bloodyflesh", "flesh"),
    (r"metal|chain|grate|vent|canister|roller|barrel|solidmetal|combine|computer|weapon|ladder|pipe", "metal"),
]


def three(p):
    return (p[0] * INCH, p[2] * INCH, -p[1] * INCH)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    n = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / n, a[1] / n, a[2] / n)


def _lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def angle_matrix(pitch, yaw, roll):
    """Valve's AngleMatrix: rows of a 3x3 whose columns are the entity's forward, left and up in world axes."""
    sp, cp = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    sy, cy = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
    sr, cr = math.sin(math.radians(roll)), math.cos(math.radians(roll))
    return (
        (cp * cy, sp * sr * cy - cr * sy, sp * cr * cy + sr * sy),
        (cp * sy, sp * sr * sy + cr * cy, sp * cr * sy - sr * cy),
        (-sp, sr * cp, cr * cp),
    )


def _mul(m, v):
    return (_dot(m[0], v), _dot(m[1], v), _dot(m[2], v))


def three_euler(m):
    """A Source rotation as three.js XYZ Euler degrees in the engine frame: R3 = C R C^T, C = (x, y, z) -> (x, z, -y)."""
    # C R C^T, written out: rows/cols reorder as (0, 2, 1) with the sign of the y axis flipped
    idx, sgn = (0, 2, 1), (1, 1, -1)
    r = [[m[idx[i]][idx[j]] * sgn[i] * sgn[j] for j in range(3)] for i in range(3)]
    m13 = max(-1.0, min(1.0, r[0][2]))
    y = math.asin(m13)
    if abs(m13) < 0.9999999:
        x, z = math.atan2(-r[1][2], r[2][2]), math.atan2(-r[0][1], r[0][0])
    else:
        x, z = math.atan2(r[2][1], r[1][1]), 0.0
    return tuple(round(math.degrees(a), 3) for a in (x, y, z))


def surface_of(keys, name):
    sp = (keys.get("$surfaceprop") or "").lower()
    for text in (sp, name.lower().split("/")[-1]):
        for rx, s in SURFACEPROPS:
            if text and re.search(rx, text):
                return s
    return None


def _truthy(v):
    try:
        return float(v) != 0
    except (TypeError, ValueError):
        return False


# ---- textures --------------------------------------------------------------------------------------------------------
class Textures:
    """VMT -> base texture -> JPEG (opaque) or PNG (alpha-tested / translucent), cached by texture and size."""

    def __init__(self, fs):
        self.fs = fs
        self.cache = {}
        self.bytes = 0
        self.missing = set()

    def material(self, name):
        shader, keys = self.fs.material(name)
        return shader or "", keys

    def image(self, texname, max_size, alpha):
        key = (texname, max_size, alpha)
        if key in self.cache:
            return self.cache[key]
        out = None
        data = self.fs.read(f"materials/{texname.lower().removesuffix('.vtf')}.vtf")
        if data:
            img, _has_alpha = vtf.decode(data)
            if img is not None:
                if max(img.size) > max_size:
                    s = max_size / max(img.size)
                    img = img.resize((max(1, round(img.size[0] * s)), max(1, round(img.size[1] * s))), Image.LANCZOS)
                buf = io.BytesIO()
                if alpha:
                    img.save(buf, "PNG", optimize=True)
                    out = (buf.getvalue(), "image/png")
                else:
                    img.convert("RGB").save(buf, "JPEG", quality=85)
                    out = (buf.getvalue(), "image/jpeg")
                self.bytes += len(out[0])
        if out is None:
            self.missing.add(texname)
        self.cache[key] = out
        return out


def material_index(glb, tex, name, keys, shader, max_size, which="$basetexture", fallback=None):
    """The glb material for a VMT (by name), with its base texture embedded; a texture the install lacks (the CS:S server's HL2
    textures CS:GO did not keep) is drawn in `fallback`, the BSP's mean colour of it, or a neutral grey."""
    translucent = _truthy(keys.get("$translucent")) or _truthy(keys.get("$additive"))
    alphatest = _truthy(keys.get("$alphatest")) or _truthy(keys.get("$decal"))
    alpha = "BLEND" if translucent else "MASK" if alphatest else None
    color = None
    if "water" in shader:
        alpha, color = "BLEND", (0.25, 0.35, 0.4, 0.75)
    t = None
    texname = keys.get(which) or (keys.get("$basetexture") if which != "$basetexture" else None)
    if texname and not color:
        img = tex.image(texname, max_size, alpha is not None)
        if img:
            t = glb.texture((texname, max_size, alpha is not None), img[0], img[1])
    if t is None and color is None:
        color = (*fallback, 1.0) if fallback else (0.6, 0.58, 0.55, 1.0)
    unlit = shader.startswith("unlit") and "sprite" not in shader
    return glb.material(name, t, alpha, color, double=_truthy(keys.get("$nocull")), unlit=unlit)


# ---- lightmap atlas --------------------------------------------------------------------------------------------------
class Atlas:
    """Shelf-packs luxel rects (each with a 1-texel border copied from its edge) into one RGB image."""

    def __init__(self, width=2048):
        self.w = width
        self.rects = {}  # key -> (x, y, w, h) of the inner rect
        self.items = []
        self.lut = {}

    def add(self, key, w, h, samples):
        self.items.append((key, w, h, samples))

    def pack(self):
        x = y = shelf = 0
        order = sorted(self.items, key=lambda it: -it[2])
        for key, w, h, _s in order:
            if x + w + 2 > self.w:
                x, y, shelf = 0, y + shelf, 0
            self.rects[key] = (x + 1, y + 1, w, h)
            x += w + 2
            shelf = max(shelf, h + 2)
        self.h = max(4, y + shelf + (-(y + shelf) % 4))

    def _texel(self, r, g, b, e):
        k = (r, g, b, e)
        v = self.lut.get(k)
        if v is None:
            if e > 127:
                e -= 256
            s = (2.0**e) / 255.0 * LIGHTMAP_STORE
            v = bytes(min(255, int(255 * min(1.0, c * s) ** (1 / 2.2) + 0.5)) for c in (r, g, b))
            self.lut[k] = v
        return v

    def image(self):
        img = bytearray(self.w * self.h * 3)
        W = self.w
        for key, w, h, samples in self.items:
            rx, ry, _w, _h = self.rects[key]
            row_cache = []
            for t in range(h):
                row = bytearray()
                for s in range(w):
                    i = (t * w + s) * 4
                    row += self._texel(samples[i], samples[i + 1], samples[i + 2], samples[i + 3]) if samples else b"\xba\xba\xba"
                row_cache.append(bytes(row))
            # the rect with its border: first/last rows and columns repeated
            for t in range(-1, h + 1):
                row = row_cache[min(max(t, 0), h - 1)]
                full = row[0:3] + row + row[-3:]
                at = ((ry + t) * W + rx - 1) * 3
                img[at : at + len(full)] = full
        return Image.frombytes("RGB", (self.w, self.h), bytes(img))


# ---- brushes -> polygons ---------------------------------------------------------------------------------------------
def brush_polygons(b, brush_index, eps=0.01):
    """The convex brush's faces as polygons (Source inches): each side's plane cut by every other side's half space."""
    first, num, _contents = b.brushes[brush_index]
    sides = [b.brushsides[first + k] for k in range(num)]
    planes = [b.planes[s["plane"]] for s in sides]
    out = []
    for k, (nx, ny, nz, d, _t) in enumerate(planes):
        if sides[k]["bevel"]:
            continue
        n = (nx, ny, nz)
        # a big quad on the plane
        a = (0.0, 0.0, 1.0) if abs(nz) < 0.9 else (1.0, 0.0, 0.0)
        u = _norm(_cross(a, n))
        v = _cross(n, u)
        c = (nx * d, ny * d, nz * d)
        R = 65536.0
        poly = [tuple(c[i] + (su * u[i] + sv * v[i]) * R for i in range(3)) for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        # (u, v, n) is right handed, so the quad winds counter-clockwise seen from outside, and clipping keeps that
        for j, (mx, my, mz, md, _t2) in enumerate(planes):
            if j == k or not poly:
                continue
            m = (mx, my, mz)
            clipped = []
            for i in range(len(poly)):
                p, q = poly[i], poly[(i + 1) % len(poly)]
                dp, dq = _dot(m, p) - md, _dot(m, q) - md
                if dp <= eps:
                    clipped.append(p)
                if (dp <= eps) != (dq <= eps):
                    clipped.append(_lerp(p, q, dp / (dp - dq)))
            poly = clipped
        if len(poly) >= 3:
            out.append((poly, n))
    return out


def model_brushes(b, model_index):
    """Brush indices under a model's BSP head node."""
    out, stack = set(), [b.models[model_index]["headnode"]]
    while stack:
        node = stack.pop()
        if node < 0:
            leaf = b.leafs[-1 - node]
            fb, nb = leaf["brushes"]
            out.update(b.leafbrushes[fb : fb + nb])
            continue
        _plane, c0, c1 = b.nodes[node]
        stack += [c0, c1]
    return out


# ---- the export --------------------------------------------------------------------------------------------------------
class Prims:
    """Triangles grouped by material key, in engine metres."""

    def __init__(self):
        self.by = {}

    def get(self, key):
        p = self.by.get(key)
        if p is None:
            p = self.by[key] = {"pos": [], "nor": [], "uv0": [], "uv1": [], "idx": []}
        return p

    def add_tri_list(self, key, verts, tris):
        """verts: [(pos3 engine, nor3 engine, uv0, uv1)], tris: index triples into verts."""
        p = self.get(key)
        base = len(p["pos"]) // 3
        for pos, nor, uv0, uv1 in verts:
            p["pos"] += pos
            p["nor"] += nor
            p["uv0"] += uv0
            p["uv1"] += uv1
        for a, b_, c in tris:
            p["idx"] += (base + a, base + b_, base + c)


def export(game, map_name, out_root, slug=None, label=None, log=print):
    t0 = time.time()
    cfg = game_config(game)
    path = os.path.join(cfg["root"], cfg["maps"], f"{map_name}.bsp")
    b = B.Bsp(path)
    fs = GameFS(game, b.pakfile)
    tex = Textures(fs)
    slug = slug or f"{map_name.split('_', 1)[-1]}-{game}"
    out = os.path.join(out_root, slug)
    for sub in ("props", "textures"):
        folder = os.path.join(out, "assets", sub)
        if os.path.isdir(folder):  # a re-export starts clean: no textures or props left from the last one
            for f in os.listdir(folder):
                os.remove(os.path.join(folder, f))
        os.makedirs(folder, exist_ok=True)
    tex_dir = os.path.join(out, "assets", "textures")
    log(
        f"{map_name} ({cfg['name']}): BSP v{b.version}, {len(b.faces)} faces, {len(b.dispinfo)} displacements, "
        f"{len(b.brushes)} brushes, {len(b.static_props)} static props, {'HDR' if b.hdr else 'LDR'} lighting"
    )

    ents = b.entities
    worldspawn = next((e for e in ents if e.get("classname") == "worldspawn"), {})

    # the 3D skybox's area: the leaf the sky_camera stands in
    sky_area = None
    cam = next((e for e in ents if e.get("classname") == "sky_camera"), None)
    if cam:
        o = tuple(float(v) for v in cam.get("origin", "0 0 0").split())
        sky_area = b.leafs[b.leaf_of(o)]["area"]
    sky_faces, sky_brushes = set(), set()
    if sky_area is not None:
        for leaf in b.leafs:
            if leaf["area"] == sky_area and leaf["contents"] != 1:
                ff, nf = leaf["faces"]
                sky_faces.update(b.leaffaces[ff : ff + nf])
                fb, nb = leaf["brushes"]
                sky_brushes.update(b.leafbrushes[fb : fb + nb])

    # brush entities to draw, with their transforms; the doors players open and what hangs on them stay out (`_door_parts`)
    door_parts = _door_parts(ents)
    brush_models = []  # (model index, rotation, origin, collide kind, classname)
    for ei, e in enumerate(ents):
        cls = e.get("classname", "")
        m = e.get("model", "")
        if cls in BRUSH_CLASSES and m.startswith("*"):
            if ei in door_parts:
                continue
            if e.get("startdisabled") == "1":
                continue  # another mode's wall, switched on by its script (Mirage's 29 retake.asite / retake.bsite blockers)
            if cls == "func_brush" and e.get("solidity") == "1":
                kind = None
            else:
                kind = BRUSH_CLASSES[cls]
            ang = [float(x) for x in (e.get("angles", "0 0 0").split() + ["0", "0", "0"])[:3]]
            org = tuple(float(x) for x in (e.get("origin", "0 0 0").split() + ["0", "0", "0"])[:3])
            ang, org = _door_open(b, e, int(m[1:]), ang, org)
            brush_models.append((int(m[1:]), angle_matrix(*ang), org, kind, cls))
    if door_parts:
        n_doors = sum(_is_door(ents[i]) for i in door_parts)
        parts = sorted({ents[i].get("classname", "?") for i in door_parts if not _is_door(ents[i])})
        n_parts = len(door_parts) - n_doors
        log(f"  doors left out: {n_doors}, with {n_parts} parts on them or played with them ({', '.join(parts) or 'none'})")

    # ---- faces: collect geometry with lightmap rects ----------------------------------------------------------------
    atlas = Atlas(2048)
    atlas.add("white", 4, 4, None)
    face_jobs = []  # (face index, rotation, origin)
    w0 = b.models[0]
    for fi in range(w0["firstface"], w0["firstface"] + w0["numfaces"]):
        face_jobs.append((fi, None, None))
    for mi, rot, org, _kind, _cls in brush_models:
        m = b.models[mi]
        for fi in range(m["firstface"], m["firstface"] + m["numfaces"]):
            face_jobs.append((fi, rot, org))

    mat_cache = {}

    def mat_of(name):
        if name not in mat_cache:
            mat_cache[name] = tex.material(name)
        return mat_cache[name]

    drawn = []
    skipped = {"flags": 0, "tools": 0, "sky": 0}
    for fi, rot, org in face_jobs:
        f = b.faces[fi]
        ti = b.texinfo[f["texinfo"]] if f["texinfo"] >= 0 else None
        if ti is None:
            continue
        if ti["flags"] & SKIP_FLAGS:
            skipped["flags"] += 1
            continue
        name = b.texdata[ti["texdata"]]["name"]
        if name.startswith("tools/"):
            skipped["tools"] += 1
            continue
        if rot is None and f["dispinfo"] < 0 and fi in sky_faces:
            skipped["sky"] += 1
            continue
        if rot is None and f["dispinfo"] >= 0 and sky_area is not None:
            st = b.dispinfo[f["dispinfo"]]["start"]
            if b.leafs[b.leaf_of(st)]["area"] == sky_area:
                skipped["sky"] += 1
                continue
        samples = None if ti["flags"] & B.SURF_NOLIGHT else b.lightmap_samples(f)
        if samples is not None:
            atlas.add(fi, f["lsize"][0] + 1, f["lsize"][1] + 1, samples)
        drawn.append((fi, rot, org, name, samples is not None))
    atlas.pack()

    prims = Prims()
    W, H = atlas.w, atlas.h
    wx, wy, _ww, _wh = atlas.rects["white"]
    white_uv = ((wx + 2) / W, (wy + 2) / H)

    def lm_uv(f, ti, p, lit, fi):
        if not lit:
            return white_uv
        rx, ry, _w, _h = atlas.rects[fi]
        (l0, l1) = ti["lv"]
        s = _dot(p, l0[:3]) + l0[3] - f["lmins"][0]
        t = _dot(p, l1[:3]) + l1[3] - f["lmins"][1]
        s = min(max(s, 0.0), f["lsize"][0])
        t = min(max(t, 0.0), f["lsize"][1])
        return ((rx + s + 0.5) / W, (ry + t + 0.5) / H)

    def tex_uv(ti, td, p):
        (t0_, t1_) = ti["tv"]
        return ((_dot(p, t0_[:3]) + t0_[3]) / max(td["w"], 1), (_dot(p, t1_[:3]) + t1_[3]) / max(td["h"], 1))

    def place(p, rot, org):
        if rot is None:
            return p
        q = _mul(rot, p)
        return (q[0] + org[0], q[1] + org[1], q[2] + org[2])

    disp_collision = []  # triangles (Source inches) of displacements, for collision.glb
    blend_keys = set()
    for fi, rot, org, name, lit in drawn:
        f = b.faces[fi]
        ti = b.texinfo[f["texinfo"]]
        td = b.texdata[ti["texdata"]]
        pl = b.planes[f["plane"]]
        # the face's own plane faces its front whatever `side` says (that bit is relative to the node the face sits on): every
        # face of Dust2 and Mirage winds clockwise about planes[planenum], 4178 with side 0 and 1983 with side 1
        n_src = (pl[0], pl[1], pl[2])
        n_world = _mul(rot, n_src) if rot else n_src
        n3 = three(n_world)
        n3 = (n3[0] / INCH, n3[1] / INCH, n3[2] / INCH)
        if f["dispinfo"] < 0:
            poly = b.face_verts(f)
            if len(poly) < 3:
                continue
            verts = [(three(place(p, rot, org)), n3, tex_uv(ti, td, p), lm_uv(f, ti, p, lit, fi)) for p in poly]
            # the polygon winds clockwise seen from the front in Source; reverse for glTF's counter-clockwise
            a0 = poly[0]
            tris = []
            for k in range(1, len(poly) - 1):
                c = _cross(_sub(poly[k], a0), _sub(poly[k + 1], a0))
                tris.append((0, k, k + 1) if _dot(c, n_src) > 0 else (0, k + 1, k))
            prims.add_tri_list(name, verts, tris)
            continue
        # displacement
        d = b.dispinfo[f["dispinfo"]]
        corners = b.face_verts(f)
        if len(corners) != 4:
            continue
        st = d["start"]
        k0 = min(range(4), key=lambda k: sum((corners[k][i] - st[i]) ** 2 for i in range(3)))
        c = corners[k0:] + corners[:k0]
        n = 1 << d["power"]
        size = n + 1
        grid, base, alpha = [], [], []
        for i in range(size):
            v1 = _lerp(c[0], c[1], i / n)
            v2 = _lerp(c[3], c[2], i / n)
            for j in range(size):
                bp = _lerp(v1, v2, j / n)
                dv = b.dispverts[d["vstart"] + i * size + j]
                grid.append((bp[0] + dv[0] * dv[3], bp[1] + dv[1] * dv[3], bp[2] + dv[2] * dv[3]))
                base.append(bp)
                alpha.append(dv[4])
        tris = []
        for i in range(n):
            for j in range(n):
                a_, b_, c_, d_ = i * size + j, i * size + j + 1, (i + 1) * size + j, (i + 1) * size + j + 1
                pair = ((a_, c_, b_), (b_, c_, d_)) if (i + j) % 2 == 0 else ((a_, c_, d_), (a_, d_, b_))
                for tri in pair:
                    cr = _cross(_sub(base[tri[1]], base[tri[0]]), _sub(base[tri[2]], base[tri[0]]))
                    tris.append(tri if _dot(cr, n_src) > 0 else (tri[0], tri[2], tri[1]))
        # smooth normals over the displaced grid
        acc = [[0.0, 0.0, 0.0] for _ in grid]
        for t in tris:
            cr = _cross(_sub(grid[t[1]], grid[t[0]]), _sub(grid[t[2]], grid[t[0]]))
            for k in t:
                acc[k][0] += cr[0]
                acc[k][1] += cr[1]
                acc[k][2] += cr[2]
        verts = []
        for k, p in enumerate(grid):
            nn = _norm(acc[k])
            verts.append(
                (three(place(p, rot, org)), (nn[0], nn[2], -nn[1]), tex_uv(ti, td, base[k]), lm_uv(f, ti, base[k], lit, fi))
            )
        shader, keys = mat_of(name)
        if "worldvertextransition" in shader and keys.get("$basetexture2"):
            first, second = [], []
            for t in tris:
                (second if (alpha[t[0]] + alpha[t[1]] + alpha[t[2]]) / 3 > 127.5 else first).append(t)
            prims.add_tri_list(name, verts, first)
            if second:
                prims.add_tri_list(name + "#2", verts, second)
                blend_keys.add(name + "#2")
        else:
            prims.add_tri_list(name, verts, tris)
        if rot is None:
            disp_collision.append(([place(p, rot, org) for p in grid], tris))

    # ---- world.glb ------------------------------------------------------------------------------------------------------
    world = Glb(textures=(tex_dir, "textures/"))
    refl = {t["name"]: t["refl"] for t in b.texdata}
    prim_list = []
    surfaces = {}
    for key, p in prims.by.items():
        base_name = key[:-2] if key.endswith("#2") else key
        shader, keys = mat_of(base_name)
        mi = material_index(
            world,
            tex,
            key,
            keys,
            shader,
            WORLD_TEX,
            "$basetexture2" if key.endswith("#2") else "$basetexture",
            fallback=refl.get(base_name),
        )
        prim_list.append((mi, p["pos"], p["nor"], p["uv0"], p["uv1"], p["idx"]))
        s = surface_of(keys, base_name)
        if s:
            surfaces[key] = s
    world.mesh("world", prim_list)
    world_bytes = world.write(os.path.join(out, "assets", "world.glb"))
    world_tris = sum(len(p["idx"]) // 3 for p in prims.by.values())
    lm_img = atlas.image()
    lm_img.save(os.path.join(out, "assets", "lightmap.png"), optimize=True)
    log(
        f"  world.glb {world_bytes / 1048576:.1f} MB: {world_tris} triangles, {len(prims.by)} materials; "
        f"lightmap {atlas.w}x{atlas.h} "
        f"({len(atlas.items) - 1} faces); skipped {skipped}"
    )

    # ---- collision.glb ----------------------------------------------------------------------------------------------------
    solid, clip = [], []  # triangles in Source inches
    ladders = []
    world_brushes = model_brushes(b, 0)
    kinds = {"solid": 0, "clip": 0, "ladder": 0}
    for bi in world_brushes:
        if bi in sky_brushes:
            continue
        contents = b.brushes[bi][2]
        if contents & B.CONTENTS_LADDER:
            ladders.append(bi)
        if contents & (B.CONTENTS_SOLID | B.CONTENTS_WINDOW):
            target, kinds["solid"] = solid, kinds["solid"] + 1
        elif contents & (B.CONTENTS_PLAYERCLIP | B.CONTENTS_GRATE | B.CONTENTS_LADDER):
            target, kinds["clip"] = clip, kinds["clip"] + 1
        else:
            continue
        for poly, _n in brush_polygons(b, bi):
            for k in range(1, len(poly) - 1):
                target.append((poly[0], poly[k], poly[k + 1]))
    for mi, rot, org, kind, _cls in brush_models:
        if not kind:
            continue
        target = solid if kind == "solid" else clip
        for bi in model_brushes(b, mi):
            if not b.brushes[bi][2] & (B.CONTENTS_SOLID | B.CONTENTS_WINDOW | B.CONTENTS_GRATE | B.CONTENTS_PLAYERCLIP):
                continue
            for poly, _n in brush_polygons(b, bi):
                poly = [place(p, rot, org) for p in poly]
                for k in range(1, len(poly) - 1):
                    target.append((poly[0], poly[k], poly[k + 1]))
    for grid, tris in disp_collision:
        for t in tris:
            solid.append((grid[t[0]], grid[t[1]], grid[t[2]]))

    # ---- props ---------------------------------------------------------------------------------------------------------
    placements = []  # (model, skin, origin, angles, scale, collide)
    # props standing in the 3D skybox's area are its miniature (Dust2's `_skybx` palms) and stay out with it
    in_sky = (lambda o: b.leafs[b.leaf_of(o)]["area"] == sky_area) if sky_area is not None else (lambda o: False)
    sky_props = 0
    for sp in b.static_props:
        if in_sky(sp["origin"]):
            sky_props += 1
            continue
        placements.append((sp["model"], sp["skin"], sp["origin"], sp["angles"], sp["scale"], sp["solid"]))
    for ei, e in enumerate(ents):
        cls = e.get("classname", "")
        if ei in door_parts:
            continue  # a door's knob or handle (`_door_parts`)
        if cls in PROP_CLASSES and e.get("model", "").endswith(".mdl") and e.get("startdisabled") != "1":
            ang = [float(x) for x in (e.get("angles", "0 0 0").split() + ["0", "0", "0"])[:3]]
            org = tuple(float(x) for x in e.get("origin", "0 0 0").split()[:3])
            if in_sky(org):
                sky_props += 1
                continue
            solid_key = e.get("solid", "6")
            placements.append(
                (
                    e["model"].lower(),
                    int(float(e.get("skin", "0") or 0)),
                    org,
                    ang,
                    float(e.get("modelscale", "1") or 1),
                    int(float(solid_key or 0)),
                )
            )
    assets, lines, failed = {}, [], set()
    prop_bytes = 0
    # a prop collides as the game does, by its .phy's convex pieces (solid 6, vphysics) or its hull box (solid 2), and those go
    # into collision.glb with the brushes: props.world draws every prop, `collide: false`. A model without a .phy is not solid
    hulls = {}
    prop_tris = 0
    stats = {"phy": 0, "phy in bone space": 0, "render mesh": 0, "box": 0, "none": 0}
    for model, _skin, org, ang, scale, solid_type in placements:
        if solid_type in (2, 6) and not os.environ.get("NO_PROP_COLLISION"):
            hk = (model, solid_type)
            if hk not in hulls:
                hulls[hk] = _prop_hull(fs, model, solid_type, stats)
            if hulls[hk]:
                m3 = angle_matrix(*ang)
                for tri in hulls[hk]:
                    solid.append(tuple(place((v[0] * scale, v[1] * scale, v[2] * scale), m3, org) for v in tri))
                prop_tris += len(hulls[hk])
    for model, skin, org, ang, scale, _solid in placements:
        key = (model, skin)
        if key not in assets:
            m = mdl.load(fs, model, skin) if model not in failed else None
            if not m or not m["parts"]:
                failed.add(model)
                assets[key] = None
            else:
                aname = re.sub(r"[^a-z0-9_]+", "_", model.removeprefix("models/").removesuffix(".mdl")).strip("_") + (
                    f"_skin{skin}" if skin else ""
                )
                g = Glb(textures=(tex_dir, "../textures/"))
                by_mat = {}
                for mat_i, pos, nor, uv, idx in m["parts"]:
                    by_mat.setdefault(mat_i, []).append((pos, nor, uv, idx))
                prim_list = []
                for mat_i, chunks in by_mat.items():
                    mname = m["materials"][mat_i]
                    shader, keys = mat_of(mname)
                    gi = material_index(g, tex, mname, keys, shader, PROP_TEX)
                    s = surface_of(keys, mname)
                    if s:
                        surfaces[mname] = s
                    P, N, U, I = [], [], [], []
                    for pos, nor, uv, idx in chunks:
                        base = len(P) // 3
                        for p in pos:
                            P += three(p)
                        for q in nor:
                            N += (q[0], q[2], -q[1])
                        for q in uv:
                            U += q
                        I += [base + i for i in idx]
                    prim_list.append((gi, P, N, U, None, I))
                g.mesh(aname, prim_list)
                prop_bytes += g.write(os.path.join(out, "assets", "props", f"{aname}.glb"))
                assets[key] = aname
        aname = assets[key]
        if not aname:
            continue
        at = three(org)
        rot = three_euler(angle_matrix(*ang))
        lines.append(
            f"  model {{ asset: {aname}, at: {at[0]:.3f} {at[1]:.3f} {at[2]:.3f}, rotate: {rot[0]} {rot[1]} {rot[2]}"
            f"{f', scale: {scale:.4f}' if abs(scale - 1) > 1e-4 else ''}, collide: false }}"
        )
    used = sorted({a for a in assets.values() if a})
    with open(os.path.join(out, "props.world"), "w") as fh:
        fh.write(f"// GENERATED by `python -m scripts.source export {game} {map_name}`: do not edit by hand.\n//\n")
        fh.write(f"// {len(lines)} model placements over {len(used)} assets ({len(failed)} models did not load).\n\n")
        for a in used:
            fh.write(f'asset {a} {{ source: "assets/props/{a}.glb" }}\n')
        fh.write("\ngroup props {\n  at: 0 0 0\n")
        fh.write("\n".join(lines) + "\n}\n")
    log(
        f"  props: {len(lines)} placements of {len(used)} models, {prop_bytes / 1048576:.1f} MB; "
        f"{sky_props} in the 3D skybox left out; "
        f"{len(failed)} failed: {sorted(failed)[:6]}"
    )

    coll = Glb()
    for node_name, tris in (("solid", solid), ("clip0x10000", clip)):
        # shared corners welded at 1 mm: brush polygons and hull pieces meet at the same points
        pos, idx, at = [], [], {}
        for t in tris:
            for p in t:
                q = three(p)
                k = (round(q[0], 3), round(q[1], 3), round(q[2], 3))
                i = at.get(k)
                if i is None:
                    i = at[k] = len(pos) // 3
                    pos += k
                idx.append(i)
        coll.mesh(node_name, [(None, pos, None, None, None, idx)])
    coll_bytes = coll.write(os.path.join(out, "assets", "collision.glb"))
    log(
        f"  collision.glb {coll_bytes / 1048576:.1f} MB: {len(solid)} solid ({prop_tris} of them props') + {len(clip)} clip "
        f"triangles ({kinds}); prop models collide by {stats}"
    )

    # ---- ladders ------------------------------------------------------------------------------------------------------
    lad_lines = []
    for k, bi in enumerate(ladders):
        pts = [p for poly, _n in brush_polygons(b, bi) for p in poly]
        if not pts:
            continue
        # the box's yaw from its biggest vertical side, then its extent in that frame
        best, yaw = 0.0, 0.0
        for poly, nrm in brush_polygons(b, bi):
            if abs(nrm[2]) > 0.1:
                continue
            area = 0.0
            for i in range(1, len(poly) - 1):
                c = _cross(_sub(poly[i], poly[0]), _sub(poly[i + 1], poly[0]))
                area += math.sqrt(_dot(c, c)) / 2
            if area > best:
                best, yaw = area, math.atan2(nrm[1], nrm[0])
        e3 = [three(p) for p in pts]
        # three's rotateY(t) takes +X to (cos t, 0, -sin t), which is Source heading t: the box's yaw is the side normal's own
        # heading. A world offset (x, z) goes into the box's frame by rotateY(-t) (world.js parseVolumes)
        t = yaw
        cx = sum(p[0] for p in e3) / len(e3)
        cz = sum(p[2] for p in e3) / len(e3)
        ct, st_ = math.cos(t), math.sin(t)
        loc = [((p[0] - cx) * ct - (p[2] - cz) * st_, p[1], (p[0] - cx) * st_ + (p[2] - cz) * ct) for p in e3]
        lo = [min(q[i] for q in loc) for i in range(3)]
        hi = [max(q[i] for q in loc) for i in range(3)]
        mx, mz = (lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2
        ccx = cx + mx * ct + mz * st_
        ccz = cz - mx * st_ + mz * ct
        lad_lines.append(
            f"ladder l{k} {{ at: {ccx:.3f} {(lo[1] + hi[1]) / 2:.3f} {ccz:.3f}, size: {hi[0] - lo[0]:.3f} {hi[1] - lo[1]:.3f} "
            f"{hi[2] - lo[2]:.3f}, rotate: {math.degrees(t):.1f} }}"
        )
    with open(os.path.join(out, "ladder.world"), "w") as fh:
        fh.write(
            f"// {map_name}'s ladder brushes (CONTENTS_LADDER), as oriented boxes. GENERATED by scripts.source; each is also a\n"
        )
        fh.write(f"// clip wall in collision.glb. {len(lad_lines)} ladder(s).\n\n" + "\n".join(lad_lines) + "\n")

    # ---- lighting, spawns, minimap, metadata ---------------------------------------------------------------------------
    sun = next((e for e in ents if e.get("classname") == "light_environment"), None)
    ws = {}
    if sun:
        yaw = float((sun.get("angles", "0 0 0").split() + ["0", "0"])[1])
        pitch = float(sun.get("pitch", "-45"))
        elev = -pitch  # degrees of the light below the horizon
        light = [float(x) for x in (sun.get("_light", "255 255 255 200").split() + ["200"])[:4]]
        amb = [float(x) for x in (sun.get("_ambient", "128 128 128 100").split() + ["100"])[:4]]
        ws["sundirection"] = f"{-elev:.2f} {yaw + 180:.2f} 0"
        ws["suncolor"] = " ".join(f"{c / 255:.4f}" for c in light[:3])
        ws["sunlight"] = "0.9"
        ws["skylight"] = "0.2"
        ws["_ambient"] = " ".join(f"{c / 255:.4f}" for c in amb[:3])
    fog = next((e for e in ents if e.get("classname") == "env_fog_controller"), None)
    sky_rgb = _sky_colour(fs, worldspawn.get("skyname", "")) or (
        tuple(float(x) / 255 for x in fog["fogcolor"].split()[:3]) if fog and fog.get("fogcolor") else (0.73, 0.78, 0.83)
    )
    ws["skycolor"] = " ".join(f"{c:.4f}" for c in sky_rgb)
    spawns = []
    for e in ents:
        cls = e.get("classname", "")
        team = {"info_player_terrorist": "axis", "info_player_counterterrorist": "allies"}.get(cls)
        if not team:
            continue
        o = tuple(float(x) for x in e.get("origin", "0 0 0").split()[:3])
        yaw = float((e.get("angles", "0 0 0").split() + ["0", "0"])[1])
        at = three(o)
        spawns.append({"class": cls, "mode": "tdm", "team": team, "start": True, "at": [round(v, 3) for v in at], "yaw": yaw})
    minimap = _minimap(fs, game, map_name, os.path.join(out, "assets", "minimap.png"), log)
    meta = {
        "_about": f"{map_name} from {cfg['name']}, exported by mw2kronk scripts.source",
        "map": map_name,
        "game": game,
        "game_name": cfg["name"],
        "worldspawn": ws,
        "spawns": spawns,
        "spawn_counts": {"axis": sum(s["team"] == "axis" for s in spawns), "allies": sum(s["team"] == "allies" for s in spawns)},
        "entity_counts": {},
        "sky_area": sky_area,
        "skyname": worldspawn.get("skyname"),
    }
    if minimap:
        meta["minimap"] = minimap
    for e in ents:
        c = e.get("classname", "?")
        meta["entity_counts"][c] = meta["entity_counts"].get(c, 0) + 1
    with open(os.path.join(out, "export_metadata.json"), "w") as fh:
        json.dump(meta, fh, indent=1)
    for k in blend_keys:
        surfaces.setdefault(k, surfaces.get(k[:-2]))
    with open(os.path.join(out, "assets", "surfaces.json"), "w") as fh:
        json.dump({k: v for k, v in sorted(surfaces.items()) if v}, fh, indent=0)

    # ---- world.world ------------------------------------------------------------------------------------------------------
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for p in prims.by.values():
        P = p["pos"]
        for i in range(3):
            if P[i::3]:
                lo[i] = min(lo[i], min(P[i::3]))
                hi[i] = max(hi[i], max(P[i::3]))
    size = " ".join(f"{max(0.0, hi[i] - lo[i]):.0f}" for i in range(3))
    sp0 = spawns[0] if spawns else {"at": [0, 0, 0]}
    title = label or map_name
    with open(os.path.join(out, "world.world"), "w") as fh:
        fh.write(f"// {map_name} — {cfg['name']}, imported from the game's own BSP.\n")
        fh.write(f"// GENERATED by `python -m scripts.source export {game} {map_name}`.\n\n")
        fh.write(
            f'world {{\n  name: "{title} ({game.upper()})"\n'
            f'  description: "{cfg["name"]}\'s {map_name}, imported from its BSP."\n'
        )
        fh.write(
            f'  category: "{cfg["name"]}"\n  game: {game}\n  sky: #{"".join(f"{int(c * 255):02x}" for c in sky_rgb)}\n}}\n\n'
        )
        fh.write('asset world { source: "assets/world.glb" }\n\ngroup map {\n  at: 0 0 0\n\n  model shell {\n    asset: world\n')
        fh.write(
            f"    at: 0 0 0\n    size: {size}\n    collide: false\n"
            '    lightmap: "assets/lightmap.png"\n    lightmap-intensity: 1\n  }\n}\n\n'
        )
        fh.write('collision { source: "assets/collision.glb" }\n\ninclude "props.world"\ninclude "ladder.world"\n\n')
        a = sp0["at"]
        fh.write(f"spawn {{ at: {a[0]} {a[1]} {a[2]} }}\n")
    _compress(out, log)
    log(
        f"  {len(spawns)} spawns, {len(lad_lines)} ladders, textures {tex.bytes / 1048576:.1f} MB ({len(tex.missing)} missing), "
        f"{len(fs.fell_back)} files from {fs.cfg.get('fallback')}, {time.time() - t0:.1f} s -> {out}"
    )
    return {"out": out, "missing_textures": sorted(tex.missing), "fs": fs}


def _is_door(e):
    """A door a player walks through: a rotating one (brush or model), or a func_door that slides sideways (`movedir` pitch under
    45 degrees). A func_door that moves up or down is a lift or a gate and stays (`_door_open` raises it)."""
    cls = e.get("classname", "")
    if cls in ("func_door_rotating", "prop_door_rotating"):
        return True
    if cls == "func_door":
        md = (e.get("movedir", "0 0 0").split() + ["0"])[0]
        try:
            return abs(float(md)) < 45
        except ValueError:
            return True
    return False


def _door_parts(ents):
    """The entities left out as doors: every door (`_is_door`) and everything parented to one, at any depth (Nuke's reactor doors
    carry their window, a func_breakable of glass, and a doorknob prop on each face; Inferno's door01 two handles); and a dynamic
    prop that an entity's outputs animate (SetAnimation) beside opening or removing a door, the door's drawn part (Inferno's
    apartment gate: a trigger_once kills the invisible func_door ele_door_R and plays prop_elevatordoor_top_1's "open"; the export
    drew that model unposed, a grey sheet along the corridor). VoD2 has no doors that open, and a door turned open in the export
    still left its parented parts where the closed door had them: the windows stood in Nuke's B doorways as player-high glass that
    blocked the way, the knobs floated in mid air. Without the door the doorway is the frame the world's brushes give it. Returns
    the set of their indexes in `ents`."""
    gone = {i for i, e in enumerate(ents) if _is_door(e)}
    names = set()

    def close():  # what hangs on anything gone goes too
        grew = True
        while grew:
            names.update(ents[i].get("targetname", "").lower() for i in gone)
            names.discard("")
            grew = False
            for i, e in enumerate(ents):
                parent = e.get("parentname", "").split(",")[0].strip().lower()
                if i not in gone and parent and parent in names:
                    gone.add(i)
                    grew = True

    close()
    animated = set()
    for e in ents:
        outs = [[f.strip().lower() for f in re.split(r"[,\x1b]", v)] for _k, v in e.get("_outputs", [])]
        if any(o[0] in names for o in outs):
            animated.update(o[0] for o in outs if len(o) > 1 and o[1].startswith("setanimation"))
    props = {i for i, e in enumerate(ents) if e.get("classname", "").startswith("prop_dynamic")}
    gone.update(i for i in props if ents[i].get("targetname", "").lower() in animated)
    close()
    return gone


def _door_open(b, e, model, ang, org):
    """A door the export keeps (a func_door that moves up or down; `_door_parts` leaves the others out) stands open: VoD2 has no
    doors that move. func_door_rotating turns `distance` degrees (90) about its origin, round Z, X with spawnflags 64 or Y with
    128, the other way with 2; func_door slides along `movedir` by its own length that way less `lip`, as the game opens it."""
    cls = e.get("classname")
    flags = int(float(e.get("spawnflags", "0") or 0))
    if cls == "func_door_rotating":
        dist = float(e.get("distance", "90") or 90) * (-1 if flags & 2 else 1)
        k = 2 if flags & 64 else 0 if flags & 128 else 1  # roll (X), pitch (Y), yaw (Z)
        ang = list(ang)
        ang[k] += dist
        return ang, org
    if cls == "func_door":
        md = [float(x) for x in (e.get("movedir", "0 0 0").split() + ["0", "0", "0"])[:3]]
        d = angle_matrix(*md)
        d = (d[0][0], d[1][0], d[2][0])  # forward
        m = b.models[model]
        ext = [m["maxs"][i] - m["mins"][i] for i in range(3)]
        travel = abs(_dot(ext, d)) - float(e.get("lip", "0") or 0)
        return ang, tuple(org[i] + d[i] * travel for i in range(3))
    return ang, org


def _prop_hull(fs, model, solid_type, stats):
    """A solid prop's collision triangles (model space, inches). The .phy as stored, or turned into model space by its bone,
    whichever lies inside the drawn mesh's box (a piece outside it belongs to an animated pose this export does not play:
    Mirage's tarps, Nuke's animated car); failing both, the drawn mesh itself when it is light and not foliage; a solid-box
    prop gets its hull box."""
    if solid_type == 2:
        stats["box"] += 1
        return phy.hull_box(fs, model)
    m = mdl.load(fs, model)
    pts = [v for part in (m["parts"] if m else []) for v in part[1]]
    if not pts:
        stats["none"] += 1
        return None
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    pad = [0.1 * (hi[i] - lo[i]) + 4 for i in range(3)]

    def inside(tris):
        return tris and all(lo[i] - pad[i] <= v[i] <= hi[i] + pad[i] for t in tris for v in t for i in range(3))

    raw = phy.load(fs, model, bone_space=False)
    if inside(raw):
        stats["phy"] += 1
        return raw
    if raw:
        boned = phy.load(fs, model)
        if inside(boned):
            stats["phy in bone space"] += 1
            return boned
    tri_count = sum(len(part[4]) // 3 for part in m["parts"])
    if tri_count <= 3000 and not re.search(r"foliage|tree|palm|plant|bush|grass|ivy|vine", model):
        stats["render mesh"] += 1
        return [tuple(part[1][part[4][k + j]] for j in range(3)) for part in m["parts"] for k in range(0, len(part[4]), 3)]
    stats["none"] += 1
    return None


def _compress(out, log):
    """gltfpack -cc over world.glb and the props when $GLTFPACK names it (`npm i gltfpack`, then its cli.js or .bin path):
    meshopt-compressed, quantised geometry (EXT_meshopt_compression, which VoD2's loader decodes); names, materials and both UV
    sets kept (-kn -km -kv), the shared textures left where they are (-tr), texture coordinates in floats (-vtf: brush UVs
    tile far outside 0-1), world positions at 16
    bits. collision.glb stays as it is: the collider reads its node names and exact positions."""
    tool = os.environ.get("GLTFPACK")
    if not tool:
        log("  geometry not compressed ($GLTFPACK not set)")
        return
    import subprocess

    cmd = ["node", tool] if tool.endswith(".js") else [tool]
    files = [os.path.join(out, "assets", "world.glb")] + sorted(
        os.path.join(out, "assets", "props", f) for f in os.listdir(os.path.join(out, "assets", "props")) if f.endswith(".glb")
    )
    before = sum(os.path.getsize(f) for f in files)
    for f in files:
        tmp = f[:-4] + ".packed.glb"
        extra = ["-vp", "16"] if f.endswith("world.glb") else []
        r = subprocess.run(
            [*cmd, "-i", f, "-o", tmp, "-cc", "-kn", "-km", "-kv", "-vtf", "-tr", *extra], capture_output=True, text=True
        )
        if r.returncode == 0 and os.path.exists(tmp):
            os.replace(tmp, f)
        else:
            log(f"  gltfpack failed on {os.path.basename(f)}: {r.stderr.strip()[:200]}")
            if os.path.exists(tmp):
                os.remove(tmp)
    after = sum(os.path.getsize(f) for f in files)
    log(f"  gltfpack: world + props {before / 1048576:.1f} -> {after / 1048576:.1f} MB")


def _sky_colour(fs, skyname):
    """The 2D sky's horizon colour: the mean of the lower half of its front face, or None."""
    if not skyname:
        return None
    for suffix in ("ft", "rt", "bk", "lf"):
        shader, keys = fs.material(f"skybox/{skyname}{suffix}")
        texname = keys.get("$basetexture") or keys.get("$hdrcompressedtexture") or keys.get("$hdrbasetexture")
        data = fs.read(f"materials/{texname.lower()}.vtf") if texname else None
        if not data:
            continue
        img, _a = vtf.decode(data)
        if img is None:
            continue
        img = img.convert("RGB").resize((32, 32))
        px = [img.getpixel((x, y)) for x in range(32) for y in range(4, 16)]
        return tuple(sum(p[i] for p in px) / len(px) / 255 for i in range(3))
    return None


def _minimap(fs, game, map_name, dst, log):
    """The radar overview turned 90 degrees anticlockwise (image up = +X, CoD's compass convention), with its corners in
    Source inches as export_metadata.json's `minimap.image_corners`."""
    txt = fs.read(f"resource/overviews/{map_name}.txt")
    if not txt:
        log("  no radar overview")
        return None
    from . import kv

    _shader, keys = kv.parse_vmt(txt.decode("latin-1"))
    px, py, scale = float(keys.get("pos_x", 0)), float(keys.get("pos_y", 0)), float(keys.get("scale", 1))
    img = None
    data = fs.read(f"resource/overviews/{map_name}_radar.dds")
    if data:
        try:
            img = Image.open(io.BytesIO(data)).convert("RGBA")
        except Exception:  # noqa: BLE001 - a DDS Pillow cannot read
            img = None
    if img is None:
        mat = keys.get("material") or f"overviews/{map_name}"
        _s, mk = fs.material(mat)
        tname = mk.get("$basetexture") or mat
        vt = fs.read(f"materials/{tname.lower()}.vtf")
        if vt:
            img, _a = vtf.decode(vt)
    if img is None:
        log("  radar image not found")
        return None
    side = img.size[0]
    img = img.rotate(90, expand=True).resize((512, 512), Image.LANCZOS)
    img.save(dst, optimize=True)
    span = side * scale
    tl = [px + span, py]
    br = [px, py - span]
    return {
        "material": f"overviews/{map_name}",
        "size": [512, 512],
        "source": "resource/overviews",
        "image_corners": {"top_left": {"origin": tl}, "bottom_right": {"origin": br}},
    }
