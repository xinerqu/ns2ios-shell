// Cheats on the iPhone, in the launcher itself: paste Atmosphère codes, switch on the entries wanted, play.
//
// The text goes to Documents/cheats/cheats.txt and the switched-on names to DM_CHEATS_ON, which hle_cheats.c reads at
// startup (dm/host/hle_cheats.c: it parses the file, then turns on the entries named in that variable). Upstream's loader
// only reads a .txt named after a build ID, so this fork's copy also accepts that fixed name under DM_CHEATS_ANY_BUILD
// (and only as the fallback: a real <build>.txt still wins). The panel therefore never has to know the build ID.
//
// What happened on the last run comes back the other way: hle_cheats.c writes Documents/cheats/last-run.txt with one line
// per entry (state, name, reason) and this screen shows it under the switches. A phone has no cheats menu, so without that
// the only trace of "this one was turned off, and why" would be the log.
//
// One app holds one game, so the codes cannot be handed to a different game by accident. They CAN belong to a different
// VERSION of this game -- the addresses in them are absolute -- which is what the header's TID/BID and the build check in
// the footer are for.
import SwiftUI
import Foundation

// How the panel tells one game from another: "" for a one-game package (the keys it always used), "game.<title ID>" otherwise
// (main.m sets DM_GAME_KEY and DM_APP_DM). CheatsView.dir follows DM_CHEATS, so the file and these keys must agree.
private func cheatKeySuffix() -> String { gameSettingsPrefix() }

struct CheatsSection: View {
    /// Where this game's cheat files live. nil: the environment's DM_CHEATS, else Documents/cheats (the one-game app). The library's
    /// game page passes ns2ios/cheats/<title ID> itself, because DM_CHEATS is only set once that game is started.
    var cheatsDir: URL? = nil

    @AppStorage("cheats.text" + cheatKeySuffix()) private var text: String = ""
    @AppStorage("cheats.on" + cheatKeySuffix()) private var onNames: String = ""
    @AppStorage("cheats.allowCode" + cheatKeySuffix()) private var allowCode = false   // off: entries that patch instructions are not switchable
    @State private var expanded = false
    @State private var lastRun: [String: RunState] = [:]
    @State private var lastBuild: String = ""
    @State private var crashed: [String] = []   // the entries the previous run died with (its armed file), off this launch
    @State private var guessedCode: Set<String> = []   // entries whose fixed-address writes land in main's text

    struct RunState { let state: String; let why: String; let measured: String }   // measured: "code" / "data" / ""

    static let nl = String(UnicodeScalar(10))

    /// The shell's own screens are English (nothing here has a localization table); this section is the one a player reads
    /// before starting, so it speaks the phone's language. zh-Hans/zh-Hant both count as Chinese.
    private static var isChinese: Bool { (Locale.preferredLanguages.first ?? "").hasPrefix("zh") }
    private static func L(_ en: String, _ cn: String) -> String { isChinese ? cn : en }

