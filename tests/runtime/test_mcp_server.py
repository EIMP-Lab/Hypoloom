from __future__ import annotations

import ast
import io
import json
from pathlib import Path

from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import FACT_SCHEMA_VERSION
from hypoloom.mcp_server import handle_request, main
from hypoloom.profile_admission import admit_profile
from hypoloom.workflow_host import WorkflowHost


_ROOT = Path(__file__).resolve().parents[2]


def _completed_run(root: Path, run_id: str) -> None:
    host = WorkflowHost(root)
    host.initialize()
    try:
        admission = admit_profile(
            _ROOT / "tests/fixtures/profiles/component-demo",
            run_id=run_id,
            allow_gpu=False,
            delay_seconds=0,
        )
        assert admission.admitted, admission.to_dict()
        assert admission.create_run_payload is not None
        response = host.handle(
            ControlRequest(
                1,
                f"create-{run_id}",
                0,
                "CREATE_RUN",
                admission.create_run_payload,
            )
        )
        assert response.status == "accepted"
        for _ in range(1000):
            if not host.tick():
                break
            head = host.store.latest_transition(run_id)
            if head is not None and head["state"] == "COMPLETED":
                break
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
    finally:
        host.close()


def test_explicit_read_only_tools_list_and_inspect_one_run(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    _completed_run(state_root, "mcp-demo")
    # SQLite may create transient WAL shared-memory coordination sidecars for
    # a query-only reader.  Compare the durable database and artifact bytes,
    # which are the state this facade promises not to mutate.
    durable_paths = (
        state_root / "state.sqlite",
        *sorted((state_root / "artifacts").rglob("*")),
    )
    before = {
        path.relative_to(state_root): path.read_bytes()
        for path in durable_paths
        if path.is_file()
    }

    listed = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "hypoloom_list_runs",
                "arguments": {"state_root": str(state_root)},
            },
        }
    )
    inspected = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "hypoloom_inspect_run",
                "arguments": {
                    "state_root": str(state_root),
                    "run_id": "mcp-demo",
                },
            },
        }
    )

    assert listed is not None and inspected is not None
    listed_payload = json.loads(listed["result"]["content"][0]["text"])
    assert listed_payload["runs"] == [
        {"revision": 9, "run_id": "mcp-demo", "state": "COMPLETED"}
    ]
    inspected_payload = json.loads(inspected["result"]["content"][0]["text"])
    assert inspected_payload["status"]["run_state"] == "COMPLETED"
    assert inspected_payload["goal"]["schema_version"] == FACT_SCHEMA_VERSION
    contract = inspected_payload["goal"]["research_contract"]
    assert [role["id"] for role in contract["roles"]] == [
        "scientist-a",
        "scientist-b",
        "scientist-c",
    ]
    assert [component["id"] for component in contract["components"]] == [
        "primary.score"
    ]
    assert inspected_payload["oracle"]["ok"] is True
    after = {
        path.relative_to(state_root): path.read_bytes()
        for path in durable_paths
        if path.is_file()
    }
    assert after == before


def test_stdio_protocol_and_errors_are_bounded() -> None:
    source = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        + "\n"
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "missing", "arguments": {}},
            }
        )
        + "\n"
    )
    destination = io.StringIO()
    assert main(source, destination) == 0
    responses = [json.loads(line) for line in destination.getvalue().splitlines()]
    assert responses[0]["result"]["serverInfo"]["name"] == "hypoloom"
    assert {item["name"] for item in responses[1]["result"]["tools"]} == {
        "hypoloom_inspect_run",
        "hypoloom_list_runs",
    }
    assert responses[2]["error"]["code"] == -32601


def test_mcp_facade_imports_no_legacy_runtime() -> None:
    path = Path(__file__).resolve().parents[2] / "hypoloom/mcp_server.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    assert "hypoloom" not in imports
    assert not any(name.startswith("hypoloom.") for name in imports)
