"""Guards the standalone-engine constraint: poker_solver/ must never
depend on the API layer or any web framework, so it stays usable as a
plain library with zero web dependencies. This is what makes that
constraint permanent rather than just true by accident.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
POKER_SOLVER_DIR = REPO_ROOT / "poker_solver"
API_DIR = REPO_ROOT / "api"
FORBIDDEN_MODULES = {"fastapi", "starlette", "uvicorn", "api"}


def _imported_top_level_modules(path: Path) -> set:
    """Top-level module names this file imports via `import x` or
    `from x import y` (absolute imports only — relative imports like
    `from .cards import Card` have node.level > 0 and are skipped, since
    they can only ever refer to something inside the same package)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    return modules


def _poker_solver_source_files():
    return sorted(POKER_SOLVER_DIR.rglob("*.py"))


def test_poker_solver_has_source_files():
    # Sanity check the scan below isn't silently checking zero files.
    assert len(_poker_solver_source_files()) >= 5


@pytest.mark.parametrize(
    "path", _poker_solver_source_files(), ids=lambda p: str(p.relative_to(POKER_SOLVER_DIR))
)
def test_no_forbidden_imports(path: Path):
    imported = _imported_top_level_modules(path)
    forbidden_found = imported & FORBIDDEN_MODULES
    assert not forbidden_found, f"{path} imports forbidden module(s): {forbidden_found}"


def test_api_imports_from_poker_solver():
    # The dependency should exist, just in the one allowed direction.
    tree = ast.parse((API_DIR / "main.py").read_text(encoding="utf-8"))
    imports_poker_solver = any(
        isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("poker_solver")
        for node in ast.walk(tree)
    )
    assert imports_poker_solver


# --- The detector itself, tested against deliberately bad/good input ---
# (so this suite proves the check actually works, not just that nothing
# currently trips it.)


def test_detector_catches_forbidden_import(tmp_path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("import fastapi\n", encoding="utf-8")
    assert _imported_top_level_modules(bad_file) & FORBIDDEN_MODULES == {"fastapi"}


def test_detector_catches_forbidden_from_import(tmp_path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("from starlette.testclient import TestClient\n", encoding="utf-8")
    assert _imported_top_level_modules(bad_file) & FORBIDDEN_MODULES == {"starlette"}

    other_file = tmp_path / "bad2.py"
    other_file.write_text("from api.main import app\n", encoding="utf-8")
    assert _imported_top_level_modules(other_file) & FORBIDDEN_MODULES == {"api"}


def test_detector_ignores_relative_imports(tmp_path):
    ok_file = tmp_path / "ok.py"
    ok_file.write_text("from .cards import Card\nfrom . import equity\n", encoding="utf-8")
    assert _imported_top_level_modules(ok_file) & FORBIDDEN_MODULES == set()


def test_detector_allows_numpy(tmp_path):
    ok_file = tmp_path / "ok.py"
    ok_file.write_text("import numpy as np\n", encoding="utf-8")
    assert _imported_top_level_modules(ok_file) & FORBIDDEN_MODULES == set()
