"""Build meta.json for a Bilibili 合集 where every episode is its own BV.

`course_prep.py meta` reads `pages[]` of a single multi-P video; a 合集 (season)
is a list of independent videos, which the view API exposes under `ugc_season`
instead. Emit the same meta.json shape, plus a per-page `bvid` that acquire
uses to build the download URL.
"""
import io
import json
import sys
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
url = sys.argv[1]
out = sys.argv[2]
m = __import__("re").search(r"(BV[0-9A-Za-z]+)", url)
req = urllib.request.Request(
    f"https://api.bilibili.com/x/web-interface/view?bvid={m.group(1)}",
    headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
d = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())["data"]
eps = [e for sec in d["ugc_season"]["sections"] for e in sec["episodes"]]
pages = []
for i, e in enumerate(eps, 1):
    pages.append({
        "index": i,
        "part": e["arc"]["title"],
        "duration": e["arc"]["duration"],
        "cid": e.get("cid"),
        "bvid": e["bvid"],
        "url": f"https://www.bilibili.com/video/{e['bvid']}/",
    })
meta = {
    "bvid": d["bvid"],
    "title": d["ugc_season"].get("title") or d["title"],
    "owner": d["owner"]["name"],
    "series_url": f"https://www.bilibili.com/video/{d['bvid']}/",
    "resolved_url": url,
    "season": True,
    "pages": pages,
}
io.open(out, "w", encoding="utf-8").write(json.dumps(meta, ensure_ascii=False, indent=2))
print(f"{len(pages)} episodes, {sum(p['duration'] for p in pages)/60:.1f} min -> {out}")
