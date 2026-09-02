import AppKit
import Carbon.HIToolbox
import CoreVideo
import QuartzCore
import WebKit

private let overlayProtocolVersion = 2

// The Yutori mark, from the platform dashboard's yutori-mark.svg (viewBox -2 -2 117 114),
// with its two filled subpaths emitted as CGPath calls because AppKit has no SVG parser.
private let yutoriMarkViewBox = CGRect(x: -2, y: -2, width: 117, height: 114)
private let menuBarIconPoints: CGFloat = 18

private func yutoriMarkGlyph() -> CGPath {
    let path = CGMutablePath()
    path.move(to: CGPoint(x: 101.976, y: 0.821))
    path.addCurve(to: CGPoint(x: 112.792, y: 4.268), control1: CGPoint(x: 106.955, y: -1.13), control2: CGPoint(x: 111.542, y: 0.545))
    path.addCurve(to: CGPoint(x: 107.458, y: 13.782), control1: CGPoint(x: 114.212, y: 8.495), control2: CGPoint(x: 111.387, y: 11.998))
    path.addCurve(to: CGPoint(x: 41.788, y: 75.917), control1: CGPoint(x: 74.847, y: 28.593), control2: CGPoint(x: 41.789, y: 54.809))
    path.addCurve(to: CGPoint(x: 56.307, y: 94.026), control1: CGPoint(x: 41.788, y: 89.734), control2: CGPoint(x: 49.874, y: 94.026))
    path.addCurve(to: CGPoint(x: 70.874, y: 75.917), control1: CGPoint(x: 62.74, y: 94.026), control2: CGPoint(x: 70.874, y: 89.735))
    path.addCurve(to: CGPoint(x: 61.224, y: 53.963), control1: CGPoint(x: 70.874, y: 67.866), control2: CGPoint(x: 65.997, y: 59.825))
    path.addCurve(to: CGPoint(x: 72.341, y: 43.538), control1: CGPoint(x: 61.248, y: 53.934), control2: CGPoint(x: 66.374, y: 47.82))
    path.addCurve(to: CGPoint(x: 86.633, y: 75.917), control1: CGPoint(x: 81.107, y: 53.662), control2: CGPoint(x: 86.633, y: 63.68))
    path.addCurve(to: CGPoint(x: 56.307, y: 110), control1: CGPoint(x: 86.633, y: 95.549), control2: CGPoint(x: 73.925, y: 110))
    path.addCurve(to: CGPoint(x: 25.982, y: 75.917), control1: CGPoint(x: 38.688, y: 110), control2: CGPoint(x: 25.982, y: 95.549))
    path.addCurve(to: CGPoint(x: 101.976, y: 0.821), control1: CGPoint(x: 25.982, y: 44.34), control2: CGPoint(x: 73.673, y: 11.912))
    path.closeSubpath()
    path.move(to: CGPoint(x: 0.372, y: 4.272))
    path.addCurve(to: CGPoint(x: 11.19, y: 0.826), control1: CGPoint(x: 1.623, y: 0.549), control2: CGPoint(x: 6.21, y: -1.125))
    path.addCurve(to: CGPoint(x: 51.019, y: 23.596), control1: CGPoint(x: 22.754, y: 5.358), control2: CGPoint(x: 37.556, y: 13.453))
    path.addCurve(to: CGPoint(x: 39.908, y: 33.718), control1: CGPoint(x: 44.765, y: 28.489), control2: CGPoint(x: 39.924, y: 33.701))
    path.addCurve(to: CGPoint(x: 5.706, y: 13.786), control1: CGPoint(x: 29.529, y: 26.092), control2: CGPoint(x: 17.588, y: 19.182))
    path.addCurve(to: CGPoint(x: 0.372, y: 4.272), control1: CGPoint(x: 1.777, y: 12.001), control2: CGPoint(x: -1.047, y: 8.499))
    path.closeSubpath()
    return path
}

/// A template image of the Yutori mark, filled, so it takes the menu bar's light or dark tint
/// like the system status items around it.
private func stopMenuBarIcon() -> NSImage {
    let glyph = yutoriMarkGlyph()
    let image = NSImage(size: NSSize(width: menuBarIconPoints, height: menuBarIconPoints), flipped: true) { rect in
        guard let context = NSGraphicsContext.current?.cgContext else { return false }
        let scale = min(rect.width / yutoriMarkViewBox.width, rect.height / yutoriMarkViewBox.height)
        context.translateBy(
            x: (rect.width - yutoriMarkViewBox.width * scale) / 2,
            y: (rect.height - yutoriMarkViewBox.height * scale) / 2
        )
        context.scaleBy(x: scale, y: scale)
        context.translateBy(x: -yutoriMarkViewBox.minX, y: -yutoriMarkViewBox.minY)
        context.addPath(glyph)
        context.setFillColor(NSColor.black.cgColor)
        context.fillPath()
        return true
    }
    image.isTemplate = true
    image.accessibilityDescription = "Yutori n2 is controlling this Mac"
    return image
}

