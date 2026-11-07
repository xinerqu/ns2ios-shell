// Library.swift — the game library page of a shell app that holds several games (dm_show_library, called by main.m with
// dm_shell_library_needed). It lists every Frameworks/DmGame<16 hex digits>.framework in the app bundle and starts the one
// the player picks.
// 文件说明：这个文件是 iOS 壳工程的游戏库页。数据来源全部是只读的文件：每个游戏 framework 里的 game.json（名字、版本、chain）
// 与 icon.png；游戏数据在 Documents/ns2ios/games/<tid>/{romfs.img,msl,aoc}，romfs.img 存在且大小 > 0 才算这个游戏可以玩；
// 壳的着色器链号在 Bundle.main 的 Info.plist 键 DMChain（可能不存在）。点卡片会调用 main.m 的 dm_shell_start_game(tid)
// （设环境 + dlopen framework + 预加载），成功就把 tid 交给 picked，失败就在卡片下面显示错误，库本身不关闭。
import PhotosUI
import SwiftUI
import UIKit

// MARK: - C interface (main.m)

// 进入该游戏：返回 NULL 成功，否则是一段错误文案。启动会做 dlopen 与预加载，所以放到后台队列调用。
@_silgen_name("dm_shell_start_game")
private func dm_shell_start_game(_ tid: UnsafePointer<CChar>) -> UnsafePointer<CChar>?

// main.m 给出的启动失败原因（例如 slim 包没找到可用的 romfs.img），NULL 表示没有。
@_silgen_name("front_ios_boot_message")
private func front_ios_boot_message() -> UnsafePointer<CChar>?

// MARK: - Data

/// game.json 的形状（每个 Frameworks/DmGame<tid>.framework 的根目录一份）。
struct GameManifest: Decodable {
    let title_id: String
    let name: String
    let names: [String: String]?
    let display_version: String?
    let chain: String?
    let modules: [String]?
}

/// 系统首选语言 → 从 names 里挑；没有就用 en-US；再没有就用第一个非空。
/// 匹配顺序：先精确（zh-Hans-CN），再按 "-" 逐段缩短（zh-Hans、zh）；都没有才退到 en-US / en-*。
func localizedName(_ names: [String: String], fallback: String) -> String {
    var table: [String: String] = [:]
    for (key, value) in names where !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
        table[key.lowercased().replacingOccurrences(of: "_", with: "-")] = value
    }
    guard !table.isEmpty else { return fallback }

    let preferred = (Locale.preferredLanguages.first ?? "en-US")
        .lowercased()
        .replacingOccurrences(of: "_", with: "-")
    var tag = preferred
    while !tag.isEmpty {
        if let hit = table[tag] { return hit }
        guard let cut = tag.lastIndex(of: "-") else { break }
        tag = String(tag[tag.startIndex..<cut])
    }
    for key in table.keys.sorted() where key == "en-us" || key == "en" || key.hasPrefix("en-") {
        if let hit = table[key] { return hit }
    }
    for key in table.keys.sorted() {
        if let hit = table[key] { return hit }
    }
    return fallback
}

/// 一个卡片要显示的全部东西：清单里读到的 + 扫描时算出来的。
private struct LibraryGame: Identifiable, Hashable {
    let tid: String
    let name: String
    let version: String?
    let dataOK: Bool          // Documents/ns2ios/games/<tid>/romfs.img 存在且非空
    let chainOK: Bool         // chain 为空、DMChain 缺失、或两者相等
    let icon: UIImage?
    let cover: UIImage?       // cover.jpg / cover.png beside game.json: a 16:9 banner when the packer or the player put one there
    let background: UIImage?  // a 16:9 picture the player picked on the device (LibraryArt.backgroundURL), nil when none
    let dmDir: URL            // the framework's dm/: its launcher.json is the settings this game offers
    var id: String { tid }
    static func == (a: LibraryGame, b: LibraryGame) -> Bool { a.tid == b.tid }
    func hash(into h: inout Hasher) { h.combine(tid) }
}

// MARK: - Scanning

