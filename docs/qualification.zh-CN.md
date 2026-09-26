# 资格:G0–G5(Qualification)

[English](qualification.md) · 简体中文

本文是资格框架与当前状态的中文导读。完整的逐条证据台账(候选、环境、命令、
结果/跳过、动作计数、资源/成本上限、观察到的清理与遗留缺口)以持续更新的
[英文版](qualification.md) 为准;中文摘要可能滞后,任何冲突以英文台账为准。

2026-09-26 汇总提交：用户授权将上下文/恢复/观测、init 一致性、Profile 凭据与
版本提醒一并提交推送。完整离线 G1 为 6153 通过、2 失败、26 按需跳过；一项是
测试临时目录超出 Unix socket 路径限制，另一项旧模板断言仍期待空白凭据进入
运行配置。采用短目录，并修正该测试的凭据剥离预期、保留其余配置精确比较后，
79 项复验全部通过；生产代码未变。6155 个非按需用例均有通过证据，不把原始
失败表述为一次全量全绿。15 个测试运行独立核对 380 次 Agent／32 次 CPU，
Oracle 均通过且无残留。打包、140 个运行时文件对比和隔离安装 CLI 通过。
证据及原始失败位于 `.local/qualification/dev-submit-20260926/`，没有修改已有
科研运行或追加付费/GPU/公开通知测试，不宣称正式 G0–G5 发布资格。
详见[英文记录](qualification.md#current-development--integrated-dev-submission-2026-09-26)。

2026-09-26 版本提醒：init 的路径检查及 `hypoloom version-check` 根据实际远端
引用比较 HEAD，默认跟踪 upstream，支持指定目标及离线模式。Git 保持唯一事实源，
不 fetch/pull，不添加版本缓存、更新服务或启动门槛；不完整历史保留未知判断。
默认总限时 5 秒；超时及 Ctrl+C 都清理所属 Git/辅助进程组。190 项聚焦检查与
7 项文档/模板检查通过，保留最初中断残留的复现失败。真实 CLI/init 用时约
4.25/4.21 秒，确认 HEAD 与当时 origin/dev 一致，同时显示未提交改动；Git 元数据和
源码未变，离线检查通过。已有运行未改，未进行全量 G1、付费/GPU、提交或推送。
详见[英文记录](qualification.md#current-development--version-reminders-2026-09-26)及
[使用指南](guides/version-check.zh-CN.md)。

2026-09-26 Profile 内联凭据：文献密钥、Telegram token 直接填写私有
`methodology.yaml`／`notify.yaml`，无需独立 env；可选 webhook／代理同样支持。
准入剥离明文，派生原文件引用，Host 复用既有 0600/no-symlink 读取器；旧来源
保留兼容，冲突拒绝，没有第二份秘密文件或新配置权威。模型认证保留现有 CC 配置。
455 项定向回归与 7 项文档/模板检查通过；两条实际 CLI 路径独立核对 11 次 Agent、
2 个 CPU Process 全部关闭，实验创建/结果/发布各 1 次，两份 Oracle 通过，无残留。
内联通知用例覆盖鉴权、503 重试、最终投递，并检查数据库、Agent 文件和两种 Issue
导出均不含测试 token。中英文教程更新，已有 Profile 和运行未迁移，未进行真实远端
通知、付费/GPU 或全量 G1 复跑；未提交推送。
详见[英文记录](qualification.md#current-development--profile-local-credentials-2026-09-26)。

2026-09-26 init 配置一致性修复：费用及时限集中到 `methodology.budget`，旧位置
单独声明仍兼容并警告，重复声明直接拒绝。实际模型／effort 生效说明复用运行时
解析器；GPU 组合、API 地址／接入方式、通知凭据及看板配置在启动前复用既有
校验。托管通知可显式选择已有 0600 dotenv 文件，不依赖交互式环境，也不改变
模型认证。346 项聚焦检查通过；全量 G1 为 6102 通过、7 失败、26 按需跳过。
7 项均为旧预算夹具与“无警告”断言冲突；仅切换这些无关测试的夹具为新格式，
保留原断言及旧格式专用覆盖，相关 128 项复验全部通过，逐项包含初始失败。
这是全量覆盖加定向复验，不宣称一次全量全绿；期间生产代码未变。文档及模板
7 项通过。独立启动审计关闭 2 次 Agent／2 个 CPU Process，实验创建、结果、发布
各 1 次，Oracle 通过，无重复、待处理效果或进程／socket 残留。两种实际 CLI
通知凭据方式的鉴权、503 重试、最终投递及退出清理通过。证据及原始失败位于
`.local/qualification/init-consistency-20260926/`。已有本地 Profile 只读准入通过，
未改运行、设置或科研文件；没有付费／GPU 资格验证、公开通知或提交推送。
详见[英文记录](qualification.md#current-development--init-configuration-consistency-2026-09-26)。

2026-09-26 观测与成本补齐：原生压缩事件、成功原生 Read 的冻结输入精确重复范围、
材料字节量与首次故障／后续恢复费用均从既有记录派生，未知值不记为零；Web 同时
展示实测输入和本地字节估算。最低内联量下，完整首请求本地拒绝且没有生成请求
转发时结束原样重试；已有工具进展的容量恢复仍保留。176 项聚焦／浏览器测试与
12 项原生 CPU／观测复验通过。最终 G1：6092 项通过、26 项按需跳过；10 个
关键原生场景独立闭合 318 次 Agent／26 次 CPU，实验创建／结果接受／发布各
26 次，无重复、待处理效果、OPEN 或进程／socket 残留。用户授权使用现有 CC
配置，含保留的禁重试对照共 5 个真实分析样本、分配 30 USD，单调用
2 USD／32 轮／300 秒。正常、小内联和正常重建三组取得正确分析，小内联重建
一组超时，五个运行均干净闭合。CC 估算合计 1.467626 USD，另一次结算未知，
2 USD 为预留而非实付；CLI 均标记未知模型计价，不能视为供应商实际账单。
小内联没有显示稳定收益，保留默认额度。未知计价投影和 Attempt 序号费用归类
的聚焦／浏览器检查 104 项通过。详见
[证据与限制](qualification.md#current-development--context-observations-and-measured-recovery-costs-2026-09-26)。
此前“D1–D6 已完成”指核心闭环，未包含本次补齐的观测和成本，也未证明科研质量。

2026-09-26 上下文与恢复：新 ACI-24 普通调用重建短会话，保留科研身份、Git 工作区、
冻结输入、实验所有权与累计费用。容量问题按节点有界恢复，API 额度问题沿用共享
等待；技术分析失败不能覆盖合法修订。已有来源验证器进入原生纠正反馈，测量诊断
明确原值与基线，教程同步更新。真实 CLI／本地 provider／CPU 的提交后容量故障与
丢回复恢复通过；两份 GLM/MiMo 历史运行的当前／保留版本只读审计通过且数据库未变。
完整 G1：6078 项通过、26 项按需跳过；9 个原生流程独立核对 311 次 Agent 调用、
26 次 CPU 执行全部闭合，实验创建／结果接受／发布各 26 次，无重复、待处理效果、
存活进程组或 socket。旧／新管理器与停止优先级等 44 项聚焦回归通过。结果、
原始失败证据与限制见[完整证据](qualification.md#current-development--bounded-context-and-recovery-2026-09-26)。
未迁移旧运行，未进行付费模型或 GPU 资格测试，不宣称科研质量或完整发布资格。

2026-09-25 Leader 经验共享：新 ACI-23 运行让被选专家通过简短辅助任务提供有来源的
建议，同行继续独立研究，不再自动暂停、独占额外周期、优先调度或改变父假设授权。
首个 FORWARD 请求决定本轮建议，后续两翼、综合和重试复用其实际输入；接收者可
采纳、改造或拒绝。重启沿用既有任务和接受记录，失败只结束本次建议；运行停止时
沿用清理事件中止协调，不清除手动暂停，也不发布迟到建议。Web 展示实际输入接收
记录，不将其冒充采纳。中英文指南同步更新。最终 122 项闭环检查、6 项浏览器检查
通过；两个原生 CLI／本地测试 API 路径共关闭 173 个 Attempt、完成 12 次 CPU 实验，
独立审计通过，无残留进程组或 socket。报告合并遗漏已派发 Observer 的问题已复现并
修复，失败证据单独保留。完整验证见[完整证据与限制](qualification.md#current-development--nonblocking-leader-experience-sharing-2026-09-25)。
旧运行及其固定版本保持不变，无付费／GPU 调用，不构成完整发布资格晋级。

2026-09-23 假设图修复：仅父子关系参与谱系布局，剪枝以可开关的虚线叠加；节点
剪枝边框与科学评估独立显示，图例按当前可见的关系、状态、控制标记和专家分组。
移除误判的关键词分类，详情保留修订原文、父子主张和剪枝来源。只读回放 r213 的
11 个节点、7 条派生及 2 条剪枝关系全部保留，4 个根节点回到起始列，图例由 15 项
减至 9 项。80 项 Web 相关检查通过；视觉复查后的连线绕行经最终 6 项图谱回归
验证，剪枝线不穿过节点内部，另有 4 项文档检查通过。既有运行、Profile 和固定
运行包保持不变。
后续消除剪枝“双头箭头”歧义：剪枝改从节点侧边下部引出、上部进入，避开父子
连线的中部箭头。同列和跨列均适用；新增端点检查先复现失败，修复后 3 项浏览器
回归通过，原数据回放确认每条剪枝线仅一个目标箭头且起点无箭头重叠。
剪枝按钮进一步改为 **Hide pruning links (N)** / **Show pruning links (N)**，明确
下一步操作；提示说明 N 为连线数量，节点及其状态始终保留。既有剪枝显示浏览器
用例通过，覆盖切换、轮询、筛选及移动端宽度；45991 观察页刷新后可见。
详见[验证记录](qualification.md#current-development--lineage-relationships-and-contextual-legend-2026-09-23)。

2026-09-23 Leader 图标修复：专家卡片读取已有 `leader.active`，普通研究节点和节点间
等待均保留活动任期皇冠，结束／失败／中止后撤下，历史记录保留。4 项真实浏览器
回归与 46 项 Web／观察投影／支持包检查通过。工作区观察页刷新后使用新脚本；
固定运行包内的旧页面保持不变，研究运行未修改或重启。
后续谱系图样式修复保留悬停、选中和关联高亮节点的专家边框颜色，以粗细区分状态；
移除蓝色光晕，键盘焦点轮廓沿用专家颜色。3 项现有图谱浏览器回归通过。
详见[验证记录](qualification.md#current-development--leader-status-icon-2026-09-23)。

2026-09-23 Observer 修复：事实 ID 与该事实自身的实体 ID 按相同含义参与资格校验，
响应和事件保留原始引用，审计继续独立验证贡献、归属、周期和预算。原生 Stop 复用
既有事后语义校验器，可在同一调用内修正无效选择。筛选说明沿用现有 LOCAL/C5 与
GOAL 契约，不放松完整目标验收，不增加用户特例。656 项定向检查和真实 Claude Code
同次调用纠错测试通过；六份原始输出只读回放中五份通过，一份仍因两个本方父假设
而正确拒绝。既有运行和本地 Profile 保持不变，未执行付费/GPU 或完整发布资格验证。
固定源码后的两条真实 CPU Leader／Debate／恢复闭环也通过：192 个 Attempt 全部成功
关闭、12 个科学结果、完整 Oracle 均通过，无残留进程组或 socket。首次源码漂移拒绝
和暴露批次审计遗漏的失败记录均保留，未修改保护逻辑或放宽断言。
详见[证据与限制](qualification.md#current-development--observer-references-and-native-feedback-2026-09-23)。

2026-09-23 通用诊断与准入修复：Oracle 按精确十进制值比较共同阈值，并正确解释
有记录证明的启动前完整性拒绝；支持包按失败时间选取，保留直接关联证据，区分源记录
完整性与导出保真度，继续验证脱敏和公开/私有范围。目标 Git 树检查复用执行验证器并
前移至 Profile 准入。报告补充状态、revision、环境说明和可选问题描述。
最终 380 项定向测试及两项原生 CLI/CPU/恢复测试通过；独立核对 35 个 Attempt 全部关闭，
两个 Oracle 通过，无残留进程组或 socket。未修改既有研究运行，未执行付费/GPU 或完整
发布资格验证。详见[英文证据与限制](qualification.md#current-development--support-evidence-oracle-and-target-admission-2026-09-23)。

2026-09-22 初始化与配置入口修复：新增 `hypoloom.bootstrap`，在内存中填写模板并
复用既有准入校验器，明确 `--write` 才创建新 Profile，`--start` 才执行已授权的托管
启动。参数由同一来源生成，启动前后复查实际 Profile；创建回复不明时先查询状态，
避免重复创建和误停。dotenv 读取器强制检查 0600，空白模板移至
`config/hypoloom.env.example`，首页与初始化展示真实配置位置。提交前复查补修异常
凭据配置段的报错，以及子命令未采用所选仓库工作目录的问题；最终合并回归与公开
v4 三专家 CPU 闭环共 379 项通过，源码包包含新入口。MERC 本地 Profile 准入通过且
未修改。未进行付费、GPU、实际 user-systemd 或跨模型验证；用户已明确授权复查后
提交并推送 dev，此授权不包含启动或修改研究运行。
详见[完整证据与限制](qualification.md#current-development--deterministic-init-and-configuration-entry-2026-09-22)。

2026-09-22 执行与归档修复：新运行采用 ACI 22，可将长实验交还 Host 等待，终态
收集后继续同一节点／会话；调用照常计费，每个实验只允许交还一次，短实验仍可
有界等待后直接完成。修复合法目录删除被归档检查误拒的问题，归档失败保留可用
证据和费用信息；修正 GPU 保留显存归因，未知归属仍按原保护处理。Leader／Debate
skill 按实际评测协议比较结果，既有 Profile 参数保持不变。针对性回归及真实
Claude Code／本地 provider／CPU 修复闭环已通过（最终选择 134 项通过，另对同一
原生用例加强并通过时序验证）；原始失败样本和验证限制保留在
[英文证据记录](qualification.md#current-development--execution-handoff-and-session-repair-2026-09-22)。
本轮没有付费模型或真实 GPU 资格验证，没有修改既有研究运行。用户随后告知已
终止既有运行，并授权将本次修复提交、推送至 dev；验证范围不因此扩大。

2026-09-22 Web 端口修复：TUI、独立 Web、Host dashboard 默认使用 `0`，由系统
直接分配空闲 loopback 端口；Profile-v4 接受 `0`，显式固定端口保留原端口冲突行为。
通知从所属看板读取实际端口，无法获知动态地址时引导查看本地连接信息，不发送
bearer 令牌。MERC 本地 Profile 和中英文模板同步更新，历史运行未修改。
273 项相关回归通过，覆盖并发实例、鉴权隔离、独立退出清理、CLI/TUI 和通知地址；
没有真实远端通知、付费/GPU 实验或新候选完整资格。
详见[实现、验证与范围](qualification.md#current-development--dynamic-web-ports-2026-09-22)。

2026-09-21 开发修复：新运行使用 ACI-21，允许依据已验证中间贡献任命 Leader，
并通过已接受交互的确切父假设执行定向续研。Debate 两方分别等待本方周期收尾，
不再因普通裁决的 best_chain 为空丢失续研。历史查询、费用缓存和后台视图准备
减少控制线程工作；终止窗口保留拒绝原因，TUI 异常写入既有诊断日志；最终报告
缺失引用可在原生 Stop 校验中反馈修复。真实 CPU 场景已覆盖有效负结果与空选择。
原闪退原因和并发负载下的响应百分位仍未确认，当前候选尚未冻结或完成全部资格。
旧 MERC 运行已按用户指示使用原版本关闭，本次没有迁移、恢复或启动付费/GPU 研究。
详见[本轮实现、证据与限制](qualification.md#current-development--responsive-control-and-evidence-directed-research-2026-09-21)。

同日后续资格：用户已确认 glm-5.3-flash、本轮 13 USD（CPU 7 / GPU 6），
原定 G4 使用 GPU 2、3，G5 使用 GPU 3，容量不足等待；用户随后指出 0、1 空闲，
已停止尚未启动实验的旧等待任务，改为 G4 使用 GPU 0、1，G5 使用 GPU 1。
模型、预算和容量要求保持不变。安装包、10 个历史运行的无变更
核验，以及原生 ACI-21 Leader / Debate / 6 次 CPU 实验闭环已通过。
G1 当前覆盖 5,867 项通过、26 项显式跳过；原始失败保留，夹具修正后分别复验，
最终输出上限夹具的整组 192 项通过。旧版 ACI-13 提前结束缺陷也已修复。
CPU 真实模型样本通过：GOAL_MET、11 次 Agent 调用和 1 次真实 CPU 实验，
费用约 4.283047 USD，无未知预留和进程残留。

G4 首次 9 项通过、1 项失败：一次进程退出采样被正确记录为未知；
8 次 CUDA 实验均成功，80 个 Attempt 关闭且无残留。保持源码与断言不变，
仅复验失败用例后通过（140.54 秒），新增 4 次 CUDA 实验及 40 个 Attempt
全部闭合；十项用例已有通过证据，原始失败仍保留。
首次 G5 GPU 样本通过（743.60 秒）：GPU 1、11 次 Agent 调用及 1 次真实
CUDA 实验成功闭合，结果 checksum=6；13 个完整归属样本、零未知项，峰值
362 MiB。最终报告、Oracle 和资源池检查通过，12 个进程组及套接字均已消失，
后台服务退出码为 0，GPU 0、1 恢复空闲。
GPU 记录费用约 3.633895 USD，本轮 CPU/GPU 合计约 7.916942 USD，低于
13 USD，无未知预留，也未重复付费样本。日志与证据在
`.local/qualification/aci21-20260921/`。
当前证据支持进入有预算上限的新课题试运行；真实模型的多专家科学决策质量、
历史 TUI 闪退归因和并发控制延迟仍有待对应场景验证。尚未启动长期课题。
详见[后续资格证据](qualification.md#readiness-qualification-continuation-2026-09-21)。

## 框架

资格属于一个精确的候选与环境。Hypoloom 保留既有的 G0–G5 晋升模型、运行时
Oracle 与回归夹具。[测试选择器](../scripts/qualify.py) 运行普通的 pytest
选择;它的退出码是测试结果,不是聚合的发布决定。被跳过的档位、缺失的保留
队列或未完成的清理不能被记录为通过。

## 晋升规则

| 阶段 | 要求的结果 |
|---|---|
| G0 | 用聚焦回归和可观察的结果,关闭每一个已复现的修复失败。 |
| G1 | 冻结目标代码/测试候选;编译,检查导入/schema/合同/技能/文档,运行完整离线索件,并校验安装/打包资产。意外失败即 NO-GO。 |
| G2 | 对声明支持的保留运行队列逐个做无变更审计;比较预期发现、逻辑行与工件,并重放适用的持久化/恢复夹具。丢失发现或新增无法解释的发现即 NO-GO。 |
| G3 | 通过真实常驻 Host、私有运行时包、broker、Agent、Process、收集、发布、恢复和下一动作完成闭环。包括注入退出和完整的后代清理。 |
| G4 | 适用时用真实 CPU/GPU 作业运行若干有界假设演化循环,包括共享、等待、失败和恢复。计数外部可观察的效果,要求无静默停滞、无自有残留。 |
| G5 | 在显式时间/轮次/成本/资源限制下运行有条件的短期真实模型兼容金丝雀。它们采样原生提示/工具/适配器兼容性,不证明科学质量或确定性耐力。 |

G3/G4 闭环是主要的工程信号。长时间科学运行或 24/72 小时浸泡是可选的、由
风险触发的,不是默认集成测试。后续关卡不能弥补更早的失败。保留原始失败和
未知费用;重试是独立的样本。源码变更需要刷新受影响的证据和最终候选检查。

把 Git 候选、环境、命令、测试结果/跳过、动作计数、资源/成本上限、观察到的
清理和遗留缺口记录在当前的资格报告中。使用普通 Git、pytest JUnit 输出和
现有的运行/池 Oracle;不要创建并行权威或校验和协议。

## 安装与离线检查

使用固定的 [environment](../environment.yml),然后按 [SETUP](../SETUP.zh-CN.md)
中的开发附加组件安装项目:

```bash
python -m pip install -e '.[dev,monitor]'
python scripts/qualify.py g1 --junitxml .local/qualification/g1.xml
python -m build --outdir .local/dist
```

还要把 wheel 安装进独立环境,并从检出之外运行其 CLI/MCP 和一个全新的一次性
bundle。检查迁移、原生技能/插件和 Web 资产存在。源码测试刻意留在 wheel 之外。
导入环、Schema、工具、技能和文档测试是离线索件的一部分。缺失的可选设施是
显式跳过。

## G2:夹具与保留运行

```bash
python scripts/qualify.py g2 --junitxml .local/qualification/g2-fixtures.xml
```

这会重放现有的 Oracle、事务、schema 迁移、备份/恢复和 bundle 测试。**它不能
替代保留运行队列。** 公开解析器夹具位于 `tests/fixtures/profiles/`;私有研究
数据不分发。

对候选支持格式内的每一个保留 Hypoloom 运行,用隔离安装的 reader 和已接受的
基线 reader 执行相同的只读审计,例如:

```bash
/absolute/reader/bin/python -B -m hypoloom.cli oracle \
  --state-root /absolute/retained/root --run-id EXACT_RUN_ID
```

比较既有审计结果和预期历史发现;独立检查逻辑状态和工件内容未改变。现存的
OPEN Attempt 是历史事实,不是结清它的许可。不要为执行 G2 启动 Host。如果该
格式需要它原始的 bundle,使用该 reader 并记录这个更窄的范围。旧项目的运行
留在源项目中,使用它们自己的 reader;Hypoloom 不声称迁移或恢复它们。

首次导入没有保留的 Hypoloom 发布队列。显式记录这个覆盖缺口;不要虚构一个
遗留兼容通过,或把夹具重放当成完整 G2。未来保留的 Hypoloom 对照使用其现有
稳定的运行标识和结果,失败与成功一样保留。

## G3:生产进程闭环

```bash
python scripts/qualify.py g3 --junitxml .local/qualification/g3.xml
```

选定的七个场景覆盖两个 CPU 轮次与观察、已提交 submit 崩溃/重入、三轮 debate
重启、公共验收与目标停止、过早的执行完成、资源技能学习,以及 TUI 离开/重入/
取消/Host 退出。它们使用临时运行根和本地脚本化 provider,并在适用处使用实际
的原生 CLI/bwrap/Host 和 CPU 进程。先安装 Claude Code 和 bubblewrap;缺失依赖
及由此产生的跳过意味着受影响的生产边界未被证明。

## G4:共享 CUDA 循环

首先授权精确的 GPU UUID 和一个支持 PyTorch/CUDA/NVML 的运行时。现有的共享
测试需要两个选定的 UUID 和足够的可用显存;它们把单卡并发工作作为跨 Host
队列的一部分来证明。测试不安装永久资源服务。

```bash
export HYPOLOOM_REAL_GPU=1
export HYPOLOOM_REAL_NATIVE_GPU=1
export HYPOLOOM_REAL_SHARED_GPU=1
export HYPOLOOM_REAL_GPU_UUID=GPU-REPLACE_WITH_AUTHORIZED_UUID
export HYPOLOOM_REAL_GPU_SECOND_UUID=GPU-REPLACE_WITH_SECOND_AUTHORIZED_UUID
export HYPOLOOM_REAL_GPU_RUNTIME=/absolute/approved/cuda-environment
python scripts/qualify.py g4 --junitxml .local/qualification/g4.xml
```

这些用例覆盖普通的测量学习,以及一个等待容量、然后校准为同卡重叠的初始
显存估算。要求终态的运行/池 Oracle、实际 CUDA 结果、完整可归因样本和零自有
进程/资源残留。除非该确切用途被单独授权,现有研究池不是测试资源。

## G5:真实模型兼容性

所有实时测试默认保持禁用。选择一个精确的可执行文件、现有的普通 mode-0600
设置文件和模型。资源可用不是支出授权。先审查每个队列的上限,包括先前已计
费用和未知预留额;缺失的成本永远不是零。

共享认证输入是 `HYPOLOOM_CLAUDE_EXECUTABLE`、`HYPOLOOM_CLAUDE_SETTINGS`、
`HYPOLOOM_CLAUDE_MODEL` 和显式的 `HYPOLOOM_REAL_CLAUDE=1` 选择加入。对 local
或 common 队列,提供 `HYPOLOOM_G5_CUMULATIVE_LIMIT_USD`(为正且不超过现有
20 USD 金丝雀上限)和 `HYPOLOOM_G5_PRIOR_CHARGED_USD`(非负且低于该上限)。
只有真正未支出的队列才声明零。剩余金额成为运行预算,现有的调用、轮次和
时间限制保留。

USD 数值是 CLI/Host 派发阈值,不是上游账单的保证上限。在途响应报告的金额
可能超过其剩余阈值;保留该费用并停止进一步派发。审查每个样本时为重试、
分析和最终报告留出资金,并把未知成本保留在同一队列中。一个失败的 4 USD
阅读样本不是再花 4 USD 就能完成的证据;增加其额度需要显式批准,而不是换
目录或削弱闭环检查。

每次调用只选择一种模式:

| 选择 | 额外的选择加入 | 测试 |
|---|---|---|
| `g5-local` | `HYPOLOOM_REAL_CURRENT_G5=1` | 原生文件工具、CPU 执行、会话和最终报告 |
| `g5-common` | `HYPOLOOM_REAL_CURRENT_COMMON_G5=1` | Grounding 创作的公共解析器、目标证据和最终报告 |
| `g5-resource` | `HYPOLOOM_REAL_SHARED_RESOURCE_G5=1`、GPU/原生 GPU 选择加入、精确 UUID/运行时和显式 `HYPOLOOM_RESOURCE_G5_ALLOCATION_USD` | 原生资源技能、真实 CUDA 执行和学习 |

local/common 标志互斥。审查并设置输入后,运行 `python scripts/qualify.py
g5-local`、`g5-common` 或 `g5-resource`。每个都报告其原始结果和实际费用;
未知用量保留预留额。选择新目录时不要自动重跑失败的付费样本或重置其成本。
成功或失败后,验证所有自有 Host、Agent、实验和池请求都已关闭。

## 无副作用地列出测试

```bash
python scripts/qualify.py g3 --collect-only
python scripts/qualify.py g4 --collect-only
python scripts/qualify.py g5-local --collect-only
```

收集不会启用资源/付费标志。在 G1、G2 夹具重放或 G3 之前清除实时选择加入;
选择器拒绝这些意外继承的标志。对聚焦修复,先直接在相关测试上调用 pytest,
再为完整漏斗冻结候选。

## 当前状态摘要(2026-09-19 顶部条目)

候选:dev @ f2de65a 之上的未提交工作树,携带八条经审阅的修复指令及其审计
后续(适配器 `claude-code-bwrap/62`、compute `local-compute/6`、runner
`local-process-group/3`;全部变更仅通过既有版本固定作用于新运行)。源自对已
终止运行 `MERC-causal-202609172306` 的只读分析发现的四类缺陷:单调增长的
提示杀死 Agent 任务、TERMINATE_LINEAGE 语义缺口、GPU 压力误杀丢弃 4.2 小时
已完成扫描工作、控制通道饥饿使操作员的 End run 看似失败。

已执行证据:提示卸载重放(OBSERVER 775→93 KB,FINAL_REPORT 885→178 KB);
tick 计时 549.6 ms → 19.9 ms,修复后脏情形平坦约 5 ms;G1 完整离线 5,843
通过/26 跳过(3 个原生失败为测试夹具问题,修复后刷新 49 通过);G2 保留队列
8/8(7 个干净 + 1 个已知历史发现,数据库字节证明未变),便携夹具选择 39/39;
G3 本轮四个场景通过;G4 压力归因通道在两个授权 UUID 上为绿,主通道 9/10
(双 Host 用例命中已记录的零未知容忍 flake,针对性重跑 2/2 通过,断言未
削弱);G5-local 付费样本首次执行即通过——真实运行达到 COMPLETED/GOAL_MET
(指标 0.875),CC 2.1.277 + glm-5.3-flash,精确计费 2.827766 USD、零未知
Attempt,卸载合同在真实模型下端到端成立。

已记录的非阻塞遗留:单个不完整归因样本上的压力决策保持保守;压力取消只阻塞
工作负载实际剩余生命;其余十二处未过滤的 `list_attempts()` 全表扫描列为
后续项;G4 闭环零未知容忍在并发 CUDA 扰动下是已知 flaky 表面。

## 英文台账条目索引

以下按时间倒序列出英文台账的条目(原文标题),点击跳转到对应章节:

- [Current development — prompt offload, termination honesty, tick cost, and attribution-aware GPU pressure, 2026-09-19](qualification.md#current-development--prompt-offload-termination-honesty-tick-cost-and-attribution-aware-gpu-pressure-2026-09-19)
- [Current development — per-Attempt billing under CC 2.1.277 cumulative session ledgers, 2026-09-19](qualification.md#current-development--per-attempt-billing-under-cc-21277-cumulative-session-ledgers-2026-09-19)
- [Current development — project GPU-pool detection and managed creation in init, 2026-09-17](qualification.md#current-development--project-gpu-pool-detection-and-managed-creation-in-init-2026-09-17)
- [Current development — wire-final input floor for bill completeness, 2026-09-17](qualification.md#current-development--wire-final-input-floor-for-bill-completeness-2026-09-17)
- [Supplementary qualification — provider-auth-cooldown repair candidate, 2026-09-17](qualification.md#supplementary-qualification--provider-auth-cooldown-repair-candidate-2026-09-17)
- [Current development — provider auth cooldown and operator recovery, 2026-09-17](qualification.md#current-development--provider-auth-cooldown-and-operator-recovery-2026-09-17)
- [Current development — project-local research-runtime session records, 2026-09-16](qualification.md#current-development--project-local-research-runtime-session-records-2026-09-16)
- [Current development — single dotenv credentials file, 2026-09-16](qualification.md#current-development--single-dotenv-credentials-file-2026-09-16)
- [Current repairs — provider-stream envelope tolerance, 2026-09-16](qualification.md#current-repairs--provider-stream-envelope-tolerance-2026-09-16)
- [Supplementary qualification — approved repair recheck, 2026-09-16](qualification.md#supplementary-qualification--approved-repair-recheck-2026-09-16)
- [Current repairs — qualification boundaries and launch inputs, 2026-09-16](qualification.md#current-repairs--qualification-boundaries-and-launch-inputs-2026-09-16)
- [Current qualification — repaired dispatch candidate, 2026-09-16](qualification.md#current-qualification--repaired-dispatch-candidate-2026-09-16)
- [Current repairs — dispatch faults and academic binding, 2026-09-16](qualification.md#current-repairs--dispatch-faults-and-academic-binding-2026-09-16)
- [Latest observation — paused DeepSeek topic, 2026-09-16](qualification.md#latest-observation--paused-deepseek-topic-2026-09-16)
- [Current full qualification — 2026-09-16](qualification.md#current-full-qualification--2026-09-16)
- [Current development — selective paper reading, 2026-09-16](qualification.md#current-development--selective-paper-reading-2026-09-16)
- [Current repairs — responsive retrieval and explicit pool binding, 2026-09-16](qualification.md#current-repairs--responsive-retrieval-and-explicit-pool-binding-2026-09-16)
- [Latest observation — stopped Kimi topic, 2026-09-15](qualification.md#latest-observation--stopped-kimi-topic-2026-09-15)
- [Current repairs — dynamic resources and truthful observations, 2026-09-15](qualification.md#current-repairs--dynamic-resources-and-truthful-observations-2026-09-15)
- [Earlier synthesis — two completed Hypoloom topics, 2026-09-15](qualification.md#earlier-synthesis--two-completed-hypoloom-topics-2026-09-15)
- [Earlier observation — second Hypoloom topic GPU wait, 2026-09-15](qualification.md#earlier-observation--second-hypoloom-topic-gpu-wait-2026-09-15)
- [Earlier two-run review — 2026-09-15 morning](qualification.md#earlier-two-run-review--2026-09-15-morning)
- [Earlier startup qualification — selected binding ready, 2026-09-15](qualification.md#earlier-startup-qualification--selected-binding-ready-2026-09-15)
- [Promotion rules](qualification.md#promotion-rules)
- [Install and offline checks](qualification.md#install-and-offline-checks)
- [G2: fixtures and retained runs](qualification.md#g2-fixtures-and-retained-runs)
- [G3: production-process closure](qualification.md#g3-production-process-closure)
- [G4: shared CUDA loops](qualification.md#g4-shared-cuda-loops)
- [G5: real-model compatibility](qualification.md#g5-real-model-compatibility)
- [Listing tests without effects](qualification.md#listing-tests-without-effects)
