"""python -m scripts.source export <css|csgo> <map> [--out DIR] [--slug SLUG] [--label "Name"]
python -m scripts.source chunks <css|csgo> <map>   # the VPK chunk files a map's materials and models are stored in

See scripts/source/export.py for what an export writes."""

import argparse
import os

from .export import export


def main():
    ap = argparse.ArgumentParser(prog="python -m scripts.source")
    ap.add_argument("command", choices=["export", "chunks"])
    ap.add_argument("game", choices=["css", "csgo"])
    ap.add_argument("map")
    ap.add_argument(
        "--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "worlds")
    )
    ap.add_argument("--slug")
    ap.add_argument("--label")
    a = ap.parse_args()
    r = export(a.game, a.map, a.out, a.slug, a.label)
    if a.command == "chunks":
        fs = r["fs"]
        for mounts in (fs.mounts, fs.fallback.mounts if fs.fallback else []):
            for kind, m in mounts:
                if kind == "vpk":
                    need = sorted(
                        c for c in m.chunks_of(fs.wanted) if not os.path.exists(os.path.join(os.path.dirname(m.dir_path), c))
                    )
                    if need:
                        print(
                            os.path.relpath(m.dir_path, fs.cfg["root"] if mounts is fs.mounts else fs.fallback.cfg["root"]),
                            len(need),
                        )
                        print("\n".join(need))


if __name__ == "__main__":
    main()
