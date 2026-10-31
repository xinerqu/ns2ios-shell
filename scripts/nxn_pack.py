#!/usr/bin/env python3
"""Pack the game-free shell and your own game files into an installable IPA, on Windows, Linux or macOS.

No Mac, no Xcode and no paid Apple developer account: the shell (built once by dm/ios/tools/shell_build.sh, or downloaded
from the Build shell workflow) supplies the app, this script supplies the game. Apple's two Mac-only steps -- `xcrun metal`
and `xcodebuild` -- are the ones the shell split removed.

Usage:
  python scripts/nxn_pack.py <shell.zip> <NSP folder> -k <prod.keys> [-o out.ipa] [-p vN] [--stop-after STEP]
  python scripts/nxn_pack.py --clean <NSP folder>          # remove what an earlier run left behind, and exit

  <NSP folder>   one game's files: the base game (.nsp, or a cartridge dump .xci) plus every update the game profile's
                 shader_versions lists (see dm/games/<title ID>/game.tsv). DLC NSPs are packed too when present.
  -k            prod.keys (also read from the KEYS environment variable)
  -p            the version to pack (default: the newest one in the folder)
  --stop-after  stop after a step, to bring the chain up one piece at a time. Steps:
                extract, rewrite, shaders, guest, assemble
                (there is no "pack": packing is the last thing that happens, so stopping before it is the same as a run
                that failed there, and stopping after it is just a run)
  --force       redo steps whose output already exists
  --keep-work   keep the working folder (<NSP folder>/<title ID>_pack) instead of removing it after a good pack, so a
                rerun reuses the extraction, the rewritten modules and the compiled shaders. Worth it when repacking the
                same game repeatedly; the folder is several times the size of the game.
  --clean       delete that working folder without packing anything
  --no-shaders  an OpenGL/Vulkan game: build it with no shader library, so it boots (music, no picture) and can capture
                its own shaders -- the first step of the route below
  --gl-capture  an OpenGL game's captured shaders (<key>.vert / <key>.frag, written by DM_GL_CAPTURE): translated to Metal
                sources here and compiled on the phone, so the package draws. This is the second step of that route.

Requirements: Python 3.9+ with lz4, capstone, fonttools and pillow and the executables from the release (nx_extract,
dmsh_xlate, spirv-cross, glslangValidator for an OpenGL game's --gl-capture, and zstd for a game whose files are
zstd-compressed) on PATH or in --tools. Every step says what is missing before it starts. The release's
nxnative-windows-tools.zip holds all five, so nothing has to be installed by hand.

Only your own game files go in, and the IPA it produces contains them: it is for your own use, do not distribute it.
"""
import argparse, concurrent.futures as cf, json, os, plistlib, re, shlex, shutil, struct, subprocess, sys, tempfile, time, zipfile
from pathlib import Path

PY = sys.executable
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if not (REPO / 'dm').is_dir() and (HERE / 'dm').is_dir():
    REPO = HERE      # also works laid out flat, as nxn_pack.py next to the dm/ it drives
DM = REPO / 'dm'
GFX = DM / 'gfx' / 'tools'
IOS = DM / 'ios'
TOOLS = DM / 'tools'

# What an update is called when its version cannot be read: its file name has no [v<N>] tag and the package keeps only an
# encrypted cnmt.nca (no plaintext cnmt.xml to take <Version> from). The string is a path component -- extract/<ver> and
# extract_<ver>.log -- so it has to be legal on every platform; "v?" was not (Windows rejects '?' in a name with
# OSError 22), and it still sorts above "v0" because 'u' sorts above any digit, so such an update stays the newest to pack.
UNKNOWN_VER = 'vunknown'


def say(s): print('[%s] %s' % (time.strftime('%H:%M:%S'), s), flush=True)
def die(s): sys.exit('[x] ' + s)
def warn(s): print('[!] ' + s, flush=True)


def folder_size(p):
    """Bytes under p, without following a symlink out of it: the game's RomFS can be large and the count is only a report."""
    n = 0
    for root, dirs, files in os.walk(p, followlinks=False):
        for f in files:
            try: n += os.lstat(os.path.join(root, f)).st_size
            except OSError: pass
    return n


def tidy(work, keep_logs=True):
    """Remove what the pack no longer needs, and return the bytes freed.

    Everything in here is either derivable from the game files again (the extraction, the rewritten modules, the split
    shaders, the assembled stage tree) or a cache the next run rebuilds. logs/ can stay: it is a few KB, and it is the only
    thing that explains a package that misbehaves on the phone. Each child is removed through its own path, and only if it
    really is inside the work folder, so a symlinked RomFS cannot take anything else with it.
    """
    total = 0
    root = work.resolve()
    for d in sorted(work.iterdir()):
        if keep_logs and d.name == 'logs': continue
        if d.parent.resolve() != root:
            warn('not removing %s: it is not inside %s' % (d, work)); continue
        n = folder_size(d) if d.is_dir() and not d.is_symlink() else d.lstat().st_size
        try:
            if d.is_dir() and not d.is_symlink(): shutil.rmtree(d)
            else: d.unlink()
        except OSError as e:
            warn('could not remove %s (%s): leaving it' % (d.name, e)); continue
        total += n
        say('  removed %-12s %9.1f MB' % (d.name + ('/' if d.is_dir() else ''), n / 1048576))
    return total


def clean_folders(folder):
    """--clean: everything a previous run left in this folder, logs included."""
    if not folder.is_dir(): die('no such folder: %s' % folder)
    works = sorted(p for p in folder.iterdir() if p.is_dir() and p.name.endswith('_pack'))
    if not works: say('nothing to clean: %s holds no <title ID>_pack folder' % folder); return
    freed = 0
    for w in works:
        say('cleaning %s' % w.name)
        freed += tidy(w, keep_logs=False)
        try: w.rmdir()
        except OSError: pass
    say('freed %.1f MB' % (freed / 1048576))


def shell_stamp(shell):
    """What the shell says it can do: dm/shell_info.txt, written by dm/ios/tools/shell_build.sh. A shell from before that
    stamp grew these keys has an empty or partial answer, which is itself the answer."""
    if not Path(shell).is_file(): return {}     # a capture run has no shell, and needs none
    with zipfile.ZipFile(shell) as z:
        names = [n for n in z.namelist() if n.startswith('Payload/') and n.endswith('/dm/shell_info.txt')]
        if not names: return {}
        txt = z.read(names[0]).decode('utf-8', 'replace')
    st = {}
    for line in txt.splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            st[k.strip()] = v.strip()
    return st


def check_shell(stamp):
    """Refuse a shell this packer cannot fill in sensibly. Both failures below build a package that is fine until it runs,
    so they are hard errors -- but only on a stamp that says so: a key that is missing means the shell predates the stamp,
    which is unknown rather than bad (the first shells shipped before it grew these keys)."""
    if not stamp:
        warn('this shell carries no dm/shell_info.txt, so it is not from this project\'s shell route: what the packer '
             'writes may not be what it reads. Use the shell from the release.')
        return
    if stamp.get('GUEST') == '0':
        die('this shell cannot load Frameworks/Guest.framework (it was built before the shell route): the game modules '
            'would never run. Use the shell from the release.')
    if stamp.get('ENV') == '0':
        die('this shell ignores dm/env.txt, so the game would get the placeholder memory layout instead of its own: the '
            'heap request it makes from it is what overflows the pool (SetHeapSize). Use the shell from the release.')
    missing = [k for k in ('GUEST', 'ENV', 'GATES') if k not in stamp]
    if missing:
        warn('this shell predates %s in dm/shell_info.txt, so it cannot be checked against this packer. If the app stops '
             'on the phone with "unknown gate kind ...", this is why: use the shell from the same release.' % ', '.join(missing))
    if stamp.get('MEM') != '1':
        # Not fatal: every game profile fits in 3,285 MiB, which an unentitled app is allowed. It shows up when a game needs
        # more than that, and then it is a memory kill with no crash log rather than a crash.
        warn('this shell carries no increased-memory-limit entitlement, so iOS caps the app at about 3,375 MB (the boot line '
             'in Documents/log.txt says which). The bigger games can be killed for memory on it: use the shell from the '
             'current release.')


def check_gates(stamp, patched, mods):
    """Every gate kind the rewriter put into a module must be one the shell dispatches.

    The rewriter turns an svc into a call the host looks up by kind (dm/host/hle.c). A shell built before a kind existed
    registers it as unknown and aborts while loading the modules -- on the phone, after a successful build, with "unknown
    gate kind spin". Catching it here costs a second and names the fix.
    """
    kinds = set()
    for m in mods:
        f = patched / (m + '.gates.tsv')
        if not f.is_file(): continue
        for line in f.read_text(errors='replace').splitlines():
            p = line.split('\t')
            if len(p) >= 2: kinds.add(p[1])
    if not kinds: return
    allowed = set(stamp.get('GATES', '').split())
    if not allowed:
        warn('the shell does not say which gate kinds it dispatches, and this game needs %s: if it stops while loading '
             'the modules with "unknown gate kind", it is too old for this packer' % ' '.join(sorted(kinds)))
        return
    missing = kinds - allowed
    if missing:
        die('this shell cannot dispatch the %s gate(s) this game needs (it dispatches: %s).\n'
            '    It is older than this packer: download the current shell from the release.'
            % (', '.join(sorted(missing)), ' '.join(sorted(allowed))))
    say('gates: %s (the shell dispatches: %s)' % (' '.join(sorted(kinds)), ' '.join(sorted(allowed))))


def run(cmd, env=None, cwd=None, log=None, what=None, hint=None):
    """Run a command, echoing it and keeping its output if a log path is given.

    hint: called with the log text when the command fails. A truthy return replaces the generic message, for a step whose
    tool can only describe a Mac-only limitation in its own terms (shader_split.py's DM_OODLE_HOST): the packer should say
    what that means for someone packing on Windows rather than pass the tool's message through.
    """
    if what: say(what)
    if os.environ.get('NXN_VERBOSE'): print('    $ ' + ' '.join(str(c) for c in cmd), flush=True)
    e = dict(os.environ, PYTHONUTF8='1')
    if env: e.update({k: str(v) for k, v in env.items()})
    t0 = time.time()
    if log:
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        with open(log, 'w', encoding='utf-8', errors='replace') as f:
            r = subprocess.run([str(c) for c in cmd], env=e, cwd=str(cwd) if cwd else None, stdout=f, stderr=subprocess.STDOUT)
    else:
        r = subprocess.run([str(c) for c in cmd], env=e, cwd=str(cwd) if cwd else None)
    if r.returncode:
        text = ''
        if log and Path(log).is_file():
            text = open(log, errors='replace').read()
        if hint and text:
            msg = hint(text)
            if msg: die(msg)
        tail = ''
        if text:
            tail = '\n---- last lines of %s ----\n%s' % (log, '\n'.join(text.splitlines()[-25:]))
        die('%s failed (exit %d)%s' % (what or cmd[0], r.returncode, tail))
    if what: say('    done in %.1fs' % (time.time() - t0))
    return r


def which(*names):
    for n in names:
        p = shutil.which(n)
        if p: return p
    return None


# ---------------------------------------------------------------- environment

def check_env(keys_path, prebuilt=None):
    """Only what every step needs. Tools for a single step are checked when that step is reached, so the chain can be
    brought up piece by piece with --stop-after without installing everything first."""
    bad = []
    for m, pkg in (('lz4.block', 'lz4'), ('capstone', 'capstone'), ('fontTools', 'fonttools'), ('PIL', 'pillow')):
        try: __import__(m)
        except Exception: bad.append('Python package %s:  %s -m pip install %s' % (m, PY, pkg))
    try:
        import compression.zstd                                    # built in from Python 3.14
    except Exception:
        try: __import__('backports.zstd')
        except Exception:
            # the release's zstd.exe does the same job and --tools is already on PATH by now, so a user never has to
            # install a Python package for this
            if not which('zstd'):
                bad.append('a zstd decoder: the release\'s nxnative-windows-tools.zip has zstd.exe (pass it with --tools), '
                           'or put zstd on PATH, or %s -m pip install backports.zstd' % PY)
    dotnet = which('dotnet') or (str(Path.home() / '.dotnet' / 'dotnet') if (Path.home() / '.dotnet' / 'dotnet').is_file() else None)
    if prebuilt:
        # nx_extract and the shader translator come prebuilt, so no .NET SDK is needed on this machine
        pass
    elif not dotnet:
        bad.append('.NET 10 SDK: https://dotnet.microsoft.com/download (or pass --tools with the prebuilt executables)')
    else:
        # having dotnet.exe proves nothing: a runtime-only install has no compiler and `dotnet build` fails later
        r = subprocess.run([dotnet, '--list-sdks'], capture_output=True, text=True)
        if not re.search(r'^10\.', (r.stdout or ''), re.M):
            bad.append('.NET 10 SDK (this dotnet has no 10.x SDK; found: %s)' % ((r.stdout or '').strip() or 'none'))
    if not Path(keys_path).is_file(): bad.append('prod.keys not found: %s' % keys_path)
    if bad: die('missing %d item(s):\n  - %s' % (len(bad), '\n  - '.join(bad)))
    return dotnet


def need_tool(name, why, hint):
    p = which(name)
    if not p: die('%s is needed to %s.\n    %s' % (name, why, hint))
    return p


# ---------------------------------------------------------------- profile

UPSTREAM_API = 'https://api.github.com/repos/seedds/nxnative/contents/dm/games/{tid}?ref=main'
UPSTREAM_RAW = 'https://raw.githubusercontent.com/seedds/nxnative/main/dm/games/{tid}/{name}'


def fetch_profile(tid):
    """A game upstream added after this copy of the repository was made: take its profile from upstream instead of refusing.

    The shell is game-agnostic, so a shell built this morning runs a game whose profile did not exist when this repository
    was cloned. The profile -- build IDs, module list, memory layout, the shader tables -- lives only in the packer, so
    without this a new game is unpackable here while the shell would happily run it. Returns True when the profile is now
    on disk; never raises, because the caller has a message for the failure anyway.
    """
    d = DM / 'games' / tid
    say('no local profile for %s: asking upstream for it' % tid)
    try:
        import urllib.request
        with urllib.request.urlopen(UPSTREAM_API.format(tid=tid), timeout=30) as r:
            entries = json.load(r)
        if not isinstance(entries, list): return False
        d.mkdir(parents=True, exist_ok=True)
        total = 0
        for e in entries:
            name = e.get('name', '')
            if e.get('type') != 'file' or not name or '/' in name: continue
            with urllib.request.urlopen(UPSTREAM_RAW.format(tid=tid, name=name), timeout=120) as r:
                data = r.read()
            (d / name).write_bytes(data)
            total += len(data)
        say('  got %d files, %.1f MB into %s' % (len(entries), total / 1048576, d))
        return (d / 'game.tsv').is_file()
    except Exception as e:                                   # noqa: BLE001 - offline is the same as "not available"
        warn('could not fetch the profile for %s (%s)' % (tid, e))
        return False


