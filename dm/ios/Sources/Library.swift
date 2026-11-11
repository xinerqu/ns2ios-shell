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
    let icon: UIImage?        // **卡片就是它**：游戏自己的封面。没有 icon 就没有封面（坤哥 2026-11-11）
    var fetchedArt: UIImage?  // 从网上刮来的 16:9 图，只当**背景**的第二层用，不上卡片；nil = 还没刮到
    let background: UIImage?  // a 16:9 picture the player picked on the device (LibraryArt.backgroundURL), nil when none
    let dmDir: URL            // the framework's dm/: its launcher.json is the settings this game offers
    var id: String { tid }
    static func == (a: LibraryGame, b: LibraryGame) -> Bool { a.tid == b.tid }
    func hash(into h: inout Hasher) { h.combine(tid) }
}

// MARK: - Scanning

// 读一张图并等比缩到最长边 maxSide：封面是 16:9，压成正方形再用就会被拉变形。
private func scaled(_ image: UIImage, maxSide: CGFloat) -> UIImage {
    let long = max(image.size.width, image.size.height)
    guard long > maxSide else { return image }
    let k = maxSide / long
    let size = CGSize(width: image.size.width * k, height: image.size.height * k)
    // scale = 1：位图的像素尺寸就等于这里的 size。不写它默认取屏幕的 3 倍，一张 1600 宽的背景会变成
    // 4800×2700 ≈ 52 MB（手机原图走 maxSide 之后约 69 MB），而这个页面同时挂着好几个游戏的图。
    let format = UIGraphicsImageRendererFormat.default()
    format.scale = 1
    return UIGraphicsImageRenderer(size: size, format: format).image { _ in image.draw(in: CGRect(origin: .zero, size: size)) }
}

private func loadImage(_ url: URL, maxSide: CGFloat) -> UIImage? {
    guard let data = try? Data(contentsOf: url), let image = UIImage(data: data) else { return nil }
    return scaled(image, maxSide: maxSide)
}

// 玩家在设备上为某个游戏挑的 16:9 背景图；App 自带的默认背景（没挑过就用它）。
enum LibraryArt {
    private static var ns2ios: URL {
        let doc = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        return doc.appendingPathComponent("ns2ios", isDirectory: true)
    }
    static func backgroundURL(_ tid: String) -> URL {
        let d = ns2ios.appendingPathComponent("art", isDirectory: true)
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d.appendingPathComponent(tid + ".jpg")
    }
    /// 从网上刮来的 16:9 图（LibraryArt 写）——**这是背景用的图，不是封面**（封面永远是游戏自己的 icon）。
    /// framework 里的东西在签名的 IPA 内、写不了，所以只落 Documents；取的时候排玩家自选背景之后、默认插画之前。
    private static func fetchedDir() -> URL {
        let d = ns2ios.appendingPathComponent("fetched", isDirectory: true)
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }
    static func fetchedArtURL(_ tid: String) -> URL { fetchedDir().appendingPathComponent(tid + ".jpg") }
    /// 这个 TID 在网上没有图（404）的记号：每次启动都去问一次没有意义，见到它就不再请求。
    static func fetchedMissingURL(_ tid: String) -> URL { fetchedDir().appendingPathComponent(tid + ".missing") }
    static func cheatsURL(_ tid: String) -> URL {
        let d = ns2ios.appendingPathComponent("cheats/\(tid)", isDirectory: true)
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }
    /// App 自带的默认背景（没挑过就用它）。**必须是 static let**：原来写成计算属性，而它现在被排在了
    /// 「玩家没挑过图」的通用分支上，每次刷新界面都会重新读盘并解码这张 6 MB 的图。
    static let defaultBackground: UIImage? = {
        guard let url = Bundle.main.url(forResource: "default-background", withExtension: "jpg",
                                        subdirectory: "dm") else {
            fputs("library: no default background in the bundle (dm/default-background.jpg)\n", stderr)
            return nil
        }
        guard let image = UIImage(contentsOfFile: url.path) else {
            fputs("library: the default background cannot be decoded\n", stderr)
            return nil
        }
        fputs("library: default background \(Int(image.size.width))x\(Int(image.size.height))\n", stderr)
        return image
    }()
}

