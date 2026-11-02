# 壳（shell）拆分方案

上游的常规路线在一台 Mac 上做完全部工作：`scripts/build_ipa_from_nsp.py` 改写游戏程序、翻译着色器、用苹果的 `metal`
编译器编译、再让 `xcodebuild` 链接出整个 app。没有 Mac 的用户这些一件都做不了。

这条路线把它拆成两半：

| | 在哪 | 做什么 |
|---|---|---|
| **1. 壳**（一次） | Mac 或 macOS CI runner | `dm/ios/tools/shell_build.sh` 编译出**完全不含游戏内容**的 app，产出几 MB 的构建原料 |
| **2. 打包**（每游戏） | Windows / Linux / macOS | `scripts/nxn_pack.py` 拿用户自己的 NSP 和 `prod.keys`，改写程序、翻译着色器、写进壳里，产出 IPA |

任何游戏内容都不会进入本仓库或 CI。壳是游戏无关的——这是最麻烦的地方，也是整件事的重点。

## 一个壳为什么能服务所有游戏

上游 nxnative 把游戏相关数据编进可执行文件，这样出来的壳只能对应编它的那个游戏。壳模式（`-DDM_SHELL`）把每一项都挪到运行期：

| 平时是编译进去的 | 壳模式下 |
|---|---|
| `gen_embed.py` 把改写后的模块映像编进 `__TEXT` / `__DM*W` 段（`host/loader.c`） | 从 `Frameworks/Guest.framework/Guest` 取——一个只装段的动态库，由 `dm/tools/guest_dylib.py` 用纯标准库写出，任何系统产出的字节都一致 |
| profile 的 `DM_GAME_MODULES`（`host/main.c:94`，只在 argv 没给模块名时使用） | 从 Guest 库的 `__dm<name>x0` 节名反推（`dm_shell_modules`），在 `main.m` 里追加进 `argv` |
| profile 的 `dmsh_chain.h` 里的 `DMSH_CHAIN` | 从 `msl/` 下唯一那个库目录名（`b-<版本>`）解析——链版本哈希了游戏的着色器表，每个游戏都不同 |
| `xcrun metal` 编出的 `.metallib` | 不以 `MTLB` 开头的文件按 MSL 源码处理，在设备上现编，之后由系统缓存 |
| 按 `romfs_marker` 定位 RomFS | 任何存在且非空的 RomFS 目录都接受 |
| profile 的内存布局（`mem_used`、`app_pool`、`mem_alias`…） | 启动时从环境变量读；打包器把游戏真实值写进 app 的 `dm/env.txt` |
| profile 里其余这些开关，宿主原先只读编译期常量（`tex_flush_upload`、`tex_dirty_upload`、`render_cond`、`alloc_big_align`、`tex_dyn_slots`、`perf_mode`、`mem_map_limit`、`game_args`、`pad_layout`） | 每个宿主读取点先问环境变量（`DM_TEX_FLUSH_UPLOAD`、`DM_RENDER_COND`、`DM_PERF_BOOST`、`DM_GAME_ARGS`…），没有再退回常量——占位 profile 里它们是关的（只有 `pad_layout full`，那是壳自己的默认）；打包器按该读取点的解析方式写值（`atoi`，所以 profile 写的 `on`、`boost` 要变成 `1`） |
| — | `dm/ios/Sources/dm_shader_prep.m`：首启把全部源码形态的着色器编一遍，带进度页 |

改写表和着色器表不用动：在 nxnative 里它们本来就是普通资源（`Gen/dm`，`ios/project.yml` 里是资源目录），运行时读取，
不是编进代码的数组。这也是为什么这个壳**构建时不需要游戏文件**，而 d2r-mobile 的 `shell_build.sh` 需要。

内存布局这一项不是显而易见的，花了真机一个来回：用占位 profile 的默认 `mem_used`（64 MB）而不是 Hades II 的 108 MB 时，
游戏按 `池 − 已用` 申请堆，多要了 44 MB，撑爆池子，在 `SetHeapSize` 里 abort。

## 构建

本机 Mac + Xcode 26：

```
bash dm/ios/tools/shell_build.sh
```

