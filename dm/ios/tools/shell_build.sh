#!/bin/bash
# Build the shell: an unsigned iOS app package that contains no game content at all.
# Users pack their own game files into it (scripts/nxn_pack.py) and sideload the result -- no Xcode, no Mac, no paid developer account.
#   shell_build.sh            ios shell (devices)
# Output ($DM_ROOT/build/shell/):
#   nxnative-shell-build-input.zip   the shell (build input, not an installable .ipa: installed as is it reports the missing game and stops)
# It holds our own host program, the font stand-ins and the icon slot. It does NOT hold: the game program, shader libraries, the RomFS,
# or any table generated from game files. The check at the end enforces that.
#
# Unlike d2r-mobile's shell_build.sh this needs NO game files: nxnative's rewriter/shader tables are already plain resources under Gen/dm
# (ios/project.yml lists them as a resource folder) rather than compiled-in arrays, so the only compile-time game dependency was the
# module images in Gen/embed.S -- and those now come from the Guest library at run time (host/loader.c, DM_SHELL).
set -u
cd "$(dirname "$0")/.." || exit 1
IOS=$PWD; DM=$(cd .. && pwd)
t() { echo "[$(date +%T)] $*"; }; die() { t "FAIL: $*"; exit 1; }

# The neutral placeholder profile: modules / romfs marker / chain version are all resolved at run time in shell mode.
export GAME=0000000000000000
export LC_ALL=C
export DM_BUNDLE_ID=${DM_BUNDLE_ID:-org.nxnative.ns2ios}
export DM_DISPLAY_NAME=${DM_DISPLAY_NAME:-ns2ios}
export DM_APP_VERSION=${DM_APP_VERSION:-1.0}
export DM_APP_CATEGORY=public.app-category.action-games
export DM_TEAM_ID=${DM_TEAM_ID:-0000000000}
export DM_SHOW_FPS=NO
export DM_LANG=${DM_LANG:-en-US}
# Upstream's project.yml names the scheme "${DM_SCHEME_NAME}" and build_ipa.sh sets it from the game name; a shell has no game,
# so it supplies its own. Without it xcodegen writes the variable name literally and xcodebuild finds no such scheme.
export DM_SCHEME_NAME=${DM_SCHEME_NAME:-"ns2ios"}

ROOT=${DM_ROOT:-$HOME/dm-work}; O=$ROOT/build/shell; DD=$O/dd; A=$O/app
rm -rf "$O" && mkdir -p "$O" || die "cannot create $O"
[ -f "../games/$GAME/game.tsv" ] || die "placeholder profile ../games/$GAME/game.tsv is missing"

t "generate Gen/ (host source links, empty module assembly, placeholder tables)"
rm -rf Gen && mkdir -p Gen/dm/font Gen/Assets.xcassets || die "cannot create Gen"
python3 tools/gen_build.py ../host Gen > Gen/dm.xcconfig || die "gen_build.py (needs make + clang from Xcode)"
# No module images in the executable: the Guest library supplies them at run time.
echo '// Shell: the module images are not in the executable (dm/ios/tools/shell_build.sh); host/loader.c fetches them from Frameworks/Guest.framework.' > Gen/embed.S
# The packer overwrites Gen/dm (a resource folder) with the game's own tables; a placeholder keeps the folder non-empty.
: > Gen/dm/placeholder.tsv
# The shell's own app icon: a library app is not a game, so its home-screen icon is ours (dm/ios/tools/app-icon.png), the same
# single-size 1024x1024 shape build_ipa.sh writes for a single game. The packer never replaces it: it only adds DmGame frameworks.
printf '{"info":{"author":"xcode","version":1}}\n' > Gen/Assets.xcassets/Contents.json
if [ -f "$DM/ios/tools/app-icon.png" ]; then
  AI=Gen/Assets.xcassets/AppIcon.appiconset; mkdir -p "$AI" || die "mkdir $AI"
  cp "$DM/ios/tools/app-icon.png" "$AI/icon.png" || die "copy the app icon"
  printf '{"images":[{"filename":"icon.png","idiom":"universal","platform":"ios","size":"1024x1024"}],"info":{"author":"xcode","version":1}}\n' > "$AI/Contents.json"
  echo 'DM_APPICON = AppIcon' >> Gen/dm.xcconfig
  t "app icon: dm/ios/tools/app-icon.png"
