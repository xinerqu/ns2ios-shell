// iOS App entry: set the environment (game files, saves, shader libraries), write the host log to Documents/log.txt, create the window (front_ios.m), then run the host's dm_main on a big-stack thread.
// The game files (RomFS) ship inside the App at <App>/romfs; Documents/romfs.img (one image, scripts/push_romfs.sh; its name in any letter case) or a Documents/romfs folder is also accepted (lets you swap them without reinstalling). The DLC folder aoc/ likewise: Documents/aoc, else <App>/aoc.
#import <UIKit/UIKit.h>
#import <Metal/Metal.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <os/proc.h>          // os_proc_available_memory: the line at boot saying whether the memory entitlement took effect
#include "game_profile.h"   // game profile constants (the RomFS marker path)
#include "front.h"
#include "romfs_image.h"
#include "romfs_identity.h"
#include "player_env.h"
#ifndef DM_PLAYER_BUILD
#define DM_PLAYER_BUILD 0
#endif

extern char **environ;

int dm_main(int argc, char **argv);   // dm/host/main.c compiled in with -Dmain=dm_main
#ifdef DM_SHELL
int dm_shell_modules(const char **out, int max);   // loader.c: the module names found in the Guest library's sections
int dm_shader_prep_begin(void (^done)(void));      // dm_shader_prep.m: compile source-form shaders once, before the host starts
#endif
UIViewController *front_ios_root(void);
void front_ios_focus_event(int ev);

static char *g_argv[16]; static int g_argc;   // 2 fixed + the Guest library's module names (a shell has no module list compiled in)

// Documents/romfs.img, one RomFS image file (dm/tools/romfs_image.py, scripts/push_romfs.sh): only a complete one counts. Its index is written last and ends the file, so a copy cut off partway is skipped
static int romfs_image_complete(NSString *path) {
    romfs_img *m = romfs_image_open(path.fileSystemRepresentation);
    if (!m) {
        if (errno != ENOENT) fprintf(stderr, "[RED] boot: invalid RomFS image %s (errno=%d); trying the folders\n", path.fileSystemRepresentation, errno);
        return 0;
    }
    romfs_image_close(m); return 1;
}
// A build made with DM_COPY_ROMFS=NO carries <App>/romfs.id (dm/host/romfs_identity.h): only a Documents/romfs.img with that identity is used. No romfs.id (the default build): any complete image
static int romfs_identity_accepts(NSString *img) {
    romfs_id want, got;
    int rc = romfs_id_check([NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:@"romfs.id"].fileSystemRepresentation, img.fileSystemRepresentation, &want, &got);
    if (rc != ROMFS_ID_OK && rc != ROMFS_ID_NONE) fprintf(stderr, "[RED] boot: Documents/%s is not the image this build needs (verdict %d); not used\n", img.lastPathComponent.UTF8String, rc);
    return rc == ROMFS_ID_OK || rc == ROMFS_ID_NONE;
}
// The images in Documents: romfs.img in any letter case, romfs.img itself first. The NSP script writes the image as RomFS.img (extract/<version>/RomFS.img) and the Mac's
// file system ignores letter case, but iOS's does not: a copy made through Finder or the Files app keeps the source's name, which an exact lookup of romfs.img never finds
static NSArray<NSString *> *romfs_document_images(NSString *doc) {
    NSMutableArray<NSString *> *imgs = [NSMutableArray array];
    for (NSString *n in [NSFileManager.defaultManager contentsOfDirectoryAtPath:doc error:nil])
        if ([n caseInsensitiveCompare:@"romfs.img"] == NSOrderedSame) [imgs addObject:[doc stringByAppendingPathComponent:n]];
    [imgs sortUsingComparator:^NSComparisonResult(NSString *a, NSString *b) {
        BOOL ea = [a.lastPathComponent isEqualToString:@"romfs.img"], eb = [b.lastPathComponent isEqualToString:@"romfs.img"];
        return ea != eb ? (ea ? NSOrderedAscending : NSOrderedDescending) : [a compare:b];
    }];
    return imgs;
}
static NSString *romfs_locate(NSString *prog) {   // prog: a game of several programs, the running program's folder in the app (p<N>); nil otherwise
    NSFileManager *fm = NSFileManager.defaultManager;
    NSString *doc = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES)[0];
    if (prog) {   // the program's files are in the app (Documents holds one image, one program's)
        NSString *p = [NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:prog], *img = [p stringByAppendingPathComponent:@"romfs.img"], *d = [p stringByAppendingPathComponent:@"romfs"];
        if (romfs_image_complete(img)) return img;
        return [fm fileExistsAtPath:[d stringByAppendingPathComponent:@(DM_GAME_ROMFS_MARKER)]] ? d : nil;
    }
    for (NSString *img in romfs_document_images(doc)) {
        if (!romfs_image_complete(img) || !romfs_identity_accepts(img)) continue;
        // A folder copy from before images is now dead weight of up to many GB (often cut off partway). Deleting its 300,000 files here took longer than iOS allows an app to start
        // (Tears of the Kingdom: killed on a black screen), so it is moved aside at once and deleted in the background, leftovers of an interrupted delete included
        NSString *old = [doc stringByAppendingPathComponent:@"romfs"], *tmp = NSTemporaryDirectory();
        if ([fm fileExistsAtPath:old] && [fm moveItemAtPath:old toPath:[tmp stringByAppendingPathComponent:[@"romfs-old-" stringByAppendingString:NSUUID.UUID.UUIDString]] error:nil])
            fprintf(stderr, "boot: Documents/romfs moved aside for deletion (Documents/%s replaces it)\n", img.lastPathComponent.UTF8String);
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_BACKGROUND, 0), ^{
            NSFileManager *bfm = [NSFileManager new];
            for (NSString *n in [bfm contentsOfDirectoryAtPath:tmp error:nil]) if ([n hasPrefix:@"romfs-old-"]) [bfm removeItemAtPath:[tmp stringByAppendingPathComponent:n] error:nil];
        });
        return img;
    }
    NSString *bundledImage = [NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:@"romfs.img"];
    NSString *documentFolder = [doc stringByAppendingPathComponent:@"romfs"];
    if ([fm fileExistsAtPath:[documentFolder stringByAppendingPathComponent:@(DM_GAME_ROMFS_MARKER)]]) return documentFolder;
    if (romfs_image_complete(bundledImage)) return bundledImage;
    for (NSString *d in @[[doc stringByAppendingPathComponent:@"romfs"], [NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:@"romfs"]]) {
#ifdef DM_SHELL
        // One shell serves any game, so it cannot know a single game's romfs_marker: the first existing, non-empty candidate wins.
        // The packer writes the game's own tree there; if it is the wrong game, the host reports it when reading the files.
        if ([fm fileExistsAtPath:d] && ([fm contentsOfDirectoryAtPath:d error:nil] ?: @[]).count) return d;
#else
        if ([fm fileExistsAtPath:[d stringByAppendingPathComponent:@(DM_GAME_ROMFS_MARKER)]]) return d;   // only a directory containing the game profile's romfs_marker path counts (Mario: System/Resource, the resource size table)
#endif
    }
    return nil;
}

