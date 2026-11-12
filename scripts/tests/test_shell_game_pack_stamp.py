"""Which build a package came from: Info.plist's DMCommit (scripts/shell_game_pack.py write_ipa).

The host prints it in its first log line (dm/host/main.c: "build: commit %s, shader chain %s, profile %s"), and a phone log
that says "commit not recorded" cannot be traced to a release -- which is what every library build did: a library package is
the shell's own app plus Frameworks, so nothing of the staged app's plist comes along, and only single-game packages got the
stamp (scripts/nxn_pack.py step_assemble). Synthetic zips only.
"""
import plistlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
import shell_game_pack                                                   # noqa: E402

SHA = 'e6b4543acbd39d0c1aa8e22d1ff8f66eb67a2c50'
BASE_PLIST = {'CFBundleIdentifier': 'org.nxnative.ns2ios', 'CFBundleExecutable': 'DmIOS',
              'CFBundleShortVersionString': '1.0', 'DMShowFPS': 'NO'}


class PackageStamp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.lib = self.root / 'library'
        (self.lib / 'DmGame0000000000000000.framework').mkdir(parents=True)
        (self.lib / 'DmGame0000000000000000.framework' / 'DmGame0000000000000000').write_bytes(b'\0')

    def pack(self, sha):
        """A shell zip with that SOURCE_SHA ('' leaves the key out, as a shell from before the stamp has it)."""
        shell = self.root / ('shell-%s.zip' % (sha[:7] or 'none'))
        pl = dict(BASE_PLIST)
        with zipfile.ZipFile(shell, 'w') as z:
            z.writestr('Payload/DmIOS.app/Info.plist', plistlib.dumps(pl))
            z.writestr('Payload/DmIOS.app/dm/shell_info.txt',
                       'KIND=ios\nGATES=svc\n' + ('SOURCE_SHA=%s\n' % sha if sha else ''))
        ipa = self.root / ('out-%s.ipa' % (sha[:7] or 'none'))
        shell_game_pack.write_ipa(shell, self.lib, ipa)
        with zipfile.ZipFile(ipa) as z:
            return plistlib.loads(z.read('Payload/DmIOS.app/Info.plist'))

    def test_the_commit_goes_in(self):
        pl = self.pack(SHA)
        self.assertEqual(pl.get('DMCommit'), SHA, 'the log line would say "commit not recorded" again')
        self.assertEqual({k: pl[k] for k in BASE_PLIST}, BASE_PLIST, 'the rest of the plist changed')

    def test_a_shell_without_the_stamp_changes_nothing(self):
        pl = self.pack('')
        self.assertNotIn('DMCommit', pl, 'a commit no shell named must not be invented')

    def test_the_shells_own_stamp_wins(self):
        """A shell built after dm/ios/tools/shell_build.sh started stamping carries the upstream base as well; a package
        must not overwrite that with the bare hash."""
        shell = self.root / 'stamped.zip'
        pl = dict(BASE_PLIST); pl['DMCommit'] = 'e6b4543acbd3+base-e2df31ca'
        with zipfile.ZipFile(shell, 'w') as z:
            z.writestr('Payload/DmIOS.app/Info.plist', plistlib.dumps(pl))
            z.writestr('Payload/DmIOS.app/dm/shell_info.txt', 'SOURCE_SHA=%s\n' % SHA)
        ipa = self.root / 'stamped.ipa'
        shell_game_pack.write_ipa(shell, self.lib, ipa)
        with zipfile.ZipFile(ipa) as z:
            got = plistlib.loads(z.read('Payload/DmIOS.app/Info.plist'))['DMCommit']
        self.assertEqual(got, 'e6b4543acbd3+base-e2df31ca', 'the base the fork sits on was dropped from the stamp')


if __name__ == '__main__':
    unittest.main()
