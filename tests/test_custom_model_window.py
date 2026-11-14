"""Native CC 2.1.325 treats unrecognized Claude-prefixed aliases as custom IDs."""
import pytest

from hypoloom.context_window import resolve_window, window_policy


@pytest.mark.parametrize("model", ["claude-private@20250101", "claude-unknown", "mimo-v2.6-flash"])
def test_explicit_custom_window_is_kept_with_independent_working_limit(model):
    environment = {"CLAUDE_CODE_MAX_CONTEXT_TOKENS": "262144"}
    value = resolve_window(model, {}, environment, {"context_metadata": False})
    assert (value["cc_context_tokens"], value["cc_window_source"]) == (262144, "cc_config")
    assert window_policy({"working_context_tokens": 120000}, value)["effective_context_tokens"] == 120000


def test_known_builtin_and_unconfigured_custom_alias_keep_their_defaults():
    value = resolve_window("claude-sonnet-4-6", {}, {"CLAUDE_CODE_MAX_CONTEXT_TOKENS": "1000000"}, {"context_metadata": False})
    assert (value["cc_context_tokens"], value["cc_window_source"]) == (200000, "cc_builtin")
    value = resolve_window("claude-private@20250101", {}, {}, {"context_metadata": False})
    assert (value["cc_context_tokens"], value["cc_window_source"]) == (1000000, "cc_default")
    value = resolve_window("claude-private@20250101", {}, {"CLAUDE_CODE_DISABLE_1M_CONTEXT": "1"}, {"context_metadata": False})
    assert value["cc_context_tokens"] == 200000
