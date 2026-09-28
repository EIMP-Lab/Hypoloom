"""Current user documentation must be navigable and retain operation boundaries."""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_public_documents_have_no_broken_local_links():
    documents = [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md"),
                 ROOT / "profiles/README.md", ROOT / "config/README.md",
                 *(ROOT / ".claude/skills").rglob("*.md")]
    missing = []
    for path in documents:
        for raw in re.findall(r"\[[^]]+\]\(([^)]+)\)", path.read_text()):
            target = raw.split("#", 1)[0]
            if target and "://" not in target and not (path.parent / target).exists():
                missing.append(f"{path.relative_to(ROOT)}: {target}")
    assert not missing, missing


def test_bootstrap_routes_current_memory_and_keeps_runtime_separate():
    text = (ROOT / "AGENTS.md").read_text()
    for path in ("docs/codex-memory/CURRENT.md", "docs/codex-memory/INDEX.md", "docs/README.md"):
        assert path in text and (ROOT / path).is_file()
    assert "`CLAUDE.md` and `.claude/`" in text
    assert "G0–G5" in text and "read-only" in text


def test_current_docs_retain_managed_start_and_end_run_semantics():
    for relative in ("README.md", "SETUP.md", "USAGE.md", "docs/operator-guide.md"):
        text = (ROOT / relative).read_text()
        for marker in ("Start now", "Prepare only", "manage_host.py", "control.sock", "user-systemd"):
            assert marker in text, (relative, marker)
    text = (ROOT / "USAGE.md").read_text()
    for marker in ("q / Leave monitor", "x / End run", "--expected-revision", "--request-id",
                   "WAITING_HUMAN", "--include-private-data"):
        assert marker in text


def test_readme_exposes_the_current_user_and_maintenance_guides():
    text = (ROOT / "README.md").read_text()
    for target in ("SETUP.md", "USAGE.md", "profiles/README.md", "docs/operator-guide.md",
                   "docs/architecture.md", "docs/qualification.md", "AGENTS.md", "CLAUDE.md"):
        assert f"]({target})" in text
