from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import pytest

import hypoloom.workflow_host as workflow_host_module
from hypoloom.artifacts import ArtifactDurabilityError
from hypoloom.agent_aci import (
    AgentCommand,
    AgentACIError,
    AgentResponse,
    WorkspaceArtifact,
    with_output_digest,
)
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import ResearchContract
from hypoloom.fake_agent import FakeAgent
from hypoloom.oracle import Oracle
from hypoloom.outcome_adapter import OUTCOME_ADAPTER_VERSION
from hypoloom.profile_admission import admit_profile
from hypoloom.runner import ProcessRequest
from hypoloom.state_store import StateTransaction
from hypoloom.workflow_host import WorkflowHost


REPOSITORY = Path(__file__).resolve().parents[2]
METHOD_FAMILY = "fake-family-v1"
_IMPLEMENTATION_ARTIFACTS = (
    "implementation_patch",
    "runner_config",
    "integration_notes",
    "candidate_manifest",
    "dual_dataset_design",
)
_EXTRA_IMPLEMENTATION_FILE = "multiemo_causal_candidate.py"
_EXTRA_IMPLEMENTATION_BYTES = b"ordinary intermediate source\n"
_NONAPPLYING_IMPLEMENTATION_PATCH = (
    b"--- /dev/null\n"
    b"+++ b/src/merc/models/multiemo_causal_candidate.py\n"
    b"@@ -0,0 +1,8 @@\n"
    b"+line one\n"
    b"+line two\n"
    b"+line three\n"
    b"+line four\n"
    b"+line five\n"
    b"+line six\n"
    b"+line seven\n"
)
_EXTRA_IMPLEMENTATION_DIAGNOSTIC = (
    "artifact_inventory_mismatch@artifact_dir: "
    "undeclared=['multiemo_causal_candidate.py'], missing=[]"
)
_PREFLIGHT_REVIEW_DIAGNOSTIC = (
    "Preflight diagnostic: candidate hunk declares eight added lines but contains seven."
)
_ANALYZE_REVIEW_DIAGNOSTIC = (
    "Analyze diagnostic: multiemo_causal_candidate.py is undeclared beside result.json."
)
_UNBOUND_IDENTITY_DIAGNOSTIC = (
    "invalid_identity@result.json.identity: digests must be 64 lowercase hex"
)


@pytest.fixture
def mount_fixture_root() -> Path:
    # bubblewrap deliberately reserves /tmp for its private tmpfs, so Host
    # read-only bindings need disposable source paths outside that namespace.
    with tempfile.TemporaryDirectory(
        prefix="hypoloom-component-g3-", dir="/var/tmp"
    ) as raw_root:
        root = Path(raw_root)
        try:
            yield root
        finally:
            data_root = root / "read-only-data"
            if data_root.is_dir():
                data_root.chmod(0o755)


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def _clean_target(root: Path) -> tuple[Path, str, dict[str, bytes]]:
    target = root / "target"
    (target / "src/merc/models").mkdir(parents=True)
    (target / "README.md").write_text("temporary component target\n", encoding="utf-8")
    (target / "src/merc/models/base.py").write_text(
        'MODEL_KEY = "baseline"\n', encoding="utf-8"
    )
    _git(target, "init", "-q")
    _git(target, "add", ".")
    _git(
        target,
        "-c",
        "user.name=Hypoloom Test",
        "-c",
        "user.email=hypoloom-test@example.invalid",
        "commit",
        "-qm",
        "temporary target base",
    )
    commit = _git(target, "rev-parse", "HEAD")
    tracked = {
        relative: (target / relative).read_bytes()
        for relative in _git(target, "ls-files").splitlines()
    }
    assert _git(target, "status", "--porcelain=v1") == ""
    return target.resolve(), commit, tracked


def _production_target(root: Path) -> tuple[Path, str]:
    target = root / "production-target"
    (target / "src/frozen_target").mkdir(parents=True)
    (target / "data").mkdir()
    (target / "src/frozen_target/__init__.py").write_text("", encoding="utf-8")
    (target / "data/fixture.txt").write_text("frozen fixture\n", encoding="utf-8")
    (target / "src/frozen_target/runner.py").write_text(
        """import argparse, json
from pathlib import Path

MARKER = "TARGET_EXECUTED"
parser = argparse.ArgumentParser()
parser.add_argument("--dataset", required=True)
parser.add_argument("--model", required=True)
parser.add_argument("--seed", required=True, type=int)
parser.add_argument("--out", required=True)
parser.add_argument("--fixture", required=True)
args = parser.parse_args()
assert (args.dataset, args.model, args.seed) == ("FIXTURE", "candidate-v1", 7)
assert Path(args.fixture).read_text(encoding="utf-8") == "frozen fixture\\n"
Path(args.out).write_text(json.dumps({
    "artifacts": {},
    "identity": {"method_family_id": "fake-family-v1"},
    "trials": [{"seed": 7, "metrics": {"max_score": 3}, "clean": True}],
}, sort_keys=True), encoding="utf-8")
print(MARKER, flush=True)
""",
        encoding="utf-8",
    )
    _git(target, "init", "-q")
    _git(target, "add", ".")
    _git(target, "-c", "user.name=Hypoloom Test", "-c", "user.email=hypoloom-test@example.invalid",
         "commit", "-qm", "frozen production fixture")
    return target.resolve(), _git(target, "rev-parse", "HEAD")


def _production_payload(run_id: str, target: Path, commit: str, *, bad_data: bool = False) -> dict[str, object]:
    admission = admit_profile(
        REPOSITORY / "tests" / "fixtures" / "profiles" / "component-demo", run_id=run_id
    )
    assert admission.create_run_payload is not None
    payload = copy.deepcopy(admission.create_run_payload)
    contract = payload["research_contract"]
    contract["goal"]["summary_metric"] = {
        "name": "max_score", "direction": "MAXIMIZE", "target": "3", "display_only": True,
    }
    component = contract["components"][0]
    component.update({
        "protocol": "frozen-single", "seeds": [7],
        "result": {"kind": "metric", "metric": "max_score", "direction": "MAXIMIZE",
                   "target": "3", "dataset": "FIXTURE"},
    })
    requirements = {
        "required_metrics": ["max_score"], "required_artifacts": [],
        "required_paths": [
            {"scope": "target", "path": "src/frozen_target/runner.py", "kind": "file"},
            {"scope": "target", "path": "data/fixture.txt", "kind": "file"},
        ],
        "required_cli_flags": ["--dataset", "--model", "--seed", "--out", "--fixture"],
        "required_source_literals": ["TARGET_EXECUTED"],
        "argv_bindings": [{"flag": "--fixture", "role": "fixture", "scope": "target",
                           "path": "data/fixture.txt", "kind": "file", "absolute": True,
                           "overlay_policy": "immutable_data"}],
    }
    if bad_data:
        requirements["data_checks"] = [{"type": "hdf5_split_manifest", "dataset": "FIXTURE",
                                         "cache": "data/missing.h5", "manifest": "data/missing.json"}]
    contract["protocols"] = {"frozen-single": {"result_kind": "metric", "requirements": requirements}}
    contract["delivery_contracts"] = {
        "enabled": True, "schema_version": 1,
        "protocols": {"frozen-single": {
            "production": True, "execution_shape": "single_trial",
            "capabilities": {
                "entrypoint": {"role": "entrypoint", "path": "src/frozen_target/runner.py",
                               "module": "frozen_target.runner", "required_literals": ["TARGET_EXECUTED"]},
                "selection": {"role": "registry", "selection": {
                    "argv_flag": "--model", "expected_values": ["candidate-v1"]}},
            },
            "proof_obligations": [],
        }},
    }
    runtime = Path(sys.prefix).resolve()
    contract["runtime"] = {"target_repo": str(target), "conda_env": str(runtime)}
    payload["execution"] = {"allow_gpu": False, "delay_seconds": 0.0,
                            "target": {"checkout": str(target), "commit": commit},
                            "runtime_prefix": str(runtime), "read_roots": []}
    return payload


def _production_design_payload(run_id: str, target: Path, commit: str) -> dict[str, object]:
    payload = _production_payload(run_id, target, commit)
    contract = payload["research_contract"]
    contract["methodology"].setdefault("controls", {})["research_loop"] = "expert-six-node/1"
    for role in contract["roles"]:
        role.setdefault("budget", {})["max_papers"] = 8
    contract["budget"]["max_retries"] = 1
    return payload


class _ForbiddenWrapperAgent(FakeAgent):
    def invoke(self, request):
        response = super().invoke(request)
        if request.command is AgentCommand.ANSWER_RENDEZVOUS:
            by_ref = {item.ref: item for item in request.input_artifacts}
            source_ref = next(ref for ref in by_ref if ref.endswith("--experiment.py"))
            assert by_ref[source_ref].content_bytes()
            if any("--spec-validation-" in ref for ref in by_ref):
                validation_ref = next(ref for ref in by_ref if ref.endswith("--observation"))
                assert json.loads(by_ref[validation_ref].content_bytes())["validation_version"] == "candidate-compile/3"
            else:
                assert any(ref.endswith("--stdout") for ref in by_ref)
                assert any(ref.endswith("--stderr") for ref in by_ref)
                evaluation_ref = next(
                    ref for ref in by_ref if ref.endswith("--evaluation")
                )
                evaluation = json.loads(by_ref[evaluation_ref].content_bytes())
                assert evaluation["outcome"] in {
                    "SUCCESS", "CLEAN_REFUTATION", "EXECUTION_FAILURE",
                    "INTEGRITY_REJECTED", "CANCELLED",
                }
                assert "validation_error" in evaluation
                assert "execution_integrity_error" in evaluation
            return response
        if request.command is not AgentCommand.DRAFT_EXPERIMENT_SPEC:
            return response
        artifact = WorkspaceArtifact.from_bytes(
            "experiment.py", b'raise AssertionError("Agent wrapper executed")\n'
        )
        return with_output_digest(replace(response, proposed_workspace_artifacts=(artifact,), output_digest=""))


def _revision_payload(run_id: str, *, max_experiments: int = 2) -> dict[str, object]:
    admission = admit_profile(
        REPOSITORY / "tests" / "fixtures" / "profiles" / "component-demo", run_id=run_id
    )
    assert admission.create_run_payload is not None
    payload = copy.deepcopy(admission.create_run_payload)
    payload["research_contract"]["budget"].update({
        "max_experiments": max_experiments,
        "max_retries": 1,
        "experiment_timeout": 10,
    })
    runtime = str(Path(sys.prefix).resolve())
    payload["research_contract"]["runtime"] = {"conda_env": runtime}
    payload["execution"]["runtime_prefix"] = runtime
    return payload


def _cross_family_inconclusive_payload(run_id: str) -> dict[str, object]:
    payload = _revision_payload(run_id, max_experiments=4)
    contract = payload["research_contract"]
    contract["goal"] = {
        "objective": "Settle a cross-family component conjunction.",
        "language": "",
        "verdict_rule": "required_conjunction",
        "summary_metric": {
            "name": "required_component_completion",
            "direction": "MAXIMIZE",
            "target": "4",
            "display_only": True,
        },
    }
    metric_result = {
        "kind": "metric",
        "metric": "score",
        "direction": "MAXIMIZE",
        "target": "3",
    }

    def metric_component(
        component_id: str,
        owner: str,
        *,
        tier: str = "must",
        dependencies: tuple[str, ...] = (),
        required_inputs: tuple[dict[str, str], ...] = (),
    ) -> dict[str, object]:
        return {
            "id": component_id,
            "tier": tier,
            "name": component_id.split(".", 1)[1],
            "owner": owner,
            "proposers": [owner],
            "dependencies": list(dependencies),
            "required_inputs": list(required_inputs),
            "protocol": "metric-only",
            "result": metric_result,
            "seeds": [],
            "required_artifacts": [],
        }

    contract["components"] = [
        metric_component("must.search-a", "scientist-a"),
        metric_component("must.search-b", "scientist-b"),
        {
            "id": "must.implementation",
            "tier": "must",
            "name": "implementation",
            "owner": "scientist-a",
            "proposers": ["scientist-a"],
            "dependencies": ["must.search-a", "must.search-b"],
            "required_inputs": [],
            "protocol": "implementation",
            "result": metric_result,
            "seeds": [],
            "required_artifacts": ["candidate_manifest"],
        },
        metric_component(
            "primary.final",
            "scientist-a",
            tier="primary",
            dependencies=("must.implementation",),
            required_inputs=(
                {
                    "resource": "implementation-candidate",
                    "produced_by": "must.implementation",
                    "artifact": "candidate_manifest",
                },
            ),
        ),
    ]
    contract["protocols"] = {
        "metric-only": {
            "result_kind": "metric",
            "requirements": {
                "required_metrics": ["score"],
            },
        },
        "implementation": {
            "result_kind": "metric",
            "requirements": {
                "required_metrics": ["score"],
                "required_artifacts": ["candidate_manifest"],
            },
        },
    }
    return payload