def load_profile(tid):
    """dm/games/<title ID>/game.tsv: key<TAB>value, several values per key, one `build` row per version."""
    d = DM / 'games' / tid
    f = d / 'game.tsv'
    if not f.is_file():
        fetch_profile(tid)
    if not f.is_file():
        die('no game profile for title ID %s (%s)\n'
            '    Supported games: %s\n'
            '    This one is neither here nor upstream: check the title ID (the packer reads it from the package itself), or '
            'use a shell and a packer that upstream has a profile for.'
            % (tid, f, ', '.join(sorted(p.name for p in (DM / 'games').iterdir() if (p / 'game.tsv').is_file()))))
    rows = {}
    for line in f.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#'): continue
        k, _, v = line.partition('\t')
        rows.setdefault(k.strip(), []).append(v)
    return d, rows



def one(rows, key, default=None):
    # The guard is not decoration: this call sits after the six-minute translation, and the one time a caller in main() bound
    # the name "rows" to a lambda the run died here with AttributeError, three minutes after the useful work was done. Say
    # what went wrong instead.
    if not isinstance(rows, dict):
        raise TypeError('one() wants the profile row table (a dict keyed by column name), got %s -- something in the same '
                        'function rebound the name "rows"' % type(rows).__name__)
    v = rows.get(key)
    return v[0] if v else default


def truthy(v):
    """A profile switch: "on" (how the profiles write it) or a number. Absent and off are the same answer."""
    return bool(v) and str(v).strip().lower() not in ('0', 'off', 'no', 'false', '')


# ---------------------------------------------------------------- NSP scanning

def tid_sidecar(xci):
    """`<file>.xci.tid` next to the dump: the title ID found by extracting it once.

    Without it, every run would extract the whole cartridge again just to find out what it is, because nothing inside a
    dump names it. It holds one line of hex.
    """
    return xci.with_name(xci.name + '.tid')


def probe_xci(nx, keys, xci, out, allow_damaged=False):
    """The title ID of a cartridge dump, read by nx_extract.

    An XCI cannot be identified the way an NSP is: a dump carries no ticket (the keys sit in each NCA's own key area), and an
    NCA header -- where the title ID lives -- is encrypted with a key from prod.keys. nx_extract has both the keys and LibHac,
    so it extracts into `out` and reports `title <id>`; the extraction is the one the pack needs anyway, so nothing is done
    twice. Returns the title ID, or None.
    """
    out.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(nx), str(keys), str(xci), '-', str(out)] + (['--allow-damaged'] if allow_damaged else []),
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    text = (r.stdout or '') + (r.stderr or '')
    (out / 'probe.log').write_text(text, encoding='utf-8', errors='replace')
    m = re.search(r'^title\s+([0-9a-fA-F]{16})', text, re.M)
    if r.returncode or not m:
        warn('nx_extract could not read %s (see %s): %s'
             % (xci.name, out / 'probe.log', (text.strip().splitlines() or ['no output'])[-1]))
        return None
    tid = m.group(1).upper()
    try: tid_sidecar(xci).write_text(tid + '\n', encoding='utf-8')
    except OSError: pass
    return tid


def nsp_meta(path):
    """Title ID, content type and version, read from the package itself.

    Two sources, in order of how much they tell us:
      1. the plaintext cnmt.xml next to the encrypted NCAs -- title ID, content type (Application / Patch / AddOnContent) and
         the numeric version, all in one place;
      2. the name of the .tik inside: a ticket is named after its rights ID, which is the title ID followed by the key
         generation, so its first 16 hex digits are the title ID. That covers packages whose tools dropped the XML (they
         often keep only cnmt.nca, which is encrypted). The version is then unknown and taken as 0.
    Returns None for an XCI (an HFS0 container without either) or a file it cannot parse."""
    try:
        with open(path, 'rb') as f:
            if f.read(4) != b'PFS0': return None
            n, strtab, _ = struct.unpack('<III', f.read(12))
            if not 0 < n < 4096: return None
            entries = [struct.unpack('<QQII', f.read(24)) for _ in range(n)]
            strtab_off, data_off = 0x10 + 24 * n, 0x10 + 24 * n + strtab
            tik_id = None
            for off, size, name_off, _r in entries:
                f.seek(strtab_off + name_off)
                nm = b''
                while True:
                    c = f.read(1)
                    if not c or c == b'\0': break
                    nm += c
                low = nm.lower()
                if low.endswith(b'.cnmt.xml') and 0 < size < (1 << 20):
                    f.seek(data_off + off)
                    t = f.read(size).decode('utf-8', 'replace')
                    mid = re.search(r'<Id>\s*0x([0-9A-Fa-f]{16})\s*</Id>', t)
                    mty = re.search(r'<Type>\s*(\w+)\s*</Type>', t)
                    mvr = re.search(r'<Version>\s*(\d+)\s*</Version>', t)
                    if mid:
                        return {'id': mid.group(1).upper(), 'type': mty.group(1) if mty else '',
                                'version': int(mvr.group(1)) if mvr else 0}
                elif low.endswith(b'.tik') and tik_id is None:
                    m = re.match(rb'([0-9a-fA-F]{16})', nm.rsplit(b'/', 1)[-1])
                    if m: tik_id = m.group(1).decode('utf-8').upper()
            if tik_id:
                return {'id': tik_id, 'type': '', 'version': 0}
    except Exception:
        pass
    return None


def name_tid(p):
    m = re.search(r'\[([0-9A-Fa-f]{16})\]', p.name)
    return m.group(1).upper() if m else None


def name_ver(p):
    m = re.search(r'\[v(\d+)\]', p.name)
    return 'v' + m.group(1) if m else None


def app_id(tid, kind):
    """The application title ID a file belongs to, so a base game, its updates and its DLC land in one group.

    A base game ends in `…000`, an update is the base + 0x800, and add-on content is the base + 0x1000 + index. Taking the
    low 12 bits off only handles the first two: a DLC's 0x1000 offset has to come off as well, or the DLC lands in a group
    of its own and a folder holding a game and its DLC reads as "2 different games" (Breath of the Wild's packs, at
    …E000 + 0x1000 + 1 and + 2, folded to 01007EF00011F000 instead of the game's 01007EF00011E000). Upstream's
    build_ipa_from_nsp.py states the same rule in one line: `app = tid if low == 0 else tid & ~0xFFF if low == 0x800
    else (tid & ~0xFFF) - 0x1000`."""
    v = int(tid, 16)
    if kind == 'dlc': v -= 0x1000
    return '%016X' % (v - (v & 0xFFF))


def scan_nsps(folder):
    """Group the folder's files into one game: the base, its updates and its DLC.

    The title ID is taken from the ticket inside the NSP, so file names do not have to follow a pattern (the [XXXXXXXXXXXXX000]
    in a name is only a fallback). An update's title ID is the base game's plus 0x800 and a DLC's is plus 0x1000 + index, which
    is how the files are grouped (app_id) -- so a folder holding a base game, its update and its DLC is one game, not three.

    The version still comes from the [vN] in the name: the ticket carries no display version, and it is only used to tell
    updates apart (a base game is v0)."""
    nsps = sorted(list(folder.glob('*.nsp')) + list(folder.glob('*.xci')))
    nsz = sorted(folder.glob('*.nsz'))
    if nsz:
        die('%d NSZ file(s) in %s: this reads NSP and XCI only.\n'
            '    Unpack them first:  nsz -D "%s"   (needs your prod.keys)' % (len(nsz), folder, nsz[0].name))
    if not nsps: die('no .nsp or .xci in %s' % folder)
    apps = {}
    for p in nsps:
        if p.stat().st_size == 0: warn('skipping empty file %s' % p.name); continue
        meta = nsp_meta(p)
        if meta:
            tid, ver = meta['id'], 'v%d' % meta['version']
            kind = {'Application': 'base', 'Patch': 'update', 'AddOnContent': 'dlc'}.get(meta['type'], 'base')
            if not meta['type']:
                # A dump whose control metadata carries no CNMT type: the title ID's low bits say what it is, the same
                # rule the name fallback below uses (an update sits at +0x800). Without this an update NSP is taken for
                # the base game and overwrites it, which is how Balatro's update came to be packed as "v0".
                low = int(tid, 16) & 0xFFF
                kind = 'base' if low == 0 else ('update' if low == 0x800 else 'dlc')
            if not meta['version']:
                nv = name_ver(p)          # ... and the display version is only in the file name
                if nv: ver = nv
            if kind == 'update' and ver in ('', 'v0'):
                # An update is never version 0. Without a name or CNMT version it would collide with the base's "v0",
                # the pair would look like one file and the base would be packed as if no update existed -- which is what
                # happened to Balatro's update NSP. UNKNOWN_VER sorts above v0, so the update is still the newest to pack.
                ver = UNKNOWN_VER
        else:
            # an XCI, or an NSP without the metadata XML: fall back to the name, then to what an earlier run recorded, and
            # to the title ID's low bits for the kind
            tid, ver = name_tid(p), name_ver(p)
            if not tid:
                try: rec = tid_sidecar(p).read_text(encoding='utf-8').strip().upper()
                except OSError: rec = ''
                if re.fullmatch(r'[0-9A-F]{16}', rec):
                    tid = rec
                    say('using the title ID an earlier run recorded for %s' % p.name)
            if not tid:
                if p.suffix.lower() != '.xci':
                    warn('cannot read %s: no metadata inside and no [XXXXXXXXXXXXX000] in the name' % p.name)
                # an .xci saying nothing here is normal (a dump has no ticket and its NCA headers are encrypted): it is
                # identified by extracting it, which happens below when it is the only file in the folder
                continue
            low = int(tid, 16) & 0xFFF
            kind = 'base' if low == 0 else ('update' if low == 0x800 else 'dlc')
        key = app_id(tid, kind)
        a = apps.setdefault(key, {'base': None, 'updates': {}, 'dlc': [], 'all': []})
        a['all'].append((kind, ver, p))
        if kind == 'base': a['base'] = p
        elif kind == 'update': a['updates'][ver or UNKNOWN_VER] = p
        else: a['dlc'].append((tid, ver, p))
    return apps


# ---------------------------------------------------------------- steps

def build_nx_extract(dotnet, work, tools_dir=None):
    names = ('nx_extract.exe', 'nx_extract')
    if tools_dir:
        for n in names:
            c = tools_dir / n
            if c.is_file(): say('using the prebuilt nx_extract: %s' % c); return c
    out = work / 'tools' / 'nx_extract'
    exe = out / ('nx_extract.exe' if os.name == 'nt' else 'nx_extract')
    if exe.is_file(): say('nx_extract already built'); return exe
    if not dotnet: die('no prebuilt nx_extract and no .NET SDK: pass --tools <folder> or install the SDK')
    run([dotnet, 'build', TOOLS / 'nx_extract', '-c', 'Release', '-o', out, '-v', 'quiet'],
        log=work / 'logs' / 'nx_extract_build.log', what='building nx_extract (.NET)')
    if not exe.is_file():
        cand = list(out.glob('nx_extract*'))
        if not cand: die('nx_extract did not appear in %s' % out)
        exe = cand[0]
    return exe


def step_extract(nx, keys, folder, picks, work, versions, allow_damaged=False):
    ex = work / 'extract'
    ex.mkdir(parents=True, exist_ok=True)
    dirs = {}
    for ver in versions:
        dst = ex / ver
        if dst.is_dir() and (dst / 'ExeFS').is_dir(): say('reusing the extracted %s' % ver); dirs[ver] = dst; continue
        base, upd = picks[ver]
        tmp = ex / (ver + '.partial')
        shutil.rmtree(tmp, ignore_errors=True)
        run([nx, keys, base, upd if upd else '-', tmp] + (['--allow-damaged'] if allow_damaged else []), log=work / 'logs' / ('extract_%s.log' % ver),
            what='extracting %s (%s)' % (ver, base.name))
        # A previous run can leave <ver> behind without an ExeFS (an interrupted extraction, or one made before the folder
        # held an update as well), and a rename onto a non-empty directory fails on Windows. Clear it and take the new one.
        if dst.exists(): shutil.rmtree(dst, ignore_errors=True)
        tmp.replace(dst)
        dirs[ver] = dst
    return dirs


def aoc_hint(text, name):
    """The one DLC failure worth translating: a package that carries the ticket but no data NCA.

    Some DLC NSPs in circulation are "licence only" -- they mark the add-on content as owned and contain no files at all,
    which is no use for a package that has to mount the RomFS. nx_extract says "no data NCA in the DLC NSP", which reads
    like a broken file; say what it actually is instead, and what to do.
    """
    if 'no data NCA in the DLC NSP' in text:
        return ('%s holds a licence but no content (a "licence only" DLC package): it marks the add-on as owned but has\n'
                '    none of its files, so there is nothing to mount. Use a DLC NSP that carries the data, or leave this\n'
                "    file out of the folder -- the game then simply sees no add-on content." % name)
    return None


