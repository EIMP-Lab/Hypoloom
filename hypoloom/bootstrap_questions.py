"""Deterministic initial questions, projected from inspect's existing facts.

No stored wizard state: ordinary bootstrap arguments remain the only choices.
The answer file is the unedited AskUserQuestion response, never a launch permit.
"""
from __future__ import annotations

import argparse
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
    # Missing credentials/executable/model are actionable configuration problems,
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
