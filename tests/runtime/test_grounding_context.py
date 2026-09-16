from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import hypoloom.grounding_context as grounding_context
from hypoloom.grounding_context import (
    GroundingContextError,
    GroundingLimits,
    build_grounding_context,
    build_grounding_context_bytes,
)


def _execution(
    target: Path | None,
    read_roots: tuple[Path, ...],
) -> dict[str, object]:
    return {
        "allow_gpu": False,
        "delay_seconds": 0.0,
        "target": (
            None
            if target is None
            else {"checkout": str(target), "commit": "a" * 40}
        ),
        "runtime_prefix": None,
        "read_roots": [str(path) for path in read_roots],
    }


def _by_path(root: dict[str, object]) -> dict[str, dict[str, object]]:
    entries = root["entries"]
    assert isinstance(entries, list)
    return {str(item["path"]): item for item in entries}


def test_context_is_deterministic_bounded_and_does_not_follow_symlinks(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "README.md").write_text("bounded target\n", encoding="utf-8")
    (target / "weights.bin").write_bytes(b"\x00\x01\x02")
    source = target / "src"
    source.mkdir()
    (source / "model.py").write_text("VALUE = 7\n", encoding="utf-8")
    metadata = target / ".git"
    metadata.mkdir()
    (metadata / "private-token").write_text("GIT-SECRET", encoding="utf-8")
    outside = tmp_path / "outside-secret.md"
    outside.write_text("OUTSIDE-SECRET", encoding="utf-8")
    (target / "linked.md").symlink_to(outside)

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "config.yaml").write_text("seed: 3\n", encoding="utf-8")
    (dataset / "rows.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (dataset / "invalid.txt").write_bytes(b"\xff\xfe")

    limits = GroundingLimits(
        max_entries=32,
        max_sample_files=8,
        max_file_bytes=64,
        max_total_sample_bytes=256,
    )
    context = build_grounding_context(_execution(target, (dataset,)), limits=limits)
    repeated = build_grounding_context(_execution(target, (dataset,)), limits=limits)
    assert context == repeated
    assert context["limits"] == limits.to_dict()
    target_context = context["target"]
    assert isinstance(target_context, dict)
    assert target_context["checkout"] == str(target.resolve())
    assert target_context["commit"] == "a" * 40
    target_entries = _by_path(target_context)
    assert list(target_entries) == sorted(target_entries)
    assert target_entries["README.md"] == {
        "kind": "file",
        "path": "README.md",
        "sample_status": "complete",
        "sample_text": "bounded target\n",
        "size_bytes": 15,
    }
    assert target_entries["weights.bin"]["sample_status"] == "unsupported_type"
    assert target_entries["linked.md"] == {
        "kind": "symlink",
        "path": "linked.md",
        "status": "not_followed",
    }
    assert target_entries[".git"] == {
        "kind": "directory",
        "path": ".git",
        "status": "excluded",
    }
    assert not any(path.startswith(".git/") for path in target_entries)
    read_roots = context["read_roots"]
    assert isinstance(read_roots, list) and len(read_roots) == 1
    data_entries = _by_path(read_roots[0])
    assert data_entries["config.yaml"]["sample_text"] == "seed: 3\n"
    assert data_entries["rows.csv"]["sample_text"] == "x,y\n1,2\n"
    assert data_entries["invalid.txt"]["sample_status"] == "non_utf8"

    encoded = build_grounding_context_bytes(
        _execution(target, (dataset,)),
        limits=limits,
    )
    assert json.loads(encoded) == context
    assert encoded == build_grounding_context_bytes(
        _execution(target, (dataset,)),
        limits=limits,
    )
    assert b"GIT-SECRET" not in encoded
    assert b"OUTSIDE-SECRET" not in encoded
    assert str(outside).encode() not in encoded


def test_sampling_records_limits_and_unreadable_files_without_error_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "data"
    root.mkdir()
    (root / "a.md").write_text("abcdefghij", encoding="utf-8")
    (root / "b.json").write_text('{"value":1}', encoding="utf-8")
    (root / "blocked.txt").write_text("SENSITIVE-BLOCKED", encoding="utf-8")
    real_open = os.open

    def controlled_open(
        path: os.PathLike[str] | str,
        flags: int,
        *args: object,
        **kwargs: object,
    ) -> int:
        if Path(path).name == "blocked.txt":
            raise PermissionError("do not expose this diagnostic")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(grounding_context.os, "open", controlled_open)
    context = build_grounding_context(
        _execution(None, (root,)),
        limits=GroundingLimits(
            max_entries=8,
            max_sample_files=8,
            max_file_bytes=4,
            max_total_sample_bytes=6,
        ),
    )
    entries = _by_path(context["read_roots"][0])
    assert entries["a.md"]["sample_text"] == "abcd"
    assert entries["a.md"]["sample_status"] == "truncated"
    assert entries["b.json"]["sample_text"] == '{"'
    assert entries["b.json"]["sample_status"] == "truncated"
    assert entries["blocked.txt"]["sample_status"] == "total_byte_limit"
    assert context["summary"] == {
        "entry_count": 3,
        "sample_bytes_read": 6,
        "sampled_file_count": 2,
        "truncated": True,
        "unreadable_count": 0,
    }
    encoded = json.dumps(context, sort_keys=True)
    assert "SENSITIVE-BLOCKED" not in encoded
    assert "do not expose this diagnostic" not in encoded

    unreadable = build_grounding_context(
        _execution(None, (root,)),
        limits=GroundingLimits(
            max_entries=8,
            max_sample_files=8,
            max_file_bytes=64,
            max_total_sample_bytes=256,
        ),
    )
    blocked = _by_path(unreadable["read_roots"][0])["blocked.txt"]
    assert blocked == {
        "kind": "file",
        "path": "blocked.txt",
        "sample_status": "unreadable",
        "size_bytes": 17,
    }
    assert unreadable["summary"]["unreadable_count"] == 1


def test_git_worktree_pointer_file_is_excluded_without_sampling(tmp_path: Path) -> None:
    root = tmp_path / "worktree"
    root.mkdir()
    (root / ".git").write_text(
        "gitdir: /private/operator/repository/.git/worktrees/secret\n",
        encoding="utf-8",
    )
    context = build_grounding_context(_execution(None, (root,)))
    assert _by_path(context["read_roots"][0])[".git"] == {
        "kind": "directory",
        "path": ".git",
        "status": "excluded",
    }
    assert "private/operator" not in json.dumps(context, sort_keys=True)


def test_directory_replaced_by_external_symlink_after_metadata_is_not_followed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    victim = root / "victim"
    victim.mkdir(parents=True)
    (victim / "inside.md").write_text("INSIDE", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("OUTSIDE-SECRET", encoding="utf-8")
    displaced = tmp_path / "displaced"
    real_stat = os.stat
    swapped = False

    def swap_after_metadata(
        path: os.PathLike[str] | str | int,
        *args: object,
        **kwargs: object,
    ) -> os.stat_result:
        nonlocal swapped
        observed = real_stat(path, *args, **kwargs)
        if (
            not swapped
            and path == victim.name
            and kwargs.get("dir_fd") is not None
            and kwargs.get("follow_symlinks") is False
        ):
            victim.rename(displaced)
            victim.symlink_to(outside, target_is_directory=True)
            swapped = True
        return observed

    monkeypatch.setattr(grounding_context.os, "stat", swap_after_metadata)
    context = build_grounding_context(_execution(None, (root,)))
    assert swapped is True
    assert _by_path(context["read_roots"][0])["victim"] == {
        "kind": "directory",
        "path": "victim",
        "status": "unreadable",
    }
    encoded = json.dumps(context, sort_keys=True)
    assert "victim/secret.md" not in encoded
    assert "OUTSIDE-SECRET" not in encoded
    assert context["summary"]["unreadable_count"] == 1
    assert context["summary"]["truncated"] is True


def test_global_entry_limit_stops_traversal_deterministically(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for ordinal in range(5):
        (first / f"item-{ordinal}.txt").write_text(str(ordinal), encoding="utf-8")
    (second / "not-visited.txt").write_text("later", encoding="utf-8")
    context = build_grounding_context(
        _execution(None, (first, second)),
        limits=GroundingLimits(
            max_entries=3,
            max_sample_files=3,
            max_file_bytes=16,
            max_total_sample_bytes=32,
        ),
    )
    roots = context["read_roots"]
    assert [item["path"] for item in roots[0]["entries"]] == [
        "item-0.txt",
        "item-1.txt",
    ]
    assert roots[0]["truncated"] is True
    assert [item["path"] for item in roots[1]["entries"]] == [
        "not-visited.txt",
    ]
    assert roots[1]["truncated"] is False
    assert context["summary"]["entry_count"] == 3
    assert context["summary"]["truncated"] is True


def test_generated_environment_does_not_starve_source_or_read_roots(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    package = target / ".venv" / "lib" / "python" / "site-packages" / "PIL"
    package.mkdir(parents=True)
    for ordinal in range(300):
        (package / f"plugin-{ordinal:03d}.py").write_text(
            f"PLUGIN = {ordinal}\n",
            encoding="utf-8",
        )
    configs = target / "configs"
    configs.mkdir()
    (configs / "train.yaml").write_text("seed: 7\n", encoding="utf-8")
    source = target / "src" / "merc"
    source.mkdir(parents=True)
    (source / "model.py").write_text("MODEL = 'merc'\n", encoding="utf-8")

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "schema.json").write_text('{"label":"emotion"}\n', encoding="utf-8")

    execution = _execution(target, (dataset,))
    context = build_grounding_context(execution)
    repeated = build_grounding_context(execution)
    assert context == repeated

    target_entries = _by_path(context["target"])
    assert target_entries[".venv"] == {
        "kind": "directory",
        "path": ".venv",
        "status": "excluded",
    }
    assert not any(path.startswith(".venv/") for path in target_entries)
    assert target_entries["configs/train.yaml"]["sample_text"] == "seed: 7\n"
    assert target_entries["src/merc/model.py"]["sample_text"] == "MODEL = 'merc'\n"

    read_entries = _by_path(context["read_roots"][0])
    assert read_entries["schema.json"]["sample_text"] == '{"label":"emotion"}\n'
    summary = context["summary"]
    limits = context["limits"]
    assert summary["entry_count"] <= limits["max_entries"]
    assert summary["sampled_file_count"] <= limits["max_sample_files"]
    assert summary["sample_bytes_read"] <= limits["max_total_sample_bytes"]


def test_breadth_first_inventory_reaches_sibling_source_directories(
    tmp_path: Path,
) -> None:
    root = tmp_path / "target"
    deep = root / "cache"
    deep.mkdir(parents=True)
    cursor = deep
    for ordinal in range(20):
        cursor = cursor / f"level-{ordinal:02d}"
        cursor.mkdir()
    configs = root / "configs"
    configs.mkdir()
    (configs / "train.yaml").write_text("batch: 4\n", encoding="utf-8")
    source = root / "src" / "merc"
    source.mkdir(parents=True)
    (source / "model.py").write_text("VALUE = 1\n", encoding="utf-8")

    context = build_grounding_context(
        _execution(root, ()),
        limits=GroundingLimits(
            max_entries=8,
            max_sample_files=4,
            max_file_bytes=64,
            max_total_sample_bytes=128,
        ),
    )
    entries = _by_path(context["target"])
    assert entries["cache"] == {"kind": "directory", "path": "cache"}
    assert entries["configs/train.yaml"]["sample_text"] == "batch: 4\n"
    assert entries["src/merc/model.py"]["sample_text"] == "VALUE = 1\n"
    assert context == build_grounding_context(
        _execution(root, ()),
        limits=GroundingLimits(
            max_entries=8,
            max_sample_files=4,
            max_file_bytes=64,
            max_total_sample_bytes=128,
        ),
    )


def test_unique_roots_share_the_global_inventory_budget(tmp_path: Path) -> None:
    target = tmp_path / "target"
    first = tmp_path / "first"
    second = tmp_path / "second"
    for root in (target, first, second):
        root.mkdir()
        for ordinal in range(8):
            (root / f"item-{ordinal}.txt").write_text(
                f"{root.name}-{ordinal}",
                encoding="utf-8",
            )
    limits = GroundingLimits(
        max_entries=6,
        max_sample_files=6,
        max_file_bytes=32,
        max_total_sample_bytes=128,
    )
    context = build_grounding_context(
        _execution(target, (first, second)),
        limits=limits,
    )
    assert len(context["target"]["entries"]) == 2
    assert [len(root["entries"]) for root in context["read_roots"]] == [2, 2]
    assert context["summary"]["entry_count"] == limits.max_entries
    assert context["summary"]["sampled_file_count"] == limits.max_sample_files
    assert context["summary"]["sample_bytes_read"] <= limits.max_total_sample_bytes
    assert context == build_grounding_context(
        _execution(target, (first, second)),
        limits=limits,
    )


def test_exact_global_entry_bound_is_not_reported_as_truncated(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "one.txt").write_text("one", encoding="utf-8")
    (second / "two.txt").write_text("two", encoding="utf-8")

    context = build_grounding_context(
        _execution(None, (first, second)),
        limits=GroundingLimits(
            max_entries=2,
            max_sample_files=2,
            max_file_bytes=8,
            max_total_sample_bytes=8,
        ),
    )
    assert [root["truncated"] for root in context["read_roots"]] == [False, False]
    assert context["summary"] == {
        "entry_count": 2,
        "sample_bytes_read": 6,
        "sampled_file_count": 2,
        "truncated": False,
        "unreadable_count": 0,
    }


def test_exact_nested_directory_bound_is_not_reported_as_truncated(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    (root / "outer" / "inner").mkdir(parents=True)
    context = build_grounding_context(
        _execution(None, (root,)),
        limits=GroundingLimits(
            max_entries=2,
            max_sample_files=1,
            max_file_bytes=1,
            max_total_sample_bytes=1,
        ),
    )
    assert [entry["path"] for entry in context["read_roots"][0]["entries"]] == [
        "outer",
        "outer/inner",
    ]
    assert context["read_roots"][0]["truncated"] is False
    assert context["summary"]["entry_count"] == 2
    assert context["summary"]["truncated"] is False


def test_duplicate_roots_keep_position_without_spending_scan_budget(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    dataset = tmp_path / "dataset"
    target.mkdir()
    dataset.mkdir()
    for ordinal in range(4):
        (target / f"target-{ordinal}.txt").write_text("target", encoding="utf-8")
        (dataset / f"data-{ordinal}.txt").write_text("data", encoding="utf-8")

    context = build_grounding_context(
        _execution(target, (target, dataset, target, dataset)),
        limits=GroundingLimits(
            max_entries=4,
            max_sample_files=4,
            max_file_bytes=16,
            max_total_sample_bytes=64,
        ),
    )
    roots = context["read_roots"]
    assert len(roots) == 4
    assert roots[0] == {
        "entries": [],
        "root": str(target),
        "status": "covered_by_target",
        "truncated": False,
    }
    assert len(roots[1]["entries"]) == 2
    assert roots[2] == {
        "entries": [],
        "root": str(target),
        "status": "covered_by_target",
        "truncated": False,
    }
    assert roots[3] == {
        "entries": [],
        "root": str(dataset),
        "status": "duplicate_root",
        "truncated": False,
    }
    assert len(context["target"]["entries"]) == 2
    assert context["summary"]["entry_count"] == 4


def test_utf8_sample_limit_keeps_the_last_complete_character(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    (root / "unicode.md").write_text("aéz", encoding="utf-8")
    context = build_grounding_context(
        _execution(None, (root,)),
        limits=GroundingLimits(
            max_entries=2,
            max_sample_files=1,
            max_file_bytes=2,
            max_total_sample_bytes=2,
        ),
    )
    sample = _by_path(context["read_roots"][0])["unicode.md"]
    assert sample["sample_status"] == "truncated"
    assert sample["sample_text"] == "a"
    assert context["summary"]["sample_bytes_read"] == 2


def test_rejects_a_root_that_no_longer_matches_admission_identity(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(GroundingContextError, match="must not be a symlink"):
        build_grounding_context(_execution(None, (linked,)))


@pytest.mark.parametrize(
    "limits",
    (
        GroundingLimits(max_entries=1, max_sample_files=1, max_file_bytes=1,
                        max_total_sample_bytes=1),
        GroundingLimits(),
    ),
)
def test_limits_are_json_safe_and_stable(limits: GroundingLimits) -> None:
    assert json.loads(json.dumps(limits.to_dict())) == limits.to_dict()
