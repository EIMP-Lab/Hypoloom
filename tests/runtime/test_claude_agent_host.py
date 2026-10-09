from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.claude_agent_host import (
    AgentDeadlineExpired,
    AgentHostError,
    AgentHostTerminalReason,
    CLAUDE_AGENT_HOST_VERSION,
    CLAUDE_AGENT_STRUCTURED_OUTPUT_FAILED_VALIDATION_CAP,
    CLAUDE_AGENT_TRANSPORT_RETRIES,
    CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
    ClaudeAgentHost,
    _output_schema,
    _prompt,
    _provider_response,
    process_group_exists,
    validate_claude_model_selector,
)
from hypoloom.agent_aci import (
    EXPERT_LOOP_ACI_SCHEMA_VERSION,
    AgentACIError,
    AgentCommand,
    AgentResponse,
    EmbeddedInput,
    required_output_schema,
)
from hypoloom.expert_loop import (
    AuxiliaryRequestKind,
    AuxiliaryTask,
    ResearchNodeSubkind,
    ResearchNodeTask,
)
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.execution_workspace import ExecutionWorkspace
from hypoloom.literature import (
    LITERATURE_STRATA,
    LiteratureFilters,
    LiteratureQuery,
    PreferredVenue,
    ScholarlySourceClass,
    admit_literature_query_plan,
)


_VALID_OUTPUT = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 1,
        "total_cost_usd": 0,
        "usage": {"input_tokens": 7, "output_tokens": 11},
        "modelUsage": {"stub-model": {}},
        "structured_output": {
            "typed_candidate": {
                "component_id": "primary.score",
                "proposer_role": "scientist",
                "method_family_id": "family-stub",
                "claim": "stub candidate",
                "assumptions": [],
            },
            "declared_effects": [
                {
                    "effect": "HYPOTHESIS_CANDIDATE_PROPOSED",
                }
            ],
        },
    },
    separators=(",", ":"),
)


def _host(tmp_path: Path, *, clock=time.monotonic) -> ClaudeAgentHost:
    stub = tmp_path / "claude-stub"
    stub.write_text(
        """#!/bin/sh
IFS= read -r PROMPT || true
case "${STUB_MODE:-valid}" in
  valid) printf '%s\\n' "$STUB_VALID_OUTPUT" ;;
  delayed) /bin/sleep 0.35; printf '%s\\n' "$STUB_VALID_OUTPUT" ;;
  malformed) printf '%s\\n' '{"structured_output":' ;;
  nonzero) printf '%s' 'stub provider failure' >&2; exit 23 ;;
  diagnostic)
    printf '%s' "$STUB_STDOUT_DIAGNOSTIC"
    printf '%s' "$STUB_DIAGNOSTIC" >&2
    exit 23
    ;;
  invalid_diagnostic) printf '\\377invalid provider stderr' >&2; exit 23 ;;
  hang) while :; do :; done ;;
  flood) while :; do printf '%01024d' 0; done ;;
  descendants) (while :; do :; done) & while :; do :; done ;;
  probe)
    SETTINGS_READ=false
    SETTINGS_WRITE=false
    FORBIDDEN_VISIBLE=false
    HOME_WRITE=false
    CONFIG_WRITE=false
    [ -r /config/settings.json ] && SETTINGS_READ=true
    (printf x >> /config/settings.json) 2>/dev/null && SETTINGS_WRITE=true
    [ -e "$HOST_FORBIDDEN" ] && FORBIDDEN_VISIBLE=true
    (printf x > "$HOME/probe") 2>/dev/null && HOME_WRITE=true
    (printf x > "$CLAUDE_CONFIG_DIR/probe") 2>/dev/null && CONFIG_WRITE=true
    CLAIM="${SETTINGS_READ}:${SETTINGS_WRITE}:${FORBIDDEN_VISIBLE}:${HOME_WRITE}:${CONFIG_WRITE}"
    printf '%s' '{"type":"result","subtype":"success","is_error":false,"num_turns":1,"total_cost_usd":0,"usage":{"input_tokens":7,"output_tokens":11},"modelUsage":{"stub-model":{}},"structured_output":{"typed_candidate":{"claim":"'
    printf '%s' "$CLAIM"
    printf '%s\\n' '","component_id":"primary.score","proposer_role":"scientist","method_family_id":"family-stub","assumptions":[]},"declared_effects":[{"effect":"HYPOTHESIS_CANDIDATE_PROPOSED"}]}}'
    ;;
  prompt)
    FOUND=false
    HIDDEN=false
    case "$PROMPT" in *"$EXPECTED_TEXT"*) FOUND=true ;; esac
    case "$PROMPT" in *"$FORBIDDEN_TEXT"*) HIDDEN=true ;; esac
    CLAIM="${FOUND}:${HIDDEN}"
    printf '%s' '{"type":"result","subtype":"success","is_error":false,"num_turns":1,"total_cost_usd":0,"usage":{"input_tokens":7,"output_tokens":11},"modelUsage":{"stub-model":{}},"structured_output":{"typed_candidate":{"claim":"'
    printf '%s' "$CLAIM"
    printf '%s\\n' '","component_id":"primary.score","proposer_role":"scientist","method_family_id":"family-stub","assumptions":[]},"declared_effects":[{"effect":"HYPOTHESIS_CANDIDATE_PROPOSED"}]}}'
    ;;
  workspace)
    (printf 'MUTATION' >> /target/model.py) 2>/dev/null || true
    printf '%s' "$STUB_WORKSPACE_SOURCE" > experiment.py
    printf '%s\n' "$STUB_VALID_OUTPUT"
    ;;
  *) exit 24 ;;
esac
""",
        encoding="utf-8",
    )
    stub.chmod(0o700)
    settings = tmp_path / "settings.json"
    settings.write_text("{}\n", encoding="utf-8")
    settings.chmod(0o600)
    return ClaudeAgentHost(
        claude_executable=stub,
        settings_path=settings,
        allowed_environment_names=(
            "STUB_MODE",
            "STUB_VALID_OUTPUT",
            "HOST_FORBIDDEN",
            "EXPECTED_TEXT",
            "FORBIDDEN_TEXT",
            "STUB_DIAGNOSTIC",
            "STUB_STDOUT_DIAGNOSTIC",
            "STUB_WORKSPACE_SOURCE",
        ),
        extra_runtime_paths=("/bin/sh", "/bin/sleep"),
        clock=clock,
    )


def _start(
    host: ClaudeAgentHost,
    *,
    mode: str = "valid",
    model: str = "stub-model",
    bind_process_group=lambda binding: None,
    extra_environment: dict[str, str] | None = None,
    request=None,
    tool_workspace=None,
):
    environment = {"STUB_MODE": mode, "STUB_VALID_OUTPUT": _VALID_OUTPUT}
    environment.update(extra_environment or {})
    return host.start(
        request or _claude_request(),
        model=model,
        provider_environment=environment,
        bind_process_group=bind_process_group,
        tool_workspace=tool_workspace,
    )


def _claude_request():
    request = default_request()
    return request.__class__.from_dict(
        {
            **request.to_dict(),
            "tool_permission_profile": CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
            "required_model_identity": "stub-model",
            "required_adapter_version": CLAUDE_AGENT_HOST_VERSION,
        }
    )


def _claude_draft_request():
    request = default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC)
    return request.__class__.from_dict(
        {
            **request.to_dict(),
            "tool_permission_profile": CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
            "required_model_identity": "stub-model",
            "required_adapter_version": CLAUDE_AGENT_HOST_VERSION,
        }
    )


def _claude_expert_design_request():
    request = default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT)
    config = EmbeddedInput.from_bytes(
        f"{request.component_id}--expert-config",
        "application/json",
        json.dumps(
            {
                "execution": {"runtime_prefix": None},
                "required_experiment_spec": {
                    "metric": {
                        "name": "score",
                        "direction": "MAXIMIZE",
                        "target": "1",
                    },
                    "evaluator_version": "component-result/7",
                    "artifact_requirements": ["result.json"],
                },
            },
            separators=(",", ":"),
        ).encode(),
    )
    return request.__class__.from_dict(
        {
            **request.to_dict(),
            "input_artifacts": [config.to_dict()],
            "immutable_context_refs": [config.ref],
            "tool_permission_profile": CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
            "required_model_identity": "stub-model",
            "required_adapter_version": CLAUDE_AGENT_HOST_VERSION,
        }
    )


def _claude_pincer_request(
    subkind: ResearchNodeSubkind,
    *,
    cycle: int = 1,
    parent_hypothesis_id: str | None = None,
):
    request = default_request(AgentCommand.HYPOTHESIS_PROPOSAL)
    assert isinstance(request.task_context, ResearchNodeTask)
    task = ResearchNodeTask(
        key=replace(request.task_context.key, subkind=subkind, cycle=cycle)
    )
    inputs = request.input_artifacts
    refs = request.immutable_context_refs
    if parent_hypothesis_id is not None:
        lineage = EmbeddedInput.from_bytes(
            "run-fake--primary.score--scientist--cycle-2--lineage-context",
            "application/json",
            json.dumps(
                {
                    "cycle": cycle,
                    "parent_hypothesis_id": parent_hypothesis_id,
                },
                separators=(",", ":"),
            ).encode(),
        )
        inputs = (*inputs, lineage)
        refs = (*refs, lineage.ref)
    request = replace(
        request,
        task_context=task,
        command_id=task.command_id,
        required_output_schema=required_output_schema(request.command, task),
        input_artifacts=inputs,
        immutable_context_refs=refs,
    )
    return request.__class__.from_dict(
        {
            **request.to_dict(),
            "tool_permission_profile": CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
            "required_model_identity": "stub-model",
            "required_adapter_version": CLAUDE_AGENT_HOST_VERSION,
        }
    )


def _claude_hypothesis_update_request(*, assessment: str = "SUPPORTED"):
    request = default_request(AgentCommand.HYPOTHESIS_UPDATE)
    hypothesis_id = "hypothesis-current"
    family = "family-current"
    hypothesis_fact_id = f"fact-{hypothesis_id}"
    assessment_fact_id = f"fact-assessment-{hypothesis_id}"
    component_config = EmbeddedInput.from_bytes(
        f"{request.run_id}--{request.component_id}--expert-config",
        "application/json",
        json.dumps(
            {
                "component": {
                    "id": request.component_id,
                    "dependencies": ["must.upstream"],
                },
                "goal": {"summary": "all required components must close"},
            },
            separators=(",", ":"),
        ).encode(),
    )
    hypothesis = EmbeddedInput.from_bytes(
        hypothesis_fact_id,
        "application/json",
        json.dumps(
            {
                "fact_id": hypothesis_fact_id,
                "fact_type": "HypothesisProposed",
                "payload": {
                    "fact_type": "HypothesisProposed",
                    "fact_id": hypothesis_fact_id,
                    "run_id": request.run_id,
                    "schema_version": 5,
                    "hypothesis_id": hypothesis_id,
                    "goal_id": "goal-fake",
                    "component_id": request.component_id,
                    "proposer_role": request.role,
                    "method_family_id": family,
                    "claim": "the local criterion is met",
                    "assumptions": [],
                    "parent_hypothesis_id": None,
                    "relation": None,
                },
            },
            separators=(",", ":"),
        ).encode(),
    )
    assessed = EmbeddedInput.from_bytes(
        assessment_fact_id,
        "application/json",
        json.dumps(
            {
                "fact_id": assessment_fact_id,
                "fact_type": "HypothesisAssessed",
                "payload": {
                    "fact_type": "HypothesisAssessed",
                    "fact_id": assessment_fact_id,
                    "run_id": request.run_id,
                    "schema_version": 5,
                    "assessment_id": f"assessment-{hypothesis_id}",
                    "hypothesis_id": hypothesis_id,
                    "component_id": request.component_id,
                    "method_family_id": family,
                    "evidence_ids": [f"evidence-{hypothesis_id}"],
                    "assessment": assessment,
                },
            },
            separators=(",", ":"),
        ).encode(),
    )
    inputs = (component_config, hypothesis, assessed)
    return request.__class__.from_dict(
        {
            **request.to_dict(),
            "method_family_id": family,
            "input_artifacts": [item.to_dict() for item in inputs],
            "immutable_context_refs": [item.ref for item in inputs],
            "tool_permission_profile": CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
            "required_model_identity": "stub-model",
            "required_adapter_version": CLAUDE_AGENT_HOST_VERSION,
        }
    )


def _claude_literature_query_plan_request(
    *, schema_version: int = EXPERT_LOOP_ACI_SCHEMA_VERSION,
) -> tuple[object, LiteratureQuery]:
    request = default_request(AgentCommand.LITERATURE_REVIEW)
    assert isinstance(request.task_context, ResearchNodeTask)
    task = ResearchNodeTask(
        key=replace(
            request.task_context.key,
            subkind=ResearchNodeSubkind.QUERY_PLAN,
        )
    )
    goal = EmbeddedInput.from_bytes(
        "goal-1",
        "application/json",
        b'{"goal":"Compare durable multimodal scientific mechanisms."}',
    )
    grounding = EmbeddedInput.from_bytes(
        f"{task.command_id}--grounding-result",
        "application/json",
        b'{"summary":"The target spans foundational and recent work."}',
    )
    expert_config = EmbeddedInput.from_bytes(
        "run-fake--primary.score--expert-config",
        "application/json",
        b'{"expert":{"id":"scientist","focus":"causal multimodal affect mechanisms"}}',
    )
    skill = EmbeddedInput.from_bytes(
        f"{task.command_id}--openalex-query-planning-skill",
        "text/markdown",
        (
            Path(__file__).resolve().parents[2]
            / "hypoloom"
            / "skills"
            / "openalex-query-planning"
            / "SKILL.md"
        ).read_bytes(),
    )
    template = LiteratureQuery(
        schema_version=1 if schema_version == 8 else 2,
        query="multimodal scientific mechanism",
        filters=LiteratureFilters(
            year_from=1990,
            year_to=2026,
            languages=("en",),
        ),
        max_items=6,
        allowed_source_classes=(
            ScholarlySourceClass.JOURNAL,
            ScholarlySourceClass.CONFERENCE,
        ),
        strata=LITERATURE_STRATA,
        latest_as_of="2026-08-29",
        immutable_context_refs=(goal.ref, expert_config.ref, grounding.ref, skill.ref),
        preferred_venues=(
            (PreferredVenue(
                name="Transactions on Affective Computing",
                source_class=ScholarlySourceClass.JOURNAL,
            ),)
            if schema_version != 8 else ()
        ),
    )
    query_template = EmbeddedInput.from_bytes(
        f"{task.command_id}--literature-query-template",
        "application/json",
        json.dumps(
            template.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
    )
    inputs = (goal, expert_config, grounding, skill, query_template)
    request = replace(
        request,
        schema_version=schema_version,
        task_context=task,
        command_id=task.command_id,
        input_artifacts=inputs,
        immutable_context_refs=tuple(item.ref for item in inputs),
        required_output_schema=required_output_schema(
            request.command, task, schema_version=schema_version,
        ),
    )
    return (
        request.__class__.from_dict(
            {
                **request.to_dict(),
                "tool_permission_profile": CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
                "required_model_identity": "stub-model",
                "required_adapter_version": CLAUDE_AGENT_HOST_VERSION,
            }
        ),
        template,
    )


def _claude_grounding_query_plan_request() -> tuple[object, LiteratureQuery]:
    request, expert_template = _claude_literature_query_plan_request()
    task = AuxiliaryTask(
        kind=AuxiliaryRequestKind.GROUNDING_QUERY_PLAN,
        run_id=request.run_id,
    )
    goal = EmbeddedInput.from_bytes(
        "goal-1",
        "application/json",
        json.dumps(
            {"objective": "通用多模态因果训练情绪识别"},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode(),
    )
    grounding_config = EmbeddedInput.from_bytes(
        f"{request.run_id}--grounding-config",
        "application/json",
        json.dumps(
            {
                "grounding_scope": "role-neutral",
                "components": [{"id": "primary.score"}],
                "protocol_catalog": {"primary": {"metric": "weighted_f1"}},
                "language_route": {
                    "route": "literature_review.query_plan",
                    "output_language": "zh-CN",
                },
            },
            separators=(",", ":"),
        ).encode(),
    )
    grounding_context = EmbeddedInput.from_bytes(
        f"{request.run_id}--grounding-context",
        "application/json",
        b'{"target":{"entries":[]},"read_roots":[]}',
    )
    skill = replace(
        next(
            item
            for item in request.input_artifacts
            if item.ref.endswith("--openalex-query-planning-skill")
        ),
        ref=f"{task.command_id}--openalex-query-planning-skill",
    )
    template = replace(
        expert_template,
        query="通用多模态因果训练情绪识别",
        immutable_context_refs=(
            goal.ref,
            grounding_config.ref,
            grounding_context.ref,
            skill.ref,
        ),
        preferred_venues=(),
    )
    query_template = EmbeddedInput.from_bytes(
        f"{task.command_id}--literature-query-template",
        "application/json",
        json.dumps(
            template.to_dict(), sort_keys=True, separators=(",", ":"),
        ).encode(),
    )
    inputs = (
        goal,
        grounding_config,
        grounding_context,
        skill,
        query_template,
    )
    return (
        replace(
            request,
            task_context=task,
            command_id=task.command_id,
            role="transport-scientist",
            input_artifacts=inputs,
            immutable_context_refs=tuple(item.ref for item in inputs),
            required_output_schema=required_output_schema(
                request.command, task, schema_version=request.schema_version,
            ),
        ),
        template,
    )


def _claude_literature_main_request():
    request = default_request(AgentCommand.LITERATURE_REVIEW)
    assert isinstance(request.task_context, ResearchNodeTask)
    query = EmbeddedInput.from_bytes(
        f"{request.task_context.command_id}--literature-query",
        "application/json",
        b'{"query":"validated query provenance"}',
    )
    packet = EmbeddedInput.from_bytes(
        f"{request.task_context.command_id}--literature-packet",
        "application/json",
        b'{"schema_version":1,"papers":[]}',
    )
    inputs = (query, packet)
    request = replace(
        request,
        input_artifacts=inputs,
        immutable_context_refs=tuple(item.ref for item in inputs),
    )
    return request.__class__.from_dict(
        {
            **request.to_dict(),
            "tool_permission_profile": CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
            "required_model_identity": "stub-model",
            "required_adapter_version": CLAUDE_AGENT_HOST_VERSION,
        }
    )


def _draft_provider_output() -> str:
    original = default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC)
    generated = FakeAgent().invoke(original)
    assert isinstance(generated, AgentResponse)
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 1,
            "total_cost_usd": 0,
            "usage": {"input_tokens": 7, "output_tokens": 11},
            "modelUsage": {"stub-model": {}},
            "structured_output": {
                "typed_candidate": generated.typed_candidate,
                "declared_effects": [
                    {"effect": item.effect} for item in generated.declared_effects
                ],
            },
        },
        separators=(",", ":"),
    )


