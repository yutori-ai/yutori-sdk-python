import AppKit
import Carbon.HIToolbox
import CoreVideo
import ImageIO
import QuartzCore
import ScreenCaptureKit
import UniformTypeIdentifiers
import WebKit

private let overlayProtocolVersion = 2

// The Yutori mark, from brand/logos/yloop/yloop-mark-light.svg (viewBox 0 0 361 350),
// with its two filled subpaths emitted as CGPath calls because AppKit has no SVG parser.
private let yutoriMarkViewBox = CGRect(x: 0, y: 0, width: 361, height: 350)
private let menuBarIconPoints: CGFloat = 18
private let statusMetricsWidthPoints: CGFloat = 188
private let statusMetricsHeightPoints: CGFloat = 22
private let statusMarkPoints: CGFloat = 16
private let statusMarkPeriodSeconds = 1.2
private let statusMarkFadeSeconds = 0.16
private let statusHistogramBins = 7
private let yutoriGreen = NSColor(srgbRed: 0x19 / 255, green: 0xb3 / 255, blue: 0x85 / 255, alpha: 1)
// Status mode (background window-scope runs): the menu shows the latest frame at this width.
private let thumbnailWidthPoints: CGFloat = 360
private let thumbnailMaxHeightPoints: CGFloat = 420
private let thumbnailInsetPoints: CGFloat = 12
// The floating activity window -- the driven window's live frame above the conversation
// with the model -- is two panels of this width: a grip strip on top, the only part that
// takes the mouse (drag, close), and the page below it, which lets every click through.
private let activityWidthPoints: CGFloat = 520
private let activityHeightPoints: CGFloat = 720
private let activityGripHeightPoints: CGFloat = 28
private let activityCornerRadius: CGFloat = 10
private let activityGripBackground = NSColor(srgbRed: 0x11 / 255, green: 0x1a / 255, blue: 0x2e / 255, alpha: 1)
private let activityGripText = NSColor(srgbRed: 0xe2 / 255, green: 0xe8 / 255, blue: 0xf0 / 255, alpha: 1)
private let activityGripMuted = NSColor(srgbRed: 0x94 / 255, green: 0xa3 / 255, blue: 0xb8 / 255, alpha: 1)
// The capture-exclusion probe: a small checkerboard shown in its own panel for one desktop
// capture at start. Its size and inset in points; `probeCells` cells per side.
private let probeSizePoints: CGFloat = 32
private let probeInsetPoints: CGFloat = 16
private let probeCells = 4
private let probeColorA = NSColor(srgbRed: 1, green: 0, blue: 1, alpha: 1)
private let probeColorB = NSColor(srgbRed: 0, green: 1, blue: 0, alpha: 1)
// A dropped activity row costs nothing; this only bounds what the host buffers while the
// activity page is still loading.
private let pendingActivityCallLimit = 500

private func yutoriMarkGlyph() -> CGPath {
    let path = CGMutablePath()
    path.move(to: CGPoint(x: 324.467, y: 2.613))
    path.addCurve(to: CGPoint(x: 358.884, y: 13.581), control1: CGPoint(x: 340.311, y: -3.596), control2: CGPoint(x: 354.905, y: 1.734))
    path.addCurve(to: CGPoint(x: 341.912, y: 43.852), control1: CGPoint(x: 363.401, y: 27.03), control2: CGPoint(x: 354.415, y: 38.174))
    path.addCurve(to: CGPoint(x: 132.964, y: 241.555), control1: CGPoint(x: 238.148, y: 90.978), control2: CGPoint(x: 132.964, y: 174.393))
    path.addCurve(to: CGPoint(x: 179.159, y: 299.175), control1: CGPoint(x: 132.964, y: 285.52), control2: CGPoint(x: 158.691, y: 299.174))
    path.addCurve(to: CGPoint(x: 225.509, y: 241.555), control1: CGPoint(x: 199.628, y: 299.175), control2: CGPoint(x: 225.509, y: 285.52))
    path.addCurve(to: CGPoint(x: 194.802, y: 171.7), control1: CGPoint(x: 225.509, y: 215.939), control2: CGPoint(x: 209.99, y: 190.354))
    path.addCurve(to: CGPoint(x: 230.176, y: 138.53), control1: CGPoint(x: 194.802, y: 171.7), control2: CGPoint(x: 211.143, y: 152.187))
    path.addCurve(to: CGPoint(x: 275.65, y: 241.555), control1: CGPoint(x: 258.067, y: 170.742), control2: CGPoint(x: 275.649, y: 202.62))
    path.addCurve(to: CGPoint(x: 179.157, y: 350), control1: CGPoint(x: 275.65, y: 304.019), control2: CGPoint(x: 235.216, y: 350))
    path.addCurve(to: CGPoint(x: 82.667, y: 241.555), control1: CGPoint(x: 123.099, y: 349.999), control2: CGPoint(x: 82.667, y: 304.019))
    path.addCurve(to: CGPoint(x: 324.467, y: 2.613), control1: CGPoint(x: 82.667, y: 141.081), control2: CGPoint(x: 234.413, y: 37.904))
    path.closeSubpath()
    path.move(to: CGPoint(x: 1.185, y: 13.595))
    path.addCurve(to: CGPoint(x: 35.602, y: 2.628), control1: CGPoint(x: 5.164, y: 1.748), control2: CGPoint(x: 19.758, y: -3.581))
    path.addCurve(to: CGPoint(x: 162.332, y: 75.076), control1: CGPoint(x: 72.399, y: 17.048), control2: CGPoint(x: 119.496, y: 42.803))
    path.addCurve(to: CGPoint(x: 126.982, y: 107.284), control1: CGPoint(x: 142.457, y: 90.626), control2: CGPoint(x: 127.068, y: 107.191))
    path.addCurve(to: CGPoint(x: 18.157, y: 43.866), control1: CGPoint(x: 93.956, y: 83.022), control2: CGPoint(x: 55.962, y: 61.035))
    path.addCurve(to: CGPoint(x: 1.185, y: 13.595), control1: CGPoint(x: 5.655, y: 38.188), control2: CGPoint(x: -3.332, y: 27.043))
    path.closeSubpath()
    return path
}

