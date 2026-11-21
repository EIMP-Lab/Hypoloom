"""Small, labeled Grounding fixtures shared by fresh and Git-seeded evaluators."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import json
import re
import subprocess
import tempfile

from .shared_acceptance import SharedAcceptance, evaluate_shared_acceptance, normalize_shared_observation


CASES = "work/acceptance-cases.json"


def case_format_example():
    """An example for a toy numeric GOAL of score > 0."""
    return {name: {"observation": {"measurements": {"score": score}, "checks": {}}, "goal_met": score > 0}
            for name, score in (("baseline", 0), ("higher", 1))}


def _object(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate evaluator example key")
    return value


def _json(data):
    return json.loads(data, object_pairs_hook=_object, parse_float=str)


def examples(data, acceptance):
    cases = _json(data)
    layout = "Use each case name as a top-level key beside baseline, with observation and boolean goal_met."
    if not isinstance(cases, dict) or "baseline" not in cases or not 2 <= len(cases) <= 8:
        raise ValueError(f"{CASES}: requires baseline and one to seven named variants. {layout}")
    result = {}
    for name, case in cases.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            raise ValueError(f"{CASES}: case names must match [A-Za-z0-9_-]{{1,64}}. {layout}")
        path = f"{CASES}/{name}"
        if not isinstance(case, dict):
            raise ValueError(f"{path}: expected a case object, got {type(case).__name__}. {layout}")
        fields = {"observation", "goal_met"}
        if set(case) != fields:
            missing = sorted(fields - case.keys())
            unexpected = [key[:64] for key in sorted(case.keys() - fields)[:4]]
            raise ValueError(f"{path}: expected exactly observation and goal_met. "
                             f"Missing {json.dumps(missing)}, unexpected {json.dumps(unexpected)}. {layout}")
        if type(case["goal_met"]) is not bool:
            raise ValueError(f"{path}/goal_met: expected a JSON boolean, got {type(case['goal_met']).__name__}")
        try:
            observation = normalize_shared_observation(case["observation"], acceptance)
        except ValueError as exc:
            raise ValueError(f"{path}/observation: {exc}") from exc
        if evaluate_shared_acceptance(acceptance, observation) != case["goal_met"]:
            raise ValueError(f"case {name}: expected observations disagree with the declared goal")
        result[name] = (observation, case["goal_met"])
    return result


def fixture_path(case, artifact):
    return ("smoke/" if case == "baseline" else f"work/cases/{case}/") + artifact


def checked_seed_spec(spec):
    if not isinstance(spec, dict) or set(spec) != {"path", "protocol"}:
        raise ValueError("evaluator_seed requires path and protocol")
    path = spec["path"]
    if (not isinstance(path, str) or not path or "\x00" in path or "\\" in path or
            PurePosixPath(path).is_absolute() or any(part in {"", ".", "..", ".git"} for part in path.split("/"))):
        raise ValueError("evaluator_seed.path must be a canonical directory relative to the Git target")
    if not isinstance(spec["protocol"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", spec["protocol"]):
        raise ValueError("evaluator_seed.protocol must identify the reviewed evaluation protocol")
    return spec


def load_seed(spec, checkout, commit, max_bytes):
    """Read only declared regular blobs from the admitted commit with a single byte budget."""
    checked_seed_spec(spec)
    files = {}

    def git(*args):
        try:
            result = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, timeout=10)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("evaluator seed Git read timed out") from exc
        if result.returncode:
            raise ValueError("cannot read evaluator seed from the bound Git target")
        return result.stdout

    remaining = max_bytes
    def read(name):
        nonlocal remaining
        if name not in files:
            relative = spec["path"] + "/" + name
            row = git("ls-tree", "-lz", commit, "--", ":(literal)" + relative).partition(b"\t")[0].split()
            if len(row) != 4 or row[0] not in {b"100644", b"100755"} or row[1] != b"blob":
                raise ValueError(f"missing regular Git blob in evaluator seed: {name}")
            size = int(row[3])
            if size > remaining:
                raise ValueError("evaluator seed exceeds the Grounding byte budget")
            content = git("cat-file", "blob", row[2].decode("ascii"))
            if len(content) != size:
                raise ValueError("evaluator seed blob size changed")
            remaining -= size
            files[name] = content
        return files[name]

    manifest = _json(read("acceptance.json"))
    if not isinstance(manifest, dict) or set(manifest) != {"protocol", "acceptance"} or manifest["protocol"] != spec["protocol"]:
        raise ValueError("evaluator seed protocol differs from the Profile")
    acceptance = SharedAcceptance.from_dict(manifest["acceptance"])
    expected = examples(read(CASES), acceptance)
    for name in acceptance.parser_files:
        if name in {"acceptance.json", CASES} or name.startswith(("smoke/", "work/cases/")) or not read(name):
            raise ValueError(f"invalid evaluator seed parser source: {name}")
    for case in expected:
        for name in acceptance.raw_artifacts:
            read(fixture_path(case, name))
    # A seed is a reusable protocol with examples, never a prior run's verdict.
    return files


def check_seed_contract(files, acceptance):
    previous = SharedAcceptance.from_dict(_json(files["acceptance.json"])["acceptance"])
    if (previous.rule != acceptance.rule or set(previous.criteria) != set(acceptance.criteria)
            or set(previous.validity_checks) != set(acceptance.validity_checks)
            or dict(previous.diagnostics) != dict(acceptance.diagnostics)
            or set(previous.raw_artifacts) != set(acceptance.raw_artifacts)):
        raise ValueError("seed contract differs from the declared criteria, checks, diagnostics or raw inputs")


def prepare_seed(directory, files):
    """Install the initial draft atomically. Retries keep the author's edits."""
    if directory.exists():
        return False
    with tempfile.TemporaryDirectory(dir=directory.parent) as temp:
        draft = Path(temp) / "draft"
        for name, content in files.items():
            path = draft / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        try:
            draft.rename(directory)
            return True
        except OSError:
            if not directory.is_dir():
                raise
            return False

