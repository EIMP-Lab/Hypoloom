"""Host-only credentials authored directly in private Profile YAML.

Admission strips literals and derives references to the original YAML files.
The same private-file reader also accepts legacy dotenv inputs; no copied secret
store is created. Model authentication remains separate. Errors never show values.
"""

from __future__ import annotations

import os
import re
import stat
import copy
from pathlib import Path, PurePosixPath
from typing import Mapping

CREDENTIALS_FILE_MAX_BYTES = 8192

TELEGRAM_BOT_TOKEN_KEY = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID_KEY = "TELEGRAM_CHAT_ID"
OPENALEX_API_KEY_KEY = "OPENALEX_API_KEY"
SEMANTIC_SCHOLAR_API_KEY_KEY = "SEMANTIC_SCHOLAR_API_KEY"
UNPAYWALL_EMAIL_KEY = "UNPAYWALL_EMAIL"
ELSEVIER_API_KEY = "ELSEVIER_API_KEY"
ELSEVIER_INSTTOKEN = "ELSEVIER_INSTTOKEN"
WEBHOOK_URL_KEY = "HYPOLOOM_WEBHOOK_URL"
WEBHOOK_TOKEN_KEY = "HYPOLOOM_WEBHOOK_TOKEN"
NOTIFICATION_PROXY_KEY = "HYPOLOOM_NOTIFICATION_PROXY"

# Authoring fields only: projection removes them before any durable/Agent input.
PROFILE_CREDENTIAL_FIELDS = {
    "methodology.yaml": {("literature",): {
        "openalex_api_key": OPENALEX_API_KEY_KEY, "unpaywall_email": UNPAYWALL_EMAIL_KEY,
        "semantic_scholar_api_key": SEMANTIC_SCHOLAR_API_KEY_KEY,
        "elsevier_api_key": ELSEVIER_API_KEY, "elsevier_insttoken": ELSEVIER_INSTTOKEN}},
    "notify.yaml": {
        (): {"proxy_url": NOTIFICATION_PROXY_KEY},
        ("sinks", "telegram"): {"bot_token": TELEGRAM_BOT_TOKEN_KEY, "chat_id": TELEGRAM_CHAT_ID_KEY},
        ("sinks", "webhook"): {"url": WEBHOOK_URL_KEY, "token": WEBHOOK_TOKEN_KEY}},
}

_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class CredentialError(ValueError):
    """A credentials file or entry is unusable; never carries secret material."""


def _private_text(value: object, *, max_bytes: int) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise CredentialError("missing or invalid credentials file path")
    if not PurePosixPath(value).is_absolute():
        raise CredentialError("credentials file path must be absolute")
    try:
        descriptor = os.open(value, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise CredentialError("credentials file is unavailable") from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise CredentialError("credentials file must be a regular file")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise CredentialError("credentials file must have mode 0600")
        raw = bytearray()
        while len(raw) <= max_bytes:
            block = os.read(descriptor, max_bytes + 1 - len(raw))
            if not block:
                break
            raw += block
    finally:
        os.close(descriptor)
    if not 0 < len(raw) <= max_bytes:
        raise CredentialError("credentials file size is outside its bound")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise CredentialError("credentials file must be UTF-8 text") from None


def _sections(document, name):
    for parents, fields in PROFILE_CREDENTIAL_FIELDS.get(name, {}).items():
        node = document
        for parent in parents:
            node = node.get(parent, {}) if isinstance(node, Mapping) else {}
        if isinstance(node, Mapping):
            yield parents, fields, node


def profile_has_credentials(document, name: str) -> bool:
    return any(node.get(field) not in (None, "")
        for _, fields, node in _sections(document, name) for field in fields)


def project_profile_credentials(document, name: str, profile_dir=None):
    """Reuse credentials_file references; never return a literal in the projection."""
    safe = copy.deepcopy(document)
    issues = []
    for parents, fields, node in _sections(safe, name):
        supplied = {field: node.pop(field) for field in fields if field in node}
        location = (name + "." + ".".join(parents)).rstrip(".")
        for field, value in supplied.items():
            if not isinstance(value, str) or any(char in value for char in ("\x00", "\r", "\n")):
                issues.append((location + "." + field, "credential must be a single-line string; quote IDs and tokens"))
        if not any(value not in (None, "") for value in supplied.values()):
            continue
        if any(key == "credentials_file" or key.endswith("_env") for key in node):
            issues.append((location, "use inline Profile credentials or a legacy file/environment source, not both"))
        elif profile_dir is None:
            issues.append((location, "inline credentials require the source Profile directory"))
        else:
            node["credentials_file"] = str(Path(profile_dir).absolute() / name)
    return safe, issues


def read_credentials_file(value: object) -> dict[str, str]:
    """Read private Profile YAML or a legacy dotenv through the same boundary."""
    name = Path(value).name if isinstance(value, str) else ""
    if name in PROFILE_CREDENTIAL_FIELDS:
        import yaml
        from .profile_admission import _UniqueKeyLoader
        text = _private_text(value, max_bytes=1024 * 1024)
        try:
            document = yaml.load(text, Loader=_UniqueKeyLoader)
        except yaml.YAMLError:
            raise CredentialError("invalid YAML in private Profile; values omitted") from None
        if not isinstance(document, Mapping):
            raise CredentialError("private Profile must be a YAML object")
        _, issues = project_profile_credentials(document, name, Path(value).parent)
        if issues:
            raise CredentialError(issues[0][1])
        entries = {}
        for _, fields, node in _sections(document, name):
            for field, key in fields.items():
                if field in node:
                    entries[key] = node[field]
        return entries
    text = _private_text(value, max_bytes=CREDENTIALS_FILE_MAX_BYTES)
    entries: dict[str, str] = {}
    for number, line in enumerate(text.split("\n"), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, secret = line.partition("=")
        key = key.strip()
        if not separator or not key:
            raise CredentialError(f"credentials file line {number} is not KEY=VALUE")
        if _KEY.fullmatch(key) is None:
            raise CredentialError(f"credentials file line {number} has an invalid key name")
        if key in entries:
            raise CredentialError(f"credentials file repeats {key}")
        entries[key] = secret.strip()
    return entries


def credential(entries: Mapping[str, str], key: str) -> str:
    value = entries.get(key)
    if value is None:
        raise CredentialError(f"credentials file lacks {key}")
    if not value:
        raise CredentialError(f"credentials file has an empty {key}")
    return value


def optional_credential_loader(source: object, key: str):
    """Bind an explicitly configured key; later source loss is never anonymous."""
    if source is None:
        return None
    entries = read_credentials_file(source)
    if entries.get(key) in (None, ""):
        return None
    credential(entries, key)
    return lambda: credential(read_credentials_file(source), key)
