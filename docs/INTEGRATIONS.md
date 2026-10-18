# Microsoft To Do 和 Apple Health 接入

首次未连接时，操作台显示连接入口和空值，不会生成示例任务或身体指标。
授权、数据文件只存在本机 `.local/`，不随 Git 推送。

## Microsoft To Do

1. 在 [Microsoft Entra 应用注册](https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade) 新建你自己的应用，支持「任何组织目录和个人 Microsoft 账号」。
2. 在 Authentication 添加 **Mobile and desktop applications** 平台，把 `http://127.0.0.1:8765/oauth/microsoft/callback` 设为回调地址。如果控制台不接受 HTTP IP 地址，用应用清单配置这个 loopback URI，参见 [重定向 URI 限制](https://learn.microsoft.com/en-us/entra/identity-platform/reply-url)。使用公开客户端，不创建 client secret。
3. 复制 Application (client) ID，在操作台「待办 → 连接与提醒」粘贴。也可用环境变量 `WOKYIS_MICROSOFT_CLIENT_ID` 配置。
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
4. 将词典保存成 iCloud Drive / Shortcuts / Wokyis / `health.json`，覆盖同名文件。首次手动运行并检查同步。
5. 在 Mac 的操作台里点击「待办 → 连接与提醒 → 选择同步文件」，选择上述 JSON。确认该 iCloud 文件已下载到 Mac。
6. 操作台每 10 秒重新读取文件；iPhone 快捷指令运行频率、Watch 采样和 iCloud 传输都会影响延迟。可按手机实际支持的个人自动化触发器安排同步。浏览器版支持「导入快照」，它不会自动刷新文件。

字段协议（下面为格式示例，并非用户数据；不应导入作为真实读数）：

```json
{
  "heartRate": {"value": 72, "at": "2026-01-01T09:30:00-05:00"},
  "hrv": {"value": 45, "at": "2026-01-01T08:00:00-05:00"},
  "restingHeartRate": {"value": 58, "at": "2026-01-01T08:00:00-05:00"},
  "sleepHours": {"value": 7.5, "at": "2026-01-01T07:00:00-05:00"}
}
```

每个字段可省略，缺失即显示“暂无数据”。文件小于 100 KB。错误/未完成同步不会被当成零值。心率超过 5 分钟会标为「等待新采样」；导入文件标为「导入快照」。恢复区域展示三个原始指标，不推断诊断或虚构恢复评分。

需要秒级连续心率时，须另做 Apple Watch/iPhone 原生配套应用并获得健康授权；本版没有宣称文件桥接是秒级实时监测。无需为了同步而启动虚假的运动记录。
