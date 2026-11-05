"""The packer asks nx_extract for the RomFS image, so the game's bytes are written once and not twice.

Why: a package has to carry the game files as one `romfs.img` (hle_fs.c reads the image; a loose tree is hundreds of
thousands of files iOS copies one by one at install). This route used to unpack that tree first and pack it into an image
afterwards, which reads and writes the whole game a second time: 15 minutes for Tears of the Kingdom, plus a minute to
delete the tree (measured 2026-11-06). nx_extract writes the image on the way out of the NCA (`--romfs-image`, the option
upstream's build_ipa_from_nsp.py:1241 has always passed), and one flag is all this route was missing.

The flag is the whole fix, so it is the thing worth pinning: dropping it does not break anything visibly -- the pack still
ends with a valid image, made the slow way.

`step_extract` also warns when the tree comes back (an nx_extract too old to know the option), which is the other half:
a silent fallback would look exactly like a fast pack that simply took 15 minutes.
"""
import importlib.util
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]      # scripts/tests/ -> the repository root
_spec = importlib.util.spec_from_file_location('nxn_pack', REPO / 'scripts' / 'nxn_pack.py')
nxn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nxn)


class TheGameIsWrittenAsAnImage(unittest.TestCase):
    def test_extract_names_the_image(self):
        cmd = nxn.extract_cmd('nx', 'prod.keys', pathlib.Path('base.nsp'), pathlib.Path('update.nsp'),
                              pathlib.Path('out'))
        self.assertEqual(cmd, ['nx', 'prod.keys', pathlib.Path('base.nsp'), pathlib.Path('update.nsp'),
                               pathlib.Path('out'), '--romfs-image'])

    def test_a_base_game_alone_still_gets_the_image(self):
        cmd = nxn.extract_cmd('nx', 'prod.keys', pathlib.Path('base.nsp'), None, pathlib.Path('out'))
        self.assertEqual(cmd[3], '-')                       # no update: nx_extract takes a dash
        self.assertIn('--romfs-image', cmd)

    def test_a_damaged_package_keeps_both(self):
        # --allow-damaged writes the bad files as read and marks them in the image's index; nx_extract strips both
        # options off before it reads its positional arguments, so their order does not matter.
        cmd = nxn.extract_cmd('nx', 'k', 'base.nsp', 'upd.nsp', 'out', allow_damaged=True)
        self.assertIn('--romfs-image', cmd)
        self.assertIn('--allow-damaged', cmd)


class TheDlcIsWrittenAsAnImage(unittest.TestCase):
    def test_aoc_names_the_image(self):
        cmd = nxn.aoc_cmd('nx', 'k', pathlib.Path('dlc.nsp'), pathlib.Path('aoc/1.partial'))
        self.assertEqual(cmd, ['nx', 'k', '--aoc', pathlib.Path('dlc.nsp'), pathlib.Path('aoc/1.partial'),
                               '--romfs-image'])

    def test_aoc_keeps_allow_damaged_after_the_title_id_slot(self):
        # nx_extract --aoc's positional arguments are <dlc.nsp> <out> [<title ID>]: appending an option must not land in
        # the title ID's place, which is why both are stripped before the arguments are read.
        cmd = nxn.aoc_cmd('nx', 'k', 'dlc.nsp', 'aoc/1.partial', allow_damaged=True)
        self.assertEqual(cmd[5:], ['--romfs-image', '--allow-damaged'])


if __name__ == '__main__':
    unittest.main()