// 读一张图并等比缩到最长边 maxSide：封面是 16:9，压成正方形再用就会被拉变形。
private func loadImage(_ url: URL, maxSide: CGFloat) -> UIImage? {
    guard let data = try? Data(contentsOf: url), let image = UIImage(data: data) else { return nil }
    let long = max(image.size.width, image.size.height)
    guard long > maxSide else { return image }
    let k = maxSide / long
    let size = CGSize(width: image.size.width * k, height: image.size.height * k)
    return UIGraphicsImageRenderer(size: size).image { _ in image.draw(in: CGRect(origin: .zero, size: size)) }
}

// 玩家在设备上为某个游戏挑的 16:9 背景图；App 自带的默认背景（没挑过就用它）。
enum LibraryArt {
    static func backgroundURL(_ tid: String) -> URL {
        let doc = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let d = doc.appendingPathComponent("ns2ios/art", isDirectory: true)
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d.appendingPathComponent(tid + ".jpg")
    }
    static func cheatsURL(_ tid: String) -> URL {
        let doc = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let d = doc.appendingPathComponent("ns2ios/cheats/\(tid)", isDirectory: true)
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }
    static var defaultBackground: UIImage? {
        guard let url = Bundle.main.url(forResource: "default-background", withExtension: "jpg", subdirectory: "dm") else { return nil }
        return UIImage(contentsOfFile: url.path)
    }
}

private enum GameLibrary {
    static func scan() -> [LibraryGame] {
        let fm = FileManager.default
        guard let dir = Bundle.main.privateFrameworksURL,
              let items = try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil,
                                                      options: [.skipsHiddenFiles])
        else { return [] }

        let shellChain = (Bundle.main.object(forInfoDictionaryKey: "DMChain") as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines).lowercased()

        var games: [LibraryGame] = []
        for item in items {
            let file = item.lastPathComponent
            guard file.hasPrefix("DmGame"), file.hasSuffix(".framework") else { continue }
            guard let data = try? Data(contentsOf: item.appendingPathComponent("game.json")),
                  let manifest = try? JSONDecoder().decode(GameManifest.self, from: data)
            else { continue }   // 没有清单的 framework 不算一个游戏
            let tid = manifest.title_id
            games.append(LibraryGame(tid: tid,
                                     name: localizedName(manifest.names ?? [:], fallback: manifest.name),
                                     version: manifest.display_version,
                                     dataOK: hasGameData(tid),
                                     chainOK: chainMatches(manifest.chain, shellChain),
                                     icon: loadIcon(item.appendingPathComponent("icon.png")),
                                     cover: loadImage(item.appendingPathComponent("cover.jpg"), maxSide: 1080)
                                         ?? loadImage(item.appendingPathComponent("cover.png"), maxSide: 1080),
                                     background: loadImage(LibraryArt.backgroundURL(tid), maxSide: 1600),
                                     dmDir: item.appendingPathComponent("dm")))
        }
        return games.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    private static func hasGameData(_ tid: String) -> Bool {
        guard !tid.isEmpty,
              let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
        else { return false }
        let romfs = docs.appendingPathComponent("ns2ios/games/\(tid)/romfs.img")
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: romfs.path),
              let size = attrs[.size] as? NSNumber
        else { return false }
        return size.int64Value > 0
    }

    private static func chainMatches(_ chain: String?, _ shell: String?) -> Bool {
        let game = (chain ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !game.isEmpty else { return true }             // 清单没写 chain：不提示
        guard let shell, !shell.isEmpty else { return true } // 壳没写 DMChain：视为 OK
        return game == shell
    }

    /// 512×512 的图标按 72pt（3x）缩一次，列表滚动时不必反复解码整张图。
    private static func loadIcon(_ url: URL) -> UIImage? {
        guard let image = UIImage(contentsOfFile: url.path) else { return nil }
        let side: CGFloat = 216
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        format.opaque = false
        return UIGraphicsImageRenderer(size: CGSize(width: side, height: side), format: format).image { _ in
            image.draw(in: CGRect(x: 0, y: 0, width: side, height: side))
        }
    }
}

