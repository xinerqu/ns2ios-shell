// Library.swift — the game library page of a shell app that holds several games (dm_show_library, called by main.m with
// dm_shell_library_needed). It lists every Frameworks/DmGame<16 hex digits>.framework in the app bundle and starts the one
// the player picks.
// 文件说明：这个文件是 iOS 壳工程的游戏库页。数据来源全部是只读的文件：每个游戏 framework 里的 game.json（名字、版本、chain）
// 与 icon.png；游戏数据在 Documents/ns2ios/games/<tid>/{romfs.img,msl,aoc}，romfs.img 存在且大小 > 0 才算这个游戏可以玩；
// 壳的着色器链号在 Bundle.main 的 Info.plist 键 DMChain（可能不存在）。点卡片会调用 main.m 的 dm_shell_start_game(tid)
// （设环境 + dlopen framework + 预加载），成功就把 tid 交给 picked，失败就在卡片下面显示错误，库本身不关闭。
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
    var id: String { tid }
    static func == (a: LibraryGame, b: LibraryGame) -> Bool { a.tid == b.tid }
    func hash(into h: inout Hasher) { h.combine(tid) }
}

// MARK: - Scanning

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
                                     icon: loadIcon(item.appendingPathComponent("icon.png"))))
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

private struct LibraryView: View {
    @ObservedObject var model: LibraryModel

    var body: some View {
        NavigationStack {
            GeometryReader { geo in
                let columns = gridColumns(geo.size)
                VStack(alignment: .leading, spacing: 0) {
                    header
                    ScrollView {
                        if model.games.isEmpty {
                            emptyState.padding(.top, 32)
                        } else if let columns {
                            LazyVGrid(columns: columns, spacing: 14) {
                                cards(vertical: true)
                            }
                            .padding(EdgeInsets(top: 10, leading: 20, bottom: 28, trailing: 20))
                        } else {
                            LazyVStack(spacing: 12) {
                                cards(vertical: false)
                            }
                            .padding(EdgeInsets(top: 10, leading: 20, bottom: 28, trailing: 20))
                        }
                    }
                }
            }
            .background { heroBackground }
            .navigationDestination(for: LibraryGame.self) { game in
                GameDetail(model: model, game: game)
            }
        }
        .preferredColorScheme(.dark)   // 库页固定深色：模糊图标底 + 深色卡片，两种系统外观下都成立
    }

