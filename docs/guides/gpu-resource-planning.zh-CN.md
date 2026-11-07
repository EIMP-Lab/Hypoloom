# GPU 显存规划

[English](gpu-resource-planning.md) · 简体中文

ACI 28 的启动预检、节点级 GPU 故障恢复及上下文/进度观测见[运行恢复指南](runtime-recovery.zh-CN.md)。

新建的 ACI 30+ 运行按接纳的显存预算共享 GPU。校准决定显存余量，不决定是否允许
共享；既有 ACI 27–29 请求保留原先按校准状态决定独占的策略。既有运行
继续使用已接纳的运行时和资源申请；更新代码不会改变正在运行的 Host 或升级共享池。

后续 init 可在已审阅的 Start now 中准备项目托管池。创建、恢复或升级服务与仓库版本
提醒是独立流程；它们保留已有池历史，并要求不存在未结束依赖。
用法见[初始化指南](initialization.zh-CN.md)。

## 运行中调整 GPU 资源集合

新 init 的 GPU 模式默认将全部可用设备纳入托管池，无需逐张选择。
池成员与项目初始选卡分开：后者遵循 Profile，之后可在 TUI 调整。
入池不预占显存；旧的部分设备池首次扩为全卡池仍需维护，见[初始化指南](initialization.zh-CN.md)。

使用本版本新建的共享池 GPU 运行可以在 TUI 按 **G / Select GPUs**（大写 G），
勾选下一批任务可用的 GPU 后应用。Web 监控的 **Select GPUs** 提供相同操作，
需要操作员链接（`--allow-provider-control`）。普通只读链接显示当前选择和待释放的卡。

支持缩减、增加、替换 GPU 集合，以及清空选择后再恢复。范围是当前绑定的共享池
授予当前用户、且本机可见的物理 GPU，使用 UUID 固定身份。显式操作员选择可覆盖
启动时的 GPU 子集，但不改变每个实验的卡数上限、显存预算、训练命令或研究验收条件。
这项控制调整当前项目的 GPU 集合；共享池的服务地址、用户权限和全局设备成员仍由池管理流程维护。

默认采用等待完成后释放的方式：

- 已启动的训练保留原有 GPU、进程和资源授权，完成后释放；不迁移 CUDA 进程，也不中止训练。
- 尚未启动的预约随新选择调整，后续派发和重试使用新集合。
- 如果选中的卡不足以满足某个多卡实验，该实验等待补足卡数，不自动降为单卡或 CPU。
- 清空选择暂停新的 GPU 派发，并释放未启动预约；已启动训练和 CPU 工作可以继续。
- 更改记录写入运行历史。Host 重启后重放同一选择，旧实验的资源来源和实际派发设备仍可审计。

先将 `STATE_ROOT` 和 `RUN_ID` 设置为目标运行，UUID 用查询结果替换：

```bash
python -m hypoloom resource-options --state-root "$STATE_ROOT" --run-id "$RUN_ID"
python -m hypoloom resource-select --state-root "$STATE_ROOT" --run-id "$RUN_ID" --remove-gpu GPU-UUID-A
python -m hypoloom resource-select --state-root "$STATE_ROOT" --run-id "$RUN_ID" --add-gpu GPU-UUID-B
python -m hypoloom resource-select --state-root "$STATE_ROOT" --run-id "$RUN_ID" --gpu GPU-UUID-B --gpu GPU-UUID-C
python -m hypoloom resource-select --state-root "$STATE_ROOT" --run-id "$RUN_ID" --all-gpus
python -m hypoloom resource-select --state-root "$STATE_ROOT" --run-id "$RUN_ID" --no-gpus
```

自动化调用可附加 `--expected-revision` 做乐观并发检查，并指定 `--request-id`。
回复超时后，使用**同一请求 ID 和相同参数**重试。接受回复代表选择已持久化；
`status.resource_selection.pending_cleanup` 表示池端预约调整仍需重试，
`draining_gpu_uuids` 列出仍被已启动任务占用的已移除卡。

Host 需要本版本的运行时，池需要通告 `resource_selection/1` 能力。
旧的冻结运行时不会通过修改源码或 Profile 获得此功能；此命令也不重启或升级资源池。

