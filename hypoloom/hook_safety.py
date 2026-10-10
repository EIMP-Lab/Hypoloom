"""Pure-stdlib interactive path safety for Hypoloom Claude hooks.

This module deliberately has no dependency on Hypoloom workflow state.
It guards only generic interactive file
effects; the resident Host and its process sandbox remain responsible for
workflow/expert authority.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping
from pathlib import Path


HYPOLOOM_STATE_ROOT_ENV = "HYPOLOOM_STATE_ROOT"
RUN_ROOT_ENV = "HYPOLOOM_RUN_DIR"

_BASH_BLACKLIST = (
    re.compile(r"\brm\s+-rf?\s+/(?!tmp\b)", re.IGNORECASE),
    re.compile(r"\brm\s+-rf?\s+~", re.IGNORECASE),
    re.compile(r"\brm\s+-rf?\s+\$HOME", re.IGNORECASE),
    re.compile(r"git\s+push\s+(--force|-f)\b", re.IGNORECASE),
    re.compile(r"git\s+reset\s+--hard", re.IGNORECASE),
    re.compile(r"\bgit\s+clean\b", re.IGNORECASE),
    re.compile(r"\bgit\s+branch\s+.*(?:-D|--delete)\b"),
    re.compile(r"(?:^|[\s;/])dd\s", re.IGNORECASE),
    re.compile(r"\bcurl\b.*\|\s*(sh|bash)\b", re.IGNORECASE),
    re.compile(r"\bwget\b.*\|\s*(sh|bash)\b", re.IGNORECASE),
)
_DESTRUCTIVE_VERBS = frozenset({
    "rm", "rmdir", "mv", "shred", "truncate", "unlink",
    "chmod", "chown", "chgrp",
})
_INDIRECT_COMMANDS = frozenset({
    "busybox", "command", "env", "nice", "nohup", "parallel", "sudo",
    "timeout", "xargs",
})
_SHELL_COMMANDS = frozenset({"bash", "dash", "sh", "zsh"})
_CONTROL_OPERAND_VERBS = frozenset({"chmod", "chown", "chgrp"})
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_GLOB_CHARS = frozenset("*?[")
_DYNAMIC_SHELL_CHARS = frozenset("$`")


def _resolve_path(value: str | os.PathLike[str], *, base: Path) -> Path:
    raw = os.fspath(value)
    if not raw or "\x00" in raw:
        raise ValueError("path is empty or contains NUL")
    if any(character in raw for character in _DYNAMIC_SHELL_CHARS):
        raise ValueError("path uses unresolved dynamic expansion")
    path = Path(os.path.expanduser(raw))
    if not path.is_absolute():
        path = base / path
    return path.resolve(strict=False)


def _within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _memory_roots(environment: Mapping[str, str]) -> tuple[Path, ...]:
    home = Path(environment.get("HOME") or os.path.expanduser("~"))
    projects = home / ".claude" / "projects"
    try:
        children = tuple(projects.iterdir())
    except OSError:
        return ()
    roots: list[Path] = []
    for child in children:
        try:
            if child.is_dir():
                roots.append((child / "memory").resolve(strict=False))
        except (OSError, RuntimeError):
            continue
    return tuple(roots)


def write_roots(
    repo_root: str | os.PathLike[str],
    *,
    environment: Mapping[str, str] | None = None,
) -> tuple[Path, ...]:
    """Return generic interactive roots, excluding workflow state authorities."""

    selected = os.environ if environment is None else environment
    repo = Path(repo_root).resolve(strict=False)
    roots = [repo / ".local", repo / "profiles/local", *_memory_roots(selected)]
    deduplicated: list[Path] = []
    for root in roots:
        if root not in deduplicated:
            deduplicated.append(root)
    return tuple(deduplicated)


def _protected_state_roots(
    repo: Path,
    environment: Mapping[str, str],
) -> tuple[Path, ...]:
    """Resolve state roots whose contents may only be changed through a Host."""

    roots = [(repo / "state").resolve(strict=False)]
    for name in (HYPOLOOM_STATE_ROOT_ENV, RUN_ROOT_ENV):
        raw_root = str(environment.get(name, "") or "").strip()
        if not raw_root:
            continue
        roots.append(_resolve_path(raw_root, base=repo))
    return tuple(dict.fromkeys(roots))


def _protected_runtime_control_roots(repo: Path) -> tuple[Path, ...]:
    """Return checked-in CC controls that a research session must not rewrite."""

    claude_root = repo / ".claude"
    return (
        (claude_root / "hooks").resolve(strict=False),
        (claude_root / "skills").resolve(strict=False),
    )


def is_write_allowed(
    target: str | os.PathLike[str],
    *,
    repo_root: str | os.PathLike[str],
    base_dir: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> tuple[bool, str]:
    """Require an interactive write to resolve beneath one explicit root."""

    selected = os.environ if environment is None else environment
    repo = Path(repo_root).resolve(strict=False)
    try:
        base = _resolve_path(base_dir, base=repo) if base_dir else repo
        resolved = _resolve_path(target, base=base)
        roots = write_roots(repo, environment=selected)
        protected = _protected_state_roots(repo, selected)
        protected_controls = _protected_runtime_control_roots(repo)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return False, f"cannot safely resolve write path {target!r}: {exc}"
    if any(_within_root(resolved, root) for root in protected):
        return False, (
            f"path {resolved} is workflow state and may only be changed through "
            "the resident Host control surface"
        )
    if any(_within_root(resolved, root) for root in protected_controls):
        return False, (
            f"path {resolved} is checked-in CC runtime control source; "
            "interactive research sessions may not rewrite it"
        )
    if any(_within_root(resolved, root) for root in roots):
        return True, ""
    return False, (
        f"path {resolved} is outside the Hypoloom interactive write roots "
        f"({', '.join(str(root) for root in roots)})"
    )


def is_bash_allowed(command: object) -> tuple[bool, str]:
    """Reject the small generic catastrophe blacklist used by the root hook."""

    if not isinstance(command, str):
        return False, "Bash command must be text"
    for pattern in _BASH_BLACKLIST:
        if pattern.search(command):
            return False, f"bash command blocked by pattern {pattern.pattern!r}"
    return True, ""


def research_shell_command(command: str, *, repo_root, base_dir=None) -> str:
    """Make framework source read-only for Bash/Monitor and their children.

    This reuses the runtime's required Linux bubblewrap. There is deliberately
    no unsandboxed fallback and no permissionDecision=allow in the hook.
    Network/user service authority is unchanged; this is a source-write boundary.
    """
    repo = Path(repo_root).resolve(strict=True)
    base = Path(base_dir or repo).resolve(strict=True)
    argv = ["/usr/bin/bwrap", "--die-with-parent", "--bind", "/", "/", "--dev-bind", "/dev", "/dev",
            "--ro-bind", str(repo), str(repo)]
    for relative in (".local", "profiles/local", "state"):
        path = repo / relative
        if path.resolve(strict=False) != path or path.is_symlink():
            raise ValueError(f"research writable directory must not be a symlink: {path}")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        argv += ["--bind", str(path), str(path)]
    return shlex.join([*argv, "--chdir", str(base), "--", "/bin/bash", "-c", command])


def clean_question_text(tool_input: dict) -> dict:
    """Remove generated CR characters only from native question display fields."""
    def fields(value, names):
        return {k: v.replace("\r", "") if k in names and isinstance(v, str) else v
                for k, v in value.items()}

    questions = tool_input.get("questions")
    if not isinstance(questions, list):
        return tool_input
    cleaned = []
    for question in questions:
        if not isinstance(question, dict):
            cleaned.append(question)
            continue
        question = fields(question, {"question", "header"})
        if isinstance(question.get("options"), list):
            question["options"] = [fields(o, {"label", "description"}) if isinstance(o, dict) else o
                                   for o in question["options"]]
        cleaned.append(question)
    return {**tool_input, "questions": cleaned}


def _shell_statements(command: str):
    # Tokenize before splitting, so quoted pipes and multiline Python/heredoc
    # bodies do not produce spurious unterminated-quote errors.
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    tokens = []
    for token in lexer:
        if token and all(c in ";&|\n" for c in token):
            if tokens: yield tokens
            tokens = []
        else:
            tokens.append(token)
    if tokens: yield tokens


def _glob_prefix(token: str) -> str:
    prefix: list[str] = []
    for character in token:
        if character in _GLOB_CHARS:
            break
        prefix.append(character)
    return "".join(prefix)


def _basename_token(token: str) -> str:
    return os.path.basename(token).lower()


def _contains_destructive_verb(tokens: list[str]) -> bool:
    return any(_basename_token(token) in _DESTRUCTIVE_VERBS for token in tokens)


def _nested_shell_command(tokens: list[str], index: int) -> str | None:
    """Return the command text for a recognizable ``shell -c`` invocation."""

    for offset in range(index + 1, len(tokens)):
        option = tokens[offset]
        if option == "--":
            continue
        if option.startswith("-") and "c" in option[1:]:
            if offset + 1 < len(tokens):
                return tokens[offset + 1]
            return ""
    return None


def is_destructive_command_safe(
    command: object,
    *,
    repo_root: str | os.PathLike[str],
    base_dir: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> tuple[bool, str]:
    """Path-scope recognizable destructive shell commands, failing closed."""

    if not isinstance(command, str):
        return False, "Bash command must be text"
    if not command.strip():
        return True, ""
    repo = Path(repo_root).resolve(strict=False)
    base = base_dir or repo
    if re.search(r"(?:^|[;&|]\s*)(?:cd|pushd)\s+", command):
        if any(
            re.search(rf"(?:^|\s){re.escape(verb)}(?:\s|$)", command)
            for verb in _DESTRUCTIVE_VERBS
        ):
            return False, "cannot safely resolve destructive targets after cd/pushd"

    try:
        statements = list(_shell_statements(command))
    except ValueError as exc:
        return False, f"cannot parse shell command safely: {exc}"
    for tokens in statements:
        index = 0
        while index < len(tokens) and _ENV_ASSIGNMENT.match(tokens[index]):
            index += 1
        if index >= len(tokens):
            continue
        verb = _basename_token(tokens[index])
        if verb == "find" and "-delete" in tokens[index + 1 :]:
            return False, "find -delete is not safely path-scoped; use explicit rm"
        find_actions = ("-exec", "-execdir", "-ok", "-okdir")
        unsafe_nested_verbs = _DESTRUCTIVE_VERBS | _SHELL_COMMANDS | {"eval"}
        if verb == "find" and any(
            marker in tokens[index + 1 :] for marker in find_actions
        ) and any(
            _basename_token(token) in unsafe_nested_verbs
            for token in tokens[index + 1 :]
        ):
            return False, "find action command is not safely path-scoped"
        if verb in _INDIRECT_COMMANDS and any(
            _basename_token(token) in unsafe_nested_verbs
            for token in tokens[index + 1 :]
        ):
            return False, (
                f"indirect destructive command through {verb!r} is not "
                "safely path-scoped; invoke the target command directly"
            )
        if verb in _SHELL_COMMANDS:
            nested = _nested_shell_command(tokens, index)
            if nested is not None:
                if not nested:
                    return False, "shell -c has no safely parseable command"
                allowed, reason = is_destructive_command_safe(
                    nested,
                    repo_root=repo,
                    base_dir=base,
                    environment=environment,
                )
                if not allowed:
                    return False, f"nested shell command is unsafe: {reason}"
        if verb == "eval":
            return False, "eval cannot be safely path-scoped"
        if verb not in _DESTRUCTIVE_VERBS:
            continue
        targets: list[str] = []
        skip_control_operand = verb in _CONTROL_OPERAND_VERBS
        for argument in tokens[index + 1 :]:
            if verb == "mv" and argument.startswith("--target-directory="):
                targets.append(argument.partition("=")[2])
                continue
            if verb == "mv" and argument.startswith("-t") and len(argument) > 2:
                targets.append(argument[2:])
                continue
            if argument.startswith("-") or argument.startswith((">", "<")):
                continue
            if skip_control_operand:
                skip_control_operand = False
                continue
            targets.append(argument)
        if not targets:
            return False, (
                f"destructive {verb!r} has no safely parseable target; refusing"
            )
        for target in targets:
            if any(character in target for character in _DYNAMIC_SHELL_CHARS):
                return False, (
                    f"destructive {verb!r} target {target!r} uses dynamic shell expansion"
                )
            prefix = _glob_prefix(target) or "."
            allowed, reason = is_write_allowed(
                prefix,
                repo_root=repo,
                base_dir=base,
                environment=environment,
            )
            if not allowed:
                return False, (
                    f"destructive {verb!r} target {target!r} is unsafe: {reason}"
                )
    return True, ""
