"""Dotenv credentials file parsing; fixtures contain synthetic values only."""
from __future__ import annotations

from pathlib import Path

import pytest

from hypoloom.credentials import (CREDENTIALS_FILE_MAX_BYTES, CredentialError, credential,
                                  read_credentials_file)


def _write(tmp_path, text, name="hypoloom.env"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_parses_comments_whitespace_and_equals_inside_values(tmp_path):
    path = _write(tmp_path, "# header\n\n  OPENALEX_API_KEY = key-with=equals \nUNPAYWALL_EMAIL=ops@example.org\n")
    entries = read_credentials_file(str(path))
    assert entries == {"OPENALEX_API_KEY": "key-with=equals", "UNPAYWALL_EMAIL": "ops@example.org"}
    assert credential(entries, "OPENALEX_API_KEY") == "key-with=equals"


def test_rejects_relative_missing_symlink_and_directory(tmp_path):
    with pytest.raises(CredentialError, match="must be absolute"):
        read_credentials_file("secrets/hypoloom.env")
    with pytest.raises(CredentialError, match="unavailable"):
        read_credentials_file(str(tmp_path / "absent"))
    target = _write(tmp_path, "OPENALEX_API_KEY=synthetic\n", name="target")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(CredentialError, match="unavailable"):
        read_credentials_file(str(link))
    with pytest.raises(CredentialError, match="regular file"):
        read_credentials_file(str(tmp_path))


def test_rejects_empty_oversize_and_non_utf8(tmp_path):
    with pytest.raises(CredentialError, match="outside its bound"):
        read_credentials_file(str(_write(tmp_path, "")))
    oversized = tmp_path / "oversized"
    oversized.write_bytes(b"A" * (CREDENTIALS_FILE_MAX_BYTES + 1))
    with pytest.raises(CredentialError, match="outside its bound"):
        read_credentials_file(str(oversized))
    binary = tmp_path / "binary"
    binary.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(CredentialError, match="UTF-8"):
        read_credentials_file(str(binary))


@pytest.mark.parametrize(
    "text,expected",
    (
        ("OPENALEX_API_KEY\n", "is not KEY=VALUE"),
        ("=orphan\n", "is not KEY=VALUE"),
        ("1KEY=value\n", "invalid key name"),
        ("OPENALEX_API_KEY=first\nOPENALEX_API_KEY=second\n", "repeats OPENALEX_API_KEY"),
    ),
)
def test_rejects_malformed_lines_without_echoing_values(tmp_path, text, expected):
    path = _write(tmp_path, text)
    with pytest.raises(CredentialError, match=expected) as captured:
        read_credentials_file(str(path))
    assert "first" not in str(captured.value) and "second" not in str(captured.value)


def test_credential_requires_present_non_empty_values():
    with pytest.raises(CredentialError, match="lacks TELEGRAM_BOT_TOKEN"):
        credential({}, "TELEGRAM_BOT_TOKEN")
    with pytest.raises(CredentialError, match="empty TELEGRAM_BOT_TOKEN"):
        credential({"TELEGRAM_BOT_TOKEN": ""}, "TELEGRAM_BOT_TOKEN")


def test_shipped_template_lists_every_conventional_key():
    template = Path(__file__).resolve().parents[2] / "profiles/templates/hypoloom.env.example"
    entries = read_credentials_file(str(template))
    assert set(entries) == {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "OPENALEX_API_KEY", "UNPAYWALL_EMAIL"}
