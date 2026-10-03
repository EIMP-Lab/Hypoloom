"""Profile authoring contains secrets; admitted/Agent-facing data never does."""
import json

import pytest
import yaml

from hypoloom import bootstrap, cli
from hypoloom.credentials import CredentialError, read_credentials_file
from hypoloom.notification_tailer import notification_preflight, notification_remote
from hypoloom.profile_admission import admit_profile
from test_bootstrap import checkout as checkout, arguments


VALUES = ("synthetic-openalex-private", "private-contact@example.invalid",
          "123456:synthetic_bot_private", "-100987654321", "synthetic-webhook-private")


def private_profile(checkout, capsys):
    repo, overrides = checkout
    assert bootstrap.main([*arguments(repo, overrides), "--write"]) == 0
    capsys.readouterr()
    root = repo / "profiles/local/topic"
    method = yaml.safe_load((root / "methodology.yaml").read_text())
    method["literature"].pop("credentials_file", None)
    method["literature"].update(openalex_api_key=VALUES[0], unpaywall_email=VALUES[1])
    (root / "methodology.yaml").write_text(yaml.safe_dump(method))
    notify = yaml.safe_load((root / "notify.yaml").read_text())
    notify["sinks"]["telegram"] = {"enabled": True, "bot_token": VALUES[2], "chat_id": VALUES[3]}
    notify["sinks"]["webhook"] = {"enabled": True, "url": "http://127.0.0.1:12345/events", "token": VALUES[4]}
    (root / "notify.yaml").write_text(yaml.safe_dump(notify))
    return root


def test_admission_projects_only_file_references_and_consumers_read_profile(checkout, capsys, tmp_path):
    root = private_profile(checkout, capsys)
    report = admit_profile(root, run_id="private-profile")
    assert report.admitted, report.to_dict()
    raw = json.dumps(report.to_dict())
    assert all(value not in raw for value in VALUES)
    product = report.create_run_payload["research_contract"]["methodology"]["controls"]["product"]
    assert product["methodology"]["literature"]["credentials_file"] == str(root / "methodology.yaml")
    config = product["notify"]
    assert config["sinks"]["telegram"]["credentials_file"] == str(root / "notify.yaml")
    assert "bot_token" not in config["sinks"]["telegram"]
    entries = read_credentials_file(str(root / "methodology.yaml"))
    assert entries == {"OPENALEX_API_KEY": VALUES[0], "UNPAYWALL_EMAIL": VALUES[1]}
    remote = notification_remote("telegram", config["sinks"]["telegram"], {})
    assert VALUES[2] in remote[0] and remote[2] == VALUES[3]
    remote = notification_remote("webhook", config["sinks"]["webhook"], {})
    assert remote[1] == {"Authorization": "Bearer " + VALUES[4]}
    assert notification_preflight(config, state_root=tmp_path / "state", environ={})["network_probe_performed"] is False
    assert not (tmp_path / "state").exists()


