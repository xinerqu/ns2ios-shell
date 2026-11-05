"""A Switch 2 dump is read the way upstream's pack_dump.py reads one, and no key is involved.

Lives under scripts/ and not dm/tools: the five folders packer_sync owns must stay upstream's, and a test of
ours beside theirs reads as a file that went missing upstream.

Why: the dump route is the only way a Switch 2 title packs at all, and its identification is the part that fails quietly --
a wrongly read program ID picks another game's profile, and the pack then fails minutes later in the shader step. This
builds the layout from bytes, so what it checks is the parsing and not a game.
"""
import importlib.util
import pathlib
import struct
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]      # scripts/tests/ -> the repository root
_spec = importlib.util.spec_from_file_location('nxn_pack', REPO / 'scripts' / 'nxn_pack.py')
nxn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nxn)

BASE_TID = 0x0400380020056000          # Splatoon Raiders, a Switch 2 title the profiles know
DLC_TID = BASE_TID + 0x1000 + 1        # add-on content sits at base + 0x1000 + index, and the index starts at 1


def npdm_bytes(tid):
    """The little of an NPDM the packer reads: META, the ACI0 header's offset and size, and the program ID."""
    off = 0x80
    b = bytearray(off + 0x40)
    b[0:4] = b'META'
    struct.pack_into('<II', b, 0x70, off, 0x40)
    b[off:off + 4] = b'ACI0'
    struct.pack_into('<I', b, off + 4, 0x40)
    struct.pack_into('<Q', b, off + 0x10, tid)
    return bytes(b)


class Switch2Dump(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        for base in (self.root / 'base', self.root / 'updates' / '458752'):
            (base / 'exefs').mkdir(parents=True)
            (base / 'exefs' / 'main.npdm').write_bytes(npdm_bytes(BASE_TID))
            (base / 'exefs' / 'main').write_bytes(b'NSO0')
            (base / 'romfs').mkdir()
            (base / 'romfs' / 'a.txt').write_text('a')
        (self.root / 'dlc' / ('%016x' % DLC_TID) / '0' / 'romfs').mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_title_id_comes_from_main_npdm(self):
        d = nxn.scan_dump(self.root)
        self.assertEqual('%016x' % BASE_TID, d['tid'])
        self.assertEqual(['v458752'], list(d['updates']))
        # The folder says 0 and the title ID says 1: the title ID wins, because the host reports add-on content 1 and up
        # (hle_misc.c). Packed as aoc/0 the DLC was invisible to the game (Donkey Kong Bananza, 2026-11-04).
        self.assertEqual([1], list(d['dlc']))

    def test_a_folder_without_the_layout_is_left_to_the_nsp_route(self):
        self.assertIsNone(nxn.scan_dump(self.root / 'base'))

    def test_a_version_is_its_folder_name_in_decimal(self):
        self.assertEqual('v458752', nxn.dump_version_name('458752'))
        self.assertEqual('v458752', nxn.dump_version_name('v458752'))
        self.assertIsNone(nxn.dump_version_name('update'))

    def test_a_step_reads_exefs_and_romfs_of_whichever_version_it_packs(self):
        dirs = nxn.dump_dirs(nxn.scan_dump(self.root), ['v0', 'v458752'])
        self.assertEqual(self.root / 'base' / 'exefs', dirs['v0'] / 'ExeFS')
        self.assertEqual(self.root / 'base' / 'romfs', dirs['v0'] / 'RomFS')
        self.assertEqual(self.root / 'updates' / '458752' / 'exefs', dirs['v458752'] / 'ExeFS')
        self.assertEqual(self.root / 'updates' / '458752' / 'romfs', dirs['v458752'] / 'RomFS')
        # a dump carries no control data, so the steps that want it have to cope with its absence
        self.assertFalse((dirs['v0'] / 'control.nacp').is_file())

    def test_a_reused_work_dir_drops_the_dlc_of_an_old_index(self):
        # --keep-work keeps work/aoc, and assemble copies the whole of it into the package: a DLC whose index moved from the
        # folder's 0 to the title ID's 1 must not leave the old folder to ship a second copy nothing reports.
        with tempfile.TemporaryDirectory() as t:
            work = pathlib.Path(t) / 'work'
            (work / 'aoc' / '0').mkdir(parents=True)
            (work / 'aoc' / '0' / 'old.bin').write_text('a copy the host never reports')
            romfs = pathlib.Path(t) / 'dlc' / '040074a01bf13001' / '0' / 'romfs'
            romfs.mkdir(parents=True)
            (romfs / 'a.bin').write_text('the DLC')
            nxn.dump_aoc({'dlc': {1: romfs}}, work, '040074a01bf12000')
            # One file per DLC (romfs_image.pack), and the reuse stamp beside it; the old index 0 folder is gone.
            self.assertEqual(['1.img', '1.img.inputs.json'], sorted(d.name for d in (work / 'aoc').iterdir()))
            import sys
            sys.path.insert(0, str(nxn.TOOLS))
            import romfs
            self.assertEqual(romfs.open(str(work / 'aoc' / '1.img') + '/a.bin', 'rb').read(), b'the DLC')


if __name__ == '__main__':
    unittest.main()
