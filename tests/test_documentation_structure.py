"""Current user documentation must be navigable and retain operation boundaries."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_public_documents_have_no_broken_local_links():
    documents = [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md"),
                 *(ROOT / "profiles").glob("*.md"), *(ROOT / "config").glob("*.md"),
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
    for relative in ("README.md", "README.zh-CN.md", "SETUP.md", "SETUP.zh-CN.md",
                     "USAGE.md", "USAGE.zh-CN.md", "docs/operator-guide.md",
                     "docs/operator-guide.zh-CN.md"):
        text = (ROOT / relative).read_text()
        for marker in ("Start now", "Prepare only", "manage_host.py", "control.sock", "user-systemd"):
            assert marker in text, (relative, marker)
    text = (ROOT / "USAGE.md").read_text()
    for marker in ("q / Leave monitor", "x / End run", "--expected-revision", "--request-id",
                   "WAITING_HUMAN", "--include-private-data"):
        assert marker in text


def test_readme_exposes_the_current_user_and_maintenance_guides():
    for suffix in (".md", ".zh-CN.md"):
        text = (ROOT / ("README" + suffix)).read_text()
        for target in ("SETUP", "USAGE", "profiles/README", "config/README",
                       "docs/operator-guide", "docs/architecture", "docs/qualification",
                       "docs/guides/local-workspace", "docs/README", "CONTRIBUTING"):
            assert f"]({target}{suffix})" in text
        for target in ("AGENTS.md", "CLAUDE.md", "docs/codex-memory/CURRENT.md",
                       "docs/codex-memory/INDEX.md", "docs/design/"):
            assert f"]({target})" in text
        index = ROOT / "docs" / ("README" + suffix)
        rows = [line for line in index.read_text().splitlines() if line.startswith("| [")]
        assert rows
        for row in rows:
            target, = re.findall(r"\[[^]]+\]\(([^)]+)\)", row)
            guide = index.parent / target
            assert guide.is_file(), (index, target)
            assert guide.name.endswith(".zh-CN.md") == (suffix == ".zh-CN.md"), (index, target)
            counterpart = (guide.with_name(guide.name.replace(".zh-CN.md", ".md"))
                           if suffix == ".zh-CN.md" else guide.with_suffix(".zh-CN.md"))
            assert counterpart.is_file(), guide
            assert f"]({counterpart.name})" in guide.read_text(), guide