// MARK: - Strings

private enum LibText {
    static var chinese: Bool {
        (Locale.preferredLanguages.first ?? "en").lowercased().hasPrefix("zh")
    }
    static func t(_ zh: String, _ en: String) -> String { chinese ? zh : en }
}

// picked 拿到的 tid 指针要在回调返回后仍然有效，这里留到进程结束（一次启动一个，泄漏可忽略）。
private var libraryLaunchTid: UnsafeMutablePointer<CChar>?

// MARK: - Model

private final class LibraryModel: ObservableObject {
    @Published private(set) var games: [LibraryGame]
    @Published private(set) var starting: String?          // 正在启动的 tid
    @Published private(set) var errors: [String: String] = [:]
    let bootMessage: String?

    private let picked: @convention(c) (UnsafePointer<CChar>?) -> Void
    private let cancelled: @convention(c) () -> Void
    weak var host: UIViewController?

    init(picked: @escaping @convention(c) (UnsafePointer<CChar>?) -> Void,
         cancelled: @escaping @convention(c) () -> Void) {
        self.picked = picked
        self.cancelled = cancelled
        self.games = GameLibrary.scan()
        self.bootMessage = front_ios_boot_message().map { String(cString: $0) }
    }

    func refresh() {
        games = GameLibrary.scan()
        errors = errors.filter { tid, _ in games.contains { $0.tid == tid } }
    }

    func start(_ game: LibraryGame) {
        guard starting == nil else { return }
        starting = game.tid
        errors[game.tid] = nil
        let tid = game.tid
        // dlopen 与预加载可能要一两秒，放到后台；回到主线程再动 UI。
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var failure: String?
            tid.withCString { path in
                if let message = dm_shell_start_game(path) { failure = String(cString: message) }
            }
            DispatchQueue.main.async {
                guard let self else { return }
                self.starting = nil
                if let failure {
                    self.errors[tid] = failure.isEmpty ? LibText.t("启动失败", "Could not start the game") : failure
                } else {
                    self.launch(tid)
                }
            }
        }
    }

    func cancel() {
        let callback = cancelled
        guard let host else { callback(); return }
        host.dismiss(animated: false) { callback() }
    }

    private func launch(_ tid: String) {
        let buffer: UnsafeMutablePointer<CChar>? = tid.withCString { strdup($0) }
        libraryLaunchTid = buffer
        guard let host else { hand(buffer); return }
        host.dismiss(animated: false) { self.hand(buffer) }
    }

    private func hand(_ buffer: UnsafeMutablePointer<CChar>?) {
        if let buffer { picked(UnsafePointer(buffer)) } else { picked(nil) }
    }
}

// MARK: - View
//
// 横屏沉浸式游戏库：中央焦点封面 + 随游戏切换的动态背景 + 底部固定 PLAY。只在这一层做视觉，数据来自 GameLibrary.scan()，
// 启动仍然只走 LibraryModel.start() -> dm_shell_start_game(tid)，不碰任何加载/启动逻辑。
// Landscape only: the app's Info.plist allows landscape on iPhone already, so no portrait layout is drawn.

private struct LibraryView: View {
    @ObservedObject var model: LibraryModel
    @State private var selected: String?          // the game in the middle of the flow
    @State private var shown: String?             // its title ID, one beat behind: the background crossfades on this
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var picking = false
    @State private var picked: PhotosPickerItem?
    @State private var showArtHint = false
    @AppStorage("artHintShown") private var artHintShown = false
    @State private var artGame: LibraryGame?      // whose background the picker is for

