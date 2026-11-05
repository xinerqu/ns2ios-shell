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


if __name__ == '__main__':
    unittest.main()
