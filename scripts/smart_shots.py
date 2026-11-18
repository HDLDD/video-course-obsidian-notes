#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Content-aware keyframe picker ("smart shots") for lecture videos.

Why not a fixed 5s/15s grid: a grid lands wherever the clock says, so it catches
animations mid-flight and misses short-lived complete states (a finished BFS
highlight, a fully-built table, the last slide build before a page turn).
On BV1zv4w63EJs (9 min, 3D data-structure visualisations) 11 of 47 picked states
did not exist anywhere within +/-5s of the old grid.

Method (validated on 7 videos of 6 types, see
A:\doing anything\shot_timing\report.md and A:\doing anything\shot_types\report.md):
  1. decode at --fps (default 2) through an ffmpeg pipe, cropped above the
     subtitle band (bottom 18%) and scaled to 320x180 gray. Subtitles change
     every sentence; leaving them in makes "the picture settled" undetectable.
     The pipe is ~20x faster than cv2.VideoCapture frame-by-frame; do NOT add
     -hwaccel d3d11va here (measured 4.7x slower than software on this box).
  2. diff = mean abs difference of blurred gray frames; edge = mean |Laplacian|
     (proxy for "how much is on screen"); ink = fraction of pixels far from the
     frame's background median (proxy for "something substantive is written").
  3. scene cuts where edge collapses relative to its recent peak, PLUS diff
     spikes (>= --spike): board scrolls and slide zooms change content without
     collapsing edge. Continuous 3D animation stays below the spike threshold.
  4. inside a scene, a "plateau" = diff below --stable-diff for >= --min-stable
     seconds. Plateaus are split recursively when their first/last quarter
     medians differ (> --split-diff): PPT page turns are often slow fades whose
     per-frame diff never leaves the plateau band, so one plateau can span
     several pages. With --ink-split X (e.g. 0.01, opt-in) plateaus also split
     on sustained ink steps >= X: PPT pen/highlighter annotation adds ink while
     per-frame diff stays ~0, so one plateau can span six annotation states.
     Pick the plateau frame with enough content that is closest
     to the plateau's per-pixel median -- the median ignores a moving occluder
     (cursor, hand, picture-in-picture), dodging transient occlusion.
  5. content gate = edge >= max(--gate-abs, 0.75*plateau max) AND
     (ink >= --ink-gate OR edge >= 0.75*max edge within +/-20s).
     The old scene-wide relative gate killed sparse-but-real pages in long
     scenes (a 4-min PPT window yielded 3 shots, an IDE window 3); ink alone
     kills dark neon strokes (low ink, high edge), hence the disjunction.
     Blank scene openings (empty axes/blackboard) fail both arms.
  6. scenes with NO plateau (continuous animation, e.g. a rotating 3D graph)
     fall back to local content maxima: edge >= 0.75*scene max and a local
     maximum over +/-1.5s, spaced >= --min-gap, at most --max-per-scene.
     ("motion dips" were tried and falsified: such segments have no dips.)
  7. guarantee one frame per scene at >= 0.9*scene max edge (min diff = most
     settled). Its `occl` field records distance to the SCENE median as a
     review hint: a transient occluder (IDE autocomplete popup) is absent from
     the scene median, so an occluded peak scores high there. Not auto-dropped:
     "far from median" can also be legitimate full content.
  8. dedup near-identical picks (keep the richer one), merge picks <1.5s apart
     that are near-identical (scroll-boundary double picks), prune fade-in/out
     duplicates within 6s, sort by time.
     --masked-dedup compares only ink pixels: in IDE screencasts a few new code
     lines move the whole-frame mean by ~3 and get merged at --dedup 4.2, while
     on the ink mask the same change is tens of levels. Use it for code/IDE
     courses; NOT for typing boards, where it keeps every partial-line pause
     (measured 10 -> 21 picks of half-typed sentences).