// Add-on content (DLC): each DLC's RomFS image at <dir>/<index>.img (or a legacy <index>/ folder). Like the game files, Documents/aoc comes before <App>/aoc.
// A DM_AOC_DIR that is already set (env.txt, the launch environment) wins. The host mounts and counts the DLC from DM_AOC_DIR (hle_fs.c, hle_misc.c)
static void aoc_locate(void) {
    if (getenv("DM_AOC_DIR")) return;
    NSFileManager *fm = NSFileManager.defaultManager;
    NSString *doc = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES)[0];
    for (NSString *d in @[[doc stringByAppendingPathComponent:@"aoc"], [NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:@"aoc"]]) {
        BOOL dir = NO;
        if (![fm fileExistsAtPath:d isDirectory:&dir] || !dir) continue;
        setenv("DM_AOC_DIR", d.fileSystemRepresentation, 0);
        fprintf(stderr, "boot: DLC from %s\n", d.fileSystemRepresentation);
        return;
    }
}

// Boot prerequisites (done before UIApplicationMain): directories / environment variables / log -- the host's early load reads these
// Why a slim build found no usable game files: the verdict on Documents/romfs.img (the first of romfs_document_images), in the log and for the screen (front_ios_boot_message). NULL when the build has no romfs.id
static char g_boot_message[640];
const char *front_ios_boot_message(void) { return g_boot_message[0] ? g_boot_message : NULL; }
// An app that holds several games: one Frameworks/DmGame<title ID>.framework per game (scripts/nxn_pack.py --framework), which carries the
// game's modules and its tables, while the big files stay in Documents -- Documents/ns2ios/games/<title ID>/{romfs.img,msl,aoc}. Adding a
// game then reinstalls a few dozen MB instead of several GB, and the game's data and saves survive that as well as a shell update.
// Returns the framework's dm/ (the host's module folder, argv[1]) with the environment set for that game; nil when this app is the
// one-game kind (Frameworks/Guest.framework, the packer writes the whole game into the bundle) -- then program_env below runs as before.
#ifdef DM_SHELL
static void env_file_read(NSString *path, NSMutableArray *from_file);   // defined below, used by dm_shell_start_game
// The game this app starts: the first Frameworks/DmGame<title ID>.framework, nil for the one-game package. tidp takes its title ID.
// The library that lets the player choose among several (and the card's icon and name) reads the same name.
static NSString *shell_game_framework(NSString *want_tid, NSString **tidp) {
    NSFileManager *fm = NSFileManager.defaultManager;
    NSString *fwdir = NSBundle.mainBundle.privateFrameworksPath;
    for (NSString *name in [fm contentsOfDirectoryAtPath:fwdir error:nil] ?: @[]) {
        if (![name hasPrefix:@"DmGame"] || ![name hasSuffix:@".framework"] || name.length <= 6 + 10 + 1) continue;
        NSString *base = [name stringByDeletingPathExtension];                                  // DmGame<16 hex digits>
        NSString *tid = [base substringFromIndex:6];
        if (want_tid && ![tid isEqualToString:want_tid]) continue;
        NSString *fw = [fwdir stringByAppendingPathComponent:name];
        if (![fm fileExistsAtPath:[fw stringByAppendingPathComponent:base]]) continue;
        if (tidp) *tidp = tid;
        return fw;
    }
    return nil;
}
// How many games this app holds. Exactly one is the old flow: entered at launch, no library (a package with a single game looks and
// behaves exactly as before). None, or several, starts on the library instead (Library.swift) and preloads only when one is picked.
int dm_shell_game_count(void) {
    int n = 0; NSFileManager *fm = NSFileManager.defaultManager;
    for (NSString *name in [fm contentsOfDirectoryAtPath:NSBundle.mainBundle.privateFrameworksPath error:nil] ?: @[])
        if ([name hasPrefix:@"DmGame"] && [name hasSuffix:@".framework"] && name.length > 6 + 10 + 1) n++;
    return n;
}
int dm_shell_library_needed(void) { return dm_shell_game_count() != 1; }
static NSString *shell_game_dm_for(NSString *doc, NSString *want_tid, char *err, size_t errn) {
    NSFileManager *fm = NSFileManager.defaultManager;
    NSString *tid = nil, *fw = shell_game_framework(want_tid, &tid);
    if (!fw) { snprintf(err, errn, "No such game in this app."); return nil; }
    {
        NSString *base = [[fw lastPathComponent] stringByDeletingPathExtension];
        NSString *exe = [fw stringByAppendingPathComponent:base];
        NSString *fwdm = [fw stringByAppendingPathComponent:@"dm"];
        NSString *home = [doc stringByAppendingPathComponent:@"ns2ios"];
        NSString *game = [[home stringByAppendingPathComponent:@"games"] stringByAppendingPathComponent:tid];
        NSString *saves = [[home stringByAppendingPathComponent:@"saves"] stringByAppendingPathComponent:tid];
        NSString *cheats = [[home stringByAppendingPathComponent:@"cheats"] stringByAppendingPathComponent:tid];
        NSString *romfs = [game stringByAppendingPathComponent:@"romfs.img"], *aoc = [game stringByAppendingPathComponent:@"aoc"];
        for (NSString *d in @[[home stringByAppendingPathComponent:@"games"], saves, cheats, [game stringByAppendingPathComponent:@"msl"],
                              [game stringByAppendingPathComponent:@"temp"]])
            [fm createDirectoryAtPath:d withIntermediateDirectories:YES attributes:nil error:nil];
        // Not an error the app can work around: the data is copied in from the Files app, so it says where (front_ios_boot_message).
        if (!romfs_image_complete(romfs)) {
            snprintf(g_boot_message, sizeof g_boot_message,
                     "%s has no game data yet. Open the Files app, this app's folder, ns2ios/games/%s, and copy the romfs.img there.",
                     base.UTF8String, tid.UTF8String);
            fprintf(stderr, "[RED] boot: %s has no game data (Documents/ns2ios/games/%s/romfs.img)\n", base.UTF8String, tid.UTF8String);
            snprintf(err, errn, "No game data: put romfs.img in the Files app, this app's ns2ios/games/%s folder.", tid.UTF8String);
            return nil;
        }
        setenv("DM_GAME_IMAGE", exe.fileSystemRepresentation, 1);   // loader.c reads the modules from this framework, not from Frameworks/Guest
        setenv("DM_APP_DM", fwdm.fileSystemRepresentation, 1);      // Launcher.swift reads this game's dm/launcher.json from here
        // Several games share one app: the saved choices and the cheat text must be per game (Launcher.swift, CheatsView.swift). A
        // one-game package keeps the keys it always used, so nothing is lost when this shell replaces an older package.
        // Several games share one app: the saved choices and the cheat text must be per game (Launcher.swift, CheatsView.swift). A
        // one-game package keeps the keys it always used, so nothing is lost when this shell replaces an older package.
        if (dm_shell_game_count() != 1) setenv("DM_GAME_KEY", "1", 1);
        setenv("DM_MSL_DIR", [game stringByAppendingPathComponent:@"msl"].fileSystemRepresentation, 1);
        NSString *shmap = [fwdm stringByAppendingPathComponent:@"shader_map.tsv"];
        if ([fm fileExistsAtPath:shmap]) setenv("DM_NVN_SHMAP", shmap.fileSystemRepresentation, 1);
        setenv("DM_ROMFS", romfs.fileSystemRepresentation, 1);
        if ([fm fileExistsAtPath:aoc]) setenv("DM_AOC_DIR", aoc.fileSystemRepresentation, 1);
        NSString *pso = [game stringByAppendingPathComponent:@"pso"];   // the offline pipeline archive, when the packer moved one here
        if ([fm fileExistsAtPath:pso]) setenv("DM_PSO_BUNDLE", pso.fileSystemRepresentation, 1);
        // Overwrite: the shared Documents/save and Documents/cheats below are for the one-game app; with several games each keeps its own,
        // so deleting one game's data never touches another's saves.
        // Its own temp: the temp: mount and the cache/sysdata storage a game writes (hle_fs.c) would otherwise be shared by every game
        setenv("DM_TEMP", [game stringByAppendingPathComponent:@"temp"].fileSystemRepresentation, 1);
        setenv("DM_SAVE", saves.fileSystemRepresentation, 1);
        setenv("DM_CHEATS", cheats.fileSystemRepresentation, 1);
        fprintf(stderr, "boot: game framework %s\n", exe.fileSystemRepresentation);
        fprintf(stderr, "boot: game files %s\n", romfs.fileSystemRepresentation);
        return fwdm;
    }
}
// The one-game package: the game in the bundle, entered at launch (no library).
static NSString *shell_game_dm(NSString *doc) { char e[512] = {0}; return shell_game_dm_for(doc, nil, e, sizeof e); }
// Called from the library (Library.swift) when the player picks a game: what boot_env does for the one-game package (the game's
// environment and module names), then the host's early loading. One game per process -- dm_profile, the framework image and the module
// names are read once -- so a second call is refused and returning to the library is a relaunch of the app.
const char *dm_shell_start_game(const char *tid) {
    static char err[512]; static int started;
    if (started) return "A game is already running. Close the app and open it again.";
    if (!tid || !*tid) return "No game chosen.";
    NSString *doc = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES)[0];
    NSString *dm = shell_game_dm_for(doc, @(tid), err, sizeof err);
    if (!dm) return err[0] ? err : "This game cannot start.";
    chdir(doc.fileSystemRepresentation);
    // The process-wide argv, the one boot_thread hands dm_main: the host refuses a preload whose arguments differ from main's
    // (main.c), so both must be the same array -- not a local one.
    g_argv[0] = strdup("dmhost"); g_argv[1] = strdup(dm.fileSystemRepresentation); g_argc = 2;
    { const char *mods[12]; int nm = dm_shell_modules(mods, 12);
      for (int k = 0; k < nm && g_argc < 16; k++) { g_argv[g_argc++] = strdup(mods[k]); fprintf(stderr, "boot: module '%s' (from the game framework)\n", mods[k]); } }
    // This game's own dm/env.txt: boot_env read only Documents/env.txt, because no game was known then.
    { NSMutableArray *keys = [NSMutableArray array]; env_file_read([dm stringByAppendingPathComponent:@"env.txt"], keys);
      NSString *prev = getenv("DM_ENV_FILE_KEYS") ? @(getenv("DM_ENV_FILE_KEYS")) : @"";
      NSString *all = [@[prev, [keys componentsJoinedByString:@" "]] componentsJoinedByString:@" "];
      setenv("DM_ENV_FILE_KEYS", [all stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceCharacterSet].UTF8String, 1); }
    dm_host_preload_start(g_argc, g_argv);   // module tables, relocation and take-over in the background while the settings page comes up
    started = 1;
    return NULL;
}
#endif
#ifndef DM_SHELL
// A one-game package keeps its game in the executable, so Library.swift (compiled into every build) links against these and finds no
// library: exactly one game, entered at launch, as before.
int dm_shell_game_count(void) { return 1; }
int dm_shell_library_needed(void) { return 0; }
const char *dm_shell_start_game(const char *tid) { (void)tid; return "This package runs the game it was built with."; }
#endif
// Why this device cannot run the games: its GPU has no BC texture formats, which the games' textures use and the host does not decompress (dm/gfx/tex/README.md; the A16 and older,
// GitHub issue #704), so the screen says so instead of Metal aborting at the game's first BC texture. NULL on a supported device. Called by startGame (front_ios.m) once the launcher has set
// its switches: this makes the process's first Metal device, and Metal reads MTL_HUD_ENABLED then; checked at launch, it kept the launcher's Performance HUD off (GitHub issue #840)
const char *front_ios_device_check(void) {
    static char msg[320]; static int done;
    if (done++) return msg[0] ? msg : NULL;
    id<MTLDevice> gpu = MTLCreateSystemDefaultDevice();   // the system default device, the one the graphics layers take (mtl_init repeats this check for the other routes)
    if (gpu.supportsBCTextureCompression) return NULL;
    snprintf(msg, sizeof msg, "This device's GPU (%s) cannot read BC-compressed textures, which the games use. It needs an iPhone 15 Pro or newer (A17 Pro chip or later) or an iPad with an M1 chip or later.",
             gpu ? gpu.name.UTF8String : "no Metal device");
    fprintf(stderr, "[RED] boot: %s\n", msg);
    return msg;
}
static void romfs_identity_report(NSString *doc) {
    romfs_id want, got;
    int rc = romfs_id_check([NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:@"romfs.id"].fileSystemRepresentation,
                            (romfs_document_images(doc).firstObject ?: [doc stringByAppendingPathComponent:@"romfs.img"]).fileSystemRepresentation, &want, &got);
    if (rc == ROMFS_ID_OK || rc == ROMFS_ID_NONE) return;
    romfs_id_message(rc, &want, &got, NSBundle.mainBundle.bundleIdentifier.UTF8String ?: "<bundle ID>", g_boot_message, sizeof g_boot_message);
    fprintf(stderr, "[RED] boot: %s\n", g_boot_message);
}

