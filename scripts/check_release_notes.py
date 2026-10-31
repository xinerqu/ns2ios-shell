#!/usr/bin/env python3
"""Check .github/release-notes.zh.tsv against the commits a release will describe.

Every release body lists the commit subjects since the last release twice: once as they are, once looked up in the
Chinese table. The lookup is a bare awk over that table, so three failures were silent until someone read a published
page (2026-10-30):

  * a subject with no row ships as "**未翻译**" in the Chinese section -- the release notes say 本版变更 and then print
    English with a note admitting it has no Chinese;
  * two rows with the same subject: awk takes the first, so whichever translation was written later never appears;
  * a row that is not exactly two tab-separated fields (a stray tab in a translation, a line saved as one column) feeds
    awk a truncated or empty Chinese line.

This reports all three before the release is published. Missing rows are a warning, not an error: the scheduled build
runs at 08:00 with nobody watching, and losing the night's shell to an untranslated commit is worse than publishing the
page with the gap named. The other two stop the build, because they make the page wrong rather than incomplete.

    python scripts/check_release_notes.py --from v0.0.0014 --to HEAD
    python scripts/check_release_notes.py --table .github/release-notes.zh.tsv --from origin/main~5 --to HEAD

Exit status: 0 clean, 0 with warnings, 1 on a structural problem.
"""
import argparse
import pathlib
import subprocess
import sys

# This repository's own bookkeeping commits. They are in the range every time it syncs the packer's copies or adds a row
# to this table, and listing them as changes to users says nothing: neither touches anything a reader of the page runs.
# The same two prefixes are dropped by .github/workflows/shell.yml, so the page and this check agree on what shipped.
SKIP_PREFIXES = ('Sync the packer', 'Release notes: ')


def git(*args):
    r = subprocess.run(['git'] + list(args), capture_output=True, text=True, encoding='utf-8', errors='replace',
                       stdin=subprocess.DEVNULL)
    if r.returncode:
        sys.exit('git %s failed: %s' % (' '.join(args), (r.stderr or '').strip()))
    return r.stdout


def read_table(path):
    """[(lineno, subject, translation)] plus the structural problems found while reading it."""
    rows, problems = [], []
    if not path.is_file():
        sys.exit('no release notes table at %s' % path)
    for n, raw in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not raw.strip():
            continue
        parts = raw.split('\t')
        if len(parts) != 2:
            problems.append('line %d: %d tab-separated fields, expected 2 (subject, Chinese)' % (n, len(parts)))
            continue
        subject, chinese = parts[0].strip(), parts[1].strip()
        if not subject or not chinese:
            problems.append('line %d: empty %s' % (n, 'subject' if not subject else 'Chinese'))
            continue
        rows.append((n, subject, chinese))
    seen = {}
    for n, subject, _ in rows:
        seen.setdefault(subject, []).append(n)
    for subject, lines in seen.items():
        if len(lines) > 1:
            problems.append('lines %s: the same subject twice; the lookup takes the first row, so the others never show: %s'
                            % (', '.join(str(x) for x in lines), subject[:70]))
    return rows, problems


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--table', default='.github/release-notes.zh.tsv')
    ap.add_argument('--from', dest='rev_from', default='', help='the previous release tag; omit to skip the commit check')
    ap.add_argument('--to', dest='rev_to', default='HEAD')
    ap.add_argument('--strict', action='store_true', help='treat a missing translation as an error too')
    a = ap.parse_args(argv)

    rows, problems = read_table(pathlib.Path(a.table))
    have = {subject for _, subject, _ in rows}

    missing, total = [], 0
    if a.rev_from:
        out = git('log', '--no-merges', '--pretty=%s', '%s..%s' % (a.rev_from, a.rev_to))
        subjects = [s for s in out.splitlines() if s.strip() and not s.startswith(SKIP_PREFIXES)]
        total = len(subjects)
        missing = [s for s in subjects if s not in have]

    print('%s: %d rows' % (a.table, len(rows)))
    if a.rev_from:
        print('%s..%s: %d commit(s) to describe, %d without a Chinese row'
              % (a.rev_from, a.rev_to, total, len(missing)))
    for p in problems:
        print('  ERROR %s' % p)
    for s in missing:
        print('  WARN no Chinese row: %s' % s)

    if missing:
        print('\nRows to add (subject<TAB>Chinese), then run this again:')
        for s in missing:
            print('%s\t' % s)

    who = 'check_release_notes.py'
    if problems:
        print('\n%s: %d structural problem(s) in the table -- fix them before publishing.' % (who, len(problems)))
        return 1
    if missing and a.strict:
        print('\n%s: %d untranslated commit(s) and --strict is on.' % (who, len(missing)))
        return 1
    if missing:
        print('\n%s: publishing is allowed; the page will name the %d untranslated row(s).' % (who, len(missing)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
