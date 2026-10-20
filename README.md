# Wokyis 小屏操作台

本机运行的七页操作台。针对 5 英寸实体屏幕设计，支持 1080×720 和 1280×720。

## 打开和操作

建议双击 **Launch Wokyis.command**，由启动器先运行本地服务，再打开独立窗口。
启动器会等待窗口关闭，随后停止自己启动的服务；已有服务不会被它终止。
也可直接打开 `build/Wokyis Console.app`，首次启动可能需要 macOS 的项目目录访问许可。
独立应用自动寻找名称包含 **Wokyis** 的显示器，在该屏幕铺满显示。
如果找不到 Wokyis，使用主显示器。

- 触控板左右滑动、鼠标在空白区域横向拖动、键盘 ← / →：切换页面。
- 底部大按钮：直接跳转。列表和长摘要上下滚动。
- 右下角全屏按钮或 ⌘F：切换窗口/铺满屏幕。原生全屏窗口覆盖 Wokyis 顶部菜单栏；应用激活时隐藏菜单栏和 Dock，切到其他应用或退出全屏后恢复。
- ⌘R：刷新；⌘1：重新移到 Wokyis；⌘Q：退出。退出同时停止由应用或启动器启动的本地服务。
- 浏览器也可打开 `http://127.0.0.1:8765`。独立应用退出后，浏览器连接随之停止。
- 直接用手指滑动屏幕需要显示器和 macOS 实际提供触控输入；软件同时支持鼠标和触控板。

## 页面与当前能力

| 页面 | 已实现 | 需要配置或存在边界 |
| --- | --- | --- |
| 纽约时钟 | 四位全屏翻页钟、日期、秒数、自动夏令时 | 使用系统时钟与 `America/New_York` |
| 上海时钟 | 翻页钟、日期、秒数 | 使用 `Asia/Shanghai`，不固定两地时差 |
| 日历 | 上海时区月历与单日议程、多 Google 账号独立 OAuth、账号标签、分页、重复日程展开、共享事件去重、每分钟同步 | 需要导入自己的 Google Desktop OAuth 配置并逐个登录；日期、跨天归属和日程时间固定使用 `Asia/Shanghai` |
| 待办与健康 | Microsoft To Do 授权、清单同步、完成/恢复任务；健康同步文件/文件夹、多指标合并、采样时间与站立/喝水提醒 | Microsoft 需要应用 ID 和用户授权；Apple Health 需要 iPhone 提供同步文件，详见 [接入说明](docs/INTEGRATIONS.md) |
| Codex | 真实账户额度和每日 token 数、会话列表、最新消息、大字摘要、仅本屏隐藏/恢复 | 默认控制服务不可用时，自动通过官方 CLI 读取本机会话历史；此时标为“本机只读连接”，额度来源标为“终端 Codex 账号”，运行/审批数量显示“—” |
| Codex 回复/审批 | 当前本地 daemon 已加载会话的显式连接、快速回复/steer、请求摘要、单次命令批准/拒绝、单轮权限批准、用户问题回复 | 当前桌面版多数会话在另一个进程运行，未能在小屏直接审批；这些会话使用“在 Codex 中打开”。文件修改审批需在原应用检查差异。真实审批链路尚未端到端验证 |
| Claude | 本机 Claude Code 日志 token 用量，输入/输出/缓存明细，包含子代理日志，按请求去重 | 不包含 Claude 网页聊天全部用量；不是订阅额度。右上角链接打开官方额度页 |

Codex 的运行状态只代表当前连接服务已确认的状态，不能覆盖其他运行实例、远程服务器或 ChatGPT 云会话。只读连接无法确认桌面版的运行和审批状态；查看会话摘要后，可用“在 Codex 中打开”继续处理。终端 CLI 账号可能与桌面版不同。额度百分比与 token 数独立呈现；缺失数据不显示成零。连接中断时，历史列表显示缓存时间，旧的运行和审批状态不再作为实时状态显示。

“× 隐藏”只写本项目 `.local/settings.json`，不会调用 Codex/ChatGPT 的归档、删除或移出项目接口。“已隐藏”按钮恢复全部本屏隐藏项。

