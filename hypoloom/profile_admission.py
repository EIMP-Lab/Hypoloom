"""Read-only, fail-closed compilation of six profile files into CREATE_RUN."""

from __future__ import annotations

import copy
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from .component_contract import component_execution_blockers, compile_component_contract
from .contract_normalization import (
    EXPERIMENT_ISOLATION_GIT_AUTHORING_WORKTREE,
    EXPERT_LOOP_PROFILE_VERSION,
    EXPERT_LOOP_RESEARCH_LOOP,
)
from .execution_workspace import (
    TargetPreparationError, bind_clean_git_target, validate_bound_git_target,
    execution_read_root_projection,
)
from .profile_v3 import (
    PROFILE_V3_FILES,
    PROFILE_V3_VERSION,
    ProfileV3Projection,
    project_profile_v3,
)
from .profile_v4 import PROFILE_V4_VERSION, ProfileV4Projection, project_profile_v4


LEGACY_PROFILE_FILES = (
    "goal.md",
    "experts.yaml",
    "methodology.yaml",
    "language.md",
    "notify.yaml",
    "safety.yaml",
)
# Retained callers use this historical alias.  New authoring uses
# PROFILE_V3_FILES; admission resolves the family from the directory itself.
PROFILE_FILES = LEGACY_PROFILE_FILES
_LEGACY_YAML_FILES = ("experts.yaml", "methodology.yaml", "notify.yaml", "safety.yaml")
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that refuses duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True, slots=True)
class ProfileBlocker:
    """One exact reason a profile cannot become a Hypoloom CREATE_RUN payload."""

    code: str
    location: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "location": self.location, "message": self.message}


@dataclass(frozen=True, slots=True)
class ProfileAdmissionReport:
    """Pure admission result; it is neither durable state nor run authority."""

    profile_name: str
    profile_path: str
    mapped_fields: tuple[str, ...]
    blockers: tuple[ProfileBlocker, ...]
    create_run_payload: dict[str, object] | None
    warnings: tuple[ProfileBlocker, ...] = ()

    @property
    def admitted(self) -> bool:
        return not self.blockers and self.create_run_payload is not None

    def to_dict(self) -> dict[str, object]:
        execution = (self.create_run_payload or {}).get("execution")
        visibility = None
        if isinstance(execution, Mapping):
            bindings, environment = execution_read_root_projection(execution, {}, generic=True)
            visibility = {"scope": "generic_execution_projection", "target": execution.get("target"),
                "runtime": {"host": execution.get("runtime_prefix"), "sandbox": "/runtime"},
                "read_roots": [{"host": str(host), "sandbox": sandbox} for host, sandbox in bindings],
                "environment": environment,
                "note": "Declared mounts only; data/model completeness is not inferred. Production plans may bind explicit data paths."}
        return {
            "profile_name": self.profile_name,
            "profile_path": self.profile_path,
            "admitted": self.admitted,
            "mapped_fields": list(self.mapped_fields),
            "blockers": [blocker.to_dict() for blocker in self.blockers],
            "warnings": [warning.to_dict() for warning in self.warnings],
            "create_run_payload": copy.deepcopy(self.create_run_payload),
            "resource_visibility": visibility,
        }


class ProfileCompilationError(ValueError):
    """Compilation was refused because admission found one or more blockers."""

    def __init__(self, report: ProfileAdmissionReport) -> None:
        self.report = report
        details = ", ".join(
            f"{blocker.code}@{blocker.location}" for blocker in report.blockers
        )
        super().__init__(
            f"profile {report.profile_name!r} is not admissible: {details}"
        )


