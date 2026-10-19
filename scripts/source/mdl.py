"""Source studio models (MDL v44-49 + VVD + VTX): a prop's LOD 0 as triangle lists, one per material.

The MDL names the materials (`textures` searched under each `cdtextures` folder, through a skin table) and
splits the model into body parts, models and meshes; the VVD holds the vertices (48 bytes: bone weights,
position, normal, uv), remapped through its fixup table when the file stores several LODs; the VTX
(`.dx90.vtx`) holds the strips, whose indices point into a strip group's vertex list, which points back into
the mesh's VVD vertices. Static props are posed in their model space, so bones are ignored.
"""

import struct

_VVD_VERTEX = 48


def _cstr(data, at):
    end = data.find(b"\0", at)
    return data[at : end if end >= 0 else len(data)].decode("latin-1")


def _vtx_data(fs, base):
    for ext in (".dx90.vtx", ".vtx", ".dx80.vtx", ".sw.vtx"):
        d = fs.read(base + ext)
        if d:
            return d
    return None


def load(fs, path, skin=0):
    """{'materials': [name], 'parts': [(material index, positions, normals, uvs, indices)]} or None."""
    path = path.lower().replace("\\", "/").removeprefix("./")
    mdl = fs.read(path)
    if not mdl or mdl[:4] != b"IDST":
        return None
    base = path[:-4]
    vvd = fs.read(base + ".vvd")
    vtx = _vtx_data(fs, base)
    if not vvd or not vtx or vvd[:4] != b"IDSV":
        return None
    version = struct.unpack_from("<i", mdl, 4)[0]
    (numtex, texidx, numcd, cdidx, numskinref, numskinfam, skinidx, numbp, bpidx) = struct.unpack_from("<9i", mdl, 204)
    textures = []
    for i in range(numtex):
        at = texidx + i * 64
        textures.append(_cstr(mdl, at + struct.unpack_from("<i", mdl, at)[0]).replace("\\", "/").lower())
    cds = [
        _cstr(mdl, struct.unpack_from("<i", mdl, cdidx + 4 * i)[0]).replace("\\", "/").lower().strip("/") for i in range(numcd)
    ]
    fam = min(max(skin, 0), max(numskinfam - 1, 0))
    skins = struct.unpack_from(f"<{numskinref}h", mdl, skinidx + fam * numskinref * 2) if numskinref else ()
    # materials: the first cdtextures folder holding the VMT
    materials = []
    for t in textures:
        found = None
        for cd in cds or [""]:
            name = f"{cd}/{t}" if cd else t
            if fs.read(f"materials/{name}.vmt") is not None:
                found = name
                break
        materials.append(found or (f"{cds[0]}/{t}" if cds else t))
    # VVD vertices for LOD 0
    (_id, _ver, _ck, numlods) = struct.unpack_from("<4i", vvd, 0)
    lod_verts = struct.unpack_from("<8i", vvd, 16)
    numfix, fixidx, vdata, _tdata = struct.unpack_from("<4i", vvd, 48)
    raw = [struct.unpack_from("<3f3f2f", vvd, vdata + i * _VVD_VERTEX + 16) for i in range(lod_verts[0])] if not numfix else None
    if numfix:
        allv = (len(vvd) - vdata) // _VVD_VERTEX
        src = [struct.unpack_from("<3f3f2f", vvd, vdata + i * _VVD_VERTEX + 16) for i in range(min(allv, max(lod_verts)))]
        raw = []
        for k in range(numfix):
            lod, start, n = struct.unpack_from("<3i", vvd, fixidx + 12 * k)
            if lod >= 0:
                raw.extend(src[start : start + n])
    # VTX: header 36 bytes; body parts 8; models 8; LODs 12; meshes 9; strip groups 25 (+8 from MDL v49); strips 27 (+8)
    vtx_ver, _cache, _mb, _mt, _mv, _ck2, vtx_lods, _mrl, vtx_bp, vtx_bpo = struct.unpack_from("<iiHHiiiiii", vtx, 0)
    sg_size = 25
    parts = []
    for bp in range(numbp):
        bpa = bpidx + bp * 16
        _bpname, nummodels, _bbase, modelidx = struct.unpack_from("<4i", mdl, bpa)
        vbp = vtx_bpo + bp * 8
        vnm, vmo = struct.unpack_from("<2i", vtx, vbp)
        for mi in range(min(nummodels, vnm, 1)):  # body group 0 only
            ma = bpa + modelidx + mi * 148
            nummeshes, meshidx, _nv, vertexindex = struct.unpack_from("<4i", mdl, ma + 72)
            model_first = vertexindex // _VVD_VERTEX
            vm = vbp + vmo + mi * 8
            _nl, lodo = struct.unpack_from("<2i", vtx, vm)
            vl = vm + lodo  # LOD 0
            nmesh, mesho = struct.unpack_from("<2i", vtx, vl)
            for k in range(min(nummeshes, nmesh)):
                mea = ma + meshidx + k * 116
                material, _mi, _mnv, vertexoffset = struct.unpack_from("<4i", mdl, mea)
                vme = vl + mesho + k * 9
                nsg, sgo = struct.unpack_from("<2i", vtx, vme)
                pos, nor, uv, idx = [], [], [], []
                for g in range(nsg):
                    if g == 0 and version >= 49 and nsg > 0:
                        # MDL v49's strip groups carry two more ints; tell by whether the next header lands sensibly
                        sg_size = 33 if _sg_is_v49(vtx, vme + sgo, nsg) else 25
                    sga = vme + sgo + g * sg_size
                    nv, vo, ni, io, _ns, _so, _sgf = struct.unpack_from("<6iB", vtx, sga)
                    base_i = len(pos)
                    for v in range(nv):
                        orig = struct.unpack_from("<H", vtx, sga + vo + v * 9 + 4)[0]
                        r = raw[model_first + vertexoffset + orig] if model_first + vertexoffset + orig < len(raw) else (0,) * 8
                        pos.append(r[0:3])
                        nor.append(r[3:6])
                        uv.append(r[6:8])
                    ids = struct.unpack_from(f"<{ni}H", vtx, sga + io)
                    for t in range(0, ni - 2, 3):
                        # Source's front faces wind clockwise; glTF's counter-clockwise
                        idx.extend((base_i + ids[t], base_i + ids[t + 2], base_i + ids[t + 1]))
                mat = skins[material] if material < len(skins) else material
                if idx:
                    parts.append((min(mat, len(materials) - 1), pos, nor, uv, idx))
    del vtx_ver, vtx_lods, vtx_bp
    return {"materials": materials, "parts": parts}