或者走 CI：**Build shell** workflow。手动触发用本分支的树；每日定时任务用上游最新提交 + 覆盖本分支的文件
（`.shell-overlay.txt`）。两者都会发布 release。构建末尾会核对 app 里没有 `romfs/`、`msl/`、`aoc/`、没有模块映像节、
`dm/font` 之外没有超过 8 MB 的文件。

流水线是 `tools → shell → smoke → release`，冒烟每次构建都做：它从私有仓库取一个小游戏的 NSP 和 `prod.keys`，用刚构建
出来的壳和工具真打一次包，再检查产物（可执行位、着色器库数量、RomFS、`env.txt` 里的内存布局、壳的 gate 声明、显示名）。
这是唯一能发现「上游改了接口、壳和打包器对不上」的环节，所以不做就不发。

两种失败要分开：

| 情况 | 处理 |
|---|---|
| 取不到测试数据（没配 token、token 过期、资产没了） | **照常发布**，release 顶部写明「未做冒烟测试」。这不是壳有问题，不能因为它就不出包 |
| 冒烟真的打不出包，或产物检查不过 | **不发布**。这基本就是壳和打包器对不上了，值得拦 |

## 跟上上游

每天北京时间早 8 点（00:00 UTC）的定时任务**只在有更新时**构建：先比上游最新提交与最近一次 release 的版本号，
一致就直接跳过，工作流几秒结束。上游支持了新游戏，这里下载到的壳跟着就能玩——不需要谁去手动跟进。

本仓库不是上游的克隆，只带差异，分两类：

| 文件 | 含义 |
|---|---|
| `.shell-overlay.txt` 里带 `+` 的行 | 本仓库**新增**的文件，整份复制到上游树上 |
| `.shell-patches/*.patch` | 本仓库对上游**已有**文件的修改；以补丁方式 `git apply` 到上游树上 |
| `.shell-base.txt` | 补丁基于哪个上游提交生成 |

补丁而不是整文件覆盖，是为了让上游对同一个文件其他位置的改动自动保留下来。于是**唯一会冲突的情况**是：上游改了与补丁同一段代码。

### 冲突怎么处理

症状：`Shell` 任务在 `Assemble the source tree` 失败，日志 `xxx.patch does not apply`。此时流水线停下，不发布——不会出现「壳和上游对不上却已经上线」的情况。

1. `git clone --depth 1 https://github.com/seedds/nxnative up`
2. `cd up && git apply --reject <本仓库>/.shell-patches/xxx.patch`，看产生了哪些 `.rej`
3. 打开 `.rej` 与上游那段新代码，**读懂之后**把改动重新落上去；直接粘旧文本通常不对
4. 重新生成补丁并更新基准：
   ```
   python dm/ios/tools/make_shell_patch.py dm/xxx/yyy.py     # 一个文件；--all 重生成全部 "~" 条目
   ```
   它按 `.shell-base.txt` 里的提交取上游原文做 diff，并把两边的 CRLF 归一（补丁是打到上游的 LF 文件上的）。
   改完基准提交后，先 `git -C up apply --check <.shell-patches/xxx.patch>` 确认能合，再一起提交文件、补丁和 `.shell-base.txt`
5. 只有第 4 步不够用时才需要动 C：上游改掉了壳模式替换的那六处（函数签名、结构体字段、`#define` 名字）或者被读写的数据结构布局，补丁合上了也编不过。这时改 `dm/host/*.c` 等文件，再重新生成补丁

判断「要不要改 C」的一条线：只要上游没碰这六处替换点和相关结构体，补丁合上就能编过。

### 主动把基准往前推（不是等 CI 报错）

CI 只在冲突发生之后才知道。要让这棵树主动跟上上游——发布时本来也该这么做，因为 release 的《Source code》
快照应该就是壳构建时用的那棵树——按这两步走：

```
python3 dm/tools/packer_sync.py . <上游克隆>            # 结束必须是 0 file(s) differ, 0 upstream-only
```

