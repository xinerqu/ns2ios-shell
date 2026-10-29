# Supported games / 支持的游戏

Generated from `dm/games/*/game.tsv` by `dm/tools/supported_list.py` — do not edit by hand.  
本页由 `dm/tools/supported_list.py` 从 `dm/games/*/game.tsv` 生成，请勿手动修改。

**104 款游戏。** 文件名不重要——打包器从包内容识别游戏，在下表里对上 title ID 就够了。

- **IPA 体积** ≈ 你的 NSP/XCI 体积 + 约 16 MB（壳本身）。游戏文件在包里是原样存放的，不再压缩。
- **首次启动**要编译着色器：打包时只把 Switch 的着色器翻译成 Metal 源码，编译在手机上做（之后有缓存）。几秒到大作几分钟，启动时有进度页。
- **内存**：iOS 给不带 entitlement 的应用约 3.4 GB；本项目的壳在构建时已请求提高上限，侧载工具保留该权限后可用到 6–8 GB。
- 表内是本仓库带参数、并且打包器跟随其快照验证过的游戏。**不在表内**的游戏，打包器会尝试从上游取它的参数并尽力打包（未经本仓库验证，可能跑不起来），不会产出半成品 IPA。
- **标着「暂不支持」的游戏**：着色器压在 Oodle / LZ5 容器里（Trinity 包、PlatinumGames PKZ、旧式 Unreal pak），只有游戏自己的解码器能解，而那段代码是 Switch 的 arm64 机器码，只能在 Apple Silicon（M 系列）的 Mac 上跑。**当前版本的打包器没有这条通路**，所以这几款在 Windows、Linux、macOS 上都会失败——它会在拆着色器那步停下并说明，不会产出装上去却画不出东西的半成品。详见 README 的「现在打不了的游戏（5 款）」。
- **标着「需抓一次」的游戏**：着色器只在游戏跑起来时才生成，没有文件可拆。先用手机装一个不带画面的包抓一次，再打第二遍——**不需要 Mac**，Windows + 手机就够。步骤见 `docs/CAPTURE.zh-CN.md`。