    // [Name] and {Name} are both switches here: the panel is the only menu a phone has, and DM_CHEATS_PANEL makes the
    // host treat a {Name} master as an ordinary entry instead of always running it.
    private var names: [String] { Self.entryNames(text) }
    private var selected: Set<String> { Self.selectedSet(onNames) }
    private var version: String { (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? "?" }
    private var declared: String? { Self.declaredBuild(text) }  // the BID the pasted file says it was written for
    private var offLastRun: [String] {
        names.filter { lastRun[$0].map { $0.state == "dead" || $0.state == "unsupported" } ?? false }
    }

    /// Entries that patch instructions: measured when the host has run them (last-run.txt's 4th column), guessed from the
    /// file otherwise. The guess only covers writes to a fixed main offset; anything that follows a pointer is data.
    private var codeEntries: Set<String> { guessedCode }
    private func isCode(_ name: String) -> Bool {
        if let m = lastRun[name]?.measured, !m.isEmpty { return m == "code" }
        return guessedCode.contains(name)
    }

    var body: some View {
        Section {
            if !crashed.isEmpty {
                Text(Self.L("The last run crashed with these on, so they are off now: \(crashed.joined(separator: ", ")). Switch one back on only if you want to try it again.",
                             "上次开着这些时崩溃了，已自动关闭：\(crashed.joined(separator: "、"))。想再试请手动打开。"))
                    .font(.footnote).foregroundStyle(.orange)
            }
            if names.isEmpty && !expanded {
                Text(Self.L("Paste the codes below, then switch on the entries you want.", "把金手指文本粘贴到下面，然后打开你要的条目。"))
                    .font(.footnote.weight(.medium)).foregroundStyle(.white.opacity(0.85))
            }
            ForEach(names, id: \.self) { name in
                VStack(alignment: .leading, spacing: 2) {
                    Toggle(name, isOn: Binding(get: { selected.contains(name) }, set: { setOn(name, $0) }))
                        .font(.body.weight(.semibold)).foregroundStyle(.white)
                        .disabled(isCode(name) && !allowCode)
                    if isCode(name) {
                        Text(Self.L("Patches instructions — the iPhone crashes on these, so they start off. Turn on \"Allow code patches\" to try one.",
                                    "改代码的条目 —— iPhone 上会崩，所以默认关闭。想试请先打开下面的「允许改代码」。"))
                            .font(.footnote).foregroundStyle(.orange)
                    } else if lastRun[name]?.measured == "data" {
                        Text(Self.L("Changes values — safe", "改数值 —— 安全")).font(.footnote.weight(.medium)).foregroundStyle(.white.opacity(0.85))
                    }
                    if let st = lastRun[name], st.state == "dead" || st.state == "unsupported" {
                        Text(st.state == "dead" ? Self.L("Turned off on the last run: ", "上次被关闭：") + Self.reason(st.why) : Self.L("Not supported: ", "不支持：") + Self.reason(st.why))
                            .font(.footnote).foregroundStyle(.orange)
                    }
                }
            }
            if !names.isEmpty {
                Toggle(Self.L("Allow code patches (they crash the game on the iPhone)", "允许改代码（iPhone 上会崩，谨慎）"), isOn: $allowCode)
                    .font(.body.weight(.semibold)).foregroundStyle(.white)
            }
            DisclosureGroup(isExpanded: $expanded) {
                TextEditor(text: Binding(get: { text }, set: setText))
                    .font(.system(.callout, design: .monospaced))
                    .frame(minHeight: 220)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .accessibilityLabel("Cheat codes")
            } label: {
                Text(names.isEmpty ? Self.L("Paste cheat codes", "粘贴金手指") : Self.L("Cheat codes (\(names.count) entries)", "金手指（\(names.count) 条）"))
                    .font(.body.weight(.semibold)).foregroundStyle(.white)
            }
        } header: {
            Text(Self.L("Cheats", "金手指"))
        } footer: {
            VStack(alignment: .leading, spacing: 4) {
                if let d = declared, !lastBuild.isEmpty, d != lastBuild {
                    Text(Self.L("These codes are for build \(d), this app has \(lastBuild). Addresses from another version can crash the game: paste the codes for this version.",
                                "这份金手指是给 \(d) 写的，本机是 \(lastBuild)。别的版本的地址可能导致崩溃，请粘贴对应版本的金手指。"))
                        .foregroundStyle(.orange)
                }
                if !offLastRun.isEmpty {
                    Text(Self.L("Last run: \(offLastRun.count) of \(names.count) entries were turned off (the reason is under each one). Codes that rewrite an instruction the iOS rewriter replaced cannot work here; codes that change values do.",
                             "上次运行：\(names.count) 条里有 \(offLastRun.count) 条被关闭（原因写在各自下面）。改写器改过的地方改不了；改数值的条目可以正常工作。"))
                }
                Text(Self.L("Atmosphère format. The addresses belong to the version this app carries (\(version)).",
                          "Atmosphère 格式。地址属于本 App 携带的版本（\(version)）。"))
            }.font(.footnote.weight(.medium)).foregroundStyle(.white.opacity(0.9))
        }
        .onAppear {
            loadArmed()                          // a run died with these on: they are off from now, and why is on screen
            guessedCode = Self.codeSet(text)     // its own estimate first: it needs no run, and last-run.txt may not exist yet
            loadLastRun()                        // what the host made of the last run; the measured column overrides the estimate
            pruneCode()
            Self.applySaved()                    // puts the saved text and switches in place, for a launch that goes straight to Play
        }
        .onChange(of: allowCode) { _, on in
            if !on { pruneCode() }   // the switch that allowed them is gone: so are they
        }
    }

    // MARK: - state changes

    private func setText(_ v: String) {
        text = v
        guessedCode = Self.codeSet(v)   // new text, new estimate: the badges and the kept switches follow it
        let live = Set(Self.entryNames(v))
        let kept = Set(names.filter { live.contains($0) && selected.contains($0) })
        if kept != selected { onNames = names.filter { kept.contains($0) }.joined(separator: "\n") }
        pruneCode()
        Self.apply(text: v, on: names.filter { kept.contains($0) && (allowCode || !isCode($0)) }, log: false)
        fputs("launcher: cheats \(names.count) entries, \(kept.count) on\n", stderr)
    }

    private func setOn(_ name: String, _ on: Bool) {
        var s = selected
        if on { s.insert(name) } else { s.remove(name) }
        onNames = names.filter { s.contains($0) }.joined(separator: "\n")
        Self.apply(text: text, on: names.filter { s.contains($0) }, log: true)
    }

    // MARK: - what the host reads and writes

    /// Called from the Play button: the section sits below the fold, so onAppear alone cannot be relied on to have run
    /// before the game starts. Reads what was saved and puts it in place.
    static func applySaved() {
        let d = UserDefaults.standard
        let text = d.string(forKey: "cheats.text") ?? ""
        var want = selectedSet(d.string(forKey: "cheats.on") ?? "")
        if !d.bool(forKey: "cheats.allowCode") { want.subtract(codeSet(text)) }   // instruction patches stay out
        apply(text: text, on: entryNames(text).filter { want.contains($0) }, log: false)
    }

    /// DM_CHEATS_ON is the entries to switch on; ANY_BUILD lets the fixed file name be read without the build ID.
    /// Overwrites what an env file set: this panel is the switch for these two, and it shows the state it applied.
    private static func apply(text: String, on: [String], log: Bool) {
        if on.isEmpty { unsetenv("DM_CHEATS_ON") } else { setenv("DM_CHEATS_ON", on.joined(separator: ","), 1) }
        setenv("DM_CHEATS_ANY_BUILD", "1", 1)
        write(text)
        if log {
            fputs("launcher: cheats \(entryNames(text).count) entries, \(on.count) on\(on.isEmpty ? "" : ": " + on.joined(separator: ", "))\n", stderr)
        }
    }

    /// The codes themselves, where hle_cheats.c looks first (DM_CHEATS points at Documents/cheats). Empty text removes
    /// the file, so a cleared panel cannot quietly keep applying last time's codes.
    ///
    /// The name is not free: the host only reads a .txt named after a build ID, so this fork's hle_cheats.c accepts
    /// cheats.txt under DM_CHEATS_ANY_BUILD (still only the fallback -- a real <build>.txt wins). One app holds one game,
    /// so a fixed name cannot pick up another game's codes.
    private static func write(_ v: String) {
        let f = dir.appendingPathComponent("cheats.txt")
        do {
            if v.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                if FileManager.default.fileExists(atPath: f.path) { try FileManager.default.removeItem(at: f) }
                return
            }
            try v.write(to: f, atomically: true, encoding: .utf8)
            fputs("launcher: cheats written to \(f.path) (\(v.count) characters)\n", stderr)
        } catch {
            fputs("[RED] launcher: cannot write \(f.path): \(error.localizedDescription)\n", stderr)
        }
    }

