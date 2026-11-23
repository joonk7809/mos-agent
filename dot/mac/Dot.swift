// Dot: an always-on black dot in the top-right corner. Control+Space (configurable) opens a prompt bar;
// the request goes to the local agent (python -m dot --daemon) over stdin/stdout.
// On submit the bar collapses back to the dot and focus returns to your app, so the agent's typing
// and clicks land there (and not on Dot). The dot turns grey while running; the answer pops up when done.
// Close the bar: Esc, the ✕, or the hotkey again. Quit: right-click the dot > Quit Dot.
// Stop (halts before the agent's next action): Option+Esc anywhere, the open hotkey while running,
// a click on the grey dot, or Esc/Stop in the bar. Esc when idle = collapse.
// Build: dot/mac/build.sh  (needs Xcode command line tools: xcode-select --install)
import AppKit
import Carbon

final class DotPanel: NSPanel {
    var onEscape: (() -> Void)?
    // Key only while the prompt is open for typing; otherwise the user's app keeps the keyboard,
    // so the agent's typing and key presses land there and never in Dot.
    var allowKey = false
    override var canBecomeKey: Bool { allowKey }
    override func cancelOperation(_ sender: Any?) { onEscape?() }
}

final class DotView: NSView {
    var color: NSColor = .black { didSet { needsDisplay = true } }
    var onClick: (() -> Void)?
    override func draw(_ dirtyRect: NSRect) {
        let r = bounds.insetBy(dx: 1, dy: 1)
        color.setFill()
        NSBezierPath(ovalIn: r).fill()
        NSColor.white.withAlphaComponent(0.35).setStroke()
        let ring = NSBezierPath(ovalIn: r)
        ring.lineWidth = 1
        ring.stroke()
    }
    override func mouseDown(with event: NSEvent) {
        if event.modifierFlags.contains(.control), let m = menu {  // Control-click = right-click
            NSMenu.popUpContextMenu(m, with: event, for: self)
        } else {
            onClick?()
        }
    }
}

final class Agent {
    private var proc: Process?
    private var stdin: FileHandle?
    var alive: Bool { proc?.isRunning ?? false }
    private var buffer = Data()
    var onEvent: (([String: Any]) -> Void)?

