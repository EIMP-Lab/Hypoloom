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
认证来源和候选模型来自选中的 CC 配置。未显式传入 `--claude-model` 时，固定问题
会显示当前模型，允许沿用或自由输入当前供应商支持的准确模型 ID；不会自动替用户
选择。已显式指定则不再询问。此选择只影响新运行，不修改 CC settings；最后仍需
核对 Profile 的角色覆盖。init 也会单独展示文献默认路由及域名覆盖，与 Telegram
和机构浏览器的网络配置分开说明。

检查返回缺失选择、阻断原因、生效配置与阶段耗时；Profile 已准备时还返回
`launch_argv` 参数数组。使用所选的 control Python 运行检查，依赖结果才对应实际解释器。
后续复查沿用同一个 run ID、状态根和 request ID。按参数审阅数组，不用 eval 执行
JSON，也不将密钥填入参数。只有获得 Start now 确认后，才给同一启动命令添加
`--start`。bootstrap 自动生成 Host/CLI 参数，先完成审阅的池准备，再创建研究运行。
Prepare only 不创建、升级或停止池。新 Profile 仍先执行审阅过的
`bootstrap prepare --write`，随后检查实际写入的 Profile。

选卡之后，若兼容共享池的显存下限与本次建议不同，init 会询问“保留当前池”或
“审阅重配置”。默认复用；配置相同不增加问答。重配置的默认成员是完整现有池，
不是本次选卡子集。最终审阅列出全池影响及下限前后值，保留池数据库和请求历史。

缺失运行状态的池登记会列出 UID、namespace、run ID、释放数、未释放数及进程状态。
仅本账户、无本地运行记录、历史请求全部释放且无关联存活进程时，才提供退役确认。
用户还须确认运行已退役并删除、未迁移到其他位置且不会恢复；框架不能仅凭零占用
推断这些事实。确认通过本次 `--pool-retired-owner namespace/run_id` 参数传递，
维护执行前仍复核，不产生永久豁免、不删除历史。下次另行维护时仍需审阅缺失状态；
普通兼容复用不重复询问。保留运行、其他用户、不可读状态及未释放请求不会因此放行。
“暂不确认”保留 `none` 并阻止启动；决定重新考虑时移除该参数后复查，无须重选其他项。

对于 Host 和进程已退出、运行状态却未结束的保留运行，若原冻结 Host 支持取消恢复，
且池请求全部释放，init 会提供“审阅结束所列运行”。准确的
`--pool-cancel-run RUN_ID:REVISION` 选择纳入最终审阅，仅在 Start now 后执行：先持久化
CANCEL，再恢复遗留 Attempt，完成正常收尾及原 API 预算内必要的最终报告，然后复查
运行、Attempt 与进程所有权，恢复共享池。`dependency_cleanup_pending` 表示收尾尚未
完成，应保留本次选择继续检查，不能再次启动 Host 或静默改为非池模式。历史版本缺少
原启动信息或收尾能力、仍有存活进程、审阅版本已改变等情况会明确阻塞，不升级旧运行，
不通过修改数据库伪造完成。

GPU 审阅同时显示整个运行的 `max_active_processes` 和授权设备数，CPU 实验也占用该
上限。授权 4 张 GPU 而并发为 1 时，共享池仍不能让实验并行；对于独立单卡实验，
可审阅将并发调至 4。init 不自动改写已有 Profile，实际启动仍受显存和多卡需求约束。

`pool_configuration_unknown` 表示托管池服务的配置读取失败，`detail` 标明具体阶段；
它不能证明存在活动依赖，也不能说明外来 GPU 进程不可读。研究 Shell 隔离可能阻止
读取池服务的 `/proc/<PID>/cwd`，此时检查改读该 unit 的 systemd 工作目录，仍校验
实际进程 UID 和启动参数。使用返回的 `interaction.inspect_argv` 复查，保留所选
reserve、全池成员及运行身份；只有决定修改选择时才重新询问池配置。
现有问答 hook 会清理菜单中的异常回车字符；已经显示的菜单需等下一次提问才刷新。

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

机构访问是 init 的明确选择项。未提供选择时，检查返回
`missing: ["institutional_browser_mode", …]` 及 `choices.institutional_browser_mode`；
对话据此询问，未回答前不提供启动参数。已经明确选择时沿用，不重复询问：

| 选择 | 参数值 | 行为 |
| --- | --- | --- |
| 不使用机构浏览器 | `off` | 保留公开检索与 OA 回退，关闭机构面板操作。 |
| Linux 本机桌面 | `local` | 在项目所在 Linux 桌面打开专用浏览器，无需 SSH 隧道。 |
| Windows 本地电脑 | `client` | 本机 Edge/Chrome 通过连接脚本提供运行级 SSH 转发。 |
| 服务器容器 | `server` | 使用已安装镜像及本次运行的私有 GUI 转发。 |
| 已有外部连接 | `existing` | 使用明确提供的外部 CDP 端点。 |

