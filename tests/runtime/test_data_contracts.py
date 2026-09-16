from __future__ import annotations

import hashlib
import json
from importlib.resources import files

from hypoloom.data_contracts import validate_split_manifest_snapshot


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_wheel_visible_module_runs_semantic_snapshot_validator() -> None:
    assert files("hypoloom").joinpath("data_contracts.py").is_file()

    split = {
        "train": ["train-dialogue"],
        "val": ["val-dialogue"],
        "test": ["test-dialogue"],
    }
    labels = {
        "train-dialogue": [0, 1],
        "val-dialogue": [1],
        "test-dialogue": [0],
    }
    label_map = {"negative": 0, "positive": 1}
    manifest = {
        "schema_version": 1,
        "dataset": "MELD",
        "split": split,
        "split_hash": _digest(split),
        "label_map": label_map,
        "label_map_hash": _digest(label_map),
        "counts": {
            "dialogues": {"train": 1, "val": 1, "test": 1},
            "utterances": {"train": 2, "val": 1, "test": 1},
            "labels": {
                "train": {"negative": 1, "positive": 1},
                "val": {"positive": 1},
                "test": {"negative": 1},
            },
        },
    }

    validate_split_manifest_snapshot(
        {
            "train-dialogue": "train",
            "val-dialogue": "dev",
            "test-dialogue": "test",
        },
        labels,
        manifest,
        expected_dataset="MELD",
    )
