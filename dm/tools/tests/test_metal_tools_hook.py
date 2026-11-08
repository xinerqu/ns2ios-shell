#!/usr/bin/env python3
"""The Apple-tools hook in dm/gfx/tools/nometal_run.py, tested without Apple's tools.

`fake_metal.py` stands in for metal/metallib (a python script, invoked through sys.executable), so what is exercised here is
this repository's own logic: the argument translation, the probe that decides whether a machine's tools are usable, using a
compiled library when one comes out, falling back to source when the tool exits non-zero **or** writes something this route
cannot read, and the version passthrough that changes the chain version.

Run: cd nxnative && python -m pytest dm/tools/tests/test_metal_tools_hook.py -q
"""
import importlib.util, os, pathlib, struct, sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
FAKE = HERE / 'fake_metal.py'
NM = HERE.parents[1] / 'gfx' / 'tools' / 'nometal_run.py'


def load():
    spec = importlib.util.spec_from_file_location('nometal_run_under_test', NM)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def nm(monkeypatch):
    m = load()
    m._TOOLS = None          # not probed yet
    m._FELL_BACK.clear()
    monkeypatch.delenv('NXN_METAL_TOOLS', raising=False)
    return m


def fake(monkeypatch, nm, ok=True):
    """Point the hook at the fake tools, the way a real machine points it at metal.exe / metallib.exe."""
    monkeypatch.setenv('FAKE_METAL', '' if ok else 'fail')
    m = [sys.executable, str(FAKE), 'metal']
    l = [sys.executable, str(FAKE), 'metallib']
    nm._TOOLS = {'metal': m, 'metallib': l}
    return nm._TOOLS


# ---- the two file formats the hook judges ---------------------------------------------------------------

def synthetic_library(names):
    ents = b''
    for n in names:
        name = n.encode() + b'\0'
        body = b'NAME' + struct.pack('<H', len(name)) + name + b'ENDT'
        ents += struct.pack('<I', len(body) + 4) + body
    listing = struct.pack('<I', len(names)) + ents
    head = bytearray(0x58)
    head[0:4] = b'MTLB'
    struct.pack_into('<Q', head, 0x10, 0x58 + len(listing))
    struct.pack_into('<Q', head, 0x18, 0x58)
    return bytes(head) + listing


def test_library_names_reads_a_library(nm, tmp_path):
    p = tmp_path / 'a.metallib'
    p.write_bytes(synthetic_library(['main0', 'main0_z']))
    assert nm.library_names(p) == ['main0', 'main0_z']


def test_library_names_rejects_source_and_truncation(nm, tmp_path):
    src = tmp_path / 'text.metallib'
    src.write_text('#include <metal_stdlib>\n')
    assert nm.library_names(src) is None
    whole = synthetic_library(['main0'])
    bad = tmp_path / 'cut.metallib'
    bad.write_bytes(whole[:-8])                       # the size at 0x10 no longer matches the file
    assert nm.library_names(bad) is None


# ---- argument translation -------------------------------------------------------------------------------

def test_as_tool_old_ios_shape(nm):
    assert nm.as_tool(['xcrun', '-sdk', 'iphoneos', 'metallib', 'a.air', '-o', 'b.metallib']) == \
        ('metallib', ['a.air', '-o', 'b.metallib'])


def test_as_tool_upstream_shape_without_program_word(nm):
    argv = ['xcrun', '-isysroot', '/sdk', '-c', '-O1', '-fmetal-math-mode=relaxed', 'x.metal', '-o', 'x.air']
    assert nm.as_tool(argv) == ('metal', ['-c', '-O1', '-fmetal-math-mode=relaxed', 'x.metal', '-o', 'x.air'])
    assert nm.as_tool(['xcrun', '-isysroot', '/sdk', 'x.air', '-o', 'x.metallib']) == \
        ('metallib', ['x.air', '-o', 'x.metallib'])


def test_unknown_shape_is_not_taken(nm):
    assert nm.as_tool(['xcrun', '-f', 'metal']) == (None, None)


# ---- the four call sites --------------------------------------------------------------------------------

def test_probe_accepts_tools_that_produce_a_library(nm, monkeypatch, tmp_path):
    monkeypatch.setenv('FAKE_METAL', '')
    monkeypatch.setenv('NXN_METAL_TOOLS', str(tmp_path))
    # the probe builds its own files in a temp folder; the fake tool is reached through _exe(), so provide both names
    (tmp_path / 'metal.bat').write_text('')            # existence is all _exe() checks for the folder form
    (tmp_path / 'metallib.bat').write_text('')
    nm._exe = lambda kind: [sys.executable, str(FAKE), kind]
    t = nm.tools()
    assert t and t['metal'][1:3] == [str(FAKE), 'metal']


