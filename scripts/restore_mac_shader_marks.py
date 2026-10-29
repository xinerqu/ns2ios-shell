#!/usr/bin/env python3
"""Put needs_mac_shaders back on the five Oodle/LZ5 profiles after a packer sync has dropped it.

`dm/tools/packer_sync.py --sync` copies upstream's `game.tsv` over this fork's for every file the overlay does not
list, and upstream has no `needs_mac_shaders` key (it is ours: `supported_list.py` marks those rows from it, and
`docs/SUPPORTED.md` says 暂不支持 for them). So every build's "Keep the packer's copies level with upstream" step
dropped the key, committed the drop, and the next local base move had to restore it by hand -- twice so far
(37f6fbc on 2026-10-28, and again in the 2026-10-29 base move).

This script is called by that workflow step right after the sync. It re-adds the key only when it is missing, so
whatever else upstream changed in the same file moves on untouched. Run it by hand for the same reason after a
`packer_sync.py --sync` in a local base move.

    python3 scripts/restore_mac_shader_marks.py [repo root]
"""

import pathlib
import sys

# The five whose shaders sit in Oodle-, LZ5- or PKZ-compressed archives: only the game's own decoder unpacks those, and
# that decoder runs in the Mac host (dm/host/hle_oodle.c), so this fork cannot pack them. Keep this list in step with
# docs/SUPPORTED.md's 暂不支持 rows.
TITLE_IDS = ('01004A4010FEA000',   # Bayonetta 3
             '01005270232F2000',   # Octopath Traveler 0
             '0100606026F12000',   # Final Fantasy Resonance
             '01006C300E9F0000',   # DRAGON QUEST XI S
             '0100F43008C44000')   # Pokemon Legends: Z-A

BLOCK = (b'# A Mac is needed once for this one: the shaders sit in Oodle- or LZ5-compressed archives, and only the game own decoder\n'
         b'# unpacks those (shader_split.py oodle(), dm/host/hle_oodle.c). The Windows packer stops and says so; supported_list.py\n'
         b'# marks the row from this key.\n'
         b'needs_mac_shaders\t1\n\n')


def main(argv):
    root = pathlib.Path(argv[0] if argv else '.')
    added = []
    for tid in TITLE_IDS:
        p = root / 'dm' / 'games' / tid / 'game.tsv'
        if not p.is_file():
            continue
        data = p.read_bytes()
        if b'needs_mac_shaders' in data:
            continue
        out = []
        for line in data.splitlines(True):
            out.append(line)
            if line.startswith((b'modules\t', b'modules ')):
                out.append(BLOCK)
        if len(out) == len(data.splitlines(True)):   # no modules line: append, so the key is never silently lost
            if out and not out[-1].endswith(b'\n'):
                out.append(b'\n')
            out.append(BLOCK)
        p.write_bytes(b''.join(out))
        added.append(tid)
    if added:
        print('needs_mac_shaders restored on %s' % ', '.join(added))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
