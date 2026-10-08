"""Bilingual Telegram summaries of existing observer status and measurements."""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone

from .observer_trends import _time


BRIEFING_SECONDS = 12 * 60 * 60
_TITLES = {
    "run_started": ("🚀", "运行开始", "Run started"),
    "run_finished": ("🏁", "运行结束", "Run finished"),
    "grounding_question": ("👤", "需要人工决策", "Human decision requested"),
    "grounding_answered": ("✅", "人工回答已记录", "Human answer recorded"),
    "hypothesis_added": ("🌱", "新增假设", "Hypothesis added"),
    "hypothesis_verified": ("✅", "假设获得支持", "Hypothesis supported"),
    "hypothesis_refuted": ("🔍", "假设被否定", "Hypothesis refuted"),
    "hypothesis_pruned": ("✂️", "假设已剪枝", "Hypothesis pruned"),
    "experiment_submitted": ("🧪", "实验已提交", "Experiment submitted"),
    "experiment_started": ("🧪", "实验开始", "Experiment started"),
    "experiment_finished": ("🧪", "实验结束", "Experiment finished"),
    "experiment_failed": ("⚠️", "实验失败", "Experiment failed"),
    "stage_started": ("▶️", "阶段开始", "Stage started"),
    "stage_finished": ("✅", "阶段结束", "Stage finished"),
    "expert_round_done": ("✅", "专家轮次结束", "Expert round finished"),
    "expert_evolved": ("🌱", "专家技能已演化", "Expert skills evolved"),
    "batch_adjudicated": ("⚖️", "假设批次已裁定", "Hypothesis batch adjudicated"),
    "final_report_ready": ("📄", "最终报告已就绪", "Final report ready"),
    "leader_selected": ("👑", "领队已选定", "Leader selected"),
    "leader_started": ("👑", "领队开始工作", "Leader started"),
    "leader_finished": ("👑", "领队工作结束", "Leader finished"),
    "leader_not_started": ("👑", "领队未启动", "Leader not started"),
    "leader_aborted": ("⚠️", "领队工作中止", "Leader aborted"),
    "graph_evolved": ("🌿", "假设图已演化", "Hypothesis graph evolved"),
    "debate_selected": ("💬", "辩论已选定", "Debate selected"),
    "debate_started": ("💬", "辩论开始", "Debate started"),
    "debate_finished": ("💬", "辩论结束", "Debate finished"),
    "debate_failed": ("⚠️", "辩论失败", "Debate failed"),
    "debate_aborted": ("⚠️", "辩论中止", "Debate aborted"),
    "debate_restarts_finished": ("💬", "辩论后重启已结束", "Post-debate restarts finished"),
    "debate_restarts_designated": ("💬", "辩论后重启已指定", "Post-debate restarts designated"),
    "new_best_result": ("📏", "实验指标记录", "Experiment metric recorded"),
    "goal_verdict_recorded": ("🎯", "GOAL 判定已记录", "GOAL verdict recorded"),
    "provider_waiting": ("⚠️", "API 等待恢复", "API waiting for recovery"),
    "provider_retry_requested": ("🔄", "已请求 API 重试", "API retry requested"),
    "provider_recovered": ("✅", "API 已恢复", "API recovered"),
    "literature_waiting": ("📚", "文献服务等待恢复", "Literature service waiting"),
    "literature_retry_requested": ("📚", "已请求文献服务重试", "Literature retry requested"),
    "literature_recovered": ("📚", "文献检索已恢复", "Literature retrieval recovered"),
    "participant_pause_requested": ("⏸️", "已请求暂停参与者", "Participant pause requested"),
    "participant_resumed": ("▶️", "参与者已恢复", "Participant resumed"),
    "operator_note_injected": ("📝", "操作备注已注入", "Operator note injected"),
    "resource_blocked": ("⚠️", "GPU 资源受阻", "GPU resources blocked"),
    "resource_rechecked": ("✅", "GPU 资源已复检", "GPU resources rechecked"),
    "error": ("⚠️", "运行错误", "Run error"),
    "budget_warning": ("⚠️", "预算提醒", "Budget warning"),
    "notification_backlog": ("📬", "历史通知摘要", "Earlier routine notifications"),
}
_TITLES["provider_auth_cooldown"] = _TITLES["provider_waiting"]
_TITLES["provider_auth_resumed"] = _TITLES["provider_retry_requested"]


def chinese(observation):
    return str(observation.get("run", {}).get("report_language", "en")).lower().split("-")[0] == "zh"


