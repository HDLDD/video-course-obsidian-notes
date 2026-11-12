#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Make every "视频 mm:ss" in the notes clickable.

A timestamp written as plain text ("视频 07:35") forces the reader to reopen the
video and drag the progress bar. Bilibili accepts a `t=` second offset, and the
manifest already carries the per-lesson URL, so this is a pure formatting pass.

Two shapes are handled:

  ![视频 05:00：说明](assets/x.png)   ->  ... · [回到 05:00](url&t=300)
  正文/表格里的「视频 13:00–15:00」  ->  视频 [13:00–15:00](url&t=780)

Idempotent: a line that already contains a bilibili link is left alone.

  python add_time_links.py --vault "<vault>/<course>" --manifest work/manifest.json [--dry-run]
"""
import argparse
import io
import json
import os
import re
import sys

IMG = re.compile(r"^(!\[视频 (\d{1,2}):(\d{2}))")
PROSE = re.compile(r"(?<![\[（(])视频 (\d{1,2}):(\d{2})(?:\s*[–—\-~]\s*(\d{1,2}):(\d{2}))?")
GUARD = re.compile(r"bilibili\.com/video/")


def secs(h, s):
    return int(h) * 60 + int(s)


def with_t(url, sec):
    return url + ("&t=" if "?" in url else "?t=") + str(sec)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    rows = json.loads(io.open(a.manifest, encoding="utf-8").read())
    files = added = touched = 0
    skipped_no_url = 0
    for r in rows:
        base = r.get("url") or ""
        if not base.startswith("http"):
            skipped_no_url += 1
            continue
        lp = os.path.join(a.vault, *r["lesson_path"].split("/"))
        if not os.path.isdir(lp):
            continue
        for f in sorted(os.listdir(lp)):
            if not f.endswith(".md"):
                continue
            p = os.path.join(lp, f)
            text = io.open(p, encoding="utf-8", newline="").read()
            out, local = [], 0
            fence = None                      # links never belong inside code
            for line in text.split("\n"):
                body = line.rstrip("\r")
                stripped = body.lstrip()
                if stripped.startswith("```") or stripped.startswith("~~~"):
                    fence = None if fence else stripped[:3]
                    out.append(line)
                    continue
                if fence or "视频 " not in body or GUARD.search(body):
                    out.append(line)
                    continue
                m = IMG.match(body)
                if m:
                    h, s = m.group(2), m.group(3)
                    body = body + " · [回到 %s:%s](%s)" % (h, s, with_t(base, secs(h, s)))
                    local += 1
                else:
                    def repl(mm):
                        nonlocal local
                        h, s = mm.group(1), mm.group(2)
                        label = "%s:%s" % (h, s)
                        start = secs(h, s)
                        if mm.group(3):
                            label += "–%s:%s" % (mm.group(3), mm.group(4))
                        local += 1
                        return "视频 [%s](%s)" % (label, with_t(base, start))
                    body = PROSE.sub(repl, body)
                out.append(body + ("\r" if line.endswith("\r") else ""))
            if local:
                files += 1
                touched += local
                added += local
                if not a.dry_run:
                    io.open(p, "w", encoding="utf-8", newline="").write("\n".join(out))
    print("lessons_without_url=%d files_changed=%d links=%d%s"
          % (skipped_no_url, files, added, " (dry-run)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