private func writeJSON(_ value: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: value) else { return }
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

private struct OverlayConfig: Decodable {
    let showStopButton: Bool
    let enableHotkey: Bool
}

private final class OverlayApp: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    private let htmlURL: URL
    private let config: OverlayConfig
    private var panel: NSPanel?
    // The Stop control is a menu bar status item with a cursor icon whose menu carries
    // the Stop action. Its on-screen frame is reported as `stop_region` so the Python
    // side keeps refusing model clicks on it.
    private var stopItem: NSStatusItem?
    // Where the shell rail starts, in overlay page points: a 16pt inset below the menu
    // bar, right-aligned with the Stop item above it. The page cannot see the menu bar.
    private var railTop: CGFloat = 0
    private var railRight: CGFloat = 16
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
        railTop = screen.frame.maxY - screen.visibleFrame.maxY + 16
        railRight = screen.frame.maxX - screen.visibleFrame.maxX + 16
        if config.showStopButton { createStopMenuBarItem() }
        let hotkeyAvailable = config.enableHotkey && registerStopHotKey()
        panel.identifier = NSUserInterfaceItemIdentifier(hotkeyAvailable ? "n2-overlay-hotkey" : "n2-overlay-no-hotkey")
        webView.loadFileURL(htmlURL, allowingReadAccessTo: htmlURL.deletingLastPathComponent())
    }

    private func createStopMenuBarItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let button = item.button {
            button.image = stopMenuBarIcon()
            button.toolTip = "Yutori n2 is controlling this Mac. Stop with ⇧⌘Esc."
        }
        let menu = NSMenu()
        menu.autoenablesItems = false
        let status = NSMenuItem(title: "Yutori n2 is controlling this Mac", action: nil, keyEquivalent: "")
        status.isEnabled = false
        menu.addItem(status)
        menu.addItem(.separator())
        let stop = NSMenuItem(title: "Stop", action: #selector(stopFromMenu), keyEquivalent: "\u{1B}")
        stop.keyEquivalentModifierMask = [.command, .shift]
        stop.target = self
        menu.addItem(stop)
        item.menu = menu
        stopItem = item
    }

    @objc private func stopFromMenu() {
        requestStop(source: "menu")
    }

    /// The Stop item's frame in the overlay's normalized 0-1000 space, or nil when the
    /// item is not on this screen (the menu bar can live on another display). Status item
    /// windows can overhang the screen frame by a point on notch displays, so the frame is
    /// clipped to the screen rather than required to sit inside it.
    private func stopItemRegion(on screen: NSScreen) -> [String: Double]? {
        guard let itemFrame = stopItem?.button?.window?.frame else { return nil }
        let frame = itemFrame.intersection(screen.frame)
        guard !frame.isEmpty else { return nil }
        return [
            "x": (frame.minX - screen.frame.minX) / screen.frame.width * 1000,
            "y": (screen.frame.maxY - frame.maxY) / screen.frame.height * 1000,
            "width": frame.width / screen.frame.width * 1000,
            "height": frame.height / screen.frame.height * 1000,
        ]
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
            "capabilities": ["capture", "encode", "shell_commands", "stop"],
        ]
        if stopItem != nil {
            ready["stop_control"] = "menu_bar"
            if let region = stopItemRegion(on: screen) { ready["stop_region"] = region }
        }
        let railStyle =
            "document.documentElement.style.setProperty('--n2-rail-top', '\(railTop)px');"
            + "document.documentElement.style.setProperty('--n2-rail-right', '\(railRight)px');"
        webView.evaluateJavaScript(railStyle) { _, _ in
            writeJSON(ready)
            self.readCommands()
        }
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
        case "shellCommands":
            guard
                let commands = command["commands"] as? [[String: Any]],
                let overflow = command["overflow"] as? Int
            else { return fail(id, "Invalid shell command request.") }
            callJavaScript(
                id: id,
                body: "return window.__n2ShellCommands(payload)",
                arguments: ["payload": ["commands": commands, "overflow": overflow]],
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
        animate(visible: false, duration: 0.06, framesAfter: 2) {
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
            self.animate(visible: true, duration: 0.12, framesAfter: 0) {
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
        completion: @escaping () -> Void
    ) {
        let windows = [panel].compactMap { $0 }
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
        if let stopItem { NSStatusBar.system.removeStatusItem(stopItem) }
        stopItem = nil
        [panel].compactMap { $0 }.forEach {
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