## 实验沙箱中的 CUDA 可用性

GPU 沙箱配置第 2 版只绑定获准的 NVIDIA 设备节点，并只读挂载
`/sys/module/nvidia/initstate` 与 `/sys/module/nvidia_uvm/initstate`。
通用实验开放 `/usr/bin` 后，NVIDIA 驱动辅助程序依赖这些状态文件识别已加载的模块；
缺少它们可能导致宿主机 CUDA 正常、沙箱内却出现 CUDA 304 错误。

实验入口启动前，隔离的检查进程会初始化 CUDA、核对可见 GPU 的 UUID，
并在每张获准设备上创建上下文、进行小量显存分配。检查限时 30 秒；
失败时以退出码 78 结束，记录 `HYPOLOOM_CUDA_UNAVAILABLE`，不执行实验入口，
由既有 Process 失败与清理流程处理。此检查不增加 PyTorch 依赖。

实验自身的框架无法使用获准 GPU 时，也必须报错；`AUTO` 允许 Host 在派发前选择 CPU，
不允许分配 GPU 后静默回退 CPU。驱动检查不证明后续训练实际使用 CUDA，
也不替代实现自身的前向、反向和 checkpoint 小规模检查。CPU 实验及指标解析器
继续保持 CPU 隔离。既有运行保留冻结运行时；应用本修复需要新运行，不能修改旧 bundle。

## 预算含义

Host 取 Profile 硬下限、Agent 申报、可比实测预算和实际 OOM 预算下界中的最大值。
可选的 `initial_vram_per_experiment` 在获得合适的成功实测前继续生效。
设备级保留量单独计算：池对同卡所需的 reserve 取最大值，
不按实验数累加。Agent 的估计已包含其不确定性，不会再统一乘一次 Host 系数。

对于配置可比、归属明确的实测：

| 证据 | 实测峰值余量 | 池内共享 |
|---|---|---|
| 没有符合条件的完整成功记录 | 已观测峰值的 25% | 容量允许时共享 |
| 最近失败或测量未知之后有完整成功记录，且覆盖历史已观测峰值 | 15%，至少 512 MiB | 容量允许时共享 |
| 至少三次上述成功，最大峰值不超过最小值的 105% | 10%，至少 512 MiB | 容量允许时共享 |

预算向上对齐 64 MiB。实际报告的 OOM 独立将原接纳预算提高 25%；降低实测
余量不会删除这一下界。失败或部分采样不能证明完整运行的最大需求；周期采样也
不保证捕获瞬时峰值。可比配置必须准确描述模型、数据/形状、精度、批大小及
执行阶段。Process 成功是执行证据，不等于科研目标达成。这些比例是工程策略，
不是已证明普遍最优的数值。

有界的 `measurements` 历史通过 `observed_peak_lower_bound_bytes`，在部分采样或设备未知时
仍可展示归属明确的单卡已观测峰值下界，帮助发现原先申报量低于实际用量。
它不能证明完整运行最大需求或配置可比性。覆盖未知时 `sample_peak_bytes` 仍为
null，不能释放初始下限或缩小余量。ACI 30+ 会使用可比任务中已归属的峰值下界，
加上保守余量来提高预算，即使其他采样不完整；共享则按该预算决定。
`measurement_attempt_ids` 仍需精确匹配工作负载
标签。Agent 需根据实际配置解释下一次估计，成功获准运行不证明实际用量未超申报。
缩小余量仍要求完整采样校准。不把整卡占用或其他所有者的历史当作本实验测量，也不向
专家泄露进程身份；更新代码不会改变旧运行。

### 设备保留量

新模板在 `methodology.yaml` 的 `budget` 下默认配置：

```yaml
gpu_vram_reserve_mb: 512
gpu_vram_reserve_percent: 5
```

比例为正时，取 512 MiB、配置的绝对下限、**已核定单卡工作负载**的该百分比
三者最大值，再向上对齐 64 MiB。12,288 MiB 任务保留 640 MiB，19,456 MiB
任务保留 1,024 MiB。启用 GPU 还须按工作负载配置正整数
`min_vram_per_experiment`；非池化固定卡运行以此下限作为工作负载预算。
省略百分比或设为 0，完全保留旧固定保留量；采用既有 Profile 不会自动改写它。