def step_aoc(nx, keys, dlcs, work, tid, force=False, allow_damaged=False):
    """The RomFS of every DLC in the folder, at work/aoc/<index>/ (index = its title ID - the base game's - 0x1000).

    The shell mounts these through DM_AOC_DIR: main.m points that at <App>/aoc when it exists, hle_fs.c's
    fs::MountAddOnContent opens DM_AOC_DIR/<index> read-only, and hle_misc.c reports the directory names as the owned
    indices (which is what nn::aoc::CountAddOnContent is asked for). So the game sees exactly the add-on content the user
    put in the folder, and nothing when the folder has none -- same as a console without the DLC installed.

    One extraction serves every version: add-on content is not part of the update, and a folder usually holds exactly one
    DLC set. nx_extract --aoc reads the DLC NSP's data NCA; a directory is only renamed into place once the extraction
    finished, so an interrupted run leaves <index>.partial and is redone.
    """
    if not dlcs: return None
    # The same DLC can sit in the folder twice (a dump and its update, or two copies of the same file): take the highest
    # version, and the larger of equal versions, as upstream's extract_aoc does.
    best = {}
    def vnum(v):
        v = (v or '').lstrip('v')
        return int(v) if v.isdigit() else 0
    for t, ver, p in dlcs:
        k = (vnum(ver), p.stat().st_size)
        if t not in best or k > best[t][0]: best[t] = (k, p)
    if len(best) < len(dlcs):
        say('%d DLC file(s) are copies of another one in the folder: packing %d' % (len(dlcs) - len(best), len(best)))
    jobs = []
    for t, (_, p) in best.items():
        idx = int(t, 16) - int(tid, 16) - 0x1000
        if idx < 0: die('%s says it is add-on content of %s (title ID %s), which is the base game or below it' % (p.name, tid, t))
        jobs.append((idx, t, p))
    jobs.sort()
    if len({i for i, _, _ in jobs}) != len(jobs):
        die('two DLC files in %s claim the same add-on index: %s'
            % (dlcs[0][2].parent, ', '.join('%d (%s)' % (i, p.name) for i, _, p in jobs)))
    out = work / 'aoc'
    out.mkdir(parents=True, exist_ok=True)
    # A DLC removed from the folder must not stay in the package: this folder outlives a run with --keep-work.
    want = {str(i) for i, _, _ in jobs}
    for d in sorted(out.iterdir()):
        if d.is_dir() and d.name not in want and (d.name.isdigit() or d.name.endswith('.partial')):
            warn('DLC index %s is no longer in the folder: dropping it from the package' % d.name)
            shutil.rmtree(d, ignore_errors=True)
    # What each index was extracted from. A DLC file swapped for another one of the same index must not be reused: this
    # folder outlives a run with --keep-work, and a stale extraction looks exactly like a fresh one otherwise (the same
    # failure as the shader directory that was picked by name instead of by what this run filled).
    stamp = work / 'aoc.sources'
    done = {}
    if stamp.is_file():
        for line in stamp.read_text(encoding='utf-8', errors='replace').splitlines():
            f = line.split('\t')
            if len(f) == 5: done[f[0]] = f[1:]
    for idx, t, p in jobs:
        dst = out / str(idx)
        src = [t, p.name, str(p.stat().st_size), str(p.stat().st_mtime_ns)]
        if dst.is_dir() and not force and done.get(str(idx)) == src:
            say('reusing the extracted DLC %d (title ID %s)' % (idx, t)); continue
        tmp = out / ('%d.partial' % idx)
        shutil.rmtree(tmp, ignore_errors=True)
        run([nx, keys, '--aoc', p, tmp] + (['--allow-damaged'] if allow_damaged else []),
            log=work / 'logs' / ('dlc_%d.log' % idx), what='extracting DLC %d (%s)' % (idx, p.name),
            hint=lambda t, n=p.name: aoc_hint(t, n))
        if dst.exists(): shutil.rmtree(dst, ignore_errors=True)
        tmp.replace(dst)
        done[str(idx)] = src
        n = sum(1 for f in dst.rglob('*') if f.is_file())
        say('DLC %d: %d files -> aoc/%d' % (idx, n, idx))
    stamp.write_text(''.join('%s\t%s\n' % (i, '\t'.join(done[i])) for i in sorted(want, key=int) if i in done),
                     encoding='utf-8')
    return out


# Files a derived profile leaves out: the build makes all of these again from the packed version's own files.
DERIVED_SKIP = {'game.tsv', 'shader_map.tsv', 'shader_facts.tsv', 'shader_pairs.tsv', 'c1_full.tsv', 'cb_extent.tsv',
                'dmsh_chain.h', 'vk_capture.tsv'}


def module_build_id(nso):
    with open(nso, 'rb') as f:
        f.seek(0x40)
        return f.read(16).hex()


def check_build_ids(game, rows, ver, mods):
    """The build IDs of the extracted modules against the ones the profile registers for this version.

    A mismatch does not have to stop the pack: upstream derives a profile from the files and carries on ("Any version works"),
    which is what derive_profile below does. Returns the mismatches so main can decide, and whether there was a row at all."""
    want = {}
    for line in rows.get('build', []):
        f = line.split('\t')
        if f[0] != ver: continue
        for item in f[2:]:
            if '=' in item: k, v = item.split('=', 1); want[k] = v
    if not want:
        return ['the profile registers no build IDs for %s' % ver], False
    bad = []
    for m in mods:
        p = game / 'ExeFS' / m
        if not p.is_file(): bad.append('%s missing' % m); continue
        got = module_build_id(p)
        if m in want and got != want[m]: bad.append('%s %s (registered %s)' % (m, got, want[m]))
    return bad, True


def identify_version(game, rows):
    """Which version the extracted files are, read from the files: the profile's build rows record the ExeFS modules'
    build IDs, and control.nacp carries the display version. Returns 'v<N>', or None when no row matches.

    The file name does not decide this, the same as it does not decide the title ID (the ticket inside the package), the
    kind (the CNMT type, then the title ID's low bits) or the DLC index. It matters for an update named with a bare
    [65536] or with nothing at all, whose CNMT is encrypted: after unpacking, the modules say exactly which build it is.
    """
    exefs = game / 'ExeFS'
    got = {}
    for p in (sorted(exefs.iterdir()) if exefs.is_dir() else []):
        # Every module, not the profile's list: a row registers more modules than the profile loads (Echoes of Wisdom's
        # row names subsdk0 and subsdk1 beside main and sdk), and rtld / main.npdm are not modules at all.
        if not p.is_file() or p.name in ('rtld', 'main.npdm'): continue
        try: got[p.name] = module_build_id(p)
        except OSError: pass
    disp = None
    if (game / 'control.nacp').is_file():
        sys.path.insert(0, str(TOOLS))
        try:
            import nacp
            disp = (nacp.version(game / 'control.nacp') or '').strip() or None
        except Exception:
            disp = None
    rows_b = [l.split('\t') for l in rows.get('build', [])]
    # The build IDs first: every ID the row registers has to match. A row that names no ID proves nothing, and a module
    # the row registers but the dump lacks (Absolum 1.0.4 has no subsdk2) must not match either -- hence all(), not any().
    for f in rows_b:
        want = {k: v for k, v in (i.split('=', 1) for i in f[2:] if '=' in i)}
        if want and got and all(got.get(k) == v for k, v in want.items()): return f[0]
    # Then the display version, which the profile records beside the version it was brought up on ("1.1.0").
    if disp:
        for f in rows_b:
            if len(f) > 1 and f[1].strip() == disp: return f[0]
    return None


def derive_profile(gdir, tid, ver, game, mods, dest, disp):
    """The profile for a version it was not brought up on, written into `dest` (outside the repository).

    Its keys as they are, with this version's own build row, package and shader versions; the shader tables, the chain header
    and the capture list are left out because the build makes them again from this version's files. Callers then put `dest`
    on DM_GAMES_DIR with DM_GAMES_FIRST=1, which every tool in the chain honours -- the same mechanism upstream uses for a
    provisional profile.
    """
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    for p in gdir.iterdir():
        if p.is_file() and p.name not in DERIVED_SKIP:
            shutil.copyfile(p, dest / p.name)
    # The module list has to follow the files, but only downwards: this build may ship a module the profile names and not
    # have it (Absolum 1.0.4 has no subsdk2), and a rewrite told to load a file the dump lacks dies on the missing file.
    # What it must NOT do is load a module the profile leaves out, because a game's ExeFS also holds things that must never
    # run on this host: Absolum ships NVIDIA's OpenGL and Vulkan drivers as subsdk0 / subsdk1 (their `_nv*` symbols), and
    # loading either one aborts inside nninitStartup. So the profile's list, minus what this build does not have.
    present = [q.name for q in (game / 'ExeFS').iterdir() if q.is_file() and q.name not in ('rtld', 'main.npdm')]
    order = lambda m: (m != 'main', 2 if m == 'sdk' else (1 if m.startswith('subsdk') and m[6:].isdigit() else 3),
                       int(m[6:]) if m.startswith('subsdk') and m[6:].isdigit() else 0)
    mods_now = [m for m in mods if m in present] or sorted(present, key=order)
    left_out = [m for m in present if m not in mods_now]
    if left_out:
        warn('%s ships %s, which profile %s does not load: leaving them out (a driver module aborts in nninitStartup)'
             % (disp, ', '.join(left_out), gdir.name))
    gone = [m for m in mods if m not in present]
    if gone: say('%s has no %s: loading %s' % (disp, ', '.join(gone), ' '.join(mods_now)))
    row = '\t'.join(['build', ver, disp] + ['%s=%s' % (m, module_build_id(game / 'ExeFS' / m)) for m in mods_now])
    out, put = ['# Derived from %s for %s (%s), a version that profile was not brought up on:' % (gdir, ver, disp),
                '# its keys as they are, this version\'s build row and module list, and shader tables from its own files'], False
    for line in (gdir / 'game.tsv').read_text(encoding='utf-8').splitlines():
        if line.split('\t', 1)[0] in ('build', 'package', 'shader_versions', 'modules'):
            if not put:
                out += [row, 'package\t%s' % ver, 'shader_versions\t%s' % ver, 'modules\t%s' % ' '.join(mods_now)]
                put = True
            continue
        out.append(line)
    (dest / 'game.tsv').write_text('\n'.join(out) + '\n', encoding='utf-8')
    return dest


def load_rows(d):
    rows = {}
    for line in (d / 'game.tsv').read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#'): continue
        k, _, v = line.partition('\t')
        rows.setdefault(k.strip(), []).append(v)
    return rows



def check_free_space(folder, factor=3.5):
    """Refuse to start when the disk cannot hold the peak.

    Packing keeps three copies of the game on disk at once -- the extraction, the assembled app tree and the IPA -- which
    measured 9.0 GiB for a 2.96 GiB cartridge dump, about 3x. Starting without that room fails halfway through the
    extraction, which leaves a partial tree of the same size to clean up, so say it up front instead.
    """
    src = sum(p.stat().st_size for p in folder.iterdir()
              if p.is_file() and p.suffix.lower() in ('.nsp', '.xci', '.nsz'))
    if not src: return                       # nothing recognised yet: scan_nsps says so with a better message
    need, free = int(src * factor), shutil.disk_usage(folder).free
    if free >= need:
        say('disk: %.1f GiB free, about %.1f GiB needed' % (free / 2**30, need / 2**30)); return
    die('not enough space on the disk holding %s: %.1f GiB free, about %.1f GiB needed.\n'
        '    %.1f GiB of game files, and packing holds three copies at once (the extraction, the assembled app and the '
        'IPA).\n    Free some space, move the game folder to another disk, or remove an earlier run\'s work folder with\n'
        '    --clean "%s" (it is several times the size of the game and only --keep-work needs it after a run).'
        % (folder, free / 2**30, need / 2**30, src / 2**30, folder))


HOST_DIRS = ('dm/host', 'dm/gfx', 'dm/gfx/nvn', 'dm/gfx/nvn/include', 'dm/gfx/tex', 'dm/gfx/vk', 'dm/gfx/gl')
CAPTURE_SECS = 120          # the longest run-time capture upstream measured is Super Mario Galaxy's 3,089 Vulkan modules


def apple_silicon():
    return subprocess.run(['sysctl', '-n', 'hw.optional.arm64'], capture_output=True, text=True).stdout.strip() == '1'


def capture_wanted(gdir):
    """The keys the profile's gl_capture.tsv lists (whole file names), or None when it has no table."""
    f = gdir / 'gl_capture.tsv'
    if not f.is_file(): return None
    keys = {l.strip() for l in f.read_text(encoding='utf-8').splitlines() if l.strip() and not l.startswith('#')}
    return keys or None


def capture_have(d):
    try: names = os.listdir(d)
    except OSError: return set()
    return {n for n in names if re.fullmatch(r'[0-9a-f]{16}\.(vert|frag)', n)}


def build_mac_host(work, gid):
    """dm/host's own compile line (make -n) with the output moved: make takes no target path with spaces, and
    dm/host/dmhost may belong to another build."""
    if sys.platform != 'darwin': die('a run-time shader capture boots the game in the Mac host: it needs macOS')
    if not apple_silicon(): die('the Mac host runs the game\'s own arm64 code natively: this Mac has an Intel processor')
    host = DM / 'host'
    out = work / 'tools' / 'dmhost'
    out.parent.mkdir(parents=True, exist_ok=True)
    # dm/host's Makefile reads the profile through gen/<id>/profile (make cannot take a path with spaces) and wants the
    # chain version header beside it: the version follows from the sources alone, so one --header serves every game.
    prof = Path(os.environ.get('DM_GAMES_DIR', '').split(os.pathsep)[0] or str(DM / 'games')) / gid
    hdr = DM / 'gfx' / 'nvn' / 'dmsh_chain.h'
    run([sys.executable, str(DM / 'gfx' / 'tools' / 'dmsh_chain.py'), '--header'],
        log=work / 'logs' / 'dmsh_chain.log', what='writing the chain version header')
    if not (prof / 'dmsh_chain.h').is_file(): shutil.copyfile(hdr, prof / 'dmsh_chain.h')
    link = host / 'gen' / gid / 'profile'
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists(): link.unlink()
    link.symlink_to(prof.resolve())
    run(['make', '-s', 'GAME=%s' % gid, 'gen/%s/game_profile.h' % gid, 'gen/vpx/libvpx.a'],
        log=work / 'logs' / 'mac_host_make.log', what='building the Mac host (headers, libvpx)', cwd=host)
    dry = subprocess.run(['make', '-n', '-B', 'GAME=%s' % gid, 'dmhost'], cwd=host, capture_output=True, text=True)
    line = next((l for l in dry.stdout.splitlines() if ' -o dmhost ' in l), None)
    if not line: die('make -n gave no compile line for the Mac host: %s' % dry.stderr.strip()[-400:])
    cmd = shlex.split(line)
    cmd[cmd.index('-o') + 1] = str(out)
    run(cmd, log=work / 'logs' / 'mac_host_build.log', what='building the Mac host', cwd=host)
    return out