// MARK: - The backdrop's picture from the network

/// 背景第二层的来源：nlib（`https://api.nlib.cc/nx/<TID>/banner` 直接给 1920×1080 JPEG；`/icon` 是方图，不用）。
/// **刮的是背景，不是封面**（封面永远是游戏自己的 icon）。只在玩家没挑过背景、卡也还没有刮来的图时问一次；
/// 下到就缩到 1080 宽存 `Documents/ns2ios/fetched/<tid>.jpg`，之后每次启动读盘。404（Switch 2 的游戏那边没有）
/// 落一个 `.missing` 记号不再问；网络失败只落一行日志、下次再试。
private enum LibraryBackdropArt {
    enum Outcome { case image(UIImage); case none; case failed(String) }

    static func fetch(_ tid: String, done: @escaping (Outcome) -> Void) {
        guard let url = URL(string: "https://api.nlib.cc/nx/\(tid)/banner") else { return done(.failed("bad title ID")) }
        var request = URLRequest(url: url)
        request.timeoutInterval = 8
        fputs("library: backdrop \(tid) asked from api.nlib.cc\n", stderr)
        URLSession.shared.dataTask(with: request) { data, response, error in
            if let error { return done(.failed(error.localizedDescription)) }
            guard let http = response as? HTTPURLResponse else { return done(.failed("no HTTP response")) }
            if http.statusCode == 404 {
                try? Data().write(to: LibraryArt.fetchedMissingURL(tid), options: .atomic)
                return done(.none)
            }
            guard http.statusCode == 200, let data, data.count > 1024, let image = UIImage(data: data) else {
                return done(.failed("HTTP \(http.statusCode), \(data?.count ?? 0) bytes"))
            }
            let small = scaled(image, maxSide: 1080)
            guard let jpeg = small.jpegData(compressionQuality: 0.86) else { return done(.failed("cannot encode the picture")) }
            do { try jpeg.write(to: LibraryArt.fetchedArtURL(tid), options: .atomic) }
            catch { return done(.failed("cannot keep it: \(error.localizedDescription)")) }
            fputs("library: backdrop \(tid) HTTP \(http.statusCode) \(data.count) bytes -> \(jpeg.count) bytes "
                  + "(\(Int(small.size.width))x\(Int(small.size.height)))\n", stderr)
            done(.image(small))
        }.resume()
    }
}

/// 上一次玩的是哪一款：真机的「最近玩过的排最前面」，不为它单开一行（坤哥 2026-11-11 定的）。
/// 文件是 Documents/ns2ios/state.json，与金手指/存档同一层；读不出来就当没玩过（库页照名字排），写不进去只留一行日志。
private enum LibraryState {
    private static var url: URL {
        let doc = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        return doc.appendingPathComponent("ns2ios/state.json")
    }

    /// tid -> 这次启动的时间（epoch 秒）。形如 {"games": {"0100…": 1762000000.0}}。
    static func lastPlayed() -> [String: Double] {
        guard let data = try? Data(contentsOf: url),
              let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let games = root["games"] as? [String: Any]
        else { return [:] }
        var out: [String: Double] = [:]
        for (tid, value) in games { if let n = value as? NSNumber { out[tid] = n.doubleValue } }
        return out
    }

