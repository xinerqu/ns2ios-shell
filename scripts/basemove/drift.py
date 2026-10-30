# -*- coding: utf-8 -*-
"""Full drift between this fork and an upstream checkout: everything except what the overlay owns.

Why: packer_sync.py only covers dm/tools, dm/gfx, dm/games, dm/host and dm/third_party, so a base move can leave docs/,
dm/ios, dm/mac and scripts/ behind without any tool noticing. This walks both trees and prints all three sets.

Relationship to preflight.py (one directory above this repo): preflight reports only a subset -- the files both trees have
whose content is not upstream's -- and only as a warning. It never looks at what only this fork has or only upstream has.
This script is the full comparison, and it is the authoritative implementation; if the two disagree, believe this one.

    python scripts/basemove/drift.py [upstream tree]      # default: ../nx-orig, env NXN_UPSTREAM
                                                          # fork tree: --repo, env NXN_REPO
"""
import argparse
import os
import pathlib
import sys

SKIP_DIRS = {'.git', '__pycache__', 'obj', 'bin', 'dmsh_xlate_bin'}
SKIP_FILES = {'c1_full.tsv', 'cb_extent.tsv', 'dmsh_chain.h', 'dmsh_chain.lock'}

# scripts/basemove/drift.py -> the fork's root
REPO = pathlib.Path(__file__).resolve().parents[2]


def overlay(repo):
    keep = set()
    for line in open(os.path.join(repo, ".shell-overlay.txt"), encoding="utf-8"):
        s = line.rstrip("\n")
        if s[:1] in ("+", "~"):
            keep.add(s[1:].split("#")[0].strip().rstrip("/"))
    return keep


def walk(root):
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in files:
            full = os.path.join(d, f)
            yield os.path.relpath(full, root).replace(os.sep, "/")


def main():
    ap = argparse.ArgumentParser(description="Full tree drift between this fork and an upstream checkout.")
    ap.add_argument("upstream", nargs="?",
                    default=os.environ.get("NXN_UPSTREAM") or str(REPO.parent / "nx-orig"))
    ap.add_argument("--repo", default=os.environ.get("NXN_REPO") or str(REPO),
                    help="the fork's tree (default: this script's repo)")
    args = ap.parse_args()
    repo, up = pathlib.Path(args.repo), pathlib.Path(args.upstream)
    # os.walk on a missing tree yields nothing and the diff would read as "clean", so say it out loud instead.
    for name, p in (("fork", repo), ("upstream", up)):
        if not p.is_dir():
            raise SystemExit("%s tree does not exist: %s" % (name, p))

    keep = overlay(repo)
    def ours(rel):
        return rel in keep or any(rel.startswith(k + "/") for k in keep)
    mine = {r for r in walk(repo) if not ours(r) and os.path.basename(r) not in SKIP_FILES}
    theirs = {r for r in walk(up) if not ours(r) and os.path.basename(r) not in SKIP_FILES}
    only_here, only_up, differ = sorted(mine - theirs), sorted(theirs - mine), []
    for r in sorted(mine & theirs):
        x = open(os.path.join(repo, r), "rb").read().replace(b"\r\n", b"\n")
        y = open(os.path.join(up, r), "rb").read().replace(b"\r\n", b"\n")
        if x != y:
            differ.append(r)
    print("fork     = %s" % repo)
    print("upstream = %s" % up)
    print("== only in this fork (%d) ==" % len(only_here))
    for r in only_here: print("   " + r)
    print("\n== only upstream (%d) ==" % len(only_up))
    for r in only_up: print("   " + r)
    print("\n== differ (%d) ==" % len(differ))
    for r in differ: print("   " + r)


if __name__ == "__main__":
    main()
