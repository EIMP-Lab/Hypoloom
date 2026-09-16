"""One dotenv-style credentials file shared by Host-facing adapters.

A Profile references the file by absolute path (`literature.credentials_file`,
`sinks.telegram.credentials_file`) so every local secret lives in a single
ignored mode-0600 file instead of one file per secret. The parser accepts a
strict auditable subset: blank lines and `#` comments, `KEY=VALUE` pairs, no
quoting, expansion, or `export`. Errors name keys and line numbers, never
values.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import PurePosixPath
from typing import Mapping

CREDENTIALS_FILE_MAX_BYTES = 8192

TELEGRAM_BOT_TOKEN_KEY = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID_KEY = "TELEGRAM_CHAT_ID"
OPENALEX_API_KEY_KEY = "OPENALEX_API_KEY"
UNPAYWALL_EMAIL_KEY = "UNPAYWALL_EMAIL"

_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class CredentialError(ValueError):
    """A credentials file or entry is unusable; never carries secret material."""


def read_credentials_file(value: object) -> dict[str, str]:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise CredentialError("missing or invalid credentials file path")
    if not PurePosixPath(value).is_absolute():
        raise CredentialError("credentials file path must be absolute")
    try:
        descriptor = os.open(value, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise CredentialError("credentials file is unavailable") from None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise CredentialError("credentials file must be a regular file")
        raw = bytearray()
        while len(raw) <= CREDENTIALS_FILE_MAX_BYTES:
            block = os.read(descriptor, CREDENTIALS_FILE_MAX_BYTES + 1 - len(raw))
            if not block:
                break
            raw += block
    finally:
        os.close(descriptor)
    if not 0 < len(raw) <= CREDENTIALS_FILE_MAX_BYTES:
        raise CredentialError("credentials file size is outside its bound")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise CredentialError("credentials file must be UTF-8 text") from None
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