Outputs in --out: shots.json (cuts + picks + params + video fingerprint),
shots.txt (one line per pick), snap/tMMSS.jpg (full-res frames),
montage_pN.jpg (4x4 contact sheets of the picks, needs matplotlib).

Idempotent: an existing shots.json whose params and video fingerprint match is
kept; --force recomputes.

Known limits: an occluder sitting still for a WHOLE plateau cannot be removed
from inside that plateau (the median contains it too) -- `occl` flags it for
review; cross-plateau templates or sentence-end frames are the way out.
Sentence-end timing itself is not usable from the pipeline's 60s-chunk
transcripts (chunk boundaries are not sentence boundaries).

Dependencies: numpy + ffmpeg/ffprobe only. cv2 and matplotlib are used when
present (cv2 gives the exact validated metrics; numpy fallbacks are close).
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

try:
    import cv2
except ImportError:  # numpy fallbacks below keep the script dependency-light
    cv2 = None

W, H = 320, 180
SW, SH = 160, 90


def blur(g):
    if cv2 is not None:
        return cv2.GaussianBlur(g, (5, 5), 0).astype(np.float32)
    a = g.astype(np.float32)
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (2, 2)
        p = np.pad(a, pad, mode="edge")
        sl = [slice(None), slice(None)]
        acc = np.zeros_like(a)
        for w, off in zip((1, 4, 6, 4, 1), range(5)):
            sl[axis] = slice(off, off + a.shape[axis])
            acc += w * p[tuple(sl)]
        a = acc / 16.0
    return a


def edge_of(g):
    if cv2 is not None:
        return float(np.abs(cv2.Laplacian(g, cv2.CV_32F)).mean())
    c = g.astype(np.float32)
    p = np.pad(c, 1, mode="edge")
    lap = 4 * c - p[:-2, 1:-1] - p[2:, 1:-1] - p[1:-1, :-2] - p[1:-1, 2:]
    return float(np.abs(lap).mean())


def ink_of(g):
    return float((np.abs(g.astype(np.int16) - np.median(g)) > 40).mean())


