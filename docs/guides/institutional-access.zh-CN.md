# 机构全文访问

[English](institutional-access.md) · 简体中文

公开检索和PDF下载照常运行。需要学校订阅时，Host复用独立Chromium浏览器中的有效登录。
连接与端口随本次运行结束释放；专用浏览器目录保留，后续运行复用仍有效的登录。
学校要求重新认证或MFA时仍需人工操作；全文就绪也不代表Agent已经读过它。

## Elsevier API

ScienceDirect 专用浏览器反复出现人机验证时，可配置官方 Article Retrieval API。
它不需要 Windows 浏览器登录。API Key 与网站 SSO 是两套认证：新 Key 默认依赖
请求服务器的机构出口 IP；校外可能需要 Elsevier 签发的 Insttoken。Key 本身不保证
全文授权。[官方认证说明](https://dev.elsevier.com/tecdoc_api_authentication.html)

1. 在服务器用本地编辑器打开 `profiles/local/<你的Profile>/methodology.yaml`。
   保留现有字段，在 `literature` 下添加 `elsevier_api_key`；已有 Insttoken 才填写
   `elsevier_insttoken`。不要把密钥发到 CC、聊天、命令行或公开模板。
2. 文件保持普通非符号链接文件、权限 `0600`；仍复用这个 Profile，不创建第二份凭据文件。
3. 在服务器执行下方只读检查，替换 Profile 和 DOI。输出只包含状态、原因、样本 URL
   和页数，不输出密钥或全文，也不创建、启动或修改科研运行。

```bash
./.conda/envs/hypoloom/bin/python -m hypoloom.elsevier \
  --credentials-file /data/Gray/Project/Hypoloom/profiles/local/YOUR_PROFILE/methodology.yaml \
  --url 'https://doi.org/REPLACE_WITH_YOUR_DOI'
```

返回 `available` 仅证明该篇当前可获取；`elsevier_authentication_failed` 表示认证失败，
`elsevier_access_denied` 表示请求的授权未通过，`elsevier_rate_limited` 表示额度／限流，
`identity_unconfirmed` 或 `elsevier_full_text_unconfirmed` 表示内容尚未验证。
这些状态不能通过重跑浏览器 init 修复。403 时检查服务器出口 IP、机构 API 权限及
Insttoken；不要把浏览器 Cookie 复制到 API 配置。已发到聊天的 Key 建议替换后本地保存。

填写后重新执行 init 的只读检查即可审阅新配置。新 Host 的文献获取会识别该 Key；
init 默认浏览器准备跳过 ScienceDirect，保留 IEEE、Springer。显式选择 ScienceDirect
浏览器准备仍被尊重。预检显示 `full_text_access: not_checked`，不会自动消耗 API 额度。
已启动运行使用其原冻结版本，不自动迁移。

对已有公开 PDF 路径保持原行为；选中 ScienceDirect／Elsevier DOI 时，官方 API 代替
匿名出版社页面和自动浏览器回退。DOI 先请求 PDF；只有 PII 的链接直接请求 XML 以核对身份。
PDF 返回 403／406 时在同一期限内
尝试一次 `view=FULL` XML；401／429 不自动重试。元数据、摘要、验证码、错误论文不会
被当成全文。XML 成功时保存提取文本并注明缺少 PDF 分页及可能缺失图形／公式排版。
失败仍可走准确 DOI 的公开全文后备或人工 PDF 导入，不再自动打开验证码页面。
`ScienceDirect=public_only` 同时关闭机构 API；要用 API 应保持 `auto`。
Windows 专用浏览器仍可用于其他平台。[官方全文接口](https://dev.elsevier.com/documentation/FullTextRetrievalAPI.wadl)
保留 Web 的可选人工 PDF 导入需要启用机构面板（浏览器模式不能为 `off`）。API 失败的
论文会出现在该面板中，等待人工导入，不自动启动浏览器；API 本身可在浏览器 `off` 时使用。

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
| Springer | 是 | 直接打开官方机构选择页，搜索学校后认证。 |

不指定平台/样本时采用上述三个默认准备平台；已设为 `public_only` 的平台不默认准备。
重复 `--provider <平台>` 替换默认名单，可加入 ACM 或目录中其他平台；`--provider none`
不连接浏览器。只传 `--sample` 时仍只处理样本所属平台。准备名单不限制科研检索范围。
ACM 的默认来自[2026 年开放获取公告](https://www.acm.org/about-acm/acm-pres-on-oa-transition-and-dl-changes)，
不代表每次程序下载都成功，也不关闭已有浏览器回退。

整个批次复用一个浏览器目录及可选的 `--institutional-library-url` 学校交互入口；
不提供学校入口时打开平台默认访问入口；Springer 使用官方机构选择页，其他平台使用
出版社首页。学校选择、SSO/MFA 仍在浏览器内完成，不保存账号密码。
可以输入该平台有权限的 DOI 或规范论文 URL 验证样本，也可传重复的 `--sample`。
提供样本时先检查当前会话，全部样本可读就跳过登录提示；公开样本可读不证明机构认证。
需要人工处理时，终端支持 `s` 跳过、`p` 仅公开获取、`q` 结束本批；回车完成或重新检查
原样本。IEEE、ScienceDirect、Springer 和可选 ACM 未指定样本时，回车／init 中回复
**完成**后自动验证固定公开 DOI；不会提前用公开样本成功代替人工登录机会。
其他未配置固定样本的平台仍记为 `not_checked`，不显示已登录。
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

### Springer 机构入口与 PDF 人机验证

Springer 默认直接打开[官方机构选择页](https://wayf.springernature.com/?redirect_uri=https%3A%2F%2Flink.springer.com%2F)。
在 **Find your institution** 中输入学校名称并选择机构，再完成学校认证。若旧批次
停在首页，可在同一专用浏览器标签打开这个入口；新版入口对新批次生效，不要求更新
Windows 助手或重建 init。自定义学校图书馆入口仍优先，重复“打开登录”仍保留已有页。
如果搜索不到学校，从学校图书馆的数据库列表进入 Springer，按学校提供的校园网络／
远程访问方式操作。个人 Springer 账号不自动代表学校订阅。
[Springer 官方说明](https://support.springernature.com/en/support/solutions/articles/6000083159-institution-not-displayed-on-institutional-login-page-for-athens-shibboleth-list)
指出可搜索的机构取决于联合认证配置；另支持
[IP、代理等访问方式](https://support.springernature.com/en/support/solutions/articles/6000080116-user-access-to-springer-nature-link-and-springer-for-r-d-content)。

ScienceDirect 文章页或机构登录成功，不保证 PDF 入口也已放行。
[Cloudflare 的验证有效期](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/challenge-passage/)
及[验证级别](https://developers.cloudflare.com/cloudflare-challenges/concepts/clearance/)由网站控制，
后续请求仍可能再次要求验证。若能在同一个专用 Edge 手动完成并打开 PDF，可在原批次
结束后重新准备失败平台，以实际 `sample_verified` 为准；若仍循环，机构 PDF 获取会
受影响。程序识别到验证时返回 `verification_required`，不将其视为论文或机构无订阅。
HTML 全文只有通过正文和论文身份校验才可使用；研究仍可走同篇公开全文／仓储来源，
或由用户合法下载并导入 PDF，但不能保证每篇都有替代来源。准备和自动获取有固定期限，
不自动代做验证或无限重试。页面打开、机构登录、下载成功及论文身份确认须分别判断。

### 固定全文样本

更新版本后，下次 init 选择机构准备即可使用；不用另外找 DOI 或运行测试代码块。
连接好 Windows 客户端，在专用浏览器完成所需的认证，然后在原 init 回复 **完成**。
程序自动获取下面的样本全文并检查内容和论文身份；每平台一个样本，复用原有获取
期限（默认 120 秒）和容量限制。跳过、仅公开、结束准备不会触发默认样本下载。

人工登录机会结束后，固定样本先走与运行时相同的公开 PDF 查询及下载路径，
未取得时再检查专用浏览器，沿用原获取期限。固定样本遇到验证码或登录页会记录
自动获取失败并进入下一平台，不要求逐篇人工下载；重复 init 不保证解决验证码。

| 平台 | 固定 DOI／出版社来源 |
| --- | --- |
| IEEE | [10.1109/ACCESS.2019.2902267](https://ieeexplore.ieee.org/document/8654603/) |
| ScienceDirect | [10.1016/j.softx.2024.101723](https://www.sciencedirect.com/science/article/pii/S2352711024000943) |
| Springer | [10.1007/s10664-020-09851-6](https://link.springer.com/article/10.1007/s10664-020-09851-6) |
| ACM（主动选择时） | [10.1145/945445.945450](https://dl.acm.org/doi/10.1145/945445.945450) |

这些是公开全文连通性样本，与学校或科研课题无关；IEEE Access 是
[开放获取期刊](https://ieeeaccess.ieee.org/about/)，ScienceDirect／Springer 页面标注
Open access，ACM 按上述 2026 公告开放研究内容。固定 DOI 属于代码的平台目录，
可用 `institutional prepare --list` 查看；不会让模型临时挑选。

结果中 `sample_verified` 加 `sample_scope: public_full_text` 仅表示该篇正文在本次
可读，**不证明机构登录、付费订阅或整个平台权限**。如需检查学校订阅的某篇论文，
用 **验证样本**提供该平台 DOI，或在独立命令传 `--sample`，替换该平台默认样本。
自选样本成功也只证明该篇可读。验证码、权限不足、连接失败、错误 PDF 会保留实际
状态，不能记作成功；可以重试失败平台或接受未验证状态继续审阅启动配置。
不会自动修改现有研究运行，检查字节释放、不进入科研论文归档。

`sample_verified` 接受已核对身份的 PDF 或正向识别的 HTML 全文。新结果另带
`media_type`、`acquisition_source`、`source_url` 和 `page_count`：仅
`application/pdf` 表示取得 PDF；`public_pdf` 表示公开来源，`browser` 表示专用
浏览器。公开来源已取得 PDF 时，不必另弹出 PDF 标签页。旧结果缺少这些字段时
不能倒推 PDF 已下载。单篇通过仍不证明后续可以
无人值守批量下载；详情页、摘要页和 HTML/XML 全文不能当作 PDF。

无人值守运行优先使用现有公开检索、PDF 候选和精确 DOI 的公开副本回退，再尝试
有效机构会话。后台遇到登录或验证码会返回不可用，保留全文缺口并继续研究；人工
处理仅是主动选择的恢复方式。ScienceDirect 的一次验证码通过不能保证后续免验证，
[Cloudflare 放行范围和有效期由站点决定](https://developers.cloudflare.com/cloudflare-challenges/concepts/clearance/)。
不使用出版社 API 时，只能自动取得当前可访问的出版社文件或合法公开副本，不能
保证每篇论文成功。[Elsevier 官方 TDM 通道](https://www.elsevier.com/about/policies-and-standards/text-and-data-mining/faq)
是可选的自动全文路径，仍须验证实际权限；XML 全文不等于出版社 PDF。

多人共用服务器、不适合部署私人登录浏览器时，可保留客户端作为可选机构来源。
客户端离线后，服务器仍可使用已归档原文及公开/OA 路径；需要客户端私有会话的新增
全文无法保证取得。初始化登录一次不等于将访问能力永久交给服务器，也不能提前取得
研究过程中尚未选中的所有论文。不要把服务器图形桌面作为这类用户的必需条件。
IEEE PDF 验收应看到实际 PDF 字节、论文身份校验和归档；HTML 全文、PDF 阅读器页面
以及公开仓储副本的单篇成功，都不能证明机构 PDF 渠道已稳定打通。

对于主动提供的其他样本，若检查遇到验证码或登录页，CC 会显示**样本页面需要人工处理**。
在专用浏览器手动通过后回 CC 回复 **完成**，程序继续检查同一个样本；这不是要求
重新登录机构入口。也可跳过、仅公开或结束准备。这里沿用样本原获取期限（默认
120 秒，显示剩余时间），回答不会重置期限；超时仍是失败。程序保留页面、不自动
操作验证码。独立非交互样本检查仍直接报告状态，不等待人工回答。

`identity_unconfirmed` 指**目标论文身份未确认**，不是机构账号身份未确认，也不证明
PDF 已经下载。程序在最终 JSON 前输出固定结果说明；CC 应原样展示。具体阶段由
`reason_code` 区分：`article_identity_unconfirmed` 是论文页面身份校验失败，
`pdf_target_unconfirmed` 是 PDF 下载目标未确认，`document_validation_failed` 是
取得内容后的文档校验未通过。旧结果没有这些原因码时，不能倒推是哪一步失败。
重复登录或重试不保证解决身份校验问题；公开／OA 回退仍可尝试，也不保证获取成功。

若要验收新用户流程，每轮可新开 CC 会话执行 `/autoresearch init`，在最终启动审阅
处暂停，不必续接旧批次。已有客户端和浏览器登录仍可能被复用；完全首次安装需在
独立测试账户验证，不能把已有登录的新 init 记作首次安装通过。

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

正常 init 使用程序给出的 `instructions_argv`，展示文件下载位置、固定启动入口、
需填写的路径和固定回复，不转贴安装程序正文。连接阶段回复 **继续连接 / 跳过准备**；平台阶段回复 **完成 /
验证样本 / 跳过此平台 / 仅公开 / 结束准备**。验证样本另提供 DOI 或论文 URL。
客户端接入后由程序自动检测。CC 展示连接说明后，应立即后台执行一个程序生成的
`watch_argv`，不能等用户另说“已连接”才开始观察。生成命令含 `--wait-seconds 3600`，
到需人工操作的阶段或批次结束即提前返回当前说明，并通过后台任务完成通知唤回 CC。
它只观察、不代答，也不延长连接、平台回答或样本获取期限。每次提交平台回复后再
观察下一阶段，同一时刻最多一个观察进程。手动运行不带此参数的 `watch` 仍默认 30 秒。
不要给生成命令再套较短的 timeout；等待期间无输出不能解释为“没有新事件”。CC
读取返回的最新平台提示后展示固定回复，不自行拼接 sleep 或轮询循环。
若接入期间已完成浏览器登录，进入平台阶段后直接回复 **完成**，
无需重新登录。连接阶段的旧回复失效时，按程序最新提示回答，不替用户自动确认完成。
**结束准备／跳过准备会终止当前批次**，不会表示“已连接”或自动重开；新建的批次
无需先结束才能测试。仅想刷新进展时读取状态，不发送结束命令。Windows 的
`run_discovery_recovered` 表示连接列表查询恢复，不等于浏览器验证成功；
`waiting_for_preparation_or_run` 表示当前没有发现活动目标，前台助手仍在等待。
助手将原话交给 `reply_argv --text`，程序按当前阶段和平台校验；后台任务完成通知
不是用户回答。批次结束后会明确提示 **重新准备**，先读取原摘要并保留已完成的平台
和模式，再原样执行摘要中的 `remaining_preparation_argv`；其中包含失败及未回答的平台，
保留连接参数、已选模式和原命令提供的样本。默认检查失败的平台也在重试名单中；
成功的平台不重复准备。没有固定或自选样本的平台手工完成仍属未验证，不重复准备。
无需新开 CC 会话或创建科研运行。

准备入口检测到验证码时，固定说明会显示“页面打开时检测到人机验证”。这是打开
页面时的观察，不证明验证已通过或机构登录已完成。仅在专用浏览器中手动完成；若
验证反复出现，可回复 **跳过此平台** 继续下一平台。程序不会代做验证码、自动刷新
或重开页面，原等待期限不延长。普通浏览器的验证和登录不会同步到专用登录目录。
这种网站验证不能靠反复重装客户端保证恢复；必要时使用平台支持的接口或手动 PDF 导入。

所选平台超时时，init 应提供 **重新准备** 或 **接受未验证状态，继续检查启动配置**，
由用户决定。准备失败不阻止科研启动，也不代表成功。如果旧版本摘要仍有失败项却输出
空重试数组，助手应使用原只读 bootstrap 检查为这些平台重新生成准备命令，保留其他
配置和启动身份。请保持专用浏览器打开。中间的 `browser_unavailable` /
`fetch_unavailable` 输出安全错误码；只有最终 `acquisition_timeout` 无法确定底层原因。
连接预检也返回固定错误码：`cdp_timeout`、`cdp_connection_refused`、
`cdp_connection_closed`、`cdp_authorization_failed`、`cdp_endpoint_unavailable`、
`cdp_context_unavailable`、`browser_driver_unavailable` 或 `cdp_connection_failed`。
它们表示浏览器控制连接故障，不代表机构登录或订阅失败；不输出原始异常和私有地址。
平台打开阶段也保留相同的连接错误码；`browser_target_closed` 表示页面、上下文或浏览器
目标关闭，`browser_operation_timeout` 表示页面操作超时，`browser_operation_failed`
表示未分类的页面操作失败。笼统的 `browser_unavailable` 不能证明用户关闭了 Edge 或
SSH 任务退出。重试沿用原有有界队列；窗口仍开着不等于控制连接一直可用。
样本获取阶段同样保留这些安全错误码。`acquisition_timeout` 只能表示本次验证未完成，
不证明样本可读、登录失败或浏览器已关闭；成功必须有 `sample_verified` 结果。


`complete` 按程序显示的样本自动检查；既无固定也无自选样本时仍是未验证。
`skip` / `public_only` 也须带当前平台。
这些命令只用于仍存活的临时准备进程，不修改科研运行；结束后读取后台命令最终摘要。

运行后按 TUI 的 **i** 查看只读机构状态：当前运行的出版社目录、公开＋机构／仅公开
策略、当前机构请求的进行／就绪／缺口数量，以及最近结果。无请求的平台也会显示；
策略表示允许使用的访问途径，不代表已登录或整个平台已验证。
Host 提供已有本地通知日志时，最近观察还会显示最后最多 1 MiB 日志中的文档验证次数。
这是尽力记录的事件，不是去重 PDF 下载数、归档数或阅读数；缓存为空不代表过去从未成功，
没有日志观察时历史未知。
面板自动随主界面刷新；底部 **Close** 或 **Esc** 返回研究主界面。没有 PDF 路径输入、
登录、重试、样本验证或模式切换操作；查看和关闭面板不改变运行。
可选人工操作仍由带文献操作授权的 Web 面板提供，不是无人值守运行的必经步骤。
不要同时运行独立准备命令与 Host 来管理同一浏览器。
“样本全文已验证”仅证明此篇在显示时间可读，样本字节检查后释放；不代表整个平台永久有权，
不代表 Agent 已读。重连同一浏览器可以复用有效会话，但学校、出版社和浏览器决定过期时间。

默认先走公开全文及当前配置的准确 DOI 开放全文备选，再尝试浏览器现有会话。
仅为工具明确选择的文献发起浏览器获取，收到元数据包不会自动把所有缺失 PDF 入队。
`web_fetch` 由工具程序在原获取期限内等待结果，不额外消耗模型调用，也不阻塞 Host。
`mode=document` 成功后直接进入原有 `/papers` 工作区及归档；同一运行的其他专家明确
请求同一 URL 时，可复用尚在有界缓存中的自动获取原文，但各自重新校验论文身份，
保留独立来源引用与归档。人工导入和其他运行的原文不参与这种复用。
后台遇到登录、验证码或访问提示即结束本次等待，研究继续其他来源。
自动机构获取从入队起最多 **120 秒**，包括排队和重试；人工点击登录／检查才开启
该平台 **600 秒**窗口。后来出现的论文不延长窗口；超时论文需要手动重试。
无权限只结束该篇，不自动判断全平台无权。浏览器断线时在剩余期限内退避，期限结束后返回缺口。

| 可选 Web 操作 | 影响 |
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
命令，也不能与机构端点／目录等选项混用。需要 Web 面板导入 PDF 时，保留启用模式并
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

**已经在 CC 中进行 init 时**，首次接入或升级采用原始脚本文件，不执行助手转贴的
安装代码。bootstrap 与准备进程共同给出 `script_source`（服务器文件位置）、
`setup_inputs`（需要填写的路径）和固定启动入口。

1. 在 Windows 打开一个用于下载的目录，通过现有 SFTP/SCP 或文件传输工具，从当前
   SSH 服务器下载程序给出的 `institutional-connect.ps1` 原始文件。不要将聊天中的
   脚本正文另存为文件。Windows 无需安装 Hypoloom 或 Python。
2. 在该下载目录打开 PowerShell 7.2+，执行：

   ```powershell
   pwsh -NoProfile -File .\institutional-connect.ps1 -Action Setup
   ```

3. 按程序提示输入 Windows 使用的 SSH 目标、服务器仓库路径、Python 路径和运行目录。
   init 会显示三个路径的实际值。标准安装的后两项可回车使用默认值；非标准安装填写
   init 显示的值，不要修改脚本源码。Setup 验证 SSH 和服务器 Python，再按原始字节
   安装到 `%LOCALAPPDATA%\Hypoloom\institutional-connect.ps1`，随后执行 Connect。
4. 看到 `client_discovery_started` 后回到原 init 查看进展，程序自动检测实际连接。
   如原批次已结束，回复 **重新准备**。后续 init 复用已安装的后台任务；任务正常时
   不需要再次下载或安装。Setup 不新开 CC 会话。

没有图形文件传输工具时，可以在 Windows 下载目录使用 OpenSSH 的交互式 SFTP。
以下目标和文件路径是示例，应使用自己原有的 SSH 目标及 init 显示的 `script_source`：

```powershell
sftp research-server
```

进入 `sftp>` 提示符后下载文件并退出（路径含空格时用双引号包围）：

```text
get /path/to/Hypoloom/hypoloom/connection_assets/institutional-connect.ps1
bye
```

确认下载成功后再运行上面的固定 Setup 入口。文件传输直接保留脚本字节，避免聊天
转述改写和本机代码页损坏程序正文。旧版本发生乱码或参数拼写错误时，重新取得
原始文件并执行 Setup，不继续修补助手生成的代码块。若旧命令停在 `>>`，先 Ctrl+C。
SSH 目标不是密码；`127.0.0.1` / `localhost` 指向 Windows 本机。普通 SSH 能输入密码
登录，不代表后台免交互认证可用；先按下方密钥/ssh-agent 步骤验证
`ssh -o BatchMode=yes <目标> exit` 返回 0。服务器仍须验证实际浏览器连接。

后台发现任务使用隐藏窗口。重新执行 `Connect` 会在严格核对用户、程序和全部连接
参数后，为旧任务更新窗口设置；正在运行的旧可见任务会重启，连接可能短暂中断。
任务已使用隐藏参数时不会因此重启。SSH 密钥和浏览器登录目录不变；需要学校认证
时浏览器仍会正常打开。`client_discovery_started` 只表示已请求任务启动，实际浏览器
连通仍以 init 的检查为准。
Connect 返回后可以关闭其 Windows PowerShell 窗口，隐藏发现任务会继续运行。
只有前台 `-Action Run` 诊断需要保持窗口打开；出现
`waiting_for_preparation_or_run` 表示没有活动连接目标，应同时保留服务器上的 CC
准备批次。这条等待提示不代表浏览器已连接成功。

旧版前台输出 `run_discovery_unavailable` 后以 2 退出，表示刷新服务端连接列表连续
失败，不能据此认定 Edge 已关闭。新版保留已有转发，按 5、10、20、随后每 30 秒
重试列表查询；每次查询仍最多 15 秒，准备／全文获取期限不延长。前台分别显示
`run_discovery_timeout`（查询超时）、`run_discovery_ssh_failed`（SSH 退出 255）、
`run_discovery_command_failed`（远端命令失败）或 `run_discovery_invalid_response`
（返回内容无效），附带重试间隔；恢复时显示 `run_discovery_recovered`。保持前台窗口
打开即可自动重试，不用反复发送“继续连接”。持续 SSH 错误需检查本机免交互登录；
命令／返回错误需检查服务器路径和安装。失败期间不会凭旧列表创建新连接，已有
转发仍由服务器逐条检查运行存活和浏览器身份，不能保证网络故障时转发本身存活。

遇到旧版这个退出原因时，按上面的文件流程重新下载并运行 Setup；已经退出的任务
可直接启动新版。若旧版 Run 或后台任务仍在运行，先用 Ctrl+C 或原参数 `-Action Stop`
停止它，再运行 Setup，以加载新文件。保留专用 Edge 和登录目录；回到原 CC 会话，
批次仍在等待时由程序自动检测，已结束时再选择“重新准备”，无需新建 init。

Windows 计划任务可能把运行账户显示为短用户名，而当前会话显示为“计算机名\\用户名”。
新版通过 Windows 解析账户 SID 后核对归属。遇到旧版因此报出的
`scheduled_task_ownership_mismatch`，重新下载原始脚本文件并运行 Setup
取得新版入口；无需改名或删除任务。真实账户、程序或连接参数不同仍会拒绝更新。

**从 Windows 开始一个新的 init 会话时**，才使用以下入口：

先按上面的文件流程完成一次 Setup。以后也可从 Windows 启动新的 init，使用原有
SSH 目标和服务器仓库位置；入口推导标准的 Python 与 state 路径：

```powershell
$researchServer = 'research-server' # 原有 user@host 或 SSH 别名
$researchRepository = '/path/to/Hypoloom'
$researchConnector = Join-Path $env:LOCALAPPDATA 'Hypoloom\institutional-connect.ps1'
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
`browser_profile_must_be_private` 时，重新下载更新后的原始脚本并执行 Setup；修复成功会显示 `browser_profile_permissions_repaired; login_data_preserved`。

最初的 `c254d5f` 修复可能只把额外授权变成显式条目而未删除；纠正版在内存中准备
保留规则后一次写回。如果前后 ACL 输出确认发生了这次旧版部分修复，只针对已确认
的残留账户，在该专用目录执行 `icacls <专用目录> /remove:g <已确认本地组>`，再接入。
连接器仍拒绝未知显式授权，重复下载不会自动删除它。错误码现区分
`browser_profile_explicit_access_requires_manual_permissions`（显式额外授权）、
`browser_profile_custom_path_requires_manual_permissions`（自定义目录）、
`browser_profile_permissions_write_failed`（写入失败）和
`browser_profile_permissions_not_applied`（读回不合格）。

脚本优先查找 Edge，再查找 Chrome。默认 `-LocalPort 0` 表示由连接器向 OS 申请
可用端口，启动浏览器时传入实际非零端口。直接给 Chromium 传调试端口 0 会启用
webdriver 标志，因此不再使用这种启动方式。端口接管仍校验回环地址和进程归属。
使用 `%LOCALAPPDATA%\Hypoloom\Institutional-msedge` 或 `Institutional-chrome`，
可通过 `-BrowserExecutable`／`-BrowserProfile` 指定原来完成登录的专用目录。
不导出 Cookie，不复制登录凭据；目录权限限于当前用户、SYSTEM 和管理员。
已有相同程序、目录及用户的非零端口专用浏览器可复用，包括手动启动的 9222 端点。

**从旧版端口 0 浏览器更新：** 安排一次客户端连接中断，以原服务器和连接参数运行
`-Action Stop`，停止旧发现任务；手动关闭 Hypoloom 专用浏览器的所有窗口。普通
浏览器不需要关闭，专用目录不需要删除。再按上面的文件流程下载原始脚本并运行
`-Action Setup`。仅覆盖文件或重开 CC 不会更新已运行的任务／浏览器启动参数。
若看到 `browser_restart_required_close_dedicated_window`，说明仍有旧参数的专用
进程，请正常退出该专用浏览器后重新 Connect；程序不会强杀浏览器或清理登录数据。

原准备批次仍在等待连接时回复“继续连接”，已结束时选择“重新准备”，无需为此
创建科研运行。在新专用窗口打开 [Cloudflare 官方检测](https://debug.challenges.cloudflare.com/)
检查 Automation Check，再在 ScienceDirect 实际完成验证和论文样本检查；消除已知
启动副作用不代表网站一定放行。登录、验证码及 MFA 仍由本人在浏览器完成。

脚本仅对活跃运行建立 SSH 转发，没有运行时不启动浏览器、不占服务器研究端口。
服务端目标为每次运行的私有 Unix socket，无需 `-RemotePort`。
断线后先核验运行仍活跃，并清理确认没有监听者的旧 socket 路径；不接管现有监听。
每条转发最多重试三次，间隔 30 秒；列表查询失败由常驻发现循环持续退避重试，
不能因此关闭健康转发。成功的新列表与逐条远程 watch 继续负责连接的创建和回收。
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

可再传`--institutional-library-url https://你的稳定图书馆入口/`。TUI显示连接状态，Web
提供可选浏览器操作；Web 的“打开登录”打开服务器标签，不收集账号。Host关停只关闭自己创建的
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

1. 按 **i** 打开“机构接入状态”，一行一个出版社，查看访问策略、当前请求数量和最近观察。
2. “公开＋机构”表示允许尝试当前机构会话；“仅公开”表示不自动使用该平台的机构浏览器。
   “尚无机构请求”不代表公开获取失败。“全文已就绪”也不代表 Agent 已读或已下载 PDF。
3. 连接端点存在仅表示已发现端点，不等于浏览器验证通过。Host 状态缺失时显示“未知”，
   与“未启用”区分。请求数量只覆盖当前有限队列，不是历史总量，也不包含公开路径下载。
4. 通过 **Close** 或 **Esc** 返回；面板不要求上传文件、登录或回答获取失败。

无法获得的全文保留缺口，其他研究工作继续。已取得且通过验证的原文由 Agent 的
`web_fetch(mode="document", url=...)` 进入既有 `/papers` 与归档流程；不改写已完成结论。

如果主动选择人工补充，可另外启动带 `--allow-literature-control` 的 Web observer，
使用其操作员链接；普通只读链接不能触发动作。Web 的 Workbench“机构全文”保留登录、
重试和导入 PDF。导入仍要求 Host 上的绝对路径和原有 PDF、DOI／标题、来源校验。
这些可选操作不从 TUI 机构面板发起。

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

`i` 面板显示最近结果的简要原因；Web 机构全文列表保留原始原因码、上次检查时间及距下次检查的估计秒数；
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

### 新 init 实测

无人值守稳定性优先时，在新 CC 会话直接输入：

```text
/autoresearch init
优先保证无人值守稳定性，使用 Windows 客户端模式。
ScienceDirect 和 ACM 设置为 public_only；IEEE 和 Springer 保留 auto，先做准备。
使用 --institutional-prepare-failure-mode public_only。
准备失败或未完成的平台自动转为仅公开，保留失败原因，不反复要求登录或重试。
完成后停在最终启动配置审阅，等待我选择 Start now。
```

ScienceDirect／ACM 是本轮已反复失败的平台示例，并非所有用户的固定限制；按自己的
实测选择。程序生成准备和启动命令，返回的 `provider_modes` 按平台替换后进入启动
配置。“仅公开”仍允许公开检索和精确 DOI 的合法全文回退，但不会自动请求该平台的
机构浏览器。已有的人工完成／跳过选择保留；失败、连接超时和未到达的平台转为
`public_only`，原失败状态与非零退出码保留，不包装成验证成功。无需再选“接受未验证”，
也不会自动启动研究。默认 `review` 策略仍提供重试／接受未验证的选择。

该策略不保证全部论文可下载：没有合法可用全文时记录缺口并继续其他工作。初次
Windows 接入和你选择保留的机构平台准备仍可能需要一次人工认证；研究期间不要求
逐篇解验证码。现有客户端助手无需为此更新，既有运行也不会被自动改动。

需要专门测试所有出版社时，使用常规审阅策略：

1. 在已更新的服务器仓库中新开 CC 会话，运行 `/autoresearch init`。
2. 选择 Windows 客户端模式及 IEEE、ScienceDirect、Springer、ACM（ACM 需主动选择）。
   健康的 Windows 后台助手可复用；本轮没有修改助手脚本，无需重复安装。
3. 每个平台打开后，在需要时完成机构认证，回 CC 回复 **完成**。已有登录态可直接完成；
   固定样本由程序自动验证，不需另找 DOI 或逐篇处理验证码。
4. 检查每个平台的状态、来源、格式和页数。验证码、403、身份未确认或超时都不是通过。
   有失败时，可选 **接受未验证状态，继续检查启动配置**，停在最终启动确认处验收；
   只有你选择 **Start now** 才启动新的科研运行。

该实测覆盖新 init 的编排和你当前环境的可用性。复用登录目录不是全新 Windows
安装验证；单个公开样本成功也不保证同出版社所有论文可自动获取。

### 独立验证获取能力

开发阶段先使用仓库内的独立入口，不需要 CC 会话或 `/autoresearch init`：

```bash
python scripts/probe_literature.py --samples --repeat 2 \
  --output .local/qualification/literature-samples-01

python scripts/probe_literature.py \
  --query 'Publish or perish but do not forget your software artifacts' \
  --max-items 1 --output .local/qualification/literature-search-01
```

在已安装项目依赖与 Poppler 的环境运行。每次选择一个**尚不存在的输出目录**；
程序保存实际 PDF 和 `report.json`，按 DOI 逐项记录来源、字节数、页数、身份核对
方式、失败阶段及 HTTP 状态。退出码 0 仅表示本次所选论文全部取得校验通过的
PDF；部分失败、无结果或只有 HTML 全文均退出 2，不代表整个平台不可访问。
公开元数据服务的可用性和限流也会影响结果。

`--doi` 可重复提供最多十篇论文；`--seconds-per-paper` 默认 45 秒，
`--total-seconds` 默认 300 秒。`--samples` 复用既有四平台样本目录，
`--repeat 2` 会重新执行两轮获取，后续失败不会被首次成功掩盖。
它复用现有公网下载、PDF 解析、精确 DOI 回退和身份校验，不调用模型、收集登录
信息或创建科研运行。独立入口、Host 文档回退和 init 样本现已共用精确 DOI 的
OpenAlex/Crossref 查询、PDF 获取与身份校验。公开元数据里的 HTTP PDF 地址只尝试
对应 HTTPS 形式，仍不接纳 HTTP 下载。

定位下载器问题时，可为单个 `--doi` 增加 `--url` 指定已知公开 PDF 地址；结果会
标为 `supplied`，**不能把它算作自动发现成功**。已建立本地 CDP 连接时，可显式传
`--browser-endpoint http://127.0.0.1:端口` 测试现有浏览器适配器。程序只操作自己
创建的标签，结束时关闭这些标签并断开连接；不会关闭用户浏览器或处理验证码。
该选项不负责安装 Windows 助手或建立 SSH 连接。不要与同一浏览器的活跃研究操作
同时执行。

先验收独立检索、下载及连续多篇结果，再接入 Host 文档归档，最后验收 init 交互。
独立实验通过不代表后两步通过；更新后的运行时用于新运行，现有运行保持冻结版本。

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
