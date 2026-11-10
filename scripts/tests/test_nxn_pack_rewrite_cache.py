"""A cache hit restores every output of patch_nso.py, the gate table included (GitHub issue #13).

Why: the rewrite cache kept .patched.bin and .fixups.tsv only, so a second pack of the same game restored those two and
no .gates.tsv. Every module then loaded with "gates 0" and the game aborted at the first gate it reached ("FATAL unknown
gate") -- on the phone, after a pack that reported success. check_gates() stayed quiet because it skips a module with no
gate table, so nothing on the packing machine noticed.

The rewriter itself is replaced by a fake: what is under test is what the cache stores and what a hit puts back.
"""
import importlib.util
import os
import pathlib
import tempfile
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[2]      # scripts/tests/ -> the repository root
_spec = importlib.util.spec_from_file_location('nxn_pack', REPO / 'scripts' / 'nxn_pack.py')
nxn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nxn)

MODS = ['main', 'sdk']
OUTPUTS = ['.patched.bin', '.fixups.tsv', '.gates.tsv', '.sites.tsv', '.cuts.tsv', '.obs.tsv', '.changed.tsv']


def fake_rewriter(calls):
    """Stands in for `patch_nso.py`: writes the outputs the real one writes, and counts the runs."""
    def run(argv, **kw):
        m = argv[-1]
        calls.append(m)
        out = pathlib.Path(argv[3])
        (out / (m + '.patched.bin')).write_bytes(b'patched ' + m.encode())
        for suf in OUTPUTS[1:]:
            (out / (m + suf)).write_text('gate\t%s\n' % suf, encoding='utf-8')
    return run


class RewriteCache(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.t.name)
        self.exe = root / 'ExeFS'
        self.exe.mkdir()
        for m in MODS: (self.exe / m).write_bytes(b'NSO0' + m.encode())
        self.cache = root / 'cache'
        self.out = root / 'patched'
        self.work = root / 'work'
        (self.work / 'logs').mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {'NXN_SHADER_CACHE': str(self.cache), 'DM_GAME': '0100F5D01DA3A000'})
        self.env.start()
        self.calls = []
        self.run = mock.patch.object(nxn, 'run', fake_rewriter(self.calls))
        self.run.start()

    def tearDown(self):
        self.run.stop()
        self.env.stop()
        self.t.cleanup()

    def pack(self, out=None, force=False):
        out = out or self.out
        nxn.step_rewrite(self.exe, MODS, out, self.work, '0100F5D01DA3A000', force=force)
        return out

    def test_second_pack_restores_the_gate_table(self):
        first = self.pack()
        for m in MODS:                                   # the first pack has it: nothing was cached yet
            self.assertTrue((first / (m + '.gates.tsv')).is_file(), m)
        self.assertEqual(self.calls, MODS)

        self.calls.clear()
        second = self.pack(pathlib.Path(self.t.name) / 'patched2')
        self.assertEqual(self.calls, [], 'the second pack must come from the cache')
        for m in MODS:
            for suf in OUTPUTS:
                self.assertTrue((second / (m + suf)).is_file(), '%s%s missing after a cache hit' % (m, suf))

    def test_an_entry_written_before_the_gate_table_was_kept_is_a_miss(self):
        self.pack()
        entry = next((self.cache / 'patched' / '0100F5D01DA3A000').iterdir())
        for f in list(entry.iterdir()):                  # what the old store wrote: two files, no manifest
            if f.name.endswith(('.gates.tsv', nxn.REWRITE_OUTPUTS_MANIFEST)): f.unlink()
        self.calls.clear()
        out = self.pack(pathlib.Path(self.t.name) / 'patched2')
        self.assertTrue(self.calls, 'an entry without a gate table must not be reused')
        for m in MODS: self.assertTrue((out / (m + '.gates.tsv')).is_file(), m)

    def test_an_entry_whose_manifest_names_a_gate_table_it_lacks_is_a_miss(self):
        self.pack()
        entry = next((self.cache / 'patched' / '0100F5D01DA3A000').iterdir())
        for f in list(entry.iterdir()):
            if f.name.endswith('.gates.tsv'): f.unlink()  # the manifest still lists it
        self.calls.clear()
        out = self.pack(pathlib.Path(self.t.name) / 'patched2')
        self.assertTrue(self.calls)
        for m in MODS: self.assertTrue((out / (m + '.gates.tsv')).is_file(), m)

    def test_a_complete_entry_is_reused_without_rewriting(self):
        self.pack()
        self.calls.clear()
        nxn.step_rewrite(self.exe, MODS, pathlib.Path(self.t.name) / 'patched2', self.work, '0100F5D01DA3A000')
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
