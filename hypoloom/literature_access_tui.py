"""Institutional access controls; authentication stays in the browser."""
from __future__ import annotations

import asyncio
import uuid
from urllib.parse import urlsplit
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static
from .control_protocol import SCHEMA_VERSION, ControlRequest, ProtocolError, request_over_socket

LABELS = {"queued": "等待获取", "checking": "检查中", "login_required": "需要浏览器登录",
    "verification_required": "需要浏览器验证", "browser_unavailable": "浏览器未连接",
    "fetch_unavailable": "加载失败，等待自动重试", "access_required": "页面提示需订阅或机构访问",
    "available": "全文已就绪", "access_denied": "当前机构无权限", "no_full_text": "未找到全文",
    "identity_unconfirmed": "论文对应关系未确认", "failed": "文件获取或解析失败"}
LABELS.update(skipped="已跳过此篇", public_only="仅公开途径", queue_timeout="排队超时",
              acquisition_timeout="获取超时", manual_timeout="主动登录等待已结束", sample_verified="样本全文已验证",
              opened="准备页已打开，请在浏览器完成登录", unchecked="未检查", tab_limit="标签页已满，请分批准备")
REASON_LABELS = {"network_changed": "网络发生变化", "network_error": "网络错误",
    "dns_failed": "域名解析失败", "connection_failed": "连接失败", "navigation_timeout": "加载超时",
    "redirect_unresolved": "重定向未完成", "http_server_error": "网站服务暂时异常",
    "http_rate_limited": "网站限制请求频率", "request_blocked": "请求被拦截", "http_error": "HTTP请求失败",
    "pdf_response_not_pdf": "PDF入口返回非PDF内容"}


def browser_connect_guide(endpoint, ui_url=None, connection=None):
    """The GUI URL selects a server-owned browser; CDP never needs a laptop tunnel."""
    if connection:
        common = "连接随本次运行分配，运行结束后释放；专用浏览器登录目录保留。\n"
        if connection.get("reason"):
            common += {"port_in_use": "所选固定端口被占用；释放后可重试。", "connection_unavailable": "连接资源暂不可用，可稍后重试。"}.get(connection["reason"], "连接暂不可用。") + "其他研究工作继续。\n"
        mode = connection.get("mode")
        if mode == "client":
            return common + "请保持 Windows 专用连接脚本运行；脚本会发现本次运行并自动建立隧道，无需填写服务器端口。\n连接状态：" + str(connection.get("state")) + "。连接后选平台 → 打开登录，在浏览器完成学校登录。"
        if mode == "local":
            return common + "在运行项目的 Linux 桌面上选平台 → 打开登录，使用弹出的专用浏览器。相同登录目录同时只允许一个运行使用。"
        if mode == "server":
            return common + "在服务器终端运行 hypoloom institutional connections --state-root <运行目录> --include-ui-credentials 获取本次浏览器入口和访问凭据，再转发显示的 GUI 端口。学校账号只在浏览器中输入。"
        return common + "使用已配置的外部浏览器完成登录；项目只释放自己的运行连接，外部浏览器仍由你管理。"
    if ui_url:
        parts = urlsplit(ui_url)
        target = "[::1]" if parts.hostname == "::1" else parts.hostname
        return ("服务器浏览器 · 登录状态保存在服务器专用浏览器目录\n"
            "仅首次登录或会话过期时，在你的电脑建立查看连接：\n"
            f"ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:{parts.port}:{target}:{parts.port} <ssh-user>@<research-server>\n"
            f"然后访问 {parts.scheme}://127.0.0.1:{parts.port}/ 查看服务器浏览器，完成学校SSO/MFA。\n"
            "同平台后续论文与新run复用仍有效的登录。完成后可断开SSH、关闭笔记本；服务器继续下载。\n"
            "账号密码只在远程浏览器页面输入；Hypoloom不收集或导出Cookie。")
    try:
        port = urlsplit(endpoint or "").port
    except ValueError:
        port = None
    from .institutional_browser import windows_browser_command
    if port:
        start = windows_browser_command(port)
        tunnel = f"ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -R 127.0.0.1:{port}:127.0.0.1:{port} <ssh-user>@<research-server>"
        note = "连接并登录后点击「检查并重试」；已结束的请求不会自动重开。"
    else:
        start = windows_browser_command("<端口>")
        tunnel = "ssh -N -R 127.0.0.1:<端口>:127.0.0.1:<端口> <ssh-user>@<research-server>"
        note = "端口以 Host 启动参数 --institutional-browser-url 为准；替换上面命令中的 <端口>。"
    return ("连接浏览器后才能登录（在学校网络的电脑上本地执行）：\n"
        f"1) 启动专用 Chrome/Edge：\n   {start}\n"
        f"2) 在同一台电脑建立 SSH 反向隧道（把 <ssh-user>@<research-server> 换成你的服务器）：\n   {tunnel}\n"
        "3) 回到本界面：选平台 →「打开登录」→ 在浏览器弹出的页面完成学校 SSO/MFA。\n"
        "账号密码与验证码只在浏览器页面里填写；本面板不收集凭据。\n" + note)


