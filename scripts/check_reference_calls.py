#!/usr/bin/env python3
"""Every option upstream's build asks nx_extract for, against the ones this packer passes.

Why this exists. Upstream's `scripts/build_ipa_from_nsp.py` and this packer unpack the same NSPs. On 2026-11-06 this route
was found to unpack a whole loose RomFS and then pack it into the image afterwards -- reading and writing the game a
second time, 15 minutes plus a minute of deleting for Tears of the Kingdom -- while upstream had passed
`nx_extract --romfs-image` all along and paid neither. What was checked at the time was the *shape* of the package (one
`romfs.img`, N entry count, an IPA with a few dozen entries); the shape was right, so nothing looked wrong. The cost was
in a flag.

So shape checks cannot catch this class, and neither can a diff of the reference pipeline's output formats. The options
themselves can: this script compares the two option sets. It reads upstream's calls out of its own source (not a copy, so
it follows upstream), asks this packer for the arguments it builds (`extract_cmd`, `aoc_cmd`, and the `--keys` check), and
lists what upstream uses and this route does not. An option that is neither passed nor explained below fails.

Adding an entry to EXPLAINED is the decision this script exists to force: "we do not need it, because ..." is a sentence
someone has to write, once, in the open.

Usage: check_reference_calls.py [repo] [upstream]      (default: . and ../nx-orig)
"""
import importlib.util
import pathlib
import re
import sys

# Options upstream passes that this route deliberately does not, and why. An option that disappears from upstream's
# source stops being listed; one that appears there without an entry here fails the check.
EXPLAINED = {
    '--manifest': 'upstream\'s survey of a game with no profile (it lists the RomFS and pulls the NROs); ours is '
                  'profile_guess.build_sources(), which reads the image through dm/tools/romfs.py',
    '--html': 'NOT DONE: the offline pages of a game\'s HtmlDocument NCAs, which the host shows over the game '
              '(hle_web.c, <App>/html). TODO 15 -- nothing in our packages carries them',
    '--app': 'a package holding several games, and the program of a multi-program application: multi-program games are '
             'not supported (TODO 10), so every call names one package',
    '--program': 'the program inside a multi-program application: same as --app',
    '--title': 'reads a package\'s title ID. This route takes it from the file name and the ticket, like upstream\'s '
               'own folder scan, and reads the profile from the extracted build IDs',
    '--contents': 'the content metas of a merged package (base + update + DLC in one file). Not supported here: such a '
                  'package is packed one part at a time by the user, or not at all',
}


def options(text):
    """The --options in a source, as a set. Both sides are Python source read as text, so this is a scan, not a parse."""
    return {m.rstrip('=') for m in re.findall(r"--[a-z][a-z-]+=?", text)}


def upstream_options(path):
    """The options upstream's script passes to nx_extract: inside `run([nx ...])` calls and `extract_flags`."""
    text = path.read_text(encoding='utf-8', errors='replace')
    calls = re.findall(r'run\(\[nx[^)]*\)', text, re.S) + re.findall(r'def extract_flags.*?\n\n', text, re.S)
    found = set()
    for c in calls:
        found |= options(c)
    # --keys / --title / --contents / --manifest / --html are passed as bare strings in those calls; keep only the ones
    # that belong to nx_extract at all, so a run() of another tool cannot add noise.
    return found - {'--verbose'}


def our_options(nxn):
    """What this packer passes: the two argument builders it has, plus its keys check."""
    texts = []
    for cmd in (nxn.extract_cmd('nx', 'keys', 'base', 'upd', 'out'),
                nxn.extract_cmd('nx', 'keys', 'base', 'upd', 'out', allow_damaged=True),
                nxn.aoc_cmd('nx', 'keys', 'dlc', 'out')):
        texts += [a for a in cmd if isinstance(a, str) and a.startswith('--')]
    source = (pathlib.Path(nxn.__file__).read_text(encoding='utf-8', errors='replace'))
    texts += re.findall(r"'(--keys|--title|--contents)'", source)
    return {t.rstrip('=') for t in texts}


def main(argv):
    repo = pathlib.Path(argv[1] if len(argv) > 1 else '.').resolve()
    up = pathlib.Path(argv[2] if len(argv) > 2 else '../nx-orig').resolve()
    script = up / 'scripts' / 'build_ipa_from_nsp.py'
    if not script.is_file():
        sys.exit('[RED] no upstream script at %s: fetch ../nx-orig first' % script)
    spec = importlib.util.spec_from_file_location('nxn_pack', repo / 'scripts' / 'nxn_pack.py')
    nxn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nxn)
    want, have = upstream_options(script), our_options(nxn)
    missing = sorted(want - have)
    unexplained = [o for o in missing if o not in EXPLAINED]
    print('upstream %s: %d option(s)' % (script.relative_to(up), len(want)))
    print('this packer: %d option(s)%s' % (len(have), '' if not (want - have) else ', of which %d upstream also uses' % len(want & have)))
    for o in missing:
        print('  %-14s upstream-only: %s' % (o, EXPLAINED.get(o, '[RED] no explanation')))
    gone = sorted(k for k in EXPLAINED if k not in want)
    for o in gone:
        print('  %-14s explained here, but upstream no longer passes it: drop the entry' % o)
    if unexplained:
        print('[RED] %d option(s) upstream passes and this route neither passes nor explains: %s'
              % (len(unexplained), ', '.join(unexplained)))
        print('     pass it (and a test pinning it), or add a line to EXPLAINED in %s' % pathlib.Path(__file__).name)
        return 1
    if gone:
        print('[RED] an explanation outlived the option: remove it from EXPLAINED')
        return 1
    print('every nx_extract option upstream passes is either passed here or explained')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
