"""Source engine BSP (VBSP versions 19-21: CS:S and CS:GO): the lumps an export reads.

Source keeps CoD's axes and units: inches, +X forward, +Y left, +Z up, so `scripts/cod`'s engine mapping
(x, y, z) -> (x, z, -y) metres applies unchanged. Struct layouts follow the SDK's bspfile.h; where a lump
changed between versions (leaves, brush sides, static props) its own version field or its stride decides.
"""

import struct
import zlib

from . import kv

LUMP_ENTITIES, LUMP_PLANES, LUMP_TEXDATA, LUMP_VERTEXES, LUMP_VISIBILITY, LUMP_NODES, LUMP_TEXINFO = 0, 1, 2, 3, 4, 5, 6
LUMP_FACES, LUMP_LIGHTING, LUMP_LEAFS, LUMP_EDGES, LUMP_SURFEDGES, LUMP_MODELS = 7, 8, 10, 12, 13, 14
LUMP_LEAFFACES, LUMP_LEAFBRUSHES, LUMP_BRUSHES, LUMP_BRUSHSIDES, LUMP_DISPINFO = 16, 17, 18, 19, 26
LUMP_DISP_VERTS, LUMP_GAME_LUMP, LUMP_PAKFILE, LUMP_TEXDATA_STRING_DATA, LUMP_TEXDATA_STRING_TABLE = 33, 35, 40, 43, 44
LUMP_LIGHTING_HDR, LUMP_FACES_HDR = 53, 58

# texinfo flags
SURF_LIGHT, SURF_SKY2D, SURF_SKY, SURF_WARP, SURF_TRANS, SURF_NODRAW = 0x1, 0x2, 0x4, 0x8, 0x10, 0x80
SURF_HINT, SURF_SKIP, SURF_NOLIGHT, SURF_BUMPLIGHT = 0x100, 0x200, 0x400, 0x800
# brush contents
CONTENTS_SOLID, CONTENTS_WINDOW, CONTENTS_GRATE, CONTENTS_WATER = 0x1, 0x2, 0x8, 0x20
CONTENTS_MOVEABLE, CONTENTS_PLAYERCLIP, CONTENTS_MONSTERCLIP = 0x4000, 0x10000, 0x20000
CONTENTS_DETAIL, CONTENTS_LADDER = 0x8000000, 0x20000000


def _lzma(data):
    """Valve's LZMA wrapper ('LZMA', actual size, lzma size, 5 property bytes) -> bytes."""
    import lzma

    if data[:4] != b"LZMA":
        return data
    actual, _packed = struct.unpack_from("<II", data, 4)
    props = data[12:17]
    hdr = props + struct.pack("<Q", actual)
    return lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(hdr + data[17:])[:actual]


