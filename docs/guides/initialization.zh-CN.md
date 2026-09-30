# 初始化与最终生效配置

[English](initialization.md) · 简体中文

ACI 28 的启动预检、节点级 GPU 故障恢复及上下文/进度观测见[运行恢复指南](runtime-recovery.zh-CN.md)。

`/autoresearch init` 用于审阅新运行。`bootstrap prepare` 在内存中填写并校验
Profile；`bootstrap launch` 输出启动命令及 `effective_configuration` 生效说明。
预览不启动 Host、不调用模型、不发送通知，也不修改已有 Profile 或运行。
审阅后仍通过既有的 Start now / Prepare only 选择决定是否执行。

`bootstrap inspect` 与保留的 `bootstrap paths` 都会对已配置远端分支做有时限的
[版本检查](version-check.zh-CN.md)，结果仅提醒，不阻止初始化；`--offline` 跳过远端查询。

## 聚合检查与一次启动

常规 init 使用一次简洁、只读的检查：

```bash
PYTHONDONTWRITEBYTECODE=1 HYPOLOOM_RUNTIME_FAMILY=hypoloom \
  .conda/envs/hypoloom/bin/python -B -m hypoloom.bootstrap inspect my-topic --cpu-only
```

省略 Profile 或执行模式可以先发现选项。共享 GPU 将 `--cpu-only` 换为
`--allow-gpu --managed-pool`，并为每个明确授权的 UUID 添加 `--gpu-device-uuid`。
`--nonpooled` 表示明确选择非池 GPU，仍须通过 Profile 的预算规则。
模型与认证默认值来自选中的 CC 配置，仅作为审阅建议；核对 Profile 覆盖，已选其他
模型时传入 `--claude-model`。

检查返回缺失选择、阻断原因、生效配置与阶段耗时；Profile 已准备时还返回
`launch_argv` 参数数组。使用所选的 control Python 运行检查，依赖结果才对应实际解释器。
后续复查沿用同一个 run ID、状态根和 request ID。按参数审阅数组，不用 eval 执行
JSON，也不将密钥填入参数。只有获得 Start now 确认后，才给同一启动命令添加
`--start`。bootstrap 自动生成 Host/CLI 参数，先完成审阅的池准备，再创建研究运行。
Prepare only 不创建、升级或停止池。新 Profile 仍先执行审阅过的
`bootstrap prepare --write`，随后检查实际写入的 Profile。

`prepare --summary` 与 `launch --summary` 省略原始候选文件、完整接纳 payload 和
重复命令向量；不加此选项仍可查看完整结果。本地检查通过不代表供应商在线或额度充足。
阶段耗时仅计量 helper 工作；工具审批、用户等待和模型响应分别统计，客户端未提供证据
时保持未知。反复出现 CC 自动模式分类超时，应报告工具准入故障，不能不断拼装更复杂
的等价 shell 命令重试。

简化后的技能按需读取详细兼容性和操作参考，仍复用既有验证器并保留启动前复核；
不生成持久化 init 清单或第二份配置。

