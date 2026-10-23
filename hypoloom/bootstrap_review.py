"""Fixed human review of existing admission/launch projections; no new authority."""
import json
from pathlib import Path


def gpu_reserve_summary(gpu, language="en"):
    """Explain the existing launch preview; never change a Profile or pool."""
    if not gpu or not (memory := gpu.get("memory_budget")):
        return []
    zh = language == "zh-CN"
    workload = memory["initial_workload_mib"]
    reserve = memory["initial_reserve_mib"]
    percent = memory["reserve_percent"]
    floor = memory["reserve_floor_mib"]
    lines = [
        (f"显存请求保护线：{reserve} MiB；初始单卡工作负载：{workload} MiB。" if zh else
         f"GPU request protection: {reserve} MiB; initial per-device workload: {workload} MiB."),
        ((f"按已核定工作负载的 {percent}% 计算，绝对下限 {floor} MiB，向上对齐 64 MiB；不是整卡容量的百分比。" if zh else
          f"Reserve is {percent}% of admitted workload, with a {floor} MiB floor, rounded up to 64 MiB; not a percentage of device capacity.")
         if percent else
         ("当前采用固定保留量，未启用百分比；沿用已有 Profile 不会自动采用新模板的 5%。" if zh else
          "Fixed reserve selected; percentage mode is disabled. Adopting an existing Profile does not apply the new template's 5%.")),
    ]
    if percent and floor * 100 >= workload * percent:
        lines.append("当前绝对下限主导请求保护线；仅修改百分比不会降低该下限。" if zh else
                     "The absolute floor currently dominates request protection; changing only the percentage cannot lower it.")
    pool = gpu.get("pool")
    if pool is not None:
        floors = ([d.get("pool_reserve_memory_mib") for d in pool["device_reserves"]]
                  if "device_reserves" in pool else [pool.get("reserve_mib")])
        known = sorted({v for v in floors if type(v) is int and v >= 0})
        if not floors or len([v for v in floors if type(v) is int and v >= 0]) != len(floors):
            lines.append("共享池下限尚未完整确认；不能据请求保护线推断实际准入保留量。" if zh else
                         "Pool floors are not fully known; the request reserve alone does not establish the admission reserve.")
        for value in known:
            effective = max(reserve, value)
            lines.append((f"共享池下限 {value} MiB → 初始准入保留量至少 {effective} MiB，工作负载加保留量至少 {workload + effective} MiB；在途请求可能要求更高保留量，其他占用另计。" if zh else
                          f"Pool floor {value} MiB → initial admission reserve at least {effective} MiB; workload plus reserve at least {workload + effective} MiB. Active requests may require a larger reserve; other occupancy is additional."))
        if any(v > reserve for v in known):
            lines.append("共享池下限高于请求保护线；降低 Profile 不能绕过它。池须空闲后按已有重配置流程调整，当前运行不变。" if zh else
                         "The pool floor exceeds request protection. Lowering the Profile cannot bypass it; use the existing reconfiguration procedure when the pool is idle. Current runs retain their policy.")
        if pool.get("action"):
            lines.append((f"池处理：{pool['action']}；全池成员：{pool.get('members', {})}。" if zh else
                          f"Pool action: {pool['action']}; full pool membership: {pool.get('members', {})}."))
        if (previous := pool.get("previous_configuration")) is not None:
            lines.append((f"池下限变更：{previous['reserve_mib']} → {pool.get('reserve_mib')} MiB；保留池数据库及请求历史。" if zh else
                          f"Pool floor change: {previous['reserve_mib']} → {pool.get('reserve_mib')} MiB; retain the pool database and request history."))
        if retired := pool.get("dependencies", {}).get("reviewed_retired_owners"):
            identities = ", ".join(f"{o['uid']}:{o['namespace']}/{o['run_id']}" for o in retired)
            lines.append((f"本次维护采用用户确认的已退役运行：{identities}。运行未迁移且不会恢复；确认不作为永久豁免，不删除历史。" if zh else
                          f"Operator-confirmed retirements for this maintenance: {identities}. These runs were not relocated and will not resume; no permanent exemption or history deletion."))
        for run in pool.get("cancellation", {}).get("runs", []):
            if run["selected"]:
                if run.get("closure") == "offline":
                    lines.append((f"启动前离线收尾旧运行：{run['run_id']}，审阅版本 {run.get('revision')}；状态 {run.get('status', run.get('reason'))}。使用原冻结生命周期保留证据，不发起模型或 GPU 工作；缺少最终报告如实记为失败，运行可能以 FAILED 结束。init 等待收尾并自动继续池准备。" if zh else
                                  f"Offline closure before startup: {run['run_id']}, reviewed revision {run.get('revision')}; {run.get('status', run.get('reason'))}. Use its original frozen lifecycle and preserve evidence, without model or GPU work. An unavailable final report is recorded as a failure; the run may end FAILED. Init waits for closure, then continues pool preparation automatically."))
                    continue
                lines.append((f"启动前结束旧运行：{run['run_id']}，审阅版本 {run.get('revision')}；状态 {run.get('status', run.get('reason'))}。使用原冻结 Host，仅执行取消收尾和原预算内最终报告；完成后复查池依赖。" if zh else
                              f"End old run before startup: {run['run_id']}, reviewed revision {run.get('revision')}; {run.get('status', run.get('reason'))}. Use its original frozen Host for cancellation closure and the original budgeted final report, then recheck pool dependencies."))
    return lines


