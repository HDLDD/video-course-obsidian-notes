# 抖音（douyin.com）取流与抽帧

抖音的网页接口要 `a_bogus` 签名，**yt-dlp 直连一定失败**：

```
ERROR: [Douyin] ...: Fresh cookies (not necessarily logged in) are needed
```

而 `--cookies-from-browser chrome` 也不行——Chrome 127+ 的 App-Bound Encryption 让
外部程序无法解密 cookie（报 `Failed to decrypt with DPAPI`）。所以抖音这条路必须
**先在浏览器里把页面跑起来**，再从网络层拿 DASH 的两条媒体地址。

## 1 解析短链

```
python scripts/douyin_media.py resolve "https://v.douyin.com/XXXX/"
→ {"ok":true,"id":"7698684871731907850","canonical":"https://www.douyin.com/video/7698684871731907850"}
```

分享页（`iesdouyin.com/share/video/<id>/`）用 curl 拿不到标题，`<title>`/`og:title` 都是空的
（JS 渲染），所以标题要么从浏览器读，要么从页面正文里取。

## 2 在浏览器里拿两条媒体地址

打开规范地址，**让视频真正播起来**（抖音常常不自动播放，按一下空格）。

- 手动：F12 → Network → 过滤 `media-video` / `media-audio` → 右键 Copy link address
- 有 browser-use MCP 时，在 console 里跑（只读，无副作用）：

```js
performance.getEntriesByType('resource').map(e => e.name)
  .filter(n => /media-(video|audio)/.test(n))
```

拿到的是**两条**：`.../media-video-hvc1/...`（HEVC 视频）和 `.../media-audio-und-mp4a/...`（音频）。
转写只需要音频；要抽帧才需要视频。

> 网络面板的完整 dump 很大（一次 500+ 条、十几万字符）。别整份读，用
> `grep -o "https://v[0-9a-z-]*\.douyinvod\.com[^ ]*media-audio[^ ]*"` 之类筛出来。
>
> 页面刚打开时 `performance` 里可能还没有媒体条目——没播放就没有。且**播放过之后**
> `performance` 可能被站点清空，这时直接看 Network 面板更稳。

## 3 下载

```
python scripts/douyin_media.py fetch --out work/v1 \
    --audio "https://...media-audio..." \
    --video "https://...media-video..."
```

脚本自动带桌面 UA + `Referer: https://www.douyin.com/`，下完打印体积与
`probe`（时长/编码/分辨率）。实测：23 分钟视频音频约 33 MB、视频 30–100 MB（4K HEVC）。

**地址有时效**（几小时），拿到就下；过期会 403，重新播一次再抓即可。

## 4 转写与抽帧

```
ffmpeg -i work/v1/audio.m4a -ac 1 -ar 16000 work/v1/audio_16k.wav      # 可选，脚本内部也会转
python scripts/transcribe_sensevoice.py --input work/v1/audio.m4a \
    --out-dir work/v1 --model-dir <models> --threads 8

python scripts/douyin_media.py frames --video work/v1/video.mp4 \
    --out work/v1/frames --times 00:07:50,00:12:25,00:20:05 --width 1400
```

实测速度：36 分钟音频 8 线程约 5–7 分钟。

> `transcribe_sensevoice.py` 内部会再跑一次 ffmpeg 规范化。**别把已经转好的
> `audio_16k.wav` 再当 `--input`**——它会把输入输出写成同一个文件，ffmpeg 直接报错
> （`exit 4294967274`）。喂原始 `.m4a` 最稳。

## 5 画面核对（教学类视频必做）

抖音的教学视频通常是"左边讲、右边 WPS 打字"，**板书上的英文才是权威**，ASR 只当底稿。
这类视频的 ASR 对中英混说的英文术语错得很规律：

| ASR | 实际 |
|---|---|
| lipfrog | leapfrog |
| ski | scale |
| post | cost |
| adance / ad once | advance |
| governness | governance |
| conrable / sential | considerable / substantial |

做法：

1. 从页面正文的「章节要点」拿每句的起始时间（抖音会给 `00:08 第一句` 这种列表）；
2. 抽**句末**的帧（下一句起点 - 15~30 秒），那里板书译文已经敲完；
3. 用 `![]()` 嵌 2–3 张到笔记里（宽度压到 1200），并在笔记里区分
   「板书原文」和「按讲解整理」，别把整理稿说成板书。

acquire 有视频时会自动跑 `scripts/smart_shots.py`（内容感知截帧：场景切分→平台期→
内容量最大帧）→ `frames/smart/shots.txt + snap/`。板书边讲边写的课，平台期≈讲师停顿，
选帧天然落在写满的时刻；仍与句末帧互相核对。按视频类型加参数（PPT 红笔/黄高亮标注课
`--ink-split 0.003 --masked-dedup`、IDE 录屏 `--masked-dedup`，打字板/动画/公式用默认），
见 SKILL.md Step 4。

## 6 坑

- **别用 `--cookies-from-browser`**：DPAPI 解密必失败，浪费时间。
- **HEVC 4K**：ffmpeg 解码没问题，但网页里可能播不动；抽帧不用管。
- **`media-video` / `media-audio` 不是同一路**：只下音频不会得到抽帧能力，只下视频转写要多解一次码。
- **短链来源不同**：`v.douyin.com` 跳 `iesdouyin` 再跳 `www.douyin.com/video/<id>`，最终 ID 以最后一跳为准。
- **合集/主页**：抖音主页不是播放列表，别指望 `--playlist`。
