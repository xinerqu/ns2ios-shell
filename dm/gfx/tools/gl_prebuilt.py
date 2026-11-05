#!/usr/bin/env python3
"""A LÖVE game's GLSL without ever running it: what DM_GL_CAPTURE would have written, straight from the game files.

Why this exists: an OpenGL game assembles its shader sources at run time (Balatro's `game.lua` calls
`love.graphics.newShader` once per `.fs` in `resources/shaders`), so no game file holds the GLSL that
dm/gfx/tools/gl_shaders.py hashes. Upstream collects it by booting the game on a Mac with DM_GL_CAPTURE, and so did this
project until now -- one run of the game per user, which is the step this script removes.

LÖVE is not a black box here: it carries its wrapper as Lua source (`wrap_GraphicsShader.lua`, embedded in the game's
`main` as a C string), and that source is what turns a `.fs` file into the two GLSL programs the game compiles. This runs
that source instead of guessing at it, so the output is the same text the game produced.

Checked against Balatro's 35 captured shaders: the 31 that come from its 19 `.fs` files and the 4 built into LÖVE hash to
exactly the keys the game computed at run time (sha256 of the GLSL, first 16 hex digits, which is the capture's file
name). Nothing here is specific to Balatro -- any LÖVE game's sources are read the same way -- but a different LÖVE build
may carry a different wrapper, so the keys are always checked against the profile's `gl_capture.tsv` when it has one.

    python3 gl_prebuilt.py <unpacked game dir> <out dir> [--gles] [--expect <gl_capture.tsv>]

`<unpacked game dir>` holds ExeFS/ (for the wrapper) and RomFS/ (for the .fs files); the output is a capture folder,
`<key>.vert` / `<key>.frag`, which is what gl_shaders.py and nxn_pack.py's --gl-capture take.

Two details decide whether the hashes match, and both are easy to get wrong:
  · the source text is read as bytes, never as text: a `.fs` that uses CRLF must keep it, because the wrapper pastes the
    file in as it is and the game hashes the CRLF (reading it as text turns 153 bytes into 153 differences);
  · `gles` is false and the host reports GLSL 3, which is what this host's EGL context is (desktop GL 3.3): the wrapper
    then wraps GLSL 1-style sources for GLSL 3 (`#define LOVE_GLSL1_ON_GLSL3 1`, `#line 0`), which is what the captured
    shaders show.
"""
import hashlib, os, struct, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def nso_image(path):
    """The decompressed text+rodata segments of an NSO (the same unpacking dm/tools/nso.py does)."""
    import lz4.block
    d = open(path, 'rb').read()
    flags = struct.unpack_from('<I', d, 0xc)[0]
    fo, mo, sz = struct.unpack_from('<III', d, 0x20)
    csz = struct.unpack_from('<I', d, 0x64)[0]
    img = d[fo:fo + csz]
    if flags >> 1 & 1: img = lz4.block.decompress(img, uncompressed_size=sz)
    return img


def lover_wrapper(main_path):
    """LÖVE's wrap_GraphicsShader.lua, as the text the game carries.

    It sits in rodata as one NUL-terminated C string, between the two 'DO NOT REMOVE' markers its build adds; those
    markers are what the boundaries are taken from here, not a fixed offset.
    """
    img = nso_image(main_path)
    at = img.find(b'function love.graphics._shaderCodeToGLSL')
    if at < 0:
        sys.exit('%s holds no wrap_GraphicsShader.lua: this is not a LÖVE game, or its LÖVE keeps the wrapper in C++'
                 % main_path)
    start = img.rfind(b'\x00', 0, at) + 1
    end = img.find(b'\x00', at)
    src = img[start:end]
    if b'DO NOT REMOVE THE ABOVE LINE' not in src or b'DO NOT REMOVE THE NEXT LINE' not in src:
        sys.exit('%s: the wrapper text looks truncated (%d bytes); the build markers are missing' % (main_path, len(src)))
    return src