| 游戏 | Title ID | 已验证版本 | 特性 |
|---|---|---|---|
| 13 Sentinels: Aegis Rim | `01003FC01670C000` | 1.10 |  |
| 13 Sentinels: Aegis Rim (Asia) | `01008D7016438000` | 1.00 |  |
| 13 Sentinels: Aegis Rim (Japan) | `010045C014650000` | 1.00 |  |
| Absolum | `0100A83022A22000` | 1.1.2（也可 1.1.1） |  |
| Ace Attorney Investigations Collection | `010005501E68C000` | 1.0.0.2 |  |
| Advance Wars 1+2: Re-Boot Camp | `0100300012F2A000` | 1.0.0 |  |
| AI: THE SOMNIUM FILES | `010089B00D09C000` | 1.0.1 |  |
| AI: THE SOMNIUM FILES - nirvanA Initiative | `0100713016CCC000` | 1.0.2 |  |
| Animal Crossing: New Horizons | `01006F8002326000` | 3.0.3 |  |
| ANIMAL WELL | `010020D01AD24000` | 1.0.10 |  |
| ASTRAL CHAIN | `01007300020FA000` | 1.0.1 | 60 fps |
| Baba Is You | `01002CD00A51C000` | 1.0.12 |  |
| Balatro | `0100CD801CE5E000` | 1.1.3 | handheld |
| BALL x PIT | `010086A022444000` | 1.293 |  |
| Bastion | `010038600B27E000` | 1.0.0 |  |
| Batman: Arkham Knight | `0100ACD0163D0000` | 1.0.5 |  |
| Bayonetta | `010076F0049A2000` | 1.1 |  |
| Bayonetta 2 | `01007960049A0000` | 1.2 |  |
| Bayonetta 3 | `01004A4010FEA000` | 1.2.0 | 暂不支持 |
| Big Brain Academy: Brain vs. Brain | `0100620012D6E000` | 1.2.0 | handheld |
| Blasphemous | `0100698009C6E000` | 1.0.8 |  |
| Blasphemous 2 | `010040801A4C8000` | 1.0.8 |  |
| Brain Memory Training Academy | `0100F5D01DA3A000` | 1.0.0 |  |
| Captain Toad: Treasure Tracker | `01009BF0072D4000` | 1.4.2 | handheld |
| Catherine: Full Body | `0100BF00112C0000` | 1.0.1a |  |
| Celeste | `01002B30028F6000` | 1.4.1.1 |  |
| Cult of the Lamb | `01002E7016C46000` | 1.5.28 |  |
| Cuphead | `0100A5C00D162000` | 1.3.7 |  |
| DARK SOULS: REMASTERED | `01004AB00A260000` | 1.0.3 |  |
| Digimon Story Cyber Sleuth: Complete Edition | `010014E00DB56000` | 1.0.3 |  |
| Donkey Kong Country Returns HD | `01009D901BC56000` | 1.1.0 |  |
| Donkey Kong Country: Tropical Freeze | `0100C1F0051B6000` | 1.0.2 |  |
| DRAGON QUEST VII Reimagined | `0100A9D01C446000` | 1.2.0 | 60 fps |
| DRAGON QUEST XI S: Echoes of an Elusive Age – Definitive Edition | `01006C300E9F0000` | 1.0.4 | 60 fps · 暂不支持 |
| FINAL FANTASY CRYSTAL CHRONICLES Remastered Edition | `0100CE4010AAC000` | 1.0.3 |  |
| FINAL FANTASY RESONANCE | `0100606026F12000` | 1.0.1 | 60 fps · 暂不支持 |
| FINAL FANTASY RESONANCE (Asian release) | `0100B8A026F14000` | 1.0.1 | 60 fps |
| FINAL FANTASY TACTICS - The Ivalice Chronicles | `010038B015560000` | 1.4.0 |  |
| FINAL FANTASY X/X-2 HD Remaster | `0100BC300CB48000` | 1.0.0 |  |
| Fire Emblem Engage | `0100A6301214E000` | 2.0.0 |  |
| Fire Emblem: Three Houses | `010055D009F78000` | 1.2.0 |  |
| Ghost Trick: Phantom Detective | `010029B018432000` | 1.0.0 |  |
| Hades | `0100535012974000` | 1.0.38233 |  |
| Hades II | `0100A00019DE0000` | 0.0.126424 |  |
| Hollow Knight: Silksong | `010013C00E930000` | 1.0.30000 |  |
| Hyrule Warriors: Age of Calamity | `01002B00111A2000` | 1.3.0 | 60 fps |
| Kirby and the Forgotten Land | `01004D300C5AE000` | 1.1.0 |  |
| Luigi's Mansion 3 | `0100DCA0064A6000` | 1.4.0 | 60 fps |
| Mario + Rabbids Kingdom Battle | `010067300059A000` | 1.9.589692 |  |
| Mario + Rabbids® Sparks of Hope | `0100317013770000` | 1.6.2225577 |  |
| Mario Kart 8 Deluxe | `0100152000022000` | 4.0.1（也可 4.0.0） |  |
| Mario vs. Donkey Kong | `0100B99019412000` | 1.0.1 |  |
| Metroid Dread | `010093801237C000` | 2.1.0 |  |
| Monster Hunter Rise | `0100B04011742000` | 16.0.2 | 60 fps |
| Monster Train 2 | `010051701FB46000` | 1.1.1 |  |
| NieR:Automata The End of YoRHa Edition | `010056B015FE8000` | 1.0.3 | 60 fps |
| NieR:Automata The End of YoRHa Edition | `0100B8E016F76000` | 1.0.3 | 60 fps |
| OCTOPATH TRAVELER | `010057D006492000` | 1.0.5 |  |
| OCTOPATH TRAVELER 0 | `01005270232F2000` | 1.0.5 | 60 fps · 暂不支持 |
| OCTOPATH TRAVELER II | `0100A3501946E000` | 1.1.1 | 60 fps |
| Paper Mario: The Origami King | `0100A3900C3E2000` | 1.0.1 |  |
| Paper Mario: The Thousand-Year Door | `0100ECD018EBE000` | 1.0.1 | 60 fps |
| Patrick's Parabox | `0100E44016C22000` | 1.0.1 |  |
| Persona 4 Golden | `010062B01525C000` | 1.0.2 |  |
| Persona 5 Royal | `01005CA01580E000` | 1.0.2 |  |
| Pikmin 4 | `0100B7C00933A000` | 1.1.1 |  |
| Pokémon Brilliant Diamond | `0100000011D90000` | 1.3.0 | 60 fps |
| Pokémon Legends: Arceus | `01001F5010DFA000` | 1.1.1 |  |
| Pokémon Legends: Z-A | `0100F43008C44000` | 2.0.2 | 暂不支持 |
| Pokémon Scarlet | `0100A3D008C5C000` | 1.0.0 |  |
| Pokémon Shield | `01008DB008C2C000` | 1.3.2 |  |
| Pokémon Sword | `0100ABF008968000` | 1.3.2 |  |
| Portal | `01007BB017812000` | 1.0.3 |  |
| Raging Loop | `010020400E1C2000` | 1.0.0 |  |
| Return of the Obra Dinn | `010032E00E6E2000` | 1.0.1 |  |
| Rhythm Heaven Groove | `0100D9F01D474000` | 1.0.0 | handheld |
| ROMANCE OF THE THREE KINGDOMS 8 REMAKE | `0100C2A01A730000` | 1.1.3 |  |
| Sea of Stars | `01008C0016544000` | 2.0.59001 |  |
| SHIN MEGAMI TENSEI V: Vengeance | `010069C01AB82000` | 1.0.3 | 60 fps |
| Silver Pines | `0100D5B026844000` | 1.0.0 |  |
| Stardew Valley | `0100E65002BB8000` | 1.6.15.13 |  |
| SteamWorld Heist II | `010089C01E9CA000` | 1.5.1 |  |
| SteamWorld Heist: Ultimate Edition | `010016100A186000` | 2.2.1 |  |
| STEINS;GATE RE:BOOT | `01008BC024FDE000` | 1.0.3 |  |
| Subnautica: Below Zero | `010014C011146000` | 1.23.55063 |  |
| Super Mario 3D World + Bowser's Fury | `010028600EBDA000` | 1.2.2 |  |
| Super Mario Bros. Wonder | `010015100B514000` | 1.2.1（也可 1.0.0 / 1.0.1） |  |
| Super Mario Galaxy | `010099C022B96000` | 1.3.1 | handheld · 需抓一次 |
| Super Mario Galaxy 2 | `0100FD8022DAA000` | 1.4.0 | handheld · 需抓一次 |
| Super Mario Odyssey | `0100000000010000` | 1.4.1 |  |
| Super Mario Party Jamboree | `0100965017338000` | 2.3.0 |  |
| Super Smash Bros. Ultimate | `01006A800016E000` | 13.0.5 |  |
| Taiko Risshiden V DX | `0100346017304000` | 1.1.1 | handheld |
| The Great Ace Attorney Chronicles | `010036E00FB20000` | 1.0.0 |  |
| The Legend of Zelda: Breath of the Wild | `01007EF00011E000` | 1.9.0 | 60 fps |
| The Legend of Zelda: Echoes of Wisdom | `01008CF01BAAC000` | 1.1.0 |  |
| The Legend of Zelda: Link's Awakening | `01006BB00C6F0000` | 1.1.0 |  |
| The Legend of Zelda: Tears of the Kingdom | `0100F2C0115B6000` | 1.4.3 | 60 fps |
| Unicorn Overlord | `010069401ADB8000` | 1.05 |  |
| Unicorn Overlord (Asia) | `010054B01AD92000` | 1.05 |  |
| Xenoblade Chronicles 2 | `0100E95004038000` | 2.1.0 |  |
| Xenoblade Chronicles 3 | `010074F013262000` | 2.2.1 |  |
| Xenoblade Chronicles: Definitive Edition | `0100FF500E34A000` | 1.1.2 |  |
| Yoshi's Crafted World | `01006000040C2000` | 1.0.1 |  |
