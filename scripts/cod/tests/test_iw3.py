"""CoD4 (IW3): the container, the zone's blocks, IWI v6 images and the loadDef flags, without the game."""

import struct
import zlib

from scripts.cod import fastfile
from scripts.cod.iw5.dumpers import image
from scripts.cod.zone.spec import IW3, SPECS


def test_an_unsigned_version_5_fastfile_is_iw3_with_zlib_after_the_header(tmp_path):
    blocks = [0] * len(IW3.blocks)
    zone = struct.pack("<11I", 8, 0, *blocks) + b"\0" * 8
    path = tmp_path / "mp_test.ff"
    path.write_bytes(b"IWffu100" + struct.pack("<I", 5) + zlib.compress(zone))
    container = fastfile.claim(str(path))
    assert container == fastfile.Container(IW3, False, 12)
    assert bytes(fastfile.inflate(str(path), container)) == zone


def test_iw3_blocks_are_t5s_order_with_vertex_and_index_and_a_four_bit_block_field():
    assert IW3.blocks == (
        "TEMP",
        "RUNTIME",
        "LARGE_RUNTIME",
        "PHYSICAL_RUNTIME",
        "VIRTUAL",
        "LARGE",
        "PHYSICAL",
        "VERTEX",
        "INDEX",
    )
    assert IW3.header_size == 44
    assert IW3.virtual == 4 and IW3.runtime_blocks == {1, 2, 3}
    assert IW3.reference(((8 << 28) | 0x10) + 1) == (8, 0x10)
    assert SPECS["iw3"] is IW3


def test_an_iwi_v6_reads_as_the_v8_file_it_would_be():
    # a 4x4 DXT1 with no mipmaps: one 8-byte block after the 28-byte v6 header
    v6 = struct.pack("<3sBbB3H4I", b"IWi", 6, 11, 0x2, 4, 4, 1, 36, 36, 36, 36) + bytes(8)
    texture = image.from_iwi(v6, None)
    assert not isinstance(texture, str), texture
    assert (texture.fmt, texture.width, texture.height, texture.mips) == ("DXT1", 4, 4, False)
    cube = image.iwi6_as_iwi8(struct.pack("<3sBbB3H4I", b"IWi", 6, 11, 0x4, 4, 4, 1, 0, 0, 0, 0))
    assert image.IWI_HEADER.unpack_from(cube, 0)[2] & image.MAPTYPE == image.CUBE
