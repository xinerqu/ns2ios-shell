# -*- coding: utf-8 -*-
"""Check that each merged file is upstream's new version plus exactly our patch -- nothing upstream added is lost.

Why: a three-way merge can report 0 conflicts and still drop a line upstream just wrote, so every base move has to be
judged on the merged output, not on the conflict count.

For every file: diff(theirs, merged). Every line removed relative to upstream's new version must be a line our patch
already removed, and every line added must be a line our patch adds. A line upstream just wrote that the merge dropped
shows up as a removal that is in no patch -- that is the failure this looks for.

The reference patch is the one in `.shell-patches/` (still generated against the OLD base), which is exactly "our patch".

usage: python scripts/basemove/check_merge.py [slug ...]
         no slug: check every <slug>.merged in the merge directory
         --merge-dir / env NXN_MERGE      (default: <repo>/../tmp/merge)
         --patches-dir / env NXN_PATCHES  (default: <repo>/.shell-patches)

Exit code 1 if anything could not be checked or has lines to explain, 0 only when every merged file was checked clean.
"""
import argparse
import difflib
import os
import pathlib
import sys

# scripts/basemove/check_merge.py -> the fork's root
REPO = pathlib.Path(__file__).resolve().parents[2]


def lines(path):
    return path.read_text(encoding='utf-8', errors='surrogateescape').splitlines()


def main():
    ap = argparse.ArgumentParser(description="Judge a base move's merged files against upstream's new version.")
    ap.add_argument('slugs', nargs='*')
    ap.add_argument('--merge-dir', default=os.environ.get('NXN_MERGE') or str(REPO.parent / 'tmp' / 'merge'))
    ap.add_argument('--patches-dir', default=os.environ.get('NXN_PATCHES') or str(REPO / '.shell-patches'))
    a = ap.parse_args()
    M, P = pathlib.Path(a.merge_dir), pathlib.Path(a.patches_dir)

    if a.slugs:
        names = a.slugs
    else:
        names = sorted(p.name[:-len('.merged')] for p in M.glob('*.merged'))
    if not names:
        print('no *.merged in %s' % M)
        print('checked 0 file(s), 0 with a problem -- nothing was checked; run rel_base.py try first')
        return 1

    print('merge dir   = %s' % M)
    print('patches dir = %s\n' % P)
    problems = 0
    review = 0
    for name in names:
        need = [(M / (name + '.merged'), 'merged'), (M / (name + '.theirs'), 'theirs'), (P / (name + '.patch'), 'patch')]
        missing = ['%s (looked in %s)' % (what, p.parent) for p, what in need if not p.is_file()]
        if missing:
            # A file was merged this round but cannot be judged: that base move was not checked, so it is a problem.
            print('SKIP %s: missing %s' % (name, '; '.join(missing)))
            problems += 1
            continue
        merged, theirs, patch = (lines(p) for p, _ in need)
        ours_minus = {l[1:] for l in patch if l.startswith('-') and not l.startswith('---')}
        ours_plus = {l[1:] for l in patch if l.startswith('+') and not l.startswith('+++')}
        lost, extra = [], []
        for l in difflib.unified_diff(theirs, merged, lineterm='', n=0):
            if l.startswith('---') or l.startswith('+++') or l.startswith('@@'):
                continue
            if l.startswith('-'):            # upstream has it, merged does not
                if l[1:] not in ours_minus:
                    lost.append(l[1:])
            elif l.startswith('+'):          # merged has it, upstream does not
                if l[1:] not in ours_plus:
                    extra.append(l[1:])
        review += len(lost) + len(extra)
        if lost or extra:
            problems += 1
        print('%s: upstream lines lost by the merge = %d, unexplained additions = %d' % (name, len(lost), len(extra)))
        for l in lost[:8]:
            print('    LOST  ' + l[:150])
        for l in extra[:8]:
            print('    EXTRA ' + l[:150])

    print('\nchecked %d file(s), %d with a problem (%d line(s) to review by hand; a line both sides rewrote is expected here)'
          % (len(names), problems, review))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