/// The Yutori mark filled in one colour at the given size.
private func yutoriMarkImage(points: CGFloat, color: NSColor) -> NSImage {
    let glyph = yutoriMarkGlyph()
    return NSImage(size: NSSize(width: points, height: points), flipped: true) { rect in
        guard let context = NSGraphicsContext.current?.cgContext else { return false }
        let scale = min(rect.width / yutoriMarkViewBox.width, rect.height / yutoriMarkViewBox.height)
        context.translateBy(
            x: (rect.width - yutoriMarkViewBox.width * scale) / 2,
            y: (rect.height - yutoriMarkViewBox.height * scale) / 2
        )
        context.scaleBy(x: scale, y: scale)
        context.translateBy(x: -yutoriMarkViewBox.minX, y: -yutoriMarkViewBox.minY)
        context.addPath(glyph)
        context.setFillColor(color.cgColor)
        context.fillPath()
        return true
    }
}

/// A template image of the Yutori mark, filled, so it takes the menu bar's light or dark tint
/// like the system status items around it.
private func stopMenuBarIcon() -> NSImage {
    let image = yutoriMarkImage(points: menuBarIconPoints, color: .black)
    image.isTemplate = true
    image.accessibilityDescription = "Yutori n2 is controlling this Mac"
    return image
}

private struct StatusMetrics: Decodable {
    let inputTokens: Int?
    let cachedInputTokens: Int?
    let outputTokens: Int?
    let latestRTTMilliseconds: Double?
    let rttSamplesMilliseconds: [Double]
    let requestInFlight: Bool

    enum CodingKeys: String, CodingKey {
        case inputTokens = "input_tokens"
        case cachedInputTokens = "cached_input_tokens"
        case outputTokens = "output_tokens"
        case latestRTTMilliseconds = "latest_rtt_ms"
        case rttSamplesMilliseconds = "rtt_samples_ms"
        case requestInFlight = "request_in_flight"
    }

    static let empty = StatusMetrics(
        inputTokens: nil,
        cachedInputTokens: nil,
        outputTokens: nil,
        latestRTTMilliseconds: nil,
        rttSamplesMilliseconds: [],
        requestInFlight: false
    )

    var isValid: Bool {
        let counts = [inputTokens, cachedInputTokens, outputTokens].compactMap { $0 }
        let timings = rttSamplesMilliseconds + [latestRTTMilliseconds].compactMap { $0 }
        return counts.allSatisfy { $0 >= 0 }
            && timings.allSatisfy { $0.isFinite && $0 >= 0 }
            && (inputTokens == nil || cachedInputTokens == nil || cachedInputTokens! <= inputTokens!)
    }

    var accessibilitySummary: String? {
        var parts: [String] = []
        if let inputTokens { parts.append("Input \(inputTokens) tokens") }
        if let cachedInputTokens { parts.append("cached input \(cachedInputTokens) tokens") }
        if let outputTokens { parts.append("output \(outputTokens) tokens") }
        if let latestRTTMilliseconds { parts.append("round trip \(Int(latestRTTMilliseconds.rounded())) milliseconds") }
        if requestInFlight { parts.append("model request in flight") }
        return parts.isEmpty ? nil : parts.joined(separator: ", ")
    }
}

private func decodedStatusMetrics(_ command: [String: Any]) -> StatusMetrics? {
    guard
        let data = try? JSONSerialization.data(withJSONObject: command),
        let metrics = try? JSONDecoder().decode(StatusMetrics.self, from: data),
        metrics.isValid
    else { return nil }
    return metrics
}

private func compactCount(_ value: Int?) -> String {
    guard let value else { return "—" }
    if value < 1_000 { return String(value) }
    let suffixes = [(1_000_000_000, "b"), (1_000_000, "m"), (1_000, "k")]
    let (scale, suffix) = suffixes.first { value >= $0.0 }!
    let scaled = Double(value) / Double(scale)
    return (scaled < 10 ? String(format: "%.1f", scaled) : String(Int(scaled.rounded()))) + suffix
}