def _draft_workspace(tmp_path: Path, attempt_id: str):
    state_root = tmp_path / "state"
    artifact_root = state_root / "artifacts"
    artifact_root.mkdir(parents=True)
    owner = ExecutionWorkspace(artifact_root, state_root=state_root)
    return owner.prepare_agent_workspace(attempt_id=attempt_id)


@pytest.mark.parametrize(
    "selector",
    ("stub-model", "mimo-v2.5-pro[1m]"),
)
def test_validate_claude_model_selector_returns_the_exact_selector(
    selector: str,
) -> None:
    assert validate_claude_model_selector(selector) == selector


@pytest.mark.parametrize(
    "selector",
    (
        None,
        "",
        " stub-model",
        "stub-model ",
        "stub model",
        "[1m]",
        "stub-model[1m][1m]",
        "default",
        "best",
        "sonnet",
        "opus",
        "haiku",
        "fable",
        "opusplan",
        "OPUS[1M]",
        "stub-model[1M]",
        "REPLACE_WITH_PINNED_MODEL",
        "replace_with_pinned_model[1m]",
        "REPLACE_WITH_PINNED_GROUNDING_MODEL",
    ),
)
def test_validate_claude_model_selector_rejects_aliases_and_malformed_selectors(
    selector: object,
) -> None:
    with pytest.raises(ValueError):
        validate_claude_model_selector(selector)


def test_experiment_output_schema_projects_the_typed_metric_contract() -> None:
    request = default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC)
    request = request.__class__.from_dict(
        {
            **request.to_dict(),
            "tool_permission_profile": CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
            "required_model_identity": "stub-model",
            "required_adapter_version": CLAUDE_AGENT_HOST_VERSION,
        }
    )
    schema = _output_schema(request)
    assert schema["required"] == [
        "typed_candidate",
        "proposed_workspace_artifacts",
        "declared_effects",
    ]
    assert "declared_effects" in schema["properties"]
    effect_item = schema["properties"]["declared_effects"]["items"]
    assert effect_item["required"] == ["effect"]
    assert set(effect_item["properties"]) == {"effect"}
    metric = schema["properties"]["typed_candidate"]["properties"]["metrics"]["items"]
    assert metric == {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string"},
            "direction": {"enum": ["MAXIMIZE", "MINIMIZE"]},
            "target": {"type": "string"},
        },
        "required": ["name", "direction", "target"],
    }
    candidate = schema["properties"]["typed_candidate"]
    assert candidate["properties"]["component_id"] == {
        "const": request.component_id
    }
    assert candidate["properties"]["owner_role"] == {"const": request.role}
    assert candidate["properties"]["method_family_id"] == {
        "const": request.method_family_id
    }
    assert candidate["properties"]["resource_preference"] == {
        "enum": ["CPU", "AUTO", "GPU_REQUIRED"]
    }
    assert candidate["properties"]["artifact_requirements"] == {
        "type": "array",
        "items": {"const": "result.json"},
    }
    workspace = schema["properties"]["proposed_workspace_artifacts"]
    assert workspace["items"]["properties"]["path"] == {
        "const": "experiment.py"
    }
    assert workspace["items"]["properties"]["content_utf8"] == {
        "type": "string",
        "minLength": 1,
    }
    assert "content_base64" not in workspace["items"]["properties"]
    instruction = json.loads(_prompt(request))["instruction"]
    assert "exactly one compact, directly runnable workspace artifact" in instruction
    assert "literal UTF-8 Python source in content_utf8" in instruction
    assert "HYPOLOOM_RESULT_PATH is the exact writable result file path" in instruction
    assert "not a directory" in instruction
    assert "never append result.json" in instruction
    assert "required_experiment_spec.result_contract" in instruction
    assert "only its required and optional top-level keys" in instruction
    assert "never inline artifact content" in instruction
    assert "validator_version is instruction, not a result.json field" in instruction
    assert "Identity is optional: omit it entirely" in instruction
    assert "64 lowercase hexadecimal characters" in instruction
    assert "placeholders such as pending, TBD, or unknown are invalid" in instruction
    assert "previous experiment source, an implementation patch" in instruction
    assert "a peer preflight reply, or Host validation/process diagnostics" in instruction
    assert "revise the Attempt-private implementation to fix the reported failures" in instruction
    assert "do not repeat known-failing source" in instruction
    assert "Host-injected resource-planning skill" in instruction
    assert "grants no device or process authority" in instruction
    assert "must not be rewritten" in instruction
    assert "implementation.patch is NOT applied to your own experiment's sandbox" in instruction
    assert "only accepted ancestor patches are applied, and only for downstream components" in instruction
    assert "experiment.py must be self-contained" in instruction
    assert "it must create them itself at runtime" in instruction
    assert "Outputs of other components are visible only when the Host declares them as required inputs" in instruction
    assert "never probe /input, /workspace, or the working directory for undeclared files" in instruction
    assert "must apply cleanly to the frozen target and produce syntactically valid Python" in instruction
    assert "The Host compiles the patched tree before any execution" in instruction
    assert "exact literals named in the goal contract to appear literally in the named files" in instruction
    assert "Do not use tools" in instruction
    assert "resource_preference" in candidate["required"]
    assert not {"gpu_uuid", "gpu_index", "device_nodes", "environment", "runtime"}.intersection(
        candidate["properties"]
    )


def test_expert_experiment_schema_freezes_host_contract_and_runtime_guarantee() -> None:
    request = _claude_expert_design_request()
    candidate = _output_schema(request)["properties"]["typed_candidate"]
    instruction = json.loads(_prompt(request, git_authoring=True))["instruction"]

    assert candidate["properties"]["metrics"] == {"const": [
        {"name": "score", "direction": "MAXIMIZE", "target": "1"},
    ]}
    assert candidate["properties"]["evaluator_version"] == {
        "const": "component-result/7",
    }
    assert candidate["properties"]["artifact_requirements"] == {
        "const": ["result.json"],
    }
    assert "execution.runtime_prefix" in instruction
    assert "only the Python standard library is guaranteed" in instruction
    assert "do not import numpy, torch" in instruction
    assert "implementation.patch is NOT applied to your own experiment's sandbox" in instruction
    assert "only accepted ancestor patches are applied, and only for downstream components" in instruction
    assert "experiment.py must be self-contained" in instruction
    assert "it must create them itself at runtime" in instruction
    assert "Outputs of other components are visible only when the Host declares them as required inputs" in instruction
    assert "never probe /input, /workspace, or the working directory for undeclared files" in instruction
    assert "must apply cleanly to the frozen target and produce syntactically valid Python" in instruction
    assert "The Host compiles the patched tree before any execution" in instruction
    assert "exact literals named in the goal contract to appear literally in the named files" in instruction


def test_expert_experiment_schema_fails_closed_without_component_config() -> None:
    request = default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT)

    with pytest.raises(AgentHostError, match="frozen component config"):
        _output_schema(request)


def test_aci9_literature_query_plan_schema_allows_only_query_and_venues() -> None:
    request, template = _claude_literature_query_plan_request()

    assert CLAUDE_AGENT_HOST_VERSION == "claude-code-bwrap/79"
    assert request.required_output_schema == "LiteratureQuery/v2"
    schema = _output_schema(request)
    assert schema["required"] == ["typed_candidate"]
    assert set(schema["properties"]) == {"typed_candidate"}
    candidate = schema["properties"]["typed_candidate"]
    assert candidate["additionalProperties"] is False
    assert candidate["required"] == ["query"]
    assert set(candidate["properties"]) == {"query", "preferred_venues"}
    assert candidate["properties"]["query"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 1_024,
    }
    venues = candidate["properties"]["preferred_venues"]
    assert "maxItems" not in venues
    assert "uniqueItems" not in venues
    assert venues["items"]["properties"]["source_class"] == {
        "enum": ["JOURNAL", "CONFERENCE"],
    }
    assert template.preferred_venues


@pytest.mark.parametrize("mismatch", [False, True])
def test_native_provider_delivery_uses_the_validated_text_and_rejects_disagreement(mismatch):
    request, _ = _claude_grounding_query_plan_request()
    text = json.dumps({"typed_candidate": {"query": "current experiment evidence"}})
    outer = json.dumps({"type": "result", "subtype": "success", "is_error": False,
        "num_turns": 1, "total_cost_usd": 0,
        "usage": {"input_tokens": 7, "output_tokens": 11}, "modelUsage": {"stub-model": {}},
        "result": text.replace("current", "different") if mismatch else text}).encode()
    response, failure, diagnostics = _provider_response(request, "stub-model", outer,
        final_text_output=True, validated_text=text)
    if mismatch:
        assert response is None and failure == "invalid_structured_output"
        assert "differs from validated" in diagnostics["provider_diagnostic"]
    else:
        assert response is not None and failure is None


def test_aci10_grounding_query_plan_is_role_neutral_and_materializes_language() -> None:
    request, template = _claude_grounding_query_plan_request()

    assert request.schema_version == EXPERT_LOOP_ACI_SCHEMA_VERSION == 13
    assert request.required_output_schema == "LiteratureQuery/v2"
    assert isinstance(request.task_context, AuxiliaryTask)
    assert request.task_context.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
    assert request.task_context.component_id is None
    assert request.task_context.expert_slot is None
    assert request.task_context.cycle is None
    assert request.role == "transport-scientist"

    provider_candidate = _output_schema(request)["properties"]["typed_candidate"]
    assert provider_candidate["additionalProperties"] is False
    assert provider_candidate["required"] == ["query"]
    assert set(provider_candidate["properties"]) == {
        "query",
        "preferred_venues",
    }

    query = '"emotion recognition"'
    outer = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 1,
            "total_cost_usd": 0,
            "usage": {"input_tokens": 7, "output_tokens": 11},
            "modelUsage": {"stub-model": {}},
            "structured_output": {
                "typed_candidate": {"query": query},
            },
        },
        separators=(",", ":"),
    ).encode()

    response, failure, _diagnostics = _provider_response(
        request, "stub-model", outer,
    )

    assert failure is None
    assert response is not None
    assert [item.to_dict() for item in response.declared_effects] == [
        {
            "effect": request.effect_requirements[0],
            "command_id": request.command_id,
            "attempt_id": request.attempt_id,
        }
    ]
    candidate = LiteratureQuery.from_dict(response.typed_candidate)
    assert admit_literature_query_plan(candidate, template=template) is candidate
    assert candidate.query == query
    assert candidate.filters == template.filters
    assert candidate.filters.languages == ("en",)
    assert candidate.immutable_context_refs == template.immutable_context_refs
    assert candidate.preferred_venues == ()

    prompt = json.loads(_prompt(request))
    instruction = prompt["instruction"]
    assert "Run-scoped role-neutral Grounding QUERY_PLAN" in instruction
    assert "role-neutral scientific goal" in instruction
    assert "role-neutral task/repository/data context" in instruction
    assert "Do not adopt the transport role as an expert persona" in instruction
    assert request.role not in instruction
    assert "executing role" not in instruction
    assert "scientific background encoded in the focus field" not in instruction
    assert "expert-config" not in instruction
    assert not any(
        item["ref"].endswith("--expert-config")
        for item in prompt["rendered_inputs"]
    )
    assert "query field is a retrieval expression" in instruction
    assert "exempt from language_route.output_language" in instruction
    assert "filters.languages=['en']" in instruction
    grounding_config = next(
        item
        for item in prompt["rendered_inputs"]
        if item["ref"].endswith("--grounding-config")
    )
    assert json.loads(grounding_config["content_utf8"])["language_route"] == {
        "route": "literature_review.query_plan",
        "output_language": "zh-CN",
    }


def test_query_plan_provider_delta_is_materialized_from_the_host_template() -> None:
    request, template = _claude_literature_query_plan_request()
    query = (
        '"multimodal emotion recognition" OR '
        '"emotion recognition in conversation"'
    )
    venues = [
        {
            "name": "IEEE Transactions on Affective Computing",
            "source_class": "JOURNAL",
        }
    ]
    outer = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 1,
            "total_cost_usd": 0,
            "usage": {"input_tokens": 7, "output_tokens": 11},
            "modelUsage": {"stub-model": {}},
            "structured_output": {
                "typed_candidate": {
                    "query": query,
                    "preferred_venues": venues,
                },
            },
        },
        separators=(",", ":"),
    ).encode()

    response, failure, _ = _provider_response(request, "stub-model", outer)

    assert failure is None
    assert response is not None
    candidate = LiteratureQuery.from_dict(response.typed_candidate)
    assert admit_literature_query_plan(candidate, template=template) is candidate
    assert candidate.query == query
    assert [item.to_dict() for item in candidate.preferred_venues] == venues
    for field in (
        "schema_version",
        "filters",
        "max_items",
        "allowed_source_classes",
        "strata",
        "latest_as_of",
        "immutable_context_refs",
    ):
        assert getattr(candidate, field) == getattr(template, field)


def test_query_plan_provider_delta_allows_omitted_soft_venues() -> None:
    request, _template = _claude_literature_query_plan_request()
    outer = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 1,
            "total_cost_usd": 0,
            "usage": {"input_tokens": 7, "output_tokens": 11},
            "modelUsage": {"stub-model": {}},
            "structured_output": {
                "typed_candidate": {"query": "multimodal emotion recognition"},
            },
        },
        separators=(",", ":"),
    ).encode()

    response, failure, _diagnostics = _provider_response(
        request,
        "stub-model",
        outer,
    )

    assert failure is None
    assert response is not None
    candidate = LiteratureQuery.from_dict(response.typed_candidate)
    assert candidate.preferred_venues == ()


def test_query_plan_rejects_provider_declared_effects() -> None:
    request, _template = _claude_literature_query_plan_request()
    outer = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 1,
            "total_cost_usd": 0,
            "usage": {"input_tokens": 7, "output_tokens": 11},
            "modelUsage": {"stub-model": {}},
            "structured_output": {
                "typed_candidate": {"query": "multimodal emotion recognition"},
                "declared_effects": [
                    {
                        "effect": request.effect_requirements[0],
                        "command_id": request.command_id,
                        "attempt_id": request.attempt_id,
                    }
                ],
            },
        },
        separators=(",", ":"),
    ).encode()

    response, failure, diagnostics = _provider_response(
        request,
        "stub-model",
        outer,
    )

    assert response is None
    assert failure == "invalid_structured_output"
    assert "validation_error" not in diagnostics


def test_non_query_output_without_declared_effects_fails_closed() -> None:
    request = _claude_request()
    outer = json.loads(_VALID_OUTPUT)
    del outer["structured_output"]["declared_effects"]

    response, failure, diagnostics = _provider_response(
        request,
        "stub-model",
        json.dumps(outer, separators=(",", ":")).encode(),
    )

    assert response is None
    assert failure == "invalid_structured_output"
    assert "validation_error" not in diagnostics