// One KEY=VALUE per line, '#' starts a comment; a name already in the environment is left alone, so Documents/env.txt wins over a
// game's own dm/env.txt. from_file collects the names this set, for DM_ENV_FILE_KEYS (the launcher keeps them).
static void env_file_read(NSString *path, NSMutableArray *from_file) {
    NSString *txt = [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:nil];
    for (NSString *raw in [txt componentsSeparatedByCharactersInSet:NSCharacterSet.newlineCharacterSet]) {
        NSString *ln = [raw stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceCharacterSet]; NSRange eq = [ln rangeOfString:@"="];
        if (!ln.length || [ln hasPrefix:@"#"] || eq.location == NSNotFound || !eq.location) continue;
        NSString *k = [ln substringToIndex:eq.location], *v = [ln substringFromIndex:eq.location + 1];
#if DM_PLAYER_BUILD
        if (dm_player_diagnostic_key(k.UTF8String)) continue;
#endif
        if (!getenv(k.UTF8String)) { setenv(k.UTF8String, v.UTF8String, 0); [from_file addObject:k];
                                     fprintf(stderr, "boot: env %s=%s (%s)\n", k.UTF8String, v.UTF8String, path.lastPathComponent.UTF8String); }
    }
}

static NSString *program_env(void);
static int boot_env(void) {
    NSFileManager *fm = NSFileManager.defaultManager;
    NSString *doc = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES)[0];
    NSString *res = NSBundle.mainBundle.resourcePath;
    for (NSString *d in @[@"save", @"temp", @"cheats"]) [fm createDirectoryAtPath:[doc stringByAppendingPathComponent:d] withIntermediateDirectories:YES attributes:nil error:nil];
    // Keep game files and scratch data out of device backups (Documents is backed up by default; a RomFS placed there is many GB). Saves, logs and the pipeline list stay in the backup
    // ns2ios/games/ is the several-games layout's data (a RomFS image per game): out, like romfs/ above. ns2ios/saves/ stays in, as save/ does.
    for (NSString *d in [@[@"romfs", @"aoc", @"temp", @"ns2ios/games"] arrayByAddingObjectsFromArray:[romfs_document_images(doc) valueForKey:@"lastPathComponent"]]) {
        NSURL *u = [NSURL fileURLWithPath:[doc stringByAppendingPathComponent:d]]; NSError *e = nil;
        if ([fm fileExistsAtPath:u.path] && ![u setResourceValue:@YES forKey:NSURLIsExcludedFromBackupKey error:&e]) fprintf(stderr, "boot: %s could not be excluded from backup: %s\n", d.UTF8String, e.localizedDescription.UTF8String);
    }
    // Log: the host only writes to stderr; redirect it to Documents/log.txt (the previous one is kept as log.prev.txt), retrieve it from the Files app when something goes wrong
    NSString *log = [doc stringByAppendingPathComponent:@"log.txt"], *prev = [doc stringByAppendingPathComponent:@"log.prev.txt"];
    [fm removeItemAtPath:prev error:nil]; [fm moveItemAtPath:log toPath:prev error:nil];
    if (freopen(log.fileSystemRepresentation, "w", stderr)) setvbuf(stderr, NULL, _IOLBF, 0);
    // Environment switch files (for on-device diagnostics: a sideloaded app cannot be given environment variables from outside): one KEY=VALUE per line, # starts a comment; Documents/env.txt takes precedence over dm/env.txt in the bundle (put there by build_ipa.sh's DM_ENV_FILE)
    NSMutableArray<NSString *> *from_file = [NSMutableArray array], *from_launch = [NSMutableArray array];
    for (char **e = environ; *e; e++) {   // set at launch (Xcode, devicectl): before the env files, and the launcher keeps them
        const char *eq = strchr(*e, '='); NSString *k = eq && !strncmp(*e, "DM_", 3) ? [[NSString alloc] initWithBytes:*e length:(NSUInteger)(eq - *e) encoding:NSUTF8StringEncoding] : nil;
        if (k) [from_launch addObject:k];
    }
