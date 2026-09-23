# 安装与配置 / Setup

[English](SETUP.md) · 简体中文

推荐入口是 Claude Code 中的 `/autoresearch init`。它先做只读检查，展示完整启动方案，
最后由用户选择 `Start now` 或 `Prepare only`。项目交互配置以
[settings.example.json](.claude/settings.example.json) 为来源；已有配置需要人工合并，
不能覆盖。正常 `Start now` 不需要先 export 四组变量、退出 Claude Code 或重开会话，
也不要求用户另开 Host/monitor 终端。

个人配置入口见 [config/README.md](config/README.md)。公开空白模板位于
`config/hypoloom.env.example`，真实文件仍位于隐藏且被 Git 忽略的
`.local/credentials/hypoloom.env`。使用控制 Python 执行
`python -B -m hypoloom.bootstrap paths --profile <name>`，可查看默认路径和
Profile 实际引用路径、存在性、权限，不显示秘密内容。模型认证仍单独配置。

init 通过 `hypoloom.bootstrap prepare` 在内存中填写、验证模板，默认零写入；
明确添加 `--write` 才创建新六文件 Profile。`bootstrap launch` 默认只预览启动
参数，授权后添加 `--start` 才执行既有托管流程，在 Host 就绪前后重读并核对 Profile。
准备期间配置变化会中止启动。配置检查不调用模型服务，不等于模型实际可用或科学
质量已验证。辅助命令用法见 [Profile 指南](profiles/README.md#deterministic-preparation--确定性准备)。

## 1. 控制环境

运行拓扑面向 Linux：需要 Git、bubblewrap（`bwrap`）、可用的用户命名空间和 Claude
Code CLI。托管启动还需要可访问的 user-systemd。控制进程使用 Python 3.12，SQLite
至少 3.51.3；系统自带 Python 的 SQLite 可能不满足要求。

```bash
conda env create --prefix ./.conda/envs/hypoloom --file environment.yml
conda run --prefix ./.conda/envs/hypoloom python -m pip install -e '.[monitor,dev]'
./.conda/envs/hypoloom/bin/python -c \
  'import sqlite3; print(sqlite3.sqlite_version); assert sqlite3.sqlite_version_info >= (3, 51, 3)'
./.conda/envs/hypoloom/bin/hypoloom --help
```

`monitor` 安装 Textual；`dev` 安装测试和打包工具。CPU/GPU 实验可以使用独立的研究
环境，不能把控制 Python 自动当作项目的 CUDA 环境。公开模板中的运行路径需要替换。
默认 CPU 沙箱要求解释器位于完整运行环境内部。普通 venv 的 Python 可能是指向环境外的
符号链接，不能据此替代完整 Conda 环境；本项目的安装与进程闭环验证使用后者。
Web 浏览器测试还需要本机 Chrome/Chromium，`dev` 已包含其 Python 驱动依赖 `websockets`。

## 2. 模型与文献认证

使用 provider 实际支持的 pinned exact selector，不能以营销别名代替已经验证的模型。
init 使用当前选定认证来源解析模型选项，展示模型标识，不显示密钥。

- **environment auth**：使用已批准的 `ANTHROPIC_API_KEY`，以及需要时的
  `ANTHROPIC_BASE_URL`。可选模型映射同样来自明确选择的来源。
- **已有设置文件**：`--claude-settings` 只接受已经存在、绝对路径、普通非符号链接、
  mode-0600 的合格文件。支持的文件内认证包括 `env.ANTHROPIC_API_KEY` 或
  `env.ANTHROPIC_AUTH_TOKEN`。`apiKeyHelper` 不能据此判为可用认证。
- 交互式 `/login` OAuth 不等于受隔离 Host 的认证。实验会话的设置和工具边界由 Host
  明确构造；不要把交互项目的设置直接交给 Agent，也不要自行追加 `--bare` 改变入口。

运行中遇到 API 限流、额度耗尽或凭据失效，请按[API 状态与恢复](docs/guides/provider-recovery.zh-CN.md)
操作，其中包含私有连接 JSON 格式、TUI/Web/CLI 切换步骤和恢复确认方式。连接可切换，
运行的模型仍固定；不要修改原始私有 settings 副本，也不要把模型 key 填进文献/通知
使用的 dotenv 文件。

文献检索使用 OpenAlex。完整原生专家路径需要一个已批准的 dotenv 凭据文件（建议
`.local/credentials/hypoloom.env`，模板见 `config/hypoloom.env.example`），
其中存放 `OPENALEX_API_KEY`，选用 Unpaywall PDF 后备时还需 `UNPAYWALL_EMAIL`；要求为
已有、绝对路径、非符号链接、mode-0600 文件。本地 v4 Profile 在
`literature.credentials_file` 声明该路径，init/操作员流程将其作为 `--credentials-file`
传给 `serve`、`profile-check` 和 `start`；旧的单密钥 `--openalex-api-key-file` 仍被
接受，二者不可混用。PDF 路径需要 Poppler 的 `pdfinfo` 和 `pdftotext`。
网络超时、响应/PDF 大小和页数上限由 init 一并审阅。检索会保留来源 URI 与提取证据；
未取得 PDF 或元数据不足不应被描述成已阅读全文。

原生 `web_search` 默认直接访问 DuckDuckGo，不读取 HTTP(S)_PROXY。该出口不可用时，
可在 Host `serve` 参数中显式选择 `--web-search-fallback openalex`，复用上述 OpenAlex
配置。后备结果保留原始搜索错误，并标注 `academic_literature`；它只提供学术文献元数据，
不代表通用网页搜索恢复，也不代表取得全文。此选项不写入 Profile，不传给 `profile-check`
或 `start`。应分别核验直接搜索和后备路径；未返回费用信息不能计为零。
显式 `web_search(..., scope="academic")` 则直接复用 `--literature-adapter openalex`
配置的客户端，无需开启网页后备。两种选择在运行配置中分别显示为 `academic_provider`
和 `search_fallback`，不可将后备关闭误判为 OpenAlex 未配置。
PDF 默认后备为 Semantic Scholar，也可显式选择 Unpaywall：在同一组文献参数中加入
`--literature-pdf-fallback unpaywall`，一起传给 `serve`、`profile-check` 和 `start`；
此时上述凭据文件还必须提供 `UNPAYWALL_EMAIL`（旧的 `--unpaywall-email-file` 单密钥
方式仍被接受，不可与凭据文件混用）。邮箱内容不会写入 Profile 或 Agent 输入。此选项
只按精确 DOI 查找开放全文，
不调用 Unpaywall 标题搜索，也不把 OpenAlex key 发给 Unpaywall。
返回地址仍经过现有 HTTPS/DNS、大小、PDF 格式和 Poppler 校验。后备遇到 429 后，会在
同一 Host 内冷却 60 秒，跨专家批次复用该状态；直接开放 PDF 下载不受后备限流影响。
配置检查不发送网络请求，应另外验证一次实际 DOI 查询与全文获取。

密钥、真实 Profile、本地 `.claude/settings.json`、研究数据和运行记录均不提交到 Git。

## 3. 选择双语 Profile

见 [模板说明](profiles/README.md)。公开模板统一采用多专家设计，提供当前 v4 的
`multi-expert-en` 和 `multi-expert-zh`。每套包含六个 YAML 文件，放入独立的
`profiles/local/<profile>` 后填写目标、模型、运行环境和数据路径。init 可完成经确认的
复制；它只逐个复制六个文件，并拒绝覆盖已有目录。

预算来自准入后的 `create_run_payload.research_contract.budget.agent_invocation`，
包括 `max_turns`、`max_tokens`、`max_output_bytes`、`wall_seconds`、`max_cost` 与
`token_enforcement_mode`。Host 参数必须匹配这些值。`max_cost` 是传给 CLI 的单次调用
费用阈值；总额和每专家阈值在 `safety.yaml`。已发出的响应可能在回报费用时超过剩余额度，
因此这些参数不等于上游账单的硬上限。Host 如实计入超额并阻止后续调用；预算规划需要为
重试、分析、报告及未知费用留出余量。观察模式只改变 token 阈值，不能解除费用、时间或资源约束。

默认 Run ID 为 `<profile>-<YYYYMMDDHHMM>`，Request ID 为同一标识加 `-create-1`。
时间戳只取一次，状态目录为 `state/<Run ID>`。冲突时应重新审阅路径，不能复用旧根。

## 4. GPU 与后台 Host

模板默认为 CPU。GPU 需要填写 Profile 并审阅精确 UUID、GPU runtime prefix、可用显存、
reserve 和并发预算。独立资源池需要先由操作者明确配置；`gpu-pool-status` 只查询，不会
创建或替换服务。共享池允许同卡多实验，实际容量和并发上限仍然生效。

“允许使用其他用户也在使用的卡”与“接入共享池”是两个独立选择。共享池绑定通过 Host 的
`--gpu-pool-socket`、`--gpu-pool-namespace` 和可选的 `--gpu-pool-server-uid` 传入，
不保存在六文件 Profile 中。在新的 CC 会话中复用已有池时，应提供已确认的 socket 路径，
为新状态目录选择独立 namespace，并在最终评审中核对实际 `serve` 参数和 UUID。
只搜索 `/tmp` 或 `/run/user` 无法判断项目目录下是否已有池；无需因此重建池或退回 nonpooled。
接入池也不会把显式 `gpus: 0` 的提交或已经发出的 CPU grant 自动改为 GPU。

`Start now` 调用 `.claude/skills/autoresearch/scripts/manage_host.py`，通过 user-systemd
启动后台 Host 并确认 `control.sock` 就绪，然后提交已经审阅的创建请求。Host readiness
timeout 默认为 30 秒，范围必须大于 0 且不超过 300 秒。
`Prepare only` 完成批准的配置准备，不启动 Host。`Linger=no` 表示退出最后一个登录会话
后不能承诺后台持续运行；init 不修改 linger。

## 5. custom interpreter（可选回退）

若不使用默认控制环境，先验证自己的 Python/SQLite 及依赖，再显式提供路径。以下是
自定义交互会话的可选方式；正常托管 init 会将路径传给所需子进程，不需要重启 Claude Code。

```bash
export HYPOLOOM_RUNTIME_FAMILY=hypoloom
export HYPOLOOM_PYTHON=/absolute/qualified/environment/bin/python
export HYPOLOOM_HOOK_PYTHON="$HYPOLOOM_PYTHON"
claude
```

安装成功不等于完成资格验收。下一步见 [日常操作](USAGE.zh-CN.md)、
[操作员指南](docs/operator-guide.zh-CN.md) 和 [G0–G5](docs/qualification.zh-CN.md)。
