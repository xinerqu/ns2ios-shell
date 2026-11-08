#!/usr/bin/env python3
"""The Apple-tools hook in dm/gfx/tools/nometal_run.py, tested without Apple's tools.

`fake_metal.py` stands in for metal/metallib (a python script, invoked through sys.executable), so what is exercised here is
this repository's own logic: the argument translation, the probe that decides whether a machine's tools are usable, using a
compiled library when one comes out, the source stand-in for a shader Apple's compiler refuses (the alpha test / logic
operation units), and the version passthrough that changes the chain version.

Run: cd nxnative && python -m pytest dm/tools/tests/test_metal_tools_hook.py -q
"""
import importlib.util, os, pathlib, struct, sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
FAKE = HERE / 'fake_metal.py'
NM = HERE.parents[1] / 'gfx' / 'tools' / 'nometal_run.py'

SOURCE = 'fragment float4 main0() { return float4(1); }\n'


def load():
    spec = importlib.util.spec_from_file_location('nometal_run_under_test', NM)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def nm(monkeypatch):
    m = load()
    m._TOOLS = None          # not probed yet
    m.SOURCE_FALLBACK.clear()
    monkeypatch.delenv('NXN_METAL_TOOLS', raising=False)
    monkeypatch.delenv('FAKE_METAL', raising=False)
    monkeypatch.delenv('FAKE_METALLIB', raising=False)
    return m


def fake(nm, ok=True):
    """Point the hook at the fake tools, the way a real machine points it at metal.exe / metallib.exe."""
    os.environ['FAKE_METAL'] = '' if ok else 'fail'
    nm._TOOLS = {'metal': [sys.executable, str(FAKE), 'metal'],
                 'metallib': [sys.executable, str(FAKE), 'metallib']}
    return nm._TOOLS


# ---- the library header the hook judges ------------------------------------------------------------------

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
    bad = tmp_path / 'cut.metallib'
    bad.write_bytes(synthetic_library(['main0'])[:-8])     # the size at 0x10 no longer matches the file
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


# ---- the probe ------------------------------------------------------------------------------------------

def test_probe_accepts_tools_that_produce_a_library(nm, monkeypatch):
    monkeypatch.setattr(nm, '_exe', lambda kind: [sys.executable, str(FAKE), kind])
    t = nm.tools()
    assert t and t['metal'][1] == str(FAKE)


def test_probe_rejects_a_tool_that_writes_garbage(nm, monkeypatch):
    monkeypatch.setenv('FAKE_METALLIB', 'garbage')
    monkeypatch.setattr(nm, '_exe', lambda kind: [sys.executable, str(FAKE), kind])
    assert nm.tools() is False


def test_probe_rejects_a_compiler_that_fails(nm, monkeypatch):
    monkeypatch.setenv('FAKE_METAL', 'fail')
    monkeypatch.setattr(nm, '_exe', lambda kind: [sys.executable, str(FAKE), kind])
    assert nm.tools() is False


# ---- the four call sites --------------------------------------------------------------------------------

def test_compile_goes_through_the_tool(nm, tmp_path):
    fake(nm)
    src = tmp_path / 'x.metal'
    src.write_text(SOURCE)
    out = tmp_path / 'x.air'
    nm.run(['xcrun', '-isysroot', '/sdk', '-c', '-O1', str(src), '-o', str(out)])
    assert out.read_text() == SOURCE                          # what the fake wrote through the hook …
    assert (tmp_path / 'x.air.called').is_file()              # … and it was the tool that wrote it


def test_link_goes_through_the_tool_and_is_read_back(nm, tmp_path):
    fake(nm)
    air = tmp_path / 'x.air'
    air.write_text(SOURCE)
    out = tmp_path / 'x.metallib'
    nm.run(['xcrun', '-sdk', 'iphoneos', 'metallib', str(air), '-o', str(out)])
    assert nm.library_names(out) == ['main0']
    assert (tmp_path / 'x.metallib.called').is_file()


def test_a_compile_the_tool_refuses_keeps_that_library_as_source(nm, tmp_path):
    """The alpha test / logic operation units are refused by Apple's compiler (invalid 'color' on a fragment input), and
    those libraries have always travelled as source. So the compile fails, and the *link* of that intermediate writes the
    source out as the library -- not the tool's half-made output, and not an empty file."""
    fake(nm, ok=False)
    src = tmp_path / 'x.metal'
    src.write_text(SOURCE)
    air = tmp_path / 'x.air'
    out = tmp_path / 'x.metallib'
    nm.run(['xcrun', '-isysroot', '/sdk', '-c', str(src), '-o', str(air)])
    assert air.read_text() == SOURCE                          # the source stands in for the failed compile
    os.environ['FAKE_METAL'] = ''                             # the tool works; only that shader was refused
    nm.run(['xcrun', '-sdk', 'iphoneos', 'metallib', str(air), '-o', str(out)])
    assert out.read_text() == SOURCE                          # … and the library holds it, as before
    assert nm.SOURCE_FALLBACK == {}                           # consumed: a second link would not see it again


def test_the_source_stand_in_is_not_remembered_for_other_shaders(nm, tmp_path):
    fake(nm, ok=False)
    src = tmp_path / 'a.metal'
    src.write_text(SOURCE)
    air = tmp_path / 'a.air'
    nm.run(['xcrun', '-isysroot', '/sdk', '-c', str(src), '-o', str(air)])
    os.environ['FAKE_METAL'] = ''
    other = tmp_path / 'b.air'
    other.write_text('fragment float4 main0() { return float4(2); }\n')
    out = tmp_path / 'b.metallib'
    nm.run(['xcrun', '-sdk', 'iphoneos', 'metallib', str(other), '-o', str(out)])
    assert nm.library_names(out) == ['main0']                 # a real library, not somebody else's source


def test_the_side_library_patch_compiles_the_unit(nm, tmp_path):
    """split_into_one_library runs for every pixel shader that has alpha test / logic operation entry points: with tools it
    compiles the unit into the one library (no cut), and the library that comes out lists every entry point."""
    fake(nm)
    msl = tmp_path / 'x_bu.metal'
    msl.write_text(SOURCE + SOURCE.replace('main0', 'main0_a'))
    lib = tmp_path / 'x_b.metallib'
    side = tmp_path / 'x_bx.metallib'
    side.write_text('stale side library from an earlier build')
    nm.split_into_one_library(None, 'ios', str(msl), [str(lib), str(side)], None)
    assert nm.library_names(lib) == ['main0', 'main0_a']
    assert not side.exists()                                   # no side library on this route


def test_the_side_library_patch_falls_back_to_source_when_refused(nm, tmp_path):
    fake(nm, ok=False)
    msl = tmp_path / 'x_bu.metal'
    msl.write_text(SOURCE)
    lib = tmp_path / 'x_b.metallib'
    nm.split_into_one_library(None, 'ios', str(msl), [str(lib)], None)
    assert lib.read_text() == msl.read_text()                  # the library is the source, as before


def test_no_tools_keeps_the_source_route(nm, tmp_path):
    nm._TOOLS = False
    src = tmp_path / 'x.metal'
    src.write_bytes(SOURCE.encode().replace(bytes((10,)), bytes((13, 10))))   # CRLF on disk, as spirv-cross writes it
    out = tmp_path / 'x.air'
    nm.run(['xcrun', '-isysroot', '/sdk', '-c', str(src), '-o', str(out)])
    assert out.read_bytes() == SOURCE.encode()                # the CRLF normalization of the old path


def test_version_comes_from_the_tool_when_present(nm):
    fake(nm)
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
