#!/usr/bin/env python3
"""Every difference between this fork and an upstream checkout has to be declared in .shell-overlay.txt.

Why: CI assembles its tree as "upstream HEAD + this fork's files", so a change this fork carries in a file that no overlay
line names never reaches the shell. Nothing fails: the app builds, boots, and behaves as upstream does. The one guard that
existed was `applied N of want` in the workflow, and it counts the "~" lines after the fact -- a file that was edited but
never added there is not in that count at all. This reads both trees and compares the three sets.

What it needs each difference to be:
  ~ path    patched onto upstream's copy: must differ from upstream, and the patch must exist
  + path    copied onto the upstream tree as it is: must be in this fork
  - path    dropped: upstream has it, this fork does not carry it (it must be gone from this fork too)
  ! path    local only: not part of the assembled tree, and not drift either (this fork's own docs and tools, upstream's
            Mac front end, upstream's own CI, the screenshots the per-game READMEs embed)
Anything else is a problem: a file that differs but is in no line, a file only this fork has that no line declares (the
copy upstream deleted, kept by accident), or a file only upstream has in an area this fork tracks (a base move that left
something behind).

    python scripts/check_tree.py [fork] [upstream]        # defaults: this repo, ../nx-orig
    python scripts/check_tree.py --overlay other.txt
"""
import argparse
import os
import pathlib
import sys

SKIP_DIRS = {'.git', '__pycache__', 'obj', 'bin', 'dmsh_xlate_bin'}
# Made per profile or per machine at build time (packer_sync.py SKIP_NAMES): not sources, and a packing run rewrites them.
# shader_facts.tsv is one of these too: a profile ships it as a one-line stub and the shader step fills it in, which is
# why this fork never commits the filled one (packer_sync would bring the stub back on the next base move).
# Only an undeclared copy is skipped -- the placeholder profile's own dmsh_chain.h is a "+" entry and has to be there.
SKIP_NAMES = {'c1_full.tsv', 'cb_extent.tsv', 'shader_facts.tsv', 'dmsh_chain.h', 'dmsh_chain.lock'}


def read_overlay(path):
    """(patched, added, dropped, local_only): path prefixes, each without its trailing slash."""
    out = {'~': [], '+': [], '-': [], '!': []}
    for line in pathlib.Path(path).read_text(encoding='utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if len(line) < 2 or line[0] not in out: continue
        p = line[1:].strip().rstrip('/')
        if p: out[line[0]].append(p)
    return out['~'], out['+'], out['-'], out['!']


def files(root):
    """{relative path: bytes}, LF-normalised: this checkout is CRLF (core.autocrlf) while upstream's is LF."""
    root = pathlib.Path(root)
    seen = {}
    for d, dirs, names in os.walk(str(root)):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for n in names:
            p = pathlib.Path(d) / n
            rel = p.relative_to(root).as_posix()
            try: seen[rel] = p.read_bytes().replace(b'\r\n', b'\n')
            except OSError: pass          # a dangling link or a file locked by a running build
    return seen


def volatile(rel):
    """A build writes these per profile or per machine; an undeclared copy is not a difference to judge."""
    return rel.rsplit('/', 1)[-1] in SKIP_NAMES


def under(rel, prefixes):
    return any(rel == p or rel.startswith(p + '/') for p in prefixes)


def main(argv):
    ap = argparse.ArgumentParser(description="Judge every difference this fork has from upstream against .shell-overlay.txt")
    ap.add_argument('fork', nargs='?', default=str(pathlib.Path(__file__).resolve().parents[1]))
    ap.add_argument('upstream', nargs='?', default=str(pathlib.Path(__file__).resolve().parents[2] / 'nx-orig'))
    ap.add_argument('--overlay', default=None, help="default: <fork>/.shell-overlay.txt")
    a = ap.parse_args(argv)

    fork_dir = pathlib.Path(a.fork)
    overlay = pathlib.Path(a.overlay or fork_dir / '.shell-overlay.txt')
    if not overlay.is_file(): sys.exit('no overlay file: %s' % overlay)
    if not pathlib.Path(a.upstream).is_dir(): sys.exit('upstream tree does not exist: %s' % a.upstream)
    patched, added, dropped, local = read_overlay(overlay)

    ours, theirs = files(fork_dir), files(a.upstream)
    problems, notes = [], []

    missing_patch = [p for p in patched if not (fork_dir / '.shell-patches' / (p.replace('/', '__') + '.patch')).is_file()]
    for p in missing_patch:
        problems.append('no patch for the "~" entry: %s (make_shell_patch.py <path>)' % p)

    for p in patched:
        if p not in ours: problems.append('"~" entry is not in this fork: %s' % p); continue
        if p not in theirs: problems.append('"~" entry is not upstream\'s file: %s (a file upstream added belongs under "+")' % p); continue
        if ours[p] == theirs[p]:
            problems.append('"~" entry is identical to upstream, so its patch does nothing: %s (drop the entry and the patch)' % p)

    for p in added:
        if p not in ours and not any(r.startswith(p + '/') for r in ours):
            problems.append('"+" entry is not in this fork: %s' % p)

    for p in dropped:
        hit = [r for r in ours if r == p or r.startswith(p + '/')]
        if hit: problems.append('"-" entry still in this fork: %s (git rm it)' % p)

    for rel, data in sorted(ours.items()):
        if under(rel, local) or under(rel, patched) or under(rel, dropped): continue
        if rel in theirs:
            if data != theirs[rel] and not under(rel, added) and not volatile(rel):
                problems.append('differs from upstream and no overlay line names it: %s -- CI builds upstream\'s copy, so '
                                'this change never reaches the shell ("~" and a patch, or "!" if the tree does not need it)' % rel)
        elif not under(rel, added) and not volatile(rel):
            problems.append('only this fork has it and no overlay line declares it: %s (the copy upstream deleted? git rm it, '
                            'or "+" if the shell tree needs it, or "!" if it is local only)' % rel)

    for rel in sorted(theirs):
        if rel in ours or under(rel, local) or under(rel, dropped) or volatile(rel): continue
        if under(rel, added): continue
        problems.append('only upstream has it and this fork tracks that area: %s (sync it, or mark the path "!" if it '
                        'stays out)' % rel)

    print('overlay: %d patched, %d added, %d dropped, %d local-only' % (len(patched), len(added), len(dropped), len(local)))
    print('trees:   %d files here, %d upstream' % (len(ours), len(theirs)))
    for n in notes: print('note:    ' + n)
    for p in problems: print('PROBLEM: ' + p)
    print('\n%s' % ('%d problem(s): a difference reaches the shell only through the overlay' % len(problems) if problems
                    else 'every difference is declared'))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
