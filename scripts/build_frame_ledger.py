#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write a per-lecture `assets/ledger.json`: what each screenshot is, and from where.

Evidence frames are cited all over the notes ("视频 03:35"), but the mapping from
`assets/3-2-03_xxx.png` back to (lesson, second, source frame) only lived in the
author's head. Once `video.mp4` / `keyframes/` are cleaned up, that chain is gone.

The ledger records, per image: sha256 + size (so the vault copy can be proven
intact later), the timestamp parsed from the image caption, and -- when the work
dir still has the extracted frame -- the source path matched by sha256.

  python build_frame_ledger.py --vault "<vault>/<course>" --manifest work/manifest.json \
      [--check] [--dry-run]

`--check` verifies existing ledgers against the files on disk and exits 1 on drift.
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys

IMG = re.compile(r"!\[视频\s*(\d{1,2}):(\d{2})[^\]]*\]\(([^)]+)\)")


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_work(row, work_root):
    w = (row.get("work") or "").strip()
    if not w:
        return None
    if os.path.isabs(w):
        return w
    return os.path.join(work_root, w) if work_root else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--work-root", default="", help="parent of e01/e02… when manifest work is a bare name")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    rows = json.loads(io.open(a.manifest, encoding="utf-8").read())
    tot = {"lessons": 0, "frames": 0, "with_t": 0, "with_source": 0, "drift": 0}
    for r in rows:
        lp = os.path.join(a.vault, *r["lesson_path"].split("/"))
        adir = os.path.join(lp, "assets")
        if not os.path.isdir(adir):
            continue
        imgs = [f for f in sorted(os.listdir(adir))
                if f.lower().endswith((".png", ".jpg", ".jpeg"))]
        if not imgs:
            continue
        tot["lessons"] += 1

        # caption -> (seconds, caption text) per file name
        caps = {}
        for f in os.listdir(lp):
            if not f.endswith(".md"):
                continue
            s = io.open(os.path.join(lp, f), encoding="utf-8", errors="replace").read()
            for m in IMG.finditer(s):
                fn = os.path.basename(m.group(3).replace("\\", "/"))
                caps[fn] = (int(m.group(1)) * 60 + int(m.group(2)), m.group(0)[:180])

        # sha256 -> source frame, when the media is still around
        src_by_hash = {}
        wd = resolve_work(r, a.work_root)
        if wd and os.path.isdir(wd):
            for sub in ("keyframes", "frames"):
                d = os.path.join(wd, sub)
                if os.path.isdir(d):
                    for f in os.listdir(d):
                        p = os.path.join(d, f)
                        if os.path.isfile(p):
                            src_by_hash.setdefault(sha256(p), os.path.relpath(p, wd).replace("\\", "/"))

        entries = []
        for f in imgs:
            p = os.path.join(adir, f)
            digest = sha256(p)
            t, cap = caps.get(f, (None, None))
            entries.append({
                "file": f,
                "sha256": digest,
                "bytes": os.path.getsize(p),
                "t": t,
                "caption": cap,
                "source": src_by_hash.get(digest),
            })
            tot["frames"] += 1
            tot["with_t"] += t is not None
            tot["with_source"] += entries[-1]["source"] is not None

        lpath = os.path.join(lp, "frames.json")
        stale = os.path.join(adir, "ledger.json")
        if os.path.isfile(stale) and not a.dry_run:
            os.remove(stale)
        if a.check:
            if not os.path.isfile(lpath):
                print("MISSING", r["slug"], "frames.json")
                tot["drift"] += 1
                continue
            old = json.loads(io.open(lpath, encoding="utf-8").read())
            old_h = {e["file"]: e["sha256"] for e in old.get("frames", [])}
            for e in entries:
                if old_h.get(e["file"]) != e["sha256"]:
                    print("DRIFT", r["slug"], e["file"])
                    tot["drift"] += 1
            continue

        doc = {
            "lesson": {"slug": r["slug"], "part": r.get("part"), "title": r.get("lesson_title"),
                       "url": r.get("url")},
            "note": "captions are the video timestamps quoted in the notes; source is the "
                    "extracted frame matched by sha256 (null once the media is cleaned up)",
            "frames": entries,
        }
        if not a.dry_run:
            io.open(lpath, "w", encoding="utf-8", newline="\n").write(
                json.dumps(doc, ensure_ascii=False, indent=2) + "\n")

    mode = "check" if a.check else ("dry-run" if a.dry_run else "write")
    print("%s lessons=%d frames=%d with_t=%d with_source=%d drift=%d"
          % (mode, tot["lessons"], tot["frames"], tot["with_t"], tot["with_source"], tot["drift"]))
    return 1 if (a.check and tot["drift"]) else 0


if __name__ == "__main__":
    sys.exit(main())
