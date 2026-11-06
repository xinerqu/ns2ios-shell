#!/usr/bin/env python3
"""Move this fork's patch base up to upstream's HEAD, the whole seven-step drill in one command.

Why: a push builds the shell from upstream's HEAD *at that moment* plus this fork's overlay, so upstream committing
between our push and CI's clone turns the build red in `Assemble the source tree` with "differs from upstream and no
overlay line names it". That happened three times in four hours on 2026-11-06, each time a manual walk through the same
steps. This is that walk, and it stops where a human is needed (a conflict) instead of guessing.

    python scripts/basemove/catch_up.py            # do everything up to staging, then say what to run
    python scripts/basemove/catch_up.py --commit   # also commit (never pushes)

What it does NOT do, deliberately:
  * resolve a conflict. It stops, names the files and the hunks, and leaves the .merged files for a human -- every
    conflict so far has been "upstream rewrote a line that reads a compiled profile constant we replace with a runtime
    accessor", which needs reading, not a rule.
  * push. CI runs on push, and a red build should never come from a script's timing.
  * write the Chinese release-notes row. That is a sentence about what upstream changed, in the words a player reads.

What it does that used to be done by hand and forgotten: the semantic report (upstream's new lines that touch
DM_GAME_* / DMSH_CHAIN / Mac-only calls -- the ones that silently do nothing in a game-free shell), copying upstream's
new files in the areas this fork tracks, regenerating the supported-games list, and running all three guards.
"""
import argparse
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent            # scripts/basemove/ -> the repository
UP = REPO.parent / 'nx-orig'
PY = sys.executable


def run(cmd, cwd=REPO, check=True, quiet=False):
    r = subprocess.run([str(c) for c in cmd], cwd=str(cwd), capture_output=True, text=True)
    if check and r.returncode:
        sys.exit('failed: %s\n%s%s' % (' '.join(str(c) for c in cmd), r.stdout, r.stderr))
    if not quiet and r.stdout.strip():
        print(r.stdout.rstrip())
    return r


def upstream_git(*args, **kw):
    return run(['git', '-c', 'http.sslBackend=openssl', '-c', 'http.schannelCheckRevoke=false'] + list(args),
               cwd=UP, **kw)


def base():
    m = re.search(r'^([0-9a-f]{40})', (REPO / '.shell-base.txt').read_text(encoding='utf-8'), re.M)
    if not m: sys.exit('.shell-base.txt names no commit')
    return m.group(1)


