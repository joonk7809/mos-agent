// Dot: a small dot rests in the top-right corner. Control+Space (configurable) slides a small chat card up
// into the middle of the screen; ask anything or give it a task. The request goes to the local agent
// (python -m dot --daemon) over stdin/stdout, and the chat keeps context for follow-ups (New Chat resets).
// While a task runs the card stays visible but lets clicks through and hands the keyboard back to your
// app, so the agent's typing and clicks land there, never in Dot. The dot turns green while working.
// Close: Esc, the hotkey, or click elsewhere. Quit: right-click the dot > Quit Dot.
// Stop (halts before the agent's next action): Option+Esc anywhere, the hotkey or a dot click while running.
// Build: dot/mac/build.sh  (needs Xcode command line tools: xcode-select --install)
import AppKit
import Carbon
import QuartzCore

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
        // Apps opened from Finder get no shell environment, so model settings also come from
        // `defaults write dev.mos.dot <key> <value>` (model, modelURL, apiKey, reasoning, maxSide, coords).
        var childEnv = env
        let fromDefaults = ["model": "DOT_MODEL", "modelURL": "DOT_MODEL_URL", "apiKey": "DOT_API_KEY",
                            "reasoning": "DOT_REASONING", "maxSide": "DOT_MAX_SIDE", "coords": "DOT_COORDS"]
        for (key, name) in fromDefaults where childEnv[name] == nil {
            if let v = UserDefaults.standard.string(forKey: key), !v.isEmpty { childEnv[name] = v }
        }
        p.environment = childEnv
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

// MARK: - Look: quiet, monochrome, hairline borders, one accent (light and dark follow the system)

enum Theme {
    static func dyn(_ light: NSColor, _ dark: NSColor) -> NSColor {
        NSColor(name: nil) { $0.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? dark : light }
    }
    static func hex(_ v: UInt32, _ a: CGFloat = 1) -> NSColor {
        NSColor(srgbRed: CGFloat((v >> 16) & 0xFF) / 255, green: CGFloat((v >> 8) & 0xFF) / 255,
                blue: CGFloat(v & 0xFF) / 255, alpha: a)
    }
    static let surface = dyn(hex(0xFFFFFF), hex(0x161618))
    static let border = dyn(hex(0x000000, 0.09), hex(0xFFFFFF, 0.09))
    static let text = dyn(hex(0x0D0D0D), hex(0xECECEE))
    static let secondary = dyn(hex(0x6E6E80), hex(0x8E8EA0))
    static let tertiary = dyn(hex(0xA1A1AA), hex(0x5C5C66))
    static let accent = hex(0x10A37F)  // used only for the "working" pulse
    static let danger = dyn(hex(0xC4373B), hex(0xF0686B))
    static let body = NSFont.systemFont(ofSize: 14)
    static let label = NSFont.systemFont(ofSize: 11.5, weight: .medium)
    static let mono = NSFont.monospacedSystemFont(ofSize: 11.5, weight: .regular)
    static let input = NSFont.systemFont(ofSize: 15)
}

/// The chat card: rounded, hairline border, soft shadow. Its layer colours follow light/dark mode.
final class CardView: NSView {
    override var wantsUpdateLayer: Bool { true }
    override func updateLayer() {
        layer?.backgroundColor = Theme.surface.cgColor
        layer?.borderColor = Theme.border.cgColor
    }
    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.cornerRadius = 14
        layer?.borderWidth = 1
        layer?.masksToBounds = true
    }
    required init?(coder: NSCoder) { fatalError() }
}

final class Hairline: NSView {
    override var wantsUpdateLayer: Bool { true }
    override func updateLayer() { layer?.backgroundColor = Theme.border.cgColor }
    override init(frame: NSRect) { super.init(frame: frame); wantsLayer = true }
    required init?(coder: NSCoder) { fatalError() }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    static var shared: AppDelegate?
    // Resting state: a small dot in the top-right corner.
    let dotSize: CGFloat = 12, margin: CGFloat = 12
    // Chat: a small centred card that slides up from below when summoned.
    let chatWidth: CGFloat = 580, inputHeight: CGFloat = 54, maxTranscript: CGFloat = 340
    var dotPanel: DotPanel!
    var dot: DotView!
    var chat: DotPanel!
    var card: CardView!
    var transcriptScroll: NSScrollView!
    var transcript: NSTextView!
    var divider: Hairline!
    var input: NSTextField!
    var hint: NSTextField!
    let agent = Agent()
    var shown = false
    var running = false
    var hotKeyRef: EventHotKeyRef?
    var context: [String: String] = [:]
    var idleHide: Timer?
    // While a task runs the card shrinks to a one-line status strip at the bottom of the screen,
    // so it hides as little as possible of what the agent sees and clicks.
    var compact: Bool { running }

