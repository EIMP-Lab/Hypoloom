"""Program-rendered client setup and preparation choices, without saved state."""
from pathlib import Path


REPLIES = {
    "继续连接": "retry_connection", "跳过准备": "finish",
    "完成": "complete", "验证样本": "verify", "跳过此平台": "skip",
    "仅公开": "public_only", "结束准备": "finish",
}
CLIENT_SETUP_COMMAND = "pwsh -NoProfile -File .\\institutional-connect.ps1 -Action Setup"


def client_entry(repository, python):
    repository = Path(repository).resolve()
    source = Path(__file__).with_name("connection_assets") / "institutional-connect.ps1"
    inputs = {"repository": str(repository), "python_executable": str(python),
              "runs_root": str(repository / "state")}
    if any(c in value for value in [str(source), *inputs.values()] for c in "\x00\r\n"):
        raise ValueError("client setup paths must not contain control characters")
    # Only a file launch is shown to the model. The installation program travels
    # through SSH file transfer, never through a model-generated code block.
    return {"runs_on": "Windows client", "requires": ["PowerShell 7.2+", "existing SSH authentication"],
            "script_source": str(source), "setup_inputs": inputs,
            "powershell": CLIENT_SETUP_COMMAND + "\n",
            "note": "Download the original script via SFTP/SCP; run Setup in its local directory. Return to the same init conversation."}


def reply_choices(view):
    allowed = set(view.get("actions", ()))
    connection = view.get("state") == "waiting_for_connection"
    finish = "跳过准备" if connection else "结束准备"
    return [text for text, action in REPLIES.items()
            if action in allowed and (action != "finish" or text == finish)]


def render_results(rows):
    """Explain the existing final observations without asserting account identity."""
    lines = ["准备结果（论文样本校验，不是机构账号身份认证）："]
    identity_reasons = {
        "article_identity_unconfirmed": "论文页面身份校验未通过，尚不能证明已下载全文",
        "pdf_target_unconfirmed": "PDF 下载目标与论文的对应关系未确认，未接纳为全文",
        "document_validation_failed": "取得内容后文档校验未通过，未接纳为目标论文全文",
    }
    for row in rows:
        state = row["state"]
        if state == "identity_unconfirmed":
            samples = [s for s in row.get("samples", ()) if s["state"] == state]
            reasons = [identity_reasons.get(s.get("reason_code"),
                "结果未区分校验阶段，尚不能证明已下载全文") for s in samples]
            detail = "；".join(dict.fromkeys(reasons)) if reasons else "尚不能确定失败阶段或是否已下载全文"
            detail += "。这是论文身份未确认，不是机构账号身份未确认；重复登录不保证解决"
        elif state == "sample_verified":
            detail = "只证明该样本当前可读，不证明机构登录成功或平台整体订阅权限"
            for sample in row.get("samples", ()):
                if sample.get("media_type") == "application/pdf":
                    origin = "公开来源" if sample.get("acquisition_source") == "public_pdf" else "浏览器"
                    detail += f"；已从{origin}取得并校验 PDF（{sample['page_count']} 页）"
                elif sample.get("media_type"):
                    detail += "；取得的是网页全文，不代表已下载 PDF"
        elif state == "verification_required":
            detail = "检测到人机验证，自动获取未通过；重复 init 或重试不保证解决，可接受未验证并继续"
        else:
            detail = "未验证样本可读性或机构权限，以本次状态为准"
        lines.append(f"{row['provider']}：{state} — {detail}。")
    return "\n".join(lines)


