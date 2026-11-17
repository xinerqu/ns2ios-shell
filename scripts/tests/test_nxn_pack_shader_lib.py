"""The app carries the game's shader library as one file, and it holds exactly what the host reads.

Why: nvn_mtl.m loads a shader from <app>/msl/b-<chain>/<key>_b.metallib, or from <chain>.img when the folder is not there
(dm/gfx/nvn/msl_image.h). A file the image lacks is not a packaging error the phone reports as one: the shader draws
nothing, the sound plays, and the log has a [RED] line per shader. So the packer writes the image and reads it back before
the pack is called good, and this checks that the file it writes is the one the host will look in.

The .metal sources and dmsh_build.py's report stay out: the host never reads them, and on this route a _b.metallib holds
MSL source rather than a compiled library (gfx/tools/nometal_run.py), which the host compiles from the image too.
"""
import importlib.util
import pathlib
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]      # scripts/tests/ -> the repository root
_spec = importlib.util.spec_from_file_location('nxn_pack', REPO / 'scripts' / 'nxn_pack.py')
nxn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nxn)

CHAIN = '211a53e4d6b9'
LIB = 'b-' + CHAIN
FILES = {
    'abcd_b.metallib': b'#include <metal_stdlib>\n// the MSL source a machine without Apple\'s compiler leaves here\n',
    'abcd_b.json': b'{"chain": "%s", "stage": "ps"}\n' % CHAIN.encode(),
    'abcd_bp.metallib': b'pulling vertex library\n',
    'inputs.tsv': ('game\t0100000000010000\nstamp\t5dd843bed8d1\n').encode(),
    'abcd_b.metal': b'// the Mac build\'s source copy, which the app does not read\n',
    '_b_build.json': b'[{"key": "abcd_b", "st": 0, "err": null}]\n',   # dmsh_build.py\'s report, for the tools
}
CARRIED = {'abcd_b.metallib', 'abcd_b.json', 'abcd_bp.metallib', 'inputs.tsv'}


def library(root):
    lib = root / LIB
    lib.mkdir(parents=True)
    for name, data in FILES.items():
        (lib / name).write_bytes(data)
    return lib


def image_entries(path):
    import sys
    sys.path.insert(0, str(nxn.TOOLS))
    import romfs
    with romfs.scandir(str(path)) as it:
        return {e.name: romfs.open(e.path, 'rb').read() for e in it}


class ShaderLibrary(unittest.TestCase):
    def pack(self, make=library):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        app = root / 'DmIOS.app'
        lib = make(root)
        n = nxn.shader_library(lib, app)
        return app, n

    def test_one_file_holding_what_the_host_reads(self):
        app, n = self.pack()
        image = app / 'msl' / (LIB + '.img')
        self.assertTrue(image.is_file(), 'the library is not one file')
        self.assertFalse((app / 'msl' / LIB).exists(), 'the folder went in beside the image as well')
        self.assertEqual(n, 1, 'the number of shaders')
        self.assertEqual(set(image_entries(image)), CARRIED)

    def test_the_bytes_are_the_libraries(self):
        app, _ = self.pack()
        got = image_entries(app / 'msl' / (LIB + '.img'))
        for name in CARRIED:
            self.assertEqual(got[name], FILES[name], name)

    def test_a_library_without_metallib_keeps_its_folder(self):
        def stamp_only(root):
            lib = root / 'b-000000000000'
            lib.mkdir(parents=True)
            (lib / 'inputs.tsv').write_bytes(b'game\t0100000000010000\n')
            return lib
        app, n = self.pack(stamp_only)
        self.assertEqual(n, 0)
        self.assertTrue((app / 'msl' / 'b-000000000000' / 'inputs.tsv').is_file(), 'a GL package lost its stamp')
        self.assertFalse((app / 'msl' / 'b-000000000000.img').exists())