    private var current: LibraryGame? {
        model.games.first { $0.tid == selected } ?? model.games.first
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Backdrop(game: model.games.first { $0.tid == shown } ?? current, animated: !reduceMotion)
                VStack(spacing: 0) {
                    topBar
                    Spacer(minLength: 0)
                    flow
                    caption
                    playButton
                    Spacer(minLength: 0)
                    footer
                }
                .padding(.horizontal, 32)
                .padding(.top, 14)
                .padding(.bottom, 10)
            }
            .toolbar(.hidden, for: .navigationBar)
            .navigationDestination(for: LibraryGame.self) { game in GameDetail(model: model, game: game) }
            .task(id: current?.tid) {
                // 背景比选中晚一拍：先换封面，再淡入新背景，避免两层同时跳
                let target = current?.tid
                if shown != target {
                    try? await Task.sleep(nanoseconds: 120_000_000)
                    withAnimation(.easeInOut(duration: 0.45)) { shown = target }
                }
            }
            .onAppear { if selected == nil { selected = model.games.first?.tid; shown = selected } }
            .alert(LibText.t("为这个游戏设置 16:9 背景图", "Set a 16:9 background for this game"), isPresented: $showArtHint) {
                Button(LibText.t("去选图", "Choose a picture")) { artHintShown = true; picking = true }
                Button(LibText.t("取消", "Cancel"), role: .cancel) { }
            } message: {
                Text(LibText.t("选中的图会作为这个游戏的背景（按 16:9 裁切），不影响游戏图标。",
                               "The picture becomes this game's background, cropped to 16:9. The game's icon is not changed."))
            }
            .photosPicker(isPresented: $picking, selection: $picked, matching: .images)
            .onChange(of: picked) { _, item in
                guard let item, let game = artGame else { return }
                Task {
                    if let data = try? await item.loadTransferable(type: Data.self), UIImage(data: data) != nil {
                        try? data.write(to: LibraryArt.backgroundURL(game.tid))
                        model.refresh()
                        shown = nil
                        try? await Task.sleep(nanoseconds: 50_000_000)
                        shown = game.tid
                    }
                    picked = nil
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    // MARK: top bar — weak on purpose, the covers own the screen

    private var topBar: some View {
        HStack(spacing: 10) {
            Text("ns2ios").font(.subheadline.weight(.semibold)).foregroundStyle(.white.opacity(0.9))
            Text(LibText.t("游戏库", "GAME LIBRARY"))
                .font(.caption2.weight(.medium)).tracking(2)
                .foregroundStyle(.white.opacity(0.45))
            Spacer(minLength: 0)
            Text(LibText.t("\(model.games.count) 款游戏", "\(model.games.count) games"))
                .font(.caption).foregroundStyle(.white.opacity(0.45))
            Button { model.refresh() } label: {
                Image(systemName: "arrow.clockwise").font(.system(size: 13, weight: .semibold))
                    .frame(width: 28, height: 28)
            }
            .buttonStyle(.plain)
            .foregroundStyle(.white.opacity(0.7))
            .accessibilityLabel(LibText.t("刷新", "Refresh"))
        }
    }

    // MARK: the flow

    private var flow: some View {
        GeometryReader { geo in
            let h = min(geo.size.height, 250)
            // 每个封面用自己的比例：横幅 16:9，图标是正方形。ViewBuilder 里不能写 func，所以用到 LibraryView 的方法。

            ScrollView(.horizontal) {
                LazyHStack(spacing: 22) {
                    ForEach(model.games) { game in
                        Button { artTapped(game) } label: {
                            CoverCell(game: game, width: cellWidth(game, height: h), height: h, focused: game.tid == selected)
                        }
                        .buttonStyle(.plain)
                        .id(game.tid)
                        .scrollTransition(.interactive, axis: .horizontal) { content, phase in
                            content
                                .scaleEffect(phase.isIdentity ? 1 : 0.78)
                                .opacity(phase.isIdentity ? 1 : 0.62)
                        }
                        .disabled(model.starting != nil && model.starting != game.tid)
                    }
                }
                .scrollTargetLayout()
                .padding(.horizontal, max(0, (geo.size.width - (current.map { cellWidth($0, height: h) } ?? h)) / 2))
            }
            .scrollTargetBehavior(.viewAligned)
            .scrollPosition(id: $selected, anchor: .center)
            .scrollIndicators(.hidden)
            .frame(height: h + 18)
        }
        .frame(maxHeight: 268)
        .overlay(alignment: .center) {
            if model.games.isEmpty { emptyHint }
        }
    }

    // MARK: the caption under the covers

    private var caption: some View {
        VStack(spacing: 6) {
            Text(current?.name ?? LibText.t("还没有游戏", "No games yet"))
                .font(.system(size: 24, weight: .bold)).foregroundStyle(.white)
                .lineLimit(1).minimumScaleFactor(0.6)
                .id(current?.tid ?? "none")
                .transition(.asymmetric(insertion: .move(edge: .bottom).combined(with: .opacity),
                                        removal: .move(edge: .top).combined(with: .opacity)))
            if let v = current?.version, !v.isEmpty {
                Text("VERSION " + v)
                    .font(.system(size: 10, weight: .medium)).tracking(1.2)
                    .foregroundStyle(.white.opacity(0.55))
                    .id("v" + (current?.tid ?? "none") + v)
            }
            if let c = current, !c.chainOK {
                Text(LibText.t("用旧版壳打包，建议重新打包", "Packed for an older shell — repack recommended"))
                    .font(.caption2).foregroundStyle(.orange.opacity(0.9))
            }
            if let tid = current?.tid, let e = model.errors[tid] {
                Text(e).font(.caption2).foregroundStyle(.red.opacity(0.95)).lineLimit(2)
            }
        }
        .animation(.easeInOut(duration: 0.25), value: current?.tid)
        .padding(.top, 6)
        .frame(height: 74)
    }

    private var playButton: some View {
        HStack(spacing: 10) { playAction; settingsLink; cheatsLink }
    }

    private var cheatsLink: some View {
        NavigationLink { if let g = current { CheatsPage(game: g) } } label: {
            HStack(spacing: 5) {
                Image(systemName: "wand.and.stars")
                Text(LibText.t("金手指", "CHEATS")).font(.footnote.weight(.semibold))
            }
            .frame(width: 116, height: 40)
            .background(.white.opacity(0.12), in: Capsule())
            .foregroundStyle(.white.opacity(0.9))
        }
        .buttonStyle(PressScaleStyle())
        .disabled(current == nil)
    }

    private var settingsLink: some View {
        NavigationLink(value: current) {
            HStack(spacing: 6) {
                Image(systemName: "slider.horizontal.3")
                Text(LibText.t("设置", "SETTINGS")).font(.footnote.weight(.semibold))
            }
            .frame(width: 116, height: 40)
            .background(.white.opacity(0.12), in: Capsule())
            .foregroundStyle(.white.opacity(0.9))
        }
        .buttonStyle(PressScaleStyle())
        .disabled(current == nil)
        .opacity(0.9)
    }

    private var playAction: some View {
        Button {
            guard let game = current else { return }
            setenv("DM_LAUNCHER", "0", 1)   // 库页就是选择页：不再弹第二个设置页
            model.start(game)
        } label: {
            HStack(spacing: 8) {
                if let tid = current?.tid, model.starting == tid {
                    ProgressView().controlSize(.small).tint(.white)
                } else {
                    Image(systemName: "play.fill")
                }
                Text(LibText.t("开始游戏", "PLAY")).font(.subheadline.weight(.semibold))
            }
            .frame(width: 210, height: 40)
            .background(
                LinearGradient(colors: [Color(red: 0.29, green: 0.42, blue: 1.0), Color(red: 0.55, green: 0.30, blue: 0.95)],
                               startPoint: .leading, endPoint: .trailing),
                in: Capsule()
            )
            .overlay(Capsule().strokeBorder(.white.opacity(0.18), lineWidth: 0.5))
            .shadow(color: .black.opacity(0.5), radius: 14, y: 6)
        }
        .buttonStyle(PressScaleStyle())
        .disabled(model.games.isEmpty || model.starting != nil || current?.dataOK == false)
        .opacity(current?.dataOK == false ? 0.45 : 1)
    }

    private var footer: some View {
        HStack {
            if let c = current {
                Label(c.dataOK ? LibText.t("可以玩", "Ready") : LibText.t("缺少游戏数据", "Game data missing"),
                      systemImage: c.dataOK ? "checkmark.circle.fill" : "tray.and.arrow.down.fill")
                    .font(.caption2).foregroundStyle(c.dataOK ? .green.opacity(0.85) : .yellow.opacity(0.9))
            }
            Spacer(minLength: 0)
            if model.games.count > 1 {
                Text(LibText.t("左右滑动切换", "Swipe to switch")).font(.caption2).foregroundStyle(.white.opacity(0.35))
            }
        }
        .frame(height: 20)
    }

    /// 一个封面要多宽：横幅按 16:9，图标按正方形（ViewBuilder 里不能声明函数，放在这里）。
    private func cellWidth(_ game: LibraryGame, height: CGFloat) -> CGFloat {
        game.cover == nil ? height : height * 16 / 9
    }

    /// 点大封面：第一次解释这是设背景，之后直接开相册。
    private func artTapped(_ game: LibraryGame) {
        artGame = game
        if artHintShown { picking = true } else { showArtHint = true }
    }

    private var emptyHint: some View {
        VStack(spacing: 8) {
            Image(systemName: "square.stack.3d.up.slash").font(.system(size: 30)).foregroundStyle(.white.opacity(0.35))
            Text(LibText.t("这个 App 里还没有游戏", "No games in this app yet"))
                .font(.headline).foregroundStyle(.white.opacity(0.8))
            Text(LibText.t("用打包器把一个游戏打进这个 App，它会出现在这里。",
                           "Pack a game into this app and it shows up here."))
                .font(.caption).foregroundStyle(.white.opacity(0.5)).multilineTextAlignment(.center)
            if let m = model.bootMessage { Text(m).font(.caption2).foregroundStyle(.white.opacity(0.4)) }
        }
        .padding(24)
    }
}

// MARK: - One cover in the flow

private struct CoverCell: View {
    let game: LibraryGame
    let width: CGFloat
    let height: CGFloat
    let focused: Bool

    var body: some View {
        ZStack {
            if let cover = game.cover {
                Image(uiImage: cover).resizable().scaledToFill()
            } else if let icon = game.icon {
                // 图标本身就是圆的方的游戏封面：直接铺满，不再套一层 16:9 的深色底板
                Image(uiImage: icon).resizable().scaledToFill()
            } else {
                LinearGradient(colors: [Color(white: 0.14), Color(white: 0.07)], startPoint: .top, endPoint: .bottom)
                Image(systemName: "gamecontroller.fill").font(.system(size: height * 0.24)).foregroundStyle(.white.opacity(0.3))
            }
        }
        .frame(width: width, height: height)
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .shadow(color: .black.opacity(focused ? 0.6 : 0.3), radius: focused ? 26 : 8, y: focused ? 10 : 4)
        .accessibilityLabel(game.name)
    }
}

// MARK: - Backdrop: the current game's art, blurred, under everything

private struct Backdrop: View {
    let game: LibraryGame?
    let animated: Bool

    var body: some View {
        ZStack {
            LinearGradient(colors: [Color(red: 0.05, green: 0.06, blue: 0.12), .black],
                           startPoint: .topLeading, endPoint: .bottomTrailing)
            if let art = game?.background ?? game?.cover ?? game?.icon ?? LibraryArt.defaultBackground {
                GeometryReader { geo in
                    Image(uiImage: art)
                        .resizable().scaledToFill()
                        .frame(width: geo.size.width, height: geo.size.height)
                        .clipped()
                        .blur(radius: 30, opaque: true)
                        .opacity(0.55)
                }
                .overlay(Color.black.opacity(0.55))
                .transition(.opacity)
                .id(game?.tid ?? "none")
            }
        }
        .ignoresSafeArea()
        .animation(animated ? .easeInOut(duration: 0.45) : nil, value: game?.tid)
    }
}

// 金手指一页：面板要的目录是 main.m 给这个游戏设的 DM_CHEATS，库阶段还没设，所以直接给它。
private struct CheatsPage: View {
    let game: LibraryGame
    var body: some View {
        ScrollView {
            CheatsSection(cheatsDir: LibraryArt.cheatsURL(game.tid))
                .padding(.horizontal, 24).padding(.vertical, 12)
        }
        .scrollDismissesKeyboard(.interactively)
        .background(Backdrop(game: game, animated: false))
        .navigationTitle(LibText.t("金手指", "Cheats"))
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct PressScaleStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.96 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

// MARK: - One game: art and Play on the left, everything else scrolling on the right

private struct GameDetail: View {
    @ObservedObject var model: LibraryModel
    let game: LibraryGame
    @State private var settings: LauncherModel?
    @State private var open: Set<String> = []      // which settings groups are expanded: one at a time by default

    private var busy: Bool { model.starting != nil }

    var body: some View {
        HStack(alignment: .top, spacing: 26) {
            left.frame(width: 330)
            right
        }
        .padding(.horizontal, 28)
        .padding(.top, 8)
        .padding(.bottom, 20)
        .background(Backdrop(game: game, animated: false))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text(game.name).font(.headline).lineLimit(1).foregroundStyle(.white)
            }
        }
        .task {
            guard settings == nil else { return }
            let url = game.dmDir.appendingPathComponent("launcher.json")
            guard let data = try? Data(contentsOf: url),
                  let file = try? JSONDecoder().decode(SettingsFile.self, from: data) else { return }
            let m = LauncherModel(file.settings, prefix: "game." + game.tid)
            settings = m
            if let first = m.sections.first(where: { !m.rows(in: $0).isEmpty }) { open = [first] }
        }
    }

    // MARK: left: the art, its state, and Play

    private var left: some View {
        VStack(alignment: .leading, spacing: 14) {
            Group {
                if let art = game.background {
                    Image(uiImage: art).resizable().scaledToFill()
                } else if let cover = game.cover {
                    Image(uiImage: cover).resizable().scaledToFill()
                } else if let icon = game.icon {
                    LinearGradient(colors: [Color(white: 0.16), Color(white: 0.07)], startPoint: .topLeading, endPoint: .bottomTrailing)
                        .overlay(Image(uiImage: icon).resizable().scaledToFit().padding(24))
                } else {
                    Color(white: 0.1).overlay(Image(systemName: "gamecontroller.fill").font(.system(size: 44)).foregroundStyle(.white.opacity(0.3)))
                }
            }
            .frame(width: 330, height: 186)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .shadow(color: .black.opacity(0.5), radius: 18, y: 8)


            VStack(alignment: .leading, spacing: 4) {
                Text(game.name).font(.title3.weight(.bold)).foregroundStyle(.white).lineLimit(2)
                Text((game.version.map { "VERSION " + $0 } ?? "") + "  ·  " + String(game.tid.suffix(8)))
                    .font(.caption).foregroundStyle(.white.opacity(0.5)).monospacedDigit()
            }

            HStack(spacing: 8) {
                Image(systemName: game.dataOK ? "checkmark.circle.fill" : "tray.and.arrow.down.fill")
                Text(game.dataOK ? (game.chainOK ? LibText.t("可以玩", "Ready") : LibText.t("旧壳打包，建议重打", "Packed for an older shell"))
                                 : LibText.t("缺少游戏数据", "Game data missing"))
            }
            .font(.caption.weight(.medium))
            .foregroundStyle(game.dataOK ? (game.chainOK ? .green : .orange) : .yellow)
            .padding(.horizontal, 10).padding(.vertical, 5)
            .background(.white.opacity(0.08), in: Capsule())

            if let e = model.errors[game.tid] {
                Text(e).font(.caption2).foregroundStyle(.red.opacity(0.95)).fixedSize(horizontal: false, vertical: true)
            }

            Button {
                settings?.save()                                  // 存到这个游戏的键下；环境变量由 dm_show_launcher 的 applySaved 设
                if settings != nil { setenv("DM_LAUNCHER", "0", 1) }   // 库页就是设置页，不再弹第二个
                model.start(game)
            } label: {
                HStack(spacing: 8) {
                    if model.starting == game.tid { ProgressView().controlSize(.small).tint(.white) }
                    else { Image(systemName: "play.fill") }
                    Text(LibText.t("开始游戏", "PLAY")).font(.subheadline.weight(.semibold))
                }
                .frame(maxWidth: .infinity, minHeight: 50)
                .background(LinearGradient(colors: [Color(red: 0.29, green: 0.42, blue: 1.0), Color(red: 0.55, green: 0.30, blue: 0.95)],
                                           startPoint: .leading, endPoint: .trailing), in: Capsule())
                .overlay(Capsule().strokeBorder(.white.opacity(0.18), lineWidth: 0.5))
            }
            .buttonStyle(PressScaleStyle())
            .disabled(busy || !game.dataOK)

            Spacer(minLength: 0)
        }
    }

    // MARK: right: the game's own options, the cheats panel, and a reset

    private var right: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                if let m = settings, !m.settings.isEmpty {
                    ForEach(m.sections, id: \.self) { section in
                        let rows = m.rows(in: section)
                        if !rows.isEmpty {
                            DisclosureGroup(isExpanded: binding(section)) {
                                VStack(spacing: 2) {
                                    ForEach(rows) { row in
                                        SettingRow(s: row, m: m).padding(.vertical, 2)
                                        Divider().overlay(Color.white.opacity(0.08))
                                    }
                                }
                                .padding(.top, 4)
                            } label: {
                                HStack(spacing: 6) {
                                    Text(section).font(.subheadline.weight(.semibold)).foregroundStyle(.white.opacity(0.85))
                                    Spacer(minLength: 0)
                                    Text("\(rows.count)").font(.caption2).foregroundStyle(.white.opacity(0.4))
                                }
                            }
                            .padding(14)
                            .background(.white.opacity(0.07), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(.white.opacity(0.1)))
                        }
                    }
                    HStack {
                        Button(LibText.t("恢复测试默认值", "Reset to tested defaults")) {
                            withAnimation { m.reset() }
                        }
                        .font(.footnote.weight(.medium)).disabled(!m.changed)
                        Spacer(minLength: 0)
                        Text(LibText.t("改动在点 PLAY 时保存", "Saved when you press PLAY"))
                            .font(.caption2).foregroundStyle(.white.opacity(0.4))
                    }
                    .padding(.top, 4)
                } else {
                    Text(LibText.t("这个游戏没有启动选项。", "This game has no launch options."))
                        .font(.footnote).foregroundStyle(.white.opacity(0.45))
                }

                // 金手指单独一页：它是我们独有的功能，不塞在设置列表底下
                NavigationLink {
                    CheatsPage(game: game)
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "wand.and.stars")
                        Text(LibText.t("金手指", "Cheats")).font(.subheadline.weight(.semibold))
                        Spacer(minLength: 0)
                        Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.white.opacity(0.4))
                    }
                    .padding(14)
                    .background(.white.opacity(0.07), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(.white.opacity(0.1)))
                }
                .buttonStyle(.plain)
                .padding(.top, 6)
            }
            .padding(.bottom, 24)
        }
        .scrollDismissesKeyboard(.interactively)
    }

    private func binding(_ section: String) -> Binding<Bool> {
        Binding(get: { open.contains(section) },
                set: { v in if v { open.insert(section) } else { open.remove(section) } })
    }

}

@_cdecl("dm_show_library")
public func dmShowLibrary(_ viewController: UnsafeMutableRawPointer,
                          _ picked: @convention(c) (UnsafePointer<CChar>?) -> Void,
                          _ cancelled: @convention(c) () -> Void) {
    let parent = Unmanaged<UIViewController>.fromOpaque(viewController).takeUnretainedValue()
    let model = LibraryModel(picked: picked, cancelled: cancelled)
    let host = DmHostingController(rootView: LibraryView(model: model))
    host.modalPresentationStyle = .fullScreen
    host.view.backgroundColor = .systemBackground
    model.host = host
    parent.present(host, animated: false)
    fputs("library: \(model.games.count) games\n", stderr)
}
