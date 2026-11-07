#!/usr/bin/env python3
# 在没有苹果 Metal 编译器的机器上跑着色器出库链（Windows / Linux / 没装 Xcode 的 Mac；给 tools/d2rm_pack.py 用）。
# 用法：nometal_run.py <脚本.py> [参数…]   例：nometal_run.py dmsh_build.py <包目录> <缓存目录>
# 做法：不改出库链的任何一个文件（它们的内容参与翻译链版本，一改内测的着色器库就得整体换代），只在本进程里把它们调外部命令的
# 那一个入口（subprocess.run）接过来，认出下面几条命令自己答，其余原样放行：
#   xcrun -sdk <x> metal --version              → 答「none」（工具指纹里编译器记成不用 → 链版本自成一支，缓存与正常模式互不相混）
#   xcrun -sdk <x> metal -c <源码> -o <中间件>    → 把源码拷成中间件（换行统一成 \n）
#   xcrun -sdk <x> metallib <中间件> -o <库>      → 把中间件原样拷成库
#   which <名字>                                 → 用 Python 自己找
#   cp -c <甲> <乙>                              → 普通拷贝
# 结果：「库文件」（*_b.metallib / *_bf.metallib）里放的是着色器源码。壳（DM_SHELL 构建）的图形层发现库不是编好的就在设备上现编
# （gfx/nvn/nvn_mtl.m），系统会把编好的结果缓存，第二次启动起不再编。
# 少掉的一道：正常模式下苹果编译器顺带验了每份源码能编过；这里不验，编不过的要到设备上用到它时才报红。
# Runs the shader build chain on a machine without Apple's Metal compiler (Windows / Linux / a Mac without Xcode; for use by tools/d2rm_pack.py).
# Usage: nometal_run.py <script.py> [args...]   Example: nometal_run.py dmsh_build.py <package dir> <cache dir>
# How: not a single file of the build chain is modified (their contents feed into the translation chain version, and any change would force a whole new generation of the beta shader libraries); only, within this process, the one entry through which they call external commands
# (subprocess.run) is taken over: the commands below are recognized and answered here, and everything else is passed through unchanged:
#   xcrun -sdk <x> metal --version              → answers "none" (the compiler is recorded as unused in the tool fingerprint → the chain version forms its own branch, and its cache never mixes with normal mode's)
#   xcrun -sdk <x> metal -c <source> -o <intermediate file>    → copies the source as the intermediate file (line endings normalized to LF)
#   xcrun -sdk <x> metallib <intermediate file> -o <library>      → copies the intermediate file verbatim as the library
#   which <name>                                 → looked up by Python itself
#   cp -c <a> <b>                              → ordinary copy
# Result: the "library files" (*_b.metallib / *_bf.metallib) contain shader source. When the graphics layer of the shell (DM_SHELL build) finds that a library is not a compiled one, it compiles it on the device
# (gfx/nvn/nvn_mtl.m); the system caches the compiled result, and from the second launch on nothing is compiled again.
# The one check that is lost: in normal mode Apple's compiler also verifies along the way that every source compiles; here that is not verified, and one that fails to compile only reports a failure when it is used on the device.
import os, runpy, shutil, subprocess, sys, threading, time

_real = subprocess.run

# 侧库（<key>_bx）在这条路上没有：上游把它从编好的库里切出来（dmsh_side.cut 在 MTLB 二进制里挑函数），这里没有编译器。
# 所以不去假造那个二进制的活，改成让**编译单元**（<key>_bu.metal，dmsh_side.unit 的产物，本来就已经同时含 plain 入口和
# <entry>_a / <entry>_l）整份当 <key>_b 的库交出去，并且不写 <key>_bx。设备端先找侧库，找不到就用基础库里的这些入口
# （nvn_mtl.m 的 at_fn / lo_fn）。代价：库大约大 19.6%（283 个像素着色器实测，每个约 +3 KB）；侧库也写一份同样文本是 +100%。
# 这条不碰任何参与链版本的译器文件（dmsh_mtlcache.py 不在 dmsh_chain.FILES 里），所以链号不变、已发的库照旧复用。
# The side library (<key>_bx) cannot exist on this route: upstream cuts it out of the compiled library (dmsh_side.cut picks
# functions out of the MTLB binary) and there is no compiler here. Rather than fake that binary step, the compile unit
# (<key>_bu.metal, dmsh_side.unit's output, which already holds the plain entry points and <entry>_a / <entry>_l) becomes the
# <key>_b library itself, and no <key>_bx is written. The host tries the side library first and falls back to these entry
# points in the base library (nvn_mtl.m at_fn / lo_fn). Cost: libraries about 19.6% larger (283 pixel shaders measured, about
# +3 KB each); writing the same text as a side library too would be +100%. No file that feeds the chain version is touched
# (dmsh_mtlcache.py is not in dmsh_chain.FILES), so the chain name stays and earlier libraries keep being reused.
SIDE_KEYS = set()   # pixel shaders whose one library also holds their alpha test / logic operation entry points
SIDE_DIRS = set()   # the library folders that got them (the stamp goes there)
SIDE_STAMP = '_b_side_one.stamp'   # not a suffix shader_lib.APP_SUFFIXES takes, so it stays out of the package


