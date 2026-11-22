# 复用共享 GOAL 评估器

[English](shared-evaluation.md) · 简体中文

## LOCAL 测量的已知答案

从 ACI 45 起，每个非空的本地测量都要提供已知答案的样例，至少一个变体应有不同的预期读数。清单位于 `/workspace/work/measurement-cases.json`。以下例子用一个名为 `dev` 的单元和 `DELTA_FROM_BASELINE` 比较，同时检验变好和变差两种情况。

```json
{
  "baseline": {"observation": {"metric": "score", "observations": {"dev": {"value": 0.75, "baseline": 0.50}}}, "metric_value": 0.25},
  "lower":    {"observation": {"metric": "score", "observations": {"dev": {"value": 0.25, "baseline": 0.50}}}, "metric_value": -0.25}
}
```

名为 `baseline` 的样例把声明的原始文件放在 `/workspace/smoke/`，各变体放在 `/workspace/work/cases/<名称>/`。总计提供 2 至 8 个样例。对于差值比较，`value` 和 `baseline` 分别填写已知的候选与对照绝对读数。Host 按声明的比较和归约规则计算 `metric_value`。设计阶段检查草稿，提交实验时再用实际捕获的解析器和样例复核，通过后才预注册。每次检查的所有样例共用 15 秒 CPU 沙箱运行时限。仅使用 GOAL 且本地测量为空的提交沿用共享评估器。

## 共享 GOAL 的判据与诊断

从 ACI 43 起，Grounding 可以在公共成功判据和有效性检查之外声明仅供报告的诊断值。诊断值为假或非零时，候选仍可达标。解析器按声明的名称和类型输出每个字段。独立 Oracle 从已发布的原始证据重新执行解析器。

| 声明 | 解析器输出 | 用途 |
| --- | --- | --- |
| 数值判据 | `measurements` | 按声明的运算符和阈值比较 |
| `IS_TRUE` 判据 | `checks` | 按 `ALL` 或 `ANY` 与其他判据组合 |
| `validity_checks` | `checks` | 候选达标需要所有有效性检查成立 |
| `diagnostics: {"cache_matches": "BOOLEAN", "offset": "DECIMAL"}` | `diagnostics` | 记录观测值供复核 |

### 在 Grounding 中运行成对样例

从 ACI 44 起，Grounding 和 Design 可以把完整草稿存入工作区，然后执行 `python3 /runtime/native_output.py --check-draft 路径`。Host 使用正式提交时的结构、引用和适用的 CPU 样例检查，并返回失败的字段或样例。草稿检查保留正式纠错次数，修改完成后仍由 Stop 校验最终提交。

启用进展时限后，首次通过结构检查、候选检查、更多解析样例或完整草稿检查都可续时。每次尝试的每个进展只计一次，续展受阶段和整场运行的硬时限约束。尝试详情显示实际生效的时限与来源、最近通过的检查和待修错误。模型 API 请求和训练进程各自计时。[预算配置](../../config/README.zh-CN.md)

跨轮比较可读取 `overview.totals.experiments.first_started_at` 和 `time_to_first_start_seconds`。正式实验完成预注册并绑定进程后，这两个字段分别给出该次进程尝试的开始时间，以及距整场运行开始的秒数，此前取值为 null。每次编写任务的 `authoring.repeated_errors` 记录连续检查遇到相同错误的重复次数。

使用答案已知的小型合成或公开样例。基准样例位于 `/acceptance/smoke/<原始文件>`。可以在 `/acceptance/work/acceptance-cases.json` 中为每个样例声明完整的预期观测值和预期结论。例如，某协议要求分数为正且样本完整，以下三例分别覆盖基准、仅缓存发生变化和样本缺失。

```json
{
  "baseline": {"observation": {"measurements": {"score": 1}, "checks": {"complete": true}, "diagnostics": {"cache_matches": true}}, "goal_met": true},
  "cache_only": {"observation": {"measurements": {"score": 1}, "checks": {"complete": true}, "diagnostics": {"cache_matches": false}}, "goal_met": true},
  "missing_sample": {"observation": {"measurements": {"score": 1}, "checks": {"complete": false}, "diagnostics": {"cache_matches": true}}, "goal_met": false}
}
```

每个变体的全部原始文件放在 `/acceptance/work/cases/<样例名>/` 下。Host 为每例创建独立的 CPU 解析沙箱，整组共享 15 秒上限和 Grounding 的字节预算。观测不符时，既有纠错流程指出失败的样例。样例与 `parser_files` 分开存放。

### 将留出数据保留给正式 GOAL 进程

在使用 `evaluation: grounding` 的 v4 Profile 中，将训练和开发数据目录列入 `safety.yaml` 的 `allow_read_paths`，把独立的留出文件或目录列入 `evaluation_only_read_paths`。

```yaml
allow_read_paths: [/datasets/study/development]
evaluation_only_read_paths: [/datasets/study/holdout]
```

正式 GOAL 原始实验进程按声明顺序读取 `/hypoloom-evaluation/1`、`/hypoloom-evaluation/2` 等只读路径，也可从 `HYPOLOOM_READ_ROOTS` 取得这些别名。设计、LOCAL、实现验证探针及入口小样本使用开发数据。解析器读取已声明的原始实验产物。准入时检查规范路径及其与其他挂载、目标仓库、运行环境和私有文件的重叠。一个文件若同时包含开发集和留出集，需要先准备按数据划分隔离的文件或视图。

### 为下一次运行复用经验证的协议

从已接纳的 Grounding Attempt 导出解析器，并提供带预期结果的样例目录。目录包含 `smoke/`、`work/acceptance-cases.json` 和 `work/cases/`。导出命令先重跑样例，成功后才创建新目录。协议版本应标识参考数据、划分、判据和证据格式。每个候选的身份应通过该候选自己的原始证据核对。

```bash
python -m hypoloom.evaluator_examples --state-root "$PRIOR_STATE" --run-id "$PRIOR_RUN" \
  --fixtures "$EXAMPLES" --protocol study-v1 --output "$TARGET/protocol-v1"
```

审阅导出目录，将其提交到研究目标仓库，再在下一份 Profile 的 `goal.yaml` 中指定它。

```yaml
evaluation: grounding
evaluator_seed:
  path: protocol-v1
  protocol: study-v1
```

准入从目标仓库绑定的 Git 提交读取指定文件。新一轮 Grounding 从这些源码和样例开始，重试会保留草稿。最终提交须保留所选协议的成功判据、有效性检查、诊断项和原始输入名称。即使修改了草稿样例，也须通过原版本中的样例。Grounding 完成后，Host 记录本轮新接纳的来源 Attempt。研究问题或数据划分发生变化时，可以选择匹配的新版协议，也可以让 Grounding 重新编写评估器。
