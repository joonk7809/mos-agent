// Dot: an always-on black dot in the top-right corner. Option+Space opens a prompt bar;
// the request goes to the local agent (python -m dot --daemon) over stdin/stdout.
// On submit the bar collapses back to the dot and focus returns to your app, so the agent's typing
// and clicks land there (and not on Dot). The dot turns grey while running; the answer pops up when done.
// Stop (halts before the agent's next action): Option+Esc anywhere, Option+Space while running,
// a click on the grey dot, or Esc/Stop in the bar. Esc when idle = collapse.
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
        // Get out of the way: collapse to the dot and hand the keyboard back to the user's app.
        collapse()
        panel.resignKey()
        previousApp?.activate(options: [])
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

    // MARK: hotkeys: Option+Space = open/close (Stop while running), Option+Esc = Stop
    var stopKeyRef: EventHotKeyRef?

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
        let sig = OSType(0x444F_5421)  // "DOT!"
        RegisterEventHotKey(UInt32(kVK_Space), UInt32(optionKey), EventHotKeyID(signature: sig, id: 1),
                            GetApplicationEventTarget(), 0, &hotKeyRef)
        RegisterEventHotKey(UInt32(kVK_Escape), UInt32(optionKey), EventHotKeyID(signature: sig, id: 2),
                            GetApplicationEventTarget(), 0, &stopKeyRef)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
