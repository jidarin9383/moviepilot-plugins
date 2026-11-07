// SPDX-License-Identifier: GPL-3.0-only
import AppKit
import ApplicationServices
import Darwin

let dispatchEnvironment = ProcessInfo.processInfo.environment
let stagingPath = dispatchEnvironment["CLOUDSHARE_MAC_STAGING"] ?? "/Volumes/Downloads/cloudshare-staging"
let mountPath = dispatchEnvironment["CLOUDSHARE_SMB_MOUNT"] ?? "/Volumes/Downloads"

struct Task: Decodable {
    let id: String
    let folder: String
    let folderID: String
    let files: [String]
}

struct AlipanTask: Decodable {
    let id: String
    let attempt: Int?
    let userId: String
    let driveId: String
    let folderID: String
    let files: [AlipanFile]
}

struct AlipanFile: Decodable {
    let name: String
    let size: Int64
}

struct DispatchFailure: Error {
    let message: String
    init(_ message: String) { self.message = message }
}

func attribute<T>(_ element: AXUIElement, _ name: String) -> T? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else {
        return nil
    }
    return value as? T
}

func elements(_ root: AXUIElement) -> [AXUIElement] {
    var result = [root]
    var index = 0
    while index < result.count && result.count < 5000 {
        result += attribute(result[index], kAXChildrenAttribute) ?? []
        index += 1
    }
    return result
}

func matches(_ element: AXUIElement, role: String, text: String) -> Bool {
    let elementRole: String? = attribute(element, kAXRoleAttribute)
    guard elementRole == role else { return false }
    let labels: [String?] = [
        attribute(element, kAXTitleAttribute), attribute(element, kAXDescriptionAttribute),
        attribute(element, kAXValueAttribute),
    ]
    return labels.compactMap { $0 }.contains(text)
}

func compact(_ value: String) -> String {
    value.filter { !$0.isWhitespace }
}

func waitFor<T>(_ reason: String, _ probe: () -> T?) throws -> T {
    let deadline = Date().addingTimeInterval(12)
    repeat {
        if let value = probe() { return value }
        RunLoop.current.run(until: Date().addingTimeInterval(0.25))
    } while Date() < deadline
    throw DispatchFailure(reason)
}

func click(_ element: AXUIElement, app: NSRunningApplication, count: Int = 1, physical: Bool = false) throws {
    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier else {
        throw DispatchFailure("Quark is no longer the active application.")
    }
    if count == 1, !physical, AXUIElementPerformAction(element, kAXPressAction as CFString) == .success {
        return
    }
    let positionValue: AXValue? = attribute(element, kAXPositionAttribute)
    let sizeValue: AXValue? = attribute(element, kAXSizeAttribute)
    var position = CGPoint.zero
    var size = CGSize.zero
    guard let positionValue, let sizeValue,
          AXValueGetValue(positionValue, .cgPoint, &position),
          AXValueGetValue(sizeValue, .cgSize, &size),
          size.width > 0, size.height > 0 else {
        throw DispatchFailure("Quark control is not visible.")
    }
    let point = CGPoint(x: position.x + size.width / 2, y: position.y + size.height / 2)
    for number in 1...count {
        for type in [CGEventType.leftMouseDown, .leftMouseUp] {
            guard let event = CGEvent(mouseEventSource: nil, mouseType: type,
                                      mouseCursorPosition: point, mouseButton: .left) else {
                throw DispatchFailure("Cannot send a mouse event.")
            }
            event.setIntegerValueField(.mouseEventClickState, value: Int64(number))
            event.post(tap: .cghidEventTap)
        }
    }
}

func key(_ code: CGKeyCode, flags: CGEventFlags = []) {
    for down in [true, false] {
        let event = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: down)
        event?.flags = flags
        event?.post(tap: .cghidEventTap)
    }
}

func mountedNASStaging() -> URL? {
    let path = stagingPath
    let root = URL(fileURLWithPath: path)
    guard path.hasPrefix("/"), mountPath.hasPrefix("/"), mountPath != "/",
          root.resolvingSymlinksInPath().path == path,
          let volume = try? root.resourceValues(forKeys: [.volumeURLKey, .volumeIsLocalKey]),
          volume.volumeIsLocal == false,
          (volume.allValues[.volumeURLKey] as? URL)?.path == mountPath else { return nil }
    return root
}

