# 机构全文访问

[English](institutional-access.md) · 简体中文

公开检索和PDF下载照常运行。需要学校订阅时，Host复用独立Chromium浏览器中的有效登录。
连接与端口随本次运行结束释放；专用浏览器目录保留，后续运行复用仍有效的登录。
学校要求重新认证或MFA时仍需人工操作；全文就绪也不代表Agent已经读过它。

## 出版社验证与 DOI 后备查询

论文详情页可打开，不代表 PDF 请求不会遇到 Cloudflare 验证。公共下载收到明确的
挑战响应头后，Hypoloom 会在读取正文前结束该请求，在既有下载记录中保留
`PDF_CHALLENGE`／`CHALLENGE`，并在原有期限内尝试剩余的公开来源。
普通 403 仍单独记为 HTTP 失败。程序不自动完成人机验证，也不会把获取失败当作
已阅读全文。

Semantic Scholar 按精确 DOI 查找公开副本。init 在各样本间复用查询实例，Host
将同一实例用于公共获取与机构浏览器路径；每个实例串行请求，每秒最多一次。
遇到 429 至少冷却 60 秒，并遵守更长的 `Retry-After`，其他公开来源不必等待
冷却结束。多个独立 Host 共用账号仍受总额度限制。可选私有密钥的填写方式见
[个人配置](../../config/README.md)；密钥不等于订阅授权，也不保证服务端会返回全文。

无人值守运行会明确保留全文缺口，继续处理其他可用来源。这些改动在新 Host 和
新准备会话中生效，不迁移既有运行，也不要求重新安装 Windows 助手。