    /// The host arms a file while a set of entries is on and removes it once the run has lasted long enough (hle_cheats.c
    /// armed_write). Finding it here means the previous run died with those entries on, and this screen is the first thing
    /// that runs in this one: it turns them off and says so, instead of leaving the panel showing switches the game will not
    /// honour until the launch after next.
    private func loadArmed() {
        guard let items = try? FileManager.default.contentsOfDirectory(at: dirURL, includingPropertiesForKeys: nil) else { return }
        for f in items where f.lastPathComponent.hasPrefix(".cheats-armed-") {
            guard let s = try? String(contentsOf: f, encoding: .utf8) else { continue }
            var names: [String] = []
            for (i, line) in s.split(whereSeparator: { $0.isNewline }).enumerated() where i > 0 {   // line 0 is the build
                let n = String(line).trimmingCharacters(in: .whitespaces)
                if !n.isEmpty { names.append(n) }
            }
            guard !names.isEmpty else { continue }
            crashed = names
            let live = Set(names)
            let kept = selected.subtracting(live)
            if kept != selected {
                onNames = self.names.filter { kept.contains($0) }.joined(separator: "\n")
                fputs("launcher: cheats \(selected.count - kept.count) entries turned off (the last run died with them on)\n", stderr)
            }
            // Consumed here, so the host does not turn them off again at Play: after this banner the player may switch one
            // back on to try it, and that has to be the last word.
            try? FileManager.default.removeItem(at: f)
        }
    }