class LiteratureAccessScreen(ModalScreen):
    BINDINGS = [("escape", "close", "返回研究"), ("l", "login", "打开登录"), ("r", "retry", "检查并重试")]
    DEFAULT_CSS = """
    LiteratureAccessScreen { align: center middle; background: $background 70%; }
    #literature-access-form { width: 90%; max-width: 105; height: 85%; border: round #63d7f2; padding: 1 2; background: $surface; }
    #literature-access-scroll { height: 1fr; }
    #literature-access-form Static { height: auto; margin-bottom: 1; }
    #literature-access-actions, #literature-access-choices { height: auto; }
    #literature-access-actions Button { width: 1fr; min-width: 8; }
    #literature-access-footer { height: 3; align-horizontal: right; }
    """

    def __init__(self, status):
        super().__init__()
        self.status, self.busy = status, False
        self._providers, self._urls = (), ()
        self._unconfirmed = None

    def compose(self) -> ComposeResult:
        with Vertical(id="literature-access-form"):
            with VerticalScroll(id="literature-access-scroll"):
                yield Static("机构全文访问 · 其他研究工作继续\n账号、密码和验证码只在学校或出版社浏览器页面填写。", markup=False)
                access = self.status.get("literature_access") or {}
                yield Static(browser_connect_guide(access.get("browser_endpoint"), access.get("browser_ui_url"), access.get("connection")),
                    markup=False, id="literature-guide")
                yield Select([], prompt="选择平台", id="literature-provider")
                yield Static("", markup=False, id="literature-pending")
                yield Select([], prompt="选择论文（导入PDF时必选）", id="literature-paper")
                yield Static("", markup=False, id="literature-selected-url")
                yield Input(placeholder="可选：验证样本 DOI 或规范论文 URL", id="literature-sample")
                yield Input(placeholder="可选：已传到服务器的PDF绝对路径", id="literature-import-path")
                yield Static("", markup=False, id="literature-access-message")
            with Horizontal(id="literature-access-actions"):
                yield Button("打开登录 · L", id="literature-login")
                yield Button("检查并重试 · R", id="literature-retry")
                yield Button("导入PDF", id="literature-import")
                yield Button("Web面板链接", id="literature-web")
            with Horizontal(id="literature-access-choices"):
                yield Button("验证样本", id="literature-verify")
                yield Button("跳过此篇", id="literature-skip")
                yield Button("恢复此篇", id="literature-restore")
                yield Button("仅公开途径", id="literature-public-only")
                yield Button("启用机构访问", id="literature-auto")
            with Horizontal(id="literature-access-footer"):
                yield Button("Close", id="literature-close", variant="primary")

    def on_mount(self):
        self.update_status(self.status)

    def update_status(self, status):
        self.status = status
        access = status.get("literature_access") or {}
        self.query_one("#literature-guide", Static).update(
            browser_connect_guide(access.get("browser_endpoint"), access.get("browser_ui_url"), access.get("connection")))
        groups = access.get("groups", [])
        providers = tuple(g["provider"] for g in groups)
        selector = self.query_one("#literature-provider", Select)
        if providers != self._providers:
            prior = selector.value
            self._providers = providers
            selector.set_options([(name, name) for name in providers])
            if providers:
                selector.value = prior if prior in providers else providers[0]
        self._details()
        writable = bool(access.get("enabled")) and status.get("run_state") not in {"STOPPING", "COMPLETED", "FAILED", "CANCELLED"}
        for button in ("login", "retry", "import", "skip", "restore", "public-only", "auto", "verify"):
            self.query_one("#literature-" + button, Button).disabled = self.busy or not writable or not providers
        if not access.get("enabled"):
            self._message("Host未启用机构访问。启动新任务时配置--institutional-browser-url；此处不填写登录凭据。")

    def on_select_changed(self, event):
        if event.select.id == "literature-provider":
            self._details()
        if event.select.id == "literature-paper":
            self.query_one("#literature-selected-url", Static).update(
                "" if event.value is Select.BLANK else str(event.value))

    def _details(self):
        selected = self.query_one("#literature-provider", Select).value
        group = next((g for g in (self.status.get("literature_access") or {}).get("groups", [])
                      if g["provider"] == selected), {"papers": []})
        prep = group.get("preparation", {"state": "unchecked"})
        self.query_one("#literature-pending", Static).update(
            f"{LABELS.get(prep['state'], prep['state'])} · {prep.get('last_checked_at') or ''}\n" +
            f"{'仅公开途径' if group.get('mode') == 'public_only' else '使用现有机构会话'} · 主动等待剩余 {group.get('manual_remaining_seconds', 0)} 秒\n" + "\n".join(
            f"{LABELS.get(p['state'], p['state'])} · {p['title']}"
            + (f" · {REASON_LABELS.get(p['reason_code'], p['reason_code'])}（{p['reason_code']}）"
               if p.get("reason_code") in REASON_LABELS else f" · {p['reason_code']}" if p.get("reason_code") else "")
            + (f" · 下次检查约{p['retry_in_seconds']}秒后" if p.get("retry_in_seconds") is not None else "")
            + (f" · 上次检查 {p['last_checked_at']}" if p.get("last_checked_at") else "")
            for p in group["papers"])
            or "目前没有待处理的机构全文请求。")
        urls = tuple(p["url"] for p in group["papers"])
        if urls != self._urls:
            self._urls = urls
            selector = self.query_one("#literature-paper", Select)
            selector.set_options([(p["title"], p["url"]) for p in group["papers"]])
            if urls:
                selector.value = urls[0]

    def _message(self, message):
        self.query_one("#literature-access-message", Static).update(message)

    async def _send(self, action):
        if self.busy or self.status.get("run_state") in {"STOPPING", "COMPLETED", "FAILED", "CANCELLED"}:
            return
        provider = self.query_one("#literature-provider", Select).value
        if provider is Select.BLANK:
            return
        payload = {"run_id": self.app.run_id, "provider": provider, "action": action}
        if action == "verify":
            from .institutional_prepare import sample_url
            try: payload["url"] = sample_url(self.query_one("#literature-sample", Input).value)
            except ValueError as exc:
                self._message(str(exc)); return
        if action in {"auto", "public_only"}:
            payload.update(action="mode", mode=action)
        if action in {"import", "skip", "restore"}:
            url = self.query_one("#literature-paper", Select).value
            if url is Select.BLANK:
                return
            payload.update(url=url)
            if action == "import": payload["path"] = self.query_one("#literature-import-path", Input).value.strip()
        durable = payload["action"] in {"skip", "restore", "mode"}
        command = (self._unconfirmed if durable and self._unconfirmed is not None and self._unconfirmed.payload == payload else
            ControlRequest(SCHEMA_VERSION, "literature-access-" + str(uuid.uuid4()),
                self.status.get("run_revision") if durable else None, "LITERATURE_ACCESS", payload))
        if durable: self._unconfirmed = command
        self.busy = True
        self.update_status(self.status)
        try:
            response = await asyncio.to_thread(request_over_socket, self.app.state_root / "control.sock",
                command, timeout=5)
            if durable: self._unconfirmed = None
            if response.status == "accepted":
                self._message("设置已保存到本次运行。" if durable else "已请求打开连接的浏览器。请在那里完成登录；系统会检查并重试，其他研究继续。"
                    if action == "open" else "已安排样本验证，不进入科研归档。" if action == "verify" else "已安排检查。全文通过验证后，研究Agent可通过原文工具读取。")
            else:
                self._message(str((response.error or {}).get("message", "Host未接受请求")))
        except (OSError, ProtocolError):
            self._message("Host响应未确认。刷新状态后可重试；这不会修改已经完成的研究结果。")
        finally:
            self.busy = False
            self.update_status(self.status)
            self.app.action_refresh()

    async def on_button_pressed(self, event):
        event.stop()
        if event.button.id == "literature-close":
            self.action_close()
        elif event.button.id == "literature-web":
            self.app.action_literature_browser()
        else:
            action = {"literature-login": "open", "literature-retry": "retry", "literature-import": "import",
                "literature-verify": "verify", "literature-skip": "skip", "literature-restore": "restore",
                "literature-public-only": "public_only", "literature-auto": "auto"}.get(event.button.id)
            if action:
                await self._send(action)

    async def action_login(self):
        await self._send("open")

    async def action_retry(self):
        await self._send("retry")

    def action_close(self):
        if not self.busy:
            self.dismiss()