def test_probe_rejects_a_tool_that_writes_garbage(nm, monkeypatch):
    monkeypatch.setenv('FAKE_METALLIB', 'garbage')
    nm._exe = lambda kind: [sys.executable, str(FAKE), kind]
    assert nm.tools() is False


def test_probe_rejects_a_compiler_that_fails(nm, monkeypatch):
    monkeypatch.setenv('FAKE_METAL', 'fail')
    nm._exe = lambda kind: [sys.executable, str(FAKE), kind]
    assert nm.tools() is False


def test_compile_goes_through_the_tool(nm, monkeypatch, tmp_path):
    fake(monkeypatch, nm)
    src = tmp_path / 'x.metal'
    src.write_text('fragment float4 main0() { return float4(1); }\n')
    out = tmp_path / 'x.air'
    nm.run(['xcrun', '-isysroot', '/sdk', '-c', '-O1', str(src), '-o', str(out)])
    assert out.read_text().startswith('fragment float4 main0')       # what the fake wrote through the hook
    assert (tmp_path / 'x.air.called').is_file()                      # … and it was the tool that wrote it


def test_link_goes_through_the_tool_and_is_read_back(nm, monkeypatch, tmp_path):
    fake(monkeypatch, nm)
    air = tmp_path / 'x.air'
    air.write_text('fragment float4 main0() { return float4(1); }\n')
    out = tmp_path / 'x.metallib'
    nm.run(['xcrun', '-sdk', 'iphoneos', 'metallib', str(air), '-o', str(out)])
    assert nm.library_names(out) == ['main0']
    assert (tmp_path / 'x.metallib.called').is_file()


def test_a_failing_link_falls_back_to_source(nm, monkeypatch, tmp_path, capsys):
    fake(monkeypatch, nm)
    monkeypatch.setenv('FAKE_METAL', 'fail')            # the tool refuses this shader
    air = tmp_path / 'x.air'
    air.write_text('fragment float4 main0() { return float4(1); }\n')
    out = tmp_path / 'x.metallib'
    nm.run(['xcrun', '-sdk', 'iphoneos', 'metallib', str(air), '-o', str(out)])
    assert out.read_text() == air.read_text()           # the source route: the text is the library
    assert 'failed' in capsys.readouterr().out


def test_a_link_that_writes_garbage_falls_back_too(nm, monkeypatch, tmp_path):
    fake(monkeypatch, nm)
    monkeypatch.setenv('FAKE_METALLIB', 'garbage')
    air = tmp_path / 'x.air'
    air.write_text('fragment float4 main0() { return float4(1); }\n')
    out = tmp_path / 'x.metallib'
    nm.run(['xcrun', '-sdk', 'iphoneos', 'metallib', str(air), '-o', str(out)])
    assert out.read_text() == air.read_text()           # not the garbage the tool wrote


def test_no_tools_keeps_the_source_route(nm, monkeypatch, tmp_path):
    nm._TOOLS = False
    src = tmp_path / 'x.metal'
    src.write_text('fragment float4 main0() { return float4(1); }\r\n')
    out = tmp_path / 'x.air'
    nm.run(['xcrun', '-isysroot', '/sdk', '-c', str(src), '-o', str(out)])
    assert out.read_bytes() == src.read_bytes().replace(b'\r\n', b'\n')   # the CRLF normalization of the old path


def test_version_comes_from_the_tool_when_present(nm, monkeypatch):
    fake(monkeypatch, nm)
    r = nm.run(['xcrun', '-sdk', 'iphoneos', 'metal', '--version'], capture_output=True, text=True)
    assert 'metalfe' in r.stdout


def test_version_is_none_without_tools(nm):
    nm._TOOLS = False
    r = nm.run(['xcrun', '-sdk', 'iphoneos', 'metal', '--version'], capture_output=True, text=True)
    assert r.stdout.strip() == 'none'


def test_env_off_disables_lookup(nm, monkeypatch, tmp_path):
    monkeypatch.setenv('NXN_METAL_TOOLS', 'off')
    (tmp_path / 'metal.exe').write_text('')
    monkeypatch.setattr(nm.shutil, 'which', lambda n: str(tmp_path / n))
    assert nm._exe('metal') is None


def test_env_folder_is_preferred(nm, monkeypatch, tmp_path):
    monkeypatch.setenv('NXN_METAL_TOOLS', str(tmp_path))
    (tmp_path / 'metal.exe').write_text('')
    got = nm._exe('metal')
    assert got and os.path.basename(got[0]) == 'metal.exe'
