"""Public support copies must not expose private values through diagnostic metadata."""
from __future__ import annotations

import json
import io
import zipfile

import pytest

from hypoloom.support_bundle import export_support_bundle
from test_debug_mode import _seed_agent_retry


def test_default_copy_omits_research_values_and_paths_from_validation_errors(tmp_path):
    root = tmp_path / "private-cohort-state"
    secret_text = "PRIVATE_COHORT_LABEL_284"
    local_path = "/home/example_researcher/private_project/cohort.tsv"
    run_id, first, second = _seed_agent_retry(root, first_diagnostics={
        "structured_output_correction": {
            "scope": "native_stop_validation", "validation_source": "host_stop_hook",
            "status": "unresolved", "accepted": False, "call_count": 1, "rejected_count": 1,
            "rounds": [{"ordinal": 1, "status": "rejected",
                "validation_error": f"Invalid value {secret_text}; source {local_path}"}]}})
    output = tmp_path / "public"
    export_support_bundle(root, run_id, output)
    data = b"\n".join(path.read_bytes() for path in output.rglob("*") if path.is_file())
    for private in (secret_text, local_path, str(root), run_id, first, second):
        assert private.encode() not in data
    debug = json.loads((output / "debug.json").read_text())
    failed, recovered = debug["attempts"]
    assert recovered["retry_of_attempt_id"] == failed["attempt_id"]
    assert failed["failure_code"] == "literature_openalex_response_invalid"
    assert failed["structured_output_correction"]["rejected_count"] == 1
    assert failed["structured_output_correction"]["rounds"][0]["status"] == "rejected"


@pytest.mark.parametrize("corrupt", [False, True])
def test_support_copy_survives_missing_or_unreadable_run_database(tmp_path, corrupt):
    root = tmp_path / "state"
    (root / "diagnostics").mkdir(parents=True)
    (root / "diagnostics/host.jsonl").write_text(json.dumps({
        "run_id": None if not corrupt else "failed-run", "phase": "serve",
        "exception_type": "OSError", "message": "private startup detail"}) + "\n")
    if corrupt:
        (root / "state.sqlite").write_bytes(b"broken database\n")
    output = tmp_path / "support"
    report = export_support_bundle(root, "failed-run" if corrupt else None, output)
    assert report["diagnosis"]["available"] is False
    assert report["completeness"] == "observed_files_only"
    debug = json.loads((output / "debug.json").read_text())
    assert debug["oracle"]["available"] is False
    assert any(item.get("record_count") == 1 for item in report["attachments"])
    data = b"\n".join(path.read_bytes() for path in output.rglob("*") if path.is_file())
    assert b"OSError" in data and b"private startup detail" not in data
    assert not (root / "state.sqlite").exists() if not corrupt else (root / "state.sqlite").read_bytes() == b"broken database\n"


def test_preview_zip_and_limits_use_the_same_sanitized_copy(tmp_path, monkeypatch):
    import hypoloom.support_bundle as support
    monkeypatch.setattr(support, "support_environment", lambda: {"scope": "exporter_environment_now"})
    root = tmp_path / "state"
    run_id, first, _ = _seed_agent_retry(root)
    capture = root / "diagnostics/agents" / run_id / first
    capture.mkdir(parents=True)
    (capture / "stdout.log").write_text("private data " * 1000)
    for index in range(15):
        (capture / f"file-{index}.txt").write_text("short private attachment\n")
    report, files = support.build_support_bundle(root, run_id, include_private_data=True,
        max_bytes=65536, max_file_bytes=256, max_files=8)
    assert report["limited"]
    assert len(files) <= 8 and sum(map(len, files.values())) <= 65536
    assert any(item["reason"] == "file_size_limit" for item in report["attachments"])
    assert all(b"private data " not in raw for raw in files.values())
    with zipfile.ZipFile(io.BytesIO(support.support_zip(files))) as archive:
        assert set(archive.namelist()) == set(files)
        for item in archive.infolist():
            assert archive.read(item) == files[item.filename]
            assert (item.external_attr >> 16) & 0o777 == 0o600
    assert not (tmp_path / "output").exists()


def test_startup_export_excludes_other_runs_and_exact_attempt_typos_fail(tmp_path):
    from hypoloom.support_bundle import SupportBundleError
    root = tmp_path / "state"
    run_id, _, _ = _seed_agent_retry(root)
    diagnostics = root / "diagnostics"
    diagnostics.mkdir()
    (diagnostics / "host.jsonl").write_text("\n".join(json.dumps(item) for item in [
        {"run_id": None, "exception_type": "OSError"},
        {"run_id": run_id, "exception_type": "ForeignRunError"}]) + "\n")
    output = tmp_path / "startup"
    export_support_bundle(root, None, output)
    encoded = b"\n".join(path.read_bytes() for path in output.rglob("*") if path.is_file())
    assert b"OSError" in encoded and b"ForeignRunError" not in encoded
    with pytest.raises(SupportBundleError, match="attempt not found"):
        export_support_bundle(root, run_id, tmp_path / "wrong", attempt_id="not-in-this-run")
    assert not (tmp_path / "wrong").exists()