    func applicationDidFinishLaunching(_ n: Notification) {
        AppDelegate.shared = self
        NSApp.setActivationPolicy(.accessory)
        signal(SIGPIPE, SIG_IGN)  // a dead daemon surfaces as a write error, not a crash
        buildDot()
        buildChat()
        registerHotKey()
        agent.onEvent = { [weak self] ev in self?.handle(ev) }
        agent.start()
        if !startupNotes.isEmpty {  // hotkey problems: say so once, without taking the keyboard
            startupNotes.forEach { appendNote($0, color: Theme.danger) }
            show(focus: false)
            hideLater(60)
        }
    }

    func applicationWillTerminate(_ n: Notification) { agent.stopProcess() }

    func screen() -> NSRect { (NSScreen.main ?? NSScreen.screens[0]).visibleFrame }

    func makeMenu() -> NSMenu {
        let m = NSMenu()
        m.addItem(withTitle: "Open Dot", action: #selector(openChat), keyEquivalent: "").target = self
        m.addItem(withTitle: "New Chat", action: #selector(newChat), keyEquivalent: "n").target = self
        m.addItem(.separator())
        m.addItem(withTitle: "Quit Dot", action: #selector(quit), keyEquivalent: "q").target = self
        return m
    }

    // MARK: dot
    func buildDot() {
        let s = screen()
        dotPanel = DotPanel(contentRect: NSRect(x: s.maxX - dotSize - margin, y: s.maxY - dotSize - margin,
                                                width: dotSize, height: dotSize),
                            styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        dotPanel.level = .statusBar
        dotPanel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        dotPanel.isOpaque = false
        dotPanel.backgroundColor = .clear
        dotPanel.hasShadow = false
        dot = DotView(frame: NSRect(x: 0, y: 0, width: dotSize, height: dotSize))
        dot.onClick = { [weak self] in self?.toggle() }
        dot.menu = makeMenu()
        dotPanel.contentView = dot
        dotPanel.orderFrontRegardless()
    }

    // MARK: chat card
    func label(_ s: String, font: NSFont, color: NSColor) -> NSTextField {
        let l = NSTextField(labelWithString: s)
        l.font = font
        l.textColor = color
        return l
    }

    func buildChat() {
        chat = DotPanel(contentRect: NSRect(x: 0, y: 0, width: chatWidth, height: inputHeight),
                        styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        chat.level = .floating
        chat.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient, .ignoresCycle]
        chat.isOpaque = false
        chat.backgroundColor = .clear
        chat.hasShadow = true
        chat.sharingType = .none  // keep Dot out of screenshots (the agent's own included) where macOS honours it
        chat.hidesOnDeactivate = false
        chat.delegate = self
        chat.onEscape = { [weak self] in self?.escape() }

        card = CardView(frame: NSRect(x: 0, y: 0, width: chatWidth, height: inputHeight))
        card.autoresizingMask = [.width, .height]
        card.menu = makeMenu()
        chat.contentView = card

        // Transcript (top). Hidden until there is something to show.
        transcriptScroll = NSScrollView(frame: .zero)
        transcriptScroll.hasVerticalScroller = true
        transcriptScroll.scrollerStyle = .overlay
        transcriptScroll.drawsBackground = false
        transcriptScroll.borderType = .noBorder
        transcript = NSTextView(frame: NSRect(x: 0, y: 0, width: chatWidth - 40, height: 10))
        transcript.isEditable = false
        transcript.isSelectable = true
        transcript.drawsBackground = false
        transcript.textContainerInset = NSSize(width: 0, height: 14)
        transcript.isVerticallyResizable = true
        transcript.isHorizontallyResizable = false
        transcript.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        transcript.textContainer?.widthTracksTextView = true
        transcript.autoresizingMask = [.width]
        transcriptScroll.documentView = transcript
        card.addSubview(transcriptScroll)

        divider = Hairline(frame: .zero)
        card.addSubview(divider)

        // Input row (bottom): borderless field + a quiet hint on the right.
        input = NSTextField(frame: .zero)
        input.isBordered = false
        input.drawsBackground = false
        input.focusRingType = .none
        input.font = Theme.input
        input.textColor = Theme.text
        input.placeholderAttributedString = NSAttributedString(string: "Ask Dot anything…",
            attributes: [.foregroundColor: Theme.tertiary, .font: Theme.input])
        input.cell?.usesSingleLineMode = true
        input.cell?.lineBreakMode = .byTruncatingTail
        input.target = self
        input.action = #selector(submit)
        card.addSubview(input)

        hint = label("↵", font: Theme.label, color: Theme.tertiary)
        hint.alignment = .right
        card.addSubview(hint)
        layoutChat(animated: false)
    }

    var transcriptHeight: CGFloat {
        guard transcript.textStorage?.length ?? 0 > 0, let lm = transcript.layoutManager,
              let tc = transcript.textContainer else { return 0 }
        lm.ensureLayout(for: tc)
        let h = lm.usedRect(for: tc).height + transcript.textContainerInset.height * 2
        return min(ceil(h), maxTranscript)
    }

    /// Card height follows the content; its top edge stays put (a little above the screen's centre).
    func targetFrame() -> NSRect {
        let s = screen()
        if compact {
            return NSRect(x: (s.midX - chatWidth / 2).rounded(), y: s.minY + 28, width: chatWidth, height: inputHeight)
        }
        let th = transcriptHeight
        let h = inputHeight + (th > 0 ? th + 1 : 0)
        let top = s.midY + min(s.height * 0.18, 180)
        return NSRect(x: (s.midX - chatWidth / 2).rounded(), y: (top - h).rounded(), width: chatWidth, height: h)
    }

    func layoutChat(animated: Bool) {
        let f = targetFrame()
        let th = compact ? 0 : f.height - inputHeight - (f.height > inputHeight ? 1 : 0)
        let pad: CGFloat = 20
        input.frame = NSRect(x: pad, y: (inputHeight - 22) / 2, width: chatWidth - pad * 2 - 120, height: 22)
        hint.frame = NSRect(x: chatWidth - pad - 116, y: (inputHeight - 16) / 2, width: 116, height: 16)
        divider.frame = NSRect(x: 0, y: inputHeight, width: chatWidth, height: 1)
        divider.isHidden = th <= 0
        transcriptScroll.frame = NSRect(x: pad, y: inputHeight + 1, width: chatWidth - pad * 2, height: max(th, 0))
        transcriptScroll.isHidden = th <= 0
        if shown {
            if animated {
                NSAnimationContext.runAnimationGroup { ctx in
                    ctx.duration = 0.12
                    chat.animator().setFrame(f, display: true)
                }
            } else {
                chat.setFrame(f, display: true)
            }
        } else {
            chat.setFrame(f, display: false)
        }
        transcript.scrollToEndOfDocument(nil)
    }

    // MARK: show / hide (slides up from below and fades in; reverse on hide)
    func show(focus: Bool) {
        idleHide?.invalidate()
        if focus {
            context = ["app": NSWorkspace.shared.frontmostApplication?.localizedName ?? ""]
        }
        let f = targetFrame()
        chat.allowKey = !running
        chat.ignoresMouseEvents = running
        if !shown {
            shown = true
            layoutChat(animated: false)
            chat.setFrame(f.offsetBy(dx: 0, dy: -36), display: false)
            chat.alphaValue = 0
            chat.orderFrontRegardless()
            NSAnimationContext.runAnimationGroup { ctx in
                ctx.duration = 0.22
                ctx.timingFunction = CAMediaTimingFunction(controlPoints: 0.2, 0.9, 0.3, 1)
                chat.animator().setFrame(f, display: true)
                chat.animator().alphaValue = 1
            }
        }
        else { layoutChat(animated: true) }  // e.g. back from the compact strip to the full card
        if focus && !running {
            chat.makeKeyAndOrderFront(nil)
            chat.makeFirstResponder(input)
        }
    }

    func hide() {
        guard shown else { return }
        shown = false
        idleHide?.invalidate()
        chat.makeFirstResponder(nil)
        chat.allowKey = false
        let f = chat.frame
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.16
            ctx.timingFunction = CAMediaTimingFunction(name: .easeIn)
            chat.animator().setFrame(f.offsetBy(dx: 0, dy: -24), display: true)
            chat.animator().alphaValue = 0
        }, completionHandler: { [weak self] in
            guard let self = self, !self.shown else { return }
            self.chat.orderOut(nil)
            self.chat.alphaValue = 1
        })
    }

    /// Give the keyboard back to the user's app (ordering out and back in is what really drops key status).
    func releaseKeyboard() {
        chat.makeFirstResponder(nil)
        chat.allowKey = false
        if chat.isKeyWindow {
            chat.orderOut(nil)
            if shown { chat.orderFrontRegardless() }
        }
    }

    // Clicking elsewhere closes an idle chat, like Spotlight.
    func windowDidResignKey(_ notification: Notification) {
        if !running && shown { hide() }
    }

    func toggle() {
        if running { stop(); return }
        if shown { hide() } else { show(focus: true) }  // to type a follow-up into a shown card, click it
    }

    /// A card nobody looks at slides away after a while; clicking it keeps it.
    func hideLater(_ seconds: TimeInterval = 45) {
        idleHide?.invalidate()
        idleHide = Timer.scheduledTimer(withTimeInterval: seconds, repeats: false) { [weak self] _ in
            guard let self = self, !self.running, !self.chat.isKeyWindow else { return }
            self.hide()
        }
    }

    func escape() { if running { stop() } else { hide() } }
    @objc func openChat() { if !running { show(focus: true) } }
    @objc func quit() {
        if running { agent.send(["type": "stop"]) }
        NSApp.terminate(nil)
    }
    @objc func newChat() {
        guard !running else { return }
        agent.send(["type": "reset"])
        transcript.string = ""
        layoutChat(animated: shown)
    }

    // MARK: transcript
    func append(_ s: NSAttributedString) {
        let ts = transcript.textStorage!
        if ts.length > 0 { ts.append(NSAttributedString(string: "\n")) }
        ts.append(s)
        layoutChat(animated: shown)
    }

    func para(_ spacingBefore: CGFloat) -> NSParagraphStyle {
        let p = NSMutableParagraphStyle()
        p.paragraphSpacingBefore = spacingBefore
        p.lineSpacing = 3
        return p
    }

    func appendTurn(role: String, text: String, color: NSColor = Theme.text) {
        let s = NSMutableAttributedString(string: role + "\n", attributes: [
            .font: Theme.label, .foregroundColor: Theme.secondary, .paragraphStyle: para(10)])
        s.append(NSAttributedString(string: text, attributes: [
            .font: Theme.body, .foregroundColor: color, .paragraphStyle: para(2)]))
        append(s)
    }

    func appendNote(_ text: String, color: NSColor = Theme.tertiary) {
        if running {  // the compact strip shows the latest step
            input.placeholderAttributedString = NSAttributedString(string: text,
                attributes: [.foregroundColor: Theme.secondary, .font: Theme.mono])
        }
        append(NSAttributedString(string: text, attributes: [
            .font: Theme.mono, .foregroundColor: color, .paragraphStyle: para(2)]))
    }

    // MARK: agent
    @objc func submit() {
        let text = input.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !running else { return }
        input.stringValue = ""
        if text == "/new" || text == "/clear" { newChat(); return }
        guard agent.alive else {
            appendNote("Dot's agent isn't running. See ~/Library/Logs/Dot/daemon.err", color: Theme.danger)
            return
        }
        appendTurn(role: "You", text: text)
        setRunning(true)
        input.placeholderAttributedString = NSAttributedString(string: text,
            attributes: [.foregroundColor: Theme.secondary, .font: Theme.input])
        layoutChat(animated: true)  // slide down into the compact strip
        agent.send(["type": "ask", "text": text, "context": context])
        // Stay visible to show progress, but let clicks pass through and hand the keyboard back,
        // so the agent's typing and clicks land in the user's app, never in Dot.
        chat.ignoresMouseEvents = true
        releaseKeyboard()
    }

    @objc func stop() {
        guard running else { return }
        agent.send(["type": "stop"])
        hint.stringValue = "Stopping…"
    }

    func setRunning(_ r: Bool) {
        running = r
        dot.color = r ? Theme.accent : .black
        hint.stringValue = r ? "Working · ⌥Esc to stop" : "↵"
        hint.textColor = r ? Theme.secondary : Theme.tertiary
        input.isEditable = !r
        if !r {
            input.placeholderAttributedString = NSAttributedString(string: "Ask a follow-up…",
                attributes: [.foregroundColor: Theme.tertiary, .font: Theme.input])
        }
        if !r {
            chat.ignoresMouseEvents = false
            chat.allowKey = true
        }
    }

    func handle(_ ev: [String: Any]) {
        switch ev["type"] as? String ?? "" {
        case "tool":
            let name = ev["name"] as? String ?? "?"
            var detail = ""
            if let a = ev["args"] as? [String: Any], let d = try? JSONSerialization.data(withJSONObject: a),
               let s = String(data: d, encoding: .utf8), s != "{}" { detail = " " + String(s.prefix(90)) }
            appendNote("→ \(name)\(detail)")
        case "status":
            if let t = ev["text"] as? String, t != "thinking" { appendNote(t) }
        case "done":
            setRunning(false)
            let status = ev["status"] as? String ?? ""
            let text = ev["text"] as? String ?? ""
            appendTurn(role: "Dot", text: text, color: status == "answered" ? Theme.text : Theme.danger)
            if status != "answered" { dot.color = .systemRed }
            show(focus: false)
            hideLater()
        case "error":
            setRunning(false)
            appendNote(ev["text"] as? String ?? "error", color: Theme.danger)
            dot.color = .systemRed
            show(focus: false)
            hideLater(60)
        case "reset":
            transcript.string = ""
            layoutChat(animated: shown)
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
