"""A game's own upscaler shaders reach the shell through dm/env.txt, not through a compiled-in list.

Why: game.py turns the profile's upscaler shaders into DM_GAME_UPSCALER_KEYS, a compile-time constant, and the game-free
shell is compiled against the placeholder profile -- the empty string. The launcher's "Game's upscaler: MetalFX" row is
drawn from settings.py, which reads the *game's* profile, so the row appeared and choosing it changed nothing until
nvn_mtl.m learned to read DM_NVN_UPSCALER_KEYS and nxn_pack.py started writing it. This checks the packer's half.

Lives under scripts/ and not dm/tools: the five folders packer_sync owns must stay upstream's (see test_nxn_pack_dump.py).
"""
import importlib.util
import pathlib
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]      # scripts/tests/ -> the repository root
_spec = importlib.util.spec_from_file_location('nxn_pack', REPO / 'scripts' / 'nxn_pack.py')
nxn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nxn)

HEADER = 'package\tindex\tstage\tdm_ctl_sha16\tsource\n'
NDSR = '/Lib/agl/agl_resource.Nin_Ounce_NVN2.release.sarc.zs/agl_ndsr.pck#NDSR@0x124700'


def profile(tmp, rows):
    d = pathlib.Path(tmp)
    (d / 'shader_map.tsv').write_text(HEADER + ''.join(r + '\n' for r in rows), encoding='utf-8')
    return d


class UpscalerKeys(unittest.TestCase):
    def test_only_a_computes_of_the_ndsr_pack_count(self):
        with tempfile.TemporaryDirectory() as t:
            gd = profile(t, [
                'a.bin\t1\tcompute\t0123456789abcdef\t' + NDSR,
                'a.bin\t2\tpixel\t1111111111111111\t' + NDSR,                       # another stage: not the upscaler
                'a.bin\t3\tcompute\t2222222222222222\t/agl/other.pck#X',            # another pack
                'a.bin\t4\tcompute\tABCDEF0123456789\t' + NDSR,                     # not a 16-digit lowercase key
                'a.bin\t5\tcompute\tfedcba9876543210\t' + NDSR,
            ])
            self.assertEqual(nxn.upscaler_keys(gd), ['0123456789abcdef', 'fedcba9876543210'])

    def test_a_profile_without_a_map_or_without_keys_writes_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            empty = pathlib.Path(t) / 'nothing'
            empty.mkdir()
            self.assertEqual(nxn.upscaler_keys(empty), [])
            gd = profile(t, ['a.bin\t1\tvertex\t0123456789abcdef\t' + NDSR])
            self.assertEqual(nxn.upscaler_keys(gd), [])

    def test_env_for_writes_the_list_and_only_from_the_profile_directory(self):
        with tempfile.TemporaryDirectory() as t:
            gd = profile(t, ['a.bin\t1\tcompute\t0123456789abcdef\t' + NDSR,
                             'a.bin\t2\tcompute\tfedcba9876543210\t' + NDSR])
            self.assertEqual([l for l in nxn.env_for({}, gd) if 'UPSCALER' in l],
                             ['DM_NVN_UPSCALER_KEYS=0123456789abcdef,fedcba9876543210'])
            # No directory, no list: the caller always passes one, and a silent empty line would be worse than none.
            self.assertEqual([l for l in nxn.env_for({}) if 'UPSCALER' in l], [])


if __name__ == '__main__':
    unittest.main()