def test_host_stamps_declared_effect_identity_from_the_request() -> None:
    request = _claude_request()

    response, failure, _diagnostics = _provider_response(
        request,
        "stub-model",
        _VALID_OUTPUT.encode(),
    )

    assert failure is None
    assert response is not None
    assert [item.to_dict() for item in response.declared_effects] == [
        {
            "effect": "HYPOTHESIS_CANDIDATE_PROPOSED",
            "command_id": request.command_id,
            "attempt_id": request.attempt_id,
        }
    ]


def test_provider_declared_effect_with_echoed_identity_fails_closed() -> None:
    request = _claude_request()
    outer = json.loads(_VALID_OUTPUT)
    outer["structured_output"]["declared_effects"][0]["command_id"] = request.command_id

    response, failure, diagnostics = _provider_response(
        request,
        "stub-model",
        json.dumps(outer, separators=(",", ":")).encode(),
    )

    assert response is None
    assert failure == "invalid_effect"
    assert diagnostics["validation_error"]["code"] == "invalid_effect"


@pytest.mark.parametrize(
    "candidate",
    (
        {},
        {
            "query": "multimodal emotion recognition",
            "preferred_venues": [],
            "max_items": 99,
        },
    ),
)
def test_query_plan_provider_delta_rejects_missing_or_frozen_fields(
    candidate: dict[str, object],
) -> None:
    request, _template = _claude_literature_query_plan_request()
    outer = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 1,
            "total_cost_usd": 0,
            "usage": {"input_tokens": 7, "output_tokens": 11},
            "modelUsage": {"stub-model": {}},
            "structured_output": {
                "typed_candidate": candidate,
            },
        },
        separators=(",", ":"),
    ).encode()

    response, failure, diagnostics = _provider_response(
        request,
        "stub-model",
        outer,
    )

    assert response is None
    assert failure == "invalid_candidate"
    assert diagnostics["validation_error"]["code"] == "invalid_candidate"


@pytest.mark.parametrize(
    ("budget_changes", "total_cost", "expected_failure"),
    (
        ({"max_output_bytes": 128}, 0, "output_too_large"),
        ({"max_cost": "0.1"}, 0.2, "cost_budget_exceeded"),
    ),
)
def test_query_plan_candidate_error_does_not_mask_invocation_limits(
    budget_changes: dict[str, object],
    total_cost: float,
    expected_failure: str,
) -> None:
    original, _template = _claude_literature_query_plan_request()
    request = replace(
        original,
        budget=replace(original.budget, **budget_changes),
    )
    outer = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 1,
            "total_cost_usd": total_cost,
            "usage": {"input_tokens": 7, "output_tokens": 11},
            "modelUsage": {"stub-model": {}},
            "structured_output": {
                "typed_candidate": {
                    "query": "multimodal emotion recognition",
                    "preferred_venues": [
                        {
                            "name": f"Venue {index}",
                            "source_class": "CONFERENCE",
                        }
                        for index in range(7)
                    ],
                },
            },
        },
        separators=(",", ":"),
    ).encode()

    response, failure, diagnostics = _provider_response(
        request,
        "stub-model",
        outer,
    )

    assert response is None
    assert failure == expected_failure
    assert diagnostics["validation_error"]["code"] == expected_failure


@pytest.mark.parametrize(
    "venues",
    (
        [
            {"name": f"Venue {index}", "source_class": "CONFERENCE"}
            for index in range(7)
        ],
        [{"name": "Unknown source class", "source_class": "PREPRINT"}],
    ),
)
def test_query_plan_provider_delta_reuses_literature_value_validation(
    venues: list[dict[str, object]],
) -> None:
    request, _template = _claude_literature_query_plan_request()
    outer = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 1,
            "total_cost_usd": 0,
            "usage": {"input_tokens": 7, "output_tokens": 11},
            "modelUsage": {"stub-model": {}},
            "structured_output": {
                "typed_candidate": {
                    "query": "multimodal emotion recognition",
                    "preferred_venues": venues,
                },
            },
        },
        separators=(",", ":"),
    ).encode()

    response, failure, diagnostics = _provider_response(
        request,
        "stub-model",
        outer,
    )

    assert response is None
    assert failure == "invalid_candidate"
    assert diagnostics["validation_error"]["code"] == "invalid_candidate"


def test_aci8_literature_query_plan_schema_remains_v1_query_only() -> None:
    request, template = _claude_literature_query_plan_request(schema_version=8)

    assert request.required_output_schema == "LiteratureQuery/v1"
    candidate = _output_schema(request)["properties"]["typed_candidate"]
    assert candidate["required"] == ["query"]
    assert set(candidate["properties"]) == {"query"}
    assert template.schema_version == 1
    instruction = json.loads(_prompt(request))["instruction"]
    assert "mutable query-plan proposal" in instruction
    assert "only query" in instruction


def test_aci9_literature_query_plan_rejects_a_v1_template() -> None:
    request, _template = _claude_literature_query_plan_request()
    legacy, _legacy_template = _claude_literature_query_plan_request(
        schema_version=8,
    )
    legacy_input = next(item for item in legacy.input_artifacts
        if item.ref.endswith("--literature-query-template"))
    inputs = tuple(
        replace(item, content_base64=legacy_input.content_base64)
        if item.ref.endswith("--literature-query-template") else item
        for item in request.input_artifacts
    )
    request = replace(request, input_artifacts=inputs)

    with pytest.raises(AgentHostError, match="requires LiteratureQuery/v2"):
        _output_schema(request)


def test_literature_query_plan_prompt_injects_only_the_packaged_instruction() -> None:
    request, _template = _claude_literature_query_plan_request()

    prompt = json.loads(_prompt(request))
    instruction = prompt["instruction"]
    skill_inputs = [
        item
        for item in prompt["rendered_inputs"]
        if item["ref"].endswith("--openalex-query-planning-skill")
    ]
    assert len(skill_inputs) == 1
    assert skill_inputs[0]["media_type"] == "text/markdown"
    assert "# OpenAlex query planning" in skill_inputs[0]["content_utf8"]
    assert "not an ambient Claude Skill or capability" in instruction
    assert "language_route" in instruction
    assert "exact output_language" in instruction
    assert "code, paths, or exact quotations" in instruction
    assert "query field is a retrieval expression" in instruction
    assert "exempt from language_route.output_language" in instruction
    assert "filters.languages=['en']" in instruction
    assert "scholarly terminology" in instruction
    for context in (
        "frozen scientific goal",
        "grounding result",
        "executing role",
        "component",
    ):
        assert context in instruction
    assert "exactly one mutable query-plan proposal" in instruction
    assert "query and optional preferred_venues" in instruction
    assert "preferred_venues may be omitted or empty" in instruction
    assert "do not repeat or change frozen template fields" in instruction
    assert "scientific background encoded in the focus field" in instruction
    assert "expert-config" in instruction
    assert "optional discovery suggestions" in instruction
    assert "not hard filters or ranking overrides" in instruction
    assert "other venues must remain eligible" in instruction
    assert "Host materializes LiteratureQuery/v2" in instruction
    assert "uppercase AND, OR, or NOT" in instruction
    assert "double quotes" in instruction
    assert "wildcards" in instruction and "fuzzy-search suffixes" in instruction
    assert "filesystem or URL paths" in instruction and "Markdown" in instruction
    assert "Do not force a recent dataset" in instruction


def test_literature_query_plan_fails_closed_without_injected_skill() -> None:
    request, _template = _claude_literature_query_plan_request()
    request = replace(
        request,
        input_artifacts=tuple(
            item
            for item in request.input_artifacts
            if not item.ref.endswith("--openalex-query-planning-skill")
        ),
    )

    with pytest.raises(AgentHostError, match="injected planning skill"):
        _output_schema(request)


def test_literature_main_keeps_packet_only_review_instructions() -> None:
    request = _claude_literature_main_request()

    prompt = json.loads(_prompt(request))
    candidate = _output_schema(request)["properties"]["typed_candidate"]
    instruction = prompt["instruction"]
    assert request.required_output_schema == "ExpertLoopCandidate/v1"
    assert candidate["properties"]["task_kind"] == {
        "const": "LITERATURE_REVIEW"
    }
    packet_ref = next(item.ref for item in request.input_artifacts
        if item.ref.endswith("--literature-packet"))
    assert candidate["properties"]["supporting_refs"] == {
        "type": "array", "items": {"enum": [packet_ref]},
    }
    assert "LITERATURE_REVIEW MAIN" in instruction
    assert "Read only the Host-supplied validated LiteraturePacket" in instruction
    assert "--literature-packet" in instruction
    assert "supporting_refs only" in instruction
    assert "do not plan, generate, or rewrite a literature query" in instruction
    assert not any(
        item["ref"].endswith("--openalex-query-planning-skill")
        for item in prompt["rendered_inputs"]
    )