private func compactRTT(_ value: Double?) -> String {
    guard let value else { return "—" }
    if value < 1_000 { return "\(Int(value.rounded()))ms" }
    return String(format: value < 10_000 ? "%.1fs" : "%.0fs", value / 1_000)
}

private func histogram(_ samples: [Double]) -> (counts: [Int], latestBin: Int?) {
    var counts = Array(repeating: 0, count: statusHistogramBins)
    guard let minimum = samples.min(), let maximum = samples.max() else { return (counts, nil) }
    if minimum == maximum {
        counts[statusHistogramBins / 2] = samples.count
        return (counts, statusHistogramBins / 2)
    }
    let width = (maximum - minimum) / Double(statusHistogramBins)
    func bin(for sample: Double) -> Int {
        min(statusHistogramBins - 1, max(0, Int((sample - minimum) / width)))
    }
    for sample in samples { counts[bin(for: sample)] += 1 }
    return (counts, samples.last.map(bin))
}

private func drawStatusText(
    _ text: String,
    in rect: NSRect,
    font: NSFont,
    color: NSColor,
    alignment: NSTextAlignment = .center
) {
    let paragraph = NSMutableParagraphStyle()
    paragraph.alignment = alignment
    NSAttributedString(
        string: text,
        attributes: [.font: font, .foregroundColor: color, .paragraphStyle: paragraph]
    ).draw(in: rect)
}

private func statusMetricsImage(
    metrics: StatusMetrics,
    appearance: NSAppearance,
    markRotation: CGFloat,
    markGreenFraction: CGFloat
) -> NSImage {
    var foreground = NSColor.labelColor
    appearance.performAsCurrentDrawingAppearance {
        foreground = NSColor.labelColor.usingColorSpace(.deviceRGB) ?? NSColor.labelColor
    }
    let muted = foreground.withAlphaComponent(0.58)
    let faint = foreground.withAlphaComponent(0.2)
    let markColor = foreground.blended(withFraction: markGreenFraction, of: yutoriGreen) ?? foreground
    let image = NSImage(size: NSSize(width: statusMetricsWidthPoints, height: statusMetricsHeightPoints), flipped: true) {
        rect in
        guard let context = NSGraphicsContext.current?.cgContext else { return false }
        let markRect = CGRect(x: 1, y: 3, width: statusMarkPoints, height: statusMarkPoints)
        let markScale = min(markRect.width / yutoriMarkViewBox.width, markRect.height / yutoriMarkViewBox.height)
        context.saveGState()
        context.translateBy(x: markRect.midX, y: markRect.midY)
        context.rotate(by: markRotation)
        context.translateBy(x: -markRect.midX, y: -markRect.midY)
        context.translateBy(x: markRect.minX, y: markRect.minY)
        context.scaleBy(x: markScale, y: markScale)
        context.addPath(yutoriMarkGlyph())
        context.setFillColor(markColor.cgColor)
        context.fillPath()
        context.restoreGState()

        let labelFont = NSFont.monospacedDigitSystemFont(ofSize: 5.5, weight: .medium)
        let valueFont = NSFont.monospacedDigitSystemFont(ofSize: 7.5, weight: .semibold)
        let columns: [(String, String, CGFloat, CGFloat)] = [
            ("IN", compactCount(metrics.inputTokens), 20, 24),
            ("CACHE", compactCount(metrics.cachedInputTokens), 45, 32),
            ("OUT", compactCount(metrics.outputTokens), 80, 20),
            ("RTT", compactRTT(metrics.latestRTTMilliseconds), 111, 31),
        ]
        for (label, value, x, width) in columns {
            drawStatusText(label, in: NSRect(x: x, y: 0, width: width, height: 8), font: labelFont, color: muted)
            drawStatusText(value, in: NSRect(x: x, y: 9, width: width, height: 10), font: valueFont, color: foreground)
        }
        faint.setFill()
        NSBezierPath(rect: NSRect(x: 105, y: 4, width: 1, height: 14)).fill()

        let distribution = histogram(metrics.rttSamplesMilliseconds)
        let maximumCount = max(1, distribution.counts.max() ?? 0)
        for index in 0..<statusHistogramBins {
            let count = distribution.counts[index]
            let height = count == 0 ? 2 : 2 + 11 * CGFloat(count) / CGFloat(maximumCount)
            let color = index == distribution.latestBin ? yutoriGreen : (count == 0 ? faint : muted)
            color.setFill()
            NSBezierPath(
                roundedRect: NSRect(x: 147 + CGFloat(index * 6), y: 18 - height, width: 4, height: height),
                xRadius: 1,
                yRadius: 1
            ).fill()
        }
        return true
    }
    image.isTemplate = false
    return image
}

private final class StatusMetricsRenderer {
    private weak var button: NSStatusBarButton?
    private var metrics = StatusMetrics.empty
    private var toolTip: String
    private var timer: Timer?
    private var appearanceObservation: NSKeyValueObservation?
    private var motionObservation: NSObjectProtocol?
    private var markGreenFrom: CGFloat = 0
    private var markGreenTo: CGFloat = 0
    private var markFadeStartedAt = CACurrentMediaTime()