    /// 启动成功的那一刻记一次：库页下次启动（每次回库都是重启 App）按它把这游戏排到最前。
    static func recordPlayed(_ tid: String) {
        var games = lastPlayed()
        games[tid] = Date().timeIntervalSince1970
        do {
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                    withIntermediateDirectories: true)
            let data = try JSONSerialization.data(withJSONObject: ["games": games], options: [.sortedKeys])
            try data.write(to: url, options: .atomic)
            fputs("library: last played \(tid)\n", stderr)
        } catch {
            // 记不下来只是少个排序，不该拦住玩家进游戏
            fputs("library: the last played game cannot be recorded (\(error))\n", stderr)
        }
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
                                     // 刮来的 16:9 图只喂背景；卡片用的是上面的 icon
                                     fetchedArt: loadImage(LibraryArt.fetchedArtURL(tid), maxSide: 1080),
                                     background: loadImage(LibraryArt.backgroundURL(tid), maxSide: 1600),
                                     dmDir: item.appendingPathComponent("dm")))
        }
        // 真机 Switch 的顺序：上次玩的那款排第一，其余按名字。没玩过的都在 0 上，排到有记录的游戏后面。
        // Swift 的 sort 不稳定，所以时间相等时必须自己按名字收口，否则每次扫描顺序可能不同。
        let lastPlayed = LibraryState.lastPlayed()
        games.sort { a, b in
            let ta = lastPlayed[a.tid] ?? 0, tb = lastPlayed[b.tid] ?? 0
            if ta != tb { return ta > tb }
            return a.name.localizedStandardCompare(b.name) == .orderedAscending
        }
        if let first = games.first, let at = lastPlayed[first.tid], at > 0 {
            fputs("library: the last played game is first (\(first.tid))\n", stderr)
        } else {
            fputs("library: no game played yet, the library is in name order\n", stderr)
        }
        fputs("library: \(games.count) game(s), \(games.filter { $0.background != nil }.count) with a picture of their own\n", stderr)
        return games
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
    private var fetching: Set<String> = []   // 正在问的 TID：同一张卡反复出现（滚动、刷新）也只问一次
    private var noArt: Set<String> = []      // 这次启动里已经确定没图的（404 或问过），不再重复问

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

    /// 库页看到一张卡时调一次：**只有背景的第二层需要它** —— 玩家已经挑了背景、或者盘上已有刮来的图，
    /// 都不发请求。取到就给这张卡补上背景用的图（`fetched/` 缓存由 LibraryBackdropArt 写盘）。
    func needBackdropArt(_ game: LibraryGame) {
        let tid = game.tid
        guard game.background == nil, game.fetchedArt == nil,
              !fetching.contains(tid), !noArt.contains(tid) else { return }
        if FileManager.default.fileExists(atPath: LibraryArt.fetchedMissingURL(tid).path) {
            noArt.insert(tid)
            fputs("library: backdrop \(tid) none (asked before), the default illustration is used\n", stderr)
            return
        }
        fetching.insert(tid)
        LibraryBackdropArt.fetch(tid) { [weak self] outcome in
            DispatchQueue.main.async {
                guard let self else { return }
                self.fetching.remove(tid)
                switch outcome {
                case .image(let picture):
                    if let i = self.games.firstIndex(where: { $0.tid == tid }) { self.games[i].fetchedArt = picture }
                case .none:
                    self.noArt.insert(tid)
                case .failed(let why):
                    // 网络抖动不记成「没有」：下次启动／下次刷新再试
                    fputs("library: backdrop \(tid) not fetched (\(why)); the default illustration is used\n", stderr)
                }
            }
        }
    }

    func start(_ game: LibraryGame) {
        guard starting == nil else { return }
        starting = game.tid
        errors[game.tid] = nil
        let tid = game.tid
        // The cheats page wrote its file and switches when it was last touched, but its @AppStorage values are the truth and
        // its onAppear may never have run for this game: put them in place here, before the host loads them. The folder is
        // passed in because DM_CHEATS is only set by dm_shell_start_game, which has not run yet (Launcher.swift's Play
        // button makes the same call for a one-game package, where the environment already points at ns2ios/cheats).
        CheatsSection.applySaved(dir: LibraryArt.cheatsURL(tid))
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
                    // 宿主已经接过去了：这一款就是「上次玩的」，下次回库它排最前
                    LibraryState.recordPlayed(tid)
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
    @State private var artGame: LibraryGame?      // whose background the picker is for
    @State private var artError: String?          // 选图/写盘失败时显示在标题下面一行
    @State private var artNote: String?           // 「背景已更新」这种一次性提示，2.6 秒后自己消失
    @State private var artNoteSeq = 0

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
            .photosPicker(isPresented: $picking, selection: $picked, matching: .images)
            .onChange(of: picked) { _, item in
                guard let item, let game = artGame else { return }
                Task { await applyArt(item, to: game) }
            }
        }
        .preferredColorScheme(.dark)
    }

    // MARK: top bar — weak on purpose, the covers own the screen

    private var topBar: some View {
        HStack(spacing: 10) {
            Text("ns2ios").font(.subheadline.weight(.bold)).foregroundStyle(.white)
            Text(LibText.t("游戏库", "GAME LIBRARY"))
                .font(.caption2.weight(.semibold)).tracking(2)
                .foregroundStyle(.white.opacity(0.85))
            Spacer(minLength: 0)
            Text(LibText.t("\(model.games.count) 款游戏", "\(model.games.count) games"))
                .font(.footnote.weight(.semibold)).foregroundStyle(.white)
            Button { model.refresh() } label: {
                Image(systemName: "arrow.clockwise").font(.system(size: 13, weight: .semibold))
                    .frame(width: 28, height: 28)
            }
            .buttonStyle(.plain)
            .foregroundStyle(.white)
            .accessibilityLabel(LibText.t("刷新", "Refresh"))
        }
    }

    // MARK: the flow

    private var flow: some View {
        GeometryReader { geo in
            // 卡片高度：坤哥 2026-11-11「图标别那么大，背景要多露一点」→ 250 降到 214。
            // 这是一处纯视觉的数字，觉得还大/太小就改这一个值。
            let h = min(geo.size.height, 214)
            // 卡片就是游戏图标，正方形，和真机 Switch 主界面一样；刮来的 16:9 图只做背景，不上卡片。
            // **滚动单元统一按最宽的那张算宽**：留白原来按"当前这张"算，滑动中 current 一变留白就跟着变，
            // 整排内容平移 → 松手被拉回原来的位置（坤哥：第 2、第 3 个很难切）。
            let unit = model.games.map { cellWidth($0, height: h) }.max() ?? h

            ScrollView(.horizontal) {
                LazyHStack(spacing: 22) {
                    ForEach(model.games) { game in
                        Button { artTapped(game) } label: {
                            CoverCell(game: game, width: cellWidth(game, height: h), height: h, focused: game.tid == selected)
                        }
                        .buttonStyle(.plain)
                        .frame(width: unit)
                        .id(game.tid)
                        // 这张卡露出来了：玩家没挑过背景、也还没有刮来的图，就去网上问一次背景用图
                        .task(id: game.tid) { model.needBackdropArt(game) }
                        .scrollTransition(.interactive, axis: .horizontal) { content, phase in
                            content
                                .scaleEffect(phase.isIdentity ? 1 : 0.78)
                                .opacity(phase.isIdentity ? 1 : 0.62)
                        }
                        .disabled(model.starting != nil && model.starting != game.tid)
                    }
                }
                .scrollTargetLayout()
            }
            .contentMargins(.horizontal, max(0, (geo.size.width - unit) / 2), for: .scrollContent)
            .scrollTargetBehavior(.viewAligned)
            .scrollPosition(id: $selected, anchor: .center)
            .scrollIndicators(.hidden)
            .frame(height: h + 18)
        }
        .frame(maxHeight: 232)
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
            if let artError {
                Text(artError).font(.caption2).foregroundStyle(.orange.opacity(0.95)).lineLimit(2)
            }
            if let artNote {
                Label(artNote, systemImage: "checkmark.circle.fill")
                    .font(.caption2).foregroundStyle(.green.opacity(0.95)).lineLimit(2)
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
            // 这个功能原来只有「点封面」一个入口，没有任何提示，用户会以为它不存在
            if current?.background == nil {
                Text(LibText.t("点卡片可设置背景图", "Tap a card to set its background"))
                    .font(.footnote.weight(.semibold)).foregroundStyle(.white.opacity(0.9))
            } else if model.games.count > 1 {
                Text(LibText.t("左右滑动切换", "Swipe to switch"))
                    .font(.footnote.weight(.semibold)).foregroundStyle(.white.opacity(0.9))
            }
        }
        .frame(height: 20)
    }

    /// 一个封面要多宽：横幅按 16:9，图标按正方形（ViewBuilder 里不能声明函数，放在这里）。
    // 卡片就是游戏图标：正方形，和真机 Switch 的主界面一样。刮来的 16:9 图**不上卡片**（那是背景用的）。
    private func cellWidth(_ game: LibraryGame, height: CGFloat) -> CGFloat { height }

    /// 点大封面：给这个游戏设 16:9 背景，直接开相册。
    /// **不要**在 alert 的按钮里开相册：alert 的 action 是在转场开始前就调用的，那一刻宿主 VC 还 present 着
    /// UIAlertController，UIKit 会直接丢弃后面这次 present——而且 picking 从此停在 true，之后每次点击都是
    /// 「true → true」，SwiftUI 认为没变化，就再也不尝试了（2026-11-07 真机症状：点了完全没反应）。
    private func artTapped(_ game: LibraryGame) {
        if selected != game.tid { selected = game.tid }   // 点到旁边的封面时先把它居中，别把图画进邻居的游戏
        artGame = game
        artError = nil
        fputs("library: art tap \(game.tid)\n", stderr)
        picking = true
    }

    /// 把相册里选中的图写进 Documents/ns2ios/art/<tid>.jpg，再让库页重扫一遍。
    /// 每一步都往 log.txt 落一行：上一版这段全是 `try?`，真机上「点了没反应」时日志里一句都没有，没法定位。
    private func applyArt(_ item: PhotosPickerItem, to game: LibraryGame) async {
        picked = nil
        let types = item.supportedContentTypes.map(\.identifier).joined(separator: ",")
        fputs("library: pick \(game.tid) types=\(types)\n", stderr)
        guard let data = try? await item.loadTransferable(type: Data.self) else {
            artError = LibText.t("读不到这张图，换一张再试", "Could not read that picture")
            fputs("library: pick failed (loadTransferable nil, try iCloud download or another picture)\n", stderr)
            return
        }
        guard UIImage(data: data) != nil else {
            artError = LibText.t("这张图无法使用，换一张再试", "That picture cannot be used")
            fputs("library: pick is not a decodable image, \(data.count) bytes\n", stderr)
            return
        }
        let url = LibraryArt.backgroundURL(game.tid)
        do {
            // 原子写：写到一半被打断会留下半个 jpg，下次读出来是 nil，看起来就是「设置没生效」
            try data.write(to: url, options: .atomic)
        } catch {
            artError = LibText.t("保存失败，再试一次", "Could not save the picture")
            fputs("library: write FAILED \(url.path): \(error)\n", stderr)
            return
        }
        let attrs = try? FileManager.default.attributesOfItem(atPath: url.path)
        let size = (attrs?[.size] as? NSNumber)?.int64Value ?? -1
        fputs("library: write ok \(url.lastPathComponent) \(size)/\(data.count) bytes\n", stderr)
        artError = nil
        model.refresh()      // 重扫会把新图读进来，Backdrop 取的是 background，画面自己就换了
        // 明文告诉用户成功了：上一版没有任何反馈，看图又看不出变化，根本没法判断
        flash(LibText.t("背景已更新", "Background updated"))
    }

    /// 标题下面闪一条提示，2.6 秒后自己消失（用序号判重，连点两次不会提前把第二条清掉）。
    private func flash(_ text: String) {
        artNote = text
        artNoteSeq += 1
        let seq = artNoteSeq
        Task {
            try? await Task.sleep(nanoseconds: 2_600_000_000)
            if seq == artNoteSeq { withAnimation(.easeOut(duration: 0.3)) { artNote = nil } }
        }
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
            if let icon = game.icon {
                // 封面就是游戏自己的 icon（坤哥 2026-11-11）。没有 icon 就等于没有封面，下面走占位。
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

// MARK: - Backdrop: the current game's art, full screen and unblurred, under everything

private struct Backdrop: View {
    let game: LibraryGame?
    let animated: Bool

    // 背景三层，按序取（坤哥 2026-11-11 原话：玩家自己挑的 > 网上缓存的 > 默认那张女孩插画）：
    //   ① 玩家用 PhotosPicker 挑的图：Documents/ns2ios/art/<tid>.jpg
    //   ② 刮来的 16:9 图（网络缓存）：Documents/ns2ios/fetched/<tid>.jpg
    //   ③ 包里自带的插画：dm/ios/tools/default-background.jpg（女孩趴在沙发上玩 Switch，shell_build.sh 拷进 dm/）
    // 只有这三层。**封面不参与**（封面是卡片上的 icon），也不拿正方形 icon 当背景。
    private var art: UIImage? {
        game?.background ?? game?.fetchedArt ?? LibraryArt.defaultBackground
    }

    // 模糊半径（pt）。**0 = 原图直接铺满**：这是坤哥点名要的（2026-11-07「几乎看不到内容，不知道成功没有」）。
    // 想加磨砂感就往大调，下面的外扩量会跟着变。30 那种等于把画面抹平，别再设回去。
    private static let blur: CGFloat = 0

    // 只在**有字的地方**压暗，中间整段留给插画：顶栏、标题/按钮带、页脚各一段，中间几乎不压。
    // 原来是平铺 0.55 黑罩，图只剩 25% 亮度；现在是「哪里要读字哪里暗，别处保持原色」。
    private static let scrim = LinearGradient(stops: [
        .init(color: .black.opacity(0.30), location: 0.00),   // 顶栏：只压一点点，别再是一块黑
        .init(color: .black.opacity(0.05), location: 0.16),   // 画面主体：几乎不压
        .init(color: .black.opacity(0.05), location: 0.58),
        .init(color: .black.opacity(0.22), location: 0.74),   // 标题
        .init(color: .black.opacity(0.45), location: 1.00)    // 页脚
    ], startPoint: .top, endPoint: .bottom)

    // 图层的身份按"用的是哪张图"算，不按游戏算：两个游戏都用默认插画时 id 不变，切过去就不会黑一下再亮。
    private var artID: String {
        guard let game else { return "none" }
        if game.background != nil { return game.tid + "/own" }
        if game.fetchedArt != nil { return game.tid + "/fetched" }
        return "default"
    }

    var body: some View {
        ZStack {
            LinearGradient(colors: [Color(red: 0.05, green: 0.06, blue: 0.12), .black],
                           startPoint: .topLeading, endPoint: .bottomTrailing)
            if let art {
                GeometryReader { geo in
                    Image(uiImage: art)
                        .resizable().scaledToFill()
                        // 先外扩两个模糊半径再模糊、最后裁回来：直接模糊的话四周取到的是视野外的黑色，
                        // 屏幕边上会有一圈黑框（opaque: true 下尤其明显）。
                        .frame(width: geo.size.width + 2 * Backdrop.blur,
                               height: geo.size.height + 2 * Backdrop.blur)
                        .blur(radius: Backdrop.blur, opaque: true)
                        .frame(width: geo.size.width, height: geo.size.height)
                        .clipped()
                }
                .overlay(Backdrop.scrim)
                .transition(.opacity)
                .id(artID)
            }
        }
        .ignoresSafeArea()
        .animation(animated ? .easeInOut(duration: 0.45) : nil, value: game?.tid)
    }
}

// 金手指一页：面板要的目录是 main.m 给这个游戏设的 DM_CHEATS，库阶段还没设，所以直接给它。
private struct CheatsPage: View {
    let game: LibraryGame
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        ScrollView {
            // 不加半透明底板：字本身加粗纯白就够了（坤哥 2026-11-08 定的口径）
            CheatsSection(cheatsDir: LibraryArt.cheatsURL(game.tid), tid: game.tid)
                .padding(.horizontal, 24)
                .padding(.top, 52)      // room for the floating back button below
                .padding(.vertical, 12)
        }
        .scrollDismissesKeyboard(.interactively)
        .background {
            ZStack {
                Backdrop(game: game, animated: false)
                Color.black.opacity(0.35)
            }
            .ignoresSafeArea()
        }
        // 整条导航栏都去掉（坤哥 2026-11-10）：留着的透明栏仍然吃掉最上面那一条的点击，内容看着在那儿却点不到。
        // 返回改成浮在左上角的按钮，不再占高度。
        .toolbar(.hidden, for: .navigationBar)
        .overlay(alignment: .topLeading) {
            Button { dismiss() } label: {
                Image(systemName: "chevron.left")
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(width: 40, height: 40)
                    .background(.black.opacity(0.35), in: Circle())
            }
            .padding(.leading, 16)
            .padding(.top, 6)
            .accessibilityLabel(LibText.t("返回", "Back"))
        }
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

    private var busy: Bool { model.starting != nil }

    /// 设置页最上面加一行「着色器」：包里以源码形式带的着色器，首启一次编完（默认），还是边玩边编。
    /// 走的是和其它设置完全相同的机制（JSON 里的一行 → SettingRow 的样式 → 存在 game.<tid>.launcher.DM_SHADER_PREP
    /// → 起游戏时 applySaved() 设进环境 → dm_shader_prep.m 读 DM_SHADER_PREP），所以外观、存档、生效路径都和
    /// 「Touch feedback」「Game rumble」那几行一模一样，不另造一套。
    /// 只在壳构建里有意义（包里的库是源码时才有这一步）；将来包里是真 metallib，这一行拨哪边都不会做事。
    private static func settingsWithShaderPrep(_ data: Data) -> [Setting] {
        let row: [String: Any] = [
            "env": "DM_SHADER_PREP",
            "kind": "toggle",
            "label": LibText.t("首次启动编译全部着色器", "Compile all shaders on first launch"),
            "note": LibText.t("开：首启多花几分钟，之后游戏里不会因为着色器卡顿。关：直接进游戏，新场景顿一下（约 0.8 秒）。",
                              "On: a few minutes on the first launch, then no shader stutter. Off: straight in, and a scene you have not seen stutters (~0.8 s)."),
            "default": "1",
            "on": "1",
            "off": "0",
            "section": LibText.t("着色器", "Shaders"),
            "level": "basic",
        ]
        var rows: [[String: Any]] = []
        if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let list = obj["settings"] as? [[String: Any]] { rows = list }
        rows.insert(row, at: 0)
        guard let merged = try? JSONSerialization.data(withJSONObject: ["settings": rows]),
              let file = try? JSONDecoder().decode(SettingsFile.self, from: merged) else { return [] }
        return file.settings
    }

    var body: some View {
        HStack(alignment: .top, spacing: 26) {
            left.frame(width: 330)
            right
        }
        .padding(.horizontal, 28)
        .padding(.top, 8)
        .padding(.bottom, 20)
        // 这一页整页都是字，背景图让位给可读性：图照旧在，但压一层 0.22 的黑
        .background {
            ZStack {
                Backdrop(game: game, animated: false)
                Color.black.opacity(0.22)
            }
            .ignoresSafeArea()
        }
        .navigationBarTitleDisplayMode(.inline)
        // 这一页不显示游戏名（页脚左边已经有大标题）——顶部那条导航栏的半透明底在照片上会露出一块
        // 「很高的框体」，很难看（坤哥 2026-11-08 点名）。背景去掉，只留返回箭头。
        .toolbarBackground(.hidden, for: .navigationBar)
        .task {
            guard settings == nil else { return }
            let url = game.dmDir.appendingPathComponent("launcher.json")
            // 没有 launcher.json 的游戏（少数）也要有这一行，所以给一份空表当底
            let data = (try? Data(contentsOf: url)) ?? Data("{\"settings\":[]}".utf8)
            let m = LauncherModel(Self.settingsWithShaderPrep(data), prefix: "game." + game.tid)
            settings = m
        }
    }

    // MARK: left: the art, its state, and Play

    private var left: some View {
        VStack(alignment: .leading, spacing: 14) {
            Group {
                // 和库页同一套规矩：玩家挑的背景 > 刮来的背景图 > 游戏 icon（当封面用）。
                if let art = game.background ?? game.fetchedArt ?? game.icon {
                    Image(uiImage: art).resizable().scaledToFill()
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
                    .font(.caption).foregroundStyle(.white.opacity(0.72)).monospacedDigit()
            }

            HStack(spacing: 8) {
                Image(systemName: game.dataOK ? "checkmark.circle.fill" : "tray.and.arrow.down.fill")
                Text(game.dataOK ? (game.chainOK ? LibText.t("可以玩", "Ready") : LibText.t("旧壳打包，建议重打", "Packed for an older shell"))
                                 : LibText.t("缺少游戏数据", "Game data missing"))
            }
            .font(.caption.weight(.medium))
            .foregroundStyle(game.dataOK ? (game.chainOK ? .green : .orange) : .yellow)
            .padding(.horizontal, 10).padding(.vertical, 5)
            

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
                    // 全部平铺：原来每组是一个折叠的二级目录，Performance 只有 HUD 一项也要点开一次，
                    // 而且折叠起来的东西用户看不见（坤哥 2026-11-08：没必要放二级目录，直接显示）
                    ForEach(m.sections, id: \.self) { section in
                        let rows = m.rows(in: section)
                        if !rows.isEmpty {
                            VStack(alignment: .leading, spacing: 2) {
                                HStack(spacing: 6) {
                                    Text(section).font(.subheadline.weight(.bold)).foregroundStyle(.white)
                                    Spacer(minLength: 0)
                                    Text("\(rows.count)").font(.caption2.weight(.semibold)).foregroundStyle(.white.opacity(0.75))
                                }
                                .padding(.top, 10).padding(.bottom, 2)
                                ForEach(rows) { row in
                                    SettingRow(s: row, m: m).padding(.vertical, 2)
                                    Divider().overlay(Color.white.opacity(0.12))
                                }
                            }
                        }
                    }
                    HStack {
                        Button(LibText.t("恢复测试默认值", "Reset to tested defaults")) {
                            withAnimation { m.reset() }
                        }
                        .font(.footnote.weight(.semibold)).foregroundStyle(.white).disabled(!m.changed)
                        Spacer(minLength: 0)
                        Text(LibText.t("改动在点 PLAY 时保存", "Saved when you press PLAY"))
                            .font(.caption2.weight(.semibold)).foregroundStyle(.white)
                    }
                    .padding(.top, 4)
                } else {
                    Text(LibText.t("这个游戏没有启动选项。", "This game has no launch options."))
                        .font(.footnote.weight(.semibold)).foregroundStyle(.white)
                }
                // 金手指**不再**放这里：库页底部已经有独立的「金手指」按钮，这里是重复入口，去掉
            }
            .padding(.bottom, 24)
        }
        .scrollDismissesKeyboard(.interactively)
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