func ensureNASMount() -> Bool {
    if mountedNASStaging() != nil { return true }
    let directory = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/CloudShare")
    let marker = directory.appendingPathComponent("mount-attempt")
    let modified = (try? FileManager.default.attributesOfItem(atPath: marker.path))?[.modificationDate] as? Date
    let elapsed = modified.map { Date().timeIntervalSince($0) } ?? 300
    if elapsed >= 300 {
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try? Data().write(to: marker)
        if let address = dispatchEnvironment["CLOUDSHARE_SMB_URL"],
           let url = URL(string: address), url.scheme == "smb", url.host != nil,
           url.user == nil, url.password == nil { NSWorkspace.shared.open(url) }
    }
    return false
}

func confirmAlipanDownload(staging: String, taskID: String) throws {
    guard AXIsProcessTrusted(),
          taskID.range(of: "^[0-9a-f]{40}$", options: .regularExpression) != nil,
          staging == stagingPath,
          mountedNASStaging() != nil else {
        throw DispatchFailure("Alipan confirmation requires Accessibility and the configured NAS path.")
    }
    let session = CGSessionCopyCurrentDictionary() as? [String: Any]
    guard session?["kCGSSessionOnConsoleKey"] as? Bool == true,
          session?["CGSSessionScreenIsLocked"] as? Bool != true,
          let app = NSRunningApplication.runningApplications(withBundleIdentifier: "com.alicloud.smartdrive").first else {
        throw DispatchFailure("Alipan confirmation is waiting for the logged-in desktop.")
    }
    let root = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetAttributeValue(root, "AXManualAccessibility" as CFString, kCFBooleanTrue)
    AXUIElementSetAttributeValue(root, "AXEnhancedUserInterface" as CFString, kCFBooleanTrue)
    func hasLabel(_ element: AXUIElement, role: String, text: String) -> Bool {
        guard (attribute(element, kAXRoleAttribute) as String?) == role else { return false }
        let labels: [String?] = [attribute(element, kAXTitleAttribute),
                                attribute(element, kAXDescriptionAttribute),
                                attribute(element, kAXValueAttribute)]
        return labels.compactMap { $0 }.contains { compact($0) == compact(text) }
    }
    var windows: [AXUIElement] = attribute(root, kAXWindowsAttribute) ?? []
    if let focused: AXUIElement = attribute(root, kAXFocusedWindowAttribute) { windows.append(focused) }
    let trees = windows.prefix(20).map(elements)
    guard let dialog = trees.first(where: { nodes in
            nodes.contains {
                hasLabel($0, role: kAXStaticTextRole, text: "下载 \(taskID) 到") ||
                hasLabel($0, role: kAXGroupRole, text: "下载 \(taskID) 到")
            } &&
            nodes.contains { hasLabel($0, role: kAXTextFieldRole, text: staging) } &&
            nodes.filter { hasLabel($0, role: kAXButtonRole, text: "确认") }.count == 1
        }) else {
        let nodes = trees.flatMap { $0 }
        let paths = nodes.filter { hasLabel($0, role: kAXTextFieldRole, text: staging) }.count
        let buttons = nodes.filter { hasLabel($0, role: kAXButtonRole, text: "确认") }.count
        if NSWorkspace.shared.frontmostApplication?.processIdentifier != app.processIdentifier,
           [CGEventType.keyDown, .mouseMoved, .leftMouseDown, .rightMouseDown].allSatisfy({
               CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: $0) >= 30
           }) {
            app.activate(options: [])
        }
        throw DispatchFailure("Alipan dialog is not ready (windows \(windows.count), paths \(paths), confirmations \(buttons)).")
    }
    if let checkbox = dialog.first(where: {
        hasLabel($0, role: kAXCheckBoxRole, text: "设为默认路径")
    }), (attribute(checkbox, kAXValueAttribute) as Int?) == 0 {
        guard AXUIElementPerformAction(checkbox, kAXPressAction as CFString) == .success else {
            throw DispatchFailure("Alipan rejected the default download path setting.")
        }
        _ = try waitFor("Alipan did not retain the default download path setting.") {
            (attribute(checkbox, kAXValueAttribute) as Int?) == 1 ? true : nil
        }
    }
    guard let button = dialog.first(where: {
        hasLabel($0, role: kAXButtonRole, text: "确认")
    }), AXUIElementPerformAction(button, kAXPressAction as CFString) == .success else {
        throw DispatchFailure("Alipan rejected the matching download confirmation.")
    }
    print("Confirmed Alipan download for \(taskID).")
}