    init(button: NSStatusBarButton, toolTip: String) {
        self.button = button
        self.toolTip = toolTip
        button.image = stopMenuBarIcon()
        button.imagePosition = .imageOnly
        button.imageScaling = .scaleNone
        button.title = ""
        appearanceObservation = button.observe(\.effectiveAppearance, options: [.new]) { [weak self] _, _ in
            self?.redraw()
        }
        motionObservation = NotificationCenter.default.addObserver(
            forName: NSWorkspace.accessibilityDisplayOptionsDidChangeNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in self?.configureTimer() }
        configureTimer()
        redraw()
    }

    deinit {
        timer?.invalidate()
        if let motionObservation { NotificationCenter.default.removeObserver(motionObservation) }
    }

    func update(_ metrics: StatusMetrics) {
        let now = CACurrentMediaTime()
        markGreenFrom = currentMarkGreen(at: now)
        markGreenTo = metrics.requestInFlight ? 1 : 0
        markFadeStartedAt = now
        self.metrics = metrics
        redraw()
    }

    func updateToolTip(_ text: String) {
        toolTip = text
        updateAccessibility()
    }

    private func configureTimer() {
        timer?.invalidate()
        timer = nil
        if NSWorkspace.shared.accessibilityDisplayShouldReduceMotion {
            markGreenFrom = markGreenTo
            redraw()
            return
        }
        let timer = Timer(timeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in self?.redraw() }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    private func currentMarkGreen(at now: CFTimeInterval) -> CGFloat {
        if NSWorkspace.shared.accessibilityDisplayShouldReduceMotion { return markGreenTo }
        let progress = min(1, max(0, (now - markFadeStartedAt) / statusMarkFadeSeconds))
        return markGreenFrom + (markGreenTo - markGreenFrom) * CGFloat(progress)
    }

    private func redraw() {
        guard let button else { return }
        let now = CACurrentMediaTime()
        let rotation = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
            ? 0
            : CGFloat((now.truncatingRemainder(dividingBy: statusMarkPeriodSeconds) / statusMarkPeriodSeconds) * 2 * .pi)
        button.image = statusMetricsImage(
            metrics: metrics,
            appearance: button.effectiveAppearance,
            markRotation: rotation,
            markGreenFraction: currentMarkGreen(at: now)
        )
        updateAccessibility()
    }

    private func updateAccessibility() {
        guard let button else { return }
        let label = metrics.accessibilitySummary.map { "\(toolTip). \($0)." } ?? toolTip
        button.toolTip = toolTip
        button.setAccessibilityLabel(label)
        button.image?.accessibilityDescription = label
    }
}

private func configureStatusButton(_ button: NSStatusBarButton, toolTip: String) -> StatusMetricsRenderer {
    StatusMetricsRenderer(button: button, toolTip: toolTip)
}

/// The display the driver captures (`CGMainDisplayID`), so the overlay, the activity window, and
/// the menu bar item land on the screen being driven and the reported geometry matches the frame
/// the model reasons over. `NSScreen.main` follows keyboard focus, which on a multi-display Mac is
/// often a different screen than the one captured; that mismatch put the overlay on the wrong
/// display and degraded the whole presentation at start.
private func captureScreen() -> NSScreen? {
    let mainDisplay = CGMainDisplayID()
    let key = NSDeviceDescriptionKey("NSScreenNumber")
    let match = NSScreen.screens.first { ($0.deviceDescription[key] as? NSNumber)?.uint32Value == mainDisplay }
    return match ?? NSScreen.main
}

private enum DesktopCaptureError: LocalizedError {
    case displayUnavailable
    case selfNotShareable

    var errorDescription: String? {
        switch self {
        case .displayUnavailable: return "The captured display is not shareable."
        case .selfNotShareable: return "The overlay's windows are not listed as shareable content."
        }
    }
}

/// PNG bytes of a captured frame. No PNG filtering: the frame is transient and decoded once, so
/// a fast encode matters more than its size on the pipe.
private func pngData(_ image: CGImage) -> Data? {
    let data = NSMutableData()
    guard let destination = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil) else {
        return nil
    }
    let properties: [CFString: Any] = [
        kCGImagePropertyPNGDictionary: [kCGImagePropertyPNGCompressionFilter: 0],
    ]
    CGImageDestinationAddImage(destination, image, properties as CFDictionary)
    return CGImageDestinationFinalize(destination) ? data as Data : nil
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
    // Whether the panels opt out of screen capture (`NSWindow.sharingType = .none`), so a desktop
    // screenshot has no Yutori drawing in it without hiding anything first. Absent means yes;
    // false keeps them capturable (screen recordings and screen shares of the run show them) and
    // the Python side takes the model's desktop frames through `captureDesktop`, which filters
    // this process's windows out on the capturer's side instead.
    let excludeFromCapture: Bool?
}

/// The capture-exclusion probe's pattern: saturated magenta and green cells, colours no desktop is
/// likely to show in exactly that arrangement. Row 0 is the top row.
private final class ProbeCheckerView: NSView {
    override var isFlipped: Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        let cell = bounds.width / CGFloat(probeCells)
        for row in 0..<probeCells {
            for column in 0..<probeCells {
                ((row + column) % 2 == 0 ? probeColorA : probeColorB).setFill()
                NSRect(x: CGFloat(column) * cell, y: CGFloat(row) * cell, width: cell, height: cell).fill()
            }
        }
    }
}

