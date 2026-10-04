"""Read-only publisher access observations from the existing Host status."""
from __future__ import annotations

from rich import box
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

LABELS = {
    "queued": "排队中", "checking": "获取中", "login_required": "本篇需要登录",
    "verification_required": "本篇遇到人机验证", "browser_unavailable": "浏览器访问不可用",
    "fetch_unavailable": "本篇加载失败", "access_required": "本篇需要订阅或机构访问",
    "available": "全文已就绪", "access_denied": "本篇访问被拒", "no_full_text": "未找到全文",
    "identity_unconfirmed": "论文对应关系未确认", "failed": "获取或解析失败",
    "skipped": "已跳过", "public_only": "仅公开", "queue_timeout": "排队超时",
    "acquisition_timeout": "获取超时", "manual_timeout": "人工等待已结束",
    "sample_verified": "单篇样本可读", "opened": "准备页已打开", "unchecked": "尚未检查",
    "not_checked": "未验证样本", "tab_limit": "浏览器标签页已满",
}
REASON_LABELS = {
    "network_changed": "网络变化", "network_error": "网络错误", "dns_failed": "域名解析失败",
    "connection_failed": "连接失败", "navigation_timeout": "加载超时",
    "redirect_unresolved": "重定向未完成", "http_server_error": "网站服务异常",
    "http_rate_limited": "网站限流", "request_blocked": "请求被拦截", "http_error": "HTTP 请求失败",
    "pdf_response_not_pdf": "PDF 入口返回其他内容", "cdp_endpoint_unavailable": "浏览器控制连接不可用",
    "browser_target_closed": "目标页面已关闭", "article_identity_unconfirmed": "论文页面未通过校验",
    "pdf_target_unconfirmed": "PDF 目标未确认", "document_validation_failed": "文档未通过校验",
}


def access_summary(status):
    access = status.get("literature_access")
    if not isinstance(access, dict) or "enabled" not in access:
        return "机构状态未知：本次未取得 Host 的机构接入状态；不能据此判断未启用或连接失败。"
    if not access["enabled"]:
        return "本运行未启用机构浏览器；公开检索与开放全文回退独立进行。"
    groups = access.get("groups", [])
    automatic = sum(g.get("mode") == "auto" for g in groups)
    public = sum(g.get("mode") == "public_only" for g in groups)
    return f"当前运行：{automatic} 个出版社允许机构访问 · {public} 个仅公开。"


def connection_summary(status):
    access = status.get("literature_access") or {}
    if not access.get("enabled"):
        return ""
    connection = access.get("connection") or {}
    mode = {"client": "Windows 客户端", "local": "Linux 桌面", "server": "服务器浏览器",
            "existing": "外部浏览器"}.get(connection.get("mode"), "已配置浏览器")
    state = connection.get("state")
    detail = {
        "allocated": "连接资源已分配，浏览器可用性未确认",
        "waiting_for_client": "等待客户端连接",
        "client_socket_present": "已发现客户端端点，浏览器可用性未确认",
        "ready": "本次连接检查通过", "unavailable": "连接资源不可用",
        "closed": "运行连接已关闭", "inactive": "运行连接未激活",
    }.get(state, "实时连接状态未知")
    if status.get("run_state") in {"COMPLETED", "FAILED", "CANCELLED"}:
        return "本运行已结束；队列不是完整历史下载统计。"
    return f"{mode} · {detail}"