class _CrossFamilyInconclusiveAgent(FakeAgent):
    _families = {
        "must.search-a": "search-family-a",
        "must.search-b": "search-family-b",
        "must.implementation": "selected-family",
        "primary.final": "selected-family",
    }

    def invoke(self, request):
        response = super().invoke(request)
        assert isinstance(response, AgentResponse)
        if request.command is AgentCommand.PROPOSE_HYPOTHESIS:
            candidate = dict(response.typed_candidate or {})
            candidate["method_family_id"] = self._families[request.component_id]
            return with_output_digest(
                replace(response, typed_candidate=candidate, output_digest="")
            )
        if (
            request.command is AgentCommand.DRAFT_EXPERIMENT_SPEC
            and request.component_id == "must.implementation"
        ):
            assert len(response.proposed_workspace_artifacts) == 1
            source = response.proposed_workspace_artifacts[0].content_bytes()
            assert source.count(b'"score": 3') == 1
            artifact = WorkspaceArtifact.from_bytes(
                "experiment.py", source.replace(b'"score": 3', b'"score": 2')
            )
            return with_output_digest(
                replace(
                    response,
                    proposed_workspace_artifacts=(artifact,),
                    output_digest="",
                )
            )
        return response


def _text_contract_revision_payload(run_id: str) -> dict[str, object]:
    payload = _revision_payload(run_id)
    contract = payload["research_contract"]
    contract["goal"]["summary_metric"] = {
        "name": "required_component_completion",
        "direction": "MAXIMIZE",
        "target": "1",
        "display_only": True,
    }
    component = contract["components"][0]
    component.update({
        "protocol": "text-report",
        "result": {"kind": "artifact", "evaluation_kind": "artifact_check"},
        "required_artifacts": ["mechanism_search", "dual_dataset_design"],
    })
    contract["protocols"] = {
        "text-report": {
            "result_kind": "artifact",
            "requirements": {
                "required_artifacts": [
                    "mechanism_search", "dual_dataset_design",
                ],
                "artifact_validators": [
                    {
                        "artifact": "mechanism_search",
                        "type": "text_contract",
                        "min_chars": 32,
                        "required_terms": ["MELD", "IEMOCAP", "tail class"],
                    },
                    {
                        "artifact": "dual_dataset_design",
                        "type": "text_contract",
                        "min_chars": 32,
                        "required_terms": ["MELD", "IEMOCAP", "shared mechanism"],
                    },
                ],
            },
        }
    }
    return payload


def _artifact_inventory_revision_payload(
    run_id: str, target: Path, commit: str
) -> dict[str, object]:
    payload = _revision_payload(run_id)
    contract = payload["research_contract"]
    contract["goal"]["summary_metric"] = {
        "name": "required_component_completion",
        "direction": "MAXIMIZE",
        "target": "1",
        "display_only": True,
    }
    component = contract["components"][0]
    component.update({
        "protocol": "implementation-artifact",
        "result": {"kind": "artifact", "evaluation_kind": "artifact_check"},
        "required_artifacts": list(_IMPLEMENTATION_ARTIFACTS),
    })
    contract["protocols"] = {
        "implementation-artifact": {
            "result_kind": "artifact",
            "requirements": {
                "required_artifacts": list(_IMPLEMENTATION_ARTIFACTS),
                "artifact_validators": [
                    {
                        "artifact": "implementation_patch",
                        "type": "unified_diff",
                        "apply_check": True,
                    },
                    {
                        "artifact": "runner_config",
                        "type": "json_contract",
                        "required_paths": ["method_family_id", "datasets"],
                    },
                    {
                        "artifact": "integration_notes",
                        "type": "text_contract",
                        "min_chars": 64,
                        "required_terms": ["implementation_patch", "runner"],
                    },
                    {
                        "artifact": "candidate_manifest",
                        "type": "json_contract",
                        "required_paths": [
                            "method_family_id",
                            "candidates.MELD.method_family_id",
                            "candidates.IEMOCAP.method_family_id",
                        ],
                        "equal_groups": [[
                            "method_family_id",
                            "candidates.MELD.method_family_id",
                            "candidates.IEMOCAP.method_family_id",
                        ]],
                    },
                    {
                        "artifact": "dual_dataset_design",
                        "type": "text_contract",
                        "min_chars": 64,
                        "required_terms": ["MELD", "IEMOCAP", "shared mechanism"],
                    },
                ],
            },
        }
    }
    contract["methodology"]["controls"]["experiment_campaigns"] = {
        "enabled": True,
        "required_roles": ["preflight", "analyze"],
        "role_owners": {
            "*": {"preflight": "scientist-b", "analyze": "scientist-c"}
        },
    }
    runtime = str(Path(sys.prefix).resolve())
    contract["runtime"] = {"target_repo": str(target), "conda_env": runtime}
    payload["execution"].update({
        "target": {"checkout": str(target), "commit": commit},
        "runtime_prefix": runtime,
        "read_roots": [],
    })
    return payload


class _CompileRepairAgent(FakeAgent):
    def __init__(self) -> None:
        super().__init__(); self.draft_requests = []

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is not AgentCommand.DRAFT_EXPERIMENT_SPEC:
            return response
        self.draft_requests.append(request)
        if len(self.draft_requests) == 1:
            artifact = WorkspaceArtifact.from_bytes("experiment.py", b"def broken(:\n")
            return with_output_digest(replace(response, proposed_workspace_artifacts=(artifact,), output_digest=""))
        refs = {item.ref for item in request.input_artifacts}
        assert any(ref.endswith("--previous-experiment.py") for ref in refs)
        assert any(ref.endswith("--validation-diagnostics") for ref in refs)
        return response


class _ResultReferenceRepairAgent(FakeAgent):
    def __init__(self) -> None:
        super().__init__(); self.draft_requests = []

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is not AgentCommand.DRAFT_EXPERIMENT_SPEC:
            return response
        self.draft_requests.append(request)
        if len(self.draft_requests) == 1:
            artifact = WorkspaceArtifact.from_bytes(
                "experiment.py", b"print('candidate without any result write')\n"
            )
            return with_output_digest(replace(response, proposed_workspace_artifacts=(artifact,), output_digest=""))
        refs = {item.ref for item in request.input_artifacts}
        assert any(ref.endswith("--previous-experiment.py") for ref in refs)
        assert any(ref.endswith("--validation-diagnostics") for ref in refs)
        return response


class _PatchCompileRepairAgent(FakeAgent):
    """First design patches a frozen target source into invalid Python."""

    def __init__(self) -> None:
        super().__init__(); self.design_requests = []

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            return response
        self.design_requests.append(request)
        if len(self.design_requests) != 1:
            refs = {item.ref for item in request.input_artifacts}
            assert any(ref.endswith("--previous-experiment.py") for ref in refs)
            assert any(ref.endswith("--previous-implementation.patch") for ref in refs)
            assert any(ref.endswith("--validation-diagnostics") for ref in refs)
            return response
        artifacts = {
            artifact.path: artifact
            for artifact in response.proposed_workspace_artifacts
        }
        broken_patch = (
            b"diff --git a/src/frozen_target/runner.py b/src/frozen_target/runner.py\n"
            b"--- a/src/frozen_target/runner.py\n"
            b"+++ b/src/frozen_target/runner.py\n"
            b"@@ -1,5 +1,6 @@\n"
            b" import argparse, json\n"
            b" from pathlib import Path\n"
            b"+  BROKEN = True\n"
            b" \n"
            b' MARKER = "TARGET_EXECUTED"\n'
            b" parser = argparse.ArgumentParser()\n"
        )
        return with_output_digest(replace(
            response,
            proposed_workspace_artifacts=(
                artifacts["experiment.py"],
                WorkspaceArtifact.from_bytes("implementation.patch", broken_patch),
            ),
            output_digest="",
        ))


class _RuntimeRepairAgent(FakeAgent):
    def __init__(self) -> None:
        super().__init__(); self.draft_requests = []

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is not AgentCommand.DRAFT_EXPERIMENT_SPEC:
            return response
        self.draft_requests.append(request)
        if len(self.draft_requests) == 1:
            artifact = WorkspaceArtifact.from_bytes(
                "experiment.py",
                b"import os\n"
                b"result_path = os.environ['HYPOLOOM_RESULT_PATH']\n"
                b"raise RuntimeError('first candidate failed')\n",
            )
            return with_output_digest(replace(response, proposed_workspace_artifacts=(artifact,), output_digest=""))
        by_ref = {item.ref: item for item in request.input_artifacts}
        refs = set(by_ref)
        assert any(ref.endswith("--predecessor-experiment.py") for ref in refs)
        assert any(ref.endswith("--predecessor-process") for ref in refs)
        stderr_ref = next(
            ref for ref in refs if ref.endswith("--predecessor-logs-stderr.log")
        )
        assert by_ref[stderr_ref].content_bytes()
        return response


class _ResultEnvelopeRepairAgent(FakeAgent):
    def __init__(self) -> None:
        super().__init__(); self.draft_requests = []

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is not AgentCommand.DRAFT_EXPERIMENT_SPEC:
            return response
        self.draft_requests.append(request)
        if len(self.draft_requests) == 1:
            artifact = WorkspaceArtifact.from_bytes(
                "experiment.py",
                b"import os\nfrom pathlib import Path\n"
                b"Path(os.environ['HYPOLOOM_RESULT_PATH']).write_text("
                b"'{\"artifacts\": {}}', encoding='utf-8')\n",
            )
            return with_output_digest(replace(
                response,
                proposed_workspace_artifacts=(artifact,),
                output_digest="",
            ))
        by_ref = {item.ref: item for item in request.input_artifacts}
        evaluation_ref = next(
            ref for ref in by_ref if ref.endswith("--predecessor-evaluation")
        )
        evaluation = json.loads(by_ref[evaluation_ref].content_bytes())
        assert evaluation["outcome"] == "EXECUTION_FAILURE"
        assert evaluation["validation_error"].startswith(
            "invalid_result_shape@result.json:"
        )
        assert evaluation["execution_integrity_error"] is None
        return response


class _TextContractRepairAgent(FakeAgent):
    def __init__(self) -> None:
        super().__init__(); self.draft_requests = []

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is not AgentCommand.DRAFT_EXPERIMENT_SPEC:
            return response
        self.draft_requests.append(request)
        if len(self.draft_requests) == 1:
            artifact = WorkspaceArtifact.from_bytes(
                "experiment.py",
                b"import json, os\nfrom pathlib import Path\n"
                b"result_path = Path(os.environ['HYPOLOOM_RESULT_PATH'])\n"
                b"(result_path.parent / 'mechanism_search.md').write_text("
                b"'MELD and IEMOCAP tail-class analysis is long enough for validation.\\n', "
                b"encoding='utf-8')\n"
                b"(result_path.parent / 'dual_dataset_design.md').write_text("
                b"'MELD and IEMOCAP use one shared mechanism in this bounded design.\\n', "
                b"encoding='utf-8')\n"
                b"result_path.write_text(json.dumps({"
                b"'artifacts': {"
                b"'mechanism_search': 'mechanism_search.md', "
                b"'dual_dataset_design': 'dual_dataset_design.md'}, "
                b"'identity': {'method_family_id': 'fake-family-v1'}}), "
                b"encoding='utf-8')\n",
            )
            return with_output_digest(replace(
                response,
                proposed_workspace_artifacts=(artifact,),
                output_digest="",
            ))
        by_ref = {item.ref: item for item in request.input_artifacts}
        evaluation_ref = next(
            ref for ref in by_ref if ref.endswith("--predecessor-evaluation")
        )
        evaluation = json.loads(by_ref[evaluation_ref].content_bytes())
        assert evaluation["outcome"] == "EXECUTION_FAILURE"
        assert evaluation["validation_error"] == (
            "text_contract_failed@mechanism_search.md: "
            "missing required text 'tail class'"
        )
        assert evaluation["execution_integrity_error"] is None
        return response


class _UnboundIdentityRepairAgent(FakeAgent):
    def __init__(self) -> None:
        super().__init__()
        self.draft_requests = []

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is not AgentCommand.DRAFT_EXPERIMENT_SPEC:
            return response
        self.draft_requests.append(request)
        if len(self.draft_requests) == 1:
            artifact = WorkspaceArtifact.from_bytes(
                "experiment.py",
                b"import json, os\nfrom pathlib import Path\n"
                b"result_path = Path(os.environ['HYPOLOOM_RESULT_PATH'])\n"
                b"(result_path.parent / 'mechanism_search.md').write_text("
                b"'MELD and IEMOCAP tail class analysis is complete and valid.\\n', "
                b"encoding='utf-8')\n"
                b"(result_path.parent / 'dual_dataset_design.md').write_text("
                b"'MELD and IEMOCAP use one shared mechanism in this bounded design.\\n', "
                b"encoding='utf-8')\n"
                b"result_path.write_text(json.dumps({"
                b"'artifacts': {"
                b"'mechanism_search': 'mechanism_search.md', "
                b"'dual_dataset_design': 'dual_dataset_design.md'}, "
                b"'identity': {"
                b"'method_family_id': 'fake-family-v1', "
                b"'implementation_sha256': 'pending_integration', "
                b"'core_config_sha256': 'pending_integration'}}), "
                b"encoding='utf-8')\n",
            )
            return with_output_digest(replace(
                response,
                proposed_workspace_artifacts=(artifact,),
                output_digest="",
            ))
        by_ref = {item.ref: item for item in request.input_artifacts}
        evaluation_ref = next(
            ref for ref in by_ref if ref.endswith("--predecessor-evaluation")
        )
        evaluation = json.loads(by_ref[evaluation_ref].content_bytes())
        assert evaluation["outcome"] == "EXECUTION_FAILURE"
        assert evaluation["validation_error"] == _UNBOUND_IDENTITY_DIAGNOSTIC
        assert evaluation["execution_integrity_error"] is None
        return response