/// The activity window's grip: the page's near-black surface with the top corners rounded,
/// the Yutori mark, and the title, drawn rather than laid out as subviews so a mouse-down
/// anywhere but the close button drags the window (`isMovableByWindowBackground`).
private final class ActivityGripView: NSView {
    private let mark = yutoriMarkImage(points: 14, color: activityGripMuted)

    override var mouseDownCanMoveWindow: Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        let shape = NSBezierPath()
        let radius = activityCornerRadius
        // Rounded at the top only: the page continues flush below.
        shape.move(to: NSPoint(x: bounds.minX, y: bounds.minY))
        shape.line(to: NSPoint(x: bounds.minX, y: bounds.maxY - radius))
        shape.appendArc(
            withCenter: NSPoint(x: bounds.minX + radius, y: bounds.maxY - radius),
            radius: radius, startAngle: 180, endAngle: 90, clockwise: true
        )
        shape.line(to: NSPoint(x: bounds.maxX - radius, y: bounds.maxY))
        shape.appendArc(
            withCenter: NSPoint(x: bounds.maxX - radius, y: bounds.maxY - radius),
            radius: radius, startAngle: 90, endAngle: 0, clockwise: true
        )
        shape.line(to: NSPoint(x: bounds.maxX, y: bounds.minY))
        shape.close()
        activityGripBackground.setFill()
        shape.fill()
        NSColor(white: 1, alpha: 0.08).setFill()
        NSRect(x: bounds.minX, y: bounds.minY, width: bounds.width, height: 1).fill()

        let markSize = mark.size
        mark.draw(
            in: NSRect(x: 12, y: (bounds.height - markSize.height) / 2, width: markSize.width, height: markSize.height)
        )
        let title = NSAttributedString(
            string: "Yutori n2 activity",
            attributes: [
                .font: NSFont.systemFont(ofSize: 12, weight: .medium),
                .foregroundColor: activityGripText,
            ]
        )
        let titleSize = title.size()
        title.draw(at: NSPoint(x: 12 + markSize.width + 8, y: (bounds.height - titleSize.height) / 2))
    }
}

private final class OverlayApp: NSObject, NSApplicationDelegate, WKNavigationDelegate, NSMenuDelegate, NSWindowDelegate {
    private let htmlURL: URL
    private let config: OverlayConfig
    private var panel: NSPanel?
    // The Stop control is a menu bar status item with a cursor icon whose menu carries
    // the Stop action. Its on-screen frame is reported as `stop_region` so the Python
    // side keeps refusing model clicks on it.
    private var stopItem: NSStatusItem?
    private var statusMetricsRenderer: StatusMetricsRenderer?
    // Status mode only: the menu's caption line and the live thumbnail of the driven window.
    private var statusMode = false
    private var statusCaptionItem: NSMenuItem?
    private var thumbnailItem: NSMenuItem?
    private var thumbnailView: NSImageView?
    // The floating activity window -- both modes -- and the demand signal the Python side
    // streams frames for in status mode (the menu is open, or the activity window is shown).
    private var statusMenu: NSMenu?
    private var activityItem: NSMenuItem?
    // The grip (title strip: drag and close) is the parent; the page is its child window,
    // ordered below, so it follows a drag while ignoring the mouse itself.
    private var activityPanel: NSPanel?
    private var activityBodyPanel: NSPanel?
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
    // The capture-exclusion probe (see `captureProbe`), alive only between its show and hide.
    private var probePanel: NSPanel?
    // The display filter `captureDesktop` reuses: the captured display minus this process's
    // windows. Dropped after a failed capture so the next one rebuilds it from fresh content.
    private var captureFilter: SCContentFilter?
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

