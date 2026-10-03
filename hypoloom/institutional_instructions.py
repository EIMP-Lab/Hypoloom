"""Program-rendered client setup and preparation choices, without saved state."""
from pathlib import Path
import shlex
from textwrap import indent


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
    # ASCII transport avoids native stdout decoding through a Windows code page.
    # This is byte transport, not a digest or a second source of script authority.
    download = _powershell_literal("base64 -- " + shlex.quote(str(source)))
    script = f"""$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion -lt [version]'7.2') {{ throw '请先运行 pwsh 进入 PowerShell 7.2+ 后再执行。' }}
if (-not (Get-Command pwsh -ErrorAction SilentlyContinue)) {{ throw '请先安装 PowerShell 7.2 或更高版本。' }}
Write-Host '填写从这台 Windows 登录 Linux 服务器的 SSH 目标；127.0.0.1/localhost 指向本机。'
$researchServer = (Read-Host '服务器 user@host 或 SSH 别名（不是密码，也不是浏览器地址）').Trim()
if ($researchServer -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.@-]*$') {{ throw '请输入 SSH 目标或别名，不要输入整条 ssh 命令。' }}
$researchConnector = Join-Path $env:LOCALAPPDATA 'Hypoloom\\institutional-connect.ps1'
$researchSourceCommand = {download}
$researchSource = & ssh -o BatchMode=yes -o ConnectTimeout=5 $researchServer $researchSourceCommand
if ($LASTEXITCODE -ne 0) {{
    throw "读取入口失败，已停止。检查服务器目标和路径；先用 ssh $researchServer exit 确认登录，再用 ssh -o BatchMode=yes $researchServer exit 验证免交互认证。若仍需密码，请先配置 SSH 密钥/ssh-agent。"
}}
if ([string]::IsNullOrWhiteSpace(($researchSource -join "`n"))) {{ throw '服务器未返回入口内容，已停止；请检查服务器目标和安装路径。' }}
$researchSourceBytes = [Convert]::FromBase64String(($researchSource -join ''))
New-Item -ItemType Directory -Force (Split-Path $researchConnector) | Out-Null
$researchTemporary = Join-Path (Split-Path $researchConnector) ([IO.Path]::GetRandomFileName())
try {{
    [IO.File]::WriteAllBytes($researchTemporary, $researchSourceBytes)
    Move-Item -LiteralPath $researchTemporary -Destination $researchConnector -Force
}} finally {{
    if (Test-Path -LiteralPath $researchTemporary) {{ Remove-Item -LiteralPath $researchTemporary }}
}}
$researchArguments = @(
    '-NoProfile', '-File', $researchConnector
    '-Action', 'Connect', '-Server', $researchServer
    '-Repository', {_powershell_literal(repository)}
    '-PythonExecutable', {_powershell_literal(python)}
    '-RunsRoot', {_powershell_literal(repository / 'state')}
)
& pwsh @researchArguments
if ($LASTEXITCODE -ne 0) {{ throw '客户端接入未完成，请按上方错误处理后重新执行。' }}"""
    # One REPL command: a terminating error must stop the rest of a pasted block,
    # not just its current line. The child scope also preserves shell preferences.
    script = "& {\n" + indent(script, "    ") + "\n}\n"
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
            lines.extend(["首次接入或连接任务未运行时，在 Windows 本地 PowerShell 整块粘贴执行下面代码（包括 & { 和最后的 }）；"
                          "不要逐行运行或复制 PS>/>> 提示符。保留当前 SSH/CC 会话。程序会询问 SSH 目标，获取入口并启动连接任务。",
                          "```powershell", view["client_entry"]["powershell"], "```",
                          "Connect 启动隐藏后台任务后返回，无需保持该 Windows PowerShell 窗口打开；保留当前 init 会话。",
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
