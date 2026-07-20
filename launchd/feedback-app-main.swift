// Native TCC identity for the pi-pulse feedback LaunchAgent.
//
// macOS does not reliably attribute protected-folder access to an
// interpreter-backed launch agent. This tiny app-bundled executable remains
// the responsible process while the existing shell/Python server runs as its
// child, allowing the user to grant Documents access to Pi Pulse Feedback
// rather than broad access to bash or Python.

import Foundation
import Darwin

let arguments = Array(CommandLine.arguments.dropFirst())

func option(_ name: String) -> String? {
    guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else {
        return nil
    }
    return arguments[index + 1]
}

func fail(_ message: String, code: Int32 = 64) -> Never {
    FileHandle.standardError.write(Data("Pi Pulse Feedback: \(message)\n".utf8))
    exit(code)
}

guard let repo = option("--repo"), repo.hasPrefix("/") else {
    fail("--repo must be an absolute path")
}

if arguments.contains("--authorize") {
    let fileManager = FileManager.default
    let statusDirectory = fileManager.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/pi-pulse-feedback")
    let statusFile = statusDirectory.appendingPathComponent("authorization-status")

    do {
        try fileManager.createDirectory(at: statusDirectory, withIntermediateDirectories: true)
        // Enumerating the protected checkout causes macOS to request the
        // app's Documents-folder consent in this interactive GUI launch.
        _ = try fileManager.contentsOfDirectory(atPath: repo)
        _ = try fileManager.contentsOfDirectory(atPath: repo + "/out")
        try "ok\n".write(to: statusFile, atomically: true, encoding: .utf8)
        exit(0)
    } catch {
        try? "denied: \(error)\n".write(to: statusFile, atomically: true, encoding: .utf8)
        fail("Documents access was not granted: \(error)", code: 77)
    }
}

guard arguments.contains("--serve") else {
    fail("expected --authorize or --serve")
}

let launcher = repo + "/scripts/feedback-server.sh"
guard FileManager.default.isExecutableFile(atPath: launcher) else {
    fail("server launcher is missing or not executable: \(launcher)", code: 78)
}

let child = Process()
child.executableURL = URL(fileURLWithPath: "/bin/bash")
child.arguments = [launcher]

// Keep this native app as the responsible process and forward launchd's
// termination signals to the actual server child.
signal(SIGTERM, SIG_IGN)
signal(SIGINT, SIG_IGN)
let signalQueue = DispatchQueue.global(qos: .utility)
let termSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: signalQueue)
let intSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: signalQueue)
termSource.setEventHandler { child.terminate() }
intSource.setEventHandler { child.interrupt() }
termSource.resume()
intSource.resume()

do {
    try child.run()
    child.waitUntilExit()
    termSource.cancel()
    intSource.cancel()
    exit(child.terminationStatus)
} catch {
    fail("could not start server: \(error)", code: 78)
}