    func start() {
        let p = Process()
        let env = ProcessInfo.processInfo.environment
        let repo = env["DOT_REPO"] ?? (Bundle.main.object(forInfoDictionaryKey: "DotRepo") as? String) ?? FileManager.default.currentDirectoryPath
        let python = env["DOT_PYTHON"] ?? (Bundle.main.object(forInfoDictionaryKey: "DotPython") as? String) ?? "python3"
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = [python, "-m", "dot", "--daemon"]
        p.currentDirectoryURL = URL(fileURLWithPath: repo)
        let inPipe = Pipe(), outPipe = Pipe()
        p.standardInput = inPipe
        p.standardOutput = outPipe
        // Daemon stderr (Python tracebacks, a missing module) goes to a log you can read.
        let logDir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/Dot")
        try? FileManager.default.createDirectory(at: logDir, withIntermediateDirectories: true)
        let errURL = logDir.appendingPathComponent("daemon.err")
        FileManager.default.createFile(atPath: errURL.path, contents: nil)
        p.standardError = (try? FileHandle(forWritingTo: errURL)) ?? FileHandle.nullDevice
        outPipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let d = h.availableData
            if d.isEmpty { h.readabilityHandler = nil; return }  // EOF: stop polling
            guard let self = self else { return }
            self.buffer.append(d)
            while let nl = self.buffer.firstIndex(of: 0x0A) {
                let line = self.buffer.subdata(in: self.buffer.startIndex..<nl)
                self.buffer.removeSubrange(self.buffer.startIndex...nl)
                if let obj = try? JSONSerialization.jsonObject(with: line) as? [String: Any] {
                    DispatchQueue.main.async { self.onEvent?(obj) }
                }
            }
        }
        p.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                self?.stdin = nil
                self?.onEvent?(["type": "error", "text": "agent process exited (see ~/Library/Logs/Dot/daemon.err)"])
            }
        }
        do {
            try p.run()
            proc = p
            stdin = inPipe.fileHandleForWriting
        } catch {
            onEvent?(["type": "error", "text": "could not start agent: \(error.localizedDescription)"])
        }
    }

    func send(_ msg: [String: Any]) {
        guard alive, let s = stdin, var d = try? JSONSerialization.data(withJSONObject: msg) else { return }
        d.append(0x0A)
        do { try s.write(contentsOf: d) } catch { stdin = nil }  // EPIPE: the daemon is gone
    }

    func stopProcess() {
        send(["type": "quit"])
        proc?.terminate()
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSTextFieldDelegate {
    static var shared: AppDelegate?
    let dotSize: CGFloat = 16, margin: CGFloat = 10
    let barSize = NSSize(width: 460, height: 240)
    var panel: DotPanel!
    var dot: DotView!
    var bar: NSVisualEffectView!
    var input: NSTextField!
    var output: NSTextView!
    var stopButton: NSButton!
    let agent = Agent()
    var expanded = false
    var running = false
    var hotKeyRef: EventHotKeyRef?

    func applicationDidFinishLaunching(_ n: Notification) {
        AppDelegate.shared = self
        NSApp.setActivationPolicy(.accessory)
        signal(SIGPIPE, SIG_IGN)  // a dead daemon surfaces as a write error, not a crash
        buildPanel()
        registerHotKey()
        agent.onEvent = { [weak self] ev in self?.handle(ev) }
        agent.start()
        collapse()
        if !startupNotes.isEmpty {  // hotkey problems: say so once, without taking the keyboard
            output.string = startupNotes.map { "⚠︎ " + $0 }.joined(separator: "\n") + "\n"
            expand(focus: false)
        }
    }

    func applicationWillTerminate(_ n: Notification) { agent.stopProcess() }

    // MARK: layout
    func screenFrame() -> NSRect { (NSScreen.main ?? NSScreen.screens[0]).visibleFrame }

    func buildPanel() {
        panel = DotPanel(contentRect: NSRect(x: 0, y: 0, width: dotSize, height: dotSize),
                         styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.level = .statusBar
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.hidesOnDeactivate = false
        panel.isMovableByWindowBackground = false
        panel.onEscape = { [weak self] in self?.escape() }

        let root = NSView(frame: NSRect(origin: .zero, size: barSize))
        panel.contentView = root

        bar = NSVisualEffectView(frame: root.bounds)
        bar.material = .hudWindow
        bar.state = .active
        bar.wantsLayer = true
        bar.layer?.cornerRadius = 14
        bar.autoresizingMask = [.width, .height]
        root.addSubview(bar)

        input = NSTextField(frame: NSRect(x: 14, y: barSize.height - 46, width: barSize.width - 28 - 70 - 30, height: 30))
        input.placeholderString = "Ask Dot anything, or tell it what to do"
        input.font = .systemFont(ofSize: 15)
        input.bezelStyle = .roundedBezel
        input.focusRingType = .none
        input.target = self
        input.action = #selector(submit)
        bar.addSubview(input)

        stopButton = NSButton(title: "Stop", target: self, action: #selector(stop))
        stopButton.frame = NSRect(x: barSize.width - 14 - 62 - 30, y: barSize.height - 46, width: 62, height: 30)
        stopButton.bezelStyle = .rounded
        stopButton.isEnabled = false
        bar.addSubview(stopButton)

        // × closes the bar back to the dot (Esc does the same once you've clicked into it).
        let close = NSButton(title: "✕", target: self, action: #selector(closeBar))
        close.frame = NSRect(x: barSize.width - 14 - 26, y: barSize.height - 44, width: 26, height: 26)
        close.bezelStyle = .inline
        close.isBordered = false
        close.toolTip = "Close (Esc). Right-click the dot to quit Dot."
        bar.addSubview(close)

        // Right-click (or Control-click) the dot or the bar: Open / Close / Quit Dot.
        let menu = NSMenu()
        menu.addItem(withTitle: "Open Dot", action: #selector(openBar), keyEquivalent: "").target = self
        menu.addItem(withTitle: "Close", action: #selector(closeBar), keyEquivalent: "").target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: "Quit Dot", action: #selector(quit), keyEquivalent: "q").target = self
        bar.menu = menu

        let scroll = NSScrollView(frame: NSRect(x: 14, y: 12, width: barSize.width - 28, height: barSize.height - 66))
        scroll.hasVerticalScroller = true
        scroll.drawsBackground = false
        output = NSTextView(frame: NSRect(origin: .zero, size: scroll.contentSize))
        output.isVerticallyResizable = true
        output.isHorizontallyResizable = false
        output.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        output.textContainer?.widthTracksTextView = true
        output.isEditable = false
        output.drawsBackground = false
        output.font = .systemFont(ofSize: 13)
        output.textColor = .labelColor
        output.autoresizingMask = [.width]
        scroll.documentView = output
        bar.addSubview(scroll)

        dot = DotView(frame: NSRect(x: 0, y: 0, width: dotSize, height: dotSize))
        dot.onClick = { [weak self] in self?.toggle() }
        dot.menu = menu
        root.addSubview(dot)
        panel.orderFrontRegardless()
    }

    func releaseKeyboard() {
        panel.makeFirstResponder(nil)
        panel.allowKey = false
        if panel.isKeyWindow { panel.orderOut(nil) }  // ordering out is what really gives up key status
    }

    func collapse() {
        releaseKeyboard()
        expanded = false
        let s = screenFrame()
        bar.isHidden = true
        dot.isHidden = false
        dot.frame = NSRect(x: 0, y: 0, width: dotSize, height: dotSize)
        panel.setFrame(NSRect(x: s.maxX - dotSize - margin, y: s.maxY - dotSize - margin,
                              width: dotSize, height: dotSize), display: true)
        panel.orderFrontRegardless()
    }

    var previousApp: NSRunningApplication?

    func expand(focus: Bool = true) {
        if focus {
            previousApp = NSWorkspace.shared.frontmostApplication
            context = ["app": previousApp?.localizedName ?? ""]
        }
        expanded = true
        let s = screenFrame()
        panel.setFrame(NSRect(x: s.maxX - barSize.width - margin, y: s.maxY - barSize.height - margin,
                              width: barSize.width, height: barSize.height), display: true)
        bar.isHidden = false
        dot.isHidden = true
        panel.allowKey = true  // clicking into a shown bar (even an answer popup) lets Esc/typing work
        if focus {
            panel.makeKeyAndOrderFront(nil)
            panel.makeFirstResponder(input)
        } else {
            panel.orderFrontRegardless()  // show the answer without taking the keyboard
        }
    }

    var context: [String: String] = [:]

    func toggle() {
        if running { stop(); return }
        expanded ? collapse() : expand()
    }

    @objc func closeBar() { if !running { collapse() } }  // while running the bar is already closed
    @objc func openBar() { if !expanded && !running { expand() } }  // never take the keyboard mid-task
    @objc func quit() {
        if running { agent.send(["type": "stop"]) }
        NSApp.terminate(nil)
    }

    func escape() {
        if running { stop() } else { collapse() }
    }

    // MARK: agent
    @objc func submit() {
        let text = input.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !running else { return }
        output.string = "› \(text)\n"
        input.stringValue = ""
        guard agent.alive else { append("⚠︎ agent isn't running (see ~/Library/Logs/Dot/daemon.err)"); return }
        setRunning(true)
        agent.send(["type": "ask", "text": text, "context": context])
        // Get out of the way: collapse to the dot and hand the keyboard back to the user's app
        // (a non-activating panel never took "frontmost" from it; collapse() gives up key status).
        collapse()
    }

    @objc func stop() {
        guard running else { return }
        agent.send(["type": "stop"])
        append("Stopping before the next action…")
    }

    func setRunning(_ r: Bool) {
        running = r
        stopButton.isEnabled = r
        dot.color = r ? NSColor(white: 0.35, alpha: 1) : .black
    }

    func append(_ line: String) {
        output.string += line + "\n"
        output.scrollToEndOfDocument(nil)
    }

    func handle(_ ev: [String: Any]) {
        let type = ev["type"] as? String ?? ""
        switch type {
        case "tool":
            let name = ev["name"] as? String ?? "?"
            var detail = ""
            if let a = ev["args"] as? [String: Any], let d = try? JSONSerialization.data(withJSONObject: a),
               let s = String(data: d, encoding: .utf8) { detail = String(s.prefix(120)) }
            append("→ \(name) \(detail)")
        case "status":
            if let t = ev["text"] as? String, t != "thinking" { append("· \(t)") }
        case "done":
            setRunning(false)
            let status = ev["status"] as? String ?? ""
            append("\n" + (ev["text"] as? String ?? "") + (status == "answered" ? "" : "  [\(status)]"))
            if status != "answered" { dot.color = .systemRed }
            if !expanded { expand(focus: false) }
        case "error":
            setRunning(false)
            append("⚠︎ \(ev["text"] as? String ?? "error")")
            dot.color = .systemRed
            if !expanded { expand(focus: false) }
        default:
            break
        }
    }

    // MARK: hotkeys
    // Open/close (Stop while running), default Control+Space; Stop, default Option+Esc. Change them with
    //   defaults write dev.mos.dot hotkey "cmd+shift+space"     (or DOT_HOTKEY / build-time DotHotkey)
    //   defaults write dev.mos.dot stopHotkey "ctrl+esc"        (or DOT_STOP_HOTKEY / DotStopHotkey)
    var stopKeyRef: EventHotKeyRef?
    var startupNotes: [String] = []

    func setting(_ env: String, _ defaultsKey: String, _ plistKey: String, _ fallback: String) -> String {
        if let v = ProcessInfo.processInfo.environment[env], !v.isEmpty { return v }
        if let v = UserDefaults.standard.string(forKey: defaultsKey), !v.isEmpty { return v }
        if let v = Bundle.main.object(forInfoDictionaryKey: plistKey) as? String, !v.isEmpty { return v }
        return fallback
    }

    static let keyCodes: [String: Int] = {
        var m: [String: Int] = ["space": kVK_Space, "esc": kVK_Escape, "escape": kVK_Escape, "return": kVK_Return,
                                "enter": kVK_Return, "tab": kVK_Tab, "`": kVK_ANSI_Grave, "/": kVK_ANSI_Slash,
                                ".": kVK_ANSI_Period, ",": kVK_ANSI_Comma, ";": kVK_ANSI_Semicolon,
                                "'": kVK_ANSI_Quote, "-": kVK_ANSI_Minus, "=": kVK_ANSI_Equal]
        let letters: [Int] = [kVK_ANSI_A, kVK_ANSI_B, kVK_ANSI_C, kVK_ANSI_D, kVK_ANSI_E, kVK_ANSI_F, kVK_ANSI_G,
                              kVK_ANSI_H, kVK_ANSI_I, kVK_ANSI_J, kVK_ANSI_K, kVK_ANSI_L, kVK_ANSI_M, kVK_ANSI_N,
                              kVK_ANSI_O, kVK_ANSI_P, kVK_ANSI_Q, kVK_ANSI_R, kVK_ANSI_S, kVK_ANSI_T, kVK_ANSI_U,
                              kVK_ANSI_V, kVK_ANSI_W, kVK_ANSI_X, kVK_ANSI_Y, kVK_ANSI_Z]
        for (i, c) in "abcdefghijklmnopqrstuvwxyz".enumerated() { m[String(c)] = letters[i] }
        let digits: [Int] = [kVK_ANSI_0, kVK_ANSI_1, kVK_ANSI_2, kVK_ANSI_3, kVK_ANSI_4, kVK_ANSI_5, kVK_ANSI_6,
                             kVK_ANSI_7, kVK_ANSI_8, kVK_ANSI_9]
        for i in 0..<10 { m[String(i)] = digits[i] }
        let fkeys: [Int] = [kVK_F1, kVK_F2, kVK_F3, kVK_F4, kVK_F5, kVK_F6, kVK_F7, kVK_F8, kVK_F9, kVK_F10,
                            kVK_F11, kVK_F12]
        for i in 0..<12 { m["f\(i + 1)"] = fkeys[i] }
        return m
    }()

    /// Note: macOS doesn't tell us when another app already owns a combo; the newest registrant usually
    /// wins. If the hotkey does nothing, pick another one.
    /// "ctrl+space" -> (keyCode, Carbon modifiers). nil if unparseable or without a modifier (except F-keys).
    func parseCombo(_ combo: String) -> (UInt32, UInt32)? {
        let parts = combo.lowercased().split(separator: "+").map { $0.trimmingCharacters(in: .whitespaces) }
        guard let key = parts.last, let code = AppDelegate.keyCodes[key] else { return nil }
        var mods = 0
        for m in parts.dropLast() {
            switch m {
            case "ctrl", "control": mods |= controlKey
            case "opt", "alt", "option": mods |= optionKey
            case "cmd", "command": mods |= cmdKey
            case "shift": mods |= shiftKey
            default: return nil
            }
        }
        let isFKey = (1...12).map { "f\($0)" }.contains(key)
        if mods == 0 && !isFKey { return nil }  // a bare letter would swallow that key everywhere
        return (UInt32(code), UInt32(mods))
    }

    /// macOS binds Control+Space to "Select the previous input source" (symbolic hotkey 60) and
    /// Control+Option+Space to "next source" (61). The system wins over our hotkey when they're on.
    func inputSourceConflict(_ code: UInt32, _ mods: UInt32) -> Bool {
        guard code == UInt32(kVK_Space), mods & UInt32(controlKey) != 0, mods & UInt32(cmdKey) == 0,
              mods & UInt32(shiftKey) == 0 else { return false }
        let id = mods & UInt32(optionKey) != 0 ? "61" : "60"
        guard let hk = UserDefaults(suiteName: "com.apple.symbolichotkeys")?
                .dictionary(forKey: "AppleSymbolicHotKeys")?[id] as? [String: Any] else {
            return true  // no entry = macOS default, which is enabled
        }
        if let enabled = hk["enabled"] as? NSNumber, !enabled.boolValue { return false }
        // With a single input source the shortcut has nothing to switch to and doesn't get in the way.
        return typingSourceCount() > 1
    }

    func typingSourceCount() -> Int {
        let sources = UserDefaults(suiteName: "com.apple.HIToolbox")?.array(forKey: "AppleEnabledInputSources") ?? []
        return sources.compactMap { $0 as? [String: Any] }
            .filter { ["Keyboard Layout", "Input Mode"].contains($0["InputSourceKind"] as? String ?? "") }.count
    }

    func register(_ name: String, _ combo: String, id: UInt32, ref: inout EventHotKeyRef?) {
        guard let parsed = parseCombo(combo) else {
            startupNotes.append("Can't read the \(name) hotkey \"\(combo)\". Use e.g. ctrl+space or cmd+shift+k.")
            return
        }
        let (code, mods) = parsed
        let status = RegisterEventHotKey(code, mods, EventHotKeyID(signature: OSType(0x444F_5421), id: id),
                                         GetApplicationEventTarget(), 0, &ref)
        if status != noErr {
            startupNotes.append("Couldn't register \(combo) (error \(status)). Pick another with: "
                                + "defaults write dev.mos.dot \(id == 1 ? "hotkey" : "stopHotkey") \"<combo>\"")
        } else if inputSourceConflict(code, mods) {
            startupNotes.append("\(combo) is also macOS's \"switch input source\" shortcut, which may take it. If so, turn it off in "
                                + "System Settings > Keyboard > Keyboard Shortcuts > Input Sources, then relaunch Dot.")
        }
    }

    func registerHotKey() {
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, event, _ -> OSStatus in
            var hk = EventHotKeyID()
            GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                              nil, MemoryLayout<EventHotKeyID>.size, nil, &hk)
            let which = hk.id
            DispatchQueue.main.async {
                if which == 2 { AppDelegate.shared?.stop() } else { AppDelegate.shared?.toggle() }
            }
            return noErr
        }, 1, &spec, nil, nil)
        register("open", setting("DOT_HOTKEY", "hotkey", "DotHotkey", "ctrl+space"), id: 1, ref: &hotKeyRef)
        register("stop", setting("DOT_STOP_HOTKEY", "stopHotkey", "DotStopHotkey", "opt+esc"), id: 2,
                 ref: &stopKeyRef)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
