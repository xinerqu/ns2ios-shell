#!/usr/bin/env python3
"""Lay a packed game out for the app that holds several games (the "shell library" layout).

    python scripts/shell_game_pack.py <pack work dir> <out dir> [--ipa <out.ipa>] [--shell <shell.zip>] [--icon <png>]

<out dir> gets:

    DmGame<title ID>.framework/      the game: its modules (guest_dylib.py), its tables in dm/, game.json, icon.png
    ns2ios/games/<title ID>/romfs.img, msl/, aoc/    the big files, copied to the phone's Files app

With --ipa it also puts the framework into a copy of the shell package and writes that .ipa (unsigned: the sideloading tool
signs it, as it signs every package we ship).

Stage 1 of the several-games work: it reads a work folder scripts/nxn_pack.py already made (stage/Payload/<App>.app has every
piece) so the device test needs no repack of a 5 GB game. The same layout is what nxn_pack.py --framework will write.
"""
import argparse, json, os, plistlib, shutil, sys, zipfile
from pathlib import Path

NXN = Path(__file__).resolve().parent.parent
TOOLS = NXN / 'dm' / 'tools'
sys.path.insert(0, str(TOOLS))
import guest_dylib, nacp                                                    # noqa: E402

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('work', help='the pack work folder (…/<title ID>_pack), or one of its stage/Payload/<App>.app folders')
    ap.add_argument('out', help='where the framework and the ns2ios folder are written')
    ap.add_argument('--ipa', help='also write this .ipa: the shell plus every DmGame*.framework in the output folder')
    ap.add_argument('--shell', help='the shell zip (…/shell-<…>.zip); default: the one beside pack.cmd')
    ap.add_argument('--icon', help='the library icon (60x60@3x from the package is used otherwise)')
    ap.add_argument('--cover', help='a 16:9 banner for the library (cover.jpg/png beside the game files is used otherwise)')
    a = ap.parse_args()

    work, out = Path(a.work).resolve(), Path(a.out).resolve()
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
    fw, n = guest_dylib.write(str(patched), str(out), mods, bundle_id='org.nxnative.game.%s' % tid.lower(), name=fwn)
    say('framework %s (%d MB)' % (Path(fw).name, n >> 20))

    fwdir = Path(fw)
    fdm = fwdir / 'dm'; fdm.mkdir(exist_ok=True)
    for src in sorted((app / 'dm').iterdir()):
        if src.is_dir() or src.name == 'placeholder.tsv': continue      # font/ is the app's, not the game's
        shutil.copyfile(src, fdm / src.name)
    say('tables: %d files' % len(list(fdm.iterdir())))

    icon = Path(a.icon) if a.icon else app / 'AppIcon60x60@3x.png'
    if icon.is_file(): shutil.copyfile(icon, fwdir / 'icon.png')
    # A 16:9 banner for the library, when one is there: cover.jpg/png beside the game files (or --cover). Without one the library draws
    # the icon on a tile, so a game never looks broken -- the library checks for this file by name.
    if a.cover:
        cover = Path(a.cover)
        if not cover.is_file(): die('no such cover: %s' % cover)
    else:
        # beside the game's files first (that is where the icon and the RomFS image are), then the work folder
        cover = next((q for q in (app / 'cover.jpg', app / 'cover.png', work / 'cover.jpg', work / 'cover.png') if q.is_file()), None)
    if cover:
        # the library looks for exactly cover.jpg or cover.png
        shutil.copyfile(cover, fwdir / ('cover.png' if cover.suffix.lower() == '.png' else 'cover.jpg'))
        say('cover: %s' % cover.name)

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
        # recursively: a GL or Vulkan game keeps gl/ and vk/ subfolders here, and hle_gl.m/hle_vk.m read their tables from there
        for src in sorted((app / 'msl').rglob('*')):
            if src.is_file():
                dst = data / 'msl' / src.relative_to(app / 'msl')
                how.append('msl/%s %s' % (src.relative_to(app / 'msl'), link_or_copy(src, dst)))
    if (app / 'aoc').is_dir():
        (data / 'aoc').mkdir(exist_ok=True)
        for src in sorted((app / 'aoc').iterdir()): how.append('aoc/%s %s' % (src.name, link_or_copy(src, data / 'aoc' / src.name)))
    # pso/ (the offline pipeline archive), html/ (offline pages) and jit/ (an nn::jit game's image): the host reads all three beside the
    # RomFS or from the bundle, so they belong to the game and travel with its data. jit/ is not optional: loader.c dies without it.
    for extra in ('pso', 'html', 'jit'):
        if not (app / extra).is_dir(): continue
        (data / extra).mkdir(exist_ok=True)
        how.append(extra + '/ %d entries' % len([s for s in (app / extra).rglob('*') if s.is_file()]))
        for src in sorted((app / extra).rglob('*')):
            if src.is_file(): link_or_copy(src, data / extra / src.relative_to(app / extra))
    say('data folder %s: %s' % (data, '; '.join(how) or 'nothing'))

    # ---- the test package: the shell with this one framework inside ----
    if a.ipa:
        shell = Path(a.shell) if a.shell else next(iter(sorted(NXN.parent.glob('ns2ios-*.zip')) or sorted(NXN.parent.glob('shell-*.zip'))), None)
        if not shell or not Path(shell).is_file(): die('no shell zip (pass --shell)')
        stem = fwdir.name
        with zipfile.ZipFile(shell) as z:
            apps = sorted({n_.split('/')[1] for n_ in z.namelist() if n_.startswith('Payload/') and n_.split('/')[1].endswith('.app')})
            if len(apps) != 1: die('the shell holds %d apps, expected 1: %s' % (len(apps), apps))
            appname = apps[0]
            with zipfile.ZipFile(a.ipa, 'w', zipfile.ZIP_DEFLATED) as o:
                for it in z.infolist():
                    o.writestr(it, z.read(it.filename))
                # every framework in the output folder, not only this run's: one .ipa that holds all the games packed so far (add a
                # game = pack it into the same folder, then this step again; the phone reinstalls the app, its data and saves stay)
                have = sorted(p for p in out.glob('DmGame*.framework') if p.is_dir())
                for fw2 in have:
                    for src in sorted(fw2.rglob('*')):
                        if src.is_file():
                            o.write(src, 'Payload/%s/Frameworks/%s/%s' % (appname, fw2.name, src.relative_to(fw2).as_posix()))
        say('package %s (%s + %d game(s): %s)' % (a.ipa, Path(shell).name, len(have), ', '.join(p.name for p in have)))

    say('done: copy %s/ns2ios to the app\'s Documents (Files app), then install the package' % out)


if __name__ == '__main__':
    main()
