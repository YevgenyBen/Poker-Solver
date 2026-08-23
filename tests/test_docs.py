"""CLAUDE.md is loaded into every session as current state — so check it.

M96. Every other doc in this project is history, consulted by search;
CLAUDE.md is the one that is *asserted* to whoever picks the project up,
and it is the only one nothing verified. Three of the four `api/config.py`
constants it named had drifted from their real values, and the file
contradicted itself in two places.

The check is narrow on purpose. Prose cannot be verified mechanically,
and pretending otherwise would produce a test that fails on rewording.
What CAN be verified is a claim of the form `NAME = value` about a
constant that really exists — which is exactly the class that went stale,
because those are the numbers a reader reasons about cost and behaviour
from.
"""

import re
from pathlib import Path

import pytest

import api.config as cfg

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLAUDE_MD = _REPO_ROOT / "CLAUDE.md"

# `NAME = 123` or `NAME=123`, with or without backticks around it.
_CLAIM = re.compile(r"\b([A-Z][A-Z0-9_]{3,})\s*=\s*(-?[\d_]+(?:\.\d+)?)\b")


def _claims():
    """(line number, constant name, claimed value) for every claim in
    CLAUDE.md about a constant that actually exists in api/config.py.

    A name that is not a real config constant is ignored rather than
    failed: the file also discusses engine-level names, response fields
    and pseudocode, and a doc test that polices vocabulary would just be
    a nuisance.
    """
    found = []
    for lineno, line in enumerate(_CLAUDE_MD.read_text(encoding="utf-8").splitlines(), 1):
        for name, value in _CLAIM.findall(line):
            if hasattr(cfg, name):
                found.append((lineno, name, value))
    return found


def test_claude_md_names_at_least_one_real_config_constant():
    """Guards the guard. If the regex or the file layout changes such
    that nothing matches any more, every other test here passes
    vacuously — which is the failure mode a doc checker is most likely to
    die of, since nobody would notice."""
    assert _claims(), "no config-constant claims found in CLAUDE.md — the scan is broken"


@pytest.mark.parametrize("lineno,name,claimed", _claims(), ids=lambda v: str(v))
def test_claude_md_config_claims_match_the_code(lineno, name, claimed):
    """The actual check.

    If this fails, CLAUDE.md is telling every future session a number the
    code does not use. Fix the doc, or — if the number is a *historical*
    value being narrated (what M24 shipped, what M26 measured) — write it
    as "N at the time" so it reads as history and is not scanned as a
    current claim.
    """
    actual = getattr(cfg, name)
    expected = float(claimed.replace("_", ""))
    assert float(actual) == expected, (
        f"CLAUDE.md:{lineno} says {name} = {claimed}, but api/config.py has {actual!r}"
    )


def test_claude_md_does_not_still_advertise_a_withdrawn_speedup():
    """M68 published a 1.95x speedup for `_simulate_equity_shared_board`
    and M70 withdrew it as an invalid cross-session comparison. CLAUDE.md
    kept quoting 1.95x in its constraints list while its own "Measuring
    performance" section, twenty lines later, said the number had been
    withdrawn — the file arguing with itself.

    Pinned by name because a withdrawn measurement reappearing is exactly
    the kind of thing that gets copied forward by someone summarising.
    """
    text = _CLAUDE_MD.read_text(encoding="utf-8")
    shared_board = [line for line in text.splitlines() if "_simulate_equity_shared_board" in line]
    assert shared_board, "the shared-board speedup note is gone entirely — was that intended?"
    context = text[text.index(shared_board[0]) : text.index(shared_board[0]) + 600]
    assert "6.06x" in context, "quote M70's interleaved 6.06x, not M68's withdrawn number"
