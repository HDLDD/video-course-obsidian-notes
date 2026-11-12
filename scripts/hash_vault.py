#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Snapshot or compare SHA-256 of every file in the vault.

The pipeline's deletion rule is "backup succeeded AND the vault is byte-identical
before and after", so this is what you run on both sides of any cleanup:

  python hash_vault.py --vault "<vault>" --out before.txt
  ... backup, then delete media ...
  python hash_vault.py --vault "<vault>" --out after.txt --compare before.txt

Exit code 1 means the vault changed -- nothing may be deleted until that is
explained.
"""
import argparse
import hashlib
import io
import os
import sys


def snapshot(vault):
    out = {}
    for root, dirs, files in os.walk(vault):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(root, f)
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            out[os.path.relpath(p, vault)] = h.hexdigest()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--compare", default="")
    a = ap.parse_args()

    cur = snapshot(a.vault)
    with io.open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        for rel in sorted(cur):
            fh.write(f"{cur[rel]}  {rel}\n")
    print(f"{len(cur)} files -> {a.out}")

    if not a.compare:
        return 0
    before = {}
    for ln in io.open(a.compare, encoding="utf-8").read().splitlines():
        digest, _, rel = ln.partition("  ")
        before[rel] = digest
    changed = [r for r in before if r not in cur or cur[r] != before[r]]
    added = [r for r in cur if r not in before]
    print(f"changed/deleted={len(changed)} added={len(added)}")
    for r in changed[:20]:
        print("  M", r)
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
