// Native TCC identity for the scheduled pi-pulse LaunchAgent.
//
// macOS does not reliably attribute protected-folder access to an
// interpreter-backed launch agent: pointing launchd at bash and letting a
// child such as node open files under ~/Documents is denied without a
// prompt on a cold start (the expand guard fetch then dies with EPERM
// before reaching the network). This tiny app-bundled executable remains
// the responsible process while pulse.sh and every stage child (bash,
// python, node, pi) runs beneath it, so a single Documents grant to
// "Pi Pulse" covers the whole tree.

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
    FileHandle.standardError.write(Data("Pi Pulse: \(message)\n".utf8))
    exit(code)
}

guard let repo = option("--repo"), repo.hasPrefix("/") else {
    fail("--repo must be an absolute path")
}

if arguments.contains("--authorize") {
    let fileManager = FileManager.default
    let statusDirectory = fileManager.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/pi-pulse")
    let statusFile = statusDirectory.appendingPathComponent("authorization-status")

    do {
        try fileManager.createDirectory(at: statusDirectory, withIntermediateDirectories: true)
        // Enumerating the protected checkout causes macOS to request the
        // app's Documents-folder consent in this interactive GUI launch.
        // The broker directory is listed too because that is the exact
        // path node must read during expand.
        _ = try fileManager.contentsOfDirectory(atPath: repo)
        _ = try fileManager.contentsOfDirectory(atPath: repo + "/sources/brave-guard")
        try "ok\n".write(to: statusFile, atomically: true, encoding: .utf8)
        exit(0)
    } catch {
        try? "denied: \(error)\n".write(to: statusFile, atomically: true, encoding: .utf8)
        fail("Documents access was not granted: \(error)", code: 77)
    }
}

guard arguments.contains("--run") else {
    fail("expected --authorize or --run")
}

let launcher = repo + "/pulse.sh"
guard FileManager.default.isExecutableFile(atPath: launcher) else {
    fail("pulse launcher is missing or not executable: \(launcher)", code: 78)
}

let child = Process()
child.executableURL = URL(fileURLWithPath: "/bin/bash")
child.arguments = [launcher]
child.currentDirectoryURL = URL(fileURLWithPath: repo)

// Keep this native app as the responsible process and forward launchd's
// termination signals to the pulse child.
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
    fail("could not start pulse.sh: \(error)", code: 78)
}