    /// What the host made of every entry last time: "# build <bid> <version>", then "state<TAB>name<TAB>reason".
    private func loadLastRun() {
        let f = dirURL.appendingPathComponent("last-run.txt")
        guard let s = try? String(contentsOf: f, encoding: .utf8) else { return }
        var states: [String: RunState] = [:]
        var build = ""
        for line in s.split(whereSeparator: { $0.isNewline }) {
            if line.hasPrefix("#") {
                let parts = line.split(separator: " ").map(String.init)
                if parts.count >= 3, parts[1] == "build" { build = parts[2].uppercased() }
                continue
            }
            let f4 = line.split(separator: "\t", maxSplits: 3, omittingEmptySubsequences: false).map(String.init)
            guard f4.count >= 2 else { continue }
            states[f4[1]] = RunState(state: f4[0], why: f4.count > 2 ? f4[2] : "", measured: f4.count > 3 ? f4[3] : "")
        }
        lastRun = states
        lastBuild = build
        guessedCode = Self.codeSet(text)
        pruneCode()
        // The host turned the suspects off at its load, but its load happens at Play: this panel is what puts them back in
        // DM_CHEATS_ON, so it has to drop them too. Without this the crash repeats every launch -- the guard never gets
        // ahead of the toggle state the player cannot see.
        let suspects = states.filter { $0.value.why.contains("crashed within") }.map(\.key)
        if !suspects.isEmpty {
            let kept = selected.subtracting(suspects)
            if kept != selected {
                onNames = names.filter { kept.contains($0) }.joined(separator: "\n")
                fputs("launcher: cheats dropped \(selected.count - kept.count) suspect entries from the switches (the last run crashed with them on)\n", stderr)
            }
        }
    }

    private var dirURL: URL { cheatsDir ?? Self.dir }
    private static var dir: URL {
        // DM_CHEATS is where the host reads cheats from: Documents/cheats for a one-game package, ns2ios/cheats/<title ID> for an app that
        // holds several games (main.m). Writing to the fixed path would have made the panel and the host disagree.
        let d = ProcessInfo.processInfo.environment["DM_CHEATS"].map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("cheats", isDirectory: true)
        if !FileManager.default.fileExists(atPath: d.path) {
            try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        }
        return d
    }

    private static func selectedSet(_ s: String) -> Set<String> { Set(s.split(separator: "\n").map(String.init)) }

    /// The host stores a reason as "(off: …)" and the row already says "turned off", so the prefix it repeats goes.
    private static func reason(_ s: String) -> String { s.hasPrefix("off: ") ? String(s.dropFirst(5)) : s }

