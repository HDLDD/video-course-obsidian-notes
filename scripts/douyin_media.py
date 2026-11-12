#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音（douyin.com）视频的取流与抽帧助手。

抖音的网页接口要 a_bogus 签名，yt-dlp 直连会被要求 cookie（Chrome 127+ 的
App-Bound 加密又让 --cookies-from-browser 失效）。可行路线是：**先在浏览器里
把页面播放起来，再从网络面板拿到 DASH 的音频/视频两条地址**，然后交给本脚本
下载。地址带时效（通常几小时），拿到就尽快下。

用法：
  python douyin_media.py resolve https://v.douyin.com/XXXX/
      短链 -> 视频 ID + 规范地址

  python douyin_media.py fetch --out work/v1 \
      --audio "https://v26-web.douyinvod.com/...media-audio..." \
      --video "https://v26-web.douyinvod.com/...media-video..."
      下载 audio.m4a / video.mp4，打印时长与体积

  python douyin_media.py frames --video work/v1/video.mp4 --out work/v1/frames \
      --times 00:00:10,00:06:10,00:13:15 --width 1400
      按时间点抽帧（PNG）

  python douyin_media.py probe --video work/v1/video.mp4
      读分辨率/编码/时长

拿到地址的办法（任一）：
  1. 浏览器 F12 -> Network -> 过滤 `media-video` / `media-audio`，
     右键 Copy link address；
  2. 有 browser-use MCP 时，在页面 console 里跑
       performance.getEntriesByType('resource').map(e=>e.name)
         .filter(n=>/media-(video|audio)/.test(n))
     取前两条（播放过才有）。
"""
import argparse
import json
import os
import re
import subprocess
import sys

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")
SHORT_RE = re.compile(r"https?://v\.douyin\.com/[A-Za-z0-9]+/?")
ID_RE = re.compile(r"/video/(\d+)")


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def resolve(url):
    r = sh(["curl", "-sIL", "--max-time", "25", "-A", UA, url])
    locs = [ln.split(":", 1)[1].strip() for ln in r.stdout.splitlines()
            if ln.lower().startswith("location:")]
    canon = locs[-1] if locs else url
    m = ID_RE.search(canon)
    return {
        "ok": bool(m),
        "id": m.group(1) if m else None,
        "url": canon,
        "canonical": "https://www.douyin.com/video/%s" % m.group(1) if m else None,
    }


def fetch(url, dest):
    r = sh(["curl", "-sS", "-L", "--max-time", "600", "-A", UA,
            "-H", "Referer: https://www.douyin.com/", "-o", dest, url])
    if not os.path.isfile(dest) or os.path.getsize(dest) < 20000:
        return {"ok": False, "dest": dest, "err": (r.stderr or "empty file").strip()[:200]}
    return {"ok": True, "dest": dest, "bytes": os.path.getsize(dest)}


def probe(path):
    if not os.path.isfile(path):
        return {"ok": False, "err": "missing " + path}
    r = sh(["ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height",
            "-show_entries", "format=duration", "-of", "json", path])
    try:
        d = json.loads(r.stdout)
    except Exception:
        return {"ok": False, "err": (r.stderr or "ffprobe failed").strip()[:200]}
    st = (d.get("streams") or [{}])[0]
    return {
        "ok": True,
        "duration": round(float(d.get("format", {}).get("duration", 0)), 1),
        "codec": st.get("codec_name"),
        "width": st.get("width"),
        "height": st.get("height"),
    }


def frames(video, outdir, times, width):
    os.makedirs(outdir, exist_ok=True)
    made = []
    for t in times:
        name = "t_" + re.sub(r"\D", "", t).ljust(6, "0")
        dst = os.path.join(outdir, name + ".png")
        vf = "scale=%d:-2" % width if width else "null"
        r = sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", t, "-i", video, "-frames:v", "1", "-vf", vf, dst])
        made.append({"t": t, "file": dst, "ok": os.path.isfile(dst),
                     "err": (r.stderr or "").strip()[:120]})
    return made


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("resolve", help="短链 -> 视频 ID")
    p.add_argument("url")

    p = sub.add_parser("fetch", help="按拿到的媒体地址下载")
    p.add_argument("--out", required=True)
    p.add_argument("--audio", default="")
    p.add_argument("--video", default="")

    p = sub.add_parser("probe", help="读时长/分辨率")
    p.add_argument("--video", required=True)

    p = sub.add_parser("frames", help="按时间点抽帧")
    p.add_argument("--video", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--times", required=True, help="逗号分隔，如 00:00:10,00:06:10")
    p.add_argument("--width", type=int, default=1400)

    a = ap.parse_args()

    if a.cmd == "resolve":
        res = resolve(a.url)
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res["ok"] else 1

    if a.cmd == "fetch":
        os.makedirs(a.out, exist_ok=True)
        out = {}
        if a.audio:
            out["audio"] = fetch(a.audio, os.path.join(a.out, "audio.m4a"))
        if a.video:
            out["video"] = fetch(a.video, os.path.join(a.out, "video.mp4"))
            if out["video"].get("ok"):
                out["probe"] = probe(out["video"]["dest"])
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0 if all(v.get("ok") for k, v in out.items() if isinstance(v, dict) and "ok" in v) else 1

    if a.cmd == "probe":
        print(json.dumps(probe(a.video), ensure_ascii=False))
        return 0

    if a.cmd == "frames":
        times = [t.strip() for t in a.times.split(",") if t.strip()]
        made = frames(a.video, a.out, times, a.width)
        print(json.dumps(made, ensure_ascii=False, indent=1))
        return 0 if all(m["ok"] for m in made) else 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