class _ArtifactInventoryRepairAgent(FakeAgent):
    def __init__(self) -> None:
        super().__init__()
        self.draft_requests = []
        self.analyze_evaluations: list[dict[str, object]] = []
        self.predecessor_evaluation: dict[str, object] | None = None

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is AgentCommand.ANSWER_RENDEZVOUS:
            by_ref = {item.ref: item for item in request.input_artifacts}
            rendezvous = json.loads(
                next(
                    item
                    for item in request.input_artifacts
                    if item.ref.endswith("--rendezvous")
                ).content_bytes()
            )
            candidate = dict(response.typed_candidate or {})
            if rendezvous["rendezvous_id"].endswith("--preflight-rendezvous"):
                candidate["summary"] = _PREFLIGHT_REVIEW_DIAGNOSTIC
            else:
                assert rendezvous["rendezvous_id"].endswith("--analyze-rendezvous")
                evaluation_ref = next(
                    ref
                    for ref in by_ref
                    if ref.endswith("--evaluation")
                    and not ref.endswith("--predecessor-evaluation")
                )
                self.analyze_evaluations.append(
                    json.loads(by_ref[evaluation_ref].content_bytes())
                )
                candidate["summary"] = _ANALYZE_REVIEW_DIAGNOSTIC
            return with_output_digest(replace(
                response, typed_candidate=candidate, output_digest=""
            ))
        if request.command is not AgentCommand.DRAFT_EXPERIMENT_SPEC:
            return response

        self.draft_requests.append(request)
        if len(self.draft_requests) == 1:
            source = response.proposed_workspace_artifacts[0].content_bytes()
            source += (
                b"\nimport hashlib\n"
                b"result_path = Path(os.environ['HYPOLOOM_RESULT_PATH'])\n"
                b"extra_payload = b'ordinary intermediate source\\n'\n"
                b"(result_path.parent / 'multiemo_causal_candidate.py').write_bytes("
                b"extra_payload)\n"
                b"result = json.loads(result_path.read_text(encoding='utf-8'))\n"
                b"patch_ref = result['artifacts']['implementation_patch']\n"
                b"(result_path.parent / patch_ref).write_bytes(" +
                repr(_NONAPPLYING_IMPLEMENTATION_PATCH).encode("ascii") +
                b")\n"
                b"result['identity']['implementation_sha256'] = "
                b"hashlib.sha256(extra_payload).hexdigest()\n"
                b"result_path.write_text(json.dumps(result, sort_keys=True), "
                b"encoding='utf-8')\n"
            )
            artifact = WorkspaceArtifact.from_bytes("experiment.py", source)
            return with_output_digest(replace(
                response,
                proposed_workspace_artifacts=(artifact,),
                output_digest="",
            ))

        by_ref = {item.ref: item for item in request.input_artifacts}
        evaluation_ref = next(
            ref for ref in by_ref if ref.endswith("--predecessor-evaluation")
        )
        self.predecessor_evaluation = json.loads(
            by_ref[evaluation_ref].content_bytes()
        )
        return response


class _ResultIdentityAgent(FakeAgent):
    def __init__(self, method_family_id: str | None, *, arm: str | None = None) -> None:
        super().__init__(); self.method_family_id = method_family_id; self.arm = arm

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is not AgentCommand.DRAFT_EXPERIMENT_SPEC:
            return response
        result = {
            "trials": [{"seed": "only", "metrics": {"score": 3}, "clean": True}],
            "artifacts": {},
        }
        if self.method_family_id is not None:
            result["identity"] = {"method_family_id": self.method_family_id}
        if self.arm is not None:
            result["trials"][0]["arm"] = self.arm
        source = (
            "import json, os\nfrom pathlib import Path\n"
            f"result = {result!r}\n"
            "Path(os.environ['HYPOLOOM_RESULT_PATH']).write_text("
            "json.dumps(result), encoding='utf-8')\n"
        ).encode()
        artifact = WorkspaceArtifact.from_bytes("experiment.py", source)
        return with_output_digest(replace(
            response,
            proposed_workspace_artifacts=(artifact,),
            output_digest="",
        ))


class _ExpireAtDraftResponseAgent(FakeAgent):
    def __init__(self, expired: list[bool]) -> None:
        super().__init__()
        self.expired = expired

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is AgentCommand.DRAFT_EXPERIMENT_SPEC:
            self.expired[0] = True
        return response


class _TokenExceededPreflightAgent(FakeAgent):
    def invoke(self, request):
        response = super().invoke(request)
        assert isinstance(response, AgentResponse)
        if (
            request.command is AgentCommand.PROPOSE_HYPOTHESIS
            and request.role == "scientist-c"
        ):
            candidate = dict(response.typed_candidate or {})
            candidate["method_family_id"] = "alternate-family-v1"
            return with_output_digest(
                replace(response, typed_candidate=candidate, output_digest="")
            )
        if request.command is AgentCommand.ANSWER_RENDEZVOUS:
            total = request.budget.max_tokens + 1
            return with_output_digest(
                replace(
                    response,
                    token_usage={
                        "input_tokens": 0,
                        "output_tokens": total,
                        "total_tokens": total,
                    },
                    output_digest="",
                )
            )
        return response


def _component_payload(
    *,
    run_id: str,
    target: Path,
    commit: str,
    runtime: Path,
    data_root: Path,
) -> dict[str, object]:
    admission = admit_profile(
        REPOSITORY / "tests" / "fixtures" / "profiles" / "component-demo",
        run_id=run_id,
    )
    assert admission.admitted, admission.to_dict()
    assert admission.create_run_payload is not None
    payload = copy.deepcopy(admission.create_run_payload)
    contract = payload["research_contract"]
    assert isinstance(contract, dict)
    contract["goal"] = {
        "objective": "Close a patch, an overlay-backed metric, and its report.",
        "language": "",
        "verdict_rule": "required_conjunction",
        "summary_metric": {
            "name": "required_component_completion",
            "direction": "MAXIMIZE",
            "target": "3",
            "display_only": True,
        },
    }
    contract["components"] = [
        {
            "id": "must.implementation",
            "tier": "must",
            "name": "implementation",
            "owner": "scientist-a",
            "proposers": ["scientist-a"],
            "dependencies": [],
            "required_inputs": [],
            "protocol": "implementation-artifact",
            "result": {"kind": "artifact", "evaluation_kind": "artifact_check"},
            "seeds": [],
            "required_artifacts": ["implementation_patch", "runner_config"],
        },
        {
            "id": "primary.metric",
            "tier": "primary",
            "name": "metric",
            "owner": "scientist-a",
            "proposers": ["scientist-a"],
            "dependencies": ["must.implementation"],
            "required_inputs": [
                {
                    "resource": "implementation",
                    "produced_by": "must.implementation",
                    "artifact": "implementation_patch",
                    "min_count": 1,
                }
            ],
            "protocol": "overlay-metric",
            "result": {
                "kind": "metric",
                "metric": "score",
                "direction": "MAXIMIZE",
                "target": "1",
                "dataset": "TEMP",
                "setting": "component-g3",
                "target_overlay_policy": "latest",
            },
            "seeds": [],
            "required_artifacts": ["per_trial_metrics"],
        },
        {
            "id": "must.report",
            "tier": "must",
            "name": "report",
            "owner": "scientist-a",
            "proposers": ["scientist-a"],
            "dependencies": ["primary.metric"],
            "required_inputs": [
                {
                    "resource": "metric-records",
                    "produced_by": "primary.metric",
                    "artifact": "per_trial_metrics",
                    "min_count": 1,
                }
            ],
            "protocol": "final-report",
            "result": {"kind": "artifact", "evaluation_kind": "artifact_check"},
            "seeds": [],
            "required_artifacts": ["final_report"],
        },
    ]
    contract["protocols"] = {
        "implementation-artifact": {
            "result_kind": "artifact",
            "requirements": {
                "required_artifacts": ["implementation_patch", "runner_config"],
                "artifact_validators": [
                    {
                        "artifact": "implementation_patch",
                        "type": "unified_diff",
                        "apply_check": True,
                    },
                    {
                        "artifact": "runner_config",
                        "type": "json_contract",
                        "required_paths": ["method_family_id", "datasets"],
                    },
                ],
            },
        },
        "overlay-metric": {
            "result_kind": "metric",
            "requirements": {
                "required_artifacts": ["per_trial_metrics"],
                "required_metrics": ["score"],
                "artifact_validators": [
                    {
                        "artifact": "per_trial_metrics",
                        "type": "json_contract",
                        "required_paths": ["trials"],
                    }
                ],
            },
        },
        "final-report": {
            "result_kind": "artifact",
            "requirements": {
                "required_artifacts": ["final_report"],
                "artifact_validators": [
                    {
                        "artifact": "final_report",
                        "type": "text_contract",
                        "min_chars": 32,
                        "required_terms": ["component closure"],
                    }
                ],
            },
        },
    }
    contract["methodology"]["controls"]["experiment_campaigns"] = {
        "enabled": True,
        "required_roles": ["preflight", "analyze"],
        "role_owners": {
            "*": {"preflight": "scientist-b", "analyze": "scientist-c"}
        },
    }
    contract["runtime"] = {
        "target_repo": str(target),
        "conda_env": str(runtime),
    }
    contract["budget"].update({
        "max_experiments": 3,
        "experiment_timeout": 15,
    })
    contract["safety"] = {
        "read_roots": [str(data_root)],
        "sandbox": "bwrap",
    }
    payload["execution"] = {
        "allow_gpu": False,
        "delay_seconds": 0.0,
        "target": {"checkout": str(target), "commit": commit},
        "runtime_prefix": str(runtime),
        "read_roots": [str(data_root)],
    }
    return payload


def test_host_implementation_patch_selects_latest_accepted_component_revision(
    tmp_path: Path,
) -> None:
    payload = _component_payload(
        run_id="latest-implementation",
        target=tmp_path / "target",
        commit="0" * 40,
        runtime=Path(sys.prefix),
        data_root=tmp_path / "data",
    )
    contract = ResearchContract.from_mapping(payload["research_contract"])
    older = tmp_path / "older.patch"
    newer = tmp_path / "newer.patch"
    older.write_bytes(b"older candidate\n")
    newer.write_bytes(b"newer candidate\n")
    outcome_payload = {
        "component_id": "must.implementation",
        "method_family_id": METHOD_FAMILY,
        "logical_artifacts": [
            {
                "logical_artifact": "implementation_patch",
                "paths": ["implementation.patch"],
            }
        ],
    }

    class _OutcomeStore:
        @staticmethod
        def inspect(statement: str, parameters: tuple[str, ...]):
            assert "rowid AS raw_order" in statement
            assert parameters == ("latest-implementation",)
            return (
                {"raw_order": 1, "fact_id": "older-fact"},
                {"raw_order": 2, "fact_id": "newer-fact"},
            )

        @staticmethod
        def list_facts(run_id: str, fact_type: str):
            assert run_id == "latest-implementation"
            assert fact_type == "CanonicalOutcomeAccepted"
            return (
                {
                    "fact_id": "newer-fact",
                    "outcome_id": "newer",
                    "artifact_id": "newer-artifact",
                    "payload": outcome_payload,
                },
                {
                    "fact_id": "older-fact",
                    "outcome_id": "older",
                    "artifact_id": "older-artifact",
                    "payload": outcome_payload,
                },
            )

    host = object.__new__(WorkflowHost)
    host.store = _OutcomeStore()
    host._research_contract = lambda run_id: contract
    sources = {"older": (older,), "newer": (newer,)}
    host._logical_sources = (
        lambda run_id, outcome_id, artifact_id, logical_id: sources[outcome_id]
    )

    assert host._implementation_patch(
        "latest-implementation", "primary.metric", METHOD_FAMILY
    ) == newer.read_bytes()


