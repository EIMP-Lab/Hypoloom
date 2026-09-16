import copy

import pytest

from hypoloom.shared_acceptance import SharedAcceptance, SharedAcceptanceError, acceptance_source_files, evaluate_shared_acceptance


def acceptance_dict():
    return {"summary": "Both held-out measurements from one candidate, with the required audit.",
        "rule": "ALL", "criteria": [{"name": "alpha", "operator": "GT", "target": "0.6674"},
            {"name": "beta", "operator": "GT", "target": "0.7305"}],
        "validity_checks": ["causal"], "parser_argv": ["python", "parse_goal.py"],
        "parser_files": ["parse_goal.py"], "raw_artifacts": ["raw.json"]}


def test_strict_and_requires_each_measurement_and_all_validity():
    declaration = SharedAcceptance.from_dict(acceptance_dict())
    assert declaration.to_dict() == acceptance_dict()
    assert evaluate_shared_acceptance(declaration, {"measurements": {"alpha": ".7", "beta": ".8"}, "checks": {"causal": True}})
    for alpha, beta in (("0.6674", ".8"), (".7", "0.7305"), (".8", ".7")):
        assert not evaluate_shared_acceptance(declaration, {"measurements": {"alpha": alpha, "beta": beta}, "checks": {"causal": True}})
    assert not evaluate_shared_acceptance(declaration, {"measurements": {"alpha": ".9", "beta": ".9"}, "checks": {"causal": False}})


def test_any_never_bypasses_mandatory_validity():
    declaration = SharedAcceptance.from_dict({**acceptance_dict(), "rule": "ANY"})
    raw = {"measurements": {"alpha": ".7", "beta": ".5"}, "checks": {"causal": True}}
    assert evaluate_shared_acceptance(declaration, raw)
    assert not evaluate_shared_acceptance(declaration, {**raw, "checks": {"causal": False}})


def test_artifact_only_needs_no_invented_metric():
    declaration = SharedAcceptance.from_dict({**acceptance_dict(), "criteria": [{"name": "package_valid", "operator": "IS_TRUE"}], "validity_checks": []})
    assert evaluate_shared_acceptance(declaration, {"measurements": {}, "checks": {"package_valid": True}})
    assert not evaluate_shared_acceptance(declaration, {"measurements": {}, "checks": {"package_valid": False}})


@pytest.mark.parametrize("operator,target,value,expected", [("GE", "2", "2", True), ("LT", "2", "2", False),
    ("LE", "2", "2", True), ("EQ", "2.00", "2", True), ("EQ", "2", "2.01", False)])
def test_scalar_operators_are_decimal_exact(operator, target, value, expected):
    declaration = SharedAcceptance.from_dict({**acceptance_dict(), "criteria": [{"name": "value", "operator": operator, "target": target}], "validity_checks": []})
    assert evaluate_shared_acceptance(declaration, {"measurements": {"value": value}, "checks": {}}) is expected


@pytest.mark.parametrize("change", [{"measurements": {"alpha": ".7"}}, {"measurements": {"alpha": "NaN", "beta": ".8"}},
    {"measurements": {"alpha": True, "beta": ".8"}}, {"checks": {}}, {"checks": {"causal": 1}},
    {"goal_met": True}, {"measurements": {"alpha": ".7", "beta": ".8", "hidden": "1"}}])
def test_missing_malformed_and_agent_verdict_outputs_fail_closed(change):
    raw = {"measurements": {"alpha": ".7", "beta": ".8"}, "checks": {"causal": True}, **change}
    with pytest.raises(SharedAcceptanceError):
        evaluate_shared_acceptance(SharedAcceptance.from_dict(acceptance_dict()), raw)


@pytest.mark.parametrize("change", [{"criteria": []}, {"rule": "all"}, {"parser_files": ["../escape.py"]},
    {"criteria": [{"name": "a", "operator": "GT", "target": "Infinity"}]},
    {"criteria": [{"name": "a", "operator": "IS_TRUE", "target": "1"}]},
    {"validity_checks": ["causal", "causal"]}])
def test_invalid_declarations_fail_closed(change):
    with pytest.raises(SharedAcceptanceError):
        SharedAcceptance.from_dict({**acceptance_dict(), **change})


def test_source_selection_requires_the_declared_actual_bytes():
    declaration = SharedAcceptance.from_dict(acceptance_dict())
    assert acceptance_source_files(declaration, {"parse_goal.py": b"print('{}')", "unrelated": b"x"}) == {"parse_goal.py": b"print('{}')"}
    for files in ({}, {"parse_goal.py": b""}):
        with pytest.raises(SharedAcceptanceError):
            acceptance_source_files(declaration, files)
    raw = acceptance_dict()
    declaration = SharedAcceptance.from_dict(raw)
    changed = copy.deepcopy(raw)
    changed["criteria"][0]["target"] = "0"
    assert declaration.to_dict() != changed