    /// Keeps instruction patches out of DM_CHEATS_ON while they are not allowed: the host would take them, and the game
    /// dies on the first one it executes.
    private func pruneCode() {
        guard !allowCode else { return }
        let code = Set(names.filter { isCode($0) })
        guard !code.isEmpty else { return }
        let kept = selected.subtracting(code)
        if kept != selected { onNames = names.filter { kept.contains($0) }.joined(separator: Self.nl) }
    }

    /// The estimate: an entry whose fixed-address stores point into main's text (below TEXT_END) patches instructions.
    /// An entry that follows a pointer at all (58xxxxxx) writes data and is never marked. TEXT_END comes from the app's own
    /// dm/main.fixups.tsv, so this stays in step with the build; without that file nothing is marked.
    static func codeSet(_ text: String) -> Set<String> {
        guard let end = textEnd else { return [] }
        var out: Set<String> = []
        var cur: String?
        var pointer = false
        func settle() { if let c = cur, pointer { out.remove(c) } }
        for raw in text.split(whereSeparator: { $0.isNewline }) {
            let line = String(raw).trimmingCharacters(in: .whitespaces)
            if line.isEmpty { continue }
            if (line.hasPrefix("[") && line.hasSuffix("]")) || (line.hasPrefix("{") && line.hasSuffix("}")) {
                settle()
                cur = String(line.dropFirst().dropLast()).trimmingCharacters(in: .whitespaces)
                pointer = false
                continue
            }
            guard let c = cur else { continue }
            let f = line.split(separator: " ", omittingEmptySubsequences: true)
            guard f.count >= 2, f[0].count == 8, let op = UInt32(f[0], radix: 16) else { continue }
            let kind = op >> 28
            if kind == 5 { pointer = true; continue }                       // 58xxxxxx: loads a pointer, writes go to the object
            guard kind == 0 || kind == 4 || kind == 8 else { continue }      // fixed-address stores
            if let addr = UInt64(f[1], radix: 16), addr < end { out.insert(c) }
        }
        settle()
        return out
    }

    /// TEXT_END from the app's own table; nil when this build has none, and then no entry is marked.
    static var textEnd: UInt64? {
        let url = Bundle.main.bundleURL.appendingPathComponent("dm/main.fixups.tsv")
        guard let s = try? String(contentsOf: url, encoding: .utf8) else { return nil }
        for line in s.split(whereSeparator: { $0.isNewline }) where line.hasPrefix("#") {
            let p = line.split(separator: "\t")
            guard p.count >= 2, p[0].contains("TEXT_END") else { continue }
            var v = p[1].trimmingCharacters(in: .whitespaces)
            if v.hasPrefix("0x") || v.hasPrefix("0X") { v.removeFirst(2) }   // the table writes 0x2836550
            return UInt64(v, radix: 16)
        }
        return nil
    }

    /// The BID the pasted text claims ("… TID: 0100… BID: 9F221E1AFA65BA54", usually its first line).
    private static func declaredBuild(_ text: String) -> String? {
        guard let r = text.range(of: "BID: *([0-9A-Fa-f]{16})", options: .regularExpression) else { return nil }
        return String(text[r].suffix(16)).uppercased()
    }

    /// The entry names in Atmosphère's format: a line that is only "[name]". The host's parser is the authority -- it also
    /// refuses a name glued to a previous line's code word -- so this list is what the panel offers, not what is applied.
    /// "[--SectionStart:Name--]" markers are dropped; a "{}" line is a master (see masterNames).
    static func entryNames(_ text: String) -> [String] {
        var out: [String] = []
        for raw in text.split(whereSeparator: { $0.isNewline }) {
            let line = String(raw).trimmingCharacters(in: .whitespaces)
            guard line.count > 2 else { continue }
            let open = line.first, close = line.last
            guard (open == "[" && close == "]") || (open == "{" && close == "}") else { continue }
            let name = String(line.dropFirst().dropLast()).trimmingCharacters(in: .whitespaces)
            if name.isEmpty || name.hasPrefix("--") || out.contains(name) { continue }   // [--SectionStart:Foo--] markers
            out.append(name)
        }
        return out
    }
}