else
  echo 'DM_APPICON =' >> Gen/dm.xcconfig
  t "no app icon: the home-screen icon stays blank"
fi
# Shared-font stand-ins: ours, not game content (iOS lacks the macOS font path build_ipa.sh uses)
cp "${STD_FONT:-/System/Library/Fonts/Supplemental/Arial Unicode.ttf}" Gen/dm/font/standard.ttf || die "copy standard font (STD_FONT=a TTF)"
# The library's default background (Library.swift): shown behind every game that has no picture of its own.
cp "$DM/ios/tools/default-background.jpg" Gen/dm/default-background.jpg || die "copy the default background"
"${FONT_PY:-python3}" "$DM/tools/make_ext_font.py" Gen/dm/font/nintendo_ext.ttf > /dev/null || die "make_ext_font.py (pip install fonttools, or FONT_PY=python with fontTools)"
# -DDM_SHELL turns on: the Guest-library image header (loader.c), source-form shader compilation (nvn_mtl.m), the runtime chain
# version, the relaxed RomFS lookup and the first-launch shader page (dm_shader_prep.m). Appended to the generated xcconfig line.
python3 - Gen/dm.xcconfig <<'PY'
import sys
p = sys.argv[1]; s = open(p, encoding='utf-8').read().split('\n')
for i, l in enumerate(s):
    if l.startswith('OTHER_CFLAGS = '):
        s[i] = l.replace('OTHER_CFLAGS = $(inherited) ', 'OTHER_CFLAGS = $(inherited) -DDM_SHELL ', 1); break
else:
    sys.exit('no OTHER_CFLAGS line in the generated xcconfig')
open(p, 'w', encoding='utf-8', newline='\n').write('\n'.join(s))
PY
grep -q -- '-DDM_SHELL' Gen/dm.xcconfig || die "could not add -DDM_SHELL to Gen/dm.xcconfig"
# keep xcodegen's output: when it silently skips a target (an info path it cannot write, say) the build fails later with
# "does not contain a scheme", which says nothing about the cause
if ! xcodegen generate > "$O/xcodegen.log" 2>&1; then tail -20 "$O/xcodegen.log"; die "xcodegen, log $O/xcodegen.log"; fi
[ -d DmIOS.xcodeproj ] || die "xcodegen produced no project (log $O/xcodegen.log)"
xcodebuild -list -project DmIOS.xcodeproj > "$O/xcodebuild-list.log" 2>&1 || true

t "xcodebuild (Release, unsigned, iPhone + iPad)"
mkdir -p "$O"
xcodebuild -project DmIOS.xcodeproj -scheme "$DM_SCHEME_NAME" -configuration Release -destination 'generic/platform=iOS' -derivedDataPath "$DD" \
  TARGETED_DEVICE_FAMILY='1,2' CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY= \
  DM_COPY_GAME_RESOURCES=NO DM_XCODE_OUTPUT_ROOT="$DD" SYMROOT="$DD/Build/Products" OBJROOT="$DD/Build/Intermediates.noindex" \
  build > "$O/build.log" 2>&1 \
  || { grep -E 'error:' "$O/build.log" | head -20; die "xcodebuild, log $O/build.log"; }
APP=$DD/Build/Products/Release-iphoneos/DmIOS.app; [ -d "$APP" ] || APP=$(grep -o '/[^ ]*/Release-iphoneos/DmIOS\.app' "$O/build.log" | tail -1)
[ -n "$APP" ] && [ -d "$APP" ] || die "no built app"
cp -R "$APP" "$A" || die "copy the app"; A=$O/app

