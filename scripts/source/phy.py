"""A studio model's collision (`.phy`): the convex pieces Havok/IVP collides it with, as triangles in Source inches.

A PHY is a 16-byte header (size, id, solid count, checksum) and then each solid: its byte size, an optional
'VPHY' header (28 bytes), an IVP compact surface (48 bytes, its ledge tree root offset at 32) and the ledges
before that root, one after another, the terminal ones (flags & 3 == 0) the pieces. A ledge is 16 bytes (point offset,
client data, flags, triangle count) followed
by its triangles (16 bytes: an info word, then three edges whose low 16 bits name a start point); the points (float
x4) sit at the ledge's own offset + its point offset. IVP keeps metres with
its own axes: Source (x, y, z) = (ivp x, ivp z, -ivp y) x 39.37.
"""

import re
import struct

_IN_PER_M = 1 / 0.0254


def load(fs, path, bone_space=True):
    """[(a, b, c)] triangles of every convex piece in model space (Source inches), or None without a .phy. Each solid is
    stored in the space of the bone the PHY's text part names for it (`solid { "index" "0" "name" "<bone>" }`); a static
    prop's one bone is the identity, a physics prop's root bone is often turned 90 degrees."""
    data = fs.read(path.lower().removeprefix("./")[:-4] + ".phy")
    if not data or len(data) < 16:
        return None
    hsize, _id, solids, _ck = struct.unpack_from("<4i", data, 0)
    at = hsize
    tris = []
    pieces = []  # (solid index, first triangle)
    for si in range(solids):
        pieces.append((si, len(tris)))
        if at + 4 > len(data):
            break
        size = struct.unpack_from("<i", data, at)[0]
        start = at + 4
        surf = start + 28 if data[start : start + 4] == b"VPHY" else start
        if surf + 48 > len(data):
            break
        root = surf + struct.unpack_from("<i", data, surf + 32)[0]
        ledge = surf + 48
        while ledge + 16 <= min(root, len(data)):
            point_ofs, _client, flags, ntri = struct.unpack_from("<iiIh", data, ledge)
            lsize = (flags >> 8) * 16
            points = ledge + point_ofs
            if ntri <= 0 or ledge + 16 + 16 * ntri > len(data) or not 0 < points < len(data):
                break  # past the last ledge (or a layout this reader does not know): keep what was read
            if flags & 3:
                # has_children: the convex hull of a ledge-tree node's children, kept for broad-phase rejection, not a piece
                # (Nuke's I-beam frame has one round the whole frame, a box across its doorways)
                ledge += 16 + 16 * ntri
                continue
            ok = True
            for t in range(ntri):
                e = struct.unpack_from("<4I", data, ledge + 16 + 16 * t)
                corner = []
                for k in (1, 2, 3):
                    at_p = points + 16 * (e[k] & 0xFFFF)
                    if at_p + 12 > len(data):
                        ok = False
                        break
                    x, y, z = struct.unpack_from("<3f", data, at_p)
                    corner.append((x * _IN_PER_M, z * _IN_PER_M, -y * _IN_PER_M))
                if not ok:
                    break
                tris.append(tuple(corner))
            if not ok:
                break
            # the next ledge follows this one's triangles: its size field also counts the points it owns in the shared
            # point array, which lies after the ledge tree
            ledge += 16 + 16 * ntri
            if ntri <= 0 or lsize <= 0:
                break
        at = start + size
    if not tris:
        return None
    # the text part after the solids: each solid's bone, then into model space
    names = {}
    text = data[at:].decode("latin-1", "replace")
    for m in re.finditer(r"solid\s*\{([^}]*)\}", text):
        idx = re.search(r'"index"\s*"(\d+)"', m.group(1))
        nm = re.search(r'"name"\s*"([^"]*)"', m.group(1))
        if idx and nm:
            names[int(idx.group(1))] = nm.group(1).lower()
    from . import mdl

    bone = mdl.bones(fs, path) if bone_space else {}
    ends = [p[1] for p in pieces[1:]] + [len(tris)]
    for (si, first), last in zip(pieces, ends, strict=True):
        tr = bone.get(names.get(si, "")) or bone.get(0)
        if not tr:
            continue
        r, t = tr
        if all(abs(r[i][j] - (i == j)) < 1e-6 for i in range(3) for j in range(3)) and all(abs(x) < 1e-6 for x in t):
            continue
        for k in range(first, last):
            tris[k] = tuple(tuple(sum(r[i][j] * (v[j] - t[j]) for j in range(3)) for i in range(3)) for v in tris[k])
    return tris


def hull_box(fs, path):
    """The model's hull box (studiohdr hull_min/max), for props whose solid type is a box, or None."""
    mdl = fs.read(path.lower().removeprefix("./"))
    if not mdl or mdl[:4] != b"IDST":
        return None
    lo = struct.unpack_from("<3f", mdl, 104)
    hi = struct.unpack_from("<3f", mdl, 116)
    if hi[0] <= lo[0]:
        return None
    c = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    faces = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))
    return [(c[f[0]], c[f[k]], c[f[k + 1]]) for f in faces for k in (1, 2)]
