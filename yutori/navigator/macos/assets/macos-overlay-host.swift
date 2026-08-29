import AppKit
import Carbon.HIToolbox
import CoreVideo
import QuartzCore
import WebKit

private let overlayProtocolVersion = 2

private func writeJSON(_ value: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: value) else { return }
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

private struct OverlayConfig: Decodable {
    let showStopButton: Bool
    let enableHotkey: Bool
}

private final class OverlayApp: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    private let htmlURL: URL
    private let config: OverlayConfig
    private var panel: NSPanel?
    private var stopPanel: NSPanel?
    private var stopRegion: [String: Double]?
    private var webView: WKWebView?
    private var screen: NSScreen?
    private var hotKey: EventHotKeyRef?
    private var hotKeyHandler: EventHandlerRef?
    private var displayLinks: [UUID: CVDisplayLink] = [:]
    private var captureID = 0
    private var transitionToken = 0
    private var stopped = false
    private var state = "starting"

    init(htmlURL: URL, config: OverlayConfig) {
        self.htmlURL = htmlURL
        self.config = config
    }

    deinit {
        if let hotKey { UnregisterEventHotKey(hotKey) }
        if let hotKeyHandler { RemoveEventHandler(hotKeyHandler) }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard let screen = NSScreen.main else {
            writeJSON(["error": "No main display is available."])
            NSApp.terminate(nil)
            return
        }
        self.screen = screen

        let panel = NSPanel(
            contentRect: screen.frame,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.backgroundColor = .clear
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.hasShadow = false
        panel.hidesOnDeactivate = false
        panel.ignoresMouseEvents = true
        panel.isOpaque = false
        panel.isReleasedWhenClosed = false
        panel.level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.overlayWindow)))
        panel.alphaValue = 0

        let webView = WKWebView(frame: NSRect(origin: .zero, size: screen.frame.size))
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")
        panel.contentView = webView
        panel.orderFrontRegardless()

        self.panel = panel
        self.webView = webView
        if config.showStopButton { createStopPanel(on: screen) }
        let hotkeyAvailable = config.enableHotkey && registerStopHotKey()
        panel.identifier = NSUserInterfaceItemIdentifier(hotkeyAvailable ? "n2-overlay-hotkey" : "n2-overlay-no-hotkey")
        webView.loadFileURL(htmlURL, allowingReadAccessTo: htmlURL.deletingLastPathComponent())
    }

    private func createStopPanel(on screen: NSScreen) {
        let size = NSSize(width: 146, height: 34)
        let origin = NSPoint(
            x: screen.visibleFrame.maxX - size.width - 16,
            y: screen.visibleFrame.maxY - size.height - 16
        )
        let panel = NSPanel(
            contentRect: NSRect(origin: origin, size: size),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.backgroundColor = .clear
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.hasShadow = false
        panel.hidesOnDeactivate = false
        panel.ignoresMouseEvents = false
        panel.isOpaque = false
        panel.isReleasedWhenClosed = false
        panel.level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.overlayWindow)) + 1)
        panel.alphaValue = 0

        let configuration = WKWebViewConfiguration()
        configuration.userContentController.add(self, name: "stop")
        let webView = WKWebView(frame: NSRect(origin: .zero, size: size), configuration: configuration)
        webView.setValue(false, forKey: "drawsBackground")
        webView.loadHTMLString(
            """
            <!doctype html><meta name="color-scheme" content="dark"><style>
            *{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;background:transparent;font-family:-apple-system,BlinkMacSystemFont,sans-serif}
            button{width:100%;height:100%;border:1px solid rgba(208,252,235,.5);border-radius:11px;color:rgba(240,255,250,.96);font-size:12px;font-weight:600;letter-spacing:.1px;background:radial-gradient(circle at 24% 12%,rgba(236,255,248,.22),transparent 48%),linear-gradient(145deg,rgba(24,170,126,.78),rgba(8,70,53,.72));-webkit-backdrop-filter:blur(16px) saturate(150%);box-shadow:inset 3px 4px 9px rgba(255,255,255,.08),inset -5px -6px 11px rgba(3,22,17,.16),inset -2px -3px 4px rgba(210,255,238,.3);cursor:default}
            button:active{transform:scale(.98)}button:focus-visible{outline:2px solid white;outline-offset:-3px}
            @media(prefers-reduced-transparency:reduce){button{background:rgba(8,70,53,.96);-webkit-backdrop-filter:none}}
            @media(prefers-contrast:more){button{border-color:white;color:white}}
            @media(prefers-reduced-motion:reduce){button{transition:none}}
            </style><button onclick="webkit.messageHandlers.stop.postMessage('button')">Stop · ⇧⌘Esc</button>
            """,
            baseURL: nil
        )
        panel.contentView = webView
        panel.orderFrontRegardless()
        stopRegion = [
            "x": (origin.x - screen.frame.minX) / screen.frame.width * 1000,
            "y": (screen.frame.maxY - origin.y - size.height) / screen.frame.height * 1000,
            "width": size.width / screen.frame.width * 1000,
            "height": size.height / screen.frame.height * 1000,
        ]
        stopPanel = panel
    }

    private func registerStopHotKey() -> Bool {
        var eventType = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let context = Unmanaged.passUnretained(self).toOpaque()
        let status = InstallEventHandler(
            GetApplicationEventTarget(),
            { _, _, userData in
                guard let userData else { return noErr }
                Unmanaged<OverlayApp>.fromOpaque(userData).takeUnretainedValue().requestStop(source: "hotkey")
                return noErr
            },
            1,
            &eventType,
            context,
            &hotKeyHandler
        )
        guard status == noErr else { return false }
        let identifier = EventHotKeyID(signature: 0x4E324355, id: 1)
        return RegisterEventHotKey(
            UInt32(kVK_Escape),
            UInt32(cmdKey | shiftKey),
            identifier,
            GetApplicationEventTarget(),
            0,
            &hotKey
        ) == noErr
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        requestStop(source: "button")
    }

    private func requestStop(source: String) {
        guard !stopped else { return }
        stopped = true
        writeJSON(["event": "stop", "source": source])
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation?) {
        guard let screen else { return }
        let hotkeyAvailable = panel?.identifier?.rawValue == "n2-overlay-hotkey"
        var ready: [String: Any] = [
            "ready": true,
            "protocol_version": overlayProtocolVersion,
            "width": Int(webView.bounds.width.rounded()),
            "height": Int(webView.bounds.height.rounded()),
            "backing_scale": screen.backingScaleFactor,
            "hotkey": hotkeyAvailable,
            "capabilities": ["capture", "encode", "background_tasks", "stop"],
        ]
        if let stopRegion { ready["stop_region"] = stopRegion }
        writeJSON(ready)
        readCommands()
    }

    private func readCommands() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            while let line = readLine() {
                guard !line.isEmpty else { continue }
                guard
                    let data = line.data(using: .utf8),
                    let envelope = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                    let id = envelope["id"] as? Int
                else {
                    writeJSON(["id": -1, "ok": false, "error": "Invalid overlay command."])
                    continue
                }
                DispatchQueue.main.async { self?.handle(id: id, envelope: envelope) }
            }
            DispatchQueue.main.async { self?.retire() }
        }
    }

    private func reply(_ id: Int, state: String? = nil, captureID: Int? = nil) {
        var value: [String: Any] = ["id": id, "ok": true]
        if let state { value["state"] = state }
        if let captureID { value["capture_id"] = captureID }
        writeJSON(value)
    }

    private func fail(_ id: Int, _ message: String) {
        writeJSON(["id": id, "ok": false, "error": message])
    }

    private func handle(id: Int, envelope: [String: Any]) {
        if let operation = envelope["operation"] as? [String: Any] {
            applyOverlayOperation(id: id, operation: operation)
            return
        }
        guard let command = envelope["command"] as? [String: Any], let op = command["op"] as? String else {
            fail(id, "Invalid overlay command.")
            return
        }
        switch op {
        case "arm":
            state = "arming"
            CATransaction.flush()
            waitForDisplayFrames(1) {
                self.state = "armed"
                self.reply(id, state: self.state)
            }
        case "reveal":
            reveal(id: id)
        case "captureHide":
            guard let requestedID = command["capture_id"] as? Int else { return fail(id, "Missing capture id.") }
            hideForCapture(id: id, captureID: requestedID)
        case "captureReveal":
            guard let requestedID = command["capture_id"] as? Int else { return fail(id, "Missing capture id.") }
            revealAfterCapture(id: id, captureID: requestedID)
        case "pulse":
            guard let point = command["point"] as? [String: Any] else { return fail(id, "Missing pulse point.") }
            callJavaScript(id: id, body: "return window.__n2OverlayPulse(point)", arguments: ["point": point])
        case "encode":
            guard
                let data = command["data"] as? String,
                let maxLongSide = command["max_long_side"] as? Int,
                let quality = command["quality"] as? Double
            else { return fail(id, "Invalid encode request.") }
            encodeObservation(id: id, data: data, maxLongSide: maxLongSide, quality: quality)
        case "backgroundTasks":
            guard
                let tasks = command["tasks"] as? [[String: Any]],
                let overflow = command["overflow"] as? Int
            else { return fail(id, "Invalid background task request.") }
            callJavaScript(
                id: id,
                body: "return window.__n2BackgroundTasks(payload)",
                arguments: ["payload": ["tasks": tasks, "overflow": overflow]],
                validateReply: true
            )
        case "retire":
            reply(id, state: "retired")
            retire()
        default:
            fail(id, "Unknown overlay command.")
        }
    }

    private func applyOverlayOperation(id: Int, operation: [String: Any]) {
        callJavaScript(
            id: id,
            body: "return await window.__n2OverlayApply(operation)",
            arguments: ["operation": operation],
            validateReply: true
        )
    }

    private func encodeObservation(id: Int, data: String, maxLongSide: Int, quality: Double) {
        guard let webView else { return fail(id, "Overlay page is unavailable.") }
        webView.callAsyncJavaScript(
            "return await window.__n2EncodeObservation(payload)",
            arguments: [
                "payload": [
                    "data": data,
                    "maxLongSide": maxLongSide,
                    "quality": quality,
                ],
            ],
            in: nil,
            in: .page
        ) { result in
            switch result {
            case .success(let value):
                guard
                    let encoded = value as? [String: Any],
                    let data = encoded["data"] as? String,
                    let format = encoded["format"] as? String
                else { return self.fail(id, "Observation encoder returned invalid data.") }
                writeJSON(["id": id, "ok": true, "encoded": ["data": data, "format": format]])
            case .failure:
                self.fail(id, "Observation encoding failed.")
            }
        }
    }

    private func callJavaScript(
        id: Int,
        body: String,
        arguments: [String: Any],
        validateReply: Bool = false
    ) {
        guard let webView else { return fail(id, "Overlay page is unavailable.") }
        webView.callAsyncJavaScript(body, arguments: arguments, in: nil, in: .page) { result in
            switch result {
            case .success(let value):
                if validateReply,
                   !((value as? [String: Any])?["ok"] as? Bool == true) {
                    self.fail(id, "Overlay runtime rejected operation.")
                } else {
                    self.reply(id)
                }
            case .failure:
                self.fail(id, "Overlay operation failed.")
            }
        }
    }

    private func reveal(id: Int) {
        guard state == "armed" else { return fail(id, "Overlay is not armed.") }
        state = "revealing"
        animate(visible: true, duration: 0.12, framesAfter: 0) {
            self.state = "visible"
            self.reply(id, state: self.state)
        }
    }

    private func hideForCapture(id: Int, captureID requestedID: Int) {
        guard requestedID > captureID else { return reply(id, state: "stale", captureID: requestedID) }
        captureID = requestedID
        transitionToken += 1
        let token = transitionToken
        state = "hiding"
        animate(visible: false, duration: 0.06, framesAfter: 2, includeStopPanel: false) {
            guard token == self.transitionToken else { return self.reply(id, state: "stale", captureID: requestedID) }
            self.state = "hidden"
            self.reply(id, state: self.state, captureID: requestedID)
        }
    }

    private func revealAfterCapture(id: Int, captureID requestedID: Int) {
        guard requestedID == captureID else { return reply(id, state: "stale", captureID: requestedID) }
        transitionToken += 1
        let token = transitionToken
        state = "revealing"
        waitForDisplayFrames(1) {
            guard token == self.transitionToken else { return self.reply(id, state: "stale", captureID: requestedID) }
            self.animate(visible: true, duration: 0.12, framesAfter: 0, includeStopPanel: false) {
                guard token == self.transitionToken else { return self.reply(id, state: "stale", captureID: requestedID) }
                self.state = "visible"
                self.reply(id, state: self.state, captureID: requestedID)
            }
        }
    }

    private func animate(
        visible: Bool,
        duration: TimeInterval,
        framesAfter: Int,
        includeStopPanel: Bool = true,
        completion: @escaping () -> Void
    ) {
        let windows = includeStopPanel ? [panel, stopPanel].compactMap { $0 } : [panel].compactMap { $0 }
        if visible { windows.forEach { $0.orderFrontRegardless() } }
        let effectiveDuration = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion ? 0 : duration
        NSAnimationContext.runAnimationGroup { context in
            context.duration = effectiveDuration
            context.timingFunction = CAMediaTimingFunction(name: .easeOut)
            windows.forEach { $0.animator().alphaValue = visible ? 1 : 0 }
        } completionHandler: {
            CATransaction.flush()
            self.waitForDisplayFrames(framesAfter, completion: completion)
        }
    }

    private func waitForDisplayFrames(_ count: Int, completion: @escaping () -> Void) {
        guard count > 0 else { return completion() }
        var displayLink: CVDisplayLink?
        guard
            CVDisplayLinkCreateWithActiveCGDisplays(&displayLink) == kCVReturnSuccess,
            let displayLink
        else {
            return DispatchQueue.main.asyncAfter(
                deadline: .now() + Double(count) / 60.0,
                execute: completion
            )
        }
        let key = UUID()
        var remaining = count
        let handlerStatus = CVDisplayLinkSetOutputHandler(displayLink) { [weak self] link, _, _, _, _ in
            guard remaining > 0 else { return kCVReturnSuccess }
            remaining -= 1
            guard remaining == 0 else { return kCVReturnSuccess }
            DispatchQueue.main.async {
                CVDisplayLinkStop(link)
                self?.displayLinks.removeValue(forKey: key)
                completion()
            }
            return kCVReturnSuccess
        }
        guard handlerStatus == kCVReturnSuccess else {
            return DispatchQueue.main.asyncAfter(
                deadline: .now() + Double(count) / 60.0,
                execute: completion
            )
        }
        displayLinks[key] = displayLink
        if CVDisplayLinkStart(displayLink) != kCVReturnSuccess {
            displayLinks.removeValue(forKey: key)
            DispatchQueue.main.asyncAfter(
                deadline: .now() + Double(count) / 60.0,
                execute: completion
            )
        }
    }

    private func retire() {
        transitionToken += 1
        displayLinks.values.forEach { CVDisplayLinkStop($0) }
        displayLinks.removeAll()
        [panel, stopPanel].compactMap { $0 }.forEach {
            $0.alphaValue = 0
            $0.orderOut(nil)
            $0.close()
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { NSApp.terminate(nil) }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation?, withError error: Error) {
        writeJSON(["error": "Overlay page failed to load."])
        NSApp.terminate(nil)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation?, withError error: Error) {
        writeJSON(["error": "Overlay page failed to load."])
        NSApp.terminate(nil)
    }
}

if CommandLine.arguments.count == 2, CommandLine.arguments[1] == "--self-test" {
    writeJSON(["protocol_version": overlayProtocolVersion])
    exit(0)
}

guard CommandLine.arguments.count == 3,
      let configData = CommandLine.arguments[2].data(using: .utf8),
      let config = try? JSONDecoder().decode(OverlayConfig.self, from: configData)
else {
    writeJSON(["error": "Expected an overlay HTML path and configuration."])
    exit(2)
}

let application = NSApplication.shared
private let delegate = OverlayApp(
    htmlURL: URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL,
    config: config
)
application.setActivationPolicy(.accessory)
application.delegate = delegate
application.run()
