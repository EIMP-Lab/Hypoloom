# 机构全文访问

[English](institutional-access.md) · 简体中文

公开检索和PDF下载照常运行。需要学校订阅时，Host复用独立Chromium浏览器中的有效登录。
推荐让浏览器常驻服务器：你远程登录一次，后续论文和新run继续复用，笔记本可关机。
学校要求重新认证或MFA时仍需人工操作；全文就绪也不代表Agent已经读过它。

## 集中准备与研究期间的处理

没有研究运行或待下载论文时，也可先准备常用平台。先建立上文所述的浏览器连接，
在服务器的项目环境中执行：

```bash
python -m hypoloom institutional prepare --list
python -m hypoloom institutional prepare \
  --institutional-browser-url http://127.0.0.1:9222 \
  --provider IEEE --provider Springer
```

命令依次打开平台入口，请在专用浏览器完成机构 SSO/MFA，再按终端提示继续。
可以输入该平台有权限的 DOI 或规范论文 URL 验证样本；也可传重复的 `--sample`。
`--non-interactive --sample <DOI或URL>` 只验证现有会话，不等待终端输入。
`--list` 只列目录，不连接浏览器。每批最多八个平台，结束后可继续下一批。
初始化审阅也提供 `institutional_preparation_argv`，只有明确选择准备才打开浏览器；
它不创建 run、Attempt、Profile 或科研归档，不调用模型。

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

先选择浏览器所在位置，再进行出版社登录。`bootstrap inspect/launch` 和独立
`institutional prepare` 共用以下可选参数；不传 mode 时保留原来的仅连接行为。

| mode | 使用场景 | 自动准备 |
| --- | --- | --- |
| `local` | 项目就在原生 Linux 桌面上 | 当前用户的 systemd 启动专用可见 Chrome/Chromium，直连回环 CDP；无需 SSH 或 Docker |
| `existing` | Windows／另一台 Linux 的浏览器，或自行管理的本机浏览器 | 检查现有 CDP，客户端自行维护隧道；不接管外部浏览器 |
| `server` | 没有桌面的服务器 | 启动仓库现有 Compose 服务，浏览器界面在回环 5800、CDP 在 9222；要求镜像已安装 |

```bash
python -m hypoloom institutional connect status --institutional-browser-mode local
python -m hypoloom institutional connect start --institutional-browser-mode local
python -m hypoloom institutional prepare --institutional-browser-mode local --provider IEEE --provider Springer
```

在 `/autoresearch init` 中明确选择上述位置，或给 `bootstrap inspect/launch` 增加
`--institutional-browser-mode local`（按实际情况改为 existing/server）。默认端点为
`http://127.0.0.1:9222`，可用 `--institutional-browser-url` 改写 local/existing 端口。
预览展示 `institutional_connection`、`institutional_connection_argv` 和
`institutional_preparation_argv`，不会启动浏览器、创建目录、登录或创建研究任务。
已选启动方式在 `launch --start` 创建 Host 前执行；连接失败返回
`institutional_connection_failed`。可重试、修改位置，或明确去掉机构连接参数后仅使用
公开/OA 来源；不会自动替你改变选择。只传旧 `--institutional-browser-url` 的行为兼容。

Linux 需在实际桌面用户的终端内执行，已有 Chrome/Chromium、用户级 systemd/busctl 和
项目的 Playwright 可选依赖。X11 使用当前 DISPLAY/XAUTHORITY；Wayland 检查当前用户的
会话 socket。SSH 中缺少桌面环境时不会借用其他用户权限。检查成功后请确认可见窗口；
CDP 可用不等于窗口可见。程序缺失、端口冲突、目录占用分别报错，不安装浏览器、不删锁。
专用目录默认在 `$XDG_DATA_HOME/hypoloom/Institutional-<浏览器程序名>`，XDG 未配置则位于
`~/.local/share/hypoloom/`。可指定绝对路径 `--institutional-browser-profile` 和程序
`--institutional-browser-executable`；目录须由当前用户拥有且为 0700，禁止日常默认目录。
重复启动复用所选 systemd 单元；退出研究不会关闭它。同目录换端口须先停止原连接。

```bash
# 使用与 start 相同的程序、目录及端点参数；正常关闭并保存浏览器会话
python -m hypoloom institutional connect stop --institutional-browser-mode local
# 服务器模式只启动已存在的本地镜像，不自动拉取或修改系统代理
python -m hypoloom institutional connect start --institutional-browser-mode server --repository /path/to/Hypoloom
```

