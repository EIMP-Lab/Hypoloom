# 机构全文访问

[English](institutional-access.md) · 简体中文

公开检索和PDF下载照常运行。需要学校订阅时，Host复用独立Chromium浏览器中的有效登录。
推荐让浏览器常驻服务器：你远程登录一次，后续论文和新run继续复用，笔记本可关机。
学校要求重新认证或MFA时仍需人工操作；全文就绪也不代表Agent已经读过它。

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

Windows笔记本用独立Chrome配置目录启动一个可见浏览器，然后在这个浏览器中完成学校登录。不要使用平时的默认Chrome目录；[Chrome的远程调试要求独立数据目录](https://developer.chrome.com/blog/remote-debugging-port)。PowerShell示例：

```powershell
$researchChrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$researchProfile = Join-Path $env:LOCALAPPDATA 'Hypoloom\InstitutionalChrome'
& $researchChrome '--remote-debugging-address=127.0.0.1' '--remote-debugging-port=9222' "--user-data-dir=$researchProfile" 'about:blank'
```

若Chrome安装位置不同，修改第一行。浏览器配置目录由你保管，重开相同目录可复用仍有效的登录。Hypoloom不导出Cookie或storage文件。

如果研究在Linux服务器、浏览器在Windows，在笔记本另开PowerShell，替换SSH目标后保持以下连接运行：

```powershell
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -R 127.0.0.1:9222:127.0.0.1:9222 user@SERVER
```

服务器通过SSH回环端口连接笔记本浏览器。CDP端口具有浏览器控制权限，只绑定回环地址；不要把它开放到公网。同机使用则不需要隧道。

在新运行的`python -m hypoloom serve ...`或`python -m hypoloom.bootstrap launch ...`命令末尾添加：

```text
--institutional-browser-url http://127.0.0.1:9222
```

可选添加`--institutional-library-url https://你的图书馆入口/`。这是稳定的公开登录入口，不是登录后的临时回调链接。未设置时直接打开所选出版社论文页。不要修改旧运行的冻结配置；为新运行配置此功能。

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

- Chrome任务：程序为上文Chrome绝对路径，参数为
  `--remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="C:\Users\你的用户名\AppData\Local\Hypoloom\InstitutionalChrome" about:blank`。
  使用与你手动登录时相同的目录，选择“仅当用户登录时运行”，让登录窗口可见。
- 隧道任务：程序为`C:\Windows\System32\OpenSSH\ssh.exe`，参数为
  `-N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -R 127.0.0.1:9222:127.0.0.1:9222 user@SERVER`。
  先手动确认SSH主机密钥，并验证该用户通过SSH密钥/ssh-agent能免交互连接。
  可设置失败后每1分钟重试；已有实例运行时不启动新实例。不要在任务参数里保存密码。

笔记本需保持开机联网，睡眠、断网或退出登录会中断连接；恢复后队列自动检查。
学校使会话过期、要求MFA时仍需本人登录。常驻连接不保证永久免登录。
在笔记本模式中，Cookie和浏览器storage只在笔记本的Chrome配置目录，不复制到服务器。

## 状态和排障日志

`fetch_unavailable`表示加载失败；`network_changed`、`dns_failed`、
`connection_failed`、`navigation_timeout`、`request_blocked`、`http_error`、
`redirect_unresolved`帮助区分原因。它们不表示登录过期，也不会阻塞同平台其他论文。
`access_required`只表示页面提示需订阅或机构访问；只有实际登录表单或登录跳转才显示
`login_required`。DNS安全诊断包含目标hostname，不带查询参数或凭据。

DOMContentLoaded之后还会给异步跳转约8秒时间，适用于DOI、Elsevier linkinghub等中转页；
中转页本身不作为论文。瞬时导航错误在同次检查内最多重试一次；已到达的登录或验证页保持原样。
连续网络、超时、跳转未完成、HTTP 5xx（`http_server_error`）或429（`http_rate_limited`）
失败时，每篇论文的检查间隔依次为30、60、120、300秒，之后保持300秒。429不会立即重试。
成功、退出瞬时失败状态或手动重试会重置退避。登录/验证仍按原节奏检查，不重新导航；
身份未确认、机构无权限仍不自动重试。PDF流式获取保留已有15秒中止计时。
尚未支持的镜像域名仍需专门适配或手动导入PDF；这次修复不保证真实订阅都能下载。
修复供新Host使用，既有冻结run不迁移、不热替换代码。

识别到`https://linkinghub.elsevier.com/retrieve/pii/S...`期刊PII入口时，可直达对应的
ScienceDirect文章页一次，不再依赖中转页的JS。改写丢弃查询参数和fragment，仍须通过DOI
身份校验；登录/验证页优先保留，不被这次跳转抢走。每次导航等待上限改为25秒，PDF获取仍为15秒。
明确显示`Redirecting`的页面，即使已到文章URL，也保持可重试，不能判成终态`no_full_text`。
真实文章页是否提供可读PDF、机构是否有权限，仍需分别验证。
ScienceDirect可能在DOM完成后才插入PDF控件；没有全文/PDF或明确访问结果时，继续检查约8秒
再判`no_full_text`。中转页JS与直达操作竞争导致`ERR_ABORTED`时，在这个时间窗口内跟随落定，
不立即误报断网；最终仍校验来源和DOI。
PDF入口返回HTML或其他非PDF字节时，显示可重试的`pdf_response_not_pdf`，不再判为缺少全文。
已核对身份的ScienceDirect文章遇到`/pdfft`中转页时，会在自己的标签页中执行页面跳转，
导航后最多继续观察25秒。登录或验证页会保留给用户，后续检查不反复刷新。
仅接收同篇文章的PDF入口或`pdf.sciencedirectassets.com`上PII一致的PDF，继续使用既有大小、
PDF解析和来源准入检查；签名参数不写入来源URL或诊断日志。
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

- 识别Nature、Springer、IEEE、ScienceDirect、ACM、Wiley、SAGE、Oxford和Taylor & Francis的常见论文域名及DOI前缀；复用通用PDF元数据和正向全文结构判断。这个名单表示已接入路由，**不表示每个平台已用真实订阅逐一验收**。
- 请求使用不带查询参数的规范论文链接或DOI。IEEE的`stamp.jsp?arnumber=...`等下载入口应改用`/document/...`论文页或DOI。
- PDF使用浏览器所在机器的网络与登录状态获取，再由Host解析。跨域PDF、学校WebVPN改写域名、专用阅读器、扫描版PDF和站点改版仍可能失败；此时保留缺口或导入PDF，不自动绕过验证码或访问控制。
- 每个Host最多32个待获取/缓存条目、64MiB缓存、8个出版社标签，单文件最多24MiB/2000页。等待队列和缓存仅在内存中；Host重启后由原有输入或工具请求重新建立。停止运行后丢弃待处理项和迟到结果；所有项清空或Host关闭时关闭Hypoloom创建的标签，保留你的浏览器和登录目录。
- 缺少标准“Abstract/Conclusions”章节标题不再导致可读PDF被舍弃；缺失的章节摘录保持空值。仅摘要、登录页、人机验证页不作为全文。

本实现复用[Playwright的CDP连接](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)，只支持Chromium系列。回归使用真实Chromium和合成出版社页面；实际学校入口、订阅权限及Windows↔Linux链路需要在你的环境联调。参见[设计](../design/institutional-literature-access.md)和[验证记录](../qualification.md)。
