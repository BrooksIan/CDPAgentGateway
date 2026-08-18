import AppKit
import Foundation
import ImageIO
import Vision

let srcPath = CommandLine.arguments[1]
let dstPath = CommandLine.arguments.count > 2 ? CommandLine.arguments[2] : srcPath

guard let src = NSImage(contentsOfFile: srcPath),
      let tiff = src.tiffRepresentation,
      let srcRep = NSBitmapImageRep(data: tiff),
      let cgImage = srcRep.cgImage
else {
    fputs("failed to load image\n", stderr)
    exit(1)
}

let width = cgImage.width
let height = cgImage.height
let W = CGFloat(width)
let H = CGFloat(height)

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
do {
    try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
} catch {
    fputs("ocr failed: \(error)\n", stderr)
    exit(1)
}

struct Hit {
    let text: String
    let rect: CGRect
}

var hits: [Hit] = []
for obs in request.results ?? [] {
    guard let candidate = obs.topCandidates(1).first else { continue }
    let box = obs.boundingBox
    hits.append(Hit(
        text: candidate.string,
        rect: CGRect(
            x: box.origin.x * W,
            y: (1.0 - box.origin.y - box.size.height) * H,
            width: box.size.width * W,
            height: box.size.height * H
        )
    ))
}

struct Plan {
    let keepPrefix: String
    let replacement: String
}

func plan(for raw: String) -> Plan? {
    let t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    let lower = t.lowercased()

    if t.range(of: #"^10\.\d{1,3}\.\d{1,3}\.\d{1,3}\d*$"#, options: .regularExpression) != nil {
        return Plan(keepPrefix: "", replacement: "10.0.0.0")
    }
    if lower == "ibrooks" {
        return Plan(keepPrefix: "", replacement: "demo-user")
    }
    if let range = lower.range(of: "ibrooks") {
        let prefix = String(t[..<range.lowerBound])
        let suffix = String(t[range.upperBound...])
        return Plan(keepPrefix: prefix, replacement: "demo-user" + suffix)
    }
    if lower.contains("brooks") {
        return Plan(keepPrefix: "", replacement: "Demo User")
    }
    if lower.contains("aw-dl") || lower.contains("go01") || lower.contains("g001") {
        if let colon = t.range(of: ":") {
            return Plan(keepPrefix: String(t[..<colon.upperBound]) + " ", replacement: "demo-dl")
        }
        return Plan(keepPrefix: "", replacement: "demo-dl")
    }
    return nil
}

func measure(_ s: String, font: NSFont) -> CGFloat {
    max(1, (s as NSString).size(withAttributes: [.font: font]).width)
}

func luminance(_ color: NSColor) -> CGFloat {
    guard let rgb = color.usingColorSpace(.deviceRGB) else { return 0.5 }
    return 0.2126 * rgb.redComponent + 0.7152 * rgb.greenComponent + 0.0722 * rgb.blueComponent
}

func sample(_ x: Int, _ yTop: Int) -> NSColor {
    let sx = min(max(x, 0), width - 1)
    let sy = min(max(yTop, 0), height - 1)
    return srcRep.colorAt(x: sx, y: sy) ?? .white
}

guard let outRep = NSBitmapImageRep(
    bitmapDataPlanes: nil,
    pixelsWide: width,
    pixelsHigh: height,
    bitsPerSample: 8,
    samplesPerPixel: 4,
    hasAlpha: true,
    isPlanar: false,
    colorSpaceName: .deviceRGB,
    bytesPerRow: 0,
    bitsPerPixel: 32
) else {
    fputs("failed to create bitmap\n", stderr)
    exit(1)
}
outRep.size = NSSize(width: width, height: height)

NSGraphicsContext.saveGraphicsState()
guard let gc = NSGraphicsContext(bitmapImageRep: outRep) else {
    fputs("failed to create graphics context\n", stderr)
    exit(1)
}
NSGraphicsContext.current = gc
gc.imageInterpolation = .none

NSImage(cgImage: cgImage, size: NSSize(width: width, height: height)).draw(
    in: NSRect(x: 0, y: 0, width: W, height: H),
    from: .zero,
    operation: .copy,
    fraction: 1.0
)

func nsRect(fromTopLeft r: CGRect) -> NSRect {
    NSRect(x: r.origin.x, y: H - r.origin.y - r.size.height, width: r.size.width, height: r.size.height)
}

var count = 0
for hit in hits {
    guard let p = plan(for: hit.text) else { continue }
    count += 1

    let fontSize = max(13, hit.rect.height * 0.78)
    let font = NSFont.systemFont(ofSize: fontSize, weight: .regular)
    let full = hit.text.trimmingCharacters(in: .whitespacesAndNewlines)
    let ratio: CGFloat
    if p.keepPrefix.isEmpty {
        ratio = 0
    } else {
        ratio = min(measure(p.keepPrefix, font: font) / measure(full, font: font), 0.92)
    }

    var cover = hit.rect
    cover.origin.x += cover.width * ratio
    cover.size.width *= (1 - ratio)
    let inSidebar = hit.rect.minX < 280
    if p.keepPrefix.isEmpty {
        cover = cover.insetBy(dx: -3, dy: -3)
        if full.lowercased().contains("brooks") {
            cover.origin.x -= 10
            cover.size.width += 14
        }
    } else {
        cover = cover.insetBy(dx: -1, dy: -3)
    }

    let bg: NSColor
    if inSidebar {
        bg = sample(200, Int(cover.midY))
    } else {
        bg = sample(Int(cover.minX - 8), Int(cover.midY))
    }
    let textColorOrig = sample(Int(hit.rect.maxX - 8), Int(hit.rect.midY))

    bg.setFill()
    nsRect(fromTopLeft: cover).fill()

    let textColor: NSColor = {
        if luminance(bg) < 0.4 {
            return NSColor(white: 0.90, alpha: 1)
        }
        if textColorOrig.blueComponent > textColorOrig.redComponent + 0.15 {
            return textColorOrig
        }
        return NSColor(white: 0.20, alpha: 1)
    }()

    let attrs: [NSAttributedString.Key: Any] = [
        .font: font,
        .foregroundColor: textColor,
    ]
    let label = p.replacement as NSString
    let size = label.size(withAttributes: attrs)
    let drawRect = nsRect(fromTopLeft: cover)
    let point = NSPoint(
        x: drawRect.origin.x + 1,
        y: drawRect.origin.y + max(0, (drawRect.height - size.height) / 2)
    )
    label.draw(at: point, withAttributes: attrs)
    fputs("  '\(full)' keep='\(p.keepPrefix)' -> '\(p.replacement)'\n", stderr)
}

NSGraphicsContext.restoreGraphicsState()
fputs("redacted \(count) regions\n", stderr)

guard let png = outRep.representation(using: .png, properties: [:]) else {
    fputs("failed to encode png\n", stderr)
    exit(1)
}
try png.write(to: URL(fileURLWithPath: dstPath))
print("wrote \(dstPath)")
