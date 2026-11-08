#!/usr/bin/env python3
"""Does the no-Metal shim still cover the commands upstream actually issues?

The base move of 2026-11-08 broke the shell three times in a row, and each time only the smoke pack said so, fifteen
minutes in:

  * dmsh_build started naming its compilers with `xcrun -sdk macosx -f metal` and `--show-sdk-path` at import time, with
    check=True, so every pack died before the first shader;
  * Cache.compile_split changed from two paths to one list of libraries, which our replacement still took the old way;
  * the compile command stopped carrying the word `metal` at all (the tool comes from `xcrun -f` now), so the branches
    that looked for it stopped matching anything.

All three are visible in the text of the tree being built, before anything compiles. This reads upstream's Python,
rebuilds the argv shapes it will issue, and runs our shim against them:

    python scripts/check_shim_surface.py . ../nx-orig

Exit status: 0 when every shape is answered, 1 when one is not (with the argv printed).
"""
import ast
import importlib.util
import os
import pathlib
import shutil
import sys
import tempfile

SHIM = 'dm/gfx/tools/nometal_run.py'
BUILD = 'dm/gfx/tools/dmsh_build.py'
CACHE = 'dm/gfx/tools/dmsh_mtlcache.py'
PCH = 'dm/gfx/tools/dmsh_pch.py'


def load_shim(repo):
    spec = importlib.util.spec_from_file_location('nometal_run_under_test', os.path.join(repo, SHIM))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # The no-compiler route, always: this check feeds the shim a synthetic `-isysroot xcrun`, which the real compiler would
    # be handed verbatim on a machine that has Apple's tools -- two false failures on 2026-11-08 (measured: 2 problems with
    # the tools installed, 0 with NXN_METAL_TOOLS=off). The route decides nothing about which commands are covered.
    m._TOOLS = {}
    return m


def assigned_list(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == name for t in node.targets):
            return node.value
    return None


def tokens(node, names):
    """The literal argv a module-level list builds: names already assigned become their own tokens."""
    if isinstance(node, ast.List):
        out = []
        for e in node.elts:
            if isinstance(e, ast.Constant) and isinstance(e.value, str):
                out.append(e.value)
            elif isinstance(e, ast.Starred) and isinstance(e.value, ast.Name):
                out.extend(names.get(e.value.id, []))
            elif isinstance(e, ast.Subscript) and isinstance(e.value, ast.Name):
                out.extend(names.get(e.value.id, []))   # MAC_METAL[4:] -- the tail only, close enough to probe the flags
            elif isinstance(e, ast.Call) and isinstance(e.func, ast.Name) and e.func.id == 'xcrun_out':
                out.append('xcrun')                     # what the shim answers for `xcrun -f`
        return out
    return []


def main(argv):
    repo = pathlib.Path(argv[1] if len(argv) > 1 else '.').resolve()
    up = pathlib.Path(argv[2] if len(argv) > 2 else '../nx-orig').resolve()
    shim = load_shim(repo)
    problems = []

    # ---- 1. the probes dmsh_build makes at import time ----
    tree = ast.parse((up / BUILD).read_text(encoding='utf-8'))
    probes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'xcrun_out':
            args = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if args:
                probes.append(['xcrun', '-sdk', 'macosx'] + args)
    for cmd in sorted({tuple(p) for p in probes}):
        try:
            r = shim.run(list(cmd), capture_output=True, text=True)
            if r.returncode != 0 or not r.stdout.strip():
                problems.append(('probe answered nothing', cmd))
        except Exception as e:                      # noqa: BLE001 -- any raise is the failure being looked for
            problems.append((str(e), cmd))

    # ---- 2. the commands a compile and a link issue ----
    names = {}
    for name in ('MAC_METAL', 'MAC_RUN', 'MAC_LINK'):
        node = assigned_list(tree, name)
        if node is not None:
            names[name] = tokens(node, names)
    d = tempfile.mkdtemp()
    src, air, lib = (os.path.join(d, n) for n in ('a.metal', 'a.air', 'a.metallib'))
    open(src, 'wb').write(b'using namespace metal;\r\n')
    # The iOS chain (dm/ios/tools/ios_metal.py) keeps its own shapes and is NOT the mac one: it still issues
    # `xcrun -sdk iphoneos metal -c …` and `xcrun -sdk iphoneos metallib <air> -o <lib>`. Both are covered here or the
    # smoke pack finds them instead -- which is exactly what happened on 2026-11-08.
    shapes = [('compile', names.get('MAC_RUN', []) + [src, '-o', air]),
              ('link', names.get('MAC_LINK', []) + [air, '-o', lib]),
              ('ios compile', ['xcrun', '-sdk', 'iphoneos', 'metal', '-c', src, '-o', air]),
              ('ios link', ['xcrun', '-sdk', 'iphoneos', 'metallib', air, '-o', lib])]
    pch = up / PCH
    if pch.is_file():
        shapes.append(('pch', names.get('MAC_RUN', []) + ['-x', 'metal-header', src, '-o', os.path.join(d, 'a.pch')]))
    for what, cmd in shapes:
        if not cmd:
            problems.append(('no %s command found in upstream' % what, []))
            continue
        try:
            shim.run(list(cmd), capture_output=True, text=True)
            if what == 'compile' and not os.path.exists(air):
                problems.append(('compile wrote no intermediate file', cmd))
            if what == 'link' and not os.path.exists(lib):
                problems.append(('link wrote no library', cmd))
        except Exception as e:                      # noqa: BLE001
            problems.append((str(e), cmd))

    # ---- 3. the methods the shim replaces ----
    def params(path, cls, meth):
        # cls None means a module-level function (the shim replaces a method with one that still takes self)
        t = ast.parse(path.read_text(encoding='utf-8'))
        if cls is None:
            for node in t.body:
                if isinstance(node, ast.FunctionDef) and node.name == meth:
                    return [a.arg for a in node.args.args]
            return None
        for node in ast.walk(t):
            if isinstance(node, ast.ClassDef) and node.name == cls:
                for f in node.body:
                    if isinstance(f, ast.FunctionDef) and f.name == meth:
                        return [a.arg for a in f.args.args]
        return None

    up_params = params(up / CACHE, 'Cache', 'compile_split')
    our_params = params(repo / SHIM, None, 'split_into_one_library') or []
    # the shim's replacement is a plain function taking the bound self
    if up_params and our_params and up_params != our_params:
        problems.append(('compile_split signature differs: upstream %s, shim %s' % (up_params, our_params), []))
    shutil.rmtree(d, ignore_errors=True)

    for why, cmd in problems:
        print('FAIL %s\n       %r' % (why, cmd))
    print('%d probe(s), %d command shape(s) checked; %d problem(s)' % (len(probes), len(shapes), len(problems)))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