def capture_gl(game, gdir, patched, work, tid):
    """The GLSL LÖVE-style games put together at run time: boot the game in the Mac host with DM_GL_CAPTURE, which writes
    each shader it is given to that folder, until every key the profile's table names is there."""
    want = capture_wanted(gdir)
    out = game / 'gl_capture'
    if want and capture_have(out) >= want:
        say('reusing the captured shaders in %s' % out); return out
    host = build_mac_host(work, tid)
    cap, rd = game / 'gl_capture.partial', work / 'tmp' / 'gl_run'
    shutil.rmtree(cap, ignore_errors=True); shutil.rmtree(rd, ignore_errors=True)
    cap.mkdir(parents=True)
    for x in ('save', 'temp', 'msl'): (rd / x).mkdir(parents=True)
    env = dict(os.environ, DM_ROMFS=str(game / 'RomFS'), DM_SAVE=str(rd / 'save'), DM_TEMP=str(rd / 'temp'),
               DM_MSL_DIR=str(rd / 'msl'), DM_GL_CAPTURE=str(cap))
    say('booting the game in the Mac host to capture the GLSL it assembles at run time (%s keys)'
        % ('all %d' % len(want) if want else 'any'))
    log = work / 'logs' / 'gl_capture.log'
    t0, last, seen = time.time(), 0, time.time()
    with open(log, 'w') as f:
        proc = subprocess.Popen([str(host), str(patched)], env=env, cwd=rd, stdout=f, stderr=subprocess.STDOUT)
        try:
            while proc.poll() is None and time.time() - t0 < CAPTURE_SECS:
                got = capture_have(cap)
                if len(got) != last: last, seen = len(got), time.time(); say('  captured %d' % last)
                if (want and got >= want) or (not want and got and time.time() - seen > 20): break
                time.sleep(0.25)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try: proc.wait(5)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait()
    got = capture_have(cap)
    if want and not got >= want:
        tail = ''.join(list(open(log, errors='replace'))[-12:]).rstrip()
        die('captured %d of %d run-time shaders (host output: %s)%s' % (len(got), len(want), log, '\n' + tail if tail else ''))
    cap.replace(out)
    shutil.rmtree(rd, ignore_errors=True)
    say('captured %d shaders in %.0f s → %s' % (len(got), time.time() - t0, out))
    return out


def find_tool(name, tools_dir):
    """A helper executable: --tools first, then the release's own folder, then PATH."""
    for d in ([tools_dir] if tools_dir else []) + [GFX]:
        for n in (name, name + '.exe'):
            if (Path(d) / n).is_file(): return Path(d) / n
    w = shutil.which(name)
    return Path(w) if w else None


def step_gl_shaders(cap, out, gdir, tid, tools_dir):
    """A GL capture (the phone's DM_GL_CAPTURE folder) → the layer's shader table and one Metal source per shader.

    dm/gfx/tools/gl_shaders.py translates GLSL → SPIR-V → MSL with glslang and spirv-cross and then calls `xcrun metal`,
    which only a Mac has; the translation is driven from here and stops before that step. The layer compiles each source
    on the phone instead: at first launch for all of them (dm/ios/Sources/dm_shader_prep.m, which leaves them in the
    system's shader cache), and on demand for any it missed. Carrying sources rather than a library is what the NVN route
    already does when the packing computer has no Apple compiler (nometal_run.py).
    """
    want, have = capture_wanted(gdir), capture_have(cap)
    if not have: die('no shaders in %s: it needs <key>.vert / <key>.frag, which a capture run writes' % cap)
    if want:
        miss = want - have
        if miss: warn('%d of the %d shaders the profile lists are not in the capture (%s%s).\n'
                      '    They are translated from nothing, so the game will report those as missing -- only the shaders '
                      'of the scenes the capture run reached are in the package.'
                      % (len(miss), len(want), ', '.join(sorted(miss)[:6]), ', ...' if len(miss) > 6 else ''))
        else: say('capture: all %d shaders the profile lists are here' % len(want))
    for n in ('glslangValidator', 'spirv-cross'):
        p = find_tool(n, tools_dir)
        if not p: die('no %s: the shaders are translated with it, so pass --tools <folder> holding the release\'s '
                      'executables, or put it on PATH' % n)
        os.environ['PATH'] = str(p.parent) + os.pathsep + os.environ.get('PATH', '')
    sys.path.insert(0, str(GFX))
    import gl_shaders as G                       # dm/gfx/tools/gl_shaders.py: the same translation the Mac route uses
    items, seen = [], set()
    for s, st, where in G.sources([str(cap)]):
        if (s, st) in seen: continue
        seen.add((s, st)); items.append((s, st, where))
    with tempfile.TemporaryDirectory() as tmp:
        with cf.ThreadPoolExecutor(os.cpu_count()) as ex:
            rows = [r for rs in ex.map(lambda it: G.one(it, tmp), items) for r in rs]
        good, failed = {}, []
        for key, stage, f, msl, where in rows:
            if not stage: failed.append((key, where, f)); continue
            if (key, stage) not in good: good[(key, stage)] = (stage, f, msl, where)
        for key, where, errs in failed:
            if not any(k == key for k, _ in good): warn('%s (%s) does not translate: %s' % (where, key, ' / '.join(errs)))
        if not good: die('none of the %d shaders in %s translates' % (len(items), cap))
        src = out / 'src'
        shutil.rmtree(out, ignore_errors=True); src.mkdir(parents=True)
        for (key, stage), (st, f, msl, where) in good.items(): shutil.copyfile(msl, src / ('%s_%s.metal' % (key, stage[0])))
        tsv = ['# GL shaders (dm/gfx/tools/gl_shaders.py): key = sha256(GLSL source)[:16], Metal function gl_<key>_v / gl_<key>_f; '
               'a: vertex inputs, u: default uniform block members (offset:type:count:stride), b: the block (slot:size), '
               't: combined samplers (slot:type), o: fragment outputs',
               '# source form (src/<key>_<v|f>.metal): the packing computer had no Apple Metal compiler, so the layer '
               'compiles these on the phone at first launch']
        tsv += ['%s\t%s\t%s\t%s' % (k[0], v[0], ' '.join(v[1]), v[3]) for k, v in sorted(good.items())]
        (out / 'gl_shaders.tsv').write_text('\n'.join(tsv) + '\n', encoding='utf-8')
        (out / 'inputs.tsv').write_text('game\t%s\n' % tid, encoding='utf-8')
    nv = sum(1 for v in good.values() if v[0] == 'vert')
    say('GL shaders: %d translated (%d vertex, %d fragment) → %s' % (len(good), nv, len(good) - nv, out))
    return len(good)


def fill_mem_used(patched, mods, gdir, rows):
    """The used memory GetInfo reports beside the heap: 64 MB unless the rewritten modules need more (dm/games/README.md).

    A profile that was brought up on one build can carry less than a later build's modules need, and a derived profile
    inherits exactly that. The game then sizes its heap as total - used, asks for more than the pool holds, and the SDK
    aborts during nninitStartup -- which is what Absolum 1.0.4 did: 138 MB of modules against a 64 MB default. Returns the
    value written, or None when the profile already has enough.
    """
    total = sum(q.stat().st_size for q in patched.glob('*.patched.bin'))   # the rewritten modules, as step_rewrite names them
    have = one(rows, 'mem_used')
    if have and int(have, 0) >= total: return None
    if not total: return None                                # nothing rewritten yet
    if total <= 0x4000000 and not have: return None          # the default covers it
    value = '0x%X' % ((total + 0xFFFFFF) & ~0xFFFFFF)        # a 16 MB granule above the measured size
    lines = (gdir / 'game.tsv').read_text(encoding='utf-8').splitlines()
    out, done = [], False
    for line in lines:
        if line.split('\t', 1)[0] == 'mem_used':
            out.append('mem_used\t' + value); done = True
        else:
            out.append(line)
    if not done: out.append('mem_used\t' + value)
    (gdir / 'game.tsv').write_text('\n'.join(out) + '\n', encoding='utf-8')
    return value, total


def step_rewrite(exe_dir, mods, out, work, force=False):
    # patch_nso.py is pure Python (capstone + lz4): its stubs are hand-assembled machine code, not built at run time,
    # so no C compiler is needed here even though its comments mention clang.
    if not force and all((out / (m + '.patched.bin')).is_file() and (out / (m + '.fixups.tsv')).is_file() for m in mods):
        say('rewritten modules already there'); return
    out.mkdir(parents=True, exist_ok=True)
    for m in mods:
        run([PY, TOOLS / 'patch_nso.py', exe_dir / m, out, m],
            log=work / 'logs' / ('rewrite_%s.log' % m), what='rewriting %s' % m)


def graphics_route(prof_dir, registered=None):
    """Which shader route this game takes, from what its profile carries.

    NVN games hold their shaders in the game files, so shader_split.py can read them. A Vulkan game may generate its SPIR-V
    at run time, and a GL game (Balatro: LÖVE puts each shader together in Lua) holds none at all -- upstream's profiles name
    those with vk_capture.tsv / gl_capture.tsv, the keys its Mac run captured. Those two routes need that capture and
    vk_shaders.py / gl_shaders.py; upstream runs them after booting the game on a Mac.
    """
    # A derived profile does not carry vk_capture.tsv (the packer leaves it out: it names a capture, not a source), so the
    # registered profile answers for a build that has one of its own.
    for d in ([prof_dir, registered] if registered and registered != prof_dir else [prof_dir]):
        if (d / 'vk_capture.tsv').is_file(): return 'vulkan'
        if (d / 'gl_capture.tsv').is_file(): return 'gl'
        # An NVN shader table with no rows is the profile saying "this game has no NVN shaders": Hades' table is a header
        # and one comment, its 74 shaders are SPIR-V in the RomFS. Without this it falls through to 'nvn' and shader_split
        # fails on a game that never had any.
        m = d / 'shader_map.tsv'
        if m.is_file():
            rows = [l for l in m.read_text(encoding='utf-8', errors='replace').splitlines() if l.strip() and not l.startswith('#')]
            if len(rows) <= 1: return 'vulkan'          # only the column header: no NVN shader at all
    return 'nvn'


def vk_capture_hint(text):
    """What vk_shaders.py's "no SPIR-V files" means when a game builds its shaders in memory instead of shipping them.

    vk_shaders.py says the same two words whether the RomFS is genuinely empty of SPIR-V or this title generates its
    modules at run time (Super Mario Galaxy, 3797 of them), and on our route there is no Mac to capture them with: the
    capture is a run on the phone, which the packer can prepare but not perform. Only that message is replaced -- every
    other failure keeps the tool's own words, exit code and log tail.
    """
    if 'no SPIR-V files' in text:
        return ('this game builds its shaders while it runs, so no file in its RomFS holds a shader to translate.\n'
                '    They are captured once on the phone, not on a Mac:\n'
                '      1. pack it with --no-shaders (the package boots and plays its music; it draws nothing)\n'
                '      2. run it with Documents/env.txt holding DM_VK_CAPTURE=<a writable folder>\n'
                '      3. pack again with --vk-capture <the folder it wrote>\n'
                '    docs/CAPTURE.md has the procedure (docs/CAPTURE.zh-CN.md in Chinese).')
    return None


def shader_split_hint(text):
    """What shader_split.py's Oodle messages mean for someone packing on Windows, instead of what they say.

    shader_split.py can decode Oodle-compressed archives, but only through the game's own decoder running in the Mac host: it
    asks for DM_OODLE_HOST and suggests `make -C dm/host GAME=<id> dmhost`. That instruction is useless here -- the whole
    point of this route is that the user has no Mac -- and repeating it makes a known limitation of the route look like a
    setup mistake on their side. So the packer says it plainly and points at the two documents that answer the next question.
    """
    if 'DM_OODLE_HOST' in text or 'DM_OODLE_MODULES' in text:
        return ('this game keeps its shaders in Oodle-compressed archives (Trinity packs, PlatinumGames PKZ, or an older\n'
                "    Unreal pak). Oodle is only decoded by the game's own code, running inside the Mac host, and this route\n"
                '    has no Mac at all (tools/ holds .exe files only), so this title cannot be packed here, on any machine:\n'
                '    docs/SUPPORTED.md lists it as 暂不支持.\n'
                '\n'
                '    Games that pack straight from Windows are listed in the same file.')
    return None


def oodle_archives(game, mods):
    """The reasons this extraction keeps its shaders where only the game's own Oodle decoder reaches them, or {}.

    Two of profile_guess.build_sources' answers name archives the shader split reads through that decoder running in the
    Mac host (dm/host/hle_oodle.c, DM_OODLE_SERVE): Trinity packs, PlatinumGames PKZ and Game Freak GFPAK archives with
    Oodle Kraken entries (oodle_packs), and Unreal legacy paks with the shaders inline (ue_legacy, LZ5). Until now they
    were only met at the shader step, after the extraction and a rewrite of main and sdk -- minutes of work for an answer
    the file headers already hold. The scan is upstream's own (checked against every profile), reads headers only, and
    costs one walk of the RomFS. The other answers are left to their own messages: ue_decode wants the game booted on a
    Mac, and the three captures are what docs/CAPTURE.md answers.
    """
    sys.path.insert(0, str(DM / 'games'))
    try:
        import profile_guess
        src = profile_guess.build_sources(game / 'ExeFS', game / 'RomFS', mods)
    except Exception as e:                                    # noqa: BLE001 - any failure here only costs the early stop
        warn('could not scan the RomFS for Oodle-compressed shader archives (%s): packing on as before' % e)
        return {}
    return {k: src[k][1] for k in ('oodle_packs', 'ue_legacy') if src.get(k) and src[k][0]}


def step_shaders(dirs, versions, prof_dir, tables, work, tid, force=False):
    """shader_split.py: pull the shaders out of the game files and produce the four tables. The rows must match the
    profile's (that is how we know the files are the version the profile was built from).

    `versions` are read in ONE split, in the profile's shader_versions order. A profile whose tables are the union of
    several versions (Wonder: v0 + v327680; Mario Kart 8 Deluxe: v1441792 + v1507328) has to be split from every one of
    them: that single package holds the shader records the tables index into, and one version alone holds fewer, so
    dmsh_build.py stopped with a bare IndexError (a user's report, GitHub issue #5). Upstream splits them the same way:
    build_ipa_from_nsp.py passes one RomFS/main per shader_versions entry and shader_split.py takes comma-separated lists.
    Returns the differences against the profile's tables, which the caller judges (main()): shader_facts.tsv is merged
    and written back, the others must match or the build cannot index its package."""
    # Only NVN games come here (main(): `if route == 'nvn'`): a GL or Vulkan game's shaders are not in its files, and the
    # refusal for those lives there, next to the --no-shaders / --gl-capture / --vk-capture runs it points at.
    pkg, tb = work / 'shader' / 'pkg', work / 'shader' / 'tables'
    stamp = tb / 'versions.txt'
    key = ' '.join(versions)
    if not force and (pkg / 'index.tsv').is_file() and stamp.is_file() \
            and stamp.read_text(encoding='utf-8').strip() == key and all((tb / n).is_file() for n in tables):
        say('shader split already done (%s)' % key); return pkg, tb, []
    tb.mkdir(parents=True, exist_ok=True)
    # argv of shader_split.py: romfs, main, package, shader_map.tsv, shader_facts.tsv, shader_pairs.tsv, pso_pairs.tsv --
    # the last is what the pipeline prebuild takes of agl's BAHS program variations, as upstream's build_ipa_from_nsp.py
    # asks for it (split_shaders, names + ('pso_pairs.tsv',)). Several versions go in as comma-separated lists in the
    # same order as the profile's shader_versions, because the tables on disk are a union over exactly that set.
    names = ['shader_map.tsv', 'shader_facts.tsv', 'shader_pairs.tsv', 'pso_pairs.tsv']
    run([PY, GFX / 'shader_split.py', ','.join(str(dirs[v] / 'RomFS') for v in versions),
         ','.join(str(dirs[v] / 'ExeFS' / 'main') for v in versions), pkg] + [tb / n for n in names],
        env={'DM_GAME': tid}, log=work / 'logs' / 'shader_split.log', what='splitting the shaders out of the game files',
        hint=shader_split_hint)
    stamp.write_text(key, encoding='utf-8')   # the reuse above must not serve another set of versions
    diff = []
    for n in names:
        a, b = tb / n, prof_dir / n
        if b.is_file():
            ra = [l for l in a.read_text(errors='replace').splitlines() if l and not l.startswith('#')]
            rb = [l for l in b.read_text(errors='replace').splitlines() if l and not l.startswith('#')]
            if ra != rb: diff.append(n)
    return pkg, tb, diff


