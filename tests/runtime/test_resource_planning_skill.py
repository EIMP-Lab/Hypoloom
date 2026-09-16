from __future__ import annotations

from pathlib import Path

from hypoloom.domain import ResourcePreference


REPOSITORY = Path(__file__).resolve().parents[2]
SKILL = (
    REPOSITORY
    / "hypoloom"
    / "skills"
    / "experiment-resource-planning"
    / "SKILL.md"
)


def _document() -> str:
    return SKILL.read_text(encoding="utf-8")


def _preference_examples(body: str) -> dict[ResourcePreference, str]:
    rows: dict[ResourcePreference, str] = {}
    in_table = False
    for line in body.splitlines():
        if line == "| Scientific condition | `resource_preference` |":
            in_table = True
            continue
        if not in_table or line == "| --- | --- |":
            continue
        if not line.startswith("|"):
            break
        condition, raw_preference = (
            value.strip().strip("`") for value in line.strip("|").split("|", 1)
        )
        preference = ResourcePreference(raw_preference)
        assert preference not in rows
        rows[preference] = condition
    return rows


def _norm(value: str) -> str:
    return " ".join(value.lower().split())


def test_skill_is_one_role_parameterized_packaged_source() -> None:
    body = _document()

    assert not body.startswith("---\n")
    assert "name: experiment-resource-planning" not in body
    assert "not a callable Skill" in body
    assert "exact name from the available Skill list" in body
    assert "agent_request_metadata.role" in body
    assert "Do not hardcode a role" in body
    assert "{{" not in body and "<role" not in body.lower()
    assert not (
        REPOSITORY / ".claude" / "skills" / "experiment-resource-planning"
    ).exists()


def test_decision_examples_cover_the_exact_resource_preference_behavior() -> None:
    body = _document()
    examples = _preference_examples(body)

    assert set(examples) == set(ResourcePreference)
    assert "gpu acceleration is not material" in _norm(examples[ResourcePreference.CPU])
    assert "documentation, report, checklist" in _norm(
        examples[ResourcePreference.CPU]
    )
    assert "non-production artifact" in _norm(examples[ResourcePreference.CPU])
    assert "cpu remains scientifically valid" in _norm(
        examples[ResourcePreference.AUTO]
    )
    required = _norm(examples[ResourcePreference.GPU_REQUIRED])
    assert "invalid or infeasible" in required
    assert "concrete evidence" in required


def test_advice_preserves_host_authority_and_routes_pooled_native_learning() -> None:
    body = _document()
    normalized = _norm(body)

    assert "host alone validates availability, chooses actual resources" in normalized
    assert "greatest live free vram" in normalized
    assert "stable uuid only as a deterministic tie-break" in normalized
    assert "never choose, name, rank, or request a uuid" in normalized
    assert "do not infer process ownership" in normalized
    assert "never kill, signal, reset, suspend, renice" in normalized
    assert "the old same-root non-busy/fixed-grant rule does not govern pooled scheduling" in normalized
    assert "only self_evolve's writable native plugin permits a strategy update" in normalized
    assert "omission keeps the profile initial workload budget when configured, otherwise its floor" in normalized
    assert (
        "do not update a profile, reserve, allowlist, runtime, host policy"
        in normalized
    )


def test_vram_advice_requires_measurement_or_explicit_accounting() -> None:
    body = _document()
    normalized = _norm(body)

    assert "a measured peak from the same model" in normalized
    assert "an explicit scaling relation for every changed dimension" in normalized
    assert "parameters, gradients, optimizer state, activations" in normalized
    assert (
        "keep the workload estimate separate from the host's reserve" in normalized
    )
    assert "rather than fabricating precision" in normalized
