"""Independent Hypoloom control client and explicit Workflow Host launcher."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import Sequence

from .agent_aci import DEFAULT_REAL_AGENT_BUDGET, AgentBudget
from .control_protocol import ControlRequest, request_over_socket
from .backup_restore import BackupRestoreError, restore_backup
from .host_settings import HostConfigurationError
from .oracle import Oracle, audit_all_runs


_SAFE_PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _state_root(value: str | None) -> Path:
    selected = value or os.environ.get("HYPOLOOM_STATE_ROOT")
    if not selected:
        raise ValueError("--state-root or HYPOLOOM_STATE_ROOT is required")
    return Path(selected).expanduser().resolve()


def _profile_path(value: str, profiles_root: str | None) -> Path:
    if _SAFE_PROFILE_NAME.fullmatch(value) is None:
        raise ValueError("profile must be one safe path component")
    selected = profiles_root or os.environ.get("HYPOLOOM_PROFILES_ROOT")
    if not selected:
        raise ValueError(
            "--profiles-root or HYPOLOOM_PROFILES_ROOT is required"
        )
    root = Path(selected).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"profiles root is not a directory: {root}")
    candidate = (root / value).resolve()
    if candidate.parent != root:
        raise ValueError("profile escaped the profiles root")
    return candidate


def _add_client_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-root")
    parser.add_argument("--request-id", default=None)
    parser.add_argument("--timeout", type=float, default=5.0)


def _add_profile_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("profile")
    parser.add_argument("--profiles-root")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--process-delay", type=float, default=0.0)
    resources = parser.add_mutually_exclusive_group(required=True)
    resources.add_argument("--allow-gpu", action="store_true", help="explicitly authorize GPU use for this run")
    resources.add_argument("--cpu-only", dest="allow_gpu", action="store_false", help="explicitly run without GPU access")
    _add_literature_arguments(parser)


def _add_literature_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--literature-adapter",
        choices=("offline", "openalex"),
        default="offline",
        help="explicit literature qualification; offline performs no network I/O",
    )
    parser.add_argument("--openalex-api-key-file")
    parser.add_argument("--credentials-file",
                        help="existing dotenv credentials file providing OPENALEX_API_KEY and, for the Unpaywall PDF fallback, UNPAYWALL_EMAIL")
    parser.add_argument("--literature-pdf-fallback", choices=("semantic-scholar", "unpaywall"), default="semantic-scholar")
    parser.add_argument("--unpaywall-email-file", help="existing contact-email file for explicitly selected Unpaywall PDF fallback")
    parser.add_argument("--literature-pdfinfo", default="/usr/bin/pdfinfo")
    parser.add_argument("--literature-pdftotext", default="/usr/bin/pdftotext")
    parser.add_argument("--literature-timeout", type=float, default=15.0)
    parser.add_argument(
        "--literature-max-response-bytes",
        type=int,
        default=2 * 1024 * 1024,
    )
    parser.add_argument(
        "--literature-max-pdf-bytes",
        type=int,
        default=16 * 1024 * 1024,
    )
    parser.add_argument("--literature-max-pdf-pages", type=int, default=2_000)


def _profile_literature_source(args, report) -> None:
    """Derive the existing Host file argument from the admitted Profile."""
    if args.literature_adapter != "openalex" or not report.admitted:
        return
    product = report.create_run_payload["research_contract"]["methodology"]["controls"].get("product", {})
    source = product.get("methodology", {}).get("literature", {}).get("credentials_file")
    if source is None:
        return
    if (args.credentials_file is not None and args.credentials_file != source) or args.openalex_api_key_file is not None:
        raise ValueError("literature credentials are declared in the Profile; remove the conflicting CLI source")
    args.credentials_file = source


def _literature_adapter(args: argparse.Namespace) -> object | None:
    if args.literature_adapter == "offline":
        if args.literature_pdf_fallback != "semantic-scholar" or args.unpaywall_email_file is not None:
            raise ValueError("Unpaywall requires --literature-adapter openalex and explicit PDF fallback selection")
        if args.openalex_api_key_file is not None or args.credentials_file is not None:
            raise ValueError(
                "--openalex-api-key-file/--credentials-file require --literature-adapter openalex"
            )
        return None
    if args.openalex_api_key_file is not None and args.credentials_file is not None:
        raise ValueError("use exactly one of --openalex-api-key-file or --credentials-file")
    if args.openalex_api_key_file is None and args.credentials_file is None:
        raise ValueError(
            "--literature-adapter openalex requires --openalex-api-key-file or --credentials-file"
        )
    api_key = None
    unpaywall_email = None
    if args.credentials_file is not None:
        from .credentials import OPENALEX_API_KEY_KEY, UNPAYWALL_EMAIL_KEY, credential, read_credentials_file

        entries = read_credentials_file(args.credentials_file)
        api_key = credential(entries, OPENALEX_API_KEY_KEY)
        if args.literature_pdf_fallback == "unpaywall":
            if args.unpaywall_email_file is not None:
                raise ValueError("use exactly one of --unpaywall-email-file or UNPAYWALL_EMAIL in --credentials-file")
            unpaywall_email = credential(entries, UNPAYWALL_EMAIL_KEY)
    from .literature import build_openalex_literature_io

    adapter = build_openalex_literature_io(
        api_key=api_key,
        api_key_file=args.openalex_api_key_file,
        pdfinfo_path=args.literature_pdfinfo,
        pdftotext_path=args.literature_pdftotext,
        timeout_seconds=args.literature_timeout,
        max_response_bytes=args.literature_max_response_bytes,
        max_pdf_bytes=args.literature_max_pdf_bytes,
        max_pdf_pages=args.literature_max_pdf_pages,
        pdf_fallback=args.literature_pdf_fallback,
        unpaywall_email=unpaywall_email,
        unpaywall_email_file=args.unpaywall_email_file,
    )
    if args.credentials_file is not None:
        adapter.fetcher.api_key_loader = lambda: credential(read_credentials_file(args.credentials_file), OPENALEX_API_KEY_KEY)
        from .credentials import ELSEVIER_API_KEY, ELSEVIER_INSTTOKEN
        from .elsevier import ElsevierArticleAPI
        if entries.get(ELSEVIER_INSTTOKEN) and not entries.get(ELSEVIER_API_KEY):
            raise ValueError("Elsevier institutional token requires an Elsevier API key in the same private source")
        if entries.get(ELSEVIER_API_KEY):
            adapter.article_api = ElsevierArticleAPI(args.credentials_file,
                max_bytes=args.literature_max_pdf_bytes, timeout_seconds=args.literature_timeout)
    return adapter


def _literature_preflight(args: argparse.Namespace) -> dict[str, object]:
    adapter = _literature_adapter(args)
    if adapter is None:
        return {
            "adapter": "offline",
            "qualified": True,
            "network_probe_performed": False,
        }
    return {**adapter.preflight(), "qualified": True}


def _submit(args: argparse.Namespace, command: str, payload: dict[str, object]) -> int:
    root = _state_root(args.state_root)
    request = ControlRequest(
        schema_version=1,
        request_id=args.request_id or str(uuid.uuid4()),
        expected_revision=args.expected_revision,
        command=command,
        payload=payload,
    )
    try:
        response = request_over_socket(root / "control.sock", request, timeout=args.timeout)
    except (ConnectionError, TimeoutError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 3
    stream = sys.stdout if response.status == "accepted" else sys.stderr
    print(json.dumps(response.to_mapping(), sort_keys=True), file=stream)
    return 0 if response.status == "accepted" else 2


def build_parser() -> argparse.ArgumentParser:
    from .web_observer import DEFAULT_PORT
    from .version_check import DEFAULT_TIMEOUT

    parser = argparse.ArgumentParser(prog="hypoloom")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    from .institutional_prepare import add_parser as add_institutional_parser
    add_institutional_parser(subparsers)
    versions = subparsers.add_parser("version-check", help="advisory comparison with the remote Git branch; never updates code")
    versions.add_argument("--repository", help="checkout to inspect; default: the current Hypoloom installation")
    versions.add_argument("--remote", help="configured Git remote name; default: branch upstream, then origin")
    versions.add_argument("--branch", help="remote branch; default: tracked branch, then current branch")
    versions.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help=f"total check deadline in seconds (default: {DEFAULT_TIMEOUT:g}, maximum: 30)")
    versions.add_argument("--offline", action="store_true", help="show local identity without contacting a remote")
    versions.add_argument("--json", action="store_true")

    comparison = subparsers.add_parser("reliability-compare", help="compare provider execution evidence across Hypoloom roots, read-only")
    comparison.add_argument("--state-root", action="append", required=True)
    comparison_format = comparison.add_mutually_exclusive_group()
    comparison_format.add_argument("--json", action="store_true")
    comparison_format.add_argument("--markdown", action="store_true")

    pool_serve = subparsers.add_parser("gpu-pool-serve", help="explicitly start the node-local GPU capacity service (GPU-P1)")
    pool_serve.add_argument("--pool-root", required=True)
    pool_serve.add_argument("--socket-path", required=True)
    pool_serve.add_argument("--member", action="append", required=True, metavar="UID=GPU_UUID[,GPU_UUID...]")
    pool_serve.add_argument("--reserve-mib", type=int, default=0)
    pool_serve.add_argument("--socket-gid", type=int, help="explicit group for a mode-0660 socket; parent directory permissions are operator-owned")
    pool_status = subparsers.add_parser("gpu-pool-status", help="query authorized aggregate capacity; creates no pool or run")
    pool_status.add_argument("--expected-server-uid", type=int, default=None)
    pool_status.add_argument("--socket-path", required=True)
    pool_status.add_argument("--timeout", type=float, default=5.0)
    pool_audit = subparsers.add_parser("gpu-pool-audit", help="read-only independent capacity-record audit, not a run qualification")
    pool_audit.add_argument("--pool-root", required=True)

    serve = subparsers.add_parser("serve", help="start the sole resident Workflow Host")
    serve.add_argument("--state-root")
    serve.add_argument("--fault-point", default=None)
    serve.add_argument("--agent-adapter", choices=("fake", "claude"), default="fake")
    serve.add_argument("--fake-agent-mode")
    serve.add_argument("--notification-output-root", help="disjoint notification delivery output; default: state-root sibling ending -notifications")
    serve.add_argument("--notification-proxy-env", help="explicit environment variable for the notification HTTP CONNECT proxy")
    serve.add_argument("--notification-credentials-file", help="existing 0600 dotenv file supplying notification variables; replaces ambient notification values")
    serve.add_argument("--claude-executable")
    serve.add_argument("--claude-settings")
    serve.add_argument(
        "--claude-model",
        help="pinned non-alias Claude selector; a trailing [1m] is preserved for CC",
    )
    _add_literature_arguments(serve)
    from .institutional_options import add_arguments as add_institutional_arguments
    add_institutional_arguments(serve, connection=True)
    serve.add_argument("--web-search-fallback", choices=("openalex",),
        help="explicit academic-metadata fallback when DuckDuckGo fails; requires OpenAlex literature configuration")
    serve.add_argument("--claude-bwrap")
    serve.add_argument("--agent-max-output-bytes", type=int)
    serve.add_argument("--agent-max-tokens", type=int)
    serve.add_argument("--agent-max-turns", type=int)
    serve.add_argument("--agent-wall-seconds", type=float)
    serve.add_argument("--agent-max-cost-usd")
    serve.add_argument("--gpu-runtime-prefix")
    serve.add_argument("--gpu-device-uuid", action="append", default=None)
    serve.add_argument("--gpu-pool-socket", help="connect this Host to an existing shared GPU pool socket")
    serve.add_argument("--gpu-pool-namespace", help="stable operator-assigned pool identity for this state root")
    serve.add_argument("--gpu-pool-server-uid", type=int, help="require the pool socket peer to have this OS UID")
    serve.add_argument(
        "--exit-when-terminal",
        action="store_true",
        help=(
            "exit cleanly once at least one run exists, every run is terminal, "
            "and no Attempt is open"
        ),
    )

    profile_check = subparsers.add_parser(
        "profile-check",
        help="compile a profile read-only or report every unsupported semantic",
    )
    _add_profile_arguments(profile_check)

    start = subparsers.add_parser(
        "start",
        help="admit one compatible profile and submit its exact CREATE_RUN payload",
    )
    _add_client_arguments(start)
    _add_profile_arguments(start)
    start.set_defaults(expected_revision=0)

    status = subparsers.add_parser("status", help="inspect status through the Host")
    _add_client_arguments(status)
    status.add_argument("--run-id", required=True)
    status.set_defaults(expected_revision=None)

    answer = subparsers.add_parser("answer", help="answer one exact pending Grounding clarification through the Host")
    _add_client_arguments(answer)
    answer.add_argument("--run-id", required=True)
    answer.add_argument("--decision-id", required=True)
    answer.add_argument("--answer", required=True)
    answer.add_argument("--expected-revision", type=int, required=True)

    for verb in ("pause", "resume", "inject"):
        participant = subparsers.add_parser(verb, help="send a durable participant control to the running Host")
        _add_client_arguments(participant)
        participant.add_argument("--run-id", required=True)
        participant.add_argument("--expert", required=verb == "inject")
        participant.add_argument("--component")
        participant.add_argument("--chain")
        participant.add_argument("--expected-revision", type=int)
        if verb == "inject":
            participant.add_argument("text")

    resources = subparsers.add_parser("resource-recheck", help="recheck the bound GPU pool and release blocked research nodes")
    _add_client_arguments(resources)
    resources.add_argument("--run-id", required=True)
    resources.add_argument("--expected-revision", type=int)

    provider_clear = subparsers.add_parser("provider-retry-clear",
        help="allow one recovery attempt with the current or an explicitly selected API connection")
    _add_client_arguments(provider_clear)
    provider_clear.add_argument("--run-id", required=True)
    provider_clear.add_argument("--expected-revision", type=int)
    provider_clear.add_argument("--settings", help="absolute Host-side 0600 JSON connection file; env may contain only API key/auth token/base URL")
    provider_clear.add_argument("--service", choices=("model", "openalex"), default="model", help="select the unavailable dependency; OpenAlex reloads its Profile credential")

    for action in ("pin", "revert", "unpin"):
        plugin = subparsers.add_parser("plugin-" + action,
            help="Host-only plugin selection for future cycles; pin/revert lock until explicit unpin")
        _add_client_arguments(plugin)
        plugin.add_argument("--run-id", required=True)
        plugin.add_argument("--expert", "--role", dest="role", required=True)
        plugin.add_argument("--owner-kind", choices=("expert", "grounding"), default="expert")
        plugin.add_argument("--commit", required=action != "unpin", help="full Git commit; omitted unpin selects latest accepted evolution tip")
        plugin.add_argument("--expected-revision", type=int)

    for action in ("session-archive", "session-restore"):
        archive = subparsers.add_parser(action, help=("read retained standard-tar inventory or one bounded member" if action == "session-archive"
            else "explicitly restore a terminal run's archived session trees; never resume science or overwrite changed context"))
        archive.add_argument("--state-root")
        archive.add_argument("--run-id", required=True)
        if action == "session-archive":
            archive.add_argument("--member")
            archive.add_argument("--max-bytes", type=int, default=1024 * 1024)

    backup = subparsers.add_parser("backup", help="request one synchronous quiesced backup through the Host")
    _add_client_arguments(backup)
    backup.add_argument("--backup-root", required=True)
    backup.add_argument("--backup-id", required=True)
    backup.set_defaults(expected_revision=None)

    restore = subparsers.add_parser("restore", help="restore a backup offline into a nonexistent fresh state root")
    restore.add_argument("--state-root")
    restore.add_argument("--backup-path", required=True)
    restore.add_argument("--claude-settings", help="original private settings supplied separately; never read from or written into the backup")

    oracle = subparsers.add_parser("oracle", help="audit one run offline without starting a Host")
    oracle.add_argument("--state-root")
    oracle.add_argument("--run-id", required=True)

    monitor = subparsers.add_parser("monitor", help="inspect one run as an offline export, JSON, or read-only TUI")
    monitor.add_argument("--state-root")
    monitor.add_argument("--run-id", required=True)
    monitor_mode = monitor.add_mutually_exclusive_group()
    monitor_mode.add_argument("--json", action="store_true", dest="json_output")
    monitor_mode.add_argument(
        "--export", dest="export_dir", metavar="DIR",
        help="export an offline research view to a new directory outside the state root",
    )
    monitor.add_argument("--interval", type=float, default=2.0)
    monitor.add_argument("--gpu-pool-socket", help="explicit authorized aggregate capacity socket for Web; no discovery")
    monitor.add_argument("--gpu-pool-server-uid", type=int)
    monitor.add_argument(
        "--web-port",
        type=int,
        default=DEFAULT_PORT,
        help="TUI Web port on 127.0.0.1 (default: 0, OS-selected free port)",
    )
    monitor.add_argument(
        "--no-web",
        action="store_false",
        dest="web_enabled",
        help="do not start the TUI-owned loopback Web observer",
    )

    web = subparsers.add_parser(
        "web",
        help="serve one bearer-protected, loopback-only read-only Web observer",
    )
    web.add_argument("--state-root")
    web.add_argument("--run-id", required=True)
    web.add_argument("--port", type=int, default=DEFAULT_PORT,
        help="loopback Web port (default: 0, OS-selected free port)")
    web.add_argument("--allow-human-answers", action="store_true", help="grant an operator link for reviewed Grounding answers")
    web.add_argument("--allow-provider-control", action="store_true", help="grant an operator recovery link for API connection selection and GPU resource recheck")
    web.add_argument("--allow-literature-control", action="store_true", help="grant an operator link for institutional full-text actions")
    web.add_argument("--gpu-pool-socket", help="explicit authorized aggregate capacity socket; no discovery")
    web.add_argument("--gpu-pool-server-uid", type=int)

    notify = subparsers.add_parser("notify", help="explicitly deliver configured events; read-only run, separate output cursors")
    notify.add_argument("--state-root")
    notify.add_argument("--run-id", required=True)
    notify.add_argument("--output-root")
    notify_action = notify.add_mutually_exclusive_group()
    notify_action.add_argument("--test", action="store_true", help="verify Telegram access without sending")
    notify_action.add_argument("--send-test", action="store_true", help="send one explicitly requested test message")
    notify_action.add_argument("--retry", action="store_true", help="request managed Telegram retry")
    notify.add_argument("--once", action="store_true")
    notify.add_argument("--poll-interval", type=float, default=5.0)
    notify.add_argument("--proxy-env", help="explicit environment variable for an HTTP CONNECT proxy; default: direct")

    graph = subparsers.add_parser("graph", help="read or export the accepted graph without starting a Host")
    graph.add_argument("--state-root")
    graph.add_argument("--run-id", required=True)
    graph.add_argument("--export", dest="graph_export", metavar="FILE.html|FILE.json")

    debug = subparsers.add_parser(
        "debug",
        help="inspect or validate durable module records without changing a run",
    )
    debug_actions = debug.add_subparsers(dest="debug_action", required=True)
    debug_inspect = debug_actions.add_parser(
        "inspect", help="derive a read-only module diagnosis",
    )
    debug_inspect.add_argument("--state-root")
    debug_inspect.add_argument("--run-id", required=True)
    debug_inspect.add_argument("--attempt-id")
    debug_inspect.add_argument(
        "--include-payloads",
        action="store_true",
        help="include exact research inputs and observations on stdout",
    )
    debug_export = debug_actions.add_parser(
        "export", help="export a reviewed support copy or one exact private Attempt fixture",
    )
    debug_export.add_argument("--state-root")
    debug_export.add_argument("--run-id", help="omit only for unassigned startup diagnostics in support mode")
    debug_export.add_argument("--attempt-id")
    debug_export.add_argument("--output", help="new private directory, or ZIP file with --archive")
    debug_export.add_argument("--support-bundle", action="store_true",
        help="write a fresh private support directory, optionally scoped to an Attempt")
    debug_export.add_argument("--include-private-data", action="store_true",
        help="include sanitized prompts, transcripts and research attachments in a support bundle")
    debug_export.add_argument("--preview", action="store_true", help="preview a support report without creating an output")
    debug_export.add_argument("--archive", action="store_true", help="write the support copy as a ZIP file")
    debug_export.add_argument("--recent-failures", action="store_true", help="include attachments for the last three failing commands and their retries")
    debug_export.add_argument("--issue-actual", help="optional observed behavior included in the reviewed support copy")
    debug_export.add_argument("--issue-expected", help="optional expected behavior")
    debug_export.add_argument("--issue-steps", help="optional reproduction steps")
    debug_export.add_argument("--issue-occurred-at", help="optional occurrence time and timezone")
    debug_export.add_argument("--max-total-mib", type=int, default=20, help="support uncompressed byte limit (default: 20 MiB)")
    debug_export.add_argument("--max-file-mib", type=int, default=4, help="support per-attachment limit (default: 4 MiB)")
    debug_export.add_argument("--max-files", type=int, default=500, help="support output file limit (default: 500)")
    debug_validate = debug_actions.add_parser(
        "validate", help="run current pure contract validators over one Attempt",
    )
    debug_validate.add_argument("--fixture")
    debug_validate.add_argument("--state-root")
    debug_validate.add_argument("--run-id")
    debug_validate.add_argument("--attempt-id")
    validation_mode = debug_validate.add_mutually_exclusive_group(required=True)
    validation_mode.add_argument(
        "--source-exact",
        action="store_const",
        const="source-exact",
        dest="validation_mode",
        help=(
            "fail closed unless this validator's complete tuple matches the "
            "frozen source tuple"
        ),
    )
    validation_mode.add_argument(
        "--candidate",
        action="store_const",
        const="candidate",
        dest="validation_mode",
        help="validate explicitly against the current counterfactual candidate",
    )
    debug_rehearse = debug_actions.add_parser(
        "rehearse-chain",
        help=(
            "rebind one validated schema-v2 QUERY_PLAN or design output to "
            "a fresh non-authoritative Host request"
        ),
    )
    debug_rehearse.add_argument("--source-state-root", required=True)
    debug_rehearse.add_argument("--source-run-id", required=True)
    debug_rehearse.add_argument("--through-attempt-id", required=True)
    debug_rehearse.add_argument("profile")
    debug_rehearse.add_argument("--profiles-root")
    debug_rehearse.add_argument("--state-root", required=True)
    debug_rehearse.add_argument("--run-id", required=True)
    debug_rehearse.add_argument("--process-delay", type=float, default=0.0)
    debug_rehearse.add_argument("--wall-seconds", type=float, default=3_600.0)
    debug_rehearse.add_argument("--allow-agent", action="store_true")
    debug_rehearse.add_argument("--allow-process", action="store_true")
    debug_rehearse.add_argument("--allow-gpu", action="store_true")
    debug_rehearse.add_argument("--gpu-device-uuid", action="append", default=None)
    debug_rehearse.add_argument("--gpu-runtime-prefix")

    cancel = subparsers.add_parser("cancel", help="persist an exact cancellation request")
    _add_client_arguments(cancel)
    cancel.add_argument("--run-id", required=True)
    cancel.add_argument("--expected-revision", required=True, type=int)
    cancel.add_argument("--cause", default="operator_request")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.subcommand == "institutional":
            from .institutional_prepare import run as prepare_institutional
            return prepare_institutional(args)
        if args.subcommand == "version-check":
            from .version_check import check_updates
            checked = check_updates(args.repository, offline=args.offline, remote=args.remote,
                                    branch=args.branch, timeout=args.timeout)
            if args.json:
                print(json.dumps(checked, ensure_ascii=False, sort_keys=True))
            else:
                print(f"Hypoloom {checked['package_version'] or 'unknown'} | "
                      f"HEAD {(checked['local_revision'] or 'unknown')[:12]} | {checked['branch'] or 'no current branch'}")
                print(checked["message"])
            return 0
        if args.subcommand == "reliability-compare":
            from .reliability_compare import as_markdown, compare_roots
            comparison = compare_roots(args.state_root)
            print(json.dumps(comparison, ensure_ascii=False, sort_keys=True) if args.json else as_markdown(comparison), end="\n" if args.json else "")
            return 0
        if args.subcommand == "gpu-pool-serve":
            from .compute_resources import observe_authorized_gpu_memory, validate_gpu_uuid
            from .gpu_pool import CapacitySample, GpuPool
            from .gpu_attribution import GpuAttributionSampler
            from .gpu_pool_server import serve_pool
            members: dict[int, tuple[str, ...]] = {}
            for member in args.member:
                uid_text, separator, devices_text = member.partition("=")
                if not separator or re.fullmatch(r"[0-9]+", uid_text) is None:
                    raise ValueError("--member requires UID=GPU_UUID[,GPU_UUID...]")
                uid = int(uid_text)
                if uid in members:
                    raise ValueError("--member must list each UID once")
                devices = tuple(devices_text.split(","))
                for device in devices:
                    validate_gpu_uuid(device)
                if len(devices) != len(set(devices)):
                    raise ValueError("--member GPU UUIDs must not contain duplicates")
                members[uid] = devices
            allowed = tuple(sorted({device for devices in members.values() for device in devices}))
            pool = GpuPool(Path(args.pool_root), members=members,
                reserve_memory_mib=args.reserve_mib,
                sampler=lambda: CapacitySample(devices=observe_authorized_gpu_memory(allowed)),
                attribution_sampler=GpuAttributionSampler().pool_residents)
            serve_pool(pool, Path(args.socket_path), socket_gid=args.socket_gid)
            return 0
        if args.subcommand == "gpu-pool-status":
            from .gpu_pool import PoolError
            from .gpu_pool_client import PoolClient
            try:
                result = PoolClient(Path(args.socket_path), timeout_seconds=args.timeout,
                    expected_server_uid=args.expected_server_uid).request("snapshot", {})
            except PoolError as exc:
                print(json.dumps({"status": "error", "code": exc.code, "error": str(exc)}, sort_keys=True), file=sys.stderr)
                return 2
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.subcommand == "gpu-pool-audit":
            from .oracle import audit_gpu_pool
            result = audit_gpu_pool(Path(args.pool_root))
            print(json.dumps(result, sort_keys=True))
            return 0 if result["ok"] else 2
        if args.subcommand == "serve":
            if argv is None:
                from .runtime_bundle import enter_runtime_bundle
                enter_runtime_bundle(_state_root(args.state_root), sys.argv[1:])
            from .workflow_host import serve
            from .observer_services import ObserverServices
            from .compute_resources import LocalComputeResources
            if (args.gpu_pool_socket is None) != (args.gpu_pool_namespace is None):
                parser.error("--gpu-pool-socket and --gpu-pool-namespace must be configured together")
            if args.gpu_pool_server_uid is not None and (args.gpu_pool_socket is None or args.gpu_pool_server_uid < 0):
                parser.error("--gpu-pool-server-uid requires a pool socket and a nonnegative UID")
            pool_socket = None
            if args.gpu_pool_socket is not None:
                from .gpu_pool import RequestKey
                if not args.gpu_pool_socket or "\x00" in args.gpu_pool_socket:
                    parser.error("--gpu-pool-socket must be a non-empty path without NUL")
                RequestKey(os.getuid(), args.gpu_pool_namespace, "run", "execution", "attempt")
                pool_socket = Path(args.gpu_pool_socket).expanduser().absolute()
            claude = None
            budget = None
            provider_environment: dict[str, str] = {}
            claude_settings = args.claude_settings or None
            claude_required_values = (args.claude_executable, args.claude_model)
            claude_values = (*claude_required_values, claude_settings)
            if args.agent_adapter == "claude":
                if not all(claude_required_values):
                    parser.error("Claude adapter requires executable and a pinned model selector")
                if args.fake_agent_mode is not None:
                    parser.error("--fake-agent-mode is fake-only")
                if claude_settings is None:
                    api_key = os.environ.get("ANTHROPIC_API_KEY")
                    if not api_key:
                        parser.error(
                            "Claude adapter without --claude-settings requires "
                            "non-empty ANTHROPIC_API_KEY"
                        )
                    provider_environment["ANTHROPIC_API_KEY"] = api_key
                    if base_url := os.environ.get("ANTHROPIC_BASE_URL"):
                        provider_environment["ANTHROPIC_BASE_URL"] = base_url
                from .claude_agent_host import ClaudeAgentHost, validate_claude_model_selector
                validate_claude_model_selector(args.claude_model)
                claude = ClaudeAgentHost(claude_executable=args.claude_executable,
                    settings_path=claude_settings,
                    bwrap_path=args.claude_bwrap or "/usr/bin/bwrap")
                budget = AgentBudget(
                    max_turns=(DEFAULT_REAL_AGENT_BUDGET.max_turns
                               if args.agent_max_turns is None else args.agent_max_turns),
                    max_tokens=(DEFAULT_REAL_AGENT_BUDGET.max_tokens
                                if args.agent_max_tokens is None else args.agent_max_tokens),
                    max_output_bytes=(DEFAULT_REAL_AGENT_BUDGET.max_output_bytes
                                      if args.agent_max_output_bytes is None
                                      else args.agent_max_output_bytes),
                    wall_seconds=(DEFAULT_REAL_AGENT_BUDGET.wall_seconds
                                  if args.agent_wall_seconds is None else args.agent_wall_seconds),
                    max_cost=(DEFAULT_REAL_AGENT_BUDGET.max_cost
                              if args.agent_max_cost_usd is None else
                              None if args.agent_max_cost_usd == "null" else args.agent_max_cost_usd),
                )
            elif any(value is not None for value in (
                    *claude_values, args.claude_bwrap, args.agent_max_output_bytes,
                    args.agent_max_tokens, args.agent_max_turns, args.agent_wall_seconds,
                    args.agent_max_cost_usd)):
                parser.error("Agent budget options require --agent-adapter claude")
            if bool(args.gpu_device_uuid) != bool(args.gpu_runtime_prefix):
                parser.error("GPU device UUIDs and --gpu-runtime-prefix must be configured together")
            resources = LocalComputeResources(
                allowed_gpu_uuids=tuple(args.gpu_device_uuid or ()),
                gpu_runtime_prefix=args.gpu_runtime_prefix,
            )
            literature_io = _literature_adapter(args)
            web_options = {}
            from .institutional_options import build as build_institutional
            access = build_institutional(args)
            if access is not None: web_options["institutional_access"] = access
            if args.web_search_fallback is not None and literature_io is None:
                parser.error("--web-search-fallback openalex requires --literature-adapter openalex")
            if literature_io is not None:
                from .grounding_web import GroundingWeb
                web_options["grounding_web"] = GroundingWeb(academic_search=literature_io.fetcher,
                    search_fallback=literature_io.fetcher if args.web_search_fallback is not None else None)
            from .notification_tailer import notification_environment
            notification_env = notification_environment(args.notification_credentials_file)
            return serve(
                _state_root(args.state_root),
                fault_point=args.fault_point,
                fake_agent_mode=args.fake_agent_mode or "VALID", claude_agent_host=claude,
                claude_model=args.claude_model, agent_budget=budget,
                compute_resources=resources, provider_environment=provider_environment,
                gpu_pool_socket=pool_socket, gpu_pool_namespace=args.gpu_pool_namespace,
                gpu_pool_server_uid=args.gpu_pool_server_uid,
                exit_when_terminal=args.exit_when_terminal,
                literature_io=literature_io,
                **web_options,
                services_factory=lambda: ObserverServices(_state_root(args.state_root), output_root=args.notification_output_root,
                    proxy_env=args.notification_proxy_env, environ=notification_env),
            )
        if args.subcommand in {"profile-check", "start"}:
            from .profile_admission import ProfileBlocker, admit_profile

            report = admit_profile(
                _profile_path(args.profile, args.profiles_root),
                run_id=args.run_id,
                allow_gpu=args.allow_gpu,
                delay_seconds=args.process_delay,
            )
            try:
                _profile_literature_source(args, report)
                literature_preflight = _literature_preflight(args)
            except ValueError as exc:
                code = getattr(getattr(exc, "code", None), "value", None)
                literature_preflight = {
                    "adapter": args.literature_adapter,
                    "qualified": False,
                    "error_code": code or "LITERATURE_PREFLIGHT_FAILED",
                    "error": str(exc),
                    "network_probe_performed": False,
                }
                report = replace(
                    report,
                    blockers=report.blockers
                    + (
                        ProfileBlocker(
                            code="literature_preflight_failed",
                            location="operator.literature",
                            message=str(exc),
                        ),
                    ),
                    create_run_payload=None,
                )
            if args.subcommand == "profile-check" or not report.admitted:
                stream = sys.stdout if report.admitted else sys.stderr
                payload = report.to_dict()
                payload["literature_preflight"] = literature_preflight
                print(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    file=stream,
                )
                return 0 if report.admitted else 2
            if report.create_run_payload is None:
                raise AssertionError("admitted profile lacks CREATE_RUN payload")
            return _submit(args, "CREATE_RUN", report.create_run_payload)
        if args.subcommand == "status":
            return _submit(args, "STATUS", {"run_id": args.run_id})
        if args.subcommand == "answer":
            if not args.answer.strip() or "\x00" in args.answer:
                raise ValueError("Grounding answer must be nonempty text")
            return _submit(args, "ANSWER", {"run_id": args.run_id,
                "decision_id": args.decision_id, "answer": args.answer})
        if args.subcommand == "backup":
            return _submit(args, "BACKUP", {"backup_root": args.backup_root, "backup_id": args.backup_id})
        if args.subcommand == "restore":
            restored = restore_backup(args.backup_path, _state_root(args.state_root), audit=audit_all_runs, claude_settings=args.claude_settings)
            print(json.dumps({"status": "accepted", "result": {"path": str(restored)}}, sort_keys=True))
            return 0
        if args.subcommand == "oracle":
            root = _state_root(args.state_root)
            report = Oracle(root / "state.sqlite", root / "artifacts").audit(args.run_id)
            print(report.to_json())
            return 0 if report.ok else 2
        if args.subcommand == "monitor":
            from .monitor import run_monitor
            return run_monitor(
                _state_root(args.state_root), args.run_id,
                json_output=args.json_output, interval=args.interval,
                export_dir=args.export_dir,
                web_enabled=args.web_enabled, web_port=args.web_port,
                **({"gpu_pool_socket": args.gpu_pool_socket, "gpu_pool_server_uid": args.gpu_pool_server_uid}
                   if args.gpu_pool_socket is not None or args.gpu_pool_server_uid is not None else {}),
            )
        if args.subcommand == "web":
            from .web_observer import run_web_observer
            return run_web_observer(
                _state_root(args.state_root),
                args.run_id,
                port=args.port,
                **({"allow_human_answers": True} if args.allow_human_answers else {}),
                **({"allow_provider_control": True} if args.allow_provider_control else {}),
                **({"allow_literature_control": True} if args.allow_literature_control else {}),
                **({"gpu_pool_socket": args.gpu_pool_socket, "gpu_pool_server_uid": args.gpu_pool_server_uid}
                   if args.gpu_pool_socket is not None or args.gpu_pool_server_uid is not None else {}),
            )
        if args.subcommand == "notify":
            from .notification_tailer import main as notify_main
            arguments = ["--state-root", str(_state_root(args.state_root)), "--run-id", args.run_id,
                "--poll-interval", str(args.poll_interval)]
            if args.output_root is not None: arguments.extend(["--output-root", args.output_root])
            for flag in ("test", "send_test", "retry"):
                if getattr(args, flag): arguments.append("--" + flag.replace("_", "-"))
            if args.once:
                arguments.append("--once")
            if args.proxy_env is not None:
                arguments.extend(["--proxy-env", args.proxy_env])
            return notify_main(arguments)
        if args.subcommand == "graph":
            if args.graph_export:
                from .graph_export import export_graph
                value = export_graph(_state_root(args.state_root), args.run_id, args.graph_export)
            else:
                from .observation_api import build_product_observation
                observation = build_product_observation(_state_root(args.state_root), args.run_id)
                value = {"run_id": args.run_id, "observed_at": observation["observed_at"], "graph": observation["graph"]}
            print(json.dumps(value, ensure_ascii=False, sort_keys=True))
            return 0
        if args.subcommand in {"pause", "resume", "inject"}:
            from .participant_controls import ParticipantScope
            scope = ParticipantScope(args.expert, args.component, args.chain)
            payload = {"run_id": args.run_id, "scope": asdict(scope), "reason": "operator"}
            if args.subcommand == "inject":
                if not args.text.strip() or "\x00" in args.text:
                    raise ValueError("operator note must be nonempty text")
                payload["text"] = args.text
            return _submit(args, args.subcommand.upper(), payload)
        if args.subcommand == "resource-recheck":
            return _submit(args, "RESOURCE_RECHECK", {"run_id": args.run_id, "reason": "operator"})
        if args.subcommand == "provider-retry-clear":
            if args.service == "openalex" and args.settings is not None: raise ValueError("OpenAlex uses its Profile credential; --settings only applies to the model API")
            return _submit(args, "PROVIDER_RETRY_CLEAR", {"run_id": args.run_id, "reason": "operator",
                **({"service": "openalex"} if args.service == "openalex" else {}),
                **({"settings_path": args.settings} if args.settings is not None else {})})
        if args.subcommand in {"plugin-pin", "plugin-revert", "plugin-unpin"}:
            from .expert_plugin import _component, _full_commit
            _component(args.role)
            if args.commit is not None:
                _full_commit(args.commit)
            action = args.subcommand.removeprefix("plugin-").upper()
            return _submit(args, action + "_PLUGIN", {"run_id": args.run_id, "role": args.role,
                "owner_kind": args.owner_kind, "commit": args.commit})
        if args.subcommand in {"session-archive", "session-restore"}:
            from .session_archive import inspect_session_archive, read_session_archive_file, restore_run_sessions
            if args.subcommand == "session-restore":
                value = {"run_id": args.run_id, "restored": [str(path) for path in restore_run_sessions(_state_root(args.state_root), args.run_id)],
                    "archive_retained": True, "run_resumed": False}
            elif args.member:
                import base64
                content = read_session_archive_file(_state_root(args.state_root), args.run_id, args.member, max_bytes=args.max_bytes)
                value = {"run_id": args.run_id, "member": args.member, "content_base64": base64.b64encode(content).decode("ascii")}
            else:
                value = {"run_id": args.run_id, "members": inspect_session_archive(_state_root(args.state_root), args.run_id)}
            print(json.dumps(value, ensure_ascii=False, sort_keys=True))
            return 0
        if args.subcommand == "debug":
            from .debug_mode import (
                build_debug_report,
                validate_debug_fixture,
                validate_debug_report,
                write_debug_fixture,
            )

            if args.debug_action == "inspect":
                report = build_debug_report(
                    _state_root(args.state_root),
                    args.run_id,
                    attempt_id=args.attempt_id,
                    include_payloads=args.include_payloads,
                )
                print(json.dumps(report, ensure_ascii=False, sort_keys=True))
                return 0
            if args.debug_action == "export":
                if args.support_bundle:
                    from .support_bundle import build_support_bundle, export_support_bundle
                    options = dict(attempt_id=args.attempt_id, include_private_data=args.include_private_data,
                        recent_failures=args.recent_failures, max_bytes=args.max_total_mib * 1024 * 1024,
                        max_file_bytes=args.max_file_mib * 1024 * 1024, max_files=args.max_files,
                        issue_description={key: value for key in ("actual", "expected", "steps", "occurred_at")
                            if (value := getattr(args, "issue_" + key)) is not None})
                    if args.preview:
                        if args.output or args.archive:
                            parser.error("--preview cannot be combined with --output or --archive")
                        report, files = build_support_bundle(_state_root(args.state_root), args.run_id, **options)
                        print(json.dumps({"status": "preview", "report": report,
                            "readme": files["README.md"].decode()}, ensure_ascii=False, sort_keys=True))
                        return 0
                    if args.output is None:
                        parser.error("support export requires --output or --preview")
                    report = export_support_bundle(_state_root(args.state_root), args.run_id,
                        args.output, archive=args.archive, **options)
                    exported = Path(args.output).expanduser().absolute()
                    print(json.dumps({"status": "exported", "kind": "support_bundle",
                        "non_authoritative": True, "path": str(exported),
                        "report_path": "report.json (inside ZIP)" if args.archive else str(exported / "report.json"),
                        "limited": report["limited"], "diagnosis_available": report["diagnosis"]["available"],
                        "run_id": args.run_id, "attempt_id": args.attempt_id,
                    }, ensure_ascii=False, sort_keys=True))
                    return 0
                if args.include_private_data or args.preview or args.archive or args.recent_failures or any(
                        getattr(args, "issue_" + key) is not None for key in ("actual", "expected", "steps", "occurred_at")):
                    parser.error("support options require --support-bundle")
                if args.run_id is None or args.output is None:
                    parser.error("exact fixture export requires --run-id and --output")
                if args.attempt_id is None:
                    parser.error("debug export requires --attempt-id unless --support-bundle is selected")
                report = build_debug_report(
                    _state_root(args.state_root),
                    args.run_id,
                    attempt_id=args.attempt_id,
                    include_payloads=True,
                )
                exported = write_debug_fixture(args.output, report)
                print(json.dumps({
                    "status": "exported",
                    "non_authoritative": True,
                    "path": str(exported),
                    "run_id": args.run_id,
                    "attempt_id": args.attempt_id,
                }, ensure_ascii=False, sort_keys=True))
                return 0
            if args.debug_action == "validate":
                if args.fixture is not None:
                    if any(value is not None for value in (
                        args.state_root, args.run_id, args.attempt_id,
                    )):
                        parser.error(
                            "debug validate --fixture cannot be combined with state/run/Attempt options"
                        )
                    result = validate_debug_fixture(
                        args.fixture, validation_mode=args.validation_mode,
                    )
                else:
                    if args.run_id is None or args.attempt_id is None:
                        parser.error(
                            "debug validate requires --fixture or --state-root/--run-id/--attempt-id"
                        )
                    report = build_debug_report(
                        _state_root(args.state_root),
                        args.run_id,
                        attempt_id=args.attempt_id,
                        include_payloads=True,
                    )
                    result = validate_debug_report(
                        report, validation_mode=args.validation_mode,
                    )
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
                return 0 if result["valid"] else 2
            if args.debug_action == "rehearse-chain":
                from .compute_resources import LocalComputeResources
                from .module_rehearsal import rehearse_module_chain
                from .profile_admission import admit_profile

                if bool(args.gpu_device_uuid) != bool(args.gpu_runtime_prefix):
                    parser.error(
                        "GPU UUIDs and --gpu-runtime-prefix must be configured together"
                    )
                if args.allow_gpu != bool(args.gpu_device_uuid):
                    parser.error(
                        "--allow-gpu requires an exact GPU UUID allowlist and runtime"
                    )
                admission = admit_profile(
                    _profile_path(args.profile, args.profiles_root),
                    run_id=args.run_id,
                    allow_gpu=args.allow_gpu,
                    delay_seconds=args.process_delay,
                )
                if not admission.admitted or admission.create_run_payload is None:
                    print(
                        json.dumps(admission.to_dict(), ensure_ascii=False, sort_keys=True),
                        file=sys.stderr,
                    )
                    return 2
                result = rehearse_module_chain(
                    source_state_root=args.source_state_root,
                    source_run_id=args.source_run_id,
                    source_attempt_id=args.through_attempt_id,
                    state_root=args.state_root,
                    run_id=args.run_id,
                    create_run_payload=admission.create_run_payload,
                    allow_agent=args.allow_agent,
                    allow_process=args.allow_process,
                    allow_gpu=args.allow_gpu,
                    compute_resources=LocalComputeResources(
                        allowed_gpu_uuids=tuple(args.gpu_device_uuid or ()),
                        gpu_runtime_prefix=args.gpu_runtime_prefix,
                    ),
                    wall_seconds=args.wall_seconds,
                )
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
                return 0 if result["closure"]["complete"] else 2
        if args.subcommand == "cancel":
            return _submit(
                args,
                "CANCEL",
                {"run_id": args.run_id, "cause": args.cause},
            )
    except HostConfigurationError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 78
    except (BackupRestoreError, RuntimeError, OSError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    except ValueError as exc:
        parser.error(str(exc))
    raise AssertionError(f"unhandled command: {args.subcommand}")


if __name__ == "__main__":
    raise SystemExit(main())