    /// Every panel Yutori draws opts out of screen capture unless the caller wants a recordable
    /// run, in which case the model's frames come from `captureDesktop` with the panels filtered
    /// out by the capturer. The Python side checks either mechanism with a probe, and hides the
    /// panels around every capture (`captureHide`/`captureReveal`) when the check fails.
    private var sharing: NSWindow.SharingType {
        config.excludeFromCapture == false ? .readOnly : .none
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
        guard let screen = captureScreen() else {
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
        panel.sharingType = sharing
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
        let item = NSStatusBar.system.statusItem(withLength: statusMetricsWidthPoints)
        if let button = item.button {
            statusMetricsRenderer = configureStatusButton(
                button,
                toolTip: "Yutori n2 is controlling this Mac. Stop with ⇧⌘Esc."
            )
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

    /// Status mode: no full-screen page, just a menu bar item that stays for the whole run.
    /// Its menu carries the run title, a caption with the latest action, the latest frame of
    /// the driven window, Show activity, and Stop (also on the ⇧⌘Esc hotkey).
    private func startStatusMode() {
        statusMode = true
        let title = config.title ?? "Yutori n2 is working in the background"
        let item = NSStatusBar.system.statusItem(withLength: statusMetricsWidthPoints)
        if let button = item.button {
            statusMetricsRenderer = configureStatusButton(button, toolTip: title)
        }
        let menu = NSMenu()
        menu.autoenablesItems = false
        let titleItem = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        titleItem.isEnabled = false
        menu.addItem(titleItem)
        let caption = NSMenuItem(title: "Waiting for the first frame", action: nil, keyEquivalent: "")
        caption.isEnabled = false
        menu.addItem(caption)
        statusCaptionItem = caption
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
        if let screen = captureScreen() {
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
        var capabilities = ["thumbnail", "status", "metrics", "stop", "preview"]
        if railWebView != nil { capabilities.append("shell_commands") }
        if activityWebView != nil { capabilities.append("transcript") }
        writeJSON([
            "ready": true,
            "protocol_version": overlayProtocolVersion,
            "mode": "status",
            "width": 0,
            "height": 0,
            "backing_scale": captureScreen()?.backingScaleFactor ?? 1,
            "hotkey": hotkeyAvailable,
            "stop_control": "menu_bar",
            "capabilities": capabilities,
        ])
        readCommands()
    }

    /// The click-through shell rail a background run gets, in its own borderless panel.
    ///
    /// A window-scope run drives one window and paints nothing on the desktop, but it still
    /// runs commands on this Mac -- and those should be visible without opening a window. It
    /// has no cursor to hang a run-command card off, which is where a foreground run shows
    /// the command it is waiting on, so the rail is the surface. The panel ignores mouse
    /// events and never takes focus, and window-scope capture sees only the target window,
    /// so neither the operator's work nor the model's view is disturbed.
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
        panel.sharingType = sharing
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
    ///
    /// Two panels. The body carries the page and ignores the mouse entirely: it floats over
    /// whatever the operator (a foreground run) or the model (its clicks are posted to the
    /// desktop) is working on, and a click there has to reach that, not the transcript. The
    /// grip above it -- the mark, the title, and a close button -- is the one part that takes
    /// the mouse: dragging it moves both, because the body is its child window, and its frame
    /// goes to the Python side (`activityGrip` events) so model clicks on it are refused the
    /// way clicks on the Stop item are. Neither panel ever takes focus.
    private func createActivityPanel() {
        guard let activityHtml = config.activityHtml else { return }
        let url = URL(fileURLWithPath: activityHtml).standardizedFileURL
        let grip = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: activityWidthPoints, height: activityGripHeightPoints),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        // Its own short title, for the window list: the menu's sentence-length one is for the
        // menu bar item. Borderless, so the grip view draws it.
        grip.title = "Yutori n2 activity"
        grip.backgroundColor = .clear
        grip.isOpaque = false
        grip.hasShadow = false
        grip.level = .floating
        grip.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        grip.hidesOnDeactivate = false
        grip.isMovableByWindowBackground = true
        grip.isReleasedWhenClosed = false
        grip.becomesKeyOnlyIfNeeded = true
        grip.sharingType = sharing
        grip.delegate = self
        let gripView = ActivityGripView(frame: NSRect(origin: .zero, size: grip.frame.size))
        let close = NSButton(
            image: closeGlyph(),
            target: self,
            action: #selector(hideActivityFromGrip)
        )
        close.isBordered = false
        close.imagePosition = .imageOnly
        close.contentTintColor = activityGripMuted
        close.toolTip = "Hide activity"
        close.setAccessibilityLabel("Hide activity")
        close.frame = NSRect(
            x: gripView.bounds.maxX - 30,
            y: (gripView.bounds.height - 20) / 2,
            width: 20,
            height: 20
        )
        close.autoresizingMask = [.minXMargin]
        gripView.addSubview(close)
        grip.contentView = gripView

        let body = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: activityWidthPoints, height: activityHeightPoints),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        body.backgroundColor = .clear
        body.isOpaque = false
        body.hasShadow = true
        body.level = .floating
        body.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        body.hidesOnDeactivate = false
        body.ignoresMouseEvents = true
        body.isReleasedWhenClosed = false
        body.sharingType = sharing
        let webView = WKWebView(frame: body.contentView?.bounds ?? .zero)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        webView.wantsLayer = true
        webView.layer?.cornerRadius = activityCornerRadius
        webView.layer?.maskedCorners = [.layerMinXMinYCorner, .layerMaxXMinYCorner]
        webView.layer?.masksToBounds = true
        body.contentView = webView
        if let screen = captureScreen() {
            // Top-left, because the shell rail owns the top-right corner and floats above this.
            let visible = screen.visibleFrame
            grip.setFrameTopLeftPoint(NSPoint(x: visible.minX + 16, y: visible.maxY - 16))
        }
        // Flush under the grip; the child offset is fixed from here on.
        body.setFrameTopLeftPoint(NSPoint(x: grip.frame.minX, y: grip.frame.minY))
        grip.addChildWindow(body, ordered: .below)
        activityPanel = grip
        activityBodyPanel = body
        activityWebView = webView
        webView.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
    }

    private func closeGlyph() -> NSImage {
        let configuration = NSImage.SymbolConfiguration(pointSize: 11, weight: .semibold)
        let symbol = NSImage(systemSymbolName: "xmark", accessibilityDescription: "Hide activity")
        return symbol?.withSymbolConfiguration(configuration) ?? NSImage()
    }

    @objc private func hideActivityFromGrip() {
        hideActivity()
    }

    /// Both panels on screen, the body under the grip. Ordering the grip alone is not enough
    /// after an `orderOut`, and ordering the body front would put it above its parent.
    private func orderActivityFront() {
        guard let activityPanel else { return }
        activityPanel.orderFrontRegardless()
        activityBodyPanel?.order(.below, relativeTo: activityPanel.windowNumber)
    }

    /// The menu's one line about the latest step; the activity window keeps the history.
    private func showCaption(_ text: String) {
        statusCaptionItem?.title = text
        statusMetricsRenderer?.updateToolTip(text)
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
        guard activityPanel != nil else { return }
        orderActivityFront()
        activityShown = true
        activityItem?.title = "Hide activity"
        syncRailVisibility()
        emitPreviewDemand()
        emitActivityGrip()
    }

    private func hideActivity() {
        activityPanel?.orderOut(nil)
        activityBodyPanel?.orderOut(nil)
        activityShown = false
        activityItem?.title = "Show activity"
        syncRailVisibility()
        emitPreviewDemand()
        emitActivityGrip()
    }

    /// Where the grip is, so the Python side refuses model clicks on it: its frame in the
    /// overlay's normalized 0-1000 space while the window is shown, null otherwise. Sent on
    /// show, hide, and every move of a drag. The body needs no such report: it ignores the
    /// mouse, so a click there lands on the desktop as intended.
    private func emitActivityGrip() {
        var region: Any = NSNull()
        if activityShown, let frame = activityPanel?.frame, let screen = self.screen ?? captureScreen(),
           let normalized = normalizedRegion(frame, on: screen) {
            region = normalized
        }
        writeJSON(["event": "activityGrip", "region": region])
    }

    func windowDidMove(_ notification: Notification) {
        guard let moved = notification.object as? NSPanel, moved === activityPanel, activityShown else { return }
        emitActivityGrip()
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
        return normalizedRegion(itemFrame, on: screen)
    }

    /// A window frame in the overlay's normalized 0-1000 space, top-left origin, clipped to
    /// the screen; nil when none of it is on this screen.
    private func normalizedRegion(_ windowFrame: NSRect, on screen: NSScreen) -> [String: Double]? {
        let frame = windowFrame.intersection(screen.frame)
        guard !frame.isEmpty else { return nil }
        // Some Swift/CoreFoundation combinations expose both CGFloat and Double arithmetic
        // candidates here. Convert before scaling so the dictionary's Double value type does
        // not have to disambiguate the numeric-literal overload.
        return [
            "x": Double((frame.minX - screen.frame.minX) / screen.frame.width) * 1000.0,
            "y": Double((screen.frame.maxY - frame.maxY) / screen.frame.height) * 1000.0,
            "width": Double(frame.width / screen.frame.width) * 1000.0,
            "height": Double(frame.height / screen.frame.height) * 1000.0,
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
                ? ["capture", "desktop_capture", "encode", "shell_commands", "stop"]
                : ["capture", "desktop_capture", "encode", "shell_commands", "stop", "transcript"],
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
            + railVisibilityScript()
    }

    /// The rail repeats what the activity window already shows in full, so it stands
    /// down while that window is open. Folded into `railStyleScript` as well, so a page
    /// that finishes loading after the operator opened the window starts out hidden.
    private func railVisibilityScript() -> String {
        "document.documentElement.toggleAttribute('data-n2-activity-open', \(activityShown));"
    }

    /// Both rail hosts: its own panel in status mode, the desktop overlay page otherwise.
    private func syncRailVisibility() {
        let script = railVisibilityScript()
        for host in [railWebView, webView].compactMap({ $0 }) {
            host.evaluateJavaScript(script, completionHandler: nil)
        }
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
        case "metrics":
            guard let metrics = decodedStatusMetrics(command) else { return fail(id, "Invalid status metrics.") }
            statusMetricsRenderer?.update(metrics)
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
        case "captureProbe":
            guard let phase = command["phase"] as? String else { return fail(id, "Missing probe phase.") }
            captureProbe(id: id, phase: phase)
        case "captureDesktop":
            captureDesktop(id: id)
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

    // MARK: Capture exclusion probe

    /// Show or hide the probe: a checkerboard in a panel that, like the overlay, opts out of
    /// screen capture. The Python side takes one desktop frame while it is shown; the pattern
    /// in that frame means this macOS ignores the opt-out and every capture must hide the
    /// overlay. The reply to "show" carries the probe's frame in overlay page points from the
    /// top-left of the screen, after the panel has been composited.
    private func captureProbe(id: Int, phase: String) {
        switch phase {
        case "show":
            guard let screen else { return fail(id, "Overlay display is unavailable.") }
            // Bottom-right of the visible frame: clear of the menu bar, the Dock, notification
            // banners, and the shell rail.
            let visible = screen.visibleFrame
            let frame = NSRect(
                x: visible.maxX - probeInsetPoints - probeSizePoints,
                y: visible.minY + probeInsetPoints,
                width: probeSizePoints,
                height: probeSizePoints
            )
            let panel = probePanel ?? makeProbePanel(frame: frame)
            panel.setFrame(frame, display: true)
            panel.orderFrontRegardless()
            probePanel = panel
            CATransaction.flush()
            waitForDisplayFrames(2) {
                writeJSON([
                    "id": id,
                    "ok": true,
                    "state": "shown",
                    "probe": [
                        "x": Double(frame.minX - screen.frame.minX),
                        "y": Double(screen.frame.maxY - frame.maxY),
                        "size": Double(probeSizePoints),
                        "cells": probeCells,
                    ],
                ])
            }
        case "hide":
            probePanel?.orderOut(nil)
            probePanel?.close()
            probePanel = nil
            reply(id, state: "hidden")
        default:
            fail(id, "Unknown probe phase.")
        }
    }

    // MARK: Desktop capture

    /// One frame of the captured display with every window of this process left out by the
    /// capturer (`SCContentFilter(display:excludingApplications:)`), so the panels can stay
    /// capturable -- visible in screen recordings and screen shares of the run -- while the
    /// model's frame still carries no Yutori drawing. Same shape as the driver's frame: the main
    /// display at native pixels, without the cursor. The reply carries the PNG and its pixel size.
    private func captureDesktop(id: Int) {
        guard !statusMode, screen != nil else { return fail(id, "Desktop capture needs the overlay display.") }
        Task { @MainActor in
            do {
                let filter = try await self.desktopCaptureFilter()
                let configuration = SCStreamConfiguration()
                let scale = CGFloat(filter.pointPixelScale)
                configuration.width = Int((filter.contentRect.width * scale).rounded())
                configuration.height = Int((filter.contentRect.height * scale).rounded())
                configuration.showsCursor = false
                configuration.captureResolution = .best
                let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: configuration)
                // PNG encoding of a full Retina frame is the slow part; keep it off the main thread
                // so the overlay keeps animating.
                guard let png = await Task.detached(operation: { pngData(image) }).value else {
                    return self.fail(id, "Desktop frame could not be encoded.")
                }
                writeJSON([
                    "id": id,
                    "ok": true,
                    "frame": ["data": png.base64EncodedString(), "width": image.width, "height": image.height],
                ])
            } catch {
                self.captureFilter = nil
                self.fail(id, "Desktop capture failed: \(error.localizedDescription)")
            }
        }
    }

    private func desktopCaptureFilter() async throws -> SCContentFilter {
        if let captureFilter { return captureFilter }
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        let displayID = CGMainDisplayID()
        guard let display = content.displays.first(where: { $0.displayID == displayID }) else {
            throw DesktopCaptureError.displayUnavailable
        }
        let pid = ProcessInfo.processInfo.processIdentifier
        if let application = content.applications.first(where: { $0.processID == pid }) {
            // By application: windows this process opens later (the activity window, the probe)
            // are left out too, so the filter can be kept.
            let filter = SCContentFilter(display: display, excludingApplications: [application], exceptingWindows: [])
            captureFilter = filter
            return filter
        }
        // Not listed as an application (no window of ours on screen yet): leave out the windows
        // by ID for this one frame, and look again next time.
        let own = Set(NSApp.windows.map { CGWindowID($0.windowNumber) })
        let windows = content.windows.filter { own.contains($0.windowID) }
        guard !windows.isEmpty else { throw DesktopCaptureError.selfNotShareable }
        return SCContentFilter(display: display, excludingWindows: windows)
    }

    private func makeProbePanel(frame: NSRect) -> NSPanel {
        let panel = NSPanel(
            contentRect: frame,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.backgroundColor = .clear
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.hasShadow = false
        panel.hidesOnDeactivate = false
        panel.ignoresMouseEvents = true
        panel.isOpaque = true
        panel.isReleasedWhenClosed = false
        panel.level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.overlayWindow)))
        panel.sharingType = sharing
        panel.contentView = ProbeCheckerView(frame: NSRect(origin: .zero, size: frame.size))
        return panel
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
        // This is the fallback for a macOS that captures panels despite `sharingType = .none`;
        // otherwise the Python side never asks for it.
        let windows = [panel, activityShown ? activityPanel : nil, activityShown ? activityBodyPanel : nil].compactMap { $0 }
        if visible {
            panel?.orderFrontRegardless()
            if activityShown { orderActivityFront() }
        }
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
        statusMetricsRenderer = nil
        if let activityPanel {
            activityPanel.delegate = nil
            if let activityBodyPanel { activityPanel.removeChildWindow(activityBodyPanel) }
            activityPanel.orderOut(nil)
            activityPanel.close()
        }
        if let activityBodyPanel {
            activityBodyPanel.orderOut(nil)
            activityBodyPanel.close()
        }
        activityPanel = nil
        activityBodyPanel = nil
        activityWebView = nil
        railWebView = nil
        [panel, railPanel, probePanel].compactMap { $0 }.forEach {
            $0.alphaValue = 0
            $0.orderOut(nil)
            $0.close()
        }
        railPanel = nil
        probePanel = nil
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