func validAlipanTask(_ task: AlipanTask) -> Bool {
    task.id.range(of: "^[0-9a-f]{40}$", options: .regularExpression) != nil &&
    (1...100000).contains(task.attempt ?? 1) &&
    [task.userId, task.driveId, task.folderID].allSatisfy {
        $0.range(of: "^[A-Za-z0-9_-]{1,128}$", options: .regularExpression) != nil
    } && !task.files.isEmpty && task.files.count <= 100 && task.files.allSatisfy { file in
        file.size > 0 && !["", ".", ".."].contains(file.name) &&
        !file.name.unicodeScalars.contains { $0.value < 32 || "/\\:".unicodeScalars.contains($0) }
    }
}

func loadAlipanTasks(_ manifest: URL) throws -> [AlipanTask] {
    let data = try Data(contentsOf: manifest)
    guard data.count <= 1_048_576 else { throw DispatchFailure("Alipan manifest is oversized.") }
    let tasks = try JSONDecoder().decode([AlipanTask].self, from: data)
    guard tasks.count <= 1000 else { throw DispatchFailure("Alipan manifest has too many tasks.") }
    return tasks.filter(validAlipanTask)
}

func processAlipanTasks() {
    let staging = stagingPath
    let manifest = URL(fileURLWithPath: staging).appendingPathComponent(".cloudshare-aliyun-dispatch.json")
    guard let tasks = try? loadAlipanTasks(manifest), !tasks.isEmpty else { return }
    guard AXIsProcessTrusted() else {
        fputs("CloudShare Dispatch requires Accessibility permission.\n", stderr)
        return
    }
    guard let root = mountedNASStaging() else {
        fputs("CloudShare Dispatch is waiting for the NAS SMB mount.\n", stderr)
        return
    }
    let claims = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/CloudShare/aliyun-dispatch")
    try? FileManager.default.createDirectory(at: claims, withIntermediateDirectories: true,
                                            attributes: [.posixPermissions: 0o700])
    for task in tasks {
        let attempt = task.attempt ?? 1
        let claim = claims.appendingPathComponent("\(task.id)-\(attempt)")
        do {
            let destination = root.appendingPathComponent(task.id)
            guard destination.resolvingSymlinksInPath().path == destination.path else {
                throw DispatchFailure("Alipan task directory crosses a symlink.")
            }
            let partial = task.files.contains {
                FileManager.default.fileExists(atPath: destination.appendingPathComponent($0.name + ".part").path)
            }
            let complete = task.files.allSatisfy { file in
                let path = destination.appendingPathComponent(file.name).path
                let info = try? FileManager.default.attributesOfItem(atPath: path)
                return (info?[.size] as? NSNumber)?.int64Value == file.size
            }
            if partial || complete { continue }
            if !FileManager.default.fileExists(atPath: claim.path) {
                if FileManager.default.fileExists(atPath: destination.path) {
                    guard Darwin.rmdir(destination.path) == 0 else {
                        throw DispatchFailure("Alipan task directory contains unexpected files.")
                    }
                }
                try Data("launching\n".utf8).write(to: claim, options: .withoutOverwriting)
                let payload: [String: Any] = ["userName": "CloudShare", "userId": task.userId,
                                              "driveId": task.driveId, "fileIds": [task.folderID]]
                let data = try JSONSerialization.data(withJSONObject: payload)
                guard let encoded = String(data: data, encoding: .utf8)?.addingPercentEncoding(withAllowedCharacters: .alphanumerics),
                      let url = URL(string: "smartdrive://common/download?data=" + encoded),
                      NSWorkspace.shared.open(url) else {
                    throw DispatchFailure("Alipan did not accept the official download command.")
                }
                try "launched\n".write(to: claim, atomically: true, encoding: .utf8)
            }
            guard (try? String(contentsOf: claim, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)) == "launched" else { continue }
            try confirmAlipanDownload(staging: staging, taskID: task.id)
            try "confirmed\n".write(to: claim, atomically: true, encoding: .utf8)
        } catch let error as DispatchFailure {
            fputs(error.message + "\n", stderr)
        } catch {
            fputs("Alipan confirmation needs attention.\n", stderr)
        }
    }
}

