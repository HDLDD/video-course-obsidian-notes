#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backfill index links from what actually exists on disk.

Subagents must not edit chapter/course indexes: two agents writing the same
chapter folder collide, and an agent that "finished" is not evidence. This
script reads the filesystem and rewrites:

  * each chapter index row's 笔记 cell -> [[讲路径/00-本讲索引|本讲笔记]] · [B站](url)
  * the chapter 整理状态 callout -> success when every lesson of that chapter exists
  * the course index 状态 column, 总进度 and the generated-count line

  python fill_indexes.py --vault "<vault>/<course>" --manifest work/manifest.json

Idempotent: run it as often as you like.
"""
import argparse
import io
import json
import os
import re


def read(p):
    return io.open(p, encoding="utf-8", newline="").read()


def write(p, s):
    io.open(p, "w", encoding="utf-8", newline="").write(s)


def replace_last_cell(line, cell):
    """Swap the final cell of a Markdown table row without tripping over the
    escaped pipes (\\|) that Obsidian links legitimately contain."""
    parts = re.split(r"(?<!\\)\|", line)
    if len(parts) >= 2 and parts[-1].strip() == "":
        parts[-2] = f" {cell} "
        return "|".join(parts)
    parts[-1] = f" {cell} "
    return "|".join(parts) + " |"


def rebuild_table(text, header_key, order, prefix_of, gen_row):
    """Make one Markdown table body contain every row of `order`, in that order.

    A course that gains chapters or lessons after the skeleton was written would
    otherwise have rows nobody can add (agents are banned from editing indexes,
    and the old loop only rewrote rows already present). Rows that match none of
    the expected prefixes are kept, untouched, at the end.
    """
    cr = "\r" if "\r\n" in text else ""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if header_key in ln and ln.rstrip("\r").lstrip().startswith("|"):
            start, j = i + 2, i + 2
            while j < len(lines) and lines[j].rstrip("\r").lstrip().startswith("|"):
                j += 1
            old = lines[start:j]
            taken, body = set(), []
            for r in order:
                p = prefix_of(r)
                hit = next((k for k, l in enumerate(old)
                            if k not in taken and l.rstrip("\r").startswith(p)), None)
                if hit is None:
                    body.append(gen_row(r) + cr)
                else:
                    taken.add(hit)
                    body.append(old[hit])
            body += [l for k, l in enumerate(old) if k not in taken]
            lines[start:j] = body
            break
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--date", default="")
    a = ap.parse_args()
    root, rows = a.vault, json.loads(read(a.manifest))
    done = arts = imgs = 0
    per_chapter = {}

    for r in rows:
        lp = os.path.join(root, *r["lesson_path"].split("/"))
        idx = os.path.join(lp, "00-本讲索引.md")
        if not os.path.isfile(idx):
            print("MISSING", r["lesson_path"])
            continue
        done += 1
        n_art = len([f for f in os.listdir(lp) if re.match(r"^\d{2}-.*\.md$", f)
                     and not f.startswith(("00-", "99-"))])
        adir = os.path.join(lp, "assets")
        n_img = len([f for f in os.listdir(adir) if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))]) if os.path.isdir(adir) else 0
        arts += n_art
        imgs += n_img
        per_chapter.setdefault(r["folder"], []).append((r["slug"], n_art, n_img))
        cell = f"[[{r['lesson_path']}/00-本讲索引\\|本讲笔记]] · [B站]({r['url']})"
        cip = os.path.join(root, *r["folder"].split("/"), "00-本章索引.md")
        if not os.path.isfile(cip):
            print("NO CHAPTER INDEX", r["folder"])
            continue
        lines = read(cip).split("\n")
        hit = False
        prefix = f"| {r['slug']} | P{r['part']} | "
        for i, ln in enumerate(lines):
            if ln.startswith(prefix):
                lines[i] = replace_last_cell(ln, cell)
                hit = True
        if not hit:
            print("ROW NOT FOUND", r["slug"], r["folder"])
        write(cip, "\n".join(lines))

    total_by_folder = {}
    rows_by_folder = {}
    for r in rows:
        total_by_folder[r["folder"]] = total_by_folder.get(r["folder"], 0) + 1
        rows_by_folder.setdefault(r["folder"], []).append(r)
    for folder, items in per_chapter.items():
        cip = os.path.join(root, *folder.split("/"), "00-本章索引.md")
        s = read(cip)
        s = rebuild_table(
            s, "| 时长 | 笔记 |", rows_by_folder[folder],
            lambda r: f"| {r['slug']} | P{r['part']} | ",
            lambda r: f"| {r['slug']} | P{r['part']} | {r['part_title']} | {r['duration']} | "
            f"待整理 · [B站]({r['url']}) |")
        if len(items) == total_by_folder[folder]:
            n_art = sum(x[1] for x in items)
            nl = "\r\n" if "\r\n" in s else "\n"
            s = re.sub(r"> \[!(?:success|warning)\] 整理状态\r?\n> [^\r\n]*",
                       f"> [!success] 整理状态{nl}> 本章 {len(items)} 讲已完成，共 {n_art} 篇知识点正文。",
                       s)
        write(cip, s)

    cpath = os.path.join(root, "00-课程索引.md")
    if os.path.isfile(cpath):
        s = read(cpath)
        lines = s.split("\n")
        for r in rows:
            ok = os.path.isfile(os.path.join(root, *r["lesson_path"].split("/"),
                                             "00-本讲索引.md"))
            cell = f"[[{r['lesson_path']}/00-本讲索引\\|完成]]" if ok else "待整理"
            prefix = f"| P{r['part']} | {r['slug']} | "
            for i, ln in enumerate(lines):
                if ln.startswith(prefix):
                    lines[i] = replace_last_cell(ln, cell)
        s = "\n".join(lines)
        label = lambda c: "附录" if c == "0" else f"第{c}章"
        groups = []
        for r in rows:
            if not groups or groups[-1][0]["chapter"] != r["chapter"]:
                groups.append([])
            groups[-1].append(r)
        s = rebuild_table(
            s, "| 讲数 | 本章索引 |", groups,
            lambda g: f"| {label(g[0]['chapter'])} | ",
            lambda g: f"| {label(g[0]['chapter'])} | P{g[0]['part']}-P{g[-1]['part']} | "
            f"{g[0]['chapter_topic']} | {len(g)} | [[{g[0]['folder']}/00-本章索引\\|本章索引]] |")

        def lesson_cell(r):
            ok = os.path.isfile(os.path.join(root, *r["lesson_path"].split("/"),
                                             "00-本讲索引.md"))
            return f"[[{r['lesson_path']}/00-本讲索引\\|完成]]" if ok else "待整理"

        s = rebuild_table(
            s, "| 时长 | 状态 |", rows,
            lambda r: f"| P{r['part']} | {r['slug']} | ",
            lambda r: f"| P{r['part']} | {r['slug']} | {r['part_title']} | "
            f"{r['duration']} | {lesson_cell(r)} |")
        s = s.replace("status: 进行中", "status: 已完成" if done == len(rows) else "status: 进行中")
        s = re.sub(r"> - 总进度：\*\*[^*]*\*\*", f"> - 总进度：**{done}/{len(rows)} 讲**", s)
        line = f"> - 已生成 {arts} 篇知识点正文、{imgs} 张截图。"
        nl = "\r\n" if "\r\n" in s else "\n"
        # drop any stale count line, then re-insert it right after 总进度 so it
        # refreshes on every run instead of drifting (and never lands at EOF)
        s = re.sub(r"(?m)^> - 已生成 .*\r?\n", "", s)
        s = re.sub(r"(?m)^> - 总进度：.*$", lambda m: m.group(0) + nl + line, s, count=1)
        if a.date:
            s = re.sub(r"^updated: .*$", f"updated: {a.date}", s, flags=re.M)
        write(cpath, s)

    print(f"lessons={done}/{len(rows)} articles={arts} images={imgs}")


if __name__ == "__main__":
    main()