def admit_profile(
    profile_dir: str | Path,
    *,
    run_id: str,
    allow_gpu: bool = False,
    delay_seconds: float = 0.0,
    source_texts: Mapping[str, str] | None = None,
) -> ProfileAdmissionReport:
    """Admit files or prospective file texts through the same compiler.

    source_texts previews a new Profile without creating its directory. It
    grants no runtime authority; start always rereads the actual six files.
    """

    root = Path(profile_dir)
    blockers: list[ProfileBlocker] = []
    mapped: list[str] = []
    texts: dict[str, str] = {}

    def block(code: str, location: str, message: str) -> None:
        blockers.append(ProfileBlocker(code=code, location=location, message=message))

    if source_texts is None and not root.is_dir():
        block(
            "profile_directory_missing", str(root), "profile path must be a directory"
        )
        return _report(root, mapped, blockers, None)

    present = set(source_texts) if source_texts is not None else {entry.name for entry in root.iterdir()}
    profile_v3 = bool(present & {"goal.yaml", "language.yaml"})
    expected_files = PROFILE_V3_FILES if profile_v3 else LEGACY_PROFILE_FILES
    yaml_files = PROFILE_V3_FILES if profile_v3 else _LEGACY_YAML_FILES
    for name in expected_files:
        path = root / name
        if source_texts is not None:
            if name not in present:
                block("missing_profile_file", name, "all six profile files are required")
            elif not isinstance(source_texts[name], str):
                block("profile_read_error", name, "profile content must be text")
            else:
                texts[name] = source_texts[name]
            continue
        if name not in present or not path.is_file():
            block("missing_profile_file", name, "all six profile files are required")
            continue
        if profile_v3 and path.is_symlink():
            block(
                "profile_file_not_regular",
                name,
                "profile files must be regular files and must not be symlinks",
            )
            continue
        try:
            texts[name] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            block("profile_read_error", name, str(exc))
    for name in sorted(present - set(expected_files)):
        block(
            "unexpected_profile_file",
            name,
            "unexamined profile files could contain semantics and are not ignored",
        )

    documents: dict[str, Mapping[str, Any]] = {}
    for name in yaml_files:
        if name not in texts:
            continue
        try:
            value = yaml.load(texts[name], Loader=_UniqueKeyLoader)
        except yaml.YAMLError as exc:
            block("invalid_yaml", name, str(exc))
            continue
        if value is None:
            value = {}
        if not isinstance(value, Mapping) or any(
            not isinstance(key, str) for key in value
        ):
            block(
                "invalid_yaml_root", name, "YAML root must be an object with text keys"
            )
            continue
        documents[name] = value

    compilation = None
    v3_projection: ProfileV3Projection | None = None
    v4_projection: ProfileV4Projection | None = None
    warnings: list[ProfileBlocker] = []
    profile_contract_version = 1
    successor_controls: dict[str, object] | None = None
    if profile_v3:
        if all(name in documents for name in PROFILE_V3_FILES):
            if documents["methodology.yaml"].get("schema_version") == PROFILE_V4_VERSION:
                v4_projection = project_profile_v4(**{name.removesuffix(".yaml"): documents[name] for name in PROFILE_V3_FILES})
                v3_projection = v4_projection.projection
                documents["notify.yaml"] = v4_projection.notify; documents["safety.yaml"] = v4_projection.safety
                profile_contract_version = PROFILE_V4_VERSION
                blockers.extend(ProfileBlocker(item.code, item.location, item.message) for item in v4_projection.issues if item not in v3_projection.issues)
                warnings.extend(ProfileBlocker(item.code, item.location, item.message) for item in v4_projection.warnings)
            else:
                v3_projection = project_profile_v3(goal=documents["goal.yaml"], experts=documents["experts.yaml"],
                    methodology=documents["methodology.yaml"], language=documents["language.yaml"])
                profile_contract_version = PROFILE_V3_VERSION
            mapped.extend(v3_projection.mapped_fields)
            blockers.extend(
                ProfileBlocker(item.code, item.location, item.message)
                for item in v3_projection.issues
            )
            documents["experts.yaml"] = v3_projection.experts
            documents["methodology.yaml"] = v3_projection.methodology
    elif "methodology.yaml" in documents:
        methodology = copy.deepcopy(dict(documents["methodology.yaml"]))
        raw_hg = methodology.get("hg")
        if isinstance(raw_hg, Mapping) and "language_routes" in raw_hg:
            block(
                "profile_contract_version_unsupported",
                "methodology.yaml.hg.language_routes",
                "typed language routes require the schema-v3 six-YAML layout",
            )
        raw_version = methodology.pop("schema_version", 1)
        if (
            isinstance(raw_version, bool)
            or not isinstance(raw_version, int)
            or raw_version not in {1, EXPERT_LOOP_PROFILE_VERSION}
        ):
            block(
                "profile_contract_version_unsupported",
                "methodology.yaml.schema_version",
                "schema_version must be 1 or 2",
            )
        else:
            profile_contract_version = raw_version
        if profile_contract_version != EXPERT_LOOP_PROFILE_VERSION and isinstance(
            methodology.get("hg"), Mapping
        ) and methodology["hg"].get(
            "experiment_isolation"
        ) == EXPERIMENT_ISOLATION_GIT_AUTHORING_WORKTREE:
            block(
                "successor_profile_version_required",
                "methodology.yaml.schema_version",
                "git_authoring_worktree requires schema_version=2",
            )
        documents["methodology.yaml"] = methodology
    if profile_contract_version in {EXPERT_LOOP_PROFILE_VERSION, PROFILE_V3_VERSION, PROFILE_V4_VERSION}:
        methodology = copy.deepcopy(dict(documents.get("methodology.yaml", {})))
        raw_hg = methodology.get("hg")
        hg = copy.deepcopy(dict(raw_hg)) if isinstance(raw_hg, Mapping) else {}
        required = {
            "research_loop": EXPERT_LOOP_RESEARCH_LOOP,
            "domain_grounding": "required",
            "experiment_isolation": EXPERIMENT_ISOLATION_GIT_AUTHORING_WORKTREE,
            "observer": "disabled",
        }
        source_prefix = "methodology.yaml.mode" if profile_v3 else "methodology.yaml.hg"
        for key, expected in required.items():
            if not profile_v3 and hg.get(key) != expected:
                block(
                    "successor_profile_contract_invalid",
                    f"{source_prefix}.{key}",
                    f"v{profile_contract_version} requires exact {key}={expected}",
                )
        successor_controls = {
            key: hg.pop(key, None) for key in ("research_loop", "observer")
        }
        methodology["hg"] = hg
        documents["methodology.yaml"] = methodology
        if profile_v3:
            mapped.append(f"methodology.yaml.schema_version={profile_contract_version} -> structured successor profile admission")
        else:
            mapped.append("methodology.yaml.schema_version=2 -> successor profile admission")

    if all(name in texts for name in expected_files) and all(
        name in documents for name in yaml_files
    ):
        goal_text = (
            v3_projection.goal_text
            if v3_projection is not None
            else texts["goal.md"]
        )
        language_text = (
            v3_projection.language_text
            if v3_projection is not None
            else texts["language.md"]
        )
        compilation = compile_component_contract(
            goal_text=goal_text,
            language_text=language_text,
            experts=documents["experts.yaml"],
            methodology=documents["methodology.yaml"],
            notify=documents["notify.yaml"],
            safety=documents["safety.yaml"],
        )
        if not profile_v3:
            mapped.extend(compilation.mapped_fields)
        blockers.extend(
            ProfileBlocker(
                item.code,
                (
                    _v3_location(
                        item.location,
                        () if v3_projection is None else v3_projection.source_locations,
                    )
                    if profile_v3
                    else item.location
                ),
                item.message,
            )
            for item in compilation.blockers
        )
        if compilation.contract is not None:
            if successor_controls is not None:
                controls = compilation.contract["methodology"]["controls"]
                controls.update(successor_controls)
                if v4_projection is not None:
                    controls["product"] = copy.deepcopy(dict(v4_projection.settings))
                    if v4_projection.settings["goal"].get("evaluation") == "grounding":
                        compilation.contract["schema_version"] = 3
                        compilation.contract["goal"]["evaluation"] = {"mode": "grounding"}
                        mapped.append("goal.yaml.evaluation=grounding -> unresolved common acceptance commission")
                    mapped.append("Profile v4 product settings -> research_contract.methodology.controls.product; inactive fields listed in warnings")
                required_components = tuple(
                    component
                    for component in compilation.contract["components"]
                    if component.get("tier") in {"primary", "must"}
                )
                required_owners: set[str] = set()
                for component in required_components:
                    component_id = str(component["id"])
                    owner = str(component["owner"])
                    proposers = component.get("proposers")
                    required_owners.add(owner)
                    if proposers != [owner]:
                        block(
                            "expert_loop_owner_proposer_mismatch",
                            (
                                f"goal.yaml.completion.signals[{component_id}]"
                                if profile_v3
                                else f"methodology.yaml.component_proposers.{component_id}"
                            ),
                            f"v{profile_contract_version} requires each primary/must component to have its owner as the sole proposer",
                        )
                for role in compilation.contract["roles"]:
                    role_id = str(role["id"])
                    if role_id not in required_owners:
                        block(
                            "expert_loop_role_without_required_component",
                            "experts.yaml.roles" if profile_v3 else "experts.yaml.enabled",
                            f"v{profile_contract_version} enabled role {role_id!r} must own at least one primary/must component",
                        )
                    role_budget = role.get("budget", {})
                    if not isinstance(role_budget, Mapping) or not isinstance(
                        role_budget.get("max_papers"), int
                    ) or role_budget["max_papers"] < 3:
                        block(
                            "expert_loop_literature_budget_too_small",
                            (
                                f"methodology.yaml.budget.roles.{role_id}.max_papers"
                                if profile_v3
                                else f"experts.yaml.budgets.{role_id}.max_papers"
                            ),
                            f"v{profile_contract_version} requires at least one paper for each of the three literature strata",
                        )
            blockers.extend(
                ProfileBlocker(item.code, item.location, item.message)
                for item in component_execution_blockers(compilation.contract)
            )
    roles = compilation.roles if compilation is not None else None
    operational: dict[str, object] = {
        "target": None,
        "runtime_prefix": None,
        "read_roots": [],
    }
    if compilation is not None and compilation.contract is not None:
        contract = compilation.contract
        runtime = contract["runtime"]
        safety_contract = contract["safety"]
        target_repo = runtime.get("target_repo")
        if profile_contract_version in {EXPERT_LOOP_PROFILE_VERSION, PROFILE_V3_VERSION, PROFILE_V4_VERSION} and target_repo is None:
            block(
                "target_binding_required",
                "methodology.yaml.runtime.target_repo",
                f"v{profile_contract_version} requires one exact clean Git target",
            )
        if target_repo is not None:
            try:
                binding = bind_clean_git_target(str(target_repo))
                validate_bound_git_target(binding)
                operational["target"] = {
                    "checkout": str(binding.checkout),
                    "commit": binding.commit,
                }
            except (OSError, TargetPreparationError, ValueError) as exc:
                block(
                    "target_binding_failed",
                    "methodology.yaml.runtime.target_repo",
                    str(exc),
                )
        runtime_prefix = runtime.get("conda_env")
        if runtime_prefix is not None:
            prefix = Path(str(runtime_prefix))
            if (
                prefix.is_symlink()
                or not prefix.is_absolute()
                or not (prefix / "bin" / "python").is_file()
            ):
                block(
                    "runtime_binding_failed",
                    "methodology.yaml.runtime.conda_env",
                    "runtime must be an absolute real environment with bin/python",
                )
            else:
                operational["runtime_prefix"] = str(prefix.resolve())
        roots = safety_contract.get("read_roots", [])
        resolved_roots: list[str] = []
        for index, value in enumerate(roots):
            root_path = Path(str(value))
            if (
                root_path.is_symlink()
                or not root_path.is_absolute()
                or not root_path.is_dir()
                or not os.access(root_path, os.R_OK | os.X_OK)
            ):
                block(
                    "read_root_binding_failed",
                    f"safety.yaml.allow_read_paths[{index}]",
                    "read root must be an existing readable/searchable absolute real directory",
                )
            else:
                resolved_roots.append(str(root_path.resolve()))
        operational["read_roots"] = resolved_roots

    if not isinstance(run_id, str) or _SAFE_RUN_ID.fullmatch(run_id) is None:
        block(
            "invalid_create_run_value",
            "CREATE_RUN.run_id",
            "run_id must be one safe path component",
        )
    if not isinstance(allow_gpu, bool):
        block(
            "invalid_create_run_value",
            "CREATE_RUN.execution.allow_gpu",
            "allow_gpu must be boolean",
        )
    if (
        isinstance(delay_seconds, bool)
        or not isinstance(delay_seconds, (int, float))
        or not math.isfinite(float(delay_seconds))
        or float(delay_seconds) < 0
    ):
        block(
            "invalid_create_run_value",
            "CREATE_RUN.execution.delay_seconds",
            "delay_seconds must be finite and non-negative",
        )

    payload: dict[str, object] | None = None
    if (
        not blockers
        and compilation is not None
        and compilation.contract is not None
        and roles is not None
    ):
        contract = compilation.contract
        payload = {
            "run_id": run_id,
            "execution": {
                "allow_gpu": allow_gpu,
                "delay_seconds": float(delay_seconds),
                **operational,
            },
            "research_contract": contract,
        }
        mapped.extend(
            (
                "operator.run_id -> run_id",
                "operator.execution -> execution",
            )
        )
    return _report(root, mapped, blockers, payload, warnings)


