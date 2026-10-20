"""`gfxworld` for IW3: IW5's writer over CoD4's flatter GfxWorld.

CoD4 has no `GfxWorldDraw`: the vertex and index arrays and their counts sit on GfxWorld
itself (`vertexCount`, `vd`, `indexCount`, `indices`), and the bounds are `mins` and
`maxs` rather than one `bounds`. Surfaces, static models and brush models have IW4's
member names, so IW5's helpers read them as they are.
"""

from ...iw5.dumpers import gfxworld as iw5
from ...iw5.dumpers.common import vec3
from ...iw6.dumpers.common import asset_path, write_json


def manifest(asset, vertex_stride, game):
    world = asset.record
    data = {"_type": "gfxworld", "_version": 1, "_game": game, "name": asset.name}
    if world["baseName"]:
        data["baseName"] = world["baseName"]
    data.update(
        checksum=world["checksum"],
        bounds={"mins": vec3(world.unpack("<3f", "mins")), "maxs": vec3(world.unpack("<3f", "maxs"))},
        vertexCount=world["vertexCount"],
        vertexStride=vertex_stride,
        vertexFile=f"{asset.name}.gfxworld_vertices.bin",
        indexCount=world["indexCount"],
        indexStride=iw5.INDEX_STRIDE,
        indexFile=f"{asset.name}.gfxworld_indices.bin",
        surfaceCount=world["surfaceCount"],
        staticModelCount=world["dpvs.smodelCount"],
        surfaces=iw5.surfaces(world),
        staticModels=iw5.static_models(world),
        staticSurfaceCount=world["dpvs.staticSurfaceCount"],
        modelCount=world["modelCount"],
        models=iw5.brush_models(world),
    )
    return data


def write(walk, out, ctx=None):
    worlds = walk.by_type("gfxworld")
    if not worlds:
        raise SystemExit(f"{walk.path} holds no GfxWorld")
    for asset in worlds:
        world = asset.record
        vertices, indices = world["vd.vertices"], world["indices"]
        data = manifest(asset, walk.program.struct("GfxWorldVertex").size, walk.game)
        iw5._check_counts(asset.name, data, vertices, indices)
        write_json(asset_path(out, f"{asset.name}.gfxworld.json"), data)
        for filename, array in ((data["vertexFile"], vertices), (data["indexFile"], indices)):
            if array is not None and len(array):
                with open(asset_path(out, filename), "wb") as fh:
                    fh.write(array.bytes())