class Completeness(unittest.TestCase):
    """The packer's count of shader libraries against shader_map.tsv (issue #16).

    A layer-only geometry shader (gs_slice) has a reflection and no library on purpose: the host draws it with the vertex
    shader's layer entry points (dmsh_entry.py). Counting it missing refused a correct Pikmin 4 build after an hour of
    translation, and told the user to repack a step that is deterministic.
    """

    SLICE = '439bed623be839b2'

    def root(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        return pathlib.Path(t.name)

    def test_a_folder_counts_a_slice_shader_as_whole(self):
        lib = self.root() / LIB
        lib.mkdir()
        (lib / 'abcd_b.metallib').write_bytes(b'library')
        (lib / 'abcd_b.json').write_bytes(b'{"stage": "ps"}')
        (lib / (self.SLICE + '_b.json')).write_bytes(b'{"stage": "gs", "gs_slice": {"loc": 1, "comp": "x"}}')
        have, slices = nxn.library_presence(lib)
        self.assertEqual(have, {'abcd'})
        self.assertEqual(slices, {self.SLICE})

    def test_a_reflection_without_a_library_is_still_missing(self):
        lib = self.root() / LIB
        lib.mkdir()
        (lib / 'abcd_b.json').write_bytes(b'{"stage": "ps"}')       # no metallib and not a slice writer
        have, slices = nxn.library_presence(lib)
        self.assertEqual(have, set())
        self.assertEqual(slices, set())

    def test_the_image_path_agrees_with_the_folder(self):
        app = self.root() / 'DmIOS.app'
        lib = self.root() / LIB
        lib.mkdir()
        (lib / 'abcd_b.metallib').write_bytes(b'library')
        (lib / (self.SLICE + '_b.json')).write_bytes(b'{"stage": "gs", "gs_slice": {"loc": 1}}')
        nxn.shader_library(lib, app)
        have, slices = nxn.library_presence(app / 'msl' / (LIB + '.img'))
        self.assertEqual(have, {'abcd'})
        self.assertEqual(slices, {self.SLICE}, 'the image path lost the slice-only key, as it did in issue #16')

    def test_the_shader_map_title_row_is_not_a_key(self):
        smap = self.root() / 'shader_map.tsv'
        smap.write_text('package\tindex\tstage\tdm_ctl_sha16\tsource\n'
                        'gs_global.bin\t2\tgeometry\t%s\t/x#UE572\n' % self.SLICE)
        self.assertEqual(nxn.shader_map_keys(smap), {self.SLICE})


class UEArchives(unittest.TestCase):
    """An Unreal game's shader code archives on the Windows route (issue #15)."""

    def root(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        return pathlib.Path(t.name)

    def version(self, root):
        d = root / 'v262144'
        paks = d / 'RomFS' / 'Game' / 'Content' / 'Paks'
        paks.mkdir(parents=True)
        (paks / 'Carrot4-Switch.pak').write_bytes(b'PAK')
        return d

    def test_paks_are_found_where_unreal_puts_them(self):
        root = self.root()
        d = self.version(root)
        found = nxn.ue_shader_dirs({'v262144': d})
        self.assertEqual(set(found), {'v262144'})
        self.assertEqual([rel for _, rel, _ in found['v262144'][2]],
                         [str(pathlib.Path('Game') / 'Content' / 'Paks' / 'Carrot4-Switch.pak')])

    def test_a_folder_with_no_paks_is_left_alone(self):
        root = self.root()
        d = root / 'v0'
        (d / 'RomFS').mkdir(parents=True)
        (d / 'RomFS' / 'main').write_bytes(b'')
        self.assertEqual(nxn.ue_shader_dirs({'v0': d}), {})

    def test_the_image_the_pack_keeps_is_searched_too(self):
        # step_extract asks nx_extract for RomFS.img, not a tree: the search has to read the image (dm/tools/romfs.py).
        root = self.root()
        d = self.version(root)
        nxn.romfs_images({'v262144': d}, root / 'work')
        self.assertTrue((d / 'RomFS.img').is_file(), 'the fixture did not become one image')
        found = nxn.ue_shader_dirs({'v262144': d})
        self.assertEqual([rel for _, rel, _ in found['v262144'][2]],
                         [str(pathlib.Path('Game') / 'Content' / 'Paks' / 'Carrot4-Switch.pak')])

    def test_the_paks_come_out_of_the_image_byte_for_byte(self):
        root = self.root()
        d = self.version(root)
        pak = d / 'RomFS' / 'Game' / 'Content' / 'Paks' / 'Carrot4-Switch.pak'
        pak.write_bytes(bytes(range(256)) * 40)
        nxn.romfs_images({'v262144': d}, root / 'work')
        _, rom, files = nxn.ue_shader_dirs({'v262144': d})['v262144']
        out = nxn.ue_paks_out(rom, files, root / 'mirror')
        self.assertEqual((out / 'Game' / 'Content' / 'Paks' / 'Carrot4-Switch.pak').read_bytes(), bytes(range(256)) * 40)

    def test_a_stamped_folder_is_reused_without_decoding(self):
        root = self.root()
        d = self.version(root)
        _, rom, files = nxn.ue_shader_dirs({'v262144': d})['v262144']
        out = d / 'ue_shaders'
        out.mkdir()
        (out / 'stamp').write_text(''.join('%s\t%d\n' % (rel, size) for _, rel, size in files))
        nxn.step_ue_shaders({'v262144': d}, root / 'work', False)   # a real run would need ExeFS/main and decode the pak
        self.assertEqual([p.name for p in out.iterdir()], ['stamp'],
                         'the reuse branch did not stop before touching the archives')


if __name__ == '__main__':
    unittest.main()
