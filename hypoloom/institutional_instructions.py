"""Program-rendered client setup and preparation choices, without saved state."""
from pathlib import Path
import shlex


REPLIES = {
    "继续连接": "retry_connection", "跳过准备": "finish",
    "完成": "complete", "验证样本": "verify", "跳过此平台": "skip",
    "仅公开": "public_only", "结束准备": "finish",
}


def _powershell_literal(value):
    value = str(value)
    if any(c in value for c in "\x00\r\n"):
        raise ValueError("client setup paths must not contain control characters")
    return "'" + value.replace("'", "''") + "'"


def client_entry(repository, python):
    repository = Path(repository).resolve()
    source = Path(__file__).with_name("connection_assets") / "institutional-connect.ps1"
    # Obtain the entire script through the operator's existing SSH trust before
    # replacing the stable local entry. No password, host guess or second init.
    download = _powershell_literal("cat -- " + shlex.quote(str(source)))
    command = ("pwsh -NoProfile -File $researchConnector -Action Connect -Server $researchServer"
               f" -Repository {_powershell_literal(repository)}"
               f" -PythonExecutable {_powershell_literal(python)}"
               f" -RunsRoot {_powershell_literal(repository / 'state')}")
    script = f"""$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion -lt [version]'7.2') {{ throw '请先运行 pwsh 进入 PowerShell 7.2+ 后再执行。' }}
if (-not (Get-Command pwsh -ErrorAction SilentlyContinue)) {{ throw '请先安装 PowerShell 7.2 或更高版本。' }}
$researchServer = Read-Host '请输入平时 SSH 使用的 user@host 或别名（不要输入密码）'
if ($researchServer -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.@-]*$') {{ throw '请输入 SSH 目标或别名，不要输入整条 ssh 命令。' }}
$researchConnector = Join-Path $env:LOCALAPPDATA 'Hypoloom\\institutional-connect.ps1'
New-Item -ItemType Directory -Force (Split-Path $researchConnector) | Out-Null
$researchSource = & ssh -o BatchMode=yes -o ConnectTimeout=5 $researchServer {download}
if ($LASTEXITCODE -ne 0) {{ throw '读取入口失败，请检查 SSH 密钥、主机确认和服务器路径。' }}
$researchTemporary = Join-Path (Split-Path $researchConnector) ([IO.Path]::GetRandomFileName())
try {{
    $researchSource | Set-Content -LiteralPath $researchTemporary -Encoding utf8
    Move-Item -LiteralPath $researchTemporary -Destination $researchConnector -Force
}} finally {{
    if (Test-Path -LiteralPath $researchTemporary) {{ Remove-Item -LiteralPath $researchTemporary }}
}}
{command}
if ($LASTEXITCODE -ne 0) {{ throw '客户端接入未完成，请按上方错误处理后重新执行。' }}"""
    return {"runs_on": "Windows client", "requires": ["PowerShell 7.2+", "existing SSH authentication"],
            "script_source": str(source), "powershell": script,
            "note": "Starts client discovery only; return to the same init conversation."}


def reply_choices(view):
    allowed = set(view.get("actions", ()))
    connection = view.get("state") == "waiting_for_connection"
    finish = "跳过准备" if connection else "结束准备"
    return [text for text, action in REPLIES.items()
            if action in allowed and (action != "finish" or text == finish)]


def render(view):
    """Display only observations supplied by the live preparation process."""
    state = view["state"]
    if state == "inactive":
        return ("准备进程已结束或当前不可用，不能继续向旧批次发送回答。\n"
                "回到原来的 init 会话回复：重新准备。助手先读取原后台任务的最终结果，"
                "执行其中程序生成的 remaining_preparation_argv；为空则无需重开。"
                "已完成的平台和模式保留，不要新开 CC 会话或启动科研运行。")
    lines = [f"准备状态：{state}。"]
    if "remaining_seconds" in view:
        lines.append(f"本次等待剩余 {view['remaining_seconds']} 秒；连接等待和平台回答各自都有期限。")
    if state == "waiting_for_connection":
        lines.append("浏览器连接尚未验证成功；本次检查未进入平台操作。")
        if "client_entry" in view:
            lines.extend(["首次接入或连接任务未运行时，在 Windows 本地 PowerShell 执行下面完整代码块；"
                          "保留当前 SSH/CC 会话。程序会询问 SSH 目标，获取入口并启动连接任务。",
                          "```powershell", view["client_entry"]["powershell"], "```",
                          "看到客户端连接任务已启动后，回到此会话回复：继续连接。启动任务不等于浏览器已连通。"])
        else:
            lines.append("完成所选浏览器的连接准备后回复：继续连接。")
    elif state == "waiting_for_user":
        lines.append(f"{view['provider']} 页面已打开。需要登录时，仅在浏览器完成学校认证和 MFA。")
        lines.append("完成表示结束此平台的人工准备；未验证样本时，不代表登录或订阅已验证。")
    elif state in {"checking_connection", "preparing_platform", "processing"}:
        lines.append("程序正在处理；尚未请求本阶段的人工完成确认。")
    elif state == "finishing":
        lines.append("正在结束并清理临时准备资源，请读取后台任务的最终结果。")
    choices = reply_choices(view)
    if choices:
        lines.append("固定回复：" + " / ".join(choices) + "。")
    if "验证样本" in choices:
        lines.append("选择验证样本时另提供一条该平台 DOI 或论文 URL；不要提供密码、Cookie 或验证码。")
    return "\n".join(lines)
