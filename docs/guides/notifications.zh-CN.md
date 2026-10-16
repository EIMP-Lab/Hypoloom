# 通知

[English](notifications.md) · 简体中文

## 配置投递

公开模板默认关闭 Telegram。通知会将信息发送到本机之外，需明确确认渠道、接收者和内容。
只读诊断或打开监控界面不代表授权发送通知。

在私有 Profile 的 `notify.yaml` 中填写 `sinks.telegram.bot_token` 和
`sinks.telegram.chat_id`，两个值均加引号，文件权限保持 0600。确认接收对象后，
将 `sinks.telegram.enabled` 设为 `true`；无需单独的 env 文件。准入会剥离凭据明文，
Host 从原 Profile 读取。秘密不能写进公开模板、`--set`、命令参数、对话或 Issue。
旧 `credentials_file` 和 `bot_token_env`／`chat_id_env` 仍可使用，但同一服务只能选择
一种凭据来源；填写内联值时删除旧来源字段。详见[个人配置指南](../../config/README.zh-CN.md)。

`events.telegram` 指定事件类型：`grounding_question` 提醒待回答的问题，
`grounding_answered` 确认答复已记录。操作员仍需通过 TUI、Web 或对应 Host 的
`answer` 命令回答；收到消息不等于已作答，也不会自动接受建议或恢复调度。
`provider_waiting`、`provider_retry_requested`、`provider_recovered` 分别表示模型
访问等待、操作员请求重试、已经观察到实际 API 恢复。旧 `provider_auth_cooldown`／
`provider_auth_resumed` 订阅仍映射到这些事件，但请求重试不代表已恢复。
其他模板事件包括新结果、协调活动开始、周期完成、错误与运行结束。
操作步骤见 [API 恢复](provider-recovery.zh-CN.md)。

Telegram 通知使用已准入 Profile 的 `language.yaml.report_language`：
`zh`／`zh-*` 使用中文，其余使用英文；旧配置回退到 `goal.language`。
条目之间空一行，以少量图标标识事件。末尾显示从运行创建起的累计时长，包括等待与
离线时间，终态后冻结；数据不可用时显示未知。通知不包含看板地址，请在本地取得
带认证的 Web 地址。

单次独立投递示例：

```bash
hypoloom notify --state-root /absolute/state/run --run-id RUN_ID \
  --output-root /absolute/separate-notification-output --once
```

将占位路径与运行 ID 替换为实际值。投递进度保存在明确指定的输出目录中，
该目录必须与只读运行状态目录分离。需要通知代理时填写 `notify.yaml.proxy_url`；
旧 `--proxy-env` 是替代来源，不能覆盖已绑定的 Profile 代理。
不要从其他服务的配置推断通知代理。回环测试不证明实际公网接收者已收到消息。

托管 `/autoresearch init` 不继承交互式通知环境；内联 Profile 无需额外凭据参数。
webhook 使用 `sinks.webhook.url` 和可选 `token`，接收地址须为 HTTPS，
仅回环接收端允许 HTTP。`--notification-output-root` 可指定独立的绝对输出目录。
预检只验证已开启的渠道，不发送消息、不打开看板；缺失配置会阻止启动。
旧 `--notification-credentials-file` 和环境来源仍可兼容，模型认证单独配置。
已有运行仍引用原凭据文件，不会自动迁移；请保留这些文件。详见[初始化指南](initialization.zh-CN.md)。

## 实验指标与 GOAL 判定

`new_best_result` 订阅区分两种标题：**首次有效测量**与**同组指标刷新**。
每个可比组的首条有效结果会通知，之后只有严格改善才通知；持平或变差不触发。
`SUCCESS` 和 `CLEAN_REFUTATION` 的有效指标均可参与，因此通知中的测量仍可能
未达到实验阈值。可比组由组件、指标与阈值、测量声明、冻结解析器内容、试验计划、
上游结果和评估器版本共同确定。条件变化后重新建立本组起点，不跨组件或整体目标排名。

通知展示组件、实验范围（`LOCAL`／`GOAL`／旧记录未知）、指标与比较方式、优化方向、
前次最佳、本次数值，以及是否达到实验指标阈值。这里的达标仅指实验指标；即使范围为
`GOAL`，也不表示整体目标已达成。

整体结果通过独立的 `goal_verdict_recorded` 通知表达：**整体 GOAL 已达成**、
**整体 GOAL 未达成**或**整体 GOAL 尚不能确定**，严格采用已记录的 GOAL 判定。
两份公开模板均包含该订阅，Telegram 仍默认关闭。新运行请在 init 前把两个事件都
加入私有 Profile；已有运行的订阅和运行时不会自动更新。通知包含指标名称和数值，
不发送研究正文或解析器代码。