回答转换为同一个 `--institutional-browser-mode` 参数，例如不使用时传
`--institutional-browser-mode off`；不传不等于已经选择 off。旧的仅 URL 参数视为
已明确选择外部连接，保留原非受管行为。直接 launch/serve 的旧脚本仍可省略模式。
off 与机构连接或机构专用配置同时提供会被拒绝，请在更改选择时移除冲突参数。

启用后，**必须在交互式会话中确认出版社候选名单**。inspect 将
`institutional_prepare_provider` 列为缺失项；程序固定展示三组平台，每组最多三项
加“本组不选”。可多选，全部八个平台也可一次选择；不再由模型自由分组或拆批。
ACM 虽推荐优先公开获取，仍必须作为可选项展示；只选择 Windows client 不代表
已经选择出版社，未回答前程序不生成准备或启动命令。已明确回答的名单随重查保留，
不重复询问。重复传 `--institutional-prepare-provider <平台>` 携带准确名单，
`none` 跳过；off 不额外询问出版社。独立准备和直接启动脚本保留原有默认值。
`Elsevier` 可作为 `ScienceDirect` 的别名。学校交互入口和浏览器位置只配置一次。
未选中的机构渠道保持禁用，公开／OA 检索获取仍可使用。Windows init 的程序会自动将
“仅公开途径”选择合并到启动审阅；独立准备命令的结果仍需带入后续启动参数。
未验证或无权限的平台不阻止开始研究。

检查与启动预览共用原校验器，预览不连接浏览器或分配端口。Start now 后为运行
分配资源；机构通道不可用时沿用有限获取和公开／OA 回退。init 使用后台
`--agent-session`，程序检查连接、打开平台并按当前阶段校验固定回复，使用生成的
`watch_argv` 观察，不由助手编写轮询或操作后台 stdin。

Windows 客户端模式下，**每次 init** 在本次所选电脑执行一次准确的 Setup／Connect
入口。首次或升级通过 SFTP/SCP 下载原始脚本，不执行助手转贴的安装源码。入口携带
本次 `-StateRoot`、`-RunId`、`-RequestId` 及可选 `-PreparationRoot`，启动一次性隐藏
任务，不登录自启或发现未来运行。Setup／Connect 返回后可关终端；只有前台 Run
诊断需要保留终端。随后回到原 CC init。

Windows 客户端准备使用 inspect 生成的 `init_session_argv`：CC 启动一个后台程序，
转述固定指令。最后一个出版社结束后，程序自动合并策略、检查原 Profile、生成完整
启动审阅（包括通知事件列表和私有凭据来源路径，不显示令牌或接收人内容、不发测试
消息）。CC 直接询问 **Start now / Prepare only**，无需再次 inspect、编写读取脚本
或手动整理配置。选择经原有回复通道提交；只有有效的 Start now 才调用现有启动流程，
结果与监控命令来自 `bootstrap_result`，不得再单独执行一次 `launch --start`。
重试保留已完成出版社；审阅阻塞或过期不会启动运行。接受 Start now 后，连接保留至
有界启动步骤完成并交接给 Host；连续失联 300 秒的退出规则不变。

