// 스캔 쪽 그림을 맥의 Vision 으로 읽는다. 모델을 부르기 전에 쪽을 가르고 번호를 찾는 데 쓴다.
// 빌드: swiftc -O tools/ocr_vision.swift -o bin/ocr_vision
// 사용: ocr_vision a.png b.png ...   → 그림마다 한 줄 JSON {"file":..,"w":..,"h":..,"lines":[[text,x,y,w,h],...]}
// 좌표는 그림 위쪽 왼쪽을 0 으로 한 0~1 비율이다.
import Foundation
import Vision
import AppKit

func ocr(_ path: String, fast: Bool) -> [String: Any] {
    guard let img = NSImage(contentsOfFile: path),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        return ["file": path, "error": "open"]
    }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = fast ? .fast : .accurate
    req.usesLanguageCorrection = false
    req.recognitionLanguages = ["en-US"]
    let handler = VNImageRequestHandler(cgImage: cg, options: [:])
    do { try handler.perform([req]) } catch { return ["file": path, "error": "\(error)"] }
    var lines: [[Any]] = []
    for obs in (req.results ?? []) {
        guard let top = obs.topCandidates(1).first else { continue }
        let b = obs.boundingBox
        lines.append([top.string, Double(b.minX), Double(1 - b.maxY), Double(b.width), Double(b.height)])
    }
    return ["file": path, "w": cg.width, "h": cg.height, "lines": lines]
}

var args = Array(CommandLine.arguments.dropFirst())
var fast = false
if let i = args.firstIndex(of: "--fast") { fast = true; args.remove(at: i) }
let group = DispatchGroup()
let q = DispatchQueue(label: "ocr", attributes: .concurrent)
var results = [[String: Any]](repeating: [:], count: args.count)
let lock = NSLock()
for (i, p) in args.enumerated() {
    group.enter()
    q.async {
        let r = ocr(p, fast: fast)
        lock.lock(); results[i] = r; lock.unlock()
        group.leave()
    }
}
group.wait()
for r in results {
    if let d = try? JSONSerialization.data(withJSONObject: r), let s = String(data: d, encoding: .utf8) { print(s) }
}
