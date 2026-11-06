"""A package made without Apple's compiler carries the alpha test and logic operation entry points in its one library.

Why: upstream cuts `<key>_bx` out of the *compiled* library (`dmsh_side.cut` picks functions out of the MTLB container), which
is a binary step -- on a machine without Apple's compiler `nometal_run.py` turns the compile into a copy and there is nothing
to cut. The pack therefore shipped every pixel shader without its `<entry>_a` / `<entry>_l`, and Super Mario Odyssey drew
bright white where shadows should be (code 4, 2026-11-06: 20,497 red lines out of 27,984 shaders, 0 `_bx.metallib`).
`nometal_run.py` now hands the compile unit (`dmsh_side.unit`'s output, which already holds both kinds of entry point) over
as `<key>_b` itself, and the host (`nvn_mtl.m` at_fn / lo_fn) falls back to the base library when there is no side one.
This pins the three things that make that true: the unit really holds both entry points, the split writes it as the base
library, and no stale side library is left behind for the host to load instead.
"""
import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]      # scripts/tests/ -> the repository root
TOOLS = REPO / 'dm' / 'gfx' / 'tools'
sys.path.insert(0, str(TOOLS))

import dmsh_side                                          # noqa: E402  (needs the path above)
_spec = importlib.util.spec_from_file_location('nometal_run', TOOLS / 'nometal_run.py')
nr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nr)                              # its own main() is behind `if __name__`, so nothing runs

# The shape dmsh_side.entries / dmsh_alpha / dmsh_logic read: one fragment entry point writing a float4 to colour 0.
MSL = '''#include <metal_stdlib>
using namespace metal;
struct main0_out
{
    float4 m_0 [[color(0)]];
};
fragment main0_out main0()
{
    main0_out out = {};
    out.m_0 = float4(1.0);
    return out;
}
'''


class OneLibraryHoldsBoth(unittest.TestCase):
    def test_the_unit_names_both_kinds_of_entry_point(self):
        unit, info = dmsh_side.unit(MSL)
        self.assertIsNotNone(unit, info)
        self.assertEqual(info['alpha'], ['main0_a'])
        self.assertEqual(info['logic'], ['main0_l'])
        # Both the side entry points and the plain one the pipeline is built from are in that one text.
        for name in ('fragment main0_out main0_a(', 'fragment main0_out main0_l(', 'fragment main0_out main0()'):
            self.assertIn(name, unit)

    def test_the_split_writes_the_unit_as_the_base_library(self):
        unit, _ = dmsh_side.unit(MSL)
        with tempfile.TemporaryDirectory() as d:
            u = os.path.join(d, 'abc_bu.metal')
            a = os.path.join(d, 'abc_b.metallib')
            b = os.path.join(d, 'abc_bx.metallib')
            with open(u, 'wb') as f:
                f.write(unit.encode())
            with open(b, 'wb') as f:                      # what a build on a Mac leaves in the same folder
                f.write(b'MTLB not really a library')
            nr.SIDE_KEYS.clear()
            nr.split_into_one_library(None, 'mac', u, a, b, None)
            with open(a, 'rb') as f:
                self.assertEqual(f.read(), unit.encode())
            self.assertFalse(os.path.exists(b), 'a stale side library would be loaded instead of the base one')
            self.assertIn('abc_b.metallib', nr.SIDE_KEYS)


if __name__ == '__main__':
    unittest.main()