def patched():
    """The "~" entries: files upstream has and this fork changes."""
    out = []
    for line in (REPO / '.shell-overlay.txt').read_text(encoding='utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if line.startswith('~'): out.append(line[1:].strip())
    return out


def main():
    ap = argparse.ArgumentParser(description='Move the patch base up to upstream HEAD.')
    ap.add_argument('--commit', action='store_true', help='commit the result (it never pushes)')
    ap.add_argument('--upstream', default=str(UP))
    a = ap.parse_args()

    old = base()
    print('patch base here: %s' % old)
    upstream_git('fetch', 'origin')
    head = upstream_git('rev-parse', 'origin/main', quiet=True).stdout.strip()
    print('upstream HEAD:   %s' % head)
    if head == old:
        print('level with upstream: nothing to do')
        return 0
    subject = upstream_git('log', '-1', '--format=%s', head, quiet=True).stdout.strip()
    count = upstream_git('rev-list', '--count', '%s..%s' % (old, head), quiet=True).stdout.strip()
    print('behind by %s commit(s); newest: %s' % (count, subject[:100]))
    upstream_git('checkout', '--detach', head, quiet=True)

    changed = set(upstream_git('diff', '--name-only', old, head, quiet=True).stdout.split())
    mine = [p for p in patched() if p in changed]
    print('\n%-2d of this fork\'s %d patched files changed upstream' % (len(mine), len(patched())))
    for p in mine: print('   %s' % p)

    # ---- the semantic report: new upstream lines that do nothing in a game-free shell
    added = upstream_git('diff', old, head, quiet=True).stdout.splitlines()
    hits = [l for l in added if l.startswith('+') and re.search(r'DM_GAME_[A-Z_]+|DMSH_CHAIN', l)]
    mac = [l for l in added if l.startswith('+') and re.search(r'os\.pread|os\.fork|ctypes\.CDLL\(None\)|fcntl|fclonefileat', l)]
    print('\nnew upstream lines that read a compiled profile constant or the chain version: %d' % len(hits))
    for l in hits[:12]: print('   %s' % l[1:].strip()[:150])
    if len(hits) > 12: print('   ... %d more' % (len(hits) - 12))
    if mac:
        print('\nnew upstream lines that are Mac-only: %d' % len(mac))
        for l in mac[:8]: print('   %s' % l[1:].strip()[:150])
    if hits:
        print('   (each of these needs to go through a runtime accessor in a game-free shell, or be shown harmless)')

    # ---- merge the patched files upstream touched
    if mine:
        print('\nthree-way merge:')
        run([PY, HERE / 'rel_base.py', 'try', '--repo', REPO, '--upstream', a.upstream] + mine)
        merged = REPO.parent / 'tmp' / 'merge'
        bad = [p for p in mine if '<<<<<<<' in (merged / (p.replace('/', '__') + '.merged')).read_text(errors='replace')]
        if bad:
            print('\nSTOP: %d file(s) conflict. Resolve these by hand, then copy every .merged back and rerun with' % len(bad))
            print('      --commit (rel_base apply re-merges and would throw the resolution away):')
            for p in bad: print('   %s' % p)
            return 2
        for p in mine:
            src = merged / (p.replace('/', '__') + '.merged')
            (REPO / p).write_bytes(src.read_bytes())
        print('   merged clean, written back: %d file(s)' % len(mine))

    (REPO / '.shell-base.txt').write_text(head + '\n', encoding='utf-8')
    print('\npatch base -> %s' % head)

    run([PY, REPO / 'dm' / 'ios' / 'tools' / 'make_shell_patch.py', '--all'])
    run([PY, HERE / 'sync_rest.py', a.upstream])
    run([PY, REPO / 'scripts' / 'restore_mac_shader_marks.py', REPO])

    # ---- upstream's new files in the areas this fork tracks
    r = run([PY, REPO / 'scripts' / 'check_tree.py', REPO, a.upstream], check=False)
    missing = sorted(set(re.findall(r'PROBLEM: only upstream has it and this fork tracks that area: (\S+)', r.stdout)))
    for p in missing:
        dst = REPO / p
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((pathlib.Path(a.upstream) / p).read_bytes())
    if missing: print('copied %d file(s) upstream added' % len(missing))

    if any(p.startswith('dm/games/') for p in upstream_git('diff', '--name-only', old, head, quiet=True).stdout.split()):
        run([PY, REPO / 'dm' / 'tools' / 'supported_list.py'])

    # ---- the three guards, in the order CI runs them
    print('\n--- guards')
    pre = run([PY, REPO.parent / 'preflight.py'], check=False)
    tree = run([PY, REPO / 'scripts' / 'check_tree.py', REPO, a.upstream], check=False)
    calls = run([PY, REPO / 'scripts' / 'check_reference_calls.py', REPO, a.upstream], check=False)
    want = len(patched())
    ok = ('%d of %d applied' % (want, want)) in pre.stdout and 'byte for byte' in pre.stdout
    ok = ok and 'every difference is declared' in tree.stdout
    ok = ok and 'either passed here or explained' in calls.stdout
    print('preflight/check_tree/check_reference_calls: %s' % ('green' if ok else 'NOT green -- read above'))
    if not ok: return 3

    # ---- stage, and say what is left
    run(['git', 'add', '-A', ':!dm/gfx/nvn/dmsh_chain.h', ':!dm/gfx/tools/dmsh_chain.lock'], cwd=REPO)
    print('\nstaged. What is left is yours:')
    print('  1. a Chinese row in .github/release-notes.zh.tsv for the subject below, then')
    print('     python scripts/check_release_notes.py --from <last commit> --to HEAD')
    print('  2. the commit message: "Move the patch base to %s: <what it changes>"' % head[:8])
    print('     body: the %s commit(s), which of the %d patched files moved, each conflict and the accessor it kept,' % (count, len(patched())))
    print('     and any DM_GAME_* line from the report above that now needs an accessor.')
    if a.commit:
        msg = 'Move the patch base to %s: %s' % (head[:8], subject.split(':')[0][:80])
        run(['git', 'commit', '-q', '-m', msg, '-m',
             'Upstream moved %s commit(s) ahead while this tree carried %s.\n\n%s' % (count, old[:8], subject)],
            cwd=REPO)
        print('\ncommitted (not pushed): %s' % msg)
    return 0


if __name__ == '__main__':
    sys.exit(main())