def test_literature_query_plan_remains_tool_less_and_has_no_native_skill_path(
    tmp_path: Path,
) -> None:
    request, _template = _claude_literature_query_plan_request()

    argv = _host(tmp_path)._sandbox_argv(request, "stub-model")
    provider = argv[argv.index("--") + 1 :]
    assert "--bare" in provider
    assert "--safe-mode" in provider
    assert provider[provider.index("--tools") + 1] == ""
    assert "--disable-slash-commands" in provider
    assert "--strict-mcp-config" in provider
    assert provider[provider.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert "--allowedTools" not in provider
    assert "--add-dir" not in provider
    assert "--plugin-dir" not in provider
    assert argv[argv.index("--chdir") + 1] == "/home/agent"


def test_tool_workspace_schema_and_prompt_use_host_captured_source() -> None:
    request = _claude_draft_request()

    schema = _output_schema(request, tool_workspace=True)
    instruction = json.loads(_prompt(request, tool_workspace=True))["instruction"]

    assert "proposed_workspace_artifacts" not in schema["properties"]
    assert "proposed_workspace_artifacts" not in schema["required"]
    assert "/workspace/experiment.py" in instruction
    assert "frozen target at /target" in instruction
    assert "no shell, test, network, Agent, MCP" in instruction
    assert "Do not use tools" not in instruction


def test_draft_file_tools_capture_one_source_and_clean_workspace(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path)
    request = _claude_draft_request()
    workspace = _draft_workspace(tmp_path, request.attempt_id)
    target_root = workspace.attempt_root / "target"
    target_root.mkdir()
    (target_root / "model.py").write_text("VALUE = 1\n", encoding="utf-8")
    workspace = replace(workspace, target_root=target_root)
    source = "# host-captured\nprint('ok')\n"

    handle = _start(
        host,
        mode="workspace",
        request=request,
        tool_workspace=workspace,
        extra_environment={
            "STUB_VALID_OUTPUT": _draft_provider_output(),
            "STUB_WORKSPACE_SOURCE": source,
        },
    )
    argv = handle.launch_argv

    assert argv[argv.index("--tools") + 1] == "Read,Glob,Grep,Edit,Write"
    assert ("--bind", str(workspace.writable_root), "/workspace") in tuple(
        zip(argv, argv[1:], argv[2:])
    )
    assert ("--ro-bind", str(target_root), "/target") in tuple(
        zip(argv, argv[1:], argv[2:])
    )
    assert argv[argv.index("--add-dir") + 1] == "/target"
    assert "--safe-mode" in argv
    assert "--bare" not in argv
    assert "Read(//config/**)" in argv
    assert "Read(//proc/**)" in argv
    assert "Edit(//target/**)" in argv
    assert "Edit(//workspace/experiment.py)" in argv
    assert "Write(//workspace/experiment.py)" in argv
    assert "Write(//workspace/**)" not in argv
    assert "Bash" not in argv[argv.index("--tools") + 1]
    assert argv[argv.index("--chdir") + 1] == "/workspace"

    observation = host.wait(handle)
    assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED
    assert observation.response is not None
    assert observation.response.proposed_workspace_artifacts[0].content_bytes() == source.encode()
    assert observation.diagnostics["workspace_source_bytes"] == len(source.encode())
    assert not workspace.attempt_root.exists()


def test_draft_start_requires_exact_workspace_binding(tmp_path: Path) -> None:
    host = _host(tmp_path)
    request = _claude_draft_request()

    with pytest.raises(AgentHostError, match="require one prepared tool workspace"):
        _start(host, request=request)

    workspace = _draft_workspace(tmp_path, request.attempt_id)
    with pytest.raises(AgentHostError, match="only experiment DRAFT"):
        _start(host, request=_claude_request(), tool_workspace=workspace)
    workspace.cleanup()


def test_invalid_draft_workspace_fails_before_transport_fd_allocation(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path)
    request = _claude_draft_request()
    workspace = _draft_workspace(tmp_path, request.attempt_id)
    workspace.writable_root.rmdir()
    before = len(os.listdir("/proc/self/fd"))

    with pytest.raises(AgentHostError, match="writable root"):
        _start(host, request=request, tool_workspace=workspace)

    assert len(os.listdir("/proc/self/fd")) == before
    workspace.cleanup()


def test_tool_workspace_cleanup_failure_returns_terminal_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _host(tmp_path)
    request = _claude_draft_request()
    workspace = _draft_workspace(tmp_path, request.attempt_id)
    original_cleanup = type(workspace).cleanup

    def fail_cleanup(self) -> None:
        raise OSError("synthetic cleanup failure")

    monkeypatch.setattr(type(workspace), "cleanup", fail_cleanup)
    handle = _start(
        host,
        mode="workspace",
        request=request,
        tool_workspace=workspace,
        extra_environment={
            "STUB_VALID_OUTPUT": _draft_provider_output(),
            "STUB_WORKSPACE_SOURCE": "print('candidate')\n",
        },
    )

    observation = host.wait(handle)

    assert observation.terminal_reason is AgentHostTerminalReason.FAILED
    assert observation.failure_code == "resource_cleanup_failed"
    assert observation.response is None
    assert "synthetic cleanup failure" in observation.diagnostics[
        "resource_cleanup_error"
    ]
    assert handle._closed
    assert host.poll(handle) is observation
    assert not host.cancel(request.attempt_id)
    assert workspace.attempt_root.exists()
    original_cleanup(workspace)


def test_provider_spawn_failure_is_typed_and_cleans_tool_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _host(tmp_path)
    request = _claude_draft_request()
    workspace = _draft_workspace(tmp_path, request.attempt_id)

    def fail_spawn(*args, **kwargs):
        raise OSError("synthetic provider spawn failure")

    monkeypatch.setattr(
        "hypoloom.claude_agent_host.subprocess.Popen", fail_spawn
    )

    with pytest.raises(AgentHostError, match="provider launch failed"):
        _start(host, request=request, tool_workspace=workspace)
    assert not workspace.attempt_root.exists()
    assert not host.cancel(request.attempt_id)


def test_proposal_schema_exposes_only_an_optional_typed_expert_signal() -> None:
    request = _claude_request()
    schema = _output_schema(request)
    assert "expert_signal" not in schema["required"]
    signal = schema["properties"]["expert_signal"]
    candidate = schema["properties"]["typed_candidate"]
    assert candidate["properties"]["component_id"] == {
        "const": request.component_id
    }
    assert candidate["properties"]["proposer_role"] == {"const": request.role}
    assert candidate["properties"]["method_family_id"] == {
        "type": "string",
        "minLength": 1,
    }
    assert "owner_role" not in candidate["properties"]
    assert signal["type"] == "object"
    assert signal["required"] == [
        "schema_version",
        "kind",
        "target_role_hint",
        "question",
        "context_refs",
    ]
    assert signal["properties"]["context_refs"]["items"]["enum"] == list(
        request.immutable_context_refs
    )
    instruction = json.loads(_prompt(request))["instruction"]
    assert "Omit expert_signal for self-contained" in instruction
    assert "never turn a statement that no peer is needed into a signal" in instruction
    assert "contains at least one exact supplied immutable context ref" in instruction
    assert "method_family_id" in instruction


def test_proposal_schema_and_prompt_keep_hypothesis_lineage_paired() -> None:
    request = _claude_request()
    candidate = _output_schema(request)["properties"]["typed_candidate"]

    assert {"parent_hypothesis_id", "relation"} <= set(candidate["required"])
    assert candidate["oneOf"] == [
        {
            "properties": {
                "parent_hypothesis_id": {"type": "null"},
                "relation": {"type": "null"},
            }
        },
        {
            "properties": {
                "parent_hypothesis_id": {"type": "string", "minLength": 1},
                "relation": {"type": "string", "minLength": 1},
            }
        },
    ]
    instruction = json.loads(_prompt(request))["instruction"]
    assert "set both to null for a root proposal" in instruction
    assert "same component" in instruction
    assert "no existing hypothesis for the same component, both fields must be null" in instruction
    assert "different component is not a parent" in instruction
    assert "cross-component lineage only through method_family_id" in instruction
    assert "candidate-validation-error" in instruction
    assert "do not repeat the rejected field combination" in instruction
    assert "rejected_structured_output or rejected_candidate" in instruction
    assert "prior non-authoritative failed output" in instruction
    assert "according to its exact diagnostic instead of repeating it" in instruction
    assert "unresolved_validation_error" in instruction
    assert "intervening provider schema failure" in instruction
    assert "operational context only" in instruction
    assert "never cite them as scientific evidence" in instruction


def test_supported_hypothesis_update_is_local_and_must_terminate() -> None:
    request = _claude_hypothesis_update_request()
    candidate = _output_schema(request)["properties"]["typed_candidate"]
    instruction = json.loads(_prompt(request))["instruction"]

    assert candidate["properties"]["update_disposition"] == {
        "const": "TERMINATE_LINEAGE",
        "description": (
            "Controls only the current local lineage. TERMINATE_LINEAGE closes "
            "this component's research loop for the rest of the run: the Host "
            "never opens another cycle for it, with the sole exception of a "
            "Debate/Leader-designated restart. It does not itself make a "
            "downstream dependent eligible. Dependency readiness is separate "
            "and an ordinary dependency also requires a current SUPPORTED "
            "assessment. REVISE_NEXT_CYCLE requests another local scientific "
            "cycle but does not bypass Host lifecycle or budget admission. "
            "To switch mechanisms next cycle, choose REVISE_NEXT_CYCLE and "
            "propose a new chain with an appropriate relation; when the batch "
            "selection's selected_tip is empty, ACI >= 17 also allows a "
            "parent=None new batch. Registered sibling chains that have not "
            "yet preregistered an experiment are silently abandoned when the "
            "component closes; confirm that is intentional before choosing "
            "TERMINATE_LINEAGE."
        ),
    }
    assert "current component's own current-tip assessment" in instruction
    assert "component.dependencies are upstream prerequisites" in instruction
    assert "downstream dependents" in instruction
    assert "global goal completion" in instruction
    assert "SUPPORTED" in instruction
    assert "must choose TERMINATE_LINEAGE" in instruction
    assert "the Host never schedules another cycle for it" in instruction
    assert "Debate/Leader-designated restart" in instruction
    assert "To switch mechanisms next cycle, choose REVISE_NEXT_CYCLE" in instruction
    assert "silently abandoned" in instruction
    assert "does not claim that downstream work or the global goal is complete" in instruction
    assert "does not by itself release or make any downstream dependent eligible" in instruction
    assert "ordinary scientific dependency usually requires" in instruction
    assert "Policy evaluates dependency readiness separately" in instruction
    assert "may release its dependents" not in instruction


@pytest.mark.parametrize(
    ("assessment", "assessment_semantics"),
    (
        (
            "INCONCLUSIVE",
            "establishes neither scientific support nor scientific refutation",
        ),
        (
            "REFUTED",
            "records the current tip's scientific refutation",
        ),
    ),
)
def test_non_supported_hypothesis_update_keeps_honest_local_dispositions(
    assessment: str,
    assessment_semantics: str,
) -> None:
    request = _claude_hypothesis_update_request(assessment=assessment)
    candidate = _output_schema(request)["properties"]["typed_candidate"]
    instruction = json.loads(_prompt(request))["instruction"]

    assert candidate["properties"]["update_disposition"] == {
        "enum": ["REVISE_NEXT_CYCLE", "TERMINATE_LINEAGE"],
        "description": (
            "Controls only the current local lineage. TERMINATE_LINEAGE closes "
            "this component's research loop for the rest of the run: the Host "
            "never opens another cycle for it, with the sole exception of a "
            "Debate/Leader-designated restart. It does not itself make a "
            "downstream dependent eligible. Dependency readiness is separate "
            "and an ordinary dependency also requires a current SUPPORTED "
            "assessment. REVISE_NEXT_CYCLE requests another local scientific "
            "cycle but does not bypass Host lifecycle or budget admission. "
            "To switch mechanisms next cycle, choose REVISE_NEXT_CYCLE and "
            "propose a new chain with an appropriate relation; when the batch "
            "selection's selected_tip is empty, ACI >= 17 also allows a "
            "parent=None new batch. Registered sibling chains that have not "
            "yet preregistered an experiment are silently abandoned when the "
            "component closes; confirm that is intentional before choosing "
            "TERMINATE_LINEAGE."
        ),
    }
    assert f"current-tip HypothesisAssessed is {assessment}" in instruction
    assert assessment_semantics in instruction
    assert "Neither disposition changes that assessment" in instruction
    assert "does not by itself release or make any downstream dependent eligible" in instruction
    assert "REVISE_NEXT_CYCLE requests another local scientific cycle" in instruction
    assert "the Host never schedules another cycle for it" in instruction
    assert "To switch mechanisms next cycle, choose REVISE_NEXT_CYCLE" in instruction
    assert "silently abandoned" in instruction
    assert "does not allocate scientific budget" in instruction
    assert "Operational failures and diagnostics are not scientific evidence" in instruction
    assert "do not convert them into SUPPORTED or REFUTED" in instruction
    assert "may release its dependents" not in instruction


def test_expert_loop_prompt_makes_supporting_refs_rejection_actionable() -> None:
    request = _claude_hypothesis_update_request()
    instruction = json.loads(_prompt(request))["instruction"]

    assert "cite only exact supplied immutable context refs in supporting_refs" in instruction
    assert "the structured output schema rejects every other string" in instruction
    assert (
        "never substitute a fact ID, attempt ID, or diagnostic ref you construct yourself"
        in instruction
    )
    assert "must be equal to one of the allowed values" in instruction
    assert "replace it with the exact supplied ref carrying that content" in instruction


@pytest.mark.parametrize(
    ("subkind", "present", "hidden"),
    (
        (
            ResearchNodeSubkind.FORWARD,
            "Reason forward only",
            "Do not infer or fabricate the BACKWARD argument",
        ),
        (
            ResearchNodeSubkind.BACKWARD,
            "Reason backward only",
            "Do not infer, request, or fabricate the FORWARD argument",
        ),
    ),
)
def test_pincer_wing_schema_and_prompt_keep_arguments_directionally_isolated(
    subkind: ResearchNodeSubkind,
    present: str,
    hidden: str,
) -> None:
    request = _claude_pincer_request(subkind)
    candidate = _output_schema(request)["properties"]["typed_candidate"]
    instruction = json.loads(_prompt(request))["instruction"]

    assert request.required_output_schema == "ExpertLoopCandidate/v1"
    assert candidate["properties"]["task_kind"] == {
        "const": "HYPOTHESIS_PROPOSAL"
    }
    assert set(candidate["required"]) == {
        "task_kind",
        "summary",
        "supporting_refs",
    }
    assert candidate["properties"]["supporting_refs"] == {
        "type": "array",
        "items": {"enum": list(request.immutable_context_refs)},
    }
    assert present in instruction
    assert hidden in instruction
    assert "Do not expose private chain-of-thought" in instruction
    assert "Use the full CC Read/Write/Edit/Glob/Grep/Bash" in instruction
    assert "Do not use tools" not in instruction


def test_expert_loop_schema_requires_empty_supporting_refs_without_context() -> None:
    request = replace(
        _claude_pincer_request(ResearchNodeSubkind.FORWARD),
        immutable_context_refs=(),
    )

    candidate = _output_schema(request)["properties"]["typed_candidate"]

    assert candidate["properties"]["supporting_refs"] == {"const": []}


def test_pincer_synthesis_schema_and_prompt_are_a_cycle_one_root_hypothesis() -> None:
    request = _claude_pincer_request(ResearchNodeSubkind.SYNTHESIS)
    candidate = _output_schema(request)["properties"]["typed_candidate"]
    instruction = json.loads(_prompt(request))["instruction"]

    assert request.required_output_schema == "HypothesisCandidate/v2"
    assert candidate["properties"]["component_id"] == {
        "const": request.component_id
    }
    assert candidate["properties"]["proposer_role"] == {"const": request.role}
    assert candidate["properties"]["method_family_id"] == {
        "type": "string",
        "minLength": 1,
    }
    assert candidate["properties"]["parent_hypothesis_id"] == {"const": None}
    assert candidate["properties"]["relation"] == {"const": None}
    assert "oneOf" not in candidate
    assert "exact validated FORWARD and BACKWARD arguments" in instruction
    assert "create one non-empty method_family_id" in instruction
    assert "set both parent_hypothesis_id and relation to null" in instruction
    assert "Do not invent a predecessor" in instruction


def test_pincer_synthesis_freezes_a_required_input_method_family() -> None:
    request = _claude_pincer_request(ResearchNodeSubkind.SYNTHESIS)
    config = EmbeddedInput.from_bytes(
        "run-fake--primary.score--expert-config",
        "application/json",
        b'{"required_method_family_id":"selected-family-v2"}',
    )
    request = replace(
        request,
        input_artifacts=(*request.input_artifacts, config),
        immutable_context_refs=(*request.immutable_context_refs, config.ref),
    )

    candidate = _output_schema(request)["properties"]["typed_candidate"]
    instruction = json.loads(_prompt(request))["instruction"]

    assert candidate["properties"]["method_family_id"] == {
        "const": "selected-family-v2"
    }
    assert "copy method_family_id exactly as 'selected-family-v2'" in instruction


def test_pincer_successor_schema_and_prompt_bind_the_exact_parent_hypothesis() -> None:
    request = _claude_pincer_request(
        ResearchNodeSubkind.SYNTHESIS,
        cycle=2,
        parent_hypothesis_id="hypothesis-cycle-1",
    )
    candidate = _output_schema(request)["properties"]["typed_candidate"]
    instruction = json.loads(_prompt(request))["instruction"]

    assert candidate["properties"]["parent_hypothesis_id"] == {
        "const": "hypothesis-cycle-1"
    }
    assert candidate["properties"]["relation"] == {
        "type": "string",
        "minLength": 1,
    }
    assert "Complete only cycle 2 SYNTHESIS" in instruction
    assert "copy parent_hypothesis_id exactly as 'hypothesis-cycle-1'" in instruction
    assert "provide a non-empty relation describing the revision" in instruction
    assert "Do not replace or omit the bound predecessor" in instruction


def test_provider_adapter_accepts_the_same_synthesis_shape_as_fake_agent() -> None:
    request = _claude_pincer_request(ResearchNodeSubkind.SYNTHESIS)
    generated = FakeAgent().invoke(request)
    assert isinstance(generated, AgentResponse)
    outer = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 1,
            "total_cost_usd": 0,
            "usage": {"input_tokens": 7, "output_tokens": 11},
            "modelUsage": {"stub-model": {}},
            "structured_output": {
                "typed_candidate": generated.typed_candidate,
                "declared_effects": [
                    {"effect": item.effect} for item in generated.declared_effects
                ],
            },
        },
        separators=(",", ":"),
    ).encode()

    response, failure, _ = _provider_response(request, "stub-model", outer)

    assert failure is None
    assert response is not None
    assert response.typed_candidate == generated.typed_candidate


def test_proposal_schema_omits_expert_signal_without_immutable_context_refs() -> None:
    request = _claude_request()
    request = request.__class__.from_dict(
        {**request.to_dict(), "immutable_context_refs": []}
    )

    schema = _output_schema(request)

    assert "expert_signal" not in schema["properties"]
    assert "expert_signal" not in schema["required"]


def _draft_provider_sample(workspace_artifact: object) -> tuple[object, str | None]:
    original = default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC)
    request = original.__class__.from_dict(
        {
            **original.to_dict(),
            "tool_permission_profile": CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
            "required_model_identity": "stub-model",
            "required_adapter_version": CLAUDE_AGENT_HOST_VERSION,
        }
    )
    generated = FakeAgent().invoke(original)
    assert isinstance(generated, AgentResponse)
    outer = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 1,
        "total_cost_usd": 0,
        "usage": {"input_tokens": 7, "output_tokens": 11},
        "modelUsage": {"stub-model": {}},
        "structured_output": {
            "typed_candidate": generated.typed_candidate,
            "proposed_workspace_artifacts": [workspace_artifact],
            "declared_effects": [{"effect": item.effect} for item in generated.declared_effects],
        },
    }
    response, error, _ = _provider_response(
        request,
        "stub-model",
        json.dumps(outer, ensure_ascii=False, separators=(",", ":")).encode(),
    )
    return response, error


def test_provider_utf8_workspace_is_canonicalized_into_durable_base64() -> None:
    source = '# UTF-8 survives: π\nprint("完成")\n'
    response, error = _draft_provider_sample(
        {"path": "experiment.py", "content_utf8": source}
    )

    assert error is None
    assert isinstance(response, AgentResponse)
    artifact = response.proposed_workspace_artifacts[0]
    assert artifact.path == "experiment.py"
    assert artifact.content_bytes() == source.encode("utf-8")
    assert set(artifact.to_dict()) == {"path", "content_base64"}


@pytest.mark.parametrize(
    "artifact",
    (
        {"path": "experiment.py", "content_base64": "cHJpbnQoMSk="},
        {
            "path": "experiment.py",
            "content_utf8": "print(1)",
            "content_base64": "cHJpbnQoMSk=",
        },
        {"path": "experiment.py", "content_utf8": "print(1)", "extra": True},
        {"path": "other.py", "content_utf8": "print(1)"},
        {"path": "experiment.py", "content_utf8": ""},
        {"path": "experiment.py", "content_utf8": 7},
    ),
)
def test_provider_workspace_rejects_ambiguous_or_invalid_wire_shapes(
    artifact: object,
) -> None:
    response, error = _draft_provider_sample(artifact)
    assert response is None
    assert error == "invalid_workspace_artifact"


def test_prompt_renders_immutable_input_bytes_exactly_once() -> None:
    request = _claude_request()
    payload = json.loads(_prompt(request))

    assert "agent_request" not in payload
    assert "input_artifacts" not in payload["agent_request_metadata"]
    assert payload["agent_request_metadata"]["immutable_context_refs"] == list(
        request.immutable_context_refs
    )
    assert payload["rendered_inputs"] == [
        {
            "ref": item.ref,
            "media_type": item.media_type,
            "content_utf8": item.content_bytes().decode("utf-8"),
        }
        for item in request.input_artifacts
    ]


def test_answer_prompt_renders_python_as_plaintext_with_review_only_instruction() -> None:
    original = default_request(AgentCommand.ANSWER_RENDEZVOUS)
    source = '# reviewer sees Python: π\nprint("完成")\n'
    source_input = EmbeddedInput.from_bytes(
        "source-attempt--experiment.py", "text/x-python", source.encode("utf-8")
    )
    request = original.__class__.from_dict(
        {
            **original.to_dict(),
            "tool_permission_profile": CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
            "required_model_identity": "stub-model",
            "required_adapter_version": CLAUDE_AGENT_HOST_VERSION,
            "immutable_context_refs": [source_input.ref],
            "input_artifacts": [
                original.input_artifacts[0].to_dict(),
                source_input.to_dict(),
            ],
        }
    )

    payload = json.loads(_prompt(request))
    rendered_source = next(
        item for item in payload["rendered_inputs"] if item["ref"] == source_input.ref
    )

    assert rendered_source == {
        "ref": source_input.ref,
        "media_type": "text/x-python",
        "content_utf8": source,
    }
    instruction = payload["instruction"]
    assert "preflight" in instruction.lower()
    assert "RendezvousReply" in instruction
    assert "supporting_refs" in instruction
    assert "experiment draft" not in instruction
    assert "parent_hypothesis_id" not in instruction
    assert "resource_preference" not in instruction


@pytest.mark.parametrize(
    "command",
    (AgentCommand.DRAFT_EXPERIMENT_SPEC, AgentCommand.ANSWER_RENDEZVOUS),
)
def test_nonproposal_schema_does_not_expose_expert_signal(command: AgentCommand) -> None:
    schema = _output_schema(_claude_request().__class__.from_dict({
        **default_request(command).to_dict(),
        "tool_permission_profile": CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
        "required_model_identity": "stub-model",
        "required_adapter_version": CLAUDE_AGENT_HOST_VERSION,
    }))
    assert "expert_signal" not in schema["properties"]


@pytest.mark.parametrize(
    ("command", "exposes_workspace"),
    (
        (AgentCommand.PROPOSE_HYPOTHESIS, False),
        (AgentCommand.DRAFT_EXPERIMENT_SPEC, True),
        (AgentCommand.ANSWER_RENDEZVOUS, False),
    ),
)
def test_only_spec_provider_schema_exposes_workspace(
    command: AgentCommand, exposes_workspace: bool,
) -> None:
    request = _claude_request().__class__.from_dict({
        **default_request(command).to_dict(),
        "tool_permission_profile": CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
        "required_model_identity": "stub-model",
        "required_adapter_version": CLAUDE_AGENT_HOST_VERSION,
    })
    schema = _output_schema(request)
    assert ("proposed_workspace_artifacts" in schema["properties"]) is exposes_workspace
    assert ("proposed_workspace_artifacts" in schema["required"]) is exposes_workspace