func dispatch(_ task: Task, claims: URL) throws {
    guard AXIsProcessTrusted() else {
        throw DispatchFailure("Allow Accessibility for this helper in System Settings.")
    }
    guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: "com.quark.desktop").first else {
        throw DispatchFailure("Open the Quark desktop app and sign in first.")
    }
    let root = AXUIElementCreateApplication(app.processIdentifier)
    if let url = app.bundleURL {
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        NSWorkspace.shared.openApplication(at: url, configuration: configuration)
    }
    _ = try waitFor("Quark did not become the active app.") {
        NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier ? true : nil
    }
    func currentElements() -> [AXUIElement] {
        guard let window: AXUIElement = attribute(root, kAXFocusedWindowAttribute) else { return [] }
        return elements(window)
    }
    func folderMatches() -> Bool {
        currentElements().contains { element in
            let url: URL? = attribute(element, kAXURLAttribute)
            return url?.absoluteString.contains("/\(task.folderID)-") == true
        }
    }
    if !folderMatches() {
        let search = try waitFor("Open Quark Drive inside the Quark app.") {
            currentElements().first { element in
                let role: String? = attribute(element, kAXRoleAttribute)
                let placeholder: String? = attribute(element, kAXPlaceholderValueAttribute)
                let description: String? = attribute(element, kAXDescriptionAttribute)
                return role == kAXTextFieldRole && [placeholder, description].compactMap { $0 }
                    .contains { $0.contains("搜网盘") }
            }
        }
        try click(search, app: app, physical: true)
        let focus = AXUIElementSetAttributeValue(search, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        let changed = AXUIElementSetAttributeValue(search, kAXValueAttribute as CFString, task.id as CFString)
        guard focus == .success, changed == .success else {
            throw DispatchFailure("Quark search rejected input (focus \(focus.rawValue), value \(changed.rawValue)).")
        }
        _ = try waitFor("Quark search field did not receive the task ID.") {
            currentElements().first { matches($0, role: kAXTextFieldRole, text: task.id) }
        }
        key(36)
        _ = try waitFor("Quark did not open its search results.") {
            currentElements().first { element in
                let url: URL? = attribute(element, kAXURLAttribute)
                return url?.absoluteString.contains("#/list/search") == true &&
                    url?.absoluteString.contains(task.id) == true
            }
        }
        if let global = currentElements().first(where: {
            matches($0, role: kAXMenuItemRole, text: "全部文件")
        }), (attribute(global, kAXSelectedAttribute) as Bool?) == false {
            try click(global, app: app)
        }

        let folder = try waitFor("Cannot locate the matching Quark folder.") {
            let rows = currentElements().filter {
                (attribute($0, kAXRoleAttribute) as String?) == kAXRowRole
            }
            let matchingRows = rows.filter { row in
                let nodes = elements(row)
                let hasID = nodes.contains { matches($0, role: kAXStaticTextRole, text: task.id) }
                let folderSize = nodes.contains { matches($0, role: kAXStaticTextRole, text: "-") }
                return hasID && folderSize
            }
            guard matchingRows.count == 1, let row = matchingRows.first else { return nil as AXUIElement? }
            return elements(row).first { element in
                let role: String? = attribute(element, kAXRoleAttribute)
                let size: AXValue? = attribute(element, kAXSizeAttribute)
                var bounds = CGSize.zero
                return role == kAXStaticTextRole && matches(element, role: kAXStaticTextRole, text: task.id)
                    && size.map { AXValueGetValue($0, .cgSize, &bounds) } == true
                    && bounds.width > 0 && bounds.height > 0
            }
        }
        try click(folder, app: app, count: 2)
    }
    let selections: [AXUIElement] = try waitFor("Quark folder or file selection does not match the dispatch.") {
        guard folderMatches() else { return nil }
        let rows = currentElements().filter { (attribute($0, kAXRoleAttribute) as String?) == kAXRowRole }
        var selected: [AXUIElement] = []
        for file in task.files {
            let matching = rows.filter { row in
                let cells = elements(row).filter { (attribute($0, kAXRoleAttribute) as String?) == kAXCellRole }
                return cells.contains { cell in
                    let pieces: [String] = elements(cell).compactMap {
                        let role: String? = attribute($0, kAXRoleAttribute)
                        return role == kAXStaticTextRole ? attribute($0, kAXValueAttribute) : nil
                    }
                    let name = compact(pieces.joined())
                    return name == compact(file) || name == compact(file) + "更新"
                }
            }
            guard matching.count == 1, let row = matching.first,
                  let checkbox = elements(row).first(where: {
                      (attribute($0, kAXRoleAttribute) as String?) == kAXCheckBoxRole
                  }),
                  (attribute(checkbox, kAXValueAttribute) as Int?) == 0 else { return nil }
            selected.append(checkbox)
        }
        return selected
    }
    guard !currentElements().contains(where: { matches($0, role: kAXButtonRole, text: "下载") }) else {
        throw DispatchFailure("The Quark selection was not empty.")
    }
    for checkbox in selections {
        try click(checkbox, app: app)
        _ = try waitFor("Quark did not select the expected file.") {
            (attribute(checkbox, kAXValueAttribute) as Int?) == 1 ? true : nil
        }
    }
    let download = try waitFor("Quark did not expose a unique Download button.") {
        let found = currentElements().filter { matches($0, role: kAXButtonRole, text: "下载") }
        return found.count == 1 ? found.first : nil
    }
    let claim = claims.appendingPathComponent(task.id, isDirectory: true)
    try FileManager.default.createDirectory(at: claim, withIntermediateDirectories: false)
    try click(download, app: app)
    print("Download requested for \(task.id). MoviePilot verifies the staged files.")
}