def test_init_and_direct_profile_check_derive_same_source_without_env_or_values(checkout, capsys, monkeypatch):
    root = private_profile(checkout, capsys)
    for name in ("OPENALEX_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)
    args = [*arguments(checkout[0], [], "launch", template=None), "--literature-adapter", "openalex"]
    assert bootstrap.main(args) == 0
    raw = capsys.readouterr().out
    assert all(value not in raw for value in VALUES)
    result = json.loads(raw)
    for name in ("host_start", "profile_check", "start"):
        command = result["commands"][name]
        assert command[command.index("--credentials-file") + 1] == str(root / "methodology.yaml")
        assert "--notification-credentials-file" not in command
    assert cli.main(["profile-check", root.name, "--profiles-root", str(root.parent), "--run-id", "check-private", "--cpu-only",
                     "--literature-adapter", "openalex"]) == 0
    assert all(value not in capsys.readouterr().out for value in VALUES)


@pytest.mark.parametrize("kind", ["mode", "conflict", "type", "yaml", "read_mount"])
def test_invalid_private_profile_fails_without_exposing_values(checkout, capsys, kind):
    root = private_profile(checkout, capsys)
    path = root / "notify.yaml"
    notify = yaml.safe_load(path.read_text())
    if kind == "mode":
        path.chmod(0o644)
    elif kind == "yaml":
        path.write_text("sinks: [\n  " + VALUES[2] + "\n")
    elif kind == "read_mount":
        safety = yaml.safe_load((root / "safety.yaml").read_text())
        safety["allow_read_paths"] = [str(root)]
        (root / "safety.yaml").write_text(yaml.safe_dump(safety))
    else:
        if kind == "conflict":
            notify["sinks"]["telegram"]["bot_token_env"] = "LEGACY_TOKEN"
        else:
            notify["sinks"]["telegram"]["chat_id"] = 12345
        path.write_text(yaml.safe_dump(notify))
    report = admit_profile(root, run_id="invalid-private")
    assert not report.admitted
    assert all(value not in json.dumps(report.to_dict()) for value in VALUES)


def test_blank_templates_need_no_private_values_and_set_cannot_carry_a_secret(checkout, capsys):
    repo, overrides = checkout
    assert bootstrap.main(arguments(repo, [*overrides, "notify.sinks.telegram.bot_token=" + VALUES[2]])) == 2
    output = capsys.readouterr()
    assert "private Profile" in output.err and VALUES[2] not in output.err + output.out
    assert not (repo / "profiles/local/topic").exists()


def test_profile_proxy_uses_same_private_notify_source(checkout, capsys, tmp_path):
    root = private_profile(checkout, capsys)
    path = root / "notify.yaml"
    config = yaml.safe_load(path.read_text())
    config["proxy_url"] = "http://127.0.0.1:1234"
    path.write_text(yaml.safe_dump(config))
    report = admit_profile(root, run_id="proxy-private")
    assert report.admitted, report.to_dict()
    config = report.create_run_payload["research_contract"]["methodology"]["controls"]["product"]["notify"]
    assert "proxy_url" not in config
    assert notification_preflight(config, state_root=tmp_path / "state", environ={})
    with pytest.raises(ValueError, match="not both"):
        notification_preflight(config, state_root=tmp_path / "state", environ={}, proxy_env="PROXY")


def test_yaml_credentials_reader_errors_do_not_print_source(tmp_path):
    path = tmp_path / "notify.yaml"
    path.write_text("sinks: [\n  " + VALUES[2] + "\n")
    path.chmod(0o600)
    with pytest.raises(CredentialError) as error:
        read_credentials_file(str(path))
    assert VALUES[2] not in str(error.value)


def test_elsevier_uses_existing_private_profile_projection_and_init_selection(checkout, capsys):
    root = private_profile(checkout, capsys)
    path = root / "methodology.yaml"
    method = yaml.safe_load(path.read_text())
    method["literature"].update(elsevier_api_key="synthetic-elsevier", elsevier_insttoken="synthetic-insttoken")
    path.write_text(yaml.safe_dump(method))
    report = admit_profile(root, run_id="elsevier-private")
    assert report.admitted, report.to_dict()
    assert "synthetic-elsevier" not in json.dumps(report.to_dict())
    assert "synthetic-insttoken" not in json.dumps(report.to_dict())
    args = bootstrap.build_parser().parse_args(["inspect", root.name, "--run-id", "elsevier-private",
        "--cpu-only", "--literature-adapter", "openalex", "--institutional-browser-mode", "client"])
    cli._profile_literature_source(args, report)
    adapter = cli._literature_adapter(args)
    assert adapter.article_api is not None
    check = adapter.preflight()["article_api"]
    assert check["full_text_access"] == "not_checked" and not check["network_probe_performed"]
    from hypoloom.institutional_options import preparation
    view = preparation(args)
    assert view["selected"] == ["IEEE", "Springer"]
    assert view["api_providers"] == ["ScienceDirect"]
    assert "synthetic" not in json.dumps(view) + json.dumps(check) + json.dumps(adapter.configuration)
    args.institutional_prepare_provider = ["ScienceDirect"]
    assert preparation(args)["selected"] == ["ScienceDirect"], "explicit browser choice is retained"