直连 HTTP 请求在 DNS、连接、TLS、响应头和正文之间共用期限，重定向和重试仅使用
剩余时间。到期或取消后终止并回收网络工作进程，额外最多预留 1 秒用于清理。持续
慢速响应不能不断刷新空闲超时；未完成 PDF 会丢弃，其他已成功的论文仍保留。
无需代理或额外 init 步骤。检索入口故障采用[文献服务自动恢复](provider-recovery.zh-CN.md#文献服务恢复)，
与单篇出版社 PDF 无法获取分别处理。

## 检索后备与可选服务器代理

新建在线 Host 在 OpenAlex 临时失败后，会自动尝试匿名 Crossref 关键词检索，
两者共用原有检索期限。论文身份、语言、来源类型和三个年代层的检查仍然有效；
后备结果不足时仍可能要求修改查询。OpenAlex 认证错误不会被后备掩盖。
此后备覆盖初始文献包检索，不保证排序相同、出版社站内搜索、全文可得，也不替代
所有 Web 搜索工具。观察界面会显示 Crossref，而不会误报 OpenAlex 已恢复。
如需关闭，在初始化时传入 `--literature-search-fallback none`。
Crossref 的[公共 API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/)
不需要额外密钥。

未配置代理时继续直连。如果实测服务器上的 HTTP CONNECT 代理能够改善某个端点，
在现有私有 `methodology.yaml` 的 `literature` 节中添加以下字段，端口换成实际值：

```yaml
literature:
  # 保留本节原有字段和凭据。
  proxy_domains: [ieeexplore.ieee.org]
  proxy_url: "http://127.0.0.1:7890"
```

文件保持 `0600`。使用旧 dotenv 配置时，可在原有私有凭据文件中加入
`HYPOLOOM_LITERATURE_PROXY`，初始化时传入
`--literature-proxy-domain ieeexplore.ieee.org`；多个域名重复传参。
域名精确匹配，不自动包含子域名。init、重试和 Host 命令只携带域名及凭据文件路径，
不会写入代理地址。地址更新在下一个选中请求时重读；修改域名列表需要新建运行。

选中域名的元数据和公开 PDF 请求仅走该代理；每次重定向重新匹配，其余域名直连。
程序不读取 `HTTP_PROXY`／`HTTPS_PROXY`。代理失败不会静默改路、解决验证码或
切换节点，也不证明订阅授权。Windows 浏览器仍使用本机网络设置。
CONNECT 连接经过校验的目标公网 IP，TLS 仍验证原域名，因此代理的域名规则未必
命中预期节点，需要单独确认出口配置。原有超时、大小、取消和私网地址限制继续有效。

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

没有研究运行或待下载论文时，也可先准备常用平台。Windows 客户端先在服务器
进入 init，再使用它生成的单运行准备命令和 Windows 入口。仅列目录或在原生 Linux
桌面独立准备时使用：

```bash
python -m hypoloom institutional prepare --list
python -m hypoloom institutional prepare \
  --institutional-browser-mode local
```

目录优先展示四个常用平台，入口来自现有平台目录，无须逐个输入网址：

| 平台 | 默认集中准备 | 说明 |
| --- | --- | --- |
| IEEE | 是 | 复用有效会话，必要时在浏览器认证。 |
| ACM | 否，可选 | 公开全文优先；不将登录作为读取论文的前提。 |
| Elsevier / ScienceDirect | 是 | 参数可使用 `ScienceDirect` 或 `Elsevier`。 |
| Springer | 是 | 直接打开官方机构选择页，搜索学校后认证。 |

不指定平台/样本时采用上述三个默认准备平台；已设为 `public_only` 或 `disabled` 的平台不默认准备。
重复 `--provider <平台>` 替换默认名单，可加入 ACM 或目录中其他平台；`--provider none`
不连接浏览器。只传 `--sample` 时仍只处理样本所属平台。准备名单不限制科研检索范围。
ACM 的默认来自[2026 年开放获取公告](https://www.acm.org/about-acm/acm-pres-on-oa-transition-and-dl-changes)，
不代表每次程序下载都成功；显式选择 ACM 后才启用其机构浏览器回退。

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

被选中论文的公开 PDF 候选都失败后，程序会尝试 **arXiv 同名论文回退**：用精确 DOI
元数据中的标题查询，核对标题、作者及已有 DOI，再下载唯一匹配版本的官方 PDF。
作者信息缺失、同名歧义、冲突 DOI、错误 PDF 或超时均保留缺口，不要求人工处理。
成功归档会标明实际 arXiv ID 和版本；预印本不能当作出版社正式版。此路径复用现有
公开下载与归档，不需要 API key、机构登录、Windows 助手，也不增加 init 配置步骤。
同一 Linux 主机共享 `/tmp` 的 Hypoloom 进程会串行调用 arXiv API，每次请求前等待
三秒，等待计入原有获取期限。多主机或隔离容器部署仍需协调总请求量；
[arXiv 官方限制](https://info.arxiv.org/help/api/tou.html)适用于受控机器整体。

公开 PDF 下载遇到临时超时时，会在本次获取剩余时间和请求预算内最多重试一次，
不会延长总期限、重新检索或保留半截 PDF。限流、拒绝访问、验证码、错误 PDF 和
论文身份不符不会触发这次重试。下载诊断会区分 DNS、TCP、TLS、发送请求、等待
响应头及正文传输阶段，并记录超时时已收到的字节数，便于排查网络波动。

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
处暂停，不必续接旧批次。原脚本和私有登录目录可复用，但每次需在所选 Windows
执行本次入口，旧助手不具备新运行的授权。完全首次安装需在独立测试账户验证，
不能把已有登录的新 init 记作首次安装通过。

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
单运行 Windows init 自然完成或非手动结束的超时失败后进入 `awaiting_launch`；读取 status 的
`summary`，不用等后台进程退出。同一临时控制目录最多保留 600 秒，供审阅并向正式
运行交接；Start now 仍需明确选择。选择 Prepare only／取消时执行返回的 `finish_argv`
撤销本次连接授权；到期也会清理，不自动启动研究。准备中主动结束／跳过则直接退出，
从后台最终结果读取摘要。已完成结果保留，启动身份不变。最终独立的
`preparation_lifecycle` 事件记录 `run_handoff`、`launch_failed`、`launch_uncertain`、`launch_review_expired` 或
`operator_finished`；出版社摘要只说明样本结果，不证明准备控制器或 Windows 助手
此刻仍存活。排障时应同时保留这两类观察。
当前 Windows init 优先使用 `init_session_argv`，由同一程序完成准备到启动审阅的
衔接。`launch_review` 在 600 秒选择窗口开始前就已生成，CC 直接展示并提交你的
**Start now / Prepare only** 固定回复，不再手动合并策略、读取通知文件或另行启动。
有效 Start now 后进入 `launching`，真实启动结果看 `bootstrap_result`；提前、过期
或重复回复均不能触发新启动。重试保留控制目录和已完成出版社。独立的
`institutional prepare` 仍是集中准备命令，不会自行创建科研运行。
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
到需人工操作的阶段、`awaiting_launch` 或批次结束即提前返回当前说明，
并通过后台任务完成通知唤回 CC。
它只观察、不代答，也不延长连接、平台回答或样本获取期限。每次提交平台回复后再
观察下一阶段，同一时刻最多一个观察进程。手动运行不带此参数的 `watch` 仍默认 30 秒。
不要给生成命令再套较短的 timeout；等待期间无输出不能解释为“没有新事件”。CC
读取返回的最新平台提示后展示固定回复，不自行拼接 sleep 或轮询循环。
若接入期间已完成浏览器登录，进入平台阶段后直接回复 **完成**，
无需重新登录。连接阶段的旧回复失效时，按程序最新提示回答，不替用户自动确认完成。
**结束准备／跳过准备会终止当前批次**，不会表示“已连接”或自动重开；新建的批次
无需先结束才能测试。仅想刷新进展时读取状态，不发送结束命令。连接查询恢复不等于
浏览器验证成功；助手只查询给定的准备／运行身份，不常驻等待服务器目录中的未来运行。
助手将原话交给 `reply_argv --text`，程序按当前阶段和平台校验；后台完成通知不是回答。
在 `awaiting_launch` 阶段选择 **重新准备** 时，原样执行摘要中的
`remaining_preparation_argv`。它是原控制目录的 `preparation retry`，只重试失败及未回答
的平台，保留启动身份、已完成结果、模式和样本；不新建准备目录或重做成功平台。
没有固定或自选样本的平台手工完成仍属未验证。控制器已到期或被结束后，旧重试命令
不能使它复活，应遵循当前程序说明，不由助手另拼一个批次。无需为重试创建科研运行。

准备入口检测到验证码时，固定说明会显示“页面打开时检测到人机验证”。这是打开
页面时的观察，不证明验证已通过或机构登录已完成。仅在专用浏览器中手动完成；若
验证反复出现，可回复 **跳过此平台** 继续下一平台。程序不会代做验证码、自动刷新
或重开页面，原等待期限不延长。普通浏览器的验证和登录不会同步到专用登录目录。
这种网站验证不能靠反复重装客户端保证恢复；必要时使用平台支持的接口或手动 PDF 导入。

所选平台超时时，init 应提供 **重新准备** 或 **接受未验证状态，继续检查启动配置**，
由用户决定。准备失败不阻止科研启动，也不代表成功。空重试数组不证明成功，应按
返回的失败策略审阅真实状态，不通过另建批次绕过控制器到期。当前助手仍有效时，
保持它的专用浏览器可用。中间的 `browser_unavailable` /
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
这些命令只用于仍存活的临时准备进程，不修改科研运行；`awaiting_launch` 时读取
status 的 `summary`，主动结束后读取后台命令最终摘要。

运行后按 TUI 的 **i** 打开英文 **Institutional access** 面板：当前运行的出版社目录、
**Public + institution / Public only / Disabled**（公开＋机构／仅公开／未启用）
策略、当前机构请求的进行／就绪／缺口数量，以及最近结果。无请求的平台也会显示；
策略表示允许使用的访问途径，不代表已登录或整个平台已验证。
未选择的出版社默认 **Disabled**，不自动使用机构浏览器；公开检索和开放全文兜底仍
独立进行。准备名单中的平台会显式携带 `auto` 到运行配置；用户设置的 `public_only`
或 `disabled` 优先。只跳过准备（`none`）时，仅保留显式配置的平台模式。
新规则用于新运行；重新打开新版 TUI 不会改写旧运行的冻结策略。
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
| `client` | Windows Edge/Chrome | 明确绑定本次 init／运行；准备与该运行复用同一助手，到期自动退出。 |
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
使用；并发时选择不同的专用目录。Windows 助手只绑定一个明确运行，结束时关闭
自己启动的专用浏览器与 SSH，保留登录目录；不接管上一次运行遗留的浏览器进程。

```bash
python -m hypoloom institutional prepare --institutional-browser-mode local --provider IEEE --provider Springer
# client 模式使用 init 生成的准备命令及 Windows 入口。
python -m hypoloom institutional connections --state-root /path/to/selected-run --run-id RUN-ID --request-id CREATE-REQUEST-ID
```

集中准备在 `<repository>/state` 下创建临时私有控制目录，仅在有界启动交接期间
保留连接。Windows 生成入口携带准确的最终 `-StateRoot`、`-RunId`、`-RequestId`，
准备期间另有 `-PreparationRoot`；父级运行目录不构成接入授权。准备不创建科研运行
或归档。在其他工作目录执行时显式传 `--repository`。

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
python -m hypoloom institutional connections --state-root /path/to/run --run-id RUN-ID --request-id CREATE-REQUEST-ID --include-ui-credentials
```

将返回的 GUI 端口通过 SSH `-L` 转发到本机，输入本次 GUI 访问凭据；学校账号只在
浏览器内部输入。只读 Web 面板不提供 GUI 密码。
`--institutional-connect-timeout` 默认 30 秒，允许 3–120 秒。连通不等于学校有订阅。

旧的 `institutional connect status|start|stop` 保留为 local/existing/server 的手动诊断命令，
资源由操作员管理。新运行不再自动调用它，也不把它的常驻资源算作运行级连接。

### Windows 终端统一 Init 入口

**每次 CC init** 都在本次希望使用的 Windows 电脑上执行一次程序生成的入口。
入口绑定审阅中的 `state_root`、`run_id` 和创建请求 ID；另一台电脑的旧助手不会
自动连接。Setup 不安装登录自启，也不发现未来运行。

首次使用或更换 Windows 机器／用户账户时，先完成下方的
[SSH 免交互认证检查](#首次接入或更换-windows-机器账户)。能用密码下载脚本，不代表
后台助手已能登录服务器。

1. 保留服务器上的 `/autoresearch init`，选择 Windows 客户端模式。程序会给出
   `script_source`、`setup_inputs` 和准确的 `powershell` 启动入口。
2. 在 Windows 通过 SFTP/SCP 或文件传输工具下载该服务器上的原始
   `institutional-connect.ps1`。升级后重新下载；不要把助手转贴的脚本正文保存为文件。
   Windows 无需安装 Hypoloom 或 Python。
3. 在下载目录打开 PowerShell 7.2+，**原样执行本次程序生成的入口**，包括
   `-StateRoot`、`-RunId`、`-RequestId` 及提供时的 `-PreparationRoot`。
   也可运行以下交互式入口，将本次 init 显示的范围值填入提示；不自行生成 ID 或沿用旧值：

   ```powershell
   pwsh -NoProfile -File .\institutional-connect.ps1 -Action Setup
   ```

4. 输入 SSH 目标及需要补充的服务器路径。Setup 校验 SSH／服务器 Python，将原文件
   安装到 `%LOCALAPPDATA%\Hypoloom\institutional-connect.ps1`，启动本次连接。
   一次性计划任务使用隐藏窗口，没有登录触发器或自动任务重启。
   Setup／Connect 返回后可以关闭这个 PowerShell 窗口。
5. 回到原 CC init。程序检测实际连接、打开所选平台，并在固定回复后验证样本。
   进入 `awaiting_launch` 即从 status 的 `summary` 开始审阅，不等待后台退出。
   Start now 将同一助手交接给该运行；Prepare only／取消执行 `finish_argv`。
   交接等待最多 600 秒，到期释放。

没有图形传输工具时，在 Windows 下载目录打开 SFTP：

```powershell
sftp research-server
```

在 `sftp>` 下载 init 显示的 `script_source` 后退出。以下为示例路径，按实际值替换；
含空格时加双引号：

```text
get /path/to/Hypoloom/hypoloom/connection_assets/institutional-connect.ps1
bye
```

文件传输保留原始字节。旧入口发生乱码或参数拼写错误时，重新下载原文件，不继续
修补聊天生成的源码。旧命令停在 `>>` 时先 Ctrl+C。SSH 目标不是密码，Windows 的
回环地址指向 Windows；Setup 前验证 `ssh -o BatchMode=yes <目标> exit` 返回 0。
任务已启动不等于浏览器已连通。

**升级已有客户端：** 先用 Ctrl+C 停止旧前台 Run。新 Setup 核对归属后清理匹配的
旧登录自启／发现任务，再建立本次一次性任务；不覆盖其他账户或程序的任务。出现
`scheduled_task_ownership_mismatch` 时检查对应任务和账户，不按通配符批量删除。
Windows 可能显示短用户名，归属按 SID 校验。旧版手工建立的固定端口或其他任务仍由
操作员明确停止。新连接前正常关闭旧 Hypoloom 专用浏览器；保留登录目录及普通浏览器。
新助手不能接管旧浏览器进程；覆盖脚本文件不会更新已运行的助手或冻结 Host。

如果改用电脑 B，还需**在电脑 A**清理历史常驻助手；服务器升级不能删除 A 的本地任务。
先更新服务器仓库，再在 A 的 PowerShell 7.2+ 执行下面完整代码块。按提示填写旧任务
原来使用的真实 SSH 目标（别名也必须相同）及 init 显示的实际仓库路径；不预填示例值。
程序先下载新版原始脚本，下载失败即停止，不覆盖旧文件；成功才替换旧安装位置。
`Uninstall` 不传运行范围，只清理核验归属的对应旧任务，不影响新版单运行任务。

```powershell
& {
    $ErrorActionPreference = 'Stop'
    if (-not $IsWindows -or $PSVersionTable.PSVersion -lt [version]'7.2') {
        throw '请在 Windows 的 PowerShell 7.2+（pwsh）中运行整块。'
    }
    $researchServer = (Read-Host '旧助手使用的实际 SSH 目标（须为原来的 user@host 或别名）').Trim()
    if ($researchServer -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.@-]*$') {
        throw '请输入实际 SSH 目标，不是密码或整条 ssh 命令。'
    }
    $researchRepository = (Read-Host '已更新服务器上的仓库绝对路径（复制 init 显示的实际值）').Trim().TrimEnd('/')
    if (-not $researchRepository.StartsWith('/') -or $researchRepository -match '[\r\n\x00]') {
        throw '请输入实际 Linux 仓库绝对路径。'
    }
    $researchConnector = Join-Path $env:LOCALAPPDATA 'Hypoloom\institutional-connect.ps1'
    $researchDirectory = Split-Path $researchConnector
    New-Item -ItemType Directory -Force $researchDirectory | Out-Null
    $researchTemporary = Join-Path $researchDirectory ([IO.Path]::GetRandomFileName())
    try {
        & scp.exe -o BatchMode=yes -o ConnectTimeout=10 "${researchServer}:$researchRepository/hypoloom/connection_assets/institutional-connect.ps1" $researchTemporary
        if ($LASTEXITCODE -ne 0) { throw '下载失败，未覆盖已安装脚本；请保留上方 SSH 错误。' }
        if ((Get-Item -LiteralPath $researchTemporary).Length -eq 0) { throw '下载内容为空，未覆盖已安装脚本。' }
        Move-Item -LiteralPath $researchTemporary -Destination $researchConnector -Force
    } finally {
        if (Test-Path -LiteralPath $researchTemporary) { Remove-Item -LiteralPath $researchTemporary }
    }
    & pwsh -NoProfile -File $researchConnector -Action Uninstall -Server $researchServer -Repository $researchRepository
    if ($LASTEXITCODE -ne 0) { throw '清理失败，请保留上方错误；不要按通配符删除任务。' }
    $researchSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $researchTaskName = "Hypoloom-Institutional-$researchServer-$researchSid"
    $researchRemaining = @(Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskName -ceq $researchTaskName })
    if ($researchRemaining.Count) { throw "此旧任务仍存在，清理未完成: $researchTaskName" }
    Write-Host "已确认此旧任务不存在: $researchTaskName"
}
```

成功提示只证明**输出的准确任务名**已不存在，不表示 A 上所有任务均已清理。
旧专用浏览器正常关闭即可，保留登录目录。仅看到 `task_stopped` 不证明旧任务已删除。

助手只跟随本次准备／运行。科研终结或进入 `STOPPING` 后，清理自己启动的 SSH 和
专用浏览器，保留登录目录。短断线只允许恢复同一运行；连续失联 300 秒后本地退出。
睡眠唤醒先核验期限，到期后不自动重连，也不寻找另一运行。此后若仍需机构接入，
须明确执行目标运行新生成的入口。

关闭 CC 或交互式 SSH 会话不会停止已经启动的受管科研运行；尚未启动的准备和交接
等待仍有期限。需要该机构通道时 Windows 和专用浏览器应保持可用；意外关闭只使该
通道不可用，有限公开／OA 回退和其他研究继续，不保证每篇 PDF 可得。只有诊断用的
前台 `-Action Run` 才需要保留终端。

本机执行入口、PowerShell 7.2+ 和有效 SSH 认证仍是前提，服务器不能凭空启动未连接
的电脑。登录和 MFA 在浏览器完成；旧运行不迁移。Windows 界面与生命周期仍需在实际
客户端验收，自动化测试不能替代此结论。

### 客户端前提与手动诊断

将 `hypoloom/connection_assets/institutional-connect.ps1` 复制到 Windows 固定位置。
要求 PowerShell 7.2+，Windows 不需要安装项目或 Python。SSH 目标为已有 `user@host`
或 SSH 别名，先确认主机密钥并配置 SSH key／ssh-agent。脚本使用 BatchMode，
不保存密码。Linux SSH 服务须允许远程 Unix socket 转发；脚本通过给定的服务器
路径和本次 init 的准确运行范围调用项目 CLI。

#### 首次接入或更换 Windows 机器／账户

复制连接脚本不会迁移旧机器的 SSH 密钥、主机确认记录、浏览器登录状态或本次接入授权。
先保证新机器能访问服务器；如果服务器地址通过 VPN／组网软件访问，新机器也需接入
相同网络。SSH 目标仍是 Linux 的 `user@host` 或本机已配置的别名；本机 Windows
用户名改变，不意味着服务器用户名也要改变。

在新机器的普通 PowerShell 中检查两种登录：

```powershell
$researchServer = (Read-Host '服务器 user@host 或 SSH 别名（不是密码）').Trim()
ssh -o ConnectTimeout=5 $researchServer exit
Write-Host "普通登录退出码：$LASTEXITCODE"
ssh -o BatchMode=yes -o ConnectTimeout=5 $researchServer exit
Write-Host "免交互登录退出码：$LASTEXITCODE"
```

首次连接提示主机指纹时，核对服务器指纹后再接受。服务器密码只在 SSH 的提示中输入。

| 结果 | 处理 |
| --- | --- |
| 超时、连接拒绝或主机名无法解析 | 检查网络／VPN、服务器地址、端口及 SSH 别名配置。 |
| 主机密钥验证失败 | 核对服务器身份，完成主机确认；不要关闭主机密钥检查。 |
| 普通登录为 `0`，免交互为 `255` 且提示 `Permission denied (publickey,password)` | 网络与密码登录可用，但当前用户的免交互认证未通过；按下面步骤配置密钥／代理。 |
| 两次均为 `0` | SSH 已就绪，直接重试 Setup；无需再生成或上传密钥。 |

Setup 的 `init_ssh_connection_unavailable_check_key_and_known_host` 是连接预检错误，
本身不能区分上述原因，也不是出版社或 Edge 登录失败。两次 SSH 均通过而 Setup
仍报错时，保留原始报错并检查其使用的同一目标、服务器路径和远程命令。

需要配置密钥时，先在 **Windows 管理员 PowerShell** 启用 ssh-agent：

```powershell
Set-Service ssh-agent -StartupType Automatic
Start-Service ssh-agent
```

随后回到**实际运行助手的 Windows 用户**的普通 PowerShell。下面使用标准用户级
`~/.ssh/id_ed25519`，不限定于 Hypoloom；已有私钥不会覆盖。若已使用其他路径的密钥，
将 `$clientKey` 改为它的路径并加载到同一用户的代理。生成时按提示设置密钥口令；
`ssh-add` 可能要求该口令，它与服务器登录密码不同。

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $researchServer = (Read-Host '服务器 user@host 或 SSH 别名（不是密码）').Trim()
    if ($researchServer -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.@-]*$') {
        throw '请输入 SSH 目标或别名，不要输入整条 ssh 命令。'
    }
    $clientKey = Join-Path $env:USERPROFILE '.ssh\id_ed25519'
    New-Item -ItemType Directory -Force (Split-Path $clientKey) | Out-Null

    if (-not (Test-Path -LiteralPath $clientKey)) {
        & ssh-keygen -t ed25519 -f $clientKey
        if ($LASTEXITCODE -ne 0) { throw '生成密钥失败，已停止。' }
    }
    if (-not (Test-Path -LiteralPath "$clientKey.pub")) {
        $publicKey = & ssh-keygen -y -f $clientKey
        if ($LASTEXITCODE -ne 0) { throw '导出公钥失败，已停止。' }
        $publicKey | Set-Content -LiteralPath "$clientKey.pub" -Encoding ascii
    }

    & ssh-add $clientKey
    if ($LASTEXITCODE -ne 0) { throw '加载密钥失败，请检查 ssh-agent 服务。' }

    $installPublicKey = 'umask 077; mkdir -p ~/.ssh && chmod 700 ~/.ssh && { printf "\n"; tr -d "\r"; } >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'
    Get-Content -LiteralPath "$clientKey.pub" | & ssh $researchServer $installPublicKey
    if ($LASTEXITCODE -ne 0) { throw '上传公钥失败，已停止。' }

    & ssh -o BatchMode=yes -o ConnectTimeout=5 $researchServer exit
    if ($LASTEXITCODE -ne 0) { throw '免交互认证仍未通过，请保留上方 SSH 错误。' }
    Write-Host '免交互 SSH 认证成功，可以重新执行 Hypoloom Setup。'
}
```

这段代码只上传公钥，追加到 Linux 用户的 `authorized_keys`，保留其他机器的授权。
给另一台服务器配置时可复用同一用户密钥，只更改 SSH 目标并上传公钥；不要覆盖已有
密钥或授权列表。私钥留在 Windows，系统配置可参考
[Windows OpenSSH 密钥管理](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement)。
认证成功后，在下载原始脚本的目录中重新执行：

```powershell
pwsh -NoProfile -File .\institutional-connect.ps1 -Action Setup
```

填写本次 init 显示的目标、路径及准确的 StateRoot／RunId／RequestId／PreparationRoot；
标准安装可使用 Python 默认值。回到原 CC：准备仍等待时自动检测，在 awaiting_launch
仅按提供的选项“重新准备”；已到期控制器不能凭旧入口复活。不必为了 SSH 配置另建
研究运行。此步骤只准备 SSH 认证，新机器的出版社登录仍在专用浏览器完成。

#### 手动连接诊断

使用新版本地脚本时，先在 Windows 读取 **Status**。它只读本机计划任务和最近的安全
诊断事件，不连接 SSH、不打开浏览器、不重启助手或创建目录；无需先停后台任务，
再用前台 Run 反复复现。复制本次 init 生成入口中的准确 Server 和 RequestId；
RequestId 是创建请求 ID，不是运行 ID。此只读操作不需要 Repository、StateRoot
或 PreparationRoot。

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $researchConnector = Join-Path $env:LOCALAPPDATA 'Hypoloom\institutional-connect.ps1'
    $researchServer = (Read-Host '本次 init 入口中的准确 Server 值（不是密码）').Trim()
    $researchRequest = (Read-Host '本次 init 入口中的准确 RequestId 值').Trim()
    & pwsh -NoProfile -File $researchConnector -Action Status -Server $researchServer -RequestId $researchRequest
    if ($LASTEXITCODE -ne 0) { throw '读取 Status 失败，请保留上方诊断输出。' }
}
```

结果包括任务状态／最近结果、`diagnostic_path` 和 `recent_events`。安全事件位于
`%LOCALAPPDATA%\Hypoloom\ConnectionLogs-<Server>\<RequestId>.jsonl`，保留量有上限。
日志是排障观察，不是连接授权，也不能保证浏览器当前健康。仅看任务 Ready 或
LastTaskResult 无法确定退出原因。日志不存在、旧助手未记录或本地读取失败时，
原因仍属未知；检查 `local_status_reason` 及提供时的 `diagnostic_reason`，保留安全输出。

| Status 观察 | 含义与处理 |
| --- | --- |
| `run_terminal` | 服务器已结束该连接范围，包括科研停止中／终态。属于正常退出，不代表出版社验证成功。 |
| `launch_review_expired` | 启动前审阅窗口到期，助手正常退出；该事件没有启动科研。 |
| `run_connection_expired` | 连续 300 秒未能确认连接／控制检查正常，计入睡眠时间；不等于仍打开的出版社页面已坏。先看之前的安全原因，再决定是否重连原目标运行。 |
| `run_discovery_timeout` | 本次范围的服务端状态查询未在期限内返回，不等于浏览器崩溃或机构登录失败。助手会丢弃该查询会话，在原失联期限内重试。 |
| `cleanup_failed` 事件 | 资源清理失败，应同时保留该事件和原始退出／失败原因，不相互覆盖；不能据此声称浏览器及全部子进程已关闭。 |

`run_terminal` 和 `launch_review_expired` 属于正常退出；连接过期和身份／隐私错误
仍属于失败。排障优先读取上述 Status，不反复重装或启动第二个前台助手。确有需要
时才以原范围执行前台 Run，并确保相同范围的后台实例已停止；不按进程名批量终止，
也不接管其他运行的浏览器。

当前助手复用一条 SSH 会话查询状态；每次仍重新读取服务端状态并校验同一运行身份。
SSH 进程仍在或旧结果可读，都不能延长连接期限。查询超时先关闭该会话再重试，迟到
结果不会被复用。下次 init 时，按其原始文件下载步骤更新本地脚本，再执行该次运行
的接入命令即可，无需自行添加 SSH 参数或手改脚本。

Setup／Connect 在启动本次范围的助手前准备目录权限，失败会直接显示在当前终端。对于当前
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
默认目录为 `%LOCALAPPDATA%\Hypoloom\Institutional-<Server>-msedge`（Chrome 同理），
按服务器账户隔离；可通过 `-BrowserExecutable`／`-BrowserProfile` 指定原来完成登录的专用目录。
不导出 Cookie，不复制登录凭据；目录权限限于当前用户、SYSTEM 和管理员。
助手仅拥有自己为本次范围启动的浏览器，不接管已运行的手工浏览器或旧运行浏览器。

**从旧版端口 0 浏览器更新：** 安排一次客户端连接中断，以原服务器和连接参数运行
`-Action Stop`，停止旧发现任务；手动关闭 Hypoloom 专用浏览器的所有窗口。普通
浏览器不需要关闭，专用目录不需要删除。再按上面的文件流程下载原始脚本并运行
`-Action Setup`。仅覆盖文件或重开 CC 不会更新已运行的任务／浏览器启动参数。
若看到 `browser_restart_required_close_dedicated_window`，说明仍有旧参数的专用
进程，请正常退出该专用浏览器后重新 Connect；程序不会强杀浏览器或清理登录数据。

原准备批次仍等待连接时回复“继续连接”，awaiting_launch 时按提供的选项“重新准备”，无需为此
创建科研运行。在新专用窗口打开 [Cloudflare 官方检测](https://debug.challenges.cloudflare.com/)
检查 Automation Check，再在 ScienceDirect 实际完成验证和论文样本检查；消除已知
启动副作用不代表网站一定放行。登录、验证码及 MFA 仍由本人在浏览器完成。

助手确认准确的准备／运行范围后才建立 SSH 转发。服务端目标为每次运行的私有
Unix socket，无需 `-RemotePort`；查询失败不能凭缓存创建新连接。短暂重试受原有
准备／获取期限及连续失联 300 秒的本地期限约束；另一运行的可用连接不是恢复目标。
Ctrl+C、Stop 或运行退出只清理本助手拥有的 SSH／浏览器资源，保留登录目录。
没有登录触发器或任务重启使它常驻等待未来运行。服务器不能唤醒电脑，登录态到期
和 MFA 仍由出版社／学校决定。

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

1. 按 **i** 打开 **Institutional access**，一行一个出版社，查看访问策略、当前请求数量和最近观察。
2. **Public + institution** 表示允许尝试当前机构会话；**Public only** 表示用户选择仅公开；
   **Disabled** 表示机构渠道未启用。后两者均不自动使用该平台的机构浏览器。
   **No institutional requests** 不代表公开获取失败。**Full text ready** 也不代表 Agent 已读或已下载 PDF。
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

明确提供非受管 `--institutional-browser-url` 时保留原来的操作员端点行为，不属于
新版单运行 Windows 助手。旧冻结 Host 保持原实现，不自动迁移。新 init 使用上述
绑定范围的客户端入口，不再配置常驻固定端口隧道或 Windows 登录自启任务。
Web 面板的 HTTP 端口与浏览器 CDP 无关。

如果曾手工创建浏览器／隧道任务，应明确停止其自动启动，新 Setup 前关闭旧专用
浏览器。Setup 仅在核对归属后清理匹配的旧助手任务，不删除其他任务或接管监听。
保留私有浏览器目录即可保留登录数据；Cookie／storage 不复制到服务器。

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
逐篇解验证码。新 init 使用新版单运行助手；旧运行不会被自动改动。

需要专门测试所有出版社时，使用常规审阅策略：

1. 在已更新的服务器仓库中新开 CC 会话，运行 `/autoresearch init`。
2. 选择 Windows 客户端模式及 IEEE、ScienceDirect、Springer、ACM（ACM 需主动选择）。
   在所选 Windows 上用新版原文件执行本次生成的 Setup／Connect 入口一次；
   先关闭旧专用浏览器，不沿用旧助手的接入授权。
3. 每个平台打开后，在需要时完成机构认证，回 CC 回复 **完成**。已有登录态可直接完成；
   固定样本由程序自动验证，不需另找 DOI 或逐篇处理验证码。
4. 检查每个平台的状态、来源、格式和页数。验证码、403、身份未确认或超时都不是通过。
   有失败时，可选 **接受未验证状态，继续检查启动配置**，停在最终启动确认处验收；
   `awaiting_launch` 表示准备已结束；只有你选择 **Start now** 才启动科研。
   选择 Prepare only／取消时，由 CC 执行返回的 `finish_argv`。
5. 检查助手拥有的浏览器／SSH 已清理且登录目录保留；下一次 init 必须执行新入口，
   旧电脑／旧助手不能自动接入。分别实测短断线、连续失联超过 300 秒和睡眠越过期限；
   自动化测试不能证明实际 Windows 界面和唤醒行为已经通过。

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
