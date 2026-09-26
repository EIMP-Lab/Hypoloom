# 初始化与最终生效配置

[English](initialization.md) · 简体中文

`/autoresearch init` 用于审阅新运行。`bootstrap prepare` 在内存中填写并校验
Profile；`bootstrap launch` 输出启动命令及 `effective_configuration` 生效说明。
预览不启动 Host、不调用模型、不发送通知，也不修改已有 Profile 或运行。
审阅后仍通过既有的 Start now / Prepare only 选择决定是否执行。

最初的 `bootstrap paths` 同时对已配置远端分支做有时限的
[版本检查](version-check.zh-CN.md)，结果仅提醒，不阻止初始化；`--offline` 跳过远端查询。

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