def generate(game_dir, gles=False):
    """[(key, 'vert'|'frag', glsl bytes, where)] for every shader the game would compile."""
    from lupa import LuaRuntime
    main = os.path.join(game_dir, 'ExeFS', 'main')
    if not os.path.isfile(main):
        sys.exit('%s: no ExeFS/main' % game_dir)
    # encoding=None keeps Lua strings as bytes in both directions: the wrapper slices the source text, so a single
    # re-encode on the way through would change what is hashed.
    L = LuaRuntime(encoding=None, unpack_returned_tuples=True)
    L.execute(b'__defaults = nil')
    L.execute(b'''love = { graphics = {
  _setDefaultShaderCode = function(d, dg) __defaults = d end,
  getSupported = function() return { glsl3 = true } end,
  isGammaCorrect = function() return false end,
} }''')
    L.execute(lover_wrapper(main))
    fn = L.globals().love.graphics._shaderCodeToGLSL
    defaults = L.globals().__defaults
    if defaults is None:
        sys.exit('%s: the wrapper ran but registered no default shaders' % main)

    out = []
    # The game files may be one RomFS.img rather than a RomFS/ folder (dm/tools/romfs_image.py; the packer makes one for
    # every game). dm/tools/romfs.py serves an image as a folder, so walking and reading go through it: with plain
    # os.walk over '<extract>/RomFS' this found no .fs at all once the folder was gone, and Balatro failed with 31 of its
    # 35 shader keys missing (2026-11-06).
    sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools'))
    import romfs
    root = os.fspath(romfs.source(game_dir))
    n_fs = 0
    for d, _, fs in romfs.os.walk(root):
        for f in sorted(fs):
            if not f.endswith('.fs'): continue        # LÖVE's merged stage file: vertex and pixel in one
            p = os.path.join(d, f)
            src = romfs.open(p, 'rb').read()           # bytes: CRLF is part of the text the game hashes
            n_fs += 1
            v, px = fn(gles, src)
            where = os.path.relpath(p, root).replace(os.sep, '/')
            for txt, stage in ((v, 'vert'), (px, 'frag')):
                if txt: out.append((hashlib.sha256(txt).hexdigest()[:16], stage, txt, where))
    # LÖVE's own shaders, which every game can use: the default vertex (a .fs with no vertex stage uses it), the default
    # pixel, and the video / array-canvas ones. They are what the wrapper hands _setDefaultShaderCode for this language.
    # the keys are Lua strings, which lupa hands over as bytes in this mode
    dflt = defaults[b'glsl3' if not gles else b'essl3']
    for kind, stage in ((b'vertex', 'vert'), (b'pixel', 'frag'), (b'videopixel', 'frag'), (b'arraypixel', 'frag')):
        txt = dflt[kind]
        out.append((hashlib.sha256(txt).hexdigest()[:16], stage, txt, 'love/' + kind.decode()))
    return out, n_fs


def main():
    a = [x for x in sys.argv[1:]]
    gles = '--gles' in a
    if gles: a.remove('--gles')
    expect = None
    if '--expect' in a:
        i = a.index('--expect'); expect = a[i + 1]; del a[i:i + 2]
    if len(a) < 2: sys.exit(__doc__)
    game, out = a[0], a[1]
    shaders, n_fs = generate(game, gles)
    os.makedirs(out, exist_ok=True)
    seen = {}
    for key, stage, txt, where in shaders:
        if (key, stage) in seen: continue
        seen[(key, stage)] = where
        with open(os.path.join(out, '%s.%s' % (key, 'vert' if stage == 'vert' else 'frag')), 'wb') as f:
            f.write(txt)
    v = sum(1 for k in seen if k[1] == 'vert')
    print('%d .fs files -> %d shaders (%d vertex, %d fragment) -> %s' % (n_fs, len(seen), v, len(seen) - v, out))
    if expect:
        want = set()
        for ln in open(expect, encoding='utf-8'):
            ln = ln.strip()
            if ln and not ln.startswith('#'): want.add(ln)
        got = {'%s.%s' % (k, 'vert' if s == 'vert' else 'frag') for (k, s) in seen}
        miss = sorted(want - got)
        if miss:
            print('[!] %d of the %d keys the profile lists are missing: %s%s'
                  % (len(miss), len(want), ', '.join(miss[:8]), ', ...' if len(miss) > 8 else ''))
            return 1
        print('all %d keys the profile lists were reproduced' % len(want))
    return 0


if __name__ == '__main__':
    sys.exit(main())