class _TopologyCheckingFakeAgent(FakeAgent):
    def __init__(self, *, target: Path, data_root: Path) -> None:
        super().__init__()
        self.target = target
        self.data_root = data_root

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is not AgentCommand.DRAFT_EXPERIMENT_SPEC:
            return response
        assert isinstance(response, AgentResponse)
        assert len(response.proposed_workspace_artifacts) == 1
        expected_inputs = {
            "must.implementation": {},
            "primary.metric": {"implementation": ["/hypoloom-inputs/1/1"]},
            "must.report": {"metric-records": ["/hypoloom-inputs/1/1"]},
        }[request.component_id]
        component_checks = ""
        if request.component_id == "primary.metric":
            component_checks = f"""
_probe_patch = _ProbePath(_probe_required["implementation"][0]).read_text(encoding="utf-8")
assert "multiemo_causal_candidate.py" in _probe_patch
_probe_candidate = _probe_target / "src/merc/models/multiemo_causal_candidate.py"
assert _probe_candidate.is_file()
assert {METHOD_FAMILY!r} in _probe_candidate.read_text(encoding="utf-8")
"""
        elif request.component_id == "must.report":
            component_checks = """
_probe_metric = _probe_json.loads(
    _ProbePath(_probe_required["metric-records"][0]).read_text(encoding="utf-8")
)
assert _probe_metric["component_id"] == "primary.metric"
assert not (_probe_target / "src/merc/models/multiemo_causal_candidate.py").exists()
"""
        else:
            component_checks = """
assert not (_probe_target / "src/merc/models/multiemo_causal_candidate.py").exists()
"""
        probe = f"""
import json as _probe_json
import os as _probe_os
from pathlib import Path as _ProbePath

_probe_required = _probe_json.loads(_probe_os.environ["HYPOLOOM_REQUIRED_INPUTS"])
assert _probe_required == {expected_inputs!r}
_probe_read_roots = _probe_json.loads(_probe_os.environ["HYPOLOOM_READ_ROOTS"])
assert _probe_read_roots == {[str(self.data_root)]!r}
assert (_ProbePath(_probe_read_roots[0]) / "dataset.txt").read_text(encoding="utf-8") == "read-only fixture\\n"
_probe_target = _ProbePath(_probe_os.environ["HYPOLOOM_TARGET_ROOT"])
assert str(_probe_target) == {str(self.target)!r}
{component_checks}
try:
    (_probe_target / "sandbox-write-must-fail").write_text("forbidden", encoding="utf-8")
except OSError:
    pass
else:
    raise AssertionError("attempt-local target mount is writable")

""".encode("utf-8")
        source = response.proposed_workspace_artifacts[0].content_bytes()
        artifact = WorkspaceArtifact.from_bytes("experiment.py", probe + source)
        return with_output_digest(
            replace(
                response,
                proposed_workspace_artifacts=(artifact,),
                output_digest="",
            )
        )


def _drive_to_completion(host: WorkflowHost, run_id: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        host.tick()
        head = host.store.latest_transition(run_id)
        if head is not None and head["state"] == "COMPLETED":
            return
        time.sleep(0.005)
    raise AssertionError(
        f"component run did not complete: {host.store.latest_transition(run_id)}"
    )


def test_host_records_cross_family_inconclusive_verdict_without_family(
    tmp_path: Path,
) -> None:
    run_id = "cross-family-inconclusive"
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root)
    host.agent = _CrossFamilyInconclusiveAgent()
    try:
        host.initialize()
        created = host.handle(
            ControlRequest(
                schema_version=1,
                request_id=f"create-{run_id}",
                expected_revision=0,
                command="CREATE_RUN",
                payload=_cross_family_inconclusive_payload(run_id),
            )
        )
        assert created.status == "accepted", created
        _drive_to_completion(host, run_id)

        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
        assessments = host.store.list_facts(run_id, "HypothesisAssessed")
        assert len(assessments) == 4
        assert {
            row["payload"]["component_id"]: row["payload"]["method_family_id"]
            for row in assessments
        } == _CrossFamilyInconclusiveAgent._families
        verdicts = host.store.list_facts(run_id, "GoalVerdictRecorded")
        assert len(verdicts) == 1
        verdict = verdicts[0]["payload"]
        assert verdict["verdict"] == "INCONCLUSIVE"
        assert verdict["method_family_id"] is None
        assert set(verdict["assessment_ids"]) == {
            row["subject_id"] for row in assessments
        }
        assert all(
            row["state"] == "CLOSED"
            for row in host.store.list_attempts()
            if row["run_id"] == run_id
        )
    finally:
        host.close()

    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(
        run_id
    )
    assert report.ok, report.issues


def _argv_triples(request: ProcessRequest, option: str) -> set[tuple[str, str]]:
    argv = request.argv
    return {
        (argv[index + 1], argv[index + 2])
        for index, value in enumerate(argv[:-2])
        if value == option
    }


def _argv_value(request: ProcessRequest, option: str, name: str) -> str:
    argv = request.argv
    for index, value in enumerate(argv[:-2]):
        if value == option and argv[index + 1] == name:
            return argv[index + 2]
    raise AssertionError(f"ProcessRequest lacks {option} {name}")


def test_artifact_durability_retry_is_recorded_before_evaluator_reentry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "recorded-evaluator-retry"
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root)
    calls = 0
    try:
        host.initialize()
        assert host.publisher is not None
        publish = host.publisher.publish

        def fail_once(*args: object, **kwargs: object):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ArtifactDurabilityError("injected fsync failure")
            return publish(*args, **kwargs)

        monkeypatch.setattr(host.publisher, "publish", fail_once)
        monkeypatch.setitem(workflow_host_module._CONFIG, "evaluator_retry_delay", 0.0)
        response = host.handle(ControlRequest(
            schema_version=1, request_id=f"create-{run_id}", expected_revision=0,
            command="CREATE_RUN", payload=_revision_payload(run_id),
        ))
        assert response.status == "accepted", response
        _drive_to_completion(host, run_id)

        retries = host.store.inspect(
            "SELECT t.payload_json,t.next_command FROM workflow_transitions t "
            "JOIN workflow_commands c ON c.command_id=t.command_id "
            "WHERE t.run_id=? AND c.command_type='RETRY_EVALUATOR'",
            (run_id,),
        )
        assert len(retries) == 1
        payload = json.loads(retries[0]["payload_json"])
        assert payload == {
            "evaluator_retry_ordinal": 1,
            "failure_class": "ArtifactDurabilityError",
            "failure_stage": "artifact_publication",
            "retry_after_seconds": 0.0,
            "source_attempt_id": f"{run_id}--component-1--experiment--process-1",
        }
        assert retries[0]["next_command"] == "ACCEPT_OUTCOME"
        assert calls >= 2
    finally:
        host.close()

    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_evaluator_retry_sequence_is_durable_and_resumes_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "recorded-evaluator-recovery"
    state_root = tmp_path / "state"
    monkeypatch.setitem(workflow_host_module._CONFIG, "evaluator_retry_delay", 0.0)
    host = WorkflowHost(state_root)
    calls = 0
    try:
        host.initialize()
        assert host.publisher is not None

        def always_fail(*args: object, **kwargs: object):
            nonlocal calls
            calls += 1
            raise ArtifactDurabilityError("injected persistent fsync failure")

        monkeypatch.setattr(host.publisher, "publish", always_fail)
        response = host.handle(ControlRequest(
            schema_version=1, request_id=f"create-{run_id}", expected_revision=0,
            command="CREATE_RUN", payload=_revision_payload(run_id),
        ))
        assert response.status == "accepted", response
        for _ in range(200):
            host.tick()
            transitions = host.store.inspect(
                "SELECT t.payload_json,t.next_command FROM workflow_transitions t "
                "JOIN workflow_commands c ON c.command_id=t.command_id "
                "WHERE t.run_id=? AND c.command_type='RETRY_EVALUATOR' "
                "ORDER BY t.revision",
                (run_id,),
            )
            if len(transitions) == 2:
                break
        assert len(transitions) == 2
        first = json.loads(transitions[0]["payload_json"])
        second = json.loads(transitions[1]["payload_json"])
        assert first["evaluator_retry_ordinal"] == 1
        assert transitions[0]["next_command"] == "ACCEPT_OUTCOME"
        assert second == {
            "evaluator_retry_ordinal": 2,
            "failure_class": "ArtifactDurabilityError",
            "failure_stage": "artifact_publication",
            "retry_after_seconds": 0.0,
            "source_attempt_id": f"{run_id}--component-1--experiment--process-1",
        }
        assert transitions[1]["next_command"] == "ACCEPT_OUTCOME"
        assert calls == 2
    finally:
        host.close()

    recovered = WorkflowHost(state_root)
    try:
        recovered.initialize()
        before = recovered.store.inspect(
            "SELECT COUNT(*) AS count FROM workflow_transitions t "
            "JOIN workflow_commands c ON c.command_id=t.command_id "
            "WHERE t.run_id=? AND c.command_type='RETRY_EVALUATOR'",
            (run_id,),
        )[0]["count"]
        _drive_to_completion(recovered, run_id)
        after = recovered.store.inspect(
            "SELECT COUNT(*) AS count FROM workflow_transitions t "
            "JOIN workflow_commands c ON c.command_id=t.command_id "
            "WHERE t.run_id=? AND c.command_type='RETRY_EVALUATOR'",
            (run_id,),
        )[0]["count"]
        assert after == before == 2
    finally:
        recovered.close()

    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_stopping_run_respects_evaluator_retry_due_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "stopping-evaluator-retry-backoff"
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root)
    try:
        host.initialize()
        assert host.publisher is not None
        publish = host.publisher.publish

        def always_fail(*args: object, **kwargs: object):
            raise ArtifactDurabilityError("injected persistent fsync failure")

        monkeypatch.setattr(host.publisher, "publish", always_fail)
        monkeypatch.setitem(
            workflow_host_module._CONFIG, "evaluator_retry_delay", 0.2,
        )
        created = host.handle(ControlRequest(
            schema_version=1, request_id=f"create-{run_id}", expected_revision=0,
            command="CREATE_RUN", payload=_revision_payload(run_id),
        ))
        assert created.status == "accepted", created

        retries: list[dict[str, object]] = []
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            host.tick()
            retries = host.store.inspect(
                "SELECT t.* FROM workflow_transitions t "
                "JOIN workflow_commands c ON c.command_id=t.command_id "
                "WHERE t.run_id=? AND c.command_type='RETRY_EVALUATOR' "
                "ORDER BY t.revision",
                (run_id,),
            )
            if retries:
                break
            time.sleep(0.005)
        assert len(retries) == 1
        due_at = float(retries[0]["due_at"])
        assert due_at > time.time()

        head = host.store.latest_transition(run_id)
        assert head is not None
        cancelled = host.handle(ControlRequest(
            schema_version=1, request_id=f"cancel-{run_id}",
            expected_revision=int(head["revision"]), command="CANCEL",
            payload={"run_id": run_id, "cause": "evaluator_retry_backoff_test"},
        ))
        assert cancelled.status == "accepted", cancelled
        for _ in range(5):
            host.tick()
        assert len(host.store.inspect(
            "SELECT 1 FROM workflow_transitions t "
            "JOIN workflow_commands c ON c.command_id=t.command_id "
            "WHERE t.run_id=? AND c.command_type='RETRY_EVALUATOR'",
            (run_id,),
        )) == 1

        monkeypatch.setattr(host.publisher, "publish", publish)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            host.tick()
            if host.store.latest_transition(run_id)["state"] == "CANCELLED":
                break
            time.sleep(0.005)
        assert host.store.latest_transition(run_id)["state"] == "CANCELLED"
    finally:
        host.close()

    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_failed_candidate_compile_is_returned_to_a_bounded_redraft(tmp_path: Path) -> None:
    run_id = "candidate-compile-repair"
    state_root = tmp_path / "state"; agent = _CompileRepairAgent(); host = WorkflowHost(state_root)
    host.agent = agent
    try:
        host.initialize()
        response = host.handle(ControlRequest(schema_version=1, request_id=f"create-{run_id}",
            expected_revision=0, command="CREATE_RUN", payload=_revision_payload(run_id)))
        assert response.status == "accepted", response
        _drive_to_completion(host, run_id)
        attempts = host.store.list_attempts(
            execution_id=f"{run_id}--component-1--experiment--candidate-validation"
        )
        assert [(row["attempt_ordinal"], row["terminal_reason"]) for row in attempts] == [
            (1, "FAILED"), (1, "SUCCEEDED"),
        ]
        assert len(agent.draft_requests) == 2
        assert not any((state_root / "artifacts" / "work").glob("*spec-validation*"))
    finally:
        host.close()
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_candidate_without_result_reference_is_rejected_before_execution(tmp_path: Path) -> None:
    run_id = "candidate-result-reference-guard"
    state_root = tmp_path / "state"; agent = _ResultReferenceRepairAgent(); host = WorkflowHost(state_root)
    host.agent = agent
    try:
        host.initialize()
        response = host.handle(ControlRequest(schema_version=1, request_id=f"create-{run_id}",
            expected_revision=0, command="CREATE_RUN", payload=_revision_payload(run_id)))
        assert response.status == "accepted", response
        _drive_to_completion(host, run_id)
        experiment_id = f"{run_id}--component-1--experiment"
        attempts = host.store.list_attempts(
            execution_id=f"{experiment_id}--candidate-validation"
        )
        assert [(row["attempt_ordinal"], row["terminal_reason"]) for row in attempts] == [
            (1, "FAILED"), (1, "SUCCEEDED"),
        ]
        diagnostics = json.loads(host.store.get_attempt(
            f"{experiment_id}--spec-validation-1"
        )["observation_json"])
        assert diagnostics["validation_version"] == "candidate-compile/3"
        assert diagnostics["returncode"] == 2
        assert "experiment.py never references HYPOLOOM_RESULT_PATH or result.json" in diagnostics["stderr_tail"]

        # The rejected candidate never spawned an execution process and never
        # reached preregistration; only the repaired candidate did.
        executions = host.store.list_attempts(
            execution_id=f"{run_id}--component-1--execution"
        )
        assert [(row["attempt_ordinal"], row["terminal_reason"]) for row in executions] == [
            (1, "SUCCEEDED"),
        ]
        preregistrations = host.store.list_facts(run_id, "ExperimentPreregistered")
        assert len(preregistrations) == 1
        assert preregistrations[0]["payload"]["execution_contract"][
            "source_agent_attempt_id"
        ] == f"{experiment_id}--agent-2"
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        assert [row["payload"]["outcome"] for row in outcomes] == ["SUCCESS"]
        assert len(agent.draft_requests) == 2
        assert not any((state_root / "artifacts" / "work").glob("*spec-validation*"))
    finally:
        host.close()
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_production_patch_compile_failure_is_rejected_before_execution(
    tmp_path: Path, mount_fixture_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The predecessor ACI10 protocol validates a closed EDI before creating its
    # experiment. Current ACI14 validates each OPEN execution Agent's submit;
    # the adjacent test exercises that protocol without faking a closed response.
    monkeypatch.setattr(workflow_host_module, "EXPERT_LOOP_ACI_SCHEMA_VERSION", 10)
    run_id = "candidate-patch-compile-guard"
    target, commit = _production_target(mount_fixture_root)
    state_root = tmp_path / "state"; agent = _PatchCompileRepairAgent(); host = WorkflowHost(state_root)
    host.agent = agent
    try:
        host.initialize()
        response = host.handle(ControlRequest(schema_version=1, request_id=f"create-{run_id}",
            expected_revision=0, command="CREATE_RUN",
            payload=_production_design_payload(run_id, target, commit)))
        assert response.status == "accepted", response
        _drive_to_completion(host, run_id)
        assert len(agent.design_requests) == 2
        experiment_id = str(agent.design_requests[0].owner_workflow_id)
        attempts = host.store.list_attempts(
            execution_id=f"{experiment_id}--candidate-validation"
        )
        assert [(row["attempt_ordinal"], row["terminal_reason"]) for row in attempts] == [
            (1, "FAILED"), (1, "SUCCEEDED"),
        ]
        diagnostics = json.loads(host.store.get_attempt(
            f"{experiment_id}--spec-validation-1"
        )["observation_json"])
        assert diagnostics["validation_version"] == "candidate-compile/3"
        assert diagnostics["terminal_reason"] == "FAILED"
        assert "returncode" not in diagnostics
        assert diagnostics["validation_error"].startswith(
            "ValueError:implementation_patch_compile_failed: "
            "src/frozen_target/runner.py: unexpected indent (line 3)"
        )

        # The rejected candidate never spawned an execution process and never
        # reached preregistration; only the repaired candidate did.
        executions = [
            row for row in host.store.list_attempts()
            if row["run_id"] == run_id
            and row["kind"] == "PROCESS"
            and row["workflow_id"] == experiment_id
            and "--spec-validation-" not in str(row["attempt_id"])
        ]
        assert [(row["attempt_ordinal"], row["terminal_reason"]) for row in executions] == [
            (1, "SUCCEEDED"),
        ]
        preregistrations = host.store.list_facts(run_id, "ExperimentPreregistered")
        assert len(preregistrations) == 1
        assert preregistrations[0]["payload"]["execution_contract"][
            "source_agent_attempt_id"
        ] == agent.design_requests[1].attempt_id
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        assert [row["payload"]["outcome"] for row in outcomes] == ["SUCCESS"]
        assert not any((state_root / "artifacts" / "work").glob("*spec-validation*"))
    finally:
        host.close()
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_current_production_submit_rejects_broken_patch_then_repairs_in_same_execution(
    tmp_path: Path, mount_fixture_root: Path,
) -> None:
    class ToolRepairAgent(_PatchCompileRepairAgent):
        submitted = None
        execution_attempt_id = None
        rejected = False

        def poll_execution(self, request, call):
            self.execution_attempt_id = request.attempt_id
            if self.submitted is None:
                design = self.design_requests[0]
                good = FakeAgent.invoke(self, design)
                # Replay the actual first design packet, whose patch fails to
                # compile; the open execution Agent receives and repairs it.
                bad = json.loads(host._expert_success(design.task_context)["observation_json"])["response"]
                with pytest.raises(AgentACIError, match="implementation_patch_compile_failed"):
                    call(request.attempt_id, "submit_experiment", {
                        "candidate": bad["typed_candidate"],
                        "workspace_files": bad["proposed_workspace_artifacts"],
                    })
                self.rejected = True
                assert not host.store.list_workflow_heads(request.run_id, "EXPERIMENT")
                assert not host.store.list_facts(request.run_id, "ExperimentPreregistered")
                self.submitted = call(request.attempt_id, "submit_experiment", {
                    "candidate": good.typed_candidate,
                    "workspace_files": [item.to_dict() for item in good.proposed_workspace_artifacts],
                })
            view = call(request.attempt_id, "wait_experiment", {
                "exp_id": self.submitted["exp_id"], "max_wait_seconds": 0,
            })
            return FakeAgent.invoke(self, request) if view["terminal"] else None

    run_id = "current-production-submit-patch-repair"
    target, commit = _production_target(mount_fixture_root)
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root)
    agent = ToolRepairAgent()
    host.agent = agent
    try:
        host.initialize()
        response = host.handle(ControlRequest(schema_version=1,
            request_id=f"create-{run_id}", expected_revision=0, command="CREATE_RUN",
            payload=_production_design_payload(run_id, target, commit)))
        assert response.status == "accepted", response
        _drive_to_completion(host, run_id)
        assert host.store.get_run(run_id)["agent_aci_version"] == "20"
        assert agent.rejected and len(agent.design_requests) == 1
        preregistrations = host.store.list_facts(run_id, "ExperimentPreregistered")
        assert len(preregistrations) == 1
        assert preregistrations[0]["payload"]["execution_contract"]["source_agent_attempt_id"] == agent.execution_attempt_id
        assert [row["payload"]["outcome"] for row in host.store.list_facts(run_id, "CanonicalOutcomeAccepted")] == ["SUCCESS"]
        processes = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]
        assert [row["terminal_reason"] for row in processes] == ["SUCCEEDED"]
        assert Path(json.loads(processes[0]["observation_json"])["process_request"]["argv"][0]).name == "bwrap"
    finally:
        host.close()
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues
    assert report.open_attempt_count == 0 and report.owned_process_residue == ()


