"""Guards the stone law: external tools are INSTRUMENTS, never ingredients.

Independent solvers, bot APIs and hand-history corpora exist in this
project to MEASURE the engine. None of them may end up inside what
ships. The reasoning is in CLAUDE.md; the short version is that a
reference has to be independent of the thing it measures, so the moment
an outside solver's output is inside `poker_solver/` we lose the only
instrument that can see the errors our own references share — and M194
established that shared model error is the entire remaining residual.

Written in the shape of `test_package_boundary.py`, which makes the
engine/API split permanent rather than true by accident. Same idea, one
layer out.

Scope is deliberately the SHIPPED packages only. Studies, harnesses and
benchmark scripts live in the scratchpad and are meant to drive these
tools; forbidding that would defeat the point.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_DIRS = (REPO_ROOT / "poker_solver", REPO_ROOT / "api")

# Import names that would mean an external engine or corpus is being
# pulled into the product. `subprocess` is here because shelling out to a
# solver binary is the most likely way this rule gets broken in practice
# — it needs no dependency and no import that looks suspicious.
FORBIDDEN_MODULES = {
    "subprocess",
    "texassolver",
    "slumbot",
    "pokerkit",
    "treys",
    "pokerstove",
    "open_spiel",
    "pyshark",
}

# Names that would indicate a shipped answer sourced from outside. These
# are matched as substrings of the SOURCE TEXT, so they also catch a
# hardcoded path or a URL that no import statement would reveal.
FORBIDDEN_TEXT = (
    "console_solver",
    "TexasSolver",
    "slumbot.com",
    "/api/new_hand",
    "piosolver",
    "PioSOLVER",
    "gtowizard",
    "hand_history",
    "handhistory",
)


def _source_files():
    for directory in SHIPPED_DIRS:
        assert directory.is_dir(), f"{directory} is missing"
        for path in sorted(directory.rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def _imported_top_level_modules(path: Path) -> set:
    """Top-level names imported absolutely. Relative imports are skipped:
    they can only refer to something inside the same package."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    return modules


ALL_FILES = list(_source_files())


def test_the_shipped_packages_have_source_files():
    """A scan over nothing passes trivially — the same trap
    test_package_boundary.py guards against."""
    assert len(ALL_FILES) > 20, f"only found {len(ALL_FILES)} shipped files"


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_shipped_module_imports_an_external_tool(path: Path):
    offending = _imported_top_level_modules(path) & FORBIDDEN_MODULES
    assert not offending, (
        f"{path.relative_to(REPO_ROOT)} imports {sorted(offending)}. External "
        f"solvers, bot APIs and hand-history tooling are instruments for "
        f"measuring this engine, never part of what ships — see the stone law "
        f"in CLAUDE.md. Drive them from the scratchpad and bring back findings."
    )


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_shipped_module_reaches_for_an_external_solver(path: Path):
    """Catches what an import scan cannot: a hardcoded binary path, a
    solver's endpoint, or a dataset filename."""
    text = path.read_text(encoding="utf-8")
    hits = sorted({needle for needle in FORBIDDEN_TEXT if needle in text})
    assert not hits, (
        f"{path.relative_to(REPO_ROOT)} references {hits}. An answer that "
        f"originates outside poker_solver/ is a lookup with our name on it, "
        f"and it would stop the tool being able to judge us — see CLAUDE.md."
    )


def test_the_import_detector_actually_catches_one(tmp_path):
    """Mutation-proofing: a scanner that matches nothing would pass every
    case above while enforcing nothing."""
    probe = tmp_path / "probe.py"
    probe.write_text("import subprocess\n", encoding="utf-8")
    assert _imported_top_level_modules(probe) & FORBIDDEN_MODULES == {"subprocess"}


def test_the_import_detector_allows_what_the_engine_really_uses(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text("import numpy\nfrom .cards import Card\nimport json\n",
                     encoding="utf-8")
    assert not (_imported_top_level_modules(probe) & FORBIDDEN_MODULES)


def test_the_text_detector_actually_catches_one():
    """The text scan is the half that catches a shell-out by path, which
    is how this rule is most likely to be broken."""
    sample = 'BINARY = r"C:\\tools\\console_solver.exe"'
    assert [n for n in FORBIDDEN_TEXT if n in sample] == ["console_solver"]


def test_the_law_is_written_down_where_it_will_be_read():
    """A guard whose reasoning lives only in a test file is a rule nobody
    knows about. CLAUDE.md is loaded every session; the law belongs there
    and this asserts it stays."""
    claude_md = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "External tools are INSTRUMENTS, never ingredients" in claude_md
    assert "Stone law" in claude_md
    assert Path(__file__).name in claude_md, (
        "CLAUDE.md should name this test, so a reader knows the law is "
        "enforced rather than aspirational"
    )
