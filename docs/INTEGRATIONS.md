# Microsoft To Do 和 Apple Health 接入

首次未连接时，操作台显示连接入口和空值，不会生成示例任务或身体指标。
授权、数据文件只存在本机 `.local/`，不随 Git 推送。

## Microsoft To Do

1. 在 [Microsoft Entra 应用注册](https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade) 新建你自己的应用。私人 To Do 可选「仅个人 Microsoft 账号」；也支持「任何组织目录和个人 Microsoft 账号」。
2. 在 Authentication 添加 **Mobile and desktop applications** 平台，把 `http://127.0.0.1:8765/oauth/microsoft/callback` 设为回调地址。如果控制台不接受 HTTP IP 地址，用应用清单配置这个 loopback URI，参见 [重定向 URI 限制](https://learn.microsoft.com/en-us/entra/identity-platform/reply-url)。使用公开客户端，不创建 client secret。
3. 复制 Application (client) ID，在操作台「待办 → 连接与提醒」选择对应账号类型并粘贴。个人账号使用 `consumers` 登录入口，组织和个人账号使用 `common`。也可用环境变量 `WOKYIS_MICROSOFT_CLIENT_ID` 配置默认的 common 应用。
4. 点击「登录 Microsoft」，在系统浏览器里完成登录和授权：`Tasks.ReadWrite`（读取和完成/恢复你的任务）、`User.Read`（显示账号名称）、`offline_access`（保持登录）。
5. 返回操作台，点击同步。可选择清单；勾选任务后写回 Microsoft；在「已完成」里可恢复。不会自动创建、删除或完成任何任务。

采用官方 OAuth authorization code + PKCE + 一次性 state。没有账户密码、共享应用密钥或浏览器 Cookie。令牌文件权限为 0600；未接入 Keychain 加密。

参考：[任务列表 API](https://learn.microsoft.com/en-us/graph/api/todotasklist-list-tasks?view=graph-rest-1.0)、[更新任务 API](https://learn.microsoft.com/en-us/graph/api/todotask-update?view=graph-rest-1.0)、[原生应用 OAuth](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)。

## Apple Health：iPhone → iCloud Drive → 操作台

[Apple 官方说明](https://developer.apple.com/documentation/healthkit/hkhealthstore/ishealthdataavailable())：macOS 不能直接读写 HealthKit 数据。此项目提供本地文件接入，需要 iPhone 从健康数据库导出。没有后台持续采样保证，Apple Watch 静息心率是间歇测量，操作台会保留采样时间。

可用 iPhone「快捷指令」搭建个人同步：

1. 加入「查找健康样本」，类型选「心率」，按开始日期降序排列，限制 1 条；授权读取。
2. 取得这个样本的数值（次/分）和采样日期；日期输出 ISO 8601，须带时区。用「词典」生成 `heartRate`，内含 `value` 和 `at`。**不要把当前时间当作旧样本的采样时间。**
3. 可选：同样读取 HRV（SDNN，毫秒）和静息心率（次/分），分别存入 `hrv` 和 `restingHeartRate`。不要把睡眠的分类样本值直接当小时数；只有已经计算好且去除重叠的睡眠总时长才传 `sleepHours`。
4. 将词典保存成个人 iCloud Drive / Wokyis / `wokyis-health.json`，覆盖同名文件。可自行创建 Wokyis 文件夹，不依赖系统自动生成 Shortcuts 文件夹。首次开启「询问存储位置」，手动运行并检查同步；验证成功后，再固定该文件夹并关闭询问。
5. 在 Mac 的操作台里点击「待办 → 连接与提醒 → 选择同步文件夹或文件」，选择个人 Wokyis 文件夹或上述 JSON。确认 iCloud 文件已下载到 Mac。
6. 操作台每 10 秒重新读取。文件夹模式会合并其中 `wokyis-health*.json` 的有效数据，按真实采样时间保留每项指标的最新读数；因此也可分别导出 `wokyis-health-resting.json`、`wokyis-health-hrv.json` 和 `wokyis-health-sleep.json`。同名文件应开启覆盖保存。iPhone 快捷指令运行频率、Watch 采样和 iCloud 传输都会影响延迟。可按手机实际支持的个人自动化触发器安排同步。浏览器版支持「导入快照」，它不会自动刷新文件。

字段协议（下面为格式示例，并非用户数据；不应导入作为真实读数）：

```json
{
  "heartRate": {"value": 72, "at": "2026-01-01T09:30:00-05:00"},
  "hrv": {"value": 45, "at": "2026-01-01T08:00:00-05:00"},
  "restingHeartRate": {"value": 58, "at": "2026-01-01T08:00:00-05:00"},
  "sleepHours": {"value": 7.5, "at": "2026-01-01T07:00:00-05:00"}
}
```

每个字段可省略，缺失即显示“暂无数据”。文件小于 1 MB。错误/未完成同步不会被当成零值。心率超过 5 分钟会标为「等待新采样」；导入文件标为「导入快照」。恢复区域展示三个原始指标，不推断诊断或虚构恢复评分。

文件夹模式只读取直接位于所选目录的 `wokyis-health*.json`，跳过符号链接。最多 32 个文件、总计 4 MB 和 5,000 条样本；无效文件会提示并跳过，完全没有有效数据时不会替换现有连接。睡眠区间跨文件按来源合并并去重，不会因同一份文件的副本或多个设备而重复累加。

### Google Health / Fitbit → Apple Health

在 iPhone 的 Google Health 中进入连接 → 合作伙伴 App → Apple Health，允许所需的**写入**类别。当前这套接入使用心率、静息心率、睡眠；不需要为此扩大 Google Health 对 Apple Health 的读取权限。Apple Watch 的数据直接进入 Apple Health，然后由同一个个人快捷指令导出。

[Google 官方支持表](https://support.google.com/googlehealth/answer/17037331)列出了上述写入能力；HRV 目前仅列为从 Apple Health 读取，不能据此声称 Fitbit 的 HRV 已写回 Apple Health。操作台可显示 Apple Watch 写入的 HRV。

### 快捷指令原始样本格式（v2）

可从每个健康样本提取类型、数值、单位、开始/结束日期和来源，导出如下结构。所有日期使用带时区的 ISO 8601；下面仅为格式示例。

```json
{
  "schemaVersion": 2,
  "samples": [
    {"kind":"heartRate","value":"72","unit":"count/min","at":"2026-01-01T09:30:00-05:00","end":"2026-01-01T09:30:00-05:00","source":"Apple Watch"},
    {"kind":"sleep","value":"Asleep","unit":"","at":"2026-01-01T00:00:00-05:00","end":"2026-01-01T07:30:00-05:00","source":"Apple Watch"}
  ]
}
```

`kind` 支持 `heartRate`、`restingHeartRate`、`hrv`、`sleep`。每个数值指标选取时间最新的有效样本，保留其来源。睡眠按来源分别合并重叠的已入睡/核心/深度/REM 区间，忽略卧床和清醒；间隔超过 90 分钟划为另一段，展示结束时间最新的一段，标为「最近睡眠」。不同设备的睡眠不相加。无法识别的单位、阶段或日期会显示跳过提示，不会猜测数值。

手机导出的 `wokyis-health.json` 可保存在个人 iCloud Drive / Wokyis 下。「合并文本」必须明确连接样本列表，「存储文件」必须明确连接已命名的 JSON，不能留空。快捷指令首次运行、健康读取授权及个人自动化需要在 iPhone 完成；先验证实际文件再连接小屏。快捷指令定义和个人数据不放进公开仓库。工作流或 iCloud 文件同步成功并不等于设备连续测量，界面以实际采样时间为准。

需要秒级连续心率时，须另做 Apple Watch/iPhone 原生配套应用并获得健康授权；本版没有宣称文件桥接是秒级实时监测。无需为了同步而启动虚假的运动记录。