def split_into_one_library(self, target, msl, a, b, split):
    """dmsh_mtlcache.Cache.compile_split on a machine without Apple's compiler: the unit's source (msl) is the library, and
    the side library is not written (it would be the same text twice). Raises nothing, so dmsh_build.ps_libraries counts the
    shader as built and names its entry points in the reflection, which is what the host reads (has_alpha / has_logic)."""
    with open(msl, 'rb') as f: src = f.read().replace(b'\r\n', b'\n')   # 与 `metal -c` 那条一样归一 LF：同样的输入各系统才会出同样的包 | normalized to LF as the `metal -c` branch above, or a Windows pack differs from a Mac's
    SIDE_KEYS.add(os.path.basename(a))
    SIDE_DIRS.add(os.path.dirname(os.path.abspath(a)))
    try:
        with open(a, 'rb') as f:
            if f.read() == src:   # 上一轮已经写过同一份：第二次打包不必重写两万份（这里没有 CAS，判据就是这个比较）
                try: os.unlink(b)
                except OSError: pass
                return            # already this very text (a second pack of the same game): no reason to write it again
    except OSError: pass
    tmp = f'{a}.tmp-{os.getpid()}-{threading.get_ident()}'
    with open(tmp, 'wb') as g: g.write(src)
    for i in range(40):   # os.replace onto a file a scanner still has open raises WinError 5 on Windows (dmsh_mtlcache._replace)
        try: os.replace(tmp, a); break
        except PermissionError:
            if i == 39: raise
            try: os.unlink(a)
            except OSError: pass
            time.sleep(0.05)
    try: os.unlink(b)     # an earlier build on another machine may have left a compiled one here (a Mac's, of another chain)
    except OSError: pass


def patch_side_libraries():
    """Makes the one library carry the side entry points, for every cache the shader build makes (dmsh_build.py's MTL)."""
    try: import dmsh_mtlcache
    except ImportError: return False
    dmsh_mtlcache.Cache.compile_split = split_into_one_library
    return True


def _done(args, out=''):
    return subprocess.CompletedProcess(args, 0, stdout=out, stderr='')