@pytest.mark.parametrize("command", tuple(AgentCommand))
def test_provider_schema_avoids_cli_strict_incompatible_array_bounds(command: AgentCommand) -> None:
    request = (
        _claude_expert_design_request()
        if command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
        else _claude_request().__class__.from_dict({
            **default_request(command).to_dict(),
            "tool_permission_profile": CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
            "required_model_identity": "stub-model",
            "required_adapter_version": CLAUDE_AGENT_HOST_VERSION,
        })
    )
    if command is AgentCommand.DEBATE_JUDGE:
        from test_debate_aci14 import judge_request
        request, _ = judge_request()
    rendered = json.dumps(_output_schema(request), sort_keys=True)
    assert "minItems" not in rendered
    assert "maxItems" not in rendered


def test_provider_typed_expert_signal_maps_to_aci_signal(tmp_path: Path) -> None:
    value = json.loads(_VALID_OUTPUT)
    value["structured_output"]["expert_signal"] = {
        "schema_version": 1,
        "kind": "HELP",
        "target_role_hint": "critic",
        "question": "Check the bounded measurement assumption.",
        "context_refs": ["goal-1"],
    }
    host = _host(tmp_path)
    observation = host.wait(_start(host, extra_environment={
        "STUB_VALID_OUTPUT": json.dumps(value, separators=(",", ":")),
    }))
    assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED
    assert observation.response is not None
    assert observation.response.expert_signal is not None
    assert observation.response.expert_signal.kind.value == "HELP"


def test_proposal_provider_envelope_injects_empty_workspace_and_rejects_extra_field(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path)
    accepted = host.wait(_start(host))
    assert accepted.terminal_reason is AgentHostTerminalReason.SUCCEEDED
    assert accepted.response is not None
    assert accepted.response.proposed_workspace_artifacts == ()

    value = json.loads(_VALID_OUTPUT)
    value["structured_output"]["proposed_workspace_artifacts"] = []
    rejected = host.wait(_start(
        host,
        request=_claude_request().__class__.from_dict({
            **_claude_request().to_dict(),
            "attempt_id": "attempt-extra-workspace",
            "command_id": "command-extra-workspace",
        }),
        extra_environment={"STUB_VALID_OUTPUT": json.dumps(value, separators=(",", ":"))},
    ))
    assert rejected.terminal_reason is AgentHostTerminalReason.INVALID_OUTPUT
    assert rejected.failure_code == "invalid_structured_output"


@pytest.mark.parametrize(
    ("parent_hypothesis_id", "relation"),
    ((None, "challenge"), ("hypothesis-parent", None)),
)
def test_invalid_hypothesis_lineage_preserves_bounded_validation_diagnostic(
    tmp_path: Path,
    parent_hypothesis_id: str | None,
    relation: str | None,
) -> None:
    value = json.loads(_VALID_OUTPUT)
    candidate = value["structured_output"]["typed_candidate"]
    candidate["parent_hypothesis_id"] = parent_hypothesis_id
    candidate["relation"] = relation

    host = _host(tmp_path)
    observation = host.wait(
        _start(
            host,
            extra_environment={
                "STUB_VALID_OUTPUT": json.dumps(value, separators=(",", ":")),
            },
        )
    )

    assert observation.terminal_reason is AgentHostTerminalReason.INVALID_OUTPUT
    assert observation.failure_code == "invalid_candidate"
    assert observation.response is None
    assert observation.diagnostics["validation_error"] == {
        "code": "invalid_candidate",
        "message": "parent_hypothesis_id and relation must be supplied together",
    }
    assert len(json.dumps(observation.diagnostics["validation_error"]).encode()) <= 65_536


def _provider_output(**updates: object) -> str:
    value = json.loads(_VALID_OUTPUT)
    value.update(updates)
    return json.dumps(value, separators=(",", ":"))


def _provider_ndjson(*events: object) -> bytes:
    return (
        "\n".join(
            json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            for event in events
        )
        + "\n"
    ).encode("utf-8")


def test_stream_json_success_keeps_final_result_as_the_postflight_authority() -> None:
    request = _claude_request()
    result = json.loads(_VALID_OUTPUT)
    raw = _provider_ndjson(
        {"type": "system", "subtype": "init", "session_id": "discard-session"},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "discard private thinking"},
                    {
                        "type": "tool_use",
                        "id": "structured-success",
                        "name": "StructuredOutput",
                        "input": result["structured_output"],
                    },
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "structured-success",
                        "content": "Structured output provided successfully",
                    }
                ],
            },
        },
        result,
    )

    response, failure, diagnostics = _provider_response(
        request,
        "stub-model",
        raw,
    )

    assert failure is None
    assert response is not None
    assert response.typed_candidate["claim"] == "stub candidate"
    assert "provider_structured_candidate" not in diagnostics
    assert "provider_diagnostic" not in diagnostics
    assert "discard private thinking" not in json.dumps(diagnostics)
    assert "discard-session" not in json.dumps(diagnostics)


@pytest.mark.parametrize(
    "raw",
    (
        _provider_ndjson(
            {"type": "system", "subtype": "init"},
            {"type": "stream_event", "event": {"type": "content_block_delta"}},
            json.loads(_VALID_OUTPUT),
        ),
        _provider_ndjson(
            json.loads(_VALID_OUTPUT),
            {"type": "user", "message": {"role": "user", "content": []}},
        ),
        _provider_ndjson(
            {"type": "system", "subtype": "init"},
            json.loads(_VALID_OUTPUT),
            json.loads(_VALID_OUTPUT),
        ),
    ),
)
def test_stream_json_rejects_partial_or_nonterminal_result_events(raw: bytes) -> None:
    response, failure, diagnostics = _provider_response(
        _claude_request(),
        "stub-model",
        raw,
    )

    assert response is None
    assert failure == "invalid_provider_envelope"
    assert diagnostics == {}


_RESUME_ARTIFACT_RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "num_turns": 0,
    "stop_reason": None,
    "terminal_reason": None,
    "total_cost_usd": 0,
    "usage": {"input_tokens": 0, "output_tokens": 0},
    "modelUsage": {},
    "origin": {"kind": "task-notification"},
}


def _notification_completions():
    first = json.loads(_VALID_OUTPUT)
    first["structured_output"]["typed_candidate"].update(parent_hypothesis_id=None, relation=None)
    first["result"] = json.dumps(first.pop("structured_output"))
    first.update(session_id="same-session", num_turns=2, total_cost_usd=0.5,
        modelUsage={"stub-model": {"inputTokens": 70, "outputTokens": 110,
            "cacheReadInputTokens": 30, "cacheCreationInputTokens": 0, "costUSD": 0.5}})
    last = json.loads(json.dumps(first))
    last.update(num_turns=1, total_cost_usd=0.6,
        origin={"kind": "task-notification", "producer": "session-task"})
    last["modelUsage"]["stub-model"].update(inputTokens=77, outputTokens=121, costUSD=0.6)
    return first, last


def test_identical_task_completion_accounts_cumulative_ledger_once():
    first, last = _notification_completions()
    request = _claude_request()
    response, failure, _ = _provider_response(request, "stub-model",
        _provider_ndjson(first, last), final_text_output=True)
    assert response is None and failure == "turn_budget_exceeded"
    request = replace(request, budget=replace(request.budget, max_turns=4, max_cost="1"))
    response, failure, diagnostics = _provider_response(request, "stub-model",
        _provider_ndjson(first, last), final_text_output=True)
    assert failure is None
    assert response.token_usage == {"input_tokens": 107, "output_tokens": 121, "total_tokens": 228}
    assert response.turn_count == 3
    assert float(response.cost) == 0.6
    assert diagnostics["provider_completion_results"] == 2
    response, failure, _ = _provider_response(request, "stub-model",
        _provider_ndjson(first, last), final_text_output=True,
        session_usage_baseline={"modelUsage": {"stub-model": {
            "inputTokens": 10, "outputTokens": 10, "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 0, "costUSD": 0.1}}, "totalCostUSD": 0.1})
    assert failure is None
    assert response.token_usage["total_tokens"] == 208
    assert float(response.cost) == 0.5


@pytest.mark.parametrize("change", ["session", "candidate", "origin", "producer", "failure",
    "ledger", "cost", "model", "turns", "trailing"])
def test_task_completion_rejects_changed_or_unaccounted_tail(change):
    first, last = _notification_completions()
    if change == "session": last["session_id"] = "foreign"
    elif change == "candidate": last["result"] += " "
    elif change == "origin": last.pop("origin")
    elif change == "producer": last["origin"].pop("producer")
    elif change == "failure": last["is_error"] = True
    elif change == "ledger": last["modelUsage"]["stub-model"]["inputTokens"] = 1
    elif change == "cost": last["modelUsage"]["stub-model"]["costUSD"] = 0.1
    elif change == "model": last["modelUsage"] = {}
    elif change == "turns": last["num_turns"] = True
    events = [first, last]
    if change == "trailing": events.append({"type": "assistant", "message": {"role": "assistant", "content": []}})
    response, failure, _ = _provider_response(_claude_request(), "stub-model",
        _provider_ndjson(*events), final_text_output=True)
    assert response is None and failure == "invalid_provider_envelope"


def _structured_exchange_and_result() -> tuple[object, ...]:
    result = json.loads(_VALID_OUTPUT)
    return (
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "structured-success",
                        "name": "StructuredOutput",
                        "input": result["structured_output"],
                    },
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "structured-success",
                        "content": "Structured output provided successfully",
                    }
                ],
            },
        },
        result,
    )


def test_stream_json_tolerates_session_resume_result_artifact() -> None:
    raw = _provider_ndjson(
        {
            "type": "system",
            "subtype": "task_notification",
            "summary": "Background shell command didn't finish before the previous session ended",
        },
        {"type": "system", "subtype": "init", "session_id": "resumed-session"},
        _RESUME_ARTIFACT_RESULT,
        {"type": "system", "subtype": "init", "session_id": "resumed-session"},
        *_structured_exchange_and_result(),
    )

    response, failure, _ = _provider_response(_claude_request(), "stub-model", raw)

    assert failure is None
    assert response is not None
    assert response.typed_candidate["claim"] == "stub candidate"


def test_stream_json_tolerates_system_notices_after_terminal_result() -> None:
    raw = _provider_ndjson(
        *_structured_exchange_and_result(),
        {"type": "system", "subtype": "background_tasks_changed", "tasks": []},
        {
            "type": "system",
            "subtype": "task_notification",
            "status": "stopped",
            "summary": "Check pytest config and count results",
        },
    )

    response, failure, _ = _provider_response(_claude_request(), "stub-model", raw)

    assert failure is None
    assert response is not None
    assert response.typed_candidate["claim"] == "stub candidate"


@pytest.mark.parametrize(
    "raw",
    (
        _provider_ndjson(
            {**_RESUME_ARTIFACT_RESULT, "origin": None},
            *_structured_exchange_and_result(),
        ),
        _provider_ndjson(
            {**_RESUME_ARTIFACT_RESULT, "num_turns": 1},
            *_structured_exchange_and_result(),
        ),
        _provider_ndjson(
            *_structured_exchange_and_result(),
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": []},
            },
        ),
    ),
)
def test_stream_json_rejects_non_artifact_or_non_system_outside_terminal_result(
    raw: bytes,
) -> None:
    response, failure, diagnostics = _provider_response(
        _claude_request(),
        "stub-model",
        raw,
    )

    assert response is None
    assert failure == "invalid_provider_envelope"
    assert diagnostics == {}


def test_stream_json_rejects_blank_or_non_object_ndjson_records() -> None:
    result = json.dumps(json.loads(_VALID_OUTPUT), separators=(",", ":"))
    for raw in (
        f'{{"type":"system","subtype":"init"}}\n\n{result}\n'.encode(),
        f'{{"type":"system","subtype":"init"}}\n[]\n{result}\n'.encode(),
    ):
        response, failure, diagnostics = _provider_response(
            _claude_request(),
            "stub-model",
            raw,
        )

        assert response is None
        assert failure in {"malformed_json", "invalid_provider_envelope"}
        assert diagnostics == {}


def test_failed_stream_retains_only_last_matching_structured_candidate(
    tmp_path: Path,
) -> None:
    request, _template = _claude_literature_query_plan_request()
    # This stub subprocess tests the retained transport parser; ACI-11's real
    # session/relay launch is exercised in test_full_cc_adapter/host_g3.
    request = replace(request, schema_version=10)
    first_candidate = {"query": "first rejected query", "max_items": 99}
    final_candidate = {
        "query": '"emotion recognition" AND transformer',
        "max_items": 99,
    }
    final_diagnostic = (
        "Output does not match required schema: /typed_candidate: "
        "must NOT have additional properties"
    )
    result = {
        "type": "result",
        "subtype": "error_max_structured_output_retries",
        "is_error": True,
        "result": "generic retry exhaustion summary",
        "errors": ["generic final error array"],
        "num_turns": 2,
        "total_cost_usd": 0.25,
        "usage": {"input_tokens": 7, "output_tokens": 11},
        "modelUsage": {"stub-model": {}},
    }
    raw = _provider_ndjson(
        {"type": "system", "subtype": "init", "session_id": "discard-session"},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "discard private thinking"},
                    {
                        "type": "tool_use",
                        "id": "structured-first",
                        "name": "StructuredOutput",
                        "input": {"typed_candidate": first_candidate},
                    },
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "structured-first",
                        "is_error": True,
                        "content": "Output does not match required schema: first",
                    }
                ],
            },
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "structured-final",
                        "name": "StructuredOutput",
                        "input": {"typed_candidate": final_candidate},
                    }
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "structured-final",
                        "is_error": True,
                        "content": final_diagnostic,
                    }
                ],
            },
        },
        result,
    )

    host = _host(tmp_path)
    observation = host.wait(
        _start(
            host,
            mode="diagnostic",
            request=request,
            extra_environment={
                "STUB_STDOUT_DIAGNOSTIC": raw.decode("utf-8"),
                "STUB_DIAGNOSTIC": "",
            },
        )
    )

    assert observation.failure_code == "nonzero_exit"
    assert observation.diagnostics["provider_subtype"] == (
        "error_max_structured_output_retries"
    )
    assert observation.diagnostics["provider_structured_candidate"] == {
        "typed_candidate": final_candidate,
    }
    assert observation.diagnostics["provider_diagnostic"] == final_diagnostic
    assert "validation_error" not in observation.diagnostics
    assert "stdout_excerpt" not in observation.diagnostics
    retained = json.dumps(observation.diagnostics, ensure_ascii=False)
    assert "discard private thinking" not in retained
    assert "discard-session" not in retained
    assert [item["tool_use_id"] for item in observation.diagnostics[
        "structured_output_correction"]["rounds"]] == ["structured-first", "structured-final"]
    assert len(
        json.dumps(
            observation.diagnostics["provider_structured_candidate"],
            ensure_ascii=False,
        ).encode("utf-8")
    ) <= 64 * 1024


def test_failed_stream_omits_structured_candidate_over_diagnostic_bound() -> None:
    result = {
        "type": "result",
        "subtype": "error_max_structured_output_retries",
        "is_error": True,
        "num_turns": 1,
        "total_cost_usd": 0,
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "modelUsage": {"stub-model": {}},
    }
    raw = _provider_ndjson(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "structured-oversize",
                        "name": "StructuredOutput",
                        "input": {
                            "typed_candidate": {"query": "x" * (64 * 1024)}
                        },
                    }
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "structured-oversize",
                        "is_error": True,
                        "content": "Output does not match required schema: too long",
                    }
                ],
            },
        },
        result,
    )

    response, failure, diagnostics = _provider_response(
        _claude_literature_query_plan_request()[0],
        "stub-model",
        raw,
    )

    assert response is None
    assert failure == "provider_not_success"
    assert "provider_structured_candidate" not in diagnostics
    assert diagnostics["provider_diagnostic"] == (
        "Output does not match required schema: too long"
    )


def test_failed_stream_omits_credential_shaped_structured_candidate() -> None:
    secret = "must-not-persist"
    result = {
        "type": "result",
        "subtype": "error_max_structured_output_retries",
        "is_error": True,
        "num_turns": 1,
        "total_cost_usd": 0,
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "modelUsage": {"stub-model": {}},
    }
    raw = _provider_ndjson(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "structured-secret",
                        "name": "StructuredOutput",
                        "input": {
                            "typed_candidate": {
                                "query": "ordinary query",
                                "api_key": secret,
                            }
                        },
                    }
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "structured-secret",
                        "is_error": True,
                        "content": "Output does not match required schema",
                    }
                ],
            },
        },
        result,
    )

    _response, _failure, diagnostics = _provider_response(
        _claude_literature_query_plan_request()[0],
        "stub-model",
        raw,
    )

    assert "provider_structured_candidate" not in diagnostics
    assert diagnostics["provider_diagnostic"] == (
        "Output does not match required schema"
    )
    assert secret not in json.dumps(diagnostics)