- **跑两遍**：先 `--sync`，再不带 `--sync`，读第二份报告。还剩下的就是真漂移。
- **`(gone upstream)` 那些必须手动删。** `--sync` 只负责复制和新增，**从不删除**。上游删文件通常意味着
  它在宿主里把根因修掉了、不再需要那个绕道补丁（例如某个资源堆或电影门的按 build ID 补丁）。
  本地留着就等于「上游已经不打的补丁我们还在打」。
- `packer_sync.py` 覆盖 `dm/tools`、`dm/gfx`、`dm/games`、`dm/host` 和 `dm/third_party`。最后一个是补上的：
  CI 的 tools 任务是从**这棵树**编译 `dmsh_xlate.exe` 的，上游加 `TranslationFlags.Msl23ImageAtomics` 时，
  编译就断在那份从没被比对过的 vendored 代码上。

**验补丁要对着它将要打上去的那棵树，不要对着自己的工作副本。** `.shell-patches/*.patch` 在本地是按
CRLF 检出的（`core.autocrlf`），而上游是 LF，于是 `git apply --check` 会报一个**并不存在**的失败。
用提交里的字节：

```
git cat-file -p HEAD:.shell-patches/xxx.patch > /tmp/xxx.patch   # 或者：tr -d '\r' < xxx.patch
git -C up apply --check /tmp/xxx.patch
```


这一步完全在 CI 里完成，Windows 端看到的只是一个新的壳 zip：命令、参数、Python 依赖都不变。

## 壳与打包器的契约

两边不是同一个东西，所以有一处必须对齐：改写器（`patch_nso.py`）会往游戏模块里写入一批调用点，宿主
（`dm/host/hle.c` 的 `gate_register`）按**类型名**分派——`svc`、`cntpct`、`cntvct`、`obs`、`spin`。壳是提前编译好的，
新打包器写进去的类型它可能不认识，结果是**打包成功、装上就崩**：

```
[dm] FATAL unknown gate kind spin
[dm] bt: abort gate_register load_modules host_preload preload_thread
```

这个坑真实发生过一次：一个早期 fork 构建的壳配上当时的打包器，游戏在 `load_modules` 里 abort。

所以 `shell_build.sh` 现在把壳能做什么写进 `dm/shell_info.txt`：

```
KIND=ios
SOURCE_SHA=<构建它的提交>
GATES=svc cntpct cntvct obs spin
GUEST=1
ENV=1
```

`GATES` 不是手写的，是从 `hle.c` 里那条 strcmp 链**抓出来**的；抓不到（比如上游改了那行的写法）就构建失败，
宁可不出包也不出一份会骗打包器的标记。`GUEST`/`ENV` 同理，从 `loader.c` 与 `main.m` 的实际代码里判断。

打包器在改写之后核对本次真正用到的类型是否都在 `GATES` 里，不在就直接停，并说明该去下最新的壳。旧壳（没有这些字段）
只警告——缺字段代表「不可验证」，不代表「坏的」，而现在发布的第一批壳就属于这一类。

## Switch 2 的游戏是一个 dump 目录，不是 NSP

Switch 2 的游戏以「一堆文件」的形式流通，而不是打包成一个 NSP，而且这些文件本身没有加密——所以这条路**一个密钥都不读**，
也不需要 `-k`。文件夹里有 `base/exefs` 和 `base/romfs` 时，`nxn_pack.py` 就按 dump 走：

```
<游戏>/
  base/exefs/             main、main.npdm、sdk、rtld
  base/romfs/
  updates/458752/exefs/   更新的版本号，十进制
  updates/458752/romfs/   打好补丁的 RomFS，不是补丁
  dlc/<title ID>/0/romfs/
```

title ID 取自 `main.npdm` 里的 program ID（dump 没有 ticket 可读），版本号取自 update 文件夹的名字；update 的 RomFS
是**单独打包**的，不叠在 base 上——它本身已经含 base 加补丁，两棵树并不等价（大金刚 Bananza 的更新会丢掉 base 的 42 个文件）。
认出身份之后，后面就是同一条链：改写、拆着色器、翻译、装配。

全程不复制、也不建链接：后面各步直接读 dump 自己的目录（`ExeFS/` 与 `RomFS/` 分别对到 `exefs/`、`romfs/`），
只有装配时那份 RomFS 拷贝会进包。DLC 会复制进工作目录，因为装配是整棵拷进 App 的。

