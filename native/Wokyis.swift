import Cocoa
import WebKit
import UniformTypeIdentifiers

final class ConsoleWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    var window: ConsoleWindow!
    var web: WKWebView!
    var child: Process?
    var serverLog: FileHandle?
    var fullscreen = true
    var savedPresentation: NSApplication.PresentationOptions?
    var root = ""
    var python = "/opt/homebrew/bin/python3"
    let endpoint = URL(string: "http://127.0.0.1:8765")!

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let url = Bundle.main.url(forResource: "config", withExtension: "json"),
           let data = try? Data(contentsOf: url),
           let settings = try? JSONSerialization.jsonObject(with: data) as? [String: String] {
            root = settings["root"] ?? ""
            python = settings["python"] ?? python
        }
        setupMenu()
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "wokyis")
        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.allowsBackForwardNavigationGestures = false
        web.setValue(false, forKey: "drawsBackground")
        let screen = wokyisScreen()
        window = ConsoleWindow(contentRect: screen.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.title = "Wokyis Console"
        window.backgroundColor = NSColor(red: 0.082, green: 0.09, blue: 0.082, alpha: 1)
        window.contentView = web
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        window.isReleasedWhenClosed = false
        window.setFrame(screen.frame, display: true)
        applyFullscreenChrome()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        verifyServer(startIfNeeded: true, attempts: 25)
        NotificationCenter.default.addObserver(self, selector: #selector(displayChanged), name: NSApplication.didChangeScreenParametersNotification, object: nil)
    }

    func wokyisScreen() -> NSScreen {
        NSScreen.screens.first(where: { $0.localizedName.lowercased().contains("wokyis") }) ?? NSScreen.main ?? NSScreen.screens[0]
    }

    func setupMenu() {
        let bar = NSMenu()
        let item = NSMenuItem()
        bar.addItem(item)
        let menu = NSMenu()
        menu.addItem(withTitle: "显示在 Wokyis", action: #selector(moveToDisplay), keyEquivalent: "1").target = self
        menu.addItem(withTitle: "切换全屏", action: #selector(toggleFullscreen), keyEquivalent: "f").target = self
        menu.addItem(withTitle: "刷新", action: #selector(reload), keyEquivalent: "r").target = self
        menu.addItem(NSMenuItem.separator())
        for (title, key, action) in [("上一个终端", NSF7FunctionKey, #selector(previousTerminal)), ("关闭当前终端", NSF8FunctionKey, #selector(closeTerminal)), ("下一个终端", NSF9FunctionKey, #selector(nextTerminal))] {
            let shortcut = NSMenuItem(title: title, action: action, keyEquivalent: String(UnicodeScalar(key)!))
            shortcut.keyEquivalentModifierMask = [.command]
            shortcut.target = self
            menu.addItem(shortcut)
        }
        menu.addItem(NSMenuItem.separator())
        menu.addItem(withTitle: "退出 Wokyis", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = menu
        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "编辑")
        for (title, action, key) in [("剪切", "cut:", "x"), ("拷贝", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")] {
            editMenu.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        editItem.submenu = editMenu
        bar.addItem(editItem)
        NSApp.mainMenu = bar
    }

    func verifyServer(startIfNeeded: Bool, attempts: Int) {
        var request = URLRequest(url: endpoint.appendingPathComponent("api/health"))
        request.timeoutInterval = 1
        URLSession.shared.dataTask(with: request) { [weak self] data, response, _ in
            DispatchQueue.main.async {
                guard let self else { return }
                if let data, let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any], object["app"] as? String == "wokyis-console" {
                    self.web.load(URLRequest(url: self.endpoint))
                } else if response != nil {
                    self.showError("8765 端口被其他服务占用。请关闭占用该端口的程序后重新打开。")
                } else if startIfNeeded {
                    self.startServer()
                    self.verifyServer(startIfNeeded: false, attempts: attempts)
                } else if attempts > 0 {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { self.verifyServer(startIfNeeded: false, attempts: attempts - 1) }
                } else {
                    self.showError("本地服务未启动。请检查项目目录和 Python 安装，再重新打开操作台。")
                }
            }
        }.resume()
    }

    func startServer() {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = ["-u", URL(fileURLWithPath: root).appendingPathComponent("server.py").path]
        process.currentDirectoryURL = URL(fileURLWithPath: root)
        let local = URL(fileURLWithPath: root).appendingPathComponent(".local")
        try? FileManager.default.createDirectory(at: local, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let log = local.appendingPathComponent("desktop-server.log")
        if !FileManager.default.fileExists(atPath: log.path) { FileManager.default.createFile(atPath: log.path, contents: nil, attributes: [.posixPermissions: 0o600]) }
        serverLog = try? FileHandle(forWritingTo: log)
        serverLog?.seekToEndOfFile()
        process.standardOutput = serverLog
        process.standardError = serverLog
        do { try process.run(); child = process }
        catch { showError("无法启动本地服务：请检查 Python 的安装路径。") }
    }

    func showError(_ message: String) {
        web.loadHTMLString("<meta charset='utf-8'><body style='background:#151715;color:#eee;font:28px -apple-system;padding:50px'><h1>Wokyis</h1><p>\(message)</p></body>", baseURL: nil)
    }

    func applyFullscreenChrome() {
        // Borderless sizing alone leaves the secondary screen's menu bar visible.
        window.level = fullscreen ? NSWindow.Level(rawValue: NSWindow.Level.mainMenu.rawValue + 1) : .normal
        if fullscreen && NSApp.isActive {
            if savedPresentation == nil { savedPresentation = NSApp.presentationOptions }
            NSApp.presentationOptions = [.hideDock, .hideMenuBar]
        } else if let original = savedPresentation {
            NSApp.presentationOptions = original
            savedPresentation = nil
        }
    }
    func applicationDidBecomeActive(_ notification: Notification) { applyFullscreenChrome() }
    func applicationDidResignActive(_ notification: Notification) {
        if let original = savedPresentation { NSApp.presentationOptions = original; savedPresentation = nil }
    }
    @objc func displayChanged() { if fullscreen { window.setFrame(wokyisScreen().frame, display: true) } }
    @objc func moveToDisplay() { fullscreen = true; window.styleMask = [.borderless]; window.setFrame(wokyisScreen().frame, display: true); window.makeKeyAndOrderFront(nil); applyFullscreenChrome() }
    @objc func toggleFullscreen() {
        fullscreen.toggle()
        if fullscreen { moveToDisplay() }
        else {
            window.styleMask = [.titled, .closable, .miniaturizable, .resizable]
            window.setContentSize(NSSize(width: 1080, height: 720))
            window.center()
            applyFullscreenChrome()
        }
    }
    @objc func reload() { web.reload() }
    @objc func previousTerminal() { web.evaluateJavaScript("window.terminalShortcut?.('prev')") }
    @objc func closeTerminal() { web.evaluateJavaScript("window.terminalShortcut?.('close')") }
    @objc func nextTerminal() { web.evaluateJavaScript("window.terminalShortcut?.('next')") }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame,
              message.frameInfo.securityOrigin.host == "127.0.0.1",
              message.frameInfo.securityOrigin.port == 8765,
              let body = message.body as? [String: String], let action = body["action"] else { return }
        if action == "fullscreen" { toggleFullscreen() }
        if action == "health-file" {
            let panel = NSOpenPanel()
            panel.allowedContentTypes = [.json]
            panel.canChooseFiles = true; panel.canChooseDirectories = true; panel.allowsMultipleSelection = false
            panel.message = "选择 Wokyis 健康同步文件夹，或单个 JSON 文件"
            panel.prompt = "连接"
            let healthFolder = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Mobile Documents/com~apple~CloudDocs/Wokyis")
            if FileManager.default.fileExists(atPath: healthFolder.path) { panel.directoryURL = healthFolder }
            panel.beginSheetModal(for: window) { response in
                guard response == .OK, let path = panel.url?.path,
                      let data = try? JSONSerialization.data(withJSONObject: [path]),
                      let literal = String(data: data, encoding: .utf8) else { return }
                self.web.evaluateJavaScript("window.dispatchEvent(new CustomEvent('health-source-selected',{detail:\(literal)[0]}))")
            }
        }
    }

    func local(_ url: URL) -> Bool { url.scheme == "http" && url.host == "127.0.0.1" && url.port == 8765 }
    func openExternal(_ url: URL) {
        guard url.scheme == "https" || url.scheme == "codex" else { return }
        NSWorkspace.shared.open(url)
    }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if local(url) || url.absoluteString == "about:blank" { decisionHandler(.allow) }
        else { openExternal(url); decisionHandler(.cancel) }
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url { openExternal(url) }
        return nil
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) { if let original = savedPresentation { NSApp.presentationOptions = original }; if let child, child.isRunning { child.terminate() }; try? serverLog?.close() }
}

let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = AppDelegate()
application.delegate = delegate
application.run()
