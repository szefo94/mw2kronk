"""KeyValues text: VMT materials and the BSP entity lump.

A VMT is `Shader { "$key" "value" ... }` with nested blocks (`Proxies`, `"GPU>=2?"`, `>=dx90`...), keys in any
case, quotes optional, `//` comments. The entity lump is a run of flat `{ "key" "value" ... }` blocks.
"""

import re

_TOKEN = re.compile(r'"([^"]*)"|(\{)|(\})|//[^\n]*|([^\s{}"]+)')


def _tokens(text):
    for m in _TOKEN.finditer(text):
        if m.group(1) is not None:
            yield ("s", m.group(1))
        elif m.group(2):
            yield ("{", None)
        elif m.group(3):
            yield ("}", None)
        elif m.group(4) is not None:
            yield ("s", m.group(4))


def parse_vmt(text):
    """(shader name lower case, {key lower case: value}) with nested blocks flattened, the outer ones first.

    Conditional blocks (`"GPU>=2?$x"` keys, `>=dx90` blocks) are read as if they hold: the PC game at full
    settings takes them. Values of later keys win, so a `>=dx90` override replaces the base key.
    """
    toks = list(_tokens(text))
    if not toks:
        return None, {}
    shader = toks[0][1].lower() if toks[0][0] == "s" else ""
    out, depth, key, i = {}, 0, None, 1
    while i < len(toks):
        kind, val = toks[i]
        if kind == "{":
            depth += 1
            key = None
        elif kind == "}":
            depth -= 1
            key = None
        elif key is None:
            key = val
        else:
            k = key.lower()
            if "?" in k:  # "GPU>=2?$detail" style conditional keys
                k = k.split("?", 1)[1]
            out[k] = val
            key = None
        i += 1
        # a key followed by a block is the block's name, not a key
        if key is not None and i < len(toks) and toks[i][0] == "{":
            key = None
    return shader, out


def parse_entities(text):
    """The entity lump as a list of {key: value} dicts (keys lower case; a repeated key keeps its first value). Every output
    (`OnTrigger` "target,input,param,delay,times", the fields split by ESC on CS:GO) is also in `_outputs` as (key, value), so a
    repeated one is kept too."""
    ents, cur, key = [], None, None
    for kind, val in _tokens(text):
        if kind == "{":
            cur, key = {}, None
        elif kind == "}":
            if cur is not None:
                ents.append(cur)
            cur, key = None, None
        elif cur is not None:
            if key is None:
                key = val.lower()
            else:
                cur.setdefault(key, val)
                if key.startswith("on") and ("," in val or "\x1b" in val):
                    cur.setdefault("_outputs", []).append((key, val))
                key = None
    return ents


def vec(s, n=3, default=0.0):
    parts = (s or "").split()
    out = []
    for i in range(n):
        try:
            out.append(float(parts[i]))
        except (IndexError, ValueError):
            out.append(default)
    return out
