import json
import sys

import pytest

from hypoloom import bootstrap, bootstrap_inspect, bootstrap_questions
from tests.runtime.test_bootstrap import checkout, arguments  # noqa: F401


def inspect_profile(checkout, capsys, monkeypatch, language="zh-CN"):
    repo, overrides = checkout
    assert bootstrap.main([*arguments(repo, overrides), "--write"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(bootstrap_inspect, "supervisor_check", lambda: {"status": "ready"})
    argv = ["inspect", "topic", "--repository", str(repo), "--cpu-only", "--offline",
            "--auth-transport", "none", "--institutional-browser-mode", "client", "--ui-language", language]
    assert bootstrap.main(argv) == 0
    return repo, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("language", ["en", "zh-CN"])
@pytest.mark.parametrize("choice", ["all", "none", "subset"])
def test_native_answers_reach_exact_review_and_session_without_start(checkout, capsys, monkeypatch, tmp_path, language, choice):
    repo, first = inspect_profile(checkout, capsys, monkeypatch, language)
    monkeypatch.setattr(bootstrap, "start_fresh", lambda *a, **k: pytest.fail("answer started research"))
    interaction = first["interaction"]
    questions = interaction["questions"]
    assert [len(q["options"]) for q in questions] == [4, 4, 3]
    assert all(q["multiSelect"] for q in questions)
    all_names = [o["label"] for q in questions for o in q["options"][:-1]]
    assert all_names == ["IEEE", "ACM", "ScienceDirect", "Springer Nature", "Wiley", "SAGE", "Oxford", "Taylor & Francis"]
    answers = {}
    expected = []
    for question in questions:
        labels = [o["label"] for o in question["options"]]
        picked = labels[:-1] if choice == "all" else labels[-1:] if choice == "none" else labels[1:2]
        answers[question["question"]] = ", ".join(picked)
        if choice != "none": expected.extend(picked)
    source = tmp_path / "answers.json"
    source.write_text(json.dumps({"answers": answers}))
    assert bootstrap.main([*interaction["answer_argv"][4:-1], str(source)]) == 0
    review = json.loads(capsys.readouterr().out)
    assert review["identity"] == first["identity"]
    assert review["interaction"]["questions"] == []
    assert review["effective_configuration"]["institutional_preparation"]["selected"] == expected
    parsed = bootstrap.build_parser().parse_args(review["launch_argv"][4:])
    assert list(dict.fromkeys(parsed.institutional_prepare_provider)) == (expected or ["none"])
    assert bool(review.get("init_session_argv")) == bool(expected)
    assert not (repo / "state").exists()


@pytest.mark.parametrize("bad", ["partial", "renamed", "mixed", "unlisted"])
def test_incomplete_or_invented_publisher_answers_do_not_advance(checkout, capsys, monkeypatch, tmp_path, bad):
    repo, first = inspect_profile(checkout, capsys, monkeypatch)
    questions = first["interaction"]["questions"]
    answers = {q["question"]: q["options"][0]["label"] for q in questions}
    if bad == "partial": answers.pop(questions[0]["question"])
    if bad == "renamed": answers["model rewritten question"] = answers.pop(questions[0]["question"])
    if bad == "mixed": answers[questions[0]["question"]] += ", " + questions[0]["options"][-1]["label"]
    if bad == "unlisted": answers[questions[0]["question"]] = "Taylor & Francis"
    source = tmp_path / "answers.json"; source.write_text(json.dumps(answers))
    assert bootstrap.main([*first["interaction"]["answer_argv"][4:-1], str(source)]) == 2
    assert not capsys.readouterr().out and not (repo / "state").exists()


def test_initial_questions_and_continuation_preserve_explicit_cpu_and_defaults(tmp_path):
    args = bootstrap.build_parser().parse_args(["inspect", "--cpu-only", "--offline", "--ui-language", "zh-CN"])
    result = {"missing": ["profile", "institutional_browser_mode"],
              "paths": {"local_profiles": ["first", "second"], "profile_templates": []}}
    first = bootstrap_questions.interaction(args, result, tmp_path)
    response = {q["question"]: q["options"][0]["label"] for q in first["questions"]}
    bootstrap_questions.apply_answers(args, result, response)
    result["missing"] = []
    current = bootstrap_questions.interaction(args, result, tmp_path)
    parsed = bootstrap.build_parser().parse_args(current["inspect_argv"][4:])
    assert parsed.profile == "first" and parsed.allow_gpu is False and parsed.offline
    assert parsed.institutional_browser_mode == "client" and parsed.ui_language == "zh-CN"
    assert parsed.control_python == sys.executable


def test_new_profile_option_is_actionable_without_a_model_chosen_name(tmp_path):
    args = bootstrap.build_parser().parse_args(["inspect"])
    result = {"missing": ["profile"], "paths": {"local_profiles": ["research"],
              "profile_templates": ["multi-expert-en", "multi-expert-zh"]}}
    question = bootstrap_questions.questions(args, result)[0][1]
    bootstrap_questions.apply_answers(args, result, {question["question"]: "New: multi-expert-zh"})
    assert args.profile == "research-2" and args.template == "multi-expert-zh"
    assert not list(tmp_path.iterdir())


def test_gpu_answer_authorizes_only_displayed_devices_and_detects_changed_inventory():
    args = bootstrap.build_parser().parse_args(["inspect", "--allow-gpu"])
    result = {"missing": ["gpu_device_uuid", "gpu_scheduling_mode"],
              "checks": {"gpu_inventory": {"devices": [{"uuid": "GPU-a"}, {"uuid": "GPU-b"}]}}}
    answer = {q["question"]: q["options"][0]["label"] for _, q, _ in bootstrap_questions.questions(args, result)}
    result["checks"]["gpu_inventory"]["devices"].append({"uuid": "GPU-c"})
    with pytest.raises(ValueError, match="stale"):
        bootstrap_questions.apply_answers(args, result, answer)
    assert args.gpu_device_uuid is None
    result["checks"]["gpu_inventory"]["devices"].pop()
    bootstrap_questions.apply_answers(args, result, answer)
    assert args.gpu_device_uuid == ["GPU-a", "GPU-b"] and args.managed_pool


@pytest.mark.parametrize("selected", ["configured-model", "replacement-model"])
def test_model_selection_is_explicit_and_survives_next_inspect(selected, tmp_path):
    args = bootstrap.build_parser().parse_args(["inspect", "--cpu-only"])
    result = {"missing": ["claude_model"], "choices": {"claude_model": "configured-model"}}
    question = bootstrap_questions.questions(args, result)[0][1]
    assert "configured-model" in question["question"]
    assert args.claude_model is None
    bootstrap_questions.apply_answers(args, result, {question["question"]: selected})
    result["missing"] = []
    interaction = bootstrap_questions.interaction(args, result, tmp_path)
    again = bootstrap.build_parser().parse_args(interaction["inspect_argv"][4:])
    assert again.claude_model == selected


def test_inspect_does_not_silently_accept_settings_model(checkout, capsys, monkeypatch):
    repo, _ = checkout
    monkeypatch.setattr(bootstrap_inspect, "_auth", lambda *a: {
        "status": "ready", "auth_source": "settings", "settings_path": "/tmp/synthetic-settings.json",
        "configured_default": {"claude_model": "configured-model"}})
    monkeypatch.setattr(bootstrap_inspect, "supervisor_check", lambda: {"status": "ready"})
    argv = ["inspect", "--repository", str(repo), "--cpu-only", "--offline", "--claude-executable", "/bin/true",
        "--institutional-browser-mode", "off"]
    assert bootstrap.main(argv) == 0
    result = json.loads(capsys.readouterr().out)
    assert "claude_model" in result["missing"] and result["choices"]["claude_model"] == "configured-model"
    assert any(q["header"] == "Base model" for q in result["interaction"]["questions"])
    assert bootstrap.main([*argv, "--claude-model", "explicit-model"]) == 0
    assert "claude_model" not in json.loads(capsys.readouterr().out)["missing"]
