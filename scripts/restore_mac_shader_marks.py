#!/usr/bin/env python3
"""Put needs_mac_shaders back on the Oodle/LZ5 profiles after a packer sync has dropped it.

`dm/tools/packer_sync.py --sync` copies upstream's `game.tsv` over this fork's for every file the overlay does not
list, and upstream has no `needs_mac_shaders` key (it is ours: `supported_list.py` marks those rows 暂不支持 from it).
So every build's "Keep the packer's copies level with upstream" step dropped the key, committed the drop, and the next
local base move had to restore it by hand -- twice so far (37f6fbc on 2026-10-28, and again in the 2026-10-29 base move).

Which profiles carry the key is read from HEAD (`git show HEAD:dm/games/<tid>/game.tsv`), not from a list kept here,
because after the sync the working tree holds upstream's copy and no longer has it. That way the next Oodle/LZ5 game
upstream adds needs no edit in this file: the build after the sync restores it from the profile that had it before.

This script is called by that workflow step right after the sync. It re-adds the key only when it is missing, so
whatever else upstream changed in the same file moves on untouched. Run it by hand for the same reason after a
`packer_sync.py --sync` in a local base move.

    python3 scripts/restore_mac_shader_marks.py [repo root]
"""

import pathlib
import subprocess
import sys

KEY = b'needs_mac_shaders'
PROFILES = 'dm/games'

# No packer has a path for this one yet: the shaders sit in Oodle-, LZ5- or PKZ-compressed archives, and only the game
# own decoder unpacks those (shader_split.py oodle(), dm/host/hle_oodle.c) -- arm64 code the Mac host runs, and this
# fork has no Mac build step at all. So it fails on Windows, Linux and macOS alike; supported_list.py marks the row
# from this key.
BLOCK = (b'# No packer has a path for this one yet: the shaders sit in Oodle-, LZ5- or PKZ-compressed archives, and only the game\n'
         b'# own decoder unpacks those (shader_split.py oodle(), dm/host/hle_oodle.c) -- arm64 code the Mac host runs, and this\n'
         b'# fork has no Mac build step at all. So it fails on Windows, Linux and macOS alike; supported_list.py marks the row\n'
         b'# from this key.\n'
         b'needs_mac_shaders\t1\n\n')


def git(root, *args):
    """stdout of `git -C <root> <args>`; -C is what lets this run from any cwd, as the CI step and a base move both do."""
    p = subprocess.run(['git', '-C', str(root)] + list(args),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode:
        raise RuntimeError('git %s failed: %s' % (' '.join(args), p.stderr.decode('utf-8', 'replace').strip()))
    return p.stdout


def marked_profiles(root):
    """Every HEAD profile that carries the key: the record of what the sync has just dropped."""
    listing = git(root, 'ls-tree', '-r', '--name-only', 'HEAD', PROFILES).decode('utf-8', 'replace').split()
    marked = []
    for path in (p for p in listing if p.endswith('/game.tsv')):
        if KEY in git(root, 'show', 'HEAD:%s' % path):
            marked.append(path)
    return marked


def main(argv):
    root = pathlib.Path(argv[0] if argv else '.')
    try:
        marked = marked_profiles(root)
    except (RuntimeError, OSError) as e:
        print('[x] cannot read the profiles in HEAD: %s' % e)
        return 1
    # No key anywhere in HEAD means the record itself is gone -- someone committed the drop. Doing nothing here would
    # leave those games looking supported, and they pack into an IPA that cannot draw, so refuse instead.
    if not marked:
        print('[x] no profile in HEAD carries %s: there is nothing to restore from.' % KEY.decode())
        print('    The key was dropped from git itself. Put it back where it belongs (git log -S %s) before'
              % KEY.decode())
        print('    running this again -- restoring nothing would mark those games supported.')
        return 1

    added, absent = [], []
    for path in marked:
        p = root / path
        if not p.is_file():
            absent.append(path)                 # upstream deleted the profile: nothing here to mark
            continue
        data = p.read_bytes()
        if KEY in data:
            continue
        out, anchored = [], False
        for line in data.splitlines(True):
            out.append(line)
            if not anchored and line.startswith((b'modules\t', b'modules ')):
                out.append(BLOCK)
                anchored = True
        if not anchored:                        # no modules line: append, so the key is never silently lost
            if out and not out[-1].endswith(b'\n'):
                out.append(b'\n')
            out.append(BLOCK)
        p.write_bytes(b''.join(out))
        added.append(path)

    for path in absent:
        print('[!] HEAD has %s but the working tree does not: skipped, look at whether the game is gone' % path)
    if added:
        print('needs_mac_shaders restored on %s' % ', '.join(a.split('/')[-2] for a in added))
    else:
        print('needs_mac_shaders already on all %d profiles -- nothing to restore' % (len(marked) - len(absent)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
