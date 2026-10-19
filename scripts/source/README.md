# scripts/source: Counter-Strike (Source engine) maps as world folders

The second importer beside `scripts/cod`: a Source BSP (VBSP 19-21, so Counter-Strike: Source and CS:GO) with its game's
VPKs becomes a `worlds/<map>-<game>/` folder in the layout `scripts/cod export` writes, which Vibe on Duty 2 loads as a
`game: css|csgo` world (its `src/v2/world.js`, `docs/CS-MAPS.md` §6).

```
.venv/bin/python -m scripts.source export css de_dust2 --out <worlds> --label "Dust II"
.venv/bin/python -m scripts.source chunks csgo de_mirage --out <scratch>   # VPK chunk files the map needs that are not on disk
```

`GLTFPACK=<gltfpack's cli.js>` adds meshopt compression of world.glb and the props. Where each game's files are: `fs.py`
`GAMES` (`~/Games/css-ds`, the CS:S dedicated server depot 232330; `~/Games/csgo`, CS:GO's content depot 731), overridable
in `source.config.json`; both download anonymously with DepotDownloader.

| Module | Reads |
|---|---|
| `bsp.py` | the lumps: faces, displacements, brushes, models, leaves and areas, texinfo/texdata, lighting, entities, static props, pakfile |
| `vpk.py` | VPK v1/v2 indexes and chunks |
| `fs.py` | the map's pakfile, then the game's VPKs, then loose files, then the fallback game (CS:S -> CS:GO for HL2's textures); VMTs with `patch` |
| `vtf.py` | VTF 7.0-7.5 to a Pillow image (DXT1/3/5 through Pillow's BCn decoder) |
| `mdl.py` | MDL v44-49 + VVD + VTX: a prop's LOD 0 per material; bones' model-space transforms |
| `phy.py` | a prop's `.phy` collision pieces (IVP compact surfaces, terminal ledges) |
| `kv.py` | KeyValues: VMTs and the entity lump |
| `glb.py` | the GLB writer (shared textures in `assets/textures`) |
| `export.py` | the world folder; its docstring lists every file it writes |