def test_final_error_array_supplies_bounded_provider_diagnostic() -> None:
    result = {
        "type": "result",
        "subtype": "error_max_structured_output_retries",
        "is_error": True,
        "errors": [
            "Failed to provide valid structured output after 1 attempts",
            "retry exhausted",
        ],
        "num_turns": 1,
        "total_cost_usd": 0,
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "modelUsage": {"stub-model": {}},
    }

    response, failure, diagnostics = _provider_response(
        _claude_literature_query_plan_request()[0],
        "stub-model",
        _provider_ndjson(
            {"type": "system", "subtype": "init"},
            result,
        ),
    )

    assert response is None
    assert failure == "provider_not_success"
    assert diagnostics["provider_diagnostic"] == (
        "Failed to provide valid structured output after 1 attempts; retry exhausted"
    )


def test_final_error_array_summary_is_bounded() -> None:
    result = {
        "type": "result",
        "subtype": "error_max_structured_output_retries",
        "is_error": True,
        "errors": ["first " + "x" * 800, "second " + "y" * 800],
        "num_turns": 1,
        "total_cost_usd": 0,
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "modelUsage": {"stub-model": {}},
    }

    _response, _failure, diagnostics = _provider_response(
        _claude_literature_query_plan_request()[0],
        "stub-model",
        _provider_ndjson(result),
    )

    summary = diagnostics["provider_diagnostic"]
    assert isinstance(summary, str)
    assert summary.startswith("first ")
    assert len(summary) == 1_024


def test_failed_stream_ignores_unmatched_tool_result() -> None:
    result = {
        "type": "result",
        "subtype": "error_max_structured_output_retries",
        "is_error": True,
        "num_turns": 1,
        "total_cost_usd": 0,
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "modelUsage": {"stub-model": {}},
    }
    raw = _provider_ndjson(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "structured-candidate",
                        "name": "StructuredOutput",
                        "input": {"typed_candidate": {"query": "rejected"}},
                    }
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "different-tool-use",
                        "is_error": True,
                        "content": "unmatched diagnostic",
                    }
                ],
            },
        },
        result,
    )

    response, failure, diagnostics = _provider_response(
        _claude_literature_query_plan_request()[0],
        "stub-model",
        raw,
    )

    assert response is None
    assert failure == "provider_not_success"
    assert "provider_structured_candidate" not in diagnostics
    assert "provider_diagnostic" not in diagnostics


def test_gate_releases_only_after_process_group_binding_returns(tmp_path: Path) -> None:
    host = _host(tmp_path)
    persisted = tmp_path / "persisted-pgid"
    callback_entered = threading.Event()
    allow_persist = threading.Event()
    result: list[object] = []

    def bind(binding) -> None:
        callback_entered.set()
        assert (
            Path(f"/proc/{binding.pid}/exe").resolve() == Path(sys.executable).resolve()
        )
        assert allow_persist.wait(3)
        persisted.write_text(str(binding.process_group_id), encoding="ascii")

    thread = threading.Thread(
        target=lambda: result.append(_start(host, bind_process_group=bind))
    )
    thread.start()
    assert callback_entered.wait(3)
    time.sleep(0.05)
    assert not persisted.exists()
    allow_persist.set()
    thread.join(timeout=3)
    assert len(result) == 1
    handle = result[0]
    assert persisted.read_text(encoding="ascii") == str(handle.process_group_id)
    observation = host.wait(handle)
    assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED
    assert observation.response is not None


def test_deadline_crossed_during_binding_never_releases_provider(
    tmp_path: Path,
) -> None:
    base = _host(tmp_path)
    clock_offset = [0.0]
    host = ClaudeAgentHost(
        claude_executable=base.claude_executable,
        settings_path=base.settings_path,
        allowed_environment_names=base.allowed_environment_names,
        extra_runtime_paths=("/bin/sh", "/bin/sleep"),
        clock=lambda: time.monotonic() + clock_offset[0],
        sleep=time.sleep,
    )
    request = _claude_draft_request()
    workspace = _draft_workspace(tmp_path, request.attempt_id)
    bindings = []

    def bind(binding) -> None:
        bindings.append(binding)
        clock_offset[0] += request.budget.wall_seconds + 1

    with pytest.raises(AgentDeadlineExpired, match="before provider release"):
        _start(
            host,
            request=request,
            bind_process_group=bind,
            tool_workspace=workspace,
        )

    assert len(bindings) == 1
    assert not process_group_exists(bindings[0].process_group_id)
    assert not workspace.attempt_root.exists()
    assert not host.cancel(request.attempt_id)


def test_concurrent_same_attempt_start_spawns_only_one_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _host(tmp_path)
    real_popen = subprocess.Popen
    first_spawn_entered = threading.Event()
    release_first_spawn = threading.Event()
    spawn_lock = threading.Lock()
    spawn_calls = 0

    def delayed_first_spawn(*args, **kwargs):
        nonlocal spawn_calls
        with spawn_lock:
            spawn_calls += 1
            call_number = spawn_calls
        if call_number == 1:
            first_spawn_entered.set()
            assert release_first_spawn.wait(3)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(
        "hypoloom.claude_agent_host.subprocess.Popen", delayed_first_spawn
    )
    results: list[object] = []

    def invoke() -> None:
        try:
            results.append(_start(host))
        except BaseException as exc:
            results.append(exc)

    first = threading.Thread(target=invoke)
    second = threading.Thread(target=invoke)
    first.start()
    assert first_spawn_entered.wait(3)
    second.start()
    second.join(timeout=1)
    release_first_spawn.set()
    first.join(timeout=3)
    second.join(timeout=3)

    handles = [item for item in results if not isinstance(item, BaseException)]
    failures = [item for item in results if isinstance(item, BaseException)]
    assert len(handles) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], AgentHostError)
    assert "attempt already running" in str(failures[0])
    assert spawn_calls == 2  # one provider launcher plus its guardian
    assert host.wait(handles[0]).terminal_reason is AgentHostTerminalReason.SUCCEEDED


@pytest.mark.parametrize("shared_cleanup_root", (True, False))
def test_rejected_concurrent_draft_start_cleans_only_unowned_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shared_cleanup_root: bool,
) -> None:
    host = _host(tmp_path)
    request = _claude_draft_request()
    workspace = _draft_workspace(tmp_path, request.attempt_id)
    if shared_cleanup_root:
        alternate_target = workspace.attempt_root / "alternate-target"
        alternate_target.mkdir()
        alias_workspace = replace(workspace, target_root=alternate_target)
    else:
        alias_workspace = _draft_workspace(tmp_path / "loser", request.attempt_id)
    source = "# concurrently owned\nprint('ok')\n"
    real_popen = subprocess.Popen
    first_spawn_entered = threading.Event()
    release_first_spawn = threading.Event()
    spawn_lock = threading.Lock()
    spawn_calls = 0

    def delayed_first_spawn(*args, **kwargs):
        nonlocal spawn_calls
        with spawn_lock:
            spawn_calls += 1
            call_number = spawn_calls
        if call_number == 1:
            first_spawn_entered.set()
            assert release_first_spawn.wait(3)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(
        "hypoloom.claude_agent_host.subprocess.Popen", delayed_first_spawn
    )
    results: list[object] = []

    def invoke(tool_workspace) -> None:
        try:
            results.append(_start(
                host,
                mode="workspace",
                request=request,
                tool_workspace=tool_workspace,
                extra_environment={
                    "STUB_VALID_OUTPUT": _draft_provider_output(),
                    "STUB_WORKSPACE_SOURCE": source,
                },
            ))
        except BaseException as exc:
            results.append(exc)

    first = threading.Thread(target=invoke, args=(workspace,))
    second = threading.Thread(target=invoke, args=(alias_workspace,))
    first.start()
    assert first_spawn_entered.wait(3)
    second.start()
    second.join(timeout=1)
    assert workspace.attempt_root.exists()
    assert alias_workspace.attempt_root.exists() is shared_cleanup_root
    release_first_spawn.set()
    first.join(timeout=3)
    second.join(timeout=3)

    handles = [item for item in results if not isinstance(item, BaseException)]
    failures = [item for item in results if isinstance(item, BaseException)]
    assert len(handles) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], AgentHostError)
    assert "attempt already running" in str(failures[0])
    assert spawn_calls == 2
    observation = host.wait(handles[0])
    assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED
    assert observation.response is not None
    assert observation.response.proposed_workspace_artifacts[0].content_bytes() == source.encode()
    assert not workspace.attempt_root.exists()
    assert not alias_workspace.attempt_root.exists()


def test_guardian_spawn_failure_keeps_reservation_until_workspace_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _host(tmp_path)
    request = _claude_draft_request()
    workspace = _draft_workspace(tmp_path, request.attempt_id)
    alias_workspace = replace(workspace)
    real_popen = subprocess.Popen
    real_cleanup = workspace.cleanup
    cleanup_entered = threading.Event()
    release_cleanup = threading.Event()
    provider_pids: list[int] = []
    spawn_calls = 0
    cleanup_calls = 0

    def fail_guardian(*args, **kwargs):
        nonlocal spawn_calls
        spawn_calls += 1
        if spawn_calls == 2:
            raise OSError("synthetic guardian spawn failure")
        process = real_popen(*args, **kwargs)
        provider_pids.append(process.pid)
        return process

    def delayed_cleanup() -> None:
        nonlocal cleanup_calls
        cleanup_calls += 1
        if cleanup_calls == 1:
            cleanup_entered.set()
            assert release_cleanup.wait(3)
        real_cleanup()

    monkeypatch.setattr(
        "hypoloom.claude_agent_host.subprocess.Popen", fail_guardian
    )
    monkeypatch.setattr(type(workspace), "cleanup", lambda self: delayed_cleanup())
    first_result: list[BaseException] = []

    def first_start() -> None:
        try:
            _start(host, request=request, tool_workspace=workspace)
        except BaseException as exc:
            first_result.append(exc)

    before = len(tuple(Path("/proc/self/fd").iterdir()))
    first = threading.Thread(target=first_start)
    first.start()
    assert cleanup_entered.wait(3)
    try:
        with pytest.raises(AgentHostError, match="attempt already running"):
            _start(host, request=request, tool_workspace=alias_workspace)
        assert workspace.attempt_root.exists()
    finally:
        release_cleanup.set()
    first.join(timeout=3)

    assert len(first_result) == 1
    assert isinstance(first_result[0], AgentHostError)
    assert "provider launch failed" in str(first_result[0])
    assert spawn_calls == 2
    assert cleanup_calls == 2
    assert not workspace.attempt_root.exists()
    assert not host._reserved_attempts
    assert all(not process_group_exists(pid) for pid in provider_pids)
    assert len(tuple(Path("/proc/self/fd").iterdir())) == before


def test_guardian_spawn_interrupt_cleans_launch_and_reraises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _host(tmp_path)
    request = _claude_draft_request()
    workspace = _draft_workspace(tmp_path, request.attempt_id)
    real_popen = subprocess.Popen
    provider_pids: list[int] = []
    spawn_calls = 0

    def interrupt_guardian(*args, **kwargs):
        nonlocal spawn_calls
        spawn_calls += 1
        if spawn_calls == 2:
            raise KeyboardInterrupt("synthetic guardian interrupt")
        process = real_popen(*args, **kwargs)
        provider_pids.append(process.pid)
        return process

    monkeypatch.setattr(
        "hypoloom.claude_agent_host.subprocess.Popen", interrupt_guardian
    )
    before = len(tuple(Path("/proc/self/fd").iterdir()))

    with pytest.raises(KeyboardInterrupt, match="synthetic guardian interrupt"):
        _start(host, request=request, tool_workspace=workspace)

    assert spawn_calls == 2
    assert not workspace.attempt_root.exists()
    assert not host._reserved_attempts
    assert all(not process_group_exists(pid) for pid in provider_pids)
    assert len(tuple(Path("/proc/self/fd").iterdir())) == before


def test_binding_failure_never_releases_provider_and_leaves_no_group(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path)
    bound_pgid: list[int] = []

    def fail_binding(binding) -> None:
        bound_pgid.append(binding.process_group_id)
        raise RuntimeError("synthetic persistence failure")

    with pytest.raises(AgentHostError, match="before provider release"):
        _start(host, bind_process_group=fail_binding)
    assert len(bound_pgid) == 1
    assert not process_group_exists(bound_pgid[0])


def test_binding_interrupt_cleans_draft_workspace_and_reraises(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path)
    request = _claude_draft_request()
    workspace = _draft_workspace(tmp_path, request.attempt_id)
    bound_pgid: list[int] = []

    def interrupt_binding(binding) -> None:
        bound_pgid.append(binding.process_group_id)
        raise KeyboardInterrupt("synthetic binding interrupt")

    before = len(tuple(Path("/proc/self/fd").iterdir()))
    with pytest.raises(KeyboardInterrupt, match="synthetic binding interrupt"):
        _start(
            host,
            request=request,
            tool_workspace=workspace,
            bind_process_group=interrupt_binding,
        )

    assert len(bound_pgid) == 1
    assert not process_group_exists(bound_pgid[0])
    assert not workspace.attempt_root.exists()
    assert not host._handles
    assert not host._reserved_attempts
    assert len(tuple(Path("/proc/self/fd").iterdir())) == before


def test_gate_release_failure_cleans_registered_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _host(tmp_path)
    bound_pgid: list[int] = []

    def bind(binding) -> None:
        bound_pgid.append(binding.process_group_id)

    def fail_release(fd: int, data: bytes) -> int:
        raise OSError("synthetic gate write failure")

    monkeypatch.setattr("hypoloom.claude_agent_host.os.write", fail_release)
    before = len(tuple(Path("/proc/self/fd").iterdir()))
    with pytest.raises(AgentHostError, match="provider release failed"):
        _start(host, bind_process_group=bind)
    after = len(tuple(Path("/proc/self/fd").iterdir()))
    assert len(bound_pgid) == 1
    assert not process_group_exists(bound_pgid[0])
    assert not host.cancel("attempt-1")
    assert after == before


def test_launch_is_bounded_structured_toolless_and_has_no_secret_argv(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path)
    secret = "sentinel-secret-must-not-appear"
    handle = _start(host, extra_environment={"HOST_FORBIDDEN": secret})
    argv = handle.launch_argv
    for flag in (
        "--safe-mode",
        "--bare",
        "--print",
        "--output-format",
        "--json-schema",
        "--permission-mode",
        "--tools",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--max-budget-usd",
        "--model",
        "--name",
    ):
        assert flag in argv
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv
    assert "--include-partial-messages" not in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert argv[argv.index("--name") + 1] == "attempt-1"
    for runtime_path in (
        "/lib/x86_64-linux-gnu/libnss_dns.so.2",
        "/lib/x86_64-linux-gnu/libnss_files.so.2",
        "/lib/x86_64-linux-gnu/libresolv.so.2",
    ):
        assert runtime_path in argv
    assert argv[argv.index("--max-turns") + 1] == "2"
    assert secret not in "\0".join(argv)
    assert "--share-net" in argv
    assert "--unshare-all" in argv
    assert host.wait(handle).terminal_reason is AgentHostTerminalReason.SUCCEEDED


def test_extended_context_selector_is_the_exact_observed_identity(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path)
    selector = "stub-model[1m]"
    request = _claude_request().__class__.from_dict(
        {
            **_claude_request().to_dict(),
            "required_model_identity": selector,
        }
    )
    output = _provider_output(modelUsage={selector: {}})

    handle = _start(
        host,
        model=selector,
        request=request,
        extra_environment={"STUB_VALID_OUTPUT": output},
    )
    argv = handle.launch_argv

    assert argv[argv.index("--model") + 1] == selector
    assert handle.model == selector
    observation = host.wait(handle)
    assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED
    assert observation.response is not None
    assert observation.response.model_identity == selector


def test_extended_context_selector_rejects_base_model_usage(tmp_path: Path) -> None:
    host = _host(tmp_path)
    selector = "stub-model[1m]"
    request = _claude_request().__class__.from_dict(
        {
            **_claude_request().to_dict(),
            "required_model_identity": selector,
        }
    )
    output = _provider_output(modelUsage={"stub-model": {}})

    observation = host.wait(
        _start(
            host,
            model=selector,
            request=request,
            extra_environment={"STUB_VALID_OUTPUT": output},
        )
    )

    assert observation.terminal_reason is AgentHostTerminalReason.INVALID_OUTPUT
    assert observation.failure_code == "model_identity_mismatch"
    assert observation.response is None


