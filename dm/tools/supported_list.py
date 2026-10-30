#!/usr/bin/env python3
"""The supported-games list, generated from the game profiles.

One page for the person who has just downloaded the shell and wants to know whether their game is in it: name, title
ID, and the version the game was verified on. Nothing else -- the numbers a packer cares about (memory layout, module
list, shader tables) mean nothing to a player and invite the wrong conclusion.

    python3 dm/tools/supported_list.py                  # writes docs/SUPPORTED.md
    python3 dm/tools/supported_list.py --tsv dist/x.tsv  # also a machine-readable copy for a release asset
    python3 dm/tools/supported_list.py --check           # exit 1 when the file on disk is out of date

Output is byte-stable: no timestamps, no version numbers, LF, UTF-8 without BOM, sorted by name. That is what lets
--check work in CI.
"""
import argparse
import sys
from pathlib import Path

PLACEHOLDER = '0000000000000000'   # the neutral profile the shell itself is compiled against, not a game


def rows_of(profile):
    """game.tsv: key<TAB>value, one `build` row per version."""
    rows = {}
    for line in profile.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#'): continue
        k, _, v = line.partition('\t')
        rows.setdefault(k.strip(), []).append(v)
    return rows


def one(rows, key, default=''):
    v = rows.get(key)
    return v[0].strip() if v else default


def builds(rows):
    """[(version, display)] in file order; a build row is `build <version> <display> <module>=<id>...`."""
    out = []
    for line in rows.get('build', []):
        f = line.split('\t')
        if len(f) >= 2: out.append((f[0].strip(), f[1].strip() if len(f) > 1 else ''))
    return out


def read_games(games_dir):
    """Every profile that is a game, sorted the way the page shows them."""
    games = []
    for d in sorted(games_dir.iterdir()):
        f = d / 'game.tsv'
        if not f.is_file() or d.name == PLACEHOLDER: continue
        rows = rows_of(f)
        versions = builds(rows)
        package = one(rows, 'package')
        verified = dict(versions).get(package, '') or (versions[-1][1] if versions else '')
        # One column answers "is my version covered": the verified one, plus the others only when there are several.
        shown = verified or '—'
        others = [v for _, v in versions if v and v != verified]
        if others: shown += '（也可 %s）' % ' / '.join(others)
        features = []
        if one(rows, 'fps_target') == '60': features.append('60 fps')
        if one(rows, 'op_mode').lower() == 'handheld': features.append('handheld')
        # A game whose shaders sit inside an Oodle- or LZ5-compressed container cannot be packed at all yet: only the game's own
        # decoder unpacks those archives (shader_split.py oodle(), dm/host/hle_oodle.c), and that decoder is arm64 code the Mac
        # host runs natively. Said here rather than inferred from the profile's prose, because a game can mention Oodle for other
        # assets and still pack fine (Shin Megami Tensei V keeps its shader records inline in Oodle-compressed pak blocks).
        if one(rows, 'needs_mac_shaders'): features.append('暂不支持')
        # A Vulkan profile that carries vk_capture.tsv is one whose shaders exist only while the game runs, so the packer
        # needs one capture from the phone before it can build a playable package -- still supported, and no Mac is
        # involved. gl_capture.tsv is not a signal: Balatro carries one as the yardstick for the LÖVE prebuilt route,
        # which produces the same shaders offline and needs no capture at all.
        if (d / 'vk_capture.tsv').is_file(): features.append('需抓一次')
        games.append({
            'tid': d.name,
            'name': one(rows, 'name') or d.name,
            'version': shown,
            'features': ' · '.join(features),
        })
    games.sort(key=lambda g: (g['name'].lower(), g['tid']))
    return games


