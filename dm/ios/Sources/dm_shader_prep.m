// 壳专用（不含游戏内容的公开版安装包，tools/shell_build.sh）：把包里以**源码**形式放的着色器一次编完，带进度页。
// 内测 / 自己用 Xcode 构建的包不编这个文件的内容。
// 为什么要有：使用者的电脑上没有苹果的着色器编译器时，着色器是以源码放进安装包的（tools/d2rm_pack.py、gfx/tools/nometal_run.py），
// 设备上用到哪个现编哪个。真机实测：平均约 80 毫秒一个、单个最长约 450 毫秒；边玩边编的话第一次进一个新场景会顿约 0.8 秒
// （编好的库同一处是 0.12 秒）。所以改成进主菜单之前一次编完——系统会把编好的结果缓存，之后游戏里再建同一个库不花时间。
// 库放在哪：库模式（一个 App 装多个游戏）把每个游戏的库放在 Documents/ns2ios/games/<tid>/msl，宿主通过 DM_MSL_DIR 拿到；
// 老的单游戏包才在 App 包里。而且上游把库改成了单个 b-<链号>.img（dm/tools/shader_lib.py），不再是一目录 *.metallib——
// 两种形态都要认，否则这个页面在库模式下根本找不到东西（2026-11-08 查出来的：页面对新形态静默跳过）。
// 做完的记号放在 Library/Caches（和系统的着色器缓存在同一处，系统清缓存时一起没，下次启动自动重做一遍）。
// 中途切到后台 / 被杀 / 点「现在进游戏」：已编的在系统缓存里，回来从头数一遍，已编的每个只要不到 1 毫秒，等于接着做。
// Shell only (the public install package with no game content, tools/shell_build.sh): compile every shader the package holds as
// source in one go, with a progress page. Beta packages / packages you build yourself with Xcode do not compile this file.
// Why it exists: when the user's computer has no Apple shader compiler, the shaders are put into the install package as source
// (tools/d2rm_pack.py, gfx/tools/nometal_run.py), and each is compiled on the device when it is first used. Measured on a real
// device: about 80 ms each, 450 ms for the longest; compiling while playing stutters for about 0.8 s the first time a scene is
// entered (0.12 s at the same spot with compiled libraries). So everything is compiled once before the main menu -- the system
// caches the results, and building the same library again inside the game then costs nothing.
// Where the library is: an app holding several games keeps each game's under Documents/ns2ios/games/<tid>/msl, which the host gets
// as DM_MSL_DIR; only the old one-game package keeps it in the app bundle. And upstream made the library one file, b-<chain>.img
// (dm/tools/shader_lib.py), no longer a folder of *.metallib -- both shapes have to be read, or this page finds nothing at all in
// library mode (2026-11-08: it skipped silently on the new shape).
// The completion marker is placed in Library/Caches (the same place as the system's shader cache, so it goes away with it and the
// next launch redoes the step once).
// Sent to the background / killed / "play now": what was compiled is in the system cache; on return it counts from the start, each
// already compiled one taking under 1 ms, which amounts to resuming.
#ifdef DM_SHELL
#import <Metal/Metal.h>
#import <UIKit/UIKit.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dm_shader_learn.h"
#include "msl_image.h"   // romfs_image.h: the shader library as one image, read in place (dm/tools/shader_lib.py)

static void pl(NSString *s) { fprintf(stderr, "[dm] %s\n", s.UTF8String); }
static int lg(void) { NSString *l = NSLocale.preferredLanguages.firstObject; return [l hasPrefix:@"zh"] ? 1 : [l hasPrefix:@"es"] ? 2 : 0; }   // 系统首选语言：1 中文 / 2 西语 / 0 其余英文 | System preferred language: 1 Chinese / 2 Spanish / 0 English otherwise
#define L3(zh, en, es) (lg() == 1 ? (zh) : lg() == 2 ? (es) : (en))
// 库目录：库模式下由宿主环境指定（main.m 按选中的游戏设），老的单游戏包才回落到 App 包里的 msl/
// The library folder: library mode names it in the environment (main.m sets it for the picked game); the old one-game package
// falls back to msl/ inside the app.
static NSString *msldir(void) {
    const char *e = getenv("DM_MSL_DIR");
    if (e && *e) return @(e);
    return [NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:@"msl"];
}
// 一个待编的着色器：真实的文件，或者镜像里的一个条目（path 为 nil 时用 img 的 fd + 偏移读）
// One shader to compile: a real file, or an entry inside the image (path nil: read through the image's fd at off/size).
@interface DmSrc : NSObject
@property (nonatomic, copy) NSString *name;
@property (nonatomic, copy) NSString *path;
@property (nonatomic, assign) unsigned long long off, size;
@end
@implementation DmSrc
@end