`launch --start` 确认成功后，完整输出和简洁输出都会包含 `monitor.cc_command`
与 `monitor.terminal_command`。init 展示完整命令供后续复用，不自动打开 TUI。
Prepare only、启动失败或创建结果未确认时，不能宣称启动成功，也不会返回这些
启动后入口。重开 CC 后可使用 `/autoresearch-monitor` 从已有状态库选择运行；
详见[监控教程](../../USAGE.zh-CN.md#tuiweb-与人工决策)。

## 预算集中配置

保留现有六份 Profile 文件。新模板将费用、时限和单次调用限制统一放在
`methodology.yaml.budget`，与重试、并发限制放在一起：

```yaml
budget:
  total_budget_usd: 45
  per_expert_budget_usd: 15
  wallclock_hours: 6
  experiment_timeout: 1800  # 秒
  agent_invocation:
    max_turns: 40
    max_tokens: 80000
    max_output_bytes: 262144
    wall_seconds: 600
    max_cost: "1"
    token_enforcement_mode: enforce
```

这是配置片段，不是完整 Profile。`goal.yaml` 保留科研目标、验收和轮数限制，
`safety.yaml` 保留权限。准入后预算及其既有执行机制继续生效。按专家配置的
预算可以覆盖共享的专家默认值，但不取代总运行费用上限。

旧 `safety.total_budget_usd`、`safety.per_expert_budget_usd`、
`safety.experiment_timeout_minutes`、`goal.stop_conditions.max_wall_clock_hours`
在对应 budget 字段缺省时仍兼容，并提示迁移。同一项限制在两处声明会报错，
即使数值相同也不会静默选一个。整理待启动 Profile 时，将旧值移入 budget，
删除旧声明；实验超时需从分钟乘以 60 转为秒。init 不自动改文件或迁移已有运行。

## Agent 实际使用的模型

预览复用运行时解析函数：阶段覆盖优先于专家配置，专家优先于 Profile defaults，
最后才使用 Host 默认模型。共享 Grounding、Observer 按各自的既有规则解析。
`effective_configuration.models.routes` 展示各可能阶段的模型、effort 及来源，
包含条件性角色；这不代表每个角色都会执行。`profile_models_overriding_host_default`
明确列出覆盖 Host 默认值的模型。effort 缺省时采用原生 CLI 默认值。

中英文模板均省略 `experts.yaml` 中的 `defaults.model`。没有显式模型覆盖时，
所有 Agent 阶段使用初始化时为 Host 选择的模型。若希望使用交互会话的模型，
准备启动命令时通过 `--claude-model` 传入已确认的模型标识，并核对生效分配。
仅需覆盖 Host 选择时才在 Profile 中添加模型字段。

已有本地 Profile 不会自动修改。使用旧 Profile 创建新运行前，若希望跟随 Host
选择，应删除 `defaults.model`，并检查专家、阶段及共享 Grounding 的模型覆盖。
应省略该字段，不要改成空字符串或 `null`。已有运行保留已接纳的配置。

交互式 Claude 会话的模型选择独立于这些配置；settings 文件的默认值也不能证明
该会话当前的 `/model` 选择。新 Profile 如果希望统一使用一个模型，应一起审阅
defaults 和所有显式覆盖；需要多模型时保留有意的覆盖，并核对最终分配。实际
Agent 会固定解析出的模型，不自动继承交互式会话的模型或 effort。

## 启动前本地检查

API 地址、认证格式和 relay 支持的接入模式复用实际传输层的校验器。预览不发起
付费探测；配置通过不等于模型在线、额度充足或网络可达。

GPU 启动要求 Host 运行环境与 Profile 一致；授权 UUID 必须出现在只读设备清单中，
Profile 的 GPU 选择须位于该集合内。`safety.gpus: []` 与 GPU 启动冲突时直接报错。
共享池预算复用 Host 的既有检查。预览不分配 GPU，也不把空闲显存视为授权。
配置合法但设备暂时忙碌仍属于运行时等待，不自动换卡或降低显存要求。

## 托管通知

直接在私有 Profile 的 `notify.yaml` 填写，无需单独维护 env：

```yaml
sinks:
  telegram:
    enabled: false  # 确认接收对象后开启
    bot_token: ""   # 本地填写，两个值都须加引号
    chat_id: ""
```

可选 webhook 使用 `sinks.webhook.url`／`token`，通知代理使用 `proxy_url`。
含凭据文件保持 0600，并放在 Agent/实验的数据和运行环境挂载目录之外。准入剥离
明文，只派生原 YAML 的文件引用；Host 复用既有私有读取器，不生成第二份密钥文件。
文献密钥填写在 `methodology.yaml`，完整字段及步骤见[个人配置教程](../../config/README.md)。
模型认证仍使用选定的 CC 配置。先准备 Profile，本地编辑后重新执行 init；真实值
不能放进 `--set`、命令行、对话、公开模板或 Git。

托管 Host 不继承交互式终端的通知秘密。旧文件/环境方式仍兼容，包括可选的
`--notification-credentials-file`，内联 Profile 不需要这些参数。同一服务不能混用
来源。`--notification-output-root` 仍可指定独立的绝对输出目录。直接 serve/notify
保留显式环境兼容；已有运行及其凭据源不会自动迁移。

启动前复用实际通知代码检查已启用通道的凭据、目标格式、代理、输出目录隔离、
日志路径和看板 loopback 地址，不发送消息或绑定监听端口。缺失配置会阻止启动。
固定端口已被占用仍可能在运行时报告错误；无固定端口要求时使用 `port: 0`。
详见[通知指南](notifications.md)。

生效说明只是从现有输入计算的结果，不保存为额外启动清单。六份 Profile、普通
Host 参数及既有读取器继续作为配置来源；运行时保护不会因预检而移除。


通知预检只验证本地配置。Telegram 已开启仍应显示为**投递未验证**，实际接口
成功响应后才确认可用。显式连接/消息测试及 Web/TUI 状态说明见
[通知指南](notifications.md#delivery-health-and-recovery--投递状态与恢复)。
