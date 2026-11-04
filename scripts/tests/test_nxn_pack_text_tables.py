"""The text tables the host compares as text leave this packer with LF endings, whatever machine packed.

Why: nvn_mtl.m's shmap_read() splits inputs.tsv on "\n" and takes the field after the tab, then requires c1_full.tsv's first
line to start with "# chain b-<V> inputs <that field> ". On Windows the chain's writers open in text mode, the field ends in
"\r", and both tables are refused: every c1 gets its own 64 KiB and uniform blocks are copied whole. That is what the phone
logged for Donkey Kong Bananza before 2026-11-04's fix ("c1_full.tsv is stale or missing" with both files in the package).
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
STAMP = '5dd843bed8d1'


def host_accepts(app):
    """What shmap_read() does, in the same steps: the stamp, then the header it must match.

    Read as bytes and decoded without newline translation: Python's text mode would turn "\\r\\n" into "\\n" and hide exactly
    the difference the host sees (NSString keeps the \\r inside the field).
    """
    stamp_txt = (app / 'msl' / ('b-' + CHAIN) / 'inputs.tsv').read_bytes().decode('utf-8', 'replace')
    stamp = None
    for ln in stamp_txt.split('\n'):
        c = ln.split('\t')
        if len(c) == 2 and c[0] == 'inputs': stamp = c[1]
    head = (app / 'dm' / 'c1_full.tsv').read_bytes().decode('utf-8', 'replace').split('\n')[0]
    return head.startswith('# chain b-%s inputs %s ' % (CHAIN, stamp or '-'))


class TextTables(unittest.TestCase):
    def test_crlf_in_the_stamp_breaks_the_header_check_and_the_fix_restores_it(self):
        with tempfile.TemporaryDirectory() as t:
            app = pathlib.Path(t)
            (app / 'dm').mkdir()
            (app / 'msl' / ('b-' + CHAIN)).mkdir(parents=True)
            (app / 'dm' / 'c1_full.tsv').write_bytes(
                ('# chain b-%s inputs %s 2026-11-04T21:24:58+08:00\n0123456789abcdef\n' % (CHAIN, STAMP)).encode())
            (app / 'msl' / ('b-' + CHAIN) / 'inputs.tsv').write_bytes(
                ('# stamp\ngame\t040074A01BF12000\r\ninputs\t%s\r\n' % STAMP).encode())
            (app / 'dm' / 'env.txt').write_bytes(b'DM_PAD_LAYOUT=1\r\n')
            shader = app / 'msl' / ('b-' + CHAIN) / 'abc_b.metallib'
            shader.write_bytes(b'fragment float4 f() { return float4(1); }\r\n')

            self.assertFalse(host_accepts(app), 'a CRLF stamp has to fail the header check -- that is the bug being fixed')
            self.assertEqual(2, nxn.text_tables_to_lf(app))
            self.assertTrue(host_accepts(app), 'with the tables rewritten the host has to take them')
            self.assertNotIn(b'\r\n', (app / 'dm' / 'env.txt').read_bytes())
            self.assertIn(b'\r\n', shader.read_bytes(), 'the shaders hold Metal source and are left as they are')

    def test_a_clean_tree_is_left_alone(self):
        with tempfile.TemporaryDirectory() as t:
            app = pathlib.Path(t)
            (app / 'dm').mkdir()
            (app / 'dm' / 'env.txt').write_bytes(b'DM_PAD_LAYOUT=1\n')
            self.assertEqual(0, nxn.text_tables_to_lf(app))


if __name__ == '__main__':
    unittest.main()