`--institutional-connect-timeout` 默认 30 秒，允许 3–120 秒，控制启动就绪等待；依赖检查、
CDP 握手和停止检查也各有时限。server 的 Compose 来源是所选仓库，安装 wheel 后仍需
提供此仓库路径。Docker 失败应修复其部署或改选 existing，不会静默换成其他方式。
`stop` 只操作匹配的受管资源；existing 不关闭外部浏览器，任何模式均不删除会话目录。

### Windows 客户端一键连接

项目附带 `hypoloom/connection_assets/institutional-connect.ps1`，可单独复制到 Windows；
Windows 不需要安装项目或 Python。使用 PowerShell 7.2+，将脚本放在固定位置。SSH 目标
是你已有的 `user@host` 或 SSH 配置别名，不能把账户密码填入目标。先在普通终端完成
`ssh research-server` 的首次主机信任，并配置本机 SSH 密钥／ssh-agent；无人值守连接
使用 BatchMode，不保存密码，服务端需有 curl。学校 SSO/MFA 仍只在浏览器内操作。

```powershell
$researchServer = 'research-server' # 替换为自己的 SSH 目标或配置别名
$researchConnector = Join-Path $env:LOCALAPPDATA 'Hypoloom\institutional-connect.ps1'
New-Item -ItemType Directory -Force (Split-Path $researchConnector) | Out-Null
scp "${researchServer}:/path/to/Hypoloom/hypoloom/connection_assets/institutional-connect.ps1" $researchConnector
& $researchConnector -Server $researchServer -Action Run
```

脚本优先查找 Edge，再查找 Chrome，使用
`%LOCALAPPDATA%\Hypoloom\Institutional-msedge` 或 `Institutional-chrome` 专用目录；原有
同目录登录可复用。程序路径和目录可用 `-BrowserExecutable`／`-BrowserProfile` 明确指定，
目录权限须限于当前用户、SYSTEM 和管理员。默认两端端口均为 9222，可用
`-LocalPort`／`-RemotePort` 改写。脚本验证本机监听进程、回环绑定及服务端 CDP 的同一
浏览器身份后报告 ready；服务器 `init` 再执行实际 CDP 握手。

Run 保持前台，Ctrl+C 清理该次创建的 SSH 子进程；已有可用隧道仅监测，不被终止。
断线后尝试重新连接，失败最多三次、间隔 30 秒；账号/主机信任预检失败直接返回。
电脑关机、睡眠、浏览器关闭或需要重新认证时，远端 init 无法自行唤醒或代替真人登录。
可选安装当前用户登录启动任务，不需要保存 Windows 密码：

```powershell
& $researchConnector -Server $researchServer -Action Install
& $researchConnector -Server $researchServer -Action Status
# 停止计划任务，或卸载；使用与安装时相同的路径及参数
& $researchConnector -Server $researchServer -Action Stop
& $researchConnector -Server $researchServer -Action Uninstall
```

Install 从下一次该用户登录开始生效；本次可另行 Run。任务拒绝覆盖不匹配的同名任务，
禁止多实例，失败按系统任务配置最多重启三次、间隔一分钟。Stop 不删除专用配置目录，
但系统结束任务时可能关闭它启动的子进程；需要持续连接时不要停止任务。前台 Run
仍通过自己的 Ctrl+C 停止。另一台 Linux 客户端可先 `connect start --institutional-browser-mode local`，
再保持原有 `ssh -N -o ExitOnForwardFailure=yes -R 127.0.0.1:9222:127.0.0.1:9222 research-server`
运行；本批未添加 Linux 隧道常驻安装器。

会话由浏览器保存，出版社/学校决定有效期和订阅范围。本批已用隔离无头 Chrome 验证
进程与会话生命周期，原生可见桌面、Windows 登录任务和容器部署的验收范围以
[验证记录](../qualification.md#institutional-connection-startup-2026-10-02)为准。

## 配置新运行

在Linux Host所用的Python环境、Hypoloom仓库根目录安装可选依赖；PDF解析仍需要项目原有的Poppler：

```bash
python -m pip install -e '.[institutional]'
command -v pdftotext
command -v pdfinfo
```

## 服务器独立运行（推荐）

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

## 笔记本浏览器模式（可选）

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

## 笔记本模式的固定端口与常驻连接

浏览器和SSH隧道按用户会话复用，不需要每个run重建。约定使用9222；每次新Host都传
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