def render_md(games):
    b = ['# Supported games / 支持的游戏', '',
         'Generated from `dm/games/*/game.tsv` by `dm/tools/supported_list.py` — do not edit by hand.  ',
         '本页由 `dm/tools/supported_list.py` 从 `dm/games/*/game.tsv` 生成，请勿手动修改。', '',
         '**%d 款游戏。** 文件名不重要——打包器从包内容识别游戏，在下表里对上 title ID 就够了。' % len(games), '',
         '- **IPA 体积** ≈ 你的 NSP/XCI 体积 + 约 16 MB（壳本身）。游戏文件在包里是原样存放的，不再压缩。',
         '- **首次启动**要编译着色器：打包时只把 Switch 的着色器翻译成 Metal 源码，编译在手机上做（之后有缓存）。'
         '几秒到大作几分钟，启动时有进度页。',
         '- **内存**：iOS 给不带 entitlement 的应用约 3.4 GB；本项目的壳在构建时已请求提高上限，'
         '侧载工具保留该权限后可用到 6–8 GB。',
         '- 表内是本仓库带参数、并且打包器跟随其快照验证过的游戏。**不在表内**的游戏，打包器会尝试从上游取它的参数并尽力打包（未经本仓库验证，可能跑不起来），不会产出半成品 IPA。',
         '- **标着「暂不支持」的游戏**：着色器压在 Oodle / LZ5 容器里（Trinity 包、PlatinumGames PKZ、旧式 Unreal pak），'
         '只有游戏自己的解码器能解，而那段代码是 Switch 的 arm64 机器码，只能在 Apple Silicon（M 系列）的 Mac 上跑。'
         '**当前版本的打包器没有这条通路**，所以这几款在 Windows、Linux、macOS 上都会失败——它会在拆着色器那步停下并说明，'
         '不会产出装上去却画不出东西的半成品。详见 README 的「现在打不了的游戏」。'
         + ('' if any('暂不支持' in g['features'] for g in games) else '（当前没有。）'),
         '- **标着「需抓一次」的游戏**：着色器只在游戏跑起来时才生成，没有文件可拆。先用手机装一个不带画面的包抓一次，'
         '再打第二遍——**不需要 Mac**，Windows + 手机就够。步骤见 `docs/CAPTURE.zh-CN.md`。'
         + ('' if any('需抓一次' in g['features'] for g in games) else '（当前没有。）'), '',
         '| 游戏 | Title ID | 已验证版本 | 特性 |',
         '|---|---|---|---|']
    for g in games:
        b.append('| %s | `%s` | %s | %s |' % (g['name'], g['tid'], g['version'], g['features']))
    b.append('')
    return '\n'.join(b)


def render_tsv(games):
    b = ['title_id\tname\tverified_version\tfeatures']
    for g in games:
        b.append('%s\t%s\t%s\t%s' % (g['tid'], g['name'], g['version'], g['features']))
    return '\n'.join(b) + '\n'


def write_or_check(text, path, check):
    """True when the file already holds exactly this text (or when it was written)."""
    old = path.read_text(encoding='utf-8') if path.is_file() else None
    if check:
        return old == text
    if old != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
    return True


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description='Generate the supported-games list from dm/games/*/game.tsv.')
    ap.add_argument('--games', default=str(here.parent / 'games'), help='profile folder (default dm/games)')
    ap.add_argument('--md', default=str(here.parent.parent / 'docs' / 'SUPPORTED.md'), help='the page to write')
    ap.add_argument('--tsv', default='', help='also write a machine-readable copy here (e.g. dist/<version>-supported.tsv)')
    ap.add_argument('--check', action='store_true', help='do not write: exit 1 when a file is out of date')
    ap.add_argument('--expect-min', type=int, default=0, help='fail when fewer games than this are found')
    a = ap.parse_args()

    games_dir = Path(a.games)
    if not games_dir.is_dir(): sys.exit('[x] no profile folder at %s' % games_dir)
    games = read_games(games_dir)
    if not games: sys.exit('[x] no game profiles under %s' % games_dir)
    if len(games) < a.expect_min:
        sys.exit('[x] only %d games found, expected at least %d: the profile folder looks truncated'
                 % (len(games), a.expect_min))
    tids = [g['tid'] for g in games]
    if len(set(tids)) != len(tids): sys.exit('[x] duplicate title IDs in %s' % games_dir)

    stale = []
    if not write_or_check(render_md(games), Path(a.md), a.check): stale.append(a.md)
    if a.tsv and not write_or_check(render_tsv(games), Path(a.tsv), a.check): stale.append(a.tsv)
    if stale:
        for s in stale: print('[x] out of date: %s' % s)
        print('    run: python3 dm/tools/supported_list.py --tsv dist/<version>-supported.tsv')
        return 1
    print('%d games%s' % (len(games), ' (up to date)' if a.check else ' -> %s' % a.md))
    return 0


if __name__ == '__main__':
    sys.exit(main())
