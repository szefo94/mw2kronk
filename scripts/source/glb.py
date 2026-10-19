"""A small GLB builder for the Source exports: meshes of indexed triangles with per-material primitives,
embedded JPEG/PNG textures, positions in engine metres. Written with `scripts.cod.gltf`'s packers."""

import os
import zlib

from ..cod.gltf import ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER, add_view, pack, write_glb

REPEAT, LINEAR, LINEAR_MIPMAP_LINEAR = 10497, 9729, 9987


class Glb:
    """`textures`: (folder, URI prefix) to write each image once into a shared folder and reference it, rather than embed it
    (the props of one map share most of their textures); None embeds."""

    def __init__(self, textures=None):
        self.textures_at = textures
        self.buf = bytearray()
        self.gltf = {
            "asset": {"version": "2.0", "generator": "mw2kronk scripts.source"},
            "scene": 0,
            "scenes": [{"nodes": []}],
            "nodes": [],
            "meshes": [],
            "accessors": [],
            "bufferViews": [],
            "buffers": [],
        }
        self._mats = {}
        self._images = {}

    def _accessor(self, typecode, values, comp, kind, count, target, minmax=False):
        view = add_view(self.buf, self.gltf["bufferViews"], pack(typecode, values), target)
        acc = {"bufferView": view, "componentType": comp, "count": count, "type": kind}
        if minmax:
            n = {"VEC3": 3, "VEC2": 2}[kind]
            acc["min"] = [min(values[i::n]) for i in range(n)]
            acc["max"] = [max(values[i::n]) for i in range(n)]
        self.gltf["accessors"].append(acc)
        return len(self.gltf["accessors"]) - 1

    def texture(self, key, data, mime):
        if key in self._images:
            return self._images[key]
        g = self.gltf
        g.setdefault("images", [])
        g.setdefault("textures", [])
        g.setdefault("samplers", [{"wrapS": REPEAT, "wrapT": REPEAT, "magFilter": LINEAR, "minFilter": LINEAR_MIPMAP_LINEAR}])
        if self.textures_at:
            folder, prefix = self.textures_at
            name = f"{zlib.crc32(repr(key).encode()) & 0xFFFFFFFF:08x}.{'png' if mime == 'image/png' else 'jpg'}"
            path = os.path.join(folder, name)
            if not os.path.exists(path):
                with open(path, "wb") as fh:
                    fh.write(data)
            g["images"].append({"uri": prefix + name, "mimeType": mime})
        else:
            view = add_view(self.buf, g["bufferViews"], data)
            g["images"].append({"bufferView": view, "mimeType": mime})
        g["textures"].append({"sampler": 0, "source": len(g["images"]) - 1})
        self._images[key] = len(g["textures"]) - 1
        return self._images[key]

    def material(self, name, texture=None, alpha=None, color=None, double=False, unlit=False):
        if name in self._mats:
            return self._mats[name]
        pbr = {"metallicFactor": 0.0, "roughnessFactor": 1.0}
        if texture is not None:
            pbr["baseColorTexture"] = {"index": texture}
        if color is not None:
            pbr["baseColorFactor"] = list(color)
        m = {"name": name, "pbrMetallicRoughness": pbr}
        if alpha == "MASK":
            m["alphaMode"], m["alphaCutoff"] = "MASK", 0.5
        elif alpha == "BLEND":
            m["alphaMode"] = "BLEND"
        if double:
            m["doubleSided"] = True
        if unlit:
            m.setdefault("extensions", {})["KHR_materials_unlit"] = {}
            self.gltf.setdefault("extensionsUsed", [])
            if "KHR_materials_unlit" not in self.gltf["extensionsUsed"]:
                self.gltf["extensionsUsed"].append("KHR_materials_unlit")
        self.gltf.setdefault("materials", []).append(m)
        self._mats[name] = len(self.gltf["materials"]) - 1
        return self._mats[name]

    def mesh(self, name, prims):
        """`prims`: [(material index or None, positions, normals or None, uv0 or None, uv1 or None, indices)], flat lists."""
        out = []
        for mat, pos, nor, uv0, uv1, idx in prims:
            if not idx:
                continue
            n = len(pos) // 3
            attrs = {"POSITION": self._accessor("f", pos, 5126, "VEC3", n, ARRAY_BUFFER, minmax=True)}
            if nor:
                attrs["NORMAL"] = self._accessor("f", nor, 5126, "VEC3", n, ARRAY_BUFFER)
            if uv0:
                attrs["TEXCOORD_0"] = self._accessor("f", uv0, 5126, "VEC2", n, ARRAY_BUFFER)
            if uv1:
                attrs["TEXCOORD_1"] = self._accessor("f", uv1, 5126, "VEC2", n, ARRAY_BUFFER)
            big = n > 65535
            prim = {
                "attributes": attrs,
                "indices": self._accessor(
                    "I" if big else "H", idx, 5125 if big else 5123, "SCALAR", len(idx), ELEMENT_ARRAY_BUFFER
                ),
            }
            if mat is not None:
                prim["material"] = mat
            out.append(prim)
        if not out:
            return None
        self.gltf["meshes"].append({"name": name, "primitives": out})
        self.gltf["nodes"].append({"name": name, "mesh": len(self.gltf["meshes"]) - 1})
        self.gltf["scenes"][0]["nodes"].append(len(self.gltf["nodes"]) - 1)
        return len(self.gltf["nodes"]) - 1

    def write(self, path):
        if self.buf:
            self.gltf["buffers"] = [{"byteLength": len(self.buf) + (-len(self.buf) % 4)}]
        else:
            self.gltf.pop("buffers")
        return write_glb(path, self.gltf, self.buf)
