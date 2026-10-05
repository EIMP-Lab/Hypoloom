"""Run existing qualification test selections; does not issue a release decision."""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
TESTS = "tests/runtime/"
SELECTIONS = {
    "g1": [],
    "g2": [TESTS + name for name in (
        "test_component_oracle.py", "test_state_transactions.py", "test_state_migration_v2.py",
        "test_state_migration_v3.py", "test_backup_restore.py", "test_runtime_bundle.py")],
    "g3": [TESTS + name for name in (
        "test_research_projection.py::test_resident_host_answers_status_through_two_cpu_cycles_and_projection",
        "test_full_cc_host_g3.py::test_full_cc_host_recovers_committed_submit_and_reconstructs_context",
        "test_claude_debate_host_g3.py::test_real_cli_debate_three_rounds_restart_both_hypotheses_and_promote_only_winner",
        "test_claude_shared_acceptance_g3.py::test_native_grounding_shared_source_local_failure_and_complete_goal_stop",
        "test_execution_completion_g3.py::test_native_execution_corrects_early_completion_before_publication",
        "test_shared_gpu_native_learning_g3.py",
        "test_operator_cutover.py::test_tui_leave_reenter_and_cancel_drains_real_cpu_before_managed_host_exit")],
    "g4": [TESTS + "test_shared_gpu_native_g4.py"],
    "g5-local": [TESTS + "test_real_current_host_g5.py::test_real_current_host_closes_tools_cpu_sessions_and_reports"],
    "g5-common": [TESTS + "test_real_current_host_g5.py::test_real_current_common_host_closes_grounding_source_goal_and_reports"],
    "g5-resource": [TESTS + "test_real_shared_gpu_g5.py::test_real_model_shared_gpu_resource_path"],
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
        epilog="Additional pytest arguments can follow --; explicit pytest options replace G1 scheduling defaults.")
    parser.add_argument("stage", choices=SELECTIONS)
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--junitxml", type=Path)
    arguments = list(sys.argv[1:] if argv is None else argv)
    separator = arguments.index("--") if "--" in arguments else len(arguments)
    args = parser.parse_args(arguments[:separator])
    pytest_args = arguments[separator + 1:]
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    if not args.collect_only and args.stage in {"g1", "g2", "g3"}:
        # An inherited paid/GPU opt-in must not change an offline/CPU selection.
        enabled = [key for key, value in environment.items()
                   if key.startswith("HYPOLOOM_REAL_") and value == "1"]
        if enabled:
            parser.error("Clear live qualification opt-ins for this stage: " + ", ".join(sorted(enabled)))
    if not args.collect_only and args.stage == "g1":
        checks = [
            [sys.executable, "-m", "compileall", "-q", "hypoloom", "tests", ".claude", "scripts"],
            [sys.executable, "-m", "ruff", "check", "--select", "F821,F822,F823",
             "hypoloom", "tests", ".claude", "scripts"],
            ["git", "diff", "--check"],
        ]
        for command in checks:
            result = subprocess.run(command, cwd=ROOT, env=environment)
            if result.returncode:
                return result.returncode
    if args.stage == "g2":
        print("G2 portable fixture replay. Retained-run cohort comparison is also required; "
              "see docs/qualification.md. This command alone cannot pass G2.", flush=True)
    if args.stage in {"g4", "g5-local", "g5-common", "g5-resource"}:
        print("Resource/paid lanes require their documented opt-ins. A skipped case "
              "is not qualification evidence.", flush=True)
    command = [sys.executable, "-B", "-m", "pytest", "-q", "-ra", *SELECTIONS[args.stage]]
    if (args.stage == "g1" and not args.collect_only and not pytest_args
            and not environment.get("PYTEST_ADDOPTS", "").strip()
            and importlib.util.find_spec("xdist") is not None):
        command += ["-n", str(min(8, max(1, os.cpu_count() or 1))), "--dist=worksteal"]
    if args.collect_only:
        command.append("--collect-only")
    if args.junitxml:
        command += ["--junitxml", str(args.junitxml.resolve())]
    command += pytest_args
    return subprocess.run(command, cwd=ROOT, env=environment).returncode


if __name__ == "__main__":
    raise SystemExit(main())
