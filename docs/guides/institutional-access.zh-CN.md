# 机构全文访问

[English](institutional-access.md) · 简体中文

公开检索和PDF下载照常运行。需要学校订阅时，Host复用独立Chromium浏览器中的有效登录。
连接与端口随本次运行结束释放；专用浏览器目录保留，后续运行复用仍有效的登录。
学校要求重新认证或MFA时仍需人工操作；全文就绪也不代表Agent已经读过它。

## 集中准备与研究期间的处理

没有研究运行或待下载论文时，也可先准备常用平台。Windows 终端用户建议使用下文的
统一 Init 入口，在同一流程中启动连接并进入服务器初始化，无须单独开启助手终端。
已有连接时也可在服务器独立执行以下命令；原生 Linux 桌面改用 `local`：

```bash
python -m hypoloom institutional prepare --list
python -m hypoloom institutional prepare \
  --institutional-browser-mode client
```

目录优先展示四个常用平台，入口来自现有平台目录，无须逐个输入网址：

| 平台 | 默认集中准备 | 说明 |
| --- | --- | --- |
| IEEE | 是 | 复用有效会话，必要时在浏览器认证。 |
| ACM | 否，可选 | 公开全文优先；不将登录作为读取论文的前提。 |
| Elsevier / ScienceDirect | 是 | 参数可使用 `ScienceDirect` 或 `Elsevier`。 |
| Springer | 是 | 复用有效会话，必要时在浏览器认证。 |

