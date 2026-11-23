// Dot: an always-on black dot in the top-right corner. Option+Space opens a prompt bar;
// the request goes to the local agent (python -m dot --daemon) over stdin/stdout.
// Esc while running = Stop (halts before the agent's next action). Esc when idle = collapse.
// Build: dot/mac/build.sh  (needs Xcode command line tools: xcode-select --install)
import AppKit
import Carbon

final class DotPanel: NSPanel {
    var onEscape: (() -> Void)?
    override var canBecomeKey: Bool { true }
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
    override func mouseDown(with event: NSEvent) { onClick?() }
}

final class Agent {
    private var proc: Process?
    private var stdin: FileHandle?
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
        p.standardError = FileHandle.nullDevice
        outPipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let d = h.availableData
            guard let self = self, !d.isEmpty else { return }
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
            DispatchQueue.main.async { self?.onEvent?(["type": "error", "text": "agent process exited"]) }
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
        guard let s = stdin, let d = try? JSONSerialization.data(withJSONObject: msg) else { return }
        s.write(d)
        s.write("\n".data(using: .utf8)!)
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
        buildPanel()
        registerHotKey()
        agent.onEvent = { [weak self] ev in self?.handle(ev) }
        agent.start()
        collapse()
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

        input = NSTextField(frame: NSRect(x: 14, y: barSize.height - 46, width: barSize.width - 28 - 70, height: 30))
        input.placeholderString = "Ask Dot anything, or tell it what to do"
        input.font = .systemFont(ofSize: 15)
        input.bezelStyle = .roundedBezel
        input.focusRingType = .none
        input.target = self
        input.action = #selector(submit)
        bar.addSubview(input)

        stopButton = NSButton(title: "Stop", target: self, action: #selector(stop))
        stopButton.frame = NSRect(x: barSize.width - 14 - 62, y: barSize.height - 46, width: 62, height: 30)
        stopButton.bezelStyle = .rounded
        stopButton.isEnabled = false
        bar.addSubview(stopButton)

        let scroll = NSScrollView(frame: NSRect(x: 14, y: 12, width: barSize.width - 28, height: barSize.height - 66))
        scroll.hasVerticalScroller = true
        scroll.drawsBackground = false
        output = NSTextView(frame: scroll.bounds)
        output.isEditable = false
        output.drawsBackground = false
        output.font = .systemFont(ofSize: 13)
        output.textColor = .labelColor
        output.autoresizingMask = [.width]
        scroll.documentView = output
        bar.addSubview(scroll)

        dot = DotView(frame: NSRect(x: 0, y: 0, width: dotSize, height: dotSize))
        dot.onClick = { [weak self] in self?.toggle() }
        root.addSubview(dot)
        panel.orderFrontRegardless()
    }

    func collapse() {
        expanded = false
        let s = screenFrame()
        bar.isHidden = true
        dot.isHidden = false
        dot.frame = NSRect(x: 0, y: 0, width: dotSize, height: dotSize)
        panel.setFrame(NSRect(x: s.maxX - dotSize - margin, y: s.maxY - dotSize - margin,
                              width: dotSize, height: dotSize), display: true)
        panel.orderFrontRegardless()
    }

    func expand() {
        let front = NSWorkspace.shared.frontmostApplication?.localizedName ?? ""
        context = ["app": front]
        expanded = true
        let s = screenFrame()
        panel.setFrame(NSRect(x: s.maxX - barSize.width - margin, y: s.maxY - barSize.height - margin,
                              width: barSize.width, height: barSize.height), display: true)
        bar.isHidden = false
        dot.isHidden = true
        panel.makeKeyAndOrderFront(nil)
        panel.makeFirstResponder(input)
    }

    var context: [String: String] = [:]

    func toggle() { expanded ? collapse() : expand() }

    func escape() {
        if running { stop() } else { collapse() }
    }

    // MARK: agent
    @objc func submit() {
        let text = input.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !running else { return }
        output.string = "› \(text)\n"
        input.stringValue = ""
        setRunning(true)
        agent.send(["type": "ask", "text": text, "context": context])
    }

    @objc func stop() {
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
            if !expanded { dot.color = status == "answered" ? .black : .systemRed }
        case "error":
            setRunning(false)
            append("⚠︎ \(ev["text"] as? String ?? "error")")
            dot.color = .systemRed
        default:
            break
        }
    }

    // MARK: hotkey (Option+Space)
    func registerHotKey() {
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, _, _ -> OSStatus in
            DispatchQueue.main.async { AppDelegate.shared?.toggle() }
            return noErr
        }, 1, &spec, nil, nil)
        let id = EventHotKeyID(signature: OSType(0x444F_5421), id: 1)  // "DOT!"
        RegisterEventHotKey(UInt32(kVK_Space), UInt32(optionKey), id, GetApplicationEventTarget(), 0, &hotKeyRef)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
