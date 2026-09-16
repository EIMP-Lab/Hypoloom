"""Disposable public views and environment summaries for the existing support export."""
from __future__ import annotations

from collections.abc import Mapping
import importlib.metadata
import platform
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess


class PublicSupportView:
    """Replace local identities within one export; never persist a reverse map."""

    _identities = {
        "run_id": "run", "derived_from_run_id": "run", "attempt_id": "attempt",
        "selected_attempt_id": "attempt", "retry_of_attempt_id": "attempt",
        "source_attempt_id": "attempt", "from_attempt_id": "attempt",
        "first_unrecovered_failure_attempt_id": "attempt", "command_id": "command",
        "workflow_id": "workflow", "session_id": "session", "component_id": "component",
        "experiment_id": "experiment", "execution_id": "execution",
        "host_generation": "host", "artifact_id": "artifact", "tool_use_id": "tool",
        "message_id": "message",
    }
    _private_fields = {
        "state_root", "stdout_path", "stderr_path", "diagnostic_summary", "validation_error",
        "traceback", "message", "error", "endpoint", "refs", "changed_field_paths",
        "candidate_fields", "location", "detail", "details", "path", "source",
        "reason", "failure_reason", "partial_reason", "response_partial_reason",
    }

    def __init__(self):
        self.aliases: dict[str, dict[str, str]] = {}

    def identity(self, value, kind="attempt"):
        if not isinstance(value, str) or not value:
            return value
        aliases = self.aliases.setdefault(kind, {})
        if value not in aliases:
            aliases[value] = f"{kind}-{len(aliases) + 1}"
        return aliases[value]

    def clean(self, value, field=""):
        kind = self._identities.get(field)
        if kind:
            return self.identity(value, kind)
        if field.endswith("attempt_ids"):
            return [self.identity(item) for item in value] if isinstance(value, (list, tuple)) else []
        if field == "identifiers" and isinstance(value, (list, tuple)):
            result = []
            for item in value:
                known = next((aliases[item] for aliases in self.aliases.values()
                              if isinstance(item, str) and item in aliases), None)
                result.append(known if known is not None else self.identity(item, "record"))
            return result
        if isinstance(value, Mapping):
            result = {}
            for key, item in value.items():
                if key in self._private_fields:
                    if item not in (None, "", [], {}):
                        result[key + "_omitted"] = True
                else:
                    result[key] = self.clean(item, key)
            return result
        if isinstance(value, (list, tuple)):
            return [self.clean(item, field) for item in value]
        if isinstance(value, str):
            # Operational strings are codes/versions, never free-form sentences,
            # URLs or absolute paths. Private opt-in retains their original text.
            if (len(value) > 160 or value.startswith(("/", "~", "\\"))
                    or "://" in value or re.fullmatch(r"[A-Za-z0-9_.:/+@()\[\]= -]+", value) is None):
                return "[omitted]"
            return value
        return value


def _command_output(argv, *, cwd=None):
    try:
        completed = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=2, check=False)
        return completed.stdout[:8192] if completed.returncode == 0 else ""
    except (OSError, ValueError, subprocess.TimeoutExpired, UnicodeError):
        return ""


