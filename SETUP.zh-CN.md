# 安装与配置

[English](SETUP.md) · 简体中文

在 Claude Code 中使用 `/autoresearch init` 准备课题并审阅启动方案。
首次运行前，请完成以下配置。

## 1. 安装控制环境

服务器需要 Linux、Conda、Git、bubblewrap（`bwrap`）、可用的用户命名空间和
Claude Code CLI。托管后台运行还需要 user-systemd。项目环境提供 Python 3.12
及兼容的 SQLite；运行时要求 SQLite 3.51.3 或更高版本。

```bash
git clone --branch main https://github.com/EIMP-Lab/Hypoloom.git
cd Hypoloom
conda env create --prefix ./.conda/envs/hypoloom --file environment.yml
conda run --prefix ./.conda/envs/hypoloom python -m pip install -e '.[monitor]'
./.conda/envs/hypoloom/bin/hypoloom --help
```

后续 `hypoloom` 命令均在此环境中执行：

```bash
conda activate ./.conda/envs/hypoloom
python -c 'import sqlite3; print(sqlite3.sqlite_version); assert sqlite3.sqlite_version_info >= (3, 51, 3)'
```

`monitor` 安装终端界面；需要通知图表时添加 `notify`，需要浏览器集成时添加
`institutional`。PDF 提取还需 Poppler 的 `pdfinfo` 和 `pdftotext`；浏览器依赖见
[机构全文访问](docs/guides/institutional-access.zh-CN.md)。

实验可使用独立的 CPU/CUDA 环境。解释器的实际路径必须位于所选运行环境内；
指向环境外解释器的 venv 符号链接不满足默认沙箱要求。请分别审阅控制环境与实验环境。

## 2. 启用项目配置

先审阅 [.claude/settings.example.json](.claude/settings.example.json)。
新仓库尚无 `.claude/settings.json` 时，执行一次：

```bash
(umask 077; set -C; cat .claude/settings.example.json > .claude/settings.json)
```

该命令拒绝覆盖已有文件。若已有项目配置，请手动合并所需 Hook 与 MCP 配置；
修改 Hook 注册后重启 Claude Code。项目配置用于保护框架文件并提供研究工具，
与模型认证分开管理。

## 3. 配置模型与文献访问

选择供应商支持的确切模型标识。初始化会展示生效模型与认证来源，不显示密钥。

| 模型认证方式 | 要求 |
| --- | --- |
| 环境变量 | 使用已批准的 `ANTHROPIC_API_KEY`；供应商需要时设置 `ANTHROPIC_BASE_URL`。 |
| 已有设置文件 | 选择绝对路径、普通非符号链接文件，权限为 0600；支持 `env.ANTHROPIC_API_KEY` 或 `env.ANTHROPIC_AUTH_TOKEN`。 |

交互式 `/login` OAuth 和 `apiKeyHelper` 不能作为隔离研究会话的认证依据。
不要将仓库的交互配置用作研究认证文件，也不要通过 `--bare` 改变启动方式。
本地配置检查通过不代表供应商一定接受请求。

文献与通知凭据填写在本地 Profile 中，不写入公开模板或命令参数：

| 文件 | 字段 |
| --- | --- |
| `methodology.yaml` | `literature.openalex_api_key`；可选 Semantic Scholar、Unpaywall 与 Elsevier 设置 |
| `notify.yaml` | 已确认的通知接收者、令牌与可选 `proxy_url` |

研究文献检索使用 OpenAlex，默认 DOI 后备查询使用 Semantic Scholar；选用 Unpaywall
时需填写联系邮箱。出版社 API 密钥或浏览器登录不能保证每篇文章都可访问。
字段、权限与填写步骤见[个人配置](config/README.zh-CN.md)。

私有 YAML 文件权限保持 0600，并放在实验数据和运行环境挂载目录之外。
模型认证单独配置。凭据、真实 Profile、研究数据和运行记录不进入 Git。

## 4. 准备并审阅课题

先将[中文模板](profiles/templates/multi-expert-zh/)复制为新的私有 Profile，
按[个人配置指南](config/README.zh-CN.md)填写六个 YAML 文件。
将目标、数据和运行环境占位路径替换为真实绝对路径。目标仓库必须干净；
实验使用私有工作树，原始仓库和获准数据保持只读。

然后在仓库目录启动 Claude Code，输入：

```text
/autoresearch init
```

选择刚准备的本地 Profile。init 也支持从[中文或英文模板](profiles/README.zh-CN.md)
引导创建新的配置，不会覆盖已有本地 Profile。

启动前核对：

- 研究目标、完成标准与专家职责。
- 实际生效模型、认证和文献访问。
- `methodology.yaml.budget` 中的时间、费用和并发限制。
- CPU/GPU 权限、运行环境及共享 GPU 池。
- 通知接收者和机构浏览器连接。

费用额度耗尽后停止派发新调用，但在途响应报告的费用可能超过剩余额度。
请为重试、分析和最终报告预留空间；未知费用单独保留。Agent 与实验时限相互独立。
完整审阅流程见[初始化指南](docs/guides/initialization.zh-CN.md)。

模板默认使用 CPU。GPU 任务需要明确的设备 UUID 和适用的 CUDA 环境。
初始化会审阅托管池的创建、复用或维护方案，再纳入 **Start now**。
**Prepare only** 不操作服务。详见 [GPU 资源](docs/guides/gpu-resource-planning.zh-CN.md)。

## 5. 启动与监控

选择 **Prepare only** 保存已确认的配置，或选择 **Start now** 启动研究。
`manage_host.py` 通过 user-systemd 启动 Host，检查 `control.sock` 就绪后提交运行，
无需另开 Host 终端。就绪等待默认 30 秒，可设置为大于零且不超过 300 秒。

初始化返回 Run ID、状态目录和监控命令。使用 `/autoresearch-monitor` 可重新打开
观察界面。退出监控不停止研究；End run 请求取消并等待清理。详见[使用指南](USAGE.zh-CN.md)。

若 user-systemd 显示 `Linger=no`，最后一个登录会话退出后，后台工作可能停止；
初始化不会修改该设置。运行中发生连接或供应商故障时，使用
[API 恢复](docs/guides/provider-recovery.zh-CN.md)，不要修改数据库或替换运行时。

## 可选配置

- **通知**：需要服务器 HTTP CONNECT 代理时，在初始化前设置 `notify.yaml`。
  它与模型、文献代理相互独立。详见[通知指南](docs/guides/notifications.zh-CN.md)。
- **机构访问**：初始化时选择浏览器模式。Windows 客户端需先配置用户级 SSH 密钥
  和免交互登录，再执行生成的 Setup 命令。详见[机构全文访问](docs/guides/institutional-access.zh-CN.md)。
- **版本提醒**：`hypoloom version-check` 检查配置的远端分支；`bootstrap paths --offline`
  跳过远端查询，不自动安装更新。详见[版本提醒](docs/guides/version-check.zh-CN.md)。
- **自定义控制 Python**：验证 Python、SQLite 和依赖后，在启动 Claude Code 前设置：

```bash
export HYPOLOOM_RUNTIME_FAMILY=hypoloom
export HYPOLOOM_PYTHON=/absolute/control-environment/bin/python
export HYPOLOOM_HOOK_PYTHON="$HYPOLOOM_PYTHON"
claude
```

已有运行保留原来的运行时和配置。更新仓库适用于新运行，不支持跨版本运行迁移。
