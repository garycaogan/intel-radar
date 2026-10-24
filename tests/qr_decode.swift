import CoreImage
import Foundation
// Manual check for the share cards (macOS, see VERIFY.md): prints the payload of every QR code CoreImage finds in each image given on the command line.
for path in CommandLine.arguments.dropFirst() {
    guard let img = CIImage(contentsOf: URL(fileURLWithPath: path)) else { print("\(path)\tUNREADABLE"); continue }
    let det = CIDetector(ofType: CIDetectorTypeQRCode, context: nil, options: [CIDetectorAccuracy: CIDetectorAccuracyHigh])
    let found = (det?.features(in: img) ?? []).compactMap { ($0 as? CIQRCodeFeature)?.messageString }
    print("\((path as NSString).lastPathComponent)\t\(found.isEmpty ? "NONE" : found.joined(separator: " | "))")
}