# What must not be in the shell. The packer writes all of these into the app it produces.
for p in romfs msl aoc Frameworks; do [ -e "$A/$p" ] && die "the shell must not carry $p/"; done
rm -rf "$A/_CodeSignature" "$A/embedded.mobileprovision"
# The memory limit is part of the package, not of the phone: iOS gives an app without com.apple.developer.kernel.increased-
# memory-limit about 3,375 MB, which the bigger games fill (Tears of the Kingdom was killed entering the overworld with 33 MB
# of headroom left). An unsigned binary has no signature for an install tool to read entitlements from, so put one there: sign
# the app ad hoc with the entitlement and ship the signature. Tools that preserve entitlements carry it to the device; the
# others fall back to today's behaviour, and the log line at boot says which one happened.
ENT=$DM/ios/DmIOS.entitlements
[ -f "$ENT" ] || die "no $ENT"
codesign --force --sign - --timestamp=none --entitlements "$ENT" "$A" > "$O/sign.log" 2>&1 \
  || { cat "$O/sign.log"; die "ad-hoc sign with $ENT (codesign)"; }
codesign -d --entitlements :- "$A" 2>/dev/null | plutil -p - > "$O/entitlements.txt" 2>/dev/null || true
grep -q 'increased-memory-limit' "$O/entitlements.txt" \
  || { cat "$O/entitlements.txt" 2>/dev/null; die "the signed app does not carry increased-memory-limit"; }
t "entitlement: $(tr -d '\n' < "$O/entitlements.txt" | sed 's/  */ /g')"
n=$(otool -l "$A/DmIOS" | grep -c -E 'sectname __dm|segname __DM'); [ "$n" = 0 ] || die "the shell executable still has $n module image sections"
# dm/font is ours (the shared-font stand-ins: Arial Unicode alone is ~23 MB), so it is exempt from the size check; anything else big
# would be game content that slipped in (a shader library, the RomFS).
big=$(find "$A" -type f -size +8M -not -path '*/dm/font/*' | head -5); [ -z "$big" ] || die "the shell carries files larger than 8 MB outside dm/font: $big"
# What this shell can do, recorded for the packer. The packer writes call sites into the game's modules whose kind is
# dispatched in host/hle.c, and a shell built before a kind existed dies on the phone with "unknown gate kind <k>" the
# moment the module loads -- which is exactly what an old shell zip plus a new packer does. So list the kinds from the code
# that dispatches them, and refuse to ship a stamp that did not come out of that grep: a stamp that cannot be trusted is
# worse than no stamp, because the packer uses it to decide whether the package is safe to build.
GATES=$(grep -o 'strcmp(kind, "[a-z]*")' ../host/hle.c | sed 's/.*"\([a-z]*\)".*/\1/' | tr '\n' ' ' | sed 's/ *$//')
case " $GATES " in
  *" svc "*" spin "*) ;;
  *) die "could not read the gate kinds out of host/hle.c (got '$GATES'): the stamp would lie to the packer" ;;
esac
GUEST=0; grep -q 'dm_shell_modules' ../host/loader.c && GUEST=1
ENV=0; grep -q 'env.txt' Sources/main.m && ENV=1
[ "$GUEST" = 1 ] || die "host/loader.c has no dm_shell_modules: this tree is not the shell route"
[ "$ENV" = 1 ] || die "Sources/main.m does not read dm/env.txt: the packer's memory layout would be ignored"
printf 'KIND=ios\nSOURCE_SHA=%s\nCHAIN_PLACEHOLDER=%s\nGATES=%s\nGUEST=%s\nENV=%s\nMEM=1\n' \
  "${GITHUB_SHA:-local}" "$(sed -n 's/^#define DMSH_CHAIN "\(.*\)"/\1/p' ../games/$GAME/dmsh_chain.h)" "$GATES" "$GUEST" "$ENV" \
  > "$A/dm/shell_info.txt"
t "shell understands gates: $GATES"

( cd "$A" && find . -type f | sort | while read -r f; do printf '%s\t%s\n' "$(stat -f %z "$f")" "$f"; done ) > "$O/shell_files.tsv"
( cd "$O" && rm -rf Payload && mkdir Payload && cp -R app Payload/DmIOS.app && zip -qry nxnative-shell-build-input.zip Payload && rm -rf Payload ) || die "pack the shell"
t "shell: $O/nxnative-shell-build-input.zip ($(du -h "$O/nxnative-shell-build-input.zip" | cut -f1), $(wc -l < "$O/shell_files.tsv" | tr -d ' ') files)"