def gpu_concurrency_summary(gpu, language="en"):
    if not gpu or not (concurrency := gpu.get("concurrency")):
        return []
    zh = language == "zh-CN"
    count, cap = concurrency["authorized_device_count"], concurrency["max_active_processes"]
    lines = [(f"实验并发：整个运行最多 {cap if cap is not None else '不限'} 个 Process，共授权 {count} 张 GPU；CPU 实验也计入该上限。" if zh else
              f"Experiment concurrency: {cap if cap is not None else 'uncapped'} Processes across the whole run, with {count} authorized GPUs; CPU experiments also count.")]
    if concurrency["below_device_count"]:
        lines.append((f"并发上限低于 GPU 数量；若实验每次使用单卡，可审阅将 max_active_processes 调至 {count}。Profile 不会自动改写。" if zh else
                      f"The cap is below the device count. For single-GPU experiments, consider reviewing max_active_processes={count}; the Profile is not changed automatically."))
    lines.append(("共享池容量不足会排队，但不会提高运行的并发上限；实际启动仍受显存、多卡及共享策略限制。" if zh else
                  "The shared pool queues capacity waits but does not raise the run's concurrency cap; memory, multi-GPU and sharing constraints still apply.") if gpu.get("pooled") else
                 ("非池模式不会使用共享池队列；实际启动仍受固定设备和显存约束。" if zh else
                  "Nonpooled execution has no shared-pool queue; fixed-device and memory constraints still apply."))
    return lines


def render(args, result, summary):
    from .bootstrap import compact_result, _literature_arguments
    from .profile_v3 import PROFILE_V3_FILES
    from .institutional_instructions import render_results
    compact = compact_result(result)
    effective = compact["effective_configuration"]
    admission = compact["admission"]
    profile = Path(admission["profile_path"])
    notifications = effective["notifications"]
    notification_route = []
    if "telegram" in notifications.get("enabled_sinks", []):
        notification_route = [
            "Telegram route: explicit HTTP proxy; connectivity and delivery remain unverified."
            if notifications.get("proxy") == "configured" else
            "Telegram route: direct. System/browser proxy settings are not inherited. If this server needs a proxy, configure notify.yaml.proxy_url before starting."]
    def value(label, data):
        return label + ": " + json.dumps(data, ensure_ascii=False, sort_keys=True)
    lines = ["Launch review — no research run has started.",
        value("Profile (adopt existing; no Profile/settings writes)", str(profile)),
        value("Profile files", [str(profile / name) for name in PROFILE_V3_FILES]),
        value("Identity", summary["scope"]),
        value("Control", {"python": args.control_python, "process_delay": args.process_delay,
                          "supervisor": result.get("supervisor", {})}),
        value("Execution", effective["execution"]),
        value("Models", effective["models"]),
        value("Context windows (per model; reserves deducted from input allowance)", effective.get("context_windows")),
        value("Read-only provider metadata queries (no generation probe)", effective.get("provider_metadata_queries", 0)),
        value("Model authentication", {"transport": args.auth_transport, "settings_path": args.claude_settings}),
        value("Budgets (null means no cap)", effective["budget"]),
        value("GPU/pool (CPU-only if absent)", effective.get("gpu")),
        *gpu_reserve_summary(effective.get("gpu"), getattr(args, "ui_language", "en")),
        *gpu_concurrency_summary(effective.get("gpu"), getattr(args, "ui_language", "en")),
        value("Notifications", notifications),
        *notification_route,
        "Notification checks are local configuration checks only; delivery is unverified. No test message is sent.",
        "Telegram is outbound-only. Interactive grounding waits for an answer and continues consuming the run wall-clock budget.",
        value("Literature preflight", result.get("literature_preflight")),
        value("Literature routing (independent of Telegram/browser)", {
            "default_route": getattr(args, "literature_default_route", None) or "direct",
            "proxy_only_domains": getattr(args, "literature_proxy_domain", []),
            "direct_domains": getattr(args, "literature_direct_domain", [])}),
        "Literature auto routing uses a configured proxy only after eligible network failures; routes are not connectivity or subscription guarantees.",
        value("Literature arguments (paths only; no credential contents)", _literature_arguments(args)),
        value("Institutional routing", effective["institutional_access"]),
        render_results(summary["preparation_summary"], summary["provider_modes"]),
        value("Admission warnings", admission.get("warnings", [])),
        "Start now: recheck the reviewed Profile, perform the displayed pool operation, start the managed Host and create this run once.",
        "Prepare only: release this preparation connection; no pool operation, Host or research run is created.",
        "Review window: 600 seconds; use the live remaining time above. Expiry releases the helper/browser and never starts research; stale Start now is rejected."]
    return "\n".join(lines)
