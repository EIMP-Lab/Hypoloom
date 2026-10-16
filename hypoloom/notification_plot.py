"""Disposable PNG rendering; optional matplotlib runs outside observer threads."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def briefing_photo(state_root, run_id, *, observed_at, language):
    """Bound diagnostic inspection and rendering; callers can always send text."""
    from .observer_trends import build_run_trends
    trends = build_run_trends(state_root, run_id, now=observed_at, inspection_seconds=3.0)
    with tempfile.TemporaryDirectory(prefix="hypoloom-plot-") as cache:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve())],
            input=json.dumps({"trends": trends, "language": language}).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15, check=True,
            env={**os.environ, "MPLCONFIGDIR": cache, "MPLBACKEND": "Agg"})
    if not result.stdout.startswith(b"\x89PNG\r\n\x1a\n") or len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError("briefing image unavailable")
    return result.stdout


def render(trends, language):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import dates, font_manager
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import MaxNLocator
    from datetime import datetime, timezone

    zh = str(language).lower().split("-")[0] == "zh"
    def tr(cn, en): return cn if zh else en
    if zh:
        candidates = ("Noto Sans CJK SC", "Noto Sans CJK JP", "WenQuanYi Zen Hei", "Droid Sans Fallback", "Microsoft YaHei", "SimHei")
        available = {font.name for font in font_manager.fontManager.ttflist}
        chosen = next((name for name in candidates if name in available), None)
        if chosen is None: raise ValueError("Chinese chart font unavailable")
        matplotlib.rcParams["font.family"] = [chosen]
    matplotlib.rcParams.update({"axes.unicode_minus": False, "font.size": 11})
    indices = [i for i, point in enumerate(trends["points"]) if point["until"] > trends["ended_at"] - 43200]
    if not indices: raise ValueError("chart has no observations")
    points = [trends["points"][i] for i in indices]
    x = [datetime.fromtimestamp(p["until"], timezone.utc) for p in points]
    def values(rows): return [float("nan") if value is None else value for value in rows]
    figure = Figure(figsize=(12, 8), dpi=120, facecolor="#f6f8fc")
    FigureCanvasAgg(figure)
    rpm, concurrency = figure.subplots(2, 1, sharex=True)
    figure.subplots_adjust(left=.08, right=.98, top=.90, bottom=.15, hspace=.40)
    figure.suptitle(tr("Hypoloom · 最近 12 小时运行趋势", "Hypoloom · Last 12 hours"), fontsize=17, x=.08, ha="left")
    for axis in (rpm, concurrency):
        axis.set_facecolor("white")
        axis.grid(axis="y", color="#e2e8f0")
        axis.spines[["top", "right"]].set_visible(False)
    rpm.plot(x, values([p["rpm"] for p in points]), color="#172554", linewidth=2.2, label=tr("总 RPM", "Total RPM"))
    incomplete = any(p["rpm"] is None for p in points)
    if incomplete:
        rpm.plot(x, [p["rpm_lower_bound"] for p in points], linestyle=":", color="#64748b", label=tr("总量已知下界", "Total known lower bound"))
    experts = sorted(trends["experts"], key=lambda e: sum(e["requests"][i] for i in indices), reverse=True)
    for series in experts[:8]:
        # Identifiers are literal labels, never matplotlib math expressions.
        label = str(series["role"]).replace("$", "＄").replace("\n", " ")[:32]
        rpm.plot(x, values([series["rpm"][i] for i in indices]), linewidth=1.2, alpha=.85, label=label)
    for key, label in (("grounding", "Grounding"), ("other", tr("其他任务", "Other tasks"))):
        series = trends[key]
        if any(series["requests"][i] or series["incomplete_attempts"][i] for i in indices):
            rpm.plot(x, values([series["rpm"][i] for i in indices]), linestyle="--", linewidth=1, label=label)
    rpm.set_ylabel("RPM")
    rpm.legend(loc="upper left", fontsize=8, ncols=3, framealpha=.9)
    for key, label, color in (("agents", "Agent", "#7c3aed"), ("experiments", tr("实验", "Experiments"), "#0891b2")):
        concurrency.step(x, [p[key] for p in points], where="pre", color=color, linewidth=1.8, label=label)
        concurrency.step(x, [p[key + "_peak"] for p in points], where="pre", color=color, linestyle=":", alpha=.6,
                         label=label + tr("（桶内峰值）", " (bucket peak)"))
    concurrency.set_ylabel(tr("并发数（记录）", "Recorded concurrency"))
    concurrency.yaxis.set_major_locator(MaxNLocator(integer=True))
    concurrency.legend(loc="upper left", fontsize=9, ncols=2)
    locator = dates.AutoDateLocator(minticks=4, maxticks=8, tz=timezone.utc)
    concurrency.xaxis.set_major_locator(locator)
    concurrency.xaxis.set_major_formatter(dates.ConciseDateFormatter(locator, tz=timezone.utc))
    concurrency.set_xlabel(tr("时间（UTC）", "Time (UTC)"))
    # Set the lower bound after plotting; setting it before plotting disables
    # y autoscaling and clips all rates/concurrency greater than the default 1.
    for axis in (rpm, concurrency): axis.set_ylim(bottom=0)
    minutes = trends["interval_seconds"] / 60
    note = tr(f"RPM 为每 {minutes:g} 分钟桶的平均请求率（含重试）；缺失区间留空。并发为已绑定 Attempt 记录，不是存活检查。",
              f"RPM: {minutes:g}-minute bucket averages, including retries; missing capture leaves gaps. Bound Attempts, not process liveness.")
    if len(experts) > 8:
        note += tr("\n仅展示请求最多的 8 位专家；总 RPM 包含所有专家。", "\nOnly the 8 busiest experts are shown; total RPM includes all experts.")
    figure.text(.08, .045, note, fontsize=9, color="#475569")
    output = io.BytesIO()
    figure.savefig(output, format="png", facecolor=figure.get_facecolor())
    return output.getvalue()


if __name__ == "__main__":
    request = json.load(sys.stdin)
    sys.stdout.buffer.write(render(request["trends"], request["language"]))
