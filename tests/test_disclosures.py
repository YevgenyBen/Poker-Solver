"""M285: every number a player is shown has a source.

The 2026-09-18 audit's F58, and three live failures of it: a figure typed
into copy with no constant behind it cannot be reached by updating the
constant. `SIZING_CAVEAT_REASON` quoted M251's "98% ... 22 spots" through
two corrections of that same figure elsewhere, and its test asserted the
literal "98%".
"""
import pathlib

import pytest

from api import config as cfg
from bench import disclosures as d

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_every_user_facing_string_that_quotes_a_number_is_registered():
    """A new warning cannot ship without saying where its numbers come from."""
    registered = {e.copy for e in d.REGISTRY}
    assert set(d.numeric_copies(cfg)) - registered == set()


def test_every_registered_disclosure_exists_and_quotes_a_number():
    for entry in d.REGISTRY:
        assert d.NUMBER.search(d.copy_text(cfg, entry.copy)), entry.copy


def test_every_number_in_every_disclosure_is_sourced():
    """The guard itself. Each number must render from a registered constant
    or be a literal carrying its source."""
    unsourced = {e.copy: d.unsourced_numbers(e, cfg) for e in d.REGISTRY}
    assert {k: v for k, v in unsourced.items() if v} == {}


def test_every_registered_constant_exists():
    missing = [(e.copy, c) for e in d.REGISTRY for c in e.constants if not hasattr(cfg, c)]
    assert missing == []


def test_every_literal_says_where_it_came_from():
    for entry in d.REGISTRY:
        for number, source in entry.literals.items():
            assert source.strip(), (entry.copy, number)


def test_every_registered_study_exists_and_can_be_run():
    """A study path that points nowhere is the failure M285 exists to end."""
    import importlib
    for entry in d.REGISTRY:
        if entry.study is None:
            continue
        assert (ROOT / entry.study).exists(), entry.study
        module = importlib.import_module(entry.study[:-3].replace("/", "."))
        assert callable(module.summarise) and callable(module.main)


def test_currency_is_stated_both_ways():
    """A figure measured under a configuration that no longer ships must say
    what shipped after it; a current one must not claim to be superseded."""
    for entry in d.REGISTRY:
        if entry.current:
            assert not entry.superseded_by, entry.copy
        else:
            assert entry.superseded_by, entry.copy
        if not entry.shown:
            assert not entry.current, "a dormant note carries no current measurement"
        assert entry.instrument in (d.ENGINE, d.REFERENCE, d.HAND_STORE), entry.copy


def test_the_matcher_is_tight_enough_to_catch_a_stray_number():
    """A loose matcher would source numbers by coincidence and this whole
    module would be a dead guard (M214). 0.9661 must not 'explain' a 1."""
    assert "1" not in d.renderings(0.9661)
    assert "97%" in d.renderings(0.9661)
    # A fraction's whole-number form is its PERCENTAGE: 0.0074 is 0.74
    # points, which copy writes as "1" point - that is a real rendering.
    assert "1" in d.renderings(0.0074)
    assert "0" not in d.renderings(0.4)
    assert d.renderings(True) == set()


def test_a_number_typed_into_copy_without_a_source_is_caught(monkeypatch):
    """The failure mode itself: someone adds a figure to copy and not to a
    constant. It must fail here, not reach a player."""
    entry = next(e for e in d.REGISTRY if e.copy == "TURN_SHOVE_NOTE")
    assert d.unsourced_numbers(entry, cfg) == []
    monkeypatch.setattr(cfg, "TURN_SHOVE_NOTE", cfg.TURN_SHOVE_NOTE + " It happens 63% of the time.")
    assert d.unsourced_numbers(entry, cfg) == ["63%"]


def test_a_constant_that_moves_away_from_its_copy_is_caught(monkeypatch):
    """The other direction: re-measuring moves the constant, and copy that
    was not updated with it stops being sourced."""
    entry = next(e for e in d.REGISTRY if e.copy == "TURN_SHOVE_NOTE")
    monkeypatch.setattr(cfg, "TURN_SHOVE_COST_BB", 3.1)
    assert "2.4" in d.unsourced_numbers(entry, cfg)


def test_the_table_reports_how_many_figures_are_not_current():
    text = d.table(cfg)
    shown = [e for e in d.REGISTRY if e.shown]
    stale = sum(1 for e in shown if not e.current)
    assert f"{len(shown)} shown to players; {stale} quote figures" in text