## 每 12 小时运行简报

在 **init 前**将 `run_briefing` 加入私有 Profile 的 `events.telegram`。
两份公开模板已包含该事件，但默认关闭 Telegram。观察服务从运行创建起每满
12 小时发送一份简报，第一次不会立即发送。简报不依赖浏览器是否打开，Host 观察服务
或独立通知进程必须保持运行。离线恢复只补最新到期的一份；运行终止后停止简报，
结束通知仍为 `run_finished`。本地日志与 webhook 也可订阅该简报事件。

简报沿用投递重试与退避，成功确认后才推进进度。普通重启不会重发已确认窗口，
但远端成功与本地保存之间崩溃仍可能重复；投递采用至少一次语义。
简报不会定期调用模型，也不额外占用 Agent 名额。

文字显示**累计状态**：GOAL 判定、已评估／必要组件数量、假设图节点／关系／状态、
实验状态和 token 用量。已评估不等于目标达成。已记录、未结算观测与预算预留分开展示；
预留不是消耗，失败尝试的已知消耗已包含在已记录总量中，用量未知的计数可能重叠。
简报不发送研究正文、人工问答正文或操作备注。

可选 PNG 展示**最近 12 小时**的总量与逐专家 RPM、Grounding／其他任务活动，
以及已记录的 Agent／实验占用与时间桶内峰值。最多单列请求最多的八位专家，总量仍包含
所有专家。RPM 包含重试，使用时间桶平均值；采集缺失留空并保留已知下界，不补零。
占用来自已绑定的执行记录，不代表操作系统进程当前存活。图中标注 UTC 时间与桶宽。
文字和曲线使用相同截止时间、独立只读事务，读取期间任务可能结算。

在控制环境中安装图表依赖：

```bash
python -m pip install -e '.[monitor,notify]'
```