def _sg_is_v49(vtx, first, n):
    """Strip groups of MDL v49 files are 33 bytes when their VTX says so: check that a 33-byte stride gives
    plausible counts for the second group (or for the first group's own offsets)."""
    if n < 2:
        nv, vo, ni, io, ns, so = struct.unpack_from("<6i", vtx, first)
        return so == 33 or (ns and so == 33)  # the strips follow the 33-byte header directly in studiomdl's layout
    nv2, vo2, ni2, io2 = struct.unpack_from("<4i", vtx, first + 33)
    return 0 <= nv2 < 65536 and 0 <= ni2 < 1 << 20 and vo2 > 0 and io2 > 0


def bones(fs, path):
    """{bone name: (R, t)} taking a point from the bone's space into model space: the inverse of each mstudiobone_t's
    poseToBone (3x4 at byte 96 of a 216-byte bone), R^T (p - t). A PHY's pieces are stored in their bone's space."""
    mdl = fs.read(path.lower().replace("\\", "/").removeprefix("./"))
    if not mdl or mdl[:4] != b"IDST":
        return {}
    numbones, boneidx = struct.unpack_from("<2i", mdl, 156)
    out = {}
    for i in range(numbones):
        at = boneidx + i * 216
        name = _cstr(mdl, at + struct.unpack_from("<i", mdl, at)[0]).lower()
        m = struct.unpack_from("<12f", mdl, at + 96)
        r = ((m[0], m[1], m[2]), (m[4], m[5], m[6]), (m[8], m[9], m[10]))
        t = (m[3], m[7], m[11])
        rt = tuple(tuple(r[j][i2] for j in range(3)) for i2 in range(3))
        out[name] = (rt, t)
        out.setdefault(i, out[name])
    return out
