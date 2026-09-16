import pytest

from hypoloom.participant_controls import ParticipantScope, fold_participant_controls
from hypoloom.participant_controls import supports_participant_controls


@pytest.mark.parametrize("version,expected", [("hypoloom-lifecycle-v7", False), ("hypoloom-lifecycle-v8", True),
    ("hypoloom-lifecycle-v9", True), (None, False)])
def test_participant_control_semantics_follow_the_shared_lifecycle_threshold(version, expected):
    assert supports_participant_controls(version) is expected


def record(identity, action, *, role=None, reason="operator", text=None, revision=1):
    return {"command_id": identity, "workflow_id": "r", "revision": revision,
            "payload": {"participant_control": {"action": action,
                "scope": {"role": role, "component_id": None, "chain": None},
                "reason": reason, **({"text": text} if text is not None else {})}}}


def test_global_pause_then_one_expert_resume_keeps_peer_paused():
    rows = [record("pause", "pause"), record("resume", "resume", role="a", revision=2)]
    view = fold_participant_controls(rows, ("a", "b"))
    assert not view.reasons_for(ParticipantScope("a", "component-a", "1"))
    assert view.reasons_for(ParticipantScope("b", "component-b", "2")) == ("operator",)
    assert view == fold_participant_controls(list(reversed(rows)), ("a", "b"))


def test_operator_resume_does_not_clear_independent_leader_or_debate():
    rows = [record("leader", "pause", role="a", reason="leader:cycle-3"),
            record("operator", "pause", role="a", revision=2),
            record("debate", "pause", role="a", reason="debate:one", revision=3),
            record("resume", "resume", revision=4)]
    view = fold_participant_controls(rows, ("a", "b"))
    scope = ParticipantScope("a", "c", "1")
    assert set(view.reasons_for(scope)) == {"leader:cycle-3", "debate:one"}
    rows.append(record("leader-done", "resume", role="a", reason="leader:cycle-3", revision=5))
    assert fold_participant_controls(rows, ("a", "b")).reasons_for(scope) == ("debate:one",)


@pytest.mark.parametrize("node", ["EXPERIMENT_EXECUTION", "RESULT_ANALYSIS", "ACCEPT_OUTCOME", "HYPOTHESIS_UPDATE", "CYCLE_REPORT"])
def test_pause_does_not_block_current_iteration_acceptance_or_closure(node):
    view = fold_participant_controls([record("pause", "pause")], ("a",))
    assert not view.blocks(ParticipantScope("a", "c", "1"), node)
    assert view.blocks(ParticipantScope("a", "c", "1"), "EXPERIMENT_DESIGN_IMPLEMENT")
    assert view.blocks(ParticipantScope("a", "c", "1"), "LITERATURE_REVIEW")
    assert view.blocks(ParticipantScope("a", "c", "1"), "CREATE_EXPERIMENT")


def test_injected_note_is_scoped_and_existing_request_refs_show_consumption():
    rows = [record("note-1", "inject", role="a", text="Measure the baseline first."),
            record("note-2", "inject", role="b", text="Check source B.", revision=2)]
    view = fold_participant_controls(rows, ("a", "b"))
    notes = view.pending_notes(ParticipantScope("a", "c", "1"), ())
    assert [(note.command_id, note.text) for note in notes] == [("note-1", "Measure the baseline first.")]
    assert view.pending_notes(ParticipantScope("a", "c", "1"), ("note-1",)) == ()


def test_control_records_fail_closed_for_unknown_participants_and_actions():
    with pytest.raises(ValueError, match="role"):
        fold_participant_controls([record("bad", "pause", role="missing")], ("a",))
    with pytest.raises(ValueError, match="action"):
        fold_participant_controls([record("bad", "accept-science")], ("a",))
    with pytest.raises(ValueError, match="chain"):
        ParticipantScope("a", "c", 0)