def compile_profile(
    profile_dir: str | Path,
    *,
    run_id: str,
    allow_gpu: bool = False,
    delay_seconds: float = 0.0,
) -> dict[str, object]:
    """Return an exact CREATE_RUN payload or raise with the admission report."""

    report = admit_profile(
        profile_dir,
        run_id=run_id,
        allow_gpu=allow_gpu,
        delay_seconds=delay_seconds,
    )
    if not report.admitted or report.create_run_payload is None:
        raise ProfileCompilationError(report)
    return copy.deepcopy(report.create_run_payload)


def _report(
    root: Path,
    mapped: list[str],
    blockers: list[ProfileBlocker],
    payload: dict[str, object] | None,
    warnings: list[ProfileBlocker] | None = None,
) -> ProfileAdmissionReport:
    return ProfileAdmissionReport(
        profile_name=root.name,
        profile_path=str(root),
        mapped_fields=tuple(mapped),
        blockers=tuple(blockers),
        create_run_payload=payload,
        warnings=tuple(warnings or ()),
    )


def _v3_location(
    location: str,
    source_locations: tuple[tuple[str, str], ...],
) -> str:
    """Translate internal v1 compiler locations back to schema-v3 sources."""

    replacements = (
        *source_locations,
        ("goal.md", "goal.yaml.objective"),
        ("language.md", "language.yaml"),
        ("methodology.yaml.success_criteria", "goal.yaml.completion.signals"),
        ("methodology.yaml.experiments.protocols", "goal.yaml.completion.evaluation_protocols"),
        ("methodology.yaml.delivery_contracts", "goal.yaml.completion.delivery_contracts"),
        ("methodology.yaml.dependencies", "goal.yaml.completion.signals"),
        ("methodology.yaml.component_owners", "methodology.yaml.mode.component_ownership"),
        ("methodology.yaml.component_proposers", "methodology.yaml.mode.component_ownership"),
        ("methodology.yaml.hg", "methodology.yaml.mode"),
        ("experts.yaml.enabled", "experts.yaml.roles"),
        ("experts.yaml.focus", "experts.yaml.roles"),
        ("experts.yaml.autonomy", "methodology.yaml.mode"),
        ("experts.yaml.budgets", "methodology.yaml.budget.roles"),
    )
    for source, destination in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        if location.startswith(source):
            return destination + location[len(source) :]
    return location


__all__ = [
    "LEGACY_PROFILE_FILES",
    "PROFILE_FILES",
    "PROFILE_V3_FILES",
    "ProfileAdmissionReport",
    "ProfileBlocker",
    "ProfileCompilationError",
    "admit_profile",
    "compile_profile",
]