#if DM_PLAYER_BUILD
    // Player-facing choices belong to the launcher, including saved choices.
    // Neither old diagnostic files nor bundled defaults may lock those rows.
    NSMutableSet<NSString *> *launcher_keys = [NSMutableSet set];
    NSData *settings_data = [NSData dataWithContentsOfFile:[res stringByAppendingPathComponent:@"dm/launcher.json"]];
    NSDictionary *settings_file = settings_data ? [NSJSONSerialization JSONObjectWithData:settings_data options:0 error:nil] : nil;
    for (NSDictionary *row in settings_file[@"settings"]) if ([row[@"env"] isKindOfClass:NSString.class]) [launcher_keys addObject:row[@"env"]];
#endif
    // Several games in one app: the game's own dm/env.txt, inside its framework, stands where the bundle's dm/env.txt does -- the packer
    // writes the profile's values (memory layout, upscaler keys, render_cond, pad layout) there, so without this the game would start on
    // the shell's placeholder profile and stop on its first allocation.
    NSString *dm_env = nil;
#ifdef DM_SHELL
    {   // With several games no game is known yet: dm_shell_start_game reads the picked one's. Exactly one, the old path, reads it here.
      NSString *fw = dm_shell_game_count() == 1 ? shell_game_framework(nil, NULL) : nil;
      if (fw) dm_env = [[fw stringByAppendingPathComponent:@"dm"] stringByAppendingPathComponent:@"env.txt"]; }
