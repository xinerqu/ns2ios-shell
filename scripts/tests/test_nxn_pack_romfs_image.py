"""A game's RomFS becomes one file before anything reads it, and nothing is lost on the way.

Why: the extraction hands over a folder of loose files -- Tears of the Kingdom's is about 800,000 of them -- and the app
carries whatever shape it gets: iOS copies those one by one at install, and the IPA writer streams them one by one. The
image is one file the host maps page by page (dm/host/romfs_image.h), and dm/tools/romfs.py serves it as a folder to
every tool, so the shader split, the profile scan and the assembly take either form. Upstream's NSP script writes this
image while it extracts (pack_dump.pack_version); this route extracted files first, so scripts/nxn_pack.py makes it here.

What this checks: the image exists, the loose tree is gone, the entry count matches the tree it came from (every file,
every folder, the root), and reading through romfs gives back the same bytes and the same names -- a dropped file is not
an error the phone reports, it is a game that cannot find its data.
"""
import importlib.util
import os
import pathlib
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]      # scripts/tests/ -> the repository root
_spec = importlib.util.spec_from_file_location('nxn_pack', REPO / 'scripts' / 'nxn_pack.py')
nxn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nxn)

FILES = {
    'top.txt': b'hello',
    'System/Resource/a.bin': b'A' * 5000,
    'Model/b.bin': b'B' * 3000,
}


class RomfsImage(unittest.TestCase):
    def build(self, files=FILES):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ver = pathlib.Path(tmp.name) / 'extract' / 'v0'
        src = ver / 'RomFS'
        for name, data in files.items():
            (src / name).parent.mkdir(parents=True, exist_ok=True)
            (src / name).write_bytes(data)
        return ver, src

    def test_one_file_holding_the_tree(self):
        ver, src = self.build()
        nxn.romfs_images({'v0': ver}, ver.parent.parent)
        self.assertTrue((ver / 'RomFS.img').is_file(), 'the RomFS is not one file')
        self.assertFalse(src.exists(), 'the loose tree stayed next to the image')

    def test_the_bytes_and_the_names_come_back(self):
        import sys
        ver, src = self.build()
        sys.path.insert(0, str(nxn.TOOLS))
        import romfs
        nxn.romfs_images({'v0': ver}, ver.parent.parent)
        img = str(ver / 'RomFS.img')
        for name, data in FILES.items():
            self.assertEqual(romfs.open(img + '/' + name, 'rb').read(), data, name)
        self.assertEqual(sorted(p.name for p in romfs.scandir(img)), ['Model', 'System', 'top.txt'])

    def test_every_entry_reaches_the_image(self):
        ver, src = self.build()
        import sys
        sys.path.insert(0, str(nxn.TOOLS))
        import romfs_image
        files = dirs = 0
        for _, subdirs, names in os.walk(src):
            dirs += len(subdirs); files += len(names)
        nxn.romfs_images({'v0': ver}, ver.parent.parent)
        with open(ver / 'RomFS.img', 'rb') as f:
            magic, _, _, n = romfs_image.HEAD.unpack(f.read(romfs_image.HEAD.size))
        self.assertEqual(magic, romfs_image.MAGIC)
        self.assertEqual(n, files + dirs + 1, 'files + folders + the root')

    def test_a_dump_folder_is_left_alone(self):
        """A dump belongs to the user: the image is never written into it."""
        ver, src = self.build()
        dump = nxn.DumpVersion(src.parent)
        nxn.romfs_images({'v0': dump}, pathlib.Path(tempfile.mkdtemp()))
        self.assertTrue(src.is_dir(), 'the dump lost its RomFS folder')
        self.assertFalse((src.parent / 'RomFS.img').is_file(), 'an image was written into the dump')


class AocImage(unittest.TestCase):
    """A DLC's RomFS becomes one <index>.img, the shape upstream's extract_aoc writes.

    The host reads either form (hle_misc.c takes <index> and <index>.img, and reports a legacy folder and its image as
    the same DLC once), so a package never has to carry both -- and a game whose own files are one romfs.img should not
    carry its add-on content as folders.
    """

    def dlc(self, idx, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = pathlib.Path(tmp.name) / 'aoc'
        src = out / str(idx)
        for name, data in files.items():
            (src / name).parent.mkdir(parents=True, exist_ok=True)
            (src / name).write_bytes(data)
        return out, src

    def test_the_folder_becomes_one_file(self):
        out, src = self.dlc(1, FILES)
        self.assertTrue(nxn.aoc_image(out, 1))
        self.assertTrue((out / '1.img').is_file())
        self.assertFalse(src.exists(), 'the loose DLC folder stayed next to the image')

    def test_reading_it_back_gives_the_same_files(self):
        import sys
        out, _ = self.dlc(2, FILES)
        sys.path.insert(0, str(nxn.TOOLS))
        import romfs
        nxn.aoc_image(out, 2)
        img = str(out / '2.img')
        for name, data in FILES.items():
            self.assertEqual(romfs.open(img + '/' + name, 'rb').read(), data, name)

    def test_a_missing_folder_is_not_an_error(self):
        out, _ = self.dlc(1, FILES)
        (out / '1').rename(out / 'elsewhere')
        self.assertFalse(nxn.aoc_image(out, 1))


if __name__ == '__main__':
    unittest.main()