def small_of(g):
    if cv2 is not None:
        return cv2.resize(g, (SW, SH))
    return g.reshape(SH, H // SH, SW, W // SW).mean(axis=(1, 3)).astype(np.uint8)


def read_frames(video, fps, crop=0.82):
    """Gray 320x180 frames at `fps`, subtitle band cropped off, via ffmpeg pipe."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(video)],
                           capture_output=True, text=True)
    dur = float(probe.stdout.strip() or 0)
    proc = subprocess.Popen([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
        "-vf", f"crop=iw:ih*{crop}:0:0,scale={W}:{H},fps={fps}",
        "-f", "rawvideo", "-pix_fmt", "gray", "-"], stdout=subprocess.PIPE)
    raw, prev, i, fs = [], None, 0, W * H
    t0 = time.time()
    while True:
        buf = proc.stdout.read(fs)
        if len(buf) < fs:
            break
        g = np.frombuffer(buf, np.uint8).reshape(H, W)
        gb = blur(g)
        raw.append({"t": i / fps, "g": g, "small": small_of(g),
                    "diff": 0.0 if prev is None else float(np.abs(gb - prev).mean())})
        prev = gb
        i += 1
        if i % 500 == 0:
            el = time.time() - t0
            eta = el / i * max(0, dur * fps - i)
            print(f"  analyze {i / fps:6.0f}s / {dur:.0f}s  eta {eta:4.0f}s", flush=True)
    proc.wait()
    if not raw:
        raise SystemExit(f"no frames decoded from {video}")
    return raw, dur


def frame_metrics(raw):
    t = np.array([r["t"] for r in raw])
    d = np.convolve(np.pad(np.array([r["diff"] for r in raw]), 1, mode="edge"),
                    np.ones(3) / 3, mode="valid")
    e = np.array([edge_of(r["g"]) for r in raw])
    ink = np.array([ink_of(r["g"]) for r in raw])
    return t, d, e, ink


def scene_cuts(t, e, d, spike):
    cuts = []
    for i in range(2, len(e)):
        peak = max(e[max(0, i - 4):i + 1])
        if e[i] < 0.65 * peak and e[i] < 0.8 * e[i - 1]:
            if not cuts or t[i] - cuts[-1] > 3:
                cuts.append(float(t[i]))
    for i in range(1, len(d) - 1):
        if d[i] >= spike and d[i] >= d[i - 1] and d[i] >= d[i + 1]:
            if not cuts or t[i] - cuts[-1] > 3:
                cuts.append(float(t[i]))
    return sorted(cuts)


def pick_scene(seg, t, d, e, ink, loc, raw, a):
    """One scene -> list of picks. seg = frame indices inside the scene."""
    emax = max(e[i] for i in seg)
    scene = []

    def small(i):
        return raw[i]["small"].astype(np.float32)

    def rich(i):
        return ink[i] >= a.ink_gate or e[i] >= 0.75 * loc[i]

    def split_plateau(s, en, depth=0):
        n = en - s + 1
        if n < 8 or depth >= 3:
            return [(s, en)]
        q = max(2, n // 4)
        m1 = np.median(np.stack([small(k) for k in range(s, s + q)]), axis=0)
        m2 = np.median(np.stack([small(k) for k in range(en - q + 1, en + 1)]), axis=0)
        dmed = float(np.abs(m1 - m2).mean())
        if dmed > a.split_diff:
            m = s + int(np.argmax(d[s:en + 1]))
        elif a.ink_split > 0:
            # PPT pen annotations: red circles / yellow highlights add (or erase)
            # ink while frame diff stays < 1 (low motion). Detect sustained ink
            # steps: 2s median after minus 2s median before, split at strongest.
            sm = np.convolve(ink[s:en + 1], np.ones(5) / 5, mode="same")
            gain, m = 0.0, -1
            for k in range(s + 4, en - 4):
                g = abs(float(np.median(sm[k + 1:k + 5]) - np.median(sm[k - 4:k])))
                if g > gain:
                    gain, m = g, k
            if m < 0 or gain < a.ink_split:
                return [(s, en)]
        else:
            return [(s, en)]
        if m - s < 3 or en - m < 3:
            return [(s, en)]
        return split_plateau(s, m - 1, depth + 1) + split_plateau(m + 1, en, depth + 1)

    # 1) settled plateaus: enough content, closest to the plateau median
    min_n = max(2, int(a.min_stable * a.fps))
    runs, cur = [], None
    for i in seg:
        if d[i] < a.stable_diff and cur is None:
            cur = i
        elif d[i] >= a.stable_diff and cur is not None:
            runs.append((cur, i - 1))
            cur = None
    if cur is not None:
        runs.append((cur, seg[-1]))
    plates = []
    for s, en in runs:
        if en - s + 1 >= min_n:
            plates += split_plateau(s, en)
    for s, en in plates:
        if en - s + 1 < min_n:
            continue
        pmax = max(e[k] for k in range(s, en + 1))
        cand = [i for i in range(s, en + 1)
                if e[i] >= max(a.gate_abs, 0.75 * pmax) and rich(i)]
        if not cand:
            continue
        med = np.median(np.stack([small(k) for k in range(s, en + 1)]), axis=0)
        best = min(cand, key=lambda i: (float(np.abs(small(i) - med).mean()), -(i - s)))
        scene.append({"t": float(t[best]), "edge": float(e[best]),
                      "ink": round(float(ink[best]), 3),
                      "occl": round(float(np.abs(small(best) - med).mean()), 2),
                      "why": "plateau"})

    # 2) no plateau at all -> continuous animation: local content maxima
    if not any(s["why"] == "plateau" for s in scene):
        cands = [i for i in seg[3:-3]
                 if e[i] >= 0.75 * emax and e[i] >= max(e[i - 3:i + 4])]
        chosen = []
        for i in sorted(cands, key=lambda k: -e[k]):
            if all(abs(t[i] - t[j]) >= a.min_gap for j in chosen):
                chosen.append(i)
        for i in sorted(chosen)[:a.max_per_scene]:
            if not any(abs(t[i] - s["t"]) < 2.0 for s in scene):
                scene.append({"t": float(t[i]), "edge": float(e[i]),
                              "ink": round(float(ink[i]), 3), "occl": None,
                              "why": "content-max"})

    # 3) guarantee the richest frame of the scene survives; occl vs scene median
    #    is a review hint for transient occluders (popups), not a drop rule
    if not any(s["edge"] >= 0.90 * emax for s in scene):
        cand = [i for i in seg if e[i] >= 0.90 * emax and rich(i)]
        if cand:
            smed = np.median(np.stack([small(k) for k in seg[::2]]), axis=0)
            best = min(cand, key=lambda i: d[i])
            scene.append({"t": float(t[best]), "edge": float(e[best]),
                          "ink": round(float(ink[best]), 3),
                          "occl": round(float(np.abs(small(best) - smed).mean()), 2),
                          "why": "content-peak"})
    return sorted(scene, key=lambda s: s["t"])


def dedup_and_prune(picks, t, raw, a):
    def idx_of(sec):
        return int(np.argmin(np.abs(t - sec)))

    def fd(i, j):
        return float(np.abs(raw[i]["small"].astype(np.float32)
                            - raw[j]["small"].astype(np.float32)).mean())

    def mfd(i, j):
        gi = raw[i]["g"].astype(np.int16)
        gj = raw[j]["g"].astype(np.int16)
        mask = (np.abs(gi - np.median(gi)) > 40) | (np.abs(gj - np.median(gj)) > 40)
        if mask.sum() < 50:
            return fd(i, j)
        return float(np.abs(gi - gj)[mask].mean())

    cmp = mfd if a.masked_dedup else fd
    kept = []
    for p in picks:
        gi = idx_of(p["t"])
        dup = next((k for k, q in enumerate(kept) if cmp(gi, idx_of(q["t"])) < a.dedup), None)
        if dup is not None:
            if p["edge"] > kept[dup]["edge"]:
                kept[dup] = p
            continue
        kept.append(p)
    # same-instant double picks (scroll/cut boundary): merge near-identical
    merged = []
    for p in kept:
        if merged and p["t"] - merged[-1]["t"] < 1.5 and fd(idx_of(p["t"]), idx_of(merged[-1]["t"])) < 6:
            if p["edge"] > merged[-1]["edge"]:
                merged[-1] = p
            continue
        merged.append(p)
    pruned = []
    for p in merged:
        if pruned and p["t"] - pruned[-1]["t"] < 6 and \
                min(p["edge"], pruned[-1]["edge"]) / max(p["edge"], pruned[-1]["edge"]) < 0.7:
            if p["edge"] > pruned[-1]["edge"]:
                pruned[-1] = p
            continue
        pruned.append(p)
    return sorted(pruned, key=lambda p: p["t"])


def mmss(sec):
    return f"{int(sec) // 60:02d}:{int(sec) % 60:02d}"


def write_snaps(video, out, picks):
    snap = out / "snap"
    snap.mkdir(parents=True, exist_ok=True)
    for p in picks:
        f = snap / f"t{mmss(p['t']).replace(':', '')}.jpg"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss",
                        f"{p['t']:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "2",
                        str(f)], check=False)
        p["file"] = f"snap/{f.name}"
    return snap


def write_montage(out, picks):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("montage skipped (matplotlib not installed)")
        return
    matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    files = [(p, out / p["file"]) for p in picks if p.get("file")]
    cols, per = 4, 16
    for page in range(0, len(files), per):
        chunk = files[page:page + per]
        rows = (len(chunk) + cols - 1) // cols
        fig, axes = plt.subplots(rows, cols, figsize=(cols * 4.2, rows * 2.6))
        axes = np.atleast_2d(axes).ravel()
        for ax in axes:
            ax.axis("off")
        for ax, (p, f) in zip(axes, chunk):
            if f.exists():
                ax.imshow(plt.imread(f))
            ax.set_title(f"{mmss(p['t'])}  {p['why']}", fontsize=9)
        plt.tight_layout()
        dst = out / f"montage_p{page // per + 1}.jpg"
        plt.savefig(dst, dpi=115)
        plt.close(fig)
        print("saved", dst)


def main():
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--stable-diff", type=float, default=1.1)
    ap.add_argument("--min-stable", type=float, default=1.5)
    ap.add_argument("--gate-abs", type=float, default=4.5)
    ap.add_argument("--ink-gate", type=float, default=0.035)
    ap.add_argument("--spike", type=float, default=12.0)
    ap.add_argument("--split-diff", type=float, default=6.0)
    ap.add_argument("--ink-split", type=float, default=0.0,
                    help="split plateaus on sustained ink steps >= this (e.g. 0.01): "
                         "for PPT lectures with progressive pen/highlighter annotation "
                         "where frame diff stays ~0 but ink accumulates")
    ap.add_argument("--dedup", type=float, default=4.2)
    ap.add_argument("--masked-dedup", action="store_true",
                    help="dedup on ink pixels only: for IDE/code screencasts where "
                         "a few new lines move the whole-frame mean by ~3")
    ap.add_argument("--min-gap", type=float, default=5.0)
    ap.add_argument("--max-per-scene", type=int, default=6)
    ap.add_argument("--crop", type=float, default=0.82,
                    help="keep this fraction of frame height (drops subtitle band)")
    ap.add_argument("--no-snap", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    video = Path(a.video).resolve()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    params = {k: getattr(a, k) for k in
              ("fps", "stable_diff", "min_stable", "gate_abs", "ink_gate", "spike",
               "split_diff", "ink_split", "dedup", "masked_dedup", "min_gap",
               "max_per_scene", "crop")}
    fp = {"mtime": video.stat().st_mtime, "size": video.stat().st_size}
    sj = out / "shots.json"
    if sj.exists() and not a.force:
        old = json.loads(sj.read_text(encoding="utf-8"))
        if old.get("params") == params and old.get("video") == fp:
            print(f"smart shots exist ({len(old['picks'])} picks), skip")
            return
    print(f"smart shots: {video.name} -> {out}", flush=True)
    raw, dur = read_frames(video, a.fps, a.crop)
    t, d, e, ink = frame_metrics(raw)
    half = int(20 * a.fps)
    loc = np.array([max(e[max(0, i - half):i + half + 1]) for i in range(len(e))])
    cuts = scene_cuts(t, e, d, a.spike)
    bounds = [0.0] + cuts + [float(t[-1]) + 1]
    picks = []
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        seg = [i for i in range(len(t)) if lo <= t[i] < hi]
        if seg:
            picks.extend(pick_scene(seg, t, d, e, ink, loc, raw, a))
    kept = dedup_and_prune(picks, t, raw, a)
    if not a.no_snap:
        write_snaps(video, out, kept)
        write_montage(out, kept)
    for p in kept:
        p["mmss"] = mmss(p["t"])
    sj.write_text(json.dumps({"video": fp, "duration": dur, "params": params,
                              "cuts": cuts, "picks": kept},
                             ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "shots.txt").write_text("\n".join(
        f"[{p['mmss']}] {p['why']:12s} edge={p['edge']:5.1f} ink={p.get('ink')} "
        f"occl={p.get('occl')}  {p.get('file', '')}"
        for p in kept) + "\n", encoding="utf-8")
    print(f"cuts={len(cuts)} picks={len(kept)}")
    for p in kept:
        print(f"  {p['mmss']}  edge={p['edge']:5.1f}  {p['why']}")


if __name__ == "__main__":
    main()