#endif
    if (!dm_env) dm_env = [[res stringByAppendingPathComponent:@"dm"] stringByAppendingPathComponent:@"env.txt"];
    for (NSString *ef in @[[doc stringByAppendingPathComponent:@"env.txt"], dm_env]) env_file_read(ef, from_file);
    setenv("DM_ENV_FILE_KEYS", [from_file componentsJoinedByString:@" "].UTF8String, 1);   // the launcher keeps what an env file set (LauncherSettings.swift)
    setenv("DM_ENV_LAUNCH_KEYS", [from_launch componentsJoinedByString:@" "].UTF8String, 1);   // and what the launch environment set (docs/frontends.md, Saved choices)
    front_hud_env();   // an env file's DM_METAL_HUD; nothing in this process makes a Metal device before startGame (front_ios.m), which applies the launcher's
    // Game language (nn::oe::GetDesiredLanguage, hle_oe.c): the one the IPA was packed with (the NSP script's LANGUAGE / --language → DMLanguage in Info.plist), unless an env file sets DM_LANG
    // (or the launcher, which replaces this default before the game starts)
    NSString *lang = [NSBundle.mainBundle objectForInfoDictionaryKey:@"DMLanguage"];
    if (!getenv("DM_LANG") && [lang isKindOfClass:NSString.class] && lang.length) setenv("DM_LANG", lang.UTF8String, 0);
    fprintf(stderr, "boot: game language %s\n", getenv("DM_LANG") ?: "en-US");
    // How much memory iOS will let this process keep. With com.apple.developer.kernel.increased-memory-limit in the package's
    // signature it is several GB (6-8 on a 12 GB phone); without it, about 3,375 MB, which the bigger games fill exactly to the
    // limit and are then killed for, with no crash log to explain it. One line, so that case can be told apart from a crash.
    { unsigned long long av = os_proc_available_memory();
      fprintf(stderr, "boot: memory limit %llu MB (%s)\n", av >> 20,
              av < (5ull << 30) ? "no increased-memory-limit entitlement: the bigger games will be killed for memory"
                                : "entitlement in effect"); }
