#!/usr/bin/env python3
"""What the shim prints, and how the packer reads what its children print.

2026-11-08: a pack died right after `tool_lock --update-lock` with `AttributeError: 'NoneType' object has no attribute
'strip'`, and not one line about it reached steps.log. The cause was two lines of code apart from each other:

  * nometal_run.py printed one Chinese line on stdout. The packer runs it with PYTHONUTF8=1, so those bytes are UTF-8.
  * nxn_pack.py read them back with `capture_output=True, text=True`, which decodes with the console's code page -- GBK on
    a Chinese Windows. GBK cannot decode that line, the capture's reader thread raises, and `Popen.stdout` ends up None.

It failed on the user's machine and not on mine only because this shell exports LANG, which makes Python prefer UTF-8. So
both halves are pinned here: every line the shim prints is ASCII, and every capture in the packer names its encoding.

Run: cd nxnative && python -m pytest dm/tools/tests/test_packer_output_encoding.py -q
"""
import ast, pathlib, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]                       # nxnative/
NM = ROOT / 'dm' / 'gfx' / 'tools' / 'nometal_run.py'
PACKER = ROOT / 'scripts' / 'nxn_pack.py'


def _texts(node):
    """The literal parts of one print()/sys.exit() argument list, f-strings included."""
    for a in node.args:
        for n in ast.walk(a):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                yield n.value


def _outputs(path):
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id == 'print':
            yield node, 'print'
        elif (isinstance(f, ast.Attribute) and f.attr == 'exit'
              and isinstance(f.value, ast.Name) and f.value.id == 'sys'):
            yield node, 'sys.exit'


def test_the_shim_prints_ascii_only():
    """A child's stdout must survive being decoded with the console's code page: ASCII is the only thing that always does."""
    bad = [(ln, t) for node, what in _outputs(NM) for t in _texts(node) for ln in [node.lineno] if not t.isascii()]
    assert not bad, 'nometal_run.py prints non-ASCII (the packer decodes with GBK): %r' % (bad,)


def test_the_shim_output_decodes_as_gbk(tmp_path, monkeypatch):
    """The same thing measured instead of read: what a run of the shim writes, decoded strictly the way GBK decodes it."""
    child = tmp_path / 'noop.py'
    child.write_text('raise SystemExit(0)\n', encoding='utf-8')
    monkeypatch.setenv('NXN_METAL_TOOLS', str(tmp_path / 'no-tools-here'))   # no probe of the real tools: this is about output
    monkeypatch.setenv('PYTHONUTF8', '1')                                   # what the packer's children run with
    r = subprocess.run([sys.executable, str(NM), str(child)], capture_output=True, timeout=300)
    assert r.returncode == 0, r.stderr
    assert r.stdout.isascii(), 'the shim printed non-ASCII: %r' % r.stdout
    r.stdout.decode('gbk')                                                   # must not raise; that is the whole point


def test_every_capture_in_the_packer_names_an_encoding():
    """`text=True` alone means the console's code page; the children run with PYTHONUTF8=1, so the two disagree."""
    lines = PACKER.read_text(encoding='utf-8').splitlines()
    bad = []
    for i, ln in enumerate(lines):
        if 'capture_output=True' not in ln:
            continue
        window = ' '.join(lines[i:i + 3])            # the call may wrap; every one of ours wraps at most once
        if 'text=True' in window and 'encoding=' not in window:
            bad.append(i + 1)
    assert not bad, 'nxn_pack.py captures output without naming an encoding: lines %s' % bad