def test_restart_reconciles_closed_draft_agent_workspace_residue(
    tmp_path: Path,
) -> None:
    run_id = "closed-draft-workspace-reconcile"
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root)
    try:
        host.initialize()
        created = host.handle(
            ControlRequest(
                schema_version=1,
                request_id=f"create-{run_id}",
                expected_revision=0,
                command="CREATE_RUN",
                payload=_revision_payload(run_id),
            )
        )
        assert created.status == "accepted", created
        _drive_to_completion(host, run_id)
        draft = next(
            row
            for row in host.store.list_attempts()
            if row["run_id"] == run_id
            and row["kind"] == "AGENT"
            and "--experiment--agent-" in str(row["attempt_id"])
        )
    finally:
        host.close()

    residue = state_root / "artifacts" / "work" / str(draft["attempt_id"])
    (residue / "draft").mkdir(parents=True)
    (residue / "draft" / "experiment.py").write_text(
        "print('residue')\n", encoding="utf-8"
    )
    before = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert "owned_work_residue" in {issue.code for issue in before.issues}

    recovered = WorkflowHost(state_root)
    try:
        recovered.initialize()
        assert not residue.exists()
    finally:
        recovered.close()
    after = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert after.ok, after.issues


@pytest.mark.parametrize("kind", ["AGENT", "PROCESS"])
def test_workspace_is_live_until_its_attempt_closes(tmp_path, kind):
    from hypoloom.oracle import _audit_owned_workspace_residue
    root = tmp_path / "artifacts"
    (root / "work" / "attempt" / "output").mkdir(parents=True)
    (root / "result-staging" / "attempt" / "output").mkdir(parents=True)
    attempt = {"attempt_id": "attempt", "kind": kind, "state": "OPEN"}
    issues = []
    _audit_owned_workspace_residue(root, [attempt], issues)
    assert issues == []
    _audit_owned_workspace_residue(root, [{**attempt, "state": "CLOSED"}], issues)
    expected = {"owned_work_residue", "owned_staging_residue"} if kind == "PROCESS" else {"owned_work_residue"}
    assert {issue.code for issue in issues} == expected


