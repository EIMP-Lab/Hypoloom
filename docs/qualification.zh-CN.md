# 资格:G0–G5(Qualification)

[English](qualification.md) · 简体中文

## 当前开发：跨阶段科研工作流 skill，2026-09-28

ACI 28 / `claude-code-bwrap/67` 已实现
[共享工作流设计](design/research-workflow-skill.zh-CN.md)。一份公共源码初始化
Grounding 与各专家独立持有的插件，原生指令使用实际绑定名称按阶段加载。
初始 Grounding 获得只读访问，保留原执行角色、模型和预算。继续复用 Git
自进化、pin/revert/unpin、验证器和科学验收，不增加记忆服务、细化计时或旧运行
迁移。[使用指南](guides/research-workflow.zh-CN.md) 说明了工作模式和演化边界。

最终 G1 覆盖全部 **6429 个收集用例：6403 通过、26 个按条件跳过**。
冻结代码的完整执行在 6263 通过、1 个失败和 26 个跳过时停止：原生权限测试的
默认临时目录过长，超过 Unix socket 路径限制。改用短临时目录后，选出的
142 个失败／未完成用例全部通过，两次结果的并集覆盖全部用例。原失败记录保留。
更早的全套执行发现了一个实际监控回归：初始 Grounding 的插件所有者可能不同于
执行角色。现已精确识别该初始任务，并保留其他所有者校验；上述最终覆盖包含修复。
前期受沙箱权限阻止的原生调用没有计作通过。

针对性验证覆盖共同初始内容和大小预算、自定义／保留插件、按阶段路由、澄清与重试
绑定、原模型选择、只读挂载、修改／不修改的自进化、中断恢复、所有者隔离和既有版本
控制。原生 CLI 实际加载了演化后的公共入口及 Grounding 参考。三类任务各执行三个
独立解析器语义用例：分类最大化、带约束数值最小化、产物复现。这些属于脚本化兼容
验证，不能证明模型的科学质量。最终 wheel/sdist 与源码中的七个公共 skill 文件一致。

独立收尾审计覆盖七个真实 Host／原生 CLI 测试运行：**211 个 Agent Attempt、
18 个真实 CPU Process** 全部 CLOSED。实验创建、预注册、规范结果和发布数量一致；
Oracle 均通过，没有重复结果、待执行副作用、存活的所属进程组或 socket。场景包括
Host 重启、插件演化、Grounding 重建、同任务续接、资源 skill 学习与多专家建议。
原生 Agent 使用本地脚本化供应端，CPU 执行和 Host 生命周期是真实的。原失败证据
与最终原生测试运行分别保存。

有限真实模型对照使用现有 CC 连接和 `mimo-v2.6-pro`，仅 Grounding 调用真实 API。
比较当前代码禁用公共指导与启用 skill 两组，而非完整重放旧版本。任务、给定证据、
模型和预算一致：每组最多两次 Grounding；每次 40 turns、3000 秒、CLI USD 2；
每组分配 USD 8／9300 秒。后续 Agent 使用原生脚本化供应端，每组真实执行一次 CPU
实验。质量要求包括四个独立解析器用例、经收集和发布的 0.75/0.75 CPU 结果以及
完整收尾。CLI 模型定价未知，其金额估计不是供应商实际账单。

两组均在一次 Grounding 调用内通过全部四个语义用例及预期 CPU 结果。skill 组实际
调用 `hypoloom-grounding:research-workflow` 并读取对应 Grounding 参考。

| 组别 | Grounding 秒数 | 原生工具调用 | 报告 token | CLI 估计 USD |
|---|---:|---:|---:|---:|
| 禁用公共指导 | 999.91 | 23 | 602504 | 0.8140256 |
| 启用公共 skill | 609.37 | 15 | 384627 | 0.7271608 |

skill 组有一次目录列举失败：它尝试访问未按原路径挂载的目标目录，随后使用挂载后的
workspace 完成任务。两组均未观察到原生压缩、本地上下文拒绝或供应商上下文失败。
工具错误计数不涵盖全部科学纠错，精确重读计数也不覆盖 Bash 读取。这一对样本是正向
观察，不能据此确认因果关系或稳定的速度／费用收益。Grounding 合计
**987131 报告 token／USD 1.5411864 CLI 估计**。独立付费样本收尾审计确认
**22 个 Agent、两个真实 CPU Process** 全部关闭，发布数量一致、Oracle 通过，
无进程／socket 残留；其中仅两个 Grounding Agent 联系真实供应商。本次对照后
不追加付费重试或模型样本。

证据保留在 `.local/qualification/research-workflow-20260928/`，原运行诊断保留在
`.local/analysis/grounding-review-20260928/`。既有科研运行、个人 Profile 和生产 GPU
池未修改。本轮不能证明 CUDA、跨供应商泛化、可重复的速度／费用收益或完整 G5 科学质量。

