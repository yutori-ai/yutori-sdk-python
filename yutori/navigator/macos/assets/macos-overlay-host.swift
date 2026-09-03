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
// Status mode (background window-scope runs): the menu shows the latest frame at this width.
private let thumbnailWidthPoints: CGFloat = 360
private let thumbnailMaxHeightPoints: CGFloat = 420
private let thumbnailInsetPoints: CGFloat = 12
// Status mode: the floating activity window -- the driven window's live frame above the
// conversation with the model -- opens at this size and is resizable from there.
private let activityWidthPoints: CGFloat = 520
private let activityHeightPoints: CGFloat = 720
// How many recent captions the menu keeps, so a glance at the menu bar shows the last few
// steps rather than only the newest one.
private let menuCaptionLines = 4
// A dropped activity row costs nothing; this only bounds what the host buffers while the
// activity page is still loading.
private let pendingActivityCallLimit = 500

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
    // "overlay" (default): the full-screen reasoning overlay. "status": a menu bar item that
    // shows the latest captured frame and Stop, plus the shell rail and the activity window,
    // for window-scope runs the user keeps working next to.
    let mode: String?
    let title: String?
    // Status mode: the page the activity window loads. Absent means the caller shipped no
    // activity page, and the run falls back to the menu bar item alone.
    let activityHtml: String?
}

