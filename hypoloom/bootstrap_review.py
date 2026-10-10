"""Fixed human review of existing admission/launch projections; no new authority."""
import json
from pathlib import Path


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
