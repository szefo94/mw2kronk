"""The IW3 dump backend: the dump tree for CoD4, from a zonecode walk.

    backend = Backend(search)             # `search`: the `;`-joined .iwd archives, or None
    backend.dump(ff, out, "clipmap")       # writes the clipmap dump under `out`

The walk-once-then-write shape is IW4's (scripts/cod/zonecode/backend.py). CoD4's PC
zones are `IWffu100` version 5, unsigned, with zlib straight after the 12-byte header
(fastfile.py); the Aspyr macOS build ships the same zones.
"""

from ..zone.spec import IW3
from ..zonecode.backend import Backend as WalkBackend
from . import dumpers


class Backend(WalkBackend):
    name = "iw3"
    spec = IW3
    dumpers = dumpers
    exe = ("IW3_EXE", "cod4")  # iw3mp.exe
