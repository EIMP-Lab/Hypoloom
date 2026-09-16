"""Independent Oracle reconstruction of admitted code/data mount separation."""

import json

import pytest

from hypoloom.oracle import (
    _OracleComponentExecution,
    _OracleMountBinding,
    _canonical_bwrap_argv,
    _oracle_component_environment_matches,
    _oracle_component_mounts,
)


TARGET = "/project/repository"


def _mounts(tmp_path, roots, *, scope="measurement", production=False):
    document = {
        "components": [{"id": "primary.score", "required_inputs": [], "protocol": "p"}],
        "safety": {"read_roots": roots},
        "runtime": {"target_repo": TARGET},
        "delivery_contracts": {"protocols": {"p": {"production": production}}},
    }
    payload = {"upstream_outcomes": []}
    if scope == "measurement":
        payload["measurement"] = {"metric": {"name": "score"}}
    elif scope is not None:
        payload["goal_evaluation"] = {"scope": scope}
    execution = _OracleComponentExecution(
        argv=("python", "experiment.py"),
        source_relative_path="experiment.py",
        target_root=TARGET,
        data_bindings=(("/legacy/data", "/hypoloom-data/cache"),) if production else (),
    )
    return _oracle_component_mounts(
        document, "primary.score", execution, {}, payload, [], {}, [], tmp_path, {}
    )


def _environment(roots, mapping=None):
    result = {
        "HYPOLOOM_RESULT_PATH": "result.json",
        "HYPOLOOM_TARGET_ROOT": TARGET,
        "HYPOLOOM_READ_ROOTS": json.dumps(roots, separators=(",", ":")),
        "HYPOLOOM_REQUIRED_INPUTS": "{}",
    }
    if mapping is not None:
        result["HYPOLOOM_READ_ROOT_MAP"] = json.dumps(
            mapping, sort_keys=True, separators=(",", ":")
        )
    return result


def _matches(environment, mounts):
    return _oracle_component_environment_matches(
        [list(item) for item in sorted(environment.items())], mounts
    )


@pytest.mark.parametrize("scope", ["measurement", "GOAL"])
def test_oracle_reconstructs_outer_alias_and_contained_original_ordinals(tmp_path, scope):
    mounts = _mounts(tmp_path, [TARGET + "/cache", "/other/data", TARGET], scope=scope)
    assert mounts is not None
    assert mounts.read_roots == ("/other/data", "/hypoloom-read-roots/3")
    assert mounts.bindings == (
        _OracleMountBinding(destination=TARGET, attempt_leaf="target"),
        _OracleMountBinding(source="/other/data", destination="/other/data"),
        _OracleMountBinding(source=TARGET, destination="/hypoloom-read-roots/3"),
    )
    assert _matches(_environment(list(mounts.read_roots), {
        TARGET: "/hypoloom-read-roots/3",
        TARGET + "/cache": "/hypoloom-read-roots/3/cache",
    }), mounts)


def test_oracle_maps_target_ancestor_and_its_contained_data_root(tmp_path):
    mounts = _mounts(tmp_path, ["/project/data", "/project"])
    assert mounts is not None
    assert mounts.read_roots == ("/hypoloom-read-roots/2",)
    assert _matches(_environment(list(mounts.read_roots), {
        "/project": "/hypoloom-read-roots/2",
        "/project/data": "/hypoloom-read-roots/2/data",
    }), mounts)


@pytest.mark.parametrize("scope", ["measurement", "GOAL", "LOCAL", None])
def test_oracle_disjoint_root_keeps_legacy_environment_bytes(tmp_path, scope):
    mounts = _mounts(tmp_path, ["/other/data"], scope=scope)
    assert mounts is not None
    assert mounts.read_roots == ("/other/data",)
    assert _matches(_environment(["/other/data"]), mounts)
    assert not _matches(_environment(["/other/data"], {}), mounts)


def test_oracle_preserves_production_driver_data_bindings(tmp_path):
    mounts = _mounts(tmp_path, [TARGET, "/other/data"], production=True)
    assert mounts is not None
    assert mounts.bindings == (
        _OracleMountBinding(destination=TARGET, attempt_leaf="target"),
        _OracleMountBinding(source="/legacy/data", destination="/hypoloom-data/cache"),
        _OracleMountBinding(source="/other/data", destination="/other/data"),
    )
    assert _matches(_environment([TARGET, "/other/data"]), mounts)


@pytest.mark.parametrize("damage", ["missing", "wrong-ordinal", "wrong-source", "missing-child", "roots", "code"])
def test_oracle_rejects_tampered_alias_environment(tmp_path, damage):
    mounts = _mounts(tmp_path, [TARGET, TARGET + "/cache"])
    assert mounts is not None
    mapping = {TARGET: "/hypoloom-read-roots/1", TARGET + "/cache": "/hypoloom-read-roots/1/cache"}
    environment = _environment(["/hypoloom-read-roots/1"], mapping)
    if damage == "missing":
        environment.pop("HYPOLOOM_READ_ROOT_MAP")
    elif damage == "wrong-ordinal":
        environment["HYPOLOOM_READ_ROOT_MAP"] = environment["HYPOLOOM_READ_ROOT_MAP"].replace("roots/1", "roots/2")
    elif damage == "wrong-source":
        environment["HYPOLOOM_READ_ROOT_MAP"] = json.dumps({"/unadmitted": "/hypoloom-read-roots/1"})
    elif damage == "missing-child":
        environment = _environment(["/hypoloom-read-roots/1"], {TARGET: "/hypoloom-read-roots/1"})
    elif damage == "roots":
        environment["HYPOLOOM_READ_ROOTS"] = json.dumps([TARGET])
    elif damage == "code":
        environment["HYPOLOOM_TARGET_ROOT"] = "/hypoloom-read-roots/1"
    assert not _matches(environment, mounts)


@pytest.mark.parametrize("damage", [None, "source", "writable", "target", "missing-map"])
def test_oracle_checks_actual_bwrap_alias_source_and_readonly_binding(tmp_path, damage):
    mounts = _mounts(tmp_path, [TARGET])
    assert mounts is not None
    environment = _environment(["/hypoloom-read-roots/1"], {TARGET: "/hypoloom-read-roots/1"})
    attempt = "experiment--process-1"
    work = tmp_path / "work" / attempt
    data_source = "/unadmitted" if damage == "source" else TARGET
    target_source = str(work / "target") if damage != "target" else TARGET
    argv = [
        "/usr/bin/bwrap", "--unshare-all", "--die-with-parent", "--new-session",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--dir", "/runtime", "--ro-bind", "/runtime-prefix", "/runtime",
        "--dir", "/input", "--dir", "/output",
        "--ro-bind", str(work / "input"), "/input",
        "--bind", str(work / "output"), "/output",
        "--ro-bind", target_source, TARGET,
        "--bind" if damage == "writable" else "--ro-bind", data_source, "/hypoloom-read-roots/1",
    ]
    if damage == "missing-map":
        environment.pop("HYPOLOOM_READ_ROOT_MAP")
    for name, value in sorted(environment.items()):
        argv.extend(("--setenv", name, value))
    argv.extend(("--chdir", "/output", "--", "/runtime/bin/python", "/input/experiment.py"))
    assert _canonical_bwrap_argv(
        argv, attempt_id=attempt, source_relative_path="experiment.py",
        execution_argv=("python", "experiment.py"), expected_bindings=(),
        expected_environment=environment, expected_component_mounts=mounts,
        allow_generic=True,
    ) is (damage is None)
