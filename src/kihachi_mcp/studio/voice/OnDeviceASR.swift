import Foundation
import Speech

func output(_ payload: [String: Any]) {
    if CommandLine.arguments.count == 3,
       let data = try? JSONSerialization.data(withJSONObject: payload) {
        try? data.write(to: URL(fileURLWithPath: CommandLine.arguments[2]), options: .atomic)
    }
}

func fail(_ message: String, _ code: Int32 = 1) -> Never {
    output(["ok": false, "error": message])
    fputs(message + "\n", stderr)
    exit(code)
}

guard [2, 3].contains(CommandLine.arguments.count) else { fail("audio_path_required") }
if CommandLine.arguments[1] == "--status" {
    let candidate = SFSpeechRecognizer(locale: Locale(identifier: "ja-JP"))
    let permission: String
    switch SFSpeechRecognizer.authorizationStatus() {
    case .authorized: permission = "authorized"
    case .denied: permission = "denied"
    case .restricted: permission = "restricted"
    case .notDetermined: permission = "not_determined"
    @unknown default: permission = "unknown"
    }
    let status: [String: Any] = [
        "authorization": permission,
        "authorize_supported": true,
        "usage_description_present": (Bundle.main.object(forInfoDictionaryKey: "NSSpeechRecognitionUsageDescription") as? String)?.isEmpty == false,
        "on_device": candidate?.supportsOnDeviceRecognition ?? false,
        "available": candidate?.isAvailable ?? false
    ]
    let data = try JSONSerialization.data(withJSONObject: status, options: [.sortedKeys])
    print(String(data: data, encoding: .utf8)!)
    exit(0)
}
guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "ja-JP")),
      recognizer.supportsOnDeviceRecognition else { fail("on_device_japanese_unavailable") }

var authorization = SFSpeechRecognizer.authorizationStatus()
if authorization == .notDetermined {
    var completed = false
    SFSpeechRecognizer.requestAuthorization { status in
        authorization = status
        completed = true
    }
    let deadline = Date().addingTimeInterval(30)
    while !completed && Date() < deadline {
        RunLoop.current.run(until: Date().addingTimeInterval(0.05))
    }
}
if CommandLine.arguments[1] == "--authorize" {
    let permission: String
    switch authorization {
    case .authorized: permission = "authorized"
    case .denied: permission = "denied"
    case .restricted: permission = "restricted"
    case .notDetermined: permission = "not_determined"
    @unknown default: permission = "unknown"
    }
    output(["ok": authorization == .authorized, "authorization": permission])
    exit(0)
}
guard authorization == .authorized else { fail("speech_permission_required") }
guard recognizer.isAvailable else { fail("recognizer_unavailable") }

let url = URL(fileURLWithPath: CommandLine.arguments[1])
let request = SFSpeechURLRecognitionRequest(url: url)
request.requiresOnDeviceRecognition = true
request.shouldReportPartialResults = false
request.contextualStrings = ["KIHACHI", "Mutashon Funk", "キック", "ハイハット", "Ableton Live"]
var finished = false
var transcription = ""
var alternatives: [String] = []
var failure = "recognition_failed"
let task = recognizer.recognitionTask(with: request) { result, error in
    if let result = result, result.isFinal {
        transcription = result.bestTranscription.formattedString
        alternatives = Array(result.transcriptions.prefix(4)).map { $0.formattedString }
        finished = true
    } else if let error = error {
        failure = "recognition_failed: \(error.localizedDescription)"
        finished = true
    }
}
let deadline = Date().addingTimeInterval(45)
while !finished && Date() < deadline {
    RunLoop.current.run(until: Date().addingTimeInterval(0.05))
}
if !finished { task.cancel(); fail("recognition_timeout") }
if transcription.isEmpty { fail(failure) }
output(["ok": true, "transcript": transcription, "alternatives": alternatives])
