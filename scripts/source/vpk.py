"""Valve's VPK archives (versions 1 and 2): a `_dir.vpk` index and its numbered `_NNN.vpk` chunks.

The index is a tree of extension -> directory -> file name, each entry naming the chunk its bytes are in
(0x7fff: the index file itself, after the tree), the offset there and the length, plus a few preload bytes
kept in the index. Paths are lower case with forward slashes; a lone space stands for "none".
"""

import os
import struct

_MAGIC = 0x55AA1234
_IN_DIR = 0x7FFF


def _cstr(data, at):
    end = data.index(b"\0", at)
    return data[at:end].decode("latin-1"), end + 1


class Vpk:
    def __init__(self, dir_path):
        self.dir_path = dir_path
        self.base = dir_path[: -len("_dir.vpk")]
        with open(dir_path, "rb") as fh:
            data = fh.read()
        magic, version, tree_size = struct.unpack_from("<III", data, 0)
        if magic != _MAGIC:
            raise ValueError(f"{dir_path}: not a VPK")
        header = 12 if version == 1 else 28
        self.data_start = header + tree_size
        self.entries = {}  # path -> (archive, offset, length, preload bytes)
        at = header
        while True:
            ext, at = _cstr(data, at)
            if not ext:
                break
            while True:
                folder, at = _cstr(data, at)
                if not folder:
                    break
                while True:
                    name, at = _cstr(data, at)
                    if not name:
                        break
                    _crc, preload, archive, offset, length, _term = struct.unpack_from("<IHHIIH", data, at)
                    at += 18
                    pre = data[at : at + preload]
                    at += preload
                    path = (f"{folder}/" if folder != " " else "") + name + (f".{ext}" if ext != " " else "")
                    self.entries[path.lower()] = (archive, offset, length, pre)
        self._dir_bytes = data
        self._files = {}

    def __contains__(self, path):
        return path.lower() in self.entries

    def chunk_path(self, archive):
        return f"{self.base}_{archive:03d}.vpk"

    def chunks_of(self, paths):
        """The chunk files the given entries are stored in."""
        out = set()
        for p in paths:
            e = self.entries.get(p.lower())
            if e and e[0] != _IN_DIR and e[2]:
                out.add(os.path.basename(self.chunk_path(e[0])))
        return out

    def read(self, path):
        e = self.entries.get(path.lower())
        if e is None:
            return None
        archive, offset, length, pre = e
        if not length:
            return pre
        if archive == _IN_DIR:
            return pre + self._dir_bytes[self.data_start + offset : self.data_start + offset + length]
        fname = self.chunk_path(archive)
        if not os.path.exists(fname):
            return None  # a chunk this install lacks (the CS:S server's HL2 textures)
        fh = self._files.get(archive)
        if fh is None:
            fh = self._files[archive] = open(fname, "rb")
        fh.seek(offset)
        return pre + fh.read(length)
