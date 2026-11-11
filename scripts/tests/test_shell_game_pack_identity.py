"""The framework's romfs.id (scripts/shell_game_pack.py write_data_identity).

An app that holds several games takes the game data from Documents/ns2ios/games/<tid>/romfs.img, which the player copies in
through the Files app: the framework has to name the image it was packed from, or the app cannot tell another version of the
game from the right one (main.m shell_game_dm_for). A folder has no identity, as upstream writes none for one. Synthetic
images only.
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
import shell_game_pack                                                   # noqa: E402

_spec = importlib.util.spec_from_file_location('romfs_image', REPO / 'dm/tools/romfs_image.py')
assert _spec is not None and _spec.loader is not None
image = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(image)


class DataIdentity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        root = Path(cls.tmp.name)
        for name, size in (('wanted', 40_000), ('other', 40_008)):   # another size, so size alone tells them apart
            d = root / name
            (d / 'sub').mkdir(parents=True)
            (d / 'marker').write_text(name)
            (d / 'sub' / 'blob').write_bytes(bytes(range(256)) * (size // 256))
            image.pack(d, root / (name + '.img'))
        cls.wanted, cls.other = root / 'wanted.img', root / 'other.img'
        cls.fw = root / 'DmGame0000000000000000.framework'
        cls.fw.mkdir()

    def test_the_identity_is_the_image_it_was_packed_from(self):
        line = shell_game_pack.write_data_identity(self.fw, self.wanted, '0000000000000000', '1.2.0', 'v131072')
        written = (self.fw / 'romfs.id').read_text(encoding='ascii')
        self.assertEqual(written, line + '\n')
        self.assertTrue(image.same_identity(written.strip(), image.identity(self.wanted)),
                        'the framework names another image than the data it was packed with')
        fields = image.parse_identity(written)
        self.assertEqual((fields['title'], fields['version'], fields['build']), ('0000000000000000', '1.2.0', 'v131072'))
        self.assertEqual(fields['size'], self.wanted.stat().st_size)

    def test_another_version_is_another_identity(self):
        line = shell_game_pack.write_data_identity(self.fw, self.wanted, 'T', '1.2.0', 'v1')
        self.assertFalse(image.same_identity(line, image.identity(self.other)),
                         'the app would accept a romfs.img of another version')

    def test_a_folder_leaves_no_identity(self):
        (self.fw / 'romfs.id').write_text('DMROMFSID1 title=T version=1 build=v size=1 index=0000000000000000\n')
        line = shell_game_pack.write_data_identity(self.fw, self.wanted.parent / 'no-such.img', 'T', '1', 'v')
        self.assertEqual(line, '')
        self.assertFalse((self.fw / 'romfs.id').exists(),
                         'a stale identity would refuse the folder form it was packed with')

    def test_an_image_that_cannot_be_identified_is_refused(self):
        broken = Path(self.tmp.name) / 'broken.img'
        broken.write_bytes(b'not a romfs image at all')
        with self.assertRaises(ValueError):
            shell_game_pack.write_data_identity(self.fw, broken, 'T', '1', 'v')


if __name__ == '__main__':
    unittest.main()