设备保留量不替代任务估计的不确定性余量。Host 使用计算值进行预览、准入和
持续压力保护，已接纳请求保持原值。池化运行实际 OOM 后，下一次可比较任务的原有
25% 预算增长也会增大比例保留量。脚本错误、保护终止和外部占用不是 OOM。
专家通过修复、CYCLE_REPORT 和 SELF_EVOLVE 记录限定范围的经验，不能改变
用户配置的策略或已接纳的请求。

池下限仍约束准入：复用 4 GiB 下限的池时，即使请求采用 5%，仍至少保留
4 GiB。init 会展示实际池下限和初始预算；`get_resources` 的 `budget_preview`
显示请求保留量，`capacity_preview` 显示实际准入保留量。运行保护使用已接纳
请求的保留量，较高池下限只限制准入、不改写该请求。空闲池须通过已有的明确
审阅重配置流程修改；正在服务运行的池及旧运行不会自动更新。

初始化审阅会分别解释请求保护线、比例的计算基数、绝对下限是否主导，以及
共享池下限对应的初始准入保留量。池下限未知时明确显示未确认。修改百分比
不会取消显式绝对下限；要采用新模板策略，需同时检查以上两个 Profile 字段
和共享池配置。已有运行使用创建时冻结的预算。

## 预览与等待

`get_resources` 仍可传空对象。ACI 27 还支持可选的 `resource_estimate`
（格式同 `submit_experiment`）及正整数 `gpus`。返回的 `budget_preview`
和 `capacity_preview` 解释申报与实测值、余量、OOM 下界、共享方式、逐卡容量
及差额。预览只读、不预留资源；实际提交重新验证并接纳当时的证据。
同运行继续遵循 FIFO：即使较小请求能装下，也可能等待队首较大请求。
不要仅缩小申报值来插队。

Web/TUI 资源面板区分：

- 容量或公平等待：等待设备容量释放或前序任务。
- 独占等待：存在不兼容的独占承诺，包括采用旧策略的已有请求；ACI 30+ 新请求
  不会仅因测量不完整就要求整卡独占。
- 容量未知：可靠的可用容量资料不完整。
- 需要修订：授权设备已知容量上界也不足以满足申请。不能仅由瞬时空闲显存低
  得出此结论；后端可提供时会单独显示驱动保留量。

提交前已确定不可满足的申请会被拒绝，并附容量细节。已排队申请若被确认不可
满足，框架安全释放请求、记录技术执行失败，通过正常 EXECUTION handoff 交还
Agent。证据关联完成后，Agent 可在原有预算内通过 `replaces_experiment_id`
修订。修订必须在科研约束内降低实际需求，单纯降低申报不能绕过实测下界。
已有 `experiment_failed` 事件携带资源原因，复用已配置通知通道；不新增通知
授权或轮询事件账本。

## 部署与验证范围

新建池化运行要求共享池声明 `resource_replanning/1` 能力。旧池会明确报告
不兼容，不会静默忽略独占约束。池部署需要独立于既有研究运行安排，不迁移已接纳运行。
init 还会检查支持 TUI 选卡的 `resource_selection/1`；旧托管服务沿用已有的升级审阅
和依赖检查。
独占仅约束池内承诺，其他外部 GPU 用户仍由原有实时容量及压力保护处理。

本功能不改变既有 reserve、不扩大设备授权、不迁移在途进程、不自动修改科研
超参数，也不拼接缺失 seed 的结果。实际验证范围见[验证记录](../qualification.zh-CN.md)；
模拟设备回归不证明真实 CUDA 的 OOM 率或模型修订质量。

有界测量历史还提供 `observed_over_budget`（没有可用自有显存样本时为未知）及
`pressure_exceeded`。覆盖未知的测量仍不能校准更小余量。ACI 30+ 池化运行中，
完整的实时归属采样确认用量未超过接纳预算时，记录 `within_budget`，不因整卡
压力终止该任务；确认超预算时记录 `self`，归属未知时保留原有保守保护。
旧运行的 `self` 仅表示释放任务可以恢复保留空间。两种解释都不证明它是占用的
唯一原因；再次提交前应分别核查预算低估与外部压力。