    // 背景：用第一个（或正在启动的那个）游戏的图标铺满、模糊、压暗；没有游戏时只有一层渐变。
    @ViewBuilder private var heroBackground: some View {
        ZStack {
            Color(uiColor: .systemBackground)
            if let art = (model.starting.flatMap { tid in model.games.first { $0.tid == tid } } ?? model.games.first)?.icon {
                Image(uiImage: art)
                    .resizable().scaledToFill()
                    .blur(radius: 60, opaque: true)
                    .opacity(0.55)
                    .overlay(Color.black.opacity(0.25))
                    .ignoresSafeArea()
            }
            LinearGradient(colors: [Color.black.opacity(0.05), Color.black.opacity(0.55)],
                           startPoint: .top, endPoint: .bottom).ignoresSafeArea()
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Text(LibText.t("游戏库", "Library"))
                .font(.largeTitle.weight(.bold))
            Spacer(minLength: 0)
            Button {
                model.refresh()
            } label: {
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 15, weight: .semibold))
                    .frame(width: 34, height: 34)
            }
            .buttonStyle(.bordered)
            .buttonBorderShape(.circle)
            .accessibilityLabel(LibText.t("刷新", "Refresh"))
        }
        .padding(.horizontal, 20)
        .padding(.top, 12)
        .padding(.bottom, 4)
    }

    /// 横屏或 iPad 用自适应网格，竖屏一行一个。
    private func gridColumns(_ size: CGSize) -> [GridItem]? {
        let wide = size.width > size.height || min(size.width, size.height) >= 600
        guard wide else { return nil }
        return [GridItem(.adaptive(minimum: 220), spacing: 12, alignment: .top)]
    }

    @ViewBuilder
    private func cards(vertical: Bool) -> some View {
        ForEach(model.games) { game in
            card(game, vertical: vertical)
        }
    }

    private func card(_ game: LibraryGame, vertical: Bool) -> some View {
        NavigationLink(value: game) {
            if vertical {
                verticalCard(game)
            } else {
                horizontalCard(game)
            }
        }
        .buttonStyle(LibraryCardStyle())
        .disabled(model.starting != nil && model.starting != game.tid)
        .opacity(model.starting == nil || model.starting == game.tid ? 1 : 0.4)
    }

    private func horizontalCard(_ game: LibraryGame) -> some View {
        HStack(alignment: .top, spacing: 12) {
            icon(game)
            VStack(alignment: .leading, spacing: 3) {
                Text(game.name).font(.headline).lineLimit(1)
                .minimumScaleFactor(0.72).truncationMode(.tail)
                versionLine(game)
                status(game)
                errorLine(game)
            }
            Spacer(minLength: 0)
            if model.starting == game.tid {
                ProgressView().controlSize(.small)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func verticalCard(_ game: LibraryGame) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top) {
                icon(game)
                Spacer(minLength: 0)
                if model.starting == game.tid {
                    ProgressView().controlSize(.small)
                }
            }
            Text(game.name).font(.headline).lineLimit(1)
                .minimumScaleFactor(0.72).truncationMode(.tail)
            versionLine(game)
            status(game)
            errorLine(game)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func icon(_ game: LibraryGame) -> some View {
        Group {
            if let image = game.icon {
                Image(uiImage: image).resizable().scaledToFill()
            } else {
                ZStack {
                    Color(uiColor: .tertiarySystemFill)
                    Image(systemName: "gamecontroller.fill")
                        .font(.system(size: 26))
                        .foregroundStyle(.secondary)
                }
            }
        }
        .frame(width: 72, height: 72)
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.08), lineWidth: 0.5)
        )
        .accessibilityHidden(true)
    }

    @ViewBuilder
    private func versionLine(_ game: LibraryGame) -> some View {
        if let version = game.version, !version.isEmpty {
            Text("v\(version)")
                .font(.subheadline)
                .monospacedDigit()
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func status(_ game: LibraryGame) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            if game.dataOK {
                statusLine("checkmark.circle.fill", .green, LibText.t("可以玩", "Ready to play"))
            } else {
                statusLine("exclamationmark.triangle.fill", .yellow,
                           LibText.t("缺少游戏数据，把 romfs.img 放到 Files 里的 ns2ios/games/\(game.tid)/",
                                     "Game data missing — put romfs.img in Files/ns2ios/games/\(game.tid)/"))
            }
            if !game.chainOK {
                statusLine("clock.arrow.circlepath", .secondary,
                           LibText.t("这个游戏是用旧版打的，建议重新打包",
                                     "Packed with an older shell — repacking is recommended"))
            }
        }
    }

    private func statusLine(_ symbol: String, _ tint: Color, _ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 5) {
            Image(systemName: symbol)
                .font(.caption.weight(.semibold))
                .foregroundStyle(tint)
            Text(text)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
    }

    @ViewBuilder
    private func errorLine(_ game: LibraryGame) -> some View {
        if let error = model.errors[game.tid], !error.isEmpty {
            HStack(alignment: .firstTextBaseline, spacing: 5) {
                Image(systemName: "exclamationmark.circle.fill")
                    .font(.caption.weight(.semibold))
                Text(error)
                    .font(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .foregroundStyle(.red)
        }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "gamecontroller")
                .font(.system(size: 44, weight: .light))
                .foregroundStyle(.tertiary)
            Text(LibText.t("没有找到游戏", "No games found"))
                .font(.title3.weight(.semibold))
            Text("这个 App 里还没有游戏。请把游戏的 framework 放到 Frameworks/DmGame<tid>.framework，把数据放到 Files 里的 ns2ios/games/<tid>/romfs.img。")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Text("No games in this app yet. Put each game's framework in Frameworks/DmGame<tid>.framework and its data in Files/ns2ios/games/<tid>/romfs.img.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            if let message = model.bootMessage, !message.isEmpty {
                Text(message)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.leading)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
                    .background(
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .fill(Color(uiColor: .secondarySystemBackground))
                    )
            }
            Button(LibText.t("关闭", "Close")) { model.cancel() }
                .buttonStyle(.bordered)
                .padding(.top, 4)
        }
        .padding(.horizontal, 28)
        .padding(.vertical, 24)
        .frame(maxWidth: 520)
        .frame(maxWidth: .infinity)
    }
}

