#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diff verbatim code in `01-视频讲解` against the frame that block cites.

v1 ("is the line anywhere on screen?") produced 30-40% noise because keyframes are
sparse and Mermaid diagrams are not code. v2 ("closest OCR line") still matched
lines from other frames. v3 anchors each code block to the timestamp written right
before it (`视频 07:35`, `（03:35 帧）`), OCRs that frame, and compares only within it:

    identical skeleton              -> ok
    same line, different characters -> report (this is what a mis-read looks like)
    line absent from that frame     -> not reported (it was not on screen)

Two noise classes that are not code are dropped on both sides before comparing:
IDE gutter line numbers (`11  int main(void)`) and parameter-name hints
(`Size:`, `Timeout:`, `msg_prio:`). OCR-side O/0 and l/1 confusion is *not*
normalised -- that is exactly the class worth surfacing.

  python check_code_ocr.py --vault "<vault>" --manifest work/manifest.json \
      [--work-root <dir>] [--lesson 15-3] [--out report.md]
"""
import argparse
import io
import json
import os
import re
import sys
from difflib import SequenceMatcher

FENCE = re.compile(r"^\s*```(\w*)")
STAMP = re.compile(r"(\d{1,2}):(\d{2})")
SKEL = re.compile(r"[^0-9a-z]+", re.I)
COMMENT = re.compile(r"//.*$|/\*.*?\*/")
GUTTER = re.compile(r"^\s*\d{1,4}\s{2,}")
HINT = re.compile(r"\b(?:Size|Timeout|msg_prio|timeout|prio):\s*")
SKIP_LANGS = {"text", "bash", "sh", "powershell", "console", "mermaid", "yaml", "toml", "json"}
SUPPLEMENT = ("笔记补充", "补充", "未经编译验证", "自拟", "视频未写", "视频没写", "非画面", "改写")

_ENGINE = None
_TEXT_CACHE = {}


def skeleton(s):
    """Letters/digits only; drops comments, gutter numbers and IDE param hints."""
    s = GUTTER.sub("", s)
    s = HINT.sub("", s)
    s = COMMENT.sub("", s)
    return SKEL.sub("", s.lower())


def same_line(a, b):
    """True when one skeleton is a prefix/contained run of the other."""
    if not a or not b:
        return False
    lo, hi = (a, b) if len(a) <= len(b) else (b, a)
    return len(lo) >= 0.85 * len(hi) and lo in hi


def ocr_text(path):
    global _ENGINE
    if path in _TEXT_CACHE:
        return _TEXT_CACHE[path]
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _ENGINE = RapidOCR()
    res, _ = _ENGINE(path)
    _TEXT_CACHE[path] = [t.strip() for _, t, c in (res or []) if c and c > 0.5 and t.strip()]
    return _TEXT_CACHE[path]


def blocks(md_path):
    """[(anchor_seconds|None, lineno, text, skeleton)] for verbatim code lines."""
    out, fence, skip, prev, anchor = [], None, False, [], None
    for i, line in enumerate(io.open(md_path, encoding="utf-8", errors="replace").read().split("\n"), 1):
        m = FENCE.match(line)
        if m:
            if fence is None:
                skip = any(k in "".join(prev[-2:]) for k in SUPPLEMENT)
                fence = m.group(1) or "text"
            else:
                fence, skip = None, False
            prev.append(line)
            continue
        if fence is None and ("视频" in line or "帧" in line):
            stamp = STAMP.search(line)
            if stamp:
                anchor = int(stamp.group(1)) * 60 + int(stamp.group(2))
        prev.append(line)
        if not fence or skip or fence in SKIP_LANGS:
            continue
        if line.lstrip().startswith(("//", "#", "/*", "*", "--")):
            continue
        sk = skeleton(line)
        if len(sk) >= 8 and sk not in {"break", "return", "continue", "else"}:
            out.append((anchor, i, line.strip(), sk))
    return out


def closest(sk, pool):
    best, raw = 0.0, ""
    for r, osk in pool:
        if abs(len(osk) - len(sk)) > max(10, len(sk)):
            continue
        ratio = SequenceMatcher(None, sk, osk).ratio()
        if ratio > best:
            best, raw = ratio, r
    return best, raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--work-root", default="")
    ap.add_argument("--lesson", default="")
    ap.add_argument("--near", type=float, default=0.90)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    rows = json.loads(io.open(a.manifest, encoding="utf-8").read())
    st = {"lessons": 0, "anchored": 0, "lines": 0, "exact": 0, "near": 0}
    report = []
    for r in rows:
        if a.lesson and r["slug"] != a.lesson:
            continue
        lp = os.path.join(a.vault, *r["lesson_path"].split("/"))
        mds = [f for f in sorted(os.listdir(lp)) if f.startswith("01-") and f.endswith(".md")] \
            if os.path.isdir(lp) else []
        if not mds:
            continue
        w = (r.get("work") or "").strip()
        wd = w if os.path.isabs(w) else (os.path.join(a.work_root, w) if a.work_root and w else "")
        kdir = os.path.join(wd, "keyframes") if wd else ""
        if not kdir or not os.path.isdir(kdir):
            continue
        frames = {}
        for f in os.listdir(kdir):
            mm = re.fullmatch(r"t(\d+)\.png", f)
            if mm:
                frames[int(mm.group(1))] = os.path.join(kdir, f)
        st["lessons"] += 1
        hits = []
        for mf in mds:
            for anchor, ln, code, sk in blocks(os.path.join(lp, mf)):
                st["lines"] += 1
                if anchor is not None and anchor in frames:
                    pool = [(t, skeleton(t)) for t in ocr_text(frames[anchor])]
                    st["anchored"] += 1
                else:
                    pool = [(t, skeleton(t)) for p in frames.values() for t in ocr_text(p)]
                pool = [(t, o) for t, o in pool if len(o) >= 8]
                if not pool:
                    continue
                sim, ocr = closest(sk, pool)
                if sim >= 0.995 or same_line(sk, skeleton(ocr)):
                    st["exact"] += 1
                elif sim >= a.near:
                    st["near"] += 1
                    hits.append((mf, ln, code, ocr, sim, anchor))
        if hits:
            report.append("## %s · %s" % (r["slug"], ", ".join(mds)))
            for mf, ln, code, ocr, sim, anchor in hits:
                where = "%d:%02d" % (anchor // 60, anchor % 60) if anchor is not None else "未锚定"
                report.append("- `%s:%d`（%s，相似度 %.2f）\n  - 笔记：`%s`\n  - OCR ：`%s`"
                              % (mf, ln, where, sim, code[:150], ocr[:150]))
            report.append("")

    head = ("lessons=%d anchored_lines=%d verbatim_lines=%d exact=%d near_miss=%d"
            % (st["lessons"], st["anchored"], st["lines"], st["exact"], st["near"]))
    print(head)
    body = ("# OCR 逐行比对（代码块锚定到它引用的那一帧）\n\n" + head +
            "\n\n只列「同一行但字符不一致」；该帧上根本没有的代码不计。命中项仍需人工看帧确认"
            "（常见噪声：OCR 把 `GPIO` 认成 `GPI0`、`On` 认成 `0n`、光标 `↓`）。\n\n" +
            "\n".join(report))
    if a.out:
        io.open(a.out, "w", encoding="utf-8").write(body)
        print("written", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