def run(args, *a, **k):
    text = k.get('text') or k.get('universal_newlines')
    enc = (lambda s: s) if text else (lambda s: s.encode())
    if isinstance(args, (list, tuple)) and args:
        c = [str(x) for x in args]
        if c[0] == 'xcrun' and 'metal' in c and '--version' in c: return _done(args, enc('none\n'))
        # dmsh_mtlcache.py fingerprints the toolchain with four probes; without a compiler they answer fixed values, so this
        # branch of the chain version is the same on every machine and its cache never mixes with the compiled one.
        if c[0] == 'xcrun' and 'metallib' in c and '--version' in c: return _done(args, enc('none\n'))
        # `xcrun -sdk macosx -f metal` / `-f metallib` and `--show-sdk-path`: upstream's dmsh_build runs them at import
        # time with check=True to name the compilers. There is no compiler here, but an answer is required or the whole
        # chain stops before it starts. Answer `xcrun` itself so MAC_RUN still begins with xcrun and the branches below
        # recognise it; the sdk path only ever travels as an -isysroot argument.
        # 上游 dmsh_build 在 import 时用 check=True 取编译器路径：这里没有编译器，但必须答出一个值，否则整条链在起点就断。
        # 答成 xcrun 本身，MAC_RUN 的首个参数仍是 xcrun，下面的拦截才认得出来。
        if c[0] == 'xcrun' and '-f' in c: return _done(args, enc('xcrun\n'))
        if c[0] == 'xcrun' and '--show-sdk-path' in c: return _done(args, enc('/nometal_run/iPhoneOS.sdk\n'))
        if c[0] == 'xcrun' and '--show-sdk-version' in c: return _done(args, enc('26.0\n'))
        if c[0] == 'xcrun' and '--show-sdk-build-version' in c: return _done(args, enc('26A000\n'))
        # 预编译头（上游 dmsh_pch.py，2026-11-04 起）：这里没有编译器，做不出也不该假造一份 pch —— 这一条**故意失败**，
        # dmsh_pch 会打一行 note 后照常不带 pch 编。这条路本来就一次编译都不做（库文件里放的是源码），所以没有代价。
        # A precompiled header (upstream dmsh_pch.py): there is no compiler here to build one and it must not be faked, so this one
        # fails on purpose -- dmsh_pch prints a note and compiles without it, which costs nothing on this route (nothing is compiled).
        if c[0] == 'xcrun' and 'metal' in c and 'metal-header' in c:
            return subprocess.CompletedProcess(args, 1, stdout=enc(''), stderr=enc('nometal_run：这里没有 Metal 编译器，不预编译头（编译照常进行）\n'))
        # 源码走标准输入（同一批上新的调用形式：`metal -c -x metal - -o air`，见 dmsh_pch._run）：参数里看不出源码是哪个，
        # 要从 stdin 取。写进中间件时仍把换行归一成 \n，理由同下面那条。
        # The source on standard input (`metal -c -x metal - -o air`, dmsh_pch._run): the arguments no longer name it, so the bytes
        # come from stdin. Still normalized to LF for the reason given below.
        if c[0] == 'xcrun' and 'metal' in c and '-x' in c and '-o' in c and c[c.index('-x') + 1:c.index('-x') + 2] == ['metal'] and '-' in c:
            o = c.index('-o'); f = k.get('stdin'); b = f.read() if f is not None else b''
            with open(c[o + 1], 'wb') as g: g.write(b.replace(b'\r\n', b'\n'))
            return _done(args, enc(''))
        if c[0] == 'xcrun' and 'metal' in c and '-c' in c and '-o' in c:   # 源码 = metal 之后第一个不是选项、也不是 -o 的值的参数（iOS 那条命令在 -c 后面还带一个版本选项） | source = the first argument after metal that is neither an option nor the value of -o (the iOS command also carries a version option after -c)
            o = c.index('-o'); src = [x for i, x in enumerate(c[c.index('metal') + 1:], c.index('metal') + 1) if not x.startswith('-') and i != o + 1]
            if len(src) != 1: raise RuntimeError('nometal_run：认不出源码参数 %r' % c)
            with open(src[0], 'rb') as f: b = f.read()
            with open(c[o + 1], 'wb') as f: f.write(b.replace(b'\r\n', b'\n'))   # Windows 上 spirv-cross 写出的源码是 \r\n 换行：进安装包前归一，同样的输入各系统出的包才相同 | On Windows spirv-cross writes sources with CRLF line endings: normalize before they go into the install package, so the same input gives the same package on every system
            return _done(args, enc(''))
        if c[0] == 'xcrun' and 'metallib' in c and '-o' in c: shutil.copyfile(c[c.index('metallib') + 1], c[c.index('-o') + 1]); return _done(args, enc(''))
        if c[0] == 'xcrun': raise RuntimeError('nometal_run：没认出的 xcrun 命令 %r' % c)
        if c[0] == 'which' and len(c) == 2: return _done(args, enc((shutil.which(c[1]) or '') + '\n'))
        if c[0] == 'cp' and len(c) == 4 and c[1] == '-c': shutil.copyfile(c[2], c[3]); return _done(args, enc(''))
    return _real(args, *a, **k)


if __name__ == '__main__':
    if len(sys.argv) < 2: sys.exit(__doc__ or '用法：nometal_run.py <脚本.py> [参数…]')
    subprocess.run = run
    patched = patch_side_libraries()
    if not patched: print('nometal_run: dmsh_mtlcache is not importable here, so the libraries keep no alpha test / logic operation entry points', flush=True)
    else: print('nometal_run: no Apple compiler, so no <key>_bx is cut: one library per pixel shader carries its alpha test and logic operation entry points (the device takes them from it)', flush=True)
    def _report():   # 判据（铁律 15）：没这行就当没接线 | the line that proves this took effect (rule 15: no line, not wired)
        if not SIDE_KEYS: return
        for d in SIDE_DIRS:   # 给 nxn_pack.step_translate 的 done() 看：没有这个戳的旧库要重建一次，否则复用上一轮没有侧入口的库
            try:
                with open(os.path.join(d, SIDE_STAMP), 'w') as f: f.write(f'{len(SIDE_KEYS)} shaders: the one library holds the alpha test and logic operation entry points too (nometal_run.py)\n')
            except OSError: pass
        print(f'nometal_run: {len(SIDE_KEYS)} pixel shader libraries hold their alpha test / logic operation entry points too (no side library on this route)', flush=True)
    if patched: import atexit; atexit.register(_report)
    script = os.path.abspath(sys.argv[1]); sys.argv = sys.argv[1:]; sys.path.insert(0, os.path.dirname(script))
    runpy.run_path(script, run_name='__main__')