def test_guardian_does_not_inherit_host_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _host(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "guardian-must-not-inherit")
    real_popen = subprocess.Popen
    child_environments: list[dict[str, str]] = []

    def recording_popen(*args, **kwargs):
        child_environments.append(dict(kwargs["env"]))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(
        "hypoloom.claude_agent_host.subprocess.Popen", recording_popen
    )
    assert host.wait(_start(host)).terminal_reason is AgentHostTerminalReason.SUCCEEDED
    assert len(child_environments) == 2
    assert "ANTHROPIC_API_KEY" not in child_environments[0]
    assert child_environments[0]["MAX_STRUCTURED_OUTPUT_RETRIES"] == str(
        CLAUDE_AGENT_STRUCTURED_OUTPUT_FAILED_VALIDATION_CAP
    )
    assert child_environments[0]["CLAUDE_CODE_MAX_RETRIES"] == str(
        CLAUDE_AGENT_TRANSPORT_RETRIES
    )
    assert child_environments[1] == {}


def test_missing_settings_uses_inline_empty_json_and_preserves_bare_isolation(
    tmp_path: Path,
) -> None:
    base = _host(tmp_path)
    host = ClaudeAgentHost(
        claude_executable=base.claude_executable,
        settings_path=None,
        allowed_environment_names=base.allowed_environment_names,
        extra_runtime_paths=("/bin/sh",),
    )

    handle = _start(host)
    argv = handle.launch_argv

    assert "--bare" in argv
    assert argv[argv.index("--settings") + 1] == "{}"
    assert "/config/settings.json" not in argv
    assert host.wait(handle).terminal_reason is AgentHostTerminalReason.SUCCEEDED


def test_preflight_validates_without_spawning_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _host(tmp_path)

    def unexpected_spawn(*args, **kwargs):
        raise AssertionError("preflight must not spawn")

    monkeypatch.setattr(
        "hypoloom.claude_agent_host.subprocess.Popen", unexpected_spawn
    )
    assert (
        host.preflight(
            model="stub-model",
            tool_permission_profile=CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
        )
        is None
    )


def test_preflight_reuses_provider_environment_allowlist_validation(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path)

    with pytest.raises(AgentHostError, match="not allowlisted"):
        host.preflight(
            model="stub-model",
            provider_environment={"UNREVIEWED_ENDPOINT": "https://example.invalid"},
        )


def test_settings_file_requires_mode_0600(tmp_path: Path) -> None:
    host = _host(tmp_path)
    assert host.settings_path is not None
    host.settings_path.chmod(0o640)

    with pytest.raises(AgentHostError, match="mode 0600"):
        host.preflight(model="stub-model")


@pytest.mark.parametrize(
    ("name", "pinned", "conflicting"),
    (
        (
            "MAX_STRUCTURED_OUTPUT_RETRIES",
            CLAUDE_AGENT_STRUCTURED_OUTPUT_FAILED_VALIDATION_CAP,
            "5",
        ),
        ("CLAUDE_CODE_MAX_RETRIES", CLAUDE_AGENT_TRANSPORT_RETRIES, "3"),
    ),
)
def test_settings_cannot_override_pinned_agent_retry_policy(
    tmp_path: Path,
    name: str,
    pinned: int,
    conflicting: str,
) -> None:
    host = _host(tmp_path)
    assert host.settings_path is not None
    host.settings_path.write_text(
        json.dumps({"env": {name: conflicting}}),
        encoding="utf-8",
    )

    with pytest.raises(
        AgentHostError,
        match=rf"settings {name} conflicts with the pinned Agent retry policy",
    ):
        host.preflight(model="stub-model")

    host.settings_path.write_text(
        json.dumps({"env": {name: str(pinned)}}), encoding="utf-8",
    )
    assert host.preflight(model="stub-model") is None


@pytest.mark.parametrize(
    ("settings_text", "match"),
    (("{", "valid JSON object"), ("[]", "JSON object")),
)
def test_invalid_settings_fail_before_fork(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings_text: str,
    match: str,
) -> None:
    host = _host(tmp_path)
    host.settings_path.write_text(settings_text, encoding="utf-8")

    def unexpected_spawn(*args, **kwargs):
        raise AssertionError("invalid settings must fail before fork")

    monkeypatch.setattr(
        "hypoloom.claude_agent_host.subprocess.Popen", unexpected_spawn
    )
    with pytest.raises(AgentHostError, match=match):
        host.start(
            _claude_request(),
            model="stub-model",
            provider_environment={
                "STUB_MODE": "valid",
                "STUB_VALID_OUTPUT": _VALID_OUTPUT,
            },
            bind_process_group=lambda binding: None,
        )