def _status(value, zh):
    if not zh: return value
    return {"CREATED": "已创建", "ACTIVE": "运行中", "WAITING_HUMAN": "等待人工回答",
        "COMPLETING": "收尾中", "STOPPING": "停止中", "COMPLETED": "已完成", "FAILED": "失败",
        "CANCELLED": "已取消", "GOAL_MET": "已达成", "GOAL_NOT_MET": "未达成",
        "GOAL_UNDETERMINED": "尚不能确定", "SUPPORTED": "支持", "REFUTED": "否定",
        "INCONCLUSIVE": "未定", "PROPOSED": "已提出", "PRUNED": "已剪枝", "UNKNOWN": "未知",
        "EXECUTING": "执行中", "SUCCEEDED": "成功", "QUEUED": "排队中", "READY": "就绪",
        "DRAFT": "草案", "PREREGISTERED": "已预注册", "COLLECTING": "收集中",
        "SETTLED": "已结算"}.get(value, value)


def duration(seconds, zh):
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
        return "未知" if zh else "unknown"
    days, remainder = divmod(int(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    return (f"{days} 天 {hours:02}:{minutes:02}:{seconds:02}" if zh else
            f"{days}d {hours:02}:{minutes:02}:{seconds:02}")


def _when(value, zh):
    if type(value) in (int, float) and math.isfinite(value):
        return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return "未知；需要人工处理" if zh else "unknown; operator action required"


def event_text(event, observation):
    """Render status and metric labels/values; omit research bodies, notes and secrets."""
    zh = chinese(observation)
    def tr(cn, en): return cn if zh else en
    kind, payload = event["type"], event.get("payload", {})
    icon, cn, en = _TITLES.get(kind, ("🔔", kind, kind))
    if kind == "new_best_result" and "previous_outcome_id" in payload:
        icon, cn, en = (("📏", "首次有效测量", "First valid measurement") if payload["previous_outcome_id"] is None else
                        ("📈", "同组指标刷新", "Comparable metric improved"))
    if kind == "goal_verdict_recorded":
        icon, cn, en = {
            "GOAL_MET": ("🎯", "整体 GOAL 已达成", "Overall GOAL met"),
            "GOAL_NOT_MET": ("🎯", "整体 GOAL 未达成", "Overall GOAL not met"),
            "INCONCLUSIVE": ("🎯", "整体 GOAL 尚不能确定", "Overall GOAL inconclusive"),
        }.get(payload.get("verdict"), (icon, cn, en))
    items = [f"{icon} Hypoloom · {tr(cn, en)} ({kind})",
             tr("运行：", "Run: ") + event["run_id"], tr("事件：", "Event: ") + event["event_id"]]
    for key, cn, en in (("role", "专家", "Expert"), ("node", "阶段", "Stage"),
                        ("experiment_id", "实验", "Experiment"), ("outcome", "结果", "Outcome"),
                        ("verdict", "判定", "Verdict"), ("state", "状态", "State"), ("error_code", "错误码", "Error code")):
        if payload.get(key) is not None:
            value = _status(str(payload[key]), zh) if key in {"state", "verdict", "outcome"} else payload[key]
            items.append(f"{tr(cn, en)}: {value}")
    if kind == "new_best_result":
        unknown = tr("未知", "unknown")
        metric = payload.get("metric") or {}
        scope = {"LOCAL": tr("LOCAL（局部实验）", "LOCAL (local experiment)"),
                 "GOAL": tr("GOAL（目标评估实验）", "GOAL (goal evaluation experiment)")}.get(payload.get("evaluation_scope"), unknown)
        direction = {"MAXIMIZE": tr("越大越好", "higher is better"),
                     "MINIMIZE": tr("越小越好", "lower is better")}.get(metric.get("direction"), unknown)
        comparison = {"VALUE": tr("直接值", "value"), "DELTA_FROM_BASELINE": tr("相对基线的差值", "delta from baseline"),
                      "RELATIVE_DELTA_FROM_BASELINE": tr("相对基线的变化率", "relative delta from baseline")}.get(payload.get("measurement_comparison"), unknown)
        previous = payload.get("previous_metric_value")
        previous = unknown if previous is None else str(previous)
        if "previous_outcome_id" in payload and payload["previous_outcome_id"] is None:
            previous = tr("无（本组首次测量）", "none (first measurement in this group)")
        target_met = payload.get("metric_target_met")
        target_status = tr("已达到", "met") if target_met is True else tr("未达到", "not met") if target_met is False else unknown
        operator = {"MAXIMIZE": "≥", "MINIMIZE": "≤"}.get(metric.get("direction"), "?")
        # Only the short metric label is presented, not its parser or research prose.
        label = " ".join(str(metric.get("name", unknown)).split())[:160]
        items.extend([
            tr("组件：", "Component: ") + str(payload.get("component_id", unknown)),
            tr("实验范围：", "Experiment scope: ") + scope,
            tr("比较范围：同一组件及相同的冻结评估条件。", "Comparison: same component and frozen evaluation conditions."),
            tr("指标：", "Metric: ") + f"{label} · {comparison} · {direction}",
            tr("本组前次最佳：", "Previous group best: ") + previous,
            tr("本次测量：", "Current measurement: ") + str(payload.get("metric_value", unknown)),
            tr("实验指标阈值：", "Experiment metric threshold: ") + f"{operator} {metric.get('target', unknown)} · {target_status}",
            tr("本通知仅报告实验指标；整体 GOAL 是否达成以独立 GOAL 判定为准。",
               "This reports an experiment metric; overall GOAL attainment requires a separate GOAL verdict.")])
    if kind == "notification_backlog":
        items.extend([", ".join(f"{key}: {value}" for key, value in payload["counts"].items()),
            tr("这是历史事件摘要；当前状态请查看 Web/TUI。", "This summarizes earlier events; consult Web/TUI for the current state.")])
    if kind in {"grounding_question", "grounding_answered"}:
        ordinal = payload.get("ordinal")
        if type(ordinal) is int and ordinal > 0: items.append(tr("问题：", "Question: ") + str(ordinal))
        if kind == "grounding_question":
            items.extend([tr("TUI：按 h → Review answer → Submit answer。", "TUI: press h → Review answer → Submit answer."),
                tr("Web：在 TUI 决策表单中选择 Browser，获取回答入口。", "Web: choose Browser in the TUI decision form for an operator answer link."),
                tr("回复 Telegram 消息不会提交答案。", "Telegram replies do not submit an answer.")])
        else: items.append(tr("可在 TUI/Web 查看已记录的回答和当前运行状态。", "View the recorded answer and current run state in TUI/Web."))
    if kind == "resource_blocked":
        items.append(tr("已提交实验继续执行。修复绑定的 GPU 池后，使用 TUI g 或 hypoloom resource-recheck。",
                        "Submitted experiments continue. Repair the bound GPU pool, then use TUI g or hypoloom resource-recheck."))
    if kind == "resource_rechecked":
        items.append(tr("受阻节点可在原有预算内重试。", "Blocked nodes may retry within their existing budgets."))
    if kind in {"provider_auth_cooldown", "provider_waiting"}:
        when = _when(payload.get("next_retry_at", payload.get("cooldown_until")), zh)
        items.extend([tr("API 不可用：", "API unavailable: ") + f"{payload.get('reason', 'auth_failed')} (HTTP {payload.get('http_status')})",
            tr(f"下次恢复尝试：{when}。这不是额度重置时间。", f"Next recovery attempt: {when}. This is not a quota reset estimate."),
            tr("已提交实验继续执行。TUI：p；CLI：hypoloom provider-retry-clear。", "Submitted experiments continue. TUI: p; CLI: hypoloom provider-retry-clear."),
            tr("切换 API 时，使用 --settings 选择受保护的连接配置文件。", "To switch API, select a protected connection file with --settings.")])
    if kind in {"provider_auth_resumed", "provider_retry_requested"}:
        items.append(tr("已请求重试；尚未确认恢复，需等待实际成功响应。", "Provider retry requested by operator. Access is not yet confirmed; awaiting a successful response."))
    if kind == "provider_recovered":
        items.append(tr("已收到成功的 API 响应；研究进度仍受任务和预算限制。", "API response received; provider availability recovered. Research progress remains subject to task and budget limits."))
    if kind.startswith("literature_"):
        items.append(tr("文献服务：", "Literature service: ") + f"{payload.get('active_service', payload.get('service', 'openalex'))}; {payload.get('reason') or kind}")
        if payload.get("active_service") == "crossref":
            items.append(tr("Crossref 回退成功；尚未确认 OpenAlex 恢复。", "Crossref fallback succeeded; OpenAlex recovery remains unconfirmed."))
        if payload.get("requires_operator"):
            items.append(tr("请修复文献连接后，在 Web/TUI 重试文献服务，或运行 provider-retry-clear --service openalex。",
                "Operator action required. Repair the literature connection, then use Literature retry in Web/TUI or provider-retry-clear --service openalex."))
        elif payload.get("next_retry_at") is not None:
            items.append(tr("下次尝试：", "Next attempt: ") + _when(payload["next_retry_at"], zh))
        if kind == "literature_recovered":
            items.append(tr("已完成实际检索；研究仍受任务和预算限制。", "An actual retrieval succeeded; research remains subject to task and budget limits."))
    items.append("⏱ " + tr("已运行：", "Elapsed: ") + duration(observation.get("summary", {}).get("elapsed_seconds"), zh))
    return "\n\n".join(items)


def due_briefing(observation):
    """Latest elapsed window only; the existing sink cursor supplies progress."""
    run, summary = observation.get("run", {}), observation.get("summary", {})
    elapsed = summary.get("elapsed_seconds")
    if (run.get("run_state") not in {"CREATED", "ACTIVE", "WAITING_HUMAN", "COMPLETING", "STOPPING"}
            or type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < BRIEFING_SECONDS
            or _time(observation.get("observed_at")) is None):
        return None
    revision = int(elapsed // BRIEFING_SECONDS)
    run_id = observation["run_id"]
    graph = observation.get("graph", {})
    states = dict(Counter(node.get("state", "UNKNOWN") for node in graph.get("nodes", [])))
    return dict(type="run_briefing", source="run-briefing", source_id=run_id, revision=revision,
        event_id=f"run-briefing:{run_id}:{revision}", run_id=run_id, created_at=observation["observed_at"],
        payload={"run": run, "elapsed_seconds": elapsed,
            "hypotheses": {"nodes": len(graph.get("nodes", [])), "edges": len(graph.get("edges", [])), "states": states},
            "experiments": summary.get("totals", {}).get("experiments", {}),
            "accounting": summary.get("accounting", {})})


def briefing_text(event, *, chart_available):
    payload = event["payload"]
    run = payload["run"]
    zh = chinese({"run": run})
    def tr(cn, en): return cn if zh else en
    def number(value): return f"{value:,}" if type(value) is int else "—"
    graph, experiments, accounting = (payload[key] for key in ("hypotheses", "experiments", "accounting"))
    # A photo and caption share one acknowledgement/cursor. Bound the identifier
    # in this compact template; the full ID remains in log/webhook projections.
    run_id = event["run_id"] if len(event["run_id"]) <= 80 else event["run_id"][:77] + "…"
    items = [tr("📋 Hypoloom · 12 小时运行简报", "📋 Hypoloom · 12-hour run briefing"),
        tr("运行：", "Run: ") + run_id + " · " + _status(run.get("run_state", "—"), zh),
        f"🎯 GOAL: {_status(run.get('goal_verdict') or tr('尚无判定', 'not assessed'), zh)} · " + tr("必要组件已评估 ", "required assessed ") +
            f"{number(run.get('required_assessment_count'))}/{number(run.get('required_component_count'))}",
        tr(f"🌿 假设图：{graph['nodes']} 节点 / {graph['edges']} 关系", f"🌿 Graph: {graph['nodes']} nodes / {graph['edges']} edges") +
            " · " + ", ".join(f"{_status(key, zh)} {value}" for key, value in sorted(graph["states"].items())),
        tr("🧪 实验累计：", "🧪 Experiments total: ") + number(experiments.get("total")) +
            " · " + ", ".join(f"{_status(key, zh)} {value}" for key, value in sorted(experiments.get("states", {}).items())),
        tr("🪙 Token 累计：已记录 ", "🪙 Tokens total: reported ") + number(accounting.get("reported_tokens")) +
            tr("；未结算观测 ", "; observed unsettled ") + number(accounting.get("observed_unsettled_tokens")) +
            tr("；预留（非消耗）", "; reserved (not usage) ") + number(accounting.get("reserved_tokens")),
        tr("用量未知的尝试：已关闭 ", "Unknown usage: closed ") + number(accounting.get("unknown_closed_attempts")) +
            tr("；token 缺失尝试 ", "; token-unavailable attempts ") + number(accounting.get("unavailable_token_attempts")),
        tr("📈 附图：最近 12 小时 RPM / 并发记录", "📈 Chart: last 12 hours RPM / recorded concurrency") if chart_available else
            tr("📈 本次趋势图不可用；已发送文字简报。", "📈 Chart unavailable for this briefing; text summary only."),
        "⏱ " + tr("已运行：", "Elapsed: ") + duration(payload["elapsed_seconds"], zh),
        tr("观测：", "Observed: ") + _when(_time(event["created_at"]), zh)]
    return "\n\n".join(items)
