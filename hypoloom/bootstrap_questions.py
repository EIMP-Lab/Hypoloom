"""Deterministic initial questions, projected from inspect's existing facts.

No stored wizard state: ordinary bootstrap arguments remain the only choices.
The answer file is the unedited AskUserQuestion response, never a launch permit.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _text(args, en, zh):
    return zh if args.ui_language == "zh-CN" else en


def questions(args, result):
    """Return (field, native question, label-to-value map) in a fixed order."""
    missing = result["missing"]
    rows = []

    def add(field, en, zh, header, options, multi=False):
        labels = [(_text(args, a, b), value, _text(args, c, d)) for a, b, value, c, d in options]
        rows.append((field, {"question": _text(args, en, zh), "header": header,
            "multiSelect": multi, "options": [{"label": label, "description": description}
                for label, _, description in labels]}, {label: value for label, value, _ in labels}))

    if "institutional_prepare_provider" in missing:
        catalog = [p for p in result["choices"]["institutional_prepare_provider"] if p["value"] != "none"]
        # Three publisher choices plus an explicit empty group fit the native
        # tool's four-option limit. Never turn recommended_prepare into an answer.
        for index in range(0, len(catalog), 3):
            group = catalog[index:index + 3]
            number = index // 3 + 1
            names = ", ".join(p["value"] for p in group)
            add("institutional_prepare_provider",
                f"Which publishers should be prepared? Group {number}: {names}. Select all that apply; choose None in this group to skip these publishers.",
                f"准备哪些出版社？第 {number} 组：{names}。可多选；不准备本组请选择“本组不选”。",
                f"Publishers {number}",
                [(p["value"], p["value"], p["value"], p["description"], p["description"]) for p in group]
                + [("None in this group", "本组不选", None, "Do not prepare these publishers", "不准备本组的出版社")], True)
        return rows

    if "profile" in missing:
        profiles = result["paths"]["local_profiles"]
        templates = result["paths"]["profile_templates"]
        name = "research"
        suffix = 2
        while name in profiles:
            name = f"research-{suffix}"
            suffix += 1
        options = [(p, p, p, "Use this existing private Profile", "使用已有私有 Profile") for p in profiles[:2]]
        options += [(f"New: {t}", f"新建：{t}", (name, t),
            f"Review a new {name} Profile from {t}", f"审阅使用 {t} 模板的新 Profile {name}") for t in templates[:2]]
        if len(options) == 1:
            options.append(("Cancel", "取消", None, "Stop initialization", "停止初始化"))
        if options:
            add("profile", f"Choose a Profile ({', '.join(profiles) or 'none'}). New options use name {name}; for another name enter name from template ({', '.join(templates)}).",
                f"选择 Profile（{', '.join(profiles) or '暂无'}）。新建选项使用名称 {name}；其他名称请输入 名称 from 模板（{', '.join(templates)}）。", "Profile", options)
    if "claude_model" in missing and (model := result.get("choices", {}).get("claude_model")):
        add("claude_model", f"Which base model should this run use? Configured default: {model}. Select it or enter another exact model ID supported by this provider. Profile role overrides remain effective.",
            f"本次运行使用哪个基模？当前配置为 {model}。可沿用，或直接输入当前供应商支持的其他准确模型 ID；Profile 中的角色覆盖仍然生效。", "Base model", [
            (model, model, model, "Use the displayed configured model", "沿用当前配置的模型"),
            ("Cancel", "取消", None, "Stop initialization", "停止初始化")])
    if "execution_mode" in missing:
        add("execution_mode", "Which compute resources may this run use?", "本次运行允许使用哪些计算资源？", "Compute", [
            ("CPU only", "仅 CPU", False, "Do not allocate GPUs", "不分配 GPU"),
            ("GPU", "GPU", True, "Select GPU devices next", "随后明确选择 GPU 设备")])
    if "institutional_browser_mode" in missing:
        add("institutional_browser_mode", "Where should institutional access run? Enter existing only for an explicit external CDP endpoint.",
            "在哪里进行机构访问？仅已有外部 CDP 端点时自由输入 existing。", "Browser", [
            ("Windows client", "Windows 客户端", "client", "Connect your Windows browser", "连接 Windows 浏览器"),
            ("Linux desktop", "Linux 桌面", "local", "Visible browser on this Linux desktop", "使用本机可见浏览器"),
            ("Server browser", "服务器浏览器", "server", "Private GUI for the server container", "通过私有图形界面使用容器浏览器"),
            ("Off", "关闭", "off", "Use public/OA retrieval", "使用公开及开放获取文献")])
    if "gpu_device_uuid" in missing:
        devices = result["checks"].get("gpu_inventory", {}).get("devices", [])
        uuids = [d["uuid"] for d in devices]
        add("gpu_device_uuid", f"Which GPUs are authorized? Observed UUIDs: {', '.join(uuids) or 'unavailable'}. For a subset, enter comma-separated UUIDs.",
            f"授权哪些 GPU？观察到的 UUID：{', '.join(uuids) or '不可用'}。选择部分设备时请输入逗号分隔的 UUID。", "GPU devices", [
            ("All observed GPUs", "所有已观察 GPU", uuids, "Authorize exactly the displayed UUIDs", "仅授权以上明确列出的设备"),
            ("CPU only", "仅 CPU", False, "Use no GPUs", "不使用 GPU")])
    if "gpu_scheduling_mode" in missing:
        add("gpu_scheduling_mode", "How should the authorized GPUs be scheduled?", "如何调度已授权的 GPU？", "Scheduling", [
            ("Managed shared pool", "托管共享池", "managed", "Review this project's shared pool", "检查本项目共享池"),
            ("Nonpooled", "不使用共享池", "nonpooled", "Explicit nonpooled allocation", "明确使用非池化分配")])
    if "pool_configuration" in missing:
        choice = result["choices"]["pool_configuration"]
        current, proposed = choice["current_reserve_mib"], choice["proposed_reserve_mib"]
        devices = ", ".join(choice["devices"])
        add("pool_configuration",
            f"The shared pool reserves {current} MiB; this init proposes {proposed} MiB. Keep it or review reconfiguration? Full pool membership remains: {devices}. Run device selection is unchanged.",
            f"共享池下限为 {current} MiB，本次建议为 {proposed} MiB。保留现状还是审阅重配置？全池成员保持：{devices}。本次运行的选卡不变。", "Pool config", [
                ("Keep current pool", "保留当前池", "reuse", "Reuse without restarting or changing pool history", "复用，不重启、不更改池历史"),
                ("Review reconfiguration", "审阅重配置", "reconfigure", "Check dependencies before changing the full pool reserve", "检查依赖后审阅全池下限变更")])
    if "pool_retired_owner" in missing:
        owners = result["choices"]["pool_retired_owner"]
        identities = json.dumps([{k: o[k] for k in ("uid", "namespace", "run_id", "released_requests")}
                                 for o in owners], ensure_ascii=False, sort_keys=True)
        add("pool_retired_owner",
            f"These pool owners have no local run state; all listed requests are released and no recorded process is alive: {identities}. Can you confirm these runs were retired and deleted, were not moved elsewhere and will not resume? Released requests alone do not prove retirement. This assertion applies only to the reviewed maintenance; history is retained. Start now is still required.",
            f"以下池登记缺少本地运行状态，所列请求均已释放且未发现记录关联的存活进程：{identities}。是否确认这些运行已退役并删除，未迁移到别处且不会恢复？请求释放本身不能证明退役。确认仅用于本次维护，历史保留；仍需最后选择 Start now。", "Retired runs", [
                ("Defer confirmation", "暂不确认", False, "Keep maintenance blocked; recheck or revise pool choices", "保持维护阻塞，可复查或调整池选择"),
                ("Confirm listed retirements", "确认所列运行已退役", True, "Confirm exactly the listed identities for this maintenance", "仅确认以上准确身份，用于本次维护")])
    if "pool_cancel_run" in missing:
        runs = result["choices"]["pool_cancel_run"]
        identities = ", ".join(f"{r['run_id']}:{r['revision']} ({r['state']})" for r in runs)
        add("pool_cancel_run",
            f"These runs still depend on the pool, but their Hosts/processes are absent and pool requests are released: {identities}. Review ending exactly these runs with their original frozen Hosts? Cancellation precedes recovery; only closure and the original budgeted final report may run. Start now is still required.",
            f"以下运行仍依赖共享池，但 Host/进程已退出，池请求均已释放：{identities}。是否审阅通过原冻结 Host 结束这些运行？先提交取消，再恢复遗留 Attempt；仅进行收尾及原预算内的最终报告，仍需最后选择 Start now。", "End old runs", [
                ("Keep these runs", "保留这些运行", False, "Keep maintenance blocked; preserve this init for recheck", "保持维护阻塞，保留本次 init 供复查"),
                ("Review ending listed runs", "审阅结束所列运行", True, "Include exactly these cancellations in the final launch review", "将以上准确运行的取消操作纳入最终启动审阅")])
    # Missing credentials/executable/default model are configuration problems,
    # not secrets or paths to collect through multiple-choice guesses.
    return rows[:4]


def apply_answers(args, result, response):
    if not isinstance(response, dict):
        raise ValueError("answers must be an AskUserQuestion response object")
    answers = response.get("answers", response)
    rows = questions(args, result)
    if not isinstance(answers, dict) or set(answers) != {q["question"] for _, q, _ in rows} or not rows:
        raise ValueError("answer every generated question unchanged; cancelled, partial or stale answers cannot advance init")
    changes = {}
    selected = []
    for field, question, mapping in rows:
        raw = answers[question["question"]]
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("each answer must be nonempty text")
        raw = raw.strip()
        if field == "institutional_prepare_provider":
            labels = list(dict.fromkeys(p.strip() for p in raw.split(",")))
            if any(label not in mapping for label in labels):
                raise ValueError("choose only the publishers in each generated group")
            values = [mapping[label] for label in labels]
            if None in values and len(values) != 1:
                raise ValueError("None in this group cannot be combined with a publisher")
            selected.extend(v for v in values if v is not None)
            continue
        value = mapping.get(raw, raw)
        if field == "profile":
            if value is None:
                raise ValueError("initialization cancelled; no Profile or run was created")
            if isinstance(value, tuple):
                changes.update(profile=value[0], template=value[1])
            elif raw in result["paths"]["local_profiles"]:
                changes["profile"] = raw
            else:
                name, separator, template = raw.partition(" from ")
                if not separator or template not in result["paths"]["profile_templates"]:
                    raise ValueError("select an existing Profile or enter: new-name from template-name")
                changes.update(profile=name, template=template)
        elif field == "claude_model":
            if value is None: raise ValueError("initialization cancelled; no run was created")
            if not isinstance(value, str) or len(value) > 256 or any(c.isspace() or ord(c) < 33 for c in value):
                raise ValueError("enter one exact model ID supported by the configured provider")
            changes[field] = value
        elif field == "execution_mode":
            if not isinstance(value, bool): raise ValueError("choose a displayed compute mode")
            changes["allow_gpu"] = value
        elif field == "institutional_browser_mode":
            from .institutional_connection import BROWSER_MODES
            if value not in BROWSER_MODES: raise ValueError("choose a supported browser mode")
            changes[field] = value
        elif field == "gpu_device_uuid":
            if value is False:
                changes.update(allow_gpu=False, gpu_device_uuid=None)
            else:
                values = value if isinstance(value, list) else [s.strip() for s in raw.split(",")]
                if not values or any(not s.startswith("GPU-") for s in values):
                    raise ValueError("GPU authorization requires explicit device UUIDs")
                changes[field] = list(dict.fromkeys(values))
        elif field == "gpu_scheduling_mode":
            if value not in {"managed", "nonpooled"}: raise ValueError("choose a displayed scheduling mode")
            changes.update(managed_pool=value == "managed", nonpooled=value == "nonpooled")
        elif field == "pool_configuration":
            if value not in {"reuse", "reconfigure"}: raise ValueError("choose a displayed pool action")
            choice = result["choices"][field]
            changes.update(pool_action=value, pool_device_uuid=choice["devices"],
                pool_reserve_mib=choice["current_reserve_mib" if value == "reuse" else "proposed_reserve_mib"])
        elif field == "pool_retired_owner":
            if not isinstance(value, bool): raise ValueError("choose a displayed retirement answer")
            changes[field] = (list(dict.fromkeys([*(args.pool_retired_owner or []),
                *(o["namespace"] + "/" + o["run_id"] for o in result["choices"][field])])) if value else ["none"])
        elif field == "pool_cancel_run":
            if not isinstance(value, bool): raise ValueError("choose a displayed cancellation answer")
            changes[field] = (list(dict.fromkeys([*(args.pool_cancel_run or []),
                *(f"{r['run_id']}:{r['revision']}" for r in result["choices"][field])])) if value else ["none"])
    if any(field == "institutional_prepare_provider" for field, _, _ in rows):
        from .institutional_options import preparation_selection
        changes["institutional_prepare_provider"] = preparation_selection(selected, {}) or ["none"]
    if changes.get("allow_gpu") is False:
        changes.update(managed_pool=False, nonpooled=False, gpu_device_uuid=None)
    for name, value in changes.items(): setattr(args, name, value)


def interaction(args, result, repository: Path):
    """Serialize the existing parser, so another question cannot lose prior choices."""
    from .bootstrap import build_parser
    parser = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)).choices["inspect"]
    argv = [args.control_python, "-B", "-m", "hypoloom.bootstrap", "inspect"]
    for action in parser._actions:
        if action.dest in {"help", "answers_file"}: continue
        value = getattr(args, action.dest, None)
        if value is None or value == action.default: continue
        if not action.option_strings:
            argv.append(str(value))
        elif isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
            if value == action.const: argv.append(action.option_strings[0])
        elif isinstance(action, argparse._AppendAction):
            for item in value: argv.extend([action.option_strings[0], str(item)])
        else:
            argv.extend([action.option_strings[0], str(value)])
    # Repository default may have depended on a previous caller's cwd.
    if "--repository" not in argv: argv += ["--repository", str(repository)]
    rows = questions(args, result)
    return {"questions": [q for _, q, _ in rows],
            "answer_argv": [*argv, "--answers-file", "<answers.json>"] if rows else None,
            "inspect_argv": argv,
            "configuration_required": [field for field in result["missing"] if field not in {f for f, _, _ in rows}]}
