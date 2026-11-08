# nxnative shell —— 不需要 Mac 就能出包

在 iPhone / iPad 上玩你自己的 Switch 游戏。这是 [nxnative](https://github.com/seedds/nxnative) 的一个分支，把构建拆成两半：  
只在真正需要 Mac 的那一半在 CI 上做**一次**，而所有需要你游戏文件的操作都在**你自己机器上**完成——Windows、Linux 或  
macOS 皆可，不需要 Xcode。

|           | 在哪做        | 做什么                                                         |
| --------- | ---------- | ----------------------------------------------------------- |
| **1. 壳**  | CI 构建，下载一次 | 几 MB 的未签名 iOS 应用，**完全不含游戏内容**                               |
| **2. 打包** | 你的机器       | `scripts/nxn_pack.py` 拿你的 NSP 和 `prod.keys`，改写程序、翻译着色器、写进壳里 |

壳是**游戏无关**的：一个壳服务支持列表里的任何游戏。任何游戏文件都不会进入这个仓库或 CI。

> English documentation: [README.md](README.md)
>
> 上游项目及其原始文档（Mac 路线）：**[seedds/nxnative](https://github.com/seedds/nxnative#readme)**。

## 快速开始（Windows）

**只下载一个文件：`ns2ios-packer.exe`** —— 最新 release 的第 5 个资产。这就是全部工具。壳、五个可执行文件、打包器
源码，全部由它从**同一个 release** 里取，所以这三样永远不会版本对不上。

机器上仍然需要：**Python 3.9 或更高**（打包器是 Python 写的，exe 只是它的窗口）、你自己的游戏文件、`prod.keys`。

| 步骤 | 做什么 |
|---|---|
| **1** | 双击 `ns2ios-packer.exe`。窗口一打开，它就去查最新 release。 |
| **2** | 点「**检查更新并下载**」。壳 zip、五个工具、打包器源码落到 `ns2ios-packer-data\versions\v0.0.00xx\`。 |
| **3** | 「**Python**」那一行会自动找到你的解释器；找不到就点「浏览…」。然后点一次「**装依赖**」——只装进这个 Python。 |
| **4** | 选游戏目录（放着 NSP/XCI 的那个）、`prod.keys`、一个**输出目录**。默认的「库模式」保持勾选。 |
| **5** | 点「**开始打包**」。每一步和输出都在日志里。打完输出目录里是**两样东西**：`ns2ios\`（游戏数据，几个 GB）和 `ns2ios.ipa`（几十 MB）。 |
| **6** | 装 `ns2ios.ipa`（SideStore、[Sideloadly](#注意事项)（会丢一个权限，见下面注意事项）、LiveContainer 都行）。**首次启动会编译一遍着色器**，屏幕上有进度页。 |
| **7** | 把 `ns2ios` 整个文件夹复制进「文件」App 的**根目录**（第一次启动它会在那里建好这个同名目录）。 |

**装一次之后是这样的**：App 叫 `ns2ios`，第一次启动它会在「文件」App 里建好自己的文件夹（就叫 `ns2ios`，
和 App 同名）。游戏本体（RomFS、着色器库、DLC）全在「文件」→ `ns2ios` → `games` → `<title ID>` 里，
**换一次壳一个字节都不用动，存档也一直留在那里**。

### 加下一个游戏

库模式（默认）是「一个 App 装多个游戏」，加一个游戏是两件事：

1. **打包**：换游戏目录，再点一次「开始打包」。
2. **装到手机**：只把输出目录里的 **`ns2ios/games/<title ID>`** 这一个文件夹复制进手机上的 `ns2ios/games/` 里；
   然后把新生成的 `ns2ios.ipa` **覆盖安装一次**——App 靠这次安装才能认出新游戏，**不能省**。

⚠️ **别把整个 `ns2ios` 文件夹再拷一遍。** iOS 的「文件」App 遇到同名文件夹只给你「替换 / 保留两者」两个选择，
**「替换」是整个文件夹换掉，不是合进去**：`save/`（存档和金手指）和别的游戏会跟着一起消失。加游戏只动
`games/<title ID>` 这一层。

只想换壳、不加游戏？打包器里有「**只重打安装包**」：不重新准备游戏，几秒出一个新 `ns2ios.ipa`，覆盖安装即可。

**东西都放在哪？** 下载的一切都在 exe 旁边的 `ns2ios-packer-data\`，一个版本一个文件夹。不写注册表、不装进系统目录：
删掉这个文件夹，就等于没装过。

**支持哪些游戏**：见 [docs/SUPPORTED.md](docs/SUPPORTED.md)——每款都列了 title ID 和已验证版本，拿文件名里那串十六进制对上即可。

**喜欢命令行？** 窗口只干一件事：替 `scripts/nxn_pack.py` 拼参数，那个脚本就在工具下载的源码里。在
`ns2ios-packer-data\versions\v0.0.00xx\src\ns2ios-shell-*` 下：

```bat
python scripts\nxn_pack.py ..\..\..\shell-<日期>-<提交>.zip D:\games\hades2 ^
    -k D:\keys\prod.keys --tools ..\..\..\tools
```

`python scripts\nxn_pack.py --help` 列了全部参数；窗口把常用的那些做成勾选，每条配一句说明。界面不会做脚本做不到的事。

### 打完包之后

临时文件会**自动删掉**：中途解出来的游戏、改写后的模块、拆出的着色器、拼装好的 app 树，加起来是游戏体积的好几倍
（4 GB 的游戏会留下十几 GB），留着没用。只保留几 KB 的日志（`<title ID>_pack\logs\`），手机上有异常时看它。

- 反复给同一个游戏重打包、省掉解包和翻译的时间：加 `--keep-work`
- 单独清掉之前留下的：`python scripts\nxn_pack.py --clean D:\games\hades2`
- 卡带转储（`.xci`）第一次打包后会生成 `你的文件.xci.tid`，记住它是什么游戏，下次不用再解一遍

## 金手指（Atmosphère 格式）

启动页里有「金手指」分区，**不用装任何额外工具**：

1. **拿到金手指文本**：网上 Atmosphère 格式的 `.txt`——一般是 `[名字]` 开头，下面跟 `040A0000 地址 值` 这种行。
2. **粘贴**：启动页 → 金手指 → 展开「粘贴金手指」→ 粘进去。
3. **勾选要的条目**。勾选状态会记住；文本留空会同时删掉手机上的文件。
4. **点开始**。面板把文本写成 `Documents/cheats/cheats.txt`，并把勾选的名字交给游戏进程。

**条目分两类，面板会标出来：**

| 标注 | 含义 | iOS 上 |
|---|---|---|
| 改数值 —— 安全 | 顺着指针改游戏对象里的数值（月跳、无限回响这类） | 正常工作 |
| 改代码的条目 | 直接改游戏程序的指令 | **实测会崩**，默认关闭 |

想试「改代码」的条目：打开分区底部的**「允许改代码」**，再单独勾一条试。

**两条保护**：① 开着某条金手指 30 秒内崩过，下次打开面板会**自动关掉它并写明原因**（一次崩溃只影响一次启动，想再试手动打开即可）；② 改写器自己改过的地址会被直接拒绝——那些字是让游戏能在 iPhone 上跑起来的关键，动了必崩。

**版本要对得上**：金手指里的地址属于某个游戏版本。面板会把你粘贴文本里的 `BID:` 和本机版本比对，不一致会在底部提示。

## 环境要求

**打包机器**上需要：

| 需要什么 | 怎么弄 |
|---|---|
| 打包器 | release 里的 `Source code (zip)`——**必须有 `dm/`**，见上面的说明 |
| Python 3.9+ | `python -m pip install lz4 capstone fonttools backports.zstd pillow`。装给**运行打包器的那一个**解释器 |
| 五个可执行文件 | 来自 `nxnative-windows-tools.zip`，用 `--tools` 指过去。打包器要调的外部程序全在里面，**包括 `zstd`**，不用自己再去找 |
| `prod.keys` | 从你自己的主机导出。**永远不离开你的机器**：它只作为参数传给 `nx_extract`，不会写进任何日志 |

不需要 .NET SDK、不需要 C++ 工具链、不需要 Mac。（想自己编工具也行：`dotnet publish dm/tools/nx_extract -r win-x64 --self-contained`，`dm/gfx/tools/dmsh_xlate` 同理；SPIRV-Cross 必须用 `vulkan-sdk-1.4.357.0` 这个 tag。）

**关于 Python**：机器上可能装了不止一个 Python，`pip install` 装到哪个、`nxn_pack.py` 用哪个，必须是同一个。
最省事的写法是用同一个解释器跑两件事：

```
C:\path\to\python.exe -m pip install lz4 capstone fonttools backports.zstd pillow
C:\path\to\python.exe scripts\nxn_pack.py ...
```

打包器开跑前会自己检查这五个包，缺了会列出该执行的命令，不会跑到一半才失败。

## 文件名无所谓

打包器从 **NSP 内部的元数据**识别游戏——加密内容旁边的 `cnmt.xml` 是明文的，里面有 title ID、内容类型（本体／更新／DLC）  
和版本号。所以文件叫什么都行。

名字里的 `[XXXXXXXXXXXXX000]` 和 `[vN]` 只在文件**不含这种元数据**时才读。**XCI 卡带**里既没有 ticket，NCA 头也是
加密的（密钥在它自己的 key area 里），所以打包器第一次会先解包一遍、从解包结果里认出 title ID，并把结果记在
`<文件>.xci.tid`；之后重打包直接读这个文件，不再解一遍。

支持格式：**NSP**（本体、更新、DLC）和 **XCI**（只能当本体）。**不支持 NSZ**：先用 `nsz -D "文件.nsz"` 解压。

## 哪些游戏能打、哪个版本能打

[`dm/games/`](dm/games/) 下有 profile 的游戏都支持。**任意版本都行**，带不带更新都可以：打包器取文件夹里**最新的那个  
版本**（也可以用 `-p vN` 指定），并用该版本自己的着色器来构建。上游每款游戏只在一个版本上验证过；换成别的版本时 app  
会说明这一点，而为已验证版本做的修复（比如 60fps）不适用于它。

更新包是可选的——只有本体也能打。文件夹里的 DLC 会被一并打进去：每个 DLC 的 RomFS 解出来挂进应用，游戏看到的正是你
放进去的那几个（编号按 title ID 自动算）；没有 DLC 时游戏就看不到 DLC，和在没插 DLC 的 Switch 上一样。

## 原理

```
CI（一次）         你的机器                           iPhone
─────────          ──────────────────────────────    ────────────────────
shell_build.sh ──► nxn_pack.py
  宿主程序            ├ 解包 NSP        (nx_extract)
  无游戏内容          ├ 改写程序        (patch_nso.py)
  无着色器            ├ 拆着色器        (shader_split.py)
  无 RomFS            ├ 译成 MSL 源码   (dmsh_xlate, spirv-cross)  ──► IPA ──► 侧载
                     ├ 写 Guest.framework (guest_dylib.py)                │
                     └ 组装 + 打 IPA   (pack_ipa.py)                      ▼
                                                                    首次启动：
                                                                    编译着色器
```

让一个壳通吃所有游戏的关键，是把两件事从编译期挪到运行期：

- **模块列表**从 `Frameworks/Guest.framework` 的 `__dm<name>x0` 节名反推（游戏程序是一个只装段的动态库，不再编进主程序）；
- **着色器链版本**从 `msl/b-<版本>` 目录名读取。

壳还是用中性占位 profile 编的，所以游戏的内存布局（`mem_used`、`app_pool` 等）由打包器写进应用的 `dm/env.txt`，宿主启动时读取。

### 着色器在运行时才拼出来的游戏（OpenGL）

OpenGL 游戏的文件里没有 GLSL：它们是跑起来之后一个个拼出来的（小丑牌的 LÖVE 在 Lua 里拼），`shader_split.py`
没有东西可拆。

**LÖVE 游戏打一次就是成品。** `dm/gfx/tools/gl_prebuilt.py` 直接跑**游戏自己带的**
`wrap_GraphicsShader.lua`——LÖVE 把这份 Lua 以文本形式编进 `main`——把它对 `resources/shaders/*.fs` 的拼接原样
跑一遍，得到的就是游戏运行时生成的那份 GLSL，**逐字节相同**，所以哈希也对得上——同一份代码做同一件事。

这件事 `nxn_pack.py` 自己做，不用装东西、不用先跑一遍游戏。小丑牌 19 个 `.fs` 出来 35 个着色器
（15 顶点 + 20 片元，含 LÖVE 自己的 4 个内置）——那是**机制**，不是你要做的步骤。

```bash
python scripts/nxn_pack.py <shell.zip> <小丑牌文件夹> -k prod.keys --tools <工具目录>
```

两个容易踩的细节，都在脚本里注释了：源文件必须**按字节读**（CRLF 是文本的一部分，按文本读会把 153 行变成 153 处差异），
以及本机的 EGL 是桌面 GL 3.3（`gles=false`，LÖVE 于是把 GLSL 1 风格的源码包成 GLSL 3）。

如果某个 GL 游戏**不是 LÖVE**，或者它的构建把包装器放进 C++（`main` 里找不到那份 Lua），离线生成会直接报错而不是
猜——那时才需要抓取：

1. `--no-shaders` 打一个不带着色器库的包并装上（能启动、有音乐、没有画面）；
2. 应用的 `Documents/env.txt` 里写 `DM_GL_CAPTURE=<可写目录>`，启动游戏，玩到想看的场景都出来过；
3. `--gl-capture <那个目录>` 重新打包。

苹果的 Metal 编译器（`xcrun metal`）只有 Mac 上有，所以包里带的是**源码**而不是编好的库——和本项目在没有 Xcode 的
机器上打 NVN 游戏时是同一个做法：手机首次启动统一编译（有进度页），平时按 key 懒加载。

### 要抓一次的游戏（**不需要 Mac**）

有几款不能「打一次直接玩」，要先用手机跑一次游戏、把运行时才生成的那些着色器抓下来，再打第二遍。**这一步全程在
Windows + 手机上完成，不需要 Mac** —— 只是多装一次包、多跑一分钟。

- **超级马里奥银河 1、2**（`010099C022B96000` / `0100FD8022DAA000`，各约一分钟）：着色器**不存在于任何文件里** ——
  Hagi 在运行时把 GameCube/Wii 的 GX 管线状态算成 SPIR-V，代码是编译过的机器码，PC 上没有可复刻的可执行生成器。
  三步：`--no-shaders` 打一个不带着色器库的包 → 装上后在 `Documents/env.txt` 写 `DM_VK_CAPTURE=vkcap` 跑一次 →
  `--vk-capture` 重打。细节：**[docs/CAPTURE.zh-CN.md](docs/CAPTURE.zh-CN.md)**（[English](docs/CAPTURE.md)）。
- **不是 LÖVE 的 OpenGL 游戏**：同样是抓一次，只是写 `DM_GL_CAPTURE`、用 `--gl-capture` 重打（三步见上一节）。

这两类都算**支持的游戏**，只是麻烦一些。抓到的目录留着，以后重打同一款不用再抓；换了别的游戏才要重抓。

### 现在打不了的游戏

下面这几款的着色器压在游戏自己的 Oodle / LZ5 压缩容器里，解压只能调**游戏自己的解码函数**，而那段代码是 Switch 的 arm64
机器码 —— 只有 Apple Silicon（M 系列）的 Mac 能原生执行它。**当前版本的打包器还没有这条通路**，所以它们在 Windows、
Linux、macOS 上都会失败。名单以 [docs/SUPPORTED.md](docs/SUPPORTED.md) 里标着「暂不支持」的行为准，下面的表只记了各自把
着色器放在了哪儿：

| 游戏 | Title ID | 着色器在哪种容器里 |
|---|---|---|
| Bayonetta 3 | `01004A4010FEA000` | PlatinumGames PKZ |
| DRAGON QUEST XI S | `01006C300E9F0000` | 旧式 Unreal pak（LZ5） |
| FINAL FANTASY RESONANCE | `0100606026F12000` | Unreal ShaderArchive |
| OCTOPATH TRAVELER 0 | `01005270232F2000` | Unreal ShaderArchive |
| Pokémon Legends: Z-A | `0100F43008C44000` | Trinity 包（Game Freak） |

打包器在**解包完成、读到那几个文件头之后、改写模块之前**就停下并说明原因，**不会**产出一个装上去却画不出东西的半成品
IPA。白跑的只有解包那一步，重试代价不大。这几款在支持列表里也标着对应标记。

> 上游（纯 Mac 路线）能出这几款，而且是**一次跑完**：它在打包流程里自动编译一个 Mac 宿主，用游戏自己的解码器把着色器解
> 出来，不用先抓一遍、也不经过手机。区别在于它要求那台机器就是 Apple Silicon 的 Mac；而「要抓一次」那类反过来 —— 手机抓、
> Windows 打，完全不需要 Mac。两件事。

其余的游戏（含上面说的 LÖVE 和 Vulkan 类）都是**打一次直接玩**。

## 注意事项

- **IPA 里含你的游戏文件。** 仅供自己使用，不要分发。
- **内存上限大约 3.4 GB，壳里已经请求了提高它的权限。** 没有 `increased-memory-limit` 这个 entitlement 时，iOS 只给
  应用约 3,375 MB；被模拟的游戏自己就要 3,285 MiB，所以大作很容易贴到墙上——**超了是被系统杀掉，不是崩溃，日志里看不到
  crash**。壳在构建时用 ad-hoc 占位签名把这个 entitlement 写进了 `DmIOS` 的签名里（没有签名的话，侧载工具无处可读，
  请求会丢失），重签时是否保留取决于工具：

  | 安装方式 | 是否保留 |
  |---|---|
  | AltStore Classic 2.2+、Impactor、Xcode + 个人团队 | 保留 |
  | LiveContainer | 保留（2026-10-23 实测：iPhone 16 Pro Max 上 `6140 MB`） |
  | SideStore + 免费 Apple ID | 保留，但需要对 App ID 做一次性修正（见下） |
  | Sideloadly | 不保留（等于没加，游戏仍在 3.4 GB 上限下跑） |

  **SideStore + 免费 Apple ID 怎么修**（一次性，之后每周刷新都保留）：免费账号本来就能拿到这个能力，但 SideStore 的
  旧构建不把它写进 App ID（[SideStore#1616](https://github.com/SideStore/SideStore/issues/1616)），装上过程是静默的——
  不报错，只是内存还是 3.3 GB。修一次即可：

  1. 先照常用 SideStore 装上这个 IPA（App ID 此时已注册到你的账号下，entitlement 尚未生效）；
  2. 用同一个 SideStore、**同一个 Apple ID** 装上 [GetMoreRam](https://github.com/hugeBlack/GetMoreRam)，打开后
     Settings 登录（要过二次验证），进 App IDs → Refresh，找到 `org.nxnative.<title ID>`，点
     **Add Increased Memory Limit**。只加这一个：Extended Virtual Addressing 是付费账号专属，壳也不需要。
     列表里找不到这个 App ID，先查 Apple ID 是否登的一致；
  3. 回 SideStore 重装一次这个 IPA——**直接覆盖安装，不要先删**（删了会丢存档）。新描述文件里就带这个 entitlement 了。

  另一个办法是换用带 [SideSign PR #4](https://github.com/SideStore/SideSign/pull/4)（2026-10-05）修复的 SideStore
  新构建，装上即生效，不用手动修。嫌麻烦也可以直接用 AltStore Classic 2.2+ 或 LiveContainer，它们无条件保留。

  判断是否生效：看 `Documents/log.txt` 的启动行 `boot: memory limit N MB`——约 3,375 MB 表示没生效，6–8 GB 表示生效。
  **任何游戏都能看出来**，不需要拿大作试：这一行说的是进程能拿多少内存，跟游戏要用多少无关。游戏跑起来之后，
  `[nvn-mtl] memory up to frame N ... headroom to the system memory limit` 那行还会持续报剩余额度。
- **每个游戏是独立的应用，可以同时装多个。** bundle ID 由 title ID 生成（`org.nxnative.<title ID>`），所以第二个游戏
  装上不会顶掉第一个，存档也各自独立。同一个游戏重打包则是覆盖升级（存档保留）。
  早于这个改动打出来的包仍是壳的 `org.nxnative.shell`，会和别的游戏冲突——重打一次即可，旧的记得手动删掉。
- **壳和打包器要配套。** 用同一个 release 里的壳，别拿以前留着的旧 zip。旧壳可能不认识新打包器写进去的东西  
  （典型症状：手机上启动就崩，日志写 `unknown gate kind spin`）。现在的打包器会先读壳自带的 `dm/shell_info.txt`，  
  在打包时就拦下来并告诉你该怎么办，而不是等装到手机上才发现。
- **首次启动比较久**：手机要把所有着色器编译一遍。Hades II 的 140 个约 5 秒；大作要几分钟（进度页会估算）。结果由系统缓存，  
  之后启动就是秒开。
- 想在真机上临时改参数而不重新打包，把 `KEY=VALUE` 写进应用的 `Documents/env.txt` 即可。
- **金手指**（Atmosphère 格式）：设置页里有一个 **Cheats（金手指）** 分区。把金手指文本粘进去，文本里每个条目都会变成
  一个独立开关 —— 只要「无限生命」就只开那一条，不必整份打开。文本会写进 `Documents/cheats/cheats.txt`，开启的条目名
  同时交给宿主，下次启动两者都还在。一个 App 里只有一个游戏，所以这份金手指不会串到别的游戏；但它的地址属于**这个版本**
  （面板上会写版本号，拿别的版本的地址可能直接崩）。iPhone 上只有写内存数据的金手指生效：App 的代码页是签名的，
  改指令的那种不起作用，日志里会说明。`DM_LAUNCHER=0`（写进 `Documents/env.txt`）会跳过设置页，开关也就没有入口。
- 崩溃时宿主会写 `Documents/log.txt`，绝大多数情况那个文件会直接说明原因。
- 打包需要 **约 NSP 两倍大小的磁盘空间**：解包出的 RomFS、组装中的 app 和最终 IPA 会同时存在。打完包临时文件自动删掉  
  （见上文的 `--keep-work` / `--clean`）。

## 仓库结构

只有前两项是**打包时必须存在**的，其余是它们依赖的、和构建壳用的部分。

```
scripts/nxn_pack.py              打个包的入口；必须在仓库里，旁边要有 dm/
dm/                              打包时的全部依赖，缺了跑不了：
dm/games/<title ID>/             每个游戏的支持参数（模块、内存布局、改写表、着色器表）
dm/tools/                        改写器（patch_nso.py）、guest_dylib.py、nacp.py
dm/gfx/tools/                    着色器链（shader_split、dmsh_build、nometal_run…）
dm/host/ dm/ios/                 宿主与 iOS 应用源码（构建壳用）
dm/ios/tools/shell_build.sh      构建壳（CI 上用，需要 macOS 26 SDK）
dm/games/0000000000000000/       壳编译时用的中性 profile
.shell-overlay.txt               本分支相对上游新增/修改的文件清单
docs/SHELL.md                    拆分的详细说明
```

## 致谢与许可

本仓库是 **seedds** 的 **[nxnative](https://github.com/seedds/nxnative)** 的分支，绝大部分实质工作来自上游：改写器、  
翻译链、宿主、游戏 profile 以及那三十来个经过真机验证的标题，都是上游的成果。

这套「原生执行」方案本身来自 **jhaizhou-ops** 的 **[d2r-mobile](https://github.com/jhaizhou-ops/d2r-mobile)**（MIT），是为  
《暗黑破坏神 II：重制版》做出来的。上游 nxnative（最初叫 smbw-mobile）就是从它的通用部分起步——NSO 改写器、Horizon 宿主、  
NVN-on-Metal 层和 iOS app 外壳——两个项目之间的修复是**双向**的。它们是**两个独立项目、两位不同作者**，各自对应各自的游戏。

本分支的「壳」路线沿用 d2r-mobile 的 `shell_build.sh` 做法；`guest_dylib.py`、`nometal_run.py`、`dm_shader_prep.m`、  
`shell_build.sh` 是从那里移植的。

**内存上限**这一块来自 **LNPANDY** 的 **[nxnative-romfs-archive](https://github.com/LNPANDY/nxnative-romfs-archive)**（MIT）：
`com.apple.developer.kernel.increased-memory-limit` 要嵌进「占位」的 ad-hoc 签名里——未签名的二进制没有签名可供侧载工具
读取，光在工程里声明是不会生效的；无该权限时 iOS 只给约 3,375 MB、《王国之泪》在常驻 3,342 MB 时被杀、以及各家侧载工具
是否保留这个权限，也都是他实测并写下来的。壳的构建时签名（`dm/ios/DmIOS.entitlements` + `shell_build.sh`）是按这个思路
自己实现的，代码没有拷贝。

`nx_extract` 使用 **[LibHac](https://github.com/Thealexbarney/LibHac)**；着色器翻译器内嵌了 Ryujinx 的着色器库；着色器由  
**[SPIRV-Cross](https://github.com/KhronosGroup/SPIRV-Cross)** 翻译；工具包里的 `zstd.exe` 是 Meta 的
**[zstd](https://github.com/facebook/zstd)** CLI v1.5.7，按它的 BSD 许可原样再分发。

**许可：MIT**，与上游一致。[LICENSE](LICENSE) 是上游自己的文件，其中保留的是原始版权声明（© 2026 jhaizhou——nxnative 从  
d2r-mobile 的通用部分起步，因此沿用了它的署名）。转载本分支或其构建产物时，请保留该声明与本许可。MIT 只覆盖代码：游戏文件、  
密钥以及你打出来的 IPA 由你自己负责，不在许可范围内。
