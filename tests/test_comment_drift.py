r"""Comments that name a function their own code does not call.

M101. Derived from a real defect rather than invented: M99 found
`api/solving.py`'s `_advise` describing its routing as going "through
`solve_flop_turn` ... shares the turn cell's cache" — eleven milestones
after M88 moved it onto `solve_flop` with its own `_flop_node_cache`. The
comment was confident, specific, and wrong, and nothing could have caught
it except someone reading that function for another reason.

`test_docs.py` (M96) does the same job for CLAUDE.md's config claims. This
is the code-comment equivalent, and it exists because the audit's *first*
attempt at detecting stale comments — a regex for `M\d+ .* (now|currently|
as of)` — flagged eight lines, all of them accurate, and missed the one
real case entirely, since that comment contains none of those words. A
detector that fires on comments which merely *sound* like claims is worse
than none: it trains you to skim its output.

The rule here is narrow on purpose: flag a comment that says this code
*goes through / calls / uses* some function, when the enclosing function
never calls it. That is checkable, and it is exactly the shape that went
stale.

Two deliberate limits, both learned by getting them wrong first:

- **Comment BLOCKS, not lines.** A disclaimer ("it used to say ...")
  routinely sits a line above the claim it disclaims. Matching per line
  flags the claim and ignores the retraction beside it — which is what
  happened on M99's own correction note the first time this ran.
- **Contrast words exempt the block.** Comments legitimately mention
  functions they do not call, to say what the code is *not* doing or what
  it used to do. Those are the most valuable comments in this codebase,
  so they must never be penalised.
"""

import ast
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parent.parent

# "this code does X via Y" — a claim about the code it sits in.
_ASSERTS = re.compile(r"\b(goes through|calls|uses|via|routed|dispatches to|shares)\b", re.I)
# "unlike Y", "rather than Y", "used to Y" — a claim about what it is NOT.
_CONTRASTS = re.compile(
    r"\b(unlike|rather than|instead of|not |never |used to|would|no longer|"
    r"deliberately|corrected|previously|replaced)\b",
    re.I,
)
_INTERESTING = re.compile(r"`?\b(solve_\w+|_query_\w+|_get_or_solve_\w+|build_\w+)\b`?")


def _source_files():
    for package in ("api", "poker_solver"):
        yield from sorted((_ROOT / package).rglob("*.py"))


def _called_names(function_node):
    """Every function name called anywhere inside `function_node`."""
    called = set()
    for node in ast.walk(function_node):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            called.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    return called


def _drifted_comments():
    """(file, line, enclosing function, named function, comment text)."""
    found = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        tree = ast.parse(text)
        functions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for function in functions:
            start = function.lineno
            end = getattr(function, "end_lineno", start)
            called = _called_names(function)
            index = start - 1
            while index < min(end, len(lines)):
                if not lines[index].strip().startswith("#"):
                    index += 1
                    continue
                block_start = index
                block = []
                while index < min(end, len(lines)) and lines[index].strip().startswith("#"):
                    block.append(lines[index].strip().lstrip("#").strip())
                    index += 1
                comment = " ".join(block)
                if not _ASSERTS.search(comment) or _CONTRASTS.search(comment):
                    continue
                for name in _INTERESTING.findall(comment):
                    if name not in called and name != function.name:
                        found.append(
                            (
                                str(path.relative_to(_ROOT)),
                                block_start + 1,
                                function.name,
                                name,
                                comment[:100],
                            )
                        )
    return found


def test_no_comment_claims_a_call_its_function_does_not_make():
    """If this fails, a comment is describing routing the code no longer
    does — fix the comment, or reword it as history ("used to go through
    X"), which the contrast-word exemption deliberately allows."""
    drifted = _drifted_comments()
    assert not drifted, "stale routing comments:\n" + "\n".join(
        f"  {path}:{line} in {fn}() names `{name}` but never calls it\n    {text}"
        for path, line, fn, name, text in drifted
    )


def test_the_detector_actually_fires_on_the_defect_it_was_built_for():
    """Guards the guard, the same way `test_docs.py` does.

    A checker that has never been seen to fire is not known to work, and
    this one is easy to neuter by accident — widen `_CONTRASTS` a little
    and it silently exempts everything. So reconstruct M99's real defect
    and require a hit on it.
    """
    source = '''
def _advise(request):
    # M84: a flop decision goes through solve_flop_turn, which shares the
    # turn cell's cache, so asking twice costs one solve.
    return _query_flop_node_from_path(request)
'''
    tree = ast.parse(source)
    function = tree.body[0]
    called = _called_names(function)
    assert "solve_flop_turn" not in called, "fixture is wrong: it must NOT call the named function"
    lines = source.splitlines()
    comment = " ".join(
        line.strip().lstrip("#").strip() for line in lines if line.strip().startswith("#")
    )
    assert _ASSERTS.search(comment), "the assertion pattern no longer recognises this claim"
    assert not _CONTRASTS.search(comment), "a contrast word is wrongly exempting a real claim"
    assert "solve_flop_turn" in _INTERESTING.findall(comment)


def test_history_and_contrast_comments_are_never_flagged():
    """The most valuable comments here say what the code is NOT doing and
    what it used to do. Penalising those would delete the project's
    institutional memory to satisfy a linter."""
    for comment in (
        "M88 moved this off solve_flop_turn rather than sharing the turn cache",
        "unlike solve_flop_turn, this averages runouts at the terminal",
        "it used to go through solve_flop_turn and no longer does",
        "deliberately does not call build_chance_node here",
    ):
        assert _CONTRASTS.search(comment), f"a history comment would be flagged: {comment!r}"
