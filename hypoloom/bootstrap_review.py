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
        value("Model authentication", {"transport": args.auth_transport, "settings_path": args.claude_settings}),
        value("Budgets (null means no cap)", effective["budget"]),
        value("GPU/pool (CPU-only if absent)", effective.get("gpu")),
        value("Notifications", effective["notifications"]),
        "Notification checks are local configuration checks only; delivery is unverified. No test message is sent.",
        "Telegram is outbound-only. Interactive grounding waits for an answer and continues consuming the run wall-clock budget.",
        value("Literature preflight", result.get("literature_preflight")),
        value("Literature arguments (paths only; no credential contents)", _literature_arguments(args)),
        value("Institutional routing", effective["institutional_access"]),
        render_results(summary["preparation_summary"], summary["provider_modes"]),
        value("Admission warnings", admission.get("warnings", [])),
        "Start now: recheck the reviewed Profile, perform the displayed pool operation, start the managed Host and create this run once.",
        "Prepare only: release this preparation connection; no pool operation, Host or research run is created.",
        "Review window: 600 seconds; use the live remaining time above. Expiry releases the helper/browser and never starts research; stale Start now is rejected."]
    return "\n".join(lines)
