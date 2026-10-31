// Cheats on the iPhone, in the launcher itself: paste Atmosphère codes, switch on the entries wanted, play.
//
// The text goes to Documents/cheats/cheats.txt and the switched-on names to DM_CHEATS_ON, which hle_cheats.c reads at
// startup (dm/host/hle_cheats.c: it parses the file, then turns on the entries named in that variable). The file is named
// cheats.txt rather than by this build's ID, so DM_CHEATS_ANY_BUILD is set too: the host takes the folder's one .txt when
// it has no file of its own build. The panel therefore never has to know the build ID.
//
// One app holds one game, so the codes cannot be handed to a different game by accident. They CAN belong to a different
// VERSION of this game -- the addresses in them are absolute -- which is why the footer names the version the app carries.
import SwiftUI
import Foundation

struct CheatsSection: View {
    @AppStorage("cheats.text") private var text: String = ""
    @AppStorage("cheats.on") private var onNames: String = ""
    @State private var expanded = false

    private var names: [String] { Self.entryNames(text) }
    private var selected: Set<String> { Set(onNames.split(separator: "\n").map(String.init)) }
    private var version: String { (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? "?" }

    var body: some View {
        Section {
            if names.isEmpty && !expanded {
                Text("Paste the codes below, then switch on the entries you want.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(names, id: \.self) { name in
                Toggle(name, isOn: Binding(get: { selected.contains(name) }, set: { setOn(name, $0) }))
                    .font(.callout)
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
            Text("Atmosphère format. The addresses belong to the version this app carries (\(version)), so codes written for another version can crash the game. On the iPhone only memory values apply: codes that change instructions do nothing.")
        }
        .onAppear {
            // A launch that goes straight through (no editing) still needs last time's file and names in place.
            if !text.isEmpty { write(text) }
            applyEnv(log: false)
        }
    }

    // MARK: - state changes

    private func setText(_ v: String) {
        text = v
        let live = Set(Self.entryNames(v))
        let kept = Set(names.filter { live.contains($0) && selected.contains($0) })
        if kept != selected { onNames = names.filter { kept.contains($0) }.joined(separator: "\n") }
        write(v)
        applyEnv(log: false)
        fputs("launcher: cheats \(names.count) entries, \(kept.count) on\n", stderr)
    }

    private func setOn(_ name: String, _ on: Bool) {
        var s = selected
        if on { s.insert(name) } else { s.remove(name) }
        onNames = names.filter { s.contains($0) }.joined(separator: "\n")
        applyEnv(log: true)
    }

    // MARK: - the two things the host reads

    /// DM_CHEATS_ON is the entries to switch on; ANY_BUILD lets a file that is not named after this build be used.
    /// Overwrites what an env file set: this panel is the switch for these two, and it shows the state it applied.
    private func applyEnv(log: Bool) {
        let on = names.filter { selected.contains($0) }
        if on.isEmpty { unsetenv("DM_CHEATS_ON") } else { setenv("DM_CHEATS_ON", on.joined(separator: ","), 1) }
        setenv("DM_CHEATS_ANY_BUILD", "1", 1)
        if log {
            fputs("launcher: cheats \(names.count) entries, \(on.count) on\(on.isEmpty ? "" : ": " + on.joined(separator: ", "))\n", stderr)
        }
    }

    /// The codes themselves, where hle_cheats.c looks first (DM_CHEATS points at Documents/cheats). Empty text removes
    /// the file, so a cleared panel cannot quietly keep applying last time's codes.
    private func write(_ v: String) {
        let f = Self.dir.appendingPathComponent("cheats.txt")
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

    private static var dir: URL {
        let d = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("cheats", isDirectory: true)
        if !FileManager.default.fileExists(atPath: d.path) {
            try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        }
        return d
    }

    /// The entry names in Atmosphère's format: a line that is only "[name]". The host's parser is the authority — it also
    /// refuses a name glued to a previous line's code word — so this list is what the panel offers, not what is applied.
    private static func entryNames(_ text: String) -> [String] {
        var out: [String] = []
        for raw in text.split(whereSeparator: { $0.isNewline }) {
            let line = String(raw).trimmingCharacters(in: .whitespaces)
            guard line.count > 2, line.hasPrefix("["), line.hasSuffix("]") else { continue }
            let name = String(line.dropFirst().dropLast()).trimmingCharacters(in: .whitespaces)
            if name.isEmpty || name.hasPrefix("--") || out.contains(name) { continue }   // [--SectionStart:Foo--] markers
            out.append(name)
        }
        return out
    }
}