不指定平台/样本时采用上述三个默认准备平台；已设为 `public_only` 的平台不默认准备。
重复 `--provider <平台>` 替换默认名单，可加入 ACM 或目录中其他平台；`--provider none`
不连接浏览器。只传 `--sample` 时仍只处理样本所属平台。准备名单不限制科研检索范围。
ACM 的默认来自[2026 年开放获取公告](https://www.acm.org/about-acm/acm-pres-on-oa-transition-and-dl-changes)，
不代表每次程序下载都成功，也不关闭已有浏览器回退。

整个批次复用一个浏览器目录及可选的 `--institutional-library-url` 学校交互入口；
不提供学校入口时打开出版社首页。学校选择、SSO/MFA 仍在浏览器内完成，不保存账号密码。
可以输入该平台有权限的 DOI 或规范论文 URL 验证样本，也可传重复的 `--sample`。
提供样本时先检查当前会话，全部样本可读就跳过登录提示；公开样本可读不证明机构认证。
需要人工处理时，终端支持 `s` 跳过、`p` 仅公开获取、`q` 结束本批；回车完成或重新检查
原样本。没有样本的回车仅表示准备操作结束，结果仍为 `not_checked`，不显示已登录。
个别平台失败后继续其他平台；仍有失败的样本时命令返回非零，不阻止后续研究启动。
`--non-interactive --sample <DOI或URL>` 只验证现有会话，不等待终端输入。
`--list` 只列目录，不连接浏览器。每批最多八个平台，结束后可继续下一批。
初始化审阅通过 `institutional_preparation` 展示名单，`institutional_preparation_argv`
给出完整命令。init 用重复的 `--institutional-prepare-provider` 自定义名单，用 `none`
跳过集中准备；这与关闭机构功能的 `off` 不同。只有明确选择准备才打开浏览器。
最终 `preparation_summary` 区分未验证、跳过、样本可读、需要认证/验证码或不可访问；
`provider_modes` / `provider_mode_argv` 给出后续运行的偏好。init 按平台替换原值后重新
审阅；独立使用命令时需自行带到后续启动参数中。结果不自动持久化或更改现有运行。
准备不创建 run、Attempt、Profile 或科研归档，不调用模型。

init 生成的准备命令使用 `--agent-session`：先在默认 30 秒连接期限内验证实际浏览器
连接，成功后才打开平台。失败后进入统一的连接等待窗口，各平台保持 `not_checked`，
不会分别空等 120 秒。程序通过同一浏览器工作线程，在每次检查结束后间隔 5 秒自动
重检；每次检查受连接超时和窗口剩余时间约束，成功后自动进入平台，无须另行回复。
`waiting_for_client` 只说明未连接，不能据此判定助手未安装或学校无权限。
进度立即输出；agent 通过临时私有控制通道查询并传回你的选择，不依赖后台任务 stdin。
你在 init 对话中选择完成、样本验证、跳过、仅公开或结束；登录、验证码与 MFA 在
专用浏览器中操作。等待回答沿用 `--institutional-manual-seconds` 的有限期限。
该期限在进入连接等待时就开始计算；进入平台回答阶段后使用该阶段自己的期限。
查询状态和“继续连接”都不会延长期限；后者只提前下一次检查，不叠加并发检查。
程序输出实际剩余秒数，不让助手自行推算。连接等待超时的摘要保留最后一次检查
结果及重新准备命令。
超时/结束会清理连接与临时目录，已完成的平台结果保留在本次输出中，研究启动身份不变。
独立交互命令仍可在终端使用；输入被关闭时明确返回 `input_required` 并清理资源。

手动诊断对话准备时，使用首次输出的 `status_argv` / `answer_argv`；例如：

```bash
python -m hypoloom institutional preparation status --state-root /path/to/temporary-preparation
python -m hypoloom institutional preparation watch --state-root /path/to/temporary-preparation
python -m hypoloom institutional preparation complete --state-root /path/to/temporary-preparation --provider IEEE
python -m hypoloom institutional preparation verify --state-root /path/to/temporary-preparation --provider IEEE --sample 10.1109/example
python -m hypoloom institutional preparation retry_connection --state-root /path/to/temporary-preparation
python -m hypoloom institutional preparation finish --state-root /path/to/temporary-preparation
```

正常 init 使用程序给出的 `instructions_argv`，原样展示操作说明、完整 Windows
代码块和固定回复。连接阶段回复 **继续连接 / 跳过准备**；平台阶段回复 **完成 /
验证样本 / 跳过此平台 / 仅公开 / 结束准备**。验证样本另提供 DOI 或论文 URL。
客户端接入后由程序自动检测。程序提供的 `watch_argv` 最多等待 30 秒，到需人工操作
的阶段或批次结束便返回当前说明；只观察、不代答。CC 使用这个命令，不自行拼接
sleep 或轮询循环。
助手将原话交给 `reply_argv --text`，程序按当前阶段和平台校验；后台任务完成通知
不是用户回答。批次结束后会明确提示 **重新准备**，先读取原摘要并保留已完成的平台
和模式，再原样执行摘要中的 `remaining_preparation_argv`；其中包含失败及未回答的平台，
保留连接参数、已选模式和原命令提供的样本。“完成”但无样本仍属未验证，不重复准备。
无需新开 CC 会话或创建科研运行。

所选平台超时时，init 应提供 **重新准备** 或 **接受未验证状态，继续检查启动配置**，
由用户决定。准备失败不阻止科研启动，也不代表成功。如果旧版本摘要仍有失败项却输出
空重试数组，助手应使用原只读 bootstrap 检查为这些平台重新生成准备命令，保留其他
配置和启动身份。保持专用浏览器打开即可。中间的 `browser_unavailable` /
`fetch_unavailable` 输出安全错误码；只有最终 `acquisition_timeout` 无法确定底层原因。
连接预检也返回固定错误码：`cdp_timeout`、`cdp_connection_refused`、
`cdp_connection_closed`、`cdp_authorization_failed`、`cdp_endpoint_unavailable`、
`cdp_context_unavailable`、`browser_driver_unavailable` 或 `cdp_connection_failed`。
它们表示浏览器控制连接故障，不代表机构登录或订阅失败；不输出原始异常和私有地址。

`complete` 没有样本时仍是未验证；`skip` / `public_only` 也须带当前平台。
这些命令只用于仍存活的临时准备进程，不修改科研运行；结束后读取后台命令最终摘要。

运行后通过 TUI 的 **i** 面板或带文献操作授权的 Web 面板管理；空待办也显示完整平台目录。
选择“打开登录”“验证样本”或“检查并重试”。不要同时运行独立准备命令与 Host 来管理同一浏览器。
“样本全文已验证”仅证明此篇在显示时间可读，样本字节检查后释放；不代表整个平台永久有权，
不代表 Agent 已读。重连同一浏览器可以复用有效会话，但学校、出版社和浏览器决定过期时间。

默认先走公开全文及当前配置的准确 DOI 开放全文备选，再尝试浏览器现有会话。
后台遇到登录、验证码或访问提示即结束本次等待，研究继续其他来源。
自动机构获取从入队起最多 **120 秒**，包括排队和重试；人工点击登录／检查才开启
该平台 **600 秒**窗口。后来出现的论文不延长窗口；超时论文需要手动重试。
无权限只结束该篇，不自动判断全平台无权。浏览器断线时在剩余期限内退避，期限结束后返回缺口。

| 操作 | 影响 |
| --- | --- |
| 跳过此篇／恢复此篇 | 当前运行跨专家生效；已知 DOI 按文献身份，否则按链接。恢复允许新请求，不后台补写结论。 |
| 仅公开途径 `public_only` | 关闭该平台自动机构访问，公开检索、OA 回退、已接纳原文及合法手动导入仍可用。 |
| 启用机构访问 `auto` | 允许再次使用已有会话；需要登录时再主动点击登录／检查。 |
| 导入 PDF | 选择论文并提供 Host 上绝对路径；仍须通过原身份、字节与来源检查。 |

跳过和平台设置复用当前运行控制日志，同版本 Host 重启后恢复；人工窗口不重放。
这些控制从 lifecycle v11 的新运行开始支持，旧运行仍使用其原冻结版本。
启动参数可供后续新运行重复使用，不修改科研 Profile：

```text
--institutional-provider-mode IEEE=auto
--institutional-provider-mode Springer=public_only
--institutional-acquisition-seconds 120
--institutional-manual-seconds 600
```

平台设置可重复，冲突值会被拒绝；期限分别允许 30–600 和 60–1800 秒，不能无限等待。
公开/OA 阶段继续使用原 HTTP、PDF 工作槽和工作窗口。已知 DOI 时，动态文档工具复用
当前选择的 Semantic Scholar 或 Unpaywall；不会因进入机构队列重复整批检索。
OA 作者稿保留实际来源和版本限制，不声称为出版社定稿。另一篇替代论文必须使用自己的引用。

## init 自动准备连接

新运行的 `bootstrap inspect/launch`、`serve` 和独立 `institutional prepare`
通过 `--institutional-browser-mode` 选择浏览器位置：

| mode | 浏览器位置 | 生命周期 |
| --- | --- | --- |
| `off` | 不使用机构浏览器 | 不创建机构连接；保留公开/OA，关闭机构面板操作。 |
| `client` | Windows Edge/Chrome | Windows 脚本发现活跃运行，每个运行建立独立的私有 SSH 转发。 |
| `local` | 原生 Linux 桌面 | 专用可见 Chrome/Chromium 通过进程管道控制，不开放 CDP TCP 端口。 |
| `server` | 无桌面的 Linux 服务器 | 每次运行使用独立容器、私有 CDP 和 GUI 转发，要求镜像已安装。 |
| `existing` | 明确指定的外部回环 CDP | 项目建立受保护的运行连接；外部浏览器仍由原管理者负责。 |

init 未选择时，inspect 的 `missing` 会包含 `institutional_browser_mode`，对话按代码
返回的选项询问；已有明确选择不重复询问。off 是显式回答，不是省略参数时自动代选。
完整流程见[初始化指南](initialization.zh-CN.md#聚合检查与一次启动)。off 不提供登录准备
命令，也不能与机构端点／目录等选项混用。需要机构面板导入 PDF 时，保留启用模式并
按平台选择 `public_only`；准备登录时选择一种启用模式。

受管监听默认绑定 **端口 0**，读取 OS 实际分配结果，与 Web 转发采用同一原则。
client/server/existing 可用 `--institutional-port N` 明确固定运行端口；冲突时拒绝接管，
只使对应连接暂不可用，不影响其他运行。端口释放后在原获取期限内可重试。
只有 existing 接受 `--institutional-browser-url` 和 `--institutional-browser-ui-url`。
只传旧 URL、不选 mode 时保留原来的非受管连接行为。

预览没有副作用。`institutional_connection` 展示策略，`institutional_connection_argv`
查询活跃连接，`institutional_preparation_argv` 用于独立集中准备。
运行创建后由 Host 分配其资源；浏览器暂未连接时沿用有限获取期限和回退策略，
不阻止其他科研工作，也不自动改选其他浏览器位置。

完成、失败、取消或停止 Host 时释放所属监听、隧道、浏览器／容器资源。
暂停参与者、等待人工和关闭 TUI/Web 不等于终止。不同运行的端点独立；同版本恢复
按原冻结策略创建新临时地址，终态运行不重建。实际端口、临时令牌不写入冻结配置。
现有研究运行不迁移。

客户端 socket 目录及容器使用以管道 EOF 为退出条件的用户级 systemd 服务；
Host 被 SIGKILL 后仍会收尾。原生浏览器随所属进程管道关闭。SSH watch 轮询私有
Host 控制接口，并核验实际 CDP 浏览器身份。在线终止按轮询间隔收尾；网络分区使用
SSH 存活检测（15 秒、连续三次），不能承诺瞬时释放。原生浏览器已在执行的操作在
原有有限期限内结束后正常关闭。Docker 收尾需要 daemon 可响应，失败会明确报告。

登录目录由浏览器保存，运行清理不删除它。local/server 同一目录同时只允许一个运行
使用；并发时选择不同的专用目录。Windows 的同一专用浏览器可以经独立隧道服务多个
运行，结束服务器运行不关闭这个用户浏览器。

```bash
python -m hypoloom institutional prepare --institutional-browser-mode local --provider IEEE --provider Springer
# 服务器上使用 Windows 连接脚本时：
python -m hypoloom institutional prepare --institutional-browser-mode client --provider Springer
python -m hypoloom institutional connections --runs-root /path/to/Hypoloom/state
```

集中准备在 `<repository>/state` 下创建临时私有控制目录，结束后清理连接；
Windows 的 `-RunsRoot` 要指向该父目录。它不创建科研运行或归档。
在其他工作目录执行时显式传 `--repository`。

local 需要实际 Linux 桌面用户的 Chrome/Chromium 和 Playwright；init 只把所选的
X11/Wayland 会话变量传给受管 Host。可见窗口须在对应机器确认。
默认目录为 `$XDG_DATA_HOME/hypoloom/Institutional-<程序名>`，未设 XDG 时在
`~/.local/share` 下。可用绝对路径 `--institutional-browser-profile` 和
`--institutional-browser-executable` 指定。目录须属当前用户且权限为 0700；
禁止日常默认目录，不删除浏览器锁，不强占忙碌目录。

client/server 的清理服务要求 Linux 用户级 systemd。server 仅从
`--institutional-compose-file`（默认 `<repository>/config/institutional-browser.compose.yaml`）
读取镜像及支持的环境变量，使用用户私有登录目录，不采用静态发布端口和卷。
不自动拉镜像、安装依赖或修改 Docker 代理；镜像需提供 `nc`。容器内部服务仅绑定回环，
不发布 Docker 端口。GUI 使用本次运行的临时认证，在服务器终端读取：

```bash
python -m hypoloom institutional connections --state-root /path/to/run --include-ui-credentials
```

将返回的 GUI 端口通过 SSH `-L` 转发到本机，输入本次 GUI 访问凭据；学校账号只在
浏览器内部输入。只读 Web 面板不提供 GUI 密码。
`--institutional-connect-timeout` 默认 30 秒，允许 3–120 秒。连通不等于学校有订阅。

旧的 `institutional connect status|start|stop` 保留为 local/existing/server 的手动诊断命令，
资源由操作员管理。新运行不再自动调用它，也不把它的常驻资源算作运行级连接。

### Windows 终端统一 Init 入口

**已经在 CC 中进行 init 时**，使用它原样展示的程序生成代码块。代码会在 Windows
询问 SSH 目标、自行取得脚本并执行 `Connect`，启动任务后返回原会话。无需本机已有
仓库，也无需自己拼接路径；不要替换成下方会新开会话的 `Init`。看到
`client_discovery_started` 后回到原会话查看进展，程序会自动检测实际连接；
需要提前检查时可回复 **继续连接**。
如原批次已结束，回复 **重新准备**。固定代码块由 bootstrap 与准备进程共用的
`client_entry` 生成，助手不应改写代码或计时说明。

完整粘贴代码块，包括开头的 `& {` 和最后的 `}`，不要复制终端里的 `PS>` / `>>`
提示符。它作为一条命令执行，下载或校验失败就结束，不覆盖已有入口，也不继续启动。
如果之前粘贴损坏的命令导致停在 `>>`，先按 Ctrl+C 取消，再粘贴完整代码块。
SSH 目标填写从这台 Windows 登录 Linux 的地址；`Read-Host` 后引号里的文字只是
问题，真正的目标要在提示后输入。`127.0.0.1` / `localhost` 指向 Windows 本机，
不是远端服务器。普通 SSH 能输入密码登录，并不代表后台免交互认证可用；先按下方
密钥/ssh-agent 步骤验证 `ssh -o BatchMode=yes <目标> exit` 返回 0，再执行接入。
Edge/Chrome 的本地版本响应只证明本地端口当时可用，服务器仍需验证实际连接。

若旧接入代码出现中文乱码，尤其把 `Write-Output` 也显示为正文，请使用更新版本
init 重新生成的完整代码块。新代码经 SSH 传输 Base64，再按原始字节写入入口，
避免本机代码页在下载阶段损坏脚本；只在最后指定 UTF-8 保存不能恢复已读坏的内容。
后台发现任务使用隐藏窗口。重新执行 `Connect` 会在严格核对用户、程序和全部连接
参数后，为旧任务更新窗口设置；正在运行的旧可见任务会重启，连接可能短暂中断。
任务已使用隐藏参数时不会因此重启。SSH 密钥和浏览器登录目录不变；需要学校认证
时浏览器仍会正常打开。`client_discovery_started` 只表示已请求任务启动，实际浏览器
连通仍以 init 的检查为准。
Connect 返回后可以关闭其 Windows PowerShell 窗口，隐藏发现任务会继续运行。
只有前台 `-Action Run` 诊断需要保持窗口打开；出现
`waiting_for_preparation_or_run` 表示没有活动连接目标，应同时保留服务器上的 CC
准备批次。这条等待提示不代表浏览器已连接成功。

Windows 计划任务可能把运行账户显示为短用户名，而当前会话显示为“计算机名\\用户名”。
新版通过 Windows 解析账户 SID 后核对归属。遇到旧版因此报出的
`scheduled_task_ownership_mismatch`，重新执行完整的 Base64 下载/Connect 代码块，
取得新版入口；无需改名或删除任务。真实账户、程序或连接参数不同仍会拒绝更新。

**从 Windows 开始一个新的 init 会话时**，才使用以下入口：

首次在 Windows 取得下面的项目入口；以后无需另开助手终端，也无需编辑助手配置。
使用你原有的 SSH 目标和服务器仓库位置，入口推导标准的 Python 与 state 路径：

```powershell
$researchServer = 'research-server' # 原有 user@host 或 SSH 别名
$researchRepository = '/path/to/Hypoloom'
$researchConnector = Join-Path $env:LOCALAPPDATA 'Hypoloom\institutional-connect.ps1'
New-Item -ItemType Directory -Force (Split-Path $researchConnector) | Out-Null
scp "${researchServer}:${researchRepository}/hypoloom/connection_assets/institutional-connect.ps1" $researchConnector
pwsh -NoProfile -File $researchConnector -Action Init -Server $researchServer -Repository $researchRepository
```

入口先检查 SSH 认证、服务器 Python 和 Claude 命令，再安装或复用属于当前用户的
连接发现计划任务并立即启动，最后进入服务器 `/autoresearch init`。该任务也在用户
登录时启动，为后续研究保留连接能力；没有活跃准备/运行时不打开浏览器。以后也可以
继续普通 SSH 登录并调用 init，复用已启动的任务。非标准安装可显式提供
`-PythonExecutable` / `-RunsRoot`；已有任务归属或参数不匹配时不会覆盖。

首次本机入口、PowerShell 7.2+、有效 SSH 认证是实际前提。纯服务器 init 无法在没有
本地执行通道的 Windows 上安装或启动程序；它会在连接步骤给出实际状态和重试/结束
选择。机构登录、验证码及 MFA 始终由本人完成。下方保留 SSH 准备与手动诊断命令。

### 客户端前提与手动诊断

将 `hypoloom/connection_assets/institutional-connect.ps1` 复制到 Windows 固定位置。
要求 PowerShell 7.2+，Windows 不需要安装项目或 Python。SSH 目标为已有 `user@host`
或 SSH 别名，先确认主机密钥并配置 SSH key／ssh-agent。脚本使用 BatchMode，
不保存密码。Linux SSH 服务须允许远程 Unix socket 转发；脚本通过下方三个明确的
服务器绝对路径调用项目 CLI。

如果现在 SSH 仍要求服务器密码，请先在 **Windows** 配置一次密钥。在管理员 PowerShell 中
启用 Windows 的 ssh-agent：

```powershell
Set-Service ssh-agent -StartupType Automatic
Start-Service ssh-agent
```

随后回到桌面用户的普通 PowerShell。生成时按提示设置密钥口令，只把公钥发到 Linux
服务器；服务器密码仅在 SSH 自己的提示中输入，不写入脚本或发到对话中：

```powershell
$researchServer = 'research-server' # 替换为自己的 user@host 或 SSH 别名
$researchKey = Join-Path $env:USERPROFILE '.ssh\hypoloom_ed25519'
New-Item -ItemType Directory -Force (Split-Path $researchKey) | Out-Null
if (!(Test-Path $researchKey)) { ssh-keygen -t ed25519 -f $researchKey }
ssh-add $researchKey
Get-Content "$researchKey.pub" | ssh $researchServer 'umask 077; mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'
ssh -o BatchMode=yes $researchServer exit
$LASTEXITCODE # 自动重连前应输出 0
```

私钥留在 Windows，系统配置可参考
[Windows OpenSSH 密钥管理](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement)。
这一步只准备 SSH 认证，出版社登录仍在专用浏览器里完成。

```powershell
$researchServer = 'research-server' # user@host or SSH alias
$researchConnector = Join-Path $env:LOCALAPPDATA 'Hypoloom\institutional-connect.ps1'
New-Item -ItemType Directory -Force (Split-Path $researchConnector) | Out-Null
scp "${researchServer}:/path/to/Hypoloom/hypoloom/connection_assets/institutional-connect.ps1" $researchConnector
$researchConnection = @{
    Server = $researchServer
    Repository = '/path/to/Hypoloom'
    PythonExecutable = '/path/to/Hypoloom/.conda/envs/hypoloom/bin/python'
    RunsRoot = '/path/to/Hypoloom/state'
}
& $researchConnector @researchConnection -Action Run
# Optional current-user logon task; repeat these same paths and options:
& $researchConnector @researchConnection -Action Install
& $researchConnector @researchConnection -Action Status
& $researchConnector @researchConnection -Action Stop
& $researchConnector @researchConnection -Action Uninstall
```

Connect/Init 在启动发现任务前准备目录权限，失败会直接显示在当前终端。对于当前
用户拥有的默认专用目录，额外的**继承 Allow**（例如其他本地组的读取权限）会在
保护父目录继承、保留既有合法主体及 Deny 后移除，再以原私有校验重新读取确认。
不修改父目录、组成员或浏览器文件内容；子项按 Windows 正常 ACL 继承传播处理，
不递归重置独立保护的子项权限。新默认目录也走同一检查。自定义目录、显式额外
授权、不同所有者及重定向目录不自动修复。旧入口因继承权限报
`browser_profile_must_be_private` 时，重新执行更新后的完整 Base64 下载/Connect
代码块；修复成功会显示 `browser_profile_permissions_repaired; login_data_preserved`。

最初的 `c254d5f` 修复可能只把额外授权变成显式条目而未删除；纠正版在内存中准备
保留规则后一次写回。如果前后 ACL 输出确认发生了这次旧版部分修复，只针对已确认
的残留账户，在该专用目录执行 `icacls <专用目录> /remove:g <已确认本地组>`，再接入。
连接器仍拒绝未知显式授权，重复下载不会自动删除它。错误码现区分
`browser_profile_explicit_access_requires_manual_permissions`（显式额外授权）、
`browser_profile_custom_path_requires_manual_permissions`（自定义目录）、
`browser_profile_permissions_write_failed`（写入失败）和
`browser_profile_permissions_not_applied`（读回不合格）。

脚本优先查找 Edge，再查找 Chrome；默认本机端口为 0，由浏览器分配。
使用 `%LOCALAPPDATA%\Hypoloom\Institutional-msedge` 或 `Institutional-chrome`，
可通过 `-BrowserExecutable`／`-BrowserProfile` 指定原来完成登录的专用目录。
不导出 Cookie，不复制登录凭据；目录权限限于当前用户、SYSTEM 和管理员。
已有相同程序、目录及用户的专用浏览器可复用，包括手动启动的 9222 端点。

脚本仅对活跃运行建立 SSH 转发，没有运行时不启动浏览器、不占服务器研究端口。
服务端目标为每次运行的私有 Unix socket，无需 `-RemotePort`。
断线后先核验运行仍活跃，并清理确认没有监听者的旧 socket 路径；不接管现有监听。
每条转发最多重试三次，间隔 30 秒；发现连接失败也有有限重试。
Run 保持前台，Ctrl+C 只清理自身 SSH 子进程。运行终结或 Host 失联时远程 watch
退出，转发随 SSH 关闭；终态运行不会因自动重连而重新出现。

Install 从下次该用户登录开始生效，当前会话可直接 Run。计划任务不覆盖归属不匹配的
同名任务；更新安装参数前，用原参数 Uninstall。旧版固定端口任务也应由操作员停止，
本脚本不会接管它们。保留登录目录；出版社或学校使会话过期、要求 MFA 时仍需本人登录。
电脑必须保持开机联网，服务器不能远程唤醒或代替登录。

原生可见桌面、Windows 原生计划任务与实际镜像部署仍需对应机器验收；
自动化范围见[验证记录](../qualification.md#run-scoped-institutional-connections-2026-10-02)。

## 配置新运行

在Linux Host所用的Python环境、Hypoloom仓库根目录安装可选依赖；PDF解析仍需要项目原有的Poppler：

```bash
python -m pip install -e '.[institutional]'
command -v pdftotext
command -v pdfinfo
```

## 手动常驻服务器浏览器（兼容模式）

复用[jlesage/docker-chromium](https://github.com/jlesage/docker-chromium/tree/v26.09.1)：
它提供网页远程桌面、CDP接口和持久配置目录。Hypoloom继续使用现有文献下载和校验链路。
此模式明确允许登录状态保存在**服务器浏览器**，替代笔记本模式的存储位置；不把Cookie
导入Hypoloom数据库、Profile、提示词、通知或代码仓库。

在Linux服务器的项目根目录启动独立浏览器服务（只需部署一次）：

```bash
docker compose -f config/institutional-browser.compose.yaml up -d
docker compose -f config/institutional-browser.compose.yaml ps
curl --fail http://127.0.0.1:9222/json/version
```

先确认Docker可用，以及5800、9222未被其他服务或旧反向隧道占用。不要直接停止不明进程。
两个端口只发布到服务器回环地址；5800用于查看浏览器，9222供Host控制，均不开放到公网。
镜像版本固定为`v26.09.1`；升级属于单独维护操作，需重新检查登录与下载。

首次登录时，在你的电脑执行以下命令，只转发远程操作界面：

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:5800:127.0.0.1:5800 user@SERVER
```

打开`http://127.0.0.1:5800/`，看到的是**服务器上的Chromium**。在其中进入学校图书馆或
Nature等平台，自行完成机构登录/MFA，并实际打开一篇订阅全文确认权限。关掉这个网页或
SSH连接不会关闭服务器浏览器。无需笔记本Chrome调试模式，也无需9222反向隧道。

为每个新run的`serve`或`bootstrap launch`加上以下参数；固定不变，不按run分配端口：

```text
--institutional-browser-url http://127.0.0.1:9222
--institutional-browser-ui-url http://127.0.0.1:5800
```

可再传`--institutional-library-url https://你的稳定图书馆入口/`。TUI和Web会显示服务器
浏览器指引；`i`面板的“打开登录”打开服务器标签，不收集账号。Host关停只关闭自己创建的
标签，浏览器容器和会话继续存在。后续run连回同一浏览器，复用各平台仍有效的会话。

登录状态由浏览器保存到Docker卷`hypoloom-institutional-browser_browser-profile`，挂在
容器的`/config`；与代码、研究状态和导出包分开。不要把卷加入Git或研究成果包，也不要
用`down -v`或清理该卷来做日常重启。`restart: unless-stopped`让容器在Docker恢复后继续
运行，`KEEP_APP_RUNNING`负责浏览器进程退出后的重启。持久Cookie可跨浏览器重启复用；
临时会话或被学校注销的登录不能保证保留。

服务器必须有自己的学校访问途径：校内出口、服务器VPN/代理，或支持校外登录的机构SSO。
笔记本能访问并不证明服务器能访问；Nature/学校若按IP或设备限制授权，仍须在服务器浏览器
验证。一个平台的登录也不代表其他出版社自动获得订阅权限。代理改写域名的限制见下文。

部署验收：登录后读论文A；断开查看连接，再由新Host读取同平台论文B；只有会话失效时才
重新打开5800登录。代码回归覆盖合成会话复用，不能替代这一步真实机构验收。

## 手动笔记本浏览器（兼容模式）

Windows笔记本用独立配置目录启动一个可见浏览器，再在其中完成学校登录。
以下命令查找常见的Chrome安装位置，找不到则使用Edge；两者分别使用自己的专用目录。
[Chrome的远程调试要求独立数据目录](https://developer.chrome.com/blog/remote-debugging-port)，
[Edge也支持同样的CDP接口](https://learn.microsoft.com/en-us/microsoft-edge/devtools/protocol/)。PowerShell示例：

```powershell
$researchBrowser = @(
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $researchBrowser) { throw 'Chrome/Edge not found; set the actual executable path.' }
$researchProfile = Join-Path $env:LOCALAPPDATA ("Hypoloom\Institutional-" + [IO.Path]::GetFileNameWithoutExtension($researchBrowser))
& $researchBrowser '--remote-debugging-address=127.0.0.1' '--remote-debugging-port=9222' "--user-data-dir=$researchProfile" 'about:blank'
Start-Sleep -Seconds 3
(Invoke-RestMethod 'http://127.0.0.1:9222/json/version').Browser
```

最后一行应显示浏览器版本，再继续启动SSH隧道。若采用自定义安装位置，将
`$researchBrowser`设为实际可执行文件路径。浏览器配置目录由你保管，重开相同目录
可复用仍有效的登录。Hypoloom不导出Cookie或storage文件。

如果研究在Linux服务器、浏览器在Windows，在笔记本另开PowerShell，替换SSH目标后保持以下连接运行：

```powershell
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -R 127.0.0.1:9222:127.0.0.1:9222 user@SERVER
```

`user@SERVER`填写平时使用的服务器地址或SSH别名，不是密码。SSH若要求认证，在
SSH终端内输入即可；保持该终端运行。

服务器通过SSH回环端口连接笔记本浏览器。CDP端口具有浏览器控制权限，只绑定回环地址；不要把它开放到公网。同机使用则不需要隧道。

在新运行的`python -m hypoloom serve ...`或`python -m hypoloom.bootstrap launch ...`命令末尾添加：

```text
--institutional-browser-url http://127.0.0.1:9222
```

可选添加`--institutional-library-url https://你的图书馆入口/`。这是稳定的公开登录入口，不是登录后的临时回调链接。未设置时直接打开所选出版社论文页。不要修改旧运行的冻结配置；为新运行配置此功能。

`https://学校域名/idp/shibboleth`一类SAML实体／元数据地址可能返回XML，并非可操作的
登录页，不应填作图书馆入口。此时省略`--institutional-library-url`，从出版社论文页的
**Institutional Sign In**选择学校，由出版社发起SSO登录。

Literature MAIN要读取补回的全文，其冻结的`methodology.literature.tools`须允许`web_fetch`。`read_papers`是额外模型阅读能力，不是浏览器登录的前提。

## 运行中怎么用

1. TUI显示“机构全文：N篇待处理”。按`i`查看按出版社合并的列表。
2. 选平台，点“打开登录”。在所选模式的浏览器中自行选择学校并完成SSO/MFA；服务器模式通过5800查看。若入口只停在图书馆首页，在该标签内进入列表中的论文。
3. 返回TUI继续观察即可。Host约每30秒检查等待中的标签，不反复跳转登录页；也可点“检查并重试”。同一浏览器的有效会话可用于后续论文，学校要求重新验证时仍需人工操作。
4. 全文通过身份和内容检查后显示“全文已就绪”。Agent在后续`web_fetch(mode="document", url=...)`中接收原文，进入现有`/papers`目录和归档；完成的文献结论不会被后台下载改写。

“当前机构无权限”“未找到全文”“论文对应关系未确认”不会自动无限重试。可换有效访问途径后手动重试，或使用PDF导入：先自行下载合法可访问的PDF、传到Host，选择对应论文并填写**Host上的绝对路径**。导入也会校验PDF、可读文本及DOI/标题对应关系；无法确认则不纳入证据。没有DOI、也没有可靠标题的请求可能需要先改用DOI链接。

Web面板与TUI等价：在TUI的`i`面板点“Web面板链接”得到带操作员授权的观察链接（只读链接本身不能触发动作），或以`python -m hypoloom.web_observer --allow-literature-control`独立启动。在Workbench的“机构全文”子页按出版社查看待处理论文，触发同样的“打开登录/检查并重试/导入PDF”动作；登录和验证码仍然只在你自己的浏览器页面完成，面板不收集账号信息。

## 旧的固定端口与常驻连接（仅兼容模式）

本节仅适用于不传 mode 的旧 URL 接口；新运行请优先使用上面的 client 模式。
此手动模式的浏览器和SSH隧道按用户会话复用，不需要每个run重建。约定使用9222；每次新Host都传
`--institutional-browser-url http://127.0.0.1:9222`。代码不会为这个参数重新分配端口。
Web面板的HTTP端口可以随机分配，与Chrome的CDP端口无关。若9222被占用，统一修改
Chrome、隧道远端和Host配置；不要跟随Web面板地址修改浏览器端口。

需要Windows登录后常驻时，可在“任务计划程序”创建两个任务，触发器均为“用户登录时”：

- 浏览器任务：程序使用上文`$researchBrowser`的实际值，目录使用`$researchProfile`的实际值；Chrome示例参数为
  `--remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="C:\Users\你的用户名\AppData\Local\Hypoloom\Institutional-chrome" about:blank`。
  Edge使用`Institutional-msedge`目录。保持与你手动登录时相同的程序和目录，选择“仅当用户登录时运行”，让登录窗口可见。
- 隧道任务：程序为`C:\Windows\System32\OpenSSH\ssh.exe`，参数为
  `-N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -R 127.0.0.1:9222:127.0.0.1:9222 user@SERVER`。
  先手动确认SSH主机密钥，并验证该用户通过SSH密钥/ssh-agent能免交互连接。
  可设置失败后每1分钟重试；已有实例运行时不启动新实例。不要在任务参数里保存密码。

笔记本需保持开机联网，睡眠、断网或退出登录会中断连接；恢复后请检查状态，已超时项手动重试。
学校使会话过期、要求MFA时仍需本人登录。常驻连接不保证永久免登录。
在笔记本模式中，Cookie和浏览器storage只在笔记本的专用浏览器配置目录，不复制到服务器。

## 状态和排障日志

`fetch_unavailable`表示加载失败；`network_changed`、`dns_failed`、
`connection_failed`、`navigation_timeout`、`request_blocked`、`http_error`、
`redirect_unresolved`帮助区分原因。它们不表示登录过期，也不会阻塞同平台其他论文。
`access_required`只表示页面提示需订阅或机构访问；只有实际登录表单或登录跳转才显示
`login_required`。DNS安全诊断包含目标hostname，不带查询参数或凭据。

DOMContentLoaded之后还会给异步跳转约8秒时间，适用于DOI、Elsevier linkinghub等中转页；
中转页本身不作为论文。瞬时导航错误在同次检查内最多重试一次；已到达的登录或验证页保持原样。
连续网络、超时、跳转未完成、HTTP 5xx（`http_server_error`）或429（`http_rate_limited`）
失败时，在剩余获取期限内按30、60、120、300秒退避；到期即结束，不会延长期限。429不会立即重试。
成功、退出瞬时失败状态或手动重试会重置退避。只有主动人工窗口内才检查登录/验证，不重新导航；
身份未确认、机构无权限仍不自动重试。PDF流式获取保留已有15秒中止计时。
尚未支持的镜像域名仍需专门适配或手动导入PDF；这次修复不保证真实订阅都能下载。
修复供新Host使用，既有冻结run不迁移、不热替换代码。

识别到`https://linkinghub.elsevier.com/retrieve/pii/S...`期刊PII入口时，可直达对应的
ScienceDirect文章页一次，不再依赖中转页的JS。改写丢弃查询参数和fragment，仍须通过DOI
身份校验；登录/验证页优先保留，不被这次跳转抢走。每次导航等待上限改为25秒，PDF获取仍为15秒。
明确显示`Redirecting`的页面，即使已到文章URL，也保持可重试，不能判成终态`no_full_text`。
真实文章页是否提供可读PDF、机构是否有权限，仍需分别验证。
ScienceDirect和IEEE可能在DOM完成后才插入PDF控件；没有全文/PDF或明确访问结果时，继续检查约8秒
再判`no_full_text`。中转页JS与直达操作竞争导致`ERR_ABORTED`时，在这个时间窗口内跟随落定，
不立即误报断网；最终仍校验来源和DOI。
PDF入口返回HTML或其他非PDF字节时，通常显示可重试的`pdf_response_not_pdf`。
既有阅读器处理后，若论文页同时明确显示付费墙，则返回`access_required`，结束后台等待，
不推断机构无订阅。HTTP服务错误、没有明确付费墙证据的非PDF响应仍可重试。
已核对身份的ScienceDirect文章遇到`/pdfft`中转页时，会在自己的标签页中执行页面跳转，
导航后最多继续观察25秒。登录或验证页会保留给用户，后续检查不反复刷新。
仅接收同篇文章的PDF入口或`pdf.sciencedirectassets.com`上PII一致的PDF，继续使用既有大小、
PDF解析和来源准入检查；签名参数不写入来源URL或诊断日志。
IEEE的HTML PDF阅读器在文章编号一致时也会继续解析；iframe必须指向IEEE同篇文章的
HTTPS PDF入口，下载内容仍须通过身份核对。文章页、DOI入口和有效机构会话复用已用
[一篇真实IEEE论文验证](../qualification.zh-CN.md#真实机构订阅与-ieee-阅读器修复2026-10-01)。
其他专用阅读器或原生下载仍可能需要手动导入。HTML不会被当成PDF，也不能据此推断机构无权限。

`i`面板和Web机构全文列表显示中文原因、原始原因码、上次检查时间及距下次检查的估计秒数；
串行队列可能延后实际检查时间。启用Profile的notification log
sink时，Host把`literature_access`事件写入原有`events.jsonl`，包含provider、规范论文URL、
`from`/`to`、`reason_code`和时间；不记录SSO跳转URL、Cookie、页面正文或PDF路径。
日志沿用既有输出目录和level，属于本地运行诊断，不发送Telegram/webhook，不改变研究事实
或通知游标。内存交接队列上限256条；Host异常退出可能丢失未落盘事件，写入失败或队列满
会在Host输出中提示，日志不能当作研究完成凭据。

单次Agent调用撞费用上限时，TUI和Web会显示`BUDGET LIMIT · error_max_budget_usd`；
其他最终失败显示`AGENT FINAL FAILURE`及失败码。即使run已结束仍显示，便于区分
正常完成和调用提前终止。额度属于冻结配置，新任务调整额度，旧运行不自动迁移。

## 开发验证

没有系统Chrome时，可以使用已安装的Playwright Chromium。Linux开发环境示例：

```bash
python -m playwright install chromium
export HYPOLOOM_TEST_CHROME="$(python -c 'from playwright.sync_api import sync_playwright; p = sync_playwright().start(); print(p.chromium.executable_path); p.stop()')"
HYPOLOOM_INSTITUTIONAL_BROWSER_TEST=1 python -m pytest tests/runtime/test_institutional_navigation.py tests/runtime/test_institutional_browser.py tests/runtime/test_literature_access_browser.py -q
```

浏览器安装由开发者显式执行；测试不会自行下载，使用独立无头配置目录和合成页面。

## 当前覆盖与限制

集中准备、运行级跳过／平台偏好和有界等待的设计依据见
[框架设计](../design/institutional-access-preparation.md)。

- 识别Nature、Springer、IEEE、ScienceDirect、ACM、Wiley、SAGE、Oxford和Taylor & Francis的常见论文域名及DOI前缀；复用通用PDF元数据和正向全文结构判断。这个名单表示已接入路由，**不表示每个平台已用真实订阅逐一验收**。
- 请求使用不带查询参数的规范论文链接或DOI。IEEE的`stamp.jsp?arnumber=...`等下载入口应改用`/document/...`论文页或DOI。
- PDF使用浏览器所在机器的网络与登录状态获取，再由Host解析。跨域PDF、学校WebVPN改写域名、专用阅读器、扫描版PDF和站点改版仍可能失败；此时保留缺口或导入PDF，不自动绕过验证码或访问控制。
- 每个Host最多32个待获取/缓存条目、64MiB缓存、8个出版社标签，单文件最多24MiB/2000页。等待队列和缓存仅在内存中；Host重启后由原有输入或工具请求重新建立。停止运行后丢弃待处理项和迟到结果；所有项清空或Host关闭时关闭Hypoloom创建的标签，保留你的浏览器和登录目录。
- 缺少标准“Abstract/Conclusions”章节标题不再导致可读PDF被舍弃；缺失的章节摘录保持空值。仅摘要、登录页、人机验证页不作为全文。

本实现复用[Playwright的CDP连接](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)，只支持Chromium系列。回归使用真实Chromium和合成出版社页面；已有IEEE与Springer真实订阅样本及Windows↔Linux链路验证，其他学校与出版社仍需在你的环境联调。参见[设计](../design/institutional-literature-access.md)和[验证记录](../qualification.zh-CN.md)。

## 检索拒绝与文档身份

公开检索验证已拒绝的候选不会自动进入机构浏览器。Host 从该精确文献包既有的下载
诊断推导限制，重试和浏览器缓存也不能绕过它；其他有效来源继续走原路径。机构登录
是单独的人工操作，不会覆盖对某篇论文来源的拒绝。

PDF 身份取自下载内容的第一物理页。落地页 DOI 只是待核对的预期值，不能充当 PDF
自身的身份。实际 DOI 冲突时，即使后面的参考文献出现预期 DOI 也会拒绝。没有可读
DOI 时，原有标题核对要求首个物理页包含所提供的标题；证据不足仍保持“身份未确认”。
因此扫描版或特殊排版可能需要进一步核验，不会被静默接受。