def test_cli_preview_and_zip_work_without_a_created_run(tmp_path, capsys):
    from hypoloom.cli import main
    root = tmp_path / "not-created"
    args = ["debug", "export", "--state-root", str(root), "--support-bundle"]
    assert main([*args, "--preview"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "preview" and preview["report"]["diagnosis"]["available"] is False
    assert not root.exists()
    output = tmp_path / "report.zip"
    assert main([*args, "--archive", "--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["diagnosis_available"] is False
    assert output.stat().st_mode & 0o777 == 0o600
    with zipfile.ZipFile(output) as archive:
        assert {"README.md", "report.json", "debug.json"} <= set(archive.namelist())
    assert not root.exists()


def test_recent_failure_selection_retains_recovery_and_private_mode_is_explicit(tmp_path):
    from hypoloom.support_bundle import build_support_bundle
    root = tmp_path / "state"
    run_id, first, second = _seed_agent_retry(root)
    report, files = build_support_bundle(root, run_id, recent_failures=True, include_private_data=True)
    projection = json.loads(files["debug.json"])
    assert report["selection"]["attachment_attempt_count"] == 2
    assert {row["attempt_id"] for row in projection["attempts"]} == {first, second}
    assert projection["attempts"][1]["retry_of_attempt_id"] == first
    assert "commands" not in projection["related_records"]


def test_admitted_profile_summary_includes_counts_but_not_profile_text(tmp_path):
    from hypoloom.support_bundle import build_support_bundle
    from test_web_observer import _create_fresh_run
    root = tmp_path / "state"
    _create_fresh_run(root, "profile-shape")
    report, _ = build_support_bundle(root, "profile-shape")
    assert report["profile_summary"]["available"]
    assert report["profile_summary"]["expert_count"] == 3
    assert report["profile_summary"]["profile_text_included"] is False
    assert report["environment"]["scope"] == "exporter_environment_now"
    assert "state_root" not in report["environment"]


@pytest.mark.parametrize("recent_failures", [False, True])
def test_unreadable_database_keeps_scoped_capture_without_inventing_attempts(tmp_path, recent_failures):
    from hypoloom.support_bundle import build_support_bundle
    root = tmp_path / "state"
    for run in ("selected-run", "foreign-run"):
        capture = root / "diagnostics/agents" / run / "capture-id"
        capture.mkdir(parents=True)
        (capture / "events.jsonl").write_text(json.dumps({"kind": "started", "attempt_id": "capture-id"}) + "\n")
        (capture / "stderr.log").write_text(run + " private detail")
    report, files = build_support_bundle(root, "selected-run", include_private_data=True, recent_failures=recent_failures)
    assert not report["diagnosis"]["available"]
    assert report["selection"]["diagnostic_only_capture_count"] == 1
    assert report["selection"]["attachment_attempt_count"] == 0
    assert any(item.get("attempt_id") == "capture-id" for item in report["attachments"])
    assert report["agent_captures"][0]["identity_source"] == "diagnostic_directory_only"
    assert json.loads(files["debug.json"])["attempts"] == []
    assert b"selected-run private detail" in b"\n".join(files.values())
    assert b"foreign-run private detail" not in b"\n".join(files.values())


def test_credentials_repeated_in_metadata_and_host_messages_are_redacted(tmp_path):
    from hypoloom.support_bundle import build_support_bundle
    root = tmp_path / "state"
    run_id, first, _ = _seed_agent_retry(root)
    capture = root / "diagnostics/agents" / run_id / first / "provider/000001"
    capture.mkdir(parents=True)
    token = "named-credential-that-was-echoed"
    (capture / "exchange.json").write_text(json.dumps({"api_key": token, "status": 401,
        "response_headers": {"x-request-id": token}}))
    (root / "diagnostics/host.jsonl").write_text(json.dumps({"run_id": run_id,
        "api_key": token, "message": "upstream echoed " + token}) + "\n")
    for private in (False, True):
        _, files = build_support_bundle(root, run_id, include_private_data=private)
        assert token.encode() not in b"\n".join(files.values())


def test_large_private_projection_keeps_diagnosis_when_payloads_do_not_fit(tmp_path):
    from hypoloom.support_bundle import build_support_bundle
    root = tmp_path / "state"
    run_id, first, second = _seed_agent_retry(root, first_diagnostics={"private_note": "large payload " * 50000})
    report, files = build_support_bundle(root, run_id, include_private_data=True)
    debug = json.loads(files["debug.json"])
    assert {row["attempt_id"] for row in debug.get("attempts", [])} == {first, second}
    assert debug["attempts"][1]["retry_of_attempt_id"] == first
    assert debug["run_versions"] and debug["oracle"]["available"]
    assert all("observation" not in row for row in debug["attempts"])
    assert report["limited"]
    assert "private_debug_payloads_exceed_size_limit" in report["attachments"][0]["redactions"]
    assert "observation" in report["attachments"][0]["omitted_fields"]