class Bsp:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as fh:
            self.data = data = fh.read()
        ident, self.version = struct.unpack_from("<4si", data, 0)
        if ident != b"VBSP":
            raise ValueError(f"{path}: not a Source BSP")
        self.lumps = [struct.unpack_from("<iii4s", data, 8 + 16 * i) for i in range(64)]
        self._parse()

    def lump(self, i):
        ofs, ln, _ver, cc = self.lumps[i]
        raw = self.data[ofs : ofs + ln]
        return _lzma(raw) if raw[:4] == b"LZMA" else raw

    def lump_version(self, i):
        return self.lumps[i][2]

    def _parse(self):
        L = self.lump
        self.planes = [struct.unpack_from("<4fi", L(LUMP_PLANES), i * 20) for i in range(len(L(LUMP_PLANES)) // 20)]
        v = L(LUMP_VERTEXES)
        self.vertexes = [struct.unpack_from("<3f", v, i * 12) for i in range(len(v) // 12)]
        e = L(LUMP_EDGES)
        self.edges = [struct.unpack_from("<2H", e, i * 4) for i in range(len(e) // 4)]
        se = L(LUMP_SURFEDGES)
        self.surfedges = struct.unpack_from(f"<{len(se) // 4}i", se)
        ti = L(LUMP_TEXINFO)
        self.texinfo = []
        for i in range(len(ti) // 72):
            f = struct.unpack_from("<16fii", ti, i * 72)
            self.texinfo.append({"tv": (f[0:4], f[4:8]), "lv": (f[8:12], f[12:16]), "flags": f[16], "texdata": f[17]})
        sd, st = L(LUMP_TEXDATA_STRING_DATA), L(LUMP_TEXDATA_STRING_TABLE)
        table = struct.unpack_from(f"<{len(st) // 4}i", st)
        names = [sd[o : sd.index(b"\0", o)].decode("latin-1").replace("\\", "/").lower() for o in table]
        td = L(LUMP_TEXDATA)
        self.texdata = []
        for i in range(len(td) // 32):
            r, g, b_, name_id, w, h, _vw, _vh = struct.unpack_from("<3f5i", td, i * 32)
            # reflectivity: the texture's mean linear colour, as vrad read it
            self.texdata.append({"name": names[name_id] if 0 <= name_id < len(names) else "", "w": w, "h": h, "refl": (r, g, b_)})
        # faces: the LDR list when the map has LDR lighting, else the HDR one (CS:GO maps are HDR only)
        self.hdr = not len(L(LUMP_LIGHTING)) and len(L(LUMP_LIGHTING_HDR)) > 0
        fl = L(LUMP_FACES_HDR) if (self.hdr and len(L(LUMP_FACES_HDR))) or not len(L(LUMP_FACES)) else L(LUMP_FACES)
        self.lighting = L(LUMP_LIGHTING_HDR) if self.hdr else L(LUMP_LIGHTING)
        self.faces = []
        for i in range(len(fl) // 56):
            plane, side, _on, firstedge, numedges, texinfo, dispinfo, _fog = struct.unpack_from("<HBBihhhh", fl, i * 56)
            styles = fl[i * 56 + 16 : i * 56 + 20]
            lightofs, _area, mx, my, sx, sy = struct.unpack_from("<if4i", fl, i * 56 + 20)
            self.faces.append(
                {
                    "plane": plane,
                    "side": side,
                    "firstedge": firstedge,
                    "numedges": numedges,
                    "texinfo": texinfo,
                    "dispinfo": dispinfo,
                    "styles": styles,
                    "lightofs": lightofs,
                    "lmins": (mx, my),
                    "lsize": (sx, sy),
                }
            )
        di = L(LUMP_DISPINFO)
        self.dispinfo = []
        for i in range(len(di) // 176):
            sx, sy, sz, vstart, _tstart, power, _mint, _smooth, contents = struct.unpack_from("<3fiiiifi", di, i * 176)
            mapface = struct.unpack_from("<H", di, i * 176 + 36)[0]
            self.dispinfo.append({"start": (sx, sy, sz), "vstart": vstart, "power": power, "contents": contents, "face": mapface})
        dv = L(LUMP_DISP_VERTS)
        self.dispverts = [struct.unpack_from("<5f", dv, i * 20) for i in range(len(dv) // 20)]
        mo = L(LUMP_MODELS)
        self.models = []
        for i in range(len(mo) // 48):
            f = struct.unpack_from("<9fiii", mo, i * 48)
            self.models.append(
                {"mins": f[0:3], "maxs": f[3:6], "origin": f[6:9], "headnode": f[9], "firstface": f[10], "numfaces": f[11]}
            )
        br = L(LUMP_BRUSHES)
        self.brushes = [struct.unpack_from("<3i", br, i * 12) for i in range(len(br) // 12)]
        bs = L(LUMP_BRUSHSIDES)
        fmt = "<HhhBB" if self.version >= 21 else "<Hhhh"
        self.brushsides = []
        for i in range(len(bs) // 8):
            s = struct.unpack_from(fmt, bs, i * 8)
            self.brushsides.append({"plane": s[0], "texinfo": s[1], "dispinfo": s[2], "bevel": bool(s[3])})
        no = L(LUMP_NODES)
        self.nodes = [struct.unpack_from("<3i", no, i * 32) for i in range(len(no) // 32)]
        le = L(LUMP_LEAFS)
        stride = 56 if self.lump_version(LUMP_LEAFS) == 0 and self.version < 20 else 32
        if len(le) % stride:
            stride = 56 if len(le) % 32 else 32
        self.leafs = []
        for i in range(len(le) // stride):
            contents, _cluster, area_flags = struct.unpack_from("<ihh", le, i * stride)
            ff, nf, fb, nb = struct.unpack_from("<4H", le, i * stride + 20)
            self.leafs.append({"contents": contents, "area": area_flags & 0x1FF, "faces": (ff, nf), "brushes": (fb, nb)})
        lf = L(LUMP_LEAFFACES)
        self.leaffaces = struct.unpack_from(f"<{len(lf) // 2}H", lf)
        lb = L(LUMP_LEAFBRUSHES)
        self.leafbrushes = struct.unpack_from(f"<{len(lb) // 2}H", lb)
        self.entities = kv.parse_entities(L(LUMP_ENTITIES).decode("latin-1", "replace"))
        self.pakfile = L(LUMP_PAKFILE)
        self.static_props = self._static_props()

    # ---- static props ('sprp' game lump) -------------------------------------------------------------------------
    def _static_props(self):
        gl_ofs = self.lumps[LUMP_GAME_LUMP][0]
        gl = self.lump(LUMP_GAME_LUMP)
        if not gl:
            return []
        n = struct.unpack_from("<i", gl, 0)[0]
        for j in range(n):
            gid, flags, gver, gofs, glen = struct.unpack_from("<iHHii", gl, 4 + 16 * j)
            if gid != 0x73707270:  # 'sprp'
                continue
            d = self.data[gofs : gofs + glen]  # game lump offsets are file offsets
            if flags & 1:
                d = _lzma(d)
            nd = struct.unpack_from("<i", d, 0)[0]
            names = [
                d[4 + 128 * k : 4 + 128 * (k + 1)].split(b"\0")[0].decode("latin-1").replace("\\", "/").lower() for k in range(nd)
            ]
            at = 4 + 128 * nd
            nl = struct.unpack_from("<i", d, at)[0]
            at += 4 + 2 * nl
            count = struct.unpack_from("<i", d, at)[0]
            at += 4
            if not count:
                return []
            stride = (len(d) - at) // count
            out = []
            for k in range(count):
                b = at + k * stride
                ox, oy, oz, p, y, r = struct.unpack_from("<6f", d, b)
                ptype, _fl, _lc, solid, pflags, skin = struct.unpack_from("<HHHBBi", d, b + 24)
                scale = 1.0
                if gver >= 11 and stride >= 80:
                    scale = struct.unpack_from("<f", d, b + stride - 4)[0] or 1.0
                out.append(
                    {
                        "model": names[ptype] if ptype < nd else "",
                        "origin": (ox, oy, oz),
                        "angles": (p, y, r),
                        "solid": solid,
                        "skin": skin,
                        "scale": scale,
                        "flags": pflags,
                    }
                )
            return out
        del gl_ofs
        return []

    # ---- geometry helpers ------------------------------------------------------------------------------------------
    def face_verts(self, f):
        out = []
        for k in range(f["numedges"]):
            e = self.surfedges[f["firstedge"] + k]
            out.append(self.vertexes[self.edges[e][0]] if e >= 0 else self.vertexes[self.edges[-e][1]])
        return out

    def leaf_of(self, p):
        """The leaf holding point `p` (Source inches), walking the BSP from model 0's head node."""
        node = self.models[0]["headnode"]
        while node >= 0:
            plane, c0, c1 = self.nodes[node]
            nx, ny, nz, dist, _t = self.planes[plane]
            node = c0 if nx * p[0] + ny * p[1] + nz * p[2] - dist >= 0 else c1
        return -1 - node

    def lightmap_samples(self, f):
        """The face's style-0 luxels as ColorRGBExp32 bytes ((w+1)*(h+1)*4), or None when it has none."""
        if f["lightofs"] < 0 or f["styles"][0] == 255:
            return None
        w, h = f["lsize"][0] + 1, f["lsize"][1] + 1
        n = w * h * 4
        data = self.lighting[f["lightofs"] : f["lightofs"] + n]
        return data if len(data) == n else None


def crc_name(s):
    return f"{zlib.crc32(s.encode()) & 0xFFFFFFFF:08x}"
