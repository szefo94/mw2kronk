"""A Source game's file system: the map's own pakfile first, then the game's VPKs, then loose files.

`GAMES` names where each supported game keeps its archives (`source.config.json` beside `cod.config.json`
can point elsewhere). CS:S here is the dedicated server's depot (app 232330), which carries every
`cstrike_pak` texture but only the index of HL2's shared textures; a texture missing there is looked up in
CS:GO's `pak01` (the same Valve paths, where CS:GO kept them) before it is given up.
"""

import io
import json
import os
import zipfile

from . import kv
from .vpk import Vpk

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))

GAMES = {
    "css": {
        "name": "Counter-Strike: Source",
        "root": "~/Games/css-ds",
        "vpks": ["cstrike/cstrike_pak_dir.vpk", "hl2/hl2_textures_dir.vpk", "hl2/hl2_misc_dir.vpk"],
        "loose": ["cstrike", "hl2"],
        "maps": "cstrike/maps",
        "fallback": "csgo",
    },
    "csgo": {
        "name": "Counter-Strike: Global Offensive",
        "root": "~/Games/csgo",
        "vpks": ["csgo/pak01_dir.vpk", "platform/platform_pak01_dir.vpk"],
        "loose": ["csgo"],
        "maps": "csgo/maps",
    },
}


def game_config(game):
    cfg = dict(GAMES[game])
    path = os.path.join(_ROOT, "source.config.json")
    if os.path.exists(path):
        cfg.update(json.load(open(path)).get(game, {}))
    cfg["root"] = os.path.expanduser(cfg["root"])
    return cfg


class GameFS:
    def __init__(self, game, pakfile=None, with_fallback=True):
        self.game = game
        self.cfg = game_config(game)
        self.mounts = []
        if pakfile:
            self.mounts.append(("pakfile", zipfile.ZipFile(io.BytesIO(pakfile))))
        for rel in self.cfg["vpks"]:
            p = os.path.join(self.cfg["root"], rel)
            if os.path.exists(p):
                self.mounts.append(("vpk", Vpk(p)))
        for rel in self.cfg["loose"]:
            self.mounts.append(("dir", os.path.join(self.cfg["root"], rel)))
        self.fallback = None
        fb = self.cfg.get("fallback")
        if with_fallback and fb and os.path.exists(os.path.expanduser(game_config(fb)["root"])):
            self.fallback = GameFS(fb, with_fallback=False)
        self._pak_names = None
        self.missing = set()
        self.fell_back = set()
        self.wanted = set()  # every path asked for, to find which VPK chunks a map needs

    def _pak_lookup(self, z, path):
        if self._pak_names is None:
            self._pak_names = {n.lower(): n for n in z.namelist()}
        return self._pak_names.get(path)

    def read(self, path, fallback=True):
        path = path.replace("\\", "/").lower().lstrip("/")
        self.wanted.add(path)
        for kind, m in self.mounts:
            if kind == "pakfile":
                name = self._pak_lookup(m, path)
                if name:
                    return m.read(name)
            elif kind == "vpk":
                if path in m:
                    data = m.read(path)
                    if data is not None:
                        return data
            else:
                p = os.path.join(m, path)
                if os.path.exists(p):
                    return open(p, "rb").read()
        if fallback and self.fallback:
            data = self.fallback.read(path, fallback=False)
            if data is not None:
                self.fell_back.add(path)
                return data
        self.missing.add(path)
        return None

    def material(self, name, _depth=0):
        """(shader, keys) of materials/<name>.vmt with `patch` includes applied, or (None, {})."""
        name = name.replace("\\", "/").lower().removeprefix("materials/").removesuffix(".vmt")
        data = self.read(f"materials/{name}.vmt")
        if data is None:
            return None, {}
        shader, keys = kv.parse_vmt(data.decode("latin-1"))
        if shader == "patch" and _depth < 4 and "include" in keys:
            base_shader, base = self.material(keys.pop("include"), _depth + 1)
            base.update(keys)
            return base_shader, base
        return shader, keys
