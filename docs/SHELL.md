# The shell split

The normal route builds everything on one Mac: `scripts/build_ipa_from_nsp.py` rewrites the game program, translates its
shaders, compiles them with Apple's `metal` compiler, and lets `xcodebuild` link the whole app. A user without a Mac cannot do
any of that.

This route splits it in two:

| | Where | What |
|---|---|---|
| **1. Shell** (once) | Mac or the `macOS` CI runner | `dm/ios/tools/shell_build.sh` compiles the app with **no game content at all** and publishes a few MB of build input |
| **2. Pack** (per game) | Windows, Linux or macOS | `scripts/nxn_pack.py` takes the user's own NSP + `prod.keys`, rewrites the program, translates the shaders, writes them into the shell and produces an IPA |

Nothing from any game ever reaches this repository or CI. The shell is generic across games, which is the hard part and the point.

## Why one shell serves any game

Upstream nxnative bakes game-specific data into the executable, so a shell would only work for the game it was built for. Shell
mode (`-DDM_SHELL`) moves each of those to run time:

| Baked in normally | In shell mode |
|---|---|
| `gen_embed.py` compiles the rewritten module images into `__TEXT` / `__DM*W` sections (`host/loader.c`) | Taken from `Frameworks/Guest.framework/Guest`, a segments-only dylib written by `dm/tools/guest_dylib.py` with nothing but the Python standard library — byte-identical output on any OS |
| `DM_GAME_MODULES` from the game profile (`host/main.c:94`, only used when argv gives no names) | Read back from the Guest library's `__dm<name>x0` section names (`dm_shell_modules`), appended to `argv` in `main.m` |
| `DMSH_CHAIN` from the profile's `dmsh_chain.h` | Resolved from the name of the one library directory under `msl/` (`b-<version>`) — the chain version hashes the game's shader tables, so it differs per game |
| `.metallib` compiled by `xcrun metal` | Files that do not start with `MTLB` are treated as MSL source and compiled on the device, then cached by the system |
| `romfs_marker`-based RomFS lookup | Any existing, non-empty RomFS directory is accepted |
| The profile's memory layout (`mem_used`, `app_pool`, `mem_alias`, …) | Read from the environment at startup; the packer writes the game's real values into the app's `dm/env.txt` |
| The rest of the profile switches the host read as compiled-in constants (`tex_flush_upload`, `tex_dirty_upload`, `render_cond`, `alloc_big_align`, `tex_dyn_slots`, `perf_mode`, `mem_map_limit`, `game_args`, `pad_layout`) | Each host site asks the environment first (`DM_TEX_FLUSH_UPLOAD`, `DM_RENDER_COND`, `DM_PERF_BOOST`, `DM_GAME_ARGS`, …) and falls back to the constant, which the placeholder profile leaves off (it sets `pad_layout full`, so that is the shell's own default); the packer writes the value in the form that site parses — `atoi`, so the profile's `on` and `boost` arrive as `1` |
| — | `dm/ios/Sources/dm_shader_prep.m` compiles all source-form shaders once on first launch with a progress page |

The rewriter and shader tables needed no work: in nxnative they are already ordinary resources (`Gen/dm`, a resource folder in
`ios/project.yml`) read at run time, not arrays compiled into code. That is why this shell needs **no game files** to build,
unlike d2r-mobile's `shell_build.sh`.

The memory layout was not obvious and cost a device round trip: with the placeholder's default `mem_used` (64 MB) instead of
Hades II's 108 MB, the game asks for `pool - used` of heap and overflows the pool by 44 MB, aborting in `SetHeapSize`.

## Building

Locally on a Mac with Xcode 26:

```
bash dm/ios/tools/shell_build.sh
```

Or on CI: run the **Build shell** workflow. Manual runs use this fork's tree; the daily schedule uses the latest upstream commit
with this fork's files copied over it (`.shell-overlay.txt`). Both publish a release. The build ends with checks that the app
carries no `romfs/`, `msl/`, `aoc/`, no module image sections and no file over 8 MB outside `dm/font`.

The pipeline is `tools → shell → smoke → release`, and the smoke pack runs on every build: it pulls a small game's NSP and
`prod.keys` from a private repository, packs it with the shell and tools that were just built, and inspects the result
(executable bits, shader library count, RomFS, the memory layout in `env.txt`, the shell's gate declaration, the display name).
That is the only step that can catch "upstream changed an interface and the shell no longer agrees with the packer", so it is
not skipped.

The two kinds of failure are handled differently:

| Situation | What happens |
|---|---|
| The test data cannot be fetched (no token, expired token, asset gone) | **publishes anyway**, with "Not smoke-tested" at the top of the release. That says nothing about the shell, and a good shell should not be withheld for it |
| The pack fails, or the produced package fails the checks | **nothing is published**. This is very likely the shell and the packer disagreeing, which is worth stopping for |

## Following upstream

The schedule runs at 08:00 Beijing time (00:00 UTC) and **only builds when upstream moved**: it compares upstream's latest
commit with the version in the newest release and skips in a few seconds when they agree. So when upstream adds support for a
new game, the shell downloaded from here plays it — nobody has to follow up by hand.

This repository is not a clone of upstream; it only carries the difference, in four kinds:

| Line in `.shell-overlay.txt` | Meaning |
|---|---|
| `+ path` | a file this fork **adds**, copied wholesale onto the upstream tree |
| `~ path` | a file this fork **changes**: `.shell-patches/<path>.patch` is `git apply`'d onto upstream's copy |
| `- path` | a file upstream has and this fork **does not carry**: the build must not copy it, and it has to be absent here |
| `! path` | **outside what this fork maintains**: the build does not use it, and a difference there is not drift (this fork's own tools and pages, upstream's Mac front end and iOS tests, the screenshots, upstream's own CI) |
| `.shell-base.txt` | the upstream commit the patches were generated against |

Patches rather than whole-file copies exist so that an upstream edit elsewhere in the same file survives. The **only** case that
conflicts is upstream changing the same lines a patch touches.

### The hole this closes

A change in a file that no overlay line names never reaches the shell: CI assembles from upstream and copies only what the
overlay says, so the build succeeds and the app behaves as upstream does. `applied N of want` does not catch it either — it
counts the `~` lines that exist, not the file that was edited and never added.

`scripts/check_tree.py` closes it: every difference between this tree and an upstream checkout must be one of the four kinds
above, and CI runs it in `Assemble the source tree` on the fresh clone, before anything is copied onto it. Run it here too,
with an upstream checkout beside this repository (`../nx-orig`):

```
python scripts/check_tree.py                 # this tree vs ../nx-orig
python scripts/check_tree.py . ../nx-orig    # the same, spelled out
```

It also says when a `~` entry has stopped earning its keep (identical to upstream) and when a `-` entry is still present.

### When it conflicts

Symptom: the `Shell` job fails in `Assemble the source tree` with `xxx.patch does not apply`. The pipeline stops there and
publishes nothing — you never get a shell that has drifted from upstream without being told.

1. `git clone --depth 1 https://github.com/seedds/nxnative up`
2. `cd up && git apply --reject <this repo>/.shell-patches/xxx.patch` and look at the `.rej` files
3. Read the `.rej` and the new upstream code, then **re-apply the intent** by hand; pasting the old text usually is not right
4. Regenerate the patch and move the base:
   ```
   python dm/ios/tools/make_shell_patch.py dm/xxx/yyy.py     # one file; --all regenerates every "~" entry
   ```
   It diffs against upstream's copy at the commit in `.shell-base.txt` and normalises CRLF on both sides, because the patch is
   applied to upstream's LF files. After moving the base commit, confirm with
   `git -C up apply --check <.shell-patches/xxx.patch>`, then commit the file, the patch and `.shell-base.txt` together
5. Only if step 4 is not enough does C need work: upstream changed one of the six substitutions shell mode makes (a signature, a
   struct field, a `#define` name) or the layout of data that is read across the boundary, so the patch applies but the build
   fails. Fix `dm/host/*.c` and regenerate the patches

The rule of thumb for "does C need work": if upstream touched none of those six substitution points or the structs around them,
the patch applying means it builds.

### Moving the base deliberately (not just when CI complains)

CI only tells you about a conflict after it happens. Bringing the tree to upstream by hand — which is what a release wants
anyway, since the `Source code` archive of a release should be the tree the shell was built from — means:

```
python3 dm/tools/packer_sync.py . <upstream clone>            # must end 0 file(s) differ, 0 upstream-only
```

- Run it **twice**: once with `--sync`, once without, and read the second report. Anything left there is drift.
- **`(gone upstream)` lines have to be deleted by hand.** `--sync` copies and adds, it never removes. Upstream deleting a
  file usually means it fixed the cause in the host and no longer needs the workaround — a per-build `patches.py` for a
  resource heap or a movie gate. Keeping a local copy means this fork keeps patching builds upstream has stopped patching.
- `packer_sync.py` covers `dm/tools`, `dm/gfx`, `dm/games`, `dm/host` and `dm/third_party`. The last one is there because
  the CI tools job compiles `dmsh_xlate.exe` from this tree: when upstream added `TranslationFlags.Msl23ImageAtomics` the
  build failed on a vendored copy that had never been compared.

**Check a patch against the tree it will be applied to, not against your working copy.** `.shell-patches/*.patch` is
checked out as CRLF (`core.autocrlf`) while upstream is LF, so `git apply --check` on the working copy reports a failure
that is not there. Use the committed bytes:

```
git cat-file -p HEAD:.shell-patches/xxx.patch > /tmp/xxx.patch   # or: tr -d '\r' < xxx.patch
git -C up apply --check /tmp/xxx.patch
```


All of this happens in CI. On Windows it shows up as nothing but a new shell zip: same command, same flags, same Python
dependencies.

## The contract between the shell and the packer

They are built separately, so one thing has to line up: the rewriter (`patch_nso.py`) writes call sites into the game's
modules and the host (`gate_register` in `dm/host/hle.c`) dispatches them **by kind name** — `svc`, `cntpct`, `cntvct`,
`obs`, `spin`. The shell is compiled in advance, so a kind a newer packer writes may be one it does not know, and the result
is a package that builds fine and dies on installation:

```
[dm] FATAL unknown gate kind spin
[dm] bt: abort gate_register load_modules host_preload preload_thread
```

That happened once for real: a shell from an early fork build paired with a later packer aborted in `load_modules`.

So `shell_build.sh` now records what the shell can do in `dm/shell_info.txt`:

```
KIND=ios
SOURCE_SHA=<the commit it was built from>
GATES=svc cntpct cntvct obs spin
GUEST=1
ENV=1
```

`GATES` is not written by hand: it is extracted from the strcmp chain in `hle.c`, and if that extraction fails (upstream
rewrote the line, say) the build fails — better no release than a stamp that lies to the packer. `GUEST` and `ENV` are
decided the same way, from the actual code in `loader.c` and `main.m`.

After the rewrite, the packer checks that every kind it actually emitted is in `GATES`, and stops with the fix if not. A
shell too old to carry these keys only gets a warning: a missing key means "cannot be verified", not "broken", and the first
shells published here are in that state.

## A Switch 2 game is a dump, not an NSP

Switch 2 titles circulate as a tree of files rather than as a package, and those files are not encrypted — so this route
reads no key at all, and `-k` is not needed for it. `nxn_pack.py` takes the tree when the folder holds `base/exefs` and
`base/romfs`:

```
<game>/
  base/exefs/             main, main.npdm, sdk, rtld
  base/romfs/
  updates/458752/exefs/   the update's version, as a decimal number
  updates/458752/romfs/   the patched RomFS, not a patch
  dlc/<title ID>/0/romfs/
```

The title ID is the program ID in `main.npdm` (a dump has no ticket to read), the version is the update folder's name, and
an update's RomFS is packed on its own rather than laid over the base — it already holds the base plus its patch, and the
two trees differ (Donkey Kong Bananza's update drops 42 base files). Everything after identification is the usual chain:
rewrite, shader split, translation, assemble.

Nothing is copied or linked to get there: the dump's own folders are what the steps read (`ExeFS/` and `RomFS/` are
answered with `exefs/` and `romfs/`), and only the RomFS copy assemble makes lands in the package. A DLC is copied into
the work folder, because assemble copies that whole tree into the app.

Upstream packs these for the Mac and has not booted one on an iPhone. The chain is the same one that builds an IPA here,
which is why this fork packs them at all — the phone is where they get tested.

## The working folder, and cleaning it up

The packer creates `<title ID>_pack` next to the game's files and keeps every intermediate product in it:

| Folder | What it is | Still needed after packing? |
|---|---|---|
| `extract/` | the ExeFS and RomFS unpacked from the NSP/XCI | no, it is unpacked again in a minute |
| `build/patched/` | the rewritten modules | no |
| `shader/` | the split shader package and the translation cache | no |
| `stage/` | the assembled app tree, including a copy of the RomFS — the largest item | no, it is in the IPA |
| `logs/` | one log per step | **yes**, a few KB, and it is what explains a problem on the phone |

So once the IPA is written, the rest is deleted and only `logs/` stays. The amount is the point: `extract/` and `stage/` each
hold a copy of the game, so a 4 GB game leaves ~16 GB behind. `--keep-work` keeps the whole tree (worth it when repacking the
same game repeatedly, as it skips the unpack and the translation), and `--clean <game folder>` clears out an earlier run.

Only directories this script created are removed, one child at a time, each checked to be inside the work folder — so a
symlink in the RomFS cannot lead the deletion anywhere else.

## Status

| Part | State |
|---|---|
| Shell mode (`-DDM_SHELL`) and the shell build | Working, published by CI as releases |
| `scripts/nxn_pack.py` (the packer) | Working end to end on Windows |
| Device test | **Passed far enough to prove the route**: the shell loads the Guest library, discovers the modules, and compiled Hades II's 140 shaders on the phone in 4.3 s. It then aborted in `SetHeapSize` on the memory layout (fixed since, patch not yet re-tested on the device) |

## What running on Windows actually required

Every one of these was macOS-only code that had never run elsewhere:

| File | Problem |
|---|---|
| `dm/gfx/tools/shader_split.py` | `os.path.relpath` yields backslashes, so recorded RomFS paths stopped matching the profile's `shader_groups`, every shader fell into `other`, and the split tables failed their own comparison |
| `dm/gfx/tools/dmsh_mtlcache.py` | `ctypes.CDLL(None)` means "the running process" on POSIX only; on Windows it raises `TypeError` at import |
| `dm/gfx/tools/dmsh_chain.py` | A single-file translator build has no `.dll` to fingerprint; the executable is `dmsh_xlate.exe`; a Windows checkout's CRLF makes the embedded source digest differ from the tree's |
| `dm/gfx/tools/nometal_run.py` | Three `xcrun` probes were unhandled (the ported script raises on any it does not know), and it deletes a temporary file per shader — which a deletion-counting harness kills midway |
| `dm/ios/tools/pack_ipa.py` | `os.stat` on Windows has no x bit, so the app's main binary was stored as 0644 and the installed app would not start |
| `dm/host/hle_svc.c` | The memory layout was compile-time only, which a game-free shell cannot supply — read from the environment now (`DM_MEM_*`, `DM_APP_POOL`, `DM_MEM_MAP_LIMIT`) |

## Self-checks worth keeping

- `shell_build.sh` refuses to publish if the app carries `romfs/`, `msl/`, `aoc/`, module image sections, or any file over
  8 MB outside `dm/font`.
- The packer compares the NSO build ID of `ExeFS/main` and `sdk` against the profile, and the split shader tables against the
  profile byte for byte. A wrong game version stops there instead of producing an IPA that fails on the phone.
- Shader completeness is decided by `dmsh_build.py`'s per-shader results, not by "some library exists": an interrupted run
  would otherwise be trusted.