#ifdef DM_SHELL
    // Zero or several games: the library is the first screen and dm_shell_start_game() (Library.swift's pick) sets the game up. Only the
    // package-wide switches belong here, before any game is known.
    if (dm_shell_library_needed()) {
        for (NSString *d in @[@"save", @"temp", @"cheats", @"ns2ios"])
            [fm createDirectoryAtPath:[doc stringByAppendingPathComponent:d] withIntermediateDirectories:YES attributes:nil error:nil];
        setenv("DM_TEMP", [doc stringByAppendingPathComponent:@"temp"].fileSystemRepresentation, 0);
        setenv("DM_CHEATS_DEFER", "1", 1);   // the cheat load waits for Play, as in the one-game package
        setenv("DM_CHEATS_PANEL", "1", 1);
        setenv("DM_GUEST_FILE_DIR", NSTemporaryDirectory().fileSystemRepresentation, 0);
        setenv("DM_FONT_DIR", [[res stringByAppendingPathComponent:@"dm"] stringByAppendingPathComponent:@"font"].fileSystemRepresentation, 0);
        chdir(doc.fileSystemRepresentation);
        fprintf(stderr, "boot: library mode, %d game(s) in this app (the host starts when one is picked)\n", dm_shell_game_count());
        return 1;
    }