def map_overflow(pkg_dir, table):
    """The first row of `table` whose index runs past what the split package holds, or None.

    dmsh_build.py's rec_of() reads the profile's shader_map.tsv rows as packs[name][index] over the package the split
    wrote, so a table split from other files ends in a bare IndexError (GitHub issue #5). Only the highest index of each
    package is compared, and the records are counted the way that tool counts them: one per BTCDBTCD control-section
    header."""
    worst = {}
    for ln in table.read_text(errors='replace').splitlines():
        c = ln.split('\t')
        if len(c) < 2 or ln.startswith('#') or not c[1].isdigit(): continue
        if int(c[1]) > worst.get(c[0], -1): worst[c[0]] = int(c[1])
    for p in sorted(worst):
        f = pkg_dir / p
        if not f.is_file(): return '%s is missing from the split' % p
        n = f.read_bytes().count(b'BTCDBTCD')
        if worst[p] >= n: return '%s#%d, and the split made %d records' % (p, worst[p], n)
    return None


def step_translate(pkg, work, xlate, iosout, tid, force=False):
    """dmsh_build.py, with no metal compiler: nometal_run.py turns `xcrun metal` into a copy, so the "libraries" hold MSL
    source and the phone compiles them once on first launch. dmsh_build.py creates <iosout>/b-<chain version> itself."""
    # Completeness is decided by the per-shader results dmsh_build.py writes, not by "some file is there": a run that was
    # interrupted leaves libraries behind, and trusting them would ship a package that only fails on the phone.
    cache = work / 'shader' / 'cache'
    def done():
        marks = sorted(cache.glob('b-*/_b_build.json'))
        if not marks: return False
        try: res = json.load(open(marks[-1], encoding='utf-8'))
        except Exception: return False
        if not res or any(r.get('st') != 'ok' for r in res): return False
        d = iosout / marks[-1].parent.name
        have = {x.name for x in d.glob('*_b.metallib')} if d.is_dir() else set()
        return all(r['key'] + '_b.metallib' in have for r in res)
    if not force and done():
        say('shader libraries already there'); return
    need_tool('spirv-cross', 'translate SPIR-V to Metal Shading Language',
              'install Vulkan SDK 1.4.357.0 (its Bin/ holds spirv-cross.exe) or build SPIRV-Cross at tag vulkan-sdk-1.4.357.0')
    nometal = GFX / 'nometal_run.py'
    if not nometal.is_file():
        die('dm/gfx/tools/nometal_run.py is missing (it is what makes this work without Apple\'s compiler)')
    # dmsh_build.py refuses to start when the toolchain does not match dmsh_chain.lock, which on a machine using the
    # prebuilt executables it never will: register this machine first. That rewrites dmsh_chain.lock and the profile's
    # dmsh_chain.h -- the header is the compile-time constant only the Xcode route needs, and the shell reads the version
    # from the directory name, so neither value is used any further here.
    genv = {'XLATE': str(xlate), 'DM_GAME': tid}
    chk = subprocess.run([PY, nometal, GFX / 'tool_lock.py', '--check-tools'], capture_output=True, text=True,
                         env=dict(os.environ, PYTHONUTF8='1', **genv))
    if chk.returncode:
        say('the toolchain differs from dmsh_chain.lock: registering this machine')
        run([PY, nometal, GFX / 'tool_lock.py', '--update-lock'], env=genv,
            log=work / 'logs' / 'chain_lock.log', what='tool_lock --update-lock')
        warn('dmsh_chain.lock now describes this machine (do not commit it)')
    # dmsh_build.py refuses to start unless the game profile's dmsh_chain.h holds the chain version it computed, and that
    # header is per game: packing a second game on the same machine fails with "run --header first" if this only happens
    # when the toolchain lock changes. It reads the tables, so it costs nothing to do every time.
    run([PY, nometal, GFX / 'dmsh_chain.py', '--header'], env=genv,
        log=work / 'logs' / 'chain_header.log', what='dmsh_chain --header')
    run([PY, nometal, GFX / 'dmsh_build.py', pkg, work / 'shader' / 'cache'],
        env={'IOSOUT': iosout, 'XLATE': xlate, 'WORK': work, 'J': os.environ.get('J', '8'), 'DM_GAME': tid},
        log=work / 'logs' / 'shaders.log', what='translating shaders to Metal source')
    # Report per directory: a work folder that survived an interrupted run holds one b-<V> per chain version, and the
    # sum of them reads as a healthy build while the directory that gets shipped holds a handful.
    libs = sorted((p for p in iosout.glob('b-*') if p.is_dir()), key=lambda d: (d.stat().st_mtime, d.name)) if iosout.is_dir() else []
    counts = [(d.name, len(list(d.glob('*_b.metallib')))) for d in libs]
    n = sum(c for _, c in counts)
    if not n: die('no shader libraries were produced (see %s)' % (work / 'logs' / 'shaders.log'))
    say('%d shader libraries%s' % (n, '' if len(counts) < 2 else ': ' + ', '.join('%s %d' % c for c in counts)))


def step_guest(patched, mods, stage):
    sys.path.insert(0, str(TOOLS))
    import guest_dylib
    fw, n = guest_dylib.write(str(patched), str(stage / 'Frameworks'), mods)
    say('Frameworks/Guest.framework: %d bytes' % n)


def env_for(rows):
    """The profile keys the shell cannot carry in its compiled-in header, as dm/env.txt lines.

    main.m exports these before the host starts; hle_svc.c reads the memory layout from them, hle_os.c the game's own getenv.
    Only keys the profile sets are written, so anything it leaves out keeps the host's compiled-in default -- the same default
    upstream compiles in when a profile has no key (dm/games/game.py: 32 MB for sys_resource).

    sys_resource is passed exactly as the profile declares it, **zero included**. It looks like a size and is read as a switch
    (IsVirtualAddressMemoryEnabled is "total != 0"), and the two answers are not interchangeable: Big Brain Academy declares 0 and
    runs only when told 0 -- 32 MB and even 1 crash in nninitStartup -- while Brain Memory declares nothing and runs at 32 MB,
    16 MB or 1 (only 0 fails there). So the profile's value is the only one to trust: upstream verified every game against it.

    game_env is the game's own environment (hle_os.c getenv answers these names): a .NET game reserves its GC region from one,
    and without it the runtime takes the default 5 x heap -- 3.75 GB for Absolum, past the pool, and the game aborts in
    nninitStartup with nothing in the log to say why.
    """
    pairs = [('mem_reserve', 'DM_MEM_RESERVE'), ('mem_alias', 'DM_MEM_ALIAS'), ('mem_stack', 'DM_MEM_STACK'),
             ('app_pool', 'DM_APP_POOL'), ('mem_used', 'DM_MEM_USED'), ('stdalloc_cap', 'DM_STDALLOC_CAP')]
    lines = ['%s=%s' % (ev, one(rows, k)) for k, ev in pairs if one(rows, k)]
    ge = one(rows, 'game_env')
    if ge: lines.append('DM_GAME_ENV=' + ge)
    sr = one(rows, 'sys_resource')
    if sr:
        try: v = int(sr, 0)
        except ValueError: v = None
        if v is not None: lines.append('DM_SYS_RESOURCE=%#x' % v)
    # The rest of what the profile turns on is compiled into the host upstream and read from the environment here: every one
    # of these has a DM_* the host asks for before its compiled-in constant (hle_svc.c I_ProgramId, hle_fpslock.c,
    # hle_nvn.c, hle_oe.c, nvn_mtl.m pso_mtl4 / present_snap). Compiled from the placeholder profile they are all off or
    # zero, so a game whose profile sets them gets none of them unless the packer writes them here.
    #
    # Each one is written in the form that host parses, which is not always the form the profile writes:
    #   present_snap  the host reads a ring length ("on" is the 2 its own compiled-in value would have been), not a flag
    #   pso_mtl4      the host compares against "1"
    #   fps_hold      the host compares against "off", so "0" would read as *on*; omit the line to leave the frame step be
    if truthy(one(rows, 'present_snap')): lines.append('DM_PRESENT_SNAP=2')
    mtl4 = one(rows, 'pso_mtl4')
    if mtl4: lines.append('DM_PSO_MTL4=' + ('1' if truthy(mtl4) else '0'))
    hold = one(rows, 'fps_hold')
    if hold: lines.append('DM_FPS_HOLD=' + ('1' if truthy(hold) else 'off'))
    for k, ev in (('fps_target', 'DM_FPS_TARGET'),):
        v = one(rows, k)
        if v: lines.append('%s=%s' % (ev, v.strip()))
    # op_mode is read as atoi() against DM_GAME_OPMODE, which is int(om == "console"): atoi("handheld") and
    # atoi("console") are both 0, so the profile's word has to become the number the host compares.
    om = one(rows, 'op_mode')
    if om: lines.append('DM_OPMODE=' + ('1' if om.strip() == 'console' else '0'))
    # program_id is deliberately NOT written. The shell is compiled from the placeholder profile, so its DM_GAME_PROGRAM_ID
    # is 0, and hle_fs.c compares IsSaveDataExisting's argument against that constant, not against the environment:
    # "an app asks whether its own save data exists" is answered by svcGetInfo(ProgramId) == DM_GAME_PROGRAM_ID. Left alone
    # both sides are 0 and the answer is yes, which is what a game needs; write the real title ID here and svcGetInfo
    # answers it while the constant stays 0, so every game is told it has no save data (Tears of the Kingdom asking for
    # Breath of the Wild's must still be told no -- that is why the constant, not the environment, is the yardstick).
    # Fixing this properly means hle_fs.c reading the same source as hle_svc.c's I_ProgramId, a change to the host.
    # Same again for the keys whose environment name does not follow the profile's: each host reads its own spelling and
    # takes the profile's text as it stands (a list of shader keys, "segment", "display").
    for k, ev in (('feedback_snap', 'DM_NVN_FB_SNAP'), ('gpu_timestamps', 'DM_NVN_GPU_TS'),
                  ('present_interval', 'DM_PRESENT_INTERVAL'), ('shader_key_skip', 'DM_SHADER_KEY_SKIP'),
                  ('ue_fps_cvar', 'DM_UE_FPS_CVAR')):
        v = one(rows, k)
        if v: lines.append('%s=%s' % (ev, v.strip()))
    # ... and the ones that are read as atoi != 0, where the profile writes "on" (atoi("on") is 0, which is off).
    # enc_thread came with the base move of 2026-10-30 (nvn_rec.h g_rec = atoi(getenv("DM_NVN_ENC_THREAD")) > 0 : DM_GAME_ENC_THREAD),
    # and only Need For Speed: Hot Pursuit Remastered sets it ("on": encoding on its own thread, 18.1 -> 16.7 ms of its frame).
    # ue_texture_pool is deliberately NOT here: hle_uecvar.c reads DM_GAME_UE_TEXTURE_POOL on the Mac only and answers 0 on iOS
    # even when the profile sets it ("on iOS only DM_UE_TEXTURE_POOL turns it on, until an iPhone run shows the memory fits"), so
    # writing the profile's value would turn on for every Unreal game what upstream keeps off on an iPhone.
    for k, ev in (('core_preempt', 'DM_PREEMPT'), ('vconv_range', 'DM_NVN_VCONV_RANGE'),
                  ('enc_thread', 'DM_NVN_ENC_THREAD')):
        v = one(rows, k)
        if v: lines.append('%s=%s' % (ev, '1' if truthy(v) else '0'))
    # The keys whose host read a compiled-in constant and nothing else (no getenv at all: dm/gfx/nvn/hle_nvn.c, dm/gfx/nvn/nvn_mtl.m,
    # dm/host/main.c, hle_os.c, hle_oe.c, hle_svc.c, dm/ios/Sources/DmGamepad.m). The game-free shell is compiled against the placeholder
    # profile, where every one of them is off or 0 (pad_layout is the exception: the placeholder asks for full, deliberately -- see below),
    # so a profile that sets them changed nothing until the host learned to ask the environment.
    # Written as the number the host's atoi() compares, never as the profile's word: atoi("on") and atoi("boost") are both 0, which reads as
    # off -- the same trap op_mode fell into. off is written too, so a stale value in Documents/env.txt cannot turn one on.
    for k, ev in (('tex_flush_upload', 'DM_TEX_FLUSH_UPLOAD'), ('tex_dirty_upload', 'DM_TEX_DIRTY_UPLOAD'),
                  ('render_cond', 'DM_RENDER_COND'), ('alloc_big_align', 'DM_ALLOC_BIG_ALIGN'),
                  ('tex_dyn_slots', 'DM_TEX_DYN_SLOTS'), ('mem_map_limit', 'DM_MEM_MAP_LIMIT')):
        v = one(rows, k)
        if v: lines.append('%s=%s' % (ev, '1' if truthy(v) else '0'))
    # perf_mode is the word "boost", not a switch: truthy("normal") would be 1 while game.py's int(pm == "boost") is 0, so compare the word.
    pm = one(rows, 'perf_mode')
    if pm: lines.append('DM_PERF_BOOST=' + ('1' if pm.strip() == 'boost' else '0'))
    # game_args goes as the profile writes it: the value holds its own '=' (Unreal's -SwitchVirtualAddressSpaceSize=2048), and main.m
    # splits dm/env.txt at the first '=' only, taking the rest whole.
    ga = one(rows, 'game_args')
    if ga: lines.append('DM_GAME_ARGS=' + ga.strip())
    # pad_layout: the profile writes simple / full / touch and game.py turns them into 0 / 1 / 2 by their position in that tuple.
    # Written only when the profile sets it. A profile that sets no key leaves the shell's own default, full (the placeholder profile asks
    # for it), where upstream's Mac host would compile game.py's default, simple. That difference is deliberate and stays: the shell has to
    # pick one layout for games it knows nothing about, and full is the one whose controls all fit.
    pl = one(rows, 'pad_layout')
    if pl and pl.strip() in ('simple', 'full', 'touch'):
        lines.append('DM_PAD_LAYOUT=%d' % ('simple', 'full', 'touch').index(pl.strip()))
    return lines


