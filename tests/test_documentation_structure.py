"""Current user documentation must be navigable and retain operation boundaries."""
from pathlib import Path
import re


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


def test_current_docs_retain_managed_start_and_end_run_semantics():
    for relative in ("README.md", "README.zh-CN.md", "SETUP.md", "SETUP.zh-CN.md",
                     "USAGE.md", "USAGE.zh-CN.md", "docs/operator-guide.md",
                     "docs/operator-guide.zh-CN.md"):
        text = (ROOT / relative).read_text()
        for marker in ("Start now", "Prepare only"):
            assert marker in text, (relative, marker)
    # The quick start links to detailed managed-start guidance instead of
    # exposing helper and socket implementation details to first-time users.
    for relative in ("SETUP.md", "SETUP.zh-CN.md", "USAGE.md", "USAGE.zh-CN.md",
                     "docs/operator-guide.md", "docs/operator-guide.zh-CN.md"):
        text = (ROOT / relative).read_text()
        for marker in ("manage_host.py", "control.sock", "user-systemd"):
            assert marker in text, (relative, marker)
    text = (ROOT / "USAGE.md").read_text()
    for marker in ("q / Leave monitor", "x / End run", "--expected-revision", "--request-id",
                   "WAITING_HUMAN", "--include-private-data"):
        assert marker in text


def test_readme_exposes_the_current_user_guides():
    for suffix in (".md", ".zh-CN.md"):
        text = (ROOT / ("README" + suffix)).read_text()
        for target in ("SETUP", "USAGE", "profiles/README", "config/README",
                       "docs/operator-guide", "docs/architecture", "docs/README",
                       "CONTRIBUTING"):
            assert f"]({target}{suffix})" in text
        index = ROOT / "docs" / ("README" + suffix)
        rows = [line for line in index.read_text().splitlines() if line.startswith("| [")]
        assert rows
        for row in rows:
            target, = re.findall(r"\[[^]]+\]\(([^)]+)\)", row)
            guide = index.parent / target
            assert guide.exists(), (index, target)
            assert guide.name.endswith(".zh-CN.md") == (suffix == ".zh-CN.md"), (index, target)
            counterpart = (guide.with_name(guide.name.replace(".zh-CN.md", ".md"))
                           if suffix == ".zh-CN.md" else guide.with_suffix(".zh-CN.md"))
            assert counterpart.exists(), guide
            assert f"]({counterpart.name})" in guide.read_text(), guide
