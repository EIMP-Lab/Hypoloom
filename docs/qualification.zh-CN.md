# 资格:G0–G5(Qualification)

[English](qualification.md) · 简体中文

本文是资格框架与当前状态的中文导读。完整的逐条证据台账(候选、环境、命令、
结果/跳过、动作计数、资源/成本上限、观察到的清理与遗留缺口)以持续更新的
[英文版](qualification.md) 为准;中文摘要可能滞后,任何冲突以英文台账为准。

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
