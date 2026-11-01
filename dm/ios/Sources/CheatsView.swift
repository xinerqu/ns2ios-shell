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

struct CheatsSection: View {
    @AppStorage("cheats.text") private var text: String = ""
    @AppStorage("cheats.on") private var onNames: String = ""
    @State private var expanded = false
    @State private var lastRun: [String: RunState] = [:]
    @State private var lastBuild: String = ""

    struct RunState { let state: String; let why: String }

    // [Name] and {Name} are both switches here: the panel is the only menu a phone has, and DM_CHEATS_PANEL makes the
    // host treat a {Name} master as an ordinary entry instead of always running it.
    private var names: [String] { Self.entryNames(text) }
    private var selected: Set<String> { Self.selectedSet(onNames) }
    private var version: String { (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? "?" }
    private var declared: String? { Self.declaredBuild(text) }  // the BID the pasted file says it was written for
    private var offLastRun: [String] {
        names.filter { lastRun[$0].map { $0.state == "dead" || $0.state == "unsupported" } ?? false }
    }

    var body: some View {
        Section {
            if names.isEmpty && !expanded {
                Text("Paste the codes below, then switch on the entries you want.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(names, id: \.self) { name in
                VStack(alignment: .leading, spacing: 2) {
                    Toggle(name, isOn: Binding(get: { selected.contains(name) }, set: { setOn(name, $0) }))
                        .font(.callout)
                    if let st = lastRun[name], st.state == "dead" || st.state == "unsupported" {
                        Text(st.state == "dead" ? "Turned off on the last run: \(Self.reason(st.why))" : "Not supported: \(Self.reason(st.why))")
                            .font(.caption2).foregroundStyle(.orange)
                    }
                }
            }
            DisclosureGroup(isExpanded: $expanded) {
                TextEditor(text: Binding(get: { text }, set: setText))
                    .font(.system(.footnote, design: .monospaced))
                    .frame(minHeight: 180)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .accessibilityLabel("Cheat codes")
            } label: {
                Text(names.isEmpty ? "Paste cheat codes" : "Cheat codes (\(names.count) entries)")
                    .font(.callout)
            }
        } header: {
            Text("Cheats")
        } footer: {
            VStack(alignment: .leading, spacing: 4) {
                if let d = declared, !lastBuild.isEmpty, d != lastBuild {
                    Text("These codes are for build \(d), this app has \(lastBuild). Addresses from another version can crash the game: paste the codes for this version.")
                        .foregroundStyle(.orange)
                }
                if !offLastRun.isEmpty {
                    Text("Last run: \(offLastRun.count) of \(names.count) entries were turned off (the reason is under each one). Codes that rewrite an instruction the iOS rewriter replaced cannot work here; codes that change values do.")
                }
                Text("Atmosphère format. The addresses belong to the version this app carries (\(version)).")
            }.font(.caption2)
        }
        .onAppear {
            loadLastRun()          // what the host made of the last run, for the notes under the switches
            Self.applySaved()      // puts the saved text and switches in place, for a launch that goes straight to Play
        }
    }

    // MARK: - state changes

    private func setText(_ v: String) {
        text = v
        let live = Set(Self.entryNames(v))
        let kept = Set(names.filter { live.contains($0) && selected.contains($0) })
        if kept != selected { onNames = names.filter { kept.contains($0) }.joined(separator: "\n") }
        Self.apply(text: v, on: names.filter { kept.contains($0) }, log: false)
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
        let want = selectedSet(d.string(forKey: "cheats.on") ?? "")
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

    /// What the host made of every entry last time: "# build <bid> <version>", then "state<TAB>name<TAB>reason".
    private func loadLastRun() {
        let f = Self.dir.appendingPathComponent("last-run.txt")
        guard let s = try? String(contentsOf: f, encoding: .utf8) else { return }
        var states: [String: RunState] = [:]
        var build = ""
        for line in s.split(whereSeparator: { $0.isNewline }) {
            if line.hasPrefix("#") {
                let parts = line.split(separator: " ").map(String.init)
                if parts.count >= 3, parts[1] == "build" { build = parts[2].uppercased() }
                continue
            }
            let f3 = line.split(separator: "\t", maxSplits: 2, omittingEmptySubsequences: false).map(String.init)
            guard f3.count >= 2 else { continue }
            states[f3[1]] = RunState(state: f3[0], why: f3.count > 2 ? f3[2] : "")
        }
        lastRun = states
        lastBuild = build
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

    private static var dir: URL {
        let d = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("cheats", isDirectory: true)
        if !FileManager.default.fileExists(atPath: d.path) {
            try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        }
        return d
    }

    private static func selectedSet(_ s: String) -> Set<String> { Set(s.split(separator: "\n").map(String.init)) }

    /// The host stores a reason as "(off: …)" and the row already says "turned off", so the prefix it repeats goes.
    private static func reason(_ s: String) -> String { s.hasPrefix("off: ") ? String(s.dropFirst(5)) : s }

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