def test_open_bounded_redraft_is_not_discarded_before_its_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "candidate-compile-async-repair"
    state_root = tmp_path / "state"; agent = _CompileRepairAgent(); host = WorkflowHost(state_root)
    host.agent = agent
    finish = host._finish_fake_agent; deferred = []

    def defer_second_draft(request, raw):
        if (request.command is AgentCommand.DRAFT_EXPERIMENT_SPEC
                and request.attempt_ordinal == 2):
            deferred.append((request, raw)); return
        finish(request, raw)

    monkeypatch.setattr(host, "_finish_fake_agent", defer_second_draft)
    try:
        host.initialize()
        response = host.handle(ControlRequest(schema_version=1, request_id=f"create-{run_id}",
            expected_revision=0, command="CREATE_RUN", payload=_revision_payload(run_id)))
        assert response.status == "accepted", response
        deadline = time.monotonic() + 10
        while not deferred and time.monotonic() < deadline:
            host.tick(); time.sleep(0.005)
        assert len(deferred) == 1

        host.tick()

        experiment_id = f"{run_id}--component-1--experiment"
        assert host.store.latest_transition(experiment_id)["state"] == "DRAFTED"
        assert host.store.get_attempt(f"{experiment_id}--agent-2")["state"] == "OPEN"

        request, raw = deferred.pop()
        finish(request, raw)
        _drive_to_completion(host, run_id)
        validations = host.store.list_attempts(
            execution_id=f"{experiment_id}--candidate-validation"
        )
        assert [row["terminal_reason"] for row in validations] == [
            "FAILED", "SUCCEEDED",
        ]
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        assert [row["payload"]["outcome"] for row in outcomes] == ["SUCCESS"]
        verdicts = host.store.list_facts(run_id, "GoalVerdictRecorded")
        assert len(verdicts) == 1
        assert verdicts[0]["payload"]["verdict"] == "GOAL_MET"
        assert len(agent.draft_requests) == 2
    finally:
        host.close()
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_token_exceeded_preflight_closes_without_preregistration_or_oracle_drift(
    tmp_path: Path,
) -> None:
    run_id = "preflight-token-budget-failure"
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root)
    host.agent = _TokenExceededPreflightAgent()
    try:
        host.initialize()
        payload = _revision_payload(run_id, max_experiments=1)
        contract = payload["research_contract"]
        contract["components"][0]["proposers"] = ["scientist-a", "scientist-c"]
        contract["methodology"]["controls"]["experiment_campaigns"] = {
            "enabled": True,
            "required_roles": ["preflight"],
            "role_owners": {"*": {"preflight": "scientist-b"}},
        }
        created = host.handle(
            ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload)
        )
        assert created.status == "accepted", created
        _drive_to_completion(host, run_id)

        _, _, replies = host._rendezvous_records(run_id)
        assert len(replies) == 1
        reply = next(iter(replies.values()))
        assert reply.disposition.value == "INTEGRITY_REJECTED"
        assert not host.store.list_facts(run_id, "ExperimentPreregistered")
        assert not host.store.list_facts(run_id, "HypothesisAssessed")
        verdicts = host.store.list_facts(run_id, "GoalVerdictRecorded")
        assert len(verdicts) == 1
        verdict = verdicts[0]["payload"]
        assert verdict["assessment_ids"] == []
        assert verdict["method_family_id"] == METHOD_FAMILY
        assert verdict["verdict"] == "INCONCLUSIVE"
    finally:
        host.close()

    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_candidate_validation_cleanup_precedes_durable_close_and_restart_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "candidate-validation-close-crash"
    state_root = tmp_path / "state"; host = WorkflowHost(state_root)
    original_close = StateTransaction.close_attempt

    def fail_validation_close(transaction, attempt_id, **kwargs):
        if "--spec-validation-" in attempt_id:
            raise RuntimeError("simulated close boundary crash")
        return original_close(transaction, attempt_id, **kwargs)

    host.initialize()
    try:
        response = host.handle(ControlRequest(schema_version=1, request_id=f"create-{run_id}",
            expected_revision=0, command="CREATE_RUN", payload=_revision_payload(run_id)))
        assert response.status == "accepted", response
        monkeypatch.setattr(StateTransaction, "close_attempt", fail_validation_close)
        deadline = time.monotonic() + 10
        with pytest.raises(RuntimeError, match="simulated close boundary crash"):
            while time.monotonic() < deadline:
                host.tick(); time.sleep(0.005)
            raise AssertionError("candidate validation did not reach its close boundary")
        attempt_id = f"{run_id}--component-1--experiment--spec-validation-1"
        assert not (state_root / "artifacts" / "work" / attempt_id).exists()
        assert host.store.get_attempt(attempt_id)["state"] == "OPEN"
    finally:
        monkeypatch.setattr(StateTransaction, "close_attempt", original_close)
        host.close()

    recovered = WorkflowHost(state_root)
    try:
        recovered.initialize()
        lost = recovered.store.get_attempt(attempt_id)
        assert lost is not None and lost["terminal_reason"] == "LOST"
        _drive_to_completion(recovered, run_id)
    finally:
        recovered.close()
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_restart_replays_a_successful_unreviewed_validation_without_another_agent(
    tmp_path: Path,
) -> None:
    run_id = "candidate-validation-accept-replay"
    state_root = tmp_path / "state"; host = WorkflowHost(state_root)
    payload = _revision_payload(run_id); payload["research_contract"]["budget"]["max_retries"] = 0
    original_accept = host._accept_agent_response

    def crash_after_validation(request, response, *, review_complete=False):
        if review_complete:
            raise RuntimeError("simulated post-validation acceptance crash")
        return original_accept(request, response, review_complete=review_complete)

    host._accept_agent_response = crash_after_validation
    try:
        host.initialize()
        created = host.handle(ControlRequest(schema_version=1, request_id=f"create-{run_id}",
            expected_revision=0, command="CREATE_RUN", payload=payload))
        assert created.status == "accepted", created
        with pytest.raises(RuntimeError, match="post-validation acceptance crash"):
            while True: host.tick(); time.sleep(0.005)
        assert len([row for row in host.store.list_attempts() if row["kind"] == "AGENT"]) == 2
    finally:
        host.close()

    recovered = WorkflowHost(state_root)
    try:
        recovered.initialize()
        for _ in range(400):
            recovered.tick()
            if recovered.store.latest_transition(run_id)["state"] == "COMPLETED": break
            time.sleep(0.005)
        assert recovered.store.latest_transition(run_id)["state"] == "COMPLETED"
        assert len([row for row in recovered.store.list_attempts() if row["kind"] == "AGENT"]) == 2
    finally:
        recovered.close()
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_cancel_during_candidate_validation_discards_success_without_preregistering(
    tmp_path: Path,
) -> None:
    run_id = "candidate-validation-cancel"
    state_root = tmp_path / "state"; host = WorkflowHost(state_root)
    try:
        host.initialize()
        created = host.handle(ControlRequest(schema_version=1, request_id=f"create-{run_id}",
            expected_revision=0, command="CREATE_RUN", payload=_revision_payload(run_id)))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 10
        while not host.spec_validations and time.monotonic() < deadline:
            host.tick(); time.sleep(0.005)
        assert host.spec_validations
        head = host.store.latest_transition(run_id)
        cancelled = host.handle(ControlRequest(schema_version=1, request_id=f"cancel-{run_id}",
            expected_revision=head["revision"], command="CANCEL",
            payload={"run_id": run_id, "cause": "candidate_validation_cancel_test"}))
        assert cancelled.status == "accepted", cancelled
        while time.monotonic() < deadline:
            host.tick()
            if host.store.latest_transition(run_id)["state"] == "CANCELLED": break
            time.sleep(0.005)
        assert host.store.latest_transition(run_id)["state"] == "CANCELLED"
        assert host.store.list_facts(run_id, "ExperimentPreregistered") == ()
    finally:
        host.close()
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_expired_profile_before_candidate_validation_opens_no_process_and_converges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "candidate-validation-expired-before-open"
    state_root = tmp_path / "state"; expired = [False]
    host = WorkflowHost(state_root); host.agent = _ExpireAtDraftResponseAgent(expired)
    started = []; original_start = host.runner.start

    def start(request, *, before_exec=None):
        started.append(request)
        return original_start(request, before_exec=before_exec)

    monkeypatch.setattr(workflow_host_module, "wallclock_expired",
                        lambda document, created_at: expired[0])
    monkeypatch.setattr(workflow_host_module, "wallclock_remaining_seconds",
                        lambda document, created_at: 0.0 if expired[0] else 3600.0)
    monkeypatch.setattr(host.runner, "start", start)
    try:
        host.initialize(); payload = _revision_payload(run_id, max_experiments=1)
        payload["research_contract"]["budget"]["wallclock_hours"] = 1
        created = host.handle(ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        _drive_to_completion(host, run_id)
        assert started == []
        assert not [row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]
        assessments = host.store.list_facts(run_id, "HypothesisAssessed")
        assert len(assessments) == 0
    finally:
        host.close()
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_profile_expiry_after_validation_intent_prevents_spawn_and_converges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "candidate-validation-expired-before-spawn"
    state_root = tmp_path / "state"; host = WorkflowHost(state_root)
    started = []; original_start = host.runner.start

    def start(request, *, before_exec=None):
        started.append(request)
        return original_start(request, before_exec=before_exec)

    monkeypatch.setattr(workflow_host_module, "wallclock_expired",
                        lambda document, created_at: False)
    monkeypatch.setattr(workflow_host_module, "wallclock_remaining_seconds",
                        lambda document, created_at: 3600.0)
    monkeypatch.setattr(host, "_run_wallclock_expired", lambda selected_run_id: True)
    monkeypatch.setattr(host.runner, "start", start)
    try:
        host.initialize(); payload = _revision_payload(run_id, max_experiments=1)
        payload["research_contract"]["budget"]["wallclock_hours"] = 1
        created = host.handle(ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        _drive_to_completion(host, run_id)
        assert started == []
        validations = [row for row in host.store.list_attempts()
                       if str(row["execution_id"]).endswith("--candidate-validation")]
        assert len(validations) == 1
        assert validations[0]["terminal_reason"] == "TIMED_OUT"
    finally:
        host.close()
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_execution_failure_creates_one_isolated_successor_with_failure_context(
    tmp_path: Path,
) -> None:
    run_id = "execution-successor-repair"
    state_root = tmp_path / "state"; agent = _RuntimeRepairAgent(); host = WorkflowHost(state_root)
    host.agent = agent
    try:
        host.initialize()
        response = host.handle(ControlRequest(schema_version=1, request_id=f"create-{run_id}",
            expected_revision=0, command="CREATE_RUN", payload=_revision_payload(run_id)))
        assert response.status == "accepted", response
        _drive_to_completion(host, run_id)
        experiments = host.store.list_facts(run_id, "ExperimentPreregistered")
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        evidences = host.store.list_facts(run_id, "EvidenceLinked")
        assessments = host.store.list_facts(run_id, "HypothesisAssessed")
        assert [row["experiment_id"] for row in experiments] == [
            f"{run_id}--component-1--experiment",
            f"{run_id}--component-1--experiment-2",
        ]
        assert experiments[1]["payload"]["revision_of_outcome_id"] == outcomes[0]["outcome_id"]
        assert [row["payload"]["outcome"] for row in outcomes] == ["EXECUTION_FAILURE", "SUCCESS"]
        assert [row["subject_id"] for row in evidences] == [
            f"{run_id}--component-1--evidence",
            f"{run_id}--component-1--evidence-2",
        ]
        assert len(assessments) == 1
        assert assessments[0]["payload"]["evidence_ids"] == [row["subject_id"] for row in evidences]
        assert len(agent.draft_requests) == 2
    finally:
        host.close()
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_invalid_result_envelope_creates_successor_with_evaluator_diagnostic(
    tmp_path: Path,
) -> None:
    run_id = "result-envelope-successor-repair"
    state_root = tmp_path / "state"; agent = _ResultEnvelopeRepairAgent(); host = WorkflowHost(state_root)
    host.agent = agent
    try:
        host.initialize()
        response = host.handle(ControlRequest(schema_version=1, request_id=f"create-{run_id}",
            expected_revision=0, command="CREATE_RUN", payload=_revision_payload(run_id)))
        assert response.status == "accepted", response
        _drive_to_completion(host, run_id)
        experiments = host.store.list_facts(run_id, "ExperimentPreregistered")
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        assessments = host.store.list_facts(run_id, "HypothesisAssessed")
        assert [row["experiment_id"] for row in experiments] == [
            f"{run_id}--component-1--experiment",
            f"{run_id}--component-1--experiment-2",
        ]
        assert experiments[1]["payload"]["revision_of_outcome_id"] == outcomes[0]["outcome_id"]
        assert [row["payload"]["outcome"] for row in outcomes] == ["EXECUTION_FAILURE", "SUCCESS"]
        assert len(assessments) == 1
        assert len(agent.draft_requests) == 2
        first_process = host.store.list_attempts(execution_id=str(outcomes[0]["execution_id"]))[0]
        assert first_process["terminal_reason"] == "SUCCEEDED"
    finally:
        host.close()

    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues

    connection = sqlite3.connect(state_root / "state.sqlite")
    try:
        connection.execute("DROP TRIGGER immutable_workflow_transitions_update")
        rowid, raw_payload = connection.execute(
            "SELECT rowid,payload_json FROM workflow_transitions WHERE run_id=? "
            "AND json_extract(payload_json,'$.outcome.validation_error') LIKE "
            "'invalid_result_shape@%'",
            (run_id,),
        ).fetchone()
        payload = json.loads(raw_payload)
        payload["outcome"]["validation_error"] = "invalid_identity@result.json.identity: forged"
        connection.execute(
            "UPDATE workflow_transitions SET payload_json=? WHERE rowid=?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), rowid),
        )
        connection.commit()
    finally:
        connection.close()
    tampered = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert "operational_outcome_causation_mismatch" in {
        issue.code for issue in tampered.issues
    }


def test_text_contract_failure_creates_successor_with_evaluator_diagnostic(
    tmp_path: Path,
) -> None:
    run_id = "text-contract-successor-repair"
    state_root = tmp_path / "state"
    agent = _TextContractRepairAgent()
    host = WorkflowHost(state_root)
    host.agent = agent
    try:
        host.initialize()
        response = host.handle(ControlRequest(
            schema_version=1,
            request_id=f"create-{run_id}",
            expected_revision=0,
            command="CREATE_RUN",
            payload=_text_contract_revision_payload(run_id),
        ))
        assert response.status == "accepted", response
        _drive_to_completion(host, run_id)

        experiments = host.store.list_facts(run_id, "ExperimentPreregistered")
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        evidences = host.store.list_facts(run_id, "EvidenceLinked")
        assessments = host.store.list_facts(run_id, "HypothesisAssessed")
        assert [row["experiment_id"] for row in experiments] == [
            f"{run_id}--component-1--experiment",
            f"{run_id}--component-1--experiment-2",
        ]
        assert experiments[1]["payload"]["revision_of_outcome_id"] == (
            outcomes[0]["outcome_id"]
        )
        assert [row["payload"]["outcome"] for row in outcomes] == [
            "EXECUTION_FAILURE",
            "SUCCESS",
        ]
        assert [row["subject_id"] for row in evidences] == [
            f"{run_id}--component-1--evidence",
            f"{run_id}--component-1--evidence-2",
        ]
        assert len(assessments) == 1
        assert assessments[0]["payload"]["assessment"] == "SUPPORTED"
        assert assessments[0]["payload"]["evidence_ids"] == [
            row["subject_id"] for row in evidences
        ]
        assert len(agent.draft_requests) == 2

        first_process = host.store.list_attempts(
            execution_id=str(outcomes[0]["execution_id"])
        )[0]
        assert first_process["terminal_reason"] == "SUCCEEDED"
        assert json.loads(first_process["observation_json"])["returncode"] == 0
        verdicts = host.store.list_facts(run_id, "GoalVerdictRecorded")
        assert len(verdicts) == 1
        assert verdicts[0]["payload"]["verdict"] == "GOAL_MET"
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
    finally:
        host.close()

    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(
        run_id
    )
    assert report.ok, report.issues


def test_unbound_optional_identity_creates_one_clean_successor(
    tmp_path: Path,
) -> None:
    run_id = "unbound-identity-successor-repair"
    state_root = tmp_path / "state"
    agent = _UnboundIdentityRepairAgent()
    host = WorkflowHost(state_root)
    host.agent = agent
    try:
        host.initialize()
        response = host.handle(ControlRequest(
            schema_version=1,
            request_id=f"create-{run_id}",
            expected_revision=0,
            command="CREATE_RUN",
            payload=_text_contract_revision_payload(run_id),
        ))
        assert response.status == "accepted", response
        _drive_to_completion(host, run_id)

        run = host.store.get_run(run_id)
        assert run is not None
        assert run["evaluator_version"] == OUTCOME_ADAPTER_VERSION
        experiments = host.store.list_facts(run_id, "ExperimentPreregistered")
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        assessments = host.store.list_facts(run_id, "HypothesisAssessed")
        assert [row["experiment_id"] for row in experiments] == [
            f"{run_id}--component-1--experiment",
            f"{run_id}--component-1--experiment-2",
        ]
        assert experiments[1]["payload"]["revision_of_outcome_id"] == (
            outcomes[0]["outcome_id"]
        )
        assert [row["payload"]["outcome"] for row in outcomes] == [
            "EXECUTION_FAILURE",
            "SUCCESS",
        ]
        assert outcomes[0]["payload"]["logical_artifacts"] == []
        assert {row["payload"]["evaluator_version"] for row in outcomes} == {
            OUTCOME_ADAPTER_VERSION
        }
        assert len(agent.draft_requests) == 2
        assert len(assessments) == 1
        assert assessments[0]["payload"]["assessment"] == "SUPPORTED"
        process_attempts = [
            host.store.list_attempts(execution_id=str(outcome["execution_id"]))[0]
            for outcome in outcomes
        ]
        assert [row["terminal_reason"] for row in process_attempts] == [
            "SUCCEEDED",
            "SUCCEEDED",
        ]
        assert all(
            Path(json.loads(row["observation_json"])["process_request"]["argv"][0]).name
            == "bwrap"
            for row in process_attempts
        )
        verdicts = host.store.list_facts(run_id, "GoalVerdictRecorded")
        assert len(verdicts) == 1
        assert verdicts[0]["payload"]["verdict"] == "GOAL_MET"
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
    finally:
        host.close()

    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(
        run_id
    )
    assert report.ok, report.issues


def test_extra_implementation_source_creates_one_clean_artifact_successor(
    tmp_path: Path,
    mount_fixture_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "artifact-inventory-successor-repair"
    state_root = tmp_path / "state"
    target, commit, tracked = _clean_target(mount_fixture_root)
    agent = _ArtifactInventoryRepairAgent()
    patch_checks: list[tuple[str, bytes]] = []
    original_check = workflow_host_module.ExecutionWorkspace.check_target_patch

    def checked_target_patch(self, **kwargs):
        patch_checks.append((str(kwargs["attempt_id"]), bytes(kwargs["patch"])))
        return original_check(self, **kwargs)

    monkeypatch.setattr(
        workflow_host_module.ExecutionWorkspace,
        "check_target_patch",
        checked_target_patch,
    )
    host = WorkflowHost(state_root)
    host.agent = agent
    try:
        host.initialize()
        response = host.handle(ControlRequest(
            schema_version=1,
            request_id=f"create-{run_id}",
            expected_revision=0,
            command="CREATE_RUN",
            payload=_artifact_inventory_revision_payload(run_id, target, commit),
        ))
        assert response.status == "accepted", response
        _drive_to_completion(host, run_id)

        run = host.store.get_run(run_id)
        assert run is not None
        # Historical /5-/6 interpretations remain Oracle compatibility concerns;
        # this production-shaped Host path proves only the current new-write version.
        assert run["evaluator_version"] == OUTCOME_ADAPTER_VERSION

        experiments = host.store.list_facts(run_id, "ExperimentPreregistered")
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        evidences = host.store.list_facts(run_id, "EvidenceLinked")
        assessments = host.store.list_facts(run_id, "HypothesisAssessed")
        assert [row["experiment_id"] for row in experiments] == [
            f"{run_id}--component-1--experiment",
            f"{run_id}--component-1--experiment-2",
        ]
        assert {row["payload"]["evaluator_version"] for row in experiments} == {
            OUTCOME_ADAPTER_VERSION
        }
        assert experiments[1]["payload"]["revision_of_outcome_id"] == (
            outcomes[0]["outcome_id"]
        )
        assert [row["payload"]["outcome"] for row in outcomes] == [
            "EXECUTION_FAILURE",
            "SUCCESS",
        ]
        assert outcomes[0]["payload"]["logical_artifacts"] == []
        assert {
            item["logical_artifact"]
            for item in outcomes[1]["payload"]["logical_artifacts"]
        } == set(_IMPLEMENTATION_ARTIFACTS)
        assert {row["payload"]["evaluator_version"] for row in outcomes} == {
            OUTCOME_ADAPTER_VERSION
        }
        assert [row["subject_id"] for row in evidences] == [
            f"{run_id}--component-1--evidence",
            f"{run_id}--component-1--evidence-2",
        ]
        assert len(assessments) == 1
        assert assessments[0]["payload"]["assessment"] == "SUPPORTED"
        assert assessments[0]["payload"]["evidence_ids"] == [
            row["subject_id"] for row in evidences
        ]

        expected_failure = {
            "outcome": "EXECUTION_FAILURE",
            "evaluator_version": OUTCOME_ADAPTER_VERSION,
            "metric_value": None,
            "criterion_met": None,
            "validation_error": _EXTRA_IMPLEMENTATION_DIAGNOSTIC,
            "execution_integrity_error": None,
        }
        expected_success = {
            "outcome": "SUCCESS",
            "evaluator_version": OUTCOME_ADAPTER_VERSION,
            "metric_value": None,
            "criterion_met": True,
            "validation_error": None,
            "execution_integrity_error": None,
        }
        assert agent.analyze_evaluations == [expected_failure, expected_success]
        assert agent.predecessor_evaluation == expected_failure
        assert len(agent.draft_requests) == 2
        assert not any(
            "--preflight-rendezvous--reply" in item.ref
            or "--analyze-rendezvous--reply" in item.ref
            for item in agent.draft_requests[0].input_artifacts
        )

        process_attempts = []
        for outcome in outcomes:
            matches = host.store.list_attempts(
                execution_id=str(outcome["execution_id"])
            )
            assert len(matches) == 1
            process_attempts.append(matches[0])
        assert len(process_attempts) == 2
        assert [row["terminal_reason"] for row in process_attempts] == [
            "SUCCEEDED",
            "SUCCEEDED",
        ]
        process_observations = [
            json.loads(row["observation_json"]) for row in process_attempts
        ]
        assert [item["returncode"] for item in process_observations] == [0, 0]
        assert [
            Path(item["process_request"]["argv"][0]).name
            for item in process_observations
        ] == ["bwrap", "bwrap"]

        origins, _, replies = host._rendezvous_records(run_id)
        predecessor_source_id = str(
            experiments[0]["payload"]["execution_contract"][
                "source_agent_attempt_id"
            ]
        )
        predecessor_process_id = str(process_attempts[0]["attempt_id"])
        expected_reviews = {}
        for phase, source_attempt_id in (
            ("preflight", predecessor_source_id),
            ("analyze", predecessor_process_id),
        ):
            matches = [
                origin
                for origin in origins
                if origin.phase == phase
                and origin.source_attempt_id == source_attempt_id
            ]
            assert len(matches) == 1
            expected_reviews[phase] = matches[0]
        expected_review_refs = [
            f"{expected_reviews[phase].rendezvous_id}--reply"
            for phase in ("preflight", "analyze")
        ]
        successor_inputs = {
            item.ref: item for item in agent.draft_requests[1].input_artifacts
        }
        assert [
            item.ref
            for item in agent.draft_requests[1].input_artifacts
            if item.ref in expected_review_refs
        ] == expected_review_refs
        assert [
            ref for ref in successor_inputs if ref.endswith("--reply")
        ] == expected_review_refs
        for phase in ("preflight", "analyze"):
            origin = expected_reviews[phase]
            assert json.loads(
                successor_inputs[f"{origin.rendezvous_id}--reply"].content_bytes()
            ) == replies[origin.rendezvous_id].to_dict()
        assert json.loads(
            successor_inputs[expected_review_refs[0]].content_bytes()
        )["summary"] == _PREFLIGHT_REVIEW_DIAGNOSTIC
        assert json.loads(
            successor_inputs[expected_review_refs[1]].content_bytes()
        )["summary"] == _ANALYZE_REVIEW_DIAGNOSTIC

        publication_roots = [
            state_root
            / "artifacts"
            / run_id
            / str(outcome["experiment_id"])
            / str(outcome["execution_id"])
            / str(attempt["attempt_id"])
            / "result"
            for outcome, attempt in zip(outcomes, process_attempts, strict=True)
        ]
        first_root, successor_root = publication_roots
        first_result = json.loads((first_root / "result.json").read_text())
        successor_result = json.loads((successor_root / "result.json").read_text())
        assert first_result["artifacts"] == successor_result["artifacts"]
        assert set(first_result["artifacts"]) == set(_IMPLEMENTATION_ARTIFACTS)
        assert first_result["identity"]["method_family_id"] == METHOD_FAMILY
        assert successor_result["identity"]["method_family_id"] == METHOD_FAMILY
        assert set(first_result["identity"]) == set(successor_result["identity"]) == {
            "method_family_id",
            "implementation_sha256",
            "core_config_sha256",
        }
        assert first_result["identity"]["core_config_sha256"] == (
            successor_result["identity"]["core_config_sha256"]
        )
        assert first_result["identity"]["implementation_sha256"] == (
            hashlib.sha256(_EXTRA_IMPLEMENTATION_BYTES).hexdigest()
        )

        declared_paths = {
            path
            for value in first_result["artifacts"].values()
            for path in ([value] if isinstance(value, str) else value)
        }
        first_paths = {
            path.relative_to(first_root).as_posix()
            for path in first_root.rglob("*")
            if path.is_file()
        }
        successor_paths = {
            path.relative_to(successor_root).as_posix()
            for path in successor_root.rglob("*")
            if path.is_file()
        }
        assert first_paths == declared_paths | {
            "result.json",
            _EXTRA_IMPLEMENTATION_FILE,
        }
        assert successor_paths == declared_paths | {"result.json"}
        assert (first_root / _EXTRA_IMPLEMENTATION_FILE).read_bytes() == (
            _EXTRA_IMPLEMENTATION_BYTES
        )
        implementation_patch = successor_result["artifacts"][
            "implementation_patch"
        ]
        assert isinstance(implementation_patch, str)
        first_patch = first_root / implementation_patch
        successor_patch = successor_root / implementation_patch
        assert first_patch.read_bytes() == _NONAPPLYING_IMPLEMENTATION_PATCH
        assert first_result["identity"]["implementation_sha256"] != (
            hashlib.sha256(first_patch.read_bytes()).hexdigest()
        )
        assert subprocess.run(
            [
                "git", "-C", str(target), "apply", "--no-index", "--check",
                "--whitespace=nowarn", str(first_patch),
            ],
            capture_output=True,
            check=False,
        ).returncode != 0
        assert subprocess.run(
            [
                "git", "-C", str(target), "apply", "--no-index", "--check",
                "--whitespace=nowarn", str(successor_patch),
            ],
            capture_output=True,
            check=False,
        ).returncode == 0
        assert successor_result["identity"]["implementation_sha256"] == (
            hashlib.sha256(successor_patch.read_bytes()).hexdigest()
        )
        expected_patch_check = (
            str(process_attempts[1]["attempt_id"]),
            successor_patch.read_bytes(),
        )
        # Evaluation runs once to prepare ANALYZE and once to accept the reviewed
        # outcome. Both checks must belong to the successor; the first candidate's
        # inventory failure must never reach the target-bound callback.
        assert patch_checks == [expected_patch_check, expected_patch_check]
        assert first_result["identity"]["implementation_sha256"] != (
            successor_result["identity"]["implementation_sha256"]
        )
        for relative in declared_paths - {implementation_patch}:
            assert (first_root / relative).read_bytes() == (
                successor_root / relative
            ).read_bytes()

        assert _git(target, "rev-parse", "HEAD") == commit
        assert _git(target, "status", "--porcelain=v1") == ""
        for relative, expected in tracked.items():
            assert (target / relative).read_bytes() == expected

        verdicts = host.store.list_facts(run_id, "GoalVerdictRecorded")
        assert len(verdicts) == 1
        assert verdicts[0]["payload"]["verdict"] == "GOAL_MET"
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
    finally:
        host.close()

    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(
        run_id
    )
    assert report.ok, report.issues


@pytest.mark.parametrize(
    ("method_family_id", "arm", "expected_outcome", "expected_metric", "assessment"),
    [
        (None, None, "SUCCESS", "3", "SUPPORTED"),
        ("wrong-family", None, "INTEGRITY_REJECTED", None, "INCONCLUSIVE"),
        (None, "bounded-cuda-checksum", "INTEGRITY_REJECTED", None, "INCONCLUSIVE"),
    ],
)
def test_optional_result_family_and_explicit_mismatch_have_canonical_outcomes(
    tmp_path: Path,
    method_family_id: str | None,
    arm: str | None,
    expected_outcome: str,
    expected_metric: str | None,
    assessment: str,
) -> None:
    run_id = f"result-family-{method_family_id or 'omitted'}"
    state_root = tmp_path / "state"; host = WorkflowHost(state_root)
    host.agent = _ResultIdentityAgent(method_family_id, arm=arm)
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN",
            _revision_payload(run_id, max_experiments=1),
        ))
        assert created.status == "accepted", created
        _drive_to_completion(host, run_id)
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        assert len(outcomes) == 1
        assert outcomes[0]["payload"]["outcome"] == expected_outcome
        assert outcomes[0]["payload"]["metric_value"] == expected_metric
        assert outcomes[0]["payload"]["logical_artifacts"] == []
        if arm is not None:
            assert outcomes[0]["payload"]["validation_code"] == "trial_completeness"
        assessments = host.store.list_facts(run_id, "HypothesisAssessed")
        assert len(assessments) == 1
        assert assessments[0]["payload"]["assessment"] == assessment
    finally:
        host.close()

    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_profile_experiment_limit_prevents_a_successor_and_closes_honestly(
    tmp_path: Path,
) -> None:
    run_id = "execution-successor-budget"
    state_root = tmp_path / "state"; agent = _RuntimeRepairAgent(); host = WorkflowHost(state_root)
    host.agent = agent
    try:
        host.initialize()
        response = host.handle(ControlRequest(schema_version=1, request_id=f"create-{run_id}",
            expected_revision=0, command="CREATE_RUN",
            payload=_revision_payload(run_id, max_experiments=1)))
        assert response.status == "accepted", response
        _drive_to_completion(host, run_id)
        assert len(host.store.list_facts(run_id, "ExperimentPreregistered")) == 1
        assert len(host.store.list_facts(run_id, "CanonicalOutcomeAccepted")) == 1
        assessment = host.store.list_facts(run_id, "HypothesisAssessed")
        assert len(assessment) == 1
        assert assessment[0]["payload"]["assessment"] == "INCONCLUSIVE"
        assert len(agent.draft_requests) == 1
    finally:
        host.close()
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_exhausted_agent_turn_budget_prevents_successor_and_assesses_predecessor(
    tmp_path: Path,
) -> None:
    run_id = "execution-successor-agent-budget"
    state_root = tmp_path / "state"; agent = _RuntimeRepairAgent(); host = WorkflowHost(state_root)
    host.agent = agent
    try:
        host.initialize(); payload = _revision_payload(run_id)
        payload["research_contract"]["budget"]["max_turns"] = 2
        created = host.handle(ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        _drive_to_completion(host, run_id)
        assert len(host.store.list_workflow_heads(run_id, "EXPERIMENT")) == 1
        assert len(host.store.list_facts(run_id, "ExperimentPreregistered")) == 1
        assessments = host.store.list_facts(run_id, "HypothesisAssessed")
        assert len(assessments) == 1
        assert assessments[0]["payload"]["assessment"] == "INCONCLUSIVE"
    finally:
        host.close()
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_successor_policy_and_transaction_recheck_profile_wallclock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "execution-successor-wallclock"
    state_root = tmp_path / "state"; expired = [False]
    host = WorkflowHost(state_root); host.agent = _RuntimeRepairAgent()
    monkeypatch.setattr(workflow_host_module, "wallclock_expired",
                        lambda document, created_at: expired[0])
    monkeypatch.setattr(workflow_host_module, "wallclock_remaining_seconds",
                        lambda document, created_at: 0.0 if expired[0] else 3600.0)
    try:
        host.initialize(); payload = _revision_payload(run_id)
        payload["research_contract"]["budget"]["wallclock_hours"] = 1
        created = host.handle(ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 10
        while not host.store.list_facts(run_id, "EvidenceLinked") and time.monotonic() < deadline:
            host.tick(); time.sleep(0.005)
        assert host.store.list_facts(run_id, "EvidenceLinked")
        current = host.store.latest_transition(run_id)
        stale_create = host.policy.next_command(host._policy_snapshot(current))
        assert stale_create is not None and stale_create.command == "CREATE_EXPERIMENT"

        expired[0] = True
        fresh = host.policy.next_command(host._policy_snapshot(current))
        assert fresh is not None and fresh.command == "RECORD_ASSESSMENT"
        host._create_experiment(run_id, stale_create)
        assert len(host.store.list_workflow_heads(run_id, "EXPERIMENT")) == 1
        _drive_to_completion(host, run_id)
        assessments = host.store.list_facts(run_id, "HypothesisAssessed")
        assert len(assessments) == 1
        assert assessments[0]["payload"]["assessment"] == "INCONCLUSIVE"
    finally:
        host.close()
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_host_executes_frozen_single_trial_target_instead_of_agent_wrapper(
    tmp_path: Path, mount_fixture_root: Path,
) -> None:
    run_id = "frozen-single-target"
    target, commit = _production_target(mount_fixture_root)
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root); host.agent = _ForbiddenWrapperAgent()
    try:
        host.initialize()
        response = host.handle(ControlRequest(schema_version=1, request_id=f"create-{run_id}",
            expected_revision=0, command="CREATE_RUN", payload=_production_payload(run_id, target, commit)))
        assert response.status == "accepted", response
        _drive_to_completion(host, run_id)
        outcome = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        assert len(outcome) == 1 and outcome[0]["payload"]["outcome"] == "SUCCESS"
        row = host.store.inspect("SELECT t.payload_json FROM workflow_transitions t JOIN workflow_commands c "
            "ON c.command_id=t.command_id WHERE t.run_id=? AND c.command_type='EXECUTE' "
            "AND json_type(t.payload_json,'$.process_request')='object'", (run_id,))[0]
        request = ProcessRequest.from_dict(json.loads(row["payload_json"])["process_request"])
        command = request.argv[request.argv.index("--") + 1:]
        assert Path(command[0]).name.startswith("python")
        assert command[1:] == (
            "-m", "frozen_target.runner", "--dataset", "FIXTURE", "--model", "candidate-v1",
            "--seed", "7", "--out", "/output/result.json", "--fixture", str(target / "data/fixture.txt"),
        )
    finally:
        host.close()
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues
    with sqlite3.connect(state_root / "state.sqlite") as connection:
        connection.execute("DROP TRIGGER immutable_research_facts_update")
        fact_id, raw = connection.execute("SELECT fact_id,payload_json FROM research_facts "
            "WHERE run_id=? AND fact_type='ExperimentPreregistered'", (run_id,)).fetchone()
        payload = json.loads(raw)
        payload["execution_contract"]["argv"] = ["python", "experiment.py"]
        connection.execute("UPDATE research_facts SET payload_json=? WHERE fact_id=?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), fact_id))
    tampered = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert "component_execution_contract_mismatch" in {item.code for item in tampered.issues}


def test_unrepresentable_single_trial_data_check_fails_at_create_run(
    tmp_path: Path, mount_fixture_root: Path,
) -> None:
    run_id = "frozen-data-preflight"
    target, commit = _production_target(mount_fixture_root)
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root); host.agent = _ForbiddenWrapperAgent()
    try:
        host.initialize()
        response = host.handle(ControlRequest(schema_version=1, request_id=f"create-{run_id}",
            expected_revision=0, command="CREATE_RUN",
            payload=_production_payload(run_id, target, commit, bad_data=True)))
        assert response.status == "rejected", response
        assert response.error is not None
        assert "semantic data checks require a Host-owned batch driver" in response.error["message"]
        assert not host.running
        assert not host.store.list_attempts()
    finally:
        host.close()


def test_component_host_closes_patch_metric_report_through_real_bwrap(
    tmp_path: Path,
    mount_fixture_root: Path,
) -> None:
    run_id = "component-host-g3"
    target, commit, tracked_before = _clean_target(mount_fixture_root)
    data_root = (mount_fixture_root / "read-only-data").resolve()
    data_root.mkdir()
    (data_root / "dataset.txt").write_text("read-only fixture\n", encoding="utf-8")
    (data_root / "dataset.txt").chmod(0o444)
    data_root.chmod(0o555)
    runtime = Path(sys.prefix).resolve()
    assert (runtime / "bin/python").is_file()
    state_root = tmp_path / "state"
    payload = _component_payload(
        run_id=run_id,
        target=target,
        commit=commit,
        runtime=runtime,
        data_root=data_root,
    )
    host = WorkflowHost(state_root)
    host.agent = _TopologyCheckingFakeAgent(target=target, data_root=data_root)
    try:
        host.initialize()
        created = host.handle(
            ControlRequest(
                schema_version=1,
                request_id=f"create-{run_id}",
                expected_revision=0,
                command="CREATE_RUN",
                payload=payload,
            )
        )
        assert created.status == "accepted", created
        _drive_to_completion(host, run_id)

        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        assert [row["payload"]["component_id"] for row in outcomes] == [
            "must.implementation",
            "primary.metric",
            "must.report",
        ]
        assert {row["payload"]["outcome"] for row in outcomes} == {"SUCCESS"}
        assert {row["payload"]["method_family_id"] for row in outcomes} == {
            METHOD_FAMILY
        }
        review_rows = host.store.inspect(
            "SELECT c.command_type,t.payload_json FROM workflow_transitions t "
            "JOIN workflow_commands c ON c.command_id=t.command_id "
            "WHERE t.run_id=? AND c.command_type IN "
            "('REQUEST_RENDEZVOUS','RESOLVE_RENDEZVOUS') ORDER BY t.rowid",
            (run_id,),
        )
        requests = [row for row in review_rows if row["command_type"] == "REQUEST_RENDEZVOUS"]
        resolutions = [row for row in review_rows if row["command_type"] == "RESOLVE_RENDEZVOUS"]
        assert len(requests) == len(resolutions) == 6
        origins = [json.loads(row["payload_json"])["rendezvous_origin"] for row in requests]
        assert {(origin["component_id"], origin["phase"]) for origin in origins} == {
            (component_id, phase)
            for component_id in ("must.implementation", "primary.metric", "must.report")
            for phase in ("preflight", "analyze")
        }
        verdicts = host.store.list_facts(run_id, "GoalVerdictRecorded")
        assert len(verdicts) == 1
        verdict = verdicts[0]["payload"]
        assert verdict["goal_id"] == f"{run_id}--goal"
        assert verdict["method_family_id"] == METHOD_FAMILY
        assert verdict["assessment_ids"] == [
            f"{run_id}--component-1--assessment",
            f"{run_id}--component-2--assessment",
            f"{run_id}--component-3--assessment",
        ]
        assert verdict["verdict"] == "GOAL_MET"

        rows = host.store.inspect(
            "SELECT payload_json FROM workflow_transitions "
            "WHERE run_id=? AND json_type(payload_json,'$.process_request')='object' "
            "ORDER BY rowid",
            (run_id,),
        )
        requests: dict[str, ProcessRequest] = {}
        for row in rows:
            transition = json.loads(row["payload_json"])
            requests[transition["component_id"]] = ProcessRequest.from_dict(
                transition["process_request"]
            )
        assert set(requests) == {
            "must.implementation",
            "primary.metric",
            "must.report",
        }
        expected_required_inputs = {
            "must.implementation": {},
            "primary.metric": {"implementation": ["/hypoloom-inputs/1/1"]},
            "must.report": {"metric-records": ["/hypoloom-inputs/1/1"]},
        }
        for component_id, request in requests.items():
            read_only = _argv_triples(request, "--ro-bind")
            writable = _argv_triples(request, "--bind")
            target_sources = [source for source, destination in read_only if destination == str(target)]
            assert target_sources == [
                str(state_root / "artifacts/work" / request.attempt_id / "target")
            ]
            assert (str(data_root), str(data_root)) in read_only
            assert (str(runtime), "/runtime") in read_only
            assert all(
                destination not in {str(target), str(data_root)}
                and not destination.startswith("/hypoloom-inputs/")
                for _, destination in writable
            )
            assert request.env == {}
            assert json.loads(
                _argv_value(request, "--setenv", "HYPOLOOM_REQUIRED_INPUTS")
            ) == expected_required_inputs[component_id]
            for paths in expected_required_inputs[component_id].values():
                for destination in paths:
                    assert any(bound_destination == destination for _, bound_destination in read_only)

        specifications = {
            row["payload"]["component_id"]: row["payload"]
            for row in host.store.list_facts(run_id, "ExperimentPreregistered")
        }
        for component_id, expected in expected_required_inputs.items():
            environment = dict(
                specifications[component_id]["execution_contract"]["environment"]
            )
            assert json.loads(environment["HYPOLOOM_REQUIRED_INPUTS"]) == expected

        assert not host.running
        assert not host.running_agents
        assert not host.store.inspect(
            "SELECT 1 FROM execution_attempts WHERE run_id=? AND state='OPEN'",
            (run_id,),
        )
    finally:
        host.close()
        data_root.chmod(0o755)

    assert _git(target, "rev-parse", "HEAD") == commit
    assert _git(target, "status", "--porcelain=v1") == ""
    assert not (target / "src/merc/models/multiemo_causal_candidate.py").exists()
    assert {
        relative: (target / relative).read_bytes() for relative in tracked_before
    } == tracked_before
    work_root = state_root / "artifacts/work"
    assert work_root.is_dir()
    assert not any(work_root.iterdir())
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues
    assert report.canonical_outcome_count == 3
    assert report.open_attempt_count == 0
    assert report.owned_process_residue == ()
