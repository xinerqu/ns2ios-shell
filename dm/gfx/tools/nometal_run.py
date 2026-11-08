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
import glob, os, runpy, shutil, struct, subprocess, sys, tempfile, threading, time

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


# ---- Apple's own compiler, when the machine has it ---------------------------------------------------------------
# 装了 Apple 官方 Metal Developer Tools for Windows 的机器上，那两条命令就真的交给它做：包里放的是编好的库，手机首启
# 不用再编。Apple 的东西一个字节都不随我们分发（许可不允许），所以只在用户自己装了的时候用；任何一步不成立就退回本文件
# 原来的行为（源码进包、设备端编），绝不会产出半编半源码的包。
# On a machine with Apple's Metal Developer Tools for Windows the two commands are handed to it for real: the package then holds
# compiled libraries and the phone compiles nothing on first launch. None of Apple's files ship with us (the licence forbids it),
# so they are used only when the user installed them, and any doubt falls back to this file's own behaviour -- never half compiled.
TOOLS_ENV = 'NXN_METAL_TOOLS'   # a folder holding metal.exe/metallib.exe, or 'off' to behave as if they are absent
PROBE_MSL = ('#include <metal_stdlib>\nusing namespace metal;\n'
             'fragment float4 dm_probe_frag(float4 p [[position]]) { return p; }\n')
PROBE_FLAGS = ['-O1', '-mios-version-min=18.0', '-fmetal-math-mode=relaxed', '-fpreserve-invariance']
_TOOLS = None      # None = not looked yet; False = absent or unusable; dict = usable


def _exe(kind):
    """metal / metallib: the folder in NXN_METAL_TOOLS first, then an installed Metal Developer Tools, then PATH."""
    e = os.environ.get(TOOLS_ENV, '').strip()
    if e.lower() in ('off', '0', 'no', 'none'): return None
    names = (kind + '.exe', kind) if os.name == 'nt' else (kind,)
    for n in names:
        p = os.path.join(e, n) if e else None
        if p and os.path.isfile(p): return [p]
    # 安装包默认落在 Program Files 下的 "Metal Developer Tools"，用户通常不把它加进 PATH：两层目录里找一遍。
    # The installer puts them under Program Files ("Metal Developer Tools") and users rarely add that to PATH: look two
    # levels down, bounded, so this stays cheap.
    if os.name == 'nt':
        for root in (os.environ.get('ProgramFiles'), os.environ.get('ProgramW6432'), os.environ.get('ProgramFiles(x86)')):
            if not root or not os.path.isdir(root): continue
            for base in sorted(glob.glob(os.path.join(root, 'Metal Developer Tools*'))):
                for depth in ('', 'bin', os.path.join('usr', 'bin')):
                    for n in names:
                        p = os.path.join(base, depth, n)
                        if os.path.isfile(p): return [p]
    for n in names:
        p = shutil.which(n)
        if p: return [p]
    return None


def library_names(path):
    """The functions a Metal library lists, or None when the file is not one this route can read. The same header layout
    pso_offline.functions and dmsh_side.cut use: 'MTLB', the file's size at 0x10, the function list's offset at 0x18."""
    try:
        with open(path, 'rb') as f: b = f.read()
        if b[:4] != b'MTLB' or len(b) < 0x58: return None
        if struct.unpack_from('<Q', b, 0x10)[0] != len(b): return None
        p = struct.unpack_from('<Q', b, 0x18)[0]
        count = struct.unpack_from('<I', b, p)[0]; p += 4; names = []
        for _ in range(count):
            size = struct.unpack_from('<I', b, p)[0]; q, end, nm = p + 4, p + size, None
            while q + 4 <= end:
                tag = b[q:q + 4]; q += 4
                if tag == b'ENDT': break
                n = struct.unpack_from('<H', b, q)[0]; q += 2
                if tag == b'NAME': nm = b[q:q + n].rstrip(b'\0').decode('utf-8', 'replace')
                q += n
            if nm: names.append(nm)
            p += size
        return names
    except (OSError, struct.error, UnicodeDecodeError):
        return None


