# -*- coding: utf-8 -*-
"""Take upstream's version of every file we hold that has fallen behind, outside packer_sync's five folders.

Why: packer_sync.py covers only dm/tools, dm/gfx, dm/games, dm/host and dm/third_party; docs/, dm/ios/, dm/mac/ and
scripts/ are nobody's job and quietly fall behind upstream. This copies upstream over those files in the fork.

Only files both sides have: this fork keeps a deliberate subset of upstream (its .gitignore says so), so a file we never
had is not added -- for that full picture use drift.py. The root .gitignore and the five needs_mac_shaders profiles are
ours and stay.

    python scripts/basemove/sync_rest.py [upstream tree]   # default: ../nx-orig, env NXN_UPSTREAM
                                                          # fork tree: --repo, env NXN_REPO
"""
import argparse
import os
import pathlib
import shutil
import sys

SKIP_DIRS = {'.git', '__pycache__', 'obj', 'bin', 'dmsh_xlate_bin'}
SKIP_FILES = {'c1_full.tsv', 'cb_extent.tsv', 'dmsh_chain.h', 'dmsh_chain.lock'}
KEEP = {'.gitignore', 'dm/games/01004A4010FEA000/game.tsv', 'dm/games/01005270232F2000/game.tsv',
        'dm/games/0100606026F12000/game.tsv', 'dm/games/01006C300E9F0000/game.tsv',
        'dm/games/0100F43008C44000/game.tsv'}

# scripts/basemove/sync_rest.py -> the fork's root
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
            yield os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/")


def main():
    ap = argparse.ArgumentParser(description="Copy upstream's newer version over every stale file the fork shares with it.")
    ap.add_argument("upstream", nargs="?",
                    default=os.environ.get("NXN_UPSTREAM") or str(REPO.parent / "nx-orig"))
    ap.add_argument("--repo", default=os.environ.get("NXN_REPO") or str(REPO),
                    help="the fork's tree (default: this script's repo)")
    args = ap.parse_args()
    repo, up = pathlib.Path(args.repo), pathlib.Path(args.upstream)
    # os.walk on a missing tree yields nothing, and a run that copies 0 files looks exactly like a run that was up to date.
    for name, p in (("fork", repo), ("upstream", up)):
        if not p.is_dir():
            raise SystemExit("%s tree does not exist: %s" % (name, p))

    own = overlay(repo)
    ours = lambda r: r in own or any(r.startswith(k + "/") for k in own) or r in KEEP
    mine = {r for r in walk(repo) if not ours(r) and os.path.basename(r) not in SKIP_FILES}
    theirs = {r for r in walk(up) if not ours(r) and os.path.basename(r) not in SKIP_FILES}
    n = 0
    for r in sorted(mine & theirs):
        x = open(os.path.join(repo, r), "rb").read().replace(b"\r\n", b"\n")
        y = open(os.path.join(up, r), "rb").read().replace(b"\r\n", b"\n")
        if x != y:
            shutil.copyfile(os.path.join(up, r), os.path.join(repo, r))
            print("took upstream  " + r)
            n += 1
    print("%d file(s) updated" % n)


if __name__ == "__main__":
    main()