def test_wrong_permission_profile_fails_before_fork(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _host(tmp_path)
    request = _claude_request()
    request = request.__class__.from_dict(
        {**request.to_dict(), "tool_permission_profile": "phase2-fake"}
    )

    def unexpected_spawn(*args, **kwargs):
        raise AssertionError("wrong permission profile must fail before fork")

    monkeypatch.setattr(
        "hypoloom.claude_agent_host.subprocess.Popen", unexpected_spawn
    )
    with pytest.raises(AgentHostError, match="tool permission profile"):
        host.start(
            request,
            model="stub-model",
            provider_environment={
                "STUB_MODE": "valid",
                "STUB_VALID_OUTPUT": _VALID_OUTPUT,
            },
            bind_process_group=lambda binding: None,
        )


def test_extra_runtime_paths_reject_arbitrary_files_and_directories(
    tmp_path: Path,
) -> None:
    base = _host(tmp_path)
    arbitrary_file = tmp_path / "state.sqlite"
    arbitrary_file.write_text("not runtime", encoding="utf-8")
    arbitrary_directory = tmp_path / "repo"
    arbitrary_directory.mkdir()
    for path in (arbitrary_file, arbitrary_directory):
        with pytest.raises(ValueError, match="extra runtime path"):
            ClaudeAgentHost(
                claude_executable=base.claude_executable,
                settings_path=base.settings_path,
                allowed_environment_names=base.allowed_environment_names,
                extra_runtime_paths=(path,),
            )


def test_extra_runtime_paths_allow_required_system_regular_files(
    tmp_path: Path,
) -> None:
    base = _host(tmp_path)
    host = ClaudeAgentHost(
        claude_executable=base.claude_executable,
        settings_path=base.settings_path,
        allowed_environment_names=base.allowed_environment_names,
        extra_runtime_paths=("/bin/sh", "/usr/bin/sed"),
    )
    assert host.preflight(model="stub-model") is None


def test_wrong_required_output_schema_fails_at_request_construction() -> None:
    request = _claude_request()
    with pytest.raises(AgentACIError) as error:
        request.__class__.from_dict(
            {
                **request.to_dict(),
                "required_output_schema": "ExperimentSpecCandidate/v1",
            }
        )
    assert error.value.code == "wrong_output_schema"


@pytest.mark.parametrize(
    "selector",
    ("other-model", "other-model[1m]", "stub-model[1m]"),
)
def test_selector_identity_must_equal_request_before_fork(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selector: str
) -> None:
    host = _host(tmp_path)

    def unexpected_spawn(*args, **kwargs):
        raise AssertionError("wrong exact model must fail before fork")

    monkeypatch.setattr(
        "hypoloom.claude_agent_host.subprocess.Popen", unexpected_spawn
    )
    with pytest.raises(AgentHostError, match="required model identity"):
        host.start(
            _claude_request(),
            model=selector,
            provider_environment={
                "STUB_MODE": "valid",
                "STUB_VALID_OUTPUT": _VALID_OUTPUT,
            },
            bind_process_group=lambda binding: None,
        )


def test_poll_returns_typed_success_without_database_authority(tmp_path: Path) -> None:
    host = _host(tmp_path)
    handle = _start(host)
    observation = None
    deadline = time.monotonic() + 3
    while observation is None and time.monotonic() < deadline:
        observation = host.poll(handle)
        time.sleep(0.01)
    assert observation is not None
    assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED
    assert observation.response is not None
    assert observation.response.model_identity == "stub-model"
    assert observation.response.token_usage == {
        "input_tokens": 7,
        "output_tokens": 11,
        "total_tokens": 18,
    }


@pytest.mark.parametrize(
    ("model_usage", "failure_code"),
    (
        (None, "missing_model_identity"),
        ({}, "missing_model_identity"),
        ({"": {}}, "invalid_model_identity"),
        ({"stub-model": {}, "other-model": {}}, "multiple_model_identities"),
        ({"other-model": {}}, "model_identity_mismatch"),
        ({"stub-model[1m]": {}}, "model_identity_mismatch"),
    ),
)
def test_provider_model_usage_requires_one_exact_nonempty_identity(
    tmp_path: Path,
    model_usage: object,
    failure_code: str,
) -> None:
    host = _host(tmp_path)
    output = _provider_output(modelUsage=model_usage)
    if model_usage is None:
        value = json.loads(output)
        del value["modelUsage"]
        output = json.dumps(value, separators=(",", ":"))
    observation = host.wait(
        _start(host, extra_environment={"STUB_VALID_OUTPUT": output})
    )
    assert observation.terminal_reason is AgentHostTerminalReason.INVALID_OUTPUT
    assert observation.failure_code == failure_code
    assert observation.response is None


@pytest.mark.parametrize(
    ("updates", "failure_code"),
    (
        ({"type": "system"}, "invalid_provider_type"),
        ({"type": "garbage"}, "invalid_provider_type"),
        ({"subtype": "error_max_budget"}, "provider_not_success"),
        ({"subtype": "error"}, "provider_not_success"),
    ),
)
def test_provider_outer_envelope_requires_exact_result_success(
    tmp_path: Path,
    updates: dict[str, object],
    failure_code: str,
) -> None:
    host = _host(tmp_path)
    observation = host.wait(
        _start(
            host,
            extra_environment={"STUB_VALID_OUTPUT": _provider_output(**updates)},
        )
    )
    assert observation.terminal_reason is AgentHostTerminalReason.INVALID_OUTPUT
    assert observation.failure_code == failure_code
    assert observation.response is None


@pytest.mark.parametrize("message", [
    "API Error: Claude's response exceeded the 32000 output token maximum.",
    "API Error: Failed to parse JSON",
])
def test_error_true_with_success_subtype_keeps_actual_api_failure(tmp_path: Path, message: str) -> None:
    host = _host(tmp_path)
    output = json.dumps({"type": "result", "subtype": "success", "is_error": True,
        "result": message, "num_turns": 1, "total_cost_usd": 0.25,
        "usage": {"input_tokens": 17, "output_tokens": 9}})
    observation = host.wait(_start(host, mode="diagnostic", extra_environment={
        "STUB_STDOUT_DIAGNOSTIC": output,
        "STUB_DIAGNOSTIC": '[claude-code:unrecognized_model] {"model":"test[1m]","query_source":"sdk"}'}))
    assert observation.failure_code == "nonzero_exit"
    assert observation.response is None
    assert observation.diagnostics["provider_diagnostic"] == message
    assert observation.diagnostics["provider_accounting"]["token_usage"]["total_tokens"] == 26


@pytest.mark.parametrize("command", [AgentCommand.OBSERVER, AgentCommand.DEBATE_JUDGE])
def test_cli_oversized_input_is_terminal_only_for_stateless_commands(command: AgentCommand) -> None:
    from hypoloom.claude_agent_host import _classify_cli_oversized_input

    diagnostics: dict[str, object] = {"provider_diagnostic": "Prompt is too long"}
    _classify_cli_oversized_input(default_request(command), diagnostics)
    assert diagnostics["provider_failure"] == {
        "code": "input_too_long", "http_status": None, "source": "cli_compaction"}
    assert "provider_failure_hint" not in diagnostics


def test_cli_oversized_input_keeps_stateful_resume_retry() -> None:
    from hypoloom.agent_context import agent_failure_stage, agent_retry_class
    from hypoloom.claude_agent_host import _classify_cli_oversized_input

    diagnostics: dict[str, object] = {"provider_diagnostic": "Prompt is too long"}
    _classify_cli_oversized_input(default_request(AgentCommand.HYPOTHESIS_PROPOSAL), diagnostics)
    assert "provider_failure" not in diagnostics
    assert diagnostics["provider_failure_hint"] == {
        "code": "input_too_long", "source": "cli_compaction"}
    details = {"diagnostics": diagnostics}
    assert agent_retry_class("nonzero_exit", details, retryable=True) == "INFRASTRUCTURE"
    assert agent_failure_stage("nonzero_exit", details) == "provider_process"


def test_cli_oversized_input_never_overrides_wire_or_unmatched_diagnostics() -> None:
    from hypoloom.claude_agent_host import _classify_cli_oversized_input

    wire = {"code": "rate_limited", "http_status": 429}
    diagnostics: dict[str, object] = {
        "provider_diagnostic": "Prompt is too long", "provider_failure": dict(wire)}
    _classify_cli_oversized_input(default_request(AgentCommand.OBSERVER), diagnostics)
    assert diagnostics["provider_failure"] == wire
    diagnostics = {"provider_diagnostic": "Stop hook rejected the structured candidate"}
    _classify_cli_oversized_input(default_request(AgentCommand.OBSERVER), diagnostics)
    assert "provider_failure" not in diagnostics
    assert "provider_failure_hint" not in diagnostics


def test_cli_oversized_stateful_attempt_observation_keeps_infrastructure_retry(tmp_path: Path) -> None:
    from hypoloom.agent_context import agent_failure_stage, agent_retry_class

    host = _host(tmp_path)
    output = json.dumps({"type": "result", "subtype": "success", "is_error": True,
        "result": "Prompt is too long", "num_turns": 1, "total_cost_usd": 0.25,
        "usage": {"input_tokens": 17, "output_tokens": 9}})
    observation = host.wait(_start(host, mode="diagnostic", extra_environment={
        "STUB_STDOUT_DIAGNOSTIC": output,
        "STUB_DIAGNOSTIC": ""}))
    assert observation.terminal_reason is AgentHostTerminalReason.FAILED
    assert observation.failure_code == "nonzero_exit"
    assert observation.diagnostics["provider_diagnostic"] == "Prompt is too long"
    assert "provider_failure" not in observation.diagnostics
    assert observation.diagnostics["provider_failure_hint"] == {
        "code": "input_too_long", "source": "cli_compaction"}
    details = {"diagnostics": observation.diagnostics}
    assert agent_retry_class(observation.failure_code, details, retryable=True) == "INFRASTRUCTURE"
    assert agent_failure_stage(observation.failure_code, details) == "provider_process"


def test_nonzero_provider_result_preserves_reported_accounting(tmp_path: Path) -> None:
    host = _host(tmp_path)
    output = json.dumps(
        {
            "type": "result",
            "subtype": "error_max_budget_usd",
            "is_error": True,
            "num_turns": 2,
            "total_cost_usd": 0.108265,
            "usage": {
                "input_tokens": 7,
                "cache_creation_input_tokens": 13,
                "cache_read_input_tokens": 17,
                "output_tokens": 11,
            },
            "modelUsage": {"stub-model": {}},
        },
        separators=(",", ":"),
    )
    observation = host.wait(
        _start(
            host,
            mode="diagnostic",
            extra_environment={
                "STUB_STDOUT_DIAGNOSTIC": output,
                "STUB_DIAGNOSTIC": "",
            },
        )
    )
    assert observation.terminal_reason is AgentHostTerminalReason.FAILED
    assert observation.failure_code == "nonzero_exit"
    assert observation.diagnostics["adapter_retry_policy"] == {
        "structured_output_failed_validation_cap": 1,
        "transport_max_retries": 0,
    }
    assert observation.diagnostics["provider_accounting"] == {
        "token_usage": {
            "input_tokens": 37,
            "output_tokens": 11,
            "total_tokens": 48,
        },
        "turn_count": 2,
        "cost": "0.108265",
        "cost_currency": "USD",
    }


def test_nonzero_provider_result_retains_only_safe_bounded_failure_text(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path)
    output = json.dumps(
        {
            "type": "result",
            "subtype": "error_max_structured_output_retries",
            "is_error": True,
            "result": "Failed to provide valid structured output after 1 attempts",
            "num_turns": 0,
            "total_cost_usd": 0,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "modelUsage": {},
        },
        separators=(",", ":"),
    )
    observation = host.wait(
        _start(
            host,
            mode="diagnostic",
            extra_environment={
                "STUB_STDOUT_DIAGNOSTIC": output,
                "STUB_DIAGNOSTIC": "",
            },
        )
    )

    assert observation.failure_code == "nonzero_exit"
    assert observation.diagnostics["provider_subtype"] == (
        "error_max_structured_output_retries"
    )
    assert observation.diagnostics["provider_diagnostic"] == (
        "Failed to provide valid structured output after 1 attempts"
    )


@pytest.mark.parametrize("top_level_usage", (None, {"input_tokens": 0, "output_tokens": 0}))
def test_error_accounting_falls_back_to_exact_model_usage_counting_separate_cache_buckets(
    tmp_path: Path, top_level_usage: object
) -> None:
    host = _host(tmp_path)
    envelope: dict[str, object] = {
        "type": "result",
        "subtype": "error_max_budget_usd",
        "is_error": True,
        "num_turns": 2,
        "total_cost_usd": 0.31,
        "modelUsage": {
            "stub-model": {
                "inputTokens": 56_500,
                "outputTokens": 250,
                "cacheReadInputTokens": 40_000,
                "cacheCreationInputTokens": 5_000,
            }
        },
    }
    if top_level_usage is not None:
        envelope["usage"] = top_level_usage
    observation = host.wait(
        _start(
            host,
            mode="diagnostic",
            extra_environment={
                "STUB_STDOUT_DIAGNOSTIC": json.dumps(envelope, separators=(",", ":")),
                "STUB_DIAGNOSTIC": "",
            },
        )
    )

    assert observation.terminal_reason is AgentHostTerminalReason.FAILED
    assert observation.failure_code == "nonzero_exit"
    assert observation.diagnostics["provider_accounting"] == {
        "token_usage": {
            "input_tokens": 101_500,
            "output_tokens": 250,
            "total_tokens": 101_750,
        },
        "turn_count": 2,
        "cost": "0.31",
        "cost_currency": "USD",
    }


@pytest.mark.parametrize(
    "model_usage",
    (
        {"other-model": {"inputTokens": 7, "outputTokens": 11}},
        {
            "stub-model": {"inputTokens": 7, "outputTokens": 11},
            "other-model": {"inputTokens": 13, "outputTokens": 17},
        },
        {"stub-model": {"inputTokens": "7", "outputTokens": 11}},
    ),
)
def test_error_accounting_does_not_fall_back_to_ambiguous_or_invalid_model_usage(
    tmp_path: Path, model_usage: object
) -> None:
    host = _host(tmp_path)
    output = json.dumps(
        {
            "type": "result",
            "subtype": "error_max_budget_usd",
            "is_error": True,
            "num_turns": 2,
            "total_cost_usd": 0.31,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "modelUsage": model_usage,
        },
        separators=(",", ":"),
    )
    observation = host.wait(
        _start(
            host,
            mode="diagnostic",
            extra_environment={
                "STUB_STDOUT_DIAGNOSTIC": output,
                "STUB_DIAGNOSTIC": "",
            },
        )
    )

    assert observation.terminal_reason is AgentHostTerminalReason.FAILED
    assert observation.failure_code == "nonzero_exit"
    assert "provider_accounting" not in observation.diagnostics


def test_cache_tokens_are_normalized_into_input_and_total_usage(tmp_path: Path) -> None:
    host = _host(tmp_path)
    output = _provider_output(
        usage={
            "input_tokens": 7,
            "cache_creation_input_tokens": 13,
            "cache_read_input_tokens": 17,
            "output_tokens": 11,
            "total_tokens": 18,
        }
    )
    observation = host.wait(
        _start(host, extra_environment={"STUB_VALID_OUTPUT": output})
    )
    assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED
    assert observation.response is not None
    assert observation.response.token_usage == {
        "input_tokens": 37,
        "output_tokens": 11,
        "total_tokens": 48,
    }
    assert observation.response.diagnostics["cache_creation_input_tokens"] == 13
    assert observation.response.diagnostics["cache_read_input_tokens"] == 17


def test_cache_tokens_can_exceed_total_token_budget(tmp_path: Path) -> None:
    host = _host(tmp_path)
    output = _provider_output(
        usage={
            "input_tokens": 7,
            "cache_creation_input_tokens": 300,
            "cache_read_input_tokens": 300,
            "output_tokens": 11,
            "total_tokens": 18,
        }
    )
    observation = host.wait(
        _start(host, extra_environment={"STUB_VALID_OUTPUT": output})
    )
    assert observation.terminal_reason is AgentHostTerminalReason.INVALID_OUTPUT
    assert observation.failure_code == "token_budget_exceeded"
    assert observation.response is None
    assert observation.diagnostics["provider_accounting"]["token_usage"] == {
        "input_tokens": 607,
        "output_tokens": 11,
        "total_tokens": 618,
    }
    assert observation.diagnostics["uncached_input_tokens"] == 7
    assert observation.diagnostics["cache_creation_input_tokens"] == 300
    assert observation.diagnostics["cache_read_input_tokens"] == 300


def test_observe_mode_keeps_successful_provider_response_over_token_limit(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path)
    request = _claude_request()
    request = request.__class__.from_dict(
        {
            **request.to_dict(),
            "budget": {
                **request.budget.to_dict(),
                "token_enforcement_mode": "observe",
            },
        }
    )
    output = _provider_output(
        usage={
            "input_tokens": 7,
            "cache_creation_input_tokens": 300,
            "cache_read_input_tokens": 300,
            "output_tokens": 11,
            "total_tokens": 18,
        }
    )

    observation = host.wait(
        _start(
            host,
            request=request,
            extra_environment={"STUB_VALID_OUTPUT": output},
        )
    )

    assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED
    assert observation.failure_code is None
    assert observation.response is not None
    assert observation.response.token_usage == {
        "input_tokens": 607,
        "output_tokens": 11,
        "total_tokens": 618,
    }
    assert observation.response.token_usage["total_tokens"] > request.budget.max_tokens


def test_transport_output_is_capped_while_provider_is_running(tmp_path: Path) -> None:
    request = _claude_request()
    request = request.__class__.from_dict(
        {
            **request.to_dict(),
            "budget": {
                **request.budget.to_dict(),
                "max_output_bytes": 1024,
                "wall_seconds": 0.2,
            },
        }
    )
    # Exercise the OS output cap before advancing the invocation clock. A
    # loaded machine need not produce one MiB within the 0.2-second deadline.
    frozen = time.monotonic()
    timeout_started = None

    def clock():
        return (frozen if timeout_started is None else
                frozen + request.budget.wall_seconds + time.monotonic() - timeout_started)

    host = _host(tmp_path, clock=clock)
    handle = _start(host, mode="flood", request=request)
    try:
        ready_deadline = time.monotonic() + 5
        while os.fstat(handle._stdout_file.fileno()).st_size <= 1024 * 1024:
            assert handle.returncode is None
            assert time.monotonic() < ready_deadline, "provider did not reach its output cap"
            time.sleep(0.01)
        assert handle.returncode is None, "the output cap must hold while the provider runs"
        timeout_started = time.monotonic()
        observation = host.wait(handle)
        elapsed = time.monotonic() - timeout_started
    finally:
        # Also advance the clock if the readiness assertion failed, so cleanup
        # retains its ordinary finite terminate/kill grace periods.
        if timeout_started is None:
            timeout_started = time.monotonic()
        host.cleanup(handle)
    assert elapsed < 1.5
    assert observation.terminal_reason is AgentHostTerminalReason.TIMED_OUT
    assert observation.failure_code == "timed_out"
    assert observation.diagnostics["stdout_bytes"] > 1024 * 1024
    assert observation.diagnostics["stdout_bytes"] <= 1024 * 1024 + 4096


def test_cancel_kills_process_group_and_descendants(tmp_path: Path) -> None:
    host = _host(tmp_path)
    handle = _start(host, mode="descendants")
    assert _await_group_size(handle.process_group_id, at_least=2)
    owned = _group_pids(handle.process_group_id)
    assert host.cancel(handle)
    observation = host.wait(handle)
    assert observation.terminal_reason is AgentHostTerminalReason.CANCELLED
    assert _await_not_live(owned)
    assert not process_group_exists(handle.process_group_id)


def test_timeout_kills_owned_process_group(tmp_path: Path) -> None:
    host = _host(tmp_path)
    request = _claude_request()
    request = request.__class__.from_dict(
        {
            **request.to_dict(),
            "budget": {**request.budget.to_dict(), "wall_seconds": 0.2},
        }
    )
    handle = host.start(
        request,
        model="stub-model",
        provider_environment={"STUB_MODE": "hang", "STUB_VALID_OUTPUT": _VALID_OUTPUT},
        bind_process_group=lambda binding: None,
    )
    observation = host.wait(handle)
    assert observation.terminal_reason is AgentHostTerminalReason.TIMED_OUT
    assert not process_group_exists(handle.process_group_id)


@pytest.mark.parametrize("observer", ("poll", "wait"))
def test_success_observed_after_invocation_wallclock_is_timed_out(
    tmp_path: Path,
    observer: str,
) -> None:
    host = _host(tmp_path)
    request = _claude_request()
    request = request.__class__.from_dict({
        **request.to_dict(),
        "budget": {**request.budget.to_dict(), "wall_seconds": 0.2},
    })
    handle = _start(host, mode="delayed", request=request)
    time.sleep(0.5)
    assert handle.returncode == 0
    observation = getattr(host, observer)(handle)
    assert observation is not None
    assert observation.terminal_reason is AgentHostTerminalReason.TIMED_OUT
    assert observation.failure_code == "timed_out"
    assert observation.response is None


@pytest.mark.parametrize(
    ("mode", "reason", "code", "returncode"),
    (
        ("malformed", AgentHostTerminalReason.INVALID_OUTPUT, "malformed_json", 0),
        ("nonzero", AgentHostTerminalReason.FAILED, "nonzero_exit", 23),
    ),
)
def test_malformed_and_nonzero_are_typed_observations(
    tmp_path: Path,
    mode: str,
    reason: AgentHostTerminalReason,
    code: str,
    returncode: int,
) -> None:
    host = _host(tmp_path)
    observation = host.wait(_start(host, mode=mode))
    assert observation.terminal_reason is reason
    assert observation.failure_code == code
    assert observation.returncode == returncode
    assert observation.response is None


def test_failed_provider_keeps_bounded_stderr_without_stdout_transcript(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path)
    diagnostic = "provider stderr: " + "x" * (64 * 1024)
    stdout_diagnostic = "provider stdout: " + "y" * (64 * 1024)
    observation = host.wait(
        _start(
            host,
            mode="diagnostic",
            extra_environment={
                "STUB_DIAGNOSTIC": diagnostic,
                "STUB_STDOUT_DIAGNOSTIC": stdout_diagnostic,
            },
        )
    )
    assert observation.terminal_reason is AgentHostTerminalReason.FAILED
    assert observation.failure_code == "nonzero_exit"
    assert "stdout_excerpt" not in observation.diagnostics
    stderr_excerpt = observation.diagnostics["stderr_excerpt"]
    assert isinstance(stderr_excerpt, str)
    assert stderr_excerpt.startswith("provider stderr: ")
    assert len(stderr_excerpt.encode("utf-8")) <= 64 * 1024
    assert stderr_excerpt != diagnostic


def test_failed_provider_diagnostic_excerpt_replaces_invalid_utf8(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path)
    observation = host.wait(_start(host, mode="invalid_diagnostic"))
    assert observation.terminal_reason is AgentHostTerminalReason.FAILED
    assert observation.diagnostics["stderr_excerpt"] == "�invalid provider stderr"


def test_settings_are_exact_read_only_and_host_paths_are_absent(tmp_path: Path) -> None:
    host = _host(tmp_path)
    forbidden = tmp_path / "host-state.sqlite"
    forbidden.write_text("authority", encoding="utf-8")
    observation = host.wait(
        _start(host, mode="probe", extra_environment={"HOST_FORBIDDEN": str(forbidden)})
    )
    assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED
    assert observation.response is not None
    assert observation.response.typed_candidate["claim"] == "true:false:false:true:true"
    assert forbidden.read_text(encoding="utf-8") == "authority"


def test_embedded_text_is_rendered_from_the_canonical_request_only(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path)
    request = _claude_request()
    bound_text = "bounded Goal context 42"
    request = request.__class__.from_dict(
        {
            **request.to_dict(),
            "input_artifacts": [
                EmbeddedInput.from_bytes(
                    "goal-context", "text/plain", bound_text.encode("utf-8")
                ).to_dict()
            ],
        }
    )
    observation = host.wait(
        _start(
            host,
            mode="prompt",
            request=request,
            extra_environment={
                "EXPECTED_TEXT": bound_text,
                "FORBIDDEN_TEXT": "unbound hidden context",
            },
        )
    )
    assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED
    assert observation.response is not None
    assert observation.response.typed_candidate["claim"] == "true:false"


def test_prompt_requires_rendered_input_constraints(tmp_path: Path) -> None:
    host = _host(tmp_path)
    observation = host.wait(
        _start(
            host,
            mode="prompt",
            extra_environment={
                "EXPECTED_TEXT": "Copy constrained field values and array entries exactly; do not paraphrase them.",
                "FORBIDDEN_TEXT": "unbound hidden context",
            },
        )
    )
    assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED
    assert observation.response is not None
    assert observation.response.typed_candidate["claim"] == "true:false"


def test_repeated_cleanup_does_not_leak_host_file_descriptors(tmp_path: Path) -> None:
    host = _host(tmp_path)
    warmup = host.wait(_start(host))
    assert warmup.terminal_reason is AgentHostTerminalReason.SUCCEEDED
    before = len(tuple(Path("/proc/self/fd").iterdir()))
    for _ in range(8):
        observation = host.wait(_start(host))
        assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED
    after = len(tuple(Path("/proc/self/fd").iterdir()))
    assert after == before


def test_partial_pipe_allocation_failure_leaks_no_file_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _host(tmp_path)
    real_pipe = os.pipe
    calls = 0

    def fail_second_pipe() -> tuple[int, int]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic EMFILE")
        return real_pipe()

    monkeypatch.setattr("hypoloom.claude_agent_host.os.pipe", fail_second_pipe)
    before = len(tuple(Path("/proc/self/fd").iterdir()))
    with pytest.raises(AgentHostError, match="Agent provider launch failed") as error:
        _start(host)
    assert isinstance(error.value.__cause__, OSError)
    assert str(error.value.__cause__) == "synthetic EMFILE"
    after = len(tuple(Path("/proc/self/fd").iterdir()))
    assert calls == 2
    assert after == before


def test_agent_host_has_no_database_or_state_store_authority() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "hypoloom"
        / "claude_agent_host.py"
    ).read_text(encoding="utf-8")
    assert "sqlite3" not in source
    assert "state_store" not in source


def test_parent_death_guardian_cleans_gated_or_running_group(tmp_path: Path) -> None:
    host = _host(tmp_path)
    worker = tmp_path / "parent_worker.py"
    worker.write_text(
        f"""from pathlib import Path
import time
from hypoloom.claude_agent_host import ClaudeAgentHost
from hypoloom.fake_agent import default_request
host = ClaudeAgentHost(
    claude_executable={str(host.claude_executable)!r},
    settings_path={str(host.settings_path)!r},
    allowed_environment_names=('STUB_MODE','STUB_VALID_OUTPUT'),
    extra_runtime_paths=('/bin/sh',),
)
handle = host.start(
    default_request().__class__.from_dict({{**default_request().to_dict(), 'tool_permission_profile':{CLAUDE_AGENT_TOOL_PERMISSION_PROFILE!r}, 'required_model_identity':'stub-model', 'required_adapter_version':{CLAUDE_AGENT_HOST_VERSION!r}}}), model='stub-model',
    provider_environment={{'STUB_MODE':'descendants','STUB_VALID_OUTPUT':{_VALID_OUTPUT!r}}},
    bind_process_group=lambda binding: Path({str(tmp_path / "owned-pgid")!r}).write_text(str(binding.process_group_id)),
)
while True: time.sleep(1)
""",
        encoding="utf-8",
    )
    parent = subprocess.Popen(
        [sys.executable, str(worker)],
        cwd=tmp_path,
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        pgid_path = tmp_path / "owned-pgid"
        deadline = time.monotonic() + 5
        while not pgid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pgid_path.exists()
        pgid = int(pgid_path.read_text(encoding="ascii"))
        assert _await_group_size(pgid, at_least=2)
        owned = _group_pids(pgid)
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait(timeout=3)
        assert _await_not_live(owned, timeout=5)
    finally:
        if parent.poll() is None:
            os.kill(parent.pid, signal.SIGKILL)
            parent.wait(timeout=3)


def _group_pids(pgid: int) -> tuple[int, ...]:
    result: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "stat").read_text(encoding="utf-8")
        except OSError:
            continue
        fields = raw[raw.rfind(")") + 2 :].split()
        if len(fields) > 2 and int(fields[2]) == pgid and fields[0] != "Z":
            result.append(int(entry.name))
    return tuple(result)


def _await_group_size(pgid: int, *, at_least: int, timeout: float = 3) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(_group_pids(pgid)) >= at_least:
            return True
        time.sleep(0.02)
    return len(_group_pids(pgid)) >= at_least


def _await_not_live(pids: tuple[int, ...], timeout: float = 3) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        live = set(_group_pids_of(pids))
        if not live:
            return True
        time.sleep(0.02)
    return not _group_pids_of(pids)


def _group_pids_of(pids: tuple[int, ...]) -> tuple[int, ...]:
    live: list[int] = []
    for pid in pids:
        try:
            raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        except OSError:
            continue
        fields = raw[raw.rfind(")") + 2 :].split()
        if not fields or fields[0] != "Z":
            live.append(pid)
    return tuple(live)