def render(view):
    """Display only observations supplied by the live preparation process."""
    state = view["state"]
    if state == "inactive":
        return ("准备进程已结束或当前不可用，不能继续向旧批次发送回答。\n"
                "回到原来的 init 会话回复：重新准备。助手先读取原后台任务的最终结果，"
                "按需执行其中程序生成的 remaining_preparation_argv，重试失败或未完成的平台。"
                "空数组不证明登录或准备成功；若旧摘要仍有失败项，须重新生成这些平台的准备命令。"
                "已完成的平台和模式保留，不要新开 CC 会话或启动科研运行。")
    lines = [f"准备状态：{state}。"]
    if "remaining_seconds" in view:
        scope = "沿用此样本的原获取期限，回答不会延长" if view.get('sample_url') else "连接等待和平台回答各自都有期限"
        lines.append(f"本次等待剩余 {view['remaining_seconds']} 秒；{scope}。")
    if state in {"checking_connection", "waiting_for_connection"}:
        automatic = view.get("automatic_connection_checks", False)
        lines.append("浏览器连接尚未验证成功；本次检查未进入平台操作。")
        if automatic:
            lines.append("程序会在同一期限内自动重检连接，成功后自动打开平台；无需反复执行接入代码或等待助手发起重试。")
            lines.append("连接助手已运行时保留它和专用浏览器；连接成功后按当前平台提示操作，无需先回复‘已连接’。")
            lines.append("继续连接仅提前下一次检查，不延长等待期限；状态查询也不会延长期限。")
        connection = view.get("connection", {})
        reasons = {
            "cdp_timeout": "浏览器控制连接检查超时",
            "cdp_connection_refused": "浏览器控制端点拒绝连接",
            "cdp_connection_closed": "浏览器控制连接已断开",
            "cdp_authorization_failed": "浏览器控制连接认证失败",
            "cdp_endpoint_unavailable": "浏览器控制端点或转发返回服务错误",
            "cdp_context_unavailable": "浏览器未提供可用的会话上下文",
            "cdp_connection_failed": "浏览器控制连接未成功，尚无法确定具体原因",
            "browser_driver_unavailable": "服务器缺少浏览器控制依赖",
        }
        reason = connection.get("reason_code")
        if reason in reasons:
            lines.append(f"最近一次检查：{reason}（{reasons[reason]}）。")
        if "client_entry" in view:
            entry = view["client_entry"]
            inputs = entry.get("setup_inputs")
            lines.extend(["首次接入或更新时，通过 SFTP/SCP 从当前服务器下载原始脚本文件到 Windows；不要把助手转贴的脚本正文保存为文件。",
                          "服务器文件：" + entry["script_source"],
                          "在下载文件所在目录打开 PowerShell 7.2+，执行这个固定入口：",
                          "```powershell", CLIENT_SETUP_COMMAND, "```",
                          "程序会询问 SSH 目标（你从 Windows 登录服务器使用的 user@host 或别名，不是密码）及服务器路径。"])
            if inputs:
                lines.extend(["服务器仓库：" + inputs["repository"],
                              "服务器 Python：" + inputs["python_executable"],
                              "服务器运行目录：" + inputs["runs_root"]])
            else:
                # An already-running older preparation has only an inline script.
                # Never render or parse that source as a substitute for path data.
                lines.append("此旧批次未单独提供路径值；请使用本次 init 审阅中的仓库、Python 和运行目录。")
            lines.extend(["Setup 安装到本机固定位置并执行 Connect；返回后可关闭此 PowerShell 窗口，保留当前 init 会话。",
                          "后续 init 复用隐藏后台任务；任务正常时无需重复下载或安装。",
                          ("接入后回到此会话查看进展；程序会自动检测实际连接。" if automatic else
                          "看到客户端连接任务已启动后，回到此会话回复：继续连接。") + "启动任务不等于浏览器已连通。"])
        else:
            lines.append("完成所选浏览器的连接准备后，程序会自动检测。" if automatic else
                         "完成所选浏览器的连接准备后回复：继续连接。")
    elif state == "waiting_for_user" and view.get('sample_url'):
        lines.append(f"{view['provider']} 样本页面需要人工处理：{view['sample_url']}。")
        lines.append("全文样本检查遇到人机验证或登录页；先在当前专用浏览器页面手动完成，再回复：完成。")
        lines.append("程序保留当前样本、标签页与原获取期限，继续校验同一份内容；不会重新打开登录入口或自动操作验证码。")
        lines.append("完成不代表全文已验证；程序继续检查，只有 sample_verified 才证明该样本可读。若仍无法通过，可跳过此平台或结束准备。")
    elif state == "waiting_for_user":
        lines.append(f"{view['provider']} 页面已打开。需要登录时，仅在浏览器完成学校认证和 MFA。")
        if view['provider'] == 'Springer':
            from .institutional_access import provider_entry_url
            lines.append("Springer 机构入口：" + provider_entry_url('Springer'))
            lines.append("在 Find your institution 中搜索学校并选择机构登录；若找不到学校，使用学校图书馆提供的 Springer 访问入口。个人账号登录不等于机构授权。")
        elif view['provider'] == 'ScienceDirect':
            lines.append("文章页和 PDF 下载页可能分别出现人机验证；登录后仍须以样本检查结果判断全文是否可获取。")
        lines.append("如果刚才已在该专用浏览器完成登录，无需重新登录，直接回复：完成。连接会自动检测，连接完成不等于平台准备已完成。")
        if view.get("reason_code") == "verification_required":
            lines.append("页面打开时检测到人机验证；这不是连接失败，也不代表机构登录已完成。")
            lines.append("仅在浏览器手动完成验证；若验证反复出现，可回复：跳过此平台。程序不会代做验证码、自动刷新或重开页面，等待期限不延长。")
        if view.get("completion_samples"):
            lines.append("回复完成后，程序自动获取并校验以下全文样本，无需另外寻找 DOI：")
            lines.extend(view["completion_samples"])
            lines.append("默认固定样本为公开全文，可用验证样本另提供 DOI；通过仅证明该篇当前可读，不代表登录或订阅已验证。验证失败会保留具体状态，允许重试或接受未验证。")
            lines.append("固定样本复用运行时的公开 PDF 获取与校验，未取得时再检查专用浏览器；结果区分公开 PDF、浏览器 PDF 与网页全文。遇到验证码直接报告自动获取失败，不要求逐篇人工验证。")
        else:
            lines.append("完成表示结束此平台的人工准备；未验证样本时，不代表登录或订阅已验证。")
    elif state in {"preparing_platform", "processing"}:
        lines.append("程序正在处理；尚未请求本阶段的人工完成确认。")
    elif state == "finishing":
        lines.append("正在结束并清理临时准备资源，请读取后台任务的最终结果。")
    choices = reply_choices(view)
    if choices:
        lines.append("固定回复：" + " / ".join(choices) + "。")
        if '结束准备' in choices or '跳过准备' in choices:
            lines.append("结束准备／跳过准备会终止本批次，不表示连接完成，也不会自动重新准备；仅在确实要停止时选择。")
    if "验证样本" in choices:
        lines.append("选择验证样本时另提供一条该平台 DOI 或论文 URL；不要提供密码、Cookie 或验证码。")
    return "\n".join(lines)
