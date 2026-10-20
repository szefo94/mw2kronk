"""scripts.source: the conversions an export rests on, without any game files.

.venv/bin/python -m pytest scripts/source/tests
"""

import math
import random
import struct

from scripts.source import export as X
from scripts.source import kv
from scripts.source.vpk import Vpk


def _rot(axis, a):
    c, s = math.cos(a), math.sin(a)
    return {
        "x": [[1, 0, 0], [0, c, -s], [0, s, c]],
        "y": [[c, 0, s], [0, 1, 0], [-s, 0, c]],
        "z": [[c, -s, 0], [s, c, 0], [0, 0, 1]],
    }[axis]


def _mm(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def test_three_euler_is_the_source_rotation_in_the_engine_frame():
    # three.js's XYZ Euler (Rx Ry Rz) of the result equals C R C^T, C = (x, y, z) -> (x, z, -y)
    rng = random.Random(7)
    idx, sgn = (0, 2, 1), (1, 1, -1)
    for _ in range(500):
        ang = [rng.uniform(-180, 180) for _ in range(3)]
        r = X.angle_matrix(*ang)
        want = [[r[idx[i]][idx[j]] * sgn[i] * sgn[j] for j in range(3)] for i in range(3)]
        e = [math.radians(v) for v in X.three_euler(r)]
        got = _mm(_mm(_rot("x", e[0]), _rot("y", e[1])), _rot("z", e[2]))
        assert max(abs(got[i][j] - want[i][j]) for i in range(3) for j in range(3)) < 1e-4


def test_angle_matrix_yaw_turns_forward_left():
    r = X.angle_matrix(0, 90, 0)
    forward = (r[0][0], r[1][0], r[2][0])
    assert [round(v, 6) for v in forward] == [0, 1, 0]  # yaw 90: +X becomes +Y (Source's left)


def test_parse_vmt_flattens_blocks_and_conditionals():
    shader, keys = kv.parse_vmt(
        '"LightmappedGeneric" { "$basetexture" "a/b" $surfaceprop concrete "GPU>=2?$detail" "d" '
        '">=dx90" { "$basetexture" "a/c" } Proxies { AnimatedTexture { animatedtextureframenumvar "$frame" } } }'
    )
    assert shader == "lightmappedgeneric"
    assert keys["$basetexture"] == "a/c" and keys["$surfaceprop"] == "concrete" and keys["$detail"] == "d"


def test_parse_entities():
    ents = kv.parse_entities(
        '{ "classname" "worldspawn" "skyname" "sky_dust" }\n{ "classname" "info_player_terrorist" "origin" "1 2 3" }'
    )
    assert [e["classname"] for e in ents] == ["worldspawn", "info_player_terrorist"] and ents[1]["origin"] == "1 2 3"


def test_vpk_reads_a_file_kept_in_the_index(tmp_path):
    payload = b"hello vpk"
    tree = b"vmt\0" + b"materials/x\0" + b"y\0" + struct.pack("<IHHIIH", 0, 0, 0x7FFF, 0, len(payload), 0xFFFF) + b"\0\0\0"
    path = tmp_path / "pak_dir.vpk"
    path.write_bytes(struct.pack("<III", 0x55AA1234, 1, len(tree)) + tree + payload)
    v = Vpk(str(path))
    assert "materials/x/y.vmt" in v and v.read("MATERIALS/X/Y.VMT") == payload


class _Cube:
    """A 64-unit cube brush, as bsp.Bsp holds one: planes facing out, no bevels."""

    def __init__(self):
        self.planes = [
            (1, 0, 0, 32, 0),
            (-1, 0, 0, 32, 0),
            (0, 1, 0, 32, 1),
            (0, -1, 0, 32, 1),
            (0, 0, 1, 32, 2),
            (0, 0, -1, 32, 2),
        ]
        self.brushes = [(0, 6, 1)]
        self.brushsides = [{"plane": i, "texinfo": 0, "dispinfo": -1, "bevel": False} for i in range(6)]


def test_brush_polygons_are_the_cube_faces_wound_outward():
    polys = X.brush_polygons(_Cube(), 0)
    assert len(polys) == 6
    for poly, n in polys:
        assert len(poly) == 4 and all(abs(X._dot(p, n) - 32) < 1e-3 for p in poly)
        c = X._cross(X._sub(poly[1], poly[0]), X._sub(poly[2], poly[0]))
        assert X._dot(c, n) > 0  # counter-clockwise seen from outside, as collision.glb writes them


def test_doors_stand_open():
    class B:
        models = [None, {"mins": (-1, 0, -53), "maxs": (1, 45, 53)}]

    ang, org = X._door_open(B, {"classname": "func_door_rotating", "distance": "90", "spawnflags": "0"}, 1, [0, 0, 0], (0, 0, 0))
    assert ang == [0, 90, 0] and org == (0, 0, 0)
    ang, _org = X._door_open(
        B, {"classname": "func_door_rotating", "distance": "90", "spawnflags": "2"}, 1, [0, 10, 0], (0, 0, 0)
    )
    assert ang == [0, -80, 0]
    _ang, org = X._door_open(B, {"classname": "func_door", "movedir": "-90 0 0", "lip": "8"}, 1, [0, 0, 0], (0, 0, 100))
    assert [round(v, 3) for v in org] == [0, 0, 198]  # up by its 106 units less the lip


def test_doors_and_their_parts_stay_out():
    ents = [
        {"classname": "worldspawn"},
        {"classname": "func_door_rotating", "targetname": "door_reactoral", "model": "*47"},
        {"classname": "func_breakable", "parentname": "door_reactoral", "model": "*60", "targetname": "win"},
        {"classname": "prop_dynamic", "parentname": "Door_ReactorAL", "model": "models/props/cs_office/doorknob.mdl"},
        {"classname": "prop_dynamic", "parentname": "win,attach", "model": "models/x.mdl"},  # on the window: out with it
        {"classname": "func_door", "targetname": "slide", "movedir": "0 180 0", "model": "*3"},  # slides sideways: a door
        {"classname": "func_door", "targetname": "lift", "movedir": "-90 0 0", "model": "*4"},  # goes up: a lift, stays
        {"classname": "prop_dynamic", "parentname": "lift", "model": "models/y.mdl"},
        {"classname": "prop_dynamic", "parentname": "emerg_rot1", "model": "models/z.mdl"},
        # Inferno's gate: a trigger kills the door and animates the gate prop; a logic_case animating a car touches no door
        {"classname": "prop_dynamic", "targetname": "gate_top", "model": "models/props/de_inferno/elevatordoor.mdl"},
        {"classname": "prop_dynamic", "targetname": "car01", "model": "models/props/de_nuke/car_nuke_animation.mdl"},
        {
            "classname": "trigger_once",
            "_outputs": [("onstarttouch", "slide,Kill,,1,-1"), ("ontrigger", "gate_top,SetAnimation,open,0,-1")],
        },
        {"classname": "logic_case", "_outputs": [("oncase03", "car01\x1bSetAnimation\x1brun\x1b0\x1b-1")]},
    ]
    assert X._door_parts(ents) == {1, 2, 3, 4, 5, 9}


def test_parse_entities_keeps_every_output():
    ents = kv.parse_entities('{ "classname" "trigger_once" "OnTrigger" "a,Kill,,0,-1" "OnTrigger" "b,SetAnimation,open,0,-1" }')
    assert ents[0]["ontrigger"] == "a,Kill,,0,-1"
    assert ents[0]["_outputs"] == [("ontrigger", "a,Kill,,0,-1"), ("ontrigger", "b,SetAnimation,open,0,-1")]