def support_environment():
    """Allowlisted current exporter environment; no env dump, host name or device IDs."""
    source = Path(__file__).resolve().parent.parent
    git_root = _command_output(["git", "rev-parse", "--show-toplevel"], cwd=source).strip()
    checkout = bool(git_root) and Path(git_root).resolve() == source
    revision = _command_output(["git", "rev-parse", "HEAD"], cwd=source).strip() if checkout else ""
    revision = revision if re.fullmatch(r"[a-f0-9]{40,64}", revision) else None
    try:
        version = importlib.metadata.version("hypoloom")
    except importlib.metadata.PackageNotFoundError:
        version = None
    cli = shutil.which("claude")
    cli_version = re.search(r"\b\d+\.\d+\.\d+(?:[-+.][\w.]+)?", _command_output([cli, "--version"]) if cli else "")
    gpu_tool = shutil.which("nvidia-smi")
    gpu_text = _command_output([gpu_tool, "--query-gpu=name,driver_version,memory.total",
        "--format=csv,noheader,nounits"]) if gpu_tool else ""
    gpus = []
    for line in gpu_text.splitlines()[:32]:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == 3 and parts[2].isdigit() and re.fullmatch(r"[\w. ()+-]{1,100}", parts[0]):
            gpus.append({"name": parts[0], "driver_version": parts[1], "memory_mib": int(parts[2])})
    modified = bool(_command_output(["git", "status", "--porcelain", "--", "hypoloom"], cwd=source)) if checkout else None
    return {"scope": "exporter_environment_now", "framework_version": version,
        "source_revision": revision, "source_modified": modified, "python": platform.python_version(),
        "python_implementation": platform.python_implementation(), "sqlite": sqlite3.sqlite_version,
        "os": platform.system(), "os_release": platform.release(), "architecture": platform.machine(),
        "claude_cli": cli_version.group() if cli_version else None,
        "gpus": gpus, "gpu_observation": "available" if gpus else "unavailable",
        "note": "Current exporter environment; it may differ from the recorded run environment."}


def support_profile_summary(projection):
    """Summarize admitted structure without profile prose, names, paths or raw settings."""
    from .support_bundle import _walk
    contracts = [item["research_contract"] for item in _walk(projection)
                 if isinstance(item, Mapping) and isinstance(item.get("research_contract"), Mapping)]
    if not contracts:
        return {"available": False, "reason": "no_recorded_contract"}
    contract = contracts[0]
    experts = contract.get("roles")
    components = contract.get("components", [])
    budget = contract.get("budget", {})
    safe_budget = {key: value for key, value in budget.items()
                   if key in {"wallclock_hours", "max_experiments", "max_cycles", "max_cost_usd"}
                   and (value is None or type(value) in {int, float})} if isinstance(budget, Mapping) else {}
    invocation = budget.get("agent_invocation", {}) if isinstance(budget, Mapping) else {}
    safe_budget["agent_invocation"] = {key: value for key, value in invocation.items()
        if key in {"max_tokens", "max_turns", "max_output_bytes", "wall_seconds", "max_cost", "token_enforcement_mode"}
        and (value is None or type(value) in {int, float} or value in ("enforce", "observe"))}
    return {"available": True, "schema_version": contract.get("schema_version"),
        "expert_count": len(experts) if isinstance(experts, (list, tuple, Mapping)) else
            len({item.get("owner_role") for item in components if isinstance(item, Mapping) and item.get("owner_role")}),
        "component_count": len(components) if isinstance(components, (list, tuple)) else None,
        "budget": safe_budget, "profile_text_included": False}


def support_readme(report, projection):
    summary = projection.get("summary", {})
    lines = ["# Hypoloom support report", "",
        "This is a diagnostic copy, not a backup or a scientific verdict.",
        "Review the files before sharing. Nothing has been uploaded.", "",
        f"Private research data requested: {report['include_private_data']}",
        f"Run diagnosis available: {report['diagnosis']['available']}",
        f"Recorded attempts in scope: {summary.get('attempt_count', 'unavailable')}",
        f"Selected attachment scope: {report['selection']['scope']}", "",
        "## Evidence", "", "See report.json for included, omitted and unavailable files.",
        "See debug.json for error codes, task stages and retry relationships.",
        "Public copies use local aliases; they cannot be passed to run commands.",
        "Free-text errors, local paths and research bodies require the private option.",
        "Private copies may contain proprietary prompts, code and data. Share privately.", "",
        "## Tell the maintainer", "", "- What were you trying to do?",
        "- What did you expect, and what happened instead?", "- Which steps reproduce the problem?",
        "- Is it repeatable, and which version last worked?", "",
        "Paste a reviewed description into the issue; attach only approved evidence.", ""]
    return "\n".join(lines).encode()