def export_seed(state_root, run_id, fixtures, protocol, output):
    """Write a portable seed from accepted bytes and locally supplied labeled inputs."""
    from .agent_aci import DEFAULT_REAL_AGENT_BUDGET
    from .agent_budget import profile_agent_budget
    from .native_output import parser_smoke
    from .shared_acceptance_host import accepted_grounding_source
    from .state_store import StateStore

    checked_seed_spec({"path": "seed", "protocol": protocol})
    output = Path(output).absolute()
    if output.exists() or output.resolve().is_relative_to(Path(state_root).resolve()):
        raise ValueError("export needs a new directory outside run state")
    with StateStore(state_root) as store:
        source = accepted_grounding_source(store, run_id)
        if source is None:
            raise ValueError("run has no accepted Grounding evaluator")
        _, acceptance, files = source
        goal = store.list_facts(run_id, "GoalDefined")[0]["payload"]
        budget = profile_agent_budget(goal["research_contract"], DEFAULT_REAL_AGENT_BUDGET).max_output_bytes
    files = dict(files)
    reserved = {"acceptance.json", CASES}
    if any(name in reserved or name.startswith(("smoke/", "work/cases/")) for name in files):
        raise ValueError("parser source collides with evaluator seed metadata or fixtures")
    files["acceptance.json"] = json.dumps({"protocol": protocol, "acceptance": acceptance.to_dict()},
                                           ensure_ascii=False, indent=2).encode()
    remaining = budget - sum(map(len, files.values()))
    def read(name):
        nonlocal remaining
        path = fixtures / name
        if remaining < 0 or path.resolve() != fixtures.resolve() / name or not path.is_file():
            raise ValueError(f"missing regular bounded fixture: {name}")
        with path.open("rb") as stream:
            content = stream.read(remaining + 1)
        remaining -= len(content)
        if remaining < 0:
            raise ValueError("evaluator seed exceeds the Grounding byte budget")
        files[name] = content

    read(CASES)
    for case in examples(files[CASES], acceptance):
        for artifact in acceptance.raw_artifacts:
            read(fixture_path(case, artifact))
    with tempfile.TemporaryDirectory(dir=output.parent) as temp:
        root = Path(temp)
        prepare_seed(root / "draft", files)
        smoke = parser_smoke(json.dumps({"typed_candidate": {"acceptance": acceptance.to_dict()}}),
            source_root=root / "draft", test_root=root, timeout_seconds=15, max_bytes=budget)
        if smoke["error"]:
            raise ValueError("cannot export evaluator: " + smoke["error"])
        if output.exists():
            raise FileExistsError(output)
        (root / "draft").rename(output)
    return output


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Export an accepted Grounding evaluator and verified scientific examples")
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(export_seed(args.state_root, args.run_id, args.fixtures, args.protocol, args.output))
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")
