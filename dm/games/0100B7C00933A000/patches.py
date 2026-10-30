# Pikmin 4 (0100B7C00933A000): the rewriter's patches of this game's own builds. dm/tools/patch_nso.py loads every profile's patches.py and applies those of a module's NSO build ID;
# CUTS, OBS and FRAME_STEP as described there ("Registered patches"). Patches of Nintendo SDK module builds are in dm/tools/sdk_patches.py
CUTS = {
  # Pikmin 4 1.1.1 (v262144) main (Unreal Engine 4.26.2; its FMallocBinned3 is replaced by the rewriter's engine-wide rule, dm/tools/ue_malloc.py). The engine's address region: nninitStartup (main+0x353f1xx) reads -SwitchVirtualAddressSpaceSize= into main+0x93c2130 (MB up to 4095), but reserves the region only when the argument
  # is absent: ldr x8,[x22,#0x130]; cbnz x8 → past the reservation; else 62 GB (orr #0xf80000000) into the size and nn::os::AllocateAddressRegion(main+0x93c2128, 62 GB). With the argument
  # the base stayed 0, and the first commit, AllocateMemoryPages(0, 2 MB) during the RHI's start-up (main+0x353ff8c), failed with 0xe03 into Unreal's fatal error. So the default is cut
  # to 4064 MB instead (no game_args), in Engage's 5 GB guest block as FINAL FANTASY RESONANCE's region
  '060f827bd2b38703e9ea11e3210d89bc': [
    (0x353f56c, 'e81361b2', '08c0bfd2', 'nninitStartup: the engine region\'s default size orr x8,xzr,#0xf80000000 (62 GB) → mov x8,#0xfe000000 (4064 MB)'),
    (0x353f578, 'e11361b2', '01c0bfd2', 'nninitStartup: AllocateAddressRegion size orr x1,xzr,#0xf80000000 (62 GB) → mov x1,#0xfe000000 (4064 MB)'),
  ],
}
# How general each patch is and could be (dm/tools/docs_patches.py "Binding", listed in docs/agents/game-patches.md): what, its binding now, the loosest it could have (goal),
# purpose, why; blocked or kept: why the gap stays open (cannot close yet, or left open on purpose); checked: what was checked on game files (else a judgement)
BINDING = [
    {'what': 'engine region 62 GB → 4064 MB', 'now': 'build', 'goal': 'engine', 'purpose': 'address space',
     'why': 'the other Unreal games cap the region with -SwitchVirtualAddressSpaceSize= (game_args); this build reserves it only without the argument, and with it the base stayed 0 '
            'and the first commit failed',
     'blocked': "open: whether that argument path is the game's own or a host fault"},
]