// 二级页面：一个游戏的大图、名字、版本、状态和 Play。设置页仍由 Launcher.swift 在 Play 之后弹出（它读这个游戏的 launcher.json），
// 所以这里只负责"选它"和"说明它现在能不能玩"。
private struct GameDetail: View {
    @ObservedObject var model: LibraryModel
    let game: LibraryGame

    private var busy: Bool { model.starting != nil }
    private var canPlay: Bool { game.dataOK && game.chainOK }

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                artwork
                    .frame(width: 168, height: 168)
                    .clipShape(RoundedRectangle(cornerRadius: 36, style: .continuous))
                    .shadow(color: .black.opacity(0.45), radius: 24, y: 12)
                VStack(spacing: 6) {
                    Text(game.name)
                        .font(.title2.weight(.bold))
                        .multilineTextAlignment(.center)
                        .minimumScaleFactor(0.7)
                    if let v = game.version, !v.isEmpty {
                        Text("v\(v)").font(.subheadline).foregroundStyle(.secondary)
                    }
                    Text(game.tid).font(.caption2.monospaced()).foregroundStyle(.tertiary)
                }
                statusPill
                if let e = model.errors[game.tid] {
                    Text(e)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 8)
                }
                playButton
                Text(LibText.t("点 Play 后先出现这个游戏的设置页，确认后再进入游戏。",
                               "Play opens this game's settings first; the game starts from there."))
                    .font(.footnote).foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            .padding(.horizontal, 28)
            .padding(.top, 24)
            .padding(.bottom, 40)
            .frame(maxWidth: 520)
            .frame(maxWidth: .infinity)
        }
        .background { background }
        .navigationTitle(LibText.t("游戏", "Game"))
        .navigationBarTitleDisplayMode(.inline)
    }

    @ViewBuilder private var artwork: some View {
        if let image = game.icon {
            Image(uiImage: image).resizable().scaledToFill()
        } else {
            ZStack {
                Color(uiColor: .tertiarySystemFill)
                Image(systemName: "gamecontroller.fill").font(.system(size: 54)).foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder private var background: some View {
        ZStack {
            Color(uiColor: .systemBackground)
            if let image = game.icon {
                Image(uiImage: image).resizable().scaledToFill()
                    .blur(radius: 70, opaque: true).opacity(0.5)
                    .overlay(Color.black.opacity(0.3))
                    .ignoresSafeArea()
            }
            LinearGradient(colors: [Color.black.opacity(0.1), Color.black.opacity(0.6)],
                           startPoint: .top, endPoint: .bottom).ignoresSafeArea()
        }
    }

    private var statusPill: some View {
        HStack(spacing: 6) {
            Image(systemName: game.dataOK ? (game.chainOK ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                                          : "tray.and.arrow.down.fill")
            Text(game.dataOK ? (game.chainOK ? LibText.t("可以玩", "Ready")
                                             : LibText.t("用旧版打过，建议重新打包", "Packed for another shell"))
                             : LibText.t("缺少游戏数据", "Game data missing"))
        }
        .font(.footnote.weight(.medium))
        .padding(.horizontal, 12).padding(.vertical, 6)
        .background(.ultraThinMaterial, in: Capsule())
        .overlay(Capsule().strokeBorder(.white.opacity(0.15)))
    }

    private var playButton: some View {
        Button {
            model.start(game)
        } label: {
            HStack(spacing: 8) {
                if model.starting == game.tid {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "play.fill")
                }
                Text(LibText.t("开始游戏", "Play")).font(.headline)
            }
            .frame(maxWidth: .infinity, minHeight: 52)
        }
        .buttonStyle(.borderedProminent)
        .buttonBorderShape(.capsule)
        .disabled(busy || !canPlay)
        .padding(.top, 6)
    }
}

private struct LibraryCardStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(Color(uiColor: .secondarySystemBackground))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(Color.primary.opacity(configuration.isPressed ? 0.07 : 0))
            )
            .scaleEffect(configuration.isPressed ? 0.985 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

// MARK: - Entry point (main.m: dm_show_library)

// Shows the library over the root view controller: picked(tid) once a game is entered, cancelled() when the empty page is closed.
// 是否显示库由调用方决定（main.m 用 dm_shell_library_needed）；这个出口只负责呈现，多退出一个库页面并不合适。
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
