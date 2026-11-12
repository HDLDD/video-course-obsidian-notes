#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit `file.c:123` style citations in a deep-mode note library.

Depth notes promise "every claim has a line number", so a citation pointing past
EOF or at a file that does not exist locally is worse than no citation. This
checks existence and range against the real source trees, and separately accepts
a line number read off the video screen **only when the note says so** nearby.

  python audit_citations.py --vault "<vault>/<course>" \
      --root "A:/path/FreeRTOS-Kernel" --root "D:/esp/v5.5/esp-idf/components/freertos"

Exit code 1 lists the offenders: out-of-range line numbers, unknown files that
are not labelled as screen reads, and screen reads with no label at all.
"""
import argparse
import io
import os
import re
import sys
from collections import Counter

CITE = re.compile(r"`([A-Za-z0-9_./+-]+\.(?:c|h|cpp|hpp|md|py|S)):(\d{1,5})(?:\s*[–\-~]\s*(\d{1,5}))?`")
# words that mark "this number came from the video, not from a file I opened"
LABELS = ("画面", "帧", "视频", "口述", "口播", "待核对", "本机", "无源码", "截图", "生成", "推导", "IDF", "上游", "V1")


def build_index(roots):
    index = {}
    for root in roots:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in {".git", "build", "test", "tests"}]
            for f in files:
                if f.endswith((".c", ".h", ".cpp", ".hpp", ".md", ".py", ".S")):
                    index.setdefault(f, []).append(os.path.join(dirpath, f))
    return index


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True)
    ap.add_argument("--root", action="append", required=True,
                    help="source tree to resolve citations against (repeatable)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    index = build_index(a.root)
    cache = {}

    def nlines(path):
        if path not in cache:
            with io.open(path, encoding="utf-8", errors="replace") as fh:
                cache[path] = sum(1 for _ in fh)
        return cache[path]

    total, bad = Counter(), []
    for dirpath, dirs, files in os.walk(a.vault):
        for f in sorted(files):
            if not f.endswith(".md"):
                continue
            p = os.path.join(dirpath, f)
            rel = os.path.relpath(p, a.vault)
            text = io.open(p, encoding="utf-8", errors="replace").read()
            lines = text.split("\n")
            for m in CITE.finditer(text):
                name, lo = m.group(1), int(m.group(2))
                hi = int(m.group(3)) if m.group(3) else lo
                total["citations"] += 1
                cands = [c for c in index.get(os.path.basename(name), [])
                         if c.replace("\\", "/").endswith("/" + name)]
                covered = [c for c in cands if nlines(c) >= hi]
                line = text[:m.start()].count("\n")
                ctx = "\n".join(lines[max(0, line - 3):line + 3])
                labelled = any(k in ctx for k in LABELS)
                # A bare `main.c:234` cannot be assumed to mean some same-named file
                # in the local trees -- the video's own project files share those names.
                if covered:
                    total["resolved"] += 1
                elif "/" in name:
                    # an explicit path can only mean a local file, so a miss is real
                    total["out-of-range" if cands else "unresolved"] += 1
                    bad.append((rel, name, hi, "超出文件行数(max=%d)" % max([nlines(c) for c in cands])
                                if cands else "本机没有该带路径的文件"))
                elif labelled:
                    total["screen-read-labelled"] += 1
                elif cands:
                    total["same-name-unmatched"] += 1
                    bad.append((rel, name, hi,
                                f"本机同名文件最多 {max(nlines(c) for c in cands)} 行；裸文件名且上下文未说明来源"))
                else:
                    total["unresolved"] += 1
                    bad.append((rel, name, lo, "本机没有该文件，且上下文未说明行号来源（画面/帧/待核对）"))

    print(dict(total))
    for rel, name, ln, why in bad[:40]:
        print(f"  BAD {rel} -> {name}:{ln}  {why}")
    if len(bad) > 40:
        print(f"  ...共 {len(bad)} 条")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
