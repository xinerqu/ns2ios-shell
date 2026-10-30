# -*- coding: utf-8 -*-
"""Three-way merge for a base move: ours (this fork, LF) x base (upstream at old base) x theirs (upstream at new base).

Why: a base move pushes the fork's patch base to a newer upstream commit, and every file the fork patches has to be
re-merged against it; this does the merge and reports the conflict count before anything is written back.

Never use shell redirection to read blobs (Git Bash rewrites LF to CRLF); all bytes come from
subprocess.run(capture_output=True).stdout.

usage:
  python scripts/basemove/rel_base.py try   <path>...    merge into <out>/<slug>.merged, report the conflict count
  python scripts/basemove/rel_base.py apply <path>...    same, but write the result back into the working tree

  fork tree:     --repo      (default: this script's repo;  env NXN_REPO)
  upstream tree: --upstream  (default: <repo>/../nx-orig;   env NXN_UPSTREAM)
  output dir:    --out       (default: <repo>/../tmp/merge; env NXN_MERGE)
"""
import argparse
import os
import pathlib
import subprocess

# scripts/basemove/rel_base.py -> the fork's root
REPO = pathlib.Path(__file__).resolve().parents[2]


def old_base(repo):
    """The base the patches were made against: read it, never hardcode it (it moves)."""
    f = pathlib.Path(repo) / ".shell-base.txt"
    if not f.is_file():
        raise SystemExit("no .shell-base.txt in %s" % repo)
    for line in open(f, encoding="utf-8"):
        line = line.strip()
        if len(line) == 40 and all(c in "0123456789abcdef" for c in line):
            return line
    raise SystemExit(".shell-base.txt names no commit")


def blob(repo, rev, path):
    p = subprocess.run(["git", "-C", str(repo), "show", "%s:%s" % (rev, path)],
                       capture_output=True, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise SystemExit("cannot read %s@%s: %s" % (path, rev, p.stderr.decode("utf-8", "replace")))
    return p.stdout.replace(b"\r\n", b"\n")


def new_base(up):
    p = subprocess.run(["git", "-C", str(up), "rev-parse", "origin/main"],
                       capture_output=True, stdin=subprocess.DEVNULL)
    rev = p.stdout.decode().strip()
    if not rev:
        raise SystemExit("cannot resolve origin/main in %s: %s" % (up, p.stderr.decode("utf-8", "replace").strip()))
    return rev


def slug(path):
    return path.replace("/", "__")


def merge(repo, up, out, rel, theirs_rev, write_back):
    base_rev = old_base(repo)
    ours = open(os.path.join(repo, rel), "rb").read().replace(b"\r\n", b"\n")
    base = blob(up, base_rev, rel)
    theirs = blob(up, theirs_rev, rel)

    out.mkdir(parents=True, exist_ok=True)
    s = slug(rel)
    paths = {}
    for tag, data in (("ours", ours), ("base", base), ("theirs", theirs)):
        paths[tag] = os.path.join(out, "%s.%s" % (s, tag))
        open(paths[tag], "wb").write(data)
    merged_path = os.path.join(out, "%s.merged" % s)

    p = subprocess.run(["git", "merge-file", "-p", "--diff3",
                        "-L", "ours(this fork)", "-L", "base(upstream@%s)" % base_rev[:7],
                        "-L", "theirs(upstream@%s)" % theirs_rev[:7],
                        paths["ours"], paths["base"], paths["theirs"]],
                       capture_output=True, stdin=subprocess.DEVNULL)
    open(merged_path, "wb").write(p.stdout)
    conflicts = p.returncode  # >0 means that many conflict hunks
    size = "%d/%d/%d" % (len(ours), len(base), len(theirs))
    print("%-40s conflicts=%-3d sizes(ours/base/theirs)=%s" % (rel, conflicts, size))

    if conflicts == 0 and write_back:
        open(os.path.join(repo, rel), "wb").write(p.stdout)
        print("    written back to the working tree")
    return conflicts


def main():
    ap = argparse.ArgumentParser(
        description="Three-way merge of this fork's patched files against a new upstream base.",
        usage="rel_base.py [options] {try,apply} <path>...")
    ap.add_argument("cmd", choices=("try", "apply"))
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--repo", default=os.environ.get("NXN_REPO") or str(REPO),
                    help="the fork's tree (default: this script's repo)")
    ap.add_argument("--upstream", default=os.environ.get("NXN_UPSTREAM") or str(REPO.parent / "nx-orig"),
                    help="the upstream checkout (default: <repo>/../nx-orig)")
    ap.add_argument("--out", default=os.environ.get("NXN_MERGE") or str(REPO.parent / "tmp" / "merge"),
                    help="where the .ours/.base/.theirs/.merged files go (default: <repo>/../tmp/merge)")
    a = ap.parse_args()
    repo, up, out = pathlib.Path(a.repo), pathlib.Path(a.upstream), pathlib.Path(a.out)
    for name, p in (("fork", repo), ("upstream", up)):
        if not p.is_dir():
            raise SystemExit("%s tree does not exist: %s" % (name, p))

    rev = new_base(up)
    print("new base = %s" % rev)
    print("out      = %s" % out)
    total = 0
    for rel in a.paths:
        total += merge(repo, up, out, rel, rev, a.cmd == "apply")
    print("total conflict hunks: %d" % total)


if __name__ == "__main__":
    main()