func runCommand(_ arguments: [String]) throws {
    if arguments.count == 2, arguments[0] == "list-aliyun" {
        for task in try loadAlipanTasks(URL(fileURLWithPath: arguments[1])) { print(task.id) }
        return
    }
    if arguments.count == 3, arguments[0] == "confirm-aliyun" {
        try confirmAlipanDownload(staging: arguments[1], taskID: arguments[2])
        return
    }
    guard arguments.count == 3, ["list", "download", "auto"].contains(arguments[0]) else {
        throw DispatchFailure("Usage: quark-dispatch list|download|auto MANIFEST TASK_ID_OR_NEXT")
    }
    let automatic = arguments[0] == "auto"
    if automatic {
        guard ensureNASMount() else { return }
        processAlipanTasks()
        let session = CGSessionCopyCurrentDictionary() as? [String: Any]
        guard session?["kCGSSessionOnConsoleKey"] as? Bool == true,
              session?["CGSSessionScreenIsLocked"] as? Bool != true,
              [CGEventType.keyDown, .mouseMoved, .leftMouseDown, .rightMouseDown].allSatisfy({
                  CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: $0) >= 30
              }) else { return }
    }
    let path = URL(fileURLWithPath: arguments[1])
    let tasks = try JSONDecoder().decode([Task].self, from: Data(contentsOf: path))
    let valid = tasks.filter { task in
        task.id.range(of: "^[0-9a-f]{40}$", options: .regularExpression) != nil &&
        task.folderID.range(of: "^[0-9a-f]{32}$", options: .regularExpression) != nil &&
        (task.folder == task.id || task.folder.hasSuffix(" - " + task.id)) &&
        !task.files.isEmpty && task.files.count <= 100 &&
        Set(task.files.map(compact)).count == task.files.count
    }
    if arguments[0] == "list" {
        for task in valid { print("\(task.id)  \(task.folder)  (\(task.files.count) files)") }
    } else {
        let claims = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/CloudShare/dispatch", isDirectory: true)
        try FileManager.default.createDirectory(at: claims, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        guard let task = valid.first(where: {
            (arguments[2] == "next" || arguments[2] == $0.id) &&
            !FileManager.default.fileExists(atPath: claims.appendingPathComponent($0.id).path)
        }) else {
            if automatic { return }
            throw DispatchFailure("No unclaimed matching task.")
        }
        try dispatch(task, claims: claims)
    }
}

func reportFailure(_ error: Error) {
    if let failure = error as? DispatchFailure { fputs(failure.message + "\n", stderr) }
    else { fputs("CloudShare dispatch failed: \(error.localizedDescription)\n", stderr) }
}

func runService() {
    let application = NSApplication.shared
    application.setActivationPolicy(.accessory)
    let logs = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/CloudShare")
    try? FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
    freopen(logs.appendingPathComponent("dispatch.log").path, "a", stdout)
    freopen(logs.appendingPathComponent("dispatch-error.log").path, "a", stderr)
    print("CloudShare Dispatch service started. Accessibility: \(AXIsProcessTrusted()).")
    fflush(stdout)
    var busy = false
    func tick() {
        guard !busy else { return }
        busy = true
        defer { busy = false; fflush(stdout) }
        do {
            try runCommand(["auto", URL(fileURLWithPath: stagingPath).appendingPathComponent(".cloudshare-dispatch.json").path, "next"])
        } catch { reportFailure(error) }
    }
    let timer = Timer(timeInterval: 30, repeats: true) { _ in tick() }
    RunLoop.main.add(timer, forMode: .common)
    withExtendedLifetime(timer) {
        tick()
        application.run()
    }
}

let arguments = Array(CommandLine.arguments.dropFirst())
if arguments == ["check-access"] {
    print(AXIsProcessTrusted() ? "Accessibility authorized." : "Accessibility permission required.")
    exit(AXIsProcessTrusted() ? 0 : 1)
}
if arguments == ["service"] { runService(); exit(0) }
do { try runCommand(arguments) }
catch { reportFailure(error); exit(1) }