#endif
    NSString *dm = program_env();
    if (!dm) { romfs_identity_report(doc); fprintf(stderr, "boot: game files not found (Documents/romfs.img, Documents/romfs or <App>/romfs)\n"); return 0; }
    setenv("DM_SAVE", [doc stringByAppendingPathComponent:@"save"].fileSystemRepresentation, 0);
    setenv("DM_TEMP", [doc stringByAppendingPathComponent:@"temp"].fileSystemRepresentation, 0);
    // Atmosphère cheat files the player drops in from the Files app: Documents/cheats/<build ID>.txt, named by the main
    // NSO's build ID (hle_cheats.c logs the one it is looking for). Nothing else on iOS sets these switches, so without
    // this line every cheat is off with "cheats: off (neither DM_CHEATS nor DM_ROOT is set)"; an env.txt line still wins.
    setenv("DM_CHEATS", [doc stringByAppendingPathComponent:@"cheats"].fileSystemRepresentation, 0);
    // The host loads at launch, before the settings screen exists, so the cheat load waits for Play (front_ios.m calls
    // dm_cheats_after_launcher): otherwise it would only log "no file for build" and never see the panel's file.
    setenv("DM_CHEATS_DEFER", "1", 1);
    // The panel is the only switch there is on a phone, so it owns every entry -- including a "{Name}" master, which
    // Atmosphère always runs: with no menu to turn one off, a file whose master breaks the game would be unusable.
    setenv("DM_CHEATS_PANEL", "1", 1);
    setenv("DM_GUEST_FILE_DIR", NSTemporaryDirectory().fileSystemRepresentation, 0);   // DM_GUEST_FILE (hle_svc.c) keeps the guest's memory in files there
    setenv("DM_FONT_DIR", [[res stringByAppendingPathComponent:@"dm"] stringByAppendingPathComponent:@"font"].fileSystemRepresentation, 0);   // Shared-font stand-ins (standard.ttf / nintendo_ext.ttf): iOS lacks the macOS font path
    aoc_locate();   // add-on content: Documents/aoc, else <App>/aoc; an empty Documents/aoc explicitly selects no DLC
    NSString *pso = [res stringByAppendingPathComponent:@"pso"];   // the pipeline archive made offline (build_ipa.sh, dm/gfx/tools/pso_offline.py): the shader pairs' bases, which the device then does not build
    if ([fm fileExistsAtPath:pso]) setenv("DM_PSO_BUNDLE", pso.fileSystemRepresentation, 0);
    chdir(doc.fileSystemRepresentation);
    g_argv[0] = strdup("dmhost"); g_argv[1] = strdup(dm.fileSystemRepresentation); g_argc = 2;
#ifdef DM_SHELL
    // A shell has no module list compiled in (DM_GAME_MODULES is only main.c's fallback when argv gives none), so the names are taken
    // from the Guest library that the user packed from their own game files: one shell then serves any game.
    { const char *mods[12]; int nm = dm_shell_modules(mods, 12);
      for (int k = 0; k < nm && g_argc < 16; k++) { g_argv[g_argc++] = strdup(mods[k]); fprintf(stderr, "boot: module '%s' (from the Guest library)\n", mods[k]); } }
#endif
    dm_host_preload_start(g_argc, g_argv);   // Module tables / relocation / takeover run early in the background, in parallel with UIKit startup (and the launcher: the settings it sets are read when the game starts)
    return 1;
}

// The running program's files (an app holding the programs of one game, build_ipa.sh PROGRAMS_TSV: Super Mario 3D All-Stars' launcher and the games it starts): dm/programs.txt lists them, and each has
// its own: <App>/p<N>/romfs.img (html and jit beside it), <App>/msl/p<N>, <App>/dm/p<N> (rewriter tables, shader tables, launcher settings) and its modules in sections tagged p<N> (DM_EMBED_TAG, loader.c).
// The host's profile is the program's (DM_PROGRAM, main.c dm_game_cur). oe::ExecuteProgram records the program and its parameters in Documents/program.bin (DM_EXEC_OUT, hle_oe.c), names it on screen, schedules a
// notification that opens the app (front_ios.m front_program_handoff) and ends; this start reads the record and takes it away, so a later start, after the player quit the game, is at the launcher as on the Switch.
// Started from the record, the settings screen stays away (DM_LAUNCHER=0): the player is on the way into a game. Returns the program's dm folder (the host's modules folder), nil when its game files are missing
static NSString *program_env(void) {
    NSFileManager *fm = NSFileManager.defaultManager;
    NSString *doc = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES)[0], *res = NSBundle.mainBundle.resourcePath;
#ifdef DM_SHELL
    {   // Several games in one app: the game its framework and its Documents/ns2ios folder name wins over everything below
        NSString *m = shell_game_dm(doc); if (m) return m;
    }
