// meetpilot-speech: on-device transcription with Apple's SpeechAnalyzer (macOS 26+).
//
// Runs next to the MeetPilot app. Audio comes in on stdin and text goes out on stdout;
// nothing is written to disk and nothing leaves the computer (Apple's on-device model).
//
// Protocol (all little-endian):
//   start:  `meetpilot-speech --locale pt-BR` prints one JSON line:
//           {"ready":true,"locale":"pt-BR"} or {"error":"..."} and exits
//   loop:   u32 sample count N, then N float32 samples (16 kHz mono, -1…1)
//           -> one JSON line {"text":"..."} or {"error":"..."}
//   N = 0 ends the process. `--check` only prints the start line and exits.

import Foundation
#if HAS_SPEECH_ANALYZER
import AVFoundation
import Speech
#endif

func emit(_ obj: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: obj),
       let line = String(data: data, encoding: .utf8) {
        FileHandle.standardOutput.write((line + "\n").data(using: .utf8)!)
    }
}

func readExactly(_ count: Int) -> Data? {
    var out = Data()
    while out.count < count {
        let chunk = FileHandle.standardInput.readData(ofLength: count - out.count)
        if chunk.isEmpty { return nil }
        out.append(chunk)
    }
    return out
}

func readSamples() -> [Float]? {
    guard let header = readExactly(4) else { return nil }
    let n = Int(header.withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) }.littleEndian)
    if n == 0 { return nil }
    guard n <= 16_000 * 120, let body = readExactly(n * 4) else { return nil }
    return body.withUnsafeBytes { raw in
        (0..<n).map { Float(bitPattern: raw.loadUnaligned(fromByteOffset: $0 * 4, as: UInt32.self).littleEndian) }
    }
}

let args = CommandLine.arguments
var localeId = "pt-BR"
if let i = args.firstIndex(of: "--locale"), i + 1 < args.count { localeId = args[i + 1] }
let checkOnly = args.contains("--check")

#if HAS_SPEECH_ANALYZER
@available(macOS 26.0, *)
final class AppleTranscriber {
    let locale: Locale

    init(localeId: String) async throws {
        guard let loc = await SpeechTranscriber.supportedLocale(equivalentTo: Locale(identifier: localeId)) else {
            throw NSError(domain: "meetpilot", code: 1, userInfo: [NSLocalizedDescriptionKey: "language not supported: \(localeId)"])
        }
        locale = loc
        // The system keeps models only for languages an app has reserved.
        if !(await AssetInventory.reservedLocales.contains(loc)) {
            _ = try await AssetInventory.reserve(locale: loc)
        }
        let probe = SpeechTranscriber(locale: loc, transcriptionOptions: [], reportingOptions: [], attributeOptions: [])
        // Downloads Apple's on-device model for this language the first time.
        if let request = try await AssetInventory.assetInstallationRequest(supporting: [probe]) {
            try await request.downloadAndInstall()
        }
    }

    func transcribe(_ samples: [Float]) async throws -> String {
        let transcriber = SpeechTranscriber(locale: locale, transcriptionOptions: [], reportingOptions: [], attributeOptions: [])
        let analyzer = SpeechAnalyzer(modules: [transcriber])
        guard let target = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else {
            throw NSError(domain: "meetpilot", code: 2, userInfo: [NSLocalizedDescriptionKey: "no audio format"])
        }
        let buffer = try convert(samples, to: target)
        let collector = Task { () throws -> String in
            var text = ""
            for try await result in transcriber.results where result.isFinal {
                text += String(result.text.characters)
            }
            return text
        }
        let (stream, input) = AsyncStream<AnalyzerInput>.makeStream()
        try await analyzer.start(inputSequence: stream)
        input.yield(AnalyzerInput(buffer: buffer))
        input.finish()
        try await analyzer.finalizeAndFinishThroughEndOfInput()
        return try await collector.value.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func convert(_ samples: [Float], to target: AVAudioFormat) throws -> AVAudioPCMBuffer {
        let source = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16_000, channels: 1, interleaved: false)!
        let input = AVAudioPCMBuffer(pcmFormat: source, frameCapacity: AVAudioFrameCount(samples.count))!
        input.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { ptr in
            input.floatChannelData![0].update(from: ptr.baseAddress!, count: samples.count)
        }
        if source == target { return input }
        guard let converter = AVAudioConverter(from: source, to: target) else {
            throw NSError(domain: "meetpilot", code: 3, userInfo: [NSLocalizedDescriptionKey: "cannot convert audio"])
        }
        let ratio = target.sampleRate / source.sampleRate
        let capacity = AVAudioFrameCount(Double(samples.count) * ratio) + 1024
        let output = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity)!
        var fed = false
        var error: NSError?
        converter.convert(to: output, error: &error) { _, status in
            if fed {
                status.pointee = .endOfStream
                return nil
            }
            fed = true
            status.pointee = .haveData
            return input
        }
        if let error { throw error }
        return output
    }
}

@available(macOS 26.0, *)
func run() async {
    do {
        let engine = try await AppleTranscriber(localeId: localeId)
        emit(["ready": true, "locale": engine.locale.identifier])
        if checkOnly { return }
        while let samples = readSamples() {
            do {
                emit(["text": try await engine.transcribe(samples)])
            } catch {
                emit(["error": error.localizedDescription])
            }
        }
    } catch {
        emit(["error": error.localizedDescription])
        exit(1)
    }
}

if #available(macOS 26.0, *) {
    await run()  // top-level await: main.swift runs as an async entry point
} else {
    emit(["error": "Apple transcription needs macOS 26 or later."])
    exit(2)
}
#else
emit(["error": "This build of MeetPilot was made without Apple's speech engine (needs the macOS 26 SDK)."])
exit(2)
#endif