上游只把这些打进 Mac App，没在 iPhone 上跑过。这里打的是同一个链的 IPA——这个仓库愿意打它们，正是因为手机才是要验的地方。

## 工作目录与清理

打包器在游戏文件夹旁边建一个 `<title ID>_pack`，一切中间产物都在里面：

| 目录 | 是什么 | 打完包还需要吗 |
|---|---|---|
| `extract/` | 从 NSP/XCI 解出来的 ExeFS 和 RomFS | 不需要，重新解一遍即可 |
| `build/patched/` | 改写后的模块 | 不需要 |
| `shader/` | 拆出的着色器包与翻译缓存 | 不需要 |
| `stage/` | 拼装好的 app 树（含一份 RomFS 拷贝，最大的一项） | 不需要，已打进 IPA |
| `logs/` | 每一步的日志 | **留着**，几 KB，手机上出问题就看它 |

所以 IPA 写完之后默认自动删除上面这些，只留 `logs/`。这个量级值得删：`extract/` + `stage/` 各是一份游戏内容，
4 GB 的游戏跑完会留下十几 GB。`--keep-work` 保留整棵树（重打包同一个游戏时省掉解包和翻译的时间），
`--clean <游戏文件夹>` 单独清掉之前留下的。

删的是自己建的目录、逐个子项删、且每个子项都确认在 `work` 里面，所以 RomFS 里若有指向别处的符号链接也不会被带出去。

## 状态

| 部分 | 状态 |
|---|---|
| 壳模式（`-DDM_SHELL`）与壳构建 | 可用，CI 以 release 形式发布 |
| `scripts/nxn_pack.py`（打包器） | Windows 上端到端可用 |
| 真机验证 | **已证明路线可行**：壳加载了 Guest 库、发现了模块、在手机上 4.3 秒编完 Hades II 的 140 个着色器；随后因内存布局在 `SetHeapSize` abort（已修，尚未在真机复测） |

## 在 Windows 上跑通实际要修的东西

下面每一条都是「只在 macOS 跑过」的代码：

| 文件 | 问题 |
|---|---|
| `dm/gfx/tools/shader_split.py` | `os.path.relpath` 返回反斜杠，记录的 RomFS 路径不再匹配 profile 的 `shader_groups`，所有着色器落进 `other`，拆出的表自校验失败 |
| `dm/gfx/tools/dmsh_mtlcache.py` | `ctypes.CDLL(None)` 只在 POSIX 表示当前进程，Windows 上 import 就抛 `TypeError` |
| `dm/gfx/tools/dmsh_chain.py` | 单文件发布的翻译器旁边没有 `.dll` 可算指纹；可执行文件叫 `dmsh_xlate.exe`；Windows checkout 的 CRLF 让内嵌源摘要与树里的不一致 |
| `dm/gfx/tools/nometal_run.py` | 三个 `xcrun` 探测未处理（移植版对不认识的调用直接抛错）；且它每个着色器删一个临时文件，被统计删除量的环境半路打断 |
| `dm/ios/tools/pack_ipa.py` | Windows 的 `os.stat` 没有 x 位，主程序被打成 0644，装上去起不来 |
| `dm/host/hle_svc.c` | 内存布局只有编译期常量，无游戏内容的壳提供不了——现在从环境变量读（`DM_MEM_*`、`DM_APP_POOL`、`DM_MEM_MAP_LIMIT`） |

## 值得保留的自检

- `shell_build.sh` 若发现 app 里带 `romfs/`、`msl/`、`aoc/`、模块映像节，或 `dm/font` 之外有超过 8 MB 的文件，就拒绝发布。
- 打包器把 `ExeFS/main`、`sdk` 的 NSO build ID 与 profile 比对，并把拆出的着色器表与 profile 逐字节比对。版本不对时当场停下，
  而不是产出一个在手机上才炸的 IPA。
- 着色器完成度以 `dmsh_build.py` 的逐项结果为准，而不是「目录里有库文件」——否则被中断的运行会被误认为已完成。