## 复古界面与常驻终端

设计约定在 DESIGN.md。Hack 与更纱等宽黑体 SC 已本地打包，中文、英文、输入框与终端采用同一字体栈。
第七页点击“新终端”创建本机 zsh；每个终端是独立 tmux session，使用项目 `.local/terminal.sock`，不触碰已有 tmux server。

- ⌘F7：上一个终端；⌘F9：下一个终端。
- ⌘F8：关闭当前终端，会结束其中运行的程序。其它终端继续运行。
- 从时钟等其它页面按快捷键，先打开终端页；不会盲关隐藏的终端。
- 顶部标签与底部大按钮提供同样操作。终端区内横向双指滑动切页；纵向滚动、方向键与选字仍归终端使用。
- 切页、刷新、退出并重新打开操作台，shell 及其中任务仍保留；重启 Mac 后不保留。
- 最多 12 个终端。终端使用当前 macOS 用户权限，不是 Codex 的受限执行沙箱。
- 某些 Mac 键盘需要同时按 Fn 才会发送 F7/F8/F9。快捷键仅在操作台应用获得焦点时生效。

## Google Calendar 首次连接

1. 在 [Google Cloud Console](https://console.cloud.google.com/) 选择自己的项目，启用 **Google Calendar API**。
2. 配置 OAuth consent screen。个人测试应用处于 Testing 时，把要连接的 Google 账号添加为测试用户。
3. 创建 OAuth client ID，应用类型选 **Desktop app / 桌面应用**，下载 JSON。
4. 操作台 → 日历 → 账号管理 → 导入 OAuth 配置 JSON。
5. 点击“添加 Google 账号”，在系统浏览器中完成登录和只读日历授权。每个账号重复一次。

客户端采用 loopback 回调和 PKCE。不要上传账号密码、浏览器 Cookie 或 Codex/Claude 令牌。
Google Testing 模式下，日历授权和 refresh token 会在 7 天后失效。长期个人使用需要把 Google 应用状态改为 **In production**，再为每个账号重新授权一次，取得新续期凭据。[Google 状态规则](https://support.google.com/cloud/answer/15549945)、[个人使用免验证条件](https://support.google.com/cloud/answer/13464323?hl=en)。

In production 不代表上架 Marketplace 或公开本机数据，但 Google 授权入口不再受测试用户名单限制。仅供本人使用时，可在私有的 `.local/google.json` 中设置 `allowedAccounts`，每项包含既有账号的 Google `id`（userinfo `sub`）和 `email`。本机会校验两个字段及新授权的邮箱验证状态，拒绝其他身份；这项限制只作用于本机操作台，不是 Google 侧的邮箱白名单。不要把个人账号列表或 OAuth 配置提交到仓库。

从测试模式迁移时，私有配置 `requireNewRefreshToken: true` 可要求每次重新授权必须收到新 refresh token，缺失时保留原连接。成功取得的新凭据记录收到时间；正常使用时自动刷新并保存返回的更新凭据。用户撤销或组织策略变化仍可能要求重登。
本项目不会创建、修改或删除任何日历事件。

## 本地运行与开发

需要 Python 3.10+ 和 tmux。Python 后端只用标准库；xterm.js、FitAddon、Hack 和更纱等宽黑体已固定版本放入 web/vendor，运行时无需联网下载。原生窗口构建需要 macOS Swift Command Line Tools。

```sh
python3 server.py
python3 scripts/build_app.py
python3 -m unittest discover -s tests -v
node --test tests/*.test.js
```

所有服务仅绑定 `127.0.0.1:8765`。`WOKYIS_CODEX_SOCKET` 可指定其他**已存在的官方 app-server Unix socket**；默认先连接 `~/.codex/app-server-control/app-server-control.sock`。仅在默认 socket 缺失或拒绝连接时，回退到 `codex app-server --stdio`；显式指定的 socket 出错时保留错误，不更改连接目标。CLI 从 PATH 或 `~/.local/bin/codex` 查找，需要在终端 Codex 中完成自己的登录。
回退进程仅允许初始化、会话列表/历史和账户用量读取；不会恢复、启动或修改会话，不能回复或审批。列表包含 CLI、编辑器和 app-server 来源，并排除子会话；不扫描日志修复元数据。退出操作台会清理自己创建的只读进程，不结束桌面 Codex 或其他 agent。不修改远程控制、全局配置或启动项。
程序不会自动启动、恢复、迁移或取消 agent；控制连接中仅在用户点击对应会话操作时进行明确动作。

`.local/` 不进 Git。Google 授权文件设为 `0600`，目录设为 `0700`；文件未使用钥匙串加密，因此应保护好当前 macOS 用户账户。HTTP Host、Origin、跨站请求和写请求头均进行校验，无 CORS 开放。OAuth URL、授权码和对话内容不写请求日志。
若移动整个项目目录，重新运行 `python3 scripts/build_app.py` 更新启动路径。
没有添加登录启动项，也没有改变系统休眠设置。

## 本次验证

- 74 项 Python 与 31 项 Node 检查通过。检查覆盖手势、时区、额度颜色、日历/待办授权、健康同步和终端保留；Codex 回归覆盖初始化并发、只读限制、断线重连、额度来源隔离、旧审批失效与自有进程清理。
- 通过 Finder 双击启动器成功启动本地服务及原生窗口；全屏显示和终端内横向翻页已检查。
- 基础检查覆盖：纽约 DST、时区日期、全天/跨日事件、共享事件去重、多账号分页与失败隔离、OAuth state/权限验证、Claude 日志去重、隐藏不触碰 Codex、审批跨会话/重复/离线保护、缺少操作内容时禁止批准。
- 真实本机 Codex 账户用量、会话列表、历史消息和 Claude 日志读取通过。
- 本地接口和静态资源返回 200；跨站读写、错误 Host 和缺失写请求头返回 403。
- 浏览器检查 1080×720、1280×720；独立 WKWebView 应用已在 Wokyis 上显示。
- 新增终端测试覆盖真实 shell 输入、PTY 尺寸、断开后状态保留、重建服务后连接、关闭隔离、非法目标与超限输入、多窗口 PTY 隔离、缓冲溢出后重新绘制。
- 未执行真实 Google 授权，未发送测试消息给用户的会话，未批准真实 agent 操作。

## 参考

- [Wokyis 官方时钟设置](https://wokyis.com/blogs/support/how-to-add-desktop-clock-widgets-to-the-monitor-on-wokyis-m5)
- [Codex App Server：账户、线程、审批协议](https://learn.chatgpt.com/docs/app-server)
- [Google Desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Google Calendar events.list](https://developers.google.com/workspace/calendar/api/v3/reference/events/list)
- [Claude Code 用量监测](https://code.claude.com/docs/en/monitoring-usage)

## 这版的交互与数据边界

- 一次横向 wheel 手势及其惯性尾部只切换一次；停止输入 320 ms 后才开始下一次手势。纵向意图锁定，防止列表滚动的横向漂移误触翻页。
- Codex 进度条按已用比例显示：低于 70% 绿色、70–84% 黄色、85–94% 橙色、95% 起红色，始终保留数字。
- 健康页显示时约每 10 秒读取本机同步文件或所选文件夹中的 `wokyis-health*.json`；这不会触发 iPhone 快捷指令或设备采样，更新仍需手机导出并等待 iCloud 同步。指标按真实采样时间合并。睡眠按来源合并重叠区间；导出仅标为「Apple Health（设备来源未提供）」时，统一取时间区间并集，无法区分 Apple Watch 与 Fitbit。旧样本不会标成实时心率，不计算没有依据的恢复分数；没有数据时显示破折号。
- 站立/喝水为可暂停、可设间隔的本机计时提醒，点击后重新计时，不写入 Apple Health。
- To Do 接入已通过模拟 Graph 响应验证；真实 Microsoft 授权和 iPhone 数据同步需由用户完成首次配置。
- 公开代码不包含技能安装包、账号授权、健康数据、会话日志或本机截图。字体与终端组件的来源、许可证在 `web/vendor/`。
