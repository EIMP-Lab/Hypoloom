"""Deterministic preparation and explicit managed launch for /autoresearch init.

The six Profile files and ordinary CLI arguments remain the only inputs.
Preview data is not a saved launch manifest or authority to start a run.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import yaml

from .cli import _SAFE_PROFILE_NAME, _add_profile_arguments, _literature_preflight
from .profile_admission import ProfileBlocker, _UniqueKeyLoader, admit_profile
from .profile_v3 import PROFILE_V3_FILES


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repository), *arguments],
                          capture_output=True, text=True, check=False)


def _repository(value: str) -> Path:
    root = Path(value).expanduser().resolve(strict=True)
    found = _git(root, "rev-parse", "--show-toplevel")
    if found.returncode or Path(found.stdout.strip()) != root:
        raise ValueError("repository must be the root of the selected Git checkout")
    return root


def _child(root: Path, name: str) -> Path:
    if _SAFE_PROFILE_NAME.fullmatch(name) is None:
        raise ValueError("profile/template name must be one safe path component")
    path = root / name
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError("profile/template must be an immediate real child")
    return path


def _ignored(repository: Path, path: Path) -> None:
    if _git(repository, "check-ignore", "--no-index", "-q", "--", str(path)).returncode:
        raise ValueError("private Profile/state path must be Git-ignored")


def _path_info(path: Path) -> dict:
    result = {"path": str(path), "exists": path.exists()}
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return result
    return {**result, "mode": format(stat.S_IMODE(mode), "04o"),
            "regular_file": stat.S_ISREG(mode), "symlink": stat.S_ISLNK(mode)}


def _credentials_reference(document: object, *parents: str) -> str | None:
    for key in (*parents, "credentials_file"):
        if not isinstance(document, dict):
            raise ValueError("Profile credential reference parent must be a YAML object")
        if key not in document:
            return None
        document = document[key]
    if document is not None and (not isinstance(document, str) or not Path(document).is_absolute()):
        raise ValueError("Profile credentials reference must be an absolute path")
    return document


def locations(repository: Path, profile: str | None = None) -> dict:
    """Show locations and metadata, never credentials or research contents."""
    local = repository / "profiles/local"
    templates = repository / "profiles/templates"
    result = {
        "credential_storage": "Private Profile methodology.yaml and notify.yaml; no separate env required.",
        "credentials": _path_info(repository / ".local/credentials/hypoloom.env"),
        "credentials_template": _path_info(repository / "config/hypoloom.env.example"),
        "profiles_root": str(local), "templates_root": str(templates),
        "state_root": str(repository / "state"),
        "model_auth": "Separate: selected environment or existing Claude settings; not this dotenv file.",
        "legacy_credentials_note": "credentials and credentials_template describe optional legacy dotenv compatibility only.",
    }
    for key, root in (("local_profiles", local), ("profile_templates", templates)):
        result[key] = sorted(p.name for p in root.iterdir()
            if p.is_dir() and not p.is_symlink() and _SAFE_PROFILE_NAME.fullmatch(p.name)) if root.is_dir() else []
    if profile:
        selected = _child(local, profile)
        result["profile"] = _path_info(selected)
        result["profile_credentials"] = {name: _path_info(selected / name)
            for name in ("methodology.yaml", "notify.yaml")}
        declared = {}
        for name in ("methodology.yaml", "notify.yaml"):
            path = selected / name
            if not path.is_file() or path.is_symlink():
                continue
            document = yaml.load(path.read_text(), Loader=_UniqueKeyLoader)
            parents = ("literature",) if name == "methodology.yaml" else ("sinks", "telegram")
            value = _credentials_reference(document, *parents)
            if value is not None:
                declared[name] = _path_info(Path(value))
        result["declared_credentials"] = declared
    return result


def candidate(repository: Path, profile: str, template: str | None, overrides: list[str]) -> tuple[Path, dict[str, str] | None]:
    destination = _child(repository / "profiles/local", profile)
    _ignored(repository, destination)
    if template is None:
        if overrides:
            raise ValueError("existing Profiles are read-only; --set requires a new template destination")
        return destination, None
    if os.path.lexists(destination):
        raise ValueError("Profile destination already exists; never overwrite or merge it")
    source = _child(repository / "profiles/templates", template)
    if {p.name for p in source.iterdir()} != set(PROFILE_V3_FILES):
        raise ValueError("template must contain exactly the six YAML files")
    texts = {}
    for name in PROFILE_V3_FILES:
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("template files must be regular and not symlinks")
        if _git(repository, "ls-files", "--error-unmatch", "--", str(path)).returncode:
            raise ValueError("template files must be tracked by Git")
        texts[name] = path.read_text(encoding="utf-8")
    seen = set()
    for override in overrides:
        field, separator, raw = override.partition("=")
        parts = field.split(".")
        name = parts.pop(0) + ".yaml"
        if not separator or not parts or any(not part for part in parts) or name not in texts or field in seen:
            raise ValueError("--set requires a unique section.field=YAML-value")
        seen.add(field)
        document = yaml.load(texts[name], Loader=_UniqueKeyLoader)
        node = document
        for part in parts[:-1]:
            if not isinstance(node, dict):
                raise ValueError("--set parent must be a YAML object")
            node = node.setdefault(part, {})
        if not isinstance(node, dict):
            raise ValueError("--set parent must be a YAML object")
        node[parts[-1]] = yaml.load(raw, Loader=_UniqueKeyLoader)
        texts[name] = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    methodology = yaml.load(texts["methodology.yaml"], Loader=_UniqueKeyLoader)
    if not isinstance(methodology, dict) or methodology.get("schema_version") != 4:
        raise ValueError("new Profiles must use a v4 template; no format migration")
    from .credentials import profile_has_credentials
    for name in ("methodology.yaml", "notify.yaml"):
        if profile_has_credentials(yaml.load(texts[name], Loader=_UniqueKeyLoader), name):
            raise ValueError("fill credentials directly in the private Profile after prepare; never put values in --set or public templates")
    return destination, texts


def write_profile(destination: Path, texts: dict[str, str]) -> None:
    """Exclusive creation only; leave any partial result for explicit review."""
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination.mkdir(mode=0o700)
    for name in PROFILE_V3_FILES:
        descriptor = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(texts[name])


def _literature_arguments(args) -> list[str]:
    names = ("literature_adapter", "credentials_file", "openalex_api_key_file", "unpaywall_email_file",
             "literature_pdf_fallback", "literature_pdfinfo", "literature_pdftotext", "literature_timeout",
             "literature_max_response_bytes", "literature_max_pdf_bytes", "literature_max_pdf_pages")
    return [value for name in names if getattr(args, name) is not None
            for value in ("--" + name.replace("_", "-"), str(getattr(args, name)))]


def model_review(contract: dict, host_model: str) -> dict:
    """An ephemeral explanation using the same resolver as actual Agent requests."""
    from .profile_language import PROFILE_LANGUAGE_ROUTES
    from .profile_v4 import resolve_node_runtime
    roster = contract.get("methodology", {}).get("controls", {}).get("product", {}).get("experts", {})
    if not roster:
        return {"host_default_model": host_model, "routes": [], "profile_models_overriding_host_default": [],
                "note": "Legacy Profile: all Agent routes use the Host model; interactive session model is not inherited."}
    rows = []
    def add(role, route, **options):
        sources = {}
        runtime = resolve_node_runtime(contract, role, route, sources=sources, **options)
        rows.append({"role": role, "route": route, **options,
            "model": runtime.get("model", host_model), "effort": runtime.get("effort"),
            "sources": {"model": sources.get("model", "Host --claude-model"),
                        "effort": sources.get("effort", "native CLI default (not inherited from interactive session)")}})
    for route in ("grounding", "graph_evolve", "observer", "debate_judge", "final_report"):
        add(None, route)
    for expert in roster.get("experts", ()):
        role = expert["name"]
        for route in (*PROFILE_LANGUAGE_ROUTES[1:], "self_evolve", "adjudicate", "debate", "leader"):
            add(role, route)
        add(None, "observer", observer_source_role=role)
        add(role, "hypothesis_proposal.synthesis", designated_leader=True)
    different = sorted({row["model"] for row in rows if row["model"] != host_model})
    return {"host_default_model": host_model, "routes": rows,
        "profile_models_overriding_host_default": different,
        "note": "All possible routes, including conditional roles. Profile overrides take precedence; interactive session model is not inherited."}


def launch_arguments(args, repository: Path, report, *, review: dict | None = None) -> dict:
    """Project admitted budgets and one literature vector into existing commands."""
    if not report.admitted:
        raise ValueError("launch arguments require an admitted Profile")
    review = {} if review is None else review
    payload = report.create_run_payload
    contract = payload["research_contract"]
    review["budget"] = contract["budget"]
    python = Path(args.control_python)
    if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("control Python must be an absolute executable file")
    state = Path(args.state_root)
    if not state.is_absolute() or os.path.lexists(state):
        raise ValueError("state root must be an absent absolute path")
    state = state.resolve()
    if state.is_relative_to(repository):
        if state.parent != repository / "state" or _SAFE_PROFILE_NAME.fullmatch(state.name) is None:
            raise ValueError("repository-local state root must be an immediate safe child of state/")
        _ignored(repository, state)
    request_id = args.request_id or args.run_id + "-create-1"
    if _SAFE_PROFILE_NAME.fullmatch(request_id) is None:
        raise ValueError("request ID must be one safe path component")
    if not 0 < args.ready_timeout <= 300:
        raise ValueError("ready timeout must be greater than zero and at most 300 seconds")
    if (args.gpu_pool_socket is None) != (args.gpu_pool_namespace is None):
        raise ValueError("pool socket and namespace must be selected together")
    if args.gpu_pool_server_uid is not None and (args.gpu_pool_socket is None or args.gpu_pool_server_uid < 0):
        raise ValueError("pool server UID requires a pool socket and nonnegative UID")
    if args.gpu_pool_socket is not None:
        from .gpu_pool import RequestKey
        if not Path(args.gpu_pool_socket).is_absolute():
            raise ValueError("pool socket must be an absolute path")
        RequestKey(os.getuid(), args.gpu_pool_namespace, "run", "execution", "attempt")
    if args.allow_gpu:
        if not args.gpu_device_uuid or not args.gpu_runtime_prefix:
            raise ValueError("GPU launch needs the exact approved UUIDs and runtime prefix")
        from .compute_resources import LocalComputeResources, ComputeResourceError
        from .profile_v4 import restrict_profile_capabilities, validate_gpu_pool_budget
        prefix = Path(args.gpu_runtime_prefix)
        if not prefix.is_absolute() or str(prefix.resolve()) != payload["execution"]["runtime_prefix"]:
            raise ValueError("Host GPU runtime must match the admitted Profile runtime.conda_env")
        validate_gpu_pool_budget(contract, pooled=args.gpu_pool_socket is not None)
        resources = LocalComputeResources(allowed_gpu_uuids=tuple(args.gpu_device_uuid), gpu_runtime_prefix=prefix)
        try:
            inventory = resources.snapshot()
        except ComputeResourceError as exc:
            raise ValueError(str(exc)) from exc
        if {gpu.uuid for gpu in inventory.gpus} != set(args.gpu_device_uuid):
            raise ValueError("Host-authorized GPU UUIDs must all be present in the observed inventory")
        selected = restrict_profile_capabilities(contract, inventory)
        if not selected.gpus:
            raise ValueError("GPU launch conflicts with Profile safety.gpus=[]; select CPU-only or review the prospective Profile")
        review["gpu"] = {"host_authorized_uuids": list(resources.allowed_gpu_uuids),
                         "profile_effective_uuids": [gpu.uuid for gpu in selected.gpus],
                         "runtime_prefix": str(prefix.resolve()), "pooled": args.gpu_pool_socket is not None}
    elif args.gpu_device_uuid or args.gpu_runtime_prefix or args.gpu_pool_socket:
        raise ValueError("CPU-only launch cannot contain GPU or pool options")
    if args.web_search_fallback is not None and args.literature_adapter != "openalex":
        raise ValueError("web fallback requires the reviewed OpenAlex adapter")
    real = args.auth_transport != "none"
    if real:
        if not args.claude_model or not args.claude_executable or args.literature_adapter != "openalex":
            raise ValueError("real launch requires an exact model, Claude executable and OpenAlex")
        if (args.auth_transport == "settings") != (args.claude_settings is not None):
            raise ValueError("settings and environment authentication cannot be mixed")
        if args.auth_transport == "environment" and not os.environ.get("ANTHROPIC_API_KEY"):
            raise ValueError("environment authentication is unavailable")
        auth_code, auth = _invoke([str(python), "-B", str(repository /
            ".claude/skills/autoresearch/scripts/resolve_model_options.py"),
            *(["--settings", args.claude_settings] if args.claude_settings else ["--environment"])],
            {"PYTHONDONTWRITEBYTECODE": "1", "HYPOLOOM_RUNTIME_FAMILY": "hypoloom"}, cwd=repository)
        if auth_code:
            raise ValueError("selected model authentication source is not qualified: " + str(auth.get("error", "inspection failed")))
        review["models"] = model_review(contract, args.claude_model)
        review["models"]["selected_auth_source_default"] = auth.get("configured_default")
        from .claude_agent_host import ClaudeAgentHost, AgentHostError
        try:
            ClaudeAgentHost(claude_executable=args.claude_executable, settings_path=args.claude_settings,
                            bwrap_path=args.claude_bwrap).preflight(model=args.claude_model)
        except AgentHostError as exc:
            raise ValueError(str(exc)) from exc
    elif args.claude_model or args.claude_executable or args.claude_settings:
        raise ValueError("fake launch cannot contain Claude authentication or model options")
    from .notification_tailer import notification_environment, notification_preflight
    product = contract.get("methodology", {}).get("controls", {}).get("product", {})
    if args.notification_output_root is not None and not Path(args.notification_output_root).is_absolute():
        raise ValueError("notification output root must be absolute")
    notification_env = notification_environment(args.notification_credentials_file, environ={})
    try:
        review["notifications"] = notification_preflight(product.get("notify", {}), state_root=state,
            output_root=None if args.notification_output_root is None else Path(args.notification_output_root),
            environ=notification_env, proxy_env=args.notification_proxy_env)
    except ValueError as exc:
        raise ValueError(str(exc) + "; fill notification credentials in the private notify.yaml; "
                         "legacy file sources remain supported, but managed init does not inherit the interactive environment") from None
    review["notifications"]["credentials_file"] = args.notification_credentials_file
    literature = _literature_arguments(args)
    cli = [str(python), "-B", "-m", "hypoloom.cli"]
    # manage_host validates the established Python -m hypoloom.cli serve shape;
    # bytecode suppression is carried in the subprocess environment below.
    serve = [str(python), "-m", "hypoloom.cli", "serve", "--state-root", str(state),
             "--exit-when-terminal", "--agent-adapter", "claude" if real else "fake", *literature]
    if real:
        budget = report.create_run_payload["research_contract"]["budget"]["agent_invocation"]
        serve += ["--claude-executable", args.claude_executable, "--claude-model", args.claude_model,
                  "--claude-bwrap", args.claude_bwrap]
        for key, flag in (("max_turns", "--agent-max-turns"), ("max_tokens", "--agent-max-tokens"),
                          ("max_output_bytes", "--agent-max-output-bytes"), ("wall_seconds", "--agent-wall-seconds"),
                          ("max_cost", "--agent-max-cost-usd")):
            serve += [flag, "null" if budget[key] is None else str(budget[key])]
    for name in ("claude_settings", "gpu_runtime_prefix", "gpu_pool_socket", "gpu_pool_namespace",
                 "gpu_pool_server_uid", "web_search_fallback", "notification_output_root", "notification_proxy_env",
                 "notification_credentials_file"):
        if getattr(args, name) is not None:
            serve += ["--" + name.replace("_", "-"), str(getattr(args, name))]
    for uuid in args.gpu_device_uuid or ():
        serve += ["--gpu-device-uuid", uuid]
    common = [args.profile, "--profiles-root", str(repository / "profiles/local"), "--run-id", args.run_id,
              "--process-delay", str(args.process_delay), "--allow-gpu" if args.allow_gpu else "--cpu-only", *literature]
    helper = [str(python), "-B", str(repository / ".claude/skills/autoresearch/scripts/manage_host.py")]
    commands = {"working_directory": str(repository),
            "environment": {"PYTHONDONTWRITEBYTECODE": "1", "HYPOLOOM_RUNTIME_FAMILY": "hypoloom"},
            "profile_check": [*cli, "profile-check", *common],
            "host_start": [*helper, "start", "--state-root", str(state), "--working-directory", str(repository),
                "--ready-timeout", str(args.ready_timeout), "--auth-transport", args.auth_transport, "--", *serve],
            "start": [*cli, "start", *common, "--state-root", str(state), "--request-id", request_id],
            "status": [*cli, "status", "--state-root", str(state), "--run-id", args.run_id],
            "host_stop": [*helper, "stop", "--state-root", str(state), "--auth-transport", args.auth_transport]}
    if args.gpu_pool_socket:
        commands["pool_status"] = [*cli, "gpu-pool-status", "--socket-path", args.gpu_pool_socket,
            *(["--expected-server-uid", str(args.gpu_pool_server_uid)] if args.gpu_pool_server_uid is not None else [])]
    return commands


def _invoke(argv: list[str], environment: dict[str, str], *, cwd: str | Path) -> tuple[int, dict]:
    try:
        completed = subprocess.run(argv, cwd=cwd, env={**os.environ, **environment}, capture_output=True,
                                   text=True, timeout=360, check=False)
        document = json.loads(completed.stdout or completed.stderr)
        if not isinstance(document, dict):
            raise ValueError("command result must be an object")
        return completed.returncode, document
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 3, {"status": "transport_error"}


def start_fresh(commands: dict, payload: dict) -> dict:
    """Use existing managed Host/CLI lifecycle; never delete or reuse a root.

    Ambiguous CREATE_RUN results are reconciled with STATUS before any action.
    An automatic transport retry is deliberately unnecessary here.
    """
    def call(name):
        return _invoke(commands[name], commands["environment"], cwd=commands["working_directory"])

    def checked():
        code, result = call("profile_check")
        return code == 0 and result.get("admitted") is True and result.get("create_run_payload") == payload

    if not checked():
        return {"status": "profile_changed_before_host"}
    if "pool_status" in commands:
        code, pool = call("pool_status")
        if code:
            return {"status": "pool_unavailable", "pool": pool}
    code, host = call("host_start")
    if code or host.get("status") != "ready":
        return {"status": "host_start_failed" if code == 2 else "host_start_uncertain", "host": host}
    if not checked():
        _, stopped = call("host_stop")
        return {"status": "profile_changed_before_create", "host": host, "cleanup": stopped}
    code, created = call("start")
    status_code, observed = call("status")
    if code == 0 and created.get("status") == "accepted":
        return {"status": "started", "host": host, "creation": created, "observation": observed}
    if status_code == 0 and observed.get("status") == "accepted":
        return {"status": "started", "host": host, "creation_reconciled": True, "observation": observed}
    if observed.get("status") == "rejected" and observed.get("error", {}).get("code") == "not_found":
        _, stopped = call("host_stop")
        return {"status": "create_failed", "host": host, "creation": created, "cleanup": stopped}
    return {"status": "creation_uncertain", "host": host, "creation": created, "observation": observed}


def build_parser() -> argparse.ArgumentParser:
    from .version_check import DEFAULT_TIMEOUT
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    paths = commands.add_parser("paths", help="show private data and template locations without reading secrets")
    paths.add_argument("--repository", default=str(Path.cwd()))
    paths.add_argument("--profile")
    paths.add_argument("--offline", action="store_true", help="skip the advisory remote version check")
    paths.add_argument("--version-timeout", type=float, default=DEFAULT_TIMEOUT, help="total version-check deadline in seconds (maximum 30)")
    for action in ("prepare", "launch"):
        command = commands.add_parser(action, help="preview Profile preparation" if action == "prepare" else "derive reviewed CLI arguments without starting anything")
        command.add_argument("--repository", default=str(Path.cwd()))
        _add_profile_arguments(command)
        command.add_argument("--template", help="tracked v4 template for a new local Profile")
        command.add_argument("--set", dest="overrides", action="append", default=[], metavar="SECTION.FIELD=YAML")
        if action == "prepare":
            command.add_argument("--write", action="store_true", help="explicitly create the reviewed six-file destination; never start a run")
        else:
            command.add_argument("--start", action="store_true", help="explicitly authorize this fresh managed Host and run; default is preview only")
            command.add_argument("--state-root", required=True)
            command.add_argument("--request-id")
            command.add_argument("--control-python", default=sys.executable)
            command.add_argument("--ready-timeout", type=float, default=30)
            command.add_argument("--auth-transport", choices=("none", "settings", "environment"), required=True)
            for name in ("claude-executable", "claude-model", "claude-settings", "gpu-runtime-prefix",
                         "gpu-pool-socket", "gpu-pool-namespace", "notification-output-root", "notification-proxy-env",
                         "notification-credentials-file"):
                command.add_argument("--" + name)
            command.add_argument("--claude-bwrap", default="/usr/bin/bwrap")
            command.add_argument("--gpu-device-uuid", action="append")
            command.add_argument("--gpu-pool-server-uid", type=int)
            command.add_argument("--web-search-fallback", choices=("openalex",))
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if os.environ.get("HYPOLOOM_RUNTIME_FAMILY") not in (None, "", "hypoloom"):
            raise ValueError("conflicting runtime family")
        repository = _repository(args.repository)
        if args.action == "paths":
            result = locations(repository, args.profile)
            from .version_check import check_updates
            result["version_check"] = check_updates(repository, offline=args.offline, timeout=args.version_timeout)
        else:
            if args.profiles_root is not None and Path(args.profiles_root).resolve() != repository / "profiles/local":
                raise ValueError("bootstrap profiles root must be repository/profiles/local")
            destination, texts = candidate(repository, args.profile, args.template, args.overrides)
            report = admit_profile(destination, run_id=args.run_id, allow_gpu=args.allow_gpu,
                                   delay_seconds=args.process_delay, source_texts=texts)
            result = {"admission": report.to_dict(), "profile_written": False,
                      "provider_probe_performed": False, "paths": locations(repository)}
            try:
                from .cli import _profile_literature_source
                _profile_literature_source(args, report)
                result["literature_preflight"] = _literature_preflight(args)
            except ValueError as exc:
                result["literature_preflight"] = {"qualified": False, "network_probe_performed": False, "error": str(exc)}
                report = replace(report, blockers=(*report.blockers,
                    ProfileBlocker("literature_preflight_failed", "operator.literature", str(exc))), create_run_payload=None)
                result["admission"] = report.to_dict()
            if args.action == "prepare":
                if args.write:
                    if texts is None:
                        raise ValueError("--write requires a new template destination")
                    write_profile(destination, texts)
                    result["profile_written"] = True
                result["candidate_files"] = texts
            elif report.admitted:
                result["effective_configuration"] = {}
                result["commands"] = launch_arguments(args, repository, report, review=result["effective_configuration"])
                if args.start:
                    if texts is not None:
                        raise ValueError("prepare the reviewed Profile before --start; templates cannot launch directly")
                    result["launch"] = start_fresh(result["commands"], report.create_run_payload)
                    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
                    return 0 if result["launch"]["status"] == "started" else 2
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0 if report.admitted else 2
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except yaml.YAMLError:
        print(json.dumps({"error": "invalid YAML in Profile or --set value"}), file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