# 编译器也会拒掉的着色器（实测：`dmsh_side.unit` 生成的 alpha test / logic operation 入口把颜色声明成片元函数的
# **输入**，Apple 的编译器报 `invalid 'color' attribute for input declaration in a fragment function`；`dmsh_fetch` 的
# 可读 tile 变体同理）。这类只能照今天这条路办——库里放源码，设备端现编（设备的编译器接受它）。所以：编不过的那一份
# 记下来，链接那一步把源码写成库。
# Shaders the compiler itself refuses (measured: the alpha test / logic operation entry points of dmsh_side.unit declare the
# colours as fragment **inputs**, which Apple's compiler rejects with `invalid 'color' attribute for input declaration in a
# fragment function`; dmsh_fetch's readable-tile variants likewise). Those keep this route's own answer -- the library holds
# source and the device compiles it, which that compiler accepts. So a compile that fails is remembered, and the link step
# writes the source out as the library.
SOURCE_FALLBACK = {}   # absolute air path -> absolute source path
_FALLBACK_LOCK = threading.Lock()


def tools():
    """{'metal': path, 'metallib': path} when both are installed **and** a shader compiles and links through them, else False.
    The probe runs once per process with this chain's own flags, so a tool this build cannot drive counts as absent."""
    global _TOOLS
    if _TOOLS is not None: return _TOOLS
    _TOOLS = False
    m, l = _exe('metal'), _exe('metallib')
    if not (m and l):
        if m or l: print('nometal_run: only one of metal/metallib was found; using the source-in-the-package route', flush=True)
        return _TOOLS
    d = tempfile.mkdtemp(prefix='dm-metal-probe-')
    src, air, lib = (os.path.join(d, n) for n in ('probe.metal', 'probe.air', 'probe.metallib'))
    try:
        with open(src, 'w', encoding='utf-8', newline='\n') as f: f.write(PROBE_MSL)
        c = subprocess.run([*m, '-c', *PROBE_FLAGS, src, '-o', air], capture_output=True, text=True)
        if c.returncode != 0:
            print('nometal_run: metal.exe cannot compile a probe shader (%s); using the source route' % _last(c), flush=True)
            return _TOOLS
        c = subprocess.run([*l, air, '-o', lib], capture_output=True, text=True)
        if c.returncode != 0:
            print('nometal_run: metallib.exe cannot link a probe (%s); using the source route' % _last(c), flush=True)
            return _TOOLS
        names = library_names(lib) or []
        if 'dm_probe_frag' not in names:
            print('nometal_run: the probe library lists %d functions and not the one compiled for (metallib.exe writes a file '
                  'this route cannot read?); using the source route' % len(names), flush=True)
            return _TOOLS
        _TOOLS = {'metal': m, 'metallib': l}
        print("nometal_run: Apple's Metal tools found (%s) -- libraries are compiled here, the phone will not compile them"
              % os.path.dirname(m[0]), flush=True)
    except OSError as e:
        print('nometal_run: the Metal tools could not be run (%s); using the source route' % e, flush=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return _TOOLS


def _last(c):
    """The last line of what a failed tool said, for the one-line note above. `subprocess.run` hands back bytes when the
    caller did not ask for text (dmsh_pch compiles from standard input without it) and str when it did, so both are read."""
    err, out = c.stderr, c.stdout
    if isinstance(err, bytes) or isinstance(out, bytes): text = ((err or b'') + (out or b'')).decode('utf-8', 'replace')
    else: text = (err or '') + (out or '')
    t = text.strip().splitlines()
    return t[-1][:200] if t else 'no output'


def _without_sdk(a):
    """The same arguments minus `-sdk <name>` / `-isysroot <path>`: on Windows the tools carry their own headers and there is no SDK."""
    out, i = [], 0
    while i < len(a):
        t = a[i]
        if t in ('-sdk', '-isysroot') and i + 1 < len(a): i += 2; continue
        if t.startswith('-isysroot=') or t.startswith('--sdk='): i += 1; continue
        out.append(t); i += 1
    return out


def as_tool(c):
    """('metal'|'metallib', arguments) for one of this route's two calls, or (None, None) for a shape it does not know.

    Upstream builds `xcrun -isysroot <sdk> -c ... <source> -o <air>` (no program word: the path came from `xcrun -f metal`) and
    the iOS chain the older `xcrun -sdk iphoneos metallib <air> -o <lib>`; both are recognised, and what is left is what the
    Windows tool takes."""
    a = _without_sdk(c[1:]) if c and c[0] == 'xcrun' else []
    if a and a[0] in ('metal', 'metallib'): return a[0], a[1:]
    if '-c' in a: return 'metal', a
    if '-o' in a: return 'metallib', a
    return None, None

def _compile_with(t, src, out, keep_as_is=False):
    """Compiles `src` into the library `out` with the tools in `t`. False when the compiler refuses the source (or the link
    fails), in which case `out` is left alone. The intermediate file never outlives the call."""
    air = out + '.air'
    try:
        r = subprocess.run([*t['metal'], '-c', *PROBE_FLAGS, src, '-o', air], capture_output=True, text=True)
        if r.returncode != 0:
            print('nometal_run: metal refused %s (%s); that library stays source, the phone compiles it'
                  % (os.path.basename(src), _last(r)), flush=True)
            return False
        r = subprocess.run([*t['metallib'], air, '-o', out], capture_output=True, text=True)
        if r.returncode != 0:
            print('nometal_run: metallib refused %s (%s); that library stays source, the phone compiles it'
                  % (os.path.basename(src), _last(r)), flush=True)
            return False
        return True
    finally:
        try: os.unlink(air)
        except OSError: pass


def split_into_one_library(self, target, msl, libs, split):
    # upstream renamed the two paths to one list (`compile_split(self, target, msl, libs, split)`): libs[0] is the
    # library this route fills with the source, the rest are the side libraries it deliberately does not write.
    a = libs[0]; b = libs[1] if len(libs) > 1 else None
    """dmsh_mtlcache.Cache.compile_split on this route: the unit's source (msl) becomes one library that also carries the
    alpha test / logic operation entry points, and no side library is cut. With Apple's tools installed the unit is compiled
    when it compiles; when it does not -- the side entry points declare colours as fragment **inputs**, which Apple's compiler
    rejects -- the source is what goes in, as it always has on this route: the device's compiler accepts it. Either way nothing
    is cut, so `dmsh_side.cut` never sees a file it cannot read. Raises nothing, so dmsh_build.ps_libraries counts the shader
    as built and names its entry points in the reflection, which is what the host reads (has_alpha / has_logic)."""
    SIDE_KEYS.add(os.path.basename(a))
    SIDE_DIRS.add(os.path.dirname(os.path.abspath(a)))
    t = tools()
    if t and _compile_with(t, msl, a, keep_as_is=True):
        if b:
            try: os.unlink(b)
            except OSError: pass
        return
    with open(msl, 'rb') as f: src = f.read().replace(b'\r\n', b'\n')   # 与 `metal -c` 那条一样归一 LF：同样的输入各系统才会出同样的包 | normalized to LF as the `metal -c` branch above, or a Windows pack differs from a Mac's
    try:
        with open(a, 'rb') as f:
            if f.read() == src:   # 上一轮已经写过同一份：第二次打包不必重写两万份（这里没有 CAS，判据就是这个比较）
                if b:
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
    if b:
        try: os.unlink(b)   # an earlier build on another machine may have left a compiled one here (a Mac's, of another chain)
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
        if c[0] == 'xcrun' and '--version' in c:
            # 装了真工具就报它自己的版本：链号因此与「没有编译器」那支不同，两边的缓存不会混。
            # With the real tools installed their own version is reported: the chain version then differs from the no-compiler
            # branch, so the two caches never mix.
            t = tools()
            if t:
                prog = 'metallib' if 'metallib' in c else 'metal'
                r = _real([*t[prog], '--version'], *a, **k)
                if r.returncode == 0: return r
                print('nometal_run: %s --version failed, reporting no compiler' % prog, flush=True)
            return _done(args, enc('none\n'))
        # dmsh_mtlcache.py fingerprints the toolchain with four probes; without a compiler they answer fixed values, so this
        # branch of the chain version is the same on every machine and its cache never mixes with the compiled one.
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
        if c[0] == 'xcrun' and 'metal-header' in c:
            return subprocess.CompletedProcess(args, 1, stdout=enc(''), stderr=enc('nometal_run：这里没有 Metal 编译器，不预编译头（编译照常进行）\n'))
        # 源码走标准输入（同一批上新的调用形式：`metal -c -x metal - -o air`，见 dmsh_pch._run）：参数里看不出源码是哪个，
        # 要从 stdin 取。写进中间件时仍把换行归一成 \n，理由同下面那条。
        # The source on standard input (`metal -c -x metal - -o air`, dmsh_pch._run): the arguments no longer name it, so the bytes
        # come from stdin. Still normalized to LF for the reason given below.
        if c[0] == 'xcrun' and '-x' in c and '-o' in c and c[c.index('-x') + 1:c.index('-x') + 2] == ['metal'] and '-' in c:
            o = c.index('-o'); f = k.get('stdin'); b = f.read() if f is not None else b''
            t = tools(); prog, rest = as_tool(c)
            if t and prog == 'metal':
                # 有编译器就照 Mac 的行为编：编得过就是真库。编不过的那一份（上面 SOURCE_FALLBACK 那段说的那类）仍旧走
                # 这条路的老办法——把源码写进中间件、记下它，等链接那一步把源码当库写出去。这样「能编的都编了，
                # 编不了的也照旧能用」，不会出现 metallib 拿到源码报 Invalid bitcode file 的连锁失败。
                # With a compiler this is a real compile; one it refuses keeps this route's old answer -- the source is written
                # into the intermediate file and remembered, and the link step writes it out as the library. Everything that
                # compiles is compiled, what cannot still works, and metallib never sees source.
                kw = {x: y for x, y in k.items() if x != 'stdin'}
                r = _real([*t['metal'], *rest], input=b, **kw)
                if r.returncode == 0: return r
                air = c[c.index('-o') + 1]
                print('nometal_run: metal refused a shader (%s); its library stays source, the phone compiles it'
                      % _last(r), flush=True)
                with _FALLBACK_LOCK:
                    SOURCE_FALLBACK[os.path.abspath(air)] = b.replace(bytes((13, 10)), bytes((10,)))
                with open(air, 'wb') as g: g.write(b.replace(b'\r\n', b'\n'))
                return _done(args, enc(''))
            with open(c[o + 1], 'wb') as g: g.write(b.replace(b'\r\n', b'\n'))
            return _done(args, enc(''))
        # 编译与链接：上游现在用 `xcrun -isysroot <sdk> -c -O1 … <源码> -o <air>`、`xcrun <air> -o <库>`，
        # **argv 里已经没有 `metal` / `metallib` 这两个词了**（编译器路径是 import 时用 `xcrun -f metal` 问出来的），
        # 所以只能按形状认：有 `-o` 就是一次产出，带 `-c` 的是编译（把源码写成中间件），不带的是链接（拷成库）。
        # Compile and link: upstream builds `xcrun -isysroot <sdk> -c -O1 … <source> -o <air>` and `xcrun <air> -o <lib>`,
        # so neither `metal` nor `metallib` appears in argv any more (the compiler path is asked for once with `xcrun -f`).
        # Recognise them by shape instead: `-o` means an output is produced, `-c` means a compile (the source becomes the
        # intermediate file), no `-c` means a link (the intermediate file is copied out as the library).
        if c[0] == 'xcrun' and '-o' in c:
            o = c.index('-o')
            # 有真工具就照原参数交它做，成功与否原样返回：这就是 Mac 上的行为，一个 shader 编不过由上层按 shader 记下来，
            # 不会连累别的。没有工具才走下面这条「源码进包」的路（那里根本没有编译）。
            # With the real tools the call is handed over as it is and the result returned as it is: this is the Mac behaviour,
            # where a shader that does not compile is counted per shader by the caller. Only without them does the source route
            # below run, where nothing is compiled at all.
            t = tools(); prog, rest = as_tool(c)
            if t and prog:
                if prog == 'metallib' and rest:
                    with _FALLBACK_LOCK: src = SOURCE_FALLBACK.pop(os.path.abspath(rest[0]), None)
                    if src is not None:   # 编不过的那一份：库里放源码，设备端照旧现编（今天这条路的行为）
                        with open(c[o + 1], 'wb') as g: g.write(src)
                        return _done(args, enc(''))
                r = _real([*t[prog], *rest], *a, **k)
                if r.returncode == 0 or prog != 'metal': return r
                # 编译器拒了这份源码（实测见 SOURCE_FALLBACK 那段）：把源码写进中间件、记下来，等链接那一步当库写出去。
                # The compiler refused this source (see SOURCE_FALLBACK above): the source goes into the intermediate file and is
                # remembered, and the link step writes it out as the library.
                skip = {i for i, x in enumerate(rest) if x in ('-o', '-x', '-isysroot', '-sdk')}
                skip |= {i + 1 for i in skip}
                inp = [x for i, x in enumerate(rest) if i not in skip and not x.startswith('-')]
                print('nometal_run: metal refused a shader (%s); its library stays source, the phone compiles it'
                      % _last(r), flush=True)
                if inp and os.path.isfile(inp[0]):
                    with open(inp[0], 'rb') as f: srcb = f.read()
                    with _FALLBACK_LOCK:
                        SOURCE_FALLBACK[os.path.abspath(c[o + 1])] = srcb.replace(bytes((13, 10)), bytes((10,)))
                    with open(c[o + 1], 'wb') as g: g.write(srcb.replace(bytes((13, 10)), bytes((10,))))
                    return _done(args, enc(''))
                return r
            # 哪些参数是「选项的值」：-o 的输出、-sdk 的名字、-isysroot 的路径、-x 的方言；程序名本身也要跳。
            # iOS 那条仍是老形状 `xcrun -sdk iphoneos metallib <air> -o <lib>`，`iphoneos` 和 `metallib` 都不是输入文件。
            # Options whose next token is a value, plus the program name: the iOS chain still issues the old
            # `xcrun -sdk iphoneos metallib <air> -o <lib>`, where iphoneos and metallib are not input files either.
            skip = {0}
            for i, t in enumerate(c):
                if t in ('-o', '-sdk', '-isysroot', '-x'): skip.add(i + 1)
            src = [x for i, x in enumerate(c)
                   if i not in skip and not x.startswith('-') and x not in ('metal', 'metallib')]
            if len(src) != 1: raise RuntimeError('nometal_run：认不出这次调用的输入文件 %r' % c)
            if '-c' in c:
                with open(src[0], 'rb') as f: b = f.read()
                with open(c[o + 1], 'wb') as g: g.write(b.replace(b'\r\n', b'\n'))   # Windows 上 spirv-cross 写出的源码是 CRLF：进包前归一，同样的输入各系统出的包才相同 | normalize CRLF so the same input gives the same package everywhere
            else:
                shutil.copyfile(src[0], c[o + 1])
            return _done(args, enc(''))
        if c[0] == 'xcrun': raise RuntimeError('nometal_run：没认出的 xcrun 命令 %r' % c)
        if c[0] == 'which' and len(c) == 2: return _done(args, enc((shutil.which(c[1]) or '') + '\n'))
        if c[0] == 'cp' and len(c) == 4 and c[1] == '-c': shutil.copyfile(c[2], c[3]); return _done(args, enc(''))
    return _real(args, *a, **k)


if __name__ == '__main__':
    # ASCII only, like every line below that this process prints: nxn_pack.step_translate captures this output with
    # text=True, which decodes with the console's code page (GBK on a Chinese Windows), and one undecodable line turns
    # the captured stdout into None -- the packer then died with AttributeError right after tool_lock (2026-11-08).
    if len(sys.argv) < 2: sys.exit(__doc__ or 'usage: nometal_run.py <script.py> [args...]')
    subprocess.run = run
    # 先探一次真工具：装了 Apple 的 Metal Developer Tools for Windows 就走上游那条「编成 metallib + 切侧库」的路，
    # 不再打 compile_split 的补丁（那补丁是给没有编译器的情况准备的）。
    # Probe the real tools first: with Apple's Metal Developer Tools for Windows installed the chain runs upstream's own
    # "compile to a library, then cut the side library" path, so compile_split keeps its own implementation.
    have = tools()
    patched = patch_side_libraries()
    if have:
        # ASCII only (see __main__ below): the packer decodes this with the console's code page. Line 151 above already
        # said the tools were found; this one says what that means for the side library.
        print('nometal_run: so no <key>_bx is cut: a unit that compiles becomes one library, and one that does not stays source',
              flush=True)
    elif not patched: print('nometal_run: dmsh_mtlcache is not importable here, so the libraries keep no alpha test / logic operation entry points', flush=True)
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