## 当前开发：监控重新进入，2026-09-28

`/autoresearch-monitor` 快捷入口与保留的 `/autoresearch monitor` 共用只读发现、
既有来源验证器及 tmux／终端启动逻辑。init 确认启动成功后返回完整监控命令；
不新增运行登记文件，不写入 CC 输入历史，也不修改已有研究运行。

114 项针对性检查通过，覆盖真实 Host 创建的 SQLite 状态、新 helper 进程重新发现、
重复 ID、损坏状态库、外部目录、参数引用、启动器行为、bootstrap 输出、运行代码包
及双语文档链接。沙箱禁止 Unix socket bind，排除了两项原生 Host socket 测试，
不计入通过；tmux 调度使用已有测试可执行文件，尚未实测 CC 原生补全界面。
本结果不代表完整 G1/G5。一次本地只读发现耗时 0.2302 秒，发现 20 个状态库，
无警告；Host 存活状态明确保持未检查。未调用供应商，也未实际打开监控。
证据：`.local/qualification/monitor-entry-20260928/`；用法见
[监控教程](../USAGE.zh-CN.md#tuiweb-与人工决策)。

## 当前开发：init 池生命周期与时延，2026-09-28

[Init 生命周期方案](design/init-managed-gpu-pool-lifecycle.md)已实现聚合检查、自动生成
启动参数及已审阅的托管池准备。既有验证器、数据库、运行版本固定和 controller 锁继续
作为事实来源；目录 flock 协调服务维护与新 Host 接纳，其竞态依据及最小机制说明见设计。

232 项针对性测试通过，覆盖真实 SQLite 历史、进程组、跨进程锁与真实 Host 的
CREATE_RUN 路径；服务 supervisor 和池传输使用测试替身。9 项原生 socket 测试无法在
本沙箱完成：Unix socket bind 被禁止（EPERM），user-systemd 也不可达。没有削弱这些
检查或将其算作通过；本结果不等于完整 G1、原生服务、CUDA 或付费模型资格。

合成 CPU Profile 单次样本中，`--summary` 将 prepare 输出从 13,535 B 减至 1,947 B；
聚合检查用进程内 0.0904 s / 5,593 B 报告 supervisor 阻断，未创建状态根或池。顺序单次
样本包含缓存差异，不能据此宣称 CC 端到端提速。默认技能入口从 1,795 行减至 191 行，
详细参考按需加载。证据位于 `.local/qualification/init-lifecycle-20260928/`。
现有生产池、研究运行与私有 Profile 保持原状；验证未调用供应商。用户随后授权提交并推送至 dev。

## 当前开发：运行准备与上下文恢复，2026-09-28

ACI 28 / `claude-code-bwrap/66` / lifecycle v10 实施了
[运行准备与恢复方案](design/runtime-readiness-and-context-recovery.md)。init 与 Host
创建在 Agent 启动前复用同一池能力和成员校验；兼容池可以覆盖本次选卡子集。
托管创建复用已有服务权威，拒绝自动替换，包括已有数据库的停止池和未解决的具名池冲突。

提交前基础设施故障使用既有命令转换与 Attempt 诊断。Host 将受影响 Agent 按技术
失败收尾，禁止用 CPU 绕过故障，并等待鉴权后的操作者资源复查。停止、清理失败、
剩余预算和已提交实验继续保持原优先级。覆盖重启恢复、幂等控制、原生 Stop、Web/TUI
及独立 Oracle；没有新增故障账本或分配权威。
[操作指南](guides/runtime-recovery.zh-CN.md)说明了恢复步骤和限制。

上下文估计对严格匹配的文本追加及已知缓存／文本表示变化保留完整 provider usage。
六个原始本地拒绝重放后，从 **364635–668006** 的整请求保守估计变为
**96507–173044** 的实测加增量估计，在未修改的策略内通过。原请求中五个属于 CLI
摘要请求；重放证明结构准入修复，不证明未发生的 provider 结果或精确 token 数。
未知变化仍保守回退。原生压缩开始／完成、本地拒绝、provider 失败和未完成响应分别
记录。Process 观察展示实际资源授权、归属进程存活及日志活动，科研进度未知时明确
保持未知；价格未知的 CLI 美元数是未验证的估算。

证据保留在 `.local/qualification/runtime-readiness-20260928/`。首轮完整 G1
在 **6075 项通过、46 项失败、26 项跳过**后中断：测试期间修改源码触发了固定运行时
保护，原不完整报告保留。实际夹具兼容修正更新版本预期、状态输入，并让池故障在
成功创建之后注入；跟进 **135 项通过**。冻结候选后的完整 G1 结果为 **6321 项通过、
2 项失败、26 项显式跳过、30 条警告，耗时 1272.28 秒**。一项 TUI 夹具将本应 ACTIVE
的状态设为 CREATED；另一项暴露真实回归：已完成实验的动态日志年龄进入冻结的
CYCLE_REPORT 输入，导致可复用的重试会话被重建。现将实时活动限制在 OPEN Process，
已关闭记录保持稳定科研输入。保留原失败，并对最终代码做聚焦验证，不重复整套测试。
静态身份／生命周期检查 **20 项通过**。

独立首轮清点还发现一个夹具虽然选了 CPU grant，仍生成 CUDA 脚本；缺少 torch
导致的执行失败已被正确收集。夹具现生成真实 CPU 实现，并明确断言 Process 与
接纳结果均成功，原始根目录保留。资源恢复／文档跟进 **18 项通过**。原生 CLI
恢复此前已独立证明 CPU 收集成功。最终代码的原生／控制／上下文／文档验证
**93 项通过，耗时 111.75 秒**，包括两项 G1 失败；原生报告修复再次复用同一会话，
并发布规范路径的记忆。

三个最终代码根目录独立清点共关闭 **56 个 Agent Attempt 与 4 个成功的真实 CPU
Process**。其中两个原生 CLI 根目录为 40 个 Agent／3 个 CPU，脚本 Agent 根目录
为 16／1。实验创建、预注册、规范结果与发布数一致，三个 Oracle 全部通过，没有
OPEN Attempt、重复结果、待发布效果、存活归属进程组或 socket。原生资源故障根
目录记录一次技术 Agent 失败、一次阻断、一次复查、一个接纳的 CPU 结果及一次假设
评估。模拟 GPU 池检查不执行 CUDA，原始与最终清点均保留各自根目录路径。

真实模型协议仅在 Grounding 使用现有 CC 连接和选定的 MiMo 模型：每组最多两次
Grounding Agent 调用，每次 40 turns、3000 秒、CLI USD 2，每组分配 USD 8 和 9300 秒。其他 Agent
使用原生本地 provider 夹具，实验在 CPU 执行。普通组与读取合成参考文件、通过已核对
的 CLI 评估选项请求提前原生压缩的组进行比较。压缩覆盖要求实际完成事件；质量要求
四个语义反例，以及同一候选在两个固定 suite 各得 0.75，并经过 Host 收集、发布与
评估。预算和 CLI 估算不能证明供应商实际账单或可重复节省。

两组 **mimo-v2.6-pro** 样本均在一次 Grounding Agent 调用内通过四个语义检查，
取得 0.75／0.75 的 CPU 结果，并完成收集、发布与 Oracle 闭环：

| 组别 | Grounding 秒数 | 完成的 provider 消息请求 | 原生压缩次数 | 报告 tokens | CLI 美元估算 |
|---|---:|---:|---:|---:|---:|
| 普通组 | 419.09 | 22 | 0 | 767520 | 0.8499168 |
| 提前原生压缩组 | 742.81 | 19 | 3 | 432588 | 1.3669376 |

压缩组实际有三次请求使用 `message_text_appended` 计量。两组均没有本地容量拒绝或
未完成的已转发消息请求，三次压缩耗时合计 332194 ms。压缩组多读了参考材料，并
采用评估专用的较早阈值，属于不同工作负载；不能由 token 差值或较高估算费用推导
普遍效率结论，生产默认阈值保持不变。两组 Grounding 合计 **1200108 tokens／
CLI 估算 USD 2.2168544**，模型价格未知。付费根目录清点关闭 **22 个 Agent、
2 个成功 CPU Process**，Oracle 通过且无进程／socket 残留；其中仅两个 Grounding
Agent 使用真实 provider，其他 20 个使用本地脚本 provider。

真实样本还暴露了有界观察的限制：完整转录有三次压缩，stdout 尾部却只保留两次开始
事件。最后的观察修复在同一日志窗口内匹配开始与完成，保留早期覆盖未知，并在 Web
分别展示转录和开始事件的覆盖范围；不改变生成、准入或恢复。原付费状态不被改写，
最终显示／计数变化由只读重放及浏览器／上下文聚焦检查验证：**63 项通过，耗时
68.34 秒**，包括明确的截断窗口反例与实际交付的浏览器显示。此后没有行为变化。

既有研究运行、个人 Profile 与生产共享池保持不变。本次不授权 CUDA、服务替换或
跨版本运行迁移，不宣称完整 G4/G5 晋级或长期科研质量。

## 当前开发：GPU 容量与资源修订，2026-09-27

ACI 27 / `claude-code-bwrap/65` 实施了
[容量与资源修订方案](design/gpu-capacity-and-resource-replanning.md)。现有实测预算
计算根据可比的已关闭 Attempt 证据解释准入并选择 25%、15% 或 10% 余量；校准后
保留至少 512 MiB，实际 OOM 的预算增长仍独立保持 25%。未知工作负载要求池内
独占，现有池事务双向执行该限制并优先选择空卡。没有新增测量库或校准标记。

只读预览与提交复用同一容量算式。可靠容量上界区分不可满足与暂时占用；未知遥测
不会变成猜测的固定保留量。已排队的不可满足申请释放原有池请求，以技术执行失败
完成收尾，再通过既有 handoff 唤醒 EXECUTION。替代实验继续遵守原科学约束和
预算。Web/TUI 与既有失败事件展示具体原因。参见
[用户指南](guides/gpu-resource-planning.zh-CN.md)。

证据保留在 `.local/qualification/gpu-replanning-20260927/`。聚焦回归覆盖
20288/20024 MiB 差额复现、多设备与未知容量、队列推进、实测校准、并发独占准入、
NVML 保留量回退、技术收尾、替代实验，以及两个 Host 崩溃／重启点。另有启动前
容量丢失场景，覆盖先存在未执行 Attempt、再进行资源修订。测试独立计数一次失败
事件、关闭 Attempt、释放池请求和接纳的替代结果，并运行现有 Oracle。浏览器验证
使用实际交付的 JavaScript，区分无测量与实测余量。原生本地 provider 测试执行真实
CLI／CPU；模拟 GPU 测试不执行 CUDA。

独立只读清点验证五个新的模拟 GPU Host 根目录：**79 个 Agent Attempt、11 个
Process Attempt、11 个已释放池请求**。两个原生资源根目录关闭 **42 个 Agent
Attempt、4 个真实 CPU Process**，覆盖资源工具调用与下一轮 Skill 演进。七个
根目录的实验创建、结果和发布数一致，Oracle 全部通过，没有 OPEN Attempt、重复
结果、待发布效果、存活归属进程组或 socket。模拟 Process 数量包含技术／启动前
收尾，不代表 GPU 执行数量；清点输出保留原测试根目录路径。

首轮聚焦结果 **577 项通过、两项失败、两项跳过**，原记录保留。两项失败来自旧测试
要求未校准工作负载同卡并发；这些场景显式保留 ACI 26，ACI 27 则独立验证独占与
释放后另一未校准任务才启动。跟进回归 **268 项通过**。首次浏览器夹具缺少驻留
Host 状态；修正夹具后通过，未改变观察器的离线行为。
一次误以串行方式启动的完整检查在 715 项通过、787.85 秒后主动中断，随后按相同
测试选择使用八个 worker 重新执行。部分日志、XML 和中断残留检查均保留；被中断
的测试没有存活归属进程组或内核 socket。

完整 G1 随后完成：**6288 项通过、4 项失败、26 项显式跳过、30 条警告，耗时
1209.28 秒**，原失败记录保留。一项暴露节点投影重复维护的版本列表遗漏 ACI 27；
现改为复用已有可读版本集合，并回归当前、旧版和未知版本。另一项发现 Oracle
直接导入了领域校验器；最终计算将类型接纳与纯数值策略分开，保留 Oracle 独立的
原始来源校验及原有导入限制。两处夹具断言更新为新增等待说明和实际当前 Grounding
版本，没有放松科学结果或来源断言。最终单元选择 **152 项通过**；Host／Oracle／
观察和真实 CLI 跟进 **149 项通过，耗时 151.29 秒**，包括原 Grounding 失败项。
四项 G1 失败均已解决。这次局部修复发生在 G1 之后，最终代码未重复运行整个测试集。
最终代码的独立清点重做五个模拟 GPU 根目录，并包含三个原生根目录：**模拟组
79 个 Agent／11 个 Process Attempt，原生组 53 个 Agent Attempt／5 个真实 CPU
Process**。八个根目录均完整关闭，创建、结果与发布数一致。七个隔离池检查通过，
11 个 GPU 请求全部释放，两个原生 CPU 池没有请求；独立进程组及内核 socket
检查没有残留。最终证据为同目录中的 `final-followup.xml`、`final-census.json`
和 `final-census-summary.json`。

既有研究运行、个人 Profile 和正在服务的共享池保持不变。新 GPU 运行要求共享池
声明 `resource_replanning/1`，旧池不能静默忽略独占要求。本次没有付费模型或真实
CUDA 实验；这些结果证明框架行为，不证明 OOM 率改善、余量比例最优、模型修订
质量或完整 G4/G5 资格。

## 当前开发：文献工具与并行提案，2026-09-27

ACI-26 / `claude-code-bwrap/64` 实施了
[文献与提案方案](design/tool-guided-reading-and-parallel-proposals.md)。
文献准备和阅读提交共用调用者现有来源查找；工具错误明确未知字段，并解释
`source_ref` 到 `paper_ref` 的衔接。批量提交仍先验证全部条目，再接纳阅读任务。
没有新增独立引用工具或隐式下载。

Host 在普通容量和预算允许时并行执行同一提案的 FORWARD/BACKWARD；
两侧沿用最初 FORWARD 请求中的 Leader 建议。SYNTHESIS 要求两侧结果均已接纳。
一侧重试保留成功侧，最终失败会取消并排空仍在运行的兄弟进程；其他阶段、重复任务
或其他轮次不会获得并发例外。一格容量与保留 ACI-25 提案继续串行。运行时指令解释
独立机制与合法单候选，不提高默认分支数量。

现有只读摘要区分论证任务、已接纳阅读命令、阅读 Attempt 和科学假设链，使用实际
请求的候选上限、共享阅读状态逻辑及已读取的工具观察。归档不全时仍显示计数下界；
等待原因是历史观察，未知 CLI 价格仍为估算。没有另建状态台账或追加 transcript 扫描。
用户说明见[阅读与提案指南](guides/reading-and-proposals.zh-CN.md)。

证据位于 `.local/qualification/parallel-proposals-20260927/`。聚焦 Host 回归覆盖两侧
不同完成顺序、一格容量、旧版串行、重试、成功侧恢复、失败后迟到输出、其他专家和
两个 CPU 轮次。真实 CLI／本地脚本 provider 验证实际进程重叠、会话隔离、正常完成、
重试、耗尽、停止、重启，以及两种原始工具错误纠正后启动三个阅读助手。
独立六根目录清点为 **80 个 Agent Attempt、4 个 CPU Process**：全部关闭，创建、
结果和发布数一致，Oracle 均通过，无重复结果、待发布效果、存活归属进程或 socket。
原夹具失败另行保留，包括终端状态预期错误、停止缺少 cause，以及原生调用期间修改
固定运行时代码导致的拒绝。

最终完整 G1 为 **6275 项通过、26 项显式跳过、30 条警告，耗时 1095.45 秒**。
最初 6261 通过／13 失败的记录保留：发现 ACI-26 reader 注册遗漏、新汇合 Oracle
未处理审核任务的空上下文、旧版号／错误文字断言、mock Host 缺少 store，以及夹具
错误地禁止同一任务的顺序重试继续使用自己的会话。289 项聚焦复测与真实 CLI 重试
先通过，再运行最终完整回归。另一个独立失败反例修复活跃链去重，改读
CREATE_EXPERIMENT 实际的 `expert_cycle` 字段。没有放松汇合、来源或会话隔离检查。
完整回归结束后唯一的运行时代码调整是 Web 零候选文案改为 `0 accepted`，避免把
“提交后被拒绝”说成“尚未提交”。最后的浏览器／摘要／文档回归 **61 项通过**；
两侧已关闭、SYNTHESIS 被拒绝的真实状态组合也单独通过。该显示文案调整后没有
重复运行整个 G1。

G2 将五个已停止的 ACI-19/21/22/23/25 历史运行与 Git 基线对照。选定的监控、计数、
计费字段和完整 Oracle 输出相同，数据库大小与修改时间未变。四个 Oracle 通过；
ACI-21 运行既有的 `session_archive_missing` 在前后均存在。该对照不迁移旧运行，
也不宣称旧运行可用新版本恢复。

有限真实模型样本使用现有 CC MiMo 连接、脚本前置阶段和隔离根目录，不使用现有研究
运行、个人 Profile、GPU 或公开通知。每调用上限 24 turns、150000 token（强制）、
600 秒、2 美元；每根目录 2100 秒，模型与基础设施重试均为零。阅读最多五个真实调用，
每个提案组最多三个。阅读刻意要求两种错误形式，并使用合成原文，因此评估的是纠错
兼容性，不是自然错误率或公网可靠性。成功样本在目标阶段接纳后通过 CANCEL 停止，
不进入实验设计，不能作为完整课题 G5 资格证明。

初次串行样本因评估脚本保留夹具的每专家 200000 token 上限而未通过：FORWARD 使用
92693，BACKWARD 实际只获准 107303，却返回 116588，现有 Host 正确拒绝。没有
SYNTHESIS、上下文容量拒绝或进程残留。原失败目录保留，并排除出配对耗时比较。
协议随后明确将新一组隔离样本的角色上限设为 750000 token，保持单调用限制及产品
默认值不变；零付费路由与收尾对照通过。原样本申报预算 6 美元，新一组申报 22 美元，
均不代表供应商账单；修正后的失败样本不自动重复执行。

修正后的串行样本接纳了 FORWARD/BACKWARD，但 SYNTHESIS 候选遗漏必需的
`parent_hypothesis_id` 和 `relation`（本例需显式为 null），被判为
`invalid_structured_output`。它提出的一种机制没有成为
已接纳假设；零模型重试下直接结束。报告 token 为 261412，CLI 估算 0.5590144 美元，
Oracle 通过，没有 OPEN Attempt 或归属进程残留。没有放宽接纳规则来使样本通过。
该 explicit-signals 夹具的工作流 `COMPLETED` 不代表 Goal 达成或科研成功。

修正后的并行样本在 **426.39 秒**内接纳两侧论证及一条综合假设，报告 token 为
319686，CLI 估算 0.6633936 美元。实际同时观察到两个存活进程组，会话、scope 和
分支分别独立；持久 Attempt 的执行区间重叠 **109.14 秒**。已接纳声明把 FORWARD
的不变性机制与 BACKWARD 的成对基线检验相联系，保留固定 split、seeds 和阈值，
并以缺少区分其他机制的证据解释单候选选择。CNN 与增强假设仍未经代码核查，多项
配方组合也未隔离单因素；该样本没有实际科研实验。一次接纳仅证明有限兼容性，不能
证明科研质量提高、稳定产生多机制，或相对失败串行样本的速度／费用收益。两个修正
提案目录的 Oracle 均通过，无 OPEN Attempt 或存活进程残留。

首个真实阅读样本纠正两种调用错误、准备两份原文并接纳两个阅读助手；主调用的工具
回复包含两个结果。助手均读出准确的 **0.83 系数／5 turns 窗口**及原文限制。但是
MAIN 累计 165377 token，下一次请求在模型进一步综合前被 150000 token 的单调用
限制本地拒绝；外层为
`nonzero_exit`，具体原因在 `provider_failure.code=token_budget_exceeded`，当前
输入估算仍在上下文窗口内。MAIN 未被接纳。此外，私有评估目标错误地要求 HTML
提供物理页码，相关页码声明不能作为位置准确性通过证据。四个真实调用均正常收尾；
该结果只证明部分工具兼容性，不代表完整文献阶段成功。

另外明确登记一次阅读跟进：保留每根目录 10 美元／5 调用／2100 秒，以及每调用
2 美元／24 turns／600 秒；仅将隔离评估的调用／角色／运行 token 上限调整为
300000／1500000／2000000，并按格式要求 HTML 行号或 PDF 物理页码。所有原始及
跟进真实根目录的申报预算合计 38 美元。原失败结果不变；这不是配对性能比较，
也不修改产品或个人 Profile 的预算。

跟进样本在 **383.83 秒**内完成文献阶段：两个阅读助手与 MAIN 均被接纳，准确报告
0.83／5 turns、原文限制及 HTML 提取行号；MAIN 将合成文献证据与尚未验证的 pilot
目标区分开。MAIN 使用 153023 token，四个真实调用合计 228323 token，CLI 估算
0.4868184 美元。两次真实阅读样本实际发出的是空 `readings` 数组及 URL 误作引用，
**没有**发出目标中要求的未知 `wait_for_completion` 字段；第二次还在空 submit 中
附带 `max_wait_seconds`。因此，未知字段的精确重放由真实 CLI／脚本 provider 测试
覆盖；真实模型证据覆盖空批次和 URL 引用的纠错、委派、原文读取及最后综合。
这不是自然错误率，也不证明所有错误形式都能自动修复。

五个真实根目录独立清点 **37 个 Agent Attempt**，全部关闭，Oracle 通过，无 OPEN、
重复／待发布效果、归属进程或 socket 残留。真实模型样本没有运行科研 CPU/GPU
实验；相关机制覆盖来自上述原生／CPU 测试。全部真实样本合计报告 **1259537 token**，
CLI 估算 **2.6614696 美元**。CLI 不识别 MiMo 价格，这些金额是回退估算，不是供应商
账单或费用保证。首次阅读另有一次本地拒绝、零转发的请求，观察中仍保留为未计量。
不能由这些有限样本推导跨供应商、持续运行、GPU、科研优势或可重复的速度／费用收益。

## 当前开发：实测上下文与任务内续作，2026-09-26

ACI-25 / `claude-code-bwrap/63` 修复已复现的 ACI-24 Grounding 回归。
未变请求历史使用完整实测 usage 加有限新增文本估算，或已有且同输入的供应商
计数；未知输入明确标为估算。独立字节、时间、请求次数、累计 token 和费用限制
继续生效。监控及支持包分开当前请求估算与最近成功实测，并保留各自请求编号。

符合完整归档、相同来源和容量余量条件的同任务纠错或传输重试，复用现有 OPEN
会话绑定；容量/压缩失败、新任务、API 连接重载后使用新会话。Git、已发布来源和
工具归档提供有限续作观察及 Grounding 草稿位置。可选容量建议复用原生输出验证
socket，不授予工具权限或接受候选。不增加事实库、检查点或必需的付费摘要。

证据位于 `.local/qualification/context-regression-20260926/` 和容量专项的
`.local/qualification/context-capacity-20260926/`。原始支持 ZIP 与历史运行保留
原位置并只读访问。两份报告中可恢复的 4 对完整请求/usage，在输入额度仍为
205568 时均可通过修复后的投影。17 个旧 GLM/MiMo 成功 Attempt 的 235 请求回放中，
82 个原低实际用量误拒请求投影可容纳，但首次拒绝锁存前实际可达的仅 38 个；
81 个真实超额度观察仍全部拒绝。不能把投影可容纳等同于完整任务成功。

以下原生测试使用真实 Host/CLI 与本地脚本 provider；模拟 usage 验证机制，
不证明真实供应商计数准确性。CLI 还会在多个 text/tool_result 块之间移动
ephemeral 缓存标记；4 个先失败
的回归复现了旧比较丢失实测依据的问题，修复只影响计量比较。原生长 Grounding
完成 14 次资料读取，第 9 请求原输入字节量 214575，修复后的估计为 16786；
重建后读取并修正保留草稿，没有重复探索，真实 CPU 两套结果均为 0.75。
普通 MODEL 纠错另行验证同会话续作、归档延续及费用增量结算；分析阶段覆盖
“同会话恢复首拒绝 → 一次原额度内基础设施新会话重建”，并实际回读原纠错来源。
基础设施重试额度为 0 时仍不会重建，没有扩容或修改用户 Profile。

初步聚焦检查 324 项通过；6 个选定原生根独立核对 67 次 Agent / 6 次真实 CPU
均闭合，Oracle 通过，无重复结果、待处理效果或进程/socket 残留。初始测试中
结束状态字段取错、误要求文件化诊断全文内联、CLI outer usage 聚合预期变化
分别保留原始失败并修正，费用及科学断言没有放宽。最终完整 G1：**6229 项通过、
26 项按需跳过、30 条警告，916.90 秒**。初次全量 6199 通过 / 25 失败 / 26 跳过
的证据保留，原非按需用例现均有通过结果。失败涉及缓存标记、旧版本及强制新会话
预期、分析恢复预算边界、原核心代码行数分配及两项测试竞争。代码行数预算未扩大；
派生观察格式化移到现有上下文模块，Host 合并重复导入，绑定校验均保留。
全量及付费 Host 退出后，最终源码只再删除一条新同任务 Oracle 的墙钟排序断言。
回归先复现了时钟回拨后合法 CLOSED→OPEN 被误报；既有事务、紧邻 Attempt 序号、
冻结请求和归档已能证明先后，前序 OPEN 与跳号仍拒绝。最后这一行修改采用聚焦
回归及只读闭环重审，不声称全量 G1 在它之后重新运行。最终聚焦 **137 项通过，
27.80 秒**，包含时钟回拨及 OPEN 前序/跳号负例。使用最终 Oracle 对此前 15 个
原生根只读重审，193 次 Agent / 16 次 CPU 全部干净闭合，原数据库未改。
最终独立选择另行核对 G1 的 10 个端到端根：312 次 Agent / 25 次真实 CPU，
Oracle 均通过，无 OPEN、待处理效果、重复结果或进程/socket 残留。加上下述
两组付费根，最终审计合计 12 根 / 320 次 Agent / 25 次 CPU；初始失败与故意
OPEN 的单测明确排除。详见 `final-independent-audit.json`。4 项文档检查、
未定义名称检查和 Git 空白检查通过。

真实模型两臂复用现有 CC 的 `mimo-v2.6-pro`、high effort / adaptive thinking，
同一双数据集 Grounding 任务；每臂 8 USD / 900 秒，每调用 2 USD / 300 秒 / 40 轮，
总分配 16 USD / 1800 秒，仅 Grounding 发真实模型请求。baseline 无故障注入；
rebuild 仅在实际 evaluator 自检成功后才计划注入容量拒绝。其余阶段为本地脚本
provider。没有可用的已配置 GLM 连接，GLM 仅做历史请求回放。

| 真实模型组 | 结果 | 完整响应观测 token | 未完整计量请求 | 已接受验收器 / CPU |
|---|---|---:|---:|---|
| baseline | 两次各 300 秒超时，第二次同会话恢复 | 59292 | 2 | 无 / 0 |
| rebuild | 两次各 300 秒超时，未到达预设注入点 | 57069 | 2 | 无 / 0 |

10 个真实生成请求全部转发且 HTTP 200，没有本地容量拒绝。两组恢复首请求的字节
估算为 156517 / 148863，均低于原 205568 输入额度。各组先完成 7 次工具调查，
随后持续推理至时限，恢复调用没有新增工具动作。两组质量检查均失败，**不能据此
声称真实模型 Grounding、草稿后重建、重读减少或成本收益已获验证**；前述长阅读、
草稿修正和 CPU 结果来自本地脚本 provider 原生测试。原样本保留失败结果；用户后续
授权的 3000 秒复评另见下文，不将旧样本改判通过。

4 次 Grounding 各按 declared 保留 2 USD / 200000 token，总 8 USD / 800000 token
是保守核算，不能当作实际花费或真实总用量。6 条完整响应合计观测 116361 token，
4 条中断流缺少完整 usage；各次 CLI 计价均未知且缺终端结算。归档观察完整不等于
供应商计费完整。两组持久运行状态均为 COMPLETED，科研结论为 INCONCLUSIVE；
共 8 次 Agent 闭合，Oracle 通过，无进程/socket 残留。
最初评估脚本的模型路由断言在零供应商请求时失败，原根保留一个无进程的 durable OPEN，
单列排除通过计数；修正本地脚本并先通过真实 CLI 全 localhost 接线验证后，执行
原定两臂。协议和明细位于证据目录的 `GROUNDING-PROTOCOL.md`、
`model-cohort-analysis.json` 和各臂 `quality-output.json`。个人 Profile 和已有运行
未改，无 GPU 或公开通知；本轮不构成完整 G5 或长期科研质量资格。

历史回放仅替换容量判断，没有模拟整个新会话。大首请求缺少锚点，或单次追加大量
文本时，仍可能被保守拒绝；此前完整实测不能冒充下次请求的精确计数。原生压缩
继续可选，预算、归档完整性和科学验收仍有约束力。ACI-24 及更早运行保持固定
行为，本轮不迁移或重启已有运行。

## 当前开发：3000 秒 Grounding 复评，2026-09-26

用户授权将评估单次 Agent 时限改为 **3000 秒**。新隔离样本每组 9300 秒，容纳
重试与收尾，外部 watchdog 9360 秒。CC MiMo、adaptive/high、每调用 USD 2 /
每组 USD 8、40 轮及 200000-token observe 均沿用；生产默认值、个人 Profile
与已有科研运行未改。

首次仅改时限的样本在 **1100.23 秒**完成 Grounding，无容量拒绝或压缩，但暴露
两处私有评估驱动问题：任务未说明脚本专家实际 raw 格式，模型选择顶层 labels，
实际输入却为每 suite 内 labels，因此 CPU 得到两套 0、complete=false；报告
测试还误要求允许数据挂载所需的祖先目录不存在。原失败根保留 1 个 OPEN 报告
Attempt，OS 无残留；Oracle 一致性通过不等于完整闭合。原证据保留在
`.local/qualification/grounding-3000s-20260926/`。

修正版在已有 TASK 明确 raw 格式，以精确允许目录树和真实私有资产不可见替换
错误的祖先断言，其余只读、角色隔离、隐藏路径检查保留；实际挂载 evaluator
与已接受文件直接比较字节，不增加 hash 或状态来源。原语义和 CPU 期望均不变。
免费正对照通过；负对照实际执行报告保护分支，并正确拒绝两套 0.5，合计 26 个
Agent、2 个真实 CPU 全闭合。新付费两组包含协议修正，不是仅改时间的单变量对比。

| 修正后的真实模型组 | Grounding 用时 | 独立语义 / CPU | 完整响应 token |
|---|---:|---|---:|
| baseline | 首轮 888.76 秒 | 四类语义正确，两套 0.75 | 514987 |
| rebuild | 注入前 387.91 秒 + 重建后 162.72 秒 | 四类语义正确，两套 0.75 | 627177 |

重建组确实在 evaluator 落盘且自测成功后注入一次容量拒绝，新会话读取原评估器
和 smoke 输入后完成。两组质量均通过，23 个 Agent、2 个真实 CPU 全闭合。
28 条实际转发响应 usage 完整，共 1142164 tokens（含缓存读取）；额外一条本地
拒绝请求转发字节为 0。观察聚合将它列为一条 unmeasured request，不代表存在
未计量的供应商生成。无原生压缩事件。

四根独立审计合计 **49 个 Agent / 4 个真实 CPU**（付费 23/2，免费控制 26/2），
无 OPEN、待处理/重复效果、进程组或 socket。恢复确实使用不同会话，第 3 个工具
成功读取原 evaluator，后续没有 Write/Edit 或经检查的 shell 改写它。第一轮完整
成功 Write/Edit 历史还原的 18358 字节，与已接受源码及实际 CPU parser 一致。
4 项文档检查和 Git 空白检查通过。原协议失败根明确排除在这些干净闭合总数之外。

修正两组 CLI 估价合计约 USD 2.5063584，每个付费 Attempt 均标记模型计价未知；
此前协议失败样本另记 USD 1.1803776、606728 tokens，均不能当作供应商账单。
本例验证有限任务的草稿接续质量，不证明稳定节省费用、跨模型质量或长期科研
资格；GLM 未追加真实样本。新证据位于
`.local/qualification/grounding-3000s-contract-20260926/`，原失败证据保留。
本次未修改生产代码。

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
