"""Task-neutral Hypoloom data-contract validators run by experiment preflight.

The HDF5 adapter intentionally imports ``h5py`` only inside the locked
experiment interpreter.  The semantic validator itself consumes ordinary
Python values so its fail-closed rules remain cheap and unit-testable in the
Hypoloom control environment.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


_SPLIT_PARTS = ("train", "val", "test")


def _json_digest(payload: Any) -> str:
    blob = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _normalized_split(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode()
    value = str(value)
    return "val" if value == "dev" else value


def load_hdf5_dialogues(
    cache: Path,
) -> tuple[dict[str, str], dict[str, list[int]]]:
    """Read only the split/label fields needed by the semantic validator."""
    import h5py  # type: ignore[import-not-found]

    cache_splits: dict[str, str] = {}
    dialogue_labels: dict[str, list[int]] = {}
    with h5py.File(cache, "r") as handle:
        for raw_id in handle.keys():
            dialogue_id = str(raw_id)
            node = handle[raw_id]
            cache_splits[dialogue_id] = _normalized_split(
                node.attrs.get("split", "")
            )
            if "label" not in node:
                raise ValueError(f"dialogue {dialogue_id} has no label dataset")
            labels = node["label"][:]
            if getattr(labels, "ndim", 1) != 1:
                raise ValueError(
                    f"dialogue {dialogue_id} label dataset must be one-dimensional"
                )
            dialogue_labels[dialogue_id] = [int(value) for value in labels]
    return cache_splits, dialogue_labels


def validate_split_manifest_snapshot(
    cache_splits: Mapping[str, Any],
    dialogue_labels: Mapping[str, Sequence[int]],
    manifest: Mapping[str, Any],
    *,
    expected_dataset: str = "",
    val_cache: Mapping[str, Any] | None = None,
    declared_val_cache: str = "",
) -> None:
    """Validate a frozen cache snapshot against its training split contract.

    MELD consumes the HDF5 ``split`` attributes directly, so all three parts
    must match exactly.  IEMOCAP derives validation dialogues from a persisted
    val-cache after reading the coarse train/test session split; consequently
    its train+val development pool is compared as a union and the final val set
    is checked against that separately frozen JSON file.
    """
    if manifest.get("schema_version") != 1:
        raise ValueError(
            f"unsupported split manifest schema_version: {manifest.get('schema_version')!r}"
        )

    actual = {part: [] for part in _SPLIT_PARTS}
    for raw_id, raw_split in cache_splits.items():
        dialogue_id = str(raw_id)
        split = _normalized_split(raw_split)
        if split not in actual:
            raise ValueError(f"dialogue {dialogue_id} has invalid split {split!r}")
        actual[split].append(dialogue_id)
    actual = {part: sorted(values) for part, values in actual.items()}

    cache_ids = set(cache_splits)
    label_ids = set(dialogue_labels)
    if cache_ids != label_ids:
        raise ValueError(
            "cache/label coverage mismatch: "
            f"cache-only={sorted(str(x) for x in cache_ids - label_ids)[:3]} "
            f"label-only={sorted(str(x) for x in label_ids - cache_ids)[:3]}"
        )

    split_payload = manifest.get("split")
    if not isinstance(split_payload, dict):
        raise ValueError("split manifest has no split object")
    unexpected_parts = sorted(set(split_payload) - set(_SPLIT_PARTS))
    if unexpected_parts:
        raise ValueError(
            f"split manifest has unsupported split parts: {unexpected_parts!r}"
        )
    expected = {
        part: sorted(str(value) for value in split_payload.get(part, []))
        for part in _SPLIT_PARTS
    }
    empty_parts = [part for part, values in expected.items() if not values]
    if empty_parts:
        raise ValueError(f"split manifest has empty split parts: {empty_parts!r}")
    for part, values in expected.items():
        if len(values) != len(set(values)):
            raise ValueError(
                f"split manifest contains duplicate dialogue ids in {part}"
            )
    owners: dict[str, str] = {}
    for part, values in expected.items():
        for dialogue_id in values:
            prior = owners.setdefault(dialogue_id, part)
            if prior != part:
                raise ValueError(
                    f"split manifest dialogue {dialogue_id} overlaps {prior}/{part}"
                )

    normalized_cache_ids = {str(value) for value in cache_ids}
    manifest_ids = set().union(*(set(values) for values in expected.values()))
    if normalized_cache_ids != manifest_ids:
        raise ValueError(
            "split/cache coverage mismatch: "
            f"cache-only={sorted(normalized_cache_ids - manifest_ids)[:3]} "
            f"manifest-only={sorted(manifest_ids - normalized_cache_ids)[:3]}"
        )

    dataset = str(expected_dataset or manifest.get("dataset", "")).upper()
    manifest_dataset = str(manifest.get("dataset", "")).upper()
    if expected_dataset and manifest_dataset != dataset:
        raise ValueError(
            f"split manifest dataset mismatch: {manifest.get('dataset')!r} "
            f"vs {expected_dataset!r}"
        )
    if dataset == "IEMOCAP":
        cache_development = set(actual["train"]) | set(actual["val"])
        expected_development = set(expected["train"]) | set(expected["val"])
        if cache_development != expected_development or actual["test"] != expected["test"]:
            raise ValueError(
                "IEMOCAP coarse split/cache mismatch: "
                f"development cache-only="
                f"{sorted(cache_development - expected_development)[:3]} "
                f"manifest-only="
                f"{sorted(expected_development - cache_development)[:3]} "
                f"test-cache-only="
                f"{sorted(set(actual['test']) - set(expected['test']))[:3]} "
                f"test-manifest-only="
                f"{sorted(set(expected['test']) - set(actual['test']))[:3]}"
            )
        if not isinstance(val_cache, Mapping):
            raise ValueError("IEMOCAP split check requires a val_cache")
        raw_val_ids = val_cache.get("val")
        if not isinstance(raw_val_ids, list):
            raise ValueError("IEMOCAP val-cache must contain a val list")
        val_ids = sorted(str(value) for value in raw_val_ids)
        if len(val_ids) != len(set(val_ids)):
            raise ValueError("IEMOCAP val-cache contains duplicate dialogue ids")
        if val_ids != expected["val"]:
            raise ValueError(
                "IEMOCAP val-cache mismatch: "
                f"cache-only={sorted(set(val_ids) - set(expected['val']))[:3]} "
                f"manifest-only={sorted(set(expected['val']) - set(val_ids))[:3]}"
            )
        if val_cache.get("seed") is not None and val_cache.get("seed") != manifest.get("seed"):
            raise ValueError(
                "IEMOCAP val-cache seed mismatch: "
                f"{val_cache.get('seed')!r} vs {manifest.get('seed')!r}"
            )
        if (
            val_cache.get("val_fraction") is not None
            and val_cache.get("val_fraction") != manifest.get("val_fraction")
        ):
            raise ValueError(
                "IEMOCAP val-cache fraction mismatch: "
                f"{val_cache.get('val_fraction')!r} "
                f"vs {manifest.get('val_fraction')!r}"
            )
        extra = manifest.get("extra") or {}
        if not isinstance(extra, Mapping):
            raise ValueError("split manifest extra must be an object")
        manifest_val_cache = str(extra.get("val_cache", ""))
        if (
            manifest_val_cache
            and manifest_val_cache.replace("\\", "/") != declared_val_cache
        ):
            raise ValueError(
                "IEMOCAP manifest val_cache mismatch: "
                f"{manifest_val_cache!r} vs {declared_val_cache!r}"
            )
    elif actual != expected:
        bits = []
        for part in _SPLIT_PARTS:
            cache_only = sorted(set(actual[part]) - set(expected[part]))
            manifest_only = sorted(set(expected[part]) - set(actual[part]))
            if cache_only or manifest_only:
                bits.append(
                    f"{part}: cache-only={cache_only[:3]} "
                    f"manifest-only={manifest_only[:3]}"
                )
        raise ValueError("split/cache mismatch: " + "; ".join(bits))

    if manifest.get("split_hash") != _json_digest(expected):
        raise ValueError(
            f"split_hash mismatch: manifest={manifest.get('split_hash')} "
            f"current={_json_digest(expected)}"
        )

    counts = manifest.get("counts")
    if not isinstance(counts, Mapping):
        raise ValueError("split manifest has no counts object")
    dialogue_counts = {part: len(values) for part, values in expected.items()}
    if counts.get("dialogues") != dialogue_counts:
        raise ValueError(
            "split manifest dialogue counts mismatch: "
            f"{counts.get('dialogues')!r} vs {dialogue_counts!r}"
        )
    utterance_counts = {
        part: sum(len(dialogue_labels[dialogue_id]) for dialogue_id in expected[part])
        for part in _SPLIT_PARTS
    }
    if counts.get("utterances") != utterance_counts:
        raise ValueError(
            "split manifest utterance counts mismatch: "
            f"{counts.get('utterances')!r} vs {utterance_counts!r}"
        )

    label_map = manifest.get("label_map")
    if not isinstance(label_map, Mapping) or not label_map:
        raise ValueError("split manifest has no label_map")
    normalized_label_map = {
        str(key): int(value) for key, value in sorted(label_map.items())
    }
    if len(set(normalized_label_map.values())) != len(normalized_label_map):
        raise ValueError("split manifest label_map contains duplicate ids")
    label_digest = _json_digest(normalized_label_map)
    if manifest.get("label_map_hash") != label_digest:
        raise ValueError(
            f"label_map_hash mismatch: manifest={manifest.get('label_map_hash')} "
            f"current={label_digest}"
        )
    id_to_name = {value: key for key, value in normalized_label_map.items()}
    label_counts: dict[str, dict[str, int]] = {}
    for part in _SPLIT_PARTS:
        counter: collections.Counter[str] = collections.Counter()
        for dialogue_id in expected[part]:
            for raw_value in dialogue_labels[dialogue_id]:
                value = int(raw_value)
                counter[id_to_name.get(value, str(value))] += 1
        label_counts[part] = dict(sorted(counter.items()))
    if counts.get("labels") != label_counts:
        raise ValueError(
            "split manifest label counts mismatch: "
            f"{counts.get('labels')!r} vs {label_counts!r}"
        )


def validate_hdf5_split_manifest(
    cache: Path,
    manifest_path: Path,
    *,
    expected_dataset: str = "",
    val_cache_path: Path | None = None,
    declared_val_cache: str = "",
) -> None:
    cache_splits, dialogue_labels = load_hdf5_dialogues(cache)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    val_cache = (
        json.loads(val_cache_path.read_text(encoding="utf-8"))
        if val_cache_path is not None
        else None
    )
    validate_split_manifest_snapshot(
        cache_splits,
        dialogue_labels,
        manifest,
        expected_dataset=expected_dataset,
        val_cache=val_cache,
        declared_val_cache=declared_val_cache,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a frozen HDF5 split manifest")
    parser.add_argument("--cache", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--dataset", default="")
    parser.add_argument("--val-cache", default="")
    parser.add_argument("--declared-val-cache", default="")
    args = parser.parse_args(argv)
    try:
        validate_hdf5_split_manifest(
            Path(args.cache),
            Path(args.manifest),
            expected_dataset=args.dataset,
            val_cache_path=Path(args.val_cache) if args.val_cache else None,
            declared_val_cache=args.declared_val_cache.replace("\\", "/"),
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