`status.state: awaiting_launch` 表示准备已完成，应展示程序审阅并立即询问固定选择，
无需等待后台进程退出。同一控制目录／助手最多等待 600 秒交接给该运行；失败平台
按返回的 `remaining_preparation_argv` 在原目录重试，保留成功结果。Prepare only／
取消执行 `finish_argv`；Start now 仍需明确授权，到期不自动启动研究。准备阶段主动
结束／跳过则直接退出，从后台输出读取结果。最终 `preparation_lifecycle` 事件分别
记录交接、审阅到期或主动结束；出版社样本摘要不证明助手仍存活。Windows 助手退出时，
先用[本地 Status 检查](institutional-access.zh-CN.md#手动连接诊断)读取安全原因；
不需 SSH、重启浏览器或前台重现，300 秒控制检查过期也不等于出版社页面故障。

运行终结或进入 `STOPPING` 后，助手关闭自有 SSH／浏览器，保留登录目录。短断线
只重连同一运行，连续失联 300 秒后本地退出；睡眠唤醒先核验期限。关闭 CC／交互式
SSH 不会停止已启动科研；Windows 缺席也不阻断公开回退和其他工作。新 init 需新入口。
升级、旧任务清理、范围值和 Windows 真人验收边界见
[机构全文指南](institutional-access.zh-CN.md#init-自动准备连接)。服务器不能凭空启动
未连接的电脑，学校 SSO/MFA 仍由真人完成。

首次接入或更换 Windows 机器／账户时，先按
[SSH 接入教程](institutional-access.zh-CN.md#首次接入或更换-windows-机器账户)
验证 `BatchMode=yes` 登录返回 `0`；密码登录成功不等于后台助手已可用。

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

最终 review 同时列出各模型的上下文窗口。adapter-80 的新 Agent 导入所选
settings 中非敏感的 CC 窗口/压缩参数；Profile 未显式限制工作上下文时自动
跟随解析窗口。本地信息不足时可进行有界、只读模型元数据查询，review 单列
查询次数，不发送生成探测。元数据缺失时回退并标注 CC 推定值。
参数、来源及 `memory.context_metadata: false` 关闭方式见
[上下文管理](context-management.zh-CN.md)。

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
若服务器通过 Clash 投递 Telegram，在**新 init 之前**于 `notify.yaml` 顶层填写
`proxy_url: "http://127.0.0.1:7891"`，端口替换为实际支持 HTTP CONNECT 的端口
（支持 HTTP 的 `mixed-port` 也可）。这里的本机是 Linux 服务器，不是 Windows。
通知代理独立于模型及文献代理；已有运行和 **Retry Telegram delivery** 不会热加载
新增或修改的代理。详见[服务器 Clash 代理](notifications.zh-CN.md#服务器-clash-代理)。

含凭据文件保持 0600，并放在 Agent/实验的数据和运行环境挂载目录之外。准入剥离
明文，只派生原 YAML 的文件引用；Host 复用既有私有读取器，不生成第二份密钥文件。
文献密钥填写在 `methodology.yaml`，完整字段及步骤见[个人配置教程](../../config/README.zh-CN.md)。
模型认证仍使用选定的 CC 配置。先准备 Profile，本地编辑后重新执行 init；真实值
不能放进 `--set`、命令行、对话、公开模板或 Git。

托管 Host 不继承交互式终端的通知秘密。旧文件/环境方式仍兼容，包括可选的
`--notification-credentials-file`，内联 Profile 不需要这些参数。同一服务不能混用
来源。`--notification-output-root` 仍可指定独立的绝对输出目录。直接 serve/notify
保留显式环境兼容；已有运行及其凭据源不会自动迁移。

启动前复用实际通知代码检查已启用通道的凭据、目标格式、代理、输出目录隔离、
日志路径和看板 loopback 地址，不发送消息或绑定监听端口。缺失配置会阻止启动。
固定端口已被占用仍可能在运行时报告错误；无固定端口要求时使用 `port: 0`。
详见[通知指南](notifications.zh-CN.md)。

生效说明只是从现有输入计算的结果，不保存为额外启动清单。六份 Profile、普通
Host 参数及既有读取器继续作为配置来源；运行时保护不会因预检而移除。


通知预检只验证本地配置。Telegram 已开启仍应显示为**投递未验证**，实际接口
成功响应后才确认可用。显式连接/消息测试及 Web/TUI 状态说明见
[通知指南](notifications.zh-CN.md#投递状态与恢复)。

init 的初始问卷由 inspect 的 `interaction.questions` 固定生成；中文会话传入
`--ui-language zh-CN`。CC 原样展示，将 AskUserQuestion 返回的原始 `answers`
对象写入 `.local/` JSON 文件，再执行生成的 `answer_argv`。程序保留已选项和运行
标识，转换成既有 CLI 参数并校验。缺失、改名、过期或矛盾答案不能推进初始化；
问卷之外需补齐的配置列于 `configuration_required`。这不代替最后的启动确认。

初始选择通过 Claude Code 内置的 `AskUserQuestion` 显示，此时尚不需要启动 Host
或机构准备会话。`ToolSearch` 只搜索延迟加载工具，搜索无结果不能证明该内置工具
不可用。若 CC 因此把问卷写成普通文字，可让它直接调用 `AskUserQuestion`，传入
已有的 `interaction.questions`，沿用本次检查与选择。若实际调用报错，应报告该
错误；这不授权代填答案或启动研究。

平台固定分为三组，每组最多三项加“本组不选”。一次可以选择全部八个平台，
无需让模型拆批；程序顺序处理并按需回收已结束的自有标签，同时保留八个标签
上限，不关闭用户原有标签，不清除登录 cookie。

研究会话不能修改 Hypoloom 框架源码。现有项目 Hook 将 Bash/Monitor 包在 Linux
bubblewrap 中，整个仓库只读，仅 `.local/`、`profiles/local/`、`state/` 可写；
Write/Edit/NotebookEdit 仍禁止直接编辑 state。该约束不自动批准命令，也不替代
Host 的运行权限。隔离缺失或不可用时明确失败，不退回无隔离执行。框架修复使用
独立获授权的开发会话。更新 Hook 注册后重启 CC；已停止及历史运行保持不变。