def publisher_table(status):
    """Format live queue observations; no probes, commands or new stored counters."""
    table = Table(box=box.SIMPLE, expand=True, padding=(0, 1), pad_edge=False)
    table.add_column("出版社", min_width=6)
    table.add_column("访问策略", min_width=8)
    table.add_column("当前进行/就绪/缺口", min_width=12)
    table.add_column("最近观察", ratio=2, overflow="fold")
    access = status.get("literature_access") or {}
    if not access.get("enabled"):
        return table
    from .institutional_access import provider_catalog
    order = {p["provider"]: i for i, p in enumerate(provider_catalog())}
    for group in sorted(access.get("groups", []), key=lambda g: order.get(g["provider"], len(order))):
        papers = group.get("papers", [])
        # remaining_seconds comes from the Host's deadline/queue validator.
        # An expired login challenge is a gap, not a request for TUI intervention.
        pending = sum(p.get("remaining_seconds") is not None for p in papers)
        ready = sum(p.get("state") == "available" for p in papers)
        gaps = sum(p.get("remaining_seconds") is None and p.get("state") not in
                   {"available", "skipped", "public_only"} for p in papers)
        observations = [p for p in papers if p.get("last_checked_at")]
        preparation = group.get("preparation") or {}
        if preparation.get("last_checked_at"):
            observations.append(preparation)
        latest = max(observations, key=lambda p: p["last_checked_at"], default=None)
        if latest:
            state = latest.get("state", "unchecked")
            reason = latest.get("reason_code")
            recent = REASON_LABELS.get(reason, LABELS.get(state, state))
        elif pending:
            recent = "等待首次获取结果"
        elif papers:
            recent = "检查时间未知"
        else:
            recent = "尚无机构请求"
        mode = {"auto": "公开＋机构", "public_only": "仅公开"}.get(group.get("mode"), "策略未知")
        history = access.get("history") or {}
        if history.get("state") == "observed":
            count = history.get("document_verified", {}).get(group["provider"], 0)
            recent += f"；日志内验证 {count} 次" if count else "；日志内未见验证"
        table.add_row(Text(group["provider"]), Text(mode), Text(f"{pending} / {ready} / {gaps}"), Text(recent))
    return table


class LiteratureAccessScreen(ModalScreen):
    BINDINGS = [("escape", "close", "返回研究")]
    DEFAULT_CSS = """
    LiteratureAccessScreen { align: center middle; background: $background 70%; }
    #literature-access-form { width: 95%; max-width: 120; height: 85%; border: round #63d7f2; padding: 1 2; background: $surface; }
    #literature-access-scroll { height: 1fr; }
    #literature-access-form Static { height: auto; }
    #literature-publishers { margin-top: 1; }
    #literature-access-footer { height: 3; align-horizontal: right; }
    """

    def __init__(self, status):
        super().__init__()
        self.status = status

    def compose(self) -> ComposeResult:
        with Vertical(id="literature-access-form"):
            with VerticalScroll(id="literature-access-scroll"):
                yield Static("机构接入状态 · 只读 · 随主界面自动刷新", markup=False)
                yield Static("", markup=False, id="literature-summary")
                yield Static("", markup=False, id="literature-connection")
                yield Static("", id="literature-publishers")
                yield Static("策略表示当前运行允许的访问途径，不代表登录成功或整个平台有订阅权限。\n"
                    "进行/就绪/缺口仅计当前队列，缓存为零不代表从未成功。日志验证仅来自最近最多 1 MiB 的已有记录；"
                    "未显示时历史未知，且不等同于 PDF 已归档或已阅读。缺口不阻止其他研究工作。",
                    markup=False, id="literature-scope")
            with Horizontal(id="literature-access-footer"):
                yield Button("Close", id="literature-close", variant="primary")

    def on_mount(self):
        self.update_status(self.status)

    def update_status(self, status):
        self.status = status
        self.query_one("#literature-summary", Static).update(access_summary(status))
        connection = self.query_one("#literature-connection", Static)
        connection.update(connection_summary(status))
        connection.display = bool((status.get("literature_access") or {}).get("enabled"))
        table = self.query_one("#literature-publishers", Static)
        table.update(publisher_table(status))
        table.display = bool((status.get("literature_access") or {}).get("groups"))

    def on_button_pressed(self, event):
        if event.button.id == "literature-close":
            event.stop()
            self.action_close()

    def action_close(self):
        self.dismiss()