通知传输本身使用标准库，图表需要 `notify` 可选依赖中的 matplotlib。
中文图片还需要系统 CJK 字体，例如 Noto Sans CJK（Debian／Ubuntu 包名为 `fonts-noto-cjk`）。
绘图在临时子进程中执行，最长 15 秒，前置诊断扫描也有界。缺少依赖或字体、超时、
绘图失败时退回一条文字简报。PNG 与说明通过一次
[Telegram sendPhoto](https://core.telegram.org/bots/api#sendphoto) 请求发送；
说明超过 1024 字符时改发完整文字。远端图片投递失败沿用重试流程，不额外发送文字，
避免远端确认不明确时重复通知。

这些设置适用于新运行及其通知服务。更新工作区不会改变已有运行的订阅或绑定运行时，
不支持跨版本迁移运行来启用新功能。

## 服务器 Clash 代理

服务器无法直连 Telegram 时，在**新 init 前**配置专用通知代理。Clash／Mihomo
应在 Linux 服务器的适当账号下运行，并保持后台可用；`127.0.0.1` 指 Hypoloom
所在服务器，不是桌面客户端。通知不会继承模型或浏览器代理，也不会自动读取
`HTTP_PROXY`、`HTTPS_PROXY` 或 `ALL_PROXY`。

init 审阅会明确显示 **Telegram route: direct**（直连）或 **explicit HTTP proxy**
（显式 HTTP 代理）。Clash 已运行但仍显示 direct，说明通知服务尚未选择它；
应先填写私有 Profile 的 `proxy_url`。安装 Clash 不会修改 Profile，配置成功也不代表网络已验证。

确认 Clash 的 `port`（HTTP）或支持 HTTP 的 `mixed-port`，再检查监听。
通知传输不支持仅提供 SOCKS 的 `socks-port`。以下以 **7891** 为例，须替换为实际
HTTP 端口；这不是项目固定端口。代理 URL 不携带账号、密码、路径或查询参数。

```bash
ss -ltnp 'sport = :7891'
```

可在服务器的项目 Python 环境执行以下可选连接检查。它验证代理到 Telegram 的
HTTP CONNECT 隧道与 TLS 证书，不使用 Bot 凭据、不发送消息。
仅看到本地端口监听不足以证明连通。

```bash
python - <<'PY'
import http.client

connection = http.client.HTTPSConnection("127.0.0.1", 7891, timeout=8)
connection.set_tunnel("api.telegram.org", 443)
try:
    connection.connect()
    print("Telegram CONNECT 与 TLS 证书验证成功；未发送消息")
finally:
    connection.close()
PY
```

在已有私有 `notify.yaml` 的**顶层**添加以下字段，与 `sinks`、`events` 同级。
保留其他通知设置及 0600 文件权限：

```yaml
proxy_url: "http://127.0.0.1:7891"
```

保存后再执行新 init。预检只验证配置，不实际连接，因此仍显示投递未验证。
上面的检查只证明网络可达；下节的 `--test` 另行验证 Bot 认证与接收权限，
`--send-test` 才额外发送测试消息。

**已有运行**的通知服务在启动时缓存代理。TUI **n**、Web 的
**Retry Telegram delivery** 和 `--retry` 只解除投递退避，不重载或切换代理。
若创建运行时没有配置代理，事后新增 `proxy_url` 不会改变已准入的配置绑定。
恢复原网络路径后可自动重试；需要更换代理时，应单独安排使用原运行版本、指定代理
启动参数并保留通知输出目录的 Host 恢复操作。不要另开第二个发送进程绕过限制，
以免重复消息；保留原投递进度。

对于**未绑定 Profile 代理**的已有运行，可主动执行以下独立认证检查。环境变量
只用于这次命令，不会发送消息，也不会给正在运行的 Host 安装代理。已绑定 Profile
代理时，使用下节不带 `--proxy-env` 的检查，不能混用两种来源。

```bash
HYPOLOOM_NOTIFY_PROXY=http://127.0.0.1:7891 hypoloom notify \
  --state-root /absolute/state/run --run-id RUN_ID \
  --proxy-env HYPOLOOM_NOTIFY_PROXY --test
```

## 投递状态与恢复

**开启通知不代表已验证投递成功。** init 预检只读取凭据并检查配置，不发送消息，
明确报告 `delivery_status: unverified`。确认通知授权后，按需选择以下操作，
不必依次执行：

```bash
hypoloom notify --state-root /absolute/state/run --run-id RUN_ID --test
hypoloom notify --state-root /absolute/state/run --run-id RUN_ID --send-test
hypoloom notify --state-root /absolute/state/run --run-id RUN_ID --retry
```

`--test` 检查 Bot 认证与接收权限；`--send-test` 额外发送一条标注测试用途的消息。
两者均不重放研究事件、不改投递进度，接口接受消息也不代表用户已阅读。
修复已绑定凭据来源或网络路径后，`--retry` 请求正在运行的托管 Host 重试 Telegram，
不会重载代理。以上模式无需 `--output-root`；普通独立投递仍需独立输出目录。

Web／TUI 显示渠道状态、待投递事件数量、本次 Host 生命周期内最近成功时间、安全的
失败类别／阶段及下次重试时间。Host 不可达时显示未知，不能视为渠道健康。
TUI **n** 打开 Telegram 重试表单，Web 操作员链接提供 **Retry Telegram delivery**。
通知失败与科学验收、模型 API 恢复相互独立，不暂停研究。

Telegram 状态还显示已绑定的“直连／HTTP 代理”，不暴露端点或凭据。
“连接方式未知”表示 Host 未提供信息，包括旧版本；不能根据事后编辑的 Profile
推断当前线路。重试保留原线路和投递进度。

若失败阶段为 `connect`，错误为 `timeout` 或 `network_unreachable` 且没有 HTTP
状态码，先排查服务器网络路径与选定代理。这时未收到 Telegram API 响应，不能据此
判断 Bot token 或接收者无效。TLS 检查通过后，仍需单独验证认证与接收权限。

暂时性错误依次退避 30、60、120、240 秒，之后最多 300 秒，并遵循更长的服务端
Retry-After。认证、接收对象／权限和 TLS 证书错误等待人工修复与重试。
传输不会跟随重定向、关闭 TLS 校验或猜测环境代理。状态详情只展示固定分类、数值
状态码／errno 和安全阶段，不暴露 Bot URL、令牌、接收者或原始响应正文。

至少五条连续积压的普通 Telegram 事件会汇总为一条消息；人工问题、错误和服务状态
变化单独发送，每轮最多五条远程消息。远端确认且本地保存成功后才推进投递进度，
包括汇总覆盖的全部来源。远端确认与本地保存之间崩溃仍可能导致重复消息。
重启后依据已记录事件和投递进度重建待发项，本次生命周期的投递状态重新从未验证开始。

文献服务通知包括 `literature_waiting`、`literature_retry_requested` 和
`literature_recovered`，已有 `error` 订阅也会接收这些操作提醒。同一等待状态不会
逐次失败刷屏；原因变化、转入人工处理、请求重试和实际获取成功会产生提醒。
详见 [API 恢复](provider-recovery.zh-CN.md)。
