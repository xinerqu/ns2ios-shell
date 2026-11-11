#!/usr/bin/env python3
"""Lay a packed game out for the app that holds several games (the "shell library" layout).

    python scripts/shell_game_pack.py <pack work dir> <out dir> [--ipa <out.ipa>] [--shell <shell.zip>] [--icon <png>]
    python scripts/shell_game_pack.py --ipa-only --library <dir> --shell <shell.zip> --ipa <out.ipa>
        only rebuild the package: every framework already in the library, the shell as it is now; no game
        folder and no work folder are touched, so it takes seconds

<out dir> gets:

    ns2ios/games/<title ID>/romfs.img, msl/, aoc/    the big files, copied to the phone's Files app

--library <dir> is where the games themselves live (DmGame<title ID>.framework: its modules from guest_dylib.py, its tables
in dm/, game.json, icon.png). It defaults to <out dir>. One library holds every game, so a game stays in the package even
when the next one is packed into a different output folder.

With --ipa it also puts every framework in the library into a copy of the shell package and writes that .ipa (unsigned: the
sideloading tool signs it, as it signs every package we ship).

Stage 1 of the several-games work: it reads a work folder scripts/nxn_pack.py already made (stage/Payload/<App>.app has every
piece) so the device test needs no repack of a 5 GB game. The same layout is what nxn_pack.py --framework will write.
"""
import argparse, json, os, plistlib, shutil, sys, zipfile
from pathlib import Path

NXN = Path(__file__).resolve().parent.parent
TOOLS = NXN / 'dm' / 'tools'
sys.path.insert(0, str(TOOLS))
import guest_dylib, nacp, romfs_image                                # noqa: E402

KEEP_TABLES = ('shader_map.tsv', 'c1_full.tsv', 'cb_extent.tsv', 'pso_manifest.tsv', 'shader_pairs.tsv', 'pso_pairs.tsv',
               'env.txt', 'launcher.json')


def say(s): print('[.] ' + s, flush=True)


def die(s): sys.exit('[x] ' + s)


def link_or_copy(src, dst):
    """A hard link where the volume allows it (the test run must not copy 7 GB), a copy otherwise."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists(): dst.unlink()
    try:
        os.link(src, dst); return 'link'
    except OSError:
        shutil.copyfile(src, dst); return 'copy'


def nacp_names(path):
    """Every language's title, not just the first one (nacp.name returns the first non-empty): the library shows a game by the
    system language, as the Switch's own home screen does."""
    import struct, zlib
    p = Path(path)
    if not p.is_file(): return {}
    b = p.read_bytes(); t = b[:0x3000]
    if len(b) >= 2:
        try:
            d = zlib.decompressobj(-15); z = d.decompress(b[2:2 + struct.unpack_from('<H', b)[0]], 0x6001)
            if d.eof and len(z) == 0x6000: t = z
        except zlib.error:
            pass
    out = {}
    for i, (code, _) in enumerate(nacp.LANGUAGES):
        s = t[i * 0x300:i * 0x300 + 0x200].split(b'\0', 1)[0]
        if not s: continue
        try: s = s.decode('utf-8')
        except UnicodeDecodeError: continue
        if s.isprintable(): out[code] = s
    return out


def chain_of(app):
    """The NVN shader library's translation chain, or '' when this game has none (GL/Vulkan games carry gl/ or vk/ tables and no
    b-<chain>.img): an all-zero chain from a placeholder header must not read as "packed for another shell" in the library."""
    for c in (app / 'msl').glob('b-*'):
        v = c.name[2:-4] if c.name.endswith('.img') else c.name[2:]
        return '' if set(v) <= {'0'} else v
    for d in (app / 'dm').glob('c1_full.tsv'):
        head = d.open(encoding='utf-8', errors='replace').readline()
        i = head.find('b-')
        if i >= 0:
            v = head[i + 2:].split()[0]
            return '' if set(v) <= {'0'} else v
    return ''


def write_ipa(shell, lib, ipa):
    """The shell package plus every framework in the library. Adding a game = pack it into the same library and run this
    again; the phone reinstalls the app and its data and saves stay. --ipa-only comes straight here."""
    with zipfile.ZipFile(shell) as z:
        apps = sorted({n_.split('/')[1] for n_ in z.namelist() if n_.startswith('Payload/') and n_.split('/')[1].endswith('.app')})
        if len(apps) != 1: die('the shell holds %d apps, expected 1: %s' % (len(apps), apps))
        appname = apps[0]
        have = sorted(p for p in lib.glob('DmGame*.framework') if p.is_dir())
        if not have: die('the library holds no DmGame*.framework: %s' % lib)
        with zipfile.ZipFile(ipa, 'w', zipfile.ZIP_DEFLATED) as o:
            for it in z.infolist():
                o.writestr(it, z.read(it.filename))
            for fw in have:
                for src in sorted(fw.rglob('*')):
                    if src.is_file():
                        o.write(src, 'Payload/%s/Frameworks/%s/%s' % (appname, fw.name, src.relative_to(fw).as_posix()))
    say('package %s (%s + %d game(s): %s)' % (ipa, shell.name, len(have), ', '.join(p.name for p in have)))