static romfs_img *g_img;   // 镜像打开一次，整个页面期间所有条目共用 | opened once, shared by every entry for the life of the page
static NSString *g_imgpath;

// 包里以源码形式放的着色器。三种来源：镜像里非 MTLB 的 *.metallib（当前形态）、目录里非 MTLB 的 *.metallib（老形态），
// 以及 GL / Vulkan 包 msl/gl/src、msl/vk/src 下的 *.metal（那边一直就是源码文件）。
// The shaders a package holds as source: the image's *.metallib that are not MTLB (the current shape), a folder's (the old one),
// and the *.metal under msl/gl/src and msl/vk/src for GL and Vulkan packages (those are source files to begin with).
static NSArray<DmSrc *> *shader_sources(void) {
    NSMutableArray<DmSrc *> *a = [NSMutableArray array];
    NSString *msl = msldir();
    NSFileManager *fm = NSFileManager.defaultManager;
    // 1) 镜像：<msl>/b-<链号>.img
    NSString *img = nil;
    for (NSString *f in [fm contentsOfDirectoryAtPath:msl error:nil] ?: @[])
        if ([f hasPrefix:@"b-"] && [f hasSuffix:@".img"]) { img = [msl stringByAppendingPathComponent:f]; break; }
    if (img) {
        g_imgpath = img;
        g_img = romfs_image_open(img.fileSystemRepresentation);
        if (!g_img) pl([NSString stringWithFormat:@"着色器准备：【红】打不开着色器库镜像 %@", img.lastPathComponent]);
        if (g_img) {
            for (uint64_t k = 1; k < g_img->n; k++) {
                const img_ent *x = &g_img->e[k];
                if (!x->type || !x->nlen || x->nlen < 11) continue;                       // type 1 = file
                const char *nm = g_img->names + x->name; size_t n = x->nlen;
                // ".metallib" 是 9 个字符：写 10 的话 memcmp 从 "b.metallib" 起比，永远不等于，于是整个镜像一个都挑不出来
                // （2026-11-08 真机日志：49144 条目的镜像「源码着色器 0 个」）。
                // ".metallib" is nine characters: reading ten compares from "b.metallib" and never matches, which is how an
                // image of 49,144 entries reported zero source libraries on the device.
                if (memcmp(nm + n - 9, ".metallib", 9) != 0) continue;
                char h[4];
                if (pread(g_img->fd, h, 4, (off_t)x->a) != 4) continue;
                if (memcmp(h, "MTLB", 4) == 0) continue;                                  // already compiled
                DmSrc *s = [DmSrc new];
                s.name = [[NSString alloc] initWithBytes:nm length:n encoding:NSUTF8StringEncoding] ?: @"?";
                s.off = x->a; s.size = x->b;
                [a addObject:s];
            }
        }
    }
    // 2) 老形态：<msl>/b-<链号>/*.metallib
    for (NSString *d in [fm contentsOfDirectoryAtPath:msl error:nil] ?: @[]) {
        if (![d hasPrefix:@"b-"] || [d hasSuffix:@".img"]) continue;
        NSString *dir = [msl stringByAppendingPathComponent:d];
        BOOL isdir = NO; if (![fm fileExistsAtPath:dir isDirectory:&isdir] || !isdir) continue;
        for (NSString *f in [[fm contentsOfDirectoryAtPath:dir error:nil] sortedArrayUsingSelector:@selector(compare:)] ?: @[]) {
            if (![f hasSuffix:@".metallib"]) continue;
            NSString *p = [dir stringByAppendingPathComponent:f];
            FILE *fp = fopen(p.fileSystemRepresentation, "rb"); if (!fp) continue;
            char h[4] = {0}; size_t got = fread(h, 1, 4, fp); fclose(fp);
            if (got != 4 || memcmp(h, "MTLB", 4) == 0) continue;
            DmSrc *s = [DmSrc new]; s.name = f; s.path = p;
            s.size = [[NSFileManager.defaultManager attributesOfItemAtPath:p error:nil] fileSize];
            [a addObject:s];
        }
    }
    // 3) GL / Vulkan：源码目录
    for (NSString *sub in @[@"gl/src", @"vk/src"]) {
        NSString *gs = [msl stringByAppendingPathComponent:sub];
        for (NSString *f in [fm contentsOfDirectoryAtPath:gs error:nil] ?: @[]) {
            if (![f hasSuffix:@".metal"]) continue;
            DmSrc *s = [DmSrc new]; s.name = f; s.path = [gs stringByAppendingPathComponent:f];
            s.size = [[NSFileManager.defaultManager attributesOfItemAtPath:s.path error:nil] fileSize];
            [a addObject:s];
        }
    }
    return a;
}
static NSString *text_of(DmSrc *s) {
    if (s.path) return [NSString stringWithContentsOfFile:s.path encoding:NSUTF8StringEncoding error:nil];
    if (!g_img || !s.size) return nil;
    NSMutableData *d = [NSMutableData dataWithLength:(NSUInteger)s.size];
    ssize_t got = pread(g_img->fd, d.mutableBytes, (size_t)s.size, (off_t)s.off);
    if (got != (ssize_t)s.size) return nil;
    return [[NSString alloc] initWithData:d encoding:NSUTF8StringEncoding];
}
static NSString *marker(NSArray<DmSrc *> *src) {
    // The key has to name this package's shaders, not just the source tree: the chain version (b-<V>) follows the source tree and
    // is the same for every game, so a marker keyed by it alone says "done" for a different game's package, or for a repack whose
    // shaders changed -- and the app then loads source-form .metallib files as if they were compiled, which Metal rejects as
    // "Invalid library file" and the game runs on with no picture at all. What distinguishes one package from another is the
    // library itself: the image's name and size (or, for a folder, the folder's inputs.tsv stamp) plus the count and bytes here.
    NSString *tag = @"dir";
    if (g_imgpath) {
        tag = [g_imgpath.lastPathComponent stringByAppendingFormat:@"-%llu", [[NSFileManager.defaultManager attributesOfItemAtPath:g_imgpath error:nil] fileSize]];
    } else {
        NSString *dir = msldir();
        for (NSString *d in [NSFileManager.defaultManager contentsOfDirectoryAtPath:dir error:nil] ?: @[])
            if ([d hasPrefix:@"b-"] && ![d hasSuffix:@".img"]) { tag = d; break; }
        NSString *stamp = [NSString stringWithContentsOfFile:[msldir() stringByAppendingPathComponent:@"gl/inputs.tsv"] encoding:NSUTF8StringEncoding error:nil];
        for (NSString *ln in [stamp componentsSeparatedByString:@"\n"]) {
            NSArray *c = [ln componentsSeparatedByString:@"\t"];
            if (c.count == 2 && ([c[0] isEqual:@"inputs"] || [c[0] isEqual:@"game"])) tag = [tag stringByAppendingFormat:@"-%@", c[1]];
        }
    }
    unsigned long long n = 0, bytes = 0;
    for (DmSrc *s in src) { n++; bytes += s.size; }
    if (n) tag = [tag stringByAppendingFormat:@"-%llux%llu", n, bytes];
    NSString *c = NSSearchPathForDirectoriesInDomains(NSCachesDirectory, NSUserDomainMask, YES).firstObject;
    long v = NSProcessInfo.processInfo.operatingSystemVersion.majorVersion * 100 + NSProcessInfo.processInfo.operatingSystemVersion.minorVersion;
    return [c stringByAppendingPathComponent:[NSString stringWithFormat:@"dm_shader_prep_%@_%ld.done", tag, v]];
}
// 要不要做：返回 1 = 已接手（显示进度页，做完调 done）；0 = 不用做（没源码着色器、上次已做完、或用户在设置里选了边玩边编）
// Whether it needs to run: 1 = taken over (the page shows, done is called when it ends); 0 = nothing to do (no source shaders, done
// last time, or the player chose "compile while playing" in the settings).
int dm_shader_prep_begin(void (^done)(void)) {
    { const char *e = getenv("DM_SHADER_PREP");
      if (e && (!strcmp(e, "0") || !strcasecmp(e, "no") || !strcasecmp(e, "off"))) {
          pl(@"着色器准备：设置里选了「边玩边编」，跳过（游戏里用到哪个编哪个）");
          return 0;
      } }
    NSMutableArray<DmSrc *> *src = [shader_sources() mutableCopy];
    // 每一步都要留一行：这个页面以前的三条「跳过」都是静默的，结果整包在边玩边编而没人知道（2026-11-08 查了两轮）
    // Every skip says why in one line: all three used to be silent, which is how a whole package came to compile on demand
    // without anyone noticing (2026-11-08, two rounds to find it).
    pl([NSString stringWithFormat:@"着色器准备：DM_SHADER_PREP=%s，库 %@%@，源码着色器 %d 个",
        getenv("DM_SHADER_PREP") ?: "(unset)", msldir(),
        g_imgpath ? [@"/" stringByAppendingFormat:@"%@（%llu 条目）", g_imgpath.lastPathComponent, g_img ? g_img->n : 0] : @"（目录形态）", (int)src.count]);
    if (!src.count) { pl(@"着色器准备：这个包里没有以源码形式放的着色器，跳过（库已是编好的库，或路径不对）"); return 0; }
    NSString *mk = marker(src);
    if ([NSFileManager.defaultManager fileExistsAtPath:mk]) { pl([NSString stringWithFormat:@"着色器准备：上次已经编完，跳过（%@）", mk.lastPathComponent]); return 0; }
    UIWindow *win = nil;
    for (UIScene *s in UIApplication.sharedApplication.connectedScenes) if ([s isKindOfClass:UIWindowScene.class]) for (UIWindow *w in ((UIWindowScene *)s).windows) if (!win || w.isKeyWindow) win = w;
    UIView *host = win.rootViewController.view ?: win; if (!host) { pl(@"【红】着色器准备：没有窗口可放进度页，跳过（进游戏后会边玩边编，第一次进新场景会顿）"); return 0; }
    UIView *v = [[UIView alloc] initWithFrame:host.bounds]; v.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight; v.backgroundColor = UIColor.blackColor;
    UILabel *title = [UILabel new], *sub = [UILabel new], *why = [UILabel new], *modes = [UILabel new], *hint = [UILabel new];
    UIProgressView *bar = [[UIProgressView alloc] initWithProgressViewStyle:UIProgressViewStyleDefault];
    UIButton *play = [UIButton buttonWithType:UIButtonTypeSystem];
    for (UILabel *l in @[title, sub, why, modes, hint]) { l.textColor = [UIColor colorWithWhite:0.85 alpha:1]; l.textAlignment = NSTextAlignmentCenter; l.numberOfLines = 0; l.translatesAutoresizingMaskIntoConstraints = NO; [v addSubview:l]; }
    title.font = [UIFont systemFontOfSize:20 weight:UIFontWeightSemibold];
    sub.font = [UIFont monospacedDigitSystemFontOfSize:15 weight:UIFontWeightRegular];
    why.font = [UIFont systemFontOfSize:13]; modes.font = [UIFont systemFontOfSize:13]; hint.font = [UIFont systemFontOfSize:12];
    why.textColor = [UIColor colorWithWhite:0.72 alpha:1]; modes.textColor = [UIColor colorWithWhite:0.72 alpha:1]; hint.textColor = [UIColor colorWithWhite:0.55 alpha:1];
    bar.translatesAutoresizingMaskIntoConstraints = NO; bar.progressTintColor = [UIColor colorWithRed:0.78 green:0.36 blue:0.16 alpha:1]; [v addSubview:bar];
    play.translatesAutoresizingMaskIntoConstraints = NO; play.titleLabel.font = [UIFont systemFontOfSize:15 weight:UIFontWeightSemibold];
    [play setTitleColor:UIColor.whiteColor forState:UIControlStateNormal];
    play.backgroundColor = [UIColor colorWithWhite:1 alpha:0.14]; play.layer.cornerRadius = 22; play.contentEdgeInsets = UIEdgeInsetsMake(10, 22, 10, 22);
    [v addSubview:play];
    title.text = L3(@"首次启动：正在准备着色器", @"First launch: preparing shaders", @"Primer inicio: preparando los shaders");
    why.text = L3(@"这些着色器本来要在游戏用到时才编：第一次进一个新场景会顿一下（约 0.8 秒）。\n现在一次编完，进游戏后就不会因为着色器卡顿了。",
                  @"These shaders would otherwise be compiled the first time the game uses them, which costs a stutter of about 0.8 s\non entering a scene you have not seen. Compiled now, the game does not stutter over them at all.",
                  @"Sin esto, los shaders se compilan la primera vez que el juego los usa, y eso cuesta un tirón de unos 0.8 s\nal entrar en una escena nueva. Compilados ahora, el juego no vuelve a tartamudear por ellos.");
    modes.text = L3(@"也可以立刻进游戏边玩边编，不玩的时候再回到这里继续编。",
                    @"Or start playing right now and compile as you go, and come back here when you are not playing to finish.",
                    @"O empieza a jugar ahora y compila mientras juegas; vuelve aquí cuando no estés jugando para terminarlo.");
    hint.text = L3(@"无论现在编还是边玩边编，编好的都会记住，可以随时游玩或离开游戏。",
                   @"Either way, what is compiled is remembered: play or leave whenever you like.",
                   @"En cualquier caso, lo compilado se recuerda: puedes jugar o salir cuando quieras.");
    [play setTitle:L3(@"现在进游戏（边玩边编）", @"Play now (compile while playing)", @"Jugar ahora (compilar al jugar)") forState:UIControlStateNormal];
    __block _Atomic int stop = 0;
    [play addAction:[UIAction actionWithHandler:^(UIAction *a) {
        (void)a; atomic_store(&stop, 1);
        pl(@"着色器准备：玩家选择现在进游戏，剩下的边玩边编");
    }] forControlEvents:UIControlEventTouchUpInside];
    [NSLayoutConstraint activateConstraints:@[
        [title.centerXAnchor constraintEqualToAnchor:v.centerXAnchor], [title.bottomAnchor constraintEqualToAnchor:bar.topAnchor constant:-22],
        [bar.centerXAnchor constraintEqualToAnchor:v.centerXAnchor], [bar.centerYAnchor constraintEqualToAnchor:v.centerYAnchor constant:-64], [bar.widthAnchor constraintEqualToAnchor:v.widthAnchor multiplier:0.46],
        [sub.centerXAnchor constraintEqualToAnchor:v.centerXAnchor], [sub.topAnchor constraintEqualToAnchor:bar.bottomAnchor constant:12],
        [why.centerXAnchor constraintEqualToAnchor:v.centerXAnchor], [why.topAnchor constraintEqualToAnchor:sub.bottomAnchor constant:20], [why.widthAnchor constraintLessThanOrEqualToAnchor:v.widthAnchor multiplier:0.78],
        [modes.centerXAnchor constraintEqualToAnchor:v.centerXAnchor], [modes.topAnchor constraintEqualToAnchor:why.bottomAnchor constant:10], [modes.widthAnchor constraintLessThanOrEqualToAnchor:v.widthAnchor multiplier:0.78],
        [play.centerXAnchor constraintEqualToAnchor:v.centerXAnchor], [play.topAnchor constraintEqualToAnchor:modes.bottomAnchor constant:18],
        [hint.centerXAnchor constraintEqualToAnchor:v.centerXAnchor], [hint.topAnchor constraintEqualToAnchor:play.bottomAnchor constant:14], [hint.widthAnchor constraintLessThanOrEqualToAnchor:v.widthAnchor multiplier:0.78]]];
    [host addSubview:v];
    UIApplication.sharedApplication.idleTimerDisabled = YES;   // 这几分钟别自动锁屏 | do not auto-lock the screen during these few minutes
    int total = (int)src.count, threads = (int)MAX(2, MIN(6, (NSInteger)NSProcessInfo.processInfo.activeProcessorCount - 2));
    const char *te = getenv("DM_SHADER_PREP_THREADS"); if (te && atoi(te) > 0) threads = atoi(te);   // 测试开关：指定并行数 | test switch: set the degree of parallelism
    pl([NSString stringWithFormat:@"着色器准备：开始，共 %d 个，%d 线程，库 %@", total, threads, g_imgpath ? g_imgpath.lastPathComponent : msldir()]);
    CFAbsoluteTime t0 = CFAbsoluteTimeGetCurrent(); __block _Atomic int fin = 0, bad = 0;
    void (^tick)(void) = ^{ int n = atomic_load(&fin); double el = CFAbsoluteTimeGetCurrent() - t0, left = n > 20 ? el / n * (total - n) : -1;
        bar.progress = (float)n / total;
        NSString *eta = left < 0 ? L3(@"正在估算剩余时间", @"estimating time left", @"calculando el tiempo restante") : left >= 90 ? [NSString stringWithFormat:L3(@"预计还要 %.0f 分钟", @"about %.0f min left", @"faltan unos %.0f min"), ceil(left / 60)] : [NSString stringWithFormat:L3(@"预计还要 %.0f 秒", @"about %.0f s left", @"faltan unos %.0f s"), ceil(left / 5) * 5];
        sub.text = [NSString stringWithFormat:@"%d / %d · %@", n, total, eta]; };
    tick();
    NSTimer *tm = [NSTimer scheduledTimerWithTimeInterval:0.5 repeats:YES block:^(NSTimer *t) { tick(); }];
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice(); dispatch_semaphore_t sem = dispatch_semaphore_create(threads); dispatch_group_t g = dispatch_group_create();
        for (DmSrc *s in src) {
            if (atomic_load(&stop)) break;
            dispatch_semaphore_wait(sem, DISPATCH_TIME_FOREVER);
            dispatch_group_async(g, dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{ @autoreleasepool {
                if (atomic_load(&stop)) { dispatch_semaphore_signal(sem); return; }
                NSString *txt = text_of(s); NSError *e = nil;
                id<MTLLibrary> l = txt ? [dev newLibraryWithSource:txt options:nil error:&e] : nil;   // 与游戏里建库用的是同一个调用、同样的参数：缓存才对得上 | the same call the game makes: only then does the cache match
                if (!l && atomic_fetch_add(&bad, 1) < 5) pl([NSString stringWithFormat:@"【红】着色器准备：%@ 编不过：%@", s.name, e.localizedDescription ?: @"读不了"]);
                // 这个着色器以前被拒过管线：把它「输出声明成整数」的版本也编一遍（同一个调用、同样的文本，游戏里建库时缓存直接命中，不再卡）
                // This shader had a pipeline refused before, so the version with those outputs declared integer is compiled here too:
                // the game then builds that library out of the cache instead of stalling the first time it is drawn.
                unsigned lm = l ? dm_shader_learn_mask(s.name.stringByDeletingPathExtension.UTF8String) : 0;
                if (lm && txt) {
                    const char *cs = txt.UTF8String; size_t on = 0;
                    char *w = dm_shader_learn_rewrite(cs, strlen(cs), lm, &on);
                    NSString *t2 = w ? [[NSString alloc] initWithBytes:w length:on encoding:NSUTF8StringEncoding] : nil;
                    free(w);
                    NSError *e2 = nil; id<MTLLibrary> l2 = t2 ? [dev newLibraryWithSource:t2 options:nil error:&e2] : nil;
                    if (!l2 && atomic_fetch_add(&bad, 1) < 5) pl([NSString stringWithFormat:@"【红】着色器准备：%@ 的整数输出版本编不过：%@", s.name, e2.localizedDescription ?: @"改写没做成"]);
                }
                int n = atomic_fetch_add(&fin, 1) + 1; if (n % 500 == 0) pl([NSString stringWithFormat:@"着色器准备：%d / %d，已用 %.0f 秒，温控档位 %ld", n, total, CFAbsoluteTimeGetCurrent() - t0, (long)NSProcessInfo.processInfo.thermalState]);
                dispatch_semaphore_signal(sem); } });
        }
        dispatch_group_wait(g, DISPATCH_TIME_FOREVER);
        int cancelled = atomic_load(&stop);
        double el = CFAbsoluteTimeGetCurrent() - t0; int nb = atomic_load(&bad), done_n = atomic_load(&fin);
        pl([NSString stringWithFormat:@"着色器准备：%@ %d 个（共 %d），编不过 %d 个，用时 %.1f 秒，%d 线程，结束时温控档位 %ld",
            cancelled ? @"中途进游戏，已编" : @"完成", done_n, total, nb, el, threads, (long)NSProcessInfo.processInfo.thermalState]);
        // 有编不过的、或玩家中途进了游戏，都不记完成：下次启动再过一遍（已编的很快）
        // Neither a failure nor a player who chose to play now records completion: the next launch runs it again (the already
        // compiled ones are fast).
        if (!nb && !cancelled) [@"ok" writeToFile:mk atomically:YES encoding:NSUTF8StringEncoding error:nil];
        dispatch_async(dispatch_get_main_queue(), ^{ [tm invalidate]; [v removeFromSuperview]; UIApplication.sharedApplication.idleTimerDisabled = NO; done(); });
    });
    return 1;
}
#endif