def step_assemble(shell, stage, game, patched, mods, gdir, rows, name, version, tid, msl_dir, work, aoc_dir=None):
    """Unpack the shell and put the game into it: guest library, MSL source, RomFS, tables, icon, identity."""
    # Move a previous stage aside instead of deleting it: this environment counts file deletions and refuses to go past a
    # threshold, which a 5000-file app tree trips instantly. Renaming keeps the build going; the old tree is reported so it
    # can be deleted by hand (it is several GB because the RomFS is copied into it).
    if stage.exists():
        # A failed or interrupted run leaves its stage tree behind, and this used to move the previous one aside instead
        # of removing it, so they piled up until the disk filled (a 4 GB game leaves ~10 GB per run). Removing is what
        # --clean does by hand and it is slow here -- every delete goes through the host's safety layer -- so try, and
        # fall back to the old rename when that is refused.
        for stale in sorted(stage.parent.glob('stage-old*')):
            try:
                shutil.rmtree(stale)
                say('removed an earlier run\'s stage tree: %s' % stale.name)
            except OSError as e:
                warn('could not remove %s (%s): it stays until --clean "%s"' % (stale.name, e, stage.parent.parent))
        old = stage.with_name('stage-old')
        if old.exists(): old = stage.with_name('stage-old-%d' % int(time.time()))
        stage.rename(old)
        warn('the previous stage tree was kept as %s (delete it when the build is confirmed)' % old.name)
    stage.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(shell) as z:
        apps = sorted({n.split('/')[1] for n in z.namelist() if n.startswith('Payload/') and n.count('/') >= 2 and n.split('/')[1].endswith('.app')})
        if len(apps) != 1: die('the shell holds %d apps, expected 1: %s' % (len(apps), apps))
        app = apps[0]
        if not any(n.startswith('Payload/%s/dm/' % app) for n in z.namelist()): die('this is not a shell (no dm/ inside): %s' % shell)
        z.extractall(stage)
    A = stage / 'Payload' / app
    say('shell unpacked: %s' % A.name)

    step_guest(patched, mods, A)
    for m in mods:
        for suf in ('.fixups.tsv', '.gates.tsv', '.cuts.tsv', '.obs.tsv'):
            s = patched / (m + suf)
            if s.is_file(): shutil.copyfile(s, A / 'dm' / s.name)

    # shader libraries: the directory name is the chain version, which the shell reads at run time
    if msl_dir.is_dir():
        dst = A / 'msl' / msl_dir.name
        shutil.copytree(msl_dir, dst)
        n = len(list(dst.glob('*_b.metallib')))
        if n: say('shader source for %d shaders -> msl/%s' % (n, msl_dir.name))   # empty for a GL package, whose shaders go to msl/gl instead

    shutil.copytree(game / 'RomFS', A / 'romfs', symlinks=True)
    say('RomFS in place')

    # Add-on content: <App>/aoc/<index>/, which main.m hands to the host as DM_AOC_DIR and hle_fs.c mounts for the game.
    # Absent when the folder holds no DLC, and then the host reports no add-on content at all -- the same as a console
    # without it installed.
    if aoc_dir is not None and aoc_dir.is_dir():
        dst = A / 'aoc'
        if dst.exists(): shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(aoc_dir, dst, symlinks=True)
        say('add-on content in place: index %s' % ', '.join(sorted((d.name for d in dst.iterdir() if d.is_dir()), key=int)))

    # tables the game profile carries (shader map, c1 list, warm-up list, uniform extents)
    for n in ('shader_map.tsv', 'pso_manifest.tsv', 'c1_full.tsv', 'cb_extent.tsv'):
        if (gdir / n).is_file(): shutil.copyfile(gdir / n, A / 'dm' / n)
    # The shader pairs the pipeline prebuild and the hybrid pipelines read (nvn_mtl.m pso_prebuild, from the folder
    # DM_NVN_SHMAP names). Upstream's build_ipa.sh copies the profile's two; the split's copy stands in for pso_pairs.tsv
    # when the profile has none, which is the substitution the Mac route makes with --pso-pairs. Without them a profile
    # with pso_mtl4 on logs "pipeline prebuild: nothing to do" and every new pipeline compiles on the submit thread.
    for n in ('shader_pairs.tsv', 'pso_pairs.tsv'):
        src = gdir / n
        if not src.is_file(): src = work / 'shader' / 'tables' / n
        if src.is_file(): shutil.copyfile(src, A / 'dm' / n)

    step_launcher(tid, patched, game, A, work)

    # The shell was built from the placeholder profile, so every value compiled into its host is the placeholder's. The
    # game's own values are written into dm/env.txt here: main.m turns that into environment variables before the host
    # starts, and the host reads them (env_for lists them and the form each one takes). Getting the memory layout wrong
    # does not crash at once -- the game asks for the wrong heap and aborts in SetHeapSize (Hades II asked for pool - 64 MB
    # instead of pool - 108 MB and overflowed the pool by 44 MB).
    env_lines = env_for(rows)
    if env_lines:
        (A / 'dm' / 'env.txt').write_text('\n'.join(env_lines) + '\n', encoding='utf-8')
        say('dm/env.txt: ' + ', '.join(env_lines))

    # App identity: name, version, bundle ID, icon.
    #
    # The bundle ID has to be per game. iOS identifies an app by it, so every package carrying the shell's
    # org.nxnative.shell is the same app: installing the second game replaces the first, however different the two look on
    # the home screen. Upstream derives it from the title ID (build_ipa_from_nsp.py), which also keeps a repack of the same
    # game an update of it rather than a second copy.
    ip = A / 'Info.plist'
    try:
        with open(ip, 'rb') as f: pl = plistlib.load(f)
        pl['CFBundleIdentifier'] = 'org.nxnative.%016x' % int(tid, 16)
        pl['CFBundleDisplayName'] = name
        pl['CFBundleShortVersionString'] = version
        pl['CFBundleVersion'] = version
        pl['DMShowFPS'] = 'NO'
        pl['DMLanguage'] = os.environ.get('NXN_LANG', 'en-US')
        # Which shell this package was made from. The host prints Info.plist's DMCommit in its first line
        # (dm/host/main.c: "build: commit %s, shader chain %s, profile %s"), and only the Mac packer wrote it -- so a
        # phone log said "not recorded" and naming the shell meant asking for dm/shell_info.txt. The shell's own stamp
        # carries the commit it was built from, so the line says something here too. Written whole (40 chars), not the
        # Mac packer's 12: this side has no git checkout to shorten against and the full hash is what a lookup needs.
        sha = shell_stamp(shell).get('SOURCE_SHA', '')
        if re.fullmatch(r'[0-9a-f]{40}', sha):
            pl['DMCommit'] = sha
            say('Info.plist: DMCommit %s (the shell this package was built from)' % sha[:12])
        else:
            warn('the shell\'s dm/shell_info.txt carries no SOURCE_SHA, so log.txt will say "commit not recorded"')
        icon_src = game / 'icon.jpg'
        if icon_src.is_file():
            try:
                from PIL import Image
                im = Image.open(icon_src).convert('RGB')
                for suffix, px in (('@2x', 120), ('@3x', 180)):
                    im.resize((px, px), Image.LANCZOS).save(A / ('AppIcon60x60%s.png' % suffix))
                # Old-style icon declaration: actool (which builds Assets.car) is Mac-only, so the shell ships an empty
                # catalog and the PNGs are declared here instead.
                pl['CFBundleIcons'] = {'CFBundlePrimaryIcon': {'CFBundleIconFiles': ['AppIcon60x60']}}
                say('icon written from the game icon')
            except Exception as e:
                warn('could not write the icon: %s' % e)
        with open(ip, 'wb') as f: plistlib.dump(pl, f)
        say('Info.plist: %s %s' % (name, version))
    except Exception as e:
        warn('could not edit Info.plist (%s); the app keeps the shell identity' % e)
    return A


def step_launcher(tid, patched, game, A, work):
    """The settings screen that upstream put in front of the game (Launcher.swift) is drawn from dm/launcher.json, which
    dm/games/settings.py writes: the rows of dm/games/settings.tsv whose condition holds for this game, with its defaults.

    Upstream's build_ipa.sh does this step; a packer that does not leaves the screen empty, and Launcher.swift then starts the
    game at once -- so this warns instead of failing, unlike the steps whose output the game itself needs.
    """
    s = DM / 'games' / 'settings.py'
    if not s.is_file(): return                     # upstream before the launcher existed
    nacp = game / 'control.nacp'
    if not nacp.is_file():
        warn('no control.nacp beside the RomFS: no dm/launcher.json, so the game starts without the settings screen')
        return
    out = A / 'dm' / 'launcher.json'
    r = subprocess.run([str(PY), str(s), tid, str(patched), str(nacp), str(out)],
                       capture_output=True, text=True, env=dict(os.environ, PYTHONUTF8='1'))
    log = work / 'logs' / 'launcher.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text((r.stdout or '') + (r.stderr or ''), encoding='utf-8', errors='replace')
    if r.returncode or not out.is_file():
        msg = ((r.stderr or r.stdout or '').strip().splitlines() or ['no output'])[-1]
        warn('dm/launcher.json could not be written (%s: see %s), so the game starts without the settings screen' % (msg, log))
        return
    say('launcher settings: %s' % (r.stdout or '').strip())