def write_data_identity(fwdir, img, tid, version, build):
    """The framework's romfs.id: the identity of the image the game data was packed from (dm/host/romfs_identity.h).
    size and index decide a match, title/version/build only name the image in the app's message. A folder has none (upstream
    writes none for one), and a stale file has to go: the same library packs the game again with the other form sometimes.
    Returns the identity line, '' for a folder; raises ValueError for an image that cannot be identified."""
    fwdir, img = Path(fwdir), Path(img)
    if not img.is_file():
        (fwdir / 'romfs.id').unlink(missing_ok=True)
        return ''
    line = romfs_image.identity(str(img), tid, version, build)
    (fwdir / 'romfs.id').write_text(line + '\n', encoding='ascii', newline='\n')
    return line


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('work', nargs='?', default='', help='the pack work folder (…/<title ID>_pack), or one of its stage/Payload/<App>.app folders')
    ap.add_argument('out', nargs='?', default='', help='where the ns2ios folder (the game data) is written')
    ap.add_argument('--ipa-only', action='store_true',
                    help='only rebuild the package from --library: no game folder and no work folder are touched')
    ap.add_argument('--library', default='', help='where the frameworks live; one folder for every game (default: the out dir)')
    ap.add_argument('--ipa', help='also write this .ipa: the shell plus every DmGame*.framework in the library')
    ap.add_argument('--shell', help='the shell zip (…/shell-<…>.zip); default: the one beside pack.cmd')
    ap.add_argument('--icon', help='the library icon (60x60@3x from the package is used otherwise)')
    a = ap.parse_args()

    if a.ipa_only:
        # 只重打安装包：库里的 framework + 现在的壳。游戏目录、工作目录一律不碰。
        if not a.ipa: die('--ipa-only needs --ipa <out.ipa>')
        if not a.library: die('--ipa-only needs --library <dir>')
        if not a.shell: die('--ipa-only needs --shell <shell.zip>')
        lib = Path(a.library).resolve()
        if not lib.is_dir(): die('no library folder: %s' % lib)
        write_ipa(Path(a.shell).resolve(), lib, Path(a.ipa).resolve())
        return

    if not a.work or not a.out: die('pass <pack work dir> <out dir>, or --ipa-only')
    work, out = Path(a.work).resolve(), Path(a.out).resolve()
    lib = Path(a.library).resolve() if a.library else out
    lib.mkdir(parents=True, exist_ok=True)
    app = work if work.name.endswith('.app') else next((p for p in sorted(work.glob('stage/Payload/*.app'))), None)
    if app is None: die('no stage/Payload/<App>.app under %s' % work)
    patched = work / 'build' / 'patched'
    if not (patched / 'main.patched.bin').is_file(): die('no build/patched/main.patched.bin under %s' % work)

    tid = None
    for part in (app.parent.parent.parent.name, app.name, work.name):
        i = part.find('_pack'); s = part[:i] if i >= 0 else part
        if len(s) == 16 and all(c in '0123456789ABCDEF' for c in s.upper()): tid = s.upper(); break
    if not tid: die('cannot tell the title ID from %s (expected <16 hex>_pack)' % work)
    say('title ID %s' % tid)

    mods = None
    prof = NXN / 'dm' / 'games' / tid / 'game.tsv'
    if prof.is_file():
        for line in prof.read_text(encoding='utf-8').splitlines():
            c = line.split('\t')
            if c[0] == 'modules': mods = c[1].split(); break
    if not mods: die('no modules row in %s' % prof)
    say('modules %s' % ' '.join(mods))

    # ---- the framework: modules + tables + what a library needs to draw the game ----
    fwn = 'DmGame%s' % tid
    fw, n = guest_dylib.write(str(patched), str(lib), mods, bundle_id='org.nxnative.game.%s' % tid.lower(), name=fwn)
    say('framework %s (%d MB)' % (Path(fw).name, n >> 20))

    fwdir = Path(fw)
    fdm = fwdir / 'dm'; fdm.mkdir(exist_ok=True)
    for src in sorted((app / 'dm').iterdir()):
        if src.is_dir() or src.name == 'placeholder.tsv': continue      # font/ is the app's, not the game's
        shutil.copyfile(src, fdm / src.name)
    say('tables: %d files' % len(list(fdm.iterdir())))

    # The card in the library IS the game's own icon (NACP -> AppIcon -> here), as on the console's home screen; there is no
    # separate cover picture (坤哥 2026-11-11: cover.jpg was never used -- dropped along with --cover).
    icon = Path(a.icon) if a.icon else app / 'AppIcon60x60@3x.png'
    if icon.is_file(): shutil.copyfile(icon, fwdir / 'icon.png')

    nacps = sorted(work.glob('extract/*/control.nacp')) or sorted(work.glob('extract/**/control.nacp'))
    names = nacp_names(nacps[-1]) if nacps else {}
    info = plistlib.loads((app / 'Info.plist').read_bytes())
    game = {'title_id': tid, 'name': nacp.name(nacps[-1]) if nacps else info.get('CFBundleDisplayName', tid),
            'names': names, 'display_version': info.get('CFBundleShortVersionString', ''), 'chain': chain_of(app),
            'modules': mods, 'layout': 1}
    (fwdir / 'game.json').write_text(json.dumps(game, ensure_ascii=False, indent=1) + '\n', encoding='utf-8', newline='\n')
    say('game.json: %d names, chain %s, version %s' % (len(names), game['chain'] or '?', game['display_version'] or '?'))

    # ---- the big files: Documents/ns2ios/games/<id>/ ----
    data = out / 'ns2ios' / 'games' / tid
    data.mkdir(parents=True, exist_ok=True)
    how = []
    if (app / 'romfs.img').is_file(): how.append('romfs.img %s' % link_or_copy(app / 'romfs.img', data / 'romfs.img'))
    elif (app / 'romfs').is_dir():
        (data / 'romfs').mkdir(exist_ok=True)
        for src in (app / 'romfs').iterdir(): link_or_copy(src, data / 'romfs' / src.name)
        how.append('romfs/ copied')
    else: die('the package holds neither romfs.img nor romfs/')
    if (app / 'msl').is_dir():
        # One library per folder: the shell takes its translation chain from whichever b-* it lists first (nvn_mtl.m
        # shell_chain), so a library of an older chain left beside this one makes the package a coin toss -- and when the
        # older one is the source form every shader is compiled on the phone again (2026-11-08: a 14.5 MB source image
        # beside the 20.2 MB compiled one, both named b-<chain>.img).
        keep = {p.name for p in (app / 'msl').glob('b-*')}
        if (data / 'msl').is_dir():
            for old in sorted((data / 'msl').glob('b-*')):
                if old.name in keep: continue
                if old.is_dir(): shutil.rmtree(old)
                else: old.unlink()
                say('dropped the superseded %s' % old.name)
        # recursively: a GL or Vulkan game keeps gl/ and vk/ subfolders here, and hle_gl.m/hle_vk.m read their tables from there
        n = 0
        for src in sorted((app / 'msl').rglob('*')):
            if src.is_file():
                link_or_copy(src, data / 'msl' / src.relative_to(app / 'msl'))
                n += 1
        how.append('msl/ %d file(s)' % n)
    if (app / 'aoc').is_dir():
        (data / 'aoc').mkdir(exist_ok=True)
        n = 0
        for src in sorted((app / 'aoc').iterdir()):
            link_or_copy(src, data / 'aoc' / src.name)
            n += 1
        how.append('aoc/ %d file(s)' % n)
    # pso/ (the offline pipeline archive), html/ (offline pages) and jit/ (an nn::jit game's image): the host reads all three beside the
    # RomFS or from the bundle, so they belong to the game and travel with its data. jit/ is not optional: loader.c dies without it.
    for extra in ('pso', 'html', 'jit'):
        if not (app / extra).is_dir(): continue
        (data / extra).mkdir(exist_ok=True)
        how.append(extra + '/ %d entries' % len([s for s in (app / extra).rglob('*') if s.is_file()]))
        for src in sorted((app / extra).rglob('*')):
            if src.is_file(): link_or_copy(src, data / extra / src.relative_to(app / extra))
    say('data folder %s: %s' % (data, ', '.join(how) or 'nothing'))

    # ---- 这份数据是不是这个 framework 打出来的那一版（上游的 romfs.id，dm/host/romfs_identity.h）----
    # 库模式下数据是玩家自己拷进「文件」的：拿错版本以前只表现为进游戏后莫名崩或花屏。framework 里带上镜像的身份，
    # 壳在启动前比一次（main.m shell_game_dm_for），对不上就说清楚要哪一版，不硬着头皮进。
    try:
        ident = write_data_identity(fwdir, data / 'romfs.img', tid, game['display_version'],
                                    info.get('CFBundleVersion', ''))
    except ValueError as e:
        die('the game data has no usable identity: %s' % e)
    say('game data identity: %s' % (ident or 'none (a RomFS folder)'))

    if a.ipa:
        shell = Path(a.shell) if a.shell else next(iter(sorted(NXN.parent.glob('ns2ios-*.zip')) or
                                                             sorted(NXN.parent.glob('shell-*.zip'))), None)
        if not shell or not Path(shell).is_file(): die('no shell zip (pass --shell)')
        write_ipa(Path(shell).resolve(), lib, Path(a.ipa).resolve())

    say('done: %s' % (Path(a.ipa).name if a.ipa else 'the game is in the library'))


if __name__ == '__main__':
    main()