#endif
    NSString *dmdir = [res stringByAppendingPathComponent:@"dm"], *msl = [res stringByAppendingPathComponent:@"msl"], *prog = nil;
    NSString *plist = [NSString stringWithContentsOfFile:[dmdir stringByAppendingPathComponent:@"programs.txt"] encoding:NSUTF8StringEncoding error:nil];
    if (plist) {
        NSArray *have = [plist componentsSeparatedByCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
        NSString *rec = [doc stringByAppendingPathComponent:@"program.bin"]; NSData *d = [NSData dataWithContentsOfFile:rec]; int32_t n = 0;
        if (d.length >= 4) { memcpy(&n, d.bytes, 4); [fm removeItemAtPath:rec error:nil]; setenv("DM_LAUNCHER", "0", 1); }
        if (![have containsObject:[NSString stringWithFormat:@"%d", n]]) { fprintf(stderr, "[RED] boot: program %d is not in this app; program 0\n", n); n = 0; d = nil; }
        prog = [NSString stringWithFormat:@"p%d", n];
        NSString *lp = [doc stringByAppendingPathComponent:@"launch_param.bin"];
        if (d.length > 4) { [[d subdataWithRange:NSMakeRange(4, d.length - 4)] writeToFile:lp atomically:YES]; setenv("DM_LAUNCH_PARAM", lp.fileSystemRepresentation, 1); }
        else { [fm removeItemAtPath:lp error:nil]; unsetenv("DM_LAUNCH_PARAM"); }
        setenv("DM_PROGRAM", [NSString stringWithFormat:@"%d", n].UTF8String, 1);
        setenv("DM_EMBED_TAG", prog.UTF8String, 1);
        setenv("DM_EXEC_OUT", rec.fileSystemRepresentation, 0);
        dmdir = [dmdir stringByAppendingPathComponent:prog]; msl = [msl stringByAppendingPathComponent:prog];
        setenv("DM_APP_DM", dmdir.fileSystemRepresentation, 1);   // the launcher's settings (Launcher.swift)
        fprintf(stderr, "boot: program %d of %s (%zu bytes of launch parameter)\n", n, [[plist stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet] stringByReplacingOccurrencesOfString:@"\n" withString:@" "].UTF8String, d.length > 4 ? (size_t)d.length - 4 : 0);
    }
    NSString *romfs = romfs_locate(prog);
    if (!romfs) return nil;
    fprintf(stderr, "boot: game files %s\n", romfs.fileSystemRepresentation);
    setenv("DM_ROMFS", romfs.fileSystemRepresentation, 0);
    setenv("DM_NVN_SHMAP", [dmdir stringByAppendingPathComponent:@"shader_map.tsv"].fileSystemRepresentation, 0);
    setenv("DM_MSL_DIR", msl.fileSystemRepresentation, 0);
    return dmdir;
}
static int g_boot_ok;

static void *boot_thread(void *p) {
    (void)p;
    static char alt[65536]; stack_t ss = {.ss_sp = alt, .ss_size = sizeof alt}; sigaltstack(&ss, 0);   // crash printing must work on the host thread too
    int rc = dm_main(g_argc, g_argv);
    fprintf(stderr, "boot: dm_main returned %d\n", rc);
    return NULL;
}
static void boot_start(void) {
    pthread_attr_t a; pthread_attr_init(&a); pthread_attr_setstacksize(&a, 64u << 20);   // the Switch main thread stack is set by npdm; the host gives plenty
    pthread_t t; pthread_create(&t, &a, boot_thread, NULL); pthread_detach(t);
}
void front_ios_boot(void) {   // Called by front_ios.m once the window and Metal view exist
    static int once; if (once++ || !g_boot_ok) return;
    void front_ios_audio_session(void); front_ios_audio_session();   // set a short I/O buffer before the game opens audio output (front_ios.m)
#ifdef DM_SHELL
    // Shaders can be in the package as source when the computer that packed it had no Apple shader compiler: compile them all once
    // here, with a progress page, then start the host. Returns 0 when there is nothing to do (no source shaders, or done last time).
    if (dm_shader_prep_begin(^{ boot_start(); })) return;
#endif
    boot_start();
}
int front_ios_boot_ok(void) { return g_boot_ok; }

@interface App : UIResponder <UIApplicationDelegate>
@end
@implementation App
- (UISceneConfiguration *)application:(UIApplication *)a configurationForConnectingSceneSession:(UISceneSession *)s options:(UISceneConnectionOptions *)o {
    return [[UISceneConfiguration alloc] initWithName:[s.role isEqualToString:UIWindowSceneSessionRoleExternalDisplayNonInteractive] ? @"TV" : @"Default" sessionRole:s.role];   // a TV (front_ios.m DmTVSceneDelegate)
}
@end

// Scene lifecycle: create the window + hand focus events to front_ios.m
@interface SceneDelegate : UIResponder <UIWindowSceneDelegate>
@property (strong, nonatomic) UIWindow *window;
@end
@implementation SceneDelegate
- (void)scene:(UIScene *)scene willConnectToSession:(UISceneSession *)session options:(UISceneConnectionOptions *)o {
    self.window = [[UIWindow alloc] initWithWindowScene:(UIWindowScene *)scene];
    self.window.rootViewController = front_ios_root();
    [self.window makeKeyAndVisible];
}
- (void)sceneWillResignActive:(UIScene *)s { front_ios_focus_event(0); }
- (void)sceneDidEnterBackground:(UIScene *)s { front_ios_focus_event(1); }
- (void)sceneWillEnterForeground:(UIScene *)s { front_ios_focus_event(2); }
- (void)sceneDidBecomeActive:(UIScene *)s { front_ios_focus_event(3); }
@end

int main(int argc, char **argv) {
    @autoreleasepool { g_boot_ok = boot_env(); }
    @autoreleasepool { return UIApplicationMain(argc, argv, nil, NSStringFromClass(App.class)); }
}