def step_pack(A, out_ipa, rows, work):
    """pack_ipa.py packs straight from the folders, streaming the big ones and keeping the executable bit."""
    app = 'Payload/%s' % A.name
    maps = ['%s=%s' % (app, A)]
    skips = ['_CodeSignature', 'embedded.mobileprovision']
    for sub in ('msl', 'romfs', 'aoc'):
        # Each big folder is mapped on its own so pack_ipa.py streams it; skipping it in the app walk keeps entries unique.
        if (A / sub).is_dir():
            maps.append('%s/%s=%s' % (app, sub, A / sub))
            skips.append(sub)
    store = one(rows, 'zip_store', '')
    args = [PY, IOS / 'tools' / 'pack_ipa.py', out_ipa]
    if store: args += ['--store', store]
    for sub in skips: args += ['--skip', '%s/%s' % (app, sub)]
    args += maps
    run(args, log=work / 'logs' / 'pack_ipa.log', what='packing the IPA')
    # pack_ipa keeps <ipa>.idx beside the IPA so a later pack can copy unchanged entries out of it. That needs APFS and
    # os.pread, so off macOS nothing is ever reused: the file would just sit next to the user's IPA.
    idx = Path(str(out_ipa) + '.idx')
    if os.name == 'nt' and idx.is_file(): idx.unlink()


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description='Pack the shell and your own game files into an IPA, without a Mac.')
    ap.add_argument('shell', nargs='?', default='', help='the shell zip (nxnative-shell-build-input.zip)')
    ap.add_argument('nsp_dir', nargs='?', default='', help="a folder with the game's NSP/XCI files")
    ap.add_argument('-k', '--keys', default=os.environ.get('KEYS', ''), help='prod.keys')
    ap.add_argument('-o', '--out', default='', help='output IPA (default: next to the NSPs)')
    ap.add_argument('-p', '--package', default='', help='version to pack (default: the newest one in the folder)')
    ap.add_argument('--stop-after', default='', choices=['', 'extract', 'rewrite', 'shaders', 'guest', 'assemble'],
                    help='stop when that step is done, leaving the work folder as it is (debugging, or checking one '
                         'step before paying for the rest)')
    ap.add_argument('--force', action='store_true', help='redo steps whose output already exists')
    ap.add_argument('--no-shaders', action='store_true', help='a GL/Vulkan game: build it without a shader library, so it boots and captures its own shaders (no picture until they are packed in)')
    ap.add_argument('--allow-damaged', action='store_true', help='read files that fail their hash check as they are: a dump with a few bad files (a couple of music tracks) still packs')
    ap.add_argument('--skip-oodle-check', action='store_true',
                    help='do not stop early for a game whose shaders sit in Oodle- or LZ5-compressed archives (it then stops '
                         'at the shader step instead). Only useful when the scan is wrong about your files')
    ap.add_argument('--capture-gl', default='', help='boot the game in the Mac host and capture the GLSL it assembles at run time, then stop (macOS, Apple silicon)')
    ap.add_argument('--vk-capture', default='', metavar='FOLDER',
                    help="a Vulkan game's captured SPIR-V (<key>.spv, which DM_VK_CAPTURE writes on the phone) for a game "
                         'whose modules are generated at run time. Games that ship SPIR-V in their RomFS need no capture '
                         'at all (Hades keeps 74 in Content/EffectsForgeSwitch).')
    ap.add_argument('--gl-capture', default='', metavar='FOLDER',
                    help="an OpenGL game's captured shaders (<key>.vert / <key>.frag, which DM_GL_CAPTURE writes on the "
                         'phone): translated to Metal sources and packed, so the game draws. Needs glslangValidator and '
                         'spirv-cross on PATH or in --tools.')
    ap.add_argument('--keep-work', action='store_true',
                    help='keep the working folder after a good pack, so a rerun reuses the extraction, the rewritten '
                         'modules and the shaders (it is several times the size of the game)')
    ap.add_argument('--clean', default='', metavar='NSP_FOLDER',
                    help='remove the working folder an earlier run left in NSP_FOLDER, and exit')
    ap.add_argument('--tools', default=os.environ.get('NXN_TOOLS', ''),
                    help='folder holding the nx_extract, dmsh_xlate, spirv-cross, glslangValidator and zstd executables '
                         '(the windows-tools CI artifact, packed as nxnative-windows-tools.zip). They are put on PATH and '
                         'used instead of building anything.')
    a = ap.parse_args()
    if a.capture_gl and a.shell and not a.nsp_dir:      # capture mode's only positional is the game folder
        a.shell, a.nsp_dir = '', a.shell

    for st in (sys.stdout, sys.stderr):
        try: st.reconfigure(errors='replace')
        except Exception: pass

    if a.clean:
        clean_folders(Path(a.clean).resolve()); return
    if not a.shell and not a.capture_gl: die("give the shell zip, or --clean <NSP folder> to remove an earlier run's temporary files")
    if not a.nsp_dir: die("give the folder holding the game's NSP/XCI files")
    if not (DM / 'games').is_dir():
        die('this script needs the repository\'s dm/ folder beside it; it is not a file to copy out on its own.\n'
            '    expected <repo>/scripts/nxn_pack.py with <repo>/dm/ next to scripts/, or nxn_pack.py next to dm/.\n'
            '    looked for %s -- the game profiles, the rewriter and the shader tools all live there.' % DM)

    if not a.capture_gl and not a.shell: die('give the shell file (unzipped or .zip), or --capture-gl to only capture shaders')
    shell = Path(a.shell).resolve() if a.shell else Path('.')
    folder = Path(a.nsp_dir).resolve()
    if not a.capture_gl and not shell.is_file(): die('no shell file: %s' % shell)
    if not folder.is_dir(): die('no such folder: %s' % folder)
    check_free_space(folder)
    # Where the IPA goes is only needed at the very end, twenty minutes away, so a folder that does not exist or a disk
    # that cannot hold the package used to cost the whole run and end in a Python traceback. Say it now instead.
    if a.out:
        parent = Path(a.out).resolve().parent
        if not parent.is_dir():
            die('the folder for -o does not exist: %s\n    Create it, or give a path inside one that does.' % parent)
        if not os.access(str(parent), os.W_OK):
            die('cannot write to the folder for -o: %s' % parent)
        src = sum(p.stat().st_size for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in ('.nsp', '.xci', '.nsz'))
        free = shutil.disk_usage(parent).free
        if src and free < src:
            die('not enough space on the disk that would hold the IPA (%s): %.1f GiB free, and the package alone is about '
                '%.1f GiB.\n    Give another -o, or free some space there.' % (parent, free / 2**30, src / 2**30))
    if not a.keys: die('give prod.keys with -k')
    keys = Path(a.keys).resolve()
    stamp = shell_stamp(shell)
    check_shell(stamp)

    tools_dir = None
    if a.tools:
        tools_dir = Path(a.tools).resolve()
        if not tools_dir.is_dir(): die('--tools %s is not a folder' % tools_dir)
        os.environ['PATH'] = str(tools_dir) + os.pathsep + os.environ.get('PATH', '')
        say('tools: %s' % tools_dir)
    dotnet = check_env(keys, tools_dir)
    apps = scan_nsps(folder)
    probed = nx = None
    if not apps:
        xcis = sorted(folder.glob('*.xci'))
        if len(xcis) == 1:
            # The only thing here is a cartridge dump, which carries nothing that identifies it without the keys: extract it
            # (work the pack needs anyway) and take the title ID from nx_extract's report.
            probed = folder / (xcis[0].stem + '.probe')
            nx = build_nx_extract(dotnet, probed, tools_dir)
            tid = probe_xci(nx, keys, xcis[0], probed / 'extract', a.allow_damaged)
            if tid:
                apps = {tid: {'base': xcis[0], 'updates': {}, 'dlc': [], 'all': [('base', 'v0', xcis[0])]}}
    if not apps:
        die('no game could be identified in %s.\n'
            '    An .nsp is read from the package itself; an .xci is read by extracting it, which needs prod.keys that match '
            'that cartridge.' % folder)
    if len(apps) != 1:
        die('the folder holds %d different games (%s); keep one game per folder' % (len(apps), ', '.join(sorted(apps))))
    tid = next(iter(apps)); fam = apps[tid]
    if not fam['base']:
        die('no base game for %s in %s -- the folder only holds updates or DLC: %s'
            % (tid, folder, ', '.join(p.name for _, _, p in fam['all'])))
    gdir, rows = load_profile(tid)
    mods = one(rows, 'modules', 'main sdk').split()
    need = one(rows, 'shader_versions', 'v0').split()
    builds = {}
    for line in rows.get('build', []):
        f = line.split('\t')
        builds[f[0]] = f[1] if len(f) > 1 else ''

    # The profile already says this title's shaders are out of reach of this route (needs_mac_shaders, the key
    # supported_list.py marks 暂不支持 from). Refuse before the extraction: the same answer cost one reporter a whole
    # unpack and then a [RED] from shader_split.py that named a folder instead of the reason (issue #3).
    if one(rows, 'needs_mac_shaders') and not a.skip_oodle_check:
        die('%s (%s) keeps its shaders where only its own decoder reaches them: in archives compressed with Oodle or LZ5,\n'
            '    unpacked by the copy of that decoder linked into the game\'s own code -- which this packer runs inside the\n'
            '    Mac host (dm/host/hle_oodle.c) and only on a Mac with Apple silicon, since it executes the game\'s ARM64\n'
            '    modules. There is no Mac in the Windows route (tools/ holds .exe files only), so this title cannot be packed\n'
            '    here, on any machine: docs/SUPPORTED.md lists it as 暂不支持.\n'
            '    If you already have the decoded shader archives from such a run (ue_shaders/ or glslc_capture/ beside the\n'
            '    extract folder), --skip-oodle-check packs on and shader_split.py uses them.'
            % (one(rows, 'name', tid), tid))

    # Which version to pack: the newest one in the folder, as upstream does ("the script packs the newest version in the
    # folder"), or -p. Only that version is extracted -- the profile's shader tables are a union of several versions, so they
    # already cover any version the profile registers, and a version it does not register is reported rather than refused.
    #
    # A cartridge dump is the exception: it carries no version at all, so "the newest in the folder" means nothing there and
    # the profile decides. That holds whether it was identified just now or by a title ID an earlier run recorded.
    is_dump = fam['base'].suffix.lower() == '.xci'
    upds = sorted(fam['updates'], key=lambda s: int(s[1:]) if s[1:].isdigit() else -1)
    pkg = a.package or (upds[-1] if upds else (one(rows, 'package', 'v0') if is_dump else 'v0'))
    if not is_dump and pkg != 'v0' and pkg not in fam['updates']:
        die('no update for %s in %s (the folder has: %s)'
            % (pkg, folder, ', '.join(upds) if upds else 'only the base game'))
    if pkg == UNKNOWN_VER:
        # Not a warning: this is settled further down, from the unpacked files (identify_version), never from the name.
        say('this update has no [v<N>] tag in its name and keeps its CNMT encrypted (cnmt.nca): the version is read from '
            'the files once they are unpacked')
    elif pkg not in builds:
        warn('%s is not a version this profile registers (%s): it was never tested, and fixes made for a tested version '
             '(60 fps, for instance) do not apply to it' % (pkg, ', '.join(sorted(builds)) or 'none'))
    elif pkg not in need:
        warn('%s is registered, but the shader tables were built from %s -- shaders this version adds may be missing'
             % (pkg, ' '.join(need)))
    versions = [pkg]
    # A profile whose shader tables are the UNION of several versions (Wonder: v0 + v327680; Mario Kart 8 Deluxe:
    # v1441792 + v1507328) has to be split from every one of them -- that is what its `shader_versions` line means, and
    # upstream splits exactly that set. One version alone holds fewer shader records than those tables index into, which
    # is what made the shader build stop with a bare IndexError (issue #5). A version the folder does not have is refused
    # here, before the extraction, with upstream's own sentence ("packing either needs both NSPs").
    derive_for = None
    if len(need) > 1 and pkg in need:
        absent = [v for v in need if v != 'v0' and v not in fam['updates']]
        if absent:
            # Upstream's rule (dm/games/README.md, shader_versions): "A registered version is built from the profile as it
            # is only when all of them are in the folder; otherwise from a derived profile, with tables from the packed
            # version alone." So this is a derived profile and a warning, not a refusal.
            derive_for = ('its shader tables are the union of %s and %s not in the folder'
                          % (' + '.join(need), ', '.join(absent) + (' is' if len(absent) == 1 else ' are')))
            warn('%s: %s.\n'
                 '    Packing %s from a profile derived from these files, with the shader tables from %s alone -- as\n'
                 '    upstream does. Put %s in the folder to pack against the tables the profile registers (a merged\n'
                 '    package does not help: it holds only the newest version\'s files).'
                 % (one(rows, 'name', tid), derive_for, pkg, pkg, ', '.join(absent)))
        else:
            versions = list(need)
            say('shader tables are the union of %s: splitting all of them' % ' + '.join(versions))
    # A cartridge dump carries no version of its own, which decides how the version is chosen (above) -- not whether an
    # update in the same folder is used. An XCI of the base game plus an NSP update is an ordinary pairing: dropping the
    # update packed the cartridge's v0 files under the update's version number, and the module build IDs then matched
    # nothing in the profile.
    picks = {v: (fam['base'], fam['updates'].get(v) if v != 'v0' else None) for v in versions}
    say('files: base %s%s' % (fam['base'].name,
        (', update %s' % fam['updates'][pkg].name) if pkg != 'v0' and pkg in fam['updates'] else ', no update')
        + (('; %d DLC' % len(fam['dlc'])) if fam['dlc'] else ''))

    work = folder / (tid + '_pack')
    work.mkdir(parents=True, exist_ok=True)
    (work / 'logs').mkdir(exist_ok=True)
    if probed and (probed / 'extract').is_dir():
        # the probe extraction is this version's extraction: move it in rather than unpack the same few GB again
        dst = work / 'extract' / pkg
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            try: shutil.move(str(probed / 'extract'), str(dst))
            except OSError as e: warn('cannot move the probe extraction into %s (%s): it will be extracted again' % (dst, e))
        shutil.rmtree(probed, ignore_errors=True)
    # Default output name: the profile's own, else the game's name. A shared fallback like "game.ipa" would let two games
    # in one folder overwrite each other's package.
    ipa_name = one(rows, 'ipa', '')
    if not ipa_name:
        ipa_name = (re.sub(r'[^0-9A-Za-z._ -]+', '', one(rows, 'name', tid)).strip() or tid) + '.ipa'
    out_ipa = Path(a.out).resolve() if a.out else folder / ipa_name
    say('game: %s (%s)' % (one(rows, 'name', tid), tid))
    say('shell: %s' % shell.name)
    say('versions to pack: %s (packing %s)' % (', '.join(versions), pkg))
    say('work folder: %s' % work)

    nx = nx or build_nx_extract(dotnet, work, tools_dir)
    dirs = step_extract(nx, keys, folder, picks, work, versions, a.allow_damaged)
    aoc_dir = step_aoc(nx, keys, fam['dlc'], work, tid, a.force, a.allow_damaged)
    if a.stop_after == 'extract': return say('stopped after extract')
    game = dirs[pkg]
    if pkg == UNKNOWN_VER:
        # Now the files are here, so the version comes from them: the ExeFS modules' build IDs, which the profile records
        # per build. The work folder keeps the label it was extracted under -- the name of a directory is no more part of
        # the identification than the name of the package was.
        try: found = identify_version(game, rows)
        except Exception as e: found = None; warn('cannot read the version from the unpacked files (%s)' % e)
        if found:
            say('the unpacked files are %s: their module build IDs are that profile build row' % found)
            pkg = found
        else:
            warn('the unpacked files match no build row in %s: packing them from a profile derived from them'
                 % gdir.name)
    # Refused here rather than at the shader step: this route has no Mac host, so an archive that only the game's own
    # Oodle decoder unpacks is the end of the road, and the extraction and the rewrite that used to run first were
    # several minutes of work for an answer the RomFS headers already hold.
    if not a.skip_oodle_check:
        why = oodle_archives(game, mods)
        if why:
            die('this game keeps its shaders in archives that only its own Oodle decoder can unpack:\n'
                + ''.join('    %s: %s\n' % kv for kv in why.items()) +
                '    Oodle is not open source. The decoder is the copy linked into the game\'s own code, and it is run\n'
                '    inside the Mac host (dm/host/hle_oodle.c) -- which needs a Mac with Apple silicon, since it executes\n'
                '    the game\'s ARM64 modules. This packer drives the Windows executables in tools/ and has no such host\n'
                '    to borrow the decoder from, so this title cannot be packed by this route, on any machine.\n'
                '    Games that pack straight from Windows: docs/SUPPORTED.md.\n'
                '    --skip-oodle-check packs on and stops at the shader step as before.')
    derived_dir = None
    reg = gdir
    bad, had_row = check_build_ids(game, rows, pkg, mods)
    if not bad and not derive_for:
        say('build IDs match the profile for %s' % pkg)
    else:
        # Upstream packs any version: when the files are not the build the profile was brought up on, it derives a profile
        # from them instead of refusing. Refusing here would turn away a dump that plays fine -- the shader tables are made
        # from these files anyway, and the rewriter's observation gates simply do not apply to an unknown build.
        sys.path.insert(0, str(TOOLS))
        try:
            import nacp
            disp = nacp.version(game / 'control.nacp') or '?'
        except Exception:
            disp = '?'
        reg = gdir          # the registered profile, for anything the derived one cannot answer (graphics_route)
        derived = work / 'profile' / tid
        derive_profile(reg, tid, pkg, game, mods, derived, disp)
        os.environ['DM_GAMES_DIR'] = os.pathsep.join(
            [str(derived.parent)] + [d for d in os.environ.get('DM_GAMES_DIR', '').split(os.pathsep) if d])
        os.environ['DM_GAMES_FIRST'] = '1'
        gdir, rows = derived, load_rows(derived)
        mods = one(rows, 'modules').split()      # the derived list, not the one the registered profile names
        derived_dir = derived
        builds[pkg] = disp
        if bad:
            # Not printed when the profile is only being derived because a version its tables cover is missing: that
            # warning above already says why, and the build IDs do match.
            warn('these files are not the build of %s that profile %s registers (%s).\n'
                 '    Packing %s from a profile derived from them, as upstream does: the shader tables come from these files, '
                 'and the observation gates registered for the other build do not apply.' % (pkg, reg.name, '; '.join(bad), pkg))
        say('derived profile: %s (%s)' % (derived, disp))

    patched = work / 'build' / 'patched'
    step_rewrite(game / 'ExeFS', mods, patched, work, a.force)
    # After the rewrite, not before: only the rewritten modules' bytes reach GetInfo's UsedMemorySize.
    # Only for a derived profile: a game the profile was brought up on has its mem_used settled by the profile author,
    # and second-guessing it would resize the heap of titles that already work.
    filled = fill_mem_used(patched, mods, gdir, rows) if derived_dir is not None else None
    if filled:
        value, total = filled
        rows = load_rows(gdir)
        say('mem_used %s: the rewritten modules are %.0f MB, more than the default 64 MB the heap is sized against'
            % (value, total / 2**20))
    if a.stop_after == 'rewrite': return say('stopped after rewrite')
    if a.capture_gl:
        # A capture run is the whole point of this invocation: the shell, the shader chain and the IPA are not involved.
        out = capture_gl(game, gdir, patched, work, tid)
        want = a.capture_gl
        if want:
            dst = Path(want).resolve()
            shutil.rmtree(dst, ignore_errors=True); shutil.copytree(out, dst)
            say('%d files → %s' % (len(os.listdir(dst)), dst))
        return say('stopped after the capture')
    check_gates(stamp, patched, mods)

    # NVN games hold their shaders in the game files, so they are split out and translated here. A GL or Vulkan game does not:
    # its shaders only exist once it runs (LÖVE assembles each one in Lua), and upstream captures them by booting the game on a
    # Mac. Until this packer has that capture route, such a game is built without a shader library -- it boots, plays its music
    # and draws nothing -- which is exactly the state DM_GL_CAPTURE needs to collect the sources (Documents/env.txt).
    route = graphics_route(gdir, reg)
    gl_out = None
    if route == 'gl':
        gl_out = work / 'build' / 'gl'
        if a.gl_capture:
            cap = Path(a.gl_capture).resolve()
        else:
            # Nothing to capture: dm/gfx/tools/gl_prebuilt.py runs the game's own LÖVE wrapper (the Lua source a LÖVE
            # binary carries) over its .fs files, which produces exactly the GLSL the game would have compiled, byte for
            # byte -- so the package is complete from the game files alone, and a user never runs the game first. The
            # capture stays as the yardstick: --gl-capture pins one, and the profile's gl_capture.tsv checks this route.
            cap = work / 'gl_prebuilt'
            args = [PY, GFX / 'gl_prebuilt.py', game, cap]
            tab = gdir / 'gl_capture.tsv'
            if tab.is_file(): args += ['--expect', tab]
            run(args, log=work / 'logs' / 'gl_prebuilt.log', what='generating the GLSL from the game files')
        step_gl_shaders(cap, gl_out, gdir, tid, a.tools)
    vk_out = None
    if route == 'vulkan' and not a.no_shaders:   # --no-shaders is the capture run: a game that builds its SPIR-V at run
                                                 # time has none in the RomFS, and a missing folder is not an error there
        # SPIR-V, not GLSL: vk_shaders.py only needs spirv-cross here (Apple's Metal compiler is the missing half, and
        # --source-only skips it), so a Windows machine produces the same table a Mac does. Most Vulkan games keep their
        # SPIR-V in the RomFS (Hades: 74 files in Content/EffectsForgeSwitch), so nothing has to be captured; a game that
        # builds its modules at run time needs --vk-capture with what DM_VK_CAPTURE wrote.
        root = work / 'build' / 'vkroot'
        ins = [game / 'RomFS'] + ([Path(a.vk_capture).resolve()] if a.vk_capture else [])
        run([PY, GFX / 'vk_shaders.py', *ins, root, '--source-only'],
            log=work / 'logs' / 'vk_shaders.log', what='translating the SPIR-V to Metal source',
            hint=vk_capture_hint)
        vk_out = root / 'vk'
        if not (vk_out / 'vk_shaders.tsv').is_file(): die('vk_shaders.py wrote no table under %s' % vk_out)
        tbl = [l for l in (vk_out / 'vk_shaders.tsv').read_text(encoding='utf-8').splitlines() if l and not l.startswith('#')]
        say('Vulkan shaders: %d translated (%d sources) -> %s' % (len(tbl), len(list((vk_out / 'src').glob('*.metal'))), vk_out))
    # A GL game with a capture needs neither: its shaders came from the capture, and there is nothing left to refuse.
    if route != 'nvn' and not a.no_shaders and gl_out is None and vk_out is None:
        # Refused by default: such a package boots and plays its music but draws nothing, and a user who did not ask for that
        # would only see a black screen with no explanation. The flag exists for the capture run that produces the shaders.
        die('%s game: its shaders are %s, so no game file holds them; upstream captures them by booting the game on a Mac.\n'
            '    Without that capture this packer cannot make a playable package.\n'
            '    To build one anyway -- it boots and plays its music but draws nothing, which is what a capture run needs --\n'
            '    add --no-shaders, then run it on the phone with Documents/env.txt holding DM_GL_CAPTURE=<writable dir>,\n'
            '    and pack the folder it wrote with --gl-capture <folder>.'
            % ('this', 'put together at run time' if route == 'gl' else 'generated at run time'))
    if route != 'nvn' and gl_out is None and vk_out is None:
        say('%s game: no NVN shaders to split or translate; building without a shader library (--no-shaders)'
            % ('OpenGL' if route == 'gl' else 'Vulkan'))
    # the four tables shader_split.py writes (upstream tuple, pso_pairs.tsv included since it is what the pipeline prebuild
    # reads; the .conflicts.tsv is derived by other tools, not passed here)
    tables = ['shader_map.tsv', 'shader_facts.tsv', 'shader_pairs.tsv', 'pso_pairs.tsv']
    if route == 'nvn':
        pkg_dir, tb, diff = step_shaders(dirs, versions, gdir, tables, work, tid, a.force)
        # shader_facts.tsv is the exception: the split merges the profile's shader_facts_manual.tsv into it, and the
        # translator reads the profile's table, so a difference there is written back below (upstream writes it back too
        # when the profile is provisional). The other tables are the ones dmsh_build.py indexes its package with, so a
        # difference only matters when it actually runs off the end.
        fatal = [n for n in diff if n != 'shader_facts.tsv']
        over = map_overflow(pkg_dir, gdir / 'shader_map.tsv') if fatal else None
        if over and derived_dir is None:
            # This is the IndexError users saw (issue #5): dmsh_build.py's rec_of reads packs[name][index] from the
            # profile's table, and the package the split just made holds fewer records. Naming it here, before the
            # translation, is upstream's behaviour too ("the split shader tables differ from game profile ...").
            die('the shader tables split from these files differ from %s\'s (%s), and its table indexes past what these\n'
                '    files hold: %s.\n'
                '    The tables were split from %s: pack those files together -- the base game and every update the profile\n'
                '    lists for this game -- or pack a version this profile registers.'
                % (gdir.name, ', '.join(fatal), over, ' + '.join(need) if need else 'another build'))
        if fatal:
            warn('the shader tables split from these files differ from %s\'s: %s (%s) -- using the ones just split'
                 % (gdir.name, ', '.join(fatal), 'indices still fit' if not over else 'they agree with the package'))
        if derived_dir is not None:
            # The shader tool reads its tables from the profile it loads, and a derived profile has none (the split just made
            # them). Without this it falls back to the registered profile's tables, which name shader packs this version lacks.
            for f in sorted(tb.glob('*.tsv')):
                shutil.copyfile(f, derived_dir / f.name)
            say('derived profile tables: %s' % ', '.join(f.name for f in sorted(tb.glob('*.tsv'))))
        else:
            # The translator answers its queries from the PROFILE's shader_facts.tsv (dmsh_facts.py: GAME.table), not from
            # the copy the split just made, so a fact that only the profile's shader_facts_manual.tsv carries never reaches
            # it: the split merges the manual rows, the translator reads the unmerged table, and the difference shows up only
            # on the phone (a pipeline Metal refuses). Written back only when the data rows actually differ, so every game
            # whose profile already matches keeps its committed table untouched. Upstream does the same when the profile is
            # provisional ("a provisional profile takes the tables as they come", scripts/build_ipa_from_nsp.py).
            facts_split, facts_prof = tb / 'shader_facts.tsv', gdir / 'shader_facts.tsv'
            if facts_split.is_file() and facts_prof.is_file():
                # Not "rows": main() binds that name to the profile's row table, and a local called rows here rebinds it for
                # the whole function, so step_assemble's one(rows, ...) got this lambda and died with
                # AttributeError: 'function' object has no attribute 'get' -- after the 6-minute translation, every time.
                fact_rows = lambda p: [l for l in p.read_text(errors='replace').splitlines() if l and not l.startswith('#')]
                if fact_rows(facts_split) != fact_rows(facts_prof):
                    shutil.copyfile(facts_split, facts_prof)
                    say('shader facts: profile %s now carries the merged table (%d rows), which is what the translator reads'
                        % (gdir.name, len(fact_rows(facts_split))))
    if a.stop_after == 'shaders': return say('stopped after the shader split')

    if route != 'nvn':
        # The b- directory is what the app looks for (main.m points DM_MSL_DIR at <App>/msl, and the chain version names one
        # directory inside it). Beyond the stamp it is empty here: a GL package's shaders are under msl/gl, which is where
        # the GL layer reads them from, and are copied there after assembly.
        msl_out = work / 'build' / 'ios' / 'none' / 'b-000000000000'
        msl_out.mkdir(parents=True, exist_ok=True)
        (msl_out / 'inputs.tsv').write_text('game\t%s\n' % tid, encoding='utf-8')
        if gl_out is None and vk_out is None:
            say('no shader library: the app will run without drawing until its shaders are captured')
    else:
        # The chain version is not resolved here on purpose. dmsh_chain.py's command-line modes fingerprint the toolchain,
        # which needs the translator's .dll files -- absent from a single-file build -- and the version it writes into a
        # profile header is a compile-time constant that only the Xcode route needs. The shell reads the version back from
        # the directory name at run time, so dmsh_build.py is left to compute it and we take whatever directory it makes.
        iosout = work / 'build' / 'ios' / 'dmsh_b'          # dmsh_build.py creates b-<chain version> inside it
        cands = ([tools_dir / n for n in ('dmsh_xlate.exe', 'dmsh_xlate')] if tools_dir else []) + \
                [GFX / 'dmsh_xlate_bin' / n for n in ('dmsh_xlate.exe', 'dmsh_xlate')]
        xlate = next((c for c in cands if c.is_file()), None)
        if not xlate: die('no shader translator: pass --tools <folder> with dmsh_xlate, or build it with dotnet publish')
        step_translate(pkg_dir, work, xlate, iosout, tid, a.force)
        libs = sorted((p for p in iosout.glob('b-*') if p.is_dir()), key=lambda d: (d.stat().st_mtime, d.name)) \
            if iosout.is_dir() else []
        if not libs: die('no shader library directory under %s' % iosout)
        # Which directory to ship is not a matter of name order. The chain version moves whenever one of the files it
        # hashes changes (our own dmsh_build.py patch did, between two runs tonight), so a work folder that survived
        # earlier runs holds one b-<V> per run, and an earlier one can still be sitting there with a full set while the
        # current run's holds the 136 shaders it got to before it died. Only a full build stamps its directory
        # (inputs.tsv, written last), and c1_full.tsv / cb_extent.tsv name that stamp in their header, so take the
        # newest stamped one; the app rejects both tables outright when the stamp is missing.
        stamped = [d for d in libs if (d / 'inputs.tsv').is_file()]
        msl_out = (stamped or libs)[-1]
        if len(libs) > 1:
            warn('%s holds %d shader library directories (%s): shipping %s'
                 % (iosout, len(libs), ', '.join(d.name for d in libs), msl_out.name))
        # A short library is the failure that only shows up on the phone (every missing shader prints [RED] and draws
        # nothing, with the sound still playing), so check it against the shader map the app will get before packing.
        want = set()
        smap = gdir / 'shader_map.tsv'
        if not smap.is_file(): smap = work / 'shader' / 'tables' / 'shader_map.tsv'
        if smap.is_file():
            for line in smap.read_text(errors='replace').splitlines():
                c = line.split('\t')
                # the file opens with a column-title row ("package index stage dm_ctl_sha16 source") that is not a
                # comment, so the key has to look like one: 16 hex digits
                if len(c) >= 4 and re.fullmatch(r'[0-9a-f]{16}', c[3]): want.add(c[3])
        have = len(list(msl_out.glob('*_b.metallib')))
        if want and have < len(want):
            die('%s has %d shader libraries but shader_map.tsv lists %d shaders: %d would draw nothing on the phone. '
                'Delete %s and pack again (a directory left by an interrupted run is the usual cause)'
                % (msl_out.name, have, len(want), len(want) - have, work))
        say('shader libraries: %s (%d of %d shaders)' % (msl_out.name, have, len(want) or have))
    if a.stop_after == 'guest': return say('stopped before assembly')

    stage = work / 'stage'
    A = step_assemble(shell, stage, game, patched, mods, gdir, rows,
                      one(rows, 'name', tid), builds[pkg], tid, msl_out, work, aoc_dir)
    if vk_out is not None:
        dst = A / 'msl' / 'vk'
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(vk_out, dst)
        say('Vulkan shader table and %d sources → msl/vk (the phone compiles them at first launch)'
            % len(list((dst / 'src').glob('*.metal'))))
    if gl_out is not None:
        # <App>/msl/gl: hle_gl.m reads gl_shaders.tsv and gl/src from DM_MSL_DIR itself, not from the chain version's directory.
        dst = A / 'msl' / 'gl'
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(gl_out, dst)
        say('GL shader table and %d sources → msl/gl (the phone compiles them at first launch)'
            % len(list((dst / 'src').glob('*.metal'))))
    # After assembly the app tree is complete and inspectable, and packing it is the only thing left: --stop-after assemble
    # is how someone looks at what the packer produced without writing a 4 GB IPA first (the work folder stays, the tidy
    # below is only reached by a run that packs).
    if a.stop_after == 'assemble':
        return say('stopped before packing: the app tree is %s' % stage)
    step_pack(A, out_ipa, rows, work)
    say('done: %s (%.0f MB)' % (out_ipa, out_ipa.stat().st_size / 1048576))
    say('next: sign it with a sideloading tool (SideStore / Sideloadly / LiveContainer) and install it on the phone')
    say('this IPA contains your game files: for your own use only, do not distribute it')

    # The IPA is written and closed, so the working tree is spent: everything in it was either derived from the game files
    # (a rerun redoes it) or copied into the package. Removing it is the default because it is several times the size of
    # the game -- a 4 GB game leaves ~16 GB behind -- and people notice that, not the 90 seconds it saves.
    if a.keep_work:
        say('working folder kept: %s (remove it with --clean "%s")' % (work, folder))
    else:
        say('removing the temporary files (--keep-work keeps them for a faster rerun)')
        freed = tidy(work)
        say('removed %.1f MB; the logs stay in %s' % (freed / 1048576, work / 'logs'))


if __name__ == '__main__':
    main()
