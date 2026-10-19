"""Valve Texture Format (VTF 7.0-7.5) -> a Pillow image of the largest mip level, first frame and face.

The high-resolution data runs from the smallest mip to the largest, each level holding every frame, face and
depth slice; 7.3+ files locate it through a resource table (tag 0x30). DXT1/3/5 go through Pillow's BCn
decoder; the uncompressed 8-bit layouts are swizzled into RGBA here.
"""

import struct

from PIL import Image

# format id -> (bytes per 4x4 block or per texel, is block compressed, Pillow raw mode or BCn number)
_FORMATS = {
    0: (4, False, "RGBA"),  # RGBA8888
    1: (4, False, "ABGR"),  # ABGR8888
    2: (3, False, "RGB"),  # RGB888
    3: (3, False, "BGR"),  # BGR888
    4: (2, False, "BGR;16"),  # RGB565 (stored as BGR565 in practice)
    5: (1, False, "L"),  # I8
    6: (2, False, "LA"),  # IA88
    8: (1, False, "A"),  # A8
    11: (4, False, "ARGB"),  # ARGB8888
    12: (4, False, "BGRA"),  # BGRA8888
    13: (8, True, 1),  # DXT1
    14: (16, True, 2),  # DXT3
    15: (16, True, 3),  # DXT5
    16: (4, False, "BGRX"),  # BGRX8888
    20: (8, True, 1),  # DXT1_ONEBITALPHA
    22: (2, False, "UV"),  # UV88 (normal maps; read as two channels)
    24: (8, False, "RGBA16F"),  # RGBA16161616F (HDR cubemaps)
}


def _level_size(fmt, w, h):
    size, block, _ = _FORMATS[fmt]
    if block:
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * size
    return w * h * size


def decode(data):
    """(Pillow RGBA image, has alpha flags) for the VTF bytes, or (None, False) for a format not handled."""
    if data[:4] != b"VTF\0":
        return None, False
    major, minor, header_size = struct.unpack_from("<III", data, 4)
    width, height, flags, frames, _first = struct.unpack_from("<HHIHH", data, 16)
    hi_fmt = struct.unpack_from("<i", data, 52)[0]
    mips = data[56]
    lo_fmt = struct.unpack_from("<i", data, 57)[0]
    lo_w, lo_h = data[61], data[62]
    depth = struct.unpack_from("<H", data, 63)[0] if minor >= 2 else 1
    depth = max(depth, 1)
    if hi_fmt not in _FORMATS:
        return None, False
    faces = 6 if flags & 0x4000 else 1  # TEXTUREFLAGS_ENVMAP
    if faces == 6 and minor < 5 and _first != 0xFFFF:
        faces = 7  # pre-7.5 cubemaps carry a spheremap face (VTFLib's rule)
    start = None
    if minor >= 3:
        count = struct.unpack_from("<I", data, 68)[0]
        for i in range(count):
            tag, rflags, off = struct.unpack_from("<3sBI", data, 80 + 8 * i)
            if tag == b"\x30\x00\x00":
                start = off
    if start is None:
        start = header_size
        if lo_fmt != -1 and lo_fmt in _FORMATS and lo_w:
            start += _level_size(lo_fmt, lo_w, lo_h)
    # skip every level smaller than the largest
    at = start
    for level in range(mips - 1, 0, -1):
        w, h = max(1, width >> level), max(1, height >> level)
        at += _level_size(hi_fmt, w, h) * frames * faces * depth
    payload = data[at : at + _level_size(hi_fmt, width, height)]
    _size, block, mode = _FORMATS[hi_fmt]
    alpha = bool(flags & (0x1000 | 0x2000))  # ONEBITALPHA | EIGHTBITALPHA
    if block:
        img = Image.frombytes("RGBA", (width, height), payload, "bcn", mode)
    elif mode == "RGBA16F":
        vals = struct.unpack_from(f"<{width * height * 4}e", payload)
        px = bytes(min(255, int(max(0.0, v) ** (1 / 2.2) * 255)) for v in vals)
        img = Image.frombytes("RGBA", (width, height), px)
    elif mode == "UV":
        img = Image.frombytes("LA", (width, height), payload).convert("RGBA")
    elif mode == "BGR;16":
        img = Image.frombytes("RGB", (width, height), payload, "raw", "BGR;16").convert("RGBA")
    elif mode == "BGRX":
        img = Image.frombytes("RGB", (width, height), payload, "raw", "BGRX").convert("RGBA")
    elif mode in ("RGBA", "ABGR", "ARGB", "BGRA"):
        img = Image.frombytes("RGBA", (width, height), payload, "raw", mode)
    elif mode in ("RGB", "BGR"):
        img = Image.frombytes("RGB", (width, height), payload, "raw", mode).convert("RGBA")
    elif mode == "A":
        a = Image.frombytes("L", (width, height), payload)
        img = Image.merge("RGBA", (Image.new("L", a.size, 255),) * 3 + (a,))
    else:  # L, LA
        img = Image.frombytes(mode, (width, height), payload).convert("RGBA")
    return img, alpha
