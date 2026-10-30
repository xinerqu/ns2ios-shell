#!/usr/bin/env python3
"""Turn a failed build log into annotations that name the patch to fix, not a line in a 10,000-line log.

There is no Mac here: the Actions page is the only view of a build. A patch that fails to apply already names itself in
the assemble step. A patch that applies and then breaks the compile reports only "Process completed with exit code 1",
and the reason is one line like

    /Users/runner/work/ns2ios-shell/ns2ios-shell/up/dm/gfx/nvn/nvn_mtl.m:11793:12: error: use of undeclared identifier

somewhere in the log of a build that took ten minutes -- and the file is one this fork patches, so what has to change is
`.shell-patches/dm__gfx__nvn__nvn_mtl.m.patch`, which is what an annotation can point at.

    python scripts/annotate_build_errors.py <build log> [<fork root>]

Inside GitHub Actions it writes `::error file=...` lines (shown on the run page) and a job summary; run by hand it just
prints what it would say. Exit status is always 0: this step runs after a failure and must not replace it.
"""
import os
import pathlib
import re
import sys

# <path>:<line>:<col>: error: <message>   (clang / clang -Wall / ld64 with a file, swiftc, dotnet's CS errors)
ERR = re.compile(r'^(?P<path>[^:\s][^:]*\.(?:c|h|m|mm|cc|cpp|swift|py|cs|s|S|plist)):(?P<line>\d+):'
                 r'(?:(?P<col>\d+):)?\s*(?:fatal\s+)?(?P<kind>error|warning):\s*(?P<msg>.*)$')
OTHER = re.compile(r'\b(?:error|Error|ERROR):\s*(?P<msg>.+)$')


def overlay(fork):
    """{path: patch file relative to the fork} for the files this fork patches ("~" entries)."""
    table = {}
    f = fork / '.shell-overlay.txt'
    if not f.is_file():
        return table
    for line in f.read_text(encoding='utf-8').splitlines():
        body = line.split('#', 1)[0].strip()
        if body.startswith('~'):
            path = body[1:].strip().rstrip('/')
            table[path] = '.shell-patches/%s.patch' % path.replace('/', '__')
    return table


def belongs(path, trees):
    """The path relative to a source tree, when the log's path lives under one of them."""
    p = path.replace('\\', '/')
    for tree in trees:
        marker = '/' + tree.strip('/') + '/'
        i = p.rfind(marker)          # the deepest occurrence: a checkout path may itself hold the word "up"
        if i >= 0:
            return p[i + len(marker):]
    return None


def main(argv):
    if not argv:
        sys.exit(__doc__)
    log = pathlib.Path(argv[0])
    fork = pathlib.Path(argv[1]) if len(argv) > 1 else pathlib.Path(__file__).resolve().parents[1]
    if not log.is_file():
        print('no build log at %s (the step failed before writing one)' % log)
        return 0

    patches = overlay(fork)
    trees = [t for t in (os.environ.get('TREE'), 'up', 'fork') if t]
    hits, seen = [], set()
    for raw in log.read_text(encoding='utf-8', errors='replace').splitlines():
        m = ERR.match(raw.strip())
        if m and m.group('kind') == 'error':
            rel = belongs(m.group('path'), trees)
            hits.append((rel or m.group('path'), int(m.group('line')), m.group('msg')[:300]))
            seen.add((rel, m.group('msg')[:120]))
        elif not m:
            o = OTHER.search(raw)
            if o and 'error' in raw.lower() and 'exit code' not in raw:
                hits.append((None, None, raw.strip()[:300]))

    if not hits:
        print('the log carries no line this can point at; the last 30 lines are the next thing to read:')
        for line in log.read_text(encoding='utf-8', errors='replace').splitlines()[-30:]:
            print('  ' + line)
        return 0

    summary, keys = [], set()
    for rel, line, msg in hits:
        key = (rel, line, msg[:80])
        if key in keys:            # the same header error repeats once per translation unit
            continue
        keys.add(key)
        patch = patches.get(rel) if rel else None
        where = patch or rel or '(no file)'
        summary.append('- `%s%s`: %s' % (where, ':%d' % line if line else '', msg))
        print('::error file=%s%s::%s' % (where, (',line=%d' % line) if line else '',
                                        ('%s -- from the patch that owns it. ' % rel if patch else '') + msg))

    top = pathlib.Path(os.environ.get('GITHUB_STEP_SUMMARY', os.devnull)) if os.environ.get('GITHUB_STEP_SUMMARY') \
        else None
    if top:
        with open(top, 'a', encoding='utf-8') as f:
            f.write('### Build errors, mapped to this fork\'s patches\n\n')
            f.write('\n'.join(summary[:20]) + '\n')
            if len(summary) > 20:
                f.write('\n(%d more in the log)\n' % (len(summary) - 20))
    else:
        print('\nwould annotate %d error(s)' % len(keys))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