private final class OverlayApp: NSObject, NSApplicationDelegate, WKNavigationDelegate, NSMenuDelegate, NSWindowDelegate {
    private let htmlURL: URL
    private let config: OverlayConfig
    private var panel: NSPanel?
    // The Stop control is a menu bar status item with a cursor icon whose menu carries
    // the Stop action. Its on-screen frame is reported as `stop_region` so the Python
    // side keeps refusing model clicks on it.
    private var stopItem: NSStatusItem?
    // Status mode only: the menu's recent caption lines and the live thumbnail of the driven window.
    private var statusMode = false
    private var captionItems: [NSMenuItem] = []
    private var captionLines: [String] = []
    private var thumbnailItem: NSMenuItem?
    private var thumbnailView: NSImageView?
    // The floating activity window -- both modes -- and the demand signal the Python side
    // streams frames for in status mode (the menu is open, or the activity window is shown).
    private var statusMenu: NSMenu?
    private var activityItem: NSMenuItem?
    private var activityPanel: NSPanel?
    private var activityWebView: WKWebView?
    private var activityReady = false
    // Rows and frames that arrived while the activity page was still loading, replayed in
    // order once it is; the window is opened lazily but the transcript starts at step one.
    private var pendingActivity: [(String, [String: Any])] = []
    private var latestFrame: NSImage?
    private var menuOpen = false
    private var activityShown = false
    // Status mode only: the click-through shell rail, in its own borderless panel because
    // there is no full-screen overlay page to hang it on.
    private var railPanel: NSPanel?
    private var railWebView: WKWebView?
    private var railReady = false
    private var pendingRail: [String: Any]?
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
        if config.mode == "status" {
            startStatusMode()
            return
        }
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
        // The conversation with the model does not depend on how the run drives the Mac, so a
        // foreground run offers the same activity window; its menu bar item is what opens it.
        createActivityPanel()
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
        let activity = NSMenuItem(title: "Show activity", action: #selector(toggleActivity), keyEquivalent: "")
        activity.target = self
        activity.isHidden = activityWebView == nil
        menu.addItem(activity)
        activityItem = activity
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

    /// Status mode: no panel and no page, just a menu bar item that stays for the whole run.
    /// Its menu carries the run title, a caption with the latest action, the latest frame of
    /// the driven window, and Stop (also on the ⇧⌘Esc hotkey).
    private func startStatusMode() {
        statusMode = true
        let title = config.title ?? "Yutori n2 is working in the background"
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let button = item.button {
            button.image = stopMenuBarIcon()
            button.toolTip = title
        }
        let menu = NSMenu()
        menu.autoenablesItems = false
        let titleItem = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        titleItem.isEnabled = false
        menu.addItem(titleItem)
        // The last few steps, oldest first, so the newest sits closest to the frame below it.
        for index in 0..<menuCaptionLines {
            let caption = NSMenuItem(
                title: index == 0 ? "Waiting for the first step" : "",
                action: nil,
                keyEquivalent: ""
            )
            caption.isEnabled = false
            caption.isHidden = index > 0
            menu.addItem(caption)
            captionItems.append(caption)
        }
        let imageView = NSImageView(
            frame: NSRect(x: 0, y: 0, width: thumbnailWidthPoints + 2 * thumbnailInsetPoints, height: 1)
        )
        imageView.imageScaling = .scaleProportionallyUpOrDown
        imageView.imageAlignment = .alignCenter
        let thumbnail = NSMenuItem(title: "", action: nil, keyEquivalent: "")
        thumbnail.view = imageView
        thumbnail.isHidden = true
        menu.addItem(thumbnail)
        thumbnailItem = thumbnail
        thumbnailView = imageView
        let live = NSMenuItem(title: "Show activity", action: #selector(toggleActivity), keyEquivalent: "")
        live.target = self
        menu.addItem(live)
        activityItem = live
        menu.delegate = self
        statusMenu = menu
        if config.showStopButton {
            menu.addItem(.separator())
            let stop = NSMenuItem(title: "Stop", action: #selector(stopFromMenu), keyEquivalent: "\u{1B}")
            stop.keyEquivalentModifierMask = [.command, .shift]
            stop.target = self
            menu.addItem(stop)
        }
        item.menu = menu
        stopItem = item
        if let screen = NSScreen.main {
            self.screen = screen
            createRailPanel(on: screen)
        }
        createActivityPanel()
        activityItem?.isHidden = activityWebView == nil
        // Only a window-scope run has frames to show: the model's view of a foreground run is
        // the desktop the operator is already looking at.
        callActivity("__n2ActivityCaption", ["text": "Waiting for the first frame"])
        let hotkeyAvailable = config.enableHotkey && registerStopHotKey()
        state = "armed"
        var capabilities = ["thumbnail", "status", "stop", "preview"]
        if railWebView != nil { capabilities.append("shell_commands") }
        if activityWebView != nil { capabilities.append("transcript") }
        writeJSON([
            "ready": true,
            "protocol_version": overlayProtocolVersion,
            "mode": "status",
            "width": 0,
            "height": 0,
            "backing_scale": NSScreen.main?.backingScaleFactor ?? 1,
            "hotkey": hotkeyAvailable,
            "stop_control": "menu_bar",
            "capabilities": capabilities,
        ])
        readCommands()
    }

    /// The click-through shell rail a background run gets, in its own borderless panel.
    ///
    /// A window-scope run drives one window and paints nothing on the desktop, but it still
    /// runs commands on this Mac -- and those should be visible without opening a window, the
    /// same way a foreground run shows them under the menu bar. The panel ignores mouse events
    /// and never takes focus, and window-scope capture sees only the target window, so neither
    /// the operator's work nor the model's view is disturbed.
    private func createRailPanel(on screen: NSScreen) {
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
        let webView = WKWebView(frame: NSRect(origin: .zero, size: screen.frame.size))
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")
        panel.contentView = webView
        panel.orderFrontRegardless()
        railPanel = panel
        railWebView = webView
        railTop = screen.frame.maxY - screen.visibleFrame.maxY + 16
        railRight = screen.frame.maxX - screen.visibleFrame.maxX + 16
        webView.loadFileURL(htmlURL, allowingReadAccessTo: htmlURL.deletingLastPathComponent())
    }

    /// The activity window: the live frame of the driven window above the conversation with
    /// the model. It is built up front, hidden, so the transcript starts at the first step
    /// no matter when the operator opens it.
    private func createActivityPanel() {
        guard let activityHtml = config.activityHtml else { return }
        let url = URL(fileURLWithPath: activityHtml).standardizedFileURL
        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: activityWidthPoints, height: activityHeightPoints),
            styleMask: [.titled, .closable, .resizable, .utilityWindow, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        // Its own short title: the menu's sentence-length one is for the menu bar item.
        panel.title = "Yutori n2 activity"
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.hidesOnDeactivate = false
        // The transcript scrolls, so dragging inside it must not drag the window.
        panel.isMovableByWindowBackground = false
        panel.isReleasedWhenClosed = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.delegate = self
        let webView = WKWebView(frame: panel.contentView?.bounds ?? .zero)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        panel.contentView = webView
        if let screen = NSScreen.main {
            // Top-left, because the shell rail owns the top-right corner and floats above this.
            let visible = screen.visibleFrame
            panel.setFrameTopLeftPoint(NSPoint(x: visible.minX + 16, y: visible.maxY - 16))
        }
        activityPanel = panel
        activityWebView = webView
        webView.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
    }

    /// Push one caption onto the menu's recent lines, dropping the oldest.
    private func showCaption(_ text: String) {
        captionLines.append(text)
        if captionLines.count > menuCaptionLines { captionLines.removeFirst(captionLines.count - menuCaptionLines) }
        for (index, item) in captionItems.enumerated() {
            let line = index < captionLines.count ? captionLines[index] : nil
            item.title = line ?? ""
            item.isHidden = line == nil
        }
        stopItem?.button?.toolTip = text
    }

    // MARK: Activity window

    func menuWillOpen(_ menu: NSMenu) {
        guard menu === statusMenu else { return }
        menuOpen = true
        emitPreviewDemand()
    }

    func menuDidClose(_ menu: NSMenu) {
        guard menu === statusMenu else { return }
        menuOpen = false
        emitPreviewDemand()
    }

    /// Tells the Python side whether anyone is looking, so it streams frames only then.
    private func emitPreviewDemand() {
        writeJSON(["event": "previewDemand", "menuOpen": menuOpen, "activityOpen": activityShown])
    }

    @objc private func toggleActivity() {
        if activityShown { hideActivity() } else { showActivity() }
    }

    private func showActivity() {
        guard let panel = activityPanel else { return }
        panel.orderFrontRegardless()
        activityShown = true
        activityItem?.title = "Hide activity"
        emitPreviewDemand()
    }

    private func hideActivity() {
        activityPanel?.orderOut(nil)
        activityShown = false
        activityItem?.title = "Show activity"
        emitPreviewDemand()
    }

    /// Send one call to the activity page, or hold it until the page finishes loading.
    private func callActivity(_ function: String, _ payload: [String: Any]) {
        guard let activityWebView else { return }
        guard activityReady else {
            if pendingActivity.count >= pendingActivityCallLimit { pendingActivity.removeFirst() }
            pendingActivity.append((function, payload))
            return
        }
        activityWebView.callAsyncJavaScript(
            "return window.\(function)(payload)",
            arguments: ["payload": payload],
            in: nil,
            in: .page,
            completionHandler: nil
        )
    }

    private func flushPendingActivity() {
        let calls = pendingActivity
        pendingActivity = []
        for (function, payload) in calls { callActivity(function, payload) }
    }

    func windowWillClose(_ notification: Notification) {
        guard let closing = notification.object as? NSPanel, closing === activityPanel else { return }
        activityShown = false
        activityItem?.title = "Show activity"
        emitPreviewDemand()
    }

    /// A streamed frame: refresh the menu thumbnail and the activity window, leave the caption alone.
    private func showPreviewFrame(_ image: NSImage, data: String) {
        latestFrame = image
        showThumbnail(image, caption: nil, data: data)
    }

    private func showThumbnail(_ image: NSImage, caption: String?, data: String) {
        // Both the model's frames and the streamed preview frames reach the host as JPEG.
        callActivity("__n2ActivityFrame", ["data": data, "mediaType": "image/jpeg"])
        guard let thumbnailView, let thumbnailItem else { return }
        let size = image.size
        var height = thumbnailWidthPoints
        if size.width > 0, size.height > 0 {
            height = min(thumbnailMaxHeightPoints, thumbnailWidthPoints * size.height / size.width)
        }
        thumbnailView.frame = NSRect(
            x: 0,
            y: 0,
            width: thumbnailWidthPoints + 2 * thumbnailInsetPoints,
            height: height + thumbnailInsetPoints
        )
        thumbnailView.image = image
        thumbnailItem.isHidden = false
        if let caption {
            showCaption(caption)
            // A model frame: keep the activity window current even between streamed frames.
            latestFrame = image
        }
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
        if webView === railWebView {
            // The rail is the only thing this page draws in status mode, and only the host
            // knows the menu bar height and which side the Dock is on.
            webView.evaluateJavaScript(railStyleScript()) { _, _ in
                self.railReady = true
                if let pending = self.pendingRail {
                    self.pendingRail = nil
                    self.renderShellCommands(pending)
                }
            }
            return
        }
        if webView === activityWebView {
            activityReady = true
            flushPendingActivity()
            return
        }
        guard let screen else { return }
        let hotkeyAvailable = panel?.identifier?.rawValue == "n2-overlay-hotkey"
        var ready: [String: Any] = [
            "ready": true,
            "protocol_version": overlayProtocolVersion,
            "width": Int(webView.bounds.width.rounded()),
            "height": Int(webView.bounds.height.rounded()),
            "backing_scale": screen.backingScaleFactor,
            "hotkey": hotkeyAvailable,
            "capabilities": activityWebView == nil
                ? ["capture", "encode", "shell_commands", "stop"]
                : ["capture", "encode", "shell_commands", "stop", "transcript"],
        ]
        if stopItem != nil {
            ready["stop_control"] = "menu_bar"
            if let region = stopItemRegion(on: screen) { ready["stop_region"] = region }
        }
        webView.evaluateJavaScript(railStyleScript()) { _, _ in
            writeJSON(ready)
            self.readCommands()
        }
    }

    private func railStyleScript() -> String {
        "document.documentElement.style.setProperty('--n2-rail-top', '\(railTop)px');"
            + "document.documentElement.style.setProperty('--n2-rail-right', '\(railRight)px');"
    }

    /// Hand the shell rail its commands, holding them while the rail page loads.
    private func renderShellCommands(_ payload: [String: Any]) {
        guard let railWebView else { return }
        guard railReady else {
            pendingRail = payload
            return
        }
        railWebView.callAsyncJavaScript(
            "return window.__n2ShellCommands(payload)",
            arguments: ["payload": payload],
            in: nil,
            in: .page,
            completionHandler: nil
        )
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
        case "arm" where statusMode:
            reply(id, state: "armed")
        case "arm":
            state = "arming"
            CATransaction.flush()
            waitForDisplayFrames(1) {
                self.state = "armed"
                self.reply(id, state: self.state)
            }
        case "reveal" where statusMode:
            state = "visible"
            reply(id, state: state)
        case "reveal":
            reveal(id: id)
        case "thumbnail":
            guard
                statusMode,
                let data = command["data"] as? String,
                let bytes = Data(base64Encoded: data),
                let image = NSImage(data: bytes)
            else { return fail(id, "Invalid thumbnail.") }
            showThumbnail(image, caption: command["caption"] as? String, data: data)
            reply(id, state: "shown")
        case "previewFrame":
            guard
                statusMode,
                let data = command["data"] as? String,
                let bytes = Data(base64Encoded: data),
                let image = NSImage(data: bytes)
            else { return fail(id, "Invalid preview frame.") }
            showPreviewFrame(image, data: data)
            reply(id, state: "shown")
        case "status":
            guard statusMode, let text = command["text"] as? String else { return fail(id, "Invalid status text.") }
            showCaption(text)
            reply(id, state: "shown")
        case "transcript":
            // The transcript is advisory: a row that the page rejects must not fail a run,
            // so the reply lands as soon as the row is dispatched or buffered.
            guard let entry = command["entry"] as? [String: Any] else {
                return fail(id, "Invalid transcript entry.")
            }
            callActivity("__n2ActivityEntry", entry)
            reply(id, state: "shown")
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
            let payload: [String: Any] = ["commands": commands, "overflow": overflow]
            if statusMode {
                renderShellCommands(payload)
                reply(id, state: "shown")
                return
            }
            callJavaScript(
                id: id,
                body: "return window.__n2ShellCommands(payload)",
                arguments: ["payload": payload],
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
        // The overlay page and, while it is open, the activity window: in foreground mode the
        // capture is the whole desktop, so anything Yutori drew has to be out of the frame.
        let windows = [panel, activityShown ? activityPanel : nil].compactMap { $0 }
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
        if let activityPanel {
            activityPanel.delegate = nil
            activityPanel.orderOut(nil)
            activityPanel.close()
        }
        activityPanel = nil
        activityWebView = nil
        railWebView = nil
        [panel, railPanel].compactMap { $0 }.forEach {
            $0.alphaValue = 0
            $0.orderOut(nil)
            $0.close()
        }
        railPanel = nil
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { NSApp.terminate(nil) }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation?, withError error: Error) {
        pageFailed(webView)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation?, withError error: Error) {
        pageFailed(webView)
    }

    /// The overlay page is the run's only surface, so losing it is fatal. The status-mode
    /// pages are extras next to a menu bar item that still carries Stop, so one that fails
    /// to load is simply dropped: the run keeps going without its rail or its transcript.
    private func pageFailed(_ webView: WKWebView) {
        if webView === railWebView {
            railWebView = nil
            pendingRail = nil
            railPanel?.orderOut(nil)
            return
        }
        if webView === activityWebView {
            activityWebView = nil
            pendingActivity = []
            activityItem?.isHidden = true
            hideActivity()
            return
        }
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